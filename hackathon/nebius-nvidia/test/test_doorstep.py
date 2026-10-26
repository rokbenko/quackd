"""The doorstep, set up and played with quackd's own commands and runs, against the stand-in.

The end-to-end tests are the scene's claims, proved with quackd 0.23 as released and both robots
running: in the stranger act the postman's words never reach the duck's model, and the line of the
duck's passport that says what its beak cannot do never reaches the postman's; in the appointment
act, once each side pinned the other, both do.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fake_tokenfactory import (
    ONBOARD_KEY,
    ONBOARD_MODEL,
    TEXT_MODEL,
    FakeTokenFactory,
    row,
    served,
    tool_completion,
)
from passport_nebius import doorstep, nebius, pilot
from passport_nebius.cli import app
from typer.testing import CliRunner

from quackd.lan.announce import local_ip
from quackd.passport_store import PassportStore
from quackd.passport_talk import HEADING

SETTINGS = nebius.Settings("https://tokenfactory.example/v1", "the-key")
runner = CliRunner()
#: The line of Microduck's passport that decides how a letter can reach it.
BEAK_LINE = "the beak scoops at the floor and nothing else"


def _text_model() -> nebius.Model:
    found = nebius._model(row(TEXT_MODEL))
    assert found is not None
    return found


MODEL = _text_model()
BRAIN = doorstep.Brain(SETTINGS, MODEL)


def _pins(state: Path, robot: doorstep.Robot) -> list[str]:
    return [p.name for p in PassportStore(str(doorstep.registry(state, robot))).known()]


def test_setup_pins_for_an_appointment_and_unpins_for_strangers(tmp_path: Path) -> None:
    state = tmp_path / "doorstep"
    first = doorstep.setup(state, "appointment")
    assert "registered postman-01 as agile_one_sn:mock" in first
    assert "registered duck-01 as microduck:sim2d" in first
    assert "duck-01 pinned postman-01" in first and "postman-01 pinned duck-01" in first
    assert _pins(state, doorstep.DUCK) == ["postman-01"]
    assert _pins(state, doorstep.POSTMAN) == ["duck-01"]
    assert doorstep.passport_file(state, doorstep.POSTMAN).read_text().count(".") == 2
    assert doorstep.setup(state, "appointment") == []
    assert sorted(doorstep.setup(state, "stranger")) == [
        "duck-01 forgot postman-01",
        "postman-01 forgot duck-01",
    ]
    assert _pins(state, doorstep.DUCK) == [] and _pins(state, doorstep.POSTMAN) == []
    assert doorstep.is_set_up(state)


def test_the_passports_say_who_each_robot_is_and_what_it_can_take(tmp_path: Path) -> None:
    from quackd.passport import document

    state = tmp_path / "doorstep"
    doorstep.setup(state, "stranger")
    views = {}
    for robot in (doorstep.POSTMAN, doorstep.DUCK):
        reading = document.read(doorstep.passport_file(state, robot).read_bytes())
        assert isinstance(reading, document.Reading) and reading.signed
        view = reading.view()
        assert view["name"] == robot.name
        assert view["owner"]["label"] == robot.owner
        assert view["safety"]["how_to_stop"] == robot.how_to_stop
        views[robot.name] = view
    postman, duck = views["postman-01"], views["duck-01"]
    assert (postman["maker"]["vendor"], postman["body"]["embodiment"]) == (
        "agile-robots",
        "humanoid",
    )
    assert postman["body"]["payload_kg"]["value"] == 3.0
    assert {"hold_out_letter", "lay_letter_down"} <= set(postman["skills"])
    assert (duck["body"]["manipulator"], duck["body"]["height_m"]["value"]) == ("beak", 0.25)
    assert any(BEAK_LINE in line for line in duck["cannot"])
    assert "grab" in duck["skills"]


def test_the_postman_starts_first_and_the_duck_meets_it(tmp_path: Path) -> None:
    started = doorstep.launches(
        tmp_path, "appointment", "both", postman=BRAIN, duck=BRAIN, postman_at="10.0.0.5:8790"
    )
    (postman, p), (duck, d) = started
    assert (postman, duck) == (doorstep.POSTMAN, doorstep.DUCK)
    assert "--meet" not in p.argv
    assert p.argv[p.argv.index("--talk-port") + 1] == "8790"
    assert d.argv[d.argv.index("--meet") + 1] == "10.0.0.5:8790"
    assert d.argv[d.argv.index("--talk-port") + 1] == "8791"
    assert p.argv[p.argv.index("--registry-dir") + 1] != d.argv[d.argv.index("--registry-dir") + 1]
    assert [
        robot
        for robot, _ in doorstep.launches(
            tmp_path, "stranger", "duck", postman=None, duck=BRAIN, postman_at="x:1"
        )
    ] == [doorstep.DUCK]
    with pytest.raises(nebius.NebiusError, match="--role"):
        doorstep.launches(
            tmp_path, "stranger", "neither", postman=BRAIN, duck=BRAIN, postman_at="x:1"
        )
    with pytest.raises(nebius.NebiusError, match="--act"):
        doorstep.launches(
            tmp_path, "rehearsal", "both", postman=BRAIN, duck=BRAIN, postman_at="x:1"
        )


def test_each_robot_flies_on_its_own_brain(tmp_path: Path) -> None:
    """The postman thinking onboard: its run names its own server, its model and its key, and
    the duck's run names Token Factory's, so neither run can reach the other's server."""
    own = nebius.Settings("http://127.0.0.1:8000/v1", "own-key", name="the onboard server")
    found = nebius._model(served(ONBOARD_MODEL))
    assert found is not None
    onboard = doorstep.Brain(own, found)
    (_, p), (_, d) = doorstep.launches(
        tmp_path, "appointment", "both", postman=onboard, duck=BRAIN, postman_at="x:1"
    )
    assert p.argv[-4:] == ("--llm", f"local:{ONBOARD_MODEL}", "--base-url", own.base_url)
    assert d.argv[-4:] == ("--llm", f"local:{TEXT_MODEL}", "--base-url", SETTINGS.base_url)
    assert (p.env, d.env) == ({"LOCAL_API_KEY": "own-key"}, {"LOCAL_API_KEY": "the-key"})
    alone = doorstep.launches(
        tmp_path, "appointment", "postman", postman=onboard, duck=None, postman_at="x:1"
    )
    assert [robot for robot, _ in alone] == [doorstep.POSTMAN], "a postman alone needs no duck"
    with pytest.raises(nebius.NebiusError, match="duck-01 has no pilot"):
        doorstep.launches(
            tmp_path, "appointment", "both", postman=onboard, duck=None, postman_at="x:1"
        )


