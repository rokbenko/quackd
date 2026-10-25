"""A policy server from the command line to the record: the flags, the factory, the goal duck,
the pilot's prompt and the run's policy block.

`--policy-url` and `--policy-token` reach the arm through `make_adapter(policy=...)`, which the
LeRobot adapter turns into the client of the server (`RemoteRunner`) and every other body
refuses, and through `describe(policy=...)`, which lets a task that allows `manipulate` be
judged before anything connects. The server here is the real one, in-process on loopback,
serving a scripted policy, as in `tests/test_policy_contract.py`, and the end-to-end run is
`quackd run --goal` on `lerobot:mujoco` over the stand-in model with the scripted pilot taught
to call `manipulate` once.

No number here comes off an arm: the travel is the stand-in's generic calibration, the policy
is `scripted:hold`, and every figure checked is one the run itself counted.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.adapters import factory
from quackd.adapters.base import (
    POLICY_SPECS,
    AdapterError,
    PolicyChoice,
    policy_choice,
    policy_hint,
)
from quackd.adapters.factory import describe, make_adapter, parse_robot_spec, registry_for
from quackd.agent.prompts import build_system_prompt, executor_section
from quackd.cli import _policy_counter, app, run_counters
from quackd.command import HIDDEN, redacted_url
from quackd.duckfile.parser import duck_from_goal
from quackd.duckfile.validate import validate_duck
from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy import server as S
from quackd_lerobot.policy.client import RemoteRunner, policy_address
from quackd_lerobot.verbs import MANIPULATE_S
from tests.gl import REQUIRE_ENV
from tests.test_policy_contract import TOKEN, Serving, _arm_on, _as_checkpoint, _serving

runner = CliRunner()

DEAD = "http://127.0.0.1:1"
"""A loopback address nothing listens on: port 1 is privileged and never a policy server."""
UNANSWERED = "quackd policy serve"
"""What the client's refusal of a server that is not there says to start, in both of its
sentences: a refused connection where the OS refuses one at once, and on Windows, which tries
a refused loopback connect again for about two seconds, a connect that timed out."""
KEYS = ("instruction", "seconds", "ended", "chunks", "clips", "hz", "skipped", "starved")
"""What a `manipulate` result carries besides its `timing`: counts and names, never an action."""


@pytest.fixture
def held() -> Iterator[Serving]:
    """`quackd policy serve --policy scripted:hold`, on loopback, with a token: a `manipulate`
    of it ends on a stall, which is a segment that ran and is ok."""
    serving = _serving(S.ServeOptions(policy="scripted:hold"))
    try:
        yield serving
    finally:
        serving.http.shutdown()
        serving.http.server_close()
        serving.app.close()


def _flat(text: str) -> str:
    """Output on one line, with no colour: under `GITHUB_ACTIONS` Typer colours its usage
    errors, and a code inside a flag's name would hide it from a plain `in`."""
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


# ── the flags ───────────────────────────────────────────────────────────────────────────


def test_the_address_is_held_redacted_from_construction_and_the_token_never_shown() -> None:
    typed = "https://rok:hunter2@gpu.example.com:9875"
    choice = PolicyChoice(f"  {typed} ", "s3cret-token-of-some-length")
    assert choice.url == redacted_url(typed) == f"https://rok:{HIDDEN}@gpu.example.com:9875"
    assert "hunter2" not in repr(choice) and "s3cret" not in repr(choice)
    assert choice.reach() == (typed, "s3cret-token-of-some-length")
    assert policy_choice(None) is None
    with pytest.raises(ValueError, match="--policy-token goes with --policy-url"):
        policy_choice(None, "a-token")
    with pytest.raises(ValueError, match="--policy-url is empty"):
        policy_choice("   ")


@pytest.mark.parametrize("typed", ["http://rok:hunter2@[::1", "rok:hunter2@127.0.0.1:9875"])
def test_an_address_redaction_cannot_read_is_refused_without_being_quoted(
    tmp_path: Path, typed: str
) -> None:
    """`redacted_url` hands back what it cannot parse as it was typed, so an address with no
    host it can find would be held, printed and refused with its password in it. It is refused
    before anything holds it, by the run, by the choice and by the client `quackd policy check`
    builds, in a sentence that does not quote it."""
    assert redacted_url(typed) == typed, "the premise: redaction leaves this one alone"
    for build in (PolicyChoice, policy_address):
        with pytest.raises(ValueError, match="--policy-url is not a URL") as refused:
            build(typed)
        assert "hunter2" not in str(refused.value)
    result = _run(tmp_path, "--robot", "lerobot:mujoco", "--policy-url", typed)
    assert result.exit_code == 1 and "--policy-url is not a URL" in _flat(result.output)
    assert "hunter2" not in result.output
    checked = runner.invoke(app, ["policy", "check", "--policy-url", typed])
    assert checked.exit_code == 1 and "--policy-url is not a URL" in _flat(checked.output)
    assert "hunter2" not in checked.output


def test_no_variable_names_a_policy_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """One line in a `.env` would otherwise put a policy in charge of every run: the address
    comes from the flag alone, and a run without it describes the arm without a policy."""
    for name in ("QUACKD_POLICY_URL", "QUACKD_POLICY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9875")
    adapter = make_adapter("lerobot:real", address="COM5")
    assert adapter.transport.policy_loop is None  # type: ignore[attr-defined]
    assert not describe(parse_robot_spec("lerobot:real")).provides("manipulate")


