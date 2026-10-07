"""The CLI wires things together; these tests prove the wiring, not the parts."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd import __version__
from quackd import cli as cli_mod
from quackd.agent.providers.factory import CLOUD_NAMES, default_model_for, model_ids
from quackd.cli import EXIT_INFEASIBLE, app
from quackd_lerobot.verbs import JOINTS as JOINT_NAMES

from .conftest import DUCKS
from .conftest import help_text as _help

runner = CliRunner()


# ── the log ───────────────────────────────────────────────────────────────────────────


def _one_command(tmp_path: Path, monkeypatch, *args: str, env: str | None = "") -> str:
    """Any command whose stderr the CliRunner folds into `output`, with the label column's
    padding squeezed out so an assertion can name a line as a reader would say it. `env` is
    what QUACKD_LOG says: "" is on (an empty value must never read as off), None removes
    it. The suite turns the log off for everyone else (conftest)."""
    if env is None:
        monkeypatch.delenv("QUACKD_LOG", raising=False)
    else:
        monkeypatch.setenv("QUACKD_LOG", env)
    result = runner.invoke(app, [*args, "--runs-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return " ".join(result.output.split())


def _log_run(tmp_path: Path, monkeypatch, *flags: str, env: str | None = "") -> str:
    """One `run` on the mock, which sends no frames and writes no GIF: the log itself is
    what these tests read."""
    return _one_command(
        tmp_path,
        monkeypatch,
        "run",
        "hello-world",
        "--llm",
        "fake",
        "--robot",
        "microduck:mock",
        "--no-gif",
        *flags,
        env=env,
    )


def _log_record(tmp_path: Path, monkeypatch, *flags: str, env: str | None = "") -> str:
    """The same run through `record`, which pins the simulator and always writes a GIF."""
    return _one_command(
        tmp_path,
        monkeypatch,
        "record",
        "hello-world",
        "--llm",
        "fake",
        *flags,
        env=env,
    )


def _kinds(tmp_path: Path) -> list[str]:
    """Every `kind` the run's transcript recorded, in order."""
    from quackd.agent.transcript import Transcript

    return [e["kind"] for e in Transcript.read(next(tmp_path.rglob("transcript.jsonl")))]


def test_the_log_is_on_by_default(tmp_path: Path, monkeypatch) -> None:
    """Someone who types `quackd run` and watches a robot move should see why it moved."""
    out = _log_run(tmp_path, monkeypatch)
    assert "system prompt" in out  # what the model was told
    assert "quack(text='hello!')" in out  # what it chose
    assert "send sound" in out  # what went to the robot
    assert "result quack ok" in out  # what came back
    assert "SUCCESS" in out  # and the outcome still reaches stdout


def test_no_log_prompt_hides_only_the_prompt(tmp_path: Path, monkeypatch) -> None:
    """The prompt is forty to seventy lines, worth reading once and tiresome on the fiftieth
    run of an afternoon. Hiding it must not cost the verbs and the intents."""
    out = _log_run(tmp_path, monkeypatch, "--no-log-prompt")
    assert "system prompt" not in out and "You are the brain" not in out
    assert "send sound" in out and "result quack ok" in out


def test_the_env_hides_the_prompt_and_the_flag_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QUACKD_LOG_PROMPT", "0")
    assert "system prompt" not in _log_run(tmp_path, monkeypatch)
    assert "system prompt" in _log_run(tmp_path, monkeypatch, "--log-prompt")


def test_no_log_leaves_the_header_and_the_outcome(tmp_path: Path, monkeypatch) -> None:
    out = _log_run(tmp_path, monkeypatch, "--no-log")
    assert "send sound" not in out and "system prompt" not in out
    assert "SUCCESS" in out and "hello-world" in out


def test_the_env_can_turn_the_log_off_too(tmp_path: Path, monkeypatch) -> None:
    """A `.env` line has to work, so the default is read when the command runs, not when the
    module is imported."""
    assert "send sound" not in _log_run(tmp_path, monkeypatch, env="0")
    assert "send sound" in _log_run(tmp_path, monkeypatch, env=None)


def test_the_flag_beats_the_env(tmp_path: Path, monkeypatch) -> None:
    assert "send sound" in _log_run(tmp_path, monkeypatch, "--log", env="0")


def test_verbose_is_the_compact_view_and_does_not_double_the_log(
    tmp_path: Path, monkeypatch
) -> None:
    """With the log on, the executor's own log lines would say every verb a second time."""
    logged = _log_run(tmp_path, monkeypatch, "--verbose")
    assert "send sound" in logged
    assert "→ quack" not in logged, "the old compact line must not double the log"
    compact = _log_run(tmp_path, monkeypatch, "--verbose", "--no-log")
    assert "→ quack" in compact and "send sound" not in compact


def test_record_writes_a_gif_and_a_transcript(tmp_path: Path, monkeypatch) -> None:
    """Every GIF in the README and every launch post comes out of `record`, and no test
    ever ran the command: it pins its own robot, always renders, and could have been broken
    for a whole release without a single failure to say so."""
    out = _log_record(tmp_path, monkeypatch)
    assert "SUCCESS" in out
    gif = next(tmp_path.rglob("run.gif"))
    assert gif.stat().st_size > 0, "an empty GIF is a README with a broken image in it"
    assert {"run_start", "llm", "verb_end", "run_end"} <= set(_kinds(tmp_path))


def test_record_no_log_still_writes_every_event(tmp_path: Path, monkeypatch) -> None:
    """The switch is about the console and nothing else (ADR-0029). The transcript is the
    record, and a run recorded quietly must be as complete as a noisy one."""
    out = _log_record(tmp_path, monkeypatch, "--no-log")
    assert "send sound" not in out and "SUCCESS" in out
    kinds = _kinds(tmp_path)
    assert "intent" in kinds and "verb_end" in kinds


def test_no_log_verbose_prints_the_dry_run_line_intact(tmp_path: Path, monkeypatch) -> None:
    """`--verbose` predates the log and people's scripts still pass it, so with the log
    off it is still the only thing that says what a dry run would have done — and the line
    opens with `[dry-run]`, which Rich would eat as a style tag if it were printed as
    markup."""
    out = _log_run(tmp_path, monkeypatch, "--no-log", "--dry-run", "--verbose")
    assert "[dry-run] would run quack(" in out
    assert "Traceback" not in out


def test_serve_mcp_forwards_no_log(monkeypatch) -> None:
    """`--no-log` is what silences an MCP server that logs to the same stderr its client
    reads. The flag is parsed by one module and honoured by another, so nothing but a call
    recorder proves it survives the hand-off."""
    from quackd import mcp_server
    from quackd.log import log_enabled_default

    captured: dict[str, object] = {}

    def recorder(**kwargs: object) -> None:  # `serve` blocks on mcp.run(); this returns
        captured.update(kwargs)

    monkeypatch.setattr(mcp_server, "serve", recorder)
    monkeypatch.setenv("QUACKD_LOG", "")  # an empty value is on

    off = runner.invoke(app, ["serve-mcp", "--no-log", "--robot", "microduck:mock"])
    assert off.exit_code == 0, off.output
    assert captured["log"] is False

    captured.clear()
    on = runner.invoke(app, ["serve-mcp", "--robot", "microduck:mock"])
    assert on.exit_code == 0, on.output
    # without the flag the CLI forwards None — "ask the environment" — because a server a
    # desktop spawned has no shell to read QUACKD_LOG in. `serve` resolves it, and on.
    assert captured["log"] is None and log_enabled_default() is True


def test_the_outcome_line_prints_the_models_reason_verbatim(tmp_path: Path, monkeypatch) -> None:
    """The reason is the model's own `declare_failure` text. A local model that leaks
    `[/think]` into it used to crash the CLI with a Rich MarkupError after a completed run,
    replacing the verdict with a traceback."""
    from quackd.agent.providers import factory
    from quackd.agent.providers.base import ToolCall
    from quackd.agent.providers.fake import FakeProvider

    reason = "the ball is [behind] the sofa [/think]"
    monkeypatch.setattr(
        factory,
        "make_provider",
        lambda *a, **k: FakeProvider(
            script=[ToolCall(name="declare_failure", arguments={"reason": reason})]
        ),
    )
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
        ],
    )
    # not the exit code: it is 1 both for the failure outcome and for the old traceback
    assert "[behind]" in result.output and "[/think]" in result.output
    assert "Traceback" not in result.output


def test_list_verbs_prints_a_description_with_brackets_verbatim(monkeypatch) -> None:
    """A verb description is manifest text, which Rich would silently eat as a style tag."""
    from quackd.verbs import registry as registry_mod
    from quackd.verbs.registry import NoParams, Verb, VerbRegistry

    reg = VerbRegistry()
    reg.register(Verb("kick", "kick [left] or [right]", lambda c, p: None, params=NoParams))  # type: ignore[arg-type]
    monkeypatch.setattr(registry_mod, "default_registry", lambda: reg)
    result = runner.invoke(app, ["list-verbs"])
    assert result.exit_code == 0, result.output
    assert "[left]" in result.output


def test_a_verbose_line_survives_a_bracket_a_planner_logged(monkeypatch) -> None:
    """The flock's planner logs a model's raw tool arguments through this line."""
    import io

    from rich.console import Console

    from quackd import cli as cli_mod
    from quackd import ui

    buf = io.StringIO()
    # the consoles live on `ui` so `--no-color` can replace them; `cli` reads them from there
    monkeypatch.setattr(ui, "err_console", Console(file=buf, force_terminal=False, width=200))
    cli_mod._verbose_line("planner: [/think] chose [bold]walk")
    out = buf.getvalue()
    assert "[/think]" in out and "[bold]walk" in out


def test_validate_starter_ducks() -> None:
    result = runner.invoke(app, ["validate", *[str(p) for p in sorted(DUCKS.glob("*.duck"))]])
    assert result.exit_code == 0, result.output
    assert "15 files valid" in result.output


def test_validate_expands_globs_itself() -> None:
    result = runner.invoke(app, ["validate", str(DUCKS / "*.duck")])
    assert result.exit_code == 0, result.output


def test_a_pattern_written_with_forward_slashes_expands_to_paths_written_that_way(
    tmp_path: Path,
) -> None:
    """On Windows `glob` joined what a wildcard matched with backslashes and kept the part
    typed before it as typed, so `quackd preflight "docs/examples/e00[1-5]/*.duck"` labelled
    its files in two slash styles at once. Anywhere else this holds as it always did."""
    from quackd.cli import _expand

    for folder, name in (("e001", "a.duck"), ("e002", "b.duck"), ("f003", "c.duck")):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / name).write_text("", encoding="utf-8")
    base = tmp_path.as_posix()
    assert _expand([f"{base}/e00[1-5]/*.duck"]) == [f"{base}/e001/a.duck", f"{base}/e002/b.duck"]
    assert _expand([f"{base}/none/*.duck"]) == [f"{base}/none/*.duck"], "no match is kept whole"


def test_validate_fails_fast(tmp_path: Path) -> None:
    bad = tmp_path / "bad.duck"
    bad.write_text("---\nduck: 0\nname: bad\n---\nbody\n", encoding="utf-8")
    unknown = tmp_path / "unknown.duck"
    unknown.write_text(
        "---\nduck: 0\nname: unknown\ndescription: d\nverbs:\n  allow: [fly]\n"
        "success: [x]\n---\n# Task\nx\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["validate", str(bad), str(unknown), str(DUCKS / "hello-world.duck")]
    )
    assert result.exit_code == 1
    assert "unknown verbs: fly" in result.output
    assert "✗" in result.output and "✓" in result.output


def test_list_verbs() -> None:
    result = runner.invoke(app, ["list-verbs"])
    assert result.exit_code == 0
    for name in ("walk", "kick", "walk_to", "quack"):
        assert name in result.output


