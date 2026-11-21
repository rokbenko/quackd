"""What a robot keeps between runs: the memory file, the `remember` tool, the prompt."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from roboglot.agent.loop import RunConfig, run_glot
from roboglot.agent.prompts import REMEMBER_NAME, build_system_prompt
from roboglot.agent.providers.base import ToolCall
from roboglot.agent.providers.fake import FakeProvider
from roboglot.agent.transcript import Transcript
from roboglot.glotfile.schema import GlotFile
from roboglot.memory import (
    MAX_ENTRIES,
    NOTE_MAX_CHARS,
    NOTE_MAX_TAGS,
    TAG_MAX_CHARS,
    RobotMemory,
    memory_dir,
    robot_slug,
)
from roboglot.transport.mock import MockTransport
from roboglot.verbs.registry import default_registry

# ── the file ────────────────────────────────────────────────────────────────────────────


def test_slug_and_dir_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert robot_slug("microduck:mock") == "microduck-mock"
    assert robot_slug("Open Duck::bridge") == "open-duck-bridge"
    monkeypatch.setenv("ROBOGLOT_MEMORY_DIR", str(tmp_path / "env"))
    assert memory_dir() == tmp_path / "env"
    assert memory_dir(tmp_path / "explicit") == tmp_path / "explicit"
    mem = RobotMemory("microduck:mock", tmp_path)
    assert mem.path == tmp_path / "microduck-mock.jsonl"


def test_remember_round_trip_and_dedup(tmp_path: Path) -> None:
    mem = RobotMemory("microduck:mock", tmp_path)
    assert mem.recall() == ""
    a = mem.remember("  the ball is usually near   the left wall ", tags=["place"], now=1.0)
    assert a.text == "the ball is usually near the left wall"
    mem.remember("The ball is usually near the left wall", tags=["ball"], now=2.0)
    notes = mem.notes()
    assert len(notes) == 1, "same sentence twice must refresh, not duplicate"
    assert notes[0].ts == 2.0 and notes[0].tags == ["ball", "place"]
    with pytest.raises(ValueError):
        mem.remember("   ")
    assert "the ball is usually near the left wall" in mem.recall()


def test_a_notes_tags_are_bounded_in_number_and_length_and_so_is_a_refresh(
    tmp_path: Path,
) -> None:
    """A note's text was cut to `NOTE_MAX_CHARS`, and its tags were cut nowhere, by number or by
    length, and a refreshed note kept the union of its old tags and the new, so one call from any
    pilot, an MCP client or a local server could grow the file every later run reads whole by as
    much as an answer holds. They are bounded now as the text is, and a refresh is bounded too,
    the note's own tags kept before the new. A handful of short tags is stored as it was."""
    mem = RobotMemory("microduck:mock", tmp_path)
    first = mem.remember(
        "the ball is by the door", tags=["x" * 100_000, *(f"t{i:04d}" for i in range(5000))]
    )
    assert len(first.tags) == NOTE_MAX_TAGS and all(len(t) <= TAG_MAX_CHARS for t in first.tags)
    again = mem.remember("the ball is by the door", tags=[f"new{i:04d}" for i in range(5000)])
    assert again.tags == first.tags, "a refresh keeps the note's own tags before any new one"
    size = mem.path.stat().st_size
    assert size < NOTE_MAX_CHARS + NOTE_MAX_TAGS * (TAG_MAX_CHARS + 4) + 200, size

    plain = RobotMemory("microduck:mock", tmp_path / "plain")
    plain.remember("the rug is slippery", tags=["rug", "floor", "kitchen"], now=1.0)
    plain.remember("the rug is slippery", tags=["hall"], now=2.0)
    assert plain.path.read_text(encoding="utf-8") == (
        '{"kind": "note", "text": "the rug is slippery", "ts": 2.0, '
        '"tags": ["floor", "hall", "kitchen", "rug"]}\n'
    )


