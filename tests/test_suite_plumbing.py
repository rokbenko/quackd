"""The suite's own plumbing, held to what it was put there to do. None of this is quackd: it is what
keeps a run of quackd's tests finishing, and finishing in the time it says."""

from __future__ import annotations

import ast
import socket
from pathlib import Path
from typing import Any

import pytest

from tests import conftest


def test_a_socket_pair_whose_connect_never_arrives_is_made_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hang this exists for: the connect goes nowhere and the `accept` behind it waits. The
    first attempt here loses its connect as that one did, and the pair still comes back, working,
    from the second."""
    monkeypatch.setattr(conftest, "SOCKETPAIR_ACCEPT_S", 0.05)
    lost: list[Any] = []

    def first_one_goes_nowhere(sock: socket.socket, address: Any) -> None:
        if not lost:
            lost.append(address)
            return
        socket.socket.connect(sock, address)

    server, client = conftest._socketpair_that_gives_up(connect=first_one_goes_nowhere)
    with server, client:
        assert lost, "the first attempt never ran"
        client.sendall(b"quack")
        assert server.recv(5) == b"quack"
        assert server.getblocking() and client.getblocking()


def test_a_socket_pair_that_never_connects_says_so_rather_than_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Giving up is the point: a named failure in one test, where CPython's waits out the job."""
    monkeypatch.setattr(conftest, "SOCKETPAIR_ACCEPT_S", 0.01)
    with pytest.raises(OSError, match="no socket pair"):
        conftest._socketpair_that_gives_up(connect=lambda sock, address: None)


def test_no_fixture_here_lists_the_base_directory_for_every_test() -> None:
    """`tmp_path_factory.mktemp` lists the whole base directory on each call, so an autouse fixture
    that calls it makes every test slower than the one before: five of them took 8,000 tests that
    did nothing from 2 minutes to over 45. A new fixture reaches for `_scratch_dir` instead."""
    tree = ast.parse(Path(conftest.__file__).read_text(encoding="utf-8"))
    calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mktemp"
    ]
    assert not calls, f"tests/conftest.py calls mktemp on lines {calls}: use _scratch_dir"


def test_each_scratch_directory_is_new_and_private(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    one = conftest._scratch_dir(tmp_path_factory, "same-name")
    two = conftest._scratch_dir(tmp_path_factory, "same-name")
    assert one != two and one.is_dir() and two.is_dir()
    assert one.parent == two.parent == tmp_path_factory.getbasetemp() / "h"
