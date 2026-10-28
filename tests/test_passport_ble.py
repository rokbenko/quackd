"""The passport's Bluetooth advert: ten bytes of service data under a UUID of its own, the 31
bytes it makes on the air, and the rule that decides which of two robots dials.

The vectors in docs/passport/vectors/ are written by scripts/passport_vectors.py with `struct`
and `uuid` and nothing of quackd's, so the advert is checked against a second implementation."""

from __future__ import annotations

import json
import random
import re
import uuid
from pathlib import Path

import pytest

from quackd.passport import beacon
from quackd.passport.beacon import (
    BLE_RX_UUID,
    BLE_SERVICE_UUID,
    BLE_TX_UUID,
    FLAG_BUSY,
    FLAG_LAN,
    FLAG_LINK,
    LEGACY_AD_BYTES,
    SERVICE_DATA_BYTES,
    BleBeacon,
    advertising_data,
    ble_service_data,
    initiates,
    new_beacon_id,
    read_advertising_data,
    read_ble_service_data,
)

VECTORS = Path(__file__).resolve().parents[1] / "docs" / "passport" / "vectors"
LINK = json.loads((VECTORS / "ble-link.json").read_text(encoding="utf-8"))
#: The Bluetooth SIG's base UUID, under which every 16- and 32-bit UUID lives.
BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"


def test_the_uuids_are_the_recipe_and_outside_the_bluetooth_base_range() -> None:
    names = {"ble-service": BLE_SERVICE_UUID, "ble-rx": BLE_RX_UUID, "ble-tx": BLE_TX_UUID}
    for name, value in names.items():
        made = uuid.uuid5(uuid.NAMESPACE_URL, f"{beacon.SPEC_URL}#{name}")
        assert str(made) == value, name
        assert made.version == 5 and not value.endswith(BASE_SUFFIX)
    assert len(set(names.values())) == 3
    assert LINK["uuids"] == names


def test_the_advert_vector_is_what_this_module_puts_on_the_air() -> None:
    hexed = (VECTORS / LINK["advert"]["file"]).read_text(encoding="utf-8").strip()
    advert = LINK["advert"]
    assert advert["flags"] == ["LAN", "LINK"]
    made = advertising_data(ble_service_data(advert["beacon_id"], lan=True, link=True))
    assert made.hex() == hexed
    assert len(made) == LEGACY_AD_BYTES
    read = read_advertising_data(bytes.fromhex(hexed))
    assert read == BleBeacon(beacon.BLE_VERSION, FLAG_LAN | FLAG_LINK, advert["beacon_id"])
    assert read is not None and read.lan and read.link and not read.busy


@pytest.mark.parametrize("flags", range(8))
def test_service_data_round_trips_every_flag_and_is_ten_bytes(flags: int) -> None:
    beacon_id = new_beacon_id()
    data = ble_service_data(
        beacon_id,
        lan=bool(flags & FLAG_LAN),
        link=bool(flags & FLAG_LINK),
        busy=bool(flags & FLAG_BUSY),
    )
    assert len(data) == SERVICE_DATA_BYTES
    assert read_ble_service_data(data) == BleBeacon(beacon.BLE_VERSION, flags, beacon_id)
    assert read_advertising_data(advertising_data(data)) == read_ble_service_data(data)


def test_another_version_or_too_few_bytes_is_no_beacon_and_more_bytes_are_ignored() -> None:
    data = ble_service_data(new_beacon_id(), lan=False, link=True)
    assert read_ble_service_data(data[:-1]) is None
    assert read_ble_service_data(bytes((beacon.BLE_VERSION + 1,)) + data[1:]) is None
    assert read_ble_service_data(data + b"later") == read_ble_service_data(data)
    # flag bits this version does not define are sent 0 and read as if they were
    loud = data[:1] + bytes((data[1] | 0xF8,)) + data[2:]
    assert read_ble_service_data(loud) == read_ble_service_data(data)


def test_a_beacon_id_is_sixteen_lower_hex_characters_or_refused() -> None:
    for bad in ("0011223344556677aa", "AABBCCDDEEFF0011", "zz11223344556677", ""):
        with pytest.raises(ValueError, match="beacon id"):
            ble_service_data(bad, lan=True, link=True)


def test_new_beacon_ids_are_random_and_shaped_alike() -> None:
    drawn = [new_beacon_id() for _ in range(1000)]
    assert all(re.fullmatch(r"[0-9a-f]{16}", d) for d in drawn)
    assert len(set(drawn)) == len(drawn)


def test_an_advert_is_found_among_other_structures_and_nowhere_else() -> None:
    data = ble_service_data(new_beacon_id(), lan=True, link=False)
    ours = advertising_data(data)
    name = bytes((5, 0x09)) + b"duck"  # a complete local name, which a passport never sends
    other_uuid = uuid.uuid4().bytes[::-1]
    elsewhere = bytes((1 + 16 + len(data), 0x21)) + other_uuid + data
    assert read_advertising_data(name + ours[3:]) == read_ble_service_data(data)
    assert read_advertising_data(elsewhere) is None
    assert read_advertising_data(ours[:-1]) is None  # a structure that runs past the end
    assert read_advertising_data(b"\x00" + ours) is None  # an early end before ours
    assert read_advertising_data(b"") is None
    assert read_advertising_data(ours + bytes(beacon.MAX_AD_BYTES)) is None


def test_an_advert_that_is_not_discoverable_says_so_in_its_flags() -> None:
    data = ble_service_data(new_beacon_id(), lan=True, link=True)
    assert advertising_data(data)[2] & 0x02
    assert not advertising_data(data, discoverable=False)[2] & 0x02
    with pytest.raises(ValueError):
        advertising_data(data[:-1])


def test_no_advert_at_all_makes_the_reader_raise_or_read_outside_it() -> None:
    rng = random.Random(56)
    for _ in range(5000):
        noise = bytes(rng.randrange(256) for _ in range(rng.randrange(40)))
        result = read_advertising_data(noise)
        assert result is None or isinstance(result, BleBeacon)


def test_exactly_one_of_two_robots_dials_and_neither_dials_itself() -> None:
    for _ in range(500):
        a, b = new_beacon_id(), new_beacon_id()
        assert initiates(a, b) != initiates(b, a)
        assert initiates(a, b) == (int(a, 16) < int(b, 16))
    same = new_beacon_id()
    assert not initiates(same, same)
    # the vectors' link is dialled by B, whose id is the lower
    opened = next(f for f in LINK["frames"] if f["kind"] == "OPEN")
    assert initiates(opened["beacon_id"], LINK["advert"]["beacon_id"])
