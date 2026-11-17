"""The onboard service end to end on the sim air: two robots' services in threads of one process,
a courier walking up to a household's robot, as `roboglot onboard demo` runs them in two.

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
import socket
import threading
import time
from collections.abc import Callable, Iterator
from datetime import date
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from roboglot.boards import BoardFacts, named
from roboglot.onboard import config, wizard
from roboglot.onboard.keys import read_robot_key
from roboglot.onboard.radio_side import proximity_policy, sim_side
from roboglot.onboard.service import (
    AlreadyRunning,
    Service,
    SimPlace,
    check_board,
    check_own_passport,
    held,
    serve,
)
from roboglot.onboard.store import OnboardDir
from roboglot.passport import PassportError, document, jws, strict
from roboglot.passport.keys import short_id_of
from roboglot.passport_talk import Talker
from roboglot.passport_trust import Judgement
from roboglot.radio.sim import write_scene

COURIER = "g1-edu-mock"
HOUSEHOLD = "agile-one-sn-mock"
SPEED = 4.0
#: The courier starts 7 m out, walks up to 1 m and stays there.
WALK = "7,0@0 1,0@8 1,0@600"
STANDS = "0,0@0"
DEADLINE_S = 60.0
#: How long a stand-in holds a race's window open for a second caller to land in it.
WINDOW_S = 3.0
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
    text = (resources.files("roboglot.onboard") / "demo" / f"{name}.yaml").read_text(
        encoding="utf-8"
    )
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
    """`roboglot onboard serve --board sim` for `home`, on a thread, until the block ends."""
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
        runtime={"software": "roboglot/test"},
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
    with held(lock), pytest.raises(AlreadyRunning, match="another roboglot onboard serve"):  # noqa: SIM117
        with held(lock):
            pass
    with held(lock):  # and it is free again once the first let go
        pass


@pytest.mark.parametrize("comes", ["a goal", "a meeting", "a goal read before the listener shut"])
def test_no_goal_or_meeting_starts_once_the_service_has_begun_to_close(
    tmp_path: Path, comes: str
) -> None:
    """close() stopped only the run in hand. A goal given on the control port, a meeting a robot
    coming near starts, or a goal op a control thread had read just before the listener closed,
    arriving while close() went on, took the empty slot and ran on after close() returned: the
    process then exited under its thread with no stop, no close of the body and no run_end.
    The radio here waits in its close() on an event, where the real one waits on its links, its
    stop and the LAN record; the read goal's handler waits for the listener to close, as a
    handler thread that read its op just before `ControlServer.close` does, which only ends the
    accept loop."""
    from roboglot.onboard import control
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from tests.test_onboard_brain import Run

    home = robot(tmp_path, HOUSEHOLD)
    in_close, let_close = threading.Event(), threading.Event()
    read, listener_shut, handled = threading.Event(), threading.Event(), threading.Event()

    class SlowSide(RadioSide):
        def close(self) -> None:
            in_close.set()
            let_close.wait(DEADLINE_S)
            super().close()

    lines: list[str] = []
    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: SlowSide(
            NoRadio(), lan=None, server_host="127.0.0.1", record_host="127.0.0.1"
        ),
        log=lines.append,
    )
    service._make_loop = lambda glot, kind, front: Run(glot, kind)  # type: ignore[method-assign]
    handle = service._control

    def late_handle(op: Any, conn: Any) -> Any:
        read.set()
        listener_shut.wait(DEADLINE_S)
        try:
            return handle(op, conn)
        finally:
            handled.set()

    if comes == "a goal read before the listener shut":
        service._control = late_handle  # type: ignore[method-assign]
    service.start()
    assert service.talker is not None and service.slot is not None, lines
    shut = service.control.close

    def shut_then_say() -> None:
        shut()
        listener_shut.set()

    service.control.close = shut_then_say
    closer = threading.Thread(target=service.close, name="close", daemon=True)
    answers: list[Any] = []

    def client() -> None:
        with (
            contextlib.suppress(Exception),
            control.connect(home.control_json, then=DEADLINE_S) as conn,
        ):
            conn.send({"op": "goal", "text": "go"})
            answers.append(conn.read())

    asker = threading.Thread(target=client, name="goal client", daemon=True)
    ours, theirs = socket.socketpair()
    late = None
    try:
        if comes == "a goal read before the listener shut":
            asker.start()
            assert read.wait(DEADLINE_S), "the handler never read the goal"
            let_close.set()
            closer.start()
            assert handled.wait(DEADLINE_S), "the handler never went on"
            said = f"the client was answered {answers}"
        else:
            closer.start()
            assert in_close.wait(DEADLINE_S), "close() never reached the radio"
            if comes == "a goal":
                service._control({"op": "goal", "text": "go"}, control.Connection(ours))
                said = "the goal op was answered"
            else:
                said = service.slot.robot_near("did:key:z6MkClosingPeer", "known")
            let_close.set()
        closer.join(DEADLINE_S)
        assert not closer.is_alive(), "close() did not return"
        running = service.slot.running
        if running is not None and running.thread is not None and running.thread.is_alive():
            late = running
        assert late is None, f"close() returned with a {late.kind} running; {said}; {lines[-3:]}"
        if comes == "a meeting":
            assert said == "no meeting: the service is stopping"
        else:
            assert "a goal: go, refused: the service is stopping" in lines
        if comes == "a goal read before the listener shut":
            asker.join(DEADLINE_S)
            assert answers == [{"ev": "refused", "reason": "the service is stopping"}]
    finally:
        let_close.set()
        listener_shut.set()
        if late is not None:
            service.slot.stop("the test ends")
            late.thread.join(DEADLINE_S)
        for end in (ours, theirs):
            with contextlib.suppress(OSError):
                end.close()
        for thread in (closer, asker):
            if thread.ident is not None:
                thread.join(DEADLINE_S)


def test_closing_fits_the_units_stop_timeout_the_run_first_and_says_what_it_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """close() gave the run 10 s and went on without a word, then said bye to each robot met
    one after another, each with its own bound, then closed the radio, the passport server and
    the listener, each with its own: with a run slow to end and two robots that do not answer
    it outlasted the unit's stop timeout, and systemd killed it partway with nothing said of
    what was left. It keeps inside one budget now, the unit's stop timeout less a margin, the
    run's stop first, and the journal says what did not finish. The service's clock is the
    test's, and the close comes as the budget ends; the run and both byes wait on an event the
    test sets only once close() is over, and the process's end is the test's stand-in."""
    import asyncio

    from roboglot.onboard import service as onboard_service
    from roboglot.onboard import systemd
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from tests.test_onboard_brain import Run

    unit = systemd.unit_text(user="robot", directory=tmp_path, roboglot="/usr/bin/roboglot")
    assert f"\nTimeoutStopSec={systemd.STOP_TIMEOUT_S}\n" in unit
    assert onboard_service.CLOSE_S < systemd.STOP_TIMEOUT_S
    hung, now = threading.Event(), [0.0]

    class Stuck(Run):
        async def run(self) -> Any:
            await asyncio.to_thread(hung.wait, DEADLINE_S)
            return await super().run()

    lines: list[str] = []
    ended: list[int] = []
    home = robot(tmp_path, HOUSEHOLD)
    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""),
        log=lines.append,
        clock=lambda: now[0],
        end_process=ended.append,
    )
    service._make_loop = lambda glot, kind, front: Stuck(glot, kind)  # type: ignore[method-assign]

    def leave(peer: str) -> None:
        lines.append(f"bye to {peer}")
        hung.wait(DEADLINE_S)

    service.start()
    closer = threading.Thread(target=service.close, name="close", daemon=True)
    try:
        assert service.talker is not None and service.slot is not None
        service.talker.peers_open = lambda: {"did:key:z6MkOne", "did:key:z6MkTwo"}  # type: ignore[method-assign]
        service.talker.leave = leave  # type: ignore[method-assign]
        assert service.slot.goal("Wait by the door.") is None
        service.stop()
        assert wait_for(lambda: "the goal is stopping: the service is stopping" in lines)
        now[0] = onboard_service.CLOSE_S  # the run took the whole budget
        closer.start()
        closer.join(DEADLINE_S)
        assert not closer.is_alive(), f"close() outlasted its budget; {lines}"
        assert wait_for(lambda: sum(line.startswith("bye to ") for line in lines) == 2), lines
        stopping = lines.index("the goal is stopping: the service is stopping")
        byes = [n for n, line in enumerate(lines) if line.startswith("bye to ")]
        assert len(byes) == 2 and stopping < min(byes), lines
        # this run waits on a call in a thread of its own, which nothing in Python ends and the
        # interpreter waits for at its exit: the process is ended, on every path, and said
        assert (
            "the goal has not ended though it was told to stop, and the service ends its process "
            "now with exit code 70: its record says what stop the body was sent; not done: the "
            "body's close, the run's end record"
        ) in lines
        assert ended == [onboard_service.LEFT_EXIT] == [70]
        left = [line for line in lines if line.startswith("the service stops before it finished")]
        # the byes hold up only the radio, which had no time left to start its close
        assert len(left) == 1 and left[0].startswith(
            "the service stops before it finished saying bye to 2 robots met and closing the radio"
        ), lines
        assert not home.control_json.exists()
    finally:
        hung.set()
        if closer.ident is not None:
            closer.join(DEADLINE_S)
        running = service.slot.running if service.slot is not None else None
        if running is not None and running.thread is not None:
            running.thread.join(DEADLINE_S)


def test_the_close_budget_counts_from_the_stop_and_not_from_the_start_of_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """systemd's stop timeout runs from its SIGTERM, but the close's budget ran from the start of
    `close`, which comes only once the look in hand when the stop arrived is over: a look slow on
    a radio loop that does not answer can take two calls onto it, so the close could run on past
    the unit's timeout by that much. The budget counts from the stop now. The service's clock
    here is the test's: the stop comes at its nought, and the close at a moment past the whole
    budget, as after a look that held the stop that long, so the close waits for nothing; the
    run does not end and a robot met does not answer, either of which would hold a close
    counted from its own start for the whole budget."""
    import asyncio

    from roboglot.onboard import service as onboard_service
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from tests.test_onboard_brain import Run

    hung, now, ended = threading.Event(), [0.0], []

    class Stuck(Run):
        async def run(self) -> Any:
            await asyncio.to_thread(hung.wait, DEADLINE_S)
            return await super().run()

    service = Service(
        robot(tmp_path, HOUSEHOLD),
        facts=sim_facts(),
        radio_side=lambda: RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""),
        log=lambda _line: None,
        clock=lambda: now[0],
        end_process=ended.append,
    )
    service._make_loop = lambda glot, kind, front: Stuck(glot, kind)  # type: ignore[method-assign]
    closer = threading.Thread(target=service.close, name="close", daemon=True)
    try:
        service.start()
        assert service.talker is not None and service.slot is not None
        # a robot met that does not answer, so a close counted from its own start would wait
        service.talker.peers_open = lambda: {"did:key:z6MkOne"}  # type: ignore[method-assign]
        service.talker.leave = lambda peer: hung.wait(DEADLINE_S)  # type: ignore[method-assign]
        assert service.slot.goal("Wait by the door.") is None
        service.stop()  # what the SIGTERM handler and the shutdown op call, at nought
        now[0] = onboard_service.CLOSE_S + 1.0  # the look in hand held the stop past it all
        closer.start()
        closer.join(WINDOW_S)
        assert not closer.is_alive(), "the close waited, though its budget was spent"
        assert ended == [onboard_service.LEFT_EXIT]
    finally:
        hung.set()
        closer.join(DEADLINE_S)
        running = service.slot.running if service.slot is not None else None
        if running is not None and running.thread is not None:
            running.thread.join(DEADLINE_S)


def test_a_call_an_ended_run_left_behind_ends_the_process_and_says_so(tmp_path: Path) -> None:
    """A verb whose blocking call would not end is left behind by the executor while its run
    goes on and ends, and the interpreter waits at its exit for every such call: the close
    found nothing unfinished and said nothing, and the process then stayed until systemd's
    kill, or for good in a terminal, where nothing ends it. A close whose budget ends with such a
    call still going says so in one line now, and ends the process with a failing exit code.
    The service's clock is the test's, the close comes as the budget ends, and the process's end
    is the test's stand-in."""
    import asyncio

    from roboglot.onboard import service as onboard_service
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from tests.test_onboard_brain import Run

    never, now, lines, ended = threading.Event(), [0.0], [], []

    class LeavesACall(Run):
        async def run(self) -> Any:
            # a verb's blocking call that would not end, left behind as the executor leaves one
            asyncio.get_running_loop().run_in_executor(None, never.wait, DEADLINE_S)
            return await super().run()

    service = Service(
        robot(tmp_path, HOUSEHOLD),
        facts=sim_facts(),
        radio_side=lambda: RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""),
        log=lines.append,
        clock=lambda: now[0],
        end_process=ended.append,
    )
    service._make_loop = lambda glot, kind, front: LeavesACall(glot, kind)  # type: ignore[method-assign]
    try:
        service.start()
        assert service.slot is not None and service.slot.goal("Wait by the door.") is None
        running = service.slot.running
        service.stop()
        assert running is not None and running.thread is not None
        running.thread.join(DEADLINE_S)
        assert not running.thread.is_alive(), "the run itself ended on its stop"
        now[0] = onboard_service.CLOSE_S
        service.close()
        assert ended == [onboard_service.LEFT_EXIT], lines
        assert lines[-1] == (
            "a call the goal handed to a thread of its own has not ended, and would hold the "
            "process for good: the service ends its process now with exit code 70"
        ), lines
    finally:
        never.set()


