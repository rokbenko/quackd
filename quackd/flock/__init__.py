"""Several robots cooperating on one task. Two kinds of it, sharing one bus.

**The coordinator** (`runner.py`, `coordinator.py`, `member.py`, `auction.py`, `planner.py`)
is the 0.3 flock: several simulated Microducks in one arena on one lockstep clock, refereed
deterministically. It exists so that cooperation is *inspectable*: every message between ducks
travels over a tiny in-process bus and lands in a transcript, the kicker is chosen by an
auction rather than by LLM vibes, and the LLM contributes at most one planning call for the
whole run. Simulator only, and still is.

**The pilots** (`pilots.py`, `talk.py`) are the 0.9 flock: one whole `AgentLoop` per body, on
wall-clock time, on any adapter and backend including mixed ones, with no referee at all. They
divide the work by talking to each other, over the same bus, with the same transcript
(ADR-0034).

The bus is a protocol rather than an implementation, so a LAN bus can slot in for either
(`mqtt_bus.py`), and nobody ever awaits it.
"""

from quackd.flock.runner import FlockResult, run_flock

__all__ = ["FlockResult", "run_flock"]
