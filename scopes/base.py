"""Backend-independent oscilloscope interface for batched side-channel capture.

The interface is built around segmented ("rapid block") acquisition:

    scope.configure(channels, acquisition, trigger)  # once, returns AcquisitionInfo
    scope.arm(n_segments)                            # one trigger per segment
    scope.wait(timeout_s)
    result = scope.fetch(buffers)                    # fills preallocated raw arrays

Samples stay as raw ADC integers end to end. Each channel's `ChannelScaling`
converts them to volts when needed: volts = raw * volts_per_count + offset_v.
"""

from __future__ import annotations

import enum
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np


class ScopeError(Exception):
    """Base error for oscilloscope failures."""


class ScopeNotFoundError(ScopeError):
    """Raised when no device (or not the requested device) can be opened."""


class ScopeDriverError(ScopeError):
    """Raised when the vendor driver/library is missing or returns an error."""


class Coupling(enum.Enum):
    AC = "ac"
    DC = "dc"  # high impedance (1 MOhm)
    DC_50OHM = "dc50"


class Edge(enum.Enum):
    RISING = "rising"
    FALLING = "falling"


@dataclass(frozen=True)
class ChannelConfig:
    """Analog input settings.

    `range_v` is the full-scale amplitude: the input covers +-range_v around
    -offset_v. `offset_v` is added to the signal before digitization (so an
    offset of -1.0 V centres the window on +1.0 V).
    """

    name: str  # "A", "B", ...
    range_v: float
    coupling: Coupling = Coupling.DC
    offset_v: float = 0.0
    bandwidth_limit_hz: float | None = None  # None = full bandwidth


@dataclass(frozen=True)
class TriggerConfig:
    """Edge trigger on one of the configured channels.

    `auto_trigger_s`: if set, the scope fires on its own after this time
    without a trigger event. Such segments are flagged in `FetchResult`.
    """

    source: str
    level_v: float
    edge: Edge = Edge.RISING
    auto_trigger_s: float | None = None


@dataclass(frozen=True)
class AcquisitionConfig:
    """Timing of each captured segment.

    `delay_s` is the position of the first sample relative to the trigger:
    positive values start recording after the trigger (trigger delay),
    negative values include pre-trigger samples.
    """

    sample_rate_hz: float
    n_samples: int
    delay_s: float = 0.0


@dataclass(frozen=True)
class ChannelScaling:
    """Conversion from raw ADC counts to volts for one channel."""

    volts_per_count: float
    offset_v: float  # added after scaling, i.e. -analogue offset
    adc_min: int
    adc_max: int

    def to_volts(self, raw: np.ndarray) -> np.ndarray:
        return raw.astype(np.float32) * np.float32(self.volts_per_count) + np.float32(self.offset_v)


@dataclass(frozen=True)
class AcquisitionInfo:
    """What the scope actually configured (may differ from what was requested)."""

    sample_interval_s: float
    n_samples: int
    delay_samples: int  # first sample index relative to trigger (negative = pre-trigger)
    dtype: np.dtype
    channels: tuple[str, ...]
    scaling: Mapping[str, ChannelScaling]
    max_segments: int  # upper bound for arm(n_segments) at this record length

    @property
    def sample_rate_hz(self) -> float:
        return 1.0 / self.sample_interval_s

    def metadata(self) -> dict:
        """Plain-dict description suitable for storing next to the traces."""
        return {
            "sample_interval_s": self.sample_interval_s,
            "n_samples": self.n_samples,
            "delay_samples": self.delay_samples,
            "dtype": np.dtype(self.dtype).str,
            "channels": list(self.channels),
            "scaling": {
                ch: {
                    "volts_per_count": s.volts_per_count,
                    "offset_v": s.offset_v,
                    "adc_min": s.adc_min,
                    "adc_max": s.adc_max,
                }
                for ch, s in self.scaling.items()
            },
        }


@dataclass
class FetchResult:
    n_segments: int
    # True where the segment was captured by the auto-trigger timeout rather
    # than by a real trigger event; re-run those segments.
    auto_triggered: np.ndarray
    # How auto_triggered was determined: "driver", "data", "timing" or "none".
    auto_trigger_detection: str
    # Per channel, True where the input exceeded the selected range.
    overrange: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def any_auto_triggered(self) -> bool:
        return bool(self.auto_triggered.any())


@dataclass(frozen=True)
class DeviceInfo:
    backend: str
    serial: str
    model: str = ""

    def __str__(self) -> str:
        return f"{self.backend}:{self.serial}" + (f" ({self.model})" if self.model else "")


class Scope(ABC):
    """Abstract oscilloscope.

    Backends subclass this and implement the abstract methods. Usage:

        with Backend.open() as scope:
            info = scope.configure([ChannelConfig("A", 0.1)], AcquisitionConfig(1e9, 10_000), trig)
            buffers = scope.allocate(n_segments=1000)
            scope.arm(1000)
            scope.wait(timeout_s=5)
            result = scope.fetch(buffers)
    """

    backend_name: str = ""

    def __init__(self) -> None:
        self._info: AcquisitionInfo | None = None

    # --- discovery / connection ----------------------------------------
    @classmethod
    @abstractmethod
    def list_devices(cls) -> list[DeviceInfo]:
        """Return the devices currently available for this backend."""

    @classmethod
    @abstractmethod
    def open(cls, serial: str | None = None) -> "Scope":
        """Open the device with `serial`, or the first one found."""

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def device_info(self) -> DeviceInfo: ...

    def __enter__(self) -> "Scope":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- configuration -------------------------------------------------
    @abstractmethod
    def configure(
        self,
        channels: Sequence[ChannelConfig],
        acquisition: AcquisitionConfig,
        trigger: TriggerConfig | None = None,
    ) -> AcquisitionInfo:
        """Apply settings. `trigger=None` means free-running (no trigger)."""

    @property
    def info(self) -> AcquisitionInfo:
        if self._info is None:
            raise ScopeError("Scope is not configured; call configure() first")
        return self._info

    def allocate(self, n_segments: int) -> dict[str, np.ndarray]:
        """Allocate fetch buffers of shape (n_segments, n_samples) per channel."""
        info = self.info
        return {ch: np.empty((n_segments, info.n_samples), dtype=info.dtype) for ch in info.channels}

    # --- acquisition ---------------------------------------------------
    @abstractmethod
    def arm(self, n_segments: int = 1) -> None:
        """Start a capture of `n_segments` triggered segments."""

    @abstractmethod
    def disarm(self) -> None:
        """Abort a running capture. Already captured segments can still be fetched."""

    @abstractmethod
    def is_ready(self) -> bool:
        """True once all armed segments have been captured."""

    def wait(self, timeout_s: float | None = None, poll_s: float = 0.0005) -> None:
        """Block until the capture completes. Raises TimeoutError (scope stays armed)."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while not self.is_ready():
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"Capture did not complete within {timeout_s} s")
            time.sleep(poll_s)

    @abstractmethod
    def fetch(self, out: Mapping[str, np.ndarray]) -> FetchResult:
        """Copy captured segments into `out` (channel -> (n_segments, n_samples) raw arrays).

        Channels may be a subset of the configured ones. Arrays must be
        C-contiguous with the dtype from `info.dtype`.
        """

    def capture(
        self, out: Mapping[str, np.ndarray], timeout_s: float | None = None
    ) -> FetchResult:
        """arm + wait + fetch for as many segments as `out` holds."""
        n_segments = next(iter(out.values())).shape[0]
        self.arm(n_segments)
        try:
            self.wait(timeout_s)
        except TimeoutError:
            self.disarm()
            raise
        return self.fetch(out)
