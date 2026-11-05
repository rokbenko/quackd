"""One paper trail for the whole flock: every bus message, plan, auction and verb.

`flock.jsonl` is the flock's own log. A coordinator stamps it in sim time (`sim_t`) so a
replay lines up with the world; a pilot flock has no world to line up with and stamps wall
seconds under `t`. Each member gets a real solo-style `Transcript` under `ducks/<name>/` for its
verb events and frames. Per-duck dirs deliberately carry no summary.json — they are not
solo runs and must not masquerade as ones; the rollup lives in the flock summary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from quackd.agent.transcript import Transcript
from quackd.flock.messages import FlockMessage

if TYPE_CHECKING:
    from quackd.log import LogEvent


class FlockTranscript:
    def __init__(self, run_dir: Path, now: Callable[[], float], *, stamp: str = "sim_t") -> None:
        self.run_dir = run_dir
        self.now = now
        self.stamp = stamp
        """What the time column is called. `sim_t` for an auction, whose clock is the world's;
        `t` for a pilot flock, whose members run in wall-clock seconds and would be lying
        under the other name."""
        self._fh = (run_dir / "flock.jsonl").open("a", encoding="utf-8")
        self._members: dict[str, Transcript] = {}
        self.events = 0

    def write(self, kind: str, **payload: Any) -> None:
        record = {self.stamp: round(self.now(), 3), "kind": kind, **payload}
        self._fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.events += 1

    def sink(self, event: LogEvent) -> None:
        """`flock.jsonl` as a `EventLog` record: the flock's own line for what belongs to no
        single member (the planner's LLM call). Stamped in `sim_t` like every other line in
        this file, so a replay lines it up with the world; the event's wall-clock `t` is
        dropped exactly as `Transcript.sink` drops it."""
        self.write(event.kind, **event.data)

    def on_bus(self, msg: FlockMessage) -> None:
        self.write("bus", msg=msg.model_dump())

    def member(self, name: str) -> Transcript:
        if name not in self._members:
            member_dir = self.run_dir / "ducks" / name
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
