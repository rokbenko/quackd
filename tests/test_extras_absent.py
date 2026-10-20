"""The default install never imports an SDK: every adapter lists, describes and constructs
with torch, lerobot, roslibpy, zmq, zeroconf and paho all absent."""

from __future__ import annotations

import subprocess
import sys

import pytest

from quackd.adapters.catalogue import BY_NAME

ADAPTERS = tuple(BY_NAME)
"""The eight quackd publishes, in the order every table that prints them uses."""

HEAVY = (
    "torch",
    "lerobot",
    "roslibpy",
    "zmq",
    "zeroconf",
    "paho",
    "mujoco",
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
)

SCRIPT = f"""
import sys
for name in {HEAVY!r}:
    sys.modules[name] = None  # any import of it now raises ImportError
import quackd
import quackd.cli
import quackd.doctor
import quackd.lan
import quackd.lan.announce
import quackd.lan.discover
import quackd.flock.mqtt_bus
import quackd.registry
import quackd.agent.decision
import quackd.agent.decision.stepper
import quackd.agent.decision.factory
import quackd.agent.loop
from quackd.agent.decision.catalogue import PRESETS
from quackd.agent.decision.factory import decision_llm_is_available, resolve_decision_mode
assert resolve_decision_mode(None, named=False) == "off"
ok, why = decision_llm_is_available(PRESETS["jev"])
assert not ok and "quackd[decision]" in why, why
ok, why = decision_llm_is_available(PRESETS["laya"])
assert not ok and "quackd[laya]" in why, why
ok, why = decision_llm_is_available(PRESETS["openai"])
assert not ok and "quackd[openai]" in why, why
ok, why = decision_llm_is_available(PRESETS["openai"], url="http://127.0.0.1:8765")
assert not ok and "quackd[openai]" in why, why
from quackd.adapters.factory import BACKENDS, RobotSpec, describe, list_adapters, make_adapter
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
make_adapter("microduck:mujoco")
make_adapter("lerobot:real", address="COM5")
make_adapter("rosbridge:ws", address="ws://robot.local:9090")
make_adapter("huebot:jsonrpc", address="tcp://127.0.0.1:9870")
# the arm simulator is built, cameras, faults and all, with or without a calibration file to
# read at connect, and nothing here connects it
make_adapter("lerobot:mujoco")
make_adapter(
    "lerobot:mujoco",
    address="arm-01.json",
    camera_url=["opencv://0?name=front", "opencv://1?name=wrist"],
    faults="handshake=0.2",
    seed=3,
)
# and with a scene of its own, as `quackd preflight` hands it one from a task's sidecar, which
# reads no physics either: the scene is laid out at connect
make_adapter(
    "lerobot:mujoco",
    scene=[{{"name": "block", "kind": "box", "size": [0.01, 0.01, 0.01], "place": "jaws"}}],
)
import quackd.preflight
from quackd.adapters.factory import is_simulator
assert is_simulator("lerobot:mujoco") and not is_simulator("lerobot:real")
# the arm simulator's upstream and its fetcher find files and never load them, and its model,
# stand-in, world, follower, clock, cameras and transport import the physics only when a model
# is loaded or drawn, so none of them may pull it in just by being imported. Its follower
# reimplements the one LeRobot function it needs rather than import LeRobot, and a fault plan
# is parsed before anything is built.
import quackd_lerobot.sim.assets
import quackd_lerobot.sim.camera
import quackd_lerobot.sim.clock
import quackd_lerobot.sim.faults
import quackd_lerobot.sim.follower
import quackd_lerobot.sim.model
import quackd_lerobot.sim.standin
import quackd_lerobot.sim.transport
import quackd_lerobot.sim.upstream_api
import quackd_lerobot.sim.world
quackd_lerobot.sim.standin.mjcf()
# a policy server, its client and their protocol need no torch either: a checkpoint is loaded
# in the server's own process when it starts, never when any of them is imported, and a
# scripted policy is served with nothing loaded at all
import quackd_lerobot.policy.client
import quackd_lerobot.policy.protocol
import quackd_lerobot.policy.server
import quackd_lerobot.policy.upstream_api
quackd_lerobot.policy.server.served_policy(
    quackd_lerobot.policy.server.ServeOptions(policy="scripted:sweep")
)
# the pipeline a checkpoint loads through imports torch, LeRobot and huggingface_hub only when
# it loads one, so its checks of a checkpoint's JSON run with none of them, and a checkpoint
# named without them is refused in words that name the extra, before anything is fetched
import quackd_lerobot.policy.fit
import quackd_lerobot.policy.pipeline
quackd_lerobot.policy.pipeline.check_processor(
    {{"steps": [{{"registry_name": "device_processor", "config": {{"device": "cpu"}}}}]}},
    "policy_preprocessor.json",
    "owner/policy@main",
)
try:
    quackd_lerobot.policy.server.served_policy(
        quackd_lerobot.policy.server.ServeOptions(policy="owner/policy@main", fps=10.0)
    )
except quackd_lerobot.policy.server.ServeRefused as e:
    assert "quackd[lerobot-vla]" in str(e), e
else:
    raise AssertionError("a checkpoint was served with no torch installed")
# and an arm handed a policy server builds the server's client and asks it nothing until it
# is asked, so naming one on the command line needs no torch and no LeRobot either
from quackd.adapters.base import PolicyChoice
_policy = PolicyChoice("http://127.0.0.1:9875", "0123456789abcdef0123456789abcdef")
make_adapter("lerobot:real", address="COM5", policy=_policy)
make_adapter("lerobot:mujoco", policy=_policy)
assert describe(RobotSpec("lerobot", "mujoco"), policy=_policy).provides("manipulate")
# and connecting it says which extra installs the physics, before it fetches a model to load
import asyncio
from quackd.adapters.base import AdapterNotInstalled
def _fetched():
    raise AssertionError("the arm's model was fetched with no physics to load it in")
quackd_lerobot.sim.transport.default_model = _fetched
try:
    asyncio.run(make_adapter("lerobot:mujoco").connect())
except AdapterNotInstalled as e:
    assert "quackd[lerobot-sim]" in str(e), e
else:
    raise AssertionError("lerobot:mujoco connected with no physics installed")
quackd_lerobot.sim.faults.FaultPlan.parse(quackd_lerobot.sim.faults.EXAMPLE, seed=0)
capped = quackd_lerobot.sim.follower.ensure_safe_goal_position({{"a": (9.0, 0.0)}}, 1.0)
assert capped == {{"a": 1.0}}, capped
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
    import quackd.flock.mqtt_bus  # noqa: F401

    for name in ("torch", "lerobot", "roslibpy", "typesafe_sdk", "laya"):
        assert name not in sys.modules, f"{name} was imported on the default path"


def test_naming_the_decision_llms_costs_nothing_to_import() -> None:
    """`quackd.cli` reads the preset table for `--help` and for `doctor`, and the table is
    data: names, one-line summaries, urls and rates, with not an import of a client in it.

    Both clients weigh what `torch` weighs -- `laya` pulls it directly -- so importing either
    one to print a help line would put several seconds on the front of every single command,
    including the ones that never go near a decision LLM."""
    import quackd.cli  # noqa: F401

    for name in ("typesafe_sdk", "laya"):
        assert name not in sys.modules, f"{name} was imported just by loading the CLI"


def test_a_machine_with_no_adapter_installed_still_works_and_says_what_to_install() -> None:
    """The claim the split exists to make. quackd on its own is the loop, the executor and the
    contract, so it has to import, list every robot it publishes and refuse to guess at a body,
    all without a single adapter present.

    Driven through the discovery seam rather than by uninstalling anything, because the suite
    runs in an environment that has all eight and a test may not take them away from it."""
    import quackd.adapters.factory as factory
    from quackd.adapters.base import AdapterNotInstalled
    from quackd.adapters.factory import NoRobotNamed

    factory._installed.cache_clear()
    real = factory._installed
    factory._installed = dict  # type: ignore[assignment]  # no entry points, nothing importable
    try:
        rows = factory.list_adapters()
        assert [r["name"] for r in rows] == list(ADAPTERS), rows
        assert not any(r["adapter_installed"] for r in rows), rows
        assert all(r["extra"].startswith("quackd[") for r in rows), rows

        # a spec still parses, so a robot can be registered, printed and reasoned about on a
        # machine that cannot build it. Asking for the body itself is what refuses.
        assert factory.parse_robot_spec("lerobot:real").key == "lerobot:real"
        with pytest.raises(AdapterNotInstalled, match=r"quackd\[lerobot\]"):
            factory.describe(factory.parse_robot_spec("lerobot:real"))
        with pytest.raises(AdapterNotInstalled, match=r"quackd\[lerobot\]"):
            factory.make_adapter("lerobot:mock")

        with pytest.raises(NoRobotNamed, match="no robot adapter is installed"):
            factory.resolve_robot(None)

        # `doctor` is where `README.md` sends a new reader to find out what this machine has,
        # and it still exits 0, because an uninstalled adapter is a choice rather than a
        # fault. What it may not do is say the simulator runs here. It does not: `quackd run`
        # on this machine refuses for want of a robot, and a green verdict claiming otherwise
        # is this command saying the opposite of the one thing it exists to say.
        import io as _io

        from rich.console import Console

        from quackd import doctor

        report = doctor.collect()
        assert report.ok, "a machine with no robot is still a working quackd"
        buf = _io.StringIO()
        Console(file=buf, width=200).print(doctor.verdict(report))
        said = buf.getvalue()
        assert "no robot adapter is installed" in said, said
        assert "the simulator and the scripted pilot run here" not in said, said
        assert "0/8 adapters installed" in " ".join(said.split()), said

        # a pilot flock does not reach past the refusal for a hardcoded duck either
        from quackd.flock.runner import member_specs

        with pytest.raises(NoRobotNamed, match="no robot adapter is installed"):
            member_specs(["a", "b"], None, None, fallback=None)
    finally:
        factory._installed = real
        factory._installed.cache_clear()


def test_the_only_adapter_installed_is_the_one_a_command_means() -> None:
    """A machine with one robot has no ambiguity to resolve, so making its owner type the name
    would be ceremony. With several installed quackd will not invent an answer, except that the
    Microduck stays the default it has always been, because the `duck: 0` starters mean it."""
    import quackd.adapters.factory as factory

    factory._installed.cache_clear()
    real = factory._installed
    try:
        factory._installed = lambda: {"lerobot": "quackd_lerobot"}  # type: ignore[assignment]
        assert factory.resolve_robot(None).key == "lerobot:mock"

        factory._installed = lambda: {  # type: ignore[assignment]
            "lerobot": "quackd_lerobot",
            "rosbridge": "quackd_rosbridge",
        }
        with pytest.raises(factory.NoRobotNamed, match="no robot named"):
            factory.resolve_robot(None)

        factory._installed = lambda: {  # type: ignore[assignment]
            "microduck": "quackd_microduck",
            "lerobot": "quackd_lerobot",
        }
        assert factory.resolve_robot(None).key == "microduck:sim2d"
    finally:
        factory._installed = real
        factory._installed.cache_clear()


def test_a_verdict_speaks_only_for_the_robots_this_machine_has() -> None:
    """An `infeasible` verdict ends a run by naming which other body could have done the task,
    and it builds each answer from that adapter's own manifest. Once the adapters became
    separate packages that became a question quackd cannot always answer: describing a robot
    whose package is absent raises, and it would raise here, inside the one outcome the whole
    verdict gate exists to produce. So the hint speaks for what is installed and nothing else,
    and a machine with one robot can only speak for that one."""
    import quackd.adapters.factory as factory
    from quackd.verdict import solo_hint

    factory._installed.cache_clear()
    real = factory._installed
    try:
        factory._installed = lambda: {"lerobot": "quackd_lerobot"}  # type: ignore[assignment]
        assert [name for name, _ in factory.installed_manifests()] == ["lerobot"]
        # the run-ending path itself: this used to raise AdapterNotInstalled for the seven
        # bodies that are not here, turning a verdict into a crash
        hint = solo_hint({"payload_kg": 3}, None)
        assert "lerobot" in hint, hint
        assert not any(other in hint for other in ("microduck", "toddlerbot", "xlerobot")), hint
    finally:
        factory._installed = real
        factory._installed.cache_clear()