def test_the_line_of_a_run_left_says_what_its_record_says_of_the_stop_the_body_was_sent(
    tmp_path: Path,
) -> None:
    """The line a close says of a run it ends the process on names what was sent to the body,
    from the run's own record as it was written: the `intent` row its transport writes once a
    stop has come back, taken or not. Each row still goes on to the goal's terminal."""
    from roboglot.log import LogEvent
    from roboglot.onboard import service as onboard_service

    shown: list[Any] = []
    service = Service(
        robot(tmp_path, HOUSEHOLD),
        facts=sim_facts(),
        radio_side=lambda: None,  # type: ignore[arg-type,return-value]
        log=lambda _line: None,
    )
    agent = object()
    assert service._stop_said(agent) == "its record says what stop the body was sent"
    heard = onboard_service._StopsHeard(shown.append)
    service._stops_heard = {id(agent): heard}
    assert service._stop_said(agent) == "no stop it was sent has come back"
    refused = LogEvent("intent", 1.0, {"intent": "stop", "accepted": False, "reason": "link down"})
    heard(refused)
    assert service._stop_said(agent) == "the stop it was sent did not land: link down"
    heard(LogEvent("intent", 2.0, {"intent": "stop", "accepted": True, "reason": None}))
    assert service._stop_said(agent) == "the body took the stop it was sent"
    assert [event.t for event in shown] == [1.0, 2.0]


