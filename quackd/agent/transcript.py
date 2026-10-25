"""Every run leaves a paper trail: `runs/<timestamp>/transcript.jsonl`, frames, a summary.

A transcript exists so a run can be argued about after the fact — which prompt, which
tool call, which result, how many tokens — and so the golden tests can pin the loop's
behaviour without an LLM in the room.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import time
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from PIL import Image

from quackd.agent.providers.base import NamedPng
from quackd.transport.base import CameraFrame

if TYPE_CHECKING:
    from quackd.log import LogEvent


def run_label(text: str) -> str:
    """What `--run-name "Example 1"` is called on disk: `example-1`.

    The same slug every other name in quackd already is (`registry._NAME_RE`,
    `adapters.factory._NAME_RE`, `memory.robot_slug`), because a run directory is typed back
    into `quackd log` and pasted into a shell, and a space or a colon in it is a quoting
    problem on two operating systems rather than one.

    This raises where `robot_slug` falls back to a default, and the difference is the whole
    point of the flag: a name that quietly became `run` would leave a bench session of a
    hundred runs in a hundred directories nobody can tell apart, which is the thing somebody
    reached for `--run-name` to avoid.

    Accents are folded rather than deleted, so `Cafe 1` and `Café 1` land in the same place
    instead of the second one reading `caf-1`. A name in a script with no ASCII form at all is
    refused, and the refusal says ASCII rather than "letters", because a directory name typed
    back into two shells on two operating systems is not the place to find out otherwise.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:64].strip("-")
    if not slug:
        raise ValueError(
            f"--run-name {text!r} has no ASCII letters or digits in it, so there is nothing "
            "to name the directory after"
        )
    return slug


