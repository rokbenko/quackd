"""`quackd log` replays a finished run. What it prints has to be what the run printed."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.cli import app

from .conftest import help_text, plain_text

runner = CliRunner()


WIDE = {"COLUMNS": "200"}
"""Rich folds a panel at the terminal width, and at the default eighty columns the line of
counters breaks in the middle of `cost $0.0327`, with the panel's border sitting between the
two halves. Several tests here read that line, so everything here is run wide enough to leave
it whole."""


def _run_output(tmp_path: Path, *flags: str) -> str:
    """One real run under `tmp_path`, and what it printed.

    Says nothing about how many run directories are there afterwards: the label test below
    makes two on purpose."""
    result = runner.invoke(
        app,
        [
            "run",
            "find-and-kick",
            "--llm",
            "fake",
            "--seed",
            "3",
            "--robot",
            "microduck:sim2d",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
            *flags,
        ],
        env=WIDE,
    )
    assert result.exit_code == 0, result.output
    return result.output


def _run(tmp_path: Path, *flags: str) -> Path:
    """One real run under `tmp_path`, and the directory it wrote."""
    _run_output(tmp_path, *flags)
    (run_dir,) = [d for d in tmp_path.iterdir() if d.is_dir()]
    return run_dir


def _log_output(tmp_path: Path, *args: str) -> tuple[int, str]:
    """A replay's exit code and its output as it was printed, line breaks and all."""
    result = runner.invoke(app, ["log", *args, "--runs-dir", str(tmp_path)], env=WIDE)
    return result.exit_code, result.output


def _replay(tmp_path: Path, *args: str) -> tuple[int, str]:
    code, out = _log_output(tmp_path, *args)
    return code, " ".join(out.split())


def _counters(output: str) -> str:
    """The one line of counters under the verdict, without the panel's border and padding.

    Read as a line rather than as a substring of the whole output, so that "no cost counter"
    is a claim about the counters and not about whether the word appears somewhere on
    screen. Matched on `steps ` and not on `llm calls`, which the rule above every turn of a
    replay also says, as `step 3/40, llm calls 3/40`."""
    lines = [
        line.strip("│| ").strip()
        for line in output.splitlines()
        if "steps " in line and "llm calls" in line
    ]
    assert len(lines) == 1, output
    return lines[0]


def test_log_replays_a_finished_run(tmp_path: Path) -> None:
    """The point of the command: the run is over, and you want to read what happened."""
    run_dir = _run(tmp_path)
    code, out = _replay(tmp_path, run_dir.name)
    assert code == 0, out
    assert "system prompt" in out
    assert "send move" in out and "result walk_to ok" in out
    assert "[scripted]" in out  # the model's reasoning, as the run showed it
    assert "SUCCESS" in out and "llm calls" in out


def test_a_decision_block_with_no_cost_key_gains_no_cost_counter(
    tmp_path: Path,
) -> None:
    """Every stepper run recorded before this change already wrote its own block, so gating
    the cost counter on that block gave those records a fourth counter reading `cost unpriced`
    that they never had and that says nothing true about them: nobody tried to price them.
    The gate is a cost key, and a stepper block without one is a run from before there were
    any."""
    run_dir = tmp_path / "20260101-120000-arm-grip-check"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "duck": "arm-grip-check",
                "outcome": "success",
                "reason": "the gripper reported closed",
                "steps": 6,
                "llm_calls": 4,
                "elapsed_s": 31.2,
                "usage": {"input_tokens": 9000, "output_tokens": 120},
                "decision": {
                    "mode": "on",
                    "llm": "jev",
                    "model": "jev-1.13.0",
                    "asked": 6,
                    "taken": 4,
                    "errors": 0,
                    "latency_s": 0.7,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "transcript.jsonl").write_text("", encoding="utf-8")

    code, out = _replay(tmp_path, run_dir.name)
    assert code == 0, out
    assert "steps 6" in out and "llm calls 4" in out and "tokens 9000+120" in out
    assert "cost" not in out, out
    assert "time" not in out, "and no clock either: it recorded none"