def test_run_hello_world_on_mock(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "SUCCESS" in result.output
    run_dirs = list(tmp_path.iterdir())
    assert len(run_dirs) == 1 and (run_dirs[0] / "transcript.jsonl").exists()


def test_missing_extra_hint_survives_rich_markup(tmp_path: Path, monkeypatch) -> None:
    from quackd.agent.providers import factory
    from quackd.agent.providers.base import ProviderNotInstalled

    def missing(name: str, **_: object) -> None:
        raise ProviderNotInstalled(name, "anthropic")

    monkeypatch.setattr(factory, "make_provider", missing)
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "anthropic",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "quackd[anthropic]" in result.output  # Rich must not eat the [anthropic] "tag"


def test_run_goal_builds_an_ad_hoc_duck(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--goal",
            "say hello and stop",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "goal" in result.output
    transcript = next(tmp_path.rglob("transcript.jsonl")).read_text(encoding="utf-8")
    assert "say hello and stop" in transcript  # the goal is the task body
    assert '"kick"' in transcript  # safe verbs are allowed
    assert "SUCCESS" in result.output


def test_goal_picks_a_matching_scripted_strategy(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--goal",
            "find the ball and kick it",
            "--llm",
            "fake",
            "--seed",
            "4",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "scripted:goal:find-and-kick" in result.output
    transcript = next(tmp_path.rglob("transcript.jsonl")).read_text(encoding="utf-8")
    assert '"name": "kick"' in transcript  # it really kicked, not just "nothing more to do"


def test_run_needs_exactly_one_of_duck_or_goal(tmp_path: Path) -> None:
    both = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--goal",
            "x",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    neither = runner.invoke(app, ["run", "--robot", "microduck:mock", "--runs-dir", str(tmp_path)])
    assert both.exit_code == 1 and neither.exit_code == 1
    assert "either" in both.output and "either" in neither.output


def _run_hello(tmp_path: Path, *flags: str) -> object:
    return runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
            *flags,
        ],
    )


def test_transport_flag_is_gone(tmp_path: Path) -> None:
    """0.4 deprecated `--transport X` in favour of `--robot microduck:X` and said it would
    be removed in 0.5. It is."""
    old = _run_hello(tmp_path, "--transport", "mock")
    assert old.exit_code != 0  # type: ignore[attr-defined]
    assert "No such option" in old.output  # type: ignore[attr-defined]
    new = _run_hello(tmp_path, "--robot", "microduck:mock")
    assert new.exit_code == 0, new.output  # type: ignore[attr-defined]
    assert "microduck:mock" in new.output  # type: ignore[attr-defined]


def test_robot_flag_errors_are_clean(tmp_path: Path) -> None:
    unknown = _run_hello(tmp_path, "--robot", "hal9000:mock")
    assert unknown.exit_code == 1 and "unknown adapter" in unknown.output  # type: ignore[attr-defined]
    bad_backend = _run_hello(tmp_path, "--robot", "microduck:hovercraft")
    assert bad_backend.exit_code == 1 and "unknown backend" in bad_backend.output  # type: ignore[attr-defined]


def test_a_second_camera_url_is_refused_by_a_one_camera_body_before_anything_runs(
    tmp_path: Path,
) -> None:
    """`--camera-url` became repeatable for the LeRobot arm's several cameras, and a
    repeatable flag is repeatable on every command line. A body that reads one has to say so
    before the run starts: opening the first url and dropping the second would leave the
    reason a camera is missing nowhere but in a transcript nobody reads twice."""
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
            "--camera-url",
            "http://10.0.0.5:9872/snapshot.jpg",
            "--camera-url",
            "http://10.0.0.6:9872/snapshot.jpg",
        ],
    )
    assert result.exit_code == 1, result.output
    out = " ".join(result.output.split())  # the console wraps the line at the terminal width
    assert "microduck:mock takes one --camera-url and 2 were given" in out
    assert "only lerobot:real and lerobot:mujoco take several" in out
    assert list(tmp_path.iterdir()) == [], "the refusal came before the run directory"