def test_the_onboard_brain_is_the_one_model_its_server_lists(onboard: FakeTokenFactory) -> None:
    brain = doorstep.onboard(onboard.base_url)
    assert (brain.model.id, brain.settings.api_key) == (ONBOARD_MODEL, ONBOARD_KEY)
    assert brain.settings.name == "the onboard server"
    assert doorstep.onboard(onboard.base_url, ONBOARD_MODEL).model.id == ONBOARD_MODEL


@pytest.mark.parametrize("robot", [doorstep.POSTMAN, doorstep.DUCK], ids=lambda r: r.name)
def test_each_task_is_valid_for_its_body(robot: doorstep.Robot) -> None:
    result = doorstep.quackd("validate", str(doorstep.scenario(robot)), "--robot", robot.spec)
    assert result.returncode == 0, result.stdout + result.stderr


# The end-to-end acts. The stand-in plays both pilots, telling them apart by their tasks: the
# postman waits for a robot under Robots nearby, then tells it one phrase; the duck waits a while
# for that phrase. What reaches each model is read off the requests themselves.

PHRASE = "A-LETTER-FOR-THIS-HOME"
POSTMAN_TASK = "You are a postman robot at the front door"
DUCK_TASK = "You are this home's robot, a small duck"


def _ports_free() -> bool:
    for port in (doorstep.POSTMAN.port, doorstep.DUCK.port):
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", port))
            except OSError:
                return False
    return True


