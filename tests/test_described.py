"""The bodies quackd only describes (ADR-0077), and the tiers every backend carries (ADR-0076).

A described body is one YAML under `quackd/adapters/bodies/` and no code, reached as
`described:<name>` through the catalogue's last row. What these tests hold is the honesty of
that: every figure names a source and the day it was read, the body's only verbs are
`report_state` and `stop`, every path that could claim to reach a robot is refused, the prompt
says quackd drives no part of it, and the last row never counts as an adapter anywhere an
adapter is counted. The tier tests hold the owner rule: bound and verified code names who
answers for it and when.
"""

from __future__ import annotations

import datetime as dt
import io
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from quackd.adapters.base import AdapterError
from quackd.adapters.catalogue import (
    ADAPTERS,
    BY_NAME,
    DESCRIBED,
    DESCRIBED_BODIES,
    FAMILIES,
    GRANDFATHERED,
    MARK,
    OFFICIAL,
    ROUTES,
    RULE_ADOPTED,
    TIERS,
    AdapterInfo,
    Tier,
    status_line,
)
from quackd.adapters.described import (
    ALLOWED,
    BodyFileError,
    DescribedAdapter,
    body_files,
    describe,
    make,
    manifest_from,
    parse_body,
)
from quackd.adapters.factory import (
    ADAPTER_NAMES,
    BACKENDS,
    RobotSpec,
    bodies_that_could,
    default_spec,
    described_manifests,
    described_that_could,
    drives,
    installed_manifests,
    list_adapters,
    make_adapter,
)
from quackd.adapters.factory import describe as describe_spec
from quackd.agent.prompts import DESCRIBED_LINE, body_lines, body_summary
from quackd.registry import RegistryError, check_name
from quackd.verdict import missing_needs, solo_hint

FRANKA = f"{DESCRIBED}:franka-research-3"

# ── the files and the catalogue agree ──────────────────────────────────────────────────────────


def test_the_catalogue_lists_exactly_the_body_files() -> None:
    """A file with no row is a body nobody can name, and a row with no file is a `--robot` that
    parses and cannot be described: the two lists are one list, alphabetical."""
    assert body_files() == DESCRIBED_BODIES
    assert tuple(sorted(DESCRIBED_BODIES)) == DESCRIBED_BODIES
    assert BY_NAME[DESCRIBED].backends == DESCRIBED_BODIES
    assert len(DESCRIBED_BODIES) >= 12


def test_the_tenth_row_is_no_adapter() -> None:
    row = BY_NAME[DESCRIBED]
    assert row.kind == "described" and row.family == ("described",) and row.route == ()
    assert row.extra is None and row.sdk is None and not row.connect
    assert all(tier.name == "described" for tier in row.tiers.values())
    assert DESCRIBED not in ADAPTER_NAMES and DESCRIBED not in BACKENDS
    assert [info.name for info in ADAPTERS] == list(ADAPTER_NAMES)
    assert len(OFFICIAL) == len(ADAPTERS) + 1
    assert not drives(DESCRIBED) and all(drives(name) for name in ADAPTER_NAMES)


# ── every body ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", DESCRIBED_BODIES)
def test_a_described_body_is_honest_about_itself(name: str) -> None:
    m = describe(name)
    assert m.id == name and m.backend == name
    assert m.verb_names() == ["report_state", "stop"]
    assert m.intents == [] and m.limits == {} and m.preconditions == {}
    # no sensors either: a manifest's sensors are what quackd reads, a verb's requirements are
    # judged against them, and quackd reads nothing from a body it only describes
    assert m.sensors == []
    assert m.extras == {"described": "maker"}
    assert m.blurb.endswith("; quackd drives no part of it"), m.blurb
    sheet = m.datasheet
    assert sheet is not None
    assert sheet.known(), f"{name} publishes no figure at all"
    for label, figure, _unit in sheet.known():
        assert "read 20" in figure.source, f"{name}'s {label} does not say when it was read"
    # an arm stands still and a legged body walks: the two fields agree
    assert (m.mobility == "none") == (m.embodiment == "arm"), name
    # the same body reads the same twice, which is what keeps a flock's roster stable, and the
    # same under a registered name: the digest is the body's, as every adapter's is
    assert describe(name).digest() == m.digest()
    named = describe(name, "b2")
    assert named.id == "b2" and named.digest() == m.digest()


