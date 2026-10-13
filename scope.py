"""List oscilloscopes for a backend and optionally record a trace.

Examples:
  python scope.py picoscope6000e
  python scope.py picoscope6000e --record --channel A --range 0.2 --rate 1.25e9 --samples 20000
  python scope.py picoscope6000e --record --trigger-channel A --trigger-level 0.05 \\
      --auto-trigger 0.1 --delay -1e-6 --out trace.npy
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from scopes import (
    BACKENDS,
    AcquisitionConfig,
    ChannelConfig,
    Coupling,
    Edge,
    ScopeError,
    TriggerConfig,
    get_backend,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enumerate oscilloscopes and record a single trace")
    parser.add_argument("backend", choices=sorted(BACKENDS), help="Oscilloscope backend")
    parser.add_argument("--serial", help="Open this device instead of the first one found")
    parser.add_argument("--record", action="store_true", help="Record a trace on --channel")

    acq = parser.add_argument_group("acquisition")
    acq.add_argument("--channel", default="A", help="Input channel (default: A)")
    acq.add_argument("--range", type=float, default=0.1, help="Input range in +-V (default: 0.1)")
    acq.add_argument("--coupling", choices=[c.value for c in Coupling], default="dc", help="Input coupling")
    acq.add_argument("--offset", type=float, default=0.0, help="Analogue offset in V (default: 0)")
    acq.add_argument("--rate", type=float, default=1.25e9, help="Sample rate in Sa/s (default: 1.25e9)")
    acq.add_argument("--samples", type=int, default=10_000, help="Samples per trace (default: 10000)")
    acq.add_argument(
        "--delay", type=float, default=0.0,
        help="First sample relative to trigger in s; negative = pre-trigger (default: 0)",
    )
    acq.add_argument("--segments", type=int, default=1, help="Number of triggered segments (default: 1)")
    acq.add_argument("--timeout", type=float, default=10.0, help="Capture timeout in s (default: 10)")

    trg = parser.add_argument_group("trigger (free-running when --trigger-channel is not given)")
    trg.add_argument("--trigger-channel", help="Trigger source channel (also captured)")
    trg.add_argument("--trigger-level", type=float, default=0.0, help="Trigger level in V (default: 0)")
    trg.add_argument("--trigger-edge", choices=[e.value for e in Edge], default="rising")
    trg.add_argument("--trigger-range", type=float, help="Range of the trigger channel if it differs from --channel")
    trg.add_argument("--auto-trigger", type=float, help="Auto-trigger timeout in s (default: wait forever)")

    parser.add_argument("--out", type=Path, help="Save raw samples to this .npy file (+ .json metadata)")
    return parser.parse_args()


def record(scope, args: argparse.Namespace) -> int:
    channel = args.channel.upper()
    channels = [ChannelConfig(channel, args.range, Coupling(args.coupling), args.offset)]
    trigger = None
    if args.trigger_channel:
        trig_ch = args.trigger_channel.upper()
        if trig_ch != channel:
            channels.append(ChannelConfig(trig_ch, args.trigger_range or args.range, Coupling.DC))
        trigger = TriggerConfig(trig_ch, args.trigger_level, Edge(args.trigger_edge), args.auto_trigger)

    info = scope.configure(channels, AcquisitionConfig(args.rate, args.samples, args.delay), trigger)
    print(
        f"Configured: {info.sample_rate_hz / 1e6:.3f} MSa/s ({info.sample_interval_s * 1e9:.3f} ns), "
        f"{info.n_samples} samples, delay {info.delay_samples} samples, dtype {np.dtype(info.dtype).name}, "
        f"max segments {info.max_segments}"
    )
    if abs(info.sample_rate_hz - args.rate) > 0.01 * args.rate:
        print(f"Note: requested {args.rate:g} Sa/s, got {info.sample_rate_hz:g} Sa/s")

    buffers = scope.allocate(args.segments)
    t0 = time.perf_counter()
    result = scope.capture(buffers, timeout_s=args.timeout)
    elapsed = time.perf_counter() - t0
    print(f"Captured {result.n_segments} segment(s) in {elapsed * 1e3:.1f} ms")

    if trigger is not None and trigger.auto_trigger_s:
        n_auto = int(result.auto_triggered.sum())
        print(f"Auto-triggered segments: {n_auto}/{result.n_segments} (detection: {result.auto_trigger_detection})")

    for ch, raw in buffers.items():
        volts = info.scaling[ch].to_volts(raw[: result.n_segments])
        over = int(result.overrange[ch].sum())
        print(
            f"  {ch}: raw [{raw.min()}, {raw.max()}]  volts min {volts.min():.4f} max {volts.max():.4f} "
            f"mean {volts.mean():.4f} std {volts.std():.4f}" + (f"  OVERRANGE in {over} segment(s)" if over else "")
        )

    if args.out:
        data = buffers[channel][: result.n_segments]
        np.save(args.out, data if args.segments > 1 else data[0])
        meta = {
            "device": str(scope.device_info),
            "channel": channel,
            **info.metadata(),
            "auto_triggered": result.auto_triggered.tolist(),
        }
        meta_path = args.out.with_suffix(".json")
        meta_path.write_text(json.dumps(meta, indent=2))
        print(f"Saved {args.out} and {meta_path}")
    return 0


def main() -> int:
    args = parse_args()
    try:
        backend = get_backend(args.backend)
        devices = backend.list_devices()
        if not devices:
            print(f"No {args.backend} devices found", file=sys.stderr)
            return 1
        print("Devices:")
        for dev in devices:
            print(f"  {dev}")
        serial = args.serial or devices[0].serial
        with backend.open(serial) as scope:
            print(f"Opened {scope.device_info}")
            if args.record:
                return record(scope, args)
    except (ScopeError, ValueError, TimeoutError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