def _system(body: dict[str, Any]) -> str:
    return " ".join(
        m["content"]
        for m in body.get("messages", [])
        if m.get("role") == "system" and isinstance(m.get("content"), str)
    )


def _latest(body: dict[str, Any]) -> str:
    for message in reversed(body.get("messages", [])):
        if message.get("role") in ("tool", "user"):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


class TwoPilots:
    """Both pilots' scripts, one turn per request, each robot's own count."""

    def __init__(self, *, linger: int = 0) -> None:
        self.turns = {"postman": 0, "duck": 0}
        self.told: str | None = None
        self.duck_waits = 0
        self.linger = linger
        """Turns the duck goes on for once it heard the phrase, so a pause can land."""
        self.lingered = 0
        self.lock = threading.Lock()

    def __call__(self, n: int, body: dict[str, Any]) -> tuple[int, Any]:
        system = _system(body)
        who = "postman" if POSTMAN_TASK in system else "duck" if DUCK_TASK in system else None
        if who is None:
            return 500, {"detail": "the stand-in cannot tell which robot this is"}
        with self.lock:
            self.turns[who] += 1
            turn = self.turns[who]
        model = str(body.get("model"))
        if turn == 1:
            verdict = {"verdict": "feasible", "reason": "a letter weighs next to nothing"}
            return 200, tool_completion(n, "assess_task", verdict, model)
        seen = _latest(body)
        if who == "postman":
            if self.told is not None:
                return 200, tool_completion(n, "declare_success", {"reason": "told it"}, model)
            if HEADING in seen:
                name = re.search(r'"name": "([^"]+)"', seen.split(HEADING, 1)[1])
                if name:
                    self.told = name.group(1)
                    text = f"{PHRASE}: I will lay it on the floor in front of you."
                    return 200, tool_completion(n, "tell", {"to": self.told, "text": text}, model)
            time.sleep(1.5)
            return 200, tool_completion(n, "report_state", {}, model)
        if PHRASE in seen or self.lingered:
            if self.lingered >= self.linger:
                return 200, tool_completion(n, "declare_success", {"reason": "heard it"}, model)
            self.lingered += 1
            time.sleep(1.0)
            return 200, tool_completion(n, "report_state", {}, model)
        if HEADING in seen:
            self.duck_waits += 1
            if self.duck_waits > 8:
                done = {"reason": "a robot is here and nothing it said reached me"}
                return 200, tool_completion(n, "declare_success", done, model)
        time.sleep(1.0)
        return 200, tool_completion(n, "report_state", {}, model)


def _play(
    state: Path, act: str, *, brake: bool = False, onboard: bool = False
) -> tuple[FakeTokenFactory, FakeTokenFactory | None, TwoPilots, dict[str, int], list[str]]:
    """One act, both robots, the stand-in playing both pilots. With `onboard`, a second
    stand-in is the postman's own model server, its key the one $ONBOARD_API_KEY holds, and
    the first is left with the duck's pilot alone. With `brake`, the postman asks the duck for a
    pause a few seconds into its run, as `doorstep pause` does."""
    pilots = TwoPilots(linger=8 if brake else 0)
    braked: list[str] = []
    threads: list[threading.Thread] = []

    def ask_for_pause() -> None:
        duck_at = f"{local_ip()}:{doorstep.DUCK.port}"
        doorstep.served(duck_at, within=60)  # as run --pause-after does: never before it listens
        time.sleep(2.0)
        result = doorstep.pause(state, duck_at, 3)
        braked.append(doorstep.outcome(result, marker=" asked "))

    def duck_started() -> None:
        if brake:
            thread = threading.Thread(target=ask_for_pause, daemon=True)
            thread.start()
            threads.append(thread)

    with contextlib.ExitStack() as servers:
        stand_in = servers.enter_context(FakeTokenFactory(chat=pilots))
        own = None
        if onboard:
            own = servers.enter_context(
                FakeTokenFactory(key=ONBOARD_KEY, models=[served(ONBOARD_MODEL)], chat=pilots)
            )
        duck = doorstep.Brain(nebius.Settings(stand_in.base_url, "stand-in-key"), MODEL)
        postman = doorstep.onboard(own.base_url) if own is not None else duck
        doorstep.setup(state, act)
        postman_at = f"{local_ip()}:{doorstep.POSTMAN.port}"
        started = doorstep.launches(
            state, act, "both", postman=postman, duck=duck, postman_at=postman_at
        )
        codes = doorstep.play(
            started, postman_at=postman_at, logs=state / "logs", on_duck_started=duck_started
        )
        for thread in threads:
            thread.join(timeout=60)
    return stand_in, own, pilots, codes, braked