@pytest.mark.parametrize("name", DESCRIBED_BODIES)
def test_the_prompt_says_quackd_drives_no_part_of_it(name: str) -> None:
    m = describe(name)
    lines = body_lines(m)
    assert DESCRIBED_LINE in lines
    first_figure = next(i for i, line in enumerate(lines) if line.startswith("- "))
    assert lines.index(DESCRIBED_LINE) < first_figure, "said before any figure"
    summary = body_summary(m)
    assert summary.startswith(f"{m.model}: {DESCRIBED_LINE} Its figures: "), summary
    assert "\n" not in summary


@pytest.mark.parametrize("name", DESCRIBED_BODIES)
async def test_the_body_connects_reports_and_stops_and_nothing_else(name: str) -> None:
    adapter = make(name)
    assert isinstance(adapter, DescribedAdapter)
    assert adapter.name == DESCRIBED and adapter.backend == name and adapter.robot_id == name
    live = await adapter.connect()
    assert live.digest() == describe(name).digest()
    # nothing here has heard from the robot, so it is not called standing or not fallen
    state = await adapter.get_state()
    assert state.policy == "described" and state.posture == "unknown"
    assert state.extras["fall_detection"] is False
    assert state.extras["posture_reason"] == "quackd reads nothing from a described body"
    assert await adapter.get_frame() is None
    health = await adapter.health()
    assert health.ok and health.reason == "quackd reads nothing from a described body"
    await adapter.stop()
    rest = await adapter.go_to_rest()
    assert rest.how == "none" and "keeps no rest pose" in rest.reason, rest
    assert list(adapter.implementations()) == ["stop"] and adapter.preconditions() == {}
    await adapter.close()


async def test_its_stop_says_there_is_nothing_to_stop_and_sends_nothing() -> None:
    """The core's words are "stopped (velocity zeroed)", a claim about a velocity it never had."""
    from quackd.safety import Executor
    from quackd.verbs.registry import registry_from_manifest

    adapter = make("unitree-b2")
    manifest = await adapter.connect()
    executor = Executor(registry_from_manifest(manifest, adapter), adapter, contract=None)
    result = await executor.run_verb("stop", {})
    assert result.ok and result.summary == "nothing to stop: quackd drives no part of it", result
    assert adapter.transport.intents == [] and adapter.transport.stops == 0
    await adapter.close()


def test_a_robot_yaml_body_is_as_it_was_and_is_not_told_it_has_a_maker() -> None:
    """`quackd onboard`'s body shares the class and none of this: the robot is there, on the
    computer this runs on, and its figures are its owner's."""
    from quackd.onboard import config
    from quackd.onboard.body import DescribedAdapter as OnboardAdapter

    file = config.load(Path(config.__file__).with_name("demo") / "g1-edu-mock.yaml")
    adapter = OnboardAdapter(file)
    assert adapter.implementations() == {}
    assert adapter.transport.get_state is not None
    state = adapter.transport._states[0]
    assert state.posture == "standing" and state.extras == {}
    manifest = adapter._static
    assert manifest.extras == {"described": True}
    assert DESCRIBED_LINE not in body_lines(manifest)
    assert DESCRIBED_LINE not in body_summary(manifest)


@pytest.mark.parametrize(
    ("kwargs", "flag"),
    [
        ({"address": "192.168.1.9"}, "--address"),
        ({"camera_url": ("opencv://0",)}, "--camera-url"),
        ({"token": "secret"}, "--token"),
        ({"rest_pose": {"elbow_flex": 0.0}}, "rest pose"),
    ],
)
def test_anything_that_would_reach_a_robot_is_refused(kwargs: dict[str, Any], flag: str) -> None:
    """A flag accepted and dropped would be a claim the body cannot keep."""
    with pytest.raises(AdapterError, match="drives no part of it") as e:
        make("unitree-b2", **kwargs)
    assert flag in str(e.value)


def test_an_unknown_body_names_the_ones_there_are() -> None:
    with pytest.raises(AdapterError, match="unitree-b2"):
        describe("unitree-b3")


# ── the file format ────────────────────────────────────────────────────────────────────────────

SOURCE = ', source: "acme.example: 1 kg, read 2026-11-05"'
HEAD = """
vendor: acme
model: walker
embodiment: biped
mobility: legged
blurb: a walking test body
"""
SHEET = f"""datasheet:
  manipulator: none
  mass_kg: {{value: 1, confidence: official{SOURCE}}}
"""
GOOD = HEAD + SHEET


