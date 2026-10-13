"""Oscilloscope abstraction layer with pluggable vendor backends.

Backends are imported lazily so that a missing vendor SDK only matters when
that backend is actually used.
"""

from __future__ import annotations

import importlib

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

BACKENDS = {
    "picoscope6000e": "scopes.picoscope6000e:PicoScope6000E",
}


def get_backend(name: str) -> type[Scope]:
    try:
        target = BACKENDS[name]
    except KeyError:
        raise ValueError(f"Unknown backend {name!r}; available: {', '.join(BACKENDS)}") from None
    module_name, class_name = target.split(":")
    return getattr(importlib.import_module(module_name), class_name)


__all__ = [
    "AcquisitionConfig",
    "AcquisitionInfo",
    "BACKENDS",
    "ChannelConfig",
    "ChannelScaling",
    "Coupling",
    "DeviceInfo",
    "Edge",
    "FetchResult",
    "Scope",
    "ScopeDriverError",
    "ScopeError",
    "ScopeNotFoundError",
    "TriggerConfig",
    "get_backend",
]
