"""`PassportStore`: robots' keys and what their owner said, under the registry directory."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from quackd.passport_store import OwnerRecord, PassportStore, StoreError


def test_a_robot_gets_a_key_once_and_keeps_it(tmp_path: Path) -> None:
    store = PassportStore(tmp_path)
    first, created = store.robot_or_new("duck-01")
    again, created_again = store.robot_or_new("duck-01")
    assert created and not created_again
    assert first.key.did == again.key.did
    assert store.robot_path("duck-01") == tmp_path / "keys" / "robots" / "duck-01.json"


@pytest.mark.skipif(sys.platform == "win32", reason="a mode is a no-op on Windows")
def test_a_key_file_is_readable_by_its_owner_alone(tmp_path: Path) -> None:
    store = PassportStore(tmp_path)
    store.robot_or_new("duck-01")
    mode = stat.S_IMODE(os.stat(store.robot_path("duck-01")).st_mode)
    assert mode == 0o600


def test_what_the_owner_said_about_stopping_it_survives_a_reload(tmp_path: Path) -> None:
    store = PassportStore(tmp_path)
    record, _ = store.robot_or_new("arm")
    record.estop = "button"
    record.how_to_stop = "the red button on the base"
    store.save_robot("arm", record)
    reloaded = PassportStore(tmp_path).robot("arm")
    assert reloaded is not None
    assert (reloaded.estop, reloaded.how_to_stop) == ("button", "the red button on the base")
    assert reloaded.key.seed == record.key.seed


def test_a_name_that_could_climb_out_of_the_directory_is_refused(tmp_path: Path) -> None:
    store = PassportStore(tmp_path)
    for name in ("../escape", "a/b", "", "UPPER", "microduck:mock"):
        with pytest.raises(StoreError):
            store.robot_path(name)


def test_a_key_file_whose_did_and_seed_disagree_is_refused(tmp_path: Path) -> None:
    store = PassportStore(tmp_path)
    store.robot_or_new("duck-01")
    path = store.robot_path("duck-01")
    path.write_text(
        path.read_text(encoding="utf-8").replace('"did": "did:key:z6Mk', '"did": "did:key:z6Mz'),
        encoding="utf-8",
    )
    with pytest.raises(StoreError, match="did"):
        store.robot("duck-01")


def test_the_owner_is_optional_and_an_empty_value_removes_it(tmp_path: Path) -> None:
    store = PassportStore(tmp_path)
    assert store.owner() == OwnerRecord()
    store.save_owner(OwnerRecord("Example lab", "https://example.org"))
    assert store.owner() == OwnerRecord("Example lab", "https://example.org")
    store.save_owner(OwnerRecord("Example lab", None))
    assert store.owner() == OwnerRecord("Example lab", None)


def test_forget_removes_nothing_when_more_than_one_pin_answers(tmp_path: Path) -> None:
    """Two owners can share a label, and a robot can name itself after another's short id, so
    a name or short id more than one pin answers to removes none until a person names one."""
    from quackd.passport import SigningKey
    from quackd.passport_store import Pin, StoreError

    store = PassportStore(tmp_path)
    one, two, three = SigningKey.generate(), SigningKey.generate(), SigningKey.generate()
    store.pin(Pin(one.did, "Example lab", "owner", "2026-10-21T00:00:00Z"))
    store.pin(Pin(two.did, "Example lab", "owner", "2026-10-21T00:00:00Z"))
    store.pin(Pin(three.did, two.short_id, "robot", "2026-10-21T00:00:00Z"))
    with pytest.raises(StoreError, match="2 pins answer"):
        store.forget("Example lab")
    with pytest.raises(StoreError, match="2 pins answer"):
        store.forget(two.short_id)
    assert len(store.known()) == 3
    assert [p.id for p in store.forget(one.short_id)] == [one.did]
    assert [p.id for p in store.forget(two.did)] == [two.did]
    assert [p.id for p in store.known()] == [three.did]


def test_a_key_that_cannot_be_written_is_a_store_error_not_a_traceback(tmp_path: Path) -> None:
    from quackd.passport_store import StoreError

    (tmp_path / "keys").write_text("a file where a folder belongs", encoding="utf-8")
    with pytest.raises(StoreError, match="could not write"):
        PassportStore(tmp_path).robot_or_new("duck-01")
