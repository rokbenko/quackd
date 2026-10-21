"""Trust in Robot Passport (docs/passport/trust.md): signatures, stamps, pins and the four tiers.

Two quackd hosts are two registry directories here. Host A drives a robot and serves its signed
passport; host B meets it, the way two machines on one network would, and every tier is reached
the way a person reaches it: by stamping, by pinning, or by driving the robot themselves.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.cli import app
from quackd.passport import SigningKey, document, stamps, strict
from quackd.passport.serve import PassportServer, Served
from quackd.passport_store import PassportStore, Pin
from quackd.passport_trust import judge

runner = CliRunner()
NOW = "2026-10-21T00:00:00Z"


def _flat(output: str) -> str:
    return " ".join(output.split())


def _reg(path: Path) -> list[str]:
    return ["--registry-dir", str(path)]


def _signed(key: SigningKey, name: str, **members: Any) -> str:
    return document.sign({"passport": "0.1", "id": key.did, "name": name, **members}, key)


@pytest.fixture
def hosts(tmp_path: Path) -> tuple[Path, Path]:
    a, b = tmp_path / "host-a", tmp_path / "host-b"
    a.mkdir()
    b.mkdir()
    return a, b


@pytest.fixture
def served_by_a(hosts: tuple[Path, Path]) -> Iterator[tuple[str, Path]]:
    """Host A's duck, stamped by A's owner, served the way `quackd passport serve` serves it."""
    a, _b = hosts
    assert runner.invoke(app, ["passport", "owner", "--label", "Lab A", *_reg(a)]).exit_code == 0
    stamped = runner.invoke(app, ["passport", "stamp", "--robot", "microduck:mock", *_reg(a)])
    assert stamped.exit_code == 0, stamped.output
    raw = runner.invoke(app, ["passport", "show", "--robot", "microduck:mock", "--json", *_reg(a)])
    token = runner.invoke(app, ["passport", "show", "--robot", "microduck:mock", "--jws", *_reg(a)])
    server = PassportServer(
        [Served("microduck-mock", raw.stdout.strip().encode(), token.stdout.strip())],
        host="127.0.0.1",
    ).start()
    try:
        yield f"127.0.0.1:{server.port}", a
    finally:
        server.close()


# ── the tiers, through the commands ─────────────────────────────────────────────────────


def test_a_robot_quackd_drives_is_signed_and_in_its_own_flock(hosts: tuple[Path, Path]) -> None:
    a, _b = hosts
    shown = runner.invoke(app, ["passport", "show", "--robot", "microduck:mock", *_reg(a)])
    assert shown.exit_code == 0, shown.output
    flat = _flat(shown.output)
    assert "signed by its own key" in flat and "flock: one of the robots this quackd drives" in flat


def test_a_stranger_becomes_known_once_pinned_and_a_stranger_again_once_forgotten(
    served_by_a: tuple[str, Path], hosts: tuple[Path, Path]
) -> None:
    where, _a = served_by_a
    _a, b = hosts
    first = runner.invoke(app, ["meet", where, *_reg(b)])
    assert first.exit_code == 0, first.output
    assert "stranger: signed by a key nobody here pinned" in _flat(first.output)
    assert "claim signed by its own key" in _flat(first.output)
    assert "a key nobody here pinned, until" in _flat(first.output)
    pinned = runner.invoke(app, ["meet", where, "--pin", "--yes", *_reg(b)])
    assert pinned.exit_code == 0, pinned.output
    assert "pinned robot microduck-mock" in _flat(pinned.output)
    again = runner.invoke(app, ["meet", where, *_reg(b)])
    assert "known: pinned on" in _flat(again.output)
    listed = runner.invoke(app, ["passport", "known", "--json", *_reg(b)])
    assert json.loads(listed.stdout)["kind"] == "robot"
    forgot = runner.invoke(app, ["passport", "forget", "microduck-mock", *_reg(b)])
    assert forgot.exit_code == 0 and "forgot robot microduck-mock" in _flat(forgot.output)
    after = runner.invoke(app, ["meet", where, *_reg(b)])
    assert "stranger" in _flat(after.output)


def test_pinning_an_owner_trusts_every_robot_it_stamps(
    served_by_a: tuple[str, Path], hosts: tuple[Path, Path]
) -> None:
    where, a = served_by_a
    _a, b = hosts
    pinned = runner.invoke(app, ["meet", where, "--pin-owner", "--yes", *_reg(b)])
    assert pinned.exit_code == 0, pinned.output
    # the owner by its key, and its label as the robot's word for it, since no owner signs it
    assert "pinned owner key" in _flat(pinned.output)
    assert "which microduck-mock calls Lab A" in _flat(pinned.output)
    # a second robot of host A's, which host B has never met, is the same owner's
    stamped = runner.invoke(app, ["passport", "stamp", "--robot", "lerobot:mock", *_reg(a)])
    assert stamped.exit_code == 0, stamped.output
    arm = runner.invoke(app, ["passport", "show", "--robot", "lerobot:mock", "--jws", *_reg(a)])
    reading = document.read_jws(arm.stdout.strip())
    assert judge(reading, PassportStore(b)).tier == "same owner"
    assert judge(reading, PassportStore(b)).why == "stamped by Lab A, an owner you pinned"


def test_a_robot_stamped_by_your_own_owner_key_is_the_same_owners(hosts: tuple[Path, Path]) -> None:
    _a, b = hosts
    elsewhere = SigningKey.generate()
    stamp = runner.invoke(app, ["passport", "stamp", "--did", elsewhere.did, *_reg(b)])
    assert stamp.exit_code == 0, stamp.output
    token = _signed(elsewhere, "cart-01", stamps=[stamp.stdout.strip()])
    judgement = judge(document.read_jws(token), PassportStore(b))
    assert (judgement.tier, judgement.why) == ("same owner", "stamped by your own owner key")


def test_restamping_replaces_your_old_stamp_rather_than_adding_one(
    hosts: tuple[Path, Path],
) -> None:
    a, _b = hosts
    for _ in range(2):
        assert (
            runner.invoke(
                app, ["passport", "stamp", "--robot", "microduck:mock", *_reg(a)]
            ).exit_code
            == 0
        )
    raw = runner.invoke(app, ["passport", "show", "--robot", "microduck:mock", "--json", *_reg(a)])
    assert len(json.loads(raw.stdout)["stamps"]) == 1


def test_stamping_says_to_back_up_the_owner_key_the_first_time_only(
    hosts: tuple[Path, Path],
) -> None:
    a, _b = hosts
    first = runner.invoke(app, ["passport", "stamp", "--robot", "microduck:mock", *_reg(a)])
    assert "back it up" in _flat(first.output)
    second = runner.invoke(app, ["passport", "stamp", "--robot", "microduck:mock", *_reg(a)])
    assert "back it up" not in _flat(second.output)
    owner = runner.invoke(app, ["passport", "owner", *_reg(a)])
    assert PassportStore(a).owner().key is not None
    assert PassportStore(a).owner().key.short_id in owner.output  # type: ignore[union-attr]


def test_an_unsigned_passport_cannot_be_pinned(hosts: tuple[Path, Path]) -> None:
    _a, b = hosts
    raw = strict.dumps({"passport": "0.1", "id": SigningKey.generate().did, "name": "x"})
    server = PassportServer([Served("x", raw)], host="127.0.0.1").start()
    try:
        refused = runner.invoke(
            app, ["meet", f"127.0.0.1:{server.port}", "--pin", "--yes", *_reg(b)]
        )
    finally:
        server.close()
    assert refused.exit_code == 1
    assert "unsigned passport cannot be pinned" in _flat(refused.output)


# ── the judgement itself ────────────────────────────────────────────────────────────────


def test_an_unsigned_passport_naming_a_pinned_id_is_still_a_stranger(
    hosts: tuple[Path, Path],
) -> None:
    _a, b = hosts
    robot = SigningKey.generate()
    store = PassportStore(b)
    store.pin(Pin(robot.did, "duck-01", "robot", NOW))
    copy = document.read_json(strict.dumps({"passport": "0.1", "id": robot.did, "name": "duck-01"}))
    judgement = judge(copy, store)
    assert judgement.tier == "stranger"
    assert any("anybody can copy an id" in w for w in judgement.warnings)


def test_a_pinned_name_with_another_key_is_warned_about_like_ssh(hosts: tuple[Path, Path]) -> None:
    _a, b = hosts
    store = PassportStore(b)
    store.pin(Pin(SigningKey.generate().did, "duck-01", "robot", NOW))
    impostor = SigningKey.generate()
    judgement = judge(document.read_jws(_signed(impostor, "duck-01")), store)
    assert judgement.tier == "stranger"
    assert any("pinned with another key" in w for w in judgement.warnings)


def test_a_stamp_from_an_owner_nobody_pinned_raises_nothing(hosts: tuple[Path, Path]) -> None:
    _a, b = hosts
    robot, owner = SigningKey.generate(), SigningKey.generate()
    token = _signed(robot, "cart-01", stamps=[stamps.make(owner, robot.did)])
    assert judge(document.read_jws(token), PassportStore(b)).tier == "stranger"


def test_meet_prefers_the_signed_passport_and_falls_back_to_json(hosts: tuple[Path, Path]) -> None:
    _a, b = hosts
    robot = SigningKey.generate()
    doc = {"passport": "0.1", "id": robot.did, "name": "cart-01"}
    raw = strict.dumps(doc)
    with_jws = PassportServer([Served("cart-01", raw, document.sign(doc, robot))], host="127.0.0.1")
    without = PassportServer([Served("cart-01", raw)], host="127.0.0.1")
    with_jws.start()
    without.start()
    try:
        signed = runner.invoke(app, ["meet", f"127.0.0.1:{with_jws.port}", "--json", *_reg(b)])
        plain = runner.invoke(app, ["meet", f"127.0.0.1:{without.port}", "--json", *_reg(b)])
    finally:
        with_jws.close()
        without.close()
    assert json.loads(signed.stdout)["signed"] is True
    assert json.loads(plain.stdout)["signed"] is False


# ── names that look like ones this quackd knows ─────────────────────────────────────────


@pytest.mark.parametrize(
    "lookalike",
    [
        "duck-01\N{ZERO WIDTH SPACE}",
        "du\N{CYRILLIC SMALL LETTER ES}k-01",
        "DUCK-01 ",
        "duck-0l",
    ],
)
def test_a_name_that_only_looks_like_a_pinned_ones_is_warned_about(
    hosts: tuple[Path, Path], lookalike: str
) -> None:
    _a, b = hosts
    store = PassportStore(b)
    store.pin(Pin(SigningKey.generate().did, "duck-01", "robot", NOW))
    judgement = judge(document.read_jws(_signed(SigningKey.generate(), lookalike)), store)
    assert judgement.tier == "stranger"
    assert any("pinned with another key" in w for w in judgement.warnings)


def test_a_robot_named_like_one_this_quackd_drives_is_warned_about(
    hosts: tuple[Path, Path],
) -> None:
    a, _b = hosts
    assert (
        runner.invoke(app, ["passport", "show", "--robot", "microduck:mock", *_reg(a)]).exit_code
        == 0
    )
    impostor = document.read_jws(_signed(SigningKey.generate(), "microduck-mock"))
    judgement = judge(impostor, PassportStore(a))
    assert judgement.tier == "stranger"
    assert any("this quackd drives a robot named microduck-mock" in w for w in judgement.warnings)


def test_an_owner_label_its_pinned_owner_did_not_stamp_is_warned_about(
    hosts: tuple[Path, Path],
) -> None:
    _a, b = hosts
    store = PassportStore(b)
    real_owner, other_owner, robot = (SigningKey.generate() for _ in range(3))
    store.pin(Pin(real_owner.did, "Example lab", "owner", NOW))
    claimed = _signed(
        robot,
        "cart-01",
        owner={"label": "Example lab"},
        stamps=[stamps.make(other_owner, robot.did)],
    )
    judgement = judge(document.read_jws(claimed), store)
    assert judgement.tier == "stranger"
    assert any(
        "an owner you pinned, and Example lab's key did not stamp it" in w
        for w in judgement.warnings
    )
    genuine = _signed(
        robot,
        "cart-01",
        owner={"label": "Example lab"},
        stamps=[stamps.make(real_owner, robot.did)],
    )
    assert judge(document.read_jws(genuine), store).warnings == ()


# ── stamping and pinning, as the reviews found them ─────────────────────────────────────


def test_a_stamp_is_shown_by_the_key_that_made_it(hosts: tuple[Path, Path]) -> None:
    a, _b = hosts
    assert (
        runner.invoke(app, ["passport", "stamp", "--robot", "microduck:mock", *_reg(a)]).exit_code
        == 0
    )
    shown = runner.invoke(app, ["passport", "show", "--robot", "microduck:mock", *_reg(a)])
    owner = PassportStore(a).owner().key
    assert owner is not None
    assert f"stamp owner {owner.short_id}, your own owner key, until" in _flat(shown.output)


def test_restamping_under_new_owner_keys_keeps_at_most_four_stamps_that_count(
    hosts: tuple[Path, Path],
) -> None:
    """As after a lost owner key: each stamp is made by a new one, and the passport still never
    carries more stamps than a reader accepts."""
    a, _b = hosts
    store = PassportStore(a)
    for _ in range(6):
        store.owner_path.unlink(missing_ok=True)
        stamped = runner.invoke(app, ["passport", "stamp", "--robot", "microduck:mock", *_reg(a)])
        assert stamped.exit_code == 0, stamped.output
    shown = runner.invoke(
        app, ["passport", "show", "--robot", "microduck:mock", "--json", *_reg(a)]
    )
    assert shown.exit_code == 0, shown.output
    assert len(json.loads(shown.stdout)["stamps"]) == 4


def test_a_stamp_for_something_that_is_not_a_robot_makes_no_owner_key(
    hosts: tuple[Path, Path],
) -> None:
    a, _b = hosts
    for target in (["--did", "did:key:z1nope"], ["--robot", "no-such-robot"]):
        refused = runner.invoke(app, ["passport", "stamp", *target, *_reg(a)])
        assert refused.exit_code != 0, refused.output
    assert PassportStore(a).owner().key is None


def test_meet_with_json_and_a_pin_leaves_stdout_the_json_alone(
    served_by_a: tuple[str, Path], hosts: tuple[Path, Path]
) -> None:
    where, _a = served_by_a
    _a, b = hosts
    met = runner.invoke(app, ["meet", where, "--json", "--pin", "--yes", *_reg(b)])
    assert met.exit_code == 0, met.output
    assert json.loads(met.stdout)["name"] == "microduck-mock"
