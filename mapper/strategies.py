"""Measurement strategies: which inputs to send at a point, and how to label them.

A strategy returns a `Plan` for each grid point: one plaintext and one class
label per trace, in capture order. To add your own, subclass `Strategy` and
reference it from the config as "my_module:MyStrategy" or
"my_file.py:MyStrategy"; extra config keys are passed to its constructor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from .config import Point


@dataclass
class Plan:
    plaintexts: np.ndarray  # (n_traces, plaintext_bytes) uint8, capture order
    labels: np.ndarray  # (n_traces,) uint8, index into Strategy.label_names

    def __len__(self) -> int:
        return len(self.labels)


class Strategy(ABC):
    label_names: tuple[str, ...] = ()
    plaintext_bytes: int = 16

    @abstractmethod
    def plan(self, point: Point, n_traces: int) -> Plan:
        """Inputs for the `n_traces` traces captured at `point`."""

    def metadata(self) -> dict:
        """Stored in the output file; describe the parameters needed to interpret the data."""
        return {}


class FixedVsRandom(Strategy):
    """Fixed-vs-random (TVLA-style): half the traces use one fixed plaintext, half random ones.

    order: "random"    - fixed and random traces are shuffled (default, avoids periodic artefacts)
           "alternate" - fixed, random, fixed, random, ...
    seed:  makes plaintexts and order reproducible (per point); null = fresh randomness.
    """

    label_names = ("fixed", "random")

    def __init__(self, fixed_plaintext: str, order: str = "random", seed: int | None = None) -> None:
        try:
            self.fixed = np.frombuffer(bytes.fromhex(fixed_plaintext), dtype=np.uint8)
        except ValueError:
            raise ValueError(f"fixed_plaintext must be a hex string, got {fixed_plaintext!r}") from None
        if not len(self.fixed):
            raise ValueError("fixed_plaintext must not be empty")
        if order not in ("random", "alternate"):
            raise ValueError("order must be \"random\" or \"alternate\"")
        self.plaintext_bytes = len(self.fixed)
        self.order = order
        self.seed = seed

    def plan(self, point: Point, n_traces: int) -> Plan:
        rng = np.random.default_rng(None if self.seed is None else [self.seed, point.index])
        if self.order == "alternate":
            labels = (np.arange(n_traces) % 2).astype(np.uint8)
        else:
            labels = np.zeros(n_traces, dtype=np.uint8)
            labels[n_traces // 2:] = 1
            rng.shuffle(labels)
        plaintexts = np.empty((n_traces, self.plaintext_bytes), dtype=np.uint8)
        plaintexts[labels == 0] = self.fixed
        n_random = int((labels == 1).sum())
        plaintexts[labels == 1] = rng.integers(0, 256, (n_random, self.plaintext_bytes), dtype=np.uint8)
        return Plan(plaintexts, labels)

    def metadata(self) -> dict:
        return {"fixed_plaintext": self.fixed.tobytes().hex(), "order": self.order, "seed": self.seed}


STRATEGIES: dict[str, type[Strategy]] = {
    "fixed_vs_random": FixedVsRandom,
}
