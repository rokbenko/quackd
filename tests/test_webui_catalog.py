"""What the page's pickers offer, read from quackd's own catalogues, and the probe that checks a
robot from a child process (`quackd.webui.catalog`, `quackd.webui.probe`). No Streamlit."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quackd.agent.providers.catalogue import PROVIDER_NAMES, default_model_for
from quackd.doctor import EXTRAS
from quackd.webui import catalog


def test_the_robots_are_list_adapters_own_rows() -> None:
    from quackd.adapters.factory import list_adapters

    assert catalog.adapters() == list_adapters()
    microduck = next(row for row in catalog.adapters() if row["name"] == "microduck")
    assert "sim2d" in microduck["backends"]
    assert catalog.is_simulated("sim2d") and catalog.is_simulated("mock")
    assert not catalog.is_simulated("jsonrpc")


def test_the_pilots_are_the_catalogues_own() -> None:
    assert catalog.vendors() == tuple(PROVIDER_NAMES)
    assert catalog.default_model("anthropic") == default_model_for("anthropic")
    assert catalog.models("anthropic")[0].id == default_model_for("anthropic")
    assert catalog.models("ollama") == [], "a local server serves what it was started with"
    assert "ollama" in catalog.local_presets()
    assert catalog.decision_llms() and catalog.policy_families()


def test_a_key_comes_from_the_page_then_the_shell_then_dotenv_and_is_only_ever_masked() -> None:
    """In the order a run the page starts finds it: a key typed into the page is put in the
    run's environment over the shell's, and a run loads `.env` without replacing either."""
    env, dot, session = {"ANTHROPIC_API_KEY": ""}, {"ANTHROPIC_API_KEY": "sk-ant-from-dotenv"}, {}
    key = catalog.key_for("ANTHROPIC_API_KEY", environment=env, dotenv_values=dot, session=session)
    assert (key.source, key.shown) == (".env", "sk-a…nv")
    shell = {"ANTHROPIC_API_KEY": "sk-ant-from-the-shell"}
    key = catalog.key_for("ANTHROPIC_API_KEY", environment=shell, dotenv_values=dot, session={})
    assert key.source == "environment", "a run reads its environment before its .env"
    typed = {"ANTHROPIC_API_KEY": "sk-ant-typed-in-the-page"}
    key = catalog.key_for("ANTHROPIC_API_KEY", environment={}, dotenv_values={}, session=typed)
    assert key.source == "session" and "typed" not in key.shown
    key = catalog.key_for("ANTHROPIC_API_KEY", environment=shell, dotenv_values=dot, session=typed)
    assert key.source == "session", "the key typed into the page is the one the run is given"
    none = catalog.key_for("ANTHROPIC_API_KEY", environment={}, dotenv_values={}, session={})
    assert (none.source, none.shown) == (None, "")


def test_a_vendors_second_name_for_its_key_counts() -> None:
    env, fallback = catalog.vendor_key("gemini") or ("", None)
    assert (env, fallback) == ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    key = catalog.key_for(
        env,
        environment={"GOOGLE_API_KEY": "AIza-long-enough"},
        dotenv_values={},
        session={},
        fallback=fallback,
    )
    assert (key.source, key.fallback) == ("environment", "GOOGLE_API_KEY")
    assert catalog.vendor_key("fake") is None, "the scripted pilot needs no key"


def test_dotenv_is_read_and_never_loaded_into_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A value the page loaded would reach every run it starts looking as if the shell had set
    it, past the checks that keep some settings out of a project's `.env`."""
    monkeypatch.delenv("QUACKD_UI_TEST_VALUE", raising=False)
    (tmp_path / ".env").write_text("QUACKD_UI_TEST_VALUE=from-the-file\n", encoding="utf-8")
    assert catalog.dotenv(tmp_path)["QUACKD_UI_TEST_VALUE"] == "from-the-file"
    assert "QUACKD_UI_TEST_VALUE" not in os.environ


def test_an_extra_is_found_without_importing_it() -> None:
    rows = catalog.extras()
    assert [label for label, _install, _here in rows] == list(EXTRAS)
    assert catalog.installed("json") and not catalog.installed("no_such_module_anywhere")
    before = set(sys.modules)  # lerobot is read from its metadata: `installed` never imports it
    catalog.installed("lerobot")
    assert "lerobot" not in set(sys.modules) - before


def test_only_the_keys_typed_with_a_value_reach_a_run() -> None:
    keys = {"ANTHROPIC_API_KEY": " sk-1 ", "OPENAI_API_KEY": "", "XAI_API_KEY": "x"}
    assert catalog.session_environment(keys) == {"ANTHROPIC_API_KEY": "sk-1", "XAI_API_KEY": "x"}
    assert catalog.session_environment(keys, ["XAI_API_KEY"]) == {"XAI_API_KEY": "x"}


def _probe(tmp_path: Path, request: object) -> dict[str, object]:
    done = subprocess.run(
        [sys.executable, "-P", "-m", "quackd.webui.probe", json.dumps(request)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_probe_checks_a_robot_in_a_child_and_answers_in_one_line(tmp_path: Path) -> None:
    mock = _probe(tmp_path, {"spec": "microduck:mock"})
    assert mock["reachable"] is not False, mock
    broken = _probe(tmp_path, {"spec": "no-such-adapter:x"})
    assert broken["reachable"] is False and broken["detail"]
    nonsense = _probe(tmp_path, ["not", "an", "object"])
    assert nonsense == {
        "reachable": False,
        "detail": "the request is not a JSON object",
        "elapsed_s": 0.0,
    }


def test_a_registered_name_and_its_spec_at_the_same_address_are_one_robot(tmp_path: Path) -> None:
    """The page holds one job per body, whichever way the body is named."""
    from quackd.registry import Registry, RobotEntry

    Registry(tmp_path).add_robot(RobotEntry(name="arm-01", spec="lerobot:real", address="COM5"))
    named = catalog.robot_identity("arm-01", None, registry_dir=str(tmp_path))
    spelled = catalog.robot_identity("lerobot:real", "com5", registry_dir=str(tmp_path))
    assert named == spelled == "lerobot:real@com5"
    other = catalog.robot_identity("lerobot:real", "COM6", registry_dir=str(tmp_path))
    assert other != named
    assert catalog.robot_identity("microduck:sim2d", None, registry_dir=str(tmp_path)) == (
        "microduck:sim2d"
    )
    assert catalog.robot_identity(None) is None
