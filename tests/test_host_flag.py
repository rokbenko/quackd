"""`--host`: which board a command uses, where that was said, and what one board cannot be.

The noun is one machine: its LLM server, its camera, its detector and its health. These
tests hold the parts that exist before any of the board's data is read: the order the three
places that can name a board are tried in, the refusal of a fleet (one board cannot be every
member's camera), the local preset moving to the board, and the token staying out of every
record a run writes. The client and the daemon are tested in their own files.

Nothing here has touched a Jetson. Where a daemon has to be listening, it is the fake in
`tests/fake_jetson_hostd.py`, which proves quackd reads the protocol and nothing about a board.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.cli import app
from quackd.host import (
    HOST_ENV,
    PROTOCOL,
    PROTOCOL_VERSION,
    TOKEN_ENV,
    HostChoice,
    HostHello,
    resolve_host,
)
from quackd.mcp_server import fleet_from_flags
from quackd.registry import Registry, RobotEntry, StoredFlock
from tests.conftest import help_text
from tests.fake_jetson_hostd import FakeHostd

runner = CliRunner()


# ── resolve_host: the flag, then the robot, then the environment ────────────────────────


def test_the_flag_beats_the_robot_which_beats_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order every quackd setting uses, with the place that won written down, so a refusal
    later can send the reader to the line they typed rather than to one they did not."""
    monkeypatch.setenv(HOST_ENV, "usual.local")
    typed = resolve_host("typed.local", "stored.local", robot="jet")
    assert (typed.host, typed.source) == ("typed.local", "--host")
    stored = resolve_host(None, "stored.local", robot="jet")
    assert (stored.host, stored.source) == ("stored.local", "robot jet (robots.json)")
    usual = resolve_host(None, None)
    assert (usual.host, usual.source) == ("usual.local", HOST_ENV)
    monkeypatch.setenv(HOST_ENV, "")
    assert resolve_host(None, None) == HostChoice(), "nothing named a board"


def test_blank_is_absent_at_every_rung(monkeypatch: pytest.MonkeyPatch) -> None:
    """`QUACKD_HOST=` in a `.env` is a shell saying unset, and `--host "$BOARD"` with an empty
    variable is nothing typed. Neither is a machine named "", and neither may hide the rung
    below it."""
    monkeypatch.setenv(HOST_ENV, "usual.local")
    assert resolve_host("  ", "", robot="jet").host == "usual.local"
    assert resolve_host("", "stored.local", robot="jet").host == "stored.local"