class JoinSpy:
    """A run's thread whose joins are noted with the time each was given, and which lets the run
    end as the close begins its wait, so what the close gave the run is read off the join and
    never off a clock."""

    def __init__(self, thread: threading.Thread, release: threading.Event) -> None:
        self.thread, self.release, self.given = thread, release, []

    def join(self, timeout: float | None = None) -> None:
        self.given.append(timeout)
        self.release.set()
        self.thread.join(timeout)

    def is_alive(self) -> bool:
        return self.thread.is_alive()


def test_a_stop_tells_the_run_at_once_whatever_the_look_in_hand_is_doing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stop reached the run only once `run()` had finished the look in hand, and a look that
    turned talk off said bye to each robot met one after another before it: with robots out of
    range, a goal running then got its stop seconds late, near systemd's kill. The stop tells
    the run at once now, on a thread of its own, whatever the look is doing. The look here turns
    talk off, a passport that ran out, and its bye waits until the test ends it."""
    from roboglot.onboard import service as onboard_service
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from tests.test_onboard_brain import Run

    hung, lines = threading.Event(), []
    real = onboard_service.check_own_passport
    failing = [False]

    def check(token: str | None, did: str, *, now: int | None = None) -> Any:
        if failing[0]:
            return onboard_service.SelfCheck(False, "its own passport does not check out: 4.5")
        return real(token, did, now=now)

    monkeypatch.setattr(onboard_service, "check_own_passport", check)
    service = Service(
        robot(tmp_path, HOUSEHOLD),
        facts=sim_facts(),
        radio_side=lambda: RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""),
        log=lines.append,
    )
    service._make_loop = lambda glot, kind, front: Run(glot, kind)  # type: ignore[method-assign]
    look = threading.Thread(target=service.tick, name="look", daemon=True)
    try:
        service.start()
        assert service.talker is not None and service.slot is not None
        service.talker.peers_open = lambda: {"did:key:z6MkOne"}  # type: ignore[method-assign]
        service.talker.leave = lambda peer: hung.wait(DEADLINE_S)  # type: ignore[method-assign]
        assert service.slot.goal("Wait by the door.") is None
        running = service.slot.running
        failing[0] = True
        service._last_status = 0.0  # the next look is due, and turns talk off
        look.start()
        assert wait_for(lambda: service._turning), "the look never began to turn talk off"
        service.stop()
        assert wait_for(lambda: running is not None and running.stop_wanted is not None, 10)
        assert look.is_alive(), "told while the look still says its byes"
        assert running is not None and running.stop_wanted == "the service is stopping"
    finally:
        hung.set()
        look.join(DEADLINE_S)
        service.close()


def test_a_run_is_given_what_is_left_of_the_budget_and_nothing_is_kept_for_byes_to_nobody(
    tmp_path: Path,
) -> None:
    """The run was joined until the deadline less the share kept for byes, whether any robot was
    met or not, and counted from the stop: after a look that held the stop, a run that would
    have ended inside what systemd gives was left, with most of the budget unused. With nobody
    met the run is given all that is left now. The service's clock is the test's, the close
    comes at a moment where a share kept for byes would leave the run nothing, and what the
    close gave the run is read off its join."""
    from roboglot.onboard import service as onboard_service
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from tests.test_onboard_brain import Run

    release, now, lines, ended = threading.Event(), [0.0], [], []

    class EndsWhenLet(Run):
        async def run(self) -> Any:
            await self.executor.abort.wait()
            import asyncio

            await asyncio.to_thread(release.wait, DEADLINE_S)
            return await super().run()

    service = Service(
        robot(tmp_path, HOUSEHOLD),
        facts=sim_facts(),
        radio_side=lambda: RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""),
        log=lines.append,
        clock=lambda: now[0],
        end_process=ended.append,
    )
    service._make_loop = lambda glot, kind, front: EndsWhenLet(glot, kind)  # type: ignore[method-assign]
    try:
        service.start()
        assert service.slot is not None and service.slot.goal("Wait by the door.") is None
        running = service.slot.running
        assert running is not None and running.thread is not None
        spy = JoinSpy(running.thread, release)
        running.thread = spy  # type: ignore[assignment]
        service.stop()
        left_s = onboard_service.AFTER_RUN_S / 2  # less than a share kept for byes
        now[0] = onboard_service.CLOSE_S - left_s
        service.close()
        assert spy.given[:1] == [left_s], spy.given
        assert not spy.is_alive() and ended == [], lines
    finally:
        release.set()
        service.close()


def test_the_lan_record_is_withdrawn_inside_the_budget_whatever_the_run_and_the_radio_take(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LAN record other robots find this one by was withdrawn last in the radio's close,
    after its two calls onto the radio's loop, while one such call was all that was kept for
    the parts after the run: after a run that took its whole share, a radio whose links and stop
    each answered inside their own bound ran past the budget, and the record stayed announced
    until its time to live ran out. The service's clock is the test's: the close comes as the
    budget ends, after a run that took it all, so nothing after the run is waited for, and the
    record is withdrawn all the same, as the close begins; the run here ends only once the test
    lets it, and the process's end the close then asks for is the test's stand-in."""
    import asyncio

    from roboglot.onboard import service as onboard_service
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from tests.test_onboard_brain import Run

    withdrawn, release, now, ended = threading.Event(), threading.Event(), [0.0], []

    class Lan:
        browser = None

        def publish(self, txt: Any, *, host: str, port: int) -> bool:
            return True

        def withdraw(self) -> None:
            withdrawn.set()

        def lookup(self, beacon_id: str) -> Any:
            return None

    class Long(Run):
        async def run(self) -> Any:
            await self.executor.abort.wait()
            await asyncio.to_thread(release.wait, DEADLINE_S)  # its teardown, a long fold
            return await super().run()

    lines: list[str] = []
    service = Service(
        home := robot(tmp_path, HOUSEHOLD),
        facts=sim_facts(),
        radio_side=lambda: RadioSide(
            NoRadio(), lan=Lan(), server_host="127.0.0.1", record_host="127.0.0.1"
        ),
        log=lines.append,
        clock=lambda: now[0],
        end_process=ended.append,
    )
    service._make_loop = lambda glot, kind, front: Long(glot, kind)  # type: ignore[method-assign]
    try:
        service.start()
        assert service.slot is not None and service.slot.goal("Wait by the door.") is None
        service.stop()
        now[0] = onboard_service.CLOSE_S  # the run took the whole budget
        service.close()
        assert wait_for(withdrawn.is_set), f"the LAN record stayed announced: {lines[-2:]}"
        assert any("closing the radio" in line for line in lines), lines  # it had no time at all
        assert not home.control_json.exists()
    finally:
        release.set()
        running = service.slot.running if service.slot is not None else None
        if running is not None and running.thread is not None:
            running.thread.join(DEADLINE_S)


