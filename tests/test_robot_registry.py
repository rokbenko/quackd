"""`quackd/registry.py`: the robots you have named and the flocks you made of them.

Not `tests/test_registry.py`, which is the verb registry. The two share a word and nothing
else.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from quackd.adapters.base import AdapterError
from quackd.host import resolve_host
from quackd.registry import (
    FlockNotRunnable,
    Registry,
    RegistryError,
    RobotEntry,
    RobotInUse,
    StoredFlock,
    UnknownFlock,
    UnknownRobot,
    check_name,
    probe_all,
    probe_entry,
    registry_dir,
    resolve_robot_ref,
)


def _entry(name: str = "duck-a", spec: str = "microduck:mock", **kw: object) -> RobotEntry:
    return RobotEntry(name=name, spec=spec, **kw)  # type: ignore[arg-type]


# ── storage ─────────────────────────────────────────────────────────────────────────────


def test_a_robot_survives_the_process_with_every_field(tmp_path: Path) -> None:
    reg = Registry(tmp_path)
    reg.add_robot(
        _entry(
            address="tcp://10.0.0.5:9871",
            token="s3cret",
            camera_url="http://10.0.0.5:9872/snapshot.jpg",
            llm="anthropic:claude-opus-5",
            note="the cream one",
        )
    )
    again = Registry(tmp_path).robot("duck-a")
    assert again.spec == "microduck:mock"
    assert again.address == "tcp://10.0.0.5:9871"
    assert again.token == "s3cret"
    assert again.llm == "anthropic:claude-opus-5"
    assert again.note == "the cream one"
    stored = json.loads((tmp_path / "robots.json").read_text(encoding="utf-8"))
    assert stored["version"] == 1
    assert "name" not in stored["robots"]["duck-a"], "the key is the name; storing it twice drifts"
    assert stored["robots"]["duck-a"]["llm"] == "anthropic:claude-opus-5"


def test_a_write_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    reg = Registry(tmp_path / "nested" / "deeper")
    reg.add_robot(_entry())
    assert reg.robots_path.exists()
    assert not list(reg.robots_path.parent.glob("*.tmp"))


def test_the_directory_is_the_flag_then_the_env_then_the_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUACKD_REGISTRY_DIR", str(tmp_path / "from-env"))
    assert registry_dir(tmp_path / "from-flag") == tmp_path / "from-flag"
    assert registry_dir() == tmp_path / "from-env"
    monkeypatch.delenv("QUACKD_REGISTRY_DIR")
    assert registry_dir() == Path("~/.quackd").expanduser()


def test_broken_json_is_one_line_and_the_file_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "robots.json").write_text("{ oh no", encoding="utf-8")
    reg = Registry(tmp_path)
    with pytest.raises(RegistryError, match="not valid JSON"):
        reg.robots()
    with pytest.raises(RegistryError):
        reg.add_robot(_entry())
    assert (tmp_path / "robots.json").read_text(encoding="utf-8") == "{ oh no"


def test_a_hand_edited_typo_names_its_own_field(tmp_path: Path) -> None:
    """Strict, unlike memory: a dropped `address` would send a robot to the wrong place."""
    (tmp_path / "robots.json").write_text(
        json.dumps({"version": 1, "robots": {"duck-a": {"spec": "microduck:mock", "adress": "x"}}}),
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="adress"):
        Registry(tmp_path).robots()


def test_a_newer_file_is_refused_rather_than_half_read(tmp_path: Path) -> None:
    (tmp_path / "flocks.json").write_text(
        json.dumps({"version": 99, "flocks": {}}), encoding="utf-8"
    )
    with pytest.raises(RegistryError, match="version 99"):
        Registry(tmp_path).flocks()


# ── names ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("Duck-A", "not a valid"),
        ("-duck", "not a valid"),
        ("x" * 65, "not a valid"),
        ("3", "simulated ducks"),
        ("microduck", "is an adapter"),
        ("microduck-sim2d", "memory file name"),
    ],
)
def test_a_name_may_not_be_something_else_it_could_be_mistaken_for(name: str, why: str) -> None:
    with pytest.raises(RegistryError, match=why):
        check_name(name)


def test_a_flock_may_be_called_after_an_adapter_but_not_after_a_number() -> None:
    assert check_name("microduck", kind="flock") == "microduck"
    with pytest.raises(RegistryError, match="simulated ducks"):
        check_name("4", kind="flock")


def test_the_spec_is_normalised_and_a_bad_one_keeps_the_adapters_own_words() -> None:
    """A spec that parses is stored canonically. One that does not is kept as written, and the
    adapter table's own words say why on the robot that is refused for it."""
    assert _entry(spec="MicroDuck").spec == "microduck:sim2d"
    assert _entry(spec="microduck:mock").spec_problem is None
    kept = _entry(spec=" bogus:nope ")
    assert (kept.spec, kept.adapter, kept.backend) == ("bogus:nope", "bogus", "nope")
    assert "unknown adapter 'bogus'" in (kept.spec_problem or "")
    with pytest.raises(AdapterError, match=r"^duck-a is registered as bogus:nope, which"):
        _ = kept.robot_spec
    assert _entry(spec="bogus").backend == "", "no colon reads as no backend, not a crash"


def test_the_door_still_refuses_a_spec_that_does_not_parse(tmp_path: Path) -> None:
    """`add_robot`, and an edit that sets the spec, check what is being written, in the
    adapter's own words: the lenient read is for files that were already there."""
    reg = Registry(tmp_path)
    with pytest.raises(RegistryError, match="unknown adapter 'bogus'"):
        reg.add_robot(_entry(spec="bogus:nope"))
    reg.add_robot(_entry())
    with pytest.raises(RegistryError, match="unknown backend 'websocket' for microduck"):
        reg.update_robot("duck-a", {"spec": "microduck:websocket"})
    assert reg.robot("duck-a").spec == "microduck:mock"
    assert not (tmp_path / "robots.json").read_text(encoding="utf-8").count("bogus")


