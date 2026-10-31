"""The onboard service end to end on the sim air: two robots' services in threads of one process,
a courier walking up to a household's robot, as `quackd onboard demo` runs them in two.

They meet over the Bluetooth link when they share no network and over the LAN when they do; a
stranger's name never reaches the other robot's brain, and a pinned owner's robot's does; a
passport changed after its owner signed it is refused. The scene runs four times faster than
the wall clock, and every check is an outcome with a generous deadline, never a timing, so a slow
runner passes too.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from quackd.boards import BoardFacts, named
from quackd.onboard import config, wizard
from quackd.onboard.keys import read_robot_key
from quackd.onboard.radio_side import proximity_policy, sim_side
from quackd.onboard.service import (
    AlreadyRunning,
    Service,
    SimPlace,
    check_board,
    check_own_passport,
    held,
    serve,
)
from quackd.onboard.store import OnboardDir
from quackd.passport import PassportError, document, jws, strict
from quackd.passport.keys import short_id_of
from quackd.passport_talk import Talker
from quackd.passport_trust import Judgement
from quackd.radio.sim import write_scene

COURIER = "g1-edu-mock"
HOUSEHOLD = "agile-one-sn-mock"
SPEED = 4.0
#: The courier starts 7 m out, walks up to 1 m and stays there.
WALK = "7,0@0 1,0@8 1,0@600"
STANDS = "0,0@0"
DEADLINE_S = 60.0
PASSPHRASE = "a passphrase for these tests only"


class NoQuestions:
    def text(self, question: str, default: str | None = None) -> str:
        return default or ""

    def secret(self, question: str, *, twice: bool = False) -> str:
        raise AssertionError("a test reads its passphrase from a file")

    def yes(self, question: str, default: bool = False) -> bool:
        return default


def sim_facts() -> BoardFacts:
    facts = named("sim").detect()
    assert facts is not None
    return facts


def robot(tmp_path: Path, name: str, *, as_name: str | None = None) -> OnboardDir:
    """A robot's directory from the demo's file for `name`, its brain the scripted pilot, its
    passport signed for the sim board."""
    home = OnboardDir(tmp_path / (as_name or name))
    home.ensure()
    text = (resources.files("quackd.onboard") / "demo" / f"{name}.yaml").read_text(encoding="utf-8")
    text = text.replace("\nmeeting:\n", '\nbrain:\n  llm: "fake"\n\nmeeting:\n', 1)
    if as_name is not None:
        text = text.replace(f"name: {name}\n", f"name: {as_name}\n", 1)
    home.robot_yaml.write_text(text, encoding="utf-8")
    secret = tmp_path / f"{home.root.name}.passphrase"
    secret.write_text(PASSPHRASE + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        secret.chmod(0o600)
    wizard.sign(
        home,
        config.load(home.robot_yaml),
        sim_facts().passport(),
        ask=NoQuestions(),
        passphrase_file=secret,
        days=1,
    )
    return home


def air_for(tmp_path: Path) -> Path:
    air = tmp_path / "air"
    air.mkdir()
    write_scene(air, t0=time.time(), speed=SPEED)
    return air


@contextlib.contextmanager
def running(home: OnboardDir, place: SimPlace) -> Iterator[Service]:
    """`quackd onboard serve --board sim` for `home`, on a thread, until the block ends."""
    started: list[Service] = []
    ready = threading.Event()
    failed: list[BaseException] = []

    def go() -> None:
        try:
            serve(
                home,
                board="sim",
                log=lambda _line: None,
                sim=place,
                started=lambda s: (started.append(s), ready.set()),
            )
        except BaseException as e:
            failed.append(e)
            ready.set()

    thread = threading.Thread(target=go, name=f"serve-{home.root.name}", daemon=True)
    thread.start()
    assert ready.wait(30), "the service did not start"
    assert not failed, failed
    try:
        yield started[0]
    finally:
        started[0].stop()
        thread.join(30)
        assert not thread.is_alive(), "the service did not stop"


def met(home: OnboardDir) -> list[dict[str, Any]]:
    try:
        lines = home.met_log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def wait_for(check: Callable[[], bool], seconds: float = DEADLINE_S) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.2)
    return check()


def pilot_saw(home: OnboardDir) -> str:
    """What the robot's pilot was shown, turn by turn, in every run: the observations its record
    keeps. The rest of the record is a person's, and names a stranger as it named itself."""
    seen = []
    for path in sorted(home.runs.rglob("transcript.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            row = json.loads(line)
            if row.get("kind") == "observation":
                seen.append(str(row.get("text", "")))
    assert seen, "no run showed its pilot anything"
    return "\n".join(seen)


def meeting_ended(home: OnboardDir) -> bool:
    return any(path.name == "summary.json" for path in home.runs.rglob("summary.json"))


# ── two robots meet ─────────────────────────────────────────────────────────────────────


def test_two_strangers_with_no_network_in_common_meet_over_the_link(tmp_path: Path) -> None:
    air = air_for(tmp_path)
    household, courier = robot(tmp_path, HOUSEHOLD), robot(tmp_path, COURIER)
    with (
        running(household, SimPlace(air, STANDS, "none", speed=SPEED)),
        running(courier, SimPlace(air, WALK, "none", speed=SPEED)),
    ):
        assert wait_for(lambda: bool(met(household)) and bool(met(courier)))
        assert wait_for(lambda: meeting_ended(household))
    row = met(household)[0]
    assert row["name"] == COURIER and row["tier"] == "stranger" and row["via"] == "ble"
    assert row["owner_label"] == "Courier company (demo)"
    assert met(courier)[0]["name"] == HOUSEHOLD
    # a stranger is shown to a brain by a name made from its key, never by the one it chose
    assert row["shown"].startswith("stranger-")
    seen = pilot_saw(household)
    assert row["shown"] in seen and COURIER not in seen


def test_a_robot_whose_owner_was_pinned_is_known_and_met_over_a_shared_lan(
    tmp_path: Path,
) -> None:
    air = air_for(tmp_path)
    household, courier = robot(tmp_path, HOUSEHOLD), robot(tmp_path, COURIER)
    owner = document.read_jws(courier.passport_jws.read_text(encoding="utf-8").strip())
    config.add_pin(
        household.robot_yaml,
        config.Pin(
            kind="owner",
            id=owner.owner_id,
            name="Courier company (demo)",
            pinned=date.today().isoformat(),
        ),
    )
    with (
        running(household, SimPlace(air, STANDS, "home", speed=SPEED)),
        running(courier, SimPlace(air, WALK, "home", speed=SPEED)),
    ):
        assert wait_for(lambda: bool(met(household)) and bool(met(courier)))
        assert wait_for(lambda: meeting_ended(household))
    row = met(household)[0]
    assert row["tier"] == "known" and row["via"] == "http" and row["shown"] == COURIER
    assert COURIER in pilot_saw(household)
    # pins are each robot's own: the courier's owner pinned nobody
    assert met(courier)[0]["tier"] == "stranger"


def test_a_passport_changed_after_its_owner_signed_it_is_refused(tmp_path: Path) -> None:
    """The intruder's own service would refuse to serve such a passport, so the intruder here is
    the service's parts without that check, as anybody's own code could be."""
    air = air_for(tmp_path)
    household = robot(tmp_path, HOUSEHOLD)
    intruder = robot(tmp_path, COURIER, as_name="g1-edu-intruder")
    head, body, sig = intruder.passport_jws.read_text(encoding="utf-8").strip().split(".")
    doc = strict.loads(jws.b64url_decode(body, rule="4.4"), limit=1 << 16)
    doc["name"] = "g1-edu-courier"  # the edit: the owner signed another name
    token = f"{head}.{jws.b64url(strict.dumps(doc))}.{sig}"
    with pytest.raises(PassportError) as refused:
        document.read_jws(token)
    assert refused.value.rule == "4.4"
    key = read_robot_key(intruder.robot_key)
    side = sim_side(
        air=air,
        path=WALK,
        network="none",
        speed=SPEED,
        policy=proximity_policy(meet_within_m=2.0, rssi_at_1m=-59.0, speed=SPEED),
    )
    talker = Talker(
        "g1-edu-intruder",
        key,
        token,
        judge=lambda _reading: Judgement("stranger", "a test's", ()),
        send=side.send,
        heard=side.heard,
        on_session=side.on_session,
        runtime={"software": "quackd/test"},
    )
    with running(household, SimPlace(air, STANDS, "none", speed=SPEED)):
        side.start(talker, short_id=short_id_of(key.did), digest="0" * 64, http_port=0)
        try:
            stop = threading.Event()
            ticker = threading.Thread(
                target=lambda: [side.tick() for _ in iter(lambda: stop.wait(0.25), True)],
                daemon=True,
            )
            ticker.start()
            assert wait_for(lambda: any("refused" in row for row in met(household)))
        finally:
            stop.set()
            side.close()
    refusals = [row for row in met(household) if "refused" in row]
    assert refusals and "4.4" in refusals[0]["why"]
    assert not [row for row in met(household) if "id" in row]  # nothing of it met anybody
    assert not talker.peers_open()


# ── the parts ───────────────────────────────────────────────────────────────────────────


def test_a_passport_signed_on_another_kind_of_computer_is_not_served(tmp_path: Path) -> None:
    home = robot(tmp_path, HOUSEHOLD)
    token = home.passport_jws.read_text(encoding="utf-8").strip()
    did = read_robot_key(home.robot_key).did
    assert check_board(check_own_passport(token, did), sim_facts()).ok
    jetson = BoardFacts("jetson", "NVIDIA Jetson AGX Thor Developer Kit", "tegra264")
    check = check_board(check_own_passport(token, did), jetson)
    assert not check.ok and "sign it here" in check.why and "runs on sim" in check.why


def test_one_service_holds_a_robots_directory_at_a_time(tmp_path: Path) -> None:
    lock = tmp_path / "service.lock"
    with held(lock), pytest.raises(AlreadyRunning, match="another quackd onboard serve"):  # noqa: SIM117
        with held(lock):
            pass
    with held(lock):  # and it is free again once the first let go
        pass


def test_a_robot_file_edited_by_hand_is_read_again_with_no_command(tmp_path: Path) -> None:
    """A pin written into robot.yaml by an editor, not by `quackd onboard pin`, still reaches
    the running service: it looks at the file's time every few seconds."""
    from tests.passport_helpers import B

    air = air_for(tmp_path)
    household = robot(tmp_path, HOUSEHOLD)
    with running(household, SimPlace(air, STANDS, "none", speed=SPEED)) as service:
        assert service.file.trust.pins == []
        pin = config.Pin(kind="robot", id=B.did, name="cart-01", pinned=date.today().isoformat())
        config.add_pin(household.robot_yaml, pin)
        assert wait_for(lambda: len(service.file.trust.pins) == 1, 20)


def test_robot_env_reaches_a_service_started_in_a_terminal_and_a_set_variable_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quackd.onboard.service import load_env

    env = tmp_path / "robot.env"
    env.write_text("LOCAL_API_KEY=from-the-file\nQUACKD_TEST_SET=from-the-file\n# a note\n")
    monkeypatch.setenv("LOCAL_API_KEY", "")  # recorded, so the test leaves it as it found it
    monkeypatch.setenv("QUACKD_TEST_SET", "from-the-shell")
    assert load_env(env) == ["LOCAL_API_KEY"]
    assert os.environ["LOCAL_API_KEY"] == "from-the-file"
    assert os.environ["QUACKD_TEST_SET"] == "from-the-shell"
    assert load_env(tmp_path / "missing.env") == []
