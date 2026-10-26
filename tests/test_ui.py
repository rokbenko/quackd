"""The house style: what it draws, what it refuses to parse, and when it stands down.

Three promises are worth a test each. Text a model or a manifest wrote is never read as
markup. A stream that cannot carry a glyph gets an ASCII one rather than a `?`. And the
status line exists only when somebody is watching, because everything this project prints
also has to survive a pipe, a test runner and a CI log.

A fourth came with the capture: what you saw is written into the run directory, and a file
somebody reads a year later must hold the run's own lines, none of Rich's scaffolding,
nothing that drives the terminal they read it in, and a word about itself if it is short.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from rich.control import Control

from quackd import ui
from quackd.log import LogEvent


def narrow() -> tuple[Console, io.BytesIO]:
    """A console on a Windows codepage: what `2> run.log` gives you there."""
    raw = io.BytesIO()
    return Console(file=io.TextIOWrapper(raw, encoding="cp1252", errors="replace"), width=90), raw


def wide(width: int = 90, **kwargs: Any) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=width, **kwargs), buf


def drawn(console: Console, raw: io.BytesIO) -> str:
    console.file.flush()
    return raw.getvalue().decode("cp1252")


def tee(width: int = 90, **kwargs: Any) -> tuple[ui.TeeConsole, io.StringIO]:
    """What `make_console` returns, with a buffer where the terminal would be.

    Every claim about the capture is a claim about two ends at once, and a `TeeConsole`
    takes no file of its own, so the buffer stands in for the screen."""
    buf = io.StringIO()
    console = ui.TeeConsole(
        file=buf, width=width, theme=ui.THEME, highlight=False, emoji=False, **kwargs
    )
    return console, buf


def saved(run_dir: Path) -> str:
    """The terminal the run wrote into its run directory."""
    return (run_dir / "terminal.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_capture_outlives_its_test() -> Iterator[None]:
    """A capture is module state. One left open by a test that failed halfway would follow
    the next test into its file, or into a test that never asked to be recorded at all."""
    yield
    capture = ui.active_capture()
    if capture is not None:
        capture.close()
    assert ui.active_capture() is None


# ── glyphs ──────────────────────────────────────────────────────────────────────────────


def test_the_glyph_half_is_chosen_from_the_stream_not_from_the_platform() -> None:
    console, _ = narrow()
    assert ui.glyphs_for(console) is ui.ASCII
    assert ui.glyphs_for(wide()[0]) is ui.UNICODE


def test_every_gutter_glyph_fits_the_gutter_in_both_halves() -> None:
    """The label column starts at a fixed offset. A glyph wider than the gutter would push
    one line's label out of line with its neighbours' and the log would read as noise."""
    from rich.cells import cell_len

    for glyphs in (ui.UNICODE, ui.ASCII):
        for mark in ui.MARKS:
            assert 0 < cell_len(glyphs.mark(mark)) <= 2, (glyphs, mark)


def test_a_mark_nobody_defined_draws_nothing() -> None:
    assert ui.UNICODE.mark(None) == ""
    assert ui.UNICODE.mark("duck") == "", "only the marks, never the decorations"
    assert ui.UNICODE.mark("nonsense") == ""


def test_the_registry_status_emoji_becomes_something_a_codepage_can_carry() -> None:
    fancy = "✅ built-in: sim2d · \U0001f9ea jsonrpc — experimental"
    assert ui.degrade(fancy, ui.UNICODE) == fancy, "a terminal that can show it, sees it"
    plain = ui.degrade(fancy, ui.ASCII)
    assert plain == "[ok] built-in: sim2d - [exp] jsonrpc - experimental"
    assert plain.isascii()


# ── text is data ────────────────────────────────────────────────────────────────────────

MARKUP = "the ball is [behind] the sofa [/think] and [bold] quackd[anthropic]"


@pytest.mark.parametrize(
    "renderable",
    [
        pytest.param(ui.fail_line(MARKUP, hint=MARKUP), id="fail_line"),
        pytest.param(ui.run_header(MARKUP, [("robot", MARKUP)], hint=MARKUP), id="run_header"),
        pytest.param(
            ui.verdict("failure", MARKUP, counters=[MARKUP], run_dir="runs/x"), id="verdict"
        ),
        pytest.param(ui.kv_grid([("key", MARKUP)]), id="kv_grid"),
    ],
)
def test_nothing_here_reads_what_somebody_else_wrote_as_markup(renderable: Any) -> None:
    """Rich drops `[bold]` silently and raises on an unpaired `[/think]`. Every string in
    this output came from a model, a manifest, or an error naming an extra."""
    console, buf = wide(400)
    console.print(renderable)
    out = buf.getvalue()
    for tag in ("[behind]", "[/think]", "[bold]", "quackd[anthropic]"):
        assert tag in out, tag


def test_the_verdict_wears_its_outcome() -> None:
    console, buf = wide(60, force_terminal=True)
    for outcome in ("success", "failure", "budget", "aborted", "infeasible"):
        console.print(ui.verdict(outcome, "why", run_dir="runs/x"))
    out = buf.getvalue()
    assert "SUCCESS" in out and "BUDGET" in out and "ABORTED" in out
    assert "INFEASIBLE" in out
    # infeasible is a warning, not a failure: nothing broke and nothing was tried
    assert ui._OUTCOME["infeasible"] == ui._OUTCOME["budget"]
    assert "\x1b[32m" in out, "success is green"
    assert "\x1b[1;31m" in out, "and a failure is not"


def test_a_panel_on_a_codepage_that_has_no_box_still_says_everything() -> None:
    console, raw = narrow()
    console.print(
        ui.run_header("find-and-kick", [("robot", "microduck:sim2d")], hint="Ctrl-C stops it")
    )
    console.print(ui.verdict("success", "kicked it", counters=["steps 7", "llm calls 8"]))
    out = drawn(console, raw)
    assert "?" not in out, "a glyph that arrives as a question mark is a glyph not worth having"
    assert out.isascii()
    for needle in ("find-and-kick", "microduck:sim2d", "Ctrl-C stops it", "SUCCESS", "kicked it"):
        assert needle in out, needle


def test_the_renderables_read_the_console_drawing_them_not_the_one_at_import() -> None:
    """`quackd log` replays onto stdout and a run narrates onto stderr, and tests hand
    round buffers of their own. Choosing the glyph when the panel is built gets all three
    wrong, which is why they are deferred."""
    panel = ui.verdict("success", "done")
    console, raw = narrow()
    console.print(panel)
    assert drawn(console, raw).isascii()
    console2, buf = wide()
    console2.print(panel)
    assert "✓" in buf.getvalue()


# ── the consoles ────────────────────────────────────────────────────────────────────────


def test_configure_rebuilds_both_consoles_so_no_color_can_reach_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ui.configure()
    assert ui.console.no_color is False
    ui.configure(no_color=True)
    assert ui.console.no_color is True and ui.err_console.no_color is True
    monkeypatch.setenv("NO_COLOR", "1")
    ui.configure()
    assert ui.console.no_color is True, "the variable alone is enough"
    monkeypatch.delenv("NO_COLOR")
    ui.configure()
    assert ui.console.no_color is False


def test_one_console_answers_and_the_other_decorates() -> None:
    ui.configure()
    assert ui.console.stderr is False and ui.err_console.stderr is True


# ── waiting ─────────────────────────────────────────────────────────────────────────────


def ticks() -> Any:
    """A clock that moves a second every time it is read, so the line has to count."""
    state = {"t": 0.0}

    def clock() -> float:
        state["t"] += 1.0
        return state["t"]

    return clock


def test_the_status_line_does_nothing_at_all_when_nobody_is_watching() -> None:
    """Under a pipe, a test runner or `TERM=dumb`: no live region, no refresh thread, and
    nothing added to the output that a script would then have to strip."""
    console, buf = wide()
    with ui.RunStatus(console) as status:
        assert status.active is False
        status.update("waiting on anthropic")
        status.sink(LogEvent("llm_request", 0.0, {"provider": "anthropic", "model": "opus"}))
        console.print("the answer")
    assert buf.getvalue() == "the answer\n"


def test_the_status_line_draws_and_prints_land_above_it() -> None:
    console, buf = wide(force_terminal=True)
    with ui.RunStatus(console) as status:
        assert status.active is True
        status.update("waiting on anthropic claude-opus-5")
        console.print("a line of narration")
    out = buf.getvalue()
    assert "a line of narration" in out
    assert "waiting on anthropic claude-opus-5" in out


def test_the_status_line_names_the_step_and_counts_a_long_wait() -> None:
    console, _ = wide()
    status = ui.RunStatus(console, clock=ticks())
    text = "[step 3/40 · llm calls 3/40, 0.1/5 min]\nstate: posture=standing"
    status.sink(LogEvent("observation", 0.0, {"text": text}))
    status.sink(LogEvent("llm_request", 0.0, {"provider": "anthropic", "model": "claude-opus-5"}))
    rendered = status.__rich__().plain
    assert "step 3/40" in rendered, "the budget's two halves only appear together here"
    assert "waiting on anthropic claude-opus-5" in rendered
    assert rendered.rstrip().endswith("s"), "a wait this long says how long"


def test_a_turn_asked_again_says_it_is_waiting_rather_than_choosing() -> None:
    """A failure quackd asks the same turn again after is followed by up to a minute in which
    nothing happens, and a line about the model's answer over that minute says the model has
    answered when it is not even being asked. An answer that came still says what it was."""
    console, _ = wide()
    status = ui.RunStatus(console)
    failed = {"provider": "anthropic", "error": "ProviderError: 529", "retry_in_s": 15.0}
    status.sink(LogEvent("llm", 0.0, failed))
    assert "waiting 15 s to ask anthropic again" in status.__rich__().plain
    status.sink(LogEvent("llm", 0.0, {"provider": "anthropic", "tool_calls": []}))
    assert "the model answered without a call" in status.__rich__().plain


def test_the_line_names_the_call_the_model_chose_and_how_long_the_last_answer_took() -> None:
    """The line after an answer used to say "choosing a verb", which was over by then: the
    `llm` record is written once the call is back. It names what the model chose, which is what
    the run is about to do, and the next wait says how long the last answer took, because a
    count of seconds alone says how long a wait has been and nothing about how long it will
    be. A failed call is not the model's answer, and its seconds are not a guide to the next."""
    console, _ = wide()
    status = ui.RunStatus(console, clock=ticks())
    request = {"provider": "anthropic", "model": "claude-opus-5-5"}
    status.sink(LogEvent("llm_request", 0.0, request))
    assert "(last call" not in status.__rich__().plain, "nothing has answered yet"
    chose = {"name": "kick", "arguments": {"leg": "left"}}
    answer = {"provider": "anthropic", "tool_calls": [chose], "latency_s": 6.25}
    status.sink(LogEvent("llm", 0.0, answer))
    assert "the model chose kick(leg='left')" in status.__rich__().plain
    status.sink(LogEvent("llm_request", 0.0, request))
    assert "waiting on anthropic claude-opus-5-5 (last call 6.2 s)" in status.__rich__().plain

    two = {"provider": "anthropic", "tool_calls": [chose, {"name": "quack"}], "latency_s": 1.0}
    status.sink(LogEvent("llm", 0.0, two))
    assert "the model chose kick(leg='left'), the first of 2" in status.__rich__().plain
    failed = {"provider": "anthropic", "error": "ProviderError: 400", "latency_s": 30.0}
    status.sink(LogEvent("llm", 0.0, failed))
    assert "the call to anthropic failed" in status.__rich__().plain
    status.sink(LogEvent("llm_request", 0.0, request))
    assert "(last call 1.0 s)" in status.__rich__().plain, "a failure's seconds are not kept"


