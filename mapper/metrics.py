"""Point metrics: reduce the traces captured at one grid point to one value.

plot-xy.py evaluates a metric for every completed point and paints the value
as a heatmap cell. To add your own, subclass `PointMetric` and pass it as
`--metric my_metrics.py:MyMetric` (extra `--metric-arg key=value` options are
passed to its constructor).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

import h5py
import numpy as np


@dataclass
class PointData:
    """Everything stored for one grid point, restricted to valid traces and the sample window."""

    index: int
    x: float
    y: float
    z: float
    traces: dict[str, np.ndarray]  # channel -> (n, n_window) raw ADC values
    plaintexts: np.ndarray  # (n, plaintext_bytes) uint8
    labels: np.ndarray  # (n,) uint8
    label_names: tuple[str, ...]
    scaling: dict[str, dict]  # channel -> {"volts_per_count", "offset_v", ...}
    sample_interval_s: float
    window: tuple[int, int]  # [start, stop) sample indices of the traces

    def volts(self, channel: str) -> np.ndarray:
        s = self.scaling[channel]
        return self.traces[channel].astype(np.float32) * np.float32(s["volts_per_count"]) + np.float32(s["offset_v"])

    def to_volts(self, channel: str, raw_value: np.ndarray | float) -> np.ndarray | float:
        """Scale a value computed on raw counts (mean, ...) to volts."""
        s = self.scaling[channel]
        return raw_value * s["volts_per_count"] + s["offset_v"]

    def label(self, name: str) -> int:
        try:
            return self.label_names.index(name)
        except ValueError:
            raise ValueError(f"No class {name!r} in this file; classes are {list(self.label_names)}") from None


class PointMetric(ABC):
    """One value per grid point. `unit` labels the colorbar."""

    title: str = ""
    unit: str = ""

    @abstractmethod
    def __call__(self, data: PointData, channel: str) -> float: ...


class MeanMetric(PointMetric):
    """Mean over all traces and all samples of the window, in volts."""

    title = "mean"
    unit = "V"

    def __call__(self, data: PointData, channel: str) -> float:
        return float(data.to_volts(channel, data.traces[channel].mean(dtype=np.float64)))


class RmsMetric(PointMetric):
    """RMS of the average trace (mean over traces first, then RMS over time), in volts.

    Averaging first removes uncorrelated noise, so this shows signal that is
    synchronous to the trigger. With raw=true the RMS is over all samples of
    all traces instead.
    """

    unit = "V"

    def __init__(self, raw: bool = False) -> None:
        self.raw = raw
        self.title = "RMS of all samples" if raw else "RMS of average trace"

    def __call__(self, data: PointData, channel: str) -> float:
        if self.raw:
            v = data.volts(channel).astype(np.float64)
        else:
            v = data.to_volts(channel, data.traces[channel].mean(axis=0, dtype=np.float64))
        return float(np.sqrt(np.mean(v * v)))


class TTestMetric(PointMetric):
    """Maximum |t| of Welch's t-test between two trace classes over the window (TVLA).

    Values above ~4.5 are commonly taken as evidence of leakage.
    """

    unit = "max |t|"

    def __init__(self, group_a: str = "fixed", group_b: str = "random") -> None:
        self.group_a, self.group_b = group_a, group_b
        self.title = f"Welch t-test {group_a} vs {group_b}"

    def __call__(self, data: PointData, channel: str) -> float:
        raw = data.traces[channel]
        a = raw[data.labels == data.label(self.group_a)].astype(np.float64)
        b = raw[data.labels == data.label(self.group_b)].astype(np.float64)
        if len(a) < 2 or len(b) < 2:
            return float("nan")
        var = a.var(axis=0, ddof=1) / len(a) + b.var(axis=0, ddof=1) / len(b)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (a.mean(axis=0) - b.mean(axis=0)) / np.sqrt(var)
        t = t[np.isfinite(t)]
        return float(np.abs(t).max()) if len(t) else float("nan")


METRICS: dict[str, type[PointMetric]] = {
    "mean": MeanMetric,
    "rms": RmsMetric,
    "ttest": TTestMetric,
}


def iter_points(f: h5py.File, channels: list[str], window: tuple[int, int | None] = (0, None)):
    """Yield PointData for every completed point of an em-mapper file."""
    scope = json.loads(f.attrs["scope"])
    label_names = tuple(str(n) for n in f.attrs["label_names"])
    n_samples = f[f"traces/{channels[0]}"].shape[1]
    start, stop = window
    stop = n_samples if stop is None else min(stop, n_samples)
    if not 0 <= start < stop:
        raise ValueError(f"Empty sample window [{start}, {stop}) for traces of {n_samples} samples")
    points = f["points"]
    done = points["done"][:]
    px, py, pz = points["x"][:], points["y"][:], points["z"][:]
    pstart, pcount = points["start"][:], points["count"][:]
    for p in np.flatnonzero(done):
        a, b = int(pstart[p]), int(pstart[p]) + int(pcount[p])
        valid = ~f["auto_triggered"][a:b]
        yield PointData(
            index=int(p),
            x=float(px[p]),
            y=float(py[p]),
            z=float(pz[p]),
            traces={ch: f[f"traces/{ch}"][a:b, start:stop][valid] for ch in channels},
            plaintexts=f["plaintext"][a:b][valid],
            labels=f["label"][a:b][valid],
            label_names=label_names,
            scaling=scope["scaling"],
            sample_interval_s=scope["sample_interval_s"],
            window=(start, stop),
        )