def new_run_dir(
    base: str | Path = "runs", name: str | None = None, label: str | None = None
) -> Path:
    """`runs/20260915-145349-goal-example-1/`: when, what, and what you called it.

    `label` is `--run-name`, already slugged, and it goes last so the timestamp prefix and the
    duck name in the middle both still resolve in `quackd log`. The collision counter goes
    after it for the same reason: two runs named the same thing in the same second read as
    `-example-1` and `-example-1-1`, and the suffix is still the last thing in the name.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{name}" if name else ""
    if label:
        suffix += f"-{label}"
    path = Path(base) / f"{stamp}{suffix}"
    i = 1
    while path.exists():
        path = Path(base) / f"{stamp}{suffix}-{i}"
        i += 1
    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        # A name adds up to 65 characters to every path inside the run, frames included, and
        # on a Windows box without long paths enabled that is how a `--run-name` turns into a
        # bare traceback from the standard library. Say which path and how long it was: the
        # number is the whole diagnosis and nothing else in the message hints at it.
        raise OSError(
            f"{e.strerror or e}: could not make the run directory {str(path)!r} "
            f"({len(str(path.resolve()))} characters). A shorter --run-name or a shorter "
            "--runs-dir will fit."
        ) from e
    return path


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class SavedFrame(NamedTuple):
    """One camera's picture for a step as it went into `frames/`: the file, and the PNG bytes
    written to it.

    The bytes are kept because they are also what the model is shown of that picture
    (`AgentLoop._observe`). A frame used to be encoded twice a turn, once for the file and once
    more for the request, and the second encoding was the same work on the same pixels with
    the same encoder, so it bought nothing but time on a worker thread a body's heartbeat
    shares the machine with. The request now carries the file's own bytes, which also makes
    the picture a reader opens afterwards the picture the pilot was sent, byte for byte."""

    path: Path
    png: bytes


class Transcript:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.path = run_dir / "transcript.jsonl"
        self.frames_dir = run_dir / "frames"
        self.images_dir = run_dir / "images"
        self._fh = self.path.open("a", encoding="utf-8")
        self._t0 = time.monotonic()
        self.started_at = datetime.now(UTC)
        """The wall clock at `t = 0`, read in the same breath as `_t0`.

        Every record carries `t`, seconds on the monotonic clock since this line ran, so one
        absolute time at the top is enough to place all of them: a record's wall time is
        `started_at + t`. Stamping each of the two hundred intents in a steering burst with
        its own ISO string would cost bytes for an answer that is already derivable.

        Until this existed the only absolute time a run had was the name of its directory,
        which is the local clock at second precision and gone the moment anybody renames it."""
        self.events = 0
        self.frame_count = 0

    def write(self, kind: str, **payload: Any) -> None:
        if self._fh.closed:
            # A verb task cancelled during teardown can narrate its last intent after the
            # record has closed. Dropping that line beats a `ValueError: I/O operation on
            # closed file` raised inside a task nobody is awaiting.
            return
        record = {"t": round(time.monotonic() - self._t0, 3), "kind": kind, **payload}
        self._fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        if kind != "intent":
            # An intent is written from inside `send_intent`, on the event loop, between two
            # deadman resends: a stalled filesystem there delays the next command past the
            # deadman and zeroes the velocity mid-stride. Every burst ends in a `verb_end`,
            # which flushes it, and the text layer's own buffer bounds what a hard kill could
            # lose to well under one verb's worth of lines.
            self._fh.flush()
        self.events += 1

    @property
    def started_at_iso(self) -> str:
        """When `t = 0` was, as the record spells it: `2026-09-21T10:15:02.123Z`."""
        return self.started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @property
    def elapsed_s(self) -> float:
        """Seconds since this transcript opened, on the clock every record's `t` is on.

        Not the budget's clock (`safety.Budget.elapsed_s`), which starts later, restarts after
        a `--by-hand` handover and runs on the simulator's time on a simulator. This one is
        the whole run, wall seconds, start to finish."""
        return time.monotonic() - self._t0

    def ended_at_iso(self, wall_s: float) -> str:
        """`started_at` plus the run's own span, rather than a second reading of the clock.

        A laptop that synced its clock mid-run, or slept through part of one, moves
        `datetime.now()` by an amount the monotonic clock never sees. Deriving the end from
        the start keeps `ended_at - started_at == wall_s` true of every record quackd writes,
        which is the arithmetic anybody reading these files will do without checking."""
        return (
            (self.started_at + timedelta(seconds=wall_s))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    def sink(self, event: LogEvent) -> None:
        """The transcript as a `EventLog` record: the event's kind and payload, on the
        transcript's own clock, so a `frame` written directly and an `observation` that came
        through the event_log never disagree about the time."""
        self.write(event.kind, **event.data)

    def save_frame(self, img: Image.Image, caption: str = "") -> Path:
        self.frames_dir.mkdir(exist_ok=True)
        path = self.frames_dir / f"{self.frame_count:04d}.png"
        img.save(path, format="PNG")
        self.write("frame", path=str(path.relative_to(self.run_dir)), caption=caption)
        self.frame_count += 1
        return path

    def save_frames(
        self, frames: Sequence[CameraFrame], caption: str = "", *, several: bool = False
    ) -> list[Path]:
        """Every camera's picture for one step, under one number.

        A body with one camera writes `0000.png` and a record with no camera in it, which is
        what every run before there could be two wrote. A body with several writes
        `0000-top.png` beside `0000-side.png` and names the camera in each record, so the
        file name says which view it is and the number still says which step.

        `several` is the body's camera count and not `len(frames)`, because a two-camera arm
        whose top lens stalls hands back one picture, and writing that as a bare `0000.png`
        leaves a file in the middle of a run with nothing anywhere saying which lens took it."""
        if len(frames) == 1 and not several:
            return [self.save_frame(frames[0].image, caption)]
        self.frames_dir.mkdir(exist_ok=True)
        paths: list[Path] = []
        for frame in frames:
            path = self.frames_dir / f"{self.frame_count:04d}-{frame.name}.png"
            frame.image.save(path, format="PNG")
            self.write(
                "frame",
                path=str(path.relative_to(self.run_dir)),
                caption=caption,
                camera=frame.name,
            )
            paths.append(path)
        # one step, one number, however many cameras were pointed at it
        self.frame_count += 1
        return paths

    async def save_frames_off_loop(
        self, frames: Sequence[CameraFrame], caption: str = "", *, several: bool = False
    ) -> list[SavedFrame]:
        """`save_frames`, with the PNGs encoded and written in a worker thread.

        Encoding a frame holds the event loop's thread for as long as it takes, and a body's
        heartbeat is waiting on that thread for an answer that has already come. The files
        and their names are `save_frames`'s, the step's number is taken before the wait so no
        other save can take it, and the records are written here, on the loop's thread, after
        their files, in the order `save_frames` writes them. The worker writes files and never
        a record, because the record is written from the loop's thread alone.

        Each picture is encoded once, into bytes, and those bytes are both what the file holds
        and what is handed back, in `frames` order, for the caller to send the model
        (`SavedFrame`). `png_bytes` is `Image.save` with the same format to a buffer rather than
        to a path, so the files are the bytes they always were."""
        one = len(frames) == 1 and not several
        number = self.frame_count
        self.frame_count += 1
        self.frames_dir.mkdir(exist_ok=True)
        named = [
            (frame, self.frames_dir / f"{number:04d}{'' if one else f'-{frame.name}'}.png")
            for frame in frames
        ]

        def save() -> list[SavedFrame]:
            saved: list[SavedFrame] = []
            for frame, path in named:
                png = png_bytes(frame.image)
                path.write_bytes(png)
                saved.append(SavedFrame(path=path, png=png))
            return saved

        saved = await asyncio.to_thread(save)
        for frame, path in named:
            where = str(path.relative_to(self.run_dir))
            if one:
                self.write("frame", path=where, caption=caption)
            else:
                self.write("frame", path=where, caption=caption, camera=frame.name)
        return saved

    def save_task_images(self, images: Sequence[NamedPng]) -> list[Path]:
        """The pictures that came with the task, written once at the top of the run.

        They go in their own directory rather than among the frames: `frames/` is numbered by
        step and is what the robot saw, and one of these belongs to no step at all. The bytes
        are the ones the model is sent, so a reader arguing about a run afterwards is looking
        at the picture the pilot looked at rather than at the file it was made from."""
        if not images:
            return []
        self.images_dir.mkdir(exist_ok=True)
        paths: list[Path] = []
        for i, image in enumerate(images):
            path = self.images_dir / f"{i:02d}-{image.name}"
            path.write_bytes(image.png)
            self.write(
                "task_image",
                path=str(path.relative_to(self.run_dir)),
                name=image.name,
                bytes=len(image.png),
            )
            paths.append(path)
        return paths

    def write_summary(self, summary: dict[str, Any]) -> Path:
        path = self.run_dir / "summary.json"
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        return path

    def close(self) -> None:
        self._fh.close()

    @staticmethod
    def read(path: Path, *, lenient: bool = False) -> list[dict[str, Any]]:
        """Every record in the file. `lenient` skips the unparsable ones and counts them under
        the key `_skipped` on the last record: a run killed mid-write leaves a half line, and
        `quackd log` should show the run that happened rather than a JSON error."""
        records: list[dict[str, Any]] = []
        skipped = 0
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    if not lenient:
                        raise
                    skipped += 1
        if skipped and records:
            records[-1]["_skipped"] = skipped
        return records