def _saw(stand_in: FakeTokenFactory, task: str, text: str) -> bool:
    """Whether `text` was ever in what the model piloting the robot with `task` was sent."""
    return any(
        task in _system(p.body) and text in json.dumps(p.body["messages"], ensure_ascii=False)
        for p in stand_in.posts()
    )


def _everything(stand_in: FakeTokenFactory) -> str:
    """Every header and body a stand-in was sent, as one text to search."""
    return json.dumps([[r.headers, r.body] for r in stand_in.requests], ensure_ascii=False)


#: How quackd tells a pilot that a stranger spoke and its words were kept from it.
WITHHELD = "a stranger's words go to the log, for a person"


needs_ports = pytest.mark.skipif(not _ports_free(), reason="ports 8790 and 8791 are in use")


@needs_ports
def test_appointment_words_and_the_beaks_limit_reach_each_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Played as the job plays it: the postman thinking onboard, and both keys in this
    process's environment, since it starts both robots."""
    monkeypatch.setenv("NEBIUS_API_KEY", "stand-in-key")
    monkeypatch.setenv("ONBOARD_API_KEY", ONBOARD_KEY)
    stand_in, own, pilots, codes, braked = _play(
        tmp_path / "doorstep", "appointment", brake=True, onboard=True
    )
    assert own is not None
    logs = tmp_path / "doorstep" / "logs"
    assert codes == {"postman-01": 0, "duck-01": 0}, [
        f.read_text(encoding="utf-8", errors="replace")[-2000:] for f in logs.glob("*.log")
    ]
    assert pilots.told == "duck-01", "a pinned robot is shown by its own name"
    assert _saw(stand_in, DUCK_TASK, "postman-01"), "and the postman too, to the duck"
    assert _saw(stand_in, DUCK_TASK, PHRASE), "the postman's words reached the duck's model"
    # what the postman decides by: the duck's passport, its limit included, in its own model's view
    assert _saw(own, POSTMAN_TASK, BEAK_LINE)
    # each robot's turns reach its own server alone, with its own model and its own key
    assert own.posts() and all(POSTMAN_TASK in _system(p.body) for p in own.posts())
    assert stand_in.posts() and all(DUCK_TASK in _system(p.body) for p in stand_in.posts())
    assert {p.body["model"] for p in own.posts()} == {ONBOARD_MODEL}
    assert {p.body["model"] for p in stand_in.posts()} == {TEXT_MODEL}
    assert "stand-in-key" not in _everything(own), "the Token Factory key reached the postman's"
    assert ONBOARD_KEY not in _everything(stand_in), "the onboard key reached Token Factory"
    # the brake: a pinned postman's pause is granted, and the duck holds still for it
    assert len(braked) == 1 and ": ok, " in braked[0], braked
    assert "holding still for 3 s" in braked[0]