# ── the factory ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", POLICY_SPECS)
def test_a_policy_puts_pick_and_manipulate_in_the_arms_static_manifest(spec: str) -> None:
    """So a task that allows them validates before anything connects, each behind a person's
    yes, as the connect that finds the policy would describe the arm."""
    choice = PolicyChoice("http://127.0.0.1:9875", TOKEN)
    without = describe(parse_robot_spec(spec))
    assert not without.provides("manipulate") and not without.provides("pick")
    manifest = describe(parse_robot_spec(spec), policy=choice)
    classes = {v.name: v.safety_class for v in manifest.verbs}
    assert classes["manipulate"] == classes["pick"] == "confirm"
    assert manifest.extras["policy"] is True


def test_the_arm_is_built_with_the_servers_client(held: Serving) -> None:
    adapter = make_adapter("lerobot:mujoco", policy=PolicyChoice(held.url, TOKEN), seed=0)
    loop = adapter.transport.policy_loop  # type: ignore[attr-defined]
    assert isinstance(loop.runner, RemoteRunner) and loop.runner.url == held.url
    # asked what it serves, as `quackd run` asks it before the header, and named as the
    # record names it, with nothing asked when the record is read
    said = adapter.ask_policy()  # type: ignore[attr-defined]
    assert said is not None and said["server"] == held.url and said["policy"] == "scripted:hold"
    assert adapter.policy_served == said  # type: ignore[attr-defined]


def test_the_mock_and_every_body_that_runs_no_policy_refuse_one() -> None:
    choice = PolicyChoice("http://127.0.0.1:9875", TOKEN)
    with pytest.raises(AdapterError, match="lerobot:mock runs its own scripted policy"):
        make_adapter("lerobot:mock", policy=choice)
    with pytest.raises(AdapterError, match="lerobot:mock runs its own scripted policy"):
        describe(parse_robot_spec("lerobot:mock"), policy=choice)
    for spec in ("microduck:sim2d", "rosbridge:mock"):
        refusals = []
        with pytest.raises(AdapterError) as built:
            make_adapter(spec, policy=choice)
        refusals.append(str(built.value))
        with pytest.raises(AdapterError) as described:
            describe(parse_robot_spec(spec), policy=choice)
        refusals.append(str(described.value))
        for said in refusals:
            assert spec in said and "--policy-url" in said, said
            assert all(arm in said for arm in POLICY_SPECS), said


@pytest.mark.parametrize(
    ("url", "needle"),
    [("http://localhost:9875", "write 127.0.0.1"), ("http://10.0.0.5:9875", "ssh -L")],
)
def test_an_address_the_client_refuses_is_refused_as_the_arm_is_built(
    url: str, needle: str
) -> None:
    with pytest.raises(AdapterError, match=needle):
        make_adapter("lerobot:real", address="COM5", policy=PolicyChoice(url, TOKEN))


def test_a_token_found_nowhere_is_refused_as_the_arm_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wire, "DEFAULT_TOKEN_FILE", str(tmp_path / "none" / "policy.token"))
    monkeypatch.setenv(wire.TOKEN_ENV, "")
    with pytest.raises(AdapterError, match=wire.TOKEN_ENV):
        make_adapter("lerobot:real", address="COM5", policy=PolicyChoice("http://127.0.0.1:9875"))
    monkeypatch.setenv(wire.TOKEN_ENV, TOKEN)
    adapter = make_adapter(
        "lerobot:real", address="COM5", policy=PolicyChoice("http://127.0.0.1:9875")
    )
    assert adapter.transport.policy_loop is not None  # type: ignore[attr-defined]


def _plugin(monkeypatch: pytest.MonkeyPatch, make: Any) -> str:
    """A third party's adapter, installed here as `toybot` with one backend, whose `make()` is
    `make` and whose `describe()` takes no policy either."""
    module = ModuleType("quackd_toybot")
    module.BACKENDS = ("sim",)  # type: ignore[attr-defined]
    module.make = make  # type: ignore[attr-defined]

    def describe(backend: str, robot_id: str | None = None) -> Any:
        return factory.describe(parse_robot_spec("lerobot:mock"))

    module.describe = describe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "quackd_toybot", module)
    installed = {**factory._installed(), "toybot": "quackd_toybot"}
    monkeypatch.setattr(factory, "_installed", lambda: installed)
    return "toybot:sim"


