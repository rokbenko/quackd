"""Decision LLMs: the turns that are a choice, answered by something that writes nothing.

quackd's loop asks one question a turn -- which single tool call now -- and pays a frontier
model's full latency for it whether the answer is `report_state` or a six-joint pose. Some of
those turns are not writing, they are choosing, and a *decision LLM* answers a choice without
generating anything: typed questions against a named state, back as a value and a probability
distribution.

TypeSafe's Jev was the first and gave the format its name; there are many now, most of them
open and several small enough to run on the machine you are reading this on. What most of them
share is a wire format rather than a vendor, which is why `catalogue.py` is a table of servers
and `systemone.py` is one client for all of them. OpenAI's Decisions API is the one that asks
the same questions in a shape of its own, so it has a client of its own too.

The pieces:

- `catalogue.py` -- which decision LLMs exist, as data, importing none of them.
- `factory.py` -- the flags and variables, to something that answers.
- `systemone.py` -- every server that speaks `POST /v1/systemone`.
- `decisions_api.py` -- OpenAI's `POST /v1/decisions`, translated both ways.
- `frames.py` -- what the one decision LLM that may see is shown of the camera, on a run that
  asks with `--decision-frames`, and the one gate every frame passes.
- `eyes.py` -- what the Microduck's `watch` verb looks through, built only through that gate,
  and the record of every frame it hands over (`quackd/verbs/watch.py` is the verb).
- `laya.py` -- the one that runs in this process instead.
- `stepper.py` -- which turns are a choice at all, and what an answer has to clear before it
  moves a servo. That is quackd's half and does not change with the vendor.

Off by default, off when the client is absent, off when a key is missing. Nothing here imports
`typesafe_sdk`, `openai` or `laya` at module scope: `quackd.agent.loop` imports this on every
run and must not pay for a vendor that is not in the run.
"""

from __future__ import annotations

from quackd.agent.decision.catalogue import ENTRY_POINT_GROUP as ENTRY_POINT_GROUP
from quackd.agent.decision.catalogue import PRESET_NAMES as PRESET_NAMES
from quackd.agent.decision.catalogue import PRESETS as PRESETS
from quackd.agent.decision.catalogue import DecisionMode as DecisionMode
from quackd.agent.decision.catalogue import DecisionSpec as DecisionSpec

__all__ = [
    "ENTRY_POINT_GROUP",
    "PRESETS",
    "PRESET_NAMES",
    "DecisionMode",
    "DecisionSpec",
]