def test_doctor_json_reports_a_second_camera_url_inside_its_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--json` promises one JSON document and nothing else, whatever went wrong, so a
    refusal raised while the robot is being built belongs in the probe rather than in a line
    of prose printed above the document a script is trying to parse."""
    from quackd import doctor

    # doctor probes four local model servers at 1.5 s each and nothing here is about them
    monkeypatch.setattr(doctor, "_probe_models", lambda url, timeout_s=1.5: ("down", "not running"))
    result = runner.invoke(
        app,
        [
            "doctor",
            "--json",
            "--robot",
            "microduck:mock",
            "--address",
            "tcp://127.0.0.1:9",
            "--camera-url",
            "http://10.0.0.5:9872/snapshot.jpg",
            "--camera-url",
            "http://10.0.0.6:9872/snapshot.jpg",
        ],
    )
    assert result.exit_code == 1, result.output
    report = json.loads(result.output)  # one document, not a refusal and then a document
    probe = report["robot"]["probe"]
    assert probe["ok"] is False and probe["rows"] == []
    assert "takes one --camera-url and 2 were given" in probe["error"]
    assert "only lerobot:real and lerobot:mujoco take several" in probe["error"]
    assert report["ok"] is False  # and the exit code follows the report, as it always did


def test_list_adapters() -> None:
    result = runner.invoke(app, ["list-adapters"])
    assert result.exit_code == 0, result.output
    for needle in ("microduck", "sim2d", "mock", "jsonrpc", "open_duck", "bridge"):
        assert needle in result.output


def test_list_verbs_for_a_robot() -> None:
    result = runner.invoke(app, ["list-verbs", "--robot", "microduck:mock"])
    assert result.exit_code == 0 and "move" in result.output and "walk" in result.output
    bad = runner.invoke(app, ["list-verbs", "--robot", "nope"])
    assert bad.exit_code == 1 and "unknown adapter" in bad.output


def test_validate_against_a_robot(tmp_path: Path) -> None:
    ok = runner.invoke(app, ["validate", "hello-world", "--robot", "microduck:mock"])
    assert ok.exit_code == 0 and "for microduck" in ok.output
    duck = tmp_path / "needs-express.duck"
    duck.write_text(
        "---\nduck: 1\nname: needs-express\ndescription: d\nrequires: [express]\n"
        "robots: microduck:mock\nverbs:\n  allow: [quack, express, stop]\nsuccess: [x]\n"
        "---\n# Task\nx\n",
        encoding="utf-8",
    )
    bad = runner.invoke(app, ["validate", str(duck)])  # the duck's own robots: default applies
    assert bad.exit_code == 1
    # printed as a plain line under the table, so it survives any terminal width
    assert "requires express, but microduck (microduck) does not provide it" in bad.output
    with_robot = runner.invoke(app, ["validate", str(duck), "--robot", "bogus:x"])
    assert with_robot.exit_code == 1 and "unknown adapter" in with_robot.output


def test_run_unknown_provider_is_a_clean_error(tmp_path: Path) -> None:
    """One flag now carries the vendor and the model, so the refusal has to teach both shapes.

    A reader who typed `--llm hal9000` cannot tell from the flag alone whether the half they
    got wrong was a vendor name or a model id, because `--llm` takes either. Printing both
    forms back -- `--llm anthropic` and `--llm anthropic:<id>` -- is what turns "that was
    refused" into "type this instead", and it costs one line of a message already on screen.
    """
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "hal9000",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    flat = " ".join(result.output.split())
    assert "unknown provider" in flat
    assert "--llm anthropic," in flat, "the bare-vendor form"
    assert f"--llm anthropic:{default_model_for('anthropic')}" in flat, "the vendor:model form"


def test_run_refuses_a_model_outside_the_catalogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the catalogue: the refusal arrives before anything is spent.

    The key is deliberately removed, so if the model were checked after the provider was built
    this would fail about `OPENAI_API_KEY` instead. Nothing may be written either: a run
    directory for a run that never started is a run that has to be explained later.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "openai:gpt-nope",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    flat = " ".join(result.output.split())
    assert "gpt-nope" in flat and "unknown model" in flat
    assert default_model_for("openai") in flat, "the refusal must offer what to pass instead"
    assert "list-models" in flat
    assert "Traceback" not in result.output
    assert list(tmp_path.iterdir()) == [], "a refused run left a directory behind"


def test_run_names_the_vendor_when_the_model_belongs_to_another_one(tmp_path: Path) -> None:
    """The commonest mistake, and the one that looks least like a mistake: a real model id
    under the wrong vendor. The answer has to be the whole spec the reader should retype, not
    just the vendor's name, because the spec is now one word on the line."""
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "openai:grok-4.6",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "--llm grok:grok-4.6" in " ".join(result.output.split())


@pytest.fixture
def _wide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich sizes a table to the terminal and folds a long id across rows to fit. That is right
    for a reader and useless for a substring assertion, so these tests ask for a wide one."""
    monkeypatch.setenv("COLUMNS", "220")


def test_list_models_prints_every_vendor_and_marks_the_defaults(_wide: None) -> None:
    result = runner.invoke(app, ["list-models"])
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    for name in CLOUD_NAMES:
        assert name in flat
        assert default_model_for(name) in flat, name
    assert "default" in flat
    assert "no catalogue" in flat, "the local presets must say why they are not in the table"
    assert "ignored" in flat, "fake must say that a model after the colon does nothing"


def test_list_models_can_be_asked_about_one_vendor(_wide: None) -> None:
    """`-l` is the short flag now: `-p` went with `--provider`, and the pair of them is one
    flag on every other command, so this command spells it the same way."""
    result = runner.invoke(app, ["list-models", "--llm", "openai"])
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "claude-opus-5" not in flat, "asking for one vendor printed another"
    assert "gpt-5.6-sol" in flat
    assert " ".join(runner.invoke(app, ["list-models", "-l", "openai"]).output.split()) == flat
    local = runner.invoke(app, ["list-models", "--llm", "ollama"])
    assert local.exit_code == 0 and "first entry" in " ".join(local.output.split())
    bad = runner.invoke(app, ["list-models", "--llm", "nope"])
    assert bad.exit_code == 1 and "unknown provider" in bad.output


def test_list_models_says_what_the_environment_pins(
    _wide: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QUACKD_LLM holds a whole spec now, so the note under the table has to read it the way
    `--llm` does: a bare id infers its vendor and the note names the pair it resolved to, and
    a spec that cannot be read at all is reported as refused rather than quietly ignored.
    A variable in a `.env` nobody has opened in months is exactly the kind of setting whose
    failure has to be printed where the reader is already looking."""
    monkeypatch.setenv("QUACKD_LLM", "grok-4.6")
    flat = " ".join(runner.invoke(app, ["list-models"]).output.split())
    assert "QUACKD_LLM=grok-4.6 pins grok:grok-4.6" in flat
    monkeypatch.setenv("QUACKD_LLM", "not-a-model")
    flat = " ".join(runner.invoke(app, ["list-models"]).output.split())
    assert "QUACKD_LLM=not-a-model is refused" in flat
    assert "unknown provider" in flat


def test_llm_completion_offers_vendors_first_and_then_that_vendors_ids() -> None:
    """One flag means completion has to answer two questions with the same keystroke.

    Before the colon the answer is the seventeen vendor names, twice over: bare, so one TAB
    takes the default model, and with a trailing colon so a second TAB carries on into the
    list. The hundred-odd model ids must stay out of that first offer, because a bare TAB
    that prints a hundred lines is a TAB nobody presses twice.

    After the colon the offer narrows to that vendor's own ids, which is why `grok:gpt`
    offers nothing at all: `--llm grok:gpt-5.6-sol` would be refused by the parser, and
    completion that offered it would be arguing with the command it is meant to help type.
    """
    from quackd.cli import _complete_llm

    ctx = SimpleNamespace(params={})
    empty = _complete_llm(ctx, "")
    names = [i for i, _ in empty]
    assert set(CLOUD_NAMES) <= set(names) and "fake" in names
    assert "anthropic:" in names, "a second TAB has to carry on into the model list"
    assert "fake:" not in names, "the scripted pilot has no model to pick"
    assert not set(model_ids("openai")) & set(names), "a bare TAB must not print every id"
    assert all(label for _, label in empty), "completion offers a half-line beside each entry"

    openai = _complete_llm(ctx, "openai:gpt")
    assert [i for i, _ in openai] == [
        f"openai:{m}" for m in model_ids("openai") if m.startswith("gpt")
    ]
    assert _complete_llm(ctx, "grok:gpt") == [], "another vendor's prefix offers nothing"

    bare = [i for i, _ in _complete_llm(ctx, "claude-op")]
    assert bare == [m for m in model_ids("anthropic") if m.startswith("claude-op")]
    assert bare, "a bare id is a legal spec, so it has to complete on its own"


def test_llm_completion_offers_openrouters_own_rows_and_nothing_fetched() -> None:
    """After the colon, the six rows the catalogue carries and no more: completion runs on
    every TAB and reads nothing but the catalogue, so OpenRouter's list stays out of it."""
    from quackd.cli import _complete_llm

    ctx = SimpleNamespace(params={})
    offered = [i for i, _ in _complete_llm(ctx, "openrouter:")]
    assert offered == [f"openrouter:{m}" for m in model_ids("openrouter")]
    claude = [i for i, _ in _complete_llm(ctx, "openrouter:anthropic/")]
    assert claude == [f"openrouter:{m}" for m in model_ids("openrouter") if "anthropic/" in m]
    # A bare id completes too, and its label names the vendor that will be called: typing
    # `openai` offers OpenRouter's `openai/...` rows beside OpenAI's own, and says whose they are.
    labels = dict(_complete_llm(ctx, "openai"))
    assert labels["openai/gpt-6-sol"].startswith("openrouter: ")


def test_list_models_says_openrouter_takes_more_than_it_lists(_wide: None) -> None:
    """Its rows are a selection, and the table alone would read as all `--llm openrouter:`
    takes. Nothing is fetched to say so: the conftest guard would fail this test if it were.
    The note says how an id quackd does not carry is handled and promises none: 0.17.0's said any
    other id with tool calling on the vendor's own model list works too, and OpenRouter's list
    carries `~` aliases, routers and `:batch` ids that quackd refuses on their spelling."""
    result = runner.invoke(app, ["list-models", "--llm", "openrouter"])
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    for model_id in model_ids("openrouter"):
        assert model_id in flat
    assert "openrouter: the rows above are a selection" in flat
    assert (
        "openrouter: the rows above are a selection. An id quackd does not carry can be named as "
        "--llm openrouter:AUTHOR/MODEL (or AUTHOR/MODEL:free). A few shapes of id are refused on "
        "their spelling alone. Any other is checked against OpenRouter's public model list when a "
        "run starts. That list has to carry the id with tool calling, and one quackd takes is "
        "priced from it."
    ) in flat
    assert "works too" not in flat and "any other id" not in flat.lower()
    assert "a selection" not in " ".join(
        runner.invoke(app, ["list-models", "--llm", "openai"]).output.split()
    )
    # one of OpenRouter's own ids names its vendor here, as it does on `--llm`
    named = runner.invoke(app, ["list-models", "--llm", "anthropic/claude-opus-5.5"])
    assert "openrouter: the rows above are a selection" in " ".join(named.output.split())


def test_list_models_points_a_slashed_id_with_no_vendor_at_openrouter(_wide: None) -> None:
    bad = runner.invoke(app, ["list-models", "--llm", "google/gemma-4-31b-it:free"])
    assert bad.exit_code == 1
    assert "reads as OpenRouter's: --llm openrouter" in " ".join(bad.output.split())
    nope = runner.invoke(app, ["list-models", "--llm", "nope"])
    assert "OpenRouter" not in nope.output, "a typo with no slash says nothing of it"


def test_run_refuses_an_openrouter_alias_before_any_key_or_robot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `~` alias is refused on its spelling: offline, with no key, nothing connected and no
    run directory made. Empty rather than deleted, so a developer's `.env` cannot refill it."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "openrouter:~anthropic/claude-opus-latest",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    flat = " ".join(result.output.split())
    assert "newest model of its family" in flat and "OPENROUTER_API_KEY" not in flat
    assert "Traceback" not in result.output
    assert list(tmp_path.iterdir()) == [], "a refused run left a directory behind"


def test_run_refuses_a_duck_the_robot_cannot_do(tmp_path: Path) -> None:
    """`serve-mcp` always validated the contract against the robot; `run` never did, and
    died halfway in with a raw VerbNotFound once the robot was already connected."""
    result = runner.invoke(
        app,
        [
            "run",
            "find-and-kick",
            "--llm",
            "fake",
            "--robot",
            "open_duck:mock",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
        ],
    )
    assert result.exit_code == 1
    flat = " ".join(result.output.split())  # rich wraps the line
    assert "find-and-kick cannot run on open_duck:mock" in flat
    assert "requires kick, but open-duck-01 (open-duck-mini-v2) does not provide it" in flat
    assert "Traceback" not in result.output
    assert list(tmp_path.iterdir()) == []  # refused before a run directory was made


def test_run_still_starts_when_the_duck_fits(tmp_path: Path) -> None:
    """The guard must refuse the impossible without over-refusing the possible."""
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
        ],
    )
    assert result.exit_code == 0, result.output


def test_a_camera_robot_that_is_not_the_simulator_still_gets_a_detector(tmp_path: Path) -> None:
    """The detector used to be attached only for sim2d, so every hardware backend with a
    camera ran blind: it fetched frames, detected nothing because nothing was detecting,
    and reported that it could not see the ball."""
    result = runner.invoke(
        app,
        [
            "run",
            "open-duck-scout",
            "--llm",
            "fake",
            "--robot",
            "open_duck:mock",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "SUCCESS" in result.output


def test_a_console_that_raises_is_reported_once_at_the_end(tmp_path: Path, monkeypatch) -> None:
    """An observer that raises never ends a run, which is right. It also meant a console that
    raised on every event produced a silent log and no sign at all that it had."""
    import quackd.log as log_module

    class Broken(log_module.ConsoleLog):  # type: ignore[misc]
        def __call__(self, event: object) -> None:
            raise RuntimeError("the terminal went away")

    monkeypatch.setattr(log_module, "ConsoleLog", Broken)
    out = _log_run(tmp_path, monkeypatch)
    assert "could not be shown" in out
    assert "transcript.jsonl has them" in out
    assert "SUCCESS" in out, "a broken console must not change the outcome"


def test_a_gif_pane_larger_than_the_offscreen_buffer_is_refused_before_anything_runs() -> None:
    """The physics model compiles a 1024 px offscreen buffer, so a larger pane fails inside
    MuJoCo halfway through a run. Typer refuses it at the boundary instead, and the number is
    spelled in `cli.py` because `cli.py` must not import `sim3d`. `tests/test_sim3d.py` pins
    the two to each other."""
    result = runner.invoke(app, ["run", "hello-world", "--gif-size", "4096"])
    assert result.exit_code == 2
    assert "1024" in result.output


# ── --json and --no-color: the output a script reads ────────────────────────────────────


def _objects(output: str) -> list[dict]:
    """The JSON lines, and nothing else may be on stdout with them."""
    lines = [line for line in output.splitlines() if line.strip()]
    assert all(line.startswith("{") for line in lines), output
    return [json.loads(line) for line in lines]


def test_validate_json_is_one_object_per_file_and_still_exits_one() -> None:
    """A script wants the rows and the exit code, not a table it has to unpick."""
    runner = CliRunner()
    ok = _objects(runner.invoke(app, ["validate", "hello-world", "--json"]).output)
    assert ok == [
        {
            "file": "hello-world",
            "name": "hello-world",
            "verbs": 3,
            "robots": [],
            "ok": True,
            "problems": [],
        }
    ]
    result = runner.invoke(
        app, ["validate", "find-and-kick", "--robot", "open_duck:mock", "--json"]
    )
    assert result.exit_code == 1, "the exit code is the same with or without --json"
    (row,) = _objects(result.output)
    assert row["ok"] is False and row["robots"] == ["open-duck-01"]
    assert "does not provide it" in row["problems"][0]


def test_validate_json_reports_a_flock_as_a_count() -> None:
    (row,) = _objects(CliRunner().invoke(app, ["validate", "flock-kick", "--json"]).output)
    assert row["flock"] == 3


def test_list_verbs_json_carries_what_the_table_shows() -> None:
    rows = _objects(CliRunner().invoke(app, ["list-verbs", "--json"]).output)
    by_name = {row["name"]: row for row in rows}
    assert {"move", "kick", "quack", "observe"} <= set(by_name)
    assert by_name["move"]["aliases"] == ["walk"]
    assert by_name["move"]["core"] is True
    assert by_name["observe"]["safety"] == "safe"
    assert "vx: float" in by_name["move"]["params"]


def test_list_adapters_json_is_the_registry_rows() -> None:
    rows = _objects(CliRunner().invoke(app, ["list-adapters", "--json"]).output)
    assert [row["name"] for row in rows][:2] == ["microduck", "lerobot"]
    assert rows[0]["installed"] is True
    assert "sim2d" in rows[0]["backends"]


def test_json_never_carries_a_rich_tag() -> None:
    """These strings are pasted into a shell prompt or a dashboard. `[green]` in one of them
    would be the table's styling leaking into the answer."""
    for argv in (["list-adapters", "--json"], ["list-verbs", "--json"]):
        for row in _objects(CliRunner().invoke(app, argv).output):
            blob = json.dumps(row)
            for tag in ("[green]", "[dim]", "[red]", "[/"):
                assert tag not in blob, (argv, tag)


def test_no_color_strips_the_colour_and_keeps_the_words() -> None:
    """Rich renders colour through the Win32 console on a legacy terminal rather than as
    escape codes, so this asks the consoles what they were told rather than grepping bytes."""
    from quackd import ui

    runner = CliRunner()
    assert runner.invoke(app, ["list-adapters"]).exit_code == 0
    assert ui.console.no_color is False
    plain = runner.invoke(app, ["--no-color", "list-adapters"])
    assert plain.exit_code == 0
    assert ui.console.no_color is True and ui.err_console.no_color is True
    assert "microduck" in plain.output and "sim2d" in plain.output


def test_no_color_reaches_the_help_typer_renders_for_itself() -> None:
    """Typer builds a console of its own for every --help, which is why the flag sets the
    variable as well as the consoles."""
    import os

    CliRunner().invoke(app, ["--no-color", "run", "--help"])
    assert os.environ.get("NO_COLOR") == "1"


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "hello-world", "--robot", "microduck:mock", "--no-gif"],
        ["record", "hello-world", "--gif-size", "64"],
        ["run", "flock-hello", "--no-gif"],
        ["run", "flock-kick", "--no-gif"],
    ],
    ids=["run", "record", "pilot-flock", "coordinator-flock"],
)
def test_extra_body_reaches_every_provider_a_run_builds(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flock builds one provider per member, so a flag that only reached the single-robot
    path would be a silent no-op on the very runs that cost the most. The two kinds of flock
    are two call sites: `flock-hello` is `method: pilots` and goes through `_run_pilots_impl`,
    `flock-kick` is `method: auction` and goes through `_run_flock_impl`."""
    body = '{"chat_template_kwargs": {"enable_thinking": false}}'
    seen: list[object] = []
    real = __import__("quackd.agent.providers.factory", fromlist=["make_provider"]).make_provider

    def recorder(name: str, **kw: object) -> object:
        seen.append(kw.get("extra_body"))
        return real("fake", duck_name=kw.get("duck_name"))  # type: ignore[arg-type]

    monkeypatch.setattr("quackd.agent.providers.factory.make_provider", recorder)
    result = runner.invoke(
        app, [*argv, "--llm", "fake", "--runs-dir", str(tmp_path / "r"), "--extra-body", body]
    )
    assert result.exit_code == 0, result.output
    assert seen, "no provider was built"
    assert all(s == body for s in seen), "the factory is handed the text as it was typed"
    # a pilot flock is one whole pilot per body; a coordinator flock is one referee for all
    # of them, so only the first should build more than one provider
    if "flock-hello" in argv:
        assert len(seen) > 1, "a pilot flock is one provider per member"


def test_a_bad_extra_body_stops_before_anything_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parse is in the factory, and the factory runs before the adapter, so the run
    directory is never made. Each door names itself: a flag somebody has just typed and a
    line in a `.env` they have forgotten want different answers."""
    runs = tmp_path / "r"
    common = [
        "run",
        "hello-world",
        "--llm",
        "vllm",
        "--robot",
        "microduck:mock",
        "--no-gif",
        "--runs-dir",
        str(runs),
        "--memory-dir",
        str(tmp_path / "m"),
    ]
    result = runner.invoke(app, [*common, "--extra-body", "[1]"])
    assert result.exit_code == 1
    assert "--extra-body" in result.output and "Traceback" not in result.output
    assert not runs.exists() or not list(runs.iterdir())

    monkeypatch.setenv("QUACKD_EXTRA_BODY", "[1]")
    result = runner.invoke(app, common)
    assert result.exit_code == 1 and "QUACKD_EXTRA_BODY" in result.output


def test_the_help_groups_the_flags_and_keeps_the_brackets_of_an_extra() -> None:
    """Twenty eight flags in one flat list is a list nobody reads. And `rich_markup_mode`
    reads `quackd[lan]` as markup, which printed an install that does not exist."""
    out = _help(["run", "--help"])
    assert "quackd[live]" in out, "an extra a reader is meant to type must survive"
    for group in ("Task", "Model", "Robot", "Output", "Memory"):
        assert group in out, group
    # Two flags became one, and half a rename is worse than either shape on its own: a help
    # page still offering `--provider` sends the reader to a flag the parser will refuse.
    assert "--llm" in out, "the one pilot flag has to be in the help"
    assert "--provider" not in out, "the old vendor flag is gone, help included"
    assert "--model " not in out, "the old model flag is gone, help included"
    root = _help(["--help"])
    assert "--no-color" in root
    for group in ("Inspect", "Run a duck", "Serve", "LAN", "Memory"):
        assert group in root, group
    assert "quackd run find-and-kick --llm fake" in root, "the epilog offers a first command"
    # the same markup trap, one level up: the core installs no robot, so the first command the
    # epilog offers has to carry the extra that makes it work, and Rich would eat the brackets
    assert "quackd[microduck]" in root, "the epilog's install line lost its extra to markup"


@pytest.mark.parametrize("command", ["serve", "check"])
def test_the_policy_help_keeps_the_extra_a_checkpoint_needs(command: str) -> None:
    """The same trap in `--policy`'s help, which `serve` and `check` share: it names the extra a
    LeRobot checkpoint needs, and an unescaped `[lerobot-vla]` printed as `which need quackd)`,
    an install line with nothing to install."""
    out = _help(["policy", command, "--help"])
    assert "quackd[lerobot-vla]" in out, "--policy's help lost its extra to markup"


def test_dash_h_is_the_same_as_help() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["-h"]).exit_code == 0
    assert "Usage" in runner.invoke(app, ["-h"]).output


def test_a_confirmation_prompt_is_asked_with_the_status_line_out_of_the_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live region redirects stdout and a y/N prompt writes without a newline, so under a
    running status the question is invisible until after it has been answered."""
    from quackd import cli as cli_mod
    from quackd import ui

    seen: list[str] = []

    class Watching:
        @contextlib.contextmanager
        def paused(self):  # type: ignore[no-untyped-def]
            seen.append("down")
            yield
            seen.append("up")

    monkeypatch.setattr(ui, "_ACTIVE", [Watching()])
    monkeypatch.setattr(cli_mod.typer, "confirm", lambda *a, **k: True)
    assert cli_mod._confirm_prompt("kick", {"leg": "right"}) is True
    assert cli_mod._acknowledge_prompt("nothing here detects a fall") is True
    assert seen == ["down", "up", "down", "up"]


def test_a_run_into_a_pipe_adds_no_status_line_to_what_a_script_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The status line is wired in whether the log is on or off, and a terminal is the only
    place it may appear. Under a runner or a pipe nothing of it reaches the output."""
    out = _log_run(tmp_path, monkeypatch, "--no-log")
    assert "SUCCESS" in out
    for chatter in ("waiting on", "observing", "choosing a verb", "finishing"):
        assert chatter not in out, chatter


def test_an_infeasible_run_exits_3_and_says_what_could(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 means the run happened and did not succeed. A run that never moved because the body
    could not is a different answer, and a script trying one body after another reads it."""
    from quackd.agent.providers import factory
    from quackd.agent.providers.base import ToolCall
    from quackd.agent.providers.fake import FakeProvider

    monkeypatch.setattr(
        factory,
        "make_provider",
        lambda *a, **k: FakeProvider(
            script=[
                ToolCall(
                    name="assess_task",
                    arguments={
                        "verdict": "infeasible",
                        "reason": "a basket of clothes is far past a beak",
                        "needs": {"payload_kg": 3.0, "manipulator": "gripper"},
                    },
                )
            ]
        ),
    )
    result = runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
        ],
    )
    assert result.exit_code == EXIT_INFEASIBLE == 3, result.output
    out = " ".join(result.output.split())
    assert "INFEASIBLE" in out
    assert "far past a beak" in out
    assert "No robot installed here meets needs" in out
    assert "toddlerbot at 1.484 kg" in out


# ── .env ────────────────────────────────────────────────────────────────────────────────


def test_env_is_also_read_from_the_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `uv tool install`ed quackd lives in its own directory, and the bare `load_dotenv()`
    walks up from there, so it finds the `.env` beside the venv and never the one beside the
    project you are standing in. The file next to the command you typed is read first.

    Neither file overrides a variable the environment already carries, which is what lets
    `QUACKD_REGISTRY_DIR=... quackd robot list` mean what it says on a machine whose `.env`
    names a different directory.
    """
    from quackd.registry import Registry, RobotEntry

    beside_the_command, in_the_environment = tmp_path / "beside", tmp_path / "exported"
    Registry(beside_the_command).add_robot(RobotEntry(name="dotenv-duck", spec="microduck:mock"))
    Registry(in_the_environment).add_robot(RobotEntry(name="exported-duck", spec="microduck:mock"))
    work = tmp_path / "work"
    work.mkdir()
    # as_posix: an unquoted dotenv value keeps its backslashes, and a Windows path is a
    # string of escapes to everything that reads one afterwards
    (work / ".env").write_text(
        f"QUACKD_REGISTRY_DIR={beside_the_command.as_posix()}\n", encoding="utf-8"
    )
    monkeypatch.chdir(work)
    monkeypatch.delenv("QUACKD_REGISTRY_DIR")  # the suite points it at a throwaway directory

    listed = runner.invoke(app, ["robot", "list"])
    assert listed.exit_code == 0, listed.output
    assert "dotenv-duck" in listed.output, "the .env in the working directory was not read"

    monkeypatch.setenv("QUACKD_REGISTRY_DIR", str(in_the_environment))
    again = runner.invoke(app, ["robot", "list"])
    assert again.exit_code == 0, again.output
    assert "exported-duck" in again.output and "dotenv-duck" not in again.output


def test_the_env_beside_the_command_is_read_before_the_one_beside_the_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order, pinned directly, because it is what the docs promise and what decides a
    disagreement: dotenv never overwrites a name it has already set, so whichever file is read
    first wins. The test above proves the working directory is read at all; this proves it is
    read before the walk up from quackd's own directory.

    Asked of the calls rather than of two files on disk, because the second lookup's root is
    wherever quackd happens to be installed and a test cannot put a file there. It also stays
    honest under coverage: `find_dotenv` returns the working directory when a trace function is
    set, so a run with `--cov` would read the right file even if the first call were deleted."""
    import quackd.cli as cli_module

    seen: list[str] = []
    monkeypatch.setattr(
        cli_module, "load_dotenv", lambda path=None, **kw: seen.append(str(path) if path else "")
    )
    # any command with a body: `--version` is eager and exits before the callback runs
    listed = runner.invoke(app, ["list-adapters"])
    assert listed.exit_code == 0, listed.output
    assert len(seen) == 2, seen
    assert seen[0] == str(Path.cwd() / ".env"), "the working directory is not read first"
    assert seen[1] == "", "the walk up from quackd's own directory is not read second"


# ── --image: the pictures that come with the task ───────────────────────────────────────


@pytest.fixture(scope="module")
def sketch(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real PNG on disk, made once, and deliberately not inside any test's `tmp_path`.

    The refusals below finish by asserting that `tmp_path` is still empty, which is the whole
    claim: nothing was written because nothing ran. A picture sitting in that same directory
    would turn that assertion into one about the picture.

    Drawn with an alpha channel on purpose. Everything `--image` sends is re-encoded to RGB
    PNG, so an RGBA source is one whose bytes on the wire are provably not a copy of the file
    they came from, and the test below can say which of the two was kept."""
    from PIL import Image, ImageDraw

    path = tmp_path_factory.mktemp("pictures") / "sketch.png"
    image = Image.new("RGBA", (48, 32), (240, 200, 40, 255))
    ImageDraw.Draw(image).ellipse((6, 4, 42, 28), outline=(10, 10, 10, 255))
    image.save(path)
    return path


@pytest.fixture(scope="module")
def plan(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A second picture with a name of its own, so the test for two `--image` flags can tell
    the two apart by what they are called rather than by counting files."""
    from PIL import Image

    path = tmp_path_factory.mktemp("pictures") / "plan.png"
    Image.new("RGB", (24, 24), (20, 90, 200)).save(path)
    return path


def _picture_run(tmp_path: Path, *flags: str, robot: str | None = "microduck:mock") -> Any:
    """`hello-world` on the simulated duck, with the runs directory pointed at `tmp_path`
    itself so that "nothing was written" is a question about one directory."""
    argv = [
        "run",
        "hello-world",
        "--llm",
        "fake",
        "--runs-dir",
        str(tmp_path),
        "--no-gif",
        "--no-log",
        *flags,
    ]
    if robot is not None:
        argv += ["--robot", robot]
    return runner.invoke(app, argv)


def test_a_picture_is_refused_by_a_pilot_that_cannot_see(tmp_path: Path, sketch: Path) -> None:
    """Refused rather than quietly dropped. A blind pilot handed "draw what is in the picture"
    with no picture improvises something, and the only record of why would be a line in a
    transcript nobody reads twice. The refusal names the pilot, because which model is driving
    is the thing the reader has to change."""
    result = _picture_run(tmp_path, "--image", str(sketch))
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())  # the console wraps the line at the terminal width
    assert "fake scripted:hello-world does not take images" in flat, flat
    assert "cannot be given 1 picture" in flat
    assert "quackd list-models" in flat, "the refusal must say where to look for a model"
    assert "Traceback" not in result.output
    assert list(tmp_path.iterdir()) == [], "the refusal came before the run directory"


def test_vision_carries_the_picture_into_the_run_directory_byte_for_byte(
    tmp_path: Path, sketch: Path
) -> None:
    """`--vision` is what a scripted pilot has instead of a vendor that takes images, and it is
    the only way to walk a picture through the whole loop with no key. The copy beside the
    transcript is the bytes the model was sent, not the file they were made from, so a reader
    arguing about the run afterwards is looking at what the pilot looked at."""
    from quackd.agent.images import load_task_images

    result = _picture_run(tmp_path, "--vision", "--image", str(sketch))
    assert result.exit_code == 0, result.output
    assert "SUCCESS" in result.output
    (run_dir,) = list(tmp_path.iterdir())
    written = run_dir / "images" / "00-sketch.png"
    assert written.exists(), sorted(p.name for p in run_dir.iterdir())
    assert written.read_bytes() == load_task_images([str(sketch)])[0].png
    assert written.read_bytes() != sketch.read_bytes(), "the run kept the file, not the PNG sent"


def test_two_pictures_both_arrive_in_the_order_they_were_typed(
    tmp_path: Path, sketch: Path, plan: Path
) -> None:
    """`--image` is repeatable, and a repeatable flag that kept only the last one would be a
    task about two pictures run against one, with nothing on screen to say so."""
    result = _picture_run(tmp_path, "--vision", "--image", str(sketch), "--image", str(plan))
    assert result.exit_code == 0, result.output
    (run_dir,) = list(tmp_path.iterdir())
    assert sorted(p.name for p in (run_dir / "images").iterdir()) == [
        "00-sketch.png",
        "01-plan.png",
    ]
    transcript = (run_dir / "transcript.jsonl").read_text(encoding="utf-8")
    assert '"name": "sketch.png"' in transcript and '"name": "plan.png"' in transcript


def test_a_picture_that_is_not_there_is_refused_before_anything_runs(tmp_path: Path) -> None:
    """The loader runs before the robot is connected, so a mistyped path costs a line rather
    than a connected arm and a run directory to explain later. The path is quoted back as it
    was typed, because that is the string with the typo in it."""
    result = _picture_run(tmp_path, "--vision", "--image", "nope.png")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "--image nope.png: no such file" in flat, flat
    assert "Traceback" not in result.output
    assert list(tmp_path.iterdir()) == [], "the refusal came before the run directory"


def test_a_picture_is_refused_for_a_flock(tmp_path: Path, sketch: Path) -> None:
    """One picture and several bodies is a question the flag cannot answer: whose task is it
    about? Dropping it silently is the failure the flag exists to prevent."""
    result = _picture_run(tmp_path, "--vision", "--flock", "2", "--image", str(sketch))
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "--image is for one robot, and this run has several" in flat, flat
    assert "drop --flock and --robots" in flat
    assert list(tmp_path.iterdir()) == [], "the refusal came before the run directory"


def test_a_picture_is_refused_when_robots_names_two_bodies(tmp_path: Path, sketch: Path) -> None:
    """The same refusal by the other door: `--flock N` is not the only way to end up with more
    than one body on the line, and `--robots` reaches a different branch of the dispatch."""
    result = _picture_run(
        tmp_path,
        "--vision",
        "--robots",
        "a=microduck:mock,b=microduck:mock",
        "--image",
        str(sketch),
        robot=None,
    )
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "--image is for one robot, and this run has several" in flat, flat
    assert list(tmp_path.iterdir()) == [], "the refusal came before the run directory"


# ── --by-hand: the person sets the pose the run starts from ─────────────────────────────


def _arm_registry(tmp_path: Path, *, rest_pose: bool) -> Path:
    """A registry holding one `lerobot:mock` arm called `arm-01`, built through the CLI rather
    than by writing the file: the pose stored is then the one `quackd robot rest-pose` records
    off the arm, which is what `--by-hand` goes looking for."""
    reg = tmp_path / "registry"
    added = runner.invoke(
        app, ["robot", "add", "arm-01", "lerobot:mock", "--registry-dir", str(reg)]
    )
    assert added.exit_code == 0, added.output
    if rest_pose:
        recorded = runner.invoke(
            app, ["robot", "rest-pose", "arm-01", "--yes", "--registry-dir", str(reg)]
        )
        assert recorded.exit_code == 0, recorded.output
    return reg


def _by_hand_run(tmp_path: Path, *flags: str, duck: str = "hello-world") -> Any:
    """A `--by-hand` run whose runs directory is `tmp_path` itself, for the refusals that have
    to leave it empty."""
    return runner.invoke(
        app,
        [
            "run",
            duck,
            "--llm",
            "fake",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
            "--no-log",
            "--by-hand",
            *flags,
        ],
    )


def test_by_hand_and_dry_run_are_refused_as_opposites(tmp_path: Path) -> None:
    """A dry run moves nothing at either end. Taking torque off an arm is the one thing here
    that is not a command to the robot but a change to it, so the two flags cannot both be
    honoured and the run says which to type instead of guessing."""
    result = _by_hand_run(tmp_path, "--robot", "microduck:mock", "--dry-run")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "--by-hand and --dry-run ask for opposite things" in flat, flat
    assert "rehearse the task with --dry-run" in flat
    assert list(tmp_path.iterdir()) == [], "the refusal came before the run directory"


def test_by_hand_is_refused_for_a_flock(tmp_path: Path) -> None:
    """One person cannot place four arms at once, and an arm nobody was asked to place would
    be released limp at its rest pose and left there for the length of the run."""
    result = _by_hand_run(tmp_path, "--robot", "microduck:mock", "--flock", "2")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "--by-hand is one person placing one arm, and this run has several robots" in flat, flat
    assert "drop --flock and --robots" in flat
    assert list(tmp_path.iterdir()) == [], "the refusal came before the run directory"


def test_by_hand_is_refused_on_a_body_that_is_not_the_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six of the seven bodies here have nothing to hand over: a walking duck with its torque
    off is a duck on the floor. The refusal names the body, because the flag is right and the
    `--robot` beside it is the half to change.

    The terminal seam is opened deliberately, so that the message under test is the one about
    the body and not the one about there being nobody to ask."""
    monkeypatch.setattr("quackd.cli._can_prompt", lambda: True)
    result = _by_hand_run(tmp_path, "--robot", "microduck:mock")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "microduck:mock is not a body a person places by hand" in flat, flat
    assert "only the LeRobot arm is" in flat
    assert "quackd list-adapters" in flat
    assert "Traceback" not in result.output
    assert list(tmp_path.iterdir()) == [], "the refusal came before the run directory"


def test_by_hand_needs_a_rest_pose_to_let_go_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release refuses anywhere but the recorded rest pose, because an arm held up by
    torque alone falls when the torque goes. An arm with no pose recorded could therefore never
    be handed over at all, and hearing that here is cheaper than hearing it from the arm."""
    monkeypatch.setattr("quackd.cli._can_prompt", lambda: True)
    reg = _arm_registry(tmp_path, rest_pose=False)
    runs = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--llm",
            "fake",
            "--robot",
            "arm-01",
            "--registry-dir",
            str(reg),
            "--runs-dir",
            str(runs),
            "--no-gif",
            "--no-log",
            "--by-hand",
        ],
    )
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "--by-hand releases the arm at its recorded rest pose" in flat, flat
    assert "this arm has none recorded" in flat
    assert "quackd robot rest-pose arm-01" in flat, "the hint must name the arm to record"
    assert "Traceback" not in result.output
    assert not runs.exists(), "the refusal came before the run directory"


def test_by_hand_with_no_terminal_to_ask_on_is_refused(tmp_path: Path) -> None:
    """A cron job, a CI step, a run piped into a file: nobody is there to press Enter, and the
    arm would be released limp at its rest pose and wait for an answer that never comes.

    The arm here is complete — registered, with a rest pose — so the only thing left to refuse
    it for is the missing terminal. Nothing is monkeypatched: under the runner stdin is not a
    terminal, which is the very situation this is about, and the test asks what the command
    did rather than how it found out."""
    reg = _arm_registry(tmp_path, rest_pose=True)
    runs = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--llm",
            "fake",
            "--robot",
            "arm-01",
            "--registry-dir",
            str(reg),
            "--runs-dir",
            str(runs),
            "--no-gif",
            "--no-log",
            "--by-hand",
        ],
    )
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "--by-hand waits for you to press Enter, and there is no terminal to ask on" in flat
    assert "run it from a terminal" in flat
    assert "Traceback" not in result.output
    assert not runs.exists(), "the refusal came before the run directory"


def test_by_hand_hands_the_arm_over_and_asks_for_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole flag, end to end, with the two things a shell cannot provide here faked and
    nothing else: that a terminal is present, and that somebody pressed Enter.

    `wait_for_enter` is replaced rather than fed keystrokes because the real one waits on the
    key thread, and the key thread only exists where stdin is a terminal. A test that reached
    the real wait would sit there until the suite's own watchdog killed it.

    What is asserted is what the person at the bench is told, in order: the arm is theirs, then
    that they can let go of the pose they set, then that the gripper opens before the arm folds
    up. Those three lines are the entire user interface of a hand-off."""
    from quackd.safety import KillSwitch

    async def pressed(self: KillSwitch, *, timeout_s: float | None = None, **_: Any) -> bool:
        return True

    monkeypatch.setattr("quackd.cli._can_prompt", lambda: True)
    monkeypatch.setattr(KillSwitch, "wait_for_enter", pressed)

    reg = _arm_registry(tmp_path, rest_pose=True)
    runs = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--llm",
            "fake",
            "--robot",
            "arm-01",
            "--registry-dir",
            str(reg),
            "--runs-dir",
            str(runs),
            "--no-gif",
            "--no-log",
            "--by-hand",
        ],
    )
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "the arm is yours: torque is off at its rest pose" in flat, flat
    assert "hold it where you want the run to start, and press Enter" in flat
    assert "you can let go" in flat, "the person is never told the arm is holding what they set"
    assert "the run is over and the arm is holding where it ended" in flat
    assert "SUCCESS" in result.output

    from quackd.agent.transcript import Transcript

    events = Transcript.read(next(runs.rglob("transcript.jsonl")))
    stages = [e["stage"] for e in events if e["kind"] == "hand_off"]
    assert stages == ["released", "held", "unloaded"], stages
    # the Enter faked above answers every wait, and the end-of-run offer is one: it must not
    # have been put at all, because this arm folded up and was let go of at its pose
    assert not [e for e in events if e["kind"] == "release"], "an offer nobody needed was made"
    assert "the arm did not reach its rest pose" not in flat


# ── quackd robot release: torque off where the arm stands, for a person holding it ──────
#
# The second door ADR-0039's first one does not replace: `--by-hand` releases at the rest pose
# and nowhere else, and this releases wherever the arm is, because a person asked for it at a
# terminal after being told to hold the arm. On 2026-09-23 the only way to take torque off an
# arm a run had left holding itself up was the power switch.


def _registered_arm(tmp_path: Path, name: str) -> Path:
    """A registry with one `lerobot:mock` arm under `name` and its rest pose recorded, built
    through the CLI as a person would."""
    reg = tmp_path / "registry"
    added = runner.invoke(app, ["robot", "add", name, "lerobot:mock", "--registry-dir", str(reg)])
    assert added.exit_code == 0, added.output
    recorded = runner.invoke(app, ["robot", "rest-pose", name, "--yes", "--registry-dir", str(reg)])
    assert recorded.exit_code == 0, recorded.output
    return reg


def _release(reg: Path, name: str, *flags: str, answer: str | None = None) -> Any:
    return runner.invoke(
        app, ["robot", "release", name, "--registry-dir", str(reg), *flags], input=answer
    )


def _watch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The order of the two things a person must see happen in order: being asked, and the
    arm being connected, which is the first thing that takes its torque off."""
    import typer

    from quackd_lerobot.mock import LeRobotMock

    seen: list[str] = []
    asked, connected = typer.confirm, LeRobotMock.connect

    def confirm(*args: Any, **kwargs: Any) -> Any:
        seen.append("asked")
        return asked(*args, **kwargs)

    async def connect(self: Any) -> Any:
        seen.append("connected")
        return await connected(self)

    monkeypatch.setattr(typer, "confirm", confirm)
    monkeypatch.setattr(LeRobotMock, "connect", connect)
    return seen


@pytest.mark.parametrize("name", ["lab-arm", "bench-2"])
def test_release_warns_then_asks_and_only_then_connects_and_reads_torque_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Connecting is the first thing that takes an arm's torque off, because LeRobot configures
    its motors with torque off, so a warning printed after the connect arrives after the arm
    has already been limp once. The warning comes first, then the question with the arm's
    name in it, then the connect, the joints where the person is holding it, what torque read
    back, and the close's own line: the arm is limp in your hands, put it down."""
    monkeypatch.setattr("quackd.cli._can_prompt", lambda: True)
    reg = _registered_arm(tmp_path, name)
    seen = _watch(monkeypatch)
    result = _release(reg, name, answer="y\n")
    assert result.exit_code == 0, result.output
    assert seen == ["asked", "connected"], seen

    flat = " ".join(result.output.split())
    order = [
        flat.index("connecting takes torque off every motor for a moment"),
        flat.index("hold it now"),
        flat.index(f"release torque on {name}? [y/N]"),
        flat.index(f"{name} (lerobot:mock) is at"),
        flat.index(f"torque reads off on every joint of {name}"),
        flat.index("the arm is limp and in your hands"),
    ]
    assert order == sorted(order), flat
    assert "shoulder_lift -90.0" in flat and "elbow_flex 90.0" in flat, "the joints it was at"
    assert "(torque was taken off where it stood, because you asked for it)" in flat, flat
    assert "put it down before you let go of it" in flat


def test_release_with_yes_warns_and_connects_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--yes` skips the question and nothing else: the warning still comes before the
    connect, which is why its help says to hold the arm before running it. No terminal is
    needed, since nothing is asked."""
    reg = _registered_arm(tmp_path, "arm-07")
    seen = _watch(monkeypatch)
    result = _release(reg, "arm-07", "--yes")
    assert result.exit_code == 0, result.output
    assert seen == ["connected"], "a question was put, or the arm was never connected"
    flat = " ".join(result.output.split())
    assert "[y/N]" not in flat
    assert flat.index("hold it now") < flat.index("arm-07 (lerobot:mock) is at")
    assert "torque reads off on every joint of arm-07" in flat


@pytest.mark.parametrize(
    "away", [{"shoulder_pan": 35.0}, {"elbow_flex": 40.0, "wrist_flex": -25.0}], ids=["1", "2"]
)
def test_release_lets_go_of_an_arm_away_from_its_rest_pose(
    tmp_path: Path, away: dict[str, float]
) -> None:
    """The arm this command exists for is one a run could not fold, so it is somewhere other
    than its recorded pose, which is the one place `--by-hand`'s door opens. The registered pose
    here is moved off where the mock arm stands, so a release through the first door would be
    refused and this one is not."""
    from quackd.registry import Registry
    from quackd_lerobot.mock import REST

    reg = _registered_arm(tmp_path, "arm-02")
    Registry(reg).update_robot("arm-02", {"rest_pose": dict(REST) | away})
    result = _release(reg, "arm-02", "--yes")
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "torque reads off on every joint of arm-02" in flat, flat
    assert "(torque was taken off where it stood, because you asked for it)" in flat
    assert "the arm is not at its rest pose" not in flat, "the rest-pose rule was applied"


def test_release_with_no_terminal_and_no_yes_refuses_before_anything_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the runner stdin is not a terminal, which is the situation itself: a pipe or a
    script cannot answer "hold the arm", so it is told to pass --yes and the arm is never
    connected, which would have been the first thing to take its torque off."""
    reg = _registered_arm(tmp_path, "arm-01")
    seen = _watch(monkeypatch)
    result = _release(reg, "arm-01", answer="y\n")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "no terminal to ask on: pass --yes to release it" in flat, flat
    assert "quackd robot release arm-01 --yes" in flat
    assert seen == [], "the arm was connected with nobody to ask"
    assert "hold it now" not in flat, "a person was told to hold an arm nothing was going to touch"