@needs_ports
def test_strangers_neither_words_nor_the_beaks_limit_reach_a_model(tmp_path: Path) -> None:
    stand_in, _own, pilots, codes, _braked = _play(tmp_path / "doorstep", "stranger")
    assert codes == {"postman-01": 0, "duck-01": 0}
    assert pilots.told is not None and pilots.told.startswith("stranger-")
    assert not _saw(stand_in, DUCK_TASK, PHRASE)
    assert not _saw(stand_in, DUCK_TASK, "postman-01"), "a name a stranger chose is its own words"
    # it was said and it arrived: quackd kept it from the model and told the model so
    assert _saw(stand_in, DUCK_TASK, WITHHELD), "the postman's words never arrived at all"
    assert pilots.duck_waits > 8, "the duck saw the postman and waited, hearing nothing"
    # a stranger's passport reaches the postman in closed words only: the beak, never its limit
    assert _saw(stand_in, POSTMAN_TASK, "beak")
    assert not _saw(stand_in, POSTMAN_TASK, BEAK_LINE)


def _done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["quackd"], code, out, err)


def test_outcome_reads_quackds_own_words_not_its_frames_or_hints() -> None:
    failed = _done(1, err="✗ error: refused under 5.7: no talk channel\n  docs/passport/talk.md\n")
    assert doorstep.outcome(failed) == "refused under 5.7: no talk channel"
    # a usage error, in the rounded panel Typer draws on Linux and macOS
    panel = _done(
        2,
        err="Usage: quackd passport set [OPTIONS]\nTry 'quackd passport set -h' for help.\n"
        "╭─ Error ───╮\n│ Missing option '--robot' / '-r'. │\n"
        "╰──────╯\n",
    )
    assert doorstep.outcome(panel) == "Missing option '--robot' / '-r'."
    asked = _done(
        0,
        out="hello: duck-01 proved it holds its key, and so did postman-01\n"
        "postman-01 asked duck-01 for a pause of 5 s: ok, stopped, and holding still for 5 s\n"
        "bye: postman-01 ended the session\n",
    )
    assert doorstep.outcome(asked, marker=" asked ").endswith("holding still for 5 s")


def test_a_robot_made_again_is_pinned_by_its_new_key(tmp_path: Path) -> None:
    state = tmp_path / "doorstep"
    doorstep.setup(state, "appointment")
    old = doorstep.passport_id(state, doorstep.POSTMAN)
    shutil.rmtree(doorstep.registry(state, doorstep.POSTMAN))
    done = doorstep.setup(state, "appointment")
    new = doorstep.passport_id(state, doorstep.POSTMAN)
    assert new != old
    assert "duck-01 forgot an old key of postman-01" in done
    assert "duck-01 pinned postman-01" in done
    duck = PassportStore(str(doorstep.registry(state, doorstep.DUCK)))
    assert [p.id for p in duck.known()] == [new]


def test_a_postman_whose_passport_never_answers_is_not_left_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doorstep, "SERVE_WITHIN_S", 1.0)
    started: list[subprocess.Popen[bytes]] = []
    real_start = doorstep.start

    def recording(launch: pilot.Launch, **popen: Any) -> subprocess.Popen[bytes]:
        process = real_start(launch, **popen)
        started.append(process)
        return process

    monkeypatch.setattr(doorstep, "start", recording)
    sleeper = pilot.Launch((sys.executable, "-c", "import time; time.sleep(120)"), {})
    with pytest.raises(nebius.NebiusError, match="no passport answered"):
        doorstep.play(
            [(doorstep.POSTMAN, sleeper), (doorstep.DUCK, sleeper)],
            postman_at="127.0.0.1:9",
            logs=tmp_path / "logs",
        )
    assert len(started) == 1, "the duck never started"
    assert started[0].poll() is not None, "the postman's run was left running"


def test_run_show_prints_both_commands_whole(tokenfactory: FakeTokenFactory) -> None:
    result = runner.invoke(
        app,
        ["doorstep", "run", "--show", "--model", TEXT_MODEL, "--postman-at", "10.0.0.5:8790"],
    )
    assert result.exit_code == 0, result.output
    postman, duck = result.output.splitlines()
    assert postman.startswith("postman-01: quackd run ") and "--talk-port 8790" in postman
    assert duck.startswith("duck-01: quackd run ") and "--meet 10.0.0.5:8790" in duck
    assert postman.endswith(tokenfactory.base_url), "a command printed whole, never wrapped"
    assert "stand-in-key" not in result.output


