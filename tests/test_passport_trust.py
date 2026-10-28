"""Trust in Robot Passport (docs/passport/trust.md): signatures, stamps, pins and the four tiers.

Two quackd hosts are two registry directories here. Host A drives a robot and serves its signed
passport; host B meets it, the way two machines on one network would, and every tier is reached
the way a person reaches it: by stamping, by pinning, or by driving the robot themselves.
"""

from __future__ import annotations

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


def test_a_stamp_for_something_that_is_not_a_robot_makes_no_owner_key(
    hosts: tuple[Path, Path],
) -> None:
    a, _b = hosts
    for target in (["--did", "did:key:z1nope"], ["--robot", "no-such-robot"]):
        refused = runner.invoke(app, ["passport", "stamp", *target, *_reg(a)])
        assert refused.exit_code != 0, refused.output
    assert PassportStore(a).owner().key is None
