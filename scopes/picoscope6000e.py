"""PicoScope 6000E series backend (ps6000a driver), e.g. PicoScope 6424E.

Talks to the vendor library `ps6000a` directly through ctypes, so the only
requirement besides numpy is an installed PicoSDK:

  Linux:   Pico apt/rpm repository, package `libps6000a` (/opt/picoscope/lib)
  Windows: PicoSDK installer (C:\\Program Files\\Pico Technology\\SDK\\lib)
  macOS:   PicoSDK .pkg (/Library/Frameworks/PicoSDK.framework)

Set PICOSDK_LIB_DIR to point at a non-standard install location.

Captures use rapid block mode: n segments are armed at once, and a single
GetValuesBulk call transfers them straight into the caller's numpy arrays.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time
import warnings
from ctypes import byref, c_char_p, c_double, c_int16, c_int32, c_uint32, c_uint64, c_void_p
from typing import Mapping, Sequence

import numpy as np

from .base import (
    AcquisitionConfig,
    AcquisitionInfo,
    ChannelConfig,
    ChannelScaling,
    Coupling,
    DeviceInfo,
    Edge,
    FetchResult,
    Scope,
    ScopeDriverError,
    ScopeError,
    ScopeNotFoundError,
    TriggerConfig,
)

# --- ps6000a constants (PicoDeviceEnums.h / PicoStatus.h) -----------------

PICO_OK = 0x00
PICO_USB3_0_DEVICE_NON_USB3_0_PORT = 0x11E

_STATUS_NAMES = {
    0x01: "PICO_MAX_UNITS_OPENED",
    0x02: "PICO_MEMORY_FAIL",
    0x03: "PICO_NOT_FOUND",
    0x07: "PICO_NOT_RESPONDING",
    0x0C: "PICO_INVALID_HANDLE",
    0x0D: "PICO_INVALID_PARAMETER",
    0x0E: "PICO_INVALID_TIMEBASE",
    0x0F: "PICO_INVALID_VOLTAGE_RANGE",
    0x10: "PICO_INVALID_CHANNEL",
    0x11: "PICO_INVALID_TRIGGER_CHANNEL",
    0x1B: "PICO_AUTO_TRIGGER_TIME_TOO_SHORT",
    0x1D: "PICO_TOO_MANY_SAMPLES",
    0x1E: "PICO_TOO_MANY_SEGMENTS",
    0x24: "PICO_DEVICE_SAMPLING",
    0x25: "PICO_NO_SAMPLES_AVAILABLE",
    0x26: "PICO_SEGMENT_OUT_OF_RANGE",
    0x27: "PICO_BUSY",
    0x2B: "PICO_INVALID_SAMPLE_INTERVAL",
    0x2C: "PICO_TRIGGER_ERROR",
    0x2D: "PICO_MEMORY",
    0x3B: "PICO_SEGMENT_NOT_USED",
    0x43: "PICO_DRIVER_FUNCTION",
    0x45: "PICO_INVALID_COUPLING",
    0x46: "PICO_BUFFERS_NOT_SET",
    0x50: "PICO_INVALID_ANALOGUE_OFFSET",
    0x11A: "PICO_POWER_SUPPLY_NOT_CONNECTED",
    0x11C: "PICO_POWER_SUPPLY_UNDERVOLTAGE",
    0x11F: "PICO_NOT_SUPPORTED_BY_THIS_DEVICE",
    0x120: "PICO_INVALID_DEVICE_RESOLUTION",
    0x129: "PICO_TOO_MANY_CHANNELS_IN_USE",
    0x12F: "PICO_ARGUMENT_OUT_OF_RANGE",
    0x143: "PICO_TIMEOUT",
    0x144: "PICO_DEVICE_NOT_FUNCTIONING",
    0x17C: "PICO_THRESHOLD_OUT_OF_RANGE",
    0x181: "PICO_TRIGGER_CHANNEL_NOT_ENABLED",
    0x185: "PICO_NO_OF_CAPTURES_OUT_OF_RANGE",
    0x192: "PICO_CAPTURES_EXCEEDS_NO_OF_SUPPORTED_SEGMENTS",
    0x300: "PICO_TRIGGER_DELAY_OUT_OF_RANGE",
}

_CHANNELS = {name: idx for idx, name in enumerate("ABCDEFGH")}

_COUPLING = {Coupling.AC: 0, Coupling.DC: 1, Coupling.DC_50OHM: 50}

# PICO_CONNECT_PROBE_RANGE for x1 probes: +-range in volts -> enum value.
_RANGES = {
    0.01: 0, 0.02: 1, 0.05: 2, 0.1: 3, 0.2: 4, 0.5: 5,
    1.0: 6, 2.0: 7, 5.0: 8, 10.0: 9, 20.0: 10,
}

_RESOLUTION = {8: 0, 10: 10, 12: 1}

PICO_RISING = 2
PICO_FALLING = 3

PICO_INT8_T = 0
PICO_INT16_T = 1

PICO_RATIO_MODE_RAW = 0x80000000
PICO_CLEAR_ALL = 0x00000001
PICO_ADD = 0x00000002

PICO_DRIVER_VERSION = 0
PICO_VARIANT_INFO = 3
PICO_BATCH_AND_SERIAL = 4

PICO_AUTO_TRIGGER = 2  # PICO_AUTO_TRIGGER_STATUS


def _status_name(code: int) -> str:
    return _STATUS_NAMES.get(code, f"0x{code:X}")


# --- library loading ------------------------------------------------------


def _candidate_paths() -> list[str]:
    name = "ps6000a"
    dirs = []
    if os.environ.get("PICOSDK_LIB_DIR"):
        dirs.append(os.environ["PICOSDK_LIB_DIR"])
    if sys.platform == "win32":
        filename = f"{name}.dll"
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(env):
                dirs.append(os.path.join(os.environ[env], "Pico Technology", "SDK", "lib"))
    elif sys.platform == "darwin":
        filename = f"lib{name}.dylib"
        dirs += [
            f"/Library/Frameworks/PicoSDK.framework/Libraries/lib{name}",
            "/Library/Frameworks/PicoSDK.framework/Libraries",
            "/Applications/PicoScope 7 T&M.app/Contents/Resources",
        ]
    else:
        filename = f"lib{name}.so"
        dirs += ["/opt/picoscope/lib"]
    paths = [os.path.join(d, filename) for d in dirs]
    found = ctypes.util.find_library(name)
    if found:
        paths.append(found)
    paths.append(filename)  # let the system loader search its default paths
    return paths


def _load_library() -> ctypes.CDLL:
    loader = ctypes.WinDLL if sys.platform == "win32" else ctypes.CDLL  # type: ignore[attr-defined]
    errors = []
    for path in _candidate_paths():
        if os.path.isabs(path) and not os.path.exists(path):
            continue
        if sys.platform == "win32" and os.path.isabs(path):
            os.add_dll_directory(os.path.dirname(path))
        try:
            lib = loader(path)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        _declare_functions(lib)
        return lib
    raise ScopeDriverError(
        "Could not load the PicoSDK ps6000a library. Install PicoSDK for your OS "
        "(https://www.picotech.com/downloads) or set PICOSDK_LIB_DIR.\n  " + "\n  ".join(errors)
    )


def _declare_functions(lib: ctypes.CDLL) -> None:
    p = c_void_p
    signatures = {
        "ps6000aEnumerateUnits": [p, c_char_p, p],
        "ps6000aOpenUnit": [p, c_char_p, c_uint32],
        "ps6000aCloseUnit": [c_int16],
        "ps6000aGetUnitInfo": [c_int16, c_char_p, c_int16, p, c_uint32],
        "ps6000aSetChannelOn": [c_int16, c_uint32, c_uint32, c_uint32, c_double, c_uint32],
        "ps6000aSetChannelOff": [c_int16, c_uint32],
        "ps6000aGetAdcLimits": [c_int16, c_uint32, p, p],
        "ps6000aNearestSampleIntervalStateless": [c_int16, c_uint32, c_double, c_uint32, p, p],
        "ps6000aGetTimebase": [c_int16, c_uint32, c_uint64, p, p, c_uint64],
        "ps6000aQueryMaxSegmentsBySamples": [c_int16, c_uint64, c_uint32, p, c_uint32],
        "ps6000aSetSimpleTrigger": [c_int16, c_int16, c_uint32, c_int16, c_uint32, c_uint64, c_uint32],
        "ps6000aMemorySegments": [c_int16, c_uint64, p],
        "ps6000aSetNoOfCaptures": [c_int16, c_uint64],
        "ps6000aRunBlock": [c_int16, c_uint64, c_uint64, c_uint32, p, c_uint64, p, p],
        "ps6000aIsReady": [c_int16, p],
        "ps6000aStop": [c_int16],
        "ps6000aGetNoOfCaptures": [c_int16, p],
        "ps6000aSetDataBuffer": [c_int16, c_uint32, p, c_int32, c_uint32, c_uint64, c_uint32, c_uint32],
        "ps6000aGetValuesBulk": [c_int16, c_uint64, p, c_uint64, c_uint64, c_uint64, c_uint32, p],
    }
    for fname, argtypes in signatures.items():
        fn = getattr(lib, fname)
        fn.argtypes = argtypes
        fn.restype = c_uint32
    # Only in newer PicoSDK releases; used for auto-trigger detection if present.
    fn = getattr(lib, "ps6000aGetAutoTriggerStatus", None)
    if fn is not None:
        fn.argtypes = [c_int16, p, c_uint64, c_uint64]
        fn.restype = c_uint32


_lib: ctypes.CDLL | None = None


def _get_lib() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        _lib = _load_library()
    return _lib


def _check(status: int, what: str) -> None:
    if status != PICO_OK:
        raise ScopeDriverError(f"{what} failed: {_status_name(status)}")


# --- backend --------------------------------------------------------------


class PicoScope6000E(Scope):
    backend_name = "picoscope6000e"

    # Samples on each side of the trigger point searched for the trigger edge
    # when auto-trigger detection falls back to inspecting the data.
    _EDGE_SEARCH_WINDOW = 8

    def __init__(self, lib: ctypes.CDLL, handle: int, resolution_bits: int) -> None:
        super().__init__()
        self._lib = lib
        self._handle = handle
        self._resolution = _RESOLUTION[resolution_bits]
        self._dtype = np.dtype(np.int8 if resolution_bits == 8 else np.int16)
        self._device = DeviceInfo(
            self.backend_name,
            serial=self._unit_info(PICO_BATCH_AND_SERIAL),
            model=self._unit_info(PICO_VARIANT_INFO),
        )
        self._timebase = 0
        self._pre_samples = 0
        self._post_samples = 0
        self._channel_cfg: dict[str, ChannelConfig] = {}
        self._trigger: TriggerConfig | None = None
        self._trigger_threshold_adc16 = 0
        self._adc_max16 = 0
        self._segments_allocated = 0
        self._armed_segments = 0
        self._buffers_key: tuple | None = None
        self._arm_time = 0.0
        self._ready_time: float | None = None

    # --- discovery / connection ----------------------------------------
    @classmethod
    def list_devices(cls) -> list[DeviceInfo]:
        lib = _get_lib()
        count = c_int16(0)
        serials = ctypes.create_string_buffer(1024)
        length = c_int16(len(serials))
        status = lib.ps6000aEnumerateUnits(byref(count), serials, byref(length))
        if status == 0x03:  # PICO_NOT_FOUND
            return []
        _check(status, "ps6000aEnumerateUnits")
        text = serials.value.decode(errors="replace")
        return [DeviceInfo(cls.backend_name, s.strip()) for s in text.split(",") if s.strip()]

    @classmethod
    def open(cls, serial: str | None = None, resolution_bits: int = 8) -> "PicoScope6000E":
        if resolution_bits not in _RESOLUTION:
            raise ValueError(f"resolution_bits must be one of {sorted(_RESOLUTION)}")
        lib = _get_lib()
        handle = c_int16(0)
        status = lib.ps6000aOpenUnit(
            byref(handle), serial.encode() if serial else None, _RESOLUTION[resolution_bits]
        )
        if status == PICO_USB3_0_DEVICE_NON_USB3_0_PORT:
            warnings.warn("PicoScope is connected to a non-USB3 port; transfers will be slow")
        elif status == 0x03:
            raise ScopeNotFoundError(f"No PicoScope 6000E found{f' with serial {serial}' if serial else ''}")
        elif status != PICO_OK:
            if handle.value > 0:
                lib.ps6000aCloseUnit(handle)
            _check(status, "ps6000aOpenUnit")
        if handle.value <= 0:
            raise ScopeNotFoundError("ps6000aOpenUnit returned no valid handle")
        return cls(lib, handle.value, resolution_bits)

    def close(self) -> None:
        if self._handle > 0:
            self._lib.ps6000aStop(self._handle)
            self._lib.ps6000aCloseUnit(self._handle)
            self._handle = 0

    @property
    def device_info(self) -> DeviceInfo:
        return self._device

    def _unit_info(self, info: int) -> str:
        buf = ctypes.create_string_buffer(64)
        required = c_int16(0)
        status = self._lib.ps6000aGetUnitInfo(self._handle, buf, len(buf), byref(required), info)
        return buf.value.decode(errors="replace") if status == PICO_OK else ""

    # --- configuration -------------------------------------------------
    def configure(
        self,
        channels: Sequence[ChannelConfig],
        acquisition: AcquisitionConfig,
        trigger: TriggerConfig | None = None,
    ) -> AcquisitionInfo:
        if not channels:
            raise ValueError("At least one channel must be enabled")
        names = [c.name.upper() for c in channels]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate channels in {names}")
        for name in names:
            if name not in _CHANNELS:
                raise ValueError(f"Unknown channel {name!r}; expected one of {''.join(_CHANNELS)}")
        if trigger is not None and trigger.source.upper() not in names:
            raise ValueError(f"Trigger source {trigger.source!r} must be one of the enabled channels {names}")
        if acquisition.n_samples <= 0:
            raise ValueError("n_samples must be positive")

        lib, h = self._lib, self._handle
        lib.ps6000aStop(h)
        self._buffers_key = None
        self._armed_segments = 0

        adc_min16, adc_max16 = c_int16(0), c_int16(0)
        _check(lib.ps6000aGetAdcLimits(h, self._resolution, byref(adc_min16), byref(adc_max16)), "ps6000aGetAdcLimits")
        self._adc_max16 = adc_max16.value
        # 8-bit data is transferred as int8, i.e. the top byte of the 16-bit ADC value.
        shift = 8 if self._dtype == np.int8 else 0
        adc_min, adc_max = adc_min16.value >> shift, adc_max16.value >> shift

        # Channels: turn requested ones on, everything else off (frees bandwidth/memory).
        cfg_by_name: dict[str, ChannelConfig] = {}
        scaling: dict[str, ChannelScaling] = {}
        flags = 0
        for cfg in channels:
            name = cfg.name.upper()
            range_code = _range_code(cfg.range_v)
            bw =0 if cfg.bandwidth_limit_hz is None else int(round(cfg.bandwidth_limit_hz))
            _check(
                lib.ps6000aSetChannelOn(h, _CHANNELS[name], _COUPLING[cfg.coupling], range_code, cfg.offset_v, bw),
                f"ps6000aSetChannelOn({name}, range={cfg.range_v} V, {cfg.coupling.value}, offset={cfg.offset_v} V)",
            )
            cfg_by_name[name] = cfg
            flags |= 1 << _CHANNELS[name]
            scaling[name] = ChannelScaling(
                volts_per_count=cfg.range_v / adc_max,
                offset_v=-cfg.offset_v or 0.0,
                adc_min=adc_min,
                adc_max=adc_max,
            )
        for name, idx in _CHANNELS.items():
            if name not in cfg_by_name:
                lib.ps6000aSetChannelOff(h, idx)  # fails harmlessly for channels the model lacks

        # Timebase: nearest achievable sample interval for this channel set.
        timebase = c_uint32(0)
        interval_avail = c_double(0.0)
        _check(
            lib.ps6000aNearestSampleIntervalStateless(
                h, flags, 1.0 / acquisition.sample_rate_hz, self._resolution, byref(timebase), byref(interval_avail)
            ),
            f"ps6000aNearestSampleIntervalStateless({acquisition.sample_rate_hz:g} Sa/s)",
        )
        interval_ns = c_double(0.0)
        max_samples = c_uint64(0)
        _check(
            lib.ps6000aGetTimebase(h, timebase.value, acquisition.n_samples, byref(interval_ns), byref(max_samples), 0),
            f"ps6000aGetTimebase({timebase.value})",
        )
        dt = interval_ns.value * 1e-9
        if acquisition.n_samples > max_samples.value:
            raise ScopeError(f"n_samples={acquisition.n_samples} exceeds device maximum {max_samples.value}")

        # Delay: negative = pre-trigger samples, positive = trigger delay in sample periods.
        delay_samples = int(round(acquisition.delay_s / dt))
        pre = max(0, -delay_samples)
        if pre > acquisition.n_samples:
            raise ValueError(f"Pre-trigger of {pre} samples exceeds n_samples={acquisition.n_samples}")
        self._timebase = timebase.value
        self._pre_samples = pre
        self._post_samples = acquisition.n_samples - pre

        # Trigger.
        if trigger is None:
            _check(lib.ps6000aSetSimpleTrigger(h, 0, 0, 0, PICO_RISING, 0, 0), "ps6000aSetSimpleTrigger(disable)")
            self._trigger = None
        else:
            src = trigger.source.upper()
            src_cfg = cfg_by_name[src]
            threshold = int(round((trigger.level_v + src_cfg.offset_v) / src_cfg.range_v * self._adc_max16))
            if not adc_min16.value <= threshold <= adc_max16.value:
                raise ValueError(
                    f"Trigger level {trigger.level_v} V is outside channel {src} input window "
                    f"[{-src_cfg.range_v - src_cfg.offset_v:g}, {src_cfg.range_v - src_cfg.offset_v:g}] V"
                )
            auto_us = 0 if not trigger.auto_trigger_s else max(1, int(round(trigger.auto_trigger_s * 1e6)))
            direction = PICO_RISING if trigger.edge == Edge.RISING else PICO_FALLING
            _check(
                lib.ps6000aSetSimpleTrigger(h, 1, _CHANNELS[src], threshold, direction, max(0, delay_samples), auto_us),
                "ps6000aSetSimpleTrigger",
            )
            self._trigger = trigger
            self._trigger_threshold_adc16 = threshold

        self._channel_cfg = cfg_by_name
        self._info = AcquisitionInfo(
            sample_interval_s=dt,
            n_samples=acquisition.n_samples,
            delay_samples=-pre if pre else max(0, delay_samples),
            dtype=self._dtype,
            channels=tuple(cfg_by_name),
            scaling=scaling,
            max_segments=self._query_max_segments(acquisition.n_samples, len(cfg_by_name)),
        )
        return self._info

    def _query_max_segments(self, n_samples: int, n_channels: int) -> int:
        n = c_uint64(0)
        status = self._lib.ps6000aQueryMaxSegmentsBySamples(
            self._handle, n_samples, n_channels, byref(n), self._resolution
        )
        return int(n.value) if status == PICO_OK else 0

    # --- acquisition ---------------------------------------------------
    def arm(self, n_segments: int = 1) -> None:
        if n_segments < 1:
            raise ValueError("n_segments must be >= 1")
        lib, h = self._lib, self._handle
        self.info  # raises if not configured
        if n_segments != self._segments_allocated:
            max_samples = c_uint64(0)
            _check(lib.ps6000aMemorySegments(h, n_segments, byref(max_samples)), f"ps6000aMemorySegments({n_segments})")
            if self.info.n_samples > max_samples.value:
                raise ScopeError(
                    f"{n_segments} segments leave only {max_samples.value} samples per segment, "
                    f"need {self.info.n_samples}; reduce n_segments (max ~{self.info.max_segments})"
                )
            _check(lib.ps6000aSetNoOfCaptures(h, n_segments), f"ps6000aSetNoOfCaptures({n_segments})")
            self._segments_allocated = n_segments
            self._buffers_key = None
        indisposed_ms = c_double(0.0)
        self._ready_time = None
        self._arm_time = time.monotonic()
        _check(
            lib.ps6000aRunBlock(
                h, self._pre_samples, self._post_samples, self._timebase, byref(indisposed_ms), 0, None, None
            ),
            "ps6000aRunBlock",
        )
        self._armed_segments = n_segments

    def disarm(self) -> None:
        _check(self._lib.ps6000aStop(self._handle), "ps6000aStop")

    def is_ready(self) -> bool:
        ready = c_int16(0)
        _check(self._lib.ps6000aIsReady(self._handle, byref(ready)), "ps6000aIsReady")
        if ready.value and self._ready_time is None:
            self._ready_time = time.monotonic()
        return bool(ready.value)

    def fetch(self, out: Mapping[str, np.ndarray]) -> FetchResult:
        if not self._armed_segments:
            raise ScopeError("Nothing to fetch; call arm() first")
        lib, h = self._lib, self._handle
        info = self.info
        out = {ch.upper(): arr for ch, arr in out.items()}
        if not out:
            raise ValueError("No output buffers given")

        captured = c_uint64(0)
        _check(lib.ps6000aGetNoOfCaptures(h, byref(captured)), "ps6000aGetNoOfCaptures")
        n = min(int(captured.value), self._armed_segments)
        if n == 0:
            raise ScopeError("No segments were captured")

        for ch, arr in out.items():
            if ch not in self._channel_cfg:
                raise ValueError(f"Channel {ch} is not enabled")
            if arr.dtype != info.dtype or arr.ndim != 2 or arr.shape[1] != info.n_samples:
                raise ValueError(
                    f"Buffer for {ch} must be {info.dtype} with shape (>= {n}, {info.n_samples}); "
                    f"got {arr.dtype} {arr.shape}"
                )
            if arr.shape[0] < n:
                raise ValueError(f"Buffer for {ch} holds {arr.shape[0]} segments, {n} were captured")
            if not arr.flags.c_contiguous or not arr.flags.writeable:
                raise ValueError(f"Buffer for {ch} must be C-contiguous and writeable")

        self._register_buffers(out, n)

        n_samples = c_uint64(info.n_samples)
        overflow = np.zeros(n, dtype=np.int16)
        _check(
            lib.ps6000aGetValuesBulk(
                h, 0, byref(n_samples), 0, n - 1, 1, PICO_RATIO_MODE_RAW, overflow.ctypes.data_as(c_void_p)
            ),
            "ps6000aGetValuesBulk",
        )
        if n_samples.value != info.n_samples:
            raise ScopeError(f"Driver returned {n_samples.value} samples per segment, expected {info.n_samples}")

        auto, method = self._detect_auto_trigger(out, n)
        overrange = {ch: (overflow & (1 << _CHANNELS[ch])) != 0 for ch in out}
        return FetchResult(n_segments=n, auto_triggered=auto, auto_trigger_detection=method, overrange=overrange)

    def _register_buffers(self, out: Mapping[str, np.ndarray], n: int) -> None:
        """Hand segment row pointers to the driver; skipped when unchanged since last fetch."""
        key = (n, tuple((ch, arr.ctypes.data, arr.strides[0]) for ch, arr in out.items()))
        if key == self._buffers_key:
            return
        lib, h = self._lib, self._handle
        data_type = PICO_INT8_T if self._dtype == np.int8 else PICO_INT16_T
        n_samples = self.info.n_samples
        action = PICO_CLEAR_ALL | PICO_ADD
        for ch, arr in out.items():
            base, stride, idx = arr.ctypes.data, arr.strides[0], _CHANNELS[ch]
            for seg in range(n):
                _check(
                    lib.ps6000aSetDataBuffer(
                        h, idx, c_void_p(base + seg * stride), n_samples, data_type, seg, PICO_RATIO_MODE_RAW, action
                    ),
                    f"ps6000aSetDataBuffer({ch}, segment {seg})",
                )
                action = PICO_ADD
        self._buffers_key = key

    def _detect_auto_trigger(self, out: Mapping[str, np.ndarray], n: int) -> tuple[np.ndarray, str]:
        """Return per-segment auto-trigger flags and the method used to determine them.

        1. "driver": ps6000aGetAutoTriggerStatus, if this PicoSDK provides it.
        2. "data":   the trigger edge must appear around the trigger point in the
                     trigger channel's data. Needs pre-trigger samples (delay_s < 0)
                     and the trigger channel among the fetched buffers.
        3. "timing": single-segment captures that took at least the auto-trigger
                     timeout are flagged.
        """
        trig = self._trigger
        if trig is None or not trig.auto_trigger_s:
            return np.zeros(n, dtype=bool), "none"

        fn = getattr(self._lib, "ps6000aGetAutoTriggerStatus", None)
        if fn is not None:
            status = np.zeros(n, dtype=np.int32)
            if fn(self._handle, status.ctypes.data_as(c_void_p), 0, n) == PICO_OK:
                return status == PICO_AUTO_TRIGGER, "driver"

        src = trig.source.upper()
        if src in out and self._pre_samples >= 2:
            w = self._EDGE_SEARCH_WINDOW
            lo = max(0, self._pre_samples - w)
            hi = min(self.info.n_samples, self._pre_samples + w)
            window = out[src][:n, lo:hi].astype(np.int32)
            threshold = self._trigger_threshold_adc16
            if self._dtype == np.int8:
                threshold >>= 8
            before, after = window[:, :-1], window[:, 1:]
            if trig.edge == Edge.RISING:
                crossed = (before < threshold) & (after >= threshold)
            else:
                crossed = (before > threshold) & (after <= threshold)
            return ~crossed.any(axis=1), "data"

        if n == 1 and self._ready_time is not None:
            elapsed = self._ready_time - self._arm_time
            return np.array([elapsed >= trig.auto_trigger_s]), "timing"

        warnings.warn(
            "Cannot detect auto-triggered segments: driver lacks ps6000aGetAutoTriggerStatus; "
            "use a negative delay_s (pre-trigger samples) and fetch the trigger channel"
        )
        return np.zeros(n, dtype=bool), "none"


def _range_code(range_v: float) -> int:
    for value, code in _RANGES.items():
        if abs(range_v - value) <= 1e-9 * max(1.0, value):
            return code
    raise ValueError(f"Unsupported range {range_v} V; choose one of {sorted(_RANGES)} (+-V)")