def test_a_body_file_takes_the_manifests_fields_and_nothing_else() -> None:
    m = manifest_from("walker", parse_body("walker", GOOD))
    assert m.model == "walker" and m.verb_names() == ["report_state", "stop"]
    assert m.blurb == "a walking test body; quackd drives no part of it"
    # what a body nobody drives cannot honestly claim is refused by name, before pydantic
    claims = ("limits", "intents", "verbs", "sensors", "safety_authority", "extras", "id")
    for key in claims:
        assert key not in ALLOWED
        with pytest.raises(BodyFileError, match=rf"'{key}' is not a key a described body may"):
            parse_body("walker", GOOD + f"{key}: {{}}\n")


def test_a_missing_field_and_a_typo_inside_one_are_named_by_file() -> None:
    with pytest.raises(BodyFileError, match=r"bodies/walker.yaml: 'datasheet' is missing"):
        parse_body("walker", HEAD)
    with pytest.raises(BodyFileError, match=r"bodies/walker.yaml: 'sheet' is not a key"):
        parse_body("walker", GOOD + "sheet: {}\n")
    with pytest.raises(BodyFileError, match="not a mapping"):
        parse_body("walker", "- a list\n")
    # a figure with no source is a rumour, and pydantic's refusal carries the file's name
    with pytest.raises(BodyFileError, match=r"(?s)bodies/walker.yaml: .*source"):
        manifest_from("walker", parse_body("walker", GOOD.replace(SOURCE, "")))
    # a typo inside the datasheet is pydantic's to refuse, by path, under the file's name
    with pytest.raises(BodyFileError, match=r"(?s)bodies/walker.yaml: .*mas_kg"):
        manifest_from("walker", parse_body("walker", GOOD.replace("mass_kg", "mas_kg")))
    # a payload on a body with no manipulator is the manifest's own refusal, by file
    payload = '  payload_kg: {value: 2, confidence: official, source: "acme.example"}\n'
    with pytest.raises(BodyFileError, match=r"(?s)bodies/walker.yaml: .*no manipulator"):
        manifest_from("walker", parse_body("walker", GOOD + payload))


# ── where a described body shows up ────────────────────────────────────────────────────────────


def test_a_described_body_is_reached_by_its_spec_like_any_other() -> None:
    spec = RobotSpec(DESCRIBED, "unitree-b2", "b2")
    m = describe_spec(spec)
    assert m.id == "b2" and m.vendor == "unitree" and m.embodiment == "quadruped"
    adapter = make_adapter(spec)
    assert isinstance(adapter, DescribedAdapter) and adapter.robot_id == "b2"


def test_list_adapters_carries_the_tier_of_every_backend_and_the_kind_of_every_row() -> None:
    rows = {row["name"]: row for row in list_adapters()}
    assert rows[DESCRIBED]["kind"] == "described"
    # no adapter to have: a script asking whether this machine has a robot is told as it always was
    assert rows[DESCRIBED]["installed"] is False and rows[DESCRIBED]["adapter_installed"] is False
    assert rows[DESCRIBED]["backends"] == list(DESCRIBED_BODIES)
    assert rows[DESCRIBED]["family"] == ["described"] and rows[DESCRIBED]["route"] == []
    # nothing installs it, so `--json` names no package a script would go and install
    assert rows[DESCRIBED]["extra"] is None
    assert all(row["extra"] == f"quackd[{name}]" for name, row in rows.items() if name != DESCRIBED)
    assert rows[DESCRIBED]["tiers"]["unitree-b2"] == {
        "tier": "described",
        "owner": None,
        "date": None,
        "note": "",
    }
    duck = rows["microduck"]
    assert duck["kind"] == "adapter" and duck["family"] == ["biped"] and duck["route"] == ["wire"]
    assert set(duck["tiers"]) == set(duck["backends"])
    assert duck["tiers"]["jsonrpc"]["tier"] == "bound"
    assert duck["tiers"]["jsonrpc"]["owner"] == GRANDFATHERED
    assert duck["tiers"]["jsonrpc"]["date"] == RULE_ADOPTED
    assert duck["tiers"]["sim2d"]["tier"] == "rehearsed"
    arm = rows["lerobot"]["tiers"]["real"]
    assert arm["tier"] == "verified" and arm["owner"] == "rok" and arm["date"] == "2026-09-15"
    # the status cell is still there for whoever reads it, rendered from the tiers
    assert rows["microduck"]["status"].startswith(f"{MARK['rehearsed']} rehearsed:")


