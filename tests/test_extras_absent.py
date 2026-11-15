"""The default install never imports an SDK: every adapter lists, describes and constructs
with torch, lerobot, roslibpy, zmq, zeroconf and paho all absent."""

from __future__ import annotations

import subprocess
import sys

import pytest

from roboglot.adapters.catalogue import BY_NAME

ADAPTERS = tuple(BY_NAME)
"""The ten Roboglot publishes and the row of bodies it describes, in the order every table that
prints them uses."""

HEAVY = (
    "torch",
    "lerobot",
    "roslibpy",
    "zmq",
    "zeroconf",
    "paho",
    "onnxruntime",
    # The three clients a decision LLM can be reached through: the System One SDK for a server,
    # hosted or your own, the in-process encoder, and OpenAI's SDK for its Decisions API. All
    # three are off by default, so a default install must import the modules that know about
    # them, build a run config and refuse `--decision-mode on` politely, with none of these
    # anywhere on the machine. `openai` is the pilot's SDK too, which is why it is blocked here
    # in a process of its own rather than checked for in this one: the suite imports it
    # wherever it is installed.
    "typesafe_sdk",
    "laya",
    "openai",
    # The page `roboglot ui` serves: the CLI, and every plain module the page is built on, import
    # without it, and only `roboglot ui` itself asks for it, naming the extra.
    "streamlit",
    # The Bluetooth half of `roboglot onboard`, Linux only, from the onboard extra: the radio's
    # seam, its sim and its pure parts import without them, and asking for the real radio
    # names the extra, or the platform, before anything that needs them is imported.
    "bleak",
    "dbus_fast",
)

