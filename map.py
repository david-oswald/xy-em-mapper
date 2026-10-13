"""Trace a square toolpath with a CNC controller.

Example:
  python map.py --port /dev/ttyUSB0 --side-mm 40 --steps 10
"""

from __future__ import annotations

import argparse

from cnc import CNCConfig, CNCController, CNCError


def interpolate_edge(start_x: float, start_y: float, end_x: float, end_y: float, steps: int):
    """Yield `steps` equally spaced points from start to end (inclusive of end)."""
    dx = (end_x - start_x) / steps
    dy = (end_y - start_y) / steps
    for i in range(1, steps + 1):
        yield start_x + dx * i, start_y + dy * i


def trace_square(
    controller: CNCController,
    x0: float,
    y0: float,
    side_mm: float,
    steps: int,
    z_work: float,
    z_safe: float,
    feed_mm_min: float,
    rapid_trace: bool,
) -> None:
    """Trace a square with `steps` line segments per edge."""
    corners = [
        (x0, y0),
        (x0 + side_mm, y0),
        (x0 + side_mm, y0 + side_mm),
        (x0, y0 + side_mm),
        (x0, y0),
    ]

    controller.rapid_to(z=z_safe)
    controller.rapid_to(x=x0, y=y0)
    controller.move_to(z=z_work, feed_rate=feed_mm_min)

    for idx in range(4):
        sx, sy = corners[idx]
        ex, ey = corners[idx + 1]
        for x, y in interpolate_edge(sx, sy, ex, ey, steps):
            if rapid_trace:
                controller.rapid_to(x=x, y=y)
            else:
                controller.move_to(x=x, y=y, feed_rate=feed_mm_min)

    controller.rapid_to(z=z_safe)
    controller.wait_until_idle(timeout_s=60.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trace a square path with a serial CNC controller")
    parser.add_argument("--port", required=True, help="Serial port, for example /dev/ttyUSB0")
    parser.add_argument("--baudrate", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--timeout", type=float, default=1.0, help="Serial timeout in seconds")
    parser.add_argument("--x0", type=float, default=0.0, help="Square start X coordinate (mm)")
    parser.add_argument("--y0", type=float, default=0.0, help="Square start Y coordinate (mm)")
    parser.add_argument("--side-mm", type=float, default=20.0, help="Square side length (mm)")
    parser.add_argument("--steps", type=int, default=5, help="Number of segments per edge")
    parser.add_argument("--z-work", type=float, default=0.0, help="Working Z height while tracing")
    parser.add_argument("--z-safe", type=float, default=5.0, help="Safe Z height for travel")
    parser.add_argument("--feed", type=float, default=600.0, help="Feed rate in mm/min")
    parser.add_argument(
        "--rapid-trace",
        action="store_true",
        help="Use G0 rapid moves for square edges instead of G1 feed moves",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.steps <= 0:
        raise ValueError("--steps must be >= 1")
    if args.side_mm <= 0:
        raise ValueError("--side-mm must be > 0")

    config = CNCConfig(port=args.port, baudrate=args.baudrate, timeout=args.timeout)

    try:
        with CNCController(config) as cnc:
            trace_square(
                controller=cnc,
                x0=args.x0,
                y0=args.y0,
                side_mm=args.side_mm,
                steps=args.steps,
                z_work=args.z_work,
                z_safe=args.z_safe,
                feed_mm_min=args.feed,
                rapid_trace=args.rapid_trace,
            )
        print("Square trace complete.")
        return 0
    except CNCError as exc:
        print(f"CNC error: {exc}")
        return 2
    except ValueError as exc:
        print(f"Invalid argument: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