def test_one_long_tag_among_a_few_is_cut_as_the_text_is(tmp_path: Path) -> None:
    """A tag's length is bounded on its own, not only by the count: a note with two tags, one of
    them a whole answer long, kept that one whole, the count never being reached. It is cut to
    `TAG_MAX_CHARS` now, and the short one is kept as it was."""
    mem = RobotMemory("microduck:mock", tmp_path)
    entry = mem.remember("the door sticks", tags=["door", "y" * 10_000])
    assert entry.tags == ["door", "y" * TAG_MAX_CHARS]
    assert mem.notes()[0].tags == ["door", "y" * TAG_MAX_CHARS]


def test_episode_and_recall_order(tmp_path: Path) -> None:
    mem = RobotMemory("microduck:mock", tmp_path)
    mem.record_episode(task="find-and-kick", outcome="failure", reason="no ball", steps=6, now=1.0)
    mem.record_episode(
        task="find-and-kick",
        outcome="success",
        reason="ball displaced",
        steps=4,
        highlights=["search_scan: ball found at 18° left", "kick: ball moved 0.5 m"],
        now=2.0,
    )
    mem.remember("kick with the right leg works", now=3.0)
    text = mem.recall()
    assert text.index("kick with the right leg works") < text.index("Your most recent runs")
    # newest first
    assert text.index("success — ball displaced") < text.index("failure — no ball")
    assert "kick: ball moved 0.5 m" in text
    assert mem.summary() == {
        "robot": "microduck:mock",
        "path": str(mem.path),
        "notes": 1,
        "episodes": 2,
    }
    assert mem.clear() == 3 and not mem.path.exists() and mem.recall() == ""


def test_cap_drops_old_episodes_before_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every write re-reads and rewrites the whole file, so this is quadratic in the cap.
    At the real 400 it cost 15 s on one machine, on four CI jobs, to prove a property that
    a cap of 12 proves just as well. The real cap is asserted below, once."""
    monkeypatch.setattr("roboglot.memory.MAX_ENTRIES", 12)
    mem = RobotMemory("microduck:mock", tmp_path)
    mem.remember("keep me", now=0.0)
    for i in range(17):
        mem.record_episode(task="d", outcome="success", reason=str(i), steps=1, now=float(i))
    entries = mem.entries()
    assert len(entries) == 12
    assert entries[0].kind == "note" and entries[0].text == "keep me"
    assert [e.text for e in entries[1:]] == [f"d: success — {i} (1 steps)" for i in range(6, 17)], (
        "the surviving episodes must be the newest, in order"
    )


def test_the_cap_is_the_one_the_docs_promise() -> None:
    assert MAX_ENTRIES == 400  # docs/guides/memory.md and ADR-0025 both say 400


def test_the_cli_and_the_mcp_server_key_every_robot_the_same_way() -> None:
    """docs/guides/memory.md promises a note saved from Claude Desktop is read by the next
    `roboglot run` on the same robot. That is only true while these two expressions agree,
    and they are written in two files that know nothing about each other."""
    from roboglot.adapters.base import AdapterError, adapter_name, backend_name
    from roboglot.adapters.factory import (
        BACKENDS,
        RobotSpec,
        make_adapter,
        refusal,
        resolve_robot,
    )

    for adapter, backends in BACKENDS.items():
        for backend in backends:
            cli_key = resolve_robot(f"{adapter}:{backend}").key  # roboglot/cli.py
            spec = RobotSpec(adapter, backend, "member")
            if refusal(spec) is not None:
                continue  # refused before it is built (ADR-0076), so nothing ever keys a memory
            try:
                transport = make_adapter(spec)
            except AdapterError as e:
                # a body with no default address, as Huebot's shim has none, is refused as it is
                # built; nothing here connects, so any address builds it
                if "needs --address" not in str(e):
                    raise
                transport = make_adapter(spec, address="tcp://127.0.0.1:9")
            # roboglot/mcp_server.py, for a team member whose name is not the adapter's
            mcp_key = f"{adapter_name(transport) or 'member'}:{backend_name(transport)}"
            assert cli_key == mcp_key, f"{adapter}:{backend} would use two memory files"


@pytest.mark.parametrize(
    "junk",
    [
        "not json at all",
        '"a bare json string"',  # valid JSON, not an object: used to raise AttributeError
        "17",
        "[1, 2, 3]",
        "null",
        "true",
        '{"kind": "note", "text": "x", "ts": "not a number"}',  # right shape, wrong types
        '{"kind": "note", "text": "x", "ts": 1.0, "tags": 5}',
    ],
)
def test_a_hand_edited_line_is_skipped_not_fatal(tmp_path: Path, junk: str) -> None:
    """The file is documented as one you can edit by hand, so every way of getting it wrong
    has to be survivable. Only `json.JSONDecodeError` was caught, so a line that parsed to
    anything but an object took the whole command down with an AttributeError."""
    mem = RobotMemory("microduck:mock", tmp_path)
    mem.remember("real", now=1.0)
    with mem.path.open("a", encoding="utf-8") as f:
        f.write(junk + "\n")
    assert [e.text for e in mem.entries()] == ["real"]
    assert "real" in mem.recall()  # and every reader built on entries() survives too
    assert mem.summary()["notes"] == 1
    # writing still works, and so does the recovery the docs point at: both go through
    # entries(), so when it raised there was no way back except deleting the file by hand
    mem.remember("another", now=2.0)
    assert mem.clear() == 2 and not mem.path.exists()


def test_a_refreshed_note_becomes_the_newest_note(tmp_path: Path) -> None:
    """`recall` and the cap both read file order, so a refresh that left the entry where it
    was made "the newest notes" a lie: the note the LLM just repeated sorted oldest."""
    mem = RobotMemory("microduck:mock", tmp_path)
    mem.remember("the sofa is on the left", now=1.0)
    for i in range(3):
        mem.remember(f"filler {i}", now=2.0 + i)
    mem.remember("The sofa is on the left", now=99.0)
    assert [e.text for e in mem.notes()][-1] == "the sofa is on the left"
    lines = [line for line in mem.recall().splitlines() if line.startswith("- ")]
    assert "the sofa is on the left" in lines[0], f"newest first is broken: {lines}"


OLD_NOTES = ["the charger is by the door", "the rug is slippery", "the cat sleeps on the sofa"]

B_NOTE = "B: the door is shut"

WRITER = r"""
import io, os, sys, time
from roboglot.memory import RobotMemory

