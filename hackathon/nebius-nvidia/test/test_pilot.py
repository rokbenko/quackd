"""The Nemotron pilot: quackd's own `run`, with its local provider pointed at Token Factory."""

from __future__ import annotations

from pathlib import Path

import pytest
from fake_tokenfactory import TEXT_MODEL, VISION_MODEL, FakeTokenFactory, row
from passport_nebius import nebius, pilot
from passport_nebius.cli import app
from typer.testing import CliRunner

runner = CliRunner()
SETTINGS = nebius.Settings("https://tokenfactory.example/v1", "the-key")


def _model(model_id: str, modality: str | None = "text->text") -> nebius.Model:
    found = nebius._model(row(model_id, modality=modality))
    assert found is not None
    return found


def test_the_pilot_is_quackds_local_provider_at_token_factory() -> None:
    launch = pilot.run_launch(SETTINGS, _model(TEXT_MODEL), ["task.duck", "--robot", "x:mock"])
    tail = launch.argv[len(pilot.QUACKD) :]
    assert tail == (
        "run",
        "task.duck",
        "--robot",
        "x:mock",
        "--llm",
        f"local:{TEXT_MODEL}",
        "--base-url",
        "https://tokenfactory.example/v1",
    )
    assert launch.env == {"LOCAL_API_KEY": "the-key"}


def test_the_key_is_never_on_a_command_line() -> None:
    launch = pilot.run_launch(SETTINGS, _model(TEXT_MODEL), ["task.duck"])
    assert not any("the-key" in arg for arg in launch.argv)
    assert "the-key" not in launch.shown()
    assert launch.shown().startswith("quackd run task.duck ")


@pytest.mark.parametrize(
    "given", [["--llm", "openai"], ["-l", "x"], ["--base-url=http://x"], ["--api-key", "k"]]
)
def test_what_this_edition_sets_cannot_be_set_twice(given: list[str]) -> None:
    with pytest.raises(nebius.NebiusError, match="set by passport-nebius"):
        pilot.run_launch(SETTINGS, _model(TEXT_MODEL), ["task.duck", *given])


def test_vision_only_for_a_model_that_reads_images() -> None:
    launch = pilot.run_launch(
        SETTINGS, _model(VISION_MODEL, "text+image->text"), ["d"], vision=True
    )
    assert launch.argv[-1] == "--vision"
    with pytest.raises(nebius.NebiusError, match="does not read images"):
        pilot.run_launch(SETTINGS, _model(TEXT_MODEL), ["d"], vision=True)
    # a list that says nothing is not a no: the run goes ahead and the command notes it
    assert pilot.run_launch(SETTINGS, _model("nvidia/x", None), ["d"], vision=True).argv[-1] == (
        "--vision"
    )


def test_a_run_is_unpriced_until_told(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pilot.unpriced(["d"])
    assert not pilot.unpriced(["d", "--price", "in=0.3,out=0.9"])
    assert not pilot.unpriced(["d", "--price=in=0.3,out=0.9"])
    monkeypatch.setenv("QUACKD_PRICE", "in=0.3,out=0.9")
    assert not pilot.unpriced(["d"])


def test_show_prints_quackds_command_without_the_key(tokenfactory: FakeTokenFactory) -> None:
    result = runner.invoke(app, ["pilot", "--model", TEXT_MODEL, "--show", "task.duck", "--talk"])
    assert result.exit_code == 0, result.output
    assert f"quackd run task.duck --talk --llm local:{TEXT_MODEL} --base-url" in result.output
    assert "stand-in-key" not in result.output


def test_an_id_spelled_otherwise_is_refused_with_the_lists_spelling(
    tokenfactory: FakeTokenFactory,
) -> None:
    result = runner.invoke(app, ["pilot", "--model", TEXT_MODEL.upper(), "--show", "d"])
    assert result.exit_code == 1
    assert f"the list spells it {TEXT_MODEL}" in result.output


def test_no_model_says_how_to_choose_one(tokenfactory: FakeTokenFactory) -> None:
    result = runner.invoke(app, ["pilot", "--show", "d"])
    assert result.exit_code == 1
    assert "NEBIUS_MODEL" in result.output


def test_a_real_quackd_run_is_piloted_through_token_factory(
    tokenfactory: FakeTokenFactory, tmp_path: Path
) -> None:
    """quackd itself, on a mock body: every request it sends carries the NVIDIA id, the key and
    the verbs as tools, and the run ends the way the stand-in's script ends it."""
    tokenfactory.script = [
        ("assess_task", {"verdict": "feasible", "reason": "reporting state moves nothing"}),
        ("report_state", {}),
        ("declare_success", {"reason": "reported my state"}),
    ]
    runs = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "pilot",
            "--model",
            TEXT_MODEL,
            "--goal",
            "Report your state once, then declare success.",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(runs),
            "--no-memory",
        ],
    )
    assert result.exit_code == 0, result.output
    posts = tokenfactory.posts()
    assert len(posts) == 3
    for post in posts:
        assert post.headers["authorization"] == "Bearer stand-in-key"
        assert post.body["model"] == TEXT_MODEL
        names = {t["function"]["name"] for t in post.body["tools"]}
        assert {"assess_task", "report_state", "declare_success"} <= names
    assert list(runs.glob("*/transcript.jsonl")), "the run wrote no transcript"


def test_a_quackd_in_the_folder_a_command_runs_from_is_never_imported(tmp_path: Path) -> None:
    """The repository root has a quackd/ of its own; the one run must be the installed one."""
    import subprocess

    planted = tmp_path / "quackd"
    planted.mkdir()
    (planted / "__init__.py").write_text("raise SystemExit('the planted quackd ran')\n")
    shown = subprocess.run(
        [*pilot.QUACKD[:2], "-c", "import quackd; print(quackd.__file__)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert str(tmp_path) not in shown.stdout
    assert "site-packages" in shown.stdout


def test_neither_a_repr_nor_a_childs_environment_carries_the_nebius_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch = pilot.run_launch(SETTINGS, _model(TEXT_MODEL), ["task.duck"])
    assert "the-key" not in repr(launch)
    monkeypatch.setenv("NEBIUS_API_KEY", "the-key")
    env = pilot.child_env(launch.env)
    assert "NEBIUS_API_KEY" not in env
    assert env["LOCAL_API_KEY"] == "the-key", "quackd's local provider reads it here, and only here"


def test_a_run_gets_its_own_pilots_key_and_neither_key_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command that plays both robots holds both keys: each run is handed only the one its
    pilot's server takes, so the duck's quackd cannot send the onboard key anywhere, nor the
    postman's the Token Factory key."""
    monkeypatch.setenv("NEBIUS_API_KEY", "token-factory-key")
    monkeypatch.setenv("ONBOARD_API_KEY", "onboard-key")
    monkeypatch.setenv("LOCAL_API_KEY", "a-key-left-in-the-shell")
    own = nebius.Settings.onboard("http://127.0.0.1:8000/v1")
    env = pilot.child_env(pilot.run_launch(own, _model(TEXT_MODEL), ["task.duck"]).env)
    assert "NEBIUS_API_KEY" not in env and "ONBOARD_API_KEY" not in env
    assert env["LOCAL_API_KEY"] == "onboard-key"
    monkeypatch.delenv("ONBOARD_API_KEY")
    keyless = nebius.Settings.onboard("http://127.0.0.1:8000/v1")
    env = pilot.child_env(pilot.run_launch(keyless, _model(TEXT_MODEL), ["task.duck"]).env)
    assert env["LOCAL_API_KEY"] == nebius.NO_KEY, "never a key some other variable left behind"