SCRIPT = f"""
import sys
for name in {HEAVY!r}:
    sys.modules[name] = None  # any import of it now raises ImportError
import roboglot
import roboglot.cli
import roboglot.doctor
import roboglot.lan
import roboglot.lan.announce
import roboglot.lan.discover
import roboglot.team.mqtt_bus
import roboglot.registry
import roboglot.agent.decision
import roboglot.agent.decision.stepper
import roboglot.agent.decision.factory
import roboglot.agent.loop
import roboglot.webui
import roboglot.webui.catalog
import roboglot.webui.jobs
import roboglot.webui.launch
import roboglot.webui.options
import roboglot.webui.probe
from roboglot.agent.decision.catalogue import PRESETS
from roboglot.agent.decision.factory import decision_llm_is_available, resolve_decision_mode
assert resolve_decision_mode(None, named=False) == "off"
ok, why = decision_llm_is_available(PRESETS["jev"])
assert not ok and "roboglot[decision]" in why, why
ok, why = decision_llm_is_available(PRESETS["laya"])
assert not ok and "roboglot[laya]" in why, why
ok, why = decision_llm_is_available(PRESETS["openai"])
assert not ok and "roboglot[openai]" in why, why
ok, why = decision_llm_is_available(PRESETS["openai"], url="http://127.0.0.1:8765")
assert not ok and "roboglot[openai]" in why, why
from roboglot.adapters.catalogue import DESCRIBED_BODIES
from roboglot.adapters.factory import BACKENDS, RobotSpec, describe, list_adapters, make_adapter
rows = list_adapters()
assert [r["name"] for r in rows] == [
    "microduck",
    "lerobot",
    "rosbridge",
    "open_duck",
    "xlerobot",
    "alohamini",
    "toddlerbot",
    "huebot",
    "moss",
    "rosarm",
    "described",
], rows
# every adapter that needs a library reports itself unusable when that library is gone. Asked
# through `sdk` rather than through `extra`, because an extra now also buys the adapter package
# itself, and a body with no SDK at all (the Open Duck, the ToddlerBot) is unaffected by torch
# being absent and should not claim to be.
assert not any(r["installed"] for r in rows if r["sdk"] is not None), rows
assert not any(r["sdk"] for r in rows), rows
for adapter, backends in BACKENDS.items():
    for backend in backends:
        m = describe(RobotSpec(adapter, backend))
        assert "stop" in m.verb_names(), (adapter, backend)
# a body Roboglot only describes needs no library at all, so every one of them reads with all gone
for body in DESCRIBED_BODIES:
    m = describe(RobotSpec("described", body))
    assert m.verb_names() == ["report_state", "stop"], body
make_adapter("lerobot:real", address="COM5")
make_adapter("rosbridge:ws", address="ws://robot.local:9090")
make_adapter("huebot:jsonrpc", address="tcp://127.0.0.1:9870")
# the industrial arms' rehearsal builds over its fake bridge, and their bound code is refused in the
# owner rule's words before anything is built, roslibpy or no roslibpy
make_adapter(RobotSpec("rosarm", "mock", robot_type="yaskawa-motomini"))
from roboglot.adapters.base import AdapterError
try:
    make_adapter("rosarm:ws", address="ws://cell.local:9090")
except AdapterError as e:
    assert "docs/adr/0076" in str(e), e
else:
    raise AssertionError("rosarm:ws was built")
# MOSS's cockpit backend builds with no address, pointed at the tunnel's near end, and opens
# nothing until it connects
make_adapter("moss:cockpit")
from roboglot.adapters.factory import needs_rest_pose
# read off the adapter's own declaration, which needs no SDK: the real arm alone must have a pose
assert needs_rest_pose("lerobot:real")
assert not needs_rest_pose("lerobot:mock")
# a policy server, its client, their protocol and the endpoint a run reaches one on need no
# torch either: a checkpoint is loaded in the server's own process when it starts, never when
# any of them is imported, and a scripted policy is served with nothing loaded at all
import roboglot_lerobot.policy.client
import roboglot_lerobot.policy.endpoint
import roboglot_lerobot.policy.endpoint_upstream_api
import roboglot_lerobot.policy.protocol
import roboglot_lerobot.policy.server
import roboglot_lerobot.policy.upstream_api
roboglot_lerobot.policy.server.served_policy(
    roboglot_lerobot.policy.server.ServeOptions(policy="scripted:sweep")
)
# the pipeline a checkpoint loads through imports torch, LeRobot and huggingface_hub only when
# it loads one, so its checks of a checkpoint's JSON run with none of them, and a checkpoint
# named without them is refused in words that name the extra, before anything is fetched
import roboglot_lerobot.policy.fit
import roboglot_lerobot.policy.pipeline
roboglot_lerobot.policy.pipeline.check_processor(
    {{"steps": [{{"registry_name": "device_processor", "config": {{"device": "cpu"}}}}]}},
    "policy_preprocessor.json",
    "owner/policy@main",
    "act",
)
try:
    roboglot_lerobot.policy.server.served_policy(
        roboglot_lerobot.policy.server.ServeOptions(policy="owner/policy@main", fps=10.0)
    )
except roboglot_lerobot.policy.server.ServeRefused as e:
    assert "roboglot[lerobot-vla]" in str(e), e
else:
    raise AssertionError("a checkpoint was served with no torch installed")
# and an arm handed a policy server builds the server's client and asks it nothing until it
# is asked, so naming one on the command line needs no torch and no LeRobot either
from roboglot.adapters.base import PolicyChoice
_policy = PolicyChoice("http://127.0.0.1:9875", "0123456789abcdef0123456789abcdef")
make_adapter("lerobot:real", address="COM5", policy=_policy)
assert describe(RobotSpec("lerobot", "real"), policy=_policy).provides("manipulate")
# the arm's follower reimplements the one LeRobot function it needs rather than import LeRobot
import roboglot_lerobot.follower
capped = roboglot_lerobot.follower.ensure_safe_goal_position({{"a": (9.0, 0.0)}}, 1.0)
assert capped == {{"a": 1.0}}, capped
# the radio: with no onboard extra, asking for Bluetooth says which extra, or which platform it
# would take, and the module that talks to BlueZ is never imported to find that out. The sim air,
# the links, proximity, meeting and the loop they run on are plain Python and import without it.
import roboglot.radio
import roboglot.radio.bluez
import roboglot.radio.bluez.core
import roboglot.radio.bluez.privacy
import roboglot.radio.fake
import roboglot.radio.links
import roboglot.radio.loop
import roboglot.radio.meet
import roboglot.radio.preflight
import roboglot.radio.proximity
import roboglot.radio.rotation
import roboglot.radio.sim
import roboglot.radio.statusview
import roboglot.radio.transport
try:
    roboglot.radio.open_radio("bluez")
except roboglot.radio.RadioUnavailable as e:
    assert e.state in ("not-installed", "unsupported"), e.state
    assert "roboglot[onboard]" in (e.hint or "") or "Linux" in str(e), (str(e), e.hint)
else:
    raise AssertionError("a BlueZ radio was built with neither bleak nor dbus-fast")
assert "roboglot.radio.bluez.radio" not in sys.modules
for name in {HEAVY!r}:
    assert sys.modules.get(name) is None, name
print("OK")
"""


def test_everything_imports_without_any_extra() -> None:
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_the_default_path_did_not_import_a_heavy_module() -> None:
    import roboglot.team.mqtt_bus  # noqa: F401

    for name in ("torch", "lerobot", "roslibpy", "typesafe_sdk", "laya"):
        assert name not in sys.modules, f"{name} was imported on the default path"


def test_naming_the_decision_llms_costs_nothing_to_import() -> None:
    """`roboglot.cli` reads the preset table for `--help` and for `doctor`, and the table is
    data: names, one-line summaries, urls and rates, with not an import of a client in it.

    Both clients weigh what `torch` weighs -- `laya` pulls it directly -- so importing either
    one to print a help line would put several seconds on the front of every single command,
    including the ones that never go near a decision LLM."""
    import roboglot.cli  # noqa: F401

    for name in ("typesafe_sdk", "laya"):
        assert name not in sys.modules, f"{name} was imported just by loading the CLI"