def _shelve(tmp_path: Path, name: str, spec: str, **fields: object) -> None:
    """A robot written to the file as an older quackd, a hand or a lost adapter left it."""
    path = tmp_path / "robots.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["robots"][name] = {"spec": spec, **fields}
    path.write_text(json.dumps(stored), encoding="utf-8")


@pytest.mark.parametrize(
    ("spec", "why", "fix"),
    [
        # a backend its adapter has dropped, as 0.19.0 dropped this one
        (
            "microduck:websocket",
            "unknown backend 'websocket' for microduck",
            "quackd robot edit kitchen --spec microduck:<backend>",
        ),
        # an adapter not installed here: a third party's, or one a newer quackd publishes
        ("acme:arm", "unknown adapter 'acme'", "install the package that provides acme, quackd"),
    ],
)
def test_a_robot_this_quackd_cannot_build_no_longer_locks_the_file(
    tmp_path: Path, spec: str, why: str, fix: str
) -> None:
    """One such robot used to make every read of the file refuse, `quackd robot remove` among
    them, until its entry was deleted by hand. It reads, edits and removes like any other, the
    robot beside it resolves, and using it is refused by its name, with the fix."""
    reg = Registry(tmp_path)
    reg.add_robot(_entry())
    _shelve(tmp_path, "kitchen", spec, note="the old one", camera_url=["a", "b"])

    entries = reg.robots()
    kitchen = entries["kitchen"]
    assert kitchen.spec == spec and why in (kitchen.spec_problem or "")
    assert kitchen.public()["spec_problem"] == kitchen.spec_problem
    assert entries["duck-a"].public()["spec_problem"] is None
    assert resolve_robot_ref("duck-a", reg).spec.key == "microduck:mock"
    with pytest.raises(AdapterError) as refused:
        resolve_robot_ref("kitchen", reg)
    said = str(refused.value)
    assert said.startswith(f"kitchen is registered as {spec}, which this quackd cannot build ("), (
        said
    )
    assert why in said and fix in said and said.endswith(", or quackd robot remove kitchen"), said

    # an edit that leaves the spec alone keeps it as written, and remove forgets it
    assert reg.update_robot("kitchen", {"note": "still the old one"}).spec == spec
    assert reg.remove_robot("kitchen") == []
    assert list(reg.robots()) == ["duck-a"]


def test_an_edit_of_the_spec_mends_a_robot_this_quackd_cannot_build(tmp_path: Path) -> None:
    reg = Registry(tmp_path)
    reg.add_robot(_entry())
    _shelve(tmp_path, "kitchen", "microduck:websocket", address="tcp://127.0.0.1:9870")
    moved = reg.update_robot("kitchen", {"spec": "microduck:jsonrpc"})
    assert moved.spec_problem is None and moved.address == "tcp://127.0.0.1:9870"
    assert resolve_robot_ref("kitchen", reg).spec.key == "microduck:jsonrpc"


def test_a_pilot_the_shelf_cannot_resolve_is_kept_rather_than_refused() -> None:
    """The door refuses one of these and reading one never does.

    `update_robot` reads every entry before it writes one, so an entry refused on the way in
    would be refused by `quackd robot list`, `show`, `remove` and by the `quackd robot edit`
    that would mend it: one bad line would take the file down and leave no command able to fix
    it. A vendor quackd drops, or a model id a catalogue retires, both arrive that way."""
    assert _entry(llm="hal9000").llm == "hal9000"


def test_one_unreadable_pilot_does_not_take_the_other_robots_with_it(tmp_path: Path) -> None:
    (tmp_path / "robots.json").write_text(
        json.dumps(
            {
                "version": 1,
                "robots": {
                    "duck-a": {"spec": "microduck:mock", "llm": "hal9000"},
                    "duck-b": {"spec": "microduck:mock", "llm": "fake"},
                },
            }
        ),
        encoding="utf-8",
    )
    robots = Registry(tmp_path).robots()
    assert sorted(robots) == ["duck-a", "duck-b"]
    assert robots["duck-b"].llm == "fake"


@pytest.mark.parametrize(
    ("legacy", "folded"),
    [
        ({"provider": "openai"}, "openai"),
        ({"provider": "openai", "model": "gpt-4o"}, "openai:gpt-4o"),
        ({"model": "claude-opus-5"}, None),
        ({"provider": None, "model": None}, None),
        ({"provider": "openai", "model": None}, "openai"),
        ({"model": "mistral"}, None),
    ],
    ids=[
        "a-vendor-alone",
        "a-vendor-and-a-model",
        "a-model-alone-named-no-pilot",
        "neither",
        "a-null-model",
        "a-model-alone-that-shares-a-vendors-name",
    ],
)
def test_a_file_written_before_the_two_flags_became_one_still_reads(
    legacy: dict[str, str | None], folded: str | None
) -> None:
    """`robots.json` is user data nobody re-saves on upgrade.

    The file a person wrote for 0.10 is still the file on disk under 0.11, and `extra="forbid"`
    would otherwise refuse the whole entry over a key that used to be correct. The fold runs
    before field validation, so it happens ahead of that refusal.

    A `model` with no `provider` is the case worth pinning. It named no pilot under the old
    pair, because the vendor came from the flag or from the default and the default was `fake`,
    which ignores a model id. So it names none now either: read as a spec, `{"model":
    "mistral"}` would quietly move a run that cost nothing onto a paid API.
    """
    entry = RobotEntry.model_validate({"name": "duck-a", "spec": "microduck:mock", **legacy})
    assert entry.llm == folded


def test_the_old_keys_are_gone_from_the_file_after_the_next_write(tmp_path: Path) -> None:
    """Reading the old shape is a kindness; writing it back would keep the file in two minds.
    One `quackd robot edit` -- or anything else that saves -- and the entry is the new shape."""
    (tmp_path / "robots.json").write_text(
        json.dumps(
            {
                "version": 1,
                "robots": {
                    "duck-a": {"spec": "microduck:mock", "provider": "openai", "model": "gpt-4o"}
                },
            }
        ),
        encoding="utf-8",
    )
    reg = Registry(tmp_path)
    assert reg.robot("duck-a").llm == "openai:gpt-4o"
    reg.update_robot("duck-a", {"note": "the cream one"})
    stored = json.loads((tmp_path / "robots.json").read_text(encoding="utf-8"))["robots"]["duck-a"]
    assert stored["llm"] == "openai:gpt-4o"
    assert "provider" not in stored and "model" not in stored


