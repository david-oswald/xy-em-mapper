"""HDF5 trace container for a mapping run.

One file per run. Per-trace data is stored in flat datasets over all traces
(row = trace), and the traces of each grid point form one contiguous slice:

    traces/<ch>         (N, n_samples) raw ADC values (int8/int16)
    plaintext           (N, plaintext_bytes) uint8
    label               (N,) uint8, index into attrs["label_names"]
    x, y, z             (N,) float64 stage position in mm
    point               (N,) uint32 grid point index
    auto_triggered      (N,) bool, still auto-triggered after all retries
    overrange/<ch>      (N,) bool
    points/x, y, z      (P,) float64
    points/start        (P,) uint64 first row of the point
    points/count        (P,) uint32
    points/done         (P,) bool, set once all traces of the point are written
    points/time         (P,) float64 Unix time the point was completed

Attributes: format_version, config (JSON), scope (JSON: sample interval,
scaling, ...), strategy / target (JSON), label_names, channels.

Rows of points that are not done contain no valid data; use points/done (or
--resume) after an interrupted run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Mapping, Sequence

import h5py
import numpy as np

from .config import Point

FORMAT_VERSION = 1
_CHUNK_BYTES = 4 * 1024 * 1024


class TraceFile:
    def __init__(self, f: h5py.File) -> None:
        self.f = f
        self.n_per_point = int(f.attrs["traces_per_point"])

    @classmethod
    def create(
        cls,
        path: Path,
        *,
        config: dict,
        points: Sequence[Point],
        traces_per_point: int,
        scope_meta: dict,
        dtype: np.dtype,
        channels: Sequence[str],
        n_samples: int,
        plaintext_bytes: int,
        label_names: Sequence[str],
        strategy_meta: dict,
        target_meta: dict,
        compression: str | None = None,
    ) -> "TraceFile":
        f = h5py.File(path, "x")  # fail if it exists
        try:
            n_points = len(points)
            n = n_points * traces_per_point
            f.attrs.update({
                "format_version": FORMAT_VERSION,
                "config": json.dumps(config),
                "scope": json.dumps(scope_meta),
                "strategy": json.dumps(strategy_meta),
                "target": json.dumps(target_meta),
                "label_names": list(label_names),
                "channels": list(channels),
                "traces_per_point": traces_per_point,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
            rows = max(1, min(traces_per_point, _CHUNK_BYTES // max(1, n_samples * np.dtype(dtype).itemsize)))
            for ch in channels:
                f.create_dataset(f"traces/{ch}", (n, n_samples), dtype=dtype, chunks=(rows, n_samples),
                                 compression=compression)
                f.create_dataset(f"overrange/{ch}", (n,), dtype=bool)
            f.create_dataset("plaintext", (n, plaintext_bytes), dtype=np.uint8)
            f.create_dataset("label", (n,), dtype=np.uint8)
            f.create_dataset("auto_triggered", (n,), dtype=bool)
            idx = np.arange(n_points)
            px = np.array([p.x for p in points], dtype=np.float64)
            py = np.array([p.y for p in points], dtype=np.float64)
            pz = np.array([p.z for p in points], dtype=np.float64)
            f["point"] = np.repeat(idx, traces_per_point).astype(np.uint32)
            f["x"] = np.repeat(px, traces_per_point)
            f["y"] = np.repeat(py, traces_per_point)
            f["z"] = np.repeat(pz, traces_per_point)
            f["points/x"] = px
            f["points/y"] = py
            f["points/z"] = pz
            f["points/start"] = (idx * traces_per_point).astype(np.uint64)
            f["points/count"] = np.full(n_points, traces_per_point, dtype=np.uint32)
            f["points/done"] = np.zeros(n_points, dtype=bool)
            f["points/time"] = np.zeros(n_points, dtype=np.float64)
            f.flush()
        except BaseException:
            f.close()
            Path(path).unlink(missing_ok=True)
            raise
        return cls(f)

    @classmethod
    def open_for_resume(cls, path: Path, config: dict) -> "TraceFile":
        f = h5py.File(path, "r+")
        stored = json.loads(f.attrs["config"])
        if stored != config:
            f.close()
            raise ValueError(f"{path} was recorded with a different config; cannot resume")
        return cls(f)

    def is_done(self, point_index: int) -> bool:
        return bool(self.f["points/done"][point_index])

    @property
    def n_done(self) -> int:
        return int(self.f["points/done"][:].sum())

    def write_traces(
        self,
        point_index: int,
        offset: int,
        traces: Mapping[str, np.ndarray],
        overrange: Mapping[str, np.ndarray],
        auto_triggered: np.ndarray,
    ) -> None:
        """Write rows [offset, offset + k) of a point's traces."""
        k = len(auto_triggered)
        a = point_index * self.n_per_point + offset
        for ch, arr in traces.items():
            self.f[f"traces/{ch}"][a:a + k] = arr[:k]
            self.f[f"overrange/{ch}"][a:a + k] = overrange[ch][:k]
        self.f["auto_triggered"][a:a + k] = auto_triggered

    def finish_point(self, point_index: int, plaintexts: np.ndarray, labels: np.ndarray) -> None:
        a = point_index * self.n_per_point
        self.f["plaintext"][a:a + self.n_per_point] = plaintexts
        self.f["label"][a:a + self.n_per_point] = labels
        self.f["points/time"][point_index] = time.time()
        self.f["points/done"][point_index] = True
        self.f.flush()

    def close(self) -> None:
        self.f.close()

    def __enter__(self) -> "TraceFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