def service_with_runs(
    tmp_path: Path, hung: threading.Event, lines: list[str]
) -> tuple[OnboardDir, Service]:
    """A started service whose goals are stand-in runs: the first ends on its stop, as a run's
    teardown does, and every later one holds out until `hung` is set, as one whose stop has
    not landed."""
    import asyncio

    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from tests.test_onboard_brain import Run

    class Stuck(Run):
        async def run(self) -> Any:
            await asyncio.to_thread(hung.wait, DEADLINE_S)
            return await super().run()

    made: list[str] = []

    def make(glot: Any, kind: str, front: Any) -> Run:
        made.append(kind)
        return (Run if len(made) == 1 else Stuck)(glot, kind)

    home = robot(tmp_path, HOUSEHOLD)
    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""),
        log=lines.append,
    )
    service._make_loop = make  # type: ignore[method-assign]
    return home, service.start()


def test_the_stop_op_says_whether_the_run_it_stopped_has_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop op answered as soon as the stop was posted, so `roboglot onboard stop` read a run
    whose stop had not landed, or whose teardown was still sending, as stopped. It waits now,
    `STOP_WAITS_S` at most, and says whether the run ended, beside the `was` a client from
    before reads."""
    from roboglot.onboard import service as onboard_service

    monkeypatch.setattr(onboard_service, "STOP_WAITS_S", 0.5)
    hung, lines = threading.Event(), []
    _home, service = service_with_runs(tmp_path, hung, lines)
    try:
        assert service.slot is not None and service.slot.goal("Wait by the door.") is None
        assert service._control({"op": "stop"}, None) == {
            "ev": "stopped",
            "was": True,
            "ended": True,
        }
        assert service._control({"op": "stop"}, None) == {"ev": "stopped", "was": False}
        assert service.slot.goal("Wait by the window.") is None
        stopped = service._control({"op": "stop"}, None)
        assert stopped == {"ev": "stopped", "was": True, "ended": False}
    finally:
        hung.set()
        service.close()


def test_a_second_stop_from_a_goals_terminal_lets_it_go_and_the_journal_says_so(
    tmp_path: Path,
) -> None:
    """A second Ctrl-C at `roboglot onboard goal` asks the run to quit at once, and the goal's
    front was built with a quit-now that did nothing: the terminal went on waiting on a run
    whose stop had not landed, and the journal said `stop: quitting at once` while nothing
    quit. The service never quits for one terminal, so that terminal is let go now and told the
    run is still ending, the journal says so, and the service runs on."""
    from roboglot.onboard import control
    from roboglot.onboard import service as onboard_service

    hung, lines = threading.Event(), []
    home, service = service_with_runs(tmp_path, hung, lines)
    assert service.slot is not None
    service.slot.goal("A first goal, stopped at once.")
    service.slot.stop_and_wait(DEADLINE_S)
    seen: list[Any] = []
    try:
        with control.connect(home.control_json, then=DEADLINE_S) as conn:
            conn.send({"op": "goal", "text": "go"})
            while not seen or seen[-1].get("ev") != "run_dir":
                seen.append(conn.read())
            conn.send({"op": "stop"})
            conn.send({"op": "stop", "now": True})
            while seen[-1] is not None:
                seen.append(conn.read())
        notices = [line.get("text") for line in seen if line and line.get("ev") == "notice"]
        assert notices == [onboard_service.STILL_ENDING], seen
        assert onboard_service.LET_GO.format(text="go") in lines
        running = service.slot.running
        assert running is not None and running.thread is not None and running.thread.is_alive()
        assert control.request(home.control_json, {"op": "status"})["run"]["run"] == "goal"
    finally:
        hung.set()
        service.close()


def test_a_robot_file_edited_by_hand_is_read_again_with_no_command(tmp_path: Path) -> None:
    """A pin written into robot.yaml by an editor, not by `roboglot onboard pin`, still reaches
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
    from roboglot.onboard.service import load_env

    env = tmp_path / "robot.env"
    env.write_text("LOCAL_API_KEY=from-the-file\nROBOGLOT_TEST_SET=from-the-file\n# a note\n")
    monkeypatch.setenv("LOCAL_API_KEY", "")  # recorded, so the test leaves it as it found it
    monkeypatch.setenv("ROBOGLOT_TEST_SET", "from-the-shell")
    assert load_env(env) == ["LOCAL_API_KEY"]
    assert os.environ["LOCAL_API_KEY"] == "from-the-file"
    assert os.environ["ROBOGLOT_TEST_SET"] == "from-the-shell"
    assert load_env(tmp_path / "missing.env") == []


def test_a_lan_hello_naming_a_beacon_comes_from_an_address_that_robots_record_names() -> None:
    """A beacon id is public, in every advert and every LAN record, so the radio having heard
    it let any host on the LAN borrow the id of a robot that really was near."""
    from roboglot.lan.lookup import LanRecord
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio, Sighting

    class Lan:
        def __init__(self) -> None:
            self.records: dict[str, LanRecord] = {}

        def publish(self, txt: Any, *, host: str, port: int) -> bool:
            return True

        def withdraw(self) -> None:
            return None

        def lookup(self, beacon_id: str) -> LanRecord | None:
            return self.records.get(beacon_id)

    lan = Lan()
    side = RadioSide(NoRadio(), lan=lan, server_host="127.0.0.1", record_host="127.0.0.1")
    side.loop.start()
    beacon = "0123456789abcdef"

    async def hear() -> None:
        side.tracker.observe(Sighting(beacon, 0, -60.0, "sim:near", time.monotonic()))

    try:
        side.loop.call(hear(), 5)
        assert side.heard(beacon, "http://10.0.0.9")  # no record to hold it to: the radio's word
        record = LanRecord("10.0.0.5", 8790, "abcd", beacon, ("10.0.0.5", "192.168.1.5"))
        lan.records[beacon] = record
        assert side.heard(beacon, "http://10.0.0.5") and side.heard(beacon, "http://192.168.1.5")
        assert not side.heard(beacon, "http://10.0.0.9")  # another host, borrowing its beacon id
        assert not side.heard("fedcba9876543210", "http://10.0.0.5")  # a beacon never heard
        assert side.heard(beacon, "ble://a1b2c3d4")  # a link is near by construction
    finally:
        side.close()


