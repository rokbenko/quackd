"""Several robots cooperating on one task: a team of pilots, talking over one bus.

One whole `AgentLoop` per body (`pilots.py`, `talk.py`), on wall-clock time, on any adapter and
backend including mixed ones, with no referee at all. They divide the work by talking to each
other, over the bus, with one transcript for the team (ADR-0034).

The bus is a protocol rather than an implementation, so a LAN bus can slot in for it
(`mqtt_bus.py`), and nobody ever awaits it.
"""

from roboglot.team.pilots import PilotTeamResult, run_pilot_team

__all__ = ["PilotTeamResult", "run_pilot_team"]
