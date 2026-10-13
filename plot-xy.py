"""Plot an em-mapper capture file as a heatmap over the XY grid.

Each grid point is reduced to one value by a metric (default: mean over all
traces and samples). Built-in metrics: mean, rms, ttest. Custom metrics:
--metric my_metrics.py:MyMetric (see mapper/metrics.py).

Examples:
  python plot-xy.py em-map.h5
  python plot-xy.py em-map.h5 --metric rms --channel A --samples 1000:3000 -o rms.png
  python plot-xy.py em-map.h5 --metric ttest --metric-arg group_a=fixed --metric-arg group_b=random
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import h5py
import numpy as np

from mapper.config import PluginSettings
from mapper.metrics import METRICS, PointMetric, iter_points
from mapper.plugins import load_plugin


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Heatmap of an em-mapper capture file")
    parser.add_argument("file", type=Path, help="HDF5 file written by em-mapper.py")
    parser.add_argument("--channel", help="Channel to evaluate (default: first recorded channel)")
    parser.add_argument("--metric", default="mean",
                        help=f"Point metric: {', '.join(METRICS)} or module:Class / file.py:Class (default: mean)")
    parser.add_argument("--metric-arg", action="append", default=[], metavar="KEY=VALUE",
                        help="Constructor argument for the metric (value parsed as JSON if possible); repeatable")
    parser.add_argument("--samples", default=":", metavar="START:STOP",
                        help="Sample window within each trace, e.g. 1000:3000 (default: all)")
    parser.add_argument("--cmap", default="viridis", help="Matplotlib colormap (default: viridis)")
    parser.add_argument("--vmin", type=float, help="Lower colour limit")
    parser.add_argument("--vmax", type=float, help="Upper colour limit")
    parser.add_argument("-o", "--output", type=Path, help="Save the plot (png, pdf, svg, ...) instead of showing it")
    parser.add_argument("--csv", type=Path, help="Also write x, y, value per point to this CSV file")
    return parser.parse_args()


def parse_window(text: str) -> tuple[int, int | None]:
    try:
        start, stop = text.split(":")
        return int(start) if start else 0, int(stop) if stop else None
    except ValueError:
        raise ValueError(f"--samples must look like START:STOP, got {text!r}") from None


def parse_metric_args(items: list[str]) -> dict:
    params = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"--metric-arg must be KEY=VALUE, got {item!r}")
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    return params


def grid_edges(values: np.ndarray) -> np.ndarray:
    """Cell edges around sorted unique coordinates (midpoints between neighbours)."""
    if len(values) == 1:
        return np.array([values[0] - 0.5, values[0] + 0.5])
    mid = (values[1:] + values[:-1]) / 2
    return np.concatenate([[2 * values[0] - mid[0]], mid, [2 * values[-1] - mid[-1]]])


def main() -> int:
    args = parse_args()
    try:
        window = parse_window(args.samples)
        metric: PointMetric = load_plugin(
            PluginSettings(args.metric, parse_metric_args(args.metric_arg)), METRICS, PointMetric, Path.cwd()
        )
    except (ValueError, TypeError, ImportError, AttributeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    with h5py.File(args.file, "r") as f:
        channels = [str(c) for c in f.attrs["channels"]]
        channel = (args.channel or channels[0]).upper()
        if channel not in channels:
            print(f"Channel {channel} not in file; recorded channels: {', '.join(channels)}", file=sys.stderr)
            return 2
        xs = np.unique(f["points/x"][:])
        ys = np.unique(f["points/y"][:])
        n_points = len(f["points/done"])
        grid = np.full((len(ys), len(xs)), np.nan)
        rows = []
        try:
            for data in iter_points(f, [channel], window):
                value = metric(data, channel)
                grid[np.searchsorted(ys, data.y), np.searchsorted(xs, data.x)] = value
                rows.append((data.x, data.y, value, len(data.labels)))
                print(f"\r{len(rows)} points", end="", file=sys.stderr, flush=True)
        except ValueError as exc:
            print(f"\nError: {exc}", file=sys.stderr)
            return 2
        print(file=sys.stderr)
        scope = json.loads(f.attrs["scope"])
        sample_interval, delay = scope["sample_interval_s"], scope["delay_samples"]
        n_samples = f[f"traces/{channel}"].shape[1]

    if not rows:
        print("No completed points in file", file=sys.stderr)
        return 1
    if args.csv:
        with args.csv.open("w") as out:
            out.write("x_mm,y_mm,value,n_traces\n")
            out.writelines(f"{x},{y},{v},{n}\n" for x, y, v, n in rows)

    import matplotlib
    if args.output:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    start, stop = window[0], n_samples if window[1] is None else min(window[1], n_samples)
    fig, ax = plt.subplots(figsize=(7, 6))
    mesh = ax.pcolormesh(grid_edges(xs), grid_edges(ys), np.ma.masked_invalid(grid),
                         cmap=args.cmap, vmin=args.vmin, vmax=args.vmax, shading="flat")
    ax.set_aspect("equal")
    ax.set_xlabel("x [mm]")
    ax.set_ylabel("y [mm]")
    title = metric.title or type(metric).__name__
    ax.set_title(
        f"{args.file.name}: {title}, channel {channel}\n"
        f"samples {start}:{stop} ({(start + delay) * sample_interval * 1e9:.0f} to "
        f"{(stop + delay) * sample_interval * 1e9:.0f} ns after trigger), "
        f"{len(rows)}/{n_points} points",
        fontsize=10,
    )
    fig.colorbar(mesh, ax=ax, label=metric.unit)
    fig.tight_layout()

    if args.output:
        fig.savefig(args.output, dpi=150)
        print(f"Saved {args.output}")
    else:
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