def test_talk_goes_off_when_its_own_passport_stops_checking_out_and_on_when_it_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service read its own passport against the clock only as it started: one that expired
    went on being served, a clock set since stayed silent, and a passport signed while talk was
    off never turned it on."""
    from roboglot.onboard import service as onboard_service

    air = air_for(tmp_path)
    household = robot(tmp_path, HOUSEHOLD)
    real = onboard_service.check_own_passport
    failing = [False]

    def check(token: str | None, did: str, *, now: int | None = None) -> Any:
        if failing[0]:
            return onboard_service.SelfCheck(False, "its own passport does not check out: 4.5")
        return real(token, did, now=now)

    monkeypatch.setattr(onboard_service, "check_own_passport", check)
    with running(household, SimPlace(air, STANDS, "none", speed=SPEED)) as service:
        assert service.talker is not None
        failing[0] = True
        assert wait_for(lambda: service.talker is None, 15)
        assert service.status()["radio"]["state"] == "off"
        failing[0] = False
        assert wait_for(lambda: service.talker is not None, 15)


def talk_off_until_signed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[OnboardDir, Callable[..., None]]:
    """A robot whose own passport has run out, so its service starts with talk off, and how to
    sign it again, as `roboglot onboard sign` does, for `days` days."""
    from roboglot.onboard import service as onboard_service

    home = robot(tmp_path, HOUSEHOLD)
    stale = home.passport_jws.read_text(encoding="utf-8").strip()
    real = onboard_service.check_own_passport

    def check(token: str | None, did: str, *, now: int | None = None) -> Any:
        if token == stale:
            return onboard_service.SelfCheck(False, "its own passport does not check out: 4.5")
        return real(token, did, now=now)

    monkeypatch.setattr(onboard_service, "check_own_passport", check)

    def sign_again(days: int = 2) -> None:
        wizard.sign(
            home,
            config.load(home.robot_yaml),
            sim_facts().passport(),
            ask=NoQuestions(),
            passphrase_file=tmp_path / f"{home.root.name}.passphrase",
            days=days,
        )

    return home, sign_again


def test_talk_starts_once_when_a_reload_and_the_tick_both_find_it_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`roboglot onboard sign` sends a reload once it has signed, on a control thread, and the
    main thread's tick looks at the passport every few seconds. With talk off, both started it:
    two radio sides and two passport servers, the service keeping one of each and closing neither
    of the others, and with a fixed port the second bind raised instead. The radio factory here
    waits, between the check and the act, for a second call a service that starts talk once never
    makes; in the real service that window is making the radio side (on a board, starting the
    DNS-SD browse) and starting the passport server."""
    from roboglot.onboard import service as onboard_service
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.passport.serve import PassportServer
    from roboglot.radio import NoRadio

    home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)
    servers: list[PassportServer] = []

    class Counted(PassportServer):
        def start(self) -> Any:
            servers.append(self)
            return super().start()

    monkeypatch.setattr(onboard_service, "PassportServer", Counted)
    sides: list[RadioSide] = []
    second = threading.Event()

    def make_side() -> RadioSide:
        sides.append(RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""))
        if len(sides) == 1:
            second.wait(WINDOW_S)  # between the check and the act: does a second one come?
        else:
            second.set()
        return sides[-1]

    service = Service(home, facts=sim_facts(), radio_side=make_side, log=lambda _line: None)
    replies: list[Any] = []
    reload = threading.Thread(
        target=lambda: replies.append(service._control({"op": "reload"}, None)), daemon=True
    )
    try:
        service.start()
        assert service.talker is None, "talk is off: its own passport has run out"
        sign_again()
        reload.start()
        assert wait_for(lambda: bool(sides), WINDOW_S)
        service.tick()  # the main thread's look lands while the reload starts talk
        reload.join(DEADLINE_S)
        assert len(sides) == 1 and len(servers) == 1, (len(sides), len(servers))
        assert service.server is servers[0] and service.talker is not None
        assert [reply.get("ok") for reply in replies] == [True]
    finally:
        if reload.ident is not None:
            reload.join(DEADLINE_S)
        service.close()
        for part in [*servers, *sides]:
            with contextlib.suppress(Exception):
                part.close()


def test_a_passport_signed_again_while_talk_is_being_made_is_the_one_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Talk's parts are made outside its lock, and a reload that came meanwhile, a second
    `roboglot onboard sign`, took the newer passport as the service's own but found no Talker
    and no passport server to hand it to; the start under way then noted parts made with the
    older one, which the robot's hellos and its server carried for the life of the service while
    status checked the newer, and the second sign was told the service served it. The radio
    factory holds the making open until the second reload has taken the newer passport, as
    making a board's radio side, its DNS-SD browse, can; nothing here waits on a clock."""
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.passport.beacon import WELL_KNOWN_JWS
    from roboglot.radio import NoRadio

    making, go_on = threading.Event(), threading.Event()
    sides: list[RadioSide] = []

    def make_side() -> RadioSide:
        sides.append(RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""))
        if len(sides) == 1:
            making.set()
            go_on.wait(DEADLINE_S)
        return sides[-1]

    home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)
    service = Service(home, facts=sim_facts(), radio_side=make_side, log=lambda _line: None)
    answers: list[Any] = []
    first = threading.Thread(target=lambda: service._control({"op": "reload"}, None), daemon=True)
    second = threading.Thread(
        target=lambda: answers.append(service._control({"op": "reload"}, None)), daemon=True
    )
    try:
        service.start()
        sign_again()
        first.start()
        assert making.wait(DEADLINE_S), "the first reload never began to make talk"
        sign_again(days=3)
        newest = home.passport_jws.read_text(encoding="utf-8").strip()
        second.start()
        assert wait_for(lambda: service.token == newest), "the second reload took nothing"
        go_on.set()
        for thread in (first, second):
            thread.join(DEADLINE_S)
        assert service.talker is not None and service.server is not None
        assert service.talker.endpoint.passport == newest, "the hellos carry the older passport"
        assert service.server._routes[WELL_KNOWN_JWS][0] == newest.encode("ascii")
        assert answers and "unserved" not in answers[0], answers
    finally:
        go_on.set()
        for thread in (first, second):
            if thread.ident is not None:
                thread.join(DEADLINE_S)
        service.close()


class SlowToStart:
    """A radio whose start waits until the test releases it, or until it is cut short, as BlueZ
    waiting on its controller at boot does, inside the start's bound; it says when its start
    began, whether it was cut short, and whether its stop came while the start still ran."""

    def __init__(self) -> None:
        from roboglot.radio import NoRadio

        self.inside, self.release = threading.Event(), threading.Event()
        self.cut_short = threading.Event()
        self.stopped_while_starting: list[bool] = []
        self._starting = False
        self._radio = NoRadio("off", "not started", kind="bluez")

    @property
    def kind(self) -> str:
        return self._radio.kind

    async def start(self, **kwargs: Any) -> Any:
        import asyncio

        self._starting = True
        self.inside.set()
        try:
            await asyncio.to_thread(self.release.wait, DEADLINE_S)
        except asyncio.CancelledError:
            self.cut_short.set()
            raise
        finally:
            self._starting = False
        return await self._radio.start(**kwargs)

    async def stop(self) -> None:
        self.stopped_while_starting.append(self._starting)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._radio, name)