def test_a_described_body_is_never_among_the_bodies_a_task_can_be_handed_to() -> None:
    """`bodies_that_could` names bodies to hand a task to, and quackd drives no part of a
    described one, so they are a list of their own, each named by the spec that reaches it."""
    assert [name for name, _m in installed_manifests()] == list(ADAPTER_NAMES)
    assert [name for name, _m in described_manifests()] == [
        f"{DESCRIBED}:{body}" for body in DESCRIBED_BODIES
    ]
    gripped = [name for name, _m, _missing in bodies_that_could({"manipulator": "gripper"})]
    assert not any(name.startswith(f"{DESCRIBED}:") for name in gripped), gripped
    # a gripper is a need the Franka's sheet meets and no other described body's does
    assert [name for name, _m in described_that_could({"manipulator": "gripper"})] == [FRANKA]


def test_the_hint_names_a_described_body_apart_and_says_nothing_drives_it() -> None:
    hint = solo_hint({"manipulator": "gripper"}, None)
    assert hint.startswith("By their datasheets, lerobot, xlerobot, alohamini and moss could")
    assert hint.endswith(
        f" By their makers' figures alone, {FRANKA} could too, and quackd drives no part of it: "
        "each is a described body."
    ), hint
    # of the bodies quackd drives, the industrial arm's rehearsal carries 3 kg by its UR5e's
    # figure, and seven described bodies do by their makers': the first sentence names it, the
    # last names three and counts, and each says which kind of body it names
    three = solo_hint({"payload_kg": 3.0}, None)
    assert three.startswith("By their datasheets, rosarm could (needs payload_kg=3)."), three
    assert three.endswith(
        f" By their makers' figures alone, {FRANKA}, {DESCRIBED}:kuka-lbr-iisy-3-r760, "
        f"{DESCRIBED}:kuka-lbr-iiwa-7-r800 and 4 more could too, and quackd drives no part of "
        "them: each is a described body."
    ), three
    # nobody quackd drives carries 6 kg, and two described bodies do: the first sentence still
    # says no robot here could, with the most about bodies quackd drives
    nobody = solo_hint({"payload_kg": 6.0}, None)
    assert nobody.startswith("No robot installed here meets needs payload_kg=6"), nobody
    assert "the most is rosarm at 5 kg" in nobody, "the most is about bodies quackd drives"
    assert nobody.endswith(
        f" By their makers' figures alone, {DESCRIBED}:kuka-lbr-iiwa-7-r800 and "
        f"{DESCRIBED}:ur7e could, and quackd drives no part of them: each is a described body."
    ), nobody
    assert "described" not in solo_hint({"payload_kg": 500.0}, None)
    assert solo_hint({}, None) == ""


