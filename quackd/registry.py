"""The robots you have named, and the flocks you made of them.

Not the *verb* registry (`quackd/verbs/registry.py`), which is a robot's vocabulary. This is
the other half of the word: a name for a body plus how to reach it, so `--robot duck-a` means
the same thing in every command instead of five flags retyped on every line, one of them a
secret that then lives in shell history.

Two files under one directory, `robots.json` and `flocks.json`. A flock is a list of robot
names, which is why they share a directory and a version: a flock that names a robot nobody
registered is the one broken state either file can be in, and it is reported rather than
repaired.

Reads are strict, unlike `RobotMemory`'s. Memory is notes a model wrote and a bad line there
is worth skipping; this is configuration a person wrote, and a silently-dropped field would
send a robot to the wrong address. A typo names its own field and stops the command.

Writes are the same trick memory uses: a temporary file renamed over the old one, so a reader
never sees half a file. Also the same accepted cost: nothing is serialised, so two commands
writing at the same instant are a read-modify-write race and the later rename wins. That is
the price of a file you can open in an editor, and it is the right price for something a
person edits by hand once a week (ADR-0034).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from quackd.adapters.base import MULTI_CAMERA_SPECS, AdapterError, camera_urls, who_takes_several
from quackd.adapters.factory import (
    ADAPTER_NAMES,
    BACKENDS,
    RobotSpec,
    adapter_names,
    default_spec,
    needs_rest_pose,
    parse_robot_spec,
)
from quackd.host import clean_token, parse_host
from quackd.memory import robot_slug

DEFAULT_DIR = "~/.quackd"
ENV_DIR = "QUACKD_REGISTRY_DIR"
ROBOTS_FILE = "robots.json"
FLOCKS_FILE = "flocks.json"
VERSION = 1

MAX_MEMBERS = 8
"""What a stored flock may hold. The run decides what it may *run*: an auction takes four."""

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
"""The same slug every member name and fleet name in quackd already is (`factory._NAME_RE`)."""


def registry_dir(override: str | Path | None = None) -> Path:
    """`--registry-dir`, else `$QUACKD_REGISTRY_DIR`, else `~/.quackd`."""
    raw = override or os.environ.get(ENV_DIR) or DEFAULT_DIR
    return Path(raw).expanduser()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reserved_slugs() -> set[str]:
    """Every memory file name an ad-hoc `adapter:backend` run would open.

    A robot registered as `microduck-sim2d` would key its memory to the same file a bare
    `--robot microduck:sim2d` run does, which is exactly the confusion registering it was
    meant to end. So the name is refused (ADR-0034 amends ADR-0025)."""
    return {robot_slug(f"{a}:{b}") for a, backends in BACKENDS.items() for b in backends}


class RegistryError(Exception):
    """Anything the registry refuses. The CLI prints it as one line, never a traceback."""


class UnknownRobot(RegistryError):
    pass


class UnknownFlock(RegistryError):
    pass


class RobotInUse(RegistryError):
    def __init__(self, name: str, flocks: Sequence[str]) -> None:
        joined = ", ".join(flocks)
        super().__init__(
            f"{name} is in {'flock' if len(flocks) == 1 else 'flocks'} {joined}: "
            f"quackd flock edit {flocks[0]} --remove {name}, or --force to drop it from "
            f"{'it' if len(flocks) == 1 else 'them'} too"
        )
        self.name = name
        self.flocks = list(flocks)


class FlockNotRunnable(RegistryError):
    pass


def _check_llm(spec: str) -> None:
    """Refuse a pilot spec on the way into the file, in the provider's own words.

    The counterpart to `RobotEntry._llm`, which reads leniently so that one bad line cannot
    take the whole registry down. Writing is the other half of that bargain: what goes in is
    checked against the catalogue in full, so the lenient read is a kindness to files that
    were already there rather than a hole somebody new can fall into."""
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.factory import parse_llm

    try:
        parse_llm(spec, source="llm")
    except ProviderError as e:
        raise RegistryError(str(e)) from e


def _check_spec(spec: str) -> None:
    """Refuse a body on the way into the file, in the adapter table's own words.

    The counterpart to `RobotEntry._spec`, which keeps a stored spec it cannot parse, for
    `_check_llm`'s reason. The refusal names no robot and no file, `_check_host`'s rule: it
    is about what was just typed."""
    try:
        parse_robot_spec(spec)
    except AdapterError as e:
        raise RegistryError(str(e)) from e


def _check_host(host: str | None, host_token: str | None) -> None:
    """Refuse a board or its token on the way into the file, in `quackd.host`'s own words.

    The counterpart to `RobotEntry._host` and `_host_token`, which read leniently for
    `_check_llm`'s reason. Only what is being written is checked: a stored host that stopped
    parsing must not stop an edit of the note beside it. The refusal carries no file and no
    robot in front, because nothing in the file is wrong yet: the words are about what was
    just typed, and `parse_host`'s and `clean_token`'s sentences already say which value."""
    try:
        if host is not None:
            parse_host(host)
        clean_token(host_token)
    except ValueError as e:
        raise RegistryError(str(e)) from e


def check_name(name: str, *, kind: str = "robot") -> str:
    """A name a person types on a command line, and never anything else it could be mistaken
    for: a number (`--flock 3` means three simulated ducks), an adapter (`--robot microduck`
    already means one), or a memory file some ad-hoc run already owns."""
    if not _NAME_RE.match(name):
        raise RegistryError(
            f"{name!r} is not a valid {kind} name: lowercase letters, digits and hyphens, "
            "starting with a letter or digit, 64 characters at most"
        )
    if name.isdigit():
        raise RegistryError(
            f"{name!r} cannot be a {kind} name: a number after --flock means that many "
            "simulated ducks"
        )
    if kind == "robot":
        if name in ADAPTER_NAMES:
            raise RegistryError(
                f"{name!r} is an adapter, and --robot {name} already means "
                f"{name}:{BACKENDS[name][0]}: pick another name"
            )
        if name in _reserved_slugs():
            raise RegistryError(
                f"{name!r} is the memory file name of an unregistered robot: pick another name"
            )
    return name


class RobotEntry(BaseModel):
    """One robot you have named: which body, where it is, and who pilots it."""

    # `hide_input_in_errors` because an entry holds two secrets, `token` and `host_token`, and
    # pydantic's own error text quotes the value it refused. Every refusal quackd prints is
    # built from the message alone (`Registry._one_line`, `cli._one_line`), but a traceback
    # or a caller printing the error whole would carry the token with it.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str
    spec: str
    """`<adapter>:<backend>`, normalised: `microduck` is stored as `microduck:sim2d`."""
    address: str | None = None
    token: str | None = None
    camera_url: str | list[str] | None = None
    """One camera as a string, or several as a list in the order given, the first being the
    primary. Read it through `camera_urls`, which is always a tuple. A file written before
    an arm could have two cameras holds a string and still loads."""
    rest_pose: dict[str, float] | None = None
    """Where this arm rests, read off the arm by `quackd robot rest-pose`. A run starts from
    it and returns to it before torque is released. None for every body quackd does not park."""
    llm: str | None = None
    """The pilot a run uses for this robot when `--llm` is absent, in the same shape `--llm`
    takes: a vendor on its own (`anthropic`), or a vendor and a model id
    (`anthropic:claude-opus-4-5`). `--llm` on the line beats this, and this beats
    `QUACKD_LLM`. Stored canonically, so a bare listed model id is written back with its
    vendor in front: `--llm claude-opus-4-5` lands here as `anthropic:claude-opus-4-5`."""
    host: str | None = None
    """The board this robot uses and quackd never runs on (`--host`): the machine its local
    model server, host camera and host detector are reached on, as `jetson.local` or
    `jetson.local:9874`. `--host` on the line beats this, and this beats `QUACKD_HOST`."""
    host_token: str | None = None
    """The token that board's daemon was started with. Kept apart from `token`, which is the
    body's own bridge token: the two are different daemons, often on different machines."""
    note: str | None = None
    added: str = Field(default_factory=_now)
    updated: str = Field(default_factory=_now)

    @model_validator(mode="before")
    @classmethod
    def _fold_legacy_llm(cls, data: Any) -> Any:
        """`provider` and `model` were two keys until 0.11, and one key since.

        `robots.json` is user data that nobody re-saves on upgrade: the file a person wrote
        eighteen months ago is still the file on disk, so the old pair is folded into `llm`
        here. `mode="before"` is the only place that can work, because it runs ahead of field
        validation and ahead of `extra="forbid"` refusing a key this model no longer has.
        `{"provider": "anthropic", "model": "claude-opus-4-5"}` reads as
        `llm="anthropic:claude-opus-4-5"`, and the next write of that robot -- a
        `quackd robot edit`, or anything else that saves the file -- stores the new shape.

        A file carrying both the new `llm` key and an old one keeps its old key, so
        `extra="forbid"` names it and the command stops. That file says two different things
        about which pilot this robot uses, and guessing which half the person meant is worse
        than refusing and letting them delete the line they did not want."""
        if not isinstance(data, dict) or data.get("llm") is not None:
            return data
        if "provider" not in data and "model" not in data:
            return data
        folded = dict(data)
        provider = folded.pop("provider", None)
        model = folded.pop("model", None)
        # A `model` with no `provider` beside it named no pilot and must not start naming one.
        # Under the old pair the vendor came from the flag or from the default, and the default
        # was `fake`, which ignores a model id entirely -- so such an entry did nothing unless
        # a `--provider` was also typed. Read as a spec it would do two things instead, both
        # bad: `{"model": "mistral"}` would become the paid Mistral API for a run that used to
        # cost nothing, and `{"model": "gpt-4-turbo"}` would be a vendor quackd cannot resolve.
        folded["llm"] = f"{provider}:{model}" if provider and model else (provider or None)
        return folded

    @field_validator("spec")
    @classmethod
    def _spec(cls, value: str) -> str:
        """Lenient on the shelf, the bargain `_llm` strikes, and for its reason.

        A spec this quackd cannot parse used to be refused here, and so on every read: one
        robot on a backend its adapter had dropped, as `microduck:websocket` was in 0.19.0, or
        on an adapter no longer installed here, made `quackd robot list`, `show`, a run of any
        other robot, and the `edit` and `remove` that would mend it all refuse the whole file
        until somebody deleted the entry by hand. So a spec that parses is stored canonically
        (`microduck` as `microduck:sim2d`) and one that does not is kept as written, stripped.
        `add_robot` and `update_robot` check the one being written (`_check_spec`), and
        `robot_spec` refuses a bad stored one by the robot's name on the command that uses it."""
        try:
            return parse_robot_spec(value).key
        except AdapterError:
            return str(value).strip()

    @field_validator("camera_url")
    @classmethod
    def _camera_url(cls, value: str | list[str] | None) -> str | list[str] | None:
        if value is None:
            return None
        given = [value] if isinstance(value, str) else list(value)
        urls: list[str] = []
        for raw in given:
            url = str(raw).strip()
            if not url:
                raise ValueError("camera_url: an empty url is not a camera")
            if url in urls:
                raise ValueError(f"camera_url: {url!r} is listed twice")
            urls.append(url)
        if not urls:
            return None
        # one camera is stored as a string, so a file written by a version that had no second
        # camera reads back byte for byte the way it was written
        return urls[0] if len(urls) == 1 else urls

    @field_validator("rest_pose")
    @classmethod
    def _rest_pose(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("rest_pose must name at least one joint")
        pose: dict[str, float] = {}
        for joint, raw in value.items():
            try:
                degrees = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"rest_pose.{joint} must be a finite number of degrees") from None
            if degrees != degrees or degrees in (float("inf"), float("-inf")):
                raise ValueError(f"rest_pose.{joint} must be a finite number of degrees")
            pose[str(joint)] = degrees
        return pose

    @model_validator(mode="after")
    def _one_camera_bodies(self) -> RobotEntry:
        """A second camera on a body that reads one is a file saying something untrue, and
        the rule here is that such a file names itself rather than being quietly trimmed."""
        several = len(self.camera_urls) > 1 and self.spec not in MULTI_CAMERA_SPECS
        # judged only against a body this quackd can build: one it cannot is refused by name
        # where it is used (`robot_spec`), and refusing it here would refuse the whole file
        if several and self.spec_problem is None:
            raise ValueError(f"{self.spec} takes one camera url; {who_takes_several()}")
        return self

    @property
    def camera_urls(self) -> tuple[str, ...]:
        """Every camera this robot was registered with, primary first."""
        return camera_urls(self.camera_url)

    @field_validator("host")
    @classmethod
    def _host(cls, value: str | None) -> str | None:
        """Lenient on the shelf, the bargain `_llm` strikes, and for its reason.

        Refusing a host here would refuse it on every read, and every registry command reads
        the whole file: `quackd robot list`, `show`, `remove`, a run of some other robot, and
        the `quackd robot edit NAME --clear host` that would mend it. A hand edit is how a bad
        host gets here, and `parse_host` is young enough to grow stricter, which would put a
        host that was fine yesterday on the wrong side of it. So the host is kept as written,
        stripped, with blank read as none, and `quackd robot show` quotes back what was
        written. `_check_host` checks the one being written, and `quackd.host.resolve_host`
        refuses a bad stored one on the run that uses it, with the robot's name in front."""
        text = (value or "").strip()
        return text or None

    @field_validator("host_token")
    @classmethod
    def _host_token(cls, value: str | None) -> str | None:
        """Stripped, and blank read as no token, the daemon's own rule. A character an HTTP
        header cannot carry is left for `_check_host` at the door and `resolve_host` at use,
        for the host's reason, and neither quotes the token while refusing it."""
        text = (value or "").strip()
        return text or None

    @field_validator("llm")
    @classmethod
    def _llm(cls, value: str | None) -> str | None:
        """Strict at the door, lenient on the shelf, and the shelf is this.

        `quackd robot add` and `quackd robot edit` check a spec against the catalogue in full,
        because the door is where the typo is actually made and the person who made it is
        looking at the answer. Reading is the opposite: whatever cannot be resolved is kept
        exactly as it was written, and the run that names that robot is what stops.

        The reason is that `update_robot` reads every entry before it writes one, so anything
        refused here is refused by `quackd robot list`, `show`, `remove` and by the very
        `quackd robot edit` that would fix it. One bad line would take the whole file down and
        leave no command able to mend it. That is a worse failure than a robot that refuses on
        the day it is run, and it is reachable by ordinary means: a catalogue that retires a
        model id, a vendor quackd drops, or a hand-edited file.

        So this canonicalises what it can -- a bare listed id gains its vendor, a vendor is
        folded to lower case -- and leaves the rest alone."""
        if value is None or not str(value).strip():
            return None
        # imported here rather than at module scope, deliberately: the provider catalogue is a
        # large import and `quackd robot list` has no reason to pay for it
        from quackd.agent.providers.base import ProviderError
        from quackd.agent.providers.factory import parse_llm

        try:
            vendor, model = parse_llm(str(value), source="llm", check_model=False)
        except ProviderError:
            return str(value).strip()
        return f"{vendor}:{model}" if model else vendor

    @property
    def adapter(self) -> str:
        return self.spec.partition(":")[0]

    @property
    def backend(self) -> str:
        # partitioned rather than split, so a stored spec with no colon (`_spec` keeps one
        # it cannot parse as written) reads as no backend instead of raising
        return self.spec.partition(":")[2]

    @property
    def key(self) -> str:
        return self.spec

    @property
    def spec_problem(self) -> str | None:
        """Why this quackd cannot build the body this robot is registered as, in the adapter
        table's own words, or None when it can. Asked of the file as it is read now, so a
        robot whose adapter is installed again parses again without anything being saved."""
        try:
            parse_robot_spec(self.spec)
        except AdapterError as e:
            return str(e)
        return None

    @property
    def robot_spec(self) -> RobotSpec:
        """`RobotSpec` carries the name, so the manifest id is the name you registered, which
        is what `--robots name=spec` has always done for a fleet.

        A stored spec this quackd cannot parse is refused here, by the robot's name and with
        the fix, because every command that builds a registered robot builds it from this:
        a run, a flock, `doctor`, `serve-mcp`, `rest-pose` and `release`."""
        if (why := self.spec_problem) is not None:
            raise AdapterError(unusable_spec(self.name, self.spec, why))
        return RobotSpec(self.adapter, self.backend, self.name)

    @property
    def memory_key(self) -> str:
        return self.name

    def adapter_kwargs(
        self,
        *,
        address: str | None = None,
        camera_url: str | Sequence[str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """The `make_adapter` keywords for this robot. A flag on the command line wins: you
        are reaching the same robot through a tunnel today, not renaming it.

        `--camera-url` on the line replaces the whole stored set rather than adding to it, for
        the same reason: naming a camera today says where the cameras are today."""
        return {
            "address": address or self.address,
            "camera_url": camera_urls(camera_url) or self.camera_urls,
            "token": token or self.token,
            "rest_pose": dict(self.rest_pose) if self.rest_pose else None,
        }

    def host_kwargs(
        self, *, host: str | None = None, host_token: str | None = None
    ) -> dict[str, str | None]:
        """The board this robot uses, by `adapter_kwargs`'s rule: a flag on the line wins,
        field by field. `--host 127.0.0.1` through a tunnel is the same board reached another
        way, so the stored token still goes with it. The environment is the rung below both,
        and `quackd.host.resolve_host` reads it."""
        return {"host": host or self.host, "host_token": host_token or self.host_token}

    def public(self) -> dict[str, Any]:
        """The `--json` shape. Neither token is ever printed, only whether there is one."""
        return {
            "name": self.name,
            "spec": self.spec,
            "adapter": self.adapter,
            "backend": self.backend,
            "address": self.address,
            "camera_url": self.camera_url,
            "rest_pose": dict(self.rest_pose) if self.rest_pose else None,
            "token_set": self.token is not None,
            "host": self.host,
            "host_token_set": self.host_token is not None,
            "llm": self.llm,
            "note": self.note,
            "spec_problem": self.spec_problem,
            "added": self.added,
            "updated": self.updated,
        }


def unusable_spec(name: str, spec: str, why: str) -> str:
    """The refusal for a registered robot this quackd cannot build: which robot, what it is
    registered as, the adapter table's reason, and the way out. An adapter this quackd has
    lost a backend of is moved to one it has; one it does not have at all may only need its
    package installed again."""
    adapter = spec.partition(":")[0].strip().lower()
    known = adapter in adapter_names()
    moves = f"quackd robot edit {name} --spec {adapter if known else '<adapter>'}:<backend>"
    install = "" if known else f"install the package that provides {adapter or 'it'}, "
    return (
        f"{name} is registered as {spec}, which this quackd cannot build ({why}): "
        f"{install}{moves}, or quackd robot remove {name}"
    )


def no_rest_pose_refusal(
    resolved: Resolved,
    *,
    what: str,
    address: str | None = None,
    camera_url: str | Sequence[str] | None = None,
    fleet: bool = False,
) -> tuple[str, str] | None:
    """The refusal for a real arm with no rest pose recorded, and the way out, or None for any
    robot that may be connected without one (`needs_rest_pose`).

    Such an arm has nothing to be folded to at the end of a run, a dry run, an MCP session or a
    flock member's run, and its close keeps its torque rather than let it fall where it stands,
    so each would end with the arm holding itself up wherever it stopped (ADR-0058). It is
    refused before anything is built or connected. `what` is the thing refused, as the sentence
    says it: `a run, a dry one included`, `an MCP session`. A registered arm is sent to record
    its pose; a bare spec names no arm to record one for, so it is sent to register one first,
    with the address and the cameras it was given. A spec in a fleet, from `--robots` or a task
    file's `robots:`, is sent on to a stored flock as well (`fleet`), since neither spelling
    reads the registry, and registering the arm alone would change nothing they run."""
    if not needs_rest_pose(resolved.spec) or resolved.adapter_kwargs()["rest_pose"] is not None:
        return None
    falls = (
        f"at the end of {what}, there is nothing to fold it to, and it would be left holding "
        "itself up wherever it stopped"
    )
    if resolved.entry is not None:
        name = resolved.entry.name
        return (
            f"{name} ({resolved.spec.key}) has no rest pose recorded, so {falls}",
            f"quackd robot rest-pose {name}",
        )
    key = resolved.spec.key
    flags = (f" --address {address}" if address else "") + "".join(
        f' --camera-url "{url}"' for url in camera_urls(camera_url)
    )
    hint = f"quackd robot add NAME {key}{flags}, then quackd robot rest-pose NAME"
    if fleet:
        hint += (
            ", then quackd flock create FLOCK --robot NAME, one --robot for each member, and run "
            "it with --flock FLOCK"
        )
    return (f"{key} names no registered arm, so it has no rest pose, and {falls}", hint)


class StoredFlock(BaseModel):
    """A named group of registered robots. Running one needs 2 to 8 of them; storing fewer is
    allowed, because that is what a flock looks like while you are still building it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    members: list[str] = Field(default_factory=list, max_length=MAX_MEMBERS)
    description: str | None = None
    created: str = Field(default_factory=_now)
    updated: str = Field(default_factory=_now)

    @field_validator("members")
    @classmethod
    def _members(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for name in value:
            if not _NAME_RE.match(name):
                raise ValueError(f"{name!r} is not a valid robot name")
            if name in seen:
                raise ValueError(f"{name!r} is listed twice")
            seen.add(name)
        return value

    def public(self, missing: Sequence[str] = ()) -> dict[str, Any]:
        return {
            "name": self.name,
            "members": list(self.members),
            "missing": list(missing),
            "runnable": not missing and 2 <= len(self.members) <= MAX_MEMBERS,
            "description": self.description,
            "created": self.created,
            "updated": self.updated,
        }


@dataclass(frozen=True)
class Resolved:
    """What `--robot <something>` turned out to mean: a registered robot, or a bare spec."""

    spec: RobotSpec
    entry: RobotEntry | None = None

    @property
    def registered(self) -> bool:
        return self.entry is not None

    @property
    def memory_key(self) -> str:
        return self.entry.memory_key if self.entry is not None else self.spec.key

    @property
    def label(self) -> str:
        """What a header and a status line call it: the name, and the body under it."""
        return f"{self.entry.name} ({self.spec.key})" if self.entry is not None else self.spec.key

    @property
    def llm(self) -> str | None:
        """The pilot this robot was registered with, `vendor` or `vendor:model`, or None for
        a bare spec: nothing was registered, so there is nothing to have recorded."""
        return self.entry.llm if self.entry is not None else None

    def adapter_kwargs(
        self,
        *,
        address: str | None = None,
        camera_url: str | Sequence[str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        if self.entry is not None:
            return self.entry.adapter_kwargs(address=address, camera_url=camera_url, token=token)
        # an unregistered robot has nothing recorded, so a rest pose is one more thing you
        # only get by naming the robot first
        return {
            "address": address,
            "camera_url": camera_urls(camera_url),
            "token": token,
            "rest_pose": None,
        }

    def host_kwargs(
        self, *, host: str | None = None, host_token: str | None = None
    ) -> dict[str, str | None]:
        """`RobotEntry.host_kwargs`, and for a bare spec only what the flags say, since
        nothing was registered to fall back on."""
        if self.entry is not None:
            return self.entry.host_kwargs(host=host, host_token=host_token)
        return {"host": host, "host_token": host_token}


#: Fields `--host` added, written to robots.json only while they hold something. quackd 0.12
#: to 0.14 read the file with `extra="forbid"`, so a key they have never heard of refuses the
#: whole file, even one that is null, and every write saves every robot. Leaving the empty ones
#: out keeps a registry that never named a board readable by them, which matters because two
#: installs on one machine share one ~/.quackd. A robot that does name a board is a file they
#: cannot read, and nothing short of dropping the board could change that.
_OMITTED_WHEN_EMPTY = ("host", "host_token")


def _stored(entry: RobotEntry) -> dict[str, Any]:
    """One robot as robots.json holds it: every field but the name, which is its key there."""
    data = entry.model_dump(exclude={"name"})
    for key in _OMITTED_WHEN_EMPTY:
        if data[key] is None:
            del data[key]
    return data


class Registry:
    """Both files, under one directory. Every method re-reads the file it needs, so a `run`
    and a `quackd robot edit` in another terminal always see each other's latest writes."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base = registry_dir(base_dir)
        self.robots_path = self.base / ROBOTS_FILE
        self.flocks_path = self.base / FLOCKS_FILE

    # ── storage ─────────────────────────────────────────────────────────────────────

    def _read(self, path: Path, key: str) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RegistryError(
                f"{path}: not valid JSON ({e.msg} at line {e.lineno}): fix it or move it aside"
            ) from e
        if not isinstance(loaded, dict):
            raise RegistryError(f"{path}: expected an object, found {type(loaded).__name__}")
        version = loaded.get("version", VERSION)
        if not isinstance(version, int) or version > VERSION:
            raise RegistryError(
                f"{path} is version {version}, and this quackd reads version {VERSION}: "
                "upgrade quackd"
            )
        section = loaded.get(key) or {}
        if not isinstance(section, dict):
            raise RegistryError(f"{path}: {key} must be an object of name -> entry")
        return section

    def _write(self, path: Path, key: str, section: Mapping[str, dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": VERSION, key: {k: section[k] for k in sorted(section)}}
        tmp = path.with_suffix(".json.tmp")
        if key == "robots":
            # robots.json holds robot tokens, so the mode goes on the TEMPORARY file,
            # before a token is written into it: the rename then carries that mode onto
            # robots.json. Setting it after the rename left the tokens world-readable for
            # the length of every write, in a file nothing cleans up if the write fails.
            # A no-op on Windows, which is why SECURITY.md calls this a file in a home
            # directory rather than a secret store.
            with contextlib.suppress(OSError):
                tmp.touch(mode=0o600)
                tmp.chmod(0o600)  # a tmp left by a failed write keeps its old mode
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:
            tmp.replace(path)
        except PermissionError as e:  # Windows: another program is holding the file open
            tmp.unlink(missing_ok=True)
            raise RegistryError(f"could not replace {path}: another program has it open") from e

    @staticmethod
    def _one_line(path: Path, name: str, e: ValidationError) -> RegistryError:
        why = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        return RegistryError(f"{path.name}: {name}: {why}")

    # ── robots ──────────────────────────────────────────────────────────────────────

    def robots(self) -> dict[str, RobotEntry]:
        """Every registered robot, by name, sorted."""
        out: dict[str, RobotEntry] = {}
        for name, raw in sorted(self._read(self.robots_path, "robots").items()):
            try:
                out[name] = RobotEntry.model_validate({**raw, "name": name})
            except ValidationError as e:
                raise self._one_line(self.robots_path, name, e) from e
        return out

    def get_robot(self, name: str) -> RobotEntry | None:
        return self.robots().get(name)

    def robot(self, name: str) -> RobotEntry:
        entry = self.get_robot(name)
        if entry is None:
            raise UnknownRobot(f"no robot called {name!r} is registered")
        return entry

    def _save_robots(self, entries: Mapping[str, RobotEntry]) -> None:
        self._write(
            self.robots_path,
            "robots",
            {n: _stored(e) for n, e in entries.items()},
        )

    def add_robot(self, entry: RobotEntry) -> RobotEntry:
        check_name(entry.name, kind="robot")
        _check_spec(entry.spec)  # the door is strict; `RobotEntry._spec` is the shelf
        if entry.llm is not None:
            _check_llm(entry.llm)  # a pilot chosen now is a pilot that can be checked now
        _check_host(entry.host, entry.host_token)
        if entry.host_token is not None and entry.host is None:
            # `resolve_host` sends a robot's token only when the robot stores a board, so a
            # token stored with none would never be sent anywhere, and saying so now beats a 401
            # on the day somebody expects it to have been
            raise RegistryError(
                "--host-token needs --host: the token is for one board's daemon, so name that "
                "board too"
            )
        entries = self.robots()
        if entry.name in entries:
            raise RegistryError(
                f"{entry.name!r} is already registered ({entries[entry.name].key}): "
                f"quackd robot edit {entry.name}, or remove it first"
            )
        entries[entry.name] = entry
        self._save_robots(entries)
        return entry

    def update_robot(self, name: str, changes: Mapping[str, Any]) -> RobotEntry:
        """Set or clear fields. A key mapped to None clears it; a key absent is left alone."""
        entries = self.robots()
        current = entries.get(name)
        if current is None:
            raise UnknownRobot(f"no robot called {name!r} is registered")
        # The pilot being set, and only the one being set. `RobotEntry` reads a spec it cannot
        # resolve rather than refusing it, because refusing on read would take down the whole
        # file including this command; but a spec handed in here is one somebody is choosing
        # now, and a choice made now can be checked now. Checking the whole entry instead
        # would put the bricking back: editing the note on a robot whose stored pilot names a
        # retired model would fail on the pilot.
        if (named := changes.get("llm")) is not None:
            _check_llm(str(named))
        # The body being set, and the board, by the same rule and for the same reason: a robot
        # whose stored spec no longer parses can still have its note edited, and its spec
        # moved to one that does.
        if (spec := changes.get("spec")) is not None:
            _check_spec(str(spec))
        _check_host(changes.get("host"), changes.get("host_token"))
        data = current.model_dump()
        data.update(changes)
        data["updated"] = _now()
        try:
            entry = RobotEntry.model_validate(data)
        except ValidationError as e:
            raise self._one_line(self.robots_path, name, e) from e
        touched = "host" in changes or "host_token" in changes
        if touched and entry.host_token is not None and entry.host is None:
            # `add_robot`'s pairing rule, asked only of an edit that touches the pair, so a
            # hand-edited token with no board beside it cannot stop an edit of the note
            raise RegistryError(
                f"{name} would have a host token and no host: the token is for one board's "
                "daemon, so give --host too, or --clear host-token"
            )
        entries[name] = entry
        self._save_robots(entries)
        return entry

    def remove_robot(self, name: str, *, force: bool = False) -> list[str]:
        """Forget a robot. Returns the flocks it was dropped from (empty unless `force`)."""
        entries = self.robots()
        if name not in entries:
            raise UnknownRobot(f"no robot called {name!r} is registered")
        holding = self.flocks_of(name)
        if holding and not force:
            raise RobotInUse(name, holding)
        del entries[name]
        self._save_robots(entries)
        if holding:
            flocks = self.flocks()
            for flock_name in holding:
                flock = flocks[flock_name]
                flocks[flock_name] = flock.model_copy(
                    update={
                        "members": [m for m in flock.members if m != name],
                        "updated": _now(),
                    }
                )
            self._save_flocks(flocks)
        return holding

    def flocks_of(self, name: str) -> list[str]:
        return [f.name for f in self.flocks().values() if name in f.members]

    # ── flocks ──────────────────────────────────────────────────────────────────────

    def flocks(self) -> dict[str, StoredFlock]:
        out: dict[str, StoredFlock] = {}
        for name, raw in sorted(self._read(self.flocks_path, "flocks").items()):
            try:
                out[name] = StoredFlock.model_validate({**raw, "name": name})
            except ValidationError as e:
                raise self._one_line(self.flocks_path, name, e) from e
        return out

    def get_flock(self, name: str) -> StoredFlock | None:
        return self.flocks().get(name)

    def flock(self, name: str) -> StoredFlock:
        found = self.get_flock(name)
        if found is None:
            raise UnknownFlock(f"no flock called {name!r}")
        return found

    def _save_flocks(self, flocks: Mapping[str, StoredFlock]) -> None:
        self._write(
            self.flocks_path,
            "flocks",
            {n: f.model_dump(exclude={"name"}) for n, f in flocks.items()},
        )

    def add_flock(self, flock: StoredFlock) -> StoredFlock:
        check_name(flock.name, kind="flock")
        flocks = self.flocks()
        if flock.name in flocks:
            raise RegistryError(f"a flock called {flock.name!r} already exists")
        known = self.robots()
        for member in flock.members:
            if member not in known:
                raise UnknownRobot(f"no robot called {member!r} is registered: quackd robot list")
        flocks[flock.name] = flock
        self._save_flocks(flocks)
        return flock

    def update_flock(
        self,
        name: str,
        *,
        add: Sequence[str] = (),
        remove: Sequence[str] = (),
        description: str | None = None,
        rename: str | None = None,
    ) -> StoredFlock:
        """`description=""` clears it, None leaves it. Removing a member that is no longer
        registered is allowed: that is how a dangling flock is repaired."""
        flocks = self.flocks()
        flock = flocks.get(name)
        if flock is None:
            raise UnknownFlock(f"no flock called {name!r}")
        known = self.robots()
        members = list(flock.members)
        for member in remove:
            if member not in members:
                raise RegistryError(f"{member!r} is not in {name}")
            members.remove(member)
        for member in add:
            if member not in known:
                raise UnknownRobot(f"no robot called {member!r} is registered: quackd robot list")
            if member in members:
                raise RegistryError(f"{member!r} is already in {name}")
            members.append(member)
        if len(members) > MAX_MEMBERS:
            raise RegistryError(
                f"a flock holds at most {MAX_MEMBERS} robots; that would be {len(members)}"
            )
        data = flock.model_dump()
        data["members"] = members
        if description is not None:
            data["description"] = description or None
        data["updated"] = _now()
        if rename is not None and rename != name:
            check_name(rename, kind="flock")
            if rename in flocks:
                raise RegistryError(f"a flock called {rename!r} already exists")
            data["name"] = rename
            del flocks[name]
            name = rename
        try:
            updated = StoredFlock.model_validate(data)
        except ValidationError as e:
            raise self._one_line(self.flocks_path, name, e) from e
        flocks[name] = updated
        self._save_flocks(flocks)
        return updated

    def delete_flock(self, name: str) -> StoredFlock:
        flocks = self.flocks()
        flock = flocks.pop(name, None)
        if flock is None:
            raise UnknownFlock(f"no flock called {name!r}")
        self._save_flocks(flocks)
        return flock

    def missing_members(self, flock: StoredFlock) -> list[str]:
        known = self.robots()
        return [m for m in flock.members if m not in known]

    def roster(self, name: str) -> dict[str, RobotEntry]:
        """A flock's robots, in the order it lists them, ready to run.

        Refuses rather than quietly running a smaller flock: a member you registered and then
        removed is a robot you meant to be there."""
        flock = self.flock(name)
        if not flock.members:
            raise FlockNotRunnable(
                f"flock {name!r} has no robots: quackd flock edit {name} --add NAME"
            )
        missing = self.missing_members(flock)
        if missing:
            joined = ", ".join(missing)
            raise FlockNotRunnable(
                f"flock {name!r} names {joined}, which "
                f"{'is' if len(missing) == 1 else 'are'} no longer registered: "
                f"quackd robot add {missing[0]} <adapter>:<backend>, or "
                f"quackd flock edit {name} --remove {missing[0]}"
            )
        known = self.robots()
        return {m: known[m] for m in flock.members}


def resolve_robot_ref(
    text: str | None,
    registry: Registry | None = None,
    *,
    duck_default: str | None = None,
) -> Resolved:
    """What `--robot <something>` means here: a registered name, or `<adapter>[:<backend>]`.

    A slug cannot contain a colon and a spec always reads as one when it has a backend, so the
    two vocabularies never collide; a bare `microduck` is an adapter because that is what it
    has always been, and `check_name` refuses registering a robot under an adapter's name."""
    reg = registry if registry is not None else Registry()
    if text is None:
        if duck_default:
            return Resolved(parse_robot_spec(duck_default))
        # no flag and no `robots:` line, so it is whatever is installed here, or a refusal
        # naming what to install. quackd stopped shipping a body to fall back to.
        return Resolved(default_spec())
    text = text.strip()
    if ":" not in text:
        entry = reg.get_robot(text)
        if entry is not None:
            return Resolved(entry.robot_spec, entry)
    try:
        return Resolved(parse_robot_spec(text))
    except AdapterError as e:
        if ":" in text:
            raise
        raise AdapterError(
            f"{text!r} is neither a registered robot (quackd robot list) nor "
            f"<adapter>[:<backend>]: {e}"
        ) from e


# ── probing ─────────────────────────────────────────────────────────────────────────────

PROBE_TIMEOUT_S = 5.0
_UNPROBEABLE = {"mujoco": "loads the physics model"}
"""Backends `--probe` declines to reach. Connecting to one fetches a model from upstream,
which is a download, not a liveness check."""


@dataclass(frozen=True)
class ProbeResult:
    reachable: bool | None
    """True, False, or None for a backend this deliberately does not reach."""
    detail: str
    elapsed_s: float


async def probe_entry(
    entry: RobotEntry,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    before_connect: Callable[[RobotEntry], None] | None = None,
) -> ProbeResult:
    """Connect, ask how it is, and close. Never raises: an unreachable robot is an answer.

    `before_connect` is handed the entry once its body is built and just before it connects,
    which a robot the probe skips, or one whose body cannot be built, never reaches: `quackd
    robot list --probe` says there that a robot is on a backend its adapter is retiring."""
    import asyncio
    import time

    from quackd.adapters.factory import make_adapter

    if (problem := entry.spec_problem) is not None:
        # ahead of the backends a probe declines, which are read off the stored spec's text: a
        # robot this quackd cannot build is a failed probe, and `robot list` says the fix
        return ProbeResult(False, problem, 0.0)
    if (why := _UNPROBEABLE.get(entry.backend)) is not None:
        return ProbeResult(None, f"skipped: {why}", 0.0)
    started = time.perf_counter()

    def elapsed() -> float:
        return round(time.perf_counter() - started, 3)

    adapter: Any = None
    closed = False
    try:
        adapter = make_adapter(
            entry.robot_spec,
            seed=0,
            address=entry.address,
            camera_url=entry.camera_urls,
            token=entry.token,
            # a probe reads and lets go; it never drives the arm anywhere, so the pose it was
            # registered with is what decides whether torque may drop at the end
            rest_pose=entry.rest_pose,
        )
        if before_connect is not None:
            before_connect(entry)
        await asyncio.wait_for(adapter.connect(), timeout=timeout_s)
        health = await asyncio.wait_for(adapter.health(), timeout=timeout_s)
        detail = health.reason or "ok"
        if health.battery_percent is not None:
            detail = f"{detail}, battery {health.battery_percent:.0f}%"
        # closed here rather than after, so a robot that kept its torque says so on the same
        # line that says it answered. A probe reads and lets go: it never drives the arm to
        # its rest pose, so an arm away from that pose keeps torque and is named.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(adapter.close(), timeout=timeout_s)
        closed = True
        result = ProbeResult(bool(health.ok), detail, elapsed())
    except TimeoutError:
        result = ProbeResult(False, f"timed out after {timeout_s:g} s", elapsed())
    except Exception as e:  # an unreachable robot must not end the command
        first = str(e).splitlines()[0] if str(e) else type(e).__name__
        result = ProbeResult(False, first, elapsed())
    # after the try rather than in a `finally`, because a probe that timed out or failed is
    # exactly when an arm is most likely to be left holding itself up, and a `finally` that
    # only closed had no way to put that on the line the reader sees
    if adapter is not None and not closed:
        with contextlib.suppress(Exception):
            await adapter.close()
    if (note := getattr(adapter, "close_note", None)) and "torque" not in result.detail:
        # the note's own words, shortened, rather than a fixed sentence. There is more than
        # one of them: one says the arm is being held up and will not fall, and one says
        # quackd could not keep it powered and it was released where it stood. Printing the
        # first for the second tells somebody an arm is safe at the moment it is not.
        return replace(result, detail=f"{result.detail}, {_torque_phrase(str(note))}")
    return result


def _torque_phrase(note: str) -> str:
    """One column's worth of a close note, keeping which way round it is.

    `robot list --probe` has a line per robot, not a paragraph, so the note is shortened here
    rather than printed whole. What may not be lost in the shortening is the direction: held
    up, let go, or not known, which is an arm that did not answer the close's own read and so
    may be limp at the switch rather than holding itself up."""
    if "could not keep torque on" in note:
        return "torque could NOT be kept: the arm was released where it stood"
    if "cannot tell whether" in note:
        return "torque unknown: the arm did not answer the close"
    if "has no rest pose recorded" in note:
        # a real arm with none is kept powered at the close rather than let go of where it
        # stands (ADR-0058), and a probe moves nothing, so this is the probe's own doing
        return "torque left on: no rest pose recorded"
    if "torque reads off on" in note:
        # the close's own read found servos limp, a brown-out the commonest cause: the arm is
        # not held up there, which is the other way round from the line below
        return "torque reads OFF: the arm may have dropped"
    return "torque left on: not at its rest pose"


def probe_all(
    entries: Iterable[RobotEntry],
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    before_connect: Callable[[RobotEntry], None] | None = None,
) -> dict[str, ProbeResult]:
    """Every robot at once, because they are separate machines and waiting serially is rude.
    `before_connect` goes to each robot's `probe_entry`."""
    import asyncio

    rows = list(entries)

    async def run() -> list[ProbeResult]:
        return list(
            await asyncio.gather(
                *(probe_entry(e, timeout_s=timeout_s, before_connect=before_connect) for e in rows)
            )
        )

    return dict(zip((e.name for e in rows), asyncio.run(run()), strict=True))


__all__ = [
    "DEFAULT_DIR",
    "ENV_DIR",
    "FLOCKS_FILE",
    "MAX_MEMBERS",
    "PROBE_TIMEOUT_S",
    "ROBOTS_FILE",
    "VERSION",
    "FlockNotRunnable",
    "ProbeResult",
    "Registry",
    "RegistryError",
    "Resolved",
    "RobotEntry",
    "RobotInUse",
    "StoredFlock",
    "UnknownFlock",
    "UnknownRobot",
    "check_name",
    "no_rest_pose_refusal",
    "probe_all",
    "probe_entry",
    "registry_dir",
    "resolve_robot_ref",
]