@pytest.mark.parametrize("starter", ["a reload", "the tick's look"])
def test_no_look_and_no_reload_waits_on_a_radio_that_is_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, starter: str
) -> None:
    """Talk's lock was held through the radio's start, up to 20 s with BlueZ slow at boot. With
    `roboglot onboard sign`'s reload starting it, the main thread's next look waited on that
    lock, and the look is where a SIGTERM's stop is read on its way to close(): a goal running
    then might never be told to stop before systemd killed the service. With the tick's own look
    starting it, the tick itself waited, and `roboglot onboard pin`'s reload with it, past its
    client's wait. The radio here starts only when the test lets it, and the look, the tick
    that started talk and a second reload must all be over while it is still starting; talk
    still starts once."""
    from roboglot.onboard.radio_side import RadioSide

    home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)
    radio, sides = SlowToStart(), []

    def make_side() -> RadioSide:
        sides.append(RadioSide(radio, lan=None, server_host="127.0.0.1", record_host=""))  # type: ignore[arg-type]
        return sides[-1]

    service = Service(home, facts=sim_facts(), radio_side=make_side, log=lambda _line: None)
    first = threading.Thread(
        target=(lambda: service._control({"op": "reload"}, None))
        if starter == "a reload"
        else service.tick,
        daemon=True,
    )
    meanwhile = threading.Thread(target=lambda: None, daemon=True)
    try:
        service.start()
        assert service.talker is None, "talk is off: its own passport has run out"
        sign_again()
        service._seen = (None, None)  # the files' times as the tick's look would find them changed
        first.start()
        assert radio.inside.wait(DEADLINE_S), "the radio's start never began"

        def looks() -> None:
            service._last_status = 0.0  # the next look is due
            service.tick()
            service._control({"op": "reload"}, None)  # what `roboglot onboard pin` sends

        meanwhile = threading.Thread(target=looks, daemon=True)
        meanwhile.start()
        # the radio's start ends only when the test lets it, so a thread that waits on it is
        # still waiting at any deadline, however generous
        meanwhile.join(DEADLINE_S)
        first.join(DEADLINE_S)
        assert not radio.release.is_set()
        assert not meanwhile.is_alive(), "a look or a reload waited on the radio's start"
        assert not first.is_alive(), f"{starter} that started talk waited on the radio's start"
        radio.release.set()
        assert wait_for(lambda: service.radio_side is not None and service.radio_side.loop.running)
        assert len(sides) == 1 and service.talker is not None
    finally:
        radio.release.set()
        for thread in (first, meanwhile):
            if thread.ident is not None:
                thread.join(DEADLINE_S)
        service.close()


def test_a_close_while_the_radio_starts_cuts_the_start_short_before_the_radios_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A close that came while a radio was starting went ahead, but the radio's stop ran beside a
    start still under way (BlueZ's stop then waits for its bring-up to let go, and is cut at its
    own bound), and the loop's end then cancelled the start, which the start's caller logged as
    `talk could not start` with talk on. The start is cut short first now, then the radio is
    stopped, and the journal says the start was cut short."""
    from roboglot.onboard.radio_side import RadioSide

    home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)
    radio, lines = SlowToStart(), []
    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: RadioSide(
            radio,  # type: ignore[arg-type]
            lan=None,
            server_host="127.0.0.1",
            record_host="",
            log=lines.append,
        ),
        log=lines.append,
    )
    reload = threading.Thread(
        target=lambda: service._control({"op": "reload"}, None), name="reload", daemon=True
    )
    try:
        service.start()
        sign_again()
        reload.start()
        assert radio.inside.wait(DEADLINE_S), "the radio's start never began"
        service.close()
        assert radio.cut_short.is_set() and radio.stopped_while_starting == [False], lines
        assert "radio bluez: its start was cut short, as the service stops" in lines
        assert wait_for(lambda: not reload.is_alive())
        assert not [line for line in lines if "talk could not start" in line], lines
    finally:
        radio.release.set()
        reload.join(DEADLINE_S)


def test_a_record_published_as_the_close_came_is_withdrawn_once_its_publish_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A radio's start published the LAN record before it looked at whether a close had come, and
    a start from a look or a reload runs on a thread the close does not wait for: a close that
    came while the record was going out withdrew nothing, and the publish then landed for a
    service that had gone, found by other robots until its time to live ran out. The LAN
    stand-in's publish here goes out only once the close is over, as zeroconf's, with its
    probes first, can come back after it."""
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio

    publishing, let_out = threading.Event(), threading.Event()
    announced: list[bool] = []

    class SlowLan:
        def publish(self, txt: Any, *, host: str, port: int) -> bool:
            publishing.set()
            let_out.wait(DEADLINE_S)
            announced.append(True)
            return True

        def withdraw(self) -> None:
            announced.append(False)

        def lookup(self, beacon_id: str) -> Any:
            return None

    home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)
    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: RadioSide(
            NoRadio(), lan=SlowLan(), server_host="127.0.0.1", record_host="127.0.0.1"
        ),
        log=lambda _line: None,
    )
    try:
        service.start()
        sign_again()
        service._control({"op": "reload"}, None)
        assert publishing.wait(DEADLINE_S), "the radio's start never published the record"
        service.close()
        let_out.set()  # the publish comes back after the close
        starting = [t for t in threading.enumerate() if t.name == "onboard-radio-start"]
        for thread in starting:
            thread.join(DEADLINE_S)
        assert announced[-1:] == [False], f"announced last: {announced}"
    finally:
        let_out.set()


def test_a_radio_start_that_never_comes_back_keeps_no_look_from_turning_talk_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Talk was marked as turning until its radio's start came back, so a start stuck in a LAN
    record's publish that never returned, or a thread that could not be had to run it, kept the
    mark for good: every later look left talk alone, and a passport that ran out never turned
    talk off. The mark ends with talk's own parts now, and the radio's start is apart from it.
    The publish here comes back only once the test has looked."""
    from roboglot.onboard import service as onboard_service
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio

    publishing, let_out = threading.Event(), threading.Event()

    class StuckLan:
        def publish(self, txt: Any, *, host: str, port: int) -> bool:
            publishing.set()
            let_out.wait(DEADLINE_S)
            return True

        def withdraw(self) -> None:
            return None

        def lookup(self, beacon_id: str) -> Any:
            return None

    home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)
    inner, expired = onboard_service.check_own_passport, [False]

    def check(token: str | None, did: str, *, now: int | None = None) -> Any:
        if expired[0]:
            return onboard_service.SelfCheck(False, "its own passport does not check out: 4.5")
        return inner(token, did, now=now)

    monkeypatch.setattr(onboard_service, "check_own_passport", check)
    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: RadioSide(
            NoRadio(), lan=StuckLan(), server_host="127.0.0.1", record_host="127.0.0.1"
        ),
        log=lambda _line: None,
    )
    try:
        service.start()
        sign_again()
        service._control({"op": "reload"}, None)
        assert publishing.wait(DEADLINE_S), "the radio's start never published the record"
        expired[0] = True  # the passport runs out while the start is stuck
        service._last_status = 0.0  # the next look is due
        service.tick()
        assert service.talker is None and service.server is None, "talk stayed on"
    finally:
        let_out.set()
        for thread in [t for t in threading.enumerate() if t.name == "onboard-radio-start"]:
            thread.join(DEADLINE_S)
        service.close()


def test_talk_does_not_turn_on_once_the_service_has_begun_to_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control listener stays open while the close waits for the run in hand, and a reload
    with a passport signed while talk was off turned talk on then: a Talker, a passport server
    and a radio, started while the service stopped, and one started after the close had read
    them was never closed. Talk is not turned on once the close has begun, and the reload's
    answer says why it is not served. The run here holds the close at its join until the reload
    is answered."""
    import asyncio

    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from tests.test_onboard_brain import Run

    home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)
    answered = threading.Event()

    class Slow(Run):
        async def run(self) -> Any:
            await self.executor.abort.wait()
            await asyncio.to_thread(answered.wait, DEADLINE_S)
            return await super().run()

    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""),
        log=lambda _line: None,
    )
    service._make_loop = lambda glot, kind, front: Slow(glot, kind)  # type: ignore[method-assign]
    closer = threading.Thread(target=service.close, name="close", daemon=True)
    try:
        service.start()
        assert service.slot is not None and service.slot.goal("Wait by the door.") is None
        closer.start()
        assert wait_for(lambda: service._shutting)
        sign_again()
        told = service._control({"op": "reload"}, None)
        answered.set()
        closer.join(DEADLINE_S)
        assert service.talker is None and service.server is None
        assert told is not None and told.get("unserved") == "the service is stopping", told
    finally:
        answered.set()
        if closer.ident is not None:
            closer.join(DEADLINE_S)