def test_release_answered_no_never_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("quackd.cli._can_prompt", lambda: True)
    reg = _registered_arm(tmp_path, "arm-01")
    seen = _watch(monkeypatch)
    result = _release(reg, "arm-01", answer="n\n")
    assert result.exit_code == 0, result.output
    assert seen == ["asked"], seen
    assert "nothing was connected, and the arm is as it was" in " ".join(result.output.split())


def test_release_refuses_a_body_that_is_not_the_arm(tmp_path: Path) -> None:
    """A walking duck with its torque off is a duck on the floor, and six bodies of seven are
    never handed to a person at all: `supports_hand_off` is the arm's alone."""
    reg = tmp_path / "registry"
    added = runner.invoke(
        app, ["robot", "add", "duck", "microduck:mock", "--registry-dir", str(reg)]
    )
    assert added.exit_code == 0, added.output
    result = _release(reg, "duck", "--yes")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "duck (microduck:mock) is not a body quackd takes torque off" in flat, flat
    assert "only the LeRobot arm is" in flat and "Traceback" not in result.output


@pytest.mark.parametrize(
    ("how", "torque_on", "said"),
    [
        (
            "refused",
            ("shoulder_pan", "elbow_flex"),
            "torque still reads on for shoulder_pan, elbow_flex: cut the power",
        ),
        ("released", ("wrist_roll",), "torque still reads on for wrist_roll: cut the power"),
        ("released", None, "torque was taken off and could not be read back"),
        ("refused", None, "nothing was released"),
    ],
    ids=["all still on", "one still on", "never read back", "never sent"],
)
def test_release_exits_non_zero_unless_every_motor_read_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    how: str,
    torque_on: tuple[str, ...] | None,
    said: str,
) -> None:
    """ "Torque reads off" is said only when the register was read and every motor said 0. A
    motor still on, a release nobody read back and a release never sent each exit 1 and say
    which, because a script, and a person, act on the exit code."""
    from quackd.adapters.base import HandResult
    from quackd_lerobot.mock import LeRobotMock

    async def let_go(self: Any, *, anywhere: bool = False) -> HandResult:
        assert anywhere, "the command asked for the rest-pose rule"
        return HandResult(how, "the arm's own reason", torque_on=torque_on)  # type: ignore[arg-type]

    monkeypatch.setattr(LeRobotMock, "let_go", let_go)
    reg = _registered_arm(tmp_path, "arm-01")
    result = _release(reg, "arm-01", "--yes")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert said in flat, flat
    assert "torque reads off" not in flat