def test_log_survives_a_summary_somebody_edited_by_hand(tmp_path: Path) -> None:
    """`quackd log` is pointed at files people edit, truncate and copy between machines. The
    renderers beside the counter line already shrug at a field that is not what it should be,
    and a counter is not worth a traceback."""
    run_dir = tmp_path / "20260101-120000-hand-edited"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "duck": "hand-edited",
                "outcome": "success",
                "reason": "somebody was in here with an editor",
                "steps": "six",
                "llm_calls": None,
                "usage": {},
                "wall_s": "a while",
                "cost_usd": "free",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "transcript.jsonl").write_text("", encoding="utf-8")

    code, out = _replay(tmp_path, run_dir.name)
    assert code == 0, out
    assert "cost unpriced" in out, "a cost nobody can read is not a cost of nothing"


def test_log_with_no_argument_picks_the_newest_run(tmp_path: Path) -> None:
    """What you want right after a run ends is the run that just ended."""
    _run(tmp_path)
    first = sorted(d.name for d in tmp_path.iterdir())[0]
    newest = tmp_path / "20991231-235959-later"
    newest.mkdir()
    (newest / "transcript.jsonl").write_text(
        json.dumps({"t": 0.0, "kind": "run_end", "outcome": "success", "reason": "the newer one"})
        + "\n",
        encoding="utf-8",
    )
    code, out = _replay(tmp_path)
    assert code == 0 and "the newer one" in out
    assert first not in out