def test_a_third_party_adapter_is_called_as_it_always_was_and_refuses_a_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its `make()` never heard of `policy`: built without one it gets exactly the keywords it
    always got, and asked for one it is refused in words that name the arm, never called with
    a keyword it would raise a TypeError on."""
    calls: list[dict[str, Any]] = []

    def make(
        backend: str,
        *,
        robot_id: str | None = None,
        seed: int | None = None,
        address: str | None = None,
        live: bool = False,
        camera_url: Any = None,
        token: str | None = None,
        rest_pose: Any = None,
    ) -> Any:
        calls.append({"backend": backend, "address": address})
        return object()

    spec = _plugin(monkeypatch, make)
    make_adapter(spec, address="toy://1")
    assert calls == [{"backend": "sim", "address": "toy://1"}]
    describe(parse_robot_spec(spec))
    choice = PolicyChoice("http://127.0.0.1:9875", TOKEN)
    with pytest.raises(AdapterError, match="toybot:sim runs no policy"):
        make_adapter(spec, policy=choice)
    with pytest.raises(AdapterError, match="toybot:sim runs no policy"):
        describe(parse_robot_spec(spec), policy=choice)
    assert len(calls) == 1, "the refusal called make() anyway"


def test_an_adapter_that_swallows_every_keyword_is_not_handed_a_policy_to_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken: list[dict[str, Any]] = []

    def make(backend: str, **kwargs: Any) -> Any:
        taken.append(kwargs)
        return object()

    spec = _plugin(monkeypatch, make)
    with pytest.raises(AdapterError, match="runs no policy"):
        make_adapter(spec, policy=PolicyChoice("http://127.0.0.1:9875", TOKEN))
    assert taken == []


# ── the goal duck and the prompt ────────────────────────────────────────────────────────


def _safe(spec: str, **kw: Any) -> list[str]:
    robot = parse_robot_spec(spec)
    registry = registry_for(robot, describe(robot, **kw))
    return sorted(v.name for v in registry.verbs() if v.safety_class == "safe")


def test_a_goal_with_a_policy_allows_manipulate_behind_a_person() -> None:
    """The one exception to a goal's safe verbs, on purpose: each segment is asked about."""
    choice = PolicyChoice("http://127.0.0.1:9875", TOKEN)
    safe = _safe("lerobot:real", policy=choice)
    assert "manipulate" not in safe and "pick" not in safe
    duck = duck_from_goal("stack the blocks", safe, confirm=["manipulate"])
    assert "manipulate" in duck.frontmatter.verbs.allow
    assert duck.frontmatter.verbs.confirm == ["manipulate"]
    manifest = describe(parse_robot_spec("lerobot:real"), policy=choice)
    assert validate_duck(duck, [manifest]) == []
    # without a policy nothing changes: the safe verbs and stop, and nothing gated
    plain = duck_from_goal("stack the blocks", _safe("lerobot:real"))
    assert plain.frontmatter.verbs.confirm == []
    assert "manipulate" not in plain.frontmatter.verbs.allow


def test_the_executor_section_is_there_only_with_manipulate() -> None:
    assert executor_section(["report_state", "move_joints", "stop"]) == ""
    with_camera = executor_section(["observe", "manipulate", "stop"])
    blind = executor_section(["report_state", "manipulate", "stop"])
    for text in (with_camera, blind):
        flat = _flat(text)
        assert text.lstrip().startswith("## Your executor")
        assert "one short subtask per call" in flat and "never declare success" in flat
    assert "`observe`" in with_camera and "fresh frame" in _flat(with_camera)
    assert "`report_state`" in blind and "fresh reading" in _flat(blind)
    # frames with every observation beat a verb that asks for one
    framed = executor_section(["observe", "report_state", "manipulate", "stop"], frames=True)
    assert "the fresh frame your next observation brings" in _flat(framed)
    assert "`observe`" not in framed and "`report_state`" not in framed
    assert "`" not in executor_section(["manipulate", "stop"]).replace("`manipulate`", "")


def test_the_prompt_of_a_goal_run_with_a_policy_tells_the_pilot_its_executor() -> None:
    choice = PolicyChoice("http://127.0.0.1:9875", TOKEN)
    robot = parse_robot_spec("lerobot:real")
    registry = registry_for(robot, describe(robot, policy=choice))
    safe = _safe("lerobot:real", policy=choice)
    duck = duck_from_goal("stack the blocks", safe, confirm=["manipulate"])
    verbs = [registry.view(n) for n in duck.frontmatter.verbs.allow]
    prompt = build_system_prompt(duck, verbs, "real", manifest=describe(robot, policy=choice))
    assert "## Your executor" in prompt
    assert "Verbs marked confirm (manipulate) ask a human" in prompt
    plain = duck_from_goal("stack the blocks", _safe("lerobot:real"))
    verbs = [registry.view(n) for n in plain.frontmatter.verbs.allow]
    assert "## Your executor" not in build_system_prompt(plain, verbs, "real")


def test_a_pilot_that_sees_the_arms_camera_judges_the_frame_after_each_segment() -> None:
    """The arm has no `observe`, camera or not, and its joints cannot show a block in a bowl.
    With a camera and a pilot that can see, every observation brings the frames, so the pilot
    is sent to the one after the segment, and only a pilot with nothing to see is sent to
    `report_state`."""
    from quackd_lerobot import lerobot_manifest

    choice = PolicyChoice("http://127.0.0.1:9875", TOKEN)
    robot = parse_robot_spec("lerobot:mujoco")
    registry = registry_for(robot, describe(robot, policy=choice))
    safe = _safe("lerobot:mujoco", policy=choice)
    duck = duck_from_goal("put the block in the bowl", safe, confirm=["manipulate"])
    verbs = [registry.view(n) for n in duck.frontmatter.verbs.allow]
    assert "observe" not in {v.name for v in verbs} and "manipulate" in {v.name for v in verbs}

    def executor(camera: bool, sees: bool) -> str:
        manifest = lerobot_manifest("mujoco", camera=camera)
        prompt = build_system_prompt(duck, verbs, "mujoco", manifest, adapter="lerobot", sees=sees)
        return _flat(prompt.split("## Your executor")[1].split("\n## ")[0])

    assert "the fresh frame your next observation brings" in executor(camera=True, sees=True)
    for camera, sees in ((True, False), (False, True)):
        said = executor(camera=camera, sees=sees)
        assert "`report_state`" in said and "frame" not in said, (camera, sees, said)


