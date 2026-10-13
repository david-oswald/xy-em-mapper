"""Mapping run: move to each grid point and capture traces there."""

from __future__ import annotations

import time
from contextlib import ExitStack
from pathlib import Path
from typing import Callable

import numpy as np

from cnc import CNCConfig, CNCController
from scopes import FetchResult, Scope, ScopeNoDataError, get_backend

from .config import MeasurementConfig, Point, StageSettings
from .plugins import load_plugin
from .storage import TraceFile
from .strategies import STRATEGIES, Plan, Strategy
from .targets import TARGETS, Target


def run(
    cfg: MeasurementConfig,
    raw_config: dict,
    output: Path,
    resume: bool = False,
    log: Callable[[str], None] = print,
) -> None:
    strategy: Strategy = load_plugin(cfg.strategy, STRATEGIES, Strategy, cfg.base_dir)
    points = cfg.grid.points()
    n = cfg.traces_per_point

    with ExitStack() as stack:
        target: Target = load_plugin(cfg.target, TARGETS, Target, cfg.base_dir)
        stack.callback(target.close)

        backend = get_backend(cfg.scope.backend)
        scope: Scope = stack.enter_context(backend.open(cfg.scope.serial))
        info = scope.configure(cfg.scope.channels, cfg.scope.acquisition, cfg.scope.trigger)
        log(f"Scope {scope.device_info}: {info.sample_rate_hz / 1e6:.3f} MSa/s, {info.n_samples} samples, "
            f"channels {', '.join(info.channels)}")

        batch = min(n, cfg.scope.segments_per_arm or n)
        if info.max_segments and batch > info.max_segments:
            log(f"Note: {batch} segments per arm exceed scope memory; using {info.max_segments}")
            batch = info.max_segments

        stage = None
        if cfg.stage is not None:
            stage = stack.enter_context(CNCController(CNCConfig(cfg.stage.port, cfg.stage.baudrate, cfg.stage.timeout)))
            stack.callback(_park, stage, cfg.stage)
        else:
            log("No stage configured: positions are recorded but the probe is not moved")

        if resume:
            store = TraceFile.open_for_resume(output, raw_config)
            log(f"Resuming {output}: {store.n_done}/{len(points)} points already done")
        else:
            store = TraceFile.create(
                output,
                config=raw_config,
                points=points,
                traces_per_point=n,
                scope_meta={"device": str(scope.device_info), **info.metadata()},
                dtype=info.dtype,
                channels=info.channels,
                n_samples=info.n_samples,
                plaintext_bytes=strategy.plaintext_bytes,
                label_names=strategy.label_names,
                strategy_meta={"name": cfg.strategy.name, **strategy.metadata()},
                target_meta={"name": cfg.target.name, **target.metadata()},
                compression=cfg.compression,
            )
        stack.enter_context(store)

        buffers = scope.allocate(batch)
        t_run = time.monotonic()
        for point in points:
            if store.is_done(point.index):
                continue
            t0 = time.monotonic()
            if stage is not None:
                _move(stage, cfg.stage, point)
            plan = strategy.plan(point, n)
            _check_plan(plan, n, strategy.plaintext_bytes)
            n_auto = _capture_point(scope, target, cfg, point, plan, buffers, store)
            dt = time.monotonic() - t0
            log(f"[{point.index + 1}/{len(points)}] x={point.x:g} y={point.y:g}: {n} traces in {dt:.2f} s "
                f"({n / dt:.0f} tr/s)" + (f", {n_auto} still auto-triggered" if n_auto else ""))
        log(f"Done in {time.monotonic() - t_run:.1f} s -> {output}")