def test_log_resolves_a_timestamp_prefix_and_a_duck_name(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    stamp = run_dir.name.split("-")[0]
    for typed in (stamp, "find-and-kick"):
        code, out = _replay(tmp_path, typed)
        assert code == 0, (typed, out)
        assert "SUCCESS" in out


def test_log_accepts_the_transcript_file_itself(tmp_path: Path) -> None:
    """A transcript someone copied out of a run directory is still a run to read."""
    run_dir = _run(tmp_path)
    code, out = _replay(tmp_path, str(run_dir / "transcript.jsonl"))
    assert code == 0 and "result walk_to ok" in out


def test_log_from_step_skips_earlier_turns(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    code, out = _replay(tmp_path, run_dir.name, "--from-step", "2")
    assert code == 0
    assert "step 2/40" in out
    assert "step 0/40" not in out and "step 1/40" not in out
    # the panel, not the run directory that also contains the name: a replay has no other
    # header, so this is the only thing saying what is being replayed
    assert "provider fake (scripted:find-and-kick)" in out


def test_log_no_prompt_and_thinking_flags(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _, full = _replay(tmp_path, run_dir.name)
    _, terse = _replay(tmp_path, run_dir.name, "--no-prompt", "--thinking", "0")
    assert "system prompt" in full and "[scripted]" in full
    assert "system prompt" not in terse and "[scripted]" not in terse
    assert "result walk_to ok" in terse  # everything else survives


def test_log_frames_are_off_unless_asked(tmp_path: Path) -> None:
    """One line per camera frame buries the four lines a reader came for."""
    run_dir = _run(tmp_path, "--vision")
    frames = [
        r
        for r in (json.loads(x) for x in (run_dir / "transcript.jsonl").read_text().splitlines())
        if r["kind"] == "frame"
    ]
    if not frames:  # a run without a camera has nothing to hide
        return
    needle = Path(frames[0]["path"]).name  # the prompt itself says "camera frame"
    _, without = _replay(tmp_path, run_dir.name, "--no-prompt")
    _, with_them = _replay(tmp_path, run_dir.name, "--no-prompt", "--frames")
    assert needle not in without
    assert needle in with_them


def test_log_of_a_transcript_without_verb_end_still_shows_results(tmp_path: Path) -> None:
    """The bundled example predates the log: it has `verb` records and no `verb_end`, and
    a replay that showed no results at all would be a replay of nothing."""
    code, out = _replay(tmp_path, "docs/assets/transcript-example.jsonl")
    assert code == 0, out
    assert "result search_scan" in out or "result quack" in out
    assert out.count("result quack ok") <= 1, "a live transcript has both kinds and must not double"


def test_log_of_a_cut_transcript_says_so(tmp_path: Path) -> None:
    """A run killed mid-write leaves half a line. Show the run, and say a line was lost."""
    run_dir = _run(tmp_path)
    path = run_dir / "transcript.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"t": 9.9, "kind": "not', encoding="utf-8")
    code, out = _replay(tmp_path, run_dir.name)
    assert code == 0, out
    assert "1 unreadable line(s) skipped" in out
    assert "SUCCESS" in out


def test_log_on_a_missing_run_is_one_line(tmp_path: Path) -> None:
    _run(tmp_path)
    code, out = _replay(tmp_path, "no-such-run")
    assert code == 1
    assert "no run matching" in out and "newest:" in out


def test_log_on_an_empty_runs_dir_says_what_to_do(tmp_path: Path) -> None:
    code, out = _replay(tmp_path)
    assert code == 1 and "quackd run" in out


def test_log_on_a_flock_run_names_every_member(tmp_path: Path) -> None:
    """A flock writes no top-level transcript: the record is one file per robot, and a
    replay that read only the first of them would quietly show a third of the run."""
    result = runner.invoke(
        app,
        [
            "run",
            "flock-kick",
            "--llm",
            "fake",
            "--seed",
            "3",
            "--no-gif",
            "--runs-dir",
            str(tmp_path),
            "--no-log",
        ],
    )
    assert result.exit_code == 0, result.output
    code, out = _replay(tmp_path, "--no-prompt", "--thinking", "0")
    assert code == 0, out
    for name in ("duck-0", "duck-1", "duck-2"):
        # the glyph gutter sits between the member's name and the label it prefixes
        assert f"{name} ▶ verb" in out, (name, out[:400])
        assert f"{name} ■ end stopped after" in out
    # the outcome comes from summary.json, since no member wrote a run_end
    assert "SUCCESS" in out and "kicker duck-" in out
    assert "llm calls" not in out, "a flock counts auctions and bids, not steps and tokens"


# ── the clocks, the price, and the name the run was given ───────────────────────────────


def test_a_replay_prints_the_time_and_the_cost_the_run_itself_printed(tmp_path: Path) -> None:
    """A replay is the run again, so its counters have to be the same line rather than a
    second dialect that drifts from the first: the live verdict, `run_end` and `summary.json`
    are one dict through one function, and this asserts the two lines character for character.

    The header is what a replay has to add for itself. Until the record carried a wall clock
    the directory name was the only thing that said when a run happened, and a directory gets
    copied and renamed; the price is there because a cost figure with no rate beside it is a
    number nobody can argue with."""
    live = _run_output(tmp_path, "--run-name", "Example 1", "--price", "in=3,out=15")
    counters = _counters(live)
    assert "time " in counters and "cost $" in counters, counters

    code, out = _log_output(tmp_path, "example-1")
    assert code == 0, out
    assert _counters(out) == counters

    (run_dir,) = [d for d in tmp_path.iterdir() if d.is_dir()]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    flat = " ".join(out.split())
    started = str(summary["started_at"]).replace("T", " ").replace("Z", " UTC")
    assert f"started {started}" in flat, flat[:400]
    assert "run name Example 1" in flat, "the header shows the name as typed, not the slug"
    assert "price $3/M in, $15/M out (--price)" in flat


def test_log_prefers_the_run_whose_name_ends_in_the_label_over_a_newer_one(
    tmp_path: Path,
) -> None:
    """The whole reason the `--run-name` pass exists, and the ten minutes it saves.

    A bench session has `-example-1` and `-example-19` in it. The loose substring match that
    used to be the last resort walks the runs newest first and returns the first name the text
    appears in, which is `-example-19` every time: you type the name you gave a run and get a
    different run, with nothing on screen to say so."""
    _run_output(tmp_path, "--run-name", "example 1")
    _run_output(tmp_path, "--run-name", "example 19")
    names = sorted(d.name for d in tmp_path.iterdir() if d.is_dir())
    (one,) = [n for n in names if n.endswith("-example-1")]
    (nineteen,) = [n for n in names if n.endswith("-example-19")]
    assert nineteen > one, "the trap is that the decoy sorts newest; these two do not"

    code, out = _replay(tmp_path, "example-1")
    assert code == 0, out
    assert nineteen not in out, "the substring pass answered before the label pass"
    assert one in out


def test_a_summary_written_before_the_clocks_replays_with_the_counters_it_had(
    tmp_path: Path,
) -> None:
    """The compatibility guarantee, written out as the thing it protects: a summary from
    before this release carries steps, llm_calls, usage, outcome and reason and nothing else,
    and `quackd log` has to replay it with exactly the three counters it printed then.

    Not `time None`, and not a cost of zero dollars for a run nobody priced. Every field
    `run_counters` reads is optional for this reason, and the time and the cost appear only
    when the summary really carries them.

    The transcript here has no `run_end` in it, which is what sends the replay to
    `summary.json` for the outcome: a run from back then, read through the file that is meant
    to outlive its transcript."""
    run_dir = tmp_path / "20260101-120000-hello-world"
    run_dir.mkdir()
    (run_dir / "transcript.jsonl").write_text(
        json.dumps(
            {"t": 0.4, "kind": "verb_end", "name": "quack", "outcome": "ok", "summary": "quacked"}
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "steps": 3,
                "llm_calls": 5,
                "usage": {"input_tokens": 120, "output_tokens": 40},
                "outcome": "success",
                "reason": "quacked and walked one step",
            }
        ),
        encoding="utf-8",
    )
    code, out = _log_output(tmp_path, run_dir.name)
    assert code == 0, out
    counters = _counters(out)
    assert counters.startswith("steps 3"), counters
    assert "llm calls 5" in counters and "tokens 120+40" in counters, counters
    assert "time" not in counters and "cost" not in counters, counters
    assert "SUCCESS" in out and "quacked and walked one step" in out


# ── the spellings the trace left behind, gone in 0.12 ───────────────────────────────────


def _typed(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    """What the saved terminal records as the command that started the run.

    `_terminal_header` reads `sys.argv` for the `$ quackd ...` line at the top of the file.
    CliRunner passes its arguments to the command directly and leaves `sys.argv` alone, so
    without this the header of a run made here opens with pytest's own command line. Nothing
    about a deprecation reads argv any more; this is only the recorded command."""
    monkeypatch.setattr(sys, "argv", ["quackd", *argv])


def _deprecations(stderr: str) -> list[str]:
    """Every deprecation line on stderr and nothing else, so "exactly one" is a claim about
    how many were printed rather than about whether the text appears somewhere."""
    return [line.strip() for line in stderr.splitlines() if "not read any more" in line]


def _name_warning(old: str, new: str) -> str:
    """The line a name this release stopped reading prints when it finds one set, spelled out
    here so a change to it has to be deliberate.

    It is the sentence `QUACKD_MODEL` gets, and it is here for the same reason: a flag that is
    gone fails loudly, because Click refuses it and names it, while a variable that is gone
    says nothing at all unless something says it."""
    return f"{old} is not read any more and this run ignores it; set {new} instead"


def _narration(tmp_path: Path, *flags: str) -> tuple[str, list[str]]:
    """One run on the mock, what it narrated, and the deprecation lines it printed.

    The mock sends no frames and writes no GIF, so stderr carries the live view and the
    warnings and nothing else. The suite runs with the live view off (conftest), which is
    what makes a variable that turns it on visible here as a change."""
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--no-gif",
            "--runs-dir",
            str(tmp_path),
            *flags,
        ],
        env=WIDE,
    )
    assert result.exit_code == 0, result.output
    return " ".join(result.stderr.split()), _deprecations(result.stderr)


def _narrow_run(tmp_path: Path, *flags: str) -> list[str]:
    """The same run at the eighty columns a terminal has when nobody widened it, line by line.

    Everything else in this file runs at two hundred so that the panel's counters stay whole,
    and that width is exactly what would hide a warning folded at eighty. Not flattened
    either, for the same reason: a fold is a line break and nothing else."""
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--no-gif",
            "--runs-dir",
            str(tmp_path),
            *flags,
        ],
        env={"COLUMNS": "80"},
    )
    assert result.exit_code == 0, result.output
    return [line.strip() for line in result.stderr.splitlines()]