def test_a_talk_start_that_fails_on_the_ticks_look_is_said_and_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passport signed while talk was off is read again by the tick, which then started talk
    with nothing round it, so a passport server that could not bind its port (taken by another
    program, or by a start already under way) raised out of the tick and ended the service. The
    journal says so now, and the next look starts talk. The server here refuses its first bind
    as a taken fixed port does."""
    from roboglot.onboard import service as onboard_service
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.passport.serve import PassportServer
    from roboglot.radio import NoRadio

    home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)
    refusals = ["address already in use"]

    class Taken(PassportServer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if refusals:
                raise OSError(refusals.pop())
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(onboard_service, "PassportServer", Taken)
    lines: list[str] = []
    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""),
        log=lines.append,
    )
    try:
        service.start()
        assert service.talker is None
        sign_again()
        service._seen = (None, None)  # the files' times as a look would find them changed
        service.tick()
        assert "talk could not start: address already in use" in lines
        assert service.talker is not None and service.server is not None
    finally:
        service.close()


def test_a_reload_whose_talk_could_not_start_answers_that_the_passport_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reload with a passport signed while talk was off, whose talk then could not start (a
    fixed port another program holds), answered as any other reload, and `roboglot onboard sign`
    told the person the running service served the passport with no passport server running.
    The answer says now that it is not served, and why, over the real control port."""
    from roboglot import cli
    from roboglot.onboard import service as onboard_service
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.passport.serve import PassportServer
    from roboglot.radio import NoRadio

    home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)

    class Taken(PassportServer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise OSError("address already in use")

    monkeypatch.setattr(onboard_service, "PassportServer", Taken)
    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host=""),
        log=lambda _line: None,
    )
    try:
        service.start()
        sign_again()
        told = cli._tell_service(home, {"op": "reload"})
        assert service.server is None and told is not None
        assert told == {
            "ev": "reloaded",
            "ok": True,
            "pins": 0,
            "unserved": "talk could not start: address already in use",
        }
    finally:
        service.close()


@pytest.mark.parametrize("when", ["at start", "when talk turns on later"])
def test_a_radio_that_does_not_answer_in_time_is_a_radio_down_and_the_service_stays_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    """Only a radio that could not be had was caught as the radio started, so one that took
    longer than its bound (BlueZ waiting on its controller at boot) raised out of the service's
    start once the Talker and the passport server were up: the service ended, and after five
    such starts systemd stopped restarting it. When talk turned on later instead, the journal
    said talk could not start while it had. The radio here answers after five times the
    shortened bound, as one waiting on its controller's command timeouts would."""
    import asyncio

    from roboglot.onboard import radio_side
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio

    monkeypatch.setattr(radio_side, "START_S", 0.2)
    said = "the radio did not answer within 0.2 s"

    class Slow(NoRadio):
        async def start(self, **kwargs: Any) -> Any:
            await asyncio.sleep(5 * radio_side.START_S)
            return await super().start(**kwargs)

    if when == "at start":
        home = robot(tmp_path, HOUSEHOLD)
    else:
        home, sign_again = talk_off_until_signed_again(tmp_path, monkeypatch)
    lines: list[str] = []
    service = Service(
        home,
        facts=sim_facts(),
        radio_side=lambda: RadioSide(
            Slow("off", "not started", kind="bluez"),
            lan=None,
            server_host="127.0.0.1",
            record_host="",
            log=lines.append,  # the service's journal, as `board_side` hands it on
        ),
        log=lines.append,
    )
    try:
        service.start()
        if when != "at start":
            assert service.talker is None
            sign_again()
            service._seen = (None, None)  # the files' times as a look would find them changed
            service.tick()
        assert service.talker is not None and service.server is not None
        # a look's radio starts on a thread of its own: its answer comes when it comes
        assert wait_for(lambda: f"radio bluez: off, {said}" in lines), lines
        assert not [line for line in lines if "talk could not start" in line], lines
        radio = service.status()["radio"]
        assert (radio["kind"], radio["state"], radio["detail"]) == ("bluez", "off", said)
    finally:
        service.close()


def test_a_radio_whose_own_start_times_out_is_a_radio_down_that_says_what_it_raised(
    tmp_path: Path,
) -> None:
    """A radio whose start raised a `TimeoutError` of its own, a D-Bus call's inside BlueZ, was
    read as one the start's bound had cut off, and the status and the journal took the error's
    words, which can be none: `radio bluez: off, ` and nothing after it. It says what the radio
    raised now, by name when it raised no words."""
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio

    class Own(NoRadio):
        async def start(self, **kwargs: Any) -> Any:
            raise TimeoutError

    lines: list[str] = []
    service = Service(
        robot(tmp_path, HOUSEHOLD),
        facts=sim_facts(),
        radio_side=lambda: RadioSide(
            Own("off", "not started", kind="bluez"),
            lan=None,
            server_host="127.0.0.1",
            record_host="",
            log=lines.append,
        ),
        log=lines.append,
    )
    try:
        service.start()
        assert service.status()["radio"]["detail"] == "TimeoutError()"
        assert "radio bluez: off, TimeoutError()" in lines
    finally:
        service.close()


def test_a_robot_file_that_stops_reading_is_named_in_status_and_the_service_runs_on(
    tmp_path: Path,
) -> None:
    """One byte that is not UTF-8 ended the service, and every restart after it."""
    air = air_for(tmp_path)
    household = robot(tmp_path, HOUSEHOLD)
    with running(household, SimPlace(air, STANDS, "none", speed=SPEED)) as service:
        text = household.robot_yaml.read_bytes()
        household.robot_yaml.write_bytes(text + "# caf\u00e9\n".encode("latin-1"))
        assert wait_for(lambda: service.file_problem is not None, 15)
        assert "not UTF-8" in str(service.status()["file_problem"])
        assert service.talker is not None