def test_the_token_climbs_its_own_ladder_so_a_tunnel_still_carries_the_robots_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--host 127.0.0.1` through an ssh tunnel reaches the very board that was registered, so
    the stored token still goes with it, the rule `--address` and `--token` already follow.
    And the environment's token sits below the robot's, as its host does: a robot registered
    with a token was registered by somebody who meant that one."""
    monkeypatch.setenv(TOKEN_ENV, "from-the-env")
    tunnel = resolve_host("127.0.0.1", "jetson.local", stored_token="stored", robot="jet")
    assert (tunnel.host, tunnel.token) == ("127.0.0.1", "stored")
    typed = resolve_host("127.0.0.1", "jetson.local", token="typed", stored_token="stored")
    assert typed.token == "typed"
    assert resolve_host(None, "jetson.local", stored_token=None).token == "from-the-env"
    assert resolve_host(None, "jetson.local", token="  ", stored_token="stored").token == "stored"


def test_a_token_from_the_environment_with_no_board_to_go_to_is_dropped_rather_than_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`QUACKD_HOST_TOKEN` in a `.env` is how the token of the board somebody usually uses is
    kept out of their shell history, and it must not stop a run that names no board."""
    monkeypatch.setenv(TOKEN_ENV, "from-the-env")
    assert resolve_host(None, None) == HostChoice()
    assert resolve_host(None, None, token="  ") == HostChoice(), "blank is nothing typed"


def test_a_typed_token_with_no_board_to_go_to_is_refused_and_not_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Typed, it is somebody who meant a board, and a run that dropped it went ahead with the
    laptop's camera and detector and exit 0, while `robot add` refuses the same flag without
    --host. The variable beside it changes nothing about that."""
    monkeypatch.setenv(TOKEN_ENV, "from-the-env")
    with pytest.raises(ValueError, match=r"^--host-token needs a board") as refused:
        resolve_host(None, None, token="typed-secret", stored_token="stored", robot="jet")
    assert "typed-secret" not in str(refused.value)
    assert "give --host too, or drop --host-token" in str(refused.value)


def test_a_refused_token_is_answered_for_the_rung_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The robot's stored token outranks `QUACKD_HOST_TOKEN`, so telling somebody whose robot
    stores one to set the variable sent them to change something the run never reads."""
    monkeypatch.setenv(TOKEN_ENV, "from-the-env")
    typed = resolve_host(None, "jetson.local", token="t", stored_token="s", robot="jet")
    assert typed.token_source == "--host-token"
    assert typed.token_fix == "pass the token the daemon was started with as --host-token"
    stored = resolve_host(None, "jetson.local", stored_token="s", robot="jet")
    assert stored.token_source == "robot jet (robots.json)"
    assert stored.token_fix.startswith("quackd robot edit jet --host-token TOKEN")
    assert TOKEN_ENV not in stored.token_fix
    usual = resolve_host(None, "jetson.local", robot="jet")
    assert usual.token_source == TOKEN_ENV and usual.token_fix.startswith(f"set {TOKEN_ENV}")


def test_the_robots_token_rides_only_when_the_robot_stores_a_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token stored with a robot is the stored board's. When the robot stores no board, the
    one `QUACKD_HOST` names is the environment's, and its token is `QUACKD_HOST_TOKEN`: sending
    the robot's there hands board A's credential to board B and is still refused. A typed
    `--host` on a robot with no board is not the tunnel case either, since there is no
    registered board for it to be a way to."""
    monkeypatch.setenv(HOST_ENV, "board-b.local")
    monkeypatch.setenv(TOKEN_ENV, "token-for-b")
    usual = resolve_host(None, None, stored_token="token-for-a", robot="jet")
    assert (usual.host, usual.token) == ("board-b.local", "token-for-b")
    typed = resolve_host(None, None, token="typed", stored_token="token-for-a", robot="jet")
    assert typed.token == "typed", "the flag still beats the environment"
    monkeypatch.delenv(TOKEN_ENV)
    assert resolve_host(None, None, stored_token="token-for-a", robot="jet").token is None
    bare = resolve_host("board-c.local", None, stored_token="token-for-a", robot="jet")
    assert (bare.host, bare.token) == ("board-c.local", None)


def test_a_token_a_header_cannot_carry_is_refused_with_where_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """robots.json reads a token leniently, so one bad line cannot stop every registry command,
    and this is where a bad stored one is caught: on the run that would send it. The place is
    named, as it is for a host, and the token never is."""
    with pytest.raises(ValueError, match=r"^the host token has a character") as refused:
        resolve_host("jetson.local", token="hunter2\x01")
    assert "hunter2" not in str(refused.value)
    with pytest.raises(ValueError, match=r"^robot jet \(robots.json\): the host token") as refused:
        resolve_host(None, "jetson.local", stored_token="hunter2\x01", robot="jet")
    assert "hunter2" not in str(refused.value)
    monkeypatch.setenv(TOKEN_ENV, "hunter2\x01")
    with pytest.raises(ValueError, match=r"^QUACKD_HOST_TOKEN: the host token") as refused:
        resolve_host("jetson.local")
    assert "hunter2" not in str(refused.value)


def test_a_host_that_is_not_one_is_refused_in_parse_hosts_words_with_where_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every `parse_host` sentence says `--host`, which is right for the flag and sends anybody
    who put it in their `.env` hunting through a command line that never had one."""
    with pytest.raises(ValueError, match=r"^--host takes a machine, not a URL"):
        resolve_host("http://jetson.local")
    monkeypatch.setenv(HOST_ENV, "http://jetson.local")
    with pytest.raises(ValueError, match=r"^QUACKD_HOST: --host takes a machine, not a URL"):
        resolve_host(None)


def test_the_token_is_never_in_the_repr_and_a_header_breaking_one_is_never_quoted() -> None:
    """A HostChoice reaches a traceback sooner or later. And the one refusal a token can earn
    here, a character no HTTP header can carry, must not print the token while saying so."""
    chosen = resolve_host("jetson.local", token="hunter2-token")
    assert chosen.token == "hunter2-token"
    assert "hunter2" not in repr(chosen)
    with pytest.raises(ValueError, match="an HTTP header cannot carry") as refused:
        resolve_host("jetson.local", token="hunter2étoken")
    assert "hunter2" not in str(refused.value)


def test_only_a_host_somebody_named_for_this_run_or_robot_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`explicit` is what moves a local preset above `QUACKD_BASE_URL`. A host from the
    environment is left for the local provider to read at the bottom of its own ladder."""
    monkeypatch.setenv(HOST_ENV, "usual.local")
    assert resolve_host("typed.local").explicit == "typed.local"
    assert resolve_host(None, "stored.local", robot="jet").explicit == "stored.local"
    usual = resolve_host(None)
    assert usual.host == "usual.local" and usual.explicit is None


# ── run: one board is one body's ────────────────────────────────────────────────────────


def _run(tmp_path: Path, *args: str) -> Any:
    return runner.invoke(
        app,
        [
            "run",
            *args,
            "--llm",
            "fake",
            "--no-gif",
            "--no-log",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--memory-dir",
            str(tmp_path / "mem"),
            "--registry-dir",
            str(tmp_path / "reg"),
        ],
    )


@pytest.fixture
def nothing_connects(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every adapter the CLI asks for, recorded. A refusal that comes after one was built came
    after the point where a real body would have been reached.

    The pilot flock's module first, and by name: it binds `make_adapter` when it is imported,
    so patching the factory alone would reach a first import made during this test and leave
    every later flock in the session building adapters through this fixture."""
    built: list[Any] = []

    def refuse(spec: Any, **kw: Any) -> Any:
        built.append(spec)
        raise AssertionError(f"{spec} was built before the refusal")

    monkeypatch.setattr("quackd.flock.pilots.make_adapter", refuse)
    monkeypatch.setattr("quackd.adapters.factory.make_adapter", refuse)
    return built


def _flat(result: Any) -> str:
    return " ".join(result.output.split())


@pytest.mark.parametrize(
    "fleet",
    [
        ("hello-world", "--robots", "a=microduck:mock,b=microduck:mock"),
        ("hello-world", "--robots", "a=microduck:mock"),
        ("flock-kick", "--flock", "2"),
        ("flock-hello",),
    ],
    ids=["robots", "robots-of-one", "flock-n", "flock-section"],
)
def test_a_host_on_the_line_is_refused_for_a_fleet_before_anything_connects(
    tmp_path: Path, nothing_connects: list[Any], fleet: tuple[str, ...]
) -> None:
    """One board is one camera and one detector, and a fleet is several bodies: no member could
    be told which of them the board's camera is, so none of them is. Every spelling of a fleet
    is refused, `--robots` even with one member, because it is the fleet spelling."""
    result = _run(tmp_path, *fleet, "--host", "jetson.local")
    assert result.exit_code == 1, result.output
    assert "--host names one machine's camera and detector, and a fleet has several bodies" in (
        _flat(result)
    )
    assert nothing_connects == []
    assert not (tmp_path / "runs").exists(), "the refusal came after the run directory"


def test_a_host_stored_with_a_flock_member_is_refused_by_that_members_name(
    tmp_path: Path, nothing_connects: list[Any]
) -> None:
    """The same claim made in robots.json. The member is named, because the flock was typed
    and the host was not: the reader has to be told which robot to edit."""
    registry = Registry(tmp_path / "reg")
    registry.add_robot(RobotEntry(name="duck", spec="microduck:mock"))
    registry.add_robot(RobotEntry(name="jet", spec="lerobot:mock", host="jetson.local"))
    registry.add_flock(StoredFlock(name="pair", members=["duck", "jet"]))
    result = _run(tmp_path, "flock-hello", "--flock", "pair")
    assert result.exit_code == 1, result.output
    flat = _flat(result)
    assert "jet has a host in robots.json" in flat, flat
    assert "quackd robot edit jet --clear host" in flat
    assert nothing_connects == []


def test_the_usual_board_in_the_environment_does_not_stop_a_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`QUACKD_HOST` is the board somebody usually uses, not a claim about these bodies, so a
    flock still runs with it set; what it moves for a fleet is only a local preset's server."""
    monkeypatch.setenv(HOST_ENV, "jetson.local")
    result = _run(tmp_path, "flock-kick", "--flock", "2", "--seed", "3")
    assert result.exit_code == 0, result.output


def test_a_host_that_is_not_one_is_refused_before_anything_connects(
    tmp_path: Path, nothing_connects: list[Any]
) -> None:
    result = _run(tmp_path, "hello-world", "--robot", "microduck:mock", "--host", "a@jetson")
    assert result.exit_code == 1, result.output
    flat = _flat(result)
    assert "--host names a machine and never carries a token" in flat, flat
    assert "a@jetson" not in flat, "a refusal that could be looking at a secret quotes nothing"
    assert nothing_connects == []


def test_a_typed_host_token_with_no_board_is_refused_before_anything_connects(
    tmp_path: Path, nothing_connects: list[Any]
) -> None:
    """It used to be dropped: the run went ahead on the laptop's camera and detector with exit
    0, and nothing on the screen said the flag had gone nowhere. A fleet has no board at all,
    and refuses the token as it refuses --host."""
    alone = _run(tmp_path, "hello-world", "--robot", "microduck:mock", "--host-token", "abc123")
    assert alone.exit_code == 1, alone.output
    assert "--host-token needs a board" in _flat(alone), alone.output
    fleet = _run(
        tmp_path,
        "hello-world",
        "--robots",
        "a=microduck:mock,b=microduck:mock",
        "--host-token",
        "abc123",
    )
    assert fleet.exit_code == 1, fleet.output
    assert "--host-token is one board's token, and a fleet has several bodies" in _flat(fleet)
    for result in (alone, fleet):
        assert "abc123" not in result.output
    assert nothing_connects == []
    assert not (tmp_path / "runs").exists()


def test_serve_mcp_refuses_a_typed_host_token_with_no_board() -> None:
    with pytest.raises(SystemExit, match=r"^--host-token needs a board"):
        fleet_from_flags(robot="microduck:mock", host_token="abc123")
    with pytest.raises(SystemExit, match=r"^--host-token is one board's token, and a fleet"):
        fleet_from_flags(robots="a=microduck:mock,b=microduck:mock", host_token="abc123")


def test_a_stored_token_the_board_refuses_is_answered_with_robot_edit_and_doctor_by_name(
    tmp_path: Path, nothing_connects: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board's daemon restarted with a new token, and the robot still stores the old one.
    The stored token outranks QUACKD_HOST_TOKEN, so the variable is set here to show that
    following the old advice changed nothing, and the refusal names the fix that lasts. The
    doctor it points at names the robot, since `doctor --host` alone carries no stored token
    and reported a healthy daemon while the run kept refusing. The daemon did answer, with a
    401, and the refusal no longer says it did not."""
    monkeypatch.setenv(TOKEN_ENV, "new-token")
    with FakeHostd(token="new-token") as hostd:
        address = hostd.address
        Registry(tmp_path / "reg").add_robot(
            RobotEntry(name="jet", spec="microduck:mock", host=address, host_token="old-token")
        )
        stored = _run(tmp_path, "hello-world", "--robot", "jet")
        tunnel = _run(tmp_path, "hello-world", "--robot", "jet", "--host", address)
    assert stored.exit_code == 1, stored.output
    flat = _flat(stored)
    assert f"the host {address} from robot jet (robots.json) answered with HTTP 401" in flat, flat
    assert "quackd robot edit jet --host-token TOKEN stores the one the daemon was started" in flat
    assert f"set {TOKEN_ENV}" not in flat and "did not answer" not in flat
    assert "quackd doctor --robot jet shows what the board says" in flat
    assert tunnel.exit_code == 1, tunnel.output
    assert f"quackd doctor --robot jet --host {address} shows" in _flat(tunnel)
    for result in (stored, tunnel):
        assert "old-token" not in result.output and "new-token" not in result.output
    assert nothing_connects == []


def _hand_edited(tmp_path: Path) -> None:
    """robots.json with a good robot and one whose host a hand broke, past every check."""
    (tmp_path / "reg").mkdir()
    (tmp_path / "reg" / "robots.json").write_text(
        json.dumps(
            {
                "version": 1,
                "robots": {
                    "duck": {"spec": "microduck:mock"},
                    "jet": {"spec": "microduck:mock", "host": "jetson.local:99999"},
                },
            }
        ),
        encoding="utf-8",
    )


def test_a_hand_edited_bad_host_stops_only_the_robot_it_belongs_to(
    tmp_path: Path, nothing_connects: list[Any]
) -> None:
    """robots.json reads a host leniently, because every command reads the whole file. So a
    run of another robot goes ahead, and a run of this one is refused before anything connects,
    with the robot's name in front of `parse_host`'s words."""
    _hand_edited(tmp_path)
    refused = _run(tmp_path, "hello-world", "--robot", "jet")
    assert refused.exit_code == 1, refused.output
    flat = _flat(refused)
    assert "robot jet (robots.json): the port in --host must be a whole number" in flat, flat
    assert nothing_connects == []


def test_a_hand_edited_bad_host_does_not_stop_a_run_of_another_robot(tmp_path: Path) -> None:
    _hand_edited(tmp_path)
    result = _run(tmp_path, "hello-world", "--robot", "duck", "--seed", "3")
    assert result.exit_code == 0, result.output


# ── run: the local preset moves to the board ────────────────────────────────────────────


@pytest.fixture
def pilots(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """What `make_provider` was asked for, answered with the scripted pilot so the run
    completes without an LLM server or the OpenAI SDK."""
    from quackd.agent.providers import factory

    seen: list[dict[str, Any]] = []
    real = factory.make_provider

    def recorder(name: str, **kw: Any) -> Any:
        seen.append({"name": name, **kw})
        return real("fake", duck_name=kw.get("duck_name"))

    monkeypatch.setattr("quackd.agent.providers.factory.make_provider", recorder)
    return seen


@pytest.fixture
def any_board(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every board `run` and `serve-mcp` asked for, each answering its hello with no camera and
    no detector, so the rest of the command is as it would be without one.

    A board has to answer since `--host` began to mean its daemon: a run whose board is down is
    refused before anything connects. These tests are about which name was chosen, and the
    names they choose between (`jetson.local`, `stored.local`) are nobody's machine, so the
    client is replaced where `reach_host` builds it rather than every name being given a fake
    daemon of its own."""
    asked: list[str] = []

    class Board:
        def __init__(
            self, host: str, *, token: str | None = None, token_fix: str | None = None
        ) -> None:
            asked.append(host)
            self.host = host
            self.address = host

        def hello(self, *, refresh: bool = False) -> HostHello:
            return HostHello(
                protocol=PROTOCOL,
                protocol_version=PROTOCOL_VERSION,
                daemon_version="0.1.0",
                hostname="board",
                python="3.10.12",
                capabilities={"camera": False, "detect": False, "tegra": False},
                camera=None,
                camera_error="started with --camera none",
                detect=None,
                detect_error="started with --no-detect",
                board_model=None,
            )

    monkeypatch.setattr("quackd.host.HostClient", Board)
    return asked


def _local_run(tmp_path: Path, *args: str) -> Any:
    return runner.invoke(
        app,
        [
            "run",
            "hello-world",
            "--llm",
            "ollama",
            "--no-gif",
            "--no-log",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--memory-dir",
            str(tmp_path / "mem"),
            "--registry-dir",
            str(tmp_path / "reg"),
            *args,
        ],
    )


def test_the_host_a_run_names_or_its_robot_stored_reaches_the_llm_preset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pilots: list[dict[str, Any]],
    any_board: list[str],
) -> None:
    """The first thing `--host` does for real: the pilot's preset is moved to the board. The
    flag beats the robot's stored host, and a host from the environment is not handed over as
    an explicit one, because it sits below `QUACKD_BASE_URL` and the provider reads it there.
    Every one of them is still the board the run asks for its daemon's hello."""
    Registry(tmp_path / "reg").add_robot(
        RobotEntry(name="jet", spec="microduck:mock", host="stored.local")
    )
    typed = _local_run(tmp_path, "--robot", "microduck:mock", "--host", "jetson.local")
    assert typed.exit_code == 0, typed.output
    assert pilots[-1]["name"] == "ollama" and pilots[-1]["host"] == "jetson.local"
    stored = _local_run(tmp_path, "--robot", "jet")
    assert stored.exit_code == 0, stored.output
    assert pilots[-1]["host"] == "stored.local"
    tunnel = _local_run(tmp_path, "--robot", "jet", "--host", "127.0.0.1")
    assert tunnel.exit_code == 0, tunnel.output
    assert pilots[-1]["host"] == "127.0.0.1", "the flag beats what the robot stored"
    monkeypatch.setenv(HOST_ENV, "usual.local")
    usual = _local_run(tmp_path, "--robot", "microduck:mock")
    assert usual.exit_code == 0, usual.output
    assert pilots[-1]["host"] is None
    assert any_board == ["jetson.local", "stored.local", "127.0.0.1", "usual.local"]


# ── the record never holds the token ────────────────────────────────────────────────────


def test_the_host_token_never_reaches_any_file_the_run_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run directory is pasted into issues and copied off a bench, and this token guards a
    camera and a GPU. The command line is written down three times (the saved terminal,
    `run_start` and the summary) and the token is in none of them.

    A fake daemon holding the same token listens at the host, so this stays a run against a
    board that answers once the run starts talking to one."""
    secret = "hunter3-host-token"
    with FakeHostd(token=secret) as hostd:
        args = [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--robot",
            "microduck:mock",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--memory-dir",
            str(tmp_path / "mem"),
            "--no-gif",
            "--host",
            hostd.address,
            "--host-token",
            secret,
        ]
        # `quackd.command` reads `sys.argv`, which CliRunner never touches
        monkeypatch.setattr(sys, "argv", ["/opt/venv/bin/quackd", *args])
        result = runner.invoke(app, args, env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    runs = tmp_path / "runs"
    written = {
        name: next(runs.rglob(name)).read_text(encoding="utf-8")
        for name in ("terminal.txt", "transcript.jsonl", "summary.json")
    }
    for name, text in written.items():
        assert secret not in text, name
        assert "--host-token" in text, f"{name}: the flag shows, so a reader sees one was given"
    start = json.loads(written["transcript.jsonl"].splitlines()[0])
    assert start["kind"] == "run_start"
    assert start["command"][-2:] == ["--host-token", "***"]
    assert json.loads(written["summary.json"])["command"] == start["command"]


# ── serve-mcp: the same refusal, the same ladder ────────────────────────────────────────


def _seed(tmp_path: Path) -> Registry:
    registry = Registry(tmp_path)
    registry.add_robot(
        RobotEntry(name="jet", spec="microduck:mock", host="jetson.local", host_token="stored")
    )
    registry.add_robot(RobotEntry(name="duck", spec="microduck:mock"))
    registry.add_flock(StoredFlock(name="pair", members=["duck", "jet"]))
    registry.add_flock(StoredFlock(name="plain", members=["duck"]))
    return registry


@pytest.mark.parametrize(
    "kwargs",
    [
        {"robots": "a=microduck:mock,b=microduck:mock", "host": "jetson.local"},
        {"robots": "a=microduck:mock", "host": "jetson.local"},
        {"flock": "plain", "host": "jetson.local"},
    ],
    ids=["robots", "robots-of-one", "flock"],
)
def test_serve_mcp_refuses_a_host_for_a_fleet(tmp_path: Path, kwargs: dict[str, Any]) -> None:
    _seed(tmp_path)
    with pytest.raises(SystemExit, match="a fleet has several bodies"):
        fleet_from_flags(registry_dir=str(tmp_path), **kwargs)


def test_serve_mcp_refuses_a_flock_whose_member_has_a_host_by_that_members_name(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    with pytest.raises(SystemExit, match=r"jet has a host in robots\.json"):
        fleet_from_flags(flock="pair", registry_dir=str(tmp_path))


def test_serve_mcp_settles_one_robots_board_by_the_same_ladder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, any_board: list[str]
) -> None:
    """The plan carries the board for the camera and the detector to read, settled the way
    `run` settles it: the robot's own, a flag over it field by field, and nothing for a fleet
    even when the environment names one, so a fleet asks no board anything."""
    _seed(tmp_path)
    stored = fleet_from_flags(robot="jet", registry_dir=str(tmp_path)).host
    assert (stored.host, stored.source, stored.token) == (
        "jetson.local",
        "robot jet (robots.json)",
        "stored",
    )
    tunnel = fleet_from_flags(robot="jet", registry_dir=str(tmp_path), host="127.0.0.1").host
    assert (tunnel.host, tunnel.token) == ("127.0.0.1", "stored")
    with pytest.raises(SystemExit, match="not a URL"):
        fleet_from_flags(robot="duck", registry_dir=str(tmp_path), host="http://jetson.local")
    monkeypatch.setenv(HOST_ENV, "usual.local")
    fleet = fleet_from_flags(robots="a=microduck:mock,b=microduck:mock", registry_dir=str(tmp_path))
    assert fleet.host == HostChoice()
    assert any_board == ["jetson.local", "127.0.0.1"]


def test_serve_mcp_takes_the_flag_on_its_command_line(tmp_path: Path) -> None:
    """The refusal above, through the command a desktop client spawns, so the flag is proved to
    reach `fleet_from_flags` rather than stop at Typer."""
    result = runner.invoke(
        app,
        ["serve-mcp", "--robots", "a=microduck:mock,b=microduck:mock", "--host", "jetson.local"],
    )
    assert result.exit_code == 1
    assert "a fleet has several bodies" in _flat(result)


# ── where the flag is offered ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", [["run"], ["serve-mcp"]], ids=" ".join)
def test_the_commands_that_use_a_board_offer_host_and_its_token(command: list[str]) -> None:
    text = help_text([*command, "--help"])
    assert "--host HOST[:PORT] A machine quackd uses and never runs on" in text, text
    assert "--robot still names the body" in text
    assert "--host-token" in text and "QUACKD_HOST_TOKEN" in text


@pytest.mark.parametrize("command", [["run"], ["serve-mcp"]], ids=" ".join)
def test_the_detector_help_names_the_extra_yolo_needs(command: list[str]) -> None:
    """The help is Rich markup, which reads an unescaped `[yolo]` as a tag and drops it: the
    help said yolo "needs quackd.", which is true of everything and names no extra."""
    text = help_text([*command, "--help"])
    assert "yolo is YOLO on this machine and needs quackd[yolo]." in text, text


@pytest.mark.parametrize("command", [["robot", "add"], ["robot", "edit"]], ids=" ".join)
def test_the_commands_that_register_a_board_say_so_in_their_own_help(command: list[str]) -> None:
    """`run`'s help is wrong here three ways: these commands have no --robot, they are what
    does the registering, and the token they take is written to robots.json, which is not
    the run record `run`'s help promises it never reaches."""
    text = help_text([*command, "--help"])
    assert "--host HOST[:PORT] The board this robot uses and quackd never runs on" in text, text
    assert "--host on a run beats this, and this beats QUACKD_HOST" in text
    assert "Kept in robots.json, as --token is" in text
    assert "--robot still names the body" not in text
    assert "never reaches the run record" not in text


@pytest.mark.parametrize("command", [["record"], ["list-models"]], ids=" ".join)
def test_the_commands_with_no_board_to_use_do_not_offer_one(command: list[str]) -> None:
    """`record` pins the simulator and `list-models` reads a catalogue; neither has a camera,
    a detector or a board's health to use."""
    assert "--host" not in help_text([*command, "--help"])