def _terminal(tmp_path: Path) -> list[str]:
    """The saved terminal of the one run under `tmp_path`, line by line.

    Read off the disk rather than out of the runner's streams, because what this file has to
    keep is what a reader opens afterwards and not what the run happened to print."""
    (path,) = list(tmp_path.rglob("terminal.txt"))
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_run_that_asked_the_model_again_says_so_in_its_counters() -> None:
    """ADR-0059: a turn the API could not answer is asked again and is not a call of its own,
    so `llm calls` stays the task's count and the tries ride beside it, with the seconds
    waited in the split, kept out of the model's. A run that never asked again prints exactly
    the counters it always did."""
    from quackd.cli import run_counters
    from quackd.log import fmt_duration

    end = {
        "steps": 4,
        "llm_calls": 6,
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "wall_s": 70.0,
        "llm_latency_s": 9.0,
    }
    before = run_counters(end)
    assert before[1] == "llm calls 6" and "waits" not in before[-1], before
    after = run_counters({**end, "llm_retries": 2, "llm_retry_wait_s": 20.0})
    assert after[1] == "llm calls 6 (+2 asked again)", after
    (spent,) = [c for c in after if c.startswith("time")]
    assert f"model {fmt_duration(9.0)}, waits {fmt_duration(20.0)}" in spent, spent


