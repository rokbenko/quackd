"""The doorstep, set up and played with quackd's own commands and runs, against the stand-in.

The end-to-end tests are the scene's one claim, proved with quackd 0.23 as released: in the
stranger act a courier's words never reach the home robot's model, and in the appointment act,
once each side pinned the other, they do.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fake_tokenfactory import TEXT_MODEL, FakeTokenFactory, row, tool_completion
from passport_nebius import doorstep, nebius

from quackd.lan.announce import local_ip
from quackd.passport_store import PassportStore
from quackd.passport_talk import HEADING

SETTINGS = nebius.Settings("https://tokenfactory.example/v1", "the-key")


def _text_model() -> nebius.Model:
    found = nebius._model(row(TEXT_MODEL))
    assert found is not None
    return found


MODEL = _text_model()


def _pins(state: Path, robot: doorstep.Robot) -> list[str]:
    return [p.name for p in PassportStore(str(doorstep.registry(state, robot))).known()]


def test_setup_pins_for_an_appointment_and_unpins_for_strangers(tmp_path: Path) -> None:
    state = tmp_path / "doorstep"
    first = doorstep.setup(state, "appointment")
    assert "registered courier-01 as alohamini:sim2d" in first
    assert "registered home-01 as toddlerbot:sim2d" in first
    assert "home-01 pinned courier-01" in first and "courier-01 pinned home-01" in first
    assert _pins(state, doorstep.HOME) == ["courier-01"]
    assert _pins(state, doorstep.COURIER) == ["home-01"]
    assert doorstep.passport_file(state, doorstep.COURIER).read_text().count(".") == 2
    assert doorstep.setup(state, "appointment") == []
    assert sorted(doorstep.setup(state, "stranger")) == [
        "courier-01 forgot home-01",
        "home-01 forgot courier-01",
    ]
    assert _pins(state, doorstep.HOME) == [] and _pins(state, doorstep.COURIER) == []
    assert doorstep.is_set_up(state)


def test_the_passports_say_who_owns_each_robot_and_how_to_stop_it(tmp_path: Path) -> None:
    from quackd.passport import document

    state = tmp_path / "doorstep"
    doorstep.setup(state, "stranger")
    for robot in (doorstep.COURIER, doorstep.HOME):
        reading = document.read(doorstep.passport_file(state, robot).read_bytes())
        assert isinstance(reading, document.Reading) and reading.signed
        view = reading.view()
        assert view["name"] == robot.name
        assert view["owner"]["label"] == robot.owner
        assert view["safety"]["how_to_stop"] == robot.how_to_stop
        assert "payload_kg" in view["body"]


def test_the_courier_starts_first_and_the_home_robot_meets_it(tmp_path: Path) -> None:
    started = doorstep.launches(
        SETTINGS, MODEL, tmp_path, "appointment", "both", courier_at="10.0.0.5:8790"
    )
    (courier, c), (home, h) = started
    assert (courier, home) == (doorstep.COURIER, doorstep.HOME)
    assert "--meet" not in c.argv
    assert c.argv[c.argv.index("--talk-port") + 1] == "8790"
    assert h.argv[h.argv.index("--meet") + 1] == "10.0.0.5:8790"
    assert h.argv[h.argv.index("--talk-port") + 1] == "8791"
    assert c.argv[c.argv.index("--registry-dir") + 1] != h.argv[h.argv.index("--registry-dir") + 1]
    assert [
        robot
        for robot, _ in doorstep.launches(
            SETTINGS, MODEL, tmp_path, "stranger", "home", courier_at="x:1"
        )
    ] == [doorstep.HOME]
    with pytest.raises(nebius.NebiusError, match="--role"):
        doorstep.launches(SETTINGS, MODEL, tmp_path, "stranger", "neither", courier_at="x:1")
    with pytest.raises(nebius.NebiusError, match="--act"):
        doorstep.launches(SETTINGS, MODEL, tmp_path, "rehearsal", "both", courier_at="x:1")


@pytest.mark.parametrize("robot", [doorstep.COURIER, doorstep.HOME], ids=lambda r: r.name)
def test_each_task_is_valid_for_its_body(robot: doorstep.Robot) -> None:
    result = doorstep.quackd("validate", str(doorstep.scenario(robot)), "--robot", robot.spec)
    assert result.returncode == 0, result.stdout + result.stderr


# The end-to-end acts. The stand-in plays both pilots, telling them apart by their tasks: the
# courier waits for a robot under Robots nearby, then tells it one phrase; the home robot waits
# a while for that phrase. What reaches each model is read off the requests themselves.

PHRASE = "PARCEL-A-IS-0.8-KG"
COURIER_TASK = "delivery robot at the front door"
HOME_TASK = "You are this home's robot"


def _ports_free() -> bool:
    for port in (doorstep.COURIER.port, doorstep.HOME.port):
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

    def __init__(self) -> None:
        self.turns = {"courier": 0, "home": 0}
        self.told: str | None = None
        self.home_waits = 0
        self.lock = threading.Lock()

    def __call__(self, n: int, body: dict[str, Any]) -> tuple[int, Any]:
        system = _system(body)
        who = "courier" if COURIER_TASK in system else "home" if HOME_TASK in system else None
        if who is None:
            return 500, {"detail": "the stand-in cannot tell which robot this is"}
        with self.lock:
            self.turns[who] += 1
            turn = self.turns[who]
        model = str(body.get("model"))
        if turn == 1:
            verdict = {"verdict": "feasible", "reason": "agreeing a handoff moves nothing heavy"}
            return 200, tool_completion(n, "assess_task", verdict, model)
        seen = _latest(body)
        if who == "courier":
            if self.told is not None:
                return 200, tool_completion(n, "declare_success", {"reason": "told it"}, model)
            if HEADING in seen:
                name = re.search(r'"name": "([^"]+)"', seen.split(HEADING, 1)[1])
                if name:
                    self.told = name.group(1)
                    text = f"{PHRASE}: parcel A, a book. Can you carry it inside?"
                    return 200, tool_completion(n, "tell", {"to": self.told, "text": text}, model)
            time.sleep(1.5)
            return 200, tool_completion(n, "report_state", {}, model)
        if PHRASE in seen:
            return 200, tool_completion(n, "declare_success", {"reason": "heard it"}, model)
        if HEADING in seen:
            self.home_waits += 1
            if self.home_waits > 8:
                done = {"reason": "a robot is here and nothing it said reached me"}
                return 200, tool_completion(n, "declare_success", done, model)
        time.sleep(1.0)
        return 200, tool_completion(n, "report_state", {}, model)


def _play(state: Path, act: str) -> tuple[FakeTokenFactory, TwoPilots, dict[str, int]]:
    pilots = TwoPilots()
    with FakeTokenFactory(chat=pilots) as stand_in:
        settings = nebius.Settings(stand_in.base_url, "stand-in-key")
        doorstep.setup(state, act)
        courier_at = f"{local_ip()}:{doorstep.COURIER.port}"
        started = doorstep.launches(settings, MODEL, state, act, "both", courier_at=courier_at)
        codes = doorstep.play(started, courier_at=courier_at, logs=state / "logs")
    return stand_in, pilots, codes


def _home_saw(stand_in: FakeTokenFactory, text: str) -> bool:
    """Whether `text` was ever in what the home robot's model was sent."""
    return any(
        HOME_TASK in _system(p.body) and text in json.dumps(p.body["messages"], ensure_ascii=False)
        for p in stand_in.posts()
    )