def test_release_whose_connect_fails_exits_non_zero_and_says_keep_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quackd.transport.base import TransportError
    from quackd_lerobot.mock import LeRobotMock

    async def connect(self: Any) -> Any:
        raise TransportError("the port went away")

    reg = _registered_arm(tmp_path, "arm-01")
    monkeypatch.setattr(LeRobotMock, "connect", connect)
    result = _release(reg, "arm-01", "--yes")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "lerobot:mock: the port went away" in flat, flat
    assert "keep hold of the arm" in flat


def _missed_rest_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    stopped: dict[str, float],
    why: str,
) -> Any:
    """A run on a registered mock arm whose rest move misses, stopping with the joints in
    `stopped`, which aborts it before the pilot and leaves the teardown's rest move missing
    too. The arm really is away from its pose afterwards, so the close judges it as one."""
    from quackd.adapters.base import RestResult
    from quackd_lerobot.mock import LeRobotMock

    async def misses(self: Any) -> RestResult:
        self.sequence.append("rest")
        self.joints.update(stopped)
        return RestResult("stalled", why)

    reg = _registered_arm(tmp_path, name)
    monkeypatch.setattr(LeRobotMock, "go_to_rest", misses)
    runs = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--llm",
            "fake",
            "--robot",
            name,
            "--registry-dir",
            str(reg),
            "--runs-dir",
            str(runs),
            "--no-gif",
            # the log on: the close's own line is a note, and a run with the log off prints
            # no note at all
            "--log",
        ],
    )
    return result, runs


