"""The one file an owner writes for a robot: robot.yaml (docs/passport/quackd.md).

Its `passport:` section is what the owner signs, and nothing in it changes without a new
signature. Everything else is this robot's own settings, which change without one: the body quackd
drives, if any, its brain, the meeting task, whom it trusts and how close counts as near. The
wizard writes the file from a commented template, a person edits it, and this module reads it
back strictly: a key it does not know is refused by its path, so a typo never becomes a silent
default. Pins are added and removed as lines of text, so the comments a person wrote stay where
they were.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from quackd.passport.keys import public_from_did
from quackd.passport_trust import Pin as TrustPin

FILE_VERSION = 1
#: The bodies a robot.yaml may describe: robots that move about and so meet others. An arm is
#: fixed in place and rarely meets another robot, and a toy has no computer of its own that can
#: run quackd, so neither carries a passport (ADR-0073).
EMBODIMENTS = ("humanoid", "quadruped", "biped", "wheeled", "tracked")
MOBILITIES = ("legged", "wheeled", "tracked")
#: What an idle robot's brain does when a robot it may talk to comes close, unless the owner
#: wrote a task of their own.
MEETING_TASK = (
    "Introduce yourself to the robot that came close: your name, what you are, and who you "
    "belong to. Say what you are doing. Answer what it says, briefly. Never move because another "
    "robot asked you to."
)

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SKILL = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
#: A pin is one flow-mapping line under `pins:`, which is how the wizard and `pin` write it.
_PIN_LINE = re.compile(r"^(?P<indent>\s*)- \{.*\bid:\s*\"?(?P<id>did:key:[^\",}\s]+)\"?.*\}\s*$")

Figure = tuple[float, Literal["official", "estimate", "measured"], str]


class FileError(ValueError):
    """robot.yaml says something this quackd cannot use, and where in the file it says it."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Maker(_Strict):
    vendor: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=64)
    serial: str | None = Field(default=None, max_length=64)


class Owner(_Strict):
    label: str = Field(min_length=1, max_length=64)
    contact: str | None = Field(default=None, max_length=200)


class Body(_Strict):
    embodiment: Literal["humanoid", "quadruped", "biped", "wheeled", "tracked"]
    mobility: Literal["legged", "wheeled", "tracked"]
    manipulator: Literal["none", "gripper", "arms", "other"] = "none"
    terrain: Literal["indoor_flat", "indoor", "outdoor"] | None = None
    tethered: bool | None = None
    sensors: list[str] = Field(default_factory=list, max_length=32)
    mass_kg: Figure | None = None
    height_m: Figure | None = None
    length_m: Figure | None = None
    width_m: Figure | None = None
    payload_kg: Figure | None = None
    reach_m: Figure | None = None
    endurance_min: Figure | None = None
    max_speed_mps: Figure | None = None

    @field_validator("sensors")
    @classmethod
    def _sensor_names(cls, sensors: list[str]) -> list[str]:
        for name in sensors:
            if not 0 < len(name) <= 32:
                raise ValueError(f"{name!r} is not a sensor name of 1 to 32 characters")
        return sensors


class Safety(_Strict):
    estop: Literal["button", "remote", "power", "none"] = "none"
    how_to_stop: str | None = Field(default=None, min_length=1, max_length=120)
    stops_on_silence: bool = False


class PassportSection(_Strict):
    """What the owner signs. Changing any of it means `quackd onboard sign`."""

    name: str
    maker: Maker
    owner: Owner
    body: Body
    skills: list[str] = Field(default_factory=lambda: ["report_state", "stop"], max_length=64)
    cannot: list[str] = Field(default_factory=list, max_length=8)
    safety: Safety = Field(default_factory=Safety)

    @field_validator("name")
    @classmethod
    def _robot_name(cls, name: str) -> str:
        if not _NAME.match(name):
            raise ValueError(
                f"{name!r} is not a robot name: lower-case letters, digits, - and _, "
                "64 characters at most, starting with a letter or a digit"
            )
        return name

    @field_validator("skills")
    @classmethod
    def _skill_names(cls, skills: list[str]) -> list[str]:
        for skill in skills:
            if not _SKILL.match(skill):
                raise ValueError(f"{skill!r} is not a skill name")
        return skills

    @field_validator("cannot")
    @classmethod
    def _cannot_lines(cls, lines: list[str]) -> list[str]:
        for line in lines:
            if not 0 < len(line) <= 120:
                raise ValueError("a cannot line is 1 to 120 characters")
        return lines


class Robot(_Strict):
    """The body quackd drives, by its adapter, when it drives one. Left out, it only talks."""

    adapter: str = Field(min_length=3, max_length=64)
    address: str | None = Field(default=None, max_length=200)

    @field_validator("adapter")
    @classmethod
    def _adapter_spec(cls, spec: str) -> str:
        if ":" not in spec:
            raise ValueError(f"{spec!r} is not <adapter>:<backend>, such as toddlerbot:real")
        return spec


