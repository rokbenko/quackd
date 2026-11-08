"""The real backend over LeRobot 0.7.0's own follower and bus, with rustypot's `Bus` faked.

Every other test of `lerobot:real` drives a fake arm that copies LeRobot's words, and a copy is
only as good as its last reading: quackd read 0.6.1's `id_=<N>`, which LeRobot's main stopped
saying on 2026-10-16 and 0.7.0 says nowhere, and over 0.7.0's bus it would have named no joint.
Here the follower, its bus, the handshake, the calibration check, `configure()`, the torque calls
and every error LeRobot raises are LeRobot's own code, with each motor's definition out of
rustypot's wheel. The one thing replaced is `rustypot.Bus`, the object that opens the serial port
and speaks the protocol, by `FakePort`, which answers from a table of registers and fails where a
test says, in rustypot's words copied from its source: `Operation timed out` for a reply that
never came, raised as rustypot's Python bus raises a bus failure, as a `RuntimeError`, and a sync
read's failure as `motor <M>: ` and why. rustypot's protocol, its retries, its flush and its
timeouts are not run, and `FakePort` takes a `retries` and ignores it, so a reply it is told to
lose is lost on every try.

No serial port is opened. `SerialMotorsBus._open_bus` is replaced for every test here, and
`rustypot.Bus` itself refuses to be built, so a path that reached for a port would fail the test
rather than open one. The port is `COM250`, which no machine has.

These need LeRobot 0.7.0 with its `[feetech]` extra, which brings rustypot, and torch under it, so
they run in CI's policy job and skip anywhere else (QUACKD_REQUIRE_LEROBOT_BUS makes a skip a
failure, as CI sets it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from quackd.transport.base import TransportError
from quackd_lerobot.real import (
    CONNECT_ATTEMPTS,
    PORT_CLOSED_BY_THE_HANDSHAKE,
    PORT_CLOSED_WRITING_NOTHING,
    PORT_NEVER_OPENED,
    SPLIT_TORQUE,
    TORQUE_RETRIES,
    LeRobotReal,
    motor_in_error,
)

if os.environ.get("QUACKD_REQUIRE_LEROBOT_BUS"):
    import lerobot  # noqa: F401
    import rustypot
else:
    pytest.importorskip("lerobot")
    rustypot = pytest.importorskip("rustypot")

from lerobot.motors import SerialMotorsBus
from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

PORT = "COM250"
"""A port no machine has, as a second guard behind the forbidden `rustypot.Bus`."""
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
NO_REPLY = "Operation timed out"
"""serialport-rs's words for a reply that never came, which rustypot passes on."""
LOW, HIGH = 1024, 3072
"""Every motor's calibrated travel, in ticks: 180 degrees, centred on 2048."""


class FakePort:
    """rustypot's Bus with the serial port taken away: each motor's registers in a table, the
    alarms each answers with, and the faults a test plants, answered through rustypot's own
    method names and signatures (`rustypot.Bus` in its wheel).

    `silent` motors answer nothing, as a motor with its cable out does: a read or a write of one
    raises rustypot's bus failure, a sync read stops at the first of them, `motor <M>: ` and why,
    as rustypot's SyncReadError reads (dynamixel_protocol/mod.rs lines 997 to 1001 at its v1.13.1
    tag), and a torque call tries every motor and hands back each that failed
    (`set_torque`, bus.rs lines 229 to 255). `deaf` motors answer reads and get no reply to a
    write, which does not take. `lost` is a write whose reply is lost, by motor, register and
    which write of that register it is, counted from 1, and that write takes effect anyway, as a
    reply lost on the way back does."""

    def __init__(self, ids: list[int]) -> None:
        self.registers: dict[int, dict[str, int]] = {n: _registers(n) for n in ids}
        self.alarms: dict[int, int] = {}
        self.silent: set[int] = set()
        self.deaf: set[int] = set()
        self.lost: set[tuple[int, str, int]] = set()
        self.writes: list[tuple[int, str, int]] = []
        self.open = True

    def _alive(self) -> None:
        if not self.open:
            raise RuntimeError("bus is closed: its serial port has been released")

    def read_register_with_error(self, id: int, name: str, retries: int = 0) -> tuple[int, int]:
        self._alive()
        if id in self.silent:
            raise RuntimeError(NO_REPLY)
        return self.registers[id][name], self.alarms.get(id, 0)

    def write_register_with_error(self, id: int, name: str, value: int, retries: int = 0) -> int:
        self._alive()
        if id in self.silent or id in self.deaf:
            raise RuntimeError(NO_REPLY)
        self.registers[id][name] = int(value)
        self.writes.append((id, name, int(value)))
        nth = sum(1 for n, written, _ in self.writes if (n, written) == (id, name))
        if (id, name, nth) in self.lost:
            raise RuntimeError(NO_REPLY)
        return self.alarms.get(id, 0)

    def sync_read_register_with_error(
        self, ids: list[int], name: str, retries: int = 0
    ) -> list[tuple[int, int]]:
        self._alive()
        for n in ids:
            if n in self.silent:
                raise RuntimeError(f"motor {n}: {NO_REPLY}")
        return [(self.registers[n][name], self.alarms.get(n, 0)) for n in ids]

    def sync_write_register(
        self, ids: list[int], name: str, values: list[int], retries: int = 0
    ) -> None:
        self._alive()
        for n, value in zip(ids, values, strict=True):
            if n not in self.silent:
                self.registers[n][name] = int(value)

    def set_torque(self, ids: list[int], enabled: bool, retries: int = 0) -> dict[int, str]:
        self._alive()
        failed: dict[int, str] = {}
        for n in ids:
            try:
                for name in ("torque_enable", "lock"):
                    self.write_register_with_error(n, name, int(enabled))
            except RuntimeError as e:
                failed[n] = str(e)
        return dict(sorted(failed.items()))

    def close(self) -> None:
        self.open = False