def test_a_run_whose_rest_move_missed_offers_the_person_at_the_terminal_torque_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offer end to end: the CLI wires the terminal into the run whenever it can prompt,
    binds it to the kill switch, and Enter releases the arm and ends on the limp line."""
    from quackd.agent.transcript import Transcript
    from quackd.safety import KillSwitch

    async def pressed(self: KillSwitch, *, timeout_s: float | None = None, **_: Any) -> bool:
        return True

    monkeypatch.setattr("quackd.cli._can_prompt", lambda: True)
    monkeypatch.setattr(KillSwitch, "wait_for_enter", pressed)
    why = "wrist_flex is at 33 with a goal of 0, and it has stopped moving"
    result, runs = _missed_rest_run(tmp_path, monkeypatch, "arm-03", {"wrist_flex": 33.0}, why)
    assert result.exit_code == 1, result.output  # the run aborted: it never reached its pose
    flat = " ".join(result.output.split())
    assert f"the arm did not reach its rest pose ({why}), so it is holding itself up" in flat
    assert "Hold it and press Enter to release torque now" in flat, flat
    # the log's line for the event, said to the person whether or not the log is on, and then
    # the close's own line
    assert "release released: torque is off where the arm stands" in flat, flat
    assert "torque is off where the arm stands: the arm is in your hands, so put it down" in flat
    assert "the arm is limp and in your hands" in flat
    assert "torque was left on" not in flat

    events = Transcript.read(next(runs.rglob("transcript.jsonl")))
    assert [e["stage"] for e in events if e["kind"] == "release"] == ["released"]
    assert [e["what"] for e in events if e["kind"] == "prompt"] == ["release"]


@pytest.mark.parametrize("name", ["lab-arm", "bench-2"])
def test_a_run_with_nobody_at_the_terminal_keeps_torque_and_names_the_arm_s_way_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """No terminal, no offer, and torque kept, as before. What changed is the line: it names
    the command that releases the arm and the one that parks it, with the name the arm was
    registered under, which is the name those commands take, and it puts holding the arm
    before all of them, because both commands begin by connecting."""
    result, _runs = _missed_rest_run(
        tmp_path, monkeypatch, name, {"elbow_flex": 12.0}, "elbow_flex is at 12 with a goal of 90"
    )
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "Hold it and press Enter" not in flat, "an offer was made to nobody"
    assert "so torque was left on and it will not fall as it stands" in flat, flat
    assert (
        "hold it first, because connecting takes torque off every motor for a moment, then run "
        f"quackd robot release {name}, or quackd doctor --robot {name} to park it, or cut its "
        "power"
    ) in flat, flat


# ── what a person is told at the arm, matched to what quackd did ─────────────────────────


@pytest.mark.parametrize("ending", ["enter", "kill switch", "no keys", "stopped", "timeout"])
async def test_the_terminal_hand_off_says_how_each_wait_ended(ending: str) -> None:
    """The wait answers with a bool, and a Ctrl-C, a terminal nobody can type into and an
    empty room all answered False, so the end-of-run offer filed a person's Ctrl-C as
    "nobody pressed Enter". The terminal now says which ending it was (`ended`), the bool is
    unchanged for the two `--by-hand` waits that need nothing more, and the saved terminal
    names the ending too."""
    from quackd.cli import _TerminalHandOff
    from quackd.safety import KillSwitch

    abort = asyncio.Event()
    switch = KillSwitch(abort)
    loop = asyncio.get_running_loop()
    switch._loop = loop  # what `install()` sets, without taking the process's SIGINT
    person = _TerminalHandOff()
    person.bind(switch)
    if ending == "enter":
        loop.call_later(0.05, switch.entered.set)
    elif ending == "kill switch":
        loop.call_later(0.05, switch._fire, "Ctrl-C")
    elif ending == "no keys":
        switch.keys_ended.set()
    elif ending == "stopped":
        loop.call_later(0.05, abort.set)
    came = await person.wait(
        "hold it", timeout_s=0.3 if ending == "timeout" else 5.0, until_abort=ending == "stopped"
    )
    assert came is (ending == "enter")
    assert person.ended == ending


def _holding_out(monkeypatch: pytest.MonkeyPatch, holdouts: tuple[str, ...]) -> None:
    """The mock arm's release keeps these motors on, as the real read-back can find them."""
    from quackd_lerobot.mock import LeRobotMock

    release = LeRobotMock.let_go

    async def let_go(self: LeRobotMock, *, anywhere: bool = False) -> Any:
        self.release_holdouts = holdouts
        return await release(self, anywhere=anywhere)

    monkeypatch.setattr(LeRobotMock, "let_go", let_go)