def test_the_advert_says_when_this_robot_is_at_a_limit() -> None:
    """Spec 5.6: the flag says a robot is at a link or session limit, and nothing ever set it."""
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.passport.beacon import read_ble_service_data
    from roboglot.passport.talk import MAX_SESSIONS
    from roboglot.radio import NoRadio

    adverts: list[bytes] = []

    class Radio(NoRadio):
        async def set_advert(self, advert: bytes) -> None:
            adverts.append(advert)

    side = RadioSide(Radio(), lan=None, server_host="127.0.0.1", record_host="127.0.0.1")
    side.loop.start()

    def busy(advert: bytes) -> bool:
        beacon = read_ble_service_data(advert)
        assert beacon is not None
        return beacon.busy

    try:
        assert not busy(side._advert())
        side.loop.call(side._tick(0.0), 5)
        assert adverts == [], "nothing changed, so nothing to send"
        side.links.max_links = 0
        side.loop.call(side._tick(1.0), 5)
        assert len(adverts) == 1 and busy(adverts[-1])
        side.loop.call(side._tick(2.0), 5)
        assert len(adverts) == 1, "said once"
        side.links.max_links = 4
        side.loop.call(side._tick(3.0), 5)
        assert len(adverts) == 2 and not busy(adverts[-1])
        sessions = dict.fromkeys(range(MAX_SESSIONS))
        side.talker = SimpleNamespace(endpoint=SimpleNamespace(sessions=sessions))
        side.loop.call(side._tick(4.0), 5)
        assert len(adverts) == 3 and busy(adverts[-1])
    finally:
        side.talker = None
        side.loop.stop()


def test_an_id_held_past_its_time_ends_what_held_it_and_only_then_changes() -> None:
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio
    from roboglot.radio.rotation import HOLD_S, RETRY_S

    # A clock that starts at nothing: on a real one the sum below can round to just under the
    # hour, and then the rotor rightly asks again after RETRY_S and this test says it never asked.
    side = RadioSide(
        NoRadio(),
        lan=None,
        server_host="127.0.0.1",
        record_host="127.0.0.1",
        clock=lambda: 0.0,
    )
    side.loop.start()
    parted: list[Any] = []
    side.meeter = SimpleNamespace(part_from_everyone=lambda then: parted.append(then))
    rotor, first = side.rotor, side.rotor.current
    try:
        side.busy_elsewhere = lambda: True
        assert rotor.tick(rotor.due_at) is None and parted == []
        assert rotor.tick(rotor.due_at + HOLD_S - RETRY_S) is None and parted == []
        assert rotor.tick(rotor.due_at + HOLD_S) is None
        assert parted == [rotor.rotate] and rotor.current == first, "ended first"
        side.busy_elsewhere = lambda: False
        parted.clear()
        assert rotor.tick(rotor.next_at) is not None
        assert parted == [], "nothing open: a rotation like any other"
    finally:
        side.meeter = None
        side.loop.stop()


def test_a_rotation_after_the_record_was_withdrawn_does_not_publish_it_again() -> None:
    """The service's close withdraws the LAN record first, and a rotation that came after, from
    the meeter parting from everyone and then rotating on a thread of its own, published the
    record again for a service that had gone: other robots found it until its time to live ran
    out. A record withdrawn for the close stays withdrawn."""
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio

    published: list[Any] = []

    class Lan:
        def publish(self, txt: Any, *, host: str, port: int) -> bool:
            published.append(txt)
            return True

        def withdraw(self) -> None:
            return None

        def lookup(self, beacon_id: str) -> Any:
            return None

    side = RadioSide(NoRadio(), lan=Lan(), server_host="127.0.0.1", record_host="127.0.0.1")
    side.loop.start()  # the rotation's new advert goes onto it
    try:
        side._txt = {"id": "abcd", "sha": "0" * 64, "bt": side.rotor.current}
        side.withdraw()
        side._rotated("0123456789abcdef")
        assert published == [], "the rotation published the withdrawn record again"
    finally:
        side.loop.stop()


def test_a_link_that_closes_is_told_to_the_talker_off_the_radios_loop() -> None:
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio

    side = RadioSide(NoRadio(), lan=None, server_host="127.0.0.1", record_host="127.0.0.1")
    closed: list[str] = []
    submitted: list[Any] = []

    def submit(call: Any, *args: Any) -> None:
        submitted.append(call)
        call(*args)

    side.talker = SimpleNamespace(link_closed=closed.append)
    side.meeter = SimpleNamespace(executor=SimpleNamespace(submit=submit))
    assert side.links.on_gone == side._link_gone
    side._link_gone("ble://abc")
    assert closed == ["ble://abc"] and submitted == [side.talker.link_closed]
    side.talker = None
    side.meeter = None


def test_an_advert_that_will_not_change_does_not_stop_the_tick() -> None:
    from roboglot.onboard.radio_side import RadioSide
    from roboglot.radio import NoRadio

    class Radio(NoRadio):
        async def set_advert(self, advert: bytes) -> None:
            raise OSError("bluetoothd went away")

    side = RadioSide(Radio(), lan=None, server_host="127.0.0.1", record_host="127.0.0.1")
    side.loop.start()
    try:
        side.links.max_links = 0
        assert side.loop.call(side._tick(0.0), 5) == []
    finally:
        side.loop.stop()


def test_a_run_grants_only_the_courtesies_the_signed_passport_offers(tmp_path: Path) -> None:
    """A robot Roboglot only talks for signs `courtesy: []`, and a run still granted a pause."""
    import asyncio

    home = robot(tmp_path, HOUSEHOLD)
    service = Service(home, facts=sim_facts(), radio_side=lambda: None, log=lambda _line: None)  # type: ignore[arg-type]
    service.talker = SimpleNamespace(courtesy=None)  # type: ignore[assignment]

    async def hold() -> None:
        service._holding(SimpleNamespace(executor=object()))

    asyncio.run(hold())
    granted = service.talker.courtesy
    assert service.check.reading is not None and service.check.reading.courtesy() == []
    assert granted.allowed("known") == () and granted.allowed("stranger") == ()
    assert granted.decide("known", "pause", {"seconds": 3}).result == "no"


def test_refusals_are_written_at_most_thirty_a_minute_and_rows_from_many_threads_all_kept(
    tmp_path: Path,
) -> None:
    home = robot(tmp_path, HOUSEHOLD)
    lines: list[str] = []
    service = Service(home, facts=sim_facts(), radio_side=lambda: None, log=lines.append)  # type: ignore[arg-type,return-value]
    for n in range(100):
        service.refused(f"ble://{n:08x}", "4.4: a test's")
    assert len(met(home)) == 30
    home.met_log.unlink()
    writers = [
        threading.Thread(
            target=lambda base=base: [
                service._append(home.met_log, {"n": base + i}) for i in range(100)
            ]
        )
        for base in range(0, 1000, 100)
    ]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()
    assert sorted(row["n"] for row in met(home)) == list(range(1000))


def test_a_meeting_keeps_no_notes_and_a_goal_does(tmp_path: Path) -> None:
    """A meeting's notes would be what a robot it met claimed, back in a later run as the robot's
    own word, so a meeting is offered no `remember`."""
    from roboglot.glotfile.parser import glot_from_goal
    from roboglot.onboard.meeting import meeting_glot

    home = robot(tmp_path, HOUSEHOLD)
    service = Service(home, facts=sim_facts(), radio_side=lambda: None, log=lambda _line: None)  # type: ignore[arg-type,return-value]
    meeting = service._make_loop(meeting_glot("say who you are"), "meeting", None)
    assert meeting.cfg.memory is None
    goal = service._make_loop(glot_from_goal("report your state", ["report_state"]), "goal", None)
    assert goal.cfg.memory is not None
