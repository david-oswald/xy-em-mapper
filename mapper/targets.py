"""Hooks into the device under test.

At each grid point the mapper calls, in this order:

    target.start(point, plan)        once, before the scope is armed
    target.next(i, plan)             once per trace, in order, while the scope is
                                     armed for the batch that contains trace i
    target.end(point, plan)          once, after all traces of the point

`next` either makes the target produce one trigger (e.g. send plaintext i)
or does nothing, for firmware that produces all triggers from one command.
For that kind of firmware, send the command in `next` when `i == 0` and leave
`scope.segments_per_arm` unset, so that all traces of a point are armed at once.

`next` may return the plaintext actually used (bytes) if the target derives
it itself; `end` may return all of them at once as an (n, plaintext_bytes)
array, e.g. read back from the target. Otherwise the plan's plaintexts are
stored. Traces that were auto-triggered are re-captured by calling `next`
again for their indices.

To add your own, subclass `Target` and reference it from the config as
"my_module:MyTarget" or "my_file.py:MyTarget"; extra config keys are passed to
its constructor.
"""

from __future__ import annotations

import numpy as np

from .config import Point
from .strategies import Plan


class Target:
    """No-op target: the device triggers by itself and uses the planned plaintexts."""

    def start(self, point: Point, plan: Plan) -> None:
        pass

    def next(self, index: int, plan: Plan) -> bytes | None:
        return None

    def end(self, point: Point, plan: Plan) -> np.ndarray | None:
        return None

    def close(self) -> None:
        """Called once at the end of the run, also on errors."""

    def metadata(self) -> dict:
        """Stored in the output file."""
        return {}


TARGETS: dict[str, type[Target]] = {
    "none": Target,
}