def _home_heard(stand_in: FakeTokenFactory) -> bool:
    return _home_saw(stand_in, PHRASE)


#: How quackd tells a pilot that a stranger spoke and its words were kept from it.
WITHHELD = "a stranger's words go to the log, for a person"


needs_ports = pytest.mark.skipif(not _ports_free(), reason="ports 8790 and 8791 are in use")


@needs_ports
def test_appointment_the_couriers_words_reach_the_home_robots_model(tmp_path: Path) -> None:
    stand_in, pilots, codes = _play(tmp_path / "doorstep", "appointment")
    logs = tmp_path / "doorstep" / "logs"
    assert codes == {"courier-01": 0, "home-01": 0}, [
        f.read_text(encoding="utf-8", errors="replace")[-2000:] for f in logs.glob("*.log")
    ]
    assert pilots.told == "home-01", "a pinned robot is shown by its own name"
    assert _home_heard(stand_in)
    assert {p.body["model"] for p in stand_in.posts()} == {TEXT_MODEL}


@needs_ports
def test_stranger_the_couriers_words_never_reach_the_home_robots_model(tmp_path: Path) -> None:
    stand_in, pilots, codes = _play(tmp_path / "doorstep", "stranger")
    assert codes == {"courier-01": 0, "home-01": 0}
    assert pilots.told is not None and pilots.told.startswith("stranger-")
    assert not _home_heard(stand_in)
    # it was said and it arrived: quackd kept it from the model and told the model so
    assert _home_saw(stand_in, WITHHELD), "the courier's words never arrived at all"
    assert pilots.home_waits > 8, "the home robot saw the courier and waited, hearing nothing"
