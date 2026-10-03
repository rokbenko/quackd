"""`quackd doctor`: it runs everywhere, it answers in data, and the WebSocket stub refuses
honestly.

The split between `collect` and `render` is the thing worth testing. The collector must not
know what green means, or `--json` has nothing underneath it to print, and the renderer must
not know anything the report does not hold.
"""

from __future__ import annotations

import ast
import io
import json
import platform
import re
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from quackd import doctor
from quackd.agent.providers.factory import CLOUD_NAMES, KEY_ENV, PROVIDER_NAMES
from quackd.cli import app
from quackd.doctor import PlacementRow
from quackd.host import HostBoard, HostClient
from quackd.registry import Registry, RobotEntry
from quackd.transport.base import CameraFrame, TransportError
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.mock import REST, LeRobotMock
from quackd_microduck.transports.websocket_stub import WebSocketTransport
from tests.fake_jetson_hostd import CannedReply, FakeHostd, dead_address, default_board
from tests.jetson_fixtures import (
    COMPATIBLE,
    MEMINFO,
    NL,
    NO_SWAPS,
    NUL,
    NVME_SWAPS,
    NVPMODEL_Q,
    ORIN_NANO,
    RELEASE_36_4_3,
    TEGRASTATS_LINE,
    ZRAM_SWAPS,
)


def test_doctor_runs() -> None:
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    for needle in (
        "providers",
        "adapters",
        "transports",
        "upstream assumptions",
        "sim2d",
        "jsonrpc",
    ):
        assert needle in result.output


def test_doctor_lists_every_provider_and_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The providers table is built from the same tables `make_provider` dispatches on, so a
    vendor that is wired up but missing a row here would be a vendor nobody could discover.

    The keys are blanked, and blanked rather than deleted: doctor names the variable it wants
    only when it is empty, and the CLI loads a developer's `.env` in its root callback, so on
    a machine with one real key this asserted against a masked value instead. Empty reads as
    unset and, unlike `delenv`, survives `load_dotenv`, which does not overwrite a name that
    is already in the environment."""
    for env in set(KEY_ENV.values()):
        monkeypatch.setenv(env, "")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    for name in PROVIDER_NAMES:
        assert name in flat, f"{name} has no row in doctor's providers table"
    for cloud in CLOUD_NAMES:
        assert KEY_ENV[cloud] in flat, f"{cloud} does not say which key it wants"


def test_doctor_shows_a_robot_manifest() -> None:
    result = CliRunner().invoke(app, ["doctor", "--robot", "microduck:mock"])
    assert result.exit_code == 0, result.output
    assert "microduck (biped" in result.output and "standing" in result.output
    bad = CliRunner().invoke(app, ["doctor", "--robot", "nope:x"])
    assert bad.exit_code == 1 and "unknown adapter" in bad.output


async def test_websocket_stub_points_at_upstream() -> None:
    t = WebSocketTransport()
    with pytest.raises(TransportError, match=r"architecture\.md"):
        await t.connect()
    await t.stop()  # never raises: a stop must always be safe


# ── the report, and the renderer that is not allowed to know anything it does not ───────


_REAL_PROBE_MODELS = doctor._probe_models
_REAL_PROBE_PLACEMENT = doctor._probe_placement
"""Captured before `_no_network` replaces them. A few tests below are about these two functions
rather than about a report, and without this they would assert against the stubs and pass
whatever the functions did."""


class _NoBoard:
    """`doctor._host_client` in every test that did not ask for a daemon. A board reached by
    accident is a test failing loudly, never one quietly waiting on the network or, worse,
    passing against a real board on somebody's desk."""

    def __init__(
        self, host: str, *, token: str | None = None, token_fix: str | None = None
    ) -> None:
        raise AssertionError(f"this test reached for a board at {host} without FakeHostd")


def _no_placement(root_url: str, timeout_s: float = 1.5) -> Any:
    raise AssertionError(f"this test asked an Ollama at {root_url} where its models are")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """doctor probes five local servers at 1.5 s each, asks an Ollama that is up where its
    models are, and reaches the daemon on a board when one is named. Nothing here is about
    whether any of those is running on the machine the suite runs on, and a suite that waits
    eight seconds to find out is a suite nobody runs. The placement probe and the board raise
    rather than answer, so a test that meant to fake one and forgot cannot pass by reaching a
    real one."""
    monkeypatch.setattr(doctor, "_probe_models", lambda url, timeout_s=1.5: ("down", "not running"))
    monkeypatch.setattr(doctor, "_probe_placement", _no_placement)
    monkeypatch.setattr(doctor, "_host_client", _NoBoard)


def test_collect_answers_in_data_with_no_styling_in_it() -> None:
    """The collector must not know what green means. It did: every cell used to be a markup
    string, which is why there was nothing underneath for --json to print."""
    report = doctor.collect()
    blob = json.dumps(report.to_dict())
    for tag in ("[green]", "[red]", "[yellow]", "[dim]", "[/"):
        assert tag not in blob, tag
    assert report.ok is True
    assert [c.name for c in report.core] == ["pydantic", "mcp", "opencv", "numpy", "Pillow"]
    assert len(report.adapters) == 7
    assert report.bundled_ducks > 0
    assert {p.upstream for p in report.pins} >= {"microduck", "lerobot", "microduck_rl"}
    assert report.assumptions, "there are unverified assumptions and doctor says so"


def test_a_missing_core_package_is_a_failure_and_a_missing_extra_is_not() -> None:
    report = doctor.collect()
    assert report.ok and not report.missing_core
    report.core[0].ok = False
    assert not report.ok and report.missing_core == [report.core[0].name]
    report = doctor.collect()
    report.extras[0].ok = False
    assert report.ok, "an extra nobody installed is a choice, not a fault"


def test_an_unknown_robot_fails_the_report_rather_than_raising() -> None:
    report = doctor.collect("nope:x")
    assert report.robot is not None and report.robot.error is not None
    assert "unknown adapter" in report.robot.error
    assert report.ok is False


def test_the_report_reads_a_robot_manifest() -> None:
    report = doctor.collect("microduck:mock")
    assert report.robot is not None and report.robot.error is None
    names = {v.name for v in report.robot.verbs}
    assert {"move", "stop", "observe"} <= names
    assert report.ok is True


def test_render_says_everything_the_report_holds() -> None:
    buf = io.StringIO()
    doctor.render(Console(file=buf, width=200), doctor.collect("microduck:mock"))
    out = buf.getvalue()
    for needle in (
        "core",
        "providers",
        "local LLM servers",
        "adapters",
        "transports",
        "optional extras",
        "upstream assumptions",
        "SUCCESS",
        "microduck (biped",
        # eight table titles used to carry these paths; losing them was losing where to read
        "read more:",
        "docs/adapters/lerobot/README.md",
        "docs/adr/0030-mujoco-physics-backend.md",
    ):
        assert needle in out, needle


