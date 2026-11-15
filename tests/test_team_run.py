"""Whole team runs: the seed sweep, the one-claimant invariant, and the failure paths."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from roboglot import __version__
from roboglot.adapters.factory import RobotSpec
from roboglot.agent.providers.fake import FakeProvider
from roboglot.cli import app
from roboglot.glotfile.parser import load_glot
from roboglot.log import LogEvent
from roboglot.team.pilots import PilotTeamResult, roster_from_specs, run_pilot_team
from roboglot.team.runner import TEAM_LOG, run_team

runner = CliRunner()
DUCK = load_glot("team-kick")
PILOT_DUCK = load_glot("team-hello")
PILOT_SPECS = {
    "duck": RobotSpec("microduck", "mock", "duck"),
    "arm": RobotSpec("lerobot", "mock", "arm"),
}
"""The other kind of team, on the mock backends `tests/test_pilots.py` runs it on. It is
here for the tests that are about something both runners do."""
# CI sets ROBOGLOT_STRICT_SEEDS=1 (see tests/test_acceptance_sim2d.py)
MIN_SUCCESSES = 10 if os.environ.get("ROBOGLOT_STRICT_SEEDS") == "1" else 8


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


async def run_pilots(runs_dir: Path, **kw: Any) -> PilotTeamResult:
    """A two-body pilot team, so a claim about both runners can be made in one place."""
    roster = roster_from_specs(PILOT_SPECS)
    return await asyncio.wait_for(
        run_pilot_team(
            PILOT_DUCK,
            roster,
            providers={name: FakeProvider.for_glot("team-hello") for name in roster},
            runs_dir=runs_dir,
            **kw,
        ),
        timeout=120,
    )


def assert_one_claimant(events: list[dict[str, Any]]) -> None:
    """Replay team.jsonl: at most one live claim, and only the claimant approaches."""
    claimant: str | None = None
    for ev in events:
        if ev["kind"] == "auction_decision":
            assert claimant is None, "a claim was granted while another was live"
            claimant = ev["kicker"]
        elif ev["kind"] == "miss":
            if ev["duck"] == claimant:
                claimant = None
        elif ev["kind"] == "verb" and ev.get("canonical", ev["name"]) in ("go_to", "kick"):
            assert ev["duck"] == claimant, f"{ev['duck']} moved on the ball without the claim"


async def test_team_acceptance_seeds(tmp_path: Path) -> None:
    successes = 0
    report = []
    for seed in range(10):
        t0 = time.perf_counter()
        result = await asyncio.wait_for(
            run_team(
                DUCK, provider=FakeProvider.for_glot("team-kick"), seed=seed, runs_dir=tmp_path
            ),
            timeout=120,  # a clock wedge must FAIL the test, not hang CI
        )
        wall = time.perf_counter() - t0
        ok = result.outcome == "success" and result.ball_displacement_m >= 0.3
        successes += ok
        report.append(f"seed {seed}: {result.outcome} truth={result.ball_displacement_m:.2f}")
        assert wall < 60, report[-1]
        events = read_jsonl(result.run_dir / "team.jsonl")
        assert_one_claimant(events)
        kinds = {e["kind"] for e in events}
        assert {"plan", "bus", "team_start", "team_end"} <= kinds
        summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["planner"]["llm_calls"] == 0  # fake provider: zero LLM calls
        assert summary["kicker"] in summary["team"]["members"] or summary["kicker"] is None
        for name in summary["team"]["members"]:
            assert (result.run_dir / "robots" / name / "transcript.jsonl").exists()
            assert not (result.run_dir / "robots" / name / "summary.json").exists()
    assert successes >= MIN_SUCCESSES, "\n".join(report)


async def test_team_dry_run_moves_nothing(tmp_path: Path) -> None:
    result = await asyncio.wait_for(
        run_team(
            DUCK,
            provider=FakeProvider.for_glot("team-kick"),
            seed=1,
            runs_dir=tmp_path,
            dry_run=True,
        ),
        timeout=120,
    )
    assert result.outcome != "success"
    assert result.ball_displacement_m == 0.0
    assert all(d["kicks_connected"] == 0 for d in result.per_duck.values())


async def test_a_team_is_logged_per_member_and_every_view_sees_only_its_own_duck(
    tmp_path: Path,
) -> None:
    seen: dict[str, list[LogEvent]] = {}

    result = await asyncio.wait_for(
        run_team(
            DUCK,
            provider=FakeProvider.for_glot("team-kick"),
            seed=3,
            runs_dir=tmp_path,
            view=lambda name: seen.setdefault(name, []).append,
        ),
        timeout=120,
    )
    assert result.log_dropped == 0  # nothing raised, so nothing was lost
    assert set(seen) == set(result.per_duck) | {TEAM_LOG}

    for name in result.per_duck:
        records = read_jsonl(result.run_dir / "robots" / name / "transcript.jsonl")
        kinds = {r["kind"] for r in records}
        assert {"verb_start", "intent", "verb_end", "member_end"} <= kinds, f"{name}: {kinds}"
        # the view saw this duck's verbs, all of them and only them: a factory that handed
        # two members one sink would show each of these twice
        mine = [(r["name"], r.get("params")) for r in records if r["kind"] == "verb_start"]
        theirs = [
            (e.data["name"], e.data.get("params")) for e in seen[name] if e.kind == "verb_start"
        ]
        assert theirs == mine

    story = {e.kind for e in seen[TEAM_LOG]}
    assert "claim" in story and "verb_start" not in story  # decisions here, verbs per duck

    team_kinds = {e["kind"] for e in read_jsonl(result.run_dir / "team.jsonl")}
    assert "member_log" in team_kinds  # the members' log lines still land here
    assert "verb_start" not in team_kinds  # and their narration still does not


async def test_team_kill_switch_stops_every_duck(tmp_path: Path) -> None:
    # fire the kill switch on the FIRST auction event: deterministic in sim time, mid-run
    def on_ready(_transport: Any, coordinator: Any) -> None:
        def on_event(kind: str, _data: dict[str, Any]) -> None:
            if kind == "auction":
                coordinator.abort.set()

        coordinator.on_event = on_event

    result = await asyncio.wait_for(
        run_team(
            DUCK,
            provider=FakeProvider.for_glot("team-kick"),
            seed=4,
            runs_dir=tmp_path,
            on_recorder=on_ready,
        ),
        timeout=120,
    )
    assert result.outcome == "aborted"
    assert "kill switch" in result.reason
    # every duck actually stopped, and nobody reached the ball after the abort
    assert all(d["final_status"] in ("aborted", "stopped") for d in result.per_duck.values())
    assert result.ball_displacement_m < 0.3


async def test_team_survives_a_dead_member(tmp_path: Path) -> None:
    # duck-2 goes SILENT from the start: aborted and muted, so no HB and no RESULT ever
    # reach the bus. Only the sim-time watchdog can notice it. Deterministic, no races.
    def on_ready(_transport: Any, coordinator: Any) -> None:
        victim = coordinator.members["duck-2"]
        victim._publish = lambda _msg: None
        victim.executor.abort.set()

    result = await asyncio.wait_for(
        run_team(
            DUCK,
            provider=FakeProvider.for_glot("team-kick"),
            seed=0,
            runs_dir=tmp_path,
            on_recorder=on_ready,
        ),
        timeout=120,  # the run must END (no clock wedge)
    )
    assert result.outcome in ("success", "failure")
    assert result.per_duck["duck-2"]["final_status"] == "aborted"
    events = read_jsonl(result.run_dir / "team.jsonl")
    assert any(e["kind"] == "member_dead" and e["duck"] == "duck-2" for e in events), (
        "the watchdog never declared the silent duck dead"
    )
    assert any(e["kind"] == "verb" and e["duck"] != "duck-2" for e in events), (
        "the survivors did nothing"
    )


async def test_team_enforces_max_minutes(tmp_path: Path) -> None:
    # regression: budget.start() was never called, so max_minutes was silently dead
    from roboglot.glotfile.parser import parse_glot_text

    duck = parse_glot_text(
        "---\nglot: 0\nname: team-blink\ndescription: d\n"
        "verbs:\n  allow: [search_scan, walk_to, walk, kick, quack, get_frame, stop]\n"
        "budgets:\n  max_steps: 60\n  max_minutes: 0.02\n"
        "success: [x]\nteam:\n  members: 2\n---\n# T\nx\n",
        "team-blink.glot",
    )
    result = await asyncio.wait_for(
        run_team(duck, provider=FakeProvider.for_glot("team-kick"), seed=1, runs_dir=tmp_path),
        timeout=120,
    )
    assert result.outcome == "failure"
    assert all(d["final_status"] == "budget" for d in result.per_duck.values())


async def test_team_honours_max_steps_override(tmp_path: Path) -> None:
    # regression: run --team silently dropped --max-steps
    result = await asyncio.wait_for(
        run_team(
            DUCK,
            provider=FakeProvider.for_glot("team-kick"),
            seed=1,
            runs_dir=tmp_path,
            max_steps=1,
        ),
        timeout=120,
    )
    assert result.outcome == "failure"
    assert all(d["final_status"] == "budget" for d in result.per_duck.values())
    assert all(d["steps"] <= 2 for d in result.per_duck.values())


async def test_the_auction_hands_over_its_run_directory_before_it_writes_anything(
    tmp_path: Path,
) -> None:
    """The CLI has been buffering your terminal since the header, with nowhere to put it, and
    this hook is where it finds one. Called late it would still produce a file, but a file
    that started after the run did, which is the one thing the capture exists to avoid."""
    seen: list[Path] = []

    def arrived(run_dir: Path) -> None:
        # inside the callback is the only place the ordering can be claimed: by the time
        # run_team returns, team.jsonl exists whether the hook was early or late
        assert not (run_dir / "team.jsonl").exists(), "the transcript was open already"
        assert list(run_dir.iterdir()) == [], f"{run_dir} already had something in it"
        seen.append(run_dir)

    result = await asyncio.wait_for(
        run_team(
            DUCK,
            provider=FakeProvider.for_glot("team-kick"),
            seed=1,
            runs_dir=tmp_path,
            max_steps=1,  # the hook fires long before the first step; this keeps the run short
            on_run_dir=arrived,
        ),
        timeout=120,
    )
    assert seen == [result.run_dir], "once per run, with the directory the result names"


async def test_a_pilot_team_hands_over_its_run_directory_before_it_writes_anything(
    tmp_path: Path,
) -> None:
    """The same hook on the other runner, for the same reason. This one has more to be early
    for: a pilot team makes a directory per member before any of them starts."""
    seen: list[Path] = []

    def arrived(run_dir: Path) -> None:
        assert not (run_dir / "team.jsonl").exists(), "the transcript was open already"
        assert not (run_dir / "robots").exists(), "a member had its directory already"
        seen.append(run_dir)

    result = await run_pilots(tmp_path, on_run_dir=arrived)
    assert seen == [result.run_dir], "once per run, with the directory the result names"


async def test_neither_runner_needs_a_hook_at_all(tmp_path: Path) -> None:
    """A keyword with a default, because the capture is the CLI's business and nobody else's:
    every other test in this file, and every one in `tests/test_pilots.py`, passes none."""
    auction = await asyncio.wait_for(
        run_team(
            DUCK,
            provider=FakeProvider.for_glot("team-kick"),
            seed=1,
            runs_dir=tmp_path / "auction",
            max_steps=1,
        ),
        timeout=120,
    )
    pilots = await run_pilots(tmp_path / "pilots")
    for run_dir in (auction.run_dir, pilots.run_dir):
        assert (run_dir / "team.jsonl").exists()
        assert not (run_dir / "terminal.txt").exists(), "nothing asked for one"


async def test_both_runners_write_the_command_and_the_version_into_the_root_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither team root writes a `run_start`, so the root summary is the only record of what
    was asked for at the team's own level. The auction's members write none either, so for
    that runner it is the only record of it anywhere: without it a coordinator team's command
    would be in the terminal file and nowhere a reader can parse. A pilot team's members are
    each an `AgentLoop` and do write one, which is why this is not the only place it is
    checked.

    `sys.argv` is pytest's own in here, because both runners read the arguments the process
    was given rather than anything passed to them. Setting it is the only way to put a typed
    command in front of that.
    """
    typed = ["roboglot", "run", "team-kick", "--team", "2", "--seed", "1"]
    monkeypatch.setattr(sys, "argv", typed)
    auction = await asyncio.wait_for(
        run_team(
            DUCK,
            provider=FakeProvider.for_glot("team-kick"),
            seed=1,
            runs_dir=tmp_path / "auction",
            max_steps=1,  # the summary is written whatever the outcome, so keep the run short
        ),
        timeout=120,
    )
    pilots = await run_pilots(tmp_path / "pilots")
    for run_dir in (auction.run_dir, pilots.run_dir):
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["command"] == typed, run_dir
        assert summary["version"] == __version__, run_dir