def test_the_counters_say_how_the_calls_spread_and_when_the_robot_first_moved() -> None:
    """The median, the ninetieth percentile and the longest call of the model and of the
    stepper, and how long the robot took to move and after how many model calls, each said only
    where its keys are. A record written before them replays with the counters it always had,
    and a spread with one figure missing is no spread rather than a counter with a hole in it."""
    from quackd.cli import run_counters
    from quackd.log import fmt_duration

    end: dict[str, Any] = {
        "steps": 4,
        "llm_calls": 6,
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "wall_s": 79.0,
        "llm_latency_s": 62.1,
    }
    old = run_counters(end)
    spread = {"llm_latency_p50_s": 6.2, "llm_latency_p90_s": 8.0, "llm_latency_max_s": 8.2}
    stepper = {"latency_p50_s": 0.31, "latency_p90_s": 0.62, "latency_max_s": 0.7}
    new = run_counters(
        {
            **end,
            **spread,
            "decision": {"latency_s": 2.0, **stepper},
            "first_motion_s": 25.5,
            "first_motion_llm_calls": 3,
        }
    )
    model = f"model calls p50 {fmt_duration(6.2)}, p90 {fmt_duration(8.0)}, max {fmt_duration(8.2)}"
    assert model in new, new
    assert f"stepper p50 {fmt_duration(0.31)}, p90 {fmt_duration(0.62)}" in " · ".join(new), new
    assert f"first motion at {fmt_duration(25.5)}, after 3 model calls" in new, new
    assert not any(c.startswith(("model calls", "stepper p50", "first motion")) for c in old), old

    one = run_counters({**end, "first_motion_s": 9.0, "first_motion_llm_calls": 1})
    assert f"first motion at {fmt_duration(9.0)}, after 1 model call" in one, one
    unmoved = run_counters({**end, "first_motion_s": None, "first_motion_llm_calls": None})
    assert not any(c.startswith("first motion") for c in unmoved), unmoved
    half = run_counters({**end, "llm_latency_p50_s": 6.2, "llm_latency_max_s": 8.2})
    assert not any(c.startswith("model calls") for c in half), half


def test_the_help_offers_log_and_trace_is_no_command() -> None:
    """0.11 kept `quackd trace` alive for one release, hidden from `--help` so that nobody
    learned a name that was going, and said in twelve files that it went in 0.12. It went.

    Refused rather than aliased, which is the shape 0.5 used on the flag `--robot` replaced:
    Click names the command it does not have, and a script that still types the old one stops
    rather than carrying on under a spelling nobody maintains. The phrase is asserted and not
    the styled name, because the runner forces colour and Typer's highlighter splits the
    quoted word across spans."""
    flat = help_text(["--help"])
    assert "log Replay a finished run" in flat
    assert "trace" not in flat, flat

    old = runner.invoke(app, ["trace"], env=WIDE)
    assert old.exit_code == 2, old.output
    assert "No such command" in plain_text(old.output), old.output