base, note, hold, signals, within, every = sys.argv[1:7]


def touch(name):
    os.close(os.open(os.path.join(signals, name), os.O_CREAT | os.O_WRONLY))


def held(name):
    touch(name + ".held")
    deadline = time.monotonic() + float(within)
    while not os.path.exists(os.path.join(signals, name + ".go")):
        if time.monotonic() > deadline:
            raise SystemExit(name + " was never let go")
        time.sleep(float(every))


real_open, real_replace = io.open, os.replace


class Halved:
    # The memory file's one write, half of it, a stop, and the rest: the file is open meanwhile.
    def __init__(self, f):
        self.f = f

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self.f.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self.f, name)

    def write(self, data):
        half = len(data) // 2
        self.f.write(data[:half])
        self.f.flush()
        held("mid-write")
        return half + self.f.write(data[half:])


def opened(file, mode="r", *args, **kwargs):
    f = real_open(file, mode, *args, **kwargs)
    return Halved(f) if "w" in mode or "x" in mode else f


def replace(src, dst, **kwargs):
    held("before-replace")
    return real_replace(src, dst, **kwargs)


if hold == "mid-write":
    io.open = opened
elif hold == "before-replace":
    os.replace = replace
try:
    RobotMemory("duck-01", base).remember(note)
    print("saved")
except OSError as e:
    print("raised " + type(e).__name__)