def test_pause_after_needs_the_duck_here(tokenfactory: FakeTokenFactory) -> None:
    args = ["doorstep", "run", "--role", "postman", "--pause-after", "5", "--model", TEXT_MODEL]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "--pause-after pauses the duck" in result.output


def test_run_show_puts_the_postman_on_its_own_server(
    tokenfactory: FakeTokenFactory, onboard: FakeTokenFactory
) -> None:
    args = ["doorstep", "run", "--show", "--model", TEXT_MODEL, "--postman-at", "10.0.0.5:8790"]
    result = runner.invoke(app, [*args, "--onboard", onboard.base_url])
    assert result.exit_code == 0, result.output
    postman, duck = result.output.splitlines()
    assert postman.endswith(f"--llm local:{ONBOARD_MODEL} --base-url {onboard.base_url}")
    assert duck.endswith(f"--llm local:{TEXT_MODEL} --base-url {tokenfactory.base_url}")
    assert {r.headers["authorization"] for r in onboard.requests} == {f"Bearer {ONBOARD_KEY}"}
    assert {r.headers["authorization"] for r in tokenfactory.requests} == {"Bearer stand-in-key"}
    assert "stand-in-key" not in result.output and ONBOARD_KEY not in result.output


def test_an_onboard_postman_alone_needs_no_token_factory_key(onboard: FakeTokenFactory) -> None:
    args = ["doorstep", "run", "--show", "--role", "postman", "--onboard", onboard.base_url]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    (postman,) = result.output.splitlines()
    assert postman.startswith("postman-01: quackd run ") and onboard.base_url in postman


@pytest.mark.parametrize(
    ("given", "said"),
    [
        (["--onboard-model", ONBOARD_MODEL], "--onboard-model names a model on the --onboard"),
        (["--role", "duck", "--onboard", "http://127.0.0.1:9/v1"], "the postman's own server"),
        (["--onboard", "http://127.0.0.1:9/v1", "--vision"], "an onboard server's list never"),
        (["--onboard", nebius.DEFAULT_BASE_URL + "/"], "names Token Factory's own address"),
        (["--onboard", "127.0.0.1:8000"], "is not an http or https URL"),
    ],
)
def test_onboard_is_refused_before_anything_is_asked(given: list[str], said: str) -> None:
    """No key is set here, so a refusal that came after the first request would say so."""
    result = runner.invoke(app, ["doorstep", "run", "--show", *given])
    assert result.exit_code == 1
    assert said in result.output


def test_each_pilot_is_named_with_the_server_it_thinks_on(
    tokenfactory: FakeTokenFactory, onboard: FakeTokenFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doorstep, "setup", lambda state, act: [])
    monkeypatch.setattr(
        doorstep, "play", lambda started, **given: {robot.name: 0 for robot, _ in started}
    )
    both = runner.invoke(
        app, ["doorstep", "run", "--model", TEXT_MODEL, "--onboard", onboard.base_url]
    )
    assert both.exit_code == 0, both.output
    assert (
        f"postman-01's pilot: {ONBOARD_MODEL} on the onboard server at {onboard.base_url}"
        in both.output
    )
    assert f"duck-01's pilot: {TEXT_MODEL} on Token Factory at {tokenfactory.base_url}" in (
        both.output
    )
    assert "records this run at $0" in both.output, "the duck's turns on Token Factory are unpriced"
    alone = runner.invoke(
        app, ["doorstep", "run", "--role", "postman", "--onboard", onboard.base_url]
    )
    assert alone.exit_code == 0, alone.output
    assert "records this run at $0" not in alone.output, "an onboard model costs its GPU's hours"