def test_a_run_tells_the_prompt_whether_its_pilot_sees_the_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop says whether the pilot is shown the frames it reads, as `_observe` decides it.
    On the mock arm, which has a camera and its own scripted policy, the scripted pilot told it
    can see is sent to the frame after a segment, and told it cannot, to `report_state`."""
    from quackd.agent.providers import fake
    from quackd.agent.providers.base import ToolCall

    def strategy(_obs: Any, _step: int, _history: Any) -> ToolCall:
        return ToolCall(name="declare_success", arguments={"reason": "nothing to do"})

    monkeypatch.setitem(fake.STRATEGIES, "hand-off", strategy)
    duck = tmp_path / "hand-off.duck"
    duck.write_text(
        "---\nduck: 1\nname: hand-off\ndescription: Hand the arm to its policy.\nverbs:\n"
        "  allow: [manipulate, report_state, stop]\n  confirm: [manipulate]\n"
        "success: [the block is in the bowl]\n---\nPut the block in the bowl.\n",
        encoding="utf-8",
    )
    for flag, said in (
        ("--vision", "the fresh frame your next observation brings"),
        ("--no-vision", "`report_state`"),
    ):
        runs = tmp_path / flag.strip("-")
        result = runner.invoke(
            app,
            [
                *("run", str(duck), "--robot", "lerobot:mock", "--llm", "fake", "--no-memory"),
                *("--yes", flag, "--runs-dir", str(runs)),
            ],
        )
        (run_dir,) = runs.iterdir()
        records = [
            json.loads(line)
            for line in (run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        (start,) = [r for r in records if r["kind"] == "run_start"]
        executor = _flat(start["system_prompt"].split("## Your executor")[1].split("\n## ")[0])
        assert said in executor, (flag, result.output, executor)


# ── the record ──────────────────────────────────────────────────────────────────────────


def test_the_counter_line_says_what_the_policy_did_and_nothing_without_one() -> None:
    end: dict[str, Any] = {"steps": 3, "llm_calls": 3, "usage": {}, "wall_s": 12.0}
    before = run_counters(end)
    assert not any(c.startswith("policy") for c in before)
    block = {
        "segments": 2,
        "seconds": 4.5,
        "wall_s": 4.4,
        "chunks": 7,
        "hz": 9.5,
        "starved_ticks": 0,
        "late_ticks": 3,
        "clips": 1,
        "round_trip_ms": {"count": 7, "mean": 2.5, "max": 4.0},
    }
    after = run_counters({**end, "policy": block})
    assert after[:3] == before[:3]
    # the split is the wall's, as the rest of it is
    assert after[3].startswith("time") and "policy 4.4 s" in after[3], after[3]
    line = _policy_counter(block)
    assert line == (
        "policy 2 segments, 7 chunks, 9.5 Hz, 3 ticks late, 1 goal clipped, round trip 2.5 ms"
    )
    assert line in after
    # and one of anything is said as one
    ones = {"segments": 1, "chunks": 1, "hz": 10.0, "starved_ticks": 1, "late_ticks": 1, "clips": 1}
    assert _policy_counter(ones) == (
        "policy 1 segment, 1 chunk, 10 Hz, 1 tick starved, 1 tick late, 1 goal clipped"
    )
    # a hand-edited record is a shorter line, never a traceback
    assert _policy_counter("nonsense") is None
    assert _policy_counter({"segments": "x", "chunks": 3}) == "policy 0 segments"
    # a policy never handed the arm: its segments alone, and no policy time beside the model's
    idle = run_counters({**end, "policy": {**block, "segments": 0, "seconds": 0.0, "wall_s": 0.0}})
    assert "policy 0 segments" in idle and "policy 0" not in idle[3], idle


def test_the_simulators_policy_seconds_are_its_own_and_never_split_the_wall() -> None:
    """On the simulator the arm's clock is the simulator's, which runs as fast as it steps, so
    a run's policy can have many more of its seconds than the run had of the wall's. The time
    split takes the wall's, and the counter says whose the others are and the rate on them."""
    end: dict[str, Any] = {"steps": 2, "llm_calls": 4, "usage": {}, "wall_s": 5.5}
    block = {
        "segments": 2,
        "seconds": 20.5,
        "clock": "sim",
        "wall_s": 2.5,
        "chunks": 41,
        "hz": 10.0,
        "starved_ticks": 0,
        "late_ticks": 0,
        "clips": 0,
    }
    (spent,) = [c for c in run_counters({**end, "policy": block}) if c.startswith("time")]
    assert spent == "time 5.5 s (policy 2.5 s)", spent
    assert _policy_counter(block) == "policy 2 segments, 41 chunks, 20.5 s sim at 10 Hz"


async def test_a_segment_through_the_server_is_counted_on_the_arms_record(
    held: Serving,
) -> None:
    """Over the suite's fake arm: the record names the server and what it serves, counts the
    segment and its chunks, and times the round trips, and the verb's own result carries
    counts and names alone."""
    _transport, adapter, ex = await _arm_on(held.client())
    try:
        before = adapter.policy_record
        assert before["segments"] == 0 and before["server"] == held.url
        assert "round_trip_ms" not in before
        result = await ex.run_verb("manipulate", {"instruction": "hold still"})
        assert result.ok, result.summary
        assert set(result.data) - {"timing"} <= set(KEYS), sorted(result.data)
        assert not any(isinstance(v, list) for v in result.data.values()), result.data
        record = adapter.policy_record
        assert record["segments"] == 1 and record["policy"] == "scripted:hold"
        assert record["chunks"] == result.data["chunks"] >= 1
        assert record["ticks"] >= 1 and record["hz"] is not None
        assert 0 < record["seconds"] <= MANIPULATE_S
        # a clock that is not lockstep is not named, and the wall's seconds are kept beside it
        assert "clock" not in record and record["wall_s"] >= 0
        assert record["loaded"] == [] and record["jpeg_quality"] is None
        trips = record["round_trip_ms"]
        assert trips["count"] >= record["chunks"] and trips["max"] >= trips["mean"] > 0
    finally:
        await adapter.close()