def test_cli_team_run_and_guards(tmp_path: Path) -> None:
    ok = runner.invoke(
        app,
        ["run", "team-kick", "--llm", "fake", "--seed", "3", "--runs-dir", str(tmp_path)],
    )
    assert ok.exit_code == 0, ok.output
    assert "team" in ok.output and "kicker " in ok.output
    run_dir = next(tmp_path.iterdir())
    assert (run_dir / "team.jsonl").exists() and (run_dir / "run.gif").exists()

    one = runner.invoke(app, ["run", "hello-world", "--team", "1", "--runs-dir", str(tmp_path)])
    assert one.exit_code == 1 and "2 to 4" in one.output

    wrong_backend = runner.invoke(
        app,
        ["run", "team-kick", "--robot", "microduck:mock", "--runs-dir", str(tmp_path)],
    )
    assert wrong_backend.exit_code == 1 and "simulator only" in wrong_backend.output


def test_cli_team_flag_on_a_solo_duck(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "find-and-kick",
            "--team",
            "2",
            "--llm",
            "fake",
            "--seed",
            "1",
            "--no-gif",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads((next(tmp_path.iterdir()) / "summary.json").read_text(encoding="utf-8"))
    assert summary["team"]["members"] == ["duck-0", "duck-1"]


def test_validate_rejects_team_with_confirm(tmp_path: Path) -> None:
    bad = tmp_path / "bad-team.glot"
    bad.write_text(
        "---\nglot: 0\nname: bad-team\ndescription: d\nverbs:\n  allow: [kick, stop]\n"
        "  confirm: [kick]\nsuccess: [x]\nteam:\n  members: 2\n---\n# T\nx\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 1 and "y/N" in result.output


def test_serve_mcp_refuses_team_ducks() -> None:
    from roboglot.mcp_server import serve

    try:
        serve(glotfile="team-kick")
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert "team" in str(e)


def test_cli_team_log_prefixes_every_line_with_its_duck_and_tells_the_teams_story(
    tmp_path: Path,
) -> None:
    """Three robots moving at once used to print nothing at all. Interleaved without a name
    on each line they would be worse than nothing, so the prefix is the whole feature."""
    args = [
        "run",
        "team-kick",
        "--llm",
        "fake",
        "--seed",
        "3",
        "--no-gif",
        "--runs-dir",
        str(tmp_path),
    ]
    on = runner.invoke(app, [*args, "--log"])
    assert on.exit_code == 0, on.output
    shown = " ".join(on.output.split())
    for needle in (
        # the name in front of the line, not merely somewhere in the output: without the
        # prefix three robots narrating at once are one interleaving nobody can read
        "duck-0 ▶ verb search_scan(",
        "send move x",  # what went to that robot
        "team ◆ auction first bid duck-",  # the coordinator's story
        "team ◆ claim duck-",
        "end stopped after",  # and each member's ending
    ):
        assert needle in shown, (needle, shown[:400])

    off = runner.invoke(app, [*args, "--no-log"])
    assert off.exit_code == 0, off.output
    quiet = " ".join(off.output.split())
    for needle in ("verb search_scan(", "send move x", "auction first bid", "end stopped"):
        assert needle not in quiet, needle
    assert "kicker " in quiet, "the outcome is not part of the log and must survive"


def test_a_team_run_keeps_one_terminal_txt_at_the_team_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two robots narrating at once is the output least likely to survive a scrollback, so it
    is kept where the run is. One file, at the root: a copy per member would split the
    interleaving down the middle, and the interleaving is what the prefix is for.

    `sys.argv` is pytest's own here, because `CliRunner` calls the command in-process while
    the header is built from the arguments the process was given. Setting it is the only way
    to put a typed command in front of that.
    """
    argv = [
        "run",
        "team-kick",
        "--team",
        "2",
        "--llm",
        "fake",
        "--api-key",
        "sk-never-in-a-run-directory",
        "--seed",
        "3",
        "--no-gif",
        "--log",
        "--runs-dir",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "argv", ["roboglot", *argv])
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, result.output

    run_dir = next(tmp_path.iterdir())
    assert list(tmp_path.rglob("terminal.txt")) == [run_dir / "terminal.txt"], (
        "the members have their own directories and neither of them gets a copy"
    )
    saved = (run_dir / "terminal.txt").read_text(encoding="utf-8")
    assert saved.startswith("$ roboglot run team-kick --team 2 ")
    assert "--api-key '***'" in saved
    assert "sk-never-in-a-run-directory" not in saved, "a run directory gets pasted into issues"
    assert "\x1b" not in saved, "colour belongs to the terminal, not to the file"

    shown = " ".join(result.output.split())
    kept = " ".join(saved.split())
    for needle in ("duck-0 ▶ verb", "duck-1 ▶ verb", "team ◆ claim duck-"):
        assert needle in shown, needle
        assert needle in kept, f"{needle} was on screen and not in the file"