@pytest.mark.parametrize(
    ("holdouts", "away", "last"),
    [
        (
            JOINT_NAMES,
            {"shoulder_pan": 35.0},
            "the release did not take, so torque was left on and it will not fall as it "
            "stands: hold it and cut its power",
        ),
        (
            JOINT_NAMES,
            {},
            "the release did not take, and the close then took torque off at the rest pose, as "
            "every close there does, with nothing to read it back: hold the arm, and cut its "
            "power if it still holds itself up",
        ),
        (
            ("elbow_flex",),
            {"shoulder_pan": 35.0},
            "but elbow_flex still reads torque on and holds: keep hold of the arm, put it down, "
            "and cut its power to let go of it",
        ),
    ],
    ids=["refused away from rest", "refused at rest", "one motor held out"],
)
def test_release_ends_on_a_line_that_matches_what_happened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    holdouts: tuple[str, ...],
    away: dict[str, float],
    last: str,
) -> None:
    """The command's last line is the close's, and three endings used to contradict what came
    before it. Every motor still on, away from the rest pose: the torque note sent the person
    to `quackd robot release`, the command that had just failed one line up. Every motor
    still on at the rest pose: the close let go of the arm without a word, after "cut the
    power". One motor still on: "nothing is holding it up", over a joint that was. Each now
    ends on a line that says what quackd did, and all three still exit 1."""
    from quackd.registry import Registry
    from quackd_lerobot.mock import REST

    reg = _registered_arm(tmp_path, "arm-04")
    Registry(reg).update_robot("arm-04", {"rest_pose": dict(REST) | away})
    _holding_out(monkeypatch, holdouts)
    result = _release(reg, "arm-04", "--yes")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert f"torque still reads on for {', '.join(holdouts)}: cut the power" in flat, flat
    assert flat.endswith(last), flat
    assert "quackd robot release" not in flat, "sent back to the command that just failed"
    assert "nothing is holding it up" not in flat


def test_doctor_builds_a_registered_arm_under_its_name_and_warns_before_it_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two things `doctor --robot NAME` got wrong about an arm left holding itself up, which
    is exactly the arm its torque note sends a person to it with.

    It dropped the name: a registered name was resolved to the bare spec, so the arm was built
    with the default id, and a real one looked up the calibration of that id whatever it was
    registered as, and every line it wrote said NAME. It is built under its name now, as a run
    builds it. And it connected without a word, though connecting takes torque off every
    motor for a moment: the warning `quackd robot release` prints is printed here too, before
    the connect, and only for a body that is handed to people."""
    import quackd_lerobot
    from quackd import cli, doctor
    from quackd.adapters.base import RestResult
    from quackd_lerobot.mock import LeRobotMock

    monkeypatch.setattr(doctor, "_probe_models", lambda url, timeout_s=1.5: ("down", "not running"))
    reg = _registered_arm(tmp_path, "bench-2")
    built: list[str | None] = []
    make = quackd_lerobot.make

    def recording(backend: str, **kwargs: Any) -> Any:
        built.append(kwargs.get("robot_id"))
        return make(backend, **kwargs)

    monkeypatch.setattr(quackd_lerobot, "make", recording)
    seen: list[str] = []
    warning = cli._doctor_warning

    def warned(**kwargs: Any) -> str:
        seen.append("warned")
        return warning(**kwargs)

    monkeypatch.setattr(cli, "_doctor_warning", warned)
    connected = LeRobotMock.connect

    async def connect(self: LeRobotMock) -> Any:
        seen.append("connected")
        return await connected(self)

    async def misses(self: LeRobotMock) -> RestResult:
        # the rest move stops short, so the close keeps torque and says so, with the name
        self.sequence.append("rest")
        self.joints["wrist_flex"] = 33.0
        return RestResult("stalled", "wrist_flex stopped 33 deg short")

    monkeypatch.setattr(LeRobotMock, "connect", connect)
    monkeypatch.setattr(LeRobotMock, "go_to_rest", misses)
    result = runner.invoke(
        app, ["doctor", "--robot", "bench-2", "--address", "mock://arm", "--registry-dir", str(reg)]
    )
    flat = " ".join(result.output.split())
    assert built == ["bench-2"], built
    assert seen == ["warned", "connected"], seen
    assert (
        "connecting takes torque off every motor for a moment, because LeRobot configures them "
        "with it off: support the arm until doctor has finished with it"
    ) in flat, flat
    assert "quackd robot release bench-2" in flat and "doctor --robot bench-2" in flat, flat
    assert "release NAME" not in flat, "the arm was built without the name it was asked for by"

    duck = runner.invoke(app, ["doctor", "--robot", "microduck:mock", "--address", "mock://x"])
    assert "connecting takes torque off" not in " ".join(duck.output.split()), duck.output


def test_the_hardware_warnings_say_when_the_arm_is_the_simulator() -> None:
    """`doctor --robot` and `robot release` warn a person before they connect a body that is
    handed to people, and tell them to support it or hold it. The arm's simulator is handed
    over as the arm is, and nobody can hold it, so each line says it is the simulator instead.
    A real arm's lines are the ones they always were, word for word."""
    from quackd import cli
    from quackd.adapters.base import CONNECTING_TAKES_TORQUE_OFF
    from quackd.adapters.factory import make_adapter

    real = f"{CONNECTING_TAKES_TORQUE_OFF}, because LeRobot configures them with it off"
    assert cli._connect_warning() == real
    assert cli._doctor_warning() == f"{real}: support the arm until doctor has finished with it"
    assert cli._release_warning() == (
        f"{real}, and the release then lets the arm fall from wherever it is: "
        "hold it now, and keep hold of it until it is down"
    )
    simulated = (
        cli._connect_warning(simulator=True),
        cli._doctor_warning(simulator=True),
        cli._release_warning(simulator=True),
    )
    for line in simulated:
        assert line.startswith("this is the arm's simulator: "), line
        assert "support the arm until" not in line and "hold it now" not in line, line
    assert simulated[1].endswith("and there is no arm to support")
    assert simulated[2].endswith("with no arm to hold")

    # asked of the built adapter, and a body that does not say is not a simulator
    assert not cli._is_simulator(make_adapter("lerobot:mock"))
    assert not cli._is_simulator(make_adapter("microduck:mock"))
    assert cli._is_simulator(make_adapter("lerobot:mujoco"))
    assert not cli._is_simulator(SimpleNamespace(is_simulator="yes"))


# ── --run-name and --price: what the run is called, and what it cost ────────────────────


def _verdict_run(tmp_path: Path, *flags: str) -> Any:
    """`hello-world` on the mock duck, at a width that leaves the counter line whole.

    Not `_run_hello`: Rich folds the verdict at the terminal width, and at the default eighty
    columns `cost $0.0163` lands half on one line and half on the next with the panel's border
    between the halves, so a flattened `"cost $0.0163" in output` is a check that can never
    pass. The runs directory is `tmp_path` itself, because the refusals below finish by
    asserting that nothing at all was written."""
    return runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
            *flags,
        ],
        env={"COLUMNS": "200"},
    )


def _counters(output: str) -> str:
    """The one line of counters under the verdict, without the panel's border and padding.

    Read as a line rather than as a substring of everything, so that "no cost counter" is a
    claim about the counters and not about whether the word appears somewhere on screen."""
    lines = [
        line.strip("│| ").strip()
        for line in output.splitlines()
        if "steps " in line and "llm calls" in line
    ]
    assert len(lines) == 1, output
    return lines[0]


def test_a_priced_run_says_how_long_it_took_and_what_it_cost(tmp_path: Path) -> None:
    """The two counters this release adds to the verdict.

    A run of a frontier model costs real money and takes real minutes, and until now neither
    number was anywhere on screen when it ended: you read the token counts and did the
    arithmetic yourself, or you found out at the end of the month.

    `--price` rather than a catalogued model because the only provider a test may run is
    `fake`, which is free: `$0` would prove the counter prints and nothing about the sum
    behind it."""
    result = _verdict_run(tmp_path, "--price", "in=3,out=15")
    assert result.exit_code == 0, result.output
    counters = _counters(result.output)
    assert "time " in counters, counters
    assert "(model " in counters, "the split is the useful half: how much of it was waiting"
    assert re.search(r"cost \$\d", counters), counters


def test_a_fake_run_with_no_price_is_free_rather_than_unpriced(tmp_path: Path) -> None:
    """`fake` sends nothing to anybody, so a run on it costs nothing and says so.

    The distinction is the whole reason `Price` and `None` are different things: `unpriced`
    means quackd has no rate for this model and the number is unknown, `$0` means the number
    is known and it is nothing. A model with no published rate printing `$0` would be telling
    somebody their frontier run was free."""
    result = _verdict_run(tmp_path)
    assert result.exit_code == 0, result.output
    counters = _counters(result.output)
    assert "cost $0" in counters and "unpriced" not in counters, counters


def test_a_run_name_becomes_the_end_of_the_run_directory(tmp_path: Path) -> None:
    """An afternoon at the bench is forty directories named after the same duck and the same
    minute, and the only way to find the one you meant is to open them.

    The name is slugged rather than taken as typed, because a run directory gets typed back
    into `quackd log` and pasted into a shell. It goes after the duck and before the
    collision counter, so the timestamp still sorts the runs."""
    result = _verdict_run(tmp_path, "--run-name", "Example 1")
    assert result.exit_code == 0, result.output
    (run_dir,) = list(tmp_path.iterdir())
    assert run_dir.name.endswith("-hello-world-example-1"), run_dir.name


def test_a_bad_run_name_or_price_is_refused_before_a_run_directory_exists(tmp_path: Path) -> None:
    """Both are typing mistakes, and a typing mistake should cost one sentence.

    They are checked at the top of the run, before a provider is built, before the robot is
    connected and before the directory is made, so an empty `tmp_path` afterwards is the
    claim: nothing moved and there is nothing to clean up. A run directory for a run that
    never started is one somebody has to explain later."""
    named = _verdict_run(tmp_path, "--run-name", "!!!")
    assert named.exit_code == 1, named.output
    flat = " ".join(named.output.split())  # the console wraps the line at the terminal width
    assert "--run-name '!!!' has no ASCII letters or digits in it" in flat, flat
    assert "Traceback" not in named.output
    assert list(tmp_path.iterdir()) == [], "the refusal came after the run directory"

    priced = _verdict_run(tmp_path, "--price", "junk")
    assert priced.exit_code == 1, priced.output
    flat = " ".join(priced.output.split())
    assert "--price 'junk' is not a price" in flat, flat
    assert "in=3,out=15" in flat, "the refusal has to show what a price looks like"
    assert "Traceback" not in priced.output
    assert list(tmp_path.iterdir()) == [], "the refusal came after the run directory"