# ── the commands ────────────────────────────────────────────────────────────────────────


def _run(tmp_path: Path, *argv: str) -> Any:
    return runner.invoke(
        app,
        [
            "run",
            "--goal",
            "hold the arm still",
            "--llm",
            "fake",
            "--no-memory",
            "--runs-dir",
            str(tmp_path / "runs"),
            *argv,
        ],
    )


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (
            ["--robots", "a=lerobot:mock,b=lerobot:mock", "--policy-url", DEAD],
            "--policy-url is one arm's policy server, and a fleet has several bodies",
        ),
        (["--robot", "microduck:sim2d", "--policy-url", DEAD], "microduck:sim2d runs no policy"),
        (["--robot", "lerobot:mock", "--policy-url", DEAD], "runs its own scripted policy"),
        (["--robot", "lerobot:mujoco", "--policy-token", TOKEN], "goes with --policy-url"),
        (
            ["--robot", "lerobot:mujoco", "--policy-url", "http://localhost:9875"],
            "write 127.0.0.1",
        ),
        (
            ["--robot", "lerobot:mujoco", "--policy-url", DEAD, "--policy-token", TOKEN],
            UNANSWERED,
        ),
    ],
)
def test_a_run_refuses_a_policy_it_cannot_use_before_anything_connects(
    tmp_path: Path, argv: list[str], needle: str
) -> None:
    result = _run(tmp_path, *argv)
    assert result.exit_code == 1 and needle in _flat(result.output), result.output
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_a_task_that_needs_a_policy_on_the_arm_says_how_to_give_it_one(tmp_path: Path) -> None:
    """`--policy-url` is the only way the arm has `pick` or `manipulate`, so a task that allows
    either, run, validated or served without it, is refused with a line saying how to give it
    one, rather than only sent to `quackd list-verbs`, which never lists them. Nothing else
    gains the line."""
    from quackd.mcp_server import fleet_from_flags

    duck = tmp_path / "hand-off.duck"
    duck.write_text(
        "---\nduck: 1\nname: hand-off\ndescription: Hand the arm to its policy.\nverbs:\n"
        "  allow: [manipulate, report_state, stop]\n  confirm: [manipulate]\n"
        "success: [the block is in the bowl]\n---\nPut the block in the bowl.\n",
        encoding="utf-8",
    )
    said = "manipulate on lerobot:mujoco comes from a policy server: start one with quackd"
    ran = runner.invoke(
        app,
        [
            *("run", str(duck), "--robot", "lerobot:mujoco", "--llm", "fake", "--no-memory"),
            *("--runs-dir", str(tmp_path / "runs")),
        ],
    )
    out = _flat(ran.output)
    assert ran.exit_code == 1 and "manipulate is not provided by" in out, ran.output
    assert said in out and "give quackd run its address with --policy-url" in out, out
    checked = runner.invoke(app, ["validate", str(duck), "--robot", "lerobot:mujoco"])
    assert checked.exit_code == 1 and said in _flat(checked.output), checked.output
    with pytest.raises(SystemExit, match="give quackd serve-mcp its address with --policy-url"):
        fleet_from_flags(robot="lerobot:mujoco", duckfile=str(duck))
    # a verb no policy gives, and a body that runs no policy, are refused as they always were
    assert policy_hint(["observe"], ["lerobot:mujoco"], "quackd run") is None
    assert policy_hint(["manipulate"], ["microduck:sim2d"], "quackd run") is None
    both = policy_hint(["pick", "manipulate"], ["lerobot:real"], "quackd run")
    assert both is not None and both.startswith("pick and manipulate on lerobot:real come from")


def test_a_server_that_is_not_there_says_where_to_look(tmp_path: Path) -> None:
    result = _run(
        tmp_path, "--robot", "lerobot:mujoco", "--policy-url", DEAD, "--policy-token", TOKEN
    )
    out = _flat(result.output)
    assert result.exit_code == 1 and UNANSWERED in out, result.output
    assert f"quackd policy check --policy-url {DEAD} asks it what it serves" in out


def test_serve_mcp_takes_a_policy_for_one_arm_and_refuses_it_for_a_fleet(
    held: Serving,
) -> None:
    from quackd.mcp_server import fleet_from_flags

    with pytest.raises(SystemExit, match="a fleet has several bodies"):
        fleet_from_flags(
            robots="a=lerobot:mock,b=lerobot:mock", policy_url=held.url, policy_token=TOKEN
        )
    with pytest.raises(AdapterError, match="microduck:sim2d runs no policy"):
        fleet_from_flags(robot="microduck:sim2d", policy_url=held.url, policy_token=TOKEN)
    with pytest.raises(SystemExit, match=UNANSWERED):
        fleet_from_flags(robot="lerobot:mujoco", policy_url=DEAD, policy_token=TOKEN)
    plan = fleet_from_flags(robot="lerobot:mujoco", policy_url=held.url, policy_token=TOKEN)
    ((name, manifest),) = plan.manifests.items()
    assert manifest.provides("manipulate") and manifest.provides("pick")
    served = plan.adapters[name].policy_served
    assert served["server"] == held.url and served["policy"] == "scripted:hold"
    refused = runner.invoke(app, ["serve-mcp", "--robot", "lerobot:mujoco", "--controller", "vla"])
    assert refused.exit_code != 0 and "--controller" in _flat(refused.output)


