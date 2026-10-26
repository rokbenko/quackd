"""Places a pilot or a person named, kept beside the robot's memory.

A place is a pose the robot stood at, under a name, in the frame it was read in. It lives in
`<memory dir>/<robot slug>.places.jsonl`, next to the memory file and keyed the same way. A slug
never contains a dot, so the two files can never be each other. Like memory, every write goes to a
temporary file and is renamed over the old one, and nothing is locked: two processes marking at
the same instant is a race the later rename wins.

A place is usable while the robot is in the frame it was marked in and knows where it is. Marked
in another frame, it is orphaned: kept and listed, so a person can see what was lost, and never
walked to. Poses are never converted from one frame into another.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from quackd.memory import memory_dir, robot_slug
from quackd.space.pose import Fix, FrameRef, LocalizationKind
from quackd.space.source import SpatialSource

PLACE_NAME_MAX = 32
MAX_PLACES = 100
RESERVED_PLACES = frozenset({"start"})
"""Names quackd marks itself: `start` is where an `explore` or a `find` began, so going back is
`go_to_place start`. A pilot or a person cannot take one."""

_NAME = re.compile(r"\w[\w '\-]*")

PlaceStatus = Literal["here", "lost", "orphaned"]
"""`here`: usable now. `lost`: the robot does not know where it is now, so no place can be walked
to until it does. `orphaned`: marked in a frame the robot is not in, and never usable again."""


def clean_name(name: str) -> str:
    """`name` with its runs of spaces made one, or ValueError saying what a name may be."""
    clean = " ".join(str(name).split())
    if not 1 <= len(clean) <= PLACE_NAME_MAX or not _NAME.fullmatch(clean):
        raise ValueError(
            f"a place's name is 1 to {PLACE_NAME_MAX} letters, digits, spaces, hyphens or "
            f"apostrophes, starting with a letter or a digit, not {name!r}"
        )
    return clean


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"not a finite number: {value!r}")
    return float(value)


@dataclass
class Place:
    name: str
    x: float
    y: float
    yaw: float
    frame: str
    kind: LocalizationKind
    ts: float
    note: str | None = None
    walked_m: float | None = None
    """For a place marked on odometry, how far the robot had walked in that frame when it was
    marked: what it has walked since is how far the place may have drifted."""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": "place",
            "name": self.name,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "yaw": round(self.yaw, 3),
            "frame": self.frame,
            "kind": self.kind,
            "ts": round(self.ts, 3),
        }
        if self.note:
            out["note"] = self.note
        if self.walked_m is not None:
            out["walked_m"] = round(self.walked_m, 2)
        return out

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Place:
        kind = d.get("kind")
        if kind not in ("map", "odometry"):
            raise ValueError(f"a place's kind is map or odometry, not {kind!r}")
        frame = d.get("frame")
        if not isinstance(frame, str) or not frame:
            raise ValueError("a place names the frame it was marked in")
        note = d.get("note")
        walked = d.get("walked_m")
        return cls(
            name=clean_name(d.get("name", "")),
            x=_number(d.get("x")),
            y=_number(d.get("y")),
            yaw=_number(d.get("yaw")),
            frame=frame,
            kind=kind,
            ts=_number(d.get("ts", 0.0)),
            note=str(note) if note else None,
            walked_m=_number(walked) if walked is not None else None,
        )


@dataclass
class FrameRecord:
    """What the store remembers of a frame a place was marked in: whether it outlives a
    connection, a fingerprint a map can be recognised again by, and whether it was wiped."""

    id: str
    kind: LocalizationKind
    persistent: bool
    ts: float
    fingerprint: tuple[tuple[float, float, float], ...] = field(default_factory=tuple)
    wiped: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": "frame",
            "id": self.id,
            "kind": self.kind,
            "persistent": self.persistent,
            "ts": round(self.ts, 3),
        }
        if self.fingerprint:
            out["fingerprint"] = [[round(v, 4) for v in pose] for pose in self.fingerprint]
        if self.wiped:
            out["wiped"] = True
        return out

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> FrameRecord:
        kind = d.get("kind")
        if kind not in ("map", "odometry") or not isinstance(d.get("id"), str):
            raise ValueError("a frame record has an id and a kind of map or odometry")
        prints = d.get("fingerprint") or []
        fingerprint = tuple(
            (_number(p[0]), _number(p[1]), _number(p[2]))
            for p in prints
            if isinstance(p, list) and len(p) == 3
        )
        return cls(
            id=d["id"],
            kind=kind,
            persistent=bool(d.get("persistent")),
            ts=_number(d.get("ts", 0.0)),
            fingerprint=fingerprint,
            wiped=bool(d.get("wiped")),
        )


Record = Place | FrameRecord


class PlaceStore:
    """One robot's places. Every method re-reads the file, as `RobotMemory` does, so a run and an
    MCP server on the same robot see each other's marks. With `persist` off (`--no-memory`, a dry
    run) the places live as long as the store and nothing is written."""

    def __init__(
        self, robot_key: str, base_dir: str | Path | None = None, *, persist: bool = True
    ) -> None:
        self.robot_key = robot_key
        self.path = memory_dir(base_dir) / f"{robot_slug(robot_key)}.places.jsonl"
        self.persist = persist
        self._held: list[Record] = []

    # ── storage ─────────────────────────────────────────────────────────────────────

    def _records(self) -> list[Record]:
        if not self.persist:
            return list(self._held)
        if not self.path.exists():
            return []
        out: list[Record] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                loaded = json.loads(line)
            except json.JSONDecodeError:
                continue  # a hand-edited line that is not JSON is skipped, as memory skips one
            if not isinstance(loaded, dict):
                continue
            try:
                if loaded.get("type") == "place":
                    out.append(Place.from_json(loaded))
                elif loaded.get("type") == "frame":
                    out.append(FrameRecord.from_json(loaded))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def _write(self, records: list[Record]) -> None:
        if not self.persist:
            self._held = list(records)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(r.to_json(), ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    # ── reading ─────────────────────────────────────────────────────────────────────

    def places(self) -> list[Place]:
        return [r for r in self._records() if isinstance(r, Place)]

    def frames(self) -> list[FrameRecord]:
        return [r for r in self._records() if isinstance(r, FrameRecord)]

    def get(self, name: str) -> Place | None:
        key = " ".join(str(name).split()).lower()
        return next((p for p in self.places() if p.name.lower() == key), None)

    def frame_record(self, frame_id: str) -> FrameRecord | None:
        return next((f for f in self.frames() if f.id == frame_id), None)

    # ── writing ─────────────────────────────────────────────────────────────────────

    def mark(self, place: Place, *, reserved_ok: bool = False) -> Place | None:
        """Keep `place`, replacing one of the same name in any case, and return the one it
        replaced, or None. Refuses a reserved name unless quackd itself is marking it, and a
        hundred and first place."""
        place.name = clean_name(place.name)
        if place.name.lower() in RESERVED_PLACES and not reserved_ok:
            raise ValueError(
                f"{place.name!r} is a place quackd marks itself, where an explore or a find "
                "began: choose another name"
            )
        records = self._records()
        old = next(
            (r for r in records if isinstance(r, Place) and r.name.lower() == place.name.lower()),
            None,
        )
        kept = [r for r in records if r is not old]
        if old is None and sum(isinstance(r, Place) for r in kept) >= MAX_PLACES:
            raise ValueError(
                f"this robot keeps {MAX_PLACES} places at most: forget one first "
                "(quackd places rm, or forget_place)"
            )
        kept.append(place)
        self._write(kept)
        return old

    def forget(self, name: str) -> Place | None:
        """Drop the place called `name`, in any case, and return it, or None if there was none."""
        old = self.get(name)
        if old is not None:
            self._write(
                [
                    r
                    for r in self._records()
                    if not (isinstance(r, Place) and r.name.lower() == old.name.lower())
                ]
            )
        return old

    def note_frame(self, record: FrameRecord) -> None:
        """Remember a frame, replacing what was known of it under the same id."""
        records = [
            r for r in self._records() if not (isinstance(r, FrameRecord) and r.id == record.id)
        ]
        records.append(record)
        self._write(records)

    def mark_wiped(self, frame_id: str) -> None:
        """Say the map a frame was is gone, so its places are orphaned for that reason."""
        record = self.frame_record(frame_id)
        if record is not None and not record.wiped:
            record.wiped = True
            self.note_frame(record)


@dataclass(frozen=True)
class PlaceView:
    """A place as the robot stands now: whether it can be walked to, why not, and where it is
    from here when it can."""

    place: Place
    status: PlaceStatus
    reason: str | None = None
    distance_m: float | None = None
    bearing_rad: float | None = None


class PlaceBook:
    """A robot's places read against where it is now. The frame a place has to match is the
    source's, resolved against the frames the store remembers, so a map recognised again keeps
    its places."""

    def __init__(self, store: PlaceStore, source: SpatialSource) -> None:
        self.store = store
        self.source = source

    def frame(self) -> FrameRef | None:
        """The frame the robot is in now, as places are compared in, and remembered in the store
        the first time it is seen, so a later session can say what became of its places."""
        frame = self.source.resolve_frame(self.store.frames())
        if frame is not None and self.store.frame_record(frame.id) is None:
            self.store.note_frame(
                FrameRecord(
                    id=frame.id, kind=frame.kind, persistent=frame.persistent, ts=time.time()
                )
            )
        return frame

    def view(
        self, place: Place, fix: Fix | None = None, frame: FrameRef | None = None
    ) -> PlaceView:
        fix = self.source.fix() if fix is None else fix
        frame = self.frame() if frame is None else frame
        if frame is None or place.frame != frame.id:
            return PlaceView(place, "orphaned", self._why_orphaned(place, frame))
        if not fix.localized or fix.pose is None:
            return PlaceView(
                place, "lost", f"the robot does not know where it is ({fix.lost or 'no reading'})"
            )
        return PlaceView(
            place,
            "here",
            None,
            fix.pose.distance_to(place.x, place.y),
            fix.pose.bearing_to(place.x, place.y),
        )

    def views(self) -> list[PlaceView]:
        """Every place: the usable ones nearest first, then those waiting for the robot to know
        where it is, then the orphaned, each group by name."""
        fix = self.source.fix()
        frame = self.frame()
        out = [self.view(p, fix, frame) for p in self.store.places()]
        rank = {"here": 0, "lost": 1, "orphaned": 2}
        return sorted(
            out,
            key=lambda v: (rank[v.status], v.distance_m or 0.0, v.place.name.lower()),
        )

    def nearest(self, n: int) -> list[PlaceView]:
        return [v for v in self.views() if v.status == "here"][:n]

    def mark(
        self, name: str, note: str | None = None, *, reserved_ok: bool = False
    ) -> tuple[Place, Place | None]:
        """Mark where the robot stands now as `name`, from its own reading and never from
        coordinates anybody gives, and return the place with the one it replaced. Refused while
        the robot does not know where it is."""
        fix = self.source.fix()
        frame = self.frame()
        if not fix.localized or fix.pose is None or frame is None:
            raise ValueError(
                "the robot does not know where it is "
                f"({fix.lost or 'no reading yet'}), so there is nowhere to mark"
            )
        place = Place(
            name=name,
            x=fix.pose.x,
            y=fix.pose.y,
            yaw=fix.pose.yaw,
            frame=frame.id,
            kind=fix.kind,
            ts=time.time(),
            note=" ".join(note.split())[:200] if note else None,
            walked_m=fix.walked_m if fix.kind == "odometry" else None,
        )
        old = self.store.mark(place, reserved_ok=reserved_ok)
        return place, old

    def _why_orphaned(self, place: Place, frame: FrameRef | None) -> str:
        record = self.store.frame_record(place.frame)
        if record is not None and record.wiped:
            return "its map was wiped"
        if frame is None:
            return "the robot is in no frame yet"
        if place.kind == "odometry":
            return "it was marked on odometry that has started again since"
        return "it was marked in another map"