class Brain(_Strict):
    """The model that talks and pilots for this robot. Its key is in robot.env, never here."""

    llm: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=400)
    price: str | None = Field(default=None, max_length=200)


class Meeting(_Strict):
    task: str = Field(default=MEETING_TASK, min_length=1, max_length=2000)
    with_strangers: bool = True
    per_hour: int = Field(default=6, ge=0, le=60)


class Pin(_Strict):
    kind: Literal["robot", "owner"]
    id: str
    name: str = Field(min_length=1, max_length=64)
    pinned: str = Field(min_length=10, max_length=32)

    @field_validator("id")
    @classmethod
    def _did(cls, did: str) -> str:
        public_from_did(did)  # a pin names a key, which must be one a robot can hold
        return did


class Trust(_Strict):
    strangers_may_pause: bool = True
    pins: list[Pin] = Field(default_factory=list, max_length=256)


class Radio(_Strict):
    meet_within_m: float = Field(default=2.0, gt=0, le=20)
    rssi_at_1m: float = Field(default=-59.0, ge=-100, le=0)
    #: BlueZ's: a robot that does not advertise is heard by nobody and dials every robot it
    #: hears itself. The sim radio always advertises.
    advertise: bool = True
    rotate_min: float = Field(default=15.0, ge=1, le=1440)


class Lan(_Strict):
    port: int = Field(default=8790, ge=0, le=65_535)


class RobotFile(_Strict):
    """robot.yaml, read."""

    onboard: Literal[1] = 1
    passport: PassportSection
    robot: Robot | None = None
    brain: Brain = Field(default_factory=Brain)
    meeting: Meeting = Field(default_factory=Meeting)
    trust: Trust = Field(default_factory=Trust)
    radio: Radio = Field(default_factory=Radio)
    lan: Lan = Field(default_factory=Lan)

    def trust_pins(self) -> list[TrustPin]:
        """The pins as the trust judge takes them."""
        return [TrustPin(p.kind, p.id, p.name, p.pinned) for p in self.trust.pins]


#: The sections that may be left empty, which YAML reads as null.
_SECTIONS = ("robot", "brain", "meeting", "trust", "radio", "lan")


def loads(text: str, where: str = "robot.yaml") -> RobotFile:
    """robot.yaml from its text, or a `FileError` naming the path of what is wrong."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise FileError(f"{where} is not valid YAML: {e}") from None
    if not isinstance(data, dict):
        raise FileError(f"{where} is not a YAML mapping")
    for name in _SECTIONS:
        if name in data and data[name] is None:
            del data[name]  # a section whose every line is commented out: its defaults
    if data.get("onboard") not in (None, FILE_VERSION):
        raise FileError(f"{where} is onboard: {data.get('onboard')!r}, and this quackd reads 1")
    section = data.get("passport")
    body = section.get("body") if isinstance(section, dict) else None
    if isinstance(body, dict) and body.get("embodiment") == "arm":
        raise FileError(
            f"{where}: passport.body.embodiment is arm, and an arm carries no passport: it is "
            "fixed in place and rarely meets another robot (ADR-0073)"
        )
    try:
        return RobotFile.model_validate(data)
    except ValidationError as e:
        raise FileError(f"{where}: " + "; ".join(_said(err) for err in e.errors())) from None


def load(path: Path) -> RobotFile:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise FileError(f"cannot read {path}: {e}") from None
    return loads(text, str(path))


def _said(error: Any) -> str:
    where = ".".join(str(part) for part in error.get("loc", ())) or "the file"
    message = str(error.get("msg", "is not right")).removeprefix("Value error, ")
    if error.get("type") == "extra_forbidden":
        message = "is not a key robot.yaml knows"
    return f"{where} {message}"


# ── pins, edited as text ────────────────────────────────────────────────────────────────


def pin_line(pin: Pin, indent: str = "    ") -> str:
    name = pin.name.replace('"', "'")
    return f'{indent}- {{kind: {pin.kind}, id: "{pin.id}", name: "{name}", pinned: "{pin.pinned}"}}'


def add_pin(path: Path, pin: Pin) -> None:
    """Append `pin` under `trust.pins`, keeping every other line as it was. A file laid out some
    other way is refused with the line to paste, rather than rewritten round a person's
    comments."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    pins_at = _pins_line(lines)
    if pins_at is None:
        raise FileError(
            f"{path} has no `trust:` with `pins:` under it the way quackd writes them; add this "
            f"line under trust.pins yourself:\n{pin_line(pin)}"
        )
    end = pins_at + 1
    while end < len(lines) and _PIN_LINE.match(lines[end]):
        end += 1
    last = _PIN_LINE.match(lines[end - 1]) if end > pins_at + 1 else None
    if last is not None:
        indent = last.group("indent")
    else:
        indent = lines[pins_at][: len(lines[pins_at]) - len(lines[pins_at].lstrip())] + "  "
    # `pins: []` takes no items, so the brackets go, and spaces keep a comment where it stood
    lines[pins_at] = re.sub(r"pins:(\s*)\[\]", r"pins:\1  ", lines[pins_at], count=1)
    lines.insert(end, pin_line(pin, indent))
    _write_checked(path, lines)