def test_a_file_that_says_it_both_ways_is_refused(tmp_path: Path) -> None:
    """An entry carrying `llm` and an old `provider` says two different things about which
    pilot this robot flies, and guessing which half the person meant is worse than stopping:
    the fix is one line deleted, and only they know which line."""
    (tmp_path / "robots.json").write_text(
        json.dumps(
            {
                "version": 1,
                "robots": {
                    "duck-a": {"spec": "microduck:mock", "llm": "openai", "provider": "anthropic"}
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="provider"):
        Registry(tmp_path).robots()


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("claude-opus-5", "anthropic:claude-opus-5"),
        ("OpenAI:gpt-4o", "openai:gpt-4o"),
        ("  anthropic  ", "anthropic"),
        ("ollama:llama3:8b", "ollama:llama3:8b"),
    ],
    ids=["a-bare-id-gains-its-vendor", "a-vendor-is-folded", "trimmed", "a-tag-keeps-its-colon"],
)
def test_the_pilot_is_stored_in_one_spelling(given: str, stored: str) -> None:
    """Two entries meaning the same pilot must not read as two different pilots, so the shelf
    keeps one spelling: the vendor lowercased and in front, the model half left exactly as the
    vendor ships it (`Qwen/Qwen3-8B` folded is a 404), and an Ollama tag's own colon intact."""
    assert _entry(llm=given).llm == stored


def test_a_model_the_catalogue_no_longer_lists_still_comes_off_the_shelf(tmp_path: Path) -> None:
    """Strict at the door, lenient on the shelf, and this is the lenient half.

    The catalogue moves under a file that does not: a vendor retires an id and every entry
    naming it is suddenly invalid. A strict read here would make every `quackd robot` command
    refuse, including the `quackd robot edit` that would fix the entry, because `update_robot`
    reads every robot in the file before it writes one. So the shelf keeps loading. It is the
    run naming that robot that stops, and `test_cli_robot.py` is where that half is held.
    """
    (tmp_path / "robots.json").write_text(
        json.dumps(
            {"version": 1, "robots": {"duck-a": {"spec": "microduck:mock", "llm": "openai:gpt-5"}}}
        ),
        encoding="utf-8",
    )
    reg = Registry(tmp_path)
    assert reg.robot("duck-a").llm == "openai:gpt-5"
    assert reg.robot("duck-a").public()["llm"] == "openai:gpt-5"
    # and the entry is still editable, which is the whole point of not refusing the read
    assert reg.update_robot("duck-a", {"note": "retire me"}).llm == "openai:gpt-5"


# ── robots ──────────────────────────────────────────────────────────────────────────────


def test_registering_the_same_name_twice_is_refused(tmp_path: Path) -> None:
    reg = Registry(tmp_path)
    reg.add_robot(_entry())
    with pytest.raises(RegistryError, match="already registered"):
        reg.add_robot(_entry(spec="microduck:sim2d"))


def test_an_unknown_robot_is_one_line(tmp_path: Path) -> None:
    with pytest.raises(UnknownRobot, match="no robot called 'nope'"):
        Registry(tmp_path).robot("nope")


def test_update_sets_clears_and_moves_the_clock_on(tmp_path: Path) -> None:
    reg = Registry(tmp_path)
    added = reg.add_robot(_entry(address="tcp://old:1", note="keep me"))
    changed = reg.update_robot("duck-a", {"address": "tcp://new:2"})
    assert changed.address == "tcp://new:2"
    assert changed.note == "keep me", "a field nobody named is left alone"
    assert changed.added == added.added
    cleared = reg.update_robot("duck-a", {"address": None})
    assert cleared.address is None


def test_update_rejects_a_bad_value_without_writing_it(tmp_path: Path) -> None:
    reg = Registry(tmp_path)
    reg.add_robot(_entry())
    with pytest.raises(RegistryError, match="unknown provider"):
        reg.update_robot("duck-a", {"llm": "hal9000"})
    assert reg.robot("duck-a").llm is None


def test_the_memory_key_is_the_name_registered_and_the_spec_otherwise(tmp_path: Path) -> None:
    reg = Registry(tmp_path)
    reg.add_robot(_entry())
    assert resolve_robot_ref("duck-a", reg).memory_key == "duck-a"
    assert resolve_robot_ref("microduck:mock", reg).memory_key == "microduck:mock"


# ── the rest pose ───────────────────────────────────────────────────────────────────────

_FOLDED = {
    "shoulder_pan": 0.0,
    "shoulder_lift": -113.5,
    "elbow_flex": 95.0,
    "wrist_flex": 12.5,
}
"""The pose the bench arm was folded into. `shoulder_lift` is past the travel its own
calibration file gives it, which is normal for a pose a person pushed the arm into by hand,
and the reason nothing here treats a recorded pose as a goal it may range-check."""


def test_a_rest_pose_survives_the_file_and_a_file_written_without_one_still_loads(
    tmp_path: Path,
) -> None:
    """A robots.json written before arms were parked has no `rest_pose` key at all, and
    upgrading quackd must not mean editing it: the field is absent rather than null, and
    every robot in that file has to keep loading, with no pose recorded for it."""
    reg = Registry(tmp_path)
    reg.add_robot(_entry("arm", "lerobot:real", rest_pose=_FOLDED))
    again = Registry(tmp_path).robot("arm")
    assert again.rest_pose == _FOLDED
    assert again.public()["rest_pose"] == _FOLDED
    stored = json.loads((tmp_path / "robots.json").read_text(encoding="utf-8"))
    assert stored["robots"]["arm"]["rest_pose"] == _FOLDED, "degrees by joint, as they were read"

    (tmp_path / "robots.json").write_text(
        json.dumps({"version": 1, "robots": {"arm": {"spec": "lerobot:real"}}}), encoding="utf-8"
    )
    older = Registry(tmp_path).robot("arm")
    assert older.rest_pose is None
    assert older.public()["rest_pose"] is None
    assert older.adapter_kwargs()["rest_pose"] is None


@pytest.mark.parametrize(
    ("pose", "why"),
    [
        ({}, "rest_pose must name at least one joint"),
        ({"elbow_flex": float("inf")}, r"rest_pose\.elbow_flex must be a finite number of degrees"),
        (
            {"elbow_flex": float("-inf")},
            r"rest_pose\.elbow_flex must be a finite number of degrees",
        ),
        ({"elbow_flex": float("nan")}, r"rest_pose\.elbow_flex must be a finite number of degrees"),
    ],
)
def test_a_rest_pose_that_is_not_finite_degrees_by_joint_is_refused(
    pose: dict[str, float], why: str
) -> None:
    """`inf` and `nan` are floats as far as pydantic is concerned, so they reach the field
    itself. An arm told to park at infinity would push until it stalled, and one told to park
    at `nan` compares false against every reading and so is never at its pose."""
    with pytest.raises(ValidationError, match=why):
        _entry("arm", "lerobot:real", rest_pose=pose)


def test_a_rest_pose_angle_that_is_not_a_number_names_the_joint_it_sits_on() -> None:
    """Here the joint is in the locator rather than in the sentence: pydantic refuses the
    value before the field validator ever runs, so the wording is its own. What matters is
    that a person editing robots.json is told which joint to look at."""
    for bad in ("sideways", None, [1]):
        with pytest.raises(
            ValidationError, match=r"rest_pose\.elbow_flex\n\s+Input should be a valid number"
        ):
            _entry("arm", "lerobot:real", rest_pose={"elbow_flex": bad})


def test_adapter_kwargs_carries_the_rest_pose_and_an_unregistered_spec_has_none(
    tmp_path: Path,
) -> None:
    reg = Registry(tmp_path)
    reg.add_robot(_entry("arm", "lerobot:real", rest_pose=_FOLDED))
    kwargs = resolve_robot_ref("arm", reg).adapter_kwargs()
    assert kwargs["rest_pose"] == _FOLDED
    kwargs["rest_pose"]["elbow_flex"] = 0.0
    assert reg.robot("arm").rest_pose == _FOLDED, "the kwargs hold a copy, not the stored dict"
    bare = resolve_robot_ref("lerobot:real", reg)
    assert not bare.registered
    assert bare.adapter_kwargs()["rest_pose"] is None, "a pose is something you get by naming it"


# ── the board a robot uses (--host) ─────────────────────────────────────────────────────


def test_a_host_and_its_token_survive_the_file_and_json_never_prints_the_token(
    tmp_path: Path,
) -> None:
    """The host is kept as typed, so `show` quotes back what was written rather than a
    `host:port` nobody typed. The token is kept apart from the body's own bridge `token`,
    because the two belong to different daemons, and `--json` says only whether it is set."""
    reg = Registry(tmp_path)
    reg.add_robot(_entry(host=" jetson.local ", host_token="hostd-s3cret\n", token="body"))
    again = Registry(tmp_path).robot("duck-a")
    assert again.host == "jetson.local"
    assert again.host_token == "hostd-s3cret", "stripped: a token file ends in a newline"
    assert again.token == "body"
    public = again.public()
    assert public["host"] == "jetson.local"
    assert public["host_token_set"] is True
    assert "hostd-s3cret" not in json.dumps(public)
    older = Registry(tmp_path)
    older.add_robot(_entry("plain"))
    assert older.robot("plain").public()["host"] is None
    assert older.robot("plain").public()["host_token_set"] is False


@pytest.mark.parametrize(
    ("host", "why"),
    [
        ("http://jetson.local", "not a URL"),
        ("user:pw@jetson.local", "never carries a token"),
        ("jetson.local:0", "whole number from 1 to 65535"),
    ],
)
def test_a_host_is_refused_on_the_way_in_in_parse_hosts_words_and_nothing_is_written(
    tmp_path: Path, host: str, why: str
) -> None:
    """The door, both of them. The words are `parse_host`'s own, with no file and no robot in
    front, because nothing in the file is wrong: the value was only just typed."""
    reg = Registry(tmp_path)
    with pytest.raises(RegistryError, match=f"^(?!robots.json).*{why}"):
        reg.add_robot(_entry(host=host))
    assert reg.get_robot("duck-a") is None
    reg.add_robot(_entry(host="jetson.local"))
    with pytest.raises(RegistryError, match=f"^(?!robots.json).*{why}"):
        reg.update_robot("duck-a", {"host": host})
    assert reg.robot("duck-a").host == "jetson.local"


def _hand_edit(tmp_path: Path, **jet: Any) -> None:
    """robots.json as somebody's editor left it: one good robot, and `jet` with whatever the
    test puts in it, written past every check the registry makes on the way in."""
    (tmp_path / "robots.json").write_text(
        json.dumps(
            {
                "version": 1,
                "robots": {
                    "duck": {"spec": "microduck:mock"},
                    "jet": {"spec": "microduck:mock", **jet},
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "jet",
    [{"host": "jetson.local:99999"}, {"host": "a b"}, {"host": "j", "host_token": "hunter2\x01"}],
    ids=["port", "space", "token"],
)
def test_a_hand_edited_bad_host_or_token_does_not_take_the_file_down(
    tmp_path: Path, jet: dict[str, str]
) -> None:
    """Lenient on the shelf, the bargain `llm` strikes. Every registry command reads the whole
    file, so a host refused on read would stop `list`, `remove`, a run of another robot and the
    very `edit --clear host` that would mend it. The run of this robot is what refuses, with
    the robot's name in front and never the token."""
    _hand_edit(tmp_path, **jet)
    reg = Registry(tmp_path)
    assert sorted(reg.robots()) == ["duck", "jet"]
    reg.update_robot("jet", {"note": "the one on the bench"})
    stored = reg.robot("jet")
    with pytest.raises(ValueError, match=r"^robot jet \(robots.json\): ") as refused:
        resolve_host(None, stored.host, stored_token=stored.host_token, robot="jet")
    assert "hunter2" not in str(refused.value)
    reg.update_robot("jet", {"host": None, "host_token": None})
    assert reg.robot("jet").host is None
    reg.remove_robot("jet")
    assert sorted(reg.robots()) == ["duck"]


def test_a_blank_host_token_is_no_token_and_one_a_header_cannot_carry_is_refused_at_the_door(
    tmp_path: Path,
) -> None:
    """Blank is the daemon's own rule for no token. A character an HTTP header cannot carry is
    refused on the way in, by `add_robot` and `update_robot` both, without quoting the token,
    rather than on the first run that sends it."""
    assert _entry(host_token="   ").host_token is None
    reg = Registry(tmp_path)
    with pytest.raises(RegistryError, match="an HTTP header cannot carry") as refused:
        reg.add_robot(_entry(host="jetson.local", host_token="hunter2é"))
    assert "hunter2" not in str(refused.value)
    assert reg.get_robot("duck-a") is None
    reg.add_robot(_entry(host="jetson.local", host_token="stored"))
    with pytest.raises(RegistryError, match="an HTTP header cannot carry") as refused:
        reg.update_robot("duck-a", {"host_token": "hunter2é"})
    assert "hunter2" not in str(refused.value)
    assert reg.robot("duck-a").host_token == "stored"


def test_a_host_token_is_stored_only_beside_the_board_it_is_for(tmp_path: Path) -> None:
    """`resolve_host` sends a robot's token only when the robot stores a board, so a token
    stored with none would never be sent, and refusing it now beats a 401 later. Only an edit that
    touches the pair is asked: a token some hand left alone must not stop a note."""
    reg = Registry(tmp_path)
    with pytest.raises(RegistryError, match="--host-token needs --host"):
        reg.add_robot(_entry(host_token="for-a-board"))
    assert reg.get_robot("duck-a") is None
    reg.add_robot(_entry(host="jetson.local", host_token="stored"))
    with pytest.raises(RegistryError, match="duck-a would have a host token and no host"):
        reg.update_robot("duck-a", {"host": None})
    assert reg.robot("duck-a").host == "jetson.local", "nothing was written"
    reg.add_robot(_entry("plain"))
    with pytest.raises(RegistryError, match="plain would have a host token and no host"):
        reg.update_robot("plain", {"host_token": "for-a-board"})
    _hand_edit(tmp_path, host_token="orphan")
    assert Registry(tmp_path).update_robot("jet", {"note": "fine"}).host_token == "orphan"


#: The fields 0.13.0's `RobotEntry` knows. It reads robots.json with `extra="forbid"`, so a key
#: outside this set refuses the whole file for it, even a key that is null.
_RELEASED_0_13 = {
    "spec",
    "address",
    "token",
    "camera_url",
    "rest_pose",
    "llm",
    "note",
    "added",
    "updated",
}


def test_a_robot_that_names_no_board_is_written_the_way_0_13_can_still_read(
    tmp_path: Path,
) -> None:
    """Two installs on one machine share one ~/.quackd, and every write saves every robot. A
    registry that never named a board must not become a file 0.13 refuses because this version
    wrote `"host": null` into each entry, by an add, an edit of a note, or a board forgotten."""
    reg = Registry(tmp_path)
    reg.add_robot(_entry("plain"))
    reg.add_robot(_entry("jet", host="jetson.local", host_token="stored"))
    reg.update_robot("plain", {"note": "the one by the door"})

    def saved() -> dict[str, dict[str, Any]]:
        loaded: dict[str, dict[str, Any]] = json.loads(
            (tmp_path / "robots.json").read_text(encoding="utf-8")
        )["robots"]
        return loaded

    assert set(saved()["plain"]) <= _RELEASED_0_13, set(saved()["plain"]) - _RELEASED_0_13
    assert saved()["jet"]["host"] == "jetson.local" and saved()["jet"]["host_token"] == "stored"
    reg.update_robot("jet", {"host": None, "host_token": None})
    assert set(saved()["jet"]) <= _RELEASED_0_13, set(saved()["jet"]) - _RELEASED_0_13
    assert reg.robot("jet").host is None, "and it still reads back here"


def test_host_kwargs_takes_the_flag_field_by_field_and_a_bare_spec_only_the_flags(
    tmp_path: Path,
) -> None:
    """`adapter_kwargs`'s rule: `--host 127.0.0.1` through a tunnel reaches the board that was
    registered, so the stored token still goes with it. The environment is the rung below
    both, and `resolve_host` reads it; nothing here does."""
    reg = Registry(tmp_path)
    reg.add_robot(_entry(host="jetson.local", host_token="stored"))
    resolved = resolve_robot_ref("duck-a", reg)
    assert resolved.host_kwargs() == {"host": "jetson.local", "host_token": "stored"}
    tunnel = resolved.host_kwargs(host="127.0.0.1")
    assert tunnel == {"host": "127.0.0.1", "host_token": "stored"}, "one flag, one field"
    assert resolved.host_kwargs(host_token="typed")["host_token"] == "typed"
    bare = resolve_robot_ref("microduck:mock", reg)
    assert bare.host_kwargs() == {"host": None, "host_token": None}
    assert bare.host_kwargs(host="jetson.local")["host"] == "jetson.local"


def test_update_sets_and_clears_the_host_and_refuses_a_bad_one_without_writing_it(
    tmp_path: Path,
) -> None:
    reg = Registry(tmp_path)
    reg.add_robot(_entry(host="jetson.local", host_token="stored"))
    with pytest.raises(RegistryError, match="not a URL"):
        reg.update_robot("duck-a", {"host": "http://other"})
    assert reg.robot("duck-a").host == "jetson.local"
    cleared = reg.update_robot("duck-a", {"host": None, "host_token": None})
    assert cleared.host is None and cleared.host_token is None


# ── cameras ─────────────────────────────────────────────────────────────────────────────

_TOP = "http://arm:9872/top.jpg"
_SIDE = "http://arm:9873/side.jpg"


def test_one_camera_is_stored_as_a_string_and_several_as_a_list(tmp_path: Path) -> None:
    """The string form is not a leftover: a robot with one camera has to write a file an
    older quackd can still read, so the list shape appears only when there is a second."""
    reg = Registry(tmp_path)
    reg.add_robot(_entry("arm", "lerobot:real", camera_url=_TOP))
    one = Registry(tmp_path).robot("arm")
    assert one.camera_url == _TOP
    assert one.camera_urls == (_TOP,), "everything reads camera_urls, which is always a tuple"
    stored = json.loads((tmp_path / "robots.json").read_text(encoding="utf-8"))
    assert stored["robots"]["arm"]["camera_url"] == _TOP, "an older quackd reads this file too"

    reg.update_robot("arm", {"camera_url": [_TOP, _SIDE]})
    both = Registry(tmp_path).robot("arm")
    assert both.camera_url == [_TOP, _SIDE]
    assert both.camera_urls == (_TOP, _SIDE), "the order given is kept: the first is the primary"
    stored = json.loads((tmp_path / "robots.json").read_text(encoding="utf-8"))
    assert stored["robots"]["arm"]["camera_url"] == [_TOP, _SIDE]


def test_a_robots_json_written_before_the_second_camera_still_loads(tmp_path: Path) -> None:
    """The back-compat guarantee for anyone upgrading: 0.9 wrote `camera_url` as a plain
    string, and that file has to load without an edit and mean one camera."""
    (tmp_path / "robots.json").write_text(
        json.dumps(
            {
                "version": 1,
                "robots": {"duck-a": {"spec": "microduck:mock", "camera_url": "http://old:9872"}},
            }
        ),
        encoding="utf-8",
    )
    entry = Registry(tmp_path).robot("duck-a")
    assert entry.camera_url == "http://old:9872"
    assert entry.camera_urls == ("http://old:9872",)
    assert entry.adapter_kwargs()["camera_url"] == ("http://old:9872",)


def test_a_blank_or_repeated_camera_url_is_refused_in_its_own_words() -> None:
    with pytest.raises(ValidationError, match="camera_url: an empty url is not a camera"):
        _entry("arm", "lerobot:real", camera_url="")
    with pytest.raises(ValidationError, match="camera_url: an empty url is not a camera"):
        _entry("arm", "lerobot:real", camera_url=[_TOP, "   "])
    with pytest.raises(ValidationError, match=f"camera_url: '{_TOP}' is listed twice"):
        _entry("arm", "lerobot:real", camera_url=[_TOP, _TOP])


def test_a_second_camera_is_refused_on_a_body_that_reads_one(tmp_path: Path) -> None:
    """A hand-edited file is the only way a duck gets two urls, and the rule is that a file
    saying something untrue names itself rather than having the extra camera quietly
    dropped: the duck would open the first one and nobody would learn why."""
    with pytest.raises(
        ValidationError,
        match=(
            "microduck:mock takes one camera url; only lerobot:real and lerobot:mujoco take several"
        ),
    ):
        _entry("duck-a", "microduck:mock", camera_url=["http://duck:1", "http://duck:2"])
    (tmp_path / "robots.json").write_text(
        json.dumps(
            {
                "version": 1,
                "robots": {
                    "duck-a": {
                        "spec": "microduck:mock",
                        "camera_url": ["http://duck:1", "http://duck:2"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match=r"duck-a: .*takes one camera url"):
        Registry(tmp_path).robots()
    arm = _entry("arm", "lerobot:real", camera_url=[_TOP, _SIDE])
    assert arm.camera_urls == (_TOP, _SIDE), "the one body that reads several takes them"


def test_adapter_kwargs_carries_every_camera_and_a_flag_replaces_the_whole_set(
    tmp_path: Path,
) -> None:
    """`--camera-url` on the line says where the cameras are today, so it replaces the stored
    set rather than adding to it. An arm whose side camera is unplugged is run by naming the
    one that is plugged in, not by editing the registry and putting it back afterwards."""
    reg = Registry(tmp_path)
    reg.add_robot(_entry("arm", "lerobot:real", camera_url=[_TOP, _SIDE]))
    resolved = resolve_robot_ref("arm", reg)
    assert resolved.adapter_kwargs()["camera_url"] == (_TOP, _SIDE)
    today = resolved.adapter_kwargs(camera_url=["http://laptop:8080/usb0"])
    assert today["camera_url"] == ("http://laptop:8080/usb0",), "a flag replaces, never appends"
    assert reg.robot("arm").camera_urls == (_TOP, _SIDE), "and it changes nothing on the file"
    assert resolve_robot_ref("lerobot:real", reg).adapter_kwargs()["camera_url"] == ()


# ── flocks ──────────────────────────────────────────────────────────────────────────────


def _pair(tmp_path: Path) -> Registry:
    reg = Registry(tmp_path)
    reg.add_robot(_entry("duck-a"))
    reg.add_robot(_entry("duck-b"))
    reg.add_robot(_entry("arm", "lerobot:mock"))
    return reg


def test_a_flock_keeps_the_order_you_listed_its_robots_in(tmp_path: Path) -> None:
    reg = _pair(tmp_path)
    reg.add_flock(StoredFlock(name="kitchen", members=["arm", "duck-a"]))
    assert list(Registry(tmp_path).roster("kitchen")) == ["arm", "duck-a"]


def test_a_flock_may_only_name_robots_that_are_registered(tmp_path: Path) -> None:
    reg = _pair(tmp_path)
    with pytest.raises(UnknownRobot, match="ghost"):
        reg.add_flock(StoredFlock(name="kitchen", members=["duck-a", "ghost"]))


def test_a_flock_may_not_list_one_robot_twice_or_hold_nine(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="listed twice"):
        StoredFlock(name="k", members=["duck-a", "duck-a"])
    with pytest.raises(ValidationError, match="at most 8"):
        StoredFlock(name="k", members=[f"duck-{i}" for i in range(9)])


def test_two_flocks_may_not_share_a_name(tmp_path: Path) -> None:
    reg = _pair(tmp_path)
    reg.add_flock(StoredFlock(name="kitchen", members=["duck-a"]))
    with pytest.raises(RegistryError, match="already exists"):
        reg.add_flock(StoredFlock(name="kitchen", members=["duck-b"]))


def test_an_unknown_flock_is_one_line(tmp_path: Path) -> None:
    with pytest.raises(UnknownFlock, match="no flock called 'nope'"):
        Registry(tmp_path).flock("nope")


def test_edit_adds_removes_describes_and_renames(tmp_path: Path) -> None:
    reg = _pair(tmp_path)
    reg.add_flock(StoredFlock(name="kitchen", members=["duck-a"]))
    reg.update_flock("kitchen", add=["arm"], description="the two by the sink")
    assert reg.flock("kitchen").members == ["duck-a", "arm"]
    reg.update_flock("kitchen", remove=["duck-a"])
    assert reg.flock("kitchen").members == ["arm"]
    reg.update_flock("kitchen", rename="sink")
    assert reg.get_flock("kitchen") is None
    assert reg.flock("sink").members == ["arm"]
    assert reg.update_flock("sink", description="").description is None


def test_edit_refuses_a_member_it_already_has_or_never_had(tmp_path: Path) -> None:
    reg = _pair(tmp_path)
    reg.add_flock(StoredFlock(name="kitchen", members=["duck-a"]))
    with pytest.raises(RegistryError, match="already in kitchen"):
        reg.update_flock("kitchen", add=["duck-a"])
    with pytest.raises(RegistryError, match="not in kitchen"):
        reg.update_flock("kitchen", remove=["arm"])


def test_deleting_a_flock_leaves_its_robots_registered(tmp_path: Path) -> None:
    reg = _pair(tmp_path)
    reg.add_flock(StoredFlock(name="kitchen", members=["duck-a", "arm"]))
    reg.delete_flock("kitchen")
    assert reg.get_flock("kitchen") is None
    assert set(reg.robots()) == {"duck-a", "duck-b", "arm"}


# ── the one broken state ────────────────────────────────────────────────────────────────


def test_removing_a_robot_a_flock_names_is_refused_and_force_drops_it(tmp_path: Path) -> None:
    reg = _pair(tmp_path)
    reg.add_flock(StoredFlock(name="kitchen", members=["duck-a", "arm"]))
    reg.add_flock(StoredFlock(name="garage", members=["duck-a"]))
    with pytest.raises(RobotInUse, match="garage, kitchen"):
        reg.remove_robot("duck-a")
    assert reg.get_robot("duck-a") is not None, "a refused remove removes nothing"
    assert reg.remove_robot("duck-a", force=True) == ["garage", "kitchen"]
    assert reg.get_robot("duck-a") is None
    assert reg.flock("kitchen").members == ["arm"]
    assert reg.flock("garage").members == []


def test_a_dangling_member_is_reported_and_refuses_to_run(tmp_path: Path) -> None:
    """A hand-edited file is the only way in, and it must not turn into a smaller flock."""
    reg = _pair(tmp_path)
    reg.add_flock(StoredFlock(name="kitchen", members=["duck-a", "arm"]))
    (tmp_path / "robots.json").write_text(
        json.dumps({"version": 1, "robots": {"arm": {"spec": "lerobot:mock"}}}), encoding="utf-8"
    )
    flock = reg.flock("kitchen")
    assert reg.missing_members(flock) == ["duck-a"]
    assert flock.public(["duck-a"])["runnable"] is False
    with pytest.raises(FlockNotRunnable, match="no longer registered"):
        reg.roster("kitchen")
    reg.update_flock("kitchen", remove=["duck-a"])
    assert list(reg.roster("kitchen")) == ["arm"], "removing a dangling member is the repair"


def test_an_empty_flock_says_how_to_fill_it(tmp_path: Path) -> None:
    reg = _pair(tmp_path)
    reg.add_flock(StoredFlock(name="kitchen"))
    with pytest.raises(FlockNotRunnable, match="has no robots"):
        reg.roster("kitchen")


# ── resolving a reference ───────────────────────────────────────────────────────────────


def test_a_reference_is_the_registry_then_a_spec_then_one_line(tmp_path: Path) -> None:
    reg = _pair(tmp_path)
    assert resolve_robot_ref(None, reg).spec.key == "microduck:sim2d"
    assert resolve_robot_ref(None, reg, duck_default="lerobot:mock").spec.key == "lerobot:mock"
    registered = resolve_robot_ref("duck-a", reg)
    assert registered.registered and registered.spec.name == "duck-a"
    assert registered.label == "duck-a (microduck:mock)"
    bare = resolve_robot_ref("microduck", reg)
    assert not bare.registered and bare.spec.key == "microduck:sim2d"
    with pytest.raises(AdapterError, match="neither a registered robot"):
        resolve_robot_ref("ghost", reg)
    with pytest.raises(AdapterError, match="unknown adapter"):
        resolve_robot_ref("ghost:nope", reg)


async def test_a_probe_fails_a_robot_this_quackd_cannot_build_before_its_backend() -> None:
    """Ahead of the backends a probe declines, which are read off the stored spec's text:
    `acme:mujoco` is not a physics model to skip, it is a body this quackd does not have."""
    result = await probe_entry(_entry("kitchen", "acme:mujoco"))
    assert result.reachable is False and "unknown adapter 'acme'" in result.detail, result


def test_a_flag_beats_the_stored_endpoint(tmp_path: Path) -> None:
    reg = Registry(tmp_path)
    reg.add_robot(_entry(address="tcp://stored:1", token="stored", camera_url="http://stored"))
    resolved = resolve_robot_ref("duck-a", reg)
    assert resolved.adapter_kwargs()["address"] == "tcp://stored:1"
    through_a_tunnel = resolved.adapter_kwargs(address="tcp://localhost:9999")
    assert through_a_tunnel["address"] == "tcp://localhost:9999"
    assert through_a_tunnel["token"] == "stored", "one flag overrides one field"


# ── probing ─────────────────────────────────────────────────────────────────────────────


async def test_a_probe_reaches_a_mock_and_reports_a_dead_address() -> None:
    ok = await probe_entry(_entry("duck-a", "microduck:mock"))
    assert ok.reachable is True and "ok" in ok.detail
    dead = await probe_entry(
        _entry("far", "open_duck:bridge", address="tcp://127.0.0.1:1"), timeout_s=3.0
    )
    assert dead.reachable is False and dead.detail


async def test_a_probe_declines_the_backend_that_would_download_a_model() -> None:
    skipped = await probe_entry(_entry("phys", "microduck:mujoco"))
    assert skipped.reachable is None and "skipped" in skipped.detail


async def test_a_probe_times_out_rather_than_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    class Slow:
        async def connect(self) -> None:
            await asyncio.sleep(30)

        async def close(self) -> None:
            return None

    monkeypatch.setattr("quackd.adapters.factory.make_adapter", lambda *a, **k: Slow())
    result = await probe_entry(_entry(), timeout_s=0.05)
    assert result.reachable is False and "timed out" in result.detail


def test_probe_all_answers_for_every_robot_at_once() -> None:
    rows = probe_all([_entry("a", "microduck:mock"), _entry("b", "lerobot:mock")])
    assert set(rows) == {"a", "b"}
    assert all(r.reachable for r in rows.values())


def _hand_back(monkeypatch: pytest.MonkeyPatch, arm: Any, seen: dict[str, Any]) -> None:
    """Every probe builds this one in-memory arm, and `seen` keeps what it was built with.

    `probe_entry` imports `make_adapter` inside the call, so the factory's module attribute
    is the seam: there is no other way to hand a probe a body it did not make itself."""
    from quackd_lerobot import LeRobotAdapter

    def fake_make_adapter(spec: Any, **kwargs: Any) -> Any:
        seen.clear()
        seen["spec"] = spec.key
        seen.update(kwargs)
        return LeRobotAdapter(arm)

    monkeypatch.setattr("quackd.adapters.factory.make_adapter", fake_make_adapter)


async def test_a_probe_hands_the_rest_pose_over_and_drives_the_arm_nowhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe reads and lets go, so it must never move the arm: `quackd robot list --probe`
    is something you type while the arm is holding something, and a probe that parked it
    would put whatever it held on the floor.

    The pose still has to reach the adapter, because it is what decides whether torque may
    drop when the probe disconnects. An arm away from it keeps torque, and the row says so on
    the same line that says the arm answered: somebody has to hold it before the power goes."""
    from quackd_lerobot.mock import LeRobotMock

    seen: dict[str, Any] = {}
    arm = LeRobotMock(rest_pose=_FOLDED)
    _hand_back(monkeypatch, arm, seen)

    result = await probe_entry(_entry("arm", "lerobot:real", rest_pose=_FOLDED))

    assert seen["spec"] == "lerobot:real"
    assert seen["rest_pose"] == _FOLDED, "the probe builds the arm with the pose it registered"
    assert "rest" not in arm.sequence, f"the probe drove the arm: {arm.sequence}"
    assert arm.sequence == ["close"]
    assert arm.actions == [], "and sent it no goal of any kind"
    assert result.reachable is True
    assert result.detail.startswith("ok")
    assert result.detail.endswith("torque left on: not at its rest pose"), result.detail
    assert arm.torque is True, "torque is what the note is about"


async def test_a_probe_that_fails_still_says_the_arm_was_left_holding_itself_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The paths that matter most, and the ones the note used to miss. A probe that timed out
    or died is exactly when an arm is most likely to be sitting there energised, and the note
    is written by the close, which used to happen in a `finally` that had no way to reach the
    line the reader sees.

    And the direction has to survive: there are two notes, one saying the arm is being held
    and one saying quackd could not hold it and let go. Printing the first for the second
    tells somebody an arm is safe at the moment it is not."""
    from quackd_lerobot.mock import LeRobotMock

    arm = LeRobotMock(rest_pose=_FOLDED)

    def build(spec: Any, **kwargs: Any) -> Any:
        """The probe's own seam, with a health call that never answers."""
        from quackd_lerobot import LeRobotAdapter

        class Wedged(LeRobotAdapter):
            async def health(self) -> Any:
                raise TimeoutError("the arm stopped answering")

        return Wedged(arm)

    monkeypatch.setattr("quackd.adapters.factory.make_adapter", build)
    result = await probe_entry(_entry("arm", "lerobot:real", rest_pose=_FOLDED))

    assert result.reachable is False
    assert "torque left on" in result.detail, result.detail
    assert arm.torque is True, "the arm was let go on the failure path"

    # the other note, which says the opposite thing, must not print as the first one
    arm2 = LeRobotMock(rest_pose=_FOLDED)
    seen2: dict[str, Any] = {}
    _hand_back(monkeypatch, arm2, seen2)
    released = "quackd could not keep torque on, so the arm was released where it stood"

    async def close_and_say() -> None:
        arm2.close_note = released

    monkeypatch.setattr(arm2, "close", close_and_say)
    row = await probe_entry(_entry("arm", "lerobot:real", rest_pose=_FOLDED))
    assert "could NOT be kept" in row.detail, row.detail
    assert "torque left on: not at its rest pose" not in row.detail, (
        "the row said the arm was being held when it had been released"
    )


async def test_a_probe_on_an_arm_already_at_its_pose_says_nothing_about_torque(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same rule: the torque line is news, and an arm that is where it
    rests is let go of the way it always was, with nothing added to the row."""
    from quackd_lerobot.mock import REST, LeRobotMock

    seen: dict[str, Any] = {}
    arm = LeRobotMock(rest_pose=dict(REST))
    _hand_back(monkeypatch, arm, seen)

    result = await probe_entry(_entry("arm", "lerobot:real", rest_pose=dict(REST)))

    assert "rest" not in arm.sequence
    assert result.detail == "ok"
    assert arm.torque is False, "an arm at its rest pose may be let go of"