def test_a_described_body_is_never_the_default_robot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare core has the described row and nothing that drives, and a command with no robot
    named must still say no robot adapter is installed rather than hand over a body that moves
    nothing and reads as a run."""
    from quackd.adapters import factory
    from quackd.adapters.factory import NoRobotNamed

    monkeypatch.setattr(factory, "_installed", lambda: {DESCRIBED: "quackd.adapters.described"})
    with pytest.raises(NoRobotNamed, match="no robot adapter is installed"):
        default_spec()


def test_the_row_names_no_body_by_itself() -> None:
    """The ten have a first backend a bare name means. This row's backends are each a different
    robot, so a bare `--robot described` is refused with the bodies there are."""
    from quackd.adapters.factory import parse_robot_spec

    with pytest.raises(AdapterError, match="names no body by itself") as e:
        parse_robot_spec(DESCRIBED)
    assert "--robot described:<body>" in str(e.value) and "unitree-b2" in str(e.value)
    assert parse_robot_spec("described:unitree-b2").key == "described:unitree-b2"
    assert parse_robot_spec("microduck").key == "microduck:sim2d", "the ten keep their first"


def test_a_registered_described_body_carries_nothing_that_reaches_a_robot(tmp_path: Path) -> None:
    """Refused at the door rather than at the first run, in the words its `make()` uses."""
    from quackd.registry import Registry, RobotEntry, probe_all

    registry = Registry(str(tmp_path))
    for field, flag in (
        ("address", "--address"),
        ("camera_url", "--camera-url"),
        ("token", "--token"),
    ):
        with pytest.raises(RegistryError, match=f"takes no {flag}: quackd drives no part"):
            registry.add_robot(
                RobotEntry(name="b2", spec="described:unitree-b2", **{field: "192.168.1.9"})
            )
    with pytest.raises(RegistryError, match="takes no rest pose: quackd drives no part"):
        registry.add_robot(
            RobotEntry(name="b2", spec="described:unitree-b2", rest_pose={"elbow_flex": 1.0})
        )
    assert registry.robots() == {}, "nothing was written"
    # a pilot is anybody's
    entry = registry.add_robot(
        RobotEntry(name="b2", spec="described:unitree-b2", llm="fake", note="in the lab")
    )
    assert entry.robot_spec.key == "described:unitree-b2"
    with pytest.raises(RegistryError, match="takes no --address: quackd drives no part"):
        registry.update_robot("b2", {"address": "192.168.1.9"})
    assert registry.update_robot("b2", {"note": "moved"}).note == "moved"
    # a probe says neither reachable nor down: a tick would read as a robot that answered
    (result,) = probe_all(registry.robots().values(), timeout_s=5).values()
    assert result.reachable is None and result.detail == "described: nothing to reach"


def test_a_hand_edited_line_takes_no_other_robot_down_and_can_be_mended(tmp_path: Path) -> None:
    """The door is strict and the shelf is not, as for a spec this quackd cannot build: a
    described body with an address in the file used to stop every command that read the file,
    `robot edit` and `robot remove` among them, until somebody opened it in an editor."""
    import json

    from quackd.registry import Registry, RobotEntry

    registry = Registry(str(tmp_path))
    registry.add_robot(RobotEntry(name="duck-a", spec="microduck:mock"))
    registry.add_robot(RobotEntry(name="b2", spec="described:unitree-b2"))
    data = json.loads(registry.robots_path.read_text(encoding="utf-8"))
    data["robots"]["b2"]["address"] = "192.168.1.9"
    registry.robots_path.write_text(json.dumps(data), encoding="utf-8")

    robots = registry.robots()
    assert robots["duck-a"].robot_spec.key == "microduck:mock"
    assert robots["b2"].address == "192.168.1.9"
    # refused where it is used, by the body's own make
    with pytest.raises(AdapterError, match="takes no --address"):
        make_adapter(robots["b2"].robot_spec, **robots["b2"].adapter_kwargs())
    # an edit of something else goes through, and the mend does
    assert registry.update_robot("b2", {"note": "moved"}).address == "192.168.1.9"
    assert registry.update_robot("b2", {"address": None}).address is None


def test_one_bare_arm_is_told_it_holds_nothing_and_meets_no_need_for_hands() -> None:
    """It was told `1 arms and no gripper: it holds by closing both on a thing`, against its own
    notes, and offered for a task that needs arms by figures no maker publishes."""
    ur5e = describe("ur5e")
    said = " ".join(body_lines(ur5e))
    assert "one arm with a bare wrist: it holds nothing until somebody fits a tool" in said
    assert "closing both" not in said
    assert missing_needs({"manipulator": "arms"}, ur5e) == ["manipulator = arms (has a bare wrist)"]
    assert missing_needs({"manipulator": "any"}, ur5e) == ["manipulator = any (has a bare wrist)"]
    assert missing_needs({"payload_kg": 5.0, "reach_m": 0.8}, ur5e) == []
    # two arms still close on a thing, and hold by it
    asimov = describe("menlo-asimov-1")
    assert missing_needs({"manipulator": "arms"}, asimov) == []
    assert "closing both" in " ".join(body_lines(asimov))


def test_announce_refuses_a_body_that_is_on_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from quackd.cli import app
    from quackd.lan import announce as lan_announce

    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("it announced a robot that is not here")

    monkeypatch.setattr(lan_announce, "announce", never)
    result = CliRunner().invoke(
        app, ["announce", "--robot", "described:unitree-b2", "--for", "0.1"]
    )
    assert result.exit_code == 1
    assert "no robot here to announce" in " ".join(result.output.split()), result.output


def test_the_refusal_with_nothing_installed_names_the_nine_and_never_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quackd.adapters import factory
    from quackd.adapters.factory import NoRobotNamed

    monkeypatch.setattr(factory, "_installed", dict)
    with pytest.raises(NoRobotNamed) as e:
        default_spec()
    assert "lerobot" in str(e.value) and "moss" in str(e.value)
    assert DESCRIBED not in str(e.value), str(e.value)


def test_a_third_partys_import_is_not_asked_whether_it_drives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A command with no robot named asks every adapter here whether it drives, and one that
    raises as it is imported must not take that command down."""
    from quackd.adapters import factory

    def explodes(name: str) -> Any:
        raise RuntimeError("it imports badly")

    monkeypatch.setattr(factory, "_module", explodes)
    assert drives("acme") is True