def test_a_provider_that_names_no_model_still_reads_as_a_sentence() -> None:
    console, _ = wide()
    status = ui.RunStatus(console)
    status.sink(LogEvent("llm_request", 0.0, {"provider": "ollama", "model": None}))
    assert "waiting on ollama the first model it serves" in status.__rich__().plain


def test_the_status_line_follows_a_run_through_its_phases() -> None:
    console, _ = wide()
    status = ui.RunStatus(console)
    for event, expected in (
        (LogEvent("verb_start", 0.0, {"name": "go_to", "params": {"target": "ball"}}), "go_to("),
        (LogEvent("verb_end", 0.0, {"name": "go_to", "ok": True}), "observing"),
        (LogEvent("claim", 0.0, {"kicker": "duck-1", "dist": 0.6}), "claim duck-1"),
        (LogEvent("note", 0.0, {"text": "kill switch: Ctrl-C"}), "stopping the robot"),
        (LogEvent("run_end", 0.0, {"outcome": "success"}), "finishing"),
    ):
        status.sink(event)
        assert expected in status.__rich__().plain, event.kind


def test_a_status_line_that_cannot_draw_never_counts_as_a_dropped_event() -> None:
    """It is decoration. The count is what the run could not *show*, and what shows things
    is the log."""
    console, _ = wide()
    status = ui.RunStatus(console)
    status.sink(object())  # not an event at all
    status.sink(LogEvent("verb_start", 0.0, {"params": "not a mapping"}))


