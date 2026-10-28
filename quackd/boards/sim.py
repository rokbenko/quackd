"""The sim board: a computer standing in for a robot's own, and saying so.

Asked for by name alone (`--board sim`): `boards.detect()` never returns it, so a laptop never
passes for a robot, and the passport it signs says `sim`, so no robot reading it is fooled either.
Its radio is the simulated air, which needs nothing of this computer.
"""

from __future__ import annotations

from quackd.boards import SIM, BoardFacts, RadioCheck

MODEL = "a computer standing in for a robot's own"


class SimBoard:
    name = SIM
    explicit_only = True
    radio_kind = "sim"

    def detect(self) -> BoardFacts:
        return BoardFacts(kind="sim", model=MODEL)

    def radio(self) -> RadioCheck:
        return RadioCheck(ok=True, adapter="sim")