@pytest.mark.parametrize(
    "tiers",
    [
        {"mock": {"name": "simulator"}},
        {"mock": {"name": "rehearsed", "note": 3}},
        {"mock": {"name": "bound", "date": dt.date(2026, 11, 5), "owner": "ann"}},
        {"mock": {"nonsense": True}},
        {"mock": "rehearsed"},
        {"mock": Tier("planned")},  # type: ignore[arg-type]
    ],
    ids=["unknown-tier", "numeric-note", "a-date-object", "unknown-field", "a-word", "a-bad-tier"],
)
def test_a_third_partys_odd_tiers_are_read_as_none_and_stop_nothing(
    monkeypatch: pytest.MonkeyPatch, tiers: dict[str, Any]
) -> None:
    import types

    from quackd.adapters import factory

    module: Any = types.ModuleType("quackd_acme")
    module.BACKENDS = ("mock",)
    module.STATUS = "installed here"
    module.TIERS = tiers
    monkeypatch.setattr(factory, "_module", lambda name: module)
    row = factory.info("acme")
    assert row.tiers == {} and row.status == "installed here"


def test_the_registry_refuses_the_row_and_its_bodies_as_robot_names() -> None:
    with pytest.raises(RegistryError, match="the row of bodies quackd only describes"):
        check_name(DESCRIBED)
    with pytest.raises(RegistryError, match="memory file name of an unregistered robot"):
        check_name("described-unitree-b2")
    assert check_name("b2") == "b2"


def test_doctor_counts_the_described_bodies_apart_from_the_adapters() -> None:
    from quackd import doctor

    report = doctor.collect()
    kinds = {row["name"]: row["kind"] for row in report.adapters}
    assert kinds[DESCRIBED] == "described" and set(kinds.values()) == {"adapter", "described"}
    buf = io.StringIO()
    Console(file=buf, width=200).print(doctor.verdict(report))
    said = " ".join(buf.getvalue().split())
    assert f"/{len(ADAPTER_NAMES)} adapters installed" in said, said
    assert f"{len(DESCRIBED_BODIES)} described bodies" in said, said


def test_the_web_page_offers_no_connect_block_for_a_described_body() -> None:
    from quackd.webui import catalog

    assert catalog.drives("microduck") and not catalog.drives(DESCRIBED)
    assert catalog.connect(DESCRIBED, "unitree-b2") is None
    assert catalog.drives("nobody-published-this"), "an unknown adapter is left to the page"


# ── a flock ────────────────────────────────────────────────────────────────────────────────────


async def test_a_pilot_flock_takes_a_described_body_beside_a_duck(tmp_path: Path) -> None:
    """The member's pilot has `report_state`, `stop` and `tell`, is told its body moves only if
    someone else moves it, and the flock plans and ends without an error."""
    import asyncio

    from quackd.agent.providers.fake import FakeProvider
    from quackd.duckfile.parser import load_duck
    from quackd.flock.pilots import roster_from_specs, run_pilot_flock

    roster = roster_from_specs(
        {
            "duck": RobotSpec("microduck", "mock", "duck"),
            "b2": RobotSpec(DESCRIBED, "unitree-b2", "b2"),
        }
    )
    result = await asyncio.wait_for(
        run_pilot_flock(
            load_duck("flock-hello"),
            roster,
            providers={name: FakeProvider.for_duck("flock-hello") for name in roster},
            runs_dir=tmp_path,
        ),
        timeout=120,
    )
    assert set(result.per_member) == {"duck", "b2"}
    assert result.per_member["b2"]["robot"] == f"{DESCRIBED}:unitree-b2"
    assert result.outcome == "success", result.reason
    assert {r["outcome"] for r in result.per_member.values()} == {"success"}


# ── the tiers and the owner rule (ADR-0076) ───────────────────────────────────────────────────


@pytest.mark.parametrize("info", OFFICIAL, ids=lambda i: i.name)
def test_every_backend_has_a_tier_and_bound_code_names_its_owner(info: AdapterInfo) -> None:
    assert set(info.tiers) == set(info.backends), info.name
    for backend, tier in info.tiers.items():
        assert tier.name in TIERS, (info.name, backend)
        assert tier.mark == MARK[tier.name]
        if tier.name in ("bound", "verified"):
            assert tier.owner, f"{info.name}:{backend} is {tier.name} and names no owner"
            assert tier.date, f"{info.name}:{backend} is {tier.name} and names no date"
            when = dt.date.fromisoformat(tier.date)  # an ISO date, or this raises
        if tier.name == "verified":
            # a bound backend's date may lie ahead, since it is the day a first run is named
            # for; a verified one's is the day it ran
            assert when <= dt.date.today(), (info.name, backend)
            assert tier.owner != GRANDFATHERED, "a run that happened has somebody's name on it"
        if tier.name == "described" and info.kind == "adapter":
            # bound code nobody has named a date for, which an adapter may carry only where its
            # make() refuses it in the owner rule's words, for every robot type (ADR-0076)
            _refused_by_the_owner_rule(info, backend)
    if info.kind == "described":
        assert {tier.name for tier in info.tiers.values()} == {"described"}