def test_serve_mcp_says_a_policy_the_arm_refuses_in_its_sentence_and_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connect's refusal of a policy that does not fit the arm is raised as the server
    starts, inside the task group the MCP SDK serves in, and used to print as a traceback with
    the sentence buried in it while the client saw only a closed connection. It is said as
    `quackd run` says it, one sentence and exit 1. The policy here learned from an arm whose
    shoulder_lift went past this one's ceiling, by twice the slack a reading is forgiven."""
    pytest.importorskip("mujoco")
    from quackd_lerobot.real import OUT_OF_RANGE_DEG, joint_ranges
    from quackd_lerobot.sim import standin
    from quackd_lerobot.sim.model import generic_calibration, load
    from quackd_lerobot.verbs import JOINTS

    monkeypatch.setattr("quackd_lerobot.sim.transport.default_model", standin.mjcf)
    travel = joint_ranges(generic_calibration(load(standin.mjcf(), seed=0)))
    q01, q99 = [], []
    for motor in JOINTS:
        low, high = travel[motor]
        middle, quarter = (low + high) / 2, (high - low) / 4
        q01.append(middle - quarter)
        q99.append(high + 2 * OUT_OF_RANGE_DEG if motor == "shoulder_lift" else middle + quarter)
    serving = _as_checkpoint(
        features={"state": len(JOINTS), "action": len(JOINTS), "images": []},
        state_quantiles={"q01": q01, "q99": q99},
    )
    try:
        result = runner.invoke(
            app,
            [
                "serve-mcp",
                "--robot",
                "lerobot:mujoco",
                "--policy-url",
                serving.url,
                "--policy-token",
                TOKEN,
                "--yes",
                "--no-memory",
            ],
        )
    finally:
        serving.http.shutdown()
        serving.http.server_close()
    if "no OpenGL context" in result.output and os.environ.get(REQUIRE_ENV) != "1":
        pytest.skip("no OpenGL context for offscreen rendering")
    out = _flat(result.output)
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output and "ExceptionGroup" not in result.output, out
    assert "calibrated travel: shoulder_lift" in out and "--accept-other-frame" in out, out
    assert "The arm was not touched" in out, out


def test_accept_other_frame_goes_with_a_policy_server_and_is_on_the_record(
    tmp_path: Path, held: Serving
) -> None:
    """`--accept-other-frame` lets a policy learned on an arm calibrated another way connect,
    and means nothing without a server to accept it of: refused alone on all three commands
    that take `--policy-url`, and with one it reaches the arm's client and the record."""
    with pytest.raises(ValueError, match="--accept-other-frame goes with --policy-url"):
        policy_choice(None, accept_other_frame=True)
    assert policy_choice(held.url, TOKEN) is not None
    refusals = [
        _run(tmp_path, "--robot", "lerobot:mujoco", "--accept-other-frame"),
        runner.invoke(
            app,
            [
                "preflight",
                "hello-world",
                "--robot",
                "lerobot:mujoco",
                "--llm",
                "fake",
                "--accept-other-frame",
                "--runs-dir",
                str(tmp_path / "runs"),
            ],
        ),
        runner.invoke(app, ["serve-mcp", "--robot", "lerobot:mujoco", "--accept-other-frame"]),
    ]
    for refused in refusals:
        assert refused.exit_code != 0, refused.output
        assert "--accept-other-frame goes with --policy-url" in _flat(refused.output)
    choice = PolicyChoice(held.url, TOKEN, accept_other_frame=True)
    adapter = make_adapter("lerobot:mujoco", policy=choice, seed=0)
    client = adapter.transport.policy_loop.runner  # type: ignore[attr-defined]
    assert isinstance(client, RemoteRunner) and client.accept_other_frame
    said = adapter.ask_policy()  # type: ignore[attr-defined]
    assert said is not None and said["accept_other_frame"] is True
    from quackd.mcp_server import fleet_from_flags

    plan = fleet_from_flags(
        robot="lerobot:mujoco", policy_url=held.url, policy_token=TOKEN, accept_other_frame=True
    )
    (served,) = [a.policy_served for a in plan.adapters.values()]
    assert served is not None and served["accept_other_frame"] is True
    plain = make_adapter("lerobot:mujoco", policy=PolicyChoice(held.url, TOKEN), seed=0)
    assert plain.ask_policy()["accept_other_frame"] is False  # type: ignore[attr-defined]