def test_pausing_takes_the_line_down_so_a_prompt_can_be_seen() -> None:
    """A live region redirects stdout and a `y/N` prompt writes without a newline, so under
    a running status the question is invisible until the answer has been typed."""
    console, _ = wide(force_terminal=True)
    with ui.RunStatus(console) as status:
        assert status.active
        with ui.pause_status():
            assert not status._status._live.is_started
        assert status._status._live.is_started


def test_pausing_when_nothing_is_up_is_allowed() -> None:
    with ui.pause_status():
        pass
    console, _ = wide()
    with ui.RunStatus(console) as status, status.paused():
        pass


def test_a_spinner_stands_aside_for_a_status_line_already_on_screen() -> None:
    """Rich 13.7, which is the floor this project declares, refuses a second live region on
    one console."""
    console, buf = wide(force_terminal=True)
    with ui.RunStatus(console), ui.spinner("probing", target=console) as say:
        say("still probing")
    assert "probing" not in buf.getvalue()


def test_a_spinner_says_nothing_into_a_pipe() -> None:
    console, buf = wide()
    with ui.spinner("listening for robots", target=console) as say:
        say("still listening")
    assert buf.getvalue() == ""


def test_the_status_stack_empties_even_when_the_run_raises() -> None:
    console, _ = wide(force_terminal=True)
    with pytest.raises(KeyboardInterrupt), ui.RunStatus(console):
        raise KeyboardInterrupt
    assert not ui._ACTIVE