"""
"""One process that remembers one note, held where `hold` says until the test lets it go:
`mid-write` with the first half of the memory's text written and its file open, which is how a
write looks to another process at that instant, or `before-replace` with its file written and
closed. Held through `io.open` and `os.replace`, which every way of writing the file goes
through, and ordered by files the test writes, never by sleeps."""


class _Writer:
    """One `WRITER` process, started by the test, and what it said."""

    def __init__(self, base: Path, signals: Path, note: str, hold: str) -> None:
        import subprocess
        import sys

        from tests.support import UNTIL_EVERY_S, UNTIL_WITHIN_S

        self.signals = signals
        self.hold = hold
        self.proc = subprocess.Popen(
            [
                *(sys.executable, "-X", "utf8", "-c", WRITER),
                *(str(base), note, hold, str(signals), str(UNTIL_WITHIN_S), str(UNTIL_EVERY_S)),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def held(self) -> None:
        from tests.support import until

        where = self.signals / f"{self.hold}.held"
        until(lambda: where.exists() or self.proc.poll() is not None, what=f"held {self.hold}")
        assert where.exists(), f"the writer ended before {self.hold}: {self.said()}"

    def go(self) -> None:
        (self.signals / f"{self.hold}.go").touch()

    def said(self) -> str:
        from tests.support import UNTIL_WITHIN_S

        out, err = self.proc.communicate(timeout=UNTIL_WITHIN_S)
        assert self.proc.returncode == 0, err
        return out.strip()


def _two_writers_left(tmp_path: Path, said: dict[str, str]) -> None:
    """What two writers at once may leave, and what they may not: every line readable, every
    note saved before them still there, each writer's word about its own note true, and no
    temporary file behind. That one of two new notes can be lost to the later replace is the
    documented race (`RobotMemory`), and it is why A's note here is one already saved."""
    mem = RobotMemory("duck-01", tmp_path)
    lines = [line for line in mem.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    texts = [e.text for e in mem.entries()]
    assert len(texts) == len(lines), f"{len(lines) - len(texts)} damaged lines dropped: {texts}"
    assert [n for n in OLD_NOTES if n not in texts] == [], f"old notes lost: {texts} ({said})"
    assert (said["B"] == "saved") == (B_NOTE in texts), f"B said {said['B']!r}: {texts}"
    assert said == {"A": "saved", "B": "saved"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_note_written_while_another_writer_is_mid_write_damages_nothing(tmp_path: Path) -> None:
    """Every writer of one robot's memory wrote through the same temporary name. So a second
    writer (a run beside the MCP server, or two terminals) that wrote while the first had that
    file open shared it: on Windows their bytes interleaved and the file the first then put in
    place dropped damaged lines in silence, old notes among them, while the second was told it
    could not remember; on POSIX the second's text was truncated under the first's. Two real
    processes here, the first held halfway through its write while the second writes in full.
    """
    signals = tmp_path / "signals"
    signals.mkdir()
    base = tmp_path
    seed = RobotMemory("duck-01", base)
    for note in OLD_NOTES:
        seed.remember(note)
    b = _Writer(base, signals, B_NOTE, "mid-write")
    try:
        b.held()
        said = {"A": _Writer(base, signals, OLD_NOTES[0], "none").said()}
    finally:
        b.go()
    said["B"] = b.said()
    _two_writers_left(base, said)


@pytest.mark.skipif(os.name != "posix", reason="the rename of a file another process holds open")
def test_a_note_written_after_another_writer_closed_its_file_is_told_the_truth(
    tmp_path: Path,
) -> None:
    """The POSIX half of the shared temporary name. The first writer closed its file and was
    about to put it in place; the second opened the same name, truncating it, and began to write;
    the first renamed it into place, so the second went on writing into what was now the memory
    file, and its own rename then raised: told "could not remember" for a note that was saved,
    in a file a reader could see half of. On Windows the rename of a file another process holds
    open is refused, so this order cannot happen there."""
    signals = tmp_path / "signals"
    signals.mkdir()
    base = tmp_path
    seed = RobotMemory("duck-01", base)
    for note in OLD_NOTES:
        seed.remember(note)
    a = _Writer(base, signals, OLD_NOTES[0], "before-replace")
    try:
        a.held()
        b = _Writer(base, signals, B_NOTE, "mid-write")
        try:
            b.held()
            a.go()
            said = {"A": a.said()}
        finally:
            b.go()
        said["B"] = b.said()
    finally:
        a.go()
    _two_writers_left(base, said)


def test_a_write_removes_a_dead_writers_temporary_file_and_nothing_else(tmp_path: Path) -> None:
    """With a temporary file of its own for every write, a writer killed between its write and
    its replace left a file no later write overwrote, where the one shared name was overwritten
    by the next write. A write now removes those older than the registry's `STALE_TEMP_S`, which
    only a dead writer's can be, and leaves a younger one, which may be a live writer's about to
    be renamed, and every file whose name is not exactly its own pattern: the old shared name,
    another robot's, a name one digit short. Ages are set on the files, so no clock is waited."""
    import time

    from roboglot import registry

    mem = RobotMemory("duck-01", tmp_path)
    mem.remember(OLD_NOTES[0])
    name = mem.path.name
    hexed = "0123456789abcdef"
    dead = tmp_path / f"{name}.{hexed}.tmp"
    young = tmp_path / f"{name}.fedcba9876543210.tmp"
    others = [
        tmp_path / f"{name}.tmp",
        tmp_path / f"{mem.path.stem}.tmp",
        tmp_path / f"{name}.{hexed[:-1]}.tmp",
        tmp_path / f"{name}.{hexed}.tmp.bak",
        tmp_path / f"other-robot.jsonl.{hexed}.tmp",
    ]
    old = time.time() - registry.STALE_TEMP_S - 1
    for left in (dead, young, *others):
        left.write_text("{}\n", encoding="utf-8")
        if left is not young:
            os.utime(left, (old, old))
    mem.remember(OLD_NOTES[1])
    assert not dead.exists(), "a dead writer's temporary file is still there"
    assert young.exists(), "a temporary file young enough to be a live writer's was removed"
    assert [p.name for p in others if not p.exists()] == []
    assert [e.text for e in mem.entries()] == OLD_NOTES[:2]


# ── the prompt ──────────────────────────────────────────────────────────────────────────


def test_prompt_mentions_memory_only_when_on(hello_glot: GlotFile) -> None:
    reg = default_registry()
    verbs = [reg.view(n) for n in hello_glot.frontmatter.verbs.allow]
    off = build_system_prompt(hello_glot, verbs)
    assert "remember" not in off.lower()
    empty = build_system_prompt(hello_glot, verbs, memory_text="")
    assert "first run on this robot" in empty and "`remember`" in empty
    full = build_system_prompt(hello_glot, verbs, memory_text="- [2026-09-03] ball by sofa")
    assert "ball by sofa" in full and "first run" not in full


# ── the loop ────────────────────────────────────────────────────────────────────────────


async def test_remember_tool_saves_a_note_without_spending_a_step(
    hello_glot: GlotFile, tmp_path: Path
) -> None:
    mem = RobotMemory("microduck:mock", tmp_path / "mem")
    script = [
        ToolCall(name="quack", arguments={"text": "hi"}),
        ToolCall(name=REMEMBER_NAME, arguments={"text": "quacking first works", "tags": ["how"]}),
        ToolCall(name="declare_success", arguments={"reason": "quacked"}),
    ]
    result = await run_glot(
        RunConfig(
            glot=hello_glot,
            provider=FakeProvider(script=script),
            transport=MockTransport(),
            runs_dir=tmp_path / "runs",
            memory=mem,
        )
    )
    assert result.outcome == "success"
    # the verdict and the note each cost an LLM call and neither costs a step
    assert result.steps == 1 and result.llm_calls == 4
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assert events[0]["memory"] == {
        "robot": "microduck:mock",
        "path": str(mem.path),
        "notes": 0,
        "episodes": 0,
    }
    assert REMEMBER_NAME in events[0]["tools"]
    kinds = [e["kind"] for e in events]
    assert "memory" in kinds
    mem_event = next(e for e in events if e["kind"] == "memory")
    assert mem_event["ok"] and mem_event["text"] == "quacking first works"
    # the LLM heard back about it on the next observation
    obs_after = [e for e in events if e["kind"] == "observation"][-1]
    assert "remembered for future runs: quacking first works" in obs_after["text"]
    # and the file now has the note plus the episode Roboglot wrote at the end
    assert [e.text for e in mem.notes()] == ["quacking first works"]
    episodes = mem.episodes()
    assert len(episodes) == 1 and episodes[0].outcome == "success"
    assert episodes[0].text.startswith("hello-world: success — quacked (1 steps)")
    # one assertion, not `A or B` where A is never true: the tone in brackets comes from
    # the robot's own sound table, so the stable part is the verb and the text it carried
    (highlight,) = episodes[0].highlights
    assert highlight.startswith("quack: quacked [") and "'hi'" in highlight


async def test_second_run_starts_with_what_the_first_learned(
    hello_glot: GlotFile, tmp_path: Path
) -> None:
    mem = RobotMemory("microduck:mock", tmp_path / "mem")
    first = await run_glot(
        RunConfig(
            glot=hello_glot,
            provider=FakeProvider(
                script=[
                    ToolCall(name=REMEMBER_NAME, arguments={"text": "the sofa is on the left"}),
                    ToolCall(name="declare_failure", arguments={"reason": "just testing"}),
                ]
            ),
            transport=MockTransport(),
            runs_dir=tmp_path / "runs",
            memory=mem,
        )
    )
    assert first.outcome == "failure"
    second = await run_glot(
        RunConfig(
            glot=hello_glot,
            provider=FakeProvider.for_glot("hello-world"),
            transport=MockTransport(),
            runs_dir=tmp_path / "runs",
            memory=mem,
        )
    )
    assert second.outcome == "success"
    start = Transcript.read(second.run_dir / "transcript.jsonl")[0]
    prompt = start["system_prompt"]
    assert "the sofa is on the left" in prompt
    assert "hello-world: failure — just testing" in prompt
    assert start["memory"]["notes"] == 1 and start["memory"]["episodes"] == 1
    assert len(mem.episodes()) == 2


async def test_memory_off_means_no_tool_no_episode(hello_glot: GlotFile, tmp_path: Path) -> None:
    result = await run_glot(
        RunConfig(
            glot=hello_glot,
            provider=FakeProvider.for_glot("hello-world"),
            transport=MockTransport(),
            runs_dir=tmp_path / "runs",
        )
    )
    start = Transcript.read(result.run_dir / "transcript.jsonl")[0]
    assert REMEMBER_NAME not in start["tools"] and start["memory"] is None
    assert "remember" not in start["system_prompt"].lower()


async def test_dry_run_writes_no_episode(hello_glot: GlotFile, tmp_path: Path) -> None:
    mem = RobotMemory("microduck:mock", tmp_path / "mem")
    await run_glot(
        RunConfig(
            glot=hello_glot,
            provider=FakeProvider.for_glot("hello-world"),
            transport=MockTransport(),
            runs_dir=tmp_path / "runs",
            memory=mem,
            dry_run=True,
        )
    )
    assert mem.entries() == []


async def test_dry_run_writes_no_note_either(hello_glot: GlotFile, tmp_path: Path) -> None:
    """`--dry-run` sends nothing and leaves nothing behind. The episode was guarded and the
    `remember` tool was not, so an inert run could write a permanent conclusion drawn from
    verb results it had invented."""
    mem = RobotMemory("microduck:mock", tmp_path / "mem")
    result = await run_glot(
        RunConfig(
            glot=hello_glot,
            provider=FakeProvider(
                script=[
                    ToolCall(name=REMEMBER_NAME, arguments={"text": "learned in a dry run"}),
                    ToolCall(name="declare_success", arguments={"reason": "done"}),
                ]
            ),
            transport=MockTransport(),
            runs_dir=tmp_path / "runs",
            memory=mem,
            dry_run=True,
        )
    )
    assert mem.entries() == [], "a dry run must not leave a note behind"
    assert not mem.path.exists()
    # and the LLM is told plainly, in the same shape a dry-run verb answers in
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    obs = [e for e in events if e["kind"] == "observation"][-1]
    assert "[dry-run] not saved: learned in a dry run" in obs["text"]
