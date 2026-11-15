"""One paper trail for the whole team: every bus message and every member's verbs.

`team.jsonl` is the team's own log, stamped in wall seconds under `t`. Each member gets a real
solo-style `Transcript` under `robots/<name>/` for its
verb events and frames. Per-duck dirs deliberately carry no summary.json — they are not
solo runs and must not masquerade as ones; the rollup lives in the team summary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roboglot.agent.transcript import Transcript
from roboglot.team.messages import TeamMessage

if TYPE_CHECKING:
    from roboglot.log import LogEvent


class TeamTranscript:
    def __init__(self, run_dir: Path, now: Callable[[], float]) -> None:
        self.run_dir = run_dir
        self.now = now
        self._fh = (run_dir / "team.jsonl").open("a", encoding="utf-8")
        self._members: dict[str, Transcript] = {}
        self.events = 0

    def write(self, kind: str, **payload: Any) -> None:
        record = {"t": round(self.now(), 3), "kind": kind, **payload}
        self._fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.events += 1

    def sink(self, event: LogEvent) -> None:
        """`team.jsonl` as a `EventLog` record: the team's own line for what belongs to no
        single member, stamped like every other line in this file."""
        self.write(event.kind, **event.data)

    def on_bus(self, msg: TeamMessage) -> None:
        self.write("bus", msg=msg.model_dump())

    def member(self, name: str) -> Transcript:
        if name not in self._members:
            member_dir = self.run_dir / "robots" / name
            member_dir.mkdir(parents=True, exist_ok=True)
            self._members[name] = Transcript(member_dir)
        return self._members[name]

    def write_summary(self, summary: dict[str, Any]) -> Path:
        path = self.run_dir / "summary.json"
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        return path

    def close(self) -> None:
        self._fh.close()
        for t in self._members.values():
            t.close()