def test_installing_the_log_handler_leaves_records_reaching_a_root_handler() -> None:
    """Turning off propagation is the obvious way to stop a record being handled twice, and
    it would have been wrong: quackd puts no handler on the root logger, so there is nothing
    to stop, and what it would really have stopped is pytest's own caplog, which is how four
    tests read what the MCP server logged."""
    import logging

    seen: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    ui.install_logging()
    ui.install_logging()  # twice: it must replace its handler, not stack a second one
    quackd_log = logging.getLogger("quackd")
    assert quackd_log.propagate is True
    assert sum(1 for h in quackd_log.handlers if type(h).__name__ == "RichHandler") == 1

    root, handler = logging.getLogger(), Capture()
    root.addHandler(handler)
    try:
        logging.getLogger("quackd.mcp").warning("a line an MCP test would assert on")
    finally:
        root.removeHandler(handler)
        for h in list(quackd_log.handlers):
            quackd_log.removeHandler(h)
    assert seen == ["a line an MCP test would assert on"]


async def test_a_release_the_arm_refused_does_not_invite_anybody_to_pick_it_up() -> None:
    """`stage` is the moment that was asked for; `how` is what the arm answered. The status
    line read the stage, so a release the arm refused still put "waiting for you to place the
    arm" under the run, and left it there through the stop and the fold, over an arm that had
    never gone limp and that nobody should be reaching for."""
    status = ui.RunStatus(Console(file=io.StringIO(), force_terminal=False))
    status.sink(LogEvent(t=0.0, kind="hand_off", data={"stage": "released", "how": "refused"}))
    assert status._text != "waiting for you to place the arm"

    status.sink(LogEvent(t=0.0, kind="hand_off", data={"stage": "released", "how": "released"}))
    assert status._text == "waiting for you to place the arm"


