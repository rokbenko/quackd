"""drive's front ends: a pipe, and the keyboard through prompt_toolkit's own pipe input."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest

from quackd.drive.terminal import Asker, drive_from_keyboard, drive_from_pipe
from tests.test_drive_session import _drive, _hold, _until


async def test_a_pipe_runs_each_line_in_turn_as_a_pipe(tmp_path: Path) -> None:
    """One line, waited for, then the next, so a captured session reads the same every time."""
    drive, body, records = await _drive(tmp_path)
    said: list[str] = []
    await drive_from_pipe(
        drive, io.StringIO("forward\nreport state\nquit\nstand\n"), say=said.append
    )
    commands = [r for r in records if r["kind"] == "command"]
    assert [c["text"] for c in commands] == ["forward", "report state", "quit"], "read past quit"
    assert {c["by"] for c in commands} == {"cli"}
    assert said[0].startswith("exact    forward: move(duration_s=1, vx=0.15)")
    assert said[1].startswith("result   ok: walked")
    assert body.intents_of("move")
    await drive.robot.close()


async def test_the_keyboard_brakes_on_an_empty_enter_and_ends_on_quit(tmp_path: Path) -> None:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    drive, _body, records = await _drive(tmp_path)
    said: list[str] = []
    with create_pipe_input() as typed:
        typed.send_text("stand\r\rquit\r")
        await asyncio.wait_for(
            drive_from_keyboard(
                drive,
                Asker(said.append),
                say=said.append,
                input=typed,
                output=DummyOutput(),
                patch=False,
            ),
            30,
        )
    await asyncio.gather(*drive._brakes)
    kinds = [r["kind"] for r in records]
    assert [r["text"] for r in records if r["kind"] == "command"] == ["stand", "", "quit"]
    assert "brake" in kinds
    await drive.robot.close()


async def test_a_question_is_answered_by_a_yes_or_a_no_and_by_nothing_else() -> None:
    """An empty Enter is the brake and a command is a newer line, so neither is a no."""
    said: list[str] = []
    asker = Asker(said.append)
    answer = asyncio.ensure_future(asker("did you mean kick(leg=left)?"))
    await asyncio.sleep(0)
    assert said == ["ask      did you mean kick(leg=left)? [y/N]"]
    for line in ("", "stop", "turn left", "yes please"):
        assert not asker.answers(line), f"{line!r} was taken for an answer"
    assert asker.answers("Yes") and await answer is True
    no = asyncio.ensure_future(asker("run it?"))
    await asyncio.sleep(0)
    assert asker.answers("n") and await no is False
    assert not asker.answers("y"), "a line with no question pending was taken for an answer"


async def test_questions_are_shown_one_at_a_time_and_a_yes_answers_the_one_shown() -> None:
    """A goal asks from its own task while a line typed over it may ask too. With one question
    pending at a time the second took the first's place, and the first waited for an answer
    nobody could give; a `y` meant for the one on the screen must never answer another."""
    said: list[str] = []
    asker = Asker(said.append)
    first = asyncio.ensure_future(asker("run kick(leg=left)?", kind="confirm"))
    second = asyncio.ensure_future(
        asker("Go ahead anyway?", detail="The ball is out of frame.", kind="decide")
    )
    await asyncio.sleep(0)
    assert said == ["ask      run kick(leg=left)? [y/N]"], "only the first is shown"
    assert asker.answers("y") and await asyncio.wait_for(first, 2) is True
    await asyncio.sleep(0)
    assert said[-1] == "ask      The ball is out of frame. Go ahead anyway? [y/N]"
    third = asyncio.ensure_future(asker("did you mean forward?"))
    await asyncio.sleep(0)
    second.cancel()  # a brake ended the goal, and took its question back
    with pytest.raises(asyncio.CancelledError):
        await second
    await asyncio.sleep(0)
    assert said[-1] == "ask      did you mean forward? [y/N]"
    assert asker.answers("n") and await asyncio.wait_for(third, 2) is False
    assert asker.pending is None and not asker.answers("y")


async def _at_keyboard(drive: Any, said: list[str], typed: Any) -> asyncio.Future[None]:
    """The keyboard front end on `typed`, its asker the session's, as `run_drive` wires them."""
    from prompt_toolkit.output import DummyOutput

    drive.ask = asker = Asker(said.append)
    return asyncio.ensure_future(
        drive_from_keyboard(
            drive, asker, say=said.append, input=typed, output=DummyOutput(), patch=False
        )
    )


async def test_an_empty_enter_brakes_while_a_question_is_pending(tmp_path: Path) -> None:
    """The brake is never taken for an answer. With a walk under way and "kick left" asking for
    a yes, the Enter that came next was read as its no, and the walk went on."""
    from prompt_toolkit.input import create_pipe_input

    drive, body, records = await _drive(tmp_path)
    drive.robot.executor.registry.get("kick").safety_class = "confirm"
    walking = _hold(body, "sleep")
    said: list[str] = []
    with create_pipe_input() as typed:
        front = await _at_keyboard(drive, said, typed)
        typed.send_text("forward\r")
        await _until(lambda: bool(body.intents_of("move")))
        typed.send_text("kick left\r")
        await _until(lambda: any(line.startswith("ask") for line in said))
        typed.send_text("\r")
        await _until(lambda: any(r["kind"] == "brake" for r in records))
        typed.send_text("quit\r")
        await asyncio.wait_for(front, 10)
    walking.set()
    ended = {r["n"]: r["outcome"] for r in records if r["kind"] == "ended"}
    assert ended[1] == "preempted", "the walk went on past the Enter"
    assert ended[2] == "dropped"
    await drive.robot.close()


async def test_quit_typed_over_a_walk_stops_the_walk(tmp_path: Path) -> None:
    """The session ends what is still moving the body rather than waiting it out."""
    from prompt_toolkit.input import create_pipe_input

    drive, body, records = await _drive(tmp_path)
    walking = _hold(body, "sleep")
    said: list[str] = []
    with create_pipe_input() as typed:
        front = await _at_keyboard(drive, said, typed)
        typed.send_text("forward\r")
        await _until(lambda: bool(body.intents_of("move")))
        typed.send_text("quit\r")
        await asyncio.wait_for(front, 10)
    walking.set()
    ended = {r["n"]: r["outcome"] for r in records if r["kind"] == "ended"}
    assert ended[1] == "preempted"
    await drive.robot.close()


@pytest.mark.parametrize("presses", ["\x03", "\x03\x03"])
async def test_ctrl_c_brakes_and_a_second_press_ends_the_session(
    presses: str, tmp_path: Path
) -> None:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    drive, _body, records = await _drive(tmp_path)
    said: list[Any] = []
    with create_pipe_input() as typed:
        typed.send_text(presses if presses == "\x03\x03" else presses + "quit\r")
        await asyncio.wait_for(
            drive_from_keyboard(
                drive,
                Asker(said.append),
                say=said.append,
                input=typed,
                output=DummyOutput(),
                patch=False,
            ),
            30,
        )
    await asyncio.gather(*drive._brakes)
    assert [r for r in records if r["kind"] == "brake"], "Ctrl-C did not brake"
    commands = [r["text"] for r in records if r["kind"] == "command"]
    assert commands == ([] if presses == "\x03\x03" else ["quit"])
    await drive.robot.close()