@pytest.mark.parametrize(
    ("command", "old"),
    [
        ("run", "--trace"),
        ("run", "--no-trace"),
        ("run", "--trace-prompt"),
        ("run", "--no-trace-prompt"),
        ("serve-mcp", "--trace"),
        ("serve-mcp", "--no-trace"),
    ],
)
def test_the_old_flags_are_refused_like_any_unknown_option(
    tmp_path: Path, command: str, old: str
) -> None:
    """Both halves of both pairs, on both commands that carried them.

    `serve-mcp` is here because its log switch is its own option object rather than the run's,
    so a spelling deleted from one and left on the other would pass a test that only ran the
    run. It is invoked with the flag and nothing else: it takes no `--runs-dir`, and passing
    one would let this pass on the wrong unknown option.

    Nothing is written, which is the other half of a refusal being a refusal: the parse fails
    before a run directory exists, so there is no half-run on disk to explain."""
    full = ["run", "hello-world", "--llm", "fake", "--robot", "microduck:mock", "--no-gif"]
    full += ["--runs-dir", str(tmp_path)]
    result = runner.invoke(app, (full if command == "run" else [command]) + [old], env=WIDE)

    # Read through `plain_text` because on GitHub Actions Typer forces colour and Rich
    # styles the name it is complaining about in pieces, so `--trace` reaches a substring
    # check as two spans with an escape sequence between the hyphens.
    refusal = plain_text(result.output)
    assert result.exit_code == 2, result.output
    assert "No such option" in refusal, refusal
    assert old in refusal, refusal
    assert not list(tmp_path.iterdir()), "a refused flag wrote a run directory"


@pytest.mark.parametrize(
    ("old", "new", "shows"),
    [
        ("QUACKD_TRACE", "QUACKD_LOG", "result quack ok"),
        ("QUACKD_TRACE_THINKING", "QUACKD_LOG_THINKING", "[scripted]"),
        ("QUACKD_TRACE_PROMPT", "QUACKD_LOG_PROMPT", "system prompt"),
    ],
)
def test_an_old_env_name_is_ignored_and_says_so_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old: str, new: str, shows: str
) -> None:
    """A variable that is gone goes quiet, and the quiet is the failure this line prevents.

    Each of these three sits in a `.env` somebody wrote months ago with the value `0`, and
    0.11 honoured it. Unread and unannounced, the same line would switch back on the very
    thing its author had turned off. So the run says what it ignored, and the proof that it
    really was ignored is that the thing `0` used to hide is on the screen.

    The new names are deleted rather than set, because the scenario is a `.env` carrying only
    the old one; the suite sets `QUACKD_LOG`, and leaving it would answer the question this
    test is asking."""
    for name in ("QUACKD_LOG", "QUACKD_LOG_THINKING", "QUACKD_LOG_PROMPT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(old, "0")

    narrated, said = _narration(tmp_path)
    assert shows in narrated, narrated
    assert said == [_name_warning(old, new)]


def test_every_old_name_that_is_set_is_named_not_only_the_one_a_run_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.11 could warn about a name only on a run that read it, so a `.env` with the log
    switched off heard about one of its three old names and nothing about the other two: with
    the log off, the thinking and the prompt settings were never consulted. Its own note said
    to read those warnings as a floor and not as a list.

    They are a list now. The names are read where the command line is, before anything asks
    what the log should do, so one run answers for the whole file."""
    for name in ("QUACKD_LOG", "QUACKD_LOG_THINKING", "QUACKD_LOG_PROMPT"):
        monkeypatch.delenv(name, raising=False)
    for name in ("QUACKD_TRACE", "QUACKD_TRACE_THINKING", "QUACKD_TRACE_PROMPT"):
        monkeypatch.setenv(name, "0")

    _, said = _narration(tmp_path)
    assert said == [
        _name_warning("QUACKD_TRACE", "QUACKD_LOG"),
        _name_warning("QUACKD_TRACE_THINKING", "QUACKD_LOG_THINKING"),
        _name_warning("QUACKD_TRACE_PROMPT", "QUACKD_LOG_PROMPT"),
    ]


def test_a_deprecation_line_is_not_folded_at_eighty_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The longest of these sentences is 99 characters, and it is an instruction a script
    greps for. Folded at the default width it breaks inside the new name, so the one part of
    the line worth reading is the part a grep no longer finds."""
    sentence = _name_warning("QUACKD_TRACE_THINKING", "QUACKD_LOG_THINKING")
    assert len(sentence) > 80, "this stopped being a test about folding"

    monkeypatch.delenv("QUACKD_LOG_THINKING", raising=False)
    monkeypatch.setenv("QUACKD_TRACE_THINKING", "0")
    lines = _narrow_run(tmp_path)
    assert sentence in lines, lines[:4]


def test_a_summary_written_before_the_rename_still_replays(tmp_path: Path) -> None:
    """A run directory is a file on disk that outlives the release that wrote it, and until
    0.11 the count of lines the console could not print was `trace_dropped`. Every reader
    takes both spellings, so the number a run really lost still reaches the panel.

    No `run_end` in the transcript, which is what sends the replay to `summary.json`: the
    file meant to outlive its transcript is the one that has to stay readable."""
    run_dir = tmp_path / "20260101-120000-before-the-rename"
    run_dir.mkdir()
    (run_dir / "transcript.jsonl").write_text(
        json.dumps(
            {"t": 0.4, "kind": "verb_end", "name": "quack", "outcome": "ok", "summary": "quacked"}
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "steps": 3,
                "llm_calls": 5,
                "usage": {"input_tokens": 120, "output_tokens": 40},
                "outcome": "success",
                "reason": "quacked and walked one step",
                "trace_dropped": 2,
            }
        ),
        encoding="utf-8",
    )
    code, out = _log_output(tmp_path, run_dir.name)
    assert code == 0, out
    assert _counters(out).startswith("steps 3"), out
    flat = " ".join(out.split())
    assert "SUCCESS" in flat and "quacked and walked one step" in flat
    assert "log: 2 line(s) could not be shown" in flat, "the old key never reached the panel"