OWNER_RULE = "bound code needs a named owner and a date for its first hardware run (docs/adr/0076)"
"""The words every refusal of bound code nobody answers for carries, the Koch's included."""


def _refused_by_the_owner_rule(info: AdapterInfo, backend: str) -> None:
    from quackd.adapters.factory import refusal

    for robot_type in info.robot_types or (None,):
        spec = RobotSpec(info.name, backend, robot_type=robot_type)
        with pytest.raises(AdapterError) as refused:
            make_adapter(spec)
        said = str(refused.value)
        assert OWNER_RULE in said, (info.name, backend, robot_type, said)
        # the registry's door asks the same question and is told the same words
        assert refusal(spec) == said, (info.name, backend, robot_type)


@pytest.mark.parametrize("info", ADAPTERS, ids=lambda i: i.name)
def test_an_adapter_names_its_family_and_its_route(info: AdapterInfo) -> None:
    assert 1 <= len(info.family) <= 2 and all(f in FAMILIES for f in info.family), info.name
    assert info.family[0] != "described"
    assert info.family[0] == describe_spec(RobotSpec(info.name, info.backends[0])).embodiment
    if len(info.family) == 2:
        assert info.family[1] == "arm", "a second family is a body's arms"
    assert info.route and all(r in ROUTES for r in info.route), info.name
    # every simulator and mock is rehearsed, and every backend with a connect block reaches a
    # robot, which is what bound and verified mean; a backend described on an adapter is bound
    # code its make() refuses (above), and has no connect block, since nobody can connect it
    reaching = {b for b, t in info.tiers.items() if t.name in ("bound", "verified")}
    refused = {b for b, t in info.tiers.items() if t.name == "described"}
    assert reaching == set(info.connect), info.name
    assert all(
        info.tiers[b].name == "rehearsed" for b in info.backends if b not in reaching | refused
    )


def test_the_nine_are_grandfathered_and_say_so() -> None:
    bound = [
        (info.name, backend)
        for info in ADAPTERS
        for backend, tier in info.tiers.items()
        if tier.name == "bound"
    ]
    assert len(bound) == 8, bound
    for name, backend in bound:
        tier = BY_NAME[name].tiers[backend]
        assert (tier.owner, tier.date) == (GRANDFATHERED, RULE_ADOPTED), (name, backend)
    # the industrial arms came after the rule, and their bound code waits for a date
    assert BY_NAME["rosarm"].tiers["ws"].name == "described"
    (verified,) = [
        (info.name, b) for info in ADAPTERS for b, t in info.tiers.items() if t.name == "verified"
    ]
    assert verified == ("lerobot", "real")


def test_the_status_line_groups_the_tiers_and_puts_the_owner_in_brackets() -> None:
    info = AdapterInfo(
        name="acme",
        backends=("mock", "ws", "real"),
        status="",
        summary="a test body",
        tiers={
            "mock": Tier("rehearsed", note="in the package"),
            "ws": Tier("bound", owner="ann", date="2026-11-01", note="never run"),
            "real": Tier("verified", owner="bob", date="2026-10-01"),
        },
        family=("wheeled",),
        route=("sdk",),
    )
    line = status_line(info)
    assert line == info.status, "a row with no status text renders one from its tiers"
    assert line.startswith(f"{MARK['verified']} verified: real (bob, 2026-10-01)"), line
    assert f"{MARK['rehearsed']} rehearsed: mock (in the package)" in line
    assert f"{MARK['bound']} bound: ws (never run; ann, 2026-11-01)" in line
    assert line.index("verified") < line.index("rehearsed") < line.index("bound")


def test_a_grandfathered_backend_prints_no_date_and_the_described_row_names_no_backend() -> None:
    duck = BY_NAME["microduck"].status
    assert f"; {GRANDFATHERED})" in duck and RULE_ADOPTED not in duck, duck
    assert "in the package" not in duck, "a note every mock would repeat is no note"
    assert BY_NAME["lerobot"].status.startswith(f"{MARK['verified']} verified: real (")
    assert "rok, 2026-09-15)" in BY_NAME["lerobot"].status
    # the described row's backends are the column beside the cell, so the cell is the tier
    assert BY_NAME[DESCRIBED].status == f"{MARK['described']} described"