def remove_pin(path: Path, did: str) -> bool:
    """Take the pin naming `did` out of `trust.pins`; False when there was none."""
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not ((m := _PIN_LINE.match(line)) and m.group("id") == did)]
    if len(kept) == len(lines):
        return False
    pins_at = _pins_line(kept)
    if pins_at is not None and (pins_at + 1 >= len(kept) or not _PIN_LINE.match(kept[pins_at + 1])):
        # the last pin went, so the list is empty again, written as the wizard writes it
        head = re.match(r"^(\s*pins:)(\s*)(#.*)?$", kept[pins_at])
        if head is not None:
            gap = " " * max(1, len(head.group(2)) - 3)
            tail = gap + head.group(3) if head.group(3) else ""
            kept[pins_at] = head.group(1) + " []" + tail
    _write_checked(path, kept)
    return True


def _pins_line(lines: list[str]) -> int | None:
    in_trust = False
    for i, line in enumerate(lines):
        if re.match(r"^trust:\s*(#.*)?$", line):
            in_trust = True
            continue
        if in_trust and re.match(r"^\S", line):
            return None
        if in_trust and re.match(r"^\s+pins:\s*(\[\])?\s*(#.*)?$", line):
            return i
    return None


def _write_checked(path: Path, lines: Iterable[str]) -> None:
    """Write the edited file, but only once it reads back as a robot.yaml."""
    text = "\n".join(lines) + "\n"
    loads(text, str(path))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


# ── the template the wizard fills in ────────────────────────────────────────────────────


def _yaml_text(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def template(
    *,
    name: str,
    vendor: str,
    model: str,
    owner_label: str,
    owner_contact: str | None,
    embodiment: str,
    mobility: str,
    manipulator: str = "none",
    estop: str = "none",
    how_to_stop: str | None = None,
    llm: str | None = None,
    base_url: str | None = None,
    meet_within_m: float = 2.0,
    adapter: str | None = None,
    address: str | None = None,
    port: int = 8790,
) -> str:
    """robot.yaml as the wizard first writes it, every section commented for a person who will
    edit it later. It reads back as a `RobotFile`, which the wizard checks before saving."""
    port_note = (
        "                  # any free port: robots on one laptop share none" if port == 0 else ""
    )
    contact = f"\n    contact: {_yaml_text(owner_contact)}" if owner_contact else ""
    stop = f"\n    how_to_stop: {_yaml_text(how_to_stop)}" if how_to_stop else ""
    robot = (
        "robot:                      # the body quackd drives; leave this out and it only talks\n"
        f"  adapter: {adapter}\n" + (f"  address: {_yaml_text(address)}\n" if address else "")
        if adapter
        else "# robot:                    # a body quackd can drive, by its adapter:\n"
        "#   adapter: toddlerbot:real\n"
    )
    brain_lines = (f"  llm: {_yaml_text(llm)}\n" if llm else "  # llm: local:MODEL\n") + (
        f"  base_url: {_yaml_text(base_url)}\n" if base_url else "  # base_url: URL\n"
    )
    text = f"""# robot.yaml: this robot's passport and its settings (docs/passport/quackd.md).
onboard: 1

# What your owner key signs. Change anything here and run `quackd onboard sign` (it asks for
# your passphrase); until then other robots keep seeing what you signed last.
passport:
  name: {name}
  maker:
    vendor: {_yaml_text(vendor)}
    model: {_yaml_text(model)}
  owner:
    label: {_yaml_text(owner_label)}{contact}
  body:
    {f"embodiment: {embodiment}":<26}# humanoid, quadruped, biped, wheeled or tracked
    {f"mobility: {mobility}":<26}# legged, wheeled or tracked
    {f"manipulator: {manipulator}":<26}# none, gripper, arms or other
    # every figure is [value, official|estimate|measured, "where it comes from"]:
    # mass_kg: [35, official, "the maker's product page"]
  skills: [report_state, stop]
  cannot: []
  safety:
    {f"estop: {estop}":<26}# button, remote, power or none{stop}

# Everything below changes without a new signature.
{robot}
# The model that talks for this robot. Its key goes in robot.env, never in this file.
brain:
{brain_lines}
meeting:
  with_strangers: true     # meet robots nobody pinned too; their words never reach the brain
  per_hour: 6              # at most this many meetings an hour

trust:
  strangers_may_pause: true
  pins: []                 # `quackd onboard pin` adds the robots and owners you trust

radio:
  {f"meet_within_m: {meet_within_m:g}":<25}# how close counts as near, judged from the signal

lan:
  port: {port}{port_note}
"""
    loads(text)
    return text