# ── the terminal capture ────────────────────────────────────────────────────────────────


def test_both_consoles_pour_into_the_file_in_the_order_they_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The answer goes to stdout and the narration to stderr, and what you saw was one
    terminal with the two interleaved. The file is that terminal, not either stream."""
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    ui.console.print("the answer")
    ui.err_console.print("the narration")
    ui.console.print("and the verdict")
    capture.close()
    out, err = capsys.readouterr()
    assert out == "the answer\nand the verdict\n"
    assert err == "the narration\n"
    assert saved(tmp_path) == "the answer\nthe narration\nand the verdict\n"


def test_a_status_line_leaves_exactly_the_line_the_run_printed(tmp_path: Path) -> None:
    """Rich puts two things on a terminal around a live region that mean nothing in a file.
    Every refresh prints a bare `Control`, and `Live.stop` asks for a trailing blank line
    through `console.line()` and then erases it again with a cursor control that never goes
    through `print`. Following either into the file would cost a blank line and a spinner
    frame for every prompt, every pause and every status that was ever up."""
    console, _ = tee(force_terminal=True)
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    with ui.RunStatus(console) as status:
        status._status._live.refresh()
        console.print("a line of narration")
        with status.paused():
            pass
    capture.close()
    out = saved(tmp_path)
    assert out == "a line of narration\n"
    assert "starting" not in out, "the spinner's own text is not a line the run printed"
    assert "\x1b" not in out, "nor are the codes it was drawn with"


def test_a_live_region_frame_puts_nothing_in_the_file(tmp_path: Path) -> None:
    """A frame draws nothing off a terminal anyway, and a file is never a terminal."""
    console, _ = tee(force_terminal=True)
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    console.print(Control())
    console.print(Control.show_cursor(False))
    capture.close()
    assert saved(tmp_path) == ""


def test_the_blank_lines_in_the_file_are_the_ones_the_run_asked_for(tmp_path: Path) -> None:
    """`print` is quackd talking and `line` is Rich tidying up after its own live region."""
    console, buf = tee()
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    console.print("kept")
    console.line(2)
    capture.close()
    assert buf.getvalue() == "kept\n\n\n"
    assert saved(tmp_path) == "kept\n"


def test_a_note_reaches_the_file_and_never_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What you typed at a prompt is echoed by the terminal itself, so quackd never prints it
    and a tee cannot see it. Without this the file would hold every question and no answer."""
    console, buf = tee()
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    console.print("place the arm on the table")
    ui.note("[y/N] y")
    capture.close()
    assert buf.getvalue() == "place the arm on the table\n"
    assert capsys.readouterr() == ("", "")
    assert saved(tmp_path) == "place the arm on the table\n[y/N] y\n"


def test_attaching_carries_the_buffer_in_with_the_header_first(tmp_path: Path) -> None:
    """The first thing a run prints is its header and the first thing a bad flag prints is an
    error, and neither of them has a run directory to go in yet."""
    console, _ = tee()
    capture = ui.begin_capture(header=("$ quackd run find-and-kick", ""))
    console.print("before there was anywhere to put it")
    assert capture.path is None
    assert list(tmp_path.iterdir()) == []
    assert capture.attach(tmp_path) == tmp_path / "terminal.txt"
    console.print("and after")
    capture.close()
    assert saved(tmp_path) == (
        "$ quackd run find-and-kick\n\nbefore there was anywhere to put it\nand after\n"
    )


def test_a_run_that_never_got_a_directory_leaves_nothing_behind(tmp_path: Path) -> None:
    """A command refused on a bad flag has printed a sentence and made no run. It must not
    leave a directory, or a file in somebody else's."""
    console, buf = tee()
    capture = ui.begin_capture(header=("$ quackd run --robot nonsense",))
    console.print("error: no adapter called nonsense")
    capture.close()
    assert capture.path is None
    assert list(tmp_path.iterdir()) == []
    assert buf.getvalue() == "error: no adapter called nonsense\n"


