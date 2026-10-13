"""Capture side-channel traces on an XY grid, driven by a JSON measurement config.

Examples:
  python em-mapper.py examples/em-mapper.json --dry-run
  python em-mapper.py examples/em-mapper.json -o run1.h5
  python em-mapper.py examples/em-mapper.json -o run1.h5 --resume
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from cnc import CNCError
from mapper import ConfigError, load_config, run
from scopes import ScopeError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture traces on an XY grid")
    parser.add_argument("config", type=Path, help="JSON measurement config")
    parser.add_argument("-o", "--output", type=Path, help="Output .h5 file (overrides \"output\" in the config)")
    parser.add_argument("--resume", action="store_true", help="Continue an interrupted run in an existing output file")
    parser.add_argument("--dry-run", action="store_true", help="Validate the config and print the plan, then exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cfg, raw = load_config(args.config)
    except (OSError, ConfigError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    output = args.output or (Path(cfg.output) if cfg.output else None)
    points = cfg.grid.points()
    n_traces = len(points) * cfg.traces_per_point
    size_gb = n_traces * cfg.scope.acquisition.n_samples * len(cfg.scope.channels) / 1e9
    xs = sorted({p.x for p in points})
    ys = sorted({p.y for p in points})
    print(f"Grid: {len(xs)} x {len(ys)} = {len(points)} points ({cfg.grid.order}), z={cfg.grid.z:g}")
    print(f"Traces: {cfg.traces_per_point} per point, {n_traces} total, ~{size_gb:.2f} GB raw "
          f"(at 8-bit; 2x for 10/12-bit)")

    if args.dry_run:
        print(f"Strategy: {cfg.strategy.name} {cfg.strategy.params}")
        print(f"Target:   {cfg.target.name} {cfg.target.params}")
        print(f"Stage:    {cfg.stage.port if cfg.stage else 'none'}")
        print(f"Output:   {output}")
        return 0

    if output is None:
        print("No output file: pass -o/--output or set \"output\" in the config", file=sys.stderr)
        return 2
    if args.resume and not output.exists():
        print(f"{output} does not exist; nothing to resume", file=sys.stderr)
        return 2
    if not args.resume and output.exists():
        print(f"{output} already exists; use --resume to continue it or choose another file", file=sys.stderr)
        return 2

    try:
        run(cfg, raw, output, resume=args.resume)
    except KeyboardInterrupt:
        print(f"\nInterrupted. Completed points are saved; continue with --resume -o {output}", file=sys.stderr)
        return 130
    except (ScopeError, CNCError, ValueError, TypeError, ImportError, TimeoutError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if output.exists():
            print(f"Completed points are saved; continue with --resume -o {output}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
