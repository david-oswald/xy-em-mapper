"""JSON measurement configuration for em-mapper.

See README.md ("EM mapping: em-mapper.py") for the full format. Unknown keys
are rejected so that typos don't silently fall back to defaults.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scopes import AcquisitionConfig, ChannelConfig, Coupling, Edge, TriggerConfig


class ConfigError(ValueError):
    """Raised for invalid or inconsistent measurement configuration."""


@dataclass(frozen=True)
class Point:
    index: int  # position in visiting order
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class ScopeSettings:
    backend: str
    channels: tuple[ChannelConfig, ...]
    acquisition: AcquisitionConfig
    trigger: TriggerConfig | None
    serial: str | None = None
    segments_per_arm: int | None = None  # None = all traces of a point in one arm
    timeout_s: float = 10.0
    max_retries: int = 3  # re-captures of auto-triggered / missing traces


@dataclass(frozen=True)
class StageSettings:
    port: str
    baudrate: int = 115200
    timeout: float = 1.0
    feed_mm_min: float = 600.0
    z_safe: float | None = None  # lift to this Z for XY moves; None = move at working Z
    settle_s: float = 0.1
    move_timeout_s: float = 60.0


@dataclass(frozen=True)
class GridSettings:
    x_start: float
    x_end: float
    x_step: float
    y_start: float
    y_end: float
    y_step: float
    z: float
    order: str = "serpentine"  # or "raster"

    def points(self) -> list[Point]:
        xs = _axis(self.x_start, self.x_end, self.x_step, "x")
        ys = _axis(self.y_start, self.y_end, self.y_step, "y")
        points = []
        for row, y in enumerate(ys):
            row_xs = xs[::-1] if self.order == "serpentine" and row % 2 else xs
            for x in row_xs:
                points.append(Point(len(points), x, y, self.z))
        return points


@dataclass(frozen=True)
class PluginSettings:
    """A strategy or target: registry name or "module:Class" / "file.py:Class", plus constructor params."""

    name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MeasurementConfig:
    scope: ScopeSettings
    grid: GridSettings
    traces_per_point: int
    strategy: PluginSettings
    target: PluginSettings
    stage: StageSettings | None
    output: str | None = None
    compression: str | None = None  # HDF5 filter for traces: None, "lzf" or "gzip"
    base_dir: Path = Path(".")  # directory of the config file; plugin files resolve against it


def load_config(path: str | Path) -> tuple[MeasurementConfig, dict]:
    """Parse a JSON config file. Returns the config and the raw JSON dict."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON: {exc}") from exc
    return parse_config(raw, base_dir=path.resolve().parent), raw


def parse_config(raw: dict, base_dir: Path = Path(".")) -> MeasurementConfig:
    d = _fields(
        raw,
        "config",
        required={"scope", "grid", "traces_per_point", "strategy"},
        optional={"target": {"name": "none"}, "stage": None, "output": None, "compression": None},
    )
    traces = d["traces_per_point"]
    if not isinstance(traces, int) or traces < 1:
        raise ConfigError("traces_per_point must be a positive integer")
    if d["compression"] not in (None, "lzf", "gzip"):
        raise ConfigError("compression must be null, \"lzf\" or \"gzip\"")
    return MeasurementConfig(
        scope=_parse_scope(d["scope"]),
        grid=_parse_grid(d["grid"]),
        traces_per_point=traces,
        strategy=_parse_plugin(d["strategy"], "strategy"),
        target=_parse_plugin(d["target"], "target"),
        stage=None if d["stage"] is None else StageSettings(**_fields(
            d["stage"], "stage", required={"port"},
            optional={k: getattr(StageSettings, k) for k in
                      ("baudrate", "timeout", "feed_mm_min", "z_safe", "settle_s", "move_timeout_s")},
        )),
        output=d["output"],
        compression=d["compression"],
        base_dir=base_dir,
    )


def _parse_scope(raw: dict) -> ScopeSettings:
    d = _fields(
        raw,
        "scope",
        required={"backend", "channels", "sample_rate_hz", "n_samples"},
        optional={"serial": None, "delay_s": 0.0, "trigger": None, "segments_per_arm": None,
                  "timeout_s": 10.0, "max_retries": 3},
    )
    if not d["channels"]:
        raise ConfigError("scope.channels must list at least one channel")
    channels = []
    for i, ch in enumerate(d["channels"]):
        c = _fields(ch, f"scope.channels[{i}]", required={"name", "range_v"},
                    optional={"coupling": "dc", "offset_v": 0.0, "bandwidth_limit_hz": None})
        channels.append(ChannelConfig(
            name=c["name"].upper(), range_v=float(c["range_v"]), coupling=_enum(Coupling, c["coupling"], "coupling"),
            offset_v=float(c["offset_v"]), bandwidth_limit_hz=c["bandwidth_limit_hz"],
        ))
    trigger = None
    if d["trigger"] is not None:
        t = _fields(d["trigger"], "scope.trigger", required={"source", "level_v"},
                    optional={"edge": "rising", "auto_trigger_s": None})
        trigger = TriggerConfig(t["source"].upper(), float(t["level_v"]), _enum(Edge, t["edge"], "edge"),
                                t["auto_trigger_s"])
    if d["segments_per_arm"] is not None and d["segments_per_arm"] < 1:
        raise ConfigError("scope.segments_per_arm must be >= 1 or null")
    return ScopeSettings(
        backend=d["backend"],
        channels=tuple(channels),
        acquisition=AcquisitionConfig(float(d["sample_rate_hz"]), int(d["n_samples"]), float(d["delay_s"])),
        trigger=trigger,
        serial=d["serial"],
        segments_per_arm=d["segments_per_arm"],
        timeout_s=float(d["timeout_s"]),
        max_retries=int(d["max_retries"]),
    )


def _parse_grid(raw: dict) -> GridSettings:
    d = _fields(raw, "grid",
                required={"x_start", "x_end", "x_step", "y_start", "y_end", "y_step", "z"},
                optional={"order": "serpentine"})
    if d["order"] not in ("serpentine", "raster"):
        raise ConfigError("grid.order must be \"serpentine\" or \"raster\"")
    grid = GridSettings(**{k: (v if k == "order" else float(v)) for k, v in d.items()})
    grid.points()  # validate axes
    return grid


def _parse_plugin(raw: dict | str, where: str) -> PluginSettings:
    if isinstance(raw, str):
        return PluginSettings(raw)
    if not isinstance(raw, dict) or "name" not in raw:
        raise ConfigError(f"{where} must be a name or an object with a \"name\" key")
    params = dict(raw)
    return PluginSettings(params.pop("name"), params)


def _axis(start: float, end: float, step: float, name: str) -> list[float]:
    if start == end:
        return [start]
    if step <= 0:
        raise ConfigError(f"grid.{name}_step must be > 0 when {name}_start != {name}_end")
    n = int(math.floor(abs(end - start) / step + 1e-9)) + 1
    sign = 1.0 if end > start else -1.0
    return [round(start + sign * step * i, 6) for i in range(n)]


def _enum(cls, value: str, what: str):
    try:
        return cls(value.lower())
    except ValueError:
        raise ConfigError(f"Invalid {what} {value!r}; expected one of {[e.value for e in cls]}") from None


def _fields(raw: Any, where: str, required: set[str], optional: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a JSON object")
    missing = required - raw.keys()
    if missing:
        raise ConfigError(f"{where}: missing {', '.join(sorted(missing))}")
    unknown = raw.keys() - required - optional.keys()
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    return {**optional, **raw}