def test_the_file_wears_the_glyphs_the_screen_wore(tmp_path: Path) -> None:
    """The sink's `encoding` is set to the source console's on every forward, and that is the
    whole of how this works: Rich reads `ascii_only` off that attribute and nothing else. A
    run redirected on a Windows codepage draws an ASCII box, and so does its saved terminal.

    The file is compared against the screen rather than against a box drawn here, because
    which box a screen gets is the operating system's answer and not this test's: Rich swaps
    the rounded corners for square ones on a legacy Windows console, so a corner written into
    the assertion passes on the machine it was written on and fails on the other two. What is
    being claimed is that the two agree, so that is what is asserted."""
    for encoding, mark, ascii_only in (("cp1252", "+", True), ("utf-8", "✓", False)):
        raw = io.BytesIO()
        console = ui.TeeConsole(
            file=io.TextIOWrapper(raw, encoding=encoding, errors="replace"),
            width=70,
            theme=ui.THEME,
            highlight=False,
            emoji=False,
        )
        run_dir = tmp_path / encoding
        run_dir.mkdir()
        capture = ui.begin_capture()
        capture.attach(run_dir)
        console.print(ui.verdict("success", "kicked it"))
        console.file.flush()
        capture.close()
        out = saved(run_dir)
        screen = raw.getvalue().decode(encoding, "replace")
        # the corner each of them drew, which is the glyph claim. Not the whole line: the
        # capture resolves its own width from the console it was opened against, so the two
        # boxes are the same drawing at two widths.
        assert out.splitlines()[0][0] == screen.splitlines()[0][0], encoding
        assert set(out.splitlines()[0]) == set(screen.splitlines()[0]), encoding
        assert f"{mark} SUCCESS" in out, encoding
        assert out.isascii() is ascii_only, encoding


def test_the_screen_keeps_its_colour_and_the_file_carries_none(tmp_path: Path) -> None:
    """The capture console is a plain one with no colour system, so a style is a pass-through
    rather than an escape code somebody has to strip to read the file."""
    console, buf = tee(60, force_terminal=True)
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    console.print(ui.verdict("success", "kicked it"))
    capture.close()
    assert "\x1b[32m" in buf.getvalue(), "green on the terminal it was drawn for"
    out = saved(tmp_path)
    assert "SUCCESS" in out and "kicked it" in out
    assert "\x1b" not in out


ESCAPED = "\x1b[2Jthe ball is \x1b[31mbehind\x1b[0m the sofa"
"""A goal somebody typed, or a line of a `.duck` fetched off the internet. `ESC[2J` clears
the screen of whoever cats the file and `ESC[31m` repaints the rest of it red."""


def test_an_escape_sequence_drives_the_terminal_and_never_the_file(tmp_path: Path) -> None:
    """The text is not ours. Rich strips its own small set on the way out and ESC has never
    been in it, so an escape that arrived inside a string rather than from a style reaches
    both ends. The screen is the one that was meant to obey it."""
    console, buf = tee()
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    console.print(ESCAPED)
    capture.close()
    assert buf.getvalue() == f"{ESCAPED}\n", "the terminal it was drawn for sees all of it"
    out = saved(tmp_path)
    assert "\x1b" not in out
    assert out == "[2Jthe ball is [31mbehind[0m the sofa\n", "inert, and still readable"


def test_the_other_characters_that_drive_a_terminal_are_dropped_too(tmp_path: Path) -> None:
    """A bell, a carriage return, a window title and a delete. Rich's own set covers the
    bell and the carriage return before either end sees them; the ESC and the DEL are this
    file's to take out, which is why the screen still has both."""
    said = "\x07the arm stalled\x7f\r\x1b]0;quackd owns your title bar\x07 at 0.3 rad"
    console, buf = tee()
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    console.print(said)
    capture.close()
    assert "\x1b" in buf.getvalue() and "\x7f" in buf.getvalue()
    out = saved(tmp_path)
    for control in ("\x07", "\r", "\x1b", "\x7f"):
        assert control not in out, repr(control)
    assert out == "the arm stalled]0;quackd owns your title bar at 0.3 rad\n"


