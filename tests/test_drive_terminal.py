"""drive's front ends: a pipe, and the keyboard through prompt_toolkit's own pipe input."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest

from quackd.drive.terminal import Asker, drive_from_keyboard, drive_from_pipe
from tests.test_drive_session import _drive


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


async def test_a_question_is_answered_by_the_next_line_typed() -> None:
    said: list[str] = []
    asker = Asker(said.append)
    answer = asyncio.ensure_future(asker("did you mean kick(leg=left)?"))
    await asyncio.sleep(0)
    assert said == ["ask      did you mean kick(leg=left)? [y/N]"]
    assert asker.answers("Yes") and await answer is True
    assert not asker.answers("sit"), "a line with no question pending was taken for an answer"


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