@pytest.mark.parametrize(
    ("dropped", "says"),
    [
        ("2", ""),
        (2.7, "log: 2 line(s) could not be shown"),
        (None, ""),
        ("twenty", ""),
    ],
    ids=["a number in quotes", "a fraction", "null", "a word"],
)
def test_a_dropped_counter_nobody_can_read_replays_as_no_counter(
    tmp_path: Path, dropped: object, says: str
) -> None:
    """The one counter that reaches the panel as an `int()` rather than through `_number`
    used to be this one, and a summary somebody had opened in an editor ended the replay in a
    traceback instead of showing the run.

    A figure that is not a figure is not a count of lost lines, so the panel says nothing
    about dropped lines at all rather than guessing at one. A real number still counts, which
    is what the fraction is here for: it is the only one of these four that says anything."""
    run_dir = tmp_path / "20260101-120000-hand-edited-counter"
    run_dir.mkdir()
    (run_dir / "transcript.jsonl").write_text("", encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "steps": 3,
                "llm_calls": 5,
                "usage": {"input_tokens": 120, "output_tokens": 40},
                "outcome": "success",
                "reason": "quacked and walked one step",
                "log_dropped": dropped,
            }
        ),
        encoding="utf-8",
    )
    code, out = _log_output(tmp_path, run_dir.name)
    assert code == 0, out
    flat = " ".join(out.split())
    assert "SUCCESS" in flat and _counters(out).startswith("steps 3"), flat
    if says:
        assert says in flat, flat
    else:
        assert "could not be shown" not in flat, flat


# ── and what the saved terminal keeps of all this ───────────────────────────────────────


def test_an_ignored_name_is_written_down_above_the_header_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root callback says this before there is a run directory to write it into, which is
    the one thing a run says with nowhere to keep it. The header replays it rather than the
    warning being moved later, because on the screen it came before the panel and a saved
    terminal that reorders what you saw is a record of a different afternoon.

    Until 0.12 this line was said from inside `quackd.log`, at the moment the value was read,
    which is after the capture opens. It is read beside `QUACKD_MODEL` in the callback now, so
    it moves up to where a reader opening the file meets it first.

    `QUACKD_LOG` is set to empty rather than deleted: an empty value is on, so the panel is
    drawn, and setting it at all shields this from a developer's own `.env`."""
    _typed(monkeypatch, "run", "hello-world")
    monkeypatch.setenv("QUACKD_LOG", "")
    monkeypatch.setenv("QUACKD_TRACE", "0")
    line = _name_warning("QUACKD_TRACE", "QUACKD_LOG")

    _, said = _narration(tmp_path)
    assert said == [line]

    lines = _terminal(tmp_path)
    assert lines[0].startswith("$ quackd run"), lines[:3]
    (at,) = [i for i, text in enumerate(lines) if text == line]
    panel = next(i for i, text in enumerate(lines) if "provider" in text)
    assert at < panel, lines[: panel + 1]