def test_tab_and_newline_are_layout_and_are_kept(tmp_path: Path) -> None:
    """The two a reader wants and no terminal obeys. A tab seldom reaches the file as a tab,
    because Rich expands one into its columns while it renders, so the filter itself is the
    only place that half of the rule can be read."""
    assert ui._printable("posture\tstanding\nballs\t1\n") == "posture\tstanding\nballs\t1\n"
    assert ui._printable("\x00\x07\r\x1b\x7f") == "", "and none of the rest of C0"
    console, _ = tee()
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    console.print("state: posture=standing\nballs seen: 1")
    capture.close()
    assert saved(tmp_path) == "state: posture=standing\nballs seen: 1\n"


def test_a_file_that_cannot_be_written_costs_the_terminal_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capture is a copy of what happened. Losing it is worth a line in nobody's run, so
    the first failure disables it and is remembered instead of raised."""
    console, buf = tee()
    capture = ui.begin_capture()

    def boom(_text: str) -> int:
        raise OSError("the disk went away")

    monkeypatch.setattr(capture._sink, "write", boom)
    console.print("the run goes on")
    console.print("and on")
    assert buf.getvalue() == "the run goes on\nand on\n"
    assert capture.broken == "OSError: the disk went away"
    assert capture.attach(tmp_path) is None
    assert list(tmp_path.iterdir()) == [], "a capture that broke leaves no half a file"


def test_a_capture_that_broke_says_where_it_stopped_in_the_file_and_on_the_screen(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capture that stops forwarding leaves a file that stops, which reads exactly like a
    run that stopped there. That is a wrong answer to the only question the file exists to
    answer, so the truncation is written where it will be read and said once on the screen
    while somebody is still standing in front of it."""
    console, buf = tee()
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    console.print("the last line anybody saw")
    writing = capture._sink.write
    to_lose = [1]

    def the_disk_goes_away(text: str) -> int:
        # one write, because what breaks a capture is between it and the file: a render that
        # raised, a codec, a disk that came back. The handle close() writes through still works
        if to_lose:
            to_lose.pop()
            raise OSError("the disk went away")
        return writing(text)

    monkeypatch.setattr(capture._sink, "write", the_disk_goes_away)
    console.print("and the one after it")
    console.print("and the one after that")
    assert capture.broken == "OSError: the disk went away"
    capture.close()

    assert buf.getvalue() == (
        "the last line anybody saw\nand the one after it\nand the one after that\n"
    ), "a file that cannot be written still costs the terminal nothing"
    assert saved(tmp_path) == (
        "the last line anybody saw\n\n(the terminal log stops here: OSError: the disk went away)\n"
    )
    err = capsys.readouterr().err
    assert err.count("terminal.txt is not the whole session") == 1, "once, not once a line"
    assert "OSError: the disk went away" in err


def test_a_capture_that_ran_to_the_end_says_nothing_about_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The line and the warning are how a short file tells you it is short. A whole one that
    carried either would teach a reader to ignore both."""
    console, _ = tee()
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    console.print("the whole session")
    capture.close()
    assert capture.broken is None
    assert saved(tmp_path) == "the whole session\n"
    assert capsys.readouterr() == ("", "")


def test_configure_leaves_an_open_capture_capturing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--no-color` rebuilds both consoles in the middle of a command that has already
    printed. The capture survives it because it is looked up at print time rather than held
    by the console, and the new pair forwards into it exactly as the old pair did."""
    capture = ui.begin_capture()
    capture.attach(tmp_path)
    ui.console.print("before")
    ui.configure(no_color=True)
    ui.console.print("after")
    ui.configure()
    capture.close()
    capsys.readouterr()
    assert saved(tmp_path) == "before\nafter\n"


def test_what_the_loggers_said_is_part_of_what_you_saw(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A warning from the camera or the transport is on the screen and belongs in the file
    with everything else that was, which it gets for free by going through `err_console`."""
    import logging

    capture = ui.begin_capture()
    capture.attach(tmp_path)
    ui.install_logging()
    quackd_log = logging.getLogger("quackd")
    try:
        logging.getLogger("quackd.transport").warning("the camera stopped answering")
    finally:
        for handler in list(quackd_log.handlers):
            quackd_log.removeHandler(handler)
    capture.close()
    capsys.readouterr()
    assert "the camera stopped answering" in saved(tmp_path)
