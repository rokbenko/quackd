"""What connecting each real backend takes, as the core catalogue says it for the page
`quackd ui`, held to the transport modules' own code: the page can never show a default address
or a token variable the transport does not use (`quackd.adapters.catalogue.Connect`)."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from quackd.adapters.catalogue import ADAPTERS, BY_NAME, Connect
from quackd.adapters.factory import _connect_of
from quackd.perception import is_simulated

BLOCKS = {info.name: info.connect for info in ADAPTERS}


def test_every_real_backend_of_the_ten_says_how_to_connect_it_and_no_simulator_does() -> None:
    for info in ADAPTERS:
        # a backend described on an adapter is bound code its make() refuses until somebody names
        # a date (ADR-0076): there is nothing to connect yet, so the page offers no fields for it
        refused = {b for b, tier in info.tiers.items() if tier.name == "described"}
        real = sorted(b for b in info.backends if not is_simulated(b) and b not in refused)
        assert sorted(info.connect) == real, info.name
        # ADR-0076: a backend with a connect block is one that reaches a robot, which is what
        # the bound and verified tiers mean, so the two lists are one list
        reaching = sorted(b for b, tier in info.tiers.items() if tier.name in ("bound", "verified"))
        assert sorted(info.connect) == reaching, info.name
        for block in info.connect.values():
            assert block.address and block.example and block.on_robot, info.name


def test_the_alohamini_setup_names_the_commit_quackd_read() -> None:
    """Upstream's newer main refuses the stop quackd sends, so a robot set up at main is one
    quackd cannot stop. The setup page said to install upstream on it and named no commit."""
    from quackd_alohamini import upstream_api as up

    zmq = BY_NAME["alohamini"].connect["zmq"]
    assert up.PIN[:7] in zmq.on_robot and up.PIN in zmq.first, zmq


def test_the_described_row_connects_nothing() -> None:
    """Its backends are bodies quackd drives no part of (ADR-0077): nothing to connect, and a
    page offering an address field for one would be a claim the body cannot keep."""
    described = BY_NAME["described"]
    assert described.kind == "described" and not described.connect
    assert not any(is_simulated(b) for b in described.backends)


def test_each_block_names_its_own_adapters_page() -> None:
    for info in ADAPTERS:
        if not info.connect:
            continue  # rosarm, which connects nothing until an owner names a date
        docs = importlib.import_module(f"quackd_{info.name}").UPSTREAMS[0][2]
        assert {block.docs for block in info.connect.values()} == {docs}, info.name


def test_a_default_address_is_the_one_its_transport_uses() -> None:
    from quackd_alohamini.zmq_host import split_address as alohamini_split
    from quackd_microduck.transports.jsonrpc_unix import default_address
    from quackd_moss import split_address as moss_split
    from quackd_open_duck.bridge import OpenDuckBridge
    from quackd_rosbridge.ws import DEFAULT_ADDRESS
    from quackd_toddlerbot.bridge import ToddlerBotBridge
    from quackd_xlerobot.zmq_host import split_address as xlerobot_split

    assert BLOCKS["microduck"]["jsonrpc"].default == default_address()
    assert BLOCKS["rosbridge"]["ws"].default == DEFAULT_ADDRESS
    assert BLOCKS["open_duck"]["bridge"].default == OpenDuckBridge().address
    assert BLOCKS["toddlerbot"]["bridge"].default == ToddlerBotBridge().address
    assert xlerobot_split(BLOCKS["xlerobot"]["zmq"].default) == xlerobot_split(None)
    assert alohamini_split(BLOCKS["alohamini"]["zmq"].default) == alohamini_split(None)
    assert moss_split(BLOCKS["moss"]["cockpit"].default) == moss_split(None)


def test_a_backend_with_no_default_is_one_its_transport_refuses_without_an_address() -> None:
    from quackd_huebot.body import NO_ADDRESS
    from quackd_lerobot.real import check_port

    assert BLOCKS["huebot"]["jsonrpc"].default is None
    assert "needs --address" in NO_ADDRESS
    assert BLOCKS["lerobot"]["real"].default is None
    with pytest.raises(Exception, match="--address must be the arm's serial port"):
        check_port("")


@pytest.mark.parametrize(
    ("adapter", "backend", "module"),
    [
        ("open_duck", "bridge", "quackd_open_duck.bridge"),
        ("toddlerbot", "bridge", "quackd_toddlerbot.bridge"),
        ("moss", "cockpit", "quackd_moss.cockpit"),
    ],
)
def test_a_token_variable_is_the_one_its_transport_reads(
    adapter: str, backend: str, module: str
) -> None:
    assert BLOCKS[adapter][backend].token_env == importlib.import_module(module).TOKEN_ENV


def test_a_body_that_takes_no_token_names_no_variable() -> None:
    takers = {("open_duck", "bridge"), ("toddlerbot", "bridge"), ("moss", "cockpit")}
    for adapter, blocks in BLOCKS.items():
        for backend, block in blocks.items():
            if (adapter, backend) not in takers:
                assert block.token_env is None, (adapter, backend)


def test_the_arm_says_before_any_check_that_a_connect_drops_its_torque_for_a_moment() -> None:
    """`quackd robot list --probe`, which the page's check is, takes torque off every motor of
    the arm as it connects, and an arm holding itself up can drop: the page says so first."""
    assert "torque off every motor" in BLOCKS["lerobot"]["real"].caution
    assert "hold an arm" in BLOCKS["lerobot"]["real"].caution


def test_a_third_partys_connect_is_read_from_its_own_module() -> None:
    block = Connect(address="a serial port", default=None, example="COM3", on_robot="nothing")
    module = SimpleNamespace(
        CONNECT={
            "real": block,
            "net": {"address": "tcp://HOST:1", "default": "tcp://x:1", "example": "tcp://x:1"},
            "broken": {"not": "a field"},
        }
    )
    found = _connect_of(module)
    assert found["real"] is block
    assert found["net"].default == "tcp://x:1"
    assert "broken" not in found, "help that cannot be read is left out, the adapter kept"
    assert _connect_of(SimpleNamespace()) == {}
    assert _connect_of(SimpleNamespace(CONNECT=["not", "a", "mapping"])) == {}
    assert _connect_of(SimpleNamespace(CONNECT="tcp://x:1")) == {}