def test_no_async_chunks_goes_with_a_policy_server_and_is_on_the_record(
    tmp_path: Path, held: Serving
) -> None:
    """`--no-async-chunks` has the arm wait for every chunk of a policy server's, and means
    nothing without a server: refused alone on all three commands that take `--policy-url`, and
    on `policy check` without `--bench`, whose pace it sets. With a server it reaches the arm's
    policy loop and the record, and asking for chunks while the arm plays is the default."""
    with pytest.raises(ValueError, match="--no-async-chunks goes with --policy-url"):
        policy_choice(None, async_chunks=False)
    refusals = [
        _run(tmp_path, "--robot", "lerobot:mujoco", "--no-async-chunks"),
        runner.invoke(
            app,
            [
                "preflight",
                "hello-world",
                "--robot",
                "lerobot:mujoco",
                "--llm",
                "fake",
                "--no-async-chunks",
                "--runs-dir",
                str(tmp_path / "runs"),
            ],
        ),
        runner.invoke(app, ["serve-mcp", "--robot", "lerobot:mujoco", "--no-async-chunks"]),
    ]
    for refused in refusals:
        assert refused.exit_code != 0, refused.output
        assert "--no-async-chunks goes with --policy-url" in _flat(refused.output)
    check = runner.invoke(app, ["policy", "check", "--policy-url", held.url, "--no-async-chunks"])
    assert check.exit_code != 0 and "--no-async-chunks goes with --bench" in _flat(check.output)
    choice = PolicyChoice(held.url, TOKEN, async_chunks=False)
    adapter = make_adapter("lerobot:mujoco", policy=choice, seed=0)
    assert adapter.transport.policy_loop.async_chunks is False  # type: ignore[attr-defined]
    said = adapter.ask_policy()  # type: ignore[attr-defined]
    assert said is not None and said["async_chunks"] is False
    from quackd.mcp_server import fleet_from_flags

    plan = fleet_from_flags(
        robot="lerobot:mujoco", policy_url=held.url, policy_token=TOKEN, async_chunks=False
    )
    (served,) = [a.policy_served for a in plan.adapters.values()]
    assert served is not None and served["async_chunks"] is False
    plain = make_adapter("lerobot:mujoco", policy=PolicyChoice(held.url, TOKEN), seed=0)
    assert plain.ask_policy()["async_chunks"] is True  # type: ignore[attr-defined]