def test_the_transports_section_is_every_adapters_and_names_each_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each installed adapter adds its own rows to the transports table, so the table is not
    one robot's. Its heading used to call them the Microduck's backends and say to pass
    `--robot microduck:<name>`, which a row for the arm's simulator made untrue, so each row
    names its own `adapter:backend` and the heading claims no body."""
    monkeypatch.setattr(doctor, "_probe_models", lambda url, timeout_s=1.5: ("down", "not running"))
    report = doctor.collect()
    rows = {row.name: row for row in report.transports}
    assert "lerobot:mujoco" in rows, sorted(rows)
    buf = io.StringIO()
    doctor.render(Console(file=buf, width=200), report)
    out = buf.getvalue()
    assert "transports (backends an adapter checks on this machine)" in out
    assert "Microduck backends" not in out and "microduck:<name>" not in out
    assert "lerobot:mujoco" in out and rows["lerobot:mujoco"].status in out


def test_the_header_names_the_microducks_protocol_only_when_it_has_a_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The version comes from the Microduck's adapter, and a machine without it has none: the
    header used to end on `duck-ipc-proto API v` with nothing after it."""

    def header(version: str) -> str:
        monkeypatch.setattr(doctor, "_microduck_api_version", lambda: version)
        buf = io.StringIO()
        doctor.render(Console(file=buf, width=200), doctor.collect())
        return buf.getvalue().splitlines()[0]

    assert "duck-ipc-proto" not in header(""), header("")
    assert header("7").rstrip().endswith("duck-ipc-proto API v7"), header("7")


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_render_loses_nothing_on_a_codepage_that_cannot_carry_it(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """A Windows pipe is cp1252 and this is the command people paste into an issue.

    `errors="strict"` is the assertion: the stream raises rather than substituting, so this
    passes only if every character doctor wrote can be carried. Counting question marks
    cannot do the same job, because the transports note says "not on a robot?" on a machine
    with no socket and that question mark is prose, not a casualty. Both platform branches of
    that note are checked, since only one of them runs on any given machine."""
    monkeypatch.setattr(sys, "platform", platform)
    raw = io.BytesIO()
    console = Console(file=io.TextIOWrapper(raw, encoding="cp1252", errors="strict"), width=120)
    doctor.render(console, doctor.collect())
    console.file.flush()
    out = raw.getvalue().decode("cp1252")
    assert out.isascii()
    assert "[ok] in the package" in out, "the registry's tick becomes something readable"
    assert "duck-ipc-proto API" in out


def test_doctor_json_is_one_document_and_carries_the_exit_code() -> None:
    result = CliRunner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["version"] and payload["python"]
    bad = CliRunner().invoke(app, ["doctor", "--robot", "nope:x", "--json"])
    assert bad.exit_code == 1
    assert json.loads(bad.output)["robot"]["error"].startswith("unknown adapter")


def test_the_progress_callback_names_the_slow_questions() -> None:
    """It feeds the spinner, which is the only reason anybody knows doctor is still alive
    while it waits on five local servers."""
    said: list[str] = []
    doctor.collect(progress=said.append)
    assert any("probing ollama" in line for line in said)
    assert any("extras" in line for line in said)


# ── the board --host names, read over the network ───────────────────────────────────────
#
# doctor reads no board of its own any more. quackd never runs on a Jetson, so the section
# appears only for a board named with --host, read through the daemon quackd ships for it. The
# dumps here are built from `tests/jetson_fixtures.py`, the one fake board every test reads,
# with the NULs removed where the daemon removes them, and the daemon is
# `tests/fake_jetson_hostd.py` on loopback. What none of it can say is whether a real Orin's
# files look like these; `docs/guides/jetson.md` says so and says what to send back.

TOKEN = "63d92f8974c051832d52dd04c78f314b01ef7436cc00e4a3805b9711e5318421"
"""Shaped like `openssl rand -hex 32`, the token bridge/jetson/README.md tells people to make."""

WIRE_COMPATIBLE = COMPATIBLE.replace(NUL, "")
"""The compatible list as `/board` sends it: the device tree separates its strings with NULs,
and the daemon strips them."""

GIB = 1024**3


def _dump_json(
    *,
    release: str | None = RELEASE_36_4_3,
    swaps: str | None = ZRAM_SWAPS,
    gpu_node: bool = True,
    device_tree: bool = True,
    nvpmodel: str | None = NVPMODEL_Q,
    tegrastats: str | None = TEGRASTATS_LINE,
) -> dict[str, Any]:
    """`/board`'s reply for a board made of the shared fixtures, with a reason under every null
    the way the daemon gives one."""
    files: dict[str, str | None] = {
        "/proc/device-tree/model": ORIN_NANO if device_tree else None,
        "/proc/device-tree/compatible": WIRE_COMPATIBLE if device_tree else None,
        "/etc/nv_tegra_release": release,
        "/proc/meminfo": MEMINFO,
        "/proc/swaps": swaps,
    }
    commands = {"nvpmodel -q": nvpmodel, "tegrastats": tegrastats}
    errors = {path: "No such file or directory" for path, text in files.items() if text is None}
    errors.update({name: "not on PATH" for name, out in commands.items() if out is None})
    return {
        "ok": True,
        "files": files,
        "nodes": {
            "/dev/nvgpu/igpu0": gpu_node,
            "/dev/nvhost-ctrl-gpu": False,
            "/dev/nvidia0": False,
        },
        "commands": commands,
        "errors": errors,
    }


def _board(reply: dict[str, Any]) -> HostBoard:
    """A `/board` reply as the client hands it to doctor."""
    return HostBoard(
        files=reply["files"],
        nodes=reply["nodes"],
        commands=reply["commands"],
        errors=reply["errors"],
    )


def _dump(**changes: Any) -> HostBoard:
    return _board(_dump_json(**changes))


def _rendered(report: doctor.DoctorReport) -> str:
    """The report as a person reads it, with Rich's line wrapping undone, so a needle that
    happens to straddle a wrap is still found."""
    buf = io.StringIO()
    doctor.render(Console(file=buf, width=200), report)
    return " ".join(buf.getvalue().split())


def _host_section(out: str) -> str:
    """The host section of a `_rendered` report: from its title to the next section's."""
    start = min(i for i in (out.find("Jetson at"), out.find("host at")) if i >= 0)
    return out[start : out.index(" core ", start)]


@pytest.fixture
def hostd(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHostd]:
    """A daemon on loopback describing the shared fake Orin, and doctor allowed to reach it."""
    monkeypatch.setattr(doctor, "_host_client", HostClient)
    with FakeHostd() as fake:
        fake.board = _dump_json()
        yield fake


def test_the_fake_daemon_serves_the_one_fake_board() -> None:
    """One board, not two that happen to agree: the fake daemon's default dump is this file's
    dump of `tests/jetson_fixtures.py`, which is also what the real daemon's tests write out
    as files."""
    assert default_board() == _dump_json()


def test_no_host_means_no_board_section_and_no_jetson_key() -> None:
    """A laptop is never read as a board. Without a host there is no section at all, and the
    top-level `jetson` of 0.13 has moved under `host`."""
    report = doctor.collect()
    assert report.host is None
    payload = report.to_dict()
    assert payload["host"] is None and "jetson" not in payload
    out = _rendered(report)
    assert "Jetson" not in out and "host at" not in out


def test_doctor_reads_nothing_of_this_machine_to_find_a_board() -> None:
    """The local probe is gone, and with it every way doctor had of reading this machine's
    /proc or forking a command on it. Checked in the source, because a check at run time on a
    laptop would only prove the laptop has no board."""
    tree = ast.parse(Path(doctor.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported |= {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imported & {"subprocess", "shutil", "pathlib", "glob"}, imported
    named = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open" not in named
    for gone in ("_HOST_ROOT", "_run_quiet", "_read_text", "_exists", "_jetson", "_GPU_NODES"):
        assert not hasattr(doctor, gone), f"doctor.{gone} is back"


def test_a_board_dump_is_read_field_by_field() -> None:
    got = doctor._jetson_from_dump(_dump())
    assert got is not None
    assert got.board == ORIN_NANO
    assert (got.l4t, got.jetpack) == ("36.4.3", "6.2")
    assert got.mem_total_bytes == 7650336 * 1024
    assert got.mem_available_bytes == 5123456 * 1024
    assert got.swap_total_bytes == 1017852 * 1024
    assert got.swap_devices == ["/dev/zram0"] and got.swap_only_zram is True
    assert got.gpu_device == "/dev/nvgpu/igpu0"
    assert got.power_mode == "15W"
    assert got.gpu_busy_pct == 0 and got.tegrastats == TEGRASTATS_LINE
    assert got.errors == {}
    assert "docker_default_runtime" not in got.to_dict(), "meaningless on a board quackd is not on"


def test_a_nul_a_daemon_left_in_never_reaches_a_cell() -> None:
    """This daemon strips the device tree's NUL terminators. One of another version might not,
    and the first version of this reader put one in a Rich cell."""
    reply = _dump_json()
    reply["files"]["/proc/device-tree/model"] = ORIN_NANO + NUL
    reply["files"]["/proc/device-tree/compatible"] = COMPATIBLE + NUL
    got = doctor._jetson_from_dump(_board(reply))
    assert got is not None and got.board == ORIN_NANO


def test_a_swapfile_on_the_ssd_is_not_zram() -> None:
    """The warning is about zram specifically, and a board that was set up properly must not
    read as one that was not."""
    got = doctor._jetson_from_dump(_dump(swaps=NVME_SWAPS))
    assert got is not None and got.swap_only_zram is False
    assert got.swap_devices == ["/ssd/16GB.swap"]
    none = doctor._jetson_from_dump(_dump(swaps=NO_SWAPS))
    assert none is not None and none.swap_total_bytes == 0 and none.swap_only_zram is False
    unread = doctor._jetson_from_dump(_dump(swaps=None))
    assert unread is not None and unread.swap_total_bytes is None, "unread is not the same as none"


def test_a_release_file_it_cannot_parse_is_still_a_tegra() -> None:
    got = doctor._jetson_from_dump(_dump(release="something nobody has seen"))
    assert got is not None, "the device tree still said Tegra"
    assert got.l4t is None and got.jetpack is None


def test_a_device_tree_with_no_release_file_is_a_board_with_no_l4t() -> None:
    """A daemon in a privileged container, or one started with `--security-opt
    systempaths=unconfined`: the device tree is readable and the board's release file is not
    in the image. Reporting the image's userspace as the board's would be the one wrong answer
    available here."""
    got = doctor._jetson_from_dump(_dump(release=None))
    assert got is not None and got.board == ORIN_NANO
    assert got.l4t is None and got.release_seen is False
    assert got.errors == {"/etc/nv_tegra_release": "No such file or directory"}


def test_a_release_file_alone_is_a_tegra_too() -> None:
    """The second of the two ways in: a masked device tree and the board's own release file."""
    got = doctor._jetson_from_dump(_dump(device_tree=False))
    assert got is not None and got.board is None
    assert (got.l4t, got.jetpack) == ("36.4.3", "6.2")


def test_only_a_tegra_answers_at_all() -> None:
    reply = _dump_json(release=None)
    reply["files"]["/proc/device-tree/model"] = "Raspberry Pi 4 Model B Rev 1.4"
    reply["files"]["/proc/device-tree/compatible"] = "raspberrypi,4-model-bbrcm,bcm2711"
    assert doctor._jetson_from_dump(_board(reply)) is None
    nothing = _dump_json(release=None, device_tree=False)
    assert doctor._jetson_from_dump(_board(nothing)) is None


@pytest.mark.parametrize(
    ("l4t", "jetpack"),
    [
        ("36.4.3", "6.2"),
        ("36.4.4", "6.2.1"),
        ("36.5.0", "6.2.2"),
        ("36.4", "6.1"),
        ("36.3.0", "6.0"),
        ("39.2.1", "7.2.1"),
        ("35.4.1", "5.x"),
        ("36.9.9", "6.x"),
        ("99.1", None),
    ],
)
def test_the_jetpack_table_names_exact_releases_and_the_major_for_the_rest(
    l4t: str, jetpack: str | None
) -> None:
    """`36.4` and `36.4.0` are the same release written two ways, and NVIDIA writes both.

    The major-only fallback is deliberate: L4T 35.1 was JetPack 5.0.2 and 35.2.1 was 5.1, so a
    guessed minor would be wrong about a board somebody owns."""
    assert doctor._jetpack_for(l4t) == jetpack


def test_a_power_mode_line_with_no_colon_is_none_rather_than_a_traceback() -> None:
    """`split(":", 1)[1]` raised IndexError here, on a machine already having a bad day."""
    assert doctor._power_mode("NV Power Mode" + NL) is None
    got = doctor._jetson_from_dump(_dump(nvpmodel="NV Power Mode" + NL))
    assert got is not None and got.power_mode is None


def test_the_command_answers_are_parsed_and_an_absent_one_is_none_with_its_reason() -> None:
    got = doctor._jetson_from_dump(
        _dump(nvpmodel="NV Fan Mode:quiet\nNV Power Mode: MAXN_SUPER\n2\n")
    )
    assert got is not None and got.power_mode == "MAXN_SUPER"
    absent = doctor._jetson_from_dump(_dump(nvpmodel=None, tegrastats=None))
    assert absent is not None
    assert absent.power_mode is None and absent.gpu_busy_pct is None and absent.tegrastats is None
    assert absent.errors == {"nvpmodel -q": "not on PATH", "tegrastats": "not on PATH"}


@pytest.mark.parametrize(
    ("line", "busy"),
    [
        (TEGRASTATS_LINE, 0),
        ("RAM 3100/7471MB CPU [9%@1510] GR3D_FREQ 37%@[1020] cpu@52C", 37),
        ("RAM 2100/3964MB CPU [4%@1479] GR3D_FREQ 99%@921 PLL@40C", 99),
        ("RAM 3100/7471MB CPU [9%@1510] cpu@52C", None),
        ("RAM 3100/7471MB GR3D_FREQ off", None),
    ],
)
def test_the_gpu_load_is_read_off_the_tegrastats_line(line: str, busy: int | None) -> None:
    """Orin prints the GPU clock after the load in brackets, older boards without them, and a
    line with no load at all has none to report rather than a guessed zero."""
    got = doctor._jetson_from_dump(_dump(tegrastats=line))
    assert got is not None and got.gpu_busy_pct == busy and got.tegrastats == line


def test_the_gpu_device_is_the_first_node_the_daemon_found() -> None:
    reply = _dump_json(gpu_node=False)
    reply["nodes"]["/dev/nvhost-ctrl-gpu"] = True
    reply["nodes"]["/dev/nvidia0"] = True
    got = doctor._jetson_from_dump(_board(reply))
    assert got is not None and got.gpu_device == "/dev/nvhost-ctrl-gpu"
    none = doctor._jetson_from_dump(_dump(gpu_node=False))
    assert none is not None and none.gpu_device is None


def test_the_host_section_reads_the_board_over_the_network(hostd: FakeHostd) -> None:
    report = doctor.collect(host=hostd.address)
    assert report.host is not None and report.host.ok is True and report.ok is True
    assert [r.path for r in hostd.requests] == ["/hello", "/healthz", "/board"]
    section = _host_section(_rendered(report))
    for needle in (
        f"Jetson at {hostd.address} (the board the daemon runs on)",
        "daemon quackd-jetson-hostd 0.1.0, protocol 1, on orin-nano",
        "camera 640x480 at 5 fps from csi, field of view 62.2 degrees",
        "detector yolov8n.pt on cuda",
        "health ok",
        ORIN_NANO,
        "36.4.3 (JetPack 6.2)",
        "shared with the GPU",
        "1.0 GiB, all zram: it compresses RAM rather than adding any (docs/guides/jetson.md)",
        "GPU device /dev/nvgpu/igpu0",
        "power mode 15W (nvpmodel -q)",
        "GPU busy 0% (GR3D_FREQ in tegrastats)",
        "GR3D_FREQ 0%@[305]",
    ):
        assert needle in section, needle


def test_the_board_rows_describe_the_board_and_never_this_machine(hostd: FakeHostd) -> None:
    """The section used to be about the machine doctor ran on, and said "here" to mean it. Read
    over the network, "here" is the laptop, which is the one thing the section is not about."""
    hostd.board = _dump_json(gpu_node=False, release=None, nvpmodel=None, tegrastats=None)
    section = _host_section(_rendered(doctor.collect(host=hostd.address)))
    assert not re.search(r"\bhere\b", section), section
    assert "this machine" not in section and "this is running on" not in section
    assert "none the daemon could see" in section and "quackd never asks for one" in section
    assert "could not read /etc/nv_tegra_release (No such file or directory)" in section
    assert "nvpmodel -q gave nothing (not on PATH)" in section
    assert "tegrastats gave nothing (not on PATH)" in section


def test_the_two_ways_l4t_can_be_unknown_read_differently(hostd: FakeHostd) -> None:
    """A file that is not there and a file it cannot read are different facts about the board,
    and the renderer once asserted the first for both. One of them is a daemon that cannot see
    the board's files; the other is a board this build has not met."""
    hostd.board = _dump_json(release="not a release line")
    unreadable = _host_section(_rendered(doctor.collect(host=hostd.address)))
    assert (
        "/etc/nv_tegra_release is on the board and its first line is not one this build knows"
        in (unreadable)
    )
    hostd.board = _dump_json(release=None)
    missing = _host_section(_rendered(doctor.collect(host=hostd.address)))
    assert "unknown: the daemon could not read /etc/nv_tegra_release" in missing
    assert "not one this build knows" not in missing


def test_a_daemon_on_a_machine_that_is_not_a_jetson_is_not_called_one(hostd: FakeHostd) -> None:
    """The daemon runs on a laptop for a test with `--camera fake`, and answers honestly that
    it is not a Tegra. A section titled Jetson there would be the one lie on the screen."""
    hostd.hello["capabilities"]["tegra"] = False
    hostd.hello["board_model"] = None
    hostd.board = _dump_json(release=None, device_tree=False, gpu_node=False)
    report = doctor.collect(host=hostd.address)
    assert report.ok is True and report.host is not None
    assert report.host.jetson is None and report.host.is_tegra is False
    title = doctor._host_title(report.host)
    assert title == f"host at {hostd.address} (the machine the daemon runs on)"
    assert "Jetson" not in title
    out = _rendered(report)
    assert "Jetson at" not in out and title in out
    assert "not a Jetson: no Tegra in its device tree, no /etc/nv_tegra_release" in out


def test_a_host_that_is_down_fails_the_report_with_a_row_saying_what_was_tried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one thing about a board that can fail doctor: its daemon not answering, which is
    what refuses a run with --host too. It says where it looked as well as what went wrong.

    A real closed port rather than a stub, so the sentence is the client's own. On Windows a
    refused loopback connect takes about the whole two-second window, which is the cost."""
    monkeypatch.setattr(doctor, "_host_client", HostClient)
    address = dead_address()
    report = doctor.collect(host=address)
    assert report.ok is False and report.host is not None and report.host.ok is False
    assert report.host.tried == f"http://{address}/hello"
    assert report.host.error is not None and address in report.host.error
    assert report.host.hello is None and report.host.jetson is None
    assert [s.state for s in report.host.servers] == ["down"] * 4, (
        "a daemon that is down still has the board's model servers asked: one can be up without it"
    )
    out = _rendered(report)
    assert f"host at {address}" in out and "Jetson at" not in out
    assert f"tried GET http://{address}/hello" in out
    assert "FAILURE" in out and " ".join(report.host.error.split()) in out


def test_a_board_name_that_finds_no_address_has_no_model_server_asked_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Four rows of "not running" under a host row saying no machine by that name exists would
    say the machine is there with its servers off, and each would wait on the same failed lookup
    first. So the presets there are reported as not asked. The lookup is refused here rather
    than made, because a real one for a missing name can take seconds to fail."""
    real = socket.getaddrinfo

    def lookup(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host == "nosuch-jetson.invalid":
            raise socket.gaierror(socket.EAI_NONAME, "no such name")
        return real(host, *args, **kwargs)

    probed: list[str] = []

    def probe(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        probed.append(url)
        return "down", "not running"

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(doctor, "_host_client", HostClient)
    monkeypatch.setattr(doctor, "_probe_models", probe)
    monkeypatch.delenv("QUACKD_BASE_URL", raising=False)
    report = doctor.collect(host="nosuch-jetson.invalid")
    assert report.ok is False and report.host is not None
    assert "no machine by that name could be found" in (report.host.error or "")
    assert probed == [], "nothing is asked at a machine with no address"
    not_asked = "not asked: nosuch-jetson.invalid has no address"
    assert [(s.preset, s.url, s.state, s.detail) for s in report.host.servers] == [
        ("ollama", "http://nosuch-jetson.invalid:11434/v1", "skipped", not_asked),
        ("vllm", "http://nosuch-jetson.invalid:8000/v1", "skipped", not_asked),
        ("llamacpp", "http://nosuch-jetson.invalid:8080/v1", "skipped", not_asked),
        ("lmstudio", "http://nosuch-jetson.invalid:1234/v1", "skipped", not_asked),
    ]
    out = _rendered(report)
    start = out.index("LLM servers, the presets on nosuch-jetson.invalid")
    servers = out[start : out.index(" adapters (--robot", start)]
    assert servers.count(not_asked) == 4 and "not running" not in servers


def test_a_daemon_that_wants_a_token_fails_the_report_until_it_has_one(hostd: FakeHostd) -> None:
    hostd.token = TOKEN
    refused = doctor.collect(host=hostd.address)
    assert refused.ok is False and refused.host is not None
    assert "--host-token" in (refused.host.error or "")
    given = doctor.collect(host=hostd.address, host_token=TOKEN)
    assert given.ok is True and given.host is not None and given.host.jetson is not None
    for report in (refused, given):
        assert TOKEN not in json.dumps(report.to_dict())
        assert TOKEN not in _rendered(report)


def test_a_token_the_daemon_echoes_back_reaches_neither_the_screen_nor_json(
    hostd: FakeHostd,
) -> None:
    """The client replaces the token in every reply as well as in every error, so a daemon that
    echoes it in the strings doctor prints, or in the ones --json carries whole, shows `<token>`
    there and never the token itself."""
    hostd.token = TOKEN
    hostd.without_camera(f"camera said {TOKEN}")
    hostd.without_detector(f"detector said {TOKEN}")
    hostd.hello["hostname"] = f"orin-{TOKEN}"
    hostd.healthz["ok"] = False
    hostd.healthz["reason"] = f"refused a client with token {TOKEN}"
    hostd.board = _dump_json(release=None, tegrastats=f"RAM 1/2MB {TOKEN} GR3D_FREQ 5%@[305]")
    hostd.board["errors"]["/etc/nv_tegra_release"] = f"no release file, token {TOKEN}"
    report = doctor.collect(host=hostd.address, host_token=TOKEN)
    assert report.ok is True and report.host is not None and report.host.jetson is not None
    shown, carried = _rendered(report), json.dumps(report.to_dict())
    for said in (
        "camera said <token>",
        "detector said <token>",
        "orin-<token>",
        "refused a client with token <token>",
        "RAM 1/2MB <token> GR3D_FREQ 5%",
        "no release file, token <token>",
    ):
        assert said in shown and said in carried, said
    assert TOKEN not in shown and TOKEN not in carried


def test_nothing_the_board_reports_changes_the_verdict(hostd: FakeHostd) -> None:
    """Once the daemon has answered, everything else is information. A board with no swap, no
    GPU node, no release file, a stopped camera and a detector on its CPU is a board this
    command has advice for, and a run with --host would still start on it."""
    hostd.board = _dump_json(
        swaps=NO_SWAPS, gpu_node=False, release=None, nvpmodel=None, tegrastats=None
    )
    hostd.healthz["ok"] = False
    hostd.healthz["reason"] = "the camera's last frame is 3.2s old (stale after 1.5s)"
    hostd.hello["detect"]["device"] = "cpu"
    report = doctor.collect(host=hostd.address)
    assert report.host is not None and report.host.jetson is not None
    assert report.host.jetson.swap_total_bytes == 0
    assert report.ok is True
    out = _rendered(report)
    assert "SUCCESS" in out
    for warning in (
        "swap none. A model that does not fit in memory cannot load",
        "health the camera's last frame is 3.2s old",
        "yolov8n.pt on cpu: the board's torch sees no CUDA",
    ):
        assert warning in out, warning


def test_a_board_or_health_that_fails_after_hello_is_a_warning_not_a_failure(
    hostd: FakeHostd,
) -> None:
    """`/hello` is what a run needs. A daemon that answered it and then failed the board dump
    or its own health is a daemon a run would still use, so the report says what failed and
    keeps its verdict."""
    hostd.replies["/board"] = CannedReply(500, b'{"ok": false, "reason": "the board is on fire"}')
    hostd.replies["/healthz"] = CannedReply(200, b"[1, 2, 3]")
    report = doctor.collect(host=hostd.address)
    assert report.ok is True and report.host is not None and report.host.ok is True
    assert report.host.jetson is None
    assert "the board is on fire" in (report.host.board_error or "")
    assert report.host.healthz_error
    out = _host_section(_rendered(report))
    assert f"Jetson at {hostd.address}" in out, "the daemon still said Tegra"
    assert "board could not be read" in out and "health could not be read" in out


def test_what_the_board_says_is_flattened_before_it_reaches_a_terminal(hostd: FakeHostd) -> None:
    """The daemon is another machine on the network, and an escape sequence in its hostname
    would be a command to whatever terminal prints this."""
    hostd.hello["hostname"] = "orin\x1b[2Jnano"
    hostd.board = _dump_json(tegrastats="RAM 1/2MB \x1b]0;owned\x07 GR3D_FREQ 5%@[305]")
    out = _rendered(doctor.collect(host=hostd.address))
    assert "\x1b" not in out and "\x07" not in out
    assert "orin [2Jnano" in out and "GPU busy 5%" in out


def test_doctor_json_carries_the_board_under_host(hostd: FakeHostd) -> None:
    result = CliRunner().invoke(app, ["doctor", "--json", "--host", hostd.address])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "jetson" not in payload, "the top-level key of 0.13 moved under host"
    for key in (
        "ok",
        "version",
        "python",
        "platform",
        "api_version",
        "core",
        "bundled_ducks",
        "providers",
        "llm_env_error",
        "steppers",
        "servers",
        "adapters",
        "transports",
        "extras",
        "assumptions",
        "pins",
        "robot",
    ):
        assert key in payload, key
    host = payload["host"]
    assert host["ok"] is True and host["error"] is None and host["address"] == hostd.address
    assert host["tried"] == f"http://{hostd.address}/hello"
    assert host["hello"]["hostname"] == "orin-nano"
    assert host["hello"]["capabilities"] == {"camera": True, "detect": True, "tegra": True}
    assert host["healthz"]["ok"] is True
    jetson = host["jetson"]
    assert jetson["board"] == ORIN_NANO
    assert jetson["l4t"] == "36.4.3" and jetson["jetpack"] == "6.2"
    assert jetson["swap_only_zram"] is True
    assert jetson["gpu_busy_pct"] == 0 and jetson["tegrastats"] == TEGRASTATS_LINE
    assert "docker_default_runtime" not in jetson


def test_doctor_json_with_a_refused_token_is_one_document_and_exit_code_one(
    hostd: FakeHostd,
) -> None:
    hostd.token = TOKEN
    result = CliRunner().invoke(app, ["doctor", "--json", "--host", hostd.address])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False and payload["host"]["ok"] is False
    assert "--host-token" in payload["host"]["error"] and payload["host"]["jetson"] is None
    given = CliRunner().invoke(
        app, ["doctor", "--json", "--host", hostd.address, "--host-token", TOKEN]
    )
    assert given.exit_code == 0, given.output
    assert TOKEN not in given.output


def test_doctor_finds_the_board_where_run_does(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The flag, then the host of the robot --robot names, then QUACKD_HOST: the ladder a run
    climbs, settled by the same function, so the two cannot disagree about a robot's board. The
    robot's token rides with its own board."""
    hostd.token = TOKEN
    Registry(tmp_path).add_robot(
        RobotEntry(name="jet", spec="microduck:mock", host=hostd.address, host_token=TOKEN)
    )

    def host_of(*args: str) -> dict[str, Any]:
        result = CliRunner().invoke(
            app, ["doctor", "--json", "--registry-dir", str(tmp_path), *args]
        )
        assert result.exit_code == 0, result.output
        return json.loads(result.output)["host"]

    stored = host_of("--robot", "jet")
    assert stored["address"] == hostd.address and stored["ok"] is True
    with FakeHostd() as other:
        assert host_of("--robot", "jet", "--host", other.address)["address"] == other.address
        monkeypatch.setenv("QUACKD_HOST", other.address)
        assert host_of("--robot", "jet")["address"] == hostd.address, "the robot beats the env"
        assert host_of()["address"] == other.address


def test_doctor_tells_a_robot_whose_stored_token_is_refused_to_store_a_new_one(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The robot's token outranks QUACKD_HOST_TOKEN, so the variable is set to the daemon's
    token here and the report still fails. The advice is the one that changes the token doctor
    and a run send, and never the variable, which told somebody to set what nothing reads."""
    hostd.token = TOKEN
    monkeypatch.setenv("QUACKD_HOST_TOKEN", TOKEN)
    Registry(tmp_path).add_robot(
        RobotEntry(name="jet", spec="microduck:mock", host=hostd.address, host_token="stale")
    )
    result = CliRunner().invoke(
        app, ["doctor", "--json", "--registry-dir", str(tmp_path), "--robot", "jet"]
    )
    assert result.exit_code == 1, result.output
    error = json.loads(result.output)["host"]["error"]
    assert "refused the token it was given: quackd robot edit jet --host-token TOKEN" in error
    assert "QUACKD_HOST_TOKEN" not in error
    assert TOKEN not in result.output and "stale" not in error


def test_a_typed_host_token_with_no_board_fails_the_report_and_nothing_is_asked() -> None:
    """Dropped, it read as a healthy report with no host section at all, and exit 0. `_NoBoard`
    fails this test if anything is asked, and --json stays one document."""
    result = CliRunner().invoke(app, ["doctor", "--json", "--host-token", TOKEN])
    assert result.exit_code == 1, result.output
    host = json.loads(result.output)["host"]
    assert (host["host"], host["ok"]) == ("", False)
    assert host["error"].startswith("--host-token needs a board")
    assert TOKEN not in result.output


def test_a_host_setting_that_is_no_machine_is_reported_and_nothing_is_asked_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Doctor is where a person comes to find a bad setting, and one in QUACKD_HOST is there
    without anybody having typed --host. So it is a failed host row in a whole report, and in
    the one document --json prints, as a bad --robot is. `_NoBoard` fails this test if anything
    is asked of it, and the presets stay on this machine. The refused value itself is never
    quoted back, since it can hold a secret written before an @, or an escape sequence: only a
    machine whose token was the trouble is named."""
    typed = CliRunner().invoke(app, ["doctor", "--host", "http://jetson.local:9874"])
    assert typed.exit_code == 1
    flat = " ".join(typed.output.split())
    assert "--host takes a machine, not a URL" in flat
    assert "bundled ducks" in flat, "the rest of the report is still there"
    assert "http://jetson.local:9874" not in flat and "host at" not in flat

    monkeypatch.setenv("QUACKD_HOST", "jetson.local/path")
    result = CliRunner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False and payload["core"] and payload["providers"]
    host = payload["host"]
    assert (host["host"], host["address"], host["ok"]) == ("", "", False)
    assert host["error"].startswith("QUACKD_HOST: --host takes a machine with no path")
    assert all("jetson.local" not in s["url"] for s in payload["servers"])
    monkeypatch.delenv("QUACKD_HOST")

    for args in (["--json"], []):
        secret = CliRunner().invoke(app, ["doctor", *args, "--host", f"{TOKEN}@jetson.local"])
        assert secret.exit_code == 1 and TOKEN not in secret.output
        assert "--host-token" in " ".join(secret.output.split())
    escaped = CliRunner().invoke(app, ["doctor", "--host", "orin\x1b[2Jnano"])
    assert escaped.exit_code == 1 and "\x1b" not in escaped.output

    monkeypatch.setenv("QUACKD_HOST_TOKEN", f"{TOKEN[:8]}\t{TOKEN[8:]}")
    bad_token = CliRunner().invoke(app, ["doctor", "--json", "--host", "jetson.local"])
    assert bad_token.exit_code == 1
    host = json.loads(bad_token.output)["host"]
    assert (host["host"], host["ok"]) == ("jetson.local", False)
    assert host["error"].startswith("QUACKD_HOST_TOKEN: the host token has a character")
    assert TOKEN[:8] not in bad_token.output and TOKEN[8:] not in bad_token.output


def test_the_servers_table_asks_the_presets_on_the_host(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a board named, the four presets are where a run with --host would reach them, and
    `local` keeps meaning QUACKD_BASE_URL. A password in that URL is probed with and never
    shown, because this screen and its spinner are pasted into issues."""
    probed: list[str] = []

    def probe(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        probed.append(url)
        return "down", "not running"

    monkeypatch.setattr(doctor, "_probe_models", probe)
    monkeypatch.setenv("QUACKD_BASE_URL", "http://me:hunter2@gpu-box:8000/v1")
    said: list[str] = []
    report = doctor.collect(host=hostd.address, progress=said.append)
    assert {s.preset: s.url for s in report.servers} == {
        "local": "http://me:***@gpu-box:8000/v1",
        "ollama": "http://127.0.0.1:11434/v1",
        "vllm": "http://127.0.0.1:8000/v1",
        "llamacpp": "http://127.0.0.1:8080/v1",
        "lmstudio": "http://127.0.0.1:1234/v1",
    }
    assert probed[0] == "http://me:hunter2@gpu-box:8000/v1", "the real URL is the one probed"
    assert report.host is not None
    assert [s.preset for s in report.host.servers] == ["ollama", "vllm", "llamacpp", "lmstudio"], (
        "the board's block holds what was asked on the board, and QUACKD_BASE_URL is not that"
    )
    out = _rendered(report)
    assert "LLM servers, the presets on 127.0.0.1" in out
    for shown in (out, json.dumps(report.to_dict()), " ".join(said)):
        assert "hunter2" not in shown

    probed.clear()
    doctor.collect()
    assert probed[1:] == [
        "http://localhost:11434/v1",
        "http://localhost:8000/v1",
        "http://localhost:8080/v1",
        "http://localhost:1234/v1",
    ], "without a board, the presets stay on this machine"


def test_the_progress_callback_names_each_network_step(hostd: FakeHostd) -> None:
    said: list[str] = []
    doctor.collect(host=hostd.address, progress=said.append)
    for step in ("asking the daemon at", "reading the health of", "reading the board at"):
        assert any(step in line and hostd.address in line for line in said), step


def test_the_host_section_survives_a_codepage_that_cannot_carry_it(
    monkeypatch: pytest.MonkeyPatch, hostd: FakeHostd
) -> None:
    """The same bar the rest of this command clears, on the section most likely to be pasted
    into an issue by somebody whose terminal is not UTF-8."""
    monkeypatch.setattr(sys, "platform", "linux")
    raw = io.BytesIO()
    console = Console(file=io.TextIOWrapper(raw, encoding="cp1252", errors="strict"), width=120)
    doctor.render(console, doctor.collect(host=hostd.address))
    console.file.flush()
    out = raw.getvalue().decode("cp1252")
    assert out.isascii()
    assert "Jetson at" in out and ORIN_NANO in out


# ── where Ollama put its models ─────────────────────────────────────────────────────────

ALL_GPU = PlacementRow("qwen3:8b", 5 * GIB, 5 * GIB)
PARTIAL = PlacementRow("llama3.3:70b", 40 * GIB, 15 * GIB)
ON_CPU = PlacementRow("tinyllama", GIB, 0)


def _ollama_up(
    monkeypatch: pytest.MonkeyPatch, rows: list[PlacementRow], note: str = ""
) -> list[str]:
    """An Ollama that answers wherever the ollama preset points, and what it has loaded. The
    list it returns fills with every root the placement probe is asked about."""
    asked: list[str] = []

    def models(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        return ("up", "qwen3:8b") if ":11434/" in url else ("down", "not running")

    def placement(root: str, timeout_s: float = 1.5) -> tuple[list[PlacementRow], str]:
        asked.append(root)
        return rows, note

    monkeypatch.setattr(doctor, "_probe_models", models)
    monkeypatch.setattr(doctor, "_probe_placement", placement)
    return asked


def test_ollama_on_the_jetson_says_where_each_model_sits(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All on the GPU, a share of it, or on the CPU with the fix the Jetson page gives for the
    commonest cause: the generic arm64 build, which has no Tegra CUDA."""
    asked = _ollama_up(monkeypatch, [ALL_GPU, PARTIAL, ON_CPU])
    report = doctor.collect(host=hostd.address)
    assert asked == ["http://127.0.0.1:11434"], "Ollama's own API is at the root, not under /v1"
    ollama = next(s for s in report.servers if s.preset == "ollama")
    assert ollama.placement is not None
    assert [(p.where, p.gpu_pct) for p in ollama.placement] == [
        ("gpu", 100),
        ("partial", 37),
        ("cpu", 0),
    ]
    out = _rendered(report)
    for needle in (
        "where ollama put its models (GET /api/ps)",
        "qwen3:8b all on the GPU, 5.0 GiB",
        "llama3.3:70b 37% on the GPU, the rest on the CPU",
        "tinyllama on the CPU: the generic arm64 build of Ollama has no Tegra CUDA, and the "
        "official installer picks the JetPack build (docs/guides/jetson.md)",
    ):
        assert needle in out, needle
    assert report.ok is True, "a model on the CPU is slow, not broken"
    payload = report.to_dict()
    row = next(s for s in payload["servers"] if s["preset"] == "ollama")
    assert [p["where"] for p in row["placement"]] == ["gpu", "partial", "cpu"]
    assert row["placement"][1] == {
        "model": "llama3.3:70b",
        "size": 40 * GIB,
        "size_vram": 15 * GIB,
        "gpu_pct": 37,
        "where": "partial",
    }
    on_board = {s["preset"]: s for s in payload["host"]["servers"]}
    assert set(on_board) == {"ollama", "vllm", "llamacpp", "lmstudio"}, "local is not the board's"
    assert on_board["ollama"] == row, "the board's block carries the same placement rows"


def test_ollama_on_this_machine_on_its_cpu_is_a_note_not_the_jetson_pitfall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _ollama_up(monkeypatch, [ON_CPU])
    out = _rendered(doctor.collect())
    assert asked == ["http://localhost:11434"]
    assert "tinyllama on the CPU: this Ollama put none of it on a GPU" in out
    assert "Tegra" not in out


def test_an_ollama_with_nothing_loaded_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _ollama_up(monkeypatch, [], "nothing loaded yet: Ollama loads a model on its first request")
    report = doctor.collect()
    assert report.ok is True
    assert "loaded nothing loaded yet: Ollama loads a model on its first request" in _rendered(
        report
    )


def test_a_model_ollama_did_not_place_is_named_and_flattened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The note can name what another machine's Ollama sent, so it is flattened like every other
    string from there before it reaches a terminal."""
    note = "llama\x1b[2J3b is loaded, and /api/ps did not say how much of it is on the GPU"
    _ollama_up(monkeypatch, [], note)
    out = _rendered(doctor.collect())
    assert "\x1b" not in out
    assert "loaded llama [2J3b is loaded, and /api/ps did not say" in out


def test_a_server_that_is_down_is_never_asked_where_its_models_are() -> None:
    """The placement probe raises in this file unless a test fakes it, so reaching this line
    means it was not called for an ollama row that is down."""
    report = doctor.collect()
    assert all(s.placement is None for s in report.servers)


@pytest.mark.parametrize(
    ("size", "vram", "where", "pct"),
    [
        (100, 100, "gpu", 100),
        (100, 150, "gpu", 100),
        (100, 99, "partial", 99),
        (1000, 999, "partial", 99),
        (1000, 1, "partial", 1),
        (100, 0, "cpu", 0),
    ],
)
def test_a_split_never_rounds_into_all_or_nothing(
    size: int, vram: int, where: str, pct: int
) -> None:
    """99.9% is not all on the GPU and 0.1% is not all on the CPU: each is a model split
    across both, which is the case worth a warning."""
    got = PlacementRow("m", size, vram)
    assert (got.where, got.gpu_pct) == (where, pct)


@pytest.mark.parametrize(
    ("url", "root"),
    [
        ("http://localhost:11434/v1", "http://localhost:11434"),
        ("http://jetson.local:11434/v1/", "http://jetson.local:11434"),
        ("http://[::1]:11434/v1", "http://[::1]:11434"),
        ("http://gpu-box:11434", "http://gpu-box:11434"),
    ],
)
def test_ollamas_own_api_is_the_preset_without_v1(url: str, root: str) -> None:
    assert doctor._ollama_root(url) == root


# ── the two probes against a real socket ────────────────────────────────────────────────


@pytest.fixture
def canned() -> Iterator[Callable[..., str]]:
    """A loopback server that answers one path with one body, for the two probes that read
    whatever a model server sends. `canned(path, body, status=200)` returns its root URL."""
    routes: dict[str, tuple[int, bytes]] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, body = routes.get(self.path, (404, b"404 page not found"))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()

    def serve(path: str, body: bytes, status: int = 200) -> str:
        routes[path] = (status, body)
        return f"http://127.0.0.1:{server.server_address[1]}"

    try:
        yield serve
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("body", "state", "detail"),
    [
        (
            b'{"object": "list", "data": [{"id": "qwen3:8b"}, {"id": "gemma3:4b"}]}',
            "up",
            "qwen3:8b, gemma3:4b",
        ),
        (b'{"object": "list", "data": null}', "up", "no models loaded"),
        (b'{"object": "list"}', "up", "no models loaded"),
        (b'[{"id": "qwen3:8b"}]', "http", "not a list of models"),
        (b'{"data": "qwen3:8b"}', "http", "not a list of models"),
        (b'"hello"', "http", "not a list of models"),
        (b"<html>it works</html>", "down", "not running"),
    ],
)
def test_a_models_reply_of_any_shape_is_a_row_and_never_an_exception(
    canned: Callable[..., str], body: bytes, state: str, detail: str
) -> None:
    """`payload.get("data", [])` sat outside the try, so a JSON list, or `"data": null` from an
    Ollama with nothing pulled, raised out of `collect()` and took the whole report with it. A
    body that is not JSON keeps the row it always had: a dev web server's index page on 8000 or
    8080 is no model server there."""
    root = canned("/v1/models", body)
    got_state, got_detail = _REAL_PROBE_MODELS(f"{root}/v1")
    assert got_state == state and detail in got_detail


def test_a_list_reply_on_a_real_socket_leaves_the_report_standing(
    canned: Callable[..., str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The crash was in `collect()`, so the fix is shown there too: the local row points at a
    server that answers with a JSON list, and the report is a report."""
    root = canned("/v1/models", b"[]")
    monkeypatch.setenv("QUACKD_BASE_URL", f"{root}/v1")

    def only_the_canned_one(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        return _REAL_PROBE_MODELS(url) if url.startswith(root) else ("down", "not running")

    monkeypatch.setattr(doctor, "_probe_models", only_the_canned_one)
    report = doctor.collect()
    local = next(s for s in report.servers if s.preset == "local")
    assert local.state == "http" and "not a list of models" in local.detail
    assert report.ok is True


def test_the_placement_probe_reads_ollamas_own_api(canned: Callable[..., str]) -> None:
    body = {
        "models": [
            {"name": "qwen3:8b", "model": "qwen3:8b", "size": 6 * GIB, "size_vram": 6 * GIB},
            {"model": "llama3.3:70b", "size": 40 * GIB, "size_vram": 15 * GIB},
            {"name": "tinyllama", "size": GIB, "size_vram": 0},
            {"name": "broken", "size": "big", "size_vram": 0},
            {"name": "empty", "size": 0, "size_vram": 0},
            {"name": "flagged", "size": True, "size_vram": True},
            "not a model",
        ]
    }
    root = canned("/api/ps", json.dumps(body).encode())
    rows, note = _REAL_PROBE_PLACEMENT(root)
    assert [(r.model, r.where) for r in rows] == [
        ("qwen3:8b", "gpu"),
        ("llama3.3:70b", "partial"),
        ("tinyllama", "cpu"),
    ]
    assert note == ""


@pytest.mark.parametrize(
    ("status", "body", "note"),
    [
        (200, b'{"models": []}', "nothing loaded yet"),
        (200, b'{"models": null}', "nothing loaded yet"),
        (200, b"[]", "not Ollama's list"),
        (200, b'{"models": "qwen3:8b"}', "not Ollama's list"),
        (
            200,
            b'{"models": [{"name": "llama3.2:3b", "size": 3000000000}]}',
            "llama3.2:3b is loaded, and /api/ps did not say how much of it is on the GPU",
        ),
        (
            200,
            b'{"models": [{"name": "qwen:0.5b", "size": 0, "size_vram": 0}, {"model": "phi3"}]}',
            "qwen:0.5b, phi3 are loaded, and /api/ps did not say how much of them is on the GPU",
        ),
        (200, b"<html>", "not JSON"),
        (404, b"404 page not found", "HTTP 404"),
    ],
)
def test_the_placement_probe_says_why_it_has_no_rows_and_never_raises(
    canned: Callable[..., str], status: int, body: bytes, note: str
) -> None:
    root = canned("/api/ps", body, status)
    rows, said = _REAL_PROBE_PLACEMENT(root)
    assert rows == [] and note in said


def test_the_header_names_the_architecture() -> None:
    """`Linux 5.15.148-tegra` and `Linux 5.15.148-generic` are two different machines, and the
    difference that matters for a wheel is the one the header did not carry."""
    report = doctor.collect()
    assert report.platform.split()[-1] == platform.machine()
    buf = io.StringIO()
    doctor.render(Console(file=buf, width=200), report)
    assert platform.machine() in buf.getvalue()


# ── what the probe does to a real arm: park it, and look through every camera ───────────

AWAY_FROM_REST = {"shoulder_pan": 40.0, "shoulder_lift": -80.0}
"""A pose the mock arm does not start in, so reaching it takes an actual move."""


def _probe_of(report: doctor.DoctorReport) -> doctor.ProbeReport:
    assert report.robot is not None and report.robot.error is None, report.robot
    assert report.robot.probe is not None and report.robot.probe.error is None, report.robot.probe
    return report.robot.probe


def _row(report: doctor.DoctorReport, what: str) -> doctor.ProbeRow:
    rows = _probe_of(report).rows
    found = [r for r in rows if r.what == what]
    assert len(found) == 1, f"wanted one {what!r} row, the probe reported {[r.what for r in rows]}"
    return found[0]


def _probed(monkeypatch: pytest.MonkeyPatch, transport: Any, **kwargs: Any) -> doctor.DoctorReport:
    """Probe one transport the test built and can read afterwards.

    `probe` makes its own adapter through the factory, so that call is the only seam a fake
    fits through. What these tests need on the other side of it reaches no command line: an
    arm that refuses to move, and a second camera."""
    monkeypatch.setattr(
        "quackd.adapters.factory.make_adapter", lambda *_a, **_k: LeRobotAdapter(transport)
    )
    return doctor.collect("lerobot:mock", address="mock://arm", **kwargs)


def test_doctor_returns_a_probed_arm_to_its_rest_pose_and_reports_that_it_did() -> None:
    """The probe is the one command that moves an arm without being given a task, and it has
    to be. It used to connect, ask its questions and disconnect, and a LeRobot arm goes limp
    the moment it is disconnected, which is how the bench arm fell at the end of a check.

    The factory is left alone here on purpose: a pose handed to `collect` reaching the arm at
    all is half of what this pins, and "returned to it" is only possible if it arrived."""
    report = doctor.collect("lerobot:mock", address="mock://arm", rest_pose=AWAY_FROM_REST)
    row = _row(report, "rest pose")
    assert (row.value, row.state) == ("returned to it", "ok")
    assert report.ok is True, "parking the arm is how a probe ends, not a fault to report"
    advisories = _probe_of(report).advisories
    assert not any("torque was left on" in a for a in advisories), advisories


def test_an_arm_already_at_its_rest_pose_says_so_rather_than_driving_it_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing on this arm reports whether a goal was reached, so the rest move compares the
    goal with the measured position. An arm that is already parked is left alone: re-sending
    the pose would push servos at a goal they are already holding, for a row that would read
    the same either way."""
    arm = LeRobotMock(rest_pose=dict(REST))
    report = _probed(monkeypatch, arm, rest_pose=dict(REST))
    row = _row(report, "rest pose")
    assert (row.value, row.state) == ("at it already", "ok")
    assert arm.actions == [], f"an arm already at rest was sent {arm.actions}"
    assert arm.sequence == ["rest", "close"], arm.sequence
    assert arm.torque is False, "an arm at its rest pose can be let go of"
    assert report.ok is True


def test_a_rest_pose_past_the_travel_is_advice_and_the_verdict_stays_green() -> None:
    """The bench's first step after the fix: doctor on an arm whose recorded fold lies past
    its calibrated travel. The arm parks at the edge of the travel, which is the pose it can
    be driven to, so the row is the ordinary green one and torque is released. What the person
    needs to know about the fold is advice, in the arm's own numbers, and failing the verdict
    over it would say an arm that did everything right is broken."""
    from quackd_lerobot.mock import MOCK_RANGES

    ceiling = MOCK_RANGES["elbow_flex"][1]
    pose = dict(AWAY_FROM_REST) | {"elbow_flex": ceiling + 12.0}
    report = doctor.collect("lerobot:mock", address="mock://arm", rest_pose=pose)
    row = _row(report, "rest pose")
    assert (row.value, row.state) == ("returned to it", "ok")
    assert report.ok is True, "parking at the reachable pose is not a fault"
    advisories = _probe_of(report).advisories
    said = [a for a in advisories if "lerobot-calibrate" in a]
    assert len(said) == 1, advisories
    assert f"elbow_flex is recorded at {pose['elbow_flex']:.0f}" in said[0], said[0]
    assert f"driven to {ceiling:.0f} and no further" in said[0], said[0]
    assert not any("torque was left on" in a for a in advisories), advisories


def test_a_robot_with_no_rest_pose_recorded_says_how_to_record_one_and_still_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not recording a pose is a choice, the way an extra nobody installed is a choice, and a
    robot that has always been let go of where it stood is not suddenly broken. Torque still
    drops, which is the behaviour of every body here except an arm with a pose to hold."""
    arm = LeRobotMock()
    report = _probed(monkeypatch, arm)
    row = _row(report, "rest pose")
    assert row.value == "none recorded (quackd robot rest-pose <name>)"
    assert row.state == "plain", "a pose nobody recorded is not a failure"
    assert report.ok is True
    assert arm.torque is False, "with no pose to hold, the arm is released as it always was"
    assert arm.close_note is None


def test_an_arm_that_arrived_and_was_still_held_at_the_close_fails_the_verdict_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gap between the two reports. The rest move says it arrived, and the disconnect's
    own re-read is a separate reading that can disagree with it: the arm drifted, or it
    stopped answering. When it does, torque is kept and a note is written, and reading only
    the rest move would print that note under a green tick and exit 0.

    A person runs `doctor` to be told whether they can walk away. Saying yes over a note that
    says the arm is still powered is the one answer this command must never give."""
    from quackd_lerobot.verbs import torque_left_on

    arm = LeRobotMock(rest_pose=dict(REST))

    async def arrive_then_drift() -> None:
        arm.sequence.append("close")
        # the arm's own sentence rather than a copy of it, so the fake says what the arm does
        arm.close_note = torque_left_on("it stopped answering", None)

    monkeypatch.setattr(arm, "close", arrive_then_drift)
    report = _probed(monkeypatch, arm, rest_pose=dict(REST))

    assert _row(report, "rest pose").state == "ok", "the move itself did report arriving"
    advisories = _probe_of(report).advisories
    assert any("torque was left on" in a for a in advisories), advisories
    assert report.ok is False, "a green verdict over a torque note walks somebody away"


def test_an_arm_that_cannot_reach_its_rest_pose_fails_the_verdict_and_says_torque_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An arm that stalled on the way to its rest pose is the one case where the disconnect
    must not drop torque, because dropping it is how the arm falls. That leaves the arm
    holding itself up with nothing on the machine saying so, so doctor fails the verdict and
    repeats the note the close left behind."""
    arm = LeRobotMock(rest_pose=AWAY_FROM_REST, rest_fails="elbow_flex is at 12 with a goal of 90")
    report = _probed(monkeypatch, arm, rest_pose=AWAY_FROM_REST)
    row = _row(report, "rest pose")
    assert row.value == "not reached: elbow_flex is at 12 with a goal of 90"
    assert row.state == "fail"
    assert _probe_of(report).ok is False
    assert report.ok is False, "an arm left holding itself up is not a machine in a good state"
    advisories = _probe_of(report).advisories
    assert any("torque was left on" in a for a in advisories), advisories
    assert arm.torque is True and arm.close_note is not None
    buf = io.StringIO()
    Console(file=buf, width=200).print(doctor.verdict(report))
    said = " ".join(buf.getvalue().split())
    assert "FAILURE" in said and "rest pose: not reached" in said, said


class _ConnectedOnRetry(LeRobotMock):
    """An arm whose connect had to be made again, reported the way the real backend reports
    it: `connect_notes`, filled by the connect that just happened."""

    def __init__(self, notes: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._retries = list(notes)
        self.connect_notes: list[str] = []

    async def connect(self) -> Any:
        connected = await super().connect()
        self.connect_notes = list(self._retries)
        return connected


def test_a_connect_the_arm_had_to_make_again_is_advice_and_the_verdict_stays_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bench, 2026-09-23: connects failed on one lost packet on the bus, and the next connect
    went through. The real backend now tries again by itself, and a probe that connected on a
    later attempt did connect: the row says so and the verdict stays green. What the person
    needs is which joint the bus dropped a packet on, because one that does it every time is a
    cable to look at, so every retry the arm reports is listed as advice, first, in its own
    words, before the probe's other advice."""
    said = [
        "the bus lost a packet on elbow_flex while connecting, and connect ran again",
        "and on wrist_roll the second time, and the next connect went through",
    ]
    arm = _ConnectedOnRetry(said, rest_pose=dict(REST))
    report = _probed(monkeypatch, arm, rest_pose=dict(REST))
    assert _row(report, "connected").value == "yes"
    advisories = _probe_of(report).advisories
    assert advisories[: len(said)] == said, advisories
    assert report.ok is True, "a connect that went through on a retry is a connect"


class _TwoEyes(LeRobotMock):
    """An arm with two cameras, answering `camera_health()` the shape the real backend does.

    Each row leaves its size out: `probe` fills that in from the frames it actually read, so
    a camera can only claim a size by having handed a picture over."""

    camera_keys = ("top", "side")

    def __init__(self, *, blind: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.blind = blind

    async def get_frames(self) -> list[CameraFrame]:
        image = await self.get_frame()
        if image is None:
            return []
        live = [name for name in self.camera_keys if name not in self.blind]
        # the first camera is the primary, and it is marked rather than assumed: a body whose
        # primary lens is the blind one must not promote the other into its place
        return [CameraFrame(n, image, primary=n == self.camera_keys[0]) for n in live]

    def camera_health(self) -> dict[str, Any]:
        return {
            "configured": True,
            "url": "opencv://0?name=top",
            "ok": "top" not in self.blind,
            "age_s": 0.0,
            "size": None,
            "error": None,
            "cameras": [
                {
                    "name": name,
                    "url": f"opencv://{index}?name={name}",
                    "ok": name not in self.blind,
                    "age_s": None if name in self.blind else 0.0,
                    "size": None,
                    "error": "TimeoutError: no frame in 2.0 s" if name in self.blind else None,
                }
                for index, name in enumerate(self.camera_keys)
            ],
        }


class _OneEye(LeRobotMock):
    """One camera, answering exactly the dict every backend answered before there were two."""

    def camera_health(self) -> dict[str, Any]:
        return {
            "configured": True,
            "url": "opencv://0",
            "ok": True,
            "age_s": 0.0,
            "size": "128x128",
            "error": None,
        }


def test_the_probe_gives_each_camera_a_row_and_names_the_one_that_gave_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A camera URL nothing checks is a camera URL that fails at the first observe, which is
    why the probe asks for a picture rather than accepting the url. With two of them, "no
    frame came back" does not say which eye closed, and the arm goes on working with the
    other one, so both the row and the advisory have to name it."""
    arm = _TwoEyes(blind=("side",))
    report = _probed(monkeypatch, arm, camera_url="opencv://0?name=top")
    top, side = _row(report, "camera top"), _row(report, "camera side")
    assert (top.value, top.state) == ("128x128", "ok")
    assert (side.value, side.state) == ("no frame", "fail")
    assert _probe_of(report).ok is False
    assert report.ok is False, "a camera that sent nothing fails the machine's verdict"
    advisories = _probe_of(report).advisories
    assert any("from side" in a and "observe" in a for a in advisories), advisories


def test_a_single_camera_still_reads_under_the_row_label_it_always_had(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A camera's name is only spoken by a body that has more than one. Labelling the row
    "camera camera" on every one-camera robot would rename the row everybody reading this
    output already knows, in exchange for a word that is quackd's own default and not
    anything its owner chose."""
    report = _probed(monkeypatch, _OneEye(), camera_url="opencv://0")
    row = _row(report, "camera")
    assert (row.value, row.state) == ("128x128", "ok")
    labels = [r.what for r in _probe_of(report).rows if r.what.startswith("camera")]
    assert labels == ["camera"], labels
    assert report.ok is True