def _registers(motor_id: int) -> dict[str, int]:
    """What an sts3215 at the middle of its travel holds, under the names LeRobot asks for."""
    return {
        "model_number": 777,
        "firmware_major_version": 3,
        "firmware_minor_version": 10,
        "min_position_limit": LOW,
        "max_position_limit": HIGH,
        "homing_offset": 0,
        "present_position": (LOW + HIGH) // 2,
        "goal_position": (LOW + HIGH) // 2,
        "torque_enable": 1,
        "lock": 1,
        "present_temperature": 31,
        "phase": 0,
        "return_delay_time": 250,
        "maximum_acceleration": 0,
        "acceleration": 0,
        "operating_mode": 0,
        "p_coefficient": 32,
        "i_coefficient": 0,
        "d_coefficient": 32,
        "max_torque_limit": 1000,
        "protection_current": 500,
        "overload_torque": 80,
    }


@pytest.fixture
def port(monkeypatch: pytest.MonkeyPatch) -> FakePort:
    """The fake port every follower here opens, and `rustypot.Bus` refusing to be built."""
    fake = FakePort(list(range(1, 7)))

    def no_port(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a test reached for a real serial port")

    def opened(self: Any) -> FakePort:
        # a new Bus over the same motors, as each connect opens one
        fake.open = True
        return fake

    monkeypatch.setattr(rustypot, "Bus", no_port)
    monkeypatch.setattr(SerialMotorsBus, "_open_bus", opened)
    return fake


@pytest.fixture
def arm(tmp_path: Path) -> SO101Follower:
    """LeRobot's own SO-101 follower, built with quackd's config (`_config_kwargs`) and a
    calibration file of its own, that every motor's registers agree with."""
    folder = tmp_path / "calibration"
    folder.mkdir()
    entries = {
        joint: {
            "id": n,
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": LOW,
            "range_max": HIGH,
        }
        for n, joint in enumerate(JOINTS, start=1)
    }
    (folder / "arm-01.json").write_text(json.dumps(entries), encoding="utf-8")
    kwargs = LeRobotReal(PORT, robot_id="arm-01")._config_kwargs()
    return SO101Follower(SO101FollowerConfig(**kwargs, calibration_dir=folder))


def _transport(arm: SO101Follower) -> LeRobotReal:
    transport = LeRobotReal(PORT, robot=arm, robot_id="arm-01")
    transport.connect_pause_s = 0.0
    return transport


async def test_a_clean_connect_reads_the_arm_and_a_close_keeps_its_torque(
    port: FakePort, arm: SO101Follower
) -> None:
    """The whole of a connect through LeRobot's code: the handshake, the calibration check
    against the file, `configure()`'s writes, and the first reads. With no rest pose the close
    keeps torque and closes the port, writing nothing more."""
    transport = _transport(arm)
    await transport.connect()
    assert transport.connect_notes == []
    assert transport.joint_range_deg["shoulder_pan"] == pytest.approx((-90.02, 90.02), abs=0.01)
    state = await transport.get_state()
    assert state.extras["torque"] is True and state.extras["temperature_c"]["gripper"] == 31
    written = {name for _, name, _ in port.writes}
    assert {"torque_enable", "lock", "return_delay_time", "p_coefficient"} <= written
    await transport.close()
    assert not port.open and not arm.is_connected
    assert all(port.registers[n]["torque_enable"] == 1 for n in port.registers)


@pytest.mark.parametrize(
    ("nth", "verb", "left"),
    [
        pytest.param(1, "disable", [0, 0, 0, 0, 0, 0], id="on the way in"),
        pytest.param(2, "enable", [1, 1, 1, 1, 1, 1], id="on the way back on"),
    ],
)
async def test_a_lost_lock_reply_in_configure_names_the_joint_and_connects_again(
    port: FakePort, arm: SO101Follower, nth: int, verb: str, left: list[int]
) -> None:
    """A `Lock` reply lost as `configure()` switches torque off or back on is LeRobot's one torque
    error, naming the motor (motors_bus.py lines 642 to 648), and every motor was tried. Lost on
    the way in it leaves every motor limp, since the write itself went through and nothing switches
    them back on before the raise (lines 701 to 705); lost on the way back on it leaves them all
    holding. Either way the attempt names elbow_flex, the port is closed with no write, and the
    next attempt connects, as `configure()` switches every motor back on."""
    port.lost.add((3, "lock", nth))
    transport = _transport(arm)
    seen: list[list[int]] = []
    close = transport._close_port

    async def closing() -> None:
        seen.append([port.registers[n]["torque_enable"] for n in range(1, 7)])
        await close()

    transport._close_port = closing  # type: ignore[method-assign]
    await transport.connect()
    (note,) = transport.connect_notes
    assert note == (
        f"connect attempt 1 of {CONNECT_ATTEMPTS} failed on elbow_flex (id 3): Failed to {verb} "
        f"torque after 1 tries on 'elbow_flex' (motor ID 3): {NO_REPLY}. "
        f"{PORT_CLOSED_WRITING_NOTHING}, and connect runs again"
    ), note
    assert seen == [left], "the motors were not left as LeRobot's torque calls leave them"
    assert all(port.registers[n]["torque_enable"] == 1 for n in range(1, 7))
    await transport.close()


async def test_a_silent_servo_is_named_by_the_handshake_which_shuts_the_port(
    port: FakePort, arm: SO101Follower
) -> None:
    """A servo that answers nothing is missing from LeRobot's handshake, which closes the port
    behind it (motors_bus.py lines 424 to 430): the refusal names wrist_roll, warns of no torque,
    and every note says LeRobot closed the port, which it did, with its reads already out."""
    port.silent.add(5)
    transport = _transport(arm)
    with pytest.raises(TransportError) as raised:
        await transport.connect()
    why = str(raised.value)
    assert why.startswith(
        f"lerobot real: connect failed {CONNECT_ATTEMPTS} times, the last on wrist_roll (id 5): "
        f"SerialMotorsBus motor check failed on port '{PORT}': Missing motor IDs: - 5 "
    ), why
    assert SPLIT_TORQUE not in why, why
    assert "Check wrist_roll's cable and connectors, that the servo supply is on" in why, why
    assert len(transport.connect_notes) == CONNECT_ATTEMPTS - 1
    for note in transport.connect_notes:
        assert f" {PORT_CLOSED_BY_THE_HANDSHAKE}, and connect runs again" in note, note
    assert not port.open and port.writes == [], "the handshake wrote to a motor"


async def test_an_overloaded_servo_answers_and_is_not_missing(
    port: FakePort, arm: SO101Follower
) -> None:
    """An sts3215's status byte is alarms only (rustypot's sts3215.rs line 19), so a servo
    answering with its overload bit set is found by the handshake and raises nothing anywhere,
    where 0.6.1 listed it as missing. LeRobot surfaces no alarm, which is why quackd's own
    temperature read is its one heat guard."""
    port.alarms[4] = 0x20
    transport = _transport(arm)
    await transport.connect()
    assert transport.connect_notes == []
    await transport.close()


async def test_a_reply_lost_in_the_calibration_check_names_the_first_silent_joint(
    port: FakePort, arm: SO101Follower, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The calibration check reads every motor's limits in sync reads after the handshake, and
    a reply lost there names the first motor that did not answer (motors_bus.py lines 1236 to
    1239). It writes nothing, so the refusal does not warn about torque."""
    transport = _transport(arm)
    calls = {"n": 0}
    real = port.sync_read_register_with_error

    def loses_its_limits(ids: list[int], name: str, retries: int = 0) -> list[tuple[int, int]]:
        if name == "min_position_limit":
            calls["n"] += 1
            raise RuntimeError(f"motor 4: {NO_REPLY}")
        return real(ids, name, retries)

    monkeypatch.setattr(port, "sync_read_register_with_error", loses_its_limits)
    with pytest.raises(TransportError) as raised:
        await transport.connect()
    why = str(raised.value)
    assert why.startswith(
        f"lerobot real: connect failed {CONNECT_ATTEMPTS} times, the last on wrist_flex (id 4), "
        "the first that did not answer: Failed to sync read 'Min_Position_Limit' from motor IDs "
        f"[1, 2, 3, 4, 5, 6] after 1 tries. motor 4: {NO_REPLY}"
    ), why
    assert SPLIT_TORQUE not in why, why
    assert calls["n"] == CONNECT_ATTEMPTS
    assert port.writes == [], "the calibration check wrote to a motor"
    for note in transport.connect_notes:
        assert f" {PORT_CLOSED_WRITING_NOTHING}, and connect runs again" in note, note


async def test_a_port_another_program_holds_never_opened(
    arm: SO101Follower, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rustypot opens the port exclusively, so one another program holds for itself fails in its
    open with an OSError, which LeRobot makes its port error (motors_bus.py lines 417 to 421).
    Nothing reached a motor, every note says so, and the refusal names no joint. The OSError's
    words are synthetic."""

    def held(self: Any) -> Any:
        raise OSError("the port is held by another program (a synthetic error)")

    monkeypatch.setattr(rustypot, "Bus", held)
    monkeypatch.setattr(SerialMotorsBus, "_open_bus", held)
    transport = _transport(arm)
    with pytest.raises(TransportError) as raised:
        await transport.connect()
    why = str(raised.value)
    assert why.startswith(
        f"lerobot real: connect failed {CONNECT_ATTEMPTS} times: Could not connect on port "
        f"'{PORT}'. Make sure you are using the correct port."
    ), why
    assert "Check the arm's cables and power, and that nothing else has COM250 open" in why, why
    for note in transport.connect_notes:
        assert f" {PORT_NEVER_OPENED}, and connect runs again" in note, note


async def test_a_release_names_the_joint_that_may_still_hold(
    port: FakePort, arm: SO101Follower
) -> None:
    """A release over a motor that stops answering tries every motor and names that one
    (motors_bus.py lines 642 to 648, num_retry 5 a motor): every other joint let go, which
    LeRobot's acknowledged writes say, and the person is told which may still hold."""
    transport = _transport(arm)
    await transport.connect()
    port.deaf.add(6)
    released = await transport.let_go(anywhere=True)
    assert released.how == "released", released
    assert released.reason.startswith(
        "torque was taken off where the arm stands on every joint but gripper, whose write "
        f"LeRobot could not confirm (ConnectionError: Failed to disable torque after "
        f"{TORQUE_RETRIES + 1} tries on 'gripper' (motor ID 6): {NO_REPLY}.), so it may still "
        "hold: hold the arm as though nothing holds it"
    ), released.reason
    assert [port.registers[n]["torque_enable"] for n in range(1, 7)] == [0, 0, 0, 0, 0, 1]
    port.deaf.clear()
    await transport.close()


async def test_a_disconnect_whose_torque_off_raised_still_releases_the_port(
    port: FakePort, arm: SO101Follower
) -> None:
    """LeRobot's bus disconnect closes the port in a `finally` whatever its torque-off did, and
    passes over a port already shut without a word (motors_bus.py lines 440 to 461), so quackd's
    close between attempts never raises on one. Driven straight through the follower and the bus
    here, as LeRobot's own disconnect is, and its error names the motor the way quackd reads it."""
    arm.connect(False)
    port.silent.add(2)
    with pytest.raises(ConnectionError) as raised:
        arm.bus.disconnect(disable_torque=True)
    assert str(raised.value) == (
        f"Failed to disable torque after 6 tries on 'shoulder_lift' (motor ID 2): {NO_REPLY}."
    )
    assert not port.open and not arm.bus.is_connected
    assert [port.registers[n]["torque_enable"] for n in range(1, 7)] == [0, 1, 0, 0, 0, 0]
    arm.bus.disconnect(disable_torque=True)  # shut already: nothing raised, nothing written
    said = str(raised.value)
    assert motor_in_error(said, arm.bus.motors) == ("shoulder_lift (id 2)", "shoulder_lift")