def test_a_machine_with_no_adapter_installed_still_works_and_says_what_to_install() -> None:
    """The claim the split exists to make. Roboglot on its own is the loop, the executor and the
    contract, so it has to import, list every robot it publishes and refuse to guess at a body,
    all without a single adapter present.

    Driven through the discovery seam rather than by uninstalling anything, because the suite
    runs in an environment that has all ten and a test may not take them away from it."""
    import roboglot.adapters.factory as factory
    from roboglot.adapters.base import AdapterNotInstalled
    from roboglot.adapters.factory import NoRobotNamed

    factory._installed.cache_clear()
    real = factory._installed
    factory._installed = dict  # type: ignore[assignment]  # no entry points, nothing importable
    try:
        rows = factory.list_adapters()
        assert [r["name"] for r in rows] == list(ADAPTERS), rows
        assert not any(r["adapter_installed"] for r in rows), rows
        assert all(r["extra"].startswith("roboglot[") for r in rows if r["kind"] == "adapter"), rows

        # a spec still parses, so a robot can be registered, printed and reasoned about on a
        # machine that cannot build it. Asking for the body itself is what refuses.
        assert factory.parse_robot_spec("lerobot:real").key == "lerobot:real"
        with pytest.raises(AdapterNotInstalled, match=r"roboglot\[lerobot\]"):
            factory.describe(factory.parse_robot_spec("lerobot:real"))
        with pytest.raises(AdapterNotInstalled, match=r"roboglot\[lerobot\]"):
            factory.make_adapter("lerobot:mock")

        with pytest.raises(NoRobotNamed, match="no robot adapter is installed"):
            factory.resolve_robot(None)

        # `doctor` is where `README.md` sends a new reader to find out what this machine has,
        # and it still exits 0, because an uninstalled adapter is a choice rather than a
        # fault. What it may not do is say the scripted pilot runs here. It does not: `roboglot
        # run` on this machine refuses for want of a robot, and a green verdict claiming otherwise
        # is this command saying the opposite of the one thing it exists to say.
        import io as _io

        from rich.console import Console

        from roboglot import doctor

        report = doctor.collect()
        assert report.ok, "a machine with no robot is still a working Roboglot"
        buf = _io.StringIO()
        Console(file=buf, width=200).print(doctor.verdict(report))
        said = buf.getvalue()
        assert "no robot adapter is installed" in said, said
        assert "the scripted pilot runs here" not in said, said
        from roboglot.adapters.factory import ADAPTER_NAMES

        counted = f"0/{len(ADAPTER_NAMES)} adapters installed"
        assert counted in " ".join(said.split()), said

        # a pilot team does not reach past the refusal for a hardcoded duck either
        from roboglot.team.pilots import member_specs

        with pytest.raises(NoRobotNamed, match="no robot adapter is installed"):
            member_specs(["a", "b"], None, None)
    finally:
        factory._installed = real
        factory._installed.cache_clear()


def test_the_only_adapter_installed_is_the_one_a_command_means() -> None:
    """A machine with one robot has no ambiguity to resolve, so making its owner type the name
    would be ceremony. With several installed Roboglot will not invent an answer, and the
    Microduck among them is no exception: there is no default robot."""
    import roboglot.adapters.factory as factory

    factory._installed.cache_clear()
    real = factory._installed
    try:
        factory._installed = lambda: {"lerobot": "roboglot_lerobot"}  # type: ignore[assignment]
        assert factory.resolve_robot(None).key == "lerobot:mock"

        factory._installed = lambda: {  # type: ignore[assignment]
            "lerobot": "roboglot_lerobot",
            "rosbridge": "roboglot_rosbridge",
        }
        with pytest.raises(factory.NoRobotNamed, match="no robot named"):
            factory.resolve_robot(None)

        factory._installed = lambda: {  # type: ignore[assignment]
            "microduck": "roboglot_microduck",
            "lerobot": "roboglot_lerobot",
        }
        with pytest.raises(factory.NoRobotNamed, match="no robot named"):
            factory.resolve_robot(None)
    finally:
        factory._installed = real
        factory._installed.cache_clear()


def test_a_verdict_speaks_only_for_the_robots_this_machine_has() -> None:
    """An `infeasible` verdict ends a run by naming which other body could have done the task,
    and it builds each answer from that adapter's own manifest. Once the adapters became
    separate packages that became a question Roboglot cannot always answer: describing a robot
    whose package is absent raises, and it would raise here, inside the one outcome the whole
    verdict gate exists to produce. So the hint speaks for what is installed and nothing else,
    and a machine with one robot can only speak for that one."""
    import roboglot.adapters.factory as factory
    from roboglot.verdict import solo_hint

    factory._installed.cache_clear()
    real = factory._installed
    try:
        factory._installed = lambda: {"lerobot": "roboglot_lerobot"}  # type: ignore[assignment]
        assert [name for name, _ in factory.installed_manifests()] == ["lerobot"]
        # the run-ending path itself: this used to raise AdapterNotInstalled for the eight
        # bodies that are not here, turning a verdict into a crash
        hint = solo_hint({"payload_kg": 3}, None)
        assert "lerobot" in hint, hint
        assert not any(other in hint for other in ("microduck", "toddlerbot", "xlerobot")), hint
    finally:
        factory._installed = real
        factory._installed.cache_clear()