def test_the_help_offers_the_name_on_disk_and_the_price_of_the_model() -> None:
    """A flag nobody can find is a flag nobody uses, and `--price` is the one that decides
    whether a run can be costed at all.

    `--price` is on `run` and not on `record`: a recording is a GIF for a README, and its
    cost is not what anybody reaches for the command to learn. The brackets of the optional
    cache rates are the same markup trap as `quackd[live]` a few tests up, which is why the
    example is asserted whole."""
    run_help = _help(["run", "--help"])
    assert "--run-name" in run_help and "--price" in run_help
    assert '"Example 1" becomes example-1' in run_help, "the help must show the name on disk"
    assert "in=3,out=15[,cache_read=0.3,cache_write=3.75]" in run_help, "markup ate the brackets"
    record_help = _help(["record", "--help"])
    assert "--run-name" in record_help
    assert "--price" not in record_help


# ── terminal.txt: what you saw while it ran ─────────────────────────────────────────────


def _typed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *flags: str,
    duck: str = "hello-world",
    answer: str | None = None,
) -> Any:
    """One run on the mock duck, invoked as if somebody had typed it at a shell.

    `sys.argv` because `CliRunner.invoke` parses a list of its own and never touches it, and
    `quackd.command` reads the command line off `sys.argv`: without this every run here
    would write down pytest's own arguments. The first word is a console script's path on
    purpose, because what the record has to show instead of it is `quackd`.

    The runs directory is a subdirectory rather than `tmp_path` itself, so a refused run can
    be asked to have left nothing at all under it. `COLUMNS` for the reason `_verdict_run`
    sets it: the capture is a console of its own and folds long lines at its width like any
    other, and these tests read the file a line at a time. `answer` is what a person types
    at a prompt."""
    args = [
        "run",
        duck,
        "--llm",
        "fake",
        "--robot",
        "microduck:mock",
        "--runs-dir",
        str(tmp_path / "runs"),
        "--no-gif",
        *flags,
    ]
    monkeypatch.setattr(sys, "argv", ["/opt/venv/bin/quackd", *args])
    return runner.invoke(app, args, input=answer, env={"COLUMNS": "200"})


def _terminal(tmp_path: Path) -> str:
    """The one terminal.txt the run left, read as it is on disk rather than as the runner
    saw it: the whole point of the file is that it is not the stream."""
    (path,) = list(tmp_path.rglob("terminal.txt"))
    return path.read_text(encoding="utf-8")


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    from quackd.agent.transcript import Transcript

    return list(Transcript.read(next(tmp_path.rglob("transcript.jsonl"))))


def _summary(tmp_path: Path) -> dict[str, Any]:
    return json.loads(next(tmp_path.rglob("summary.json")).read_text(encoding="utf-8"))


def _gated(tmp_path: Path) -> str:
    """A duck whose `quack` is behind a confirm gate, which no shipped duck has.

    The gates are the thing `--yes` answers, so a question on screen needs a duck that asks
    one, and every duck in `ducks/` is a duck somebody can run unattended."""
    path = tmp_path / "gated.duck"
    path.write_text(
        "---\nduck: 1\nname: gated\ndescription: d\nverbs:\n  allow: [quack, walk, stop]\n"
        "  confirm: [quack]\nsuccess: [x]\n---\n# Task\n\nQuack once, then stop.\n",
        encoding="utf-8",
    )
    return str(path)


def test_a_run_writes_down_the_terminal_it_showed_you(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transcript says what quackd did; this says what you saw while it did it.

    A bug report is a run directory, and the half of the evidence anybody actually looked at
    was the half that scrolled past. The file opens with the command and the version because
    the first two questions asked of any report are what was run and which quackd ran it,
    and it carries no escape codes because it is read in a browser, a diff and an issue.

    `FORCE_COLOR` so that the last claim is about the file rather than about the runner: the
    screen this run drew really is full of escape codes, and the capture is a console of its
    own that has to be the one thing in the process not writing them."""
    monkeypatch.setenv("QUACKD_LOG", "")  # the narration is the middle of the story
    monkeypatch.setenv("FORCE_COLOR", "1")
    result = _typed_run(tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output
    assert "\x1b" in result.output, "nothing is proved about the file if the screen is plain"
    text = _terminal(tmp_path)
    lines = text.splitlines()
    assert lines[0].startswith("$ quackd run hello-world --llm fake"), lines[0]
    assert lines[1].startswith(f"quackd {__version__}, started "), lines[1]
    assert re.search(r"started \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ,", lines[1]), lines[1]
    assert "microduck:mock" in text, "the header panel"
    assert "sound(tag='chirp'" in text, "a narrated line"
    assert "steps 3" in _counters(text), "the counters under the verdict"
    assert "\x1b" not in text, "a saved terminal is read anywhere but on a terminal"


def test_a_secret_on_the_command_line_is_hidden_everywhere_it_is_written_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run directory is pasted into issues and copied off a bench machine, so a key that
    reaches one is a key somebody has to rotate.

    Both spellings a shell allows, because a separate value and an `=` are hidden by
    different branches, and all three files, because the command is now written down three
    times: at the top of the saved terminal, in `run_start` and in the summary."""
    result = _typed_run(tmp_path, monkeypatch, "--api-key", "hunter2", "--token=abc")
    assert result.exit_code == 0, result.output
    text = _terminal(tmp_path)
    transcript = next(tmp_path.rglob("transcript.jsonl")).read_text(encoding="utf-8")
    written = {"terminal.txt": text, "transcript.jsonl": transcript}
    written["summary.json"] = json.dumps(_summary(tmp_path))
    for where, carrier in written.items():
        # the values as bare substrings: the claim is that the secret is nowhere in the
        # file, not that one spelling of one flag came out of the redactor redacted
        assert "***" in carrier, where
        assert "hunter2" not in carrier, where
        assert "abc" not in carrier, where
    assert "--api-key" in text and "--token=***" in text, "the flag shows; the value does not"
    assert _events(tmp_path)[0]["command"][-3:] == ["--api-key", "***", "--token=***"]


def test_the_record_says_what_was_asked_for_and_which_quackd_ran_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which robot, which model, which budget, whether it was a dry run: the flags are half
    the story of a run, and reading a transcript a month later meant guessing at them.

    The first word is `quackd` and not the path that was really invoked, which is an
    absolute path to a console script on one machine and `__main__.py` under `python -m`,
    and neither is what was typed or what a reader wants."""
    result = _typed_run(tmp_path, monkeypatch, "--seed", "7")
    assert result.exit_code == 0, result.output
    start = _events(tmp_path)[0]
    assert start["kind"] == "run_start"
    assert start["command"][:2] == ["quackd", "run"], start["command"]
    assert start["command"][-2:] == ["--seed", "7"]
    assert "/opt/venv/bin" not in " ".join(start["command"])
    assert start["version"] == __version__
    summary = _summary(tmp_path)
    assert summary["command"] == start["command"]
    assert summary["version"] == __version__


def test_a_run_refused_before_the_directory_leaves_no_terminal_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capture is opened around the whole command, so the one sentence a bad flag prints
    is in the file too. That is only safe because nothing is written until there is a run
    directory to write into: a run refused before it had one must not leave a directory
    behind just to say it was refused."""
    result = _typed_run(tmp_path, monkeypatch, "--run-name", "!!!")
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "--run-name '!!!' has no ASCII letters or digits in it" in flat, flat
    assert list(tmp_path.rglob("terminal.txt")) == []
    assert not (tmp_path / "runs").exists(), "the refusal came after the run directory"


def test_no_log_keeps_the_two_ends_of_the_file_and_drops_the_middle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switch is about the console, and the file is the console (ADR-0029). What it
    silences is the narration; what somebody who opens a saved terminal is looking for is
    almost always the header or the verdict, and neither was ever the log's to take."""
    result = _typed_run(tmp_path, monkeypatch, "--no-log")
    assert result.exit_code == 0, result.output
    text = _terminal(tmp_path)
    assert "microduck:mock" in text and "SUCCESS" in text
    assert "steps 3" in _counters(text)
    assert "sound(tag='chirp'" not in text and "system prompt" not in text


def test_a_question_and_the_answer_to_it_are_both_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither half reaches the file on its own: the question is written straight to stderr
    rather than through a console the capture can see, and the answer is echoed by the
    terminal driver, so a tee on quackd's own output gets the asking and not the answering.
    The file has both because `_ask` puts them there itself.

    The transcript gains the exchange beside the gate's note of what it decided, and quotes
    the question in the words it was asked in, which is why the two are compared rather than
    both asserted against a sentence written here.

    `_can_prompt` is forced because a `CliRunner` has no terminal under it, and the record
    refuses to name a person it cannot find one for. This is the run where somebody really is
    standing there; the run where nobody is is directly below."""
    monkeypatch.setattr(cli_mod, "_can_prompt", lambda: True)
    result = _typed_run(tmp_path, monkeypatch, duck=_gated(tmp_path), answer="y\n")
    assert result.exit_code == 0, result.output
    asked = [line for line in _terminal(tmp_path).splitlines() if "[y/N]" in line]
    assert len(asked) == 1, asked
    assert asked[0].startswith("run quack(") and asked[0].endswith("[y/N]: y"), asked[0]
    (prompt,) = [e for e in _events(tmp_path) if e["kind"] == "prompt"]
    assert prompt["what"] == "confirm" and prompt["answer"] is True
    assert prompt["question"] == asked[0].split(" [y/N]")[0]


def test_a_pipe_on_stdin_opens_the_gate_and_is_not_written_down_as_a_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`yes | quackd run` is how people drive a CLI that asks questions, out of a CI job or a
    cron wrapper, and `input()` reads a pipe exactly as happily as it reads a person.

    The gate opens, because that is what the pipe asked for and what quackd has always done,
    and the screen keeps the question and the answer because that is what was on it. What must
    not happen is the record then testifying that somebody cleared a verb on a robot nobody was
    standing next to. `_can_prompt` already knows whether there is a terminal; this is that
    answer reaching the two fields a reader would take as a witness."""
    monkeypatch.setattr(cli_mod, "_can_prompt", lambda: False)
    result = _typed_run(tmp_path, monkeypatch, duck=_gated(tmp_path), answer="y\n")
    assert result.exit_code == 0, result.output
    assert "[y/N]: y" in _terminal(tmp_path)
    events = _events(tmp_path)
    (gate,) = [e for e in events if e["kind"] == "gate" and e.get("gate") == "confirm"]
    assert gate["outcome"] == "allowed" and gate["answer"] is True
    assert "human" not in gate["reason"], gate["reason"]
    assert [e for e in events if e["kind"] == "prompt"] == []


def test_input_from_the_null_device_is_nobody_at_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam the two tests above replace, on the input that fooled it. Windows' NUL is a
    character device, so `isatty` says yes to it, and a script started with its input from NUL
    was asked every question, answered each with end-of-input, and had its record name a
    person; `--controller vla` went on to ask one instead of refusing. `/dev/null` is no
    terminal elsewhere either, and a pipe is none anywhere, so this holds on every OS."""
    with open(os.devnull, encoding="utf-8") as null:
        monkeypatch.setattr(sys, "stdin", null)
        assert cli_mod._can_prompt() is False
    read, write = os.pipe()
    os.close(write)
    with os.fdopen(read, encoding="utf-8") as pipe:
        monkeypatch.setattr(sys, "stdin", pipe)
        assert cli_mod._can_prompt() is False


def test_a_yes_run_leaves_no_claim_that_anybody_was_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--yes` answers the gate with a callable, and a record saying a question was put to
    somebody who was never at the terminal is worse than no record at all. The gate still
    ran and still says what it decided; what is missing is the exchange, because there
    wasn't one."""
    result = _typed_run(tmp_path, monkeypatch, "--yes", duck=_gated(tmp_path))
    assert result.exit_code == 0, result.output
    assert "[y/N]" not in _terminal(tmp_path)
    events = _events(tmp_path)
    assert [e for e in events if e["kind"] == "gate" and e.get("gate") == "confirm"]
    assert [e for e in events if e["kind"] == "prompt"] == []