def test_preflight_refuses_a_policy_server_that_is_not_there(tmp_path: Path) -> None:
    duck = tmp_path / "hold.duck"
    duck.write_text(
        "---\nduck: 2\nname: hold\ndescription: hold\nverbs:\n  allow: [report_state, stop]\n"
        "success: [the arm is still]\n---\nHold still.\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "preflight",
            str(duck),
            "--robot",
            "lerobot:mujoco",
            "--llm",
            "fake",
            "--policy-url",
            DEAD,
            "--policy-token",
            TOKEN,
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 1 and UNANSWERED in _flat(result.output), result.output


# ── a run, end to end ───────────────────────────────────────────────────────────────────


def test_a_goal_run_hands_the_arm_to_the_server_and_records_what_the_policy_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: Serving
) -> None:
    """`quackd run --goal` on the simulator's stand-in, with `--policy-url`: the header names
    the server and its checkpoint, the goal allows `manipulate` behind a person's yes (`--yes`
    here), the prompt tells the pilot its executor, the pilot calls `manipulate` once, and the
    summary's policy block counts the segment the server drove and says the run took a policy
    learned on another arm's frame (`--accept-other-frame`), which this one never needed."""
    pytest.importorskip("mujoco")
    from quackd.agent.providers import fake
    from quackd.agent.providers.base import ToolCall
    from quackd_lerobot.sim import standin

    monkeypatch.setattr("quackd_lerobot.sim.transport.default_model", standin.mjcf)

    def strategy(_obs: Any, step: int, _history: Any) -> ToolCall:
        if step == 0:
            return ToolCall(name="manipulate", arguments={"instruction": "hold still"})
        return ToolCall(name="declare_success", arguments={"reason": "held"})

    monkeypatch.setitem(fake.STRATEGIES, "goal", strategy)
    result = _run(
        tmp_path,
        "--robot",
        "lerobot:mujoco",
        "--policy-url",
        held.url,
        "--policy-token",
        TOKEN,
        "--accept-other-frame",
        "--yes",
    )
    if "no OpenGL context" in result.output and os.environ.get(REQUIRE_ENV) != "1":
        pytest.skip("no OpenGL context for offscreen rendering")
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert f"policy {held.url} scripted:hold" in out, "the header names the server and checkpoint"
    (run_dir,) = (tmp_path / "runs").iterdir()
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    block = summary["policy"]
    assert block["server"] == held.url and block["policy"] == "scripted:hold"
    assert block["accept_other_frame"] is True
    assert block["segments"] == 1 and block["chunks"] >= 1 and block["ticks"] >= 1
    assert block["loaded"] == [] and block["round_trip_ms"]["count"] >= 1
    # the simulator's seconds are its own and say so, and its wall seconds fit inside the run's
    assert block["clock"] == "sim" and 0 <= block["wall_s"] <= summary["wall_s"], block
    assert "policy 1 segment" in out, "the counter line under the verdict"
    assert "s sim at" in out, "the counter names the clock its rate was taken on"
    records = [
        json.loads(line)
        for line in (run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    (start,) = [r for r in records if r["kind"] == "run_start"]
    assert start["policy"]["server"] == held.url and start["policy"]["policy"] == "scripted:hold"
    assert start["policy"]["accept_other_frame"] is True
    assert start["contract"]["verbs"]["confirm"] == ["manipulate"]
    assert "## Your executor" in start["system_prompt"]
    # no camera here, so the look after a segment is a reading of the arm
    executor = start["system_prompt"].split("## Your executor")[1].split("\n## ")[0]
    assert "`report_state`" in executor and "frame" not in executor, executor
    assert TOKEN not in json.dumps(records) and TOKEN not in json.dumps(summary)
    (moved,) = [r for r in records if r["kind"] == "verb" and r.get("name") == "manipulate"]
    assert moved["ok"], moved
    assert set(moved["data"]) - {"timing"} <= set(KEYS), sorted(moved["data"])
    # and a replay opens with the same row and ends with the same counter
    replay = runner.invoke(app, ["log", str(run_dir)], env={"COLUMNS": "200"})
    assert replay.exit_code == 0, replay.output
    shown = _flat(replay.output)
    assert f"policy {held.url} scripted:hold" in shown and "policy 1 segment" in shown, shown


# ── an endpoint, by its hf:NAME ─────────────────────────────────────────────────────────

HF_TOKEN = "hf_" + "PlumbingStandIn" * 2
ENDPOINT_URL = "https://f3b5c6ee5c9dcc0e0a01.endpoints.huggingface.cloud"


def _recorded(tmp_path: Path, **kw: Any) -> Any:
    """A record of an endpoint `arm`, as `quackd policy endpoint up` keeps one, at its address."""
    from dataclasses import replace

    from quackd_lerobot.policy import endpoint

    record = replace(
        endpoint.new_record(
            "arm",
            namespace="rok",
            policy="owner/act@abc123",
            instance="nvidia-l4",
            vendor="aws",
            region="us-east-1",
            idle_minutes=15,
        ),
        url=ENDPOINT_URL,
        **kw,
    )
    endpoint.EndpointStore(tmp_path).save(record)
    return record


def test_hf_name_is_taken_on_the_flag_alone_and_a_name_hugging_face_would_refuse_is_not() -> None:
    choice = PolicyChoice(" hf:arm-01 ")
    assert (choice.url, choice.endpoint, choice.reach()) == (
        "hf:arm-01",
        "hf:arm-01",
        ("hf:arm-01", None),
    )
    assert policy_choice("hf:arm-01", registry_dir="somewhere").registry_dir == "somewhere"  # type: ignore[union-attr]
    for bad in ("hf:Arm", "hf:", "hf:" + "a" * 33, "HF:arm_1"):
        with pytest.raises(ValueError, match="is not an endpoint's hf:NAME"):
            PolicyChoice(bad)
    assert PolicyChoice("http://127.0.0.1:9875").endpoint is None
    assert describe(parse_robot_spec("lerobot:real"), policy=choice).provides("manipulate")


@pytest.fixture
def hf_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The stand-in for Hugging Face's API, with quackd pointed at it and the plumbing's own
    account token, which a run's `hf:NAME` asks before it builds the arm's client."""
    from tests.fake_hugging_face import FakeAPI, point

    with FakeAPI(token=HF_TOKEN) as api:
        point(monkeypatch, api)
        yield api


def test_hf_name_builds_the_client_from_the_record_and_never_another_servers_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hf_api: Any
) -> None:
    """The address and the token are the record's, the bearer the account's, and
    `QUACKD_POLICY_TOKEN`, which is another server's, is not read. `--policy-token` beats the
    record, as it beats everything on any server. Each build asks Hugging Face's API first,
    here its stand-in, which has the endpoint at the recorded address."""
    record = _recorded(tmp_path)
    hf_api.seed("rok", "arm", url=ENDPOINT_URL)
    monkeypatch.setenv("HF_TOKEN", HF_TOKEN)
    monkeypatch.setenv(wire.TOKEN_ENV, "e" * 64)
    choice = PolicyChoice("hf:arm", registry_dir=str(tmp_path))
    adapter = make_adapter("lerobot:real", address="COM5", policy=choice)
    client = adapter.transport.policy_loop.runner  # type: ignore[attr-defined]
    assert isinstance(client, RemoteRunner)
    assert (client.url, client.endpoint, client._token, client._bearer) == (
        ENDPOINT_URL,
        "hf:arm",
        record.token,
        HF_TOKEN,
    )
    assert client.record()["endpoint"] == "hf:arm" and client.record()["server"] == ENDPOINT_URL
    typed = PolicyChoice("hf:arm", "f" * 64, registry_dir=str(tmp_path))
    made = make_adapter("lerobot:mujoco", policy=typed, seed=0)
    assert made.transport.policy_loop.runner._token == "f" * 64  # type: ignore[attr-defined]
    assert len(hf_api.sent("/v2/endpoint/rok/arm")) == 2


@pytest.mark.parametrize("case", ["no record", "no account token"])
def test_hf_name_with_no_record_or_no_account_token_refuses_the_arm_before_it_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    if case == "no record":
        monkeypatch.setenv("HF_TOKEN", HF_TOKEN)
        needle = "no endpoint called arm is recorded on this machine"
    else:
        _recorded(tmp_path)
        needle = "no Hugging Face token to reach hf:arm"
    choice = PolicyChoice("hf:arm", registry_dir=str(tmp_path))
    with pytest.raises(AdapterError, match=needle):
        make_adapter("lerobot:real", address="COM5", policy=choice)
    result = _run(
        tmp_path,
        "--robot",
        "lerobot:mujoco",
        "--policy-url",
        "hf:arm",
        "--registry-dir",
        str(tmp_path),
    )
    assert result.exit_code == 1 and needle in _flat(result.output), result.output