def _capture_point(
    scope: Scope,
    target: Target,
    cfg: MeasurementConfig,
    point: Point,
    plan: Plan,
    buffers: dict[str, np.ndarray],
    store: TraceFile,
) -> int:
    n = len(plan)
    plaintexts = plan.plaintexts.copy()
    batch = next(iter(buffers.values())).shape[0]
    n_auto = 0

    target.start(point, plan)
    for offset in range(0, n, batch):
        k = min(batch, n - offset)
        view = {ch: arr[:k] for ch, arr in buffers.items()}
        indices = np.arange(offset, offset + k)
        overrange = {ch: np.zeros(k, dtype=bool) for ch in buffers}
        bad = _acquire(scope, target, cfg, plan, indices, view, overrange, plaintexts)

        for _ in range(cfg.scope.max_retries):
            if not bad.any():
                break
            redo = np.flatnonzero(bad)
            retry_buf = scope.allocate(len(redo))
            retry_over = {ch: np.zeros(len(redo), dtype=bool) for ch in buffers}
            still_bad = _acquire(scope, target, cfg, plan, indices[redo], retry_buf, retry_over, plaintexts)
            ok = redo[~still_bad]
            for ch in buffers:
                view[ch][ok] = retry_buf[ch][~still_bad]
                overrange[ch][ok] = retry_over[ch][~still_bad]
            bad[ok] = False

        store.write_traces(point.index, offset, view, overrange, bad)
        n_auto += int(bad.sum())

    readback = target.end(point, plan)
    if readback is not None:
        readback = np.asarray(readback, dtype=np.uint8)
        if readback.shape != plaintexts.shape:
            raise ValueError(f"Target.end returned plaintexts of shape {readback.shape}, expected {plaintexts.shape}")
        plaintexts = readback
    store.finish_point(point.index, plaintexts, plan.labels)
    return n_auto


def _acquire(
    scope: Scope,
    target: Target,
    cfg: MeasurementConfig,
    plan: Plan,
    indices: np.ndarray,
    out: dict[str, np.ndarray],
    overrange: dict[str, np.ndarray],
    plaintexts: np.ndarray,
) -> np.ndarray:
    """Capture len(indices) traces into `out`. Returns a mask of traces to redo
    (auto-triggered, or missing after a timeout)."""
    k = len(indices)
    scope.arm(k)
    for i in indices:
        used = target.next(int(i), plan)
        if used is not None:
            if len(used) != plaintexts.shape[1]:
                raise ValueError(f"Target.next returned {len(used)} plaintext bytes, expected {plaintexts.shape[1]}")
            plaintexts[i] = np.frombuffer(bytes(used), dtype=np.uint8)
    try:
        scope.wait(cfg.scope.timeout_s)
    except TimeoutError:
        scope.disarm()
    bad = np.ones(k, dtype=bool)
    try:
        result: FetchResult = scope.fetch(out)
    except ScopeNoDataError:
        return bad
    m = result.n_segments
    bad[:m] = result.auto_triggered[:m]
    for ch in overrange:
        overrange[ch][:m] = result.overrange[ch][:m]
    return bad


def _check_plan(plan: Plan, n: int, plaintext_bytes: int) -> None:
    if plan.plaintexts.shape != (n, plaintext_bytes) or plan.plaintexts.dtype != np.uint8:
        raise ValueError(f"Strategy plan has plaintexts {plan.plaintexts.dtype} {plan.plaintexts.shape}, "
                         f"expected uint8 {(n, plaintext_bytes)}")
    if plan.labels.shape != (n,):
        raise ValueError(f"Strategy plan has labels of shape {plan.labels.shape}, expected {(n,)}")


def _move(stage: CNCController, s: StageSettings, point: Point) -> None:
    if s.z_safe is not None:
        stage.move_to(z=s.z_safe, feed_rate=s.feed_mm_min)
    stage.move_to(x=point.x, y=point.y, feed_rate=s.feed_mm_min)
    stage.move_to(z=point.z, feed_rate=s.feed_mm_min)
    stage.wait_until_idle(timeout_s=s.move_timeout_s)
    if s.settle_s:
        time.sleep(s.settle_s)


def _park(stage: CNCController, s: StageSettings) -> None:
    if s.z_safe is not None:
        stage.move_to(z=s.z_safe, feed_rate=s.feed_mm_min)
        stage.wait_until_idle(timeout_s=s.move_timeout_s)