def test_the_table_prints_one_tier_to_a_line() -> None:
    """Read off the real table: the cell used to be one run of text that wrapped where the
    column happened to end, so a reader could not see where rehearsed stopped and bound began."""
    from quackd import ui

    buf = io.StringIO()
    Console(file=buf, width=240).print(ui.adapters_table(list_adapters()))
    lines = buf.getvalue().splitlines()
    # a backend is a word somebody types back, so a narrow table folds it and never cuts it
    narrow = io.StringIO()
    Console(file=narrow, width=80).print(ui.adapters_table(list_adapters()))
    assert "…" not in "".join(
        line.split("│")[2] for line in narrow.getvalue().splitlines() if line.count("│") >= 4
    ), narrow.getvalue()
    (first,) = [i for i, line in enumerate(lines) if "│ microduck" in line]
    assert "rehearsed: sim2d, mujoco (physics, needs quackd[mujoco]), mock" in lines[first]
    assert "bound" not in lines[first] and "bound: jsonrpc" in lines[first + 1], lines[first + 1]
    (described,) = [line for line in lines if f"│ {DESCRIBED}" in line]
    assert "described" in described.split("│")[3] and "built-in" in described
    # a row with no tiers, a third party's that says only STATUS, prints its status as it did
    row = {
        "name": "acme",
        "backends": ["mock"],
        "status": "installed here",
        "extra": None,
        "installed": True,
    }
    buf = io.StringIO()
    Console(file=buf, width=120).print(ui.adapters_table([row]))
    assert "installed here" in buf.getvalue() and "built-in" in buf.getvalue()


def test_a_tier_reads_back_from_the_record_json_carries() -> None:
    tier = Tier("bound", owner="ann", date="2026-11-01", note="never run")
    assert Tier.from_dict(tier.to_dict()) == tier
    with pytest.raises(ValueError, match="no tier is called 'planned'"):
        Tier.from_dict({"tier": "planned"})


def test_a_row_rebuilt_with_fewer_backends_keeps_the_tiers_of_those_it_kept() -> None:
    """`dataclasses.replace` is how a test takes the mock away from a row, and a row whose
    status still spoke of a backend it no longer has would be the table disagreeing with
    itself."""
    import dataclasses

    arm = BY_NAME["lerobot"]
    unmocked = dataclasses.replace(arm, backends=tuple(b for b in arm.backends if b != "mock"))
    assert tuple(unmocked.tiers) == unmocked.backends == ("real", "mujoco")
    assert "mock" not in unmocked.status and unmocked.status.startswith(MARK["verified"])
    assert "mock" in arm.status, "and the row it was made from is as it was"


def test_a_row_whose_tiers_and_backends_disagree_is_refused_at_import() -> None:
    with pytest.raises(ValueError, match="no tier for mock"):
        AdapterInfo(
            name="acme",
            backends=("mock",),
            status="",
            summary="a test body",
            tiers={"ws": Tier("bound", owner="ann", date="2026-11-01")},
        )
    with pytest.raises(ValueError, match="tier"):
        AdapterInfo(
            name="acme",
            backends=("mock",),
            status="",
            summary="a test body",
            tiers={"mock": Tier("planned")},  # type: ignore[arg-type]
        )


def test_a_third_party_may_declare_tiers_a_family_and_a_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read tolerantly, as `CONNECT` is: absent gives nothing and the module's `STATUS` prints."""
    import types

    from quackd.adapters import factory

    module: Any = types.ModuleType("quackd_acme")
    module.BACKENDS = ("mock", "ws")
    module.STATUS = "installed here"
    module.TIERS = {
        "mock": {"name": "rehearsed"},
        "ws": Tier("bound", owner="ann", date="2026-11-01"),
    }
    module.FAMILY = "wheeled"
    module.ROUTE = ("wire", "bridge")
    monkeypatch.setattr(factory, "_module", lambda name: module)
    row = factory.info("acme")
    assert row.kind == "adapter"
    assert {b: t.name for b, t in row.tiers.items()} == {"mock": "rehearsed", "ws": "bound"}
    # a row with tiers has no status of its own: it is the tiers, rendered
    assert row.status == (
        f"{MARK['rehearsed']} rehearsed: mock · {MARK['bound']} bound: ws (ann, 2026-11-01)"
    )
    assert row.family == ("wheeled",) and row.route == ("wire", "bridge")
    # a module with tiers for some backends only is read as declaring none
    module.TIERS = {"mock": {"name": "rehearsed"}}
    assert factory.info("acme").tiers == {}
    del module.TIERS, module.FAMILY, module.ROUTE
    bare = factory.info("acme")
    assert bare.tiers == {} and bare.family == () and bare.route == ()
    assert bare.status == "installed here"
