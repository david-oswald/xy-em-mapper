"""Loading of strategies, targets and point metrics from the config / command line."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from .config import PluginSettings


def load_plugin(settings: PluginSettings, registry: dict[str, type], base: type, base_dir: Path):
    """Instantiate a registry entry, "module:Class" or "path/to/file.py:Class"."""
    name = settings.name
    if name in registry:
        cls = registry[name]
    elif ":" in name:
        module_name, attr = name.rsplit(":", 1)
        if module_name.endswith(".py"):
            path = Path(module_name)
            if not path.is_absolute():
                path = base_dir / path
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[path.stem] = module
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(module_name)
        cls = getattr(module, attr)
    else:
        raise ValueError(f"Unknown {base.__name__.lower()} {name!r}; built-in: {', '.join(registry)}")
    if not (isinstance(cls, type) and issubclass(cls, base)):
        raise TypeError(f"{name} is not a {base.__name__} subclass")
    return cls(**settings.params)
