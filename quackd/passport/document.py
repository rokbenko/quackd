"""Reading a passport: the rules of spec section 3, in their order, and the view a reader shows.

A document breaks rules 1 to 7 and it is refused with a `PassportError` naming the rule. It
survives them and the rest are readings rather than refusals: an unknown member is ignored, an
unknown value reads as `other`, `safety` reads as the worst case, and a required extension the
reader does not understand stops it talking. Each of those leaves a note, so what a reader did
with a passport is as visible as what the passport said."""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, TypeGuard

from quackd.passport import jws, stamps, strict
from quackd.passport.errors import PassportError
from quackd.passport.keys import DID_PREFIX, public_from_did, short_id_of
from quackd.passport.stamps import Stamp

VERSION = "0.1"
MAJOR = 0
#: What a reader must accept, and what a writer should stay under (spec 2.1).
MAX_BYTES = 8192
WRITER_BYTES = 2048
PASSPORT_TYP = "robot-passport+jws"
DEFAULT_TTL_S = 3600
MAX_TTL_S = 86_400

EMBODIMENTS = ("biped", "quadruped", "humanoid", "wheeled", "tracked", "arm", "aerial", "other")
MOBILITIES = ("none", "legged", "wheeled", "tracked", "flying", "other")
MANIPULATORS = ("none", "beak", "gripper", "arms", "other")
TERRAINS = ("indoor_flat", "indoor", "outdoor", "other")
CORE_SENSORS = (
    "camera",
    "microphone",
    "imu",
    "odometry",
    "tof",
    "lidar",
    "battery",
    "joint_state",
)
CORE_SKILLS = (
    "stop",
    "observe",
    "report_state",
    "move",
    "go_to",
    "search_scan",
    "approach_and",
    "say",
)
COURTESIES = ("pause", "identify")
ESTOPS = ("button", "remote", "power", "none")
CONFIDENCES = ("official", "estimate", "measured")
FIGURES = (
    "mass_kg",
    "height_m",
    "length_m",
    "width_m",
    "payload_kg",
    "reach_m",
    "endurance_min",
    "max_speed_mps",
)
VIAS = ("http", "mqtt")
#: Why a stamp that checks out still does not count on an unsigned passport (spec 4.5).
UNSIGNED_STAMP = "the passport is unsigned, so nothing shows the key it stamps wrote it"
#: The extensions quackd understands, so a required one of these does not stop it talking.
KNOWN_EXTENSIONS = frozenset({"org.quackd"})

_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_DID = re.compile(r"^did:key:z6Mk[1-9A-HJ-NP-Za-km-z]{44}$")
_UUID = re.compile(
    r"^urn:uuid:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SKILL = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_EXT = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_JWS = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

_MEMBERS = {
    "": (
        "passport",
        "id",
        "name",
        "maker",
        "software",
        "owner",
        "body",
        "skills",
        "courtesy",
        "cannot",
        "safety",
        "talk",
        "stamps",
        "ext",
        "issued",
        "ttl_s",
    ),
    "maker": ("vendor", "model", "serial"),
    "software": ("name", "version"),
    "owner": ("label", "contact"),
    "body": ("embodiment", "mobility", "manipulator", "terrain", "tethered", "sensors", *FIGURES),
    "safety": ("stops_on_silence", "estop", "how_to_stop"),
}


def _refuse(rule: str, message: str) -> PassportError:
    return PassportError(rule, message)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _text(
    value: object, where: str, limit: int, *, rule: str = "rule 5", empty: bool = True
) -> str:
    if not isinstance(value, str):
        raise _refuse(rule, f"{where} is not a string")
    if len(value) > limit:
        raise _refuse(rule, f"{where} is {len(value)} characters, over the {limit} allowed")
    if not empty and not value:
        raise _refuse(rule, f"{where} is empty")
    return value


def _object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _refuse("rule 5", f"{where} is not an object")
    return value


def _array(value: object, where: str, limit: int) -> list[Any]:
    if not isinstance(value, list):
        raise _refuse("rule 5", f"{where} is not an array")
    if len(value) > limit:
        raise _refuse("rule 5", f"{where} holds {len(value)} items, over the {limit} allowed")
    return value


def check_version(value: object) -> str:
    """Rule 3 for `passport`, then rule 4: a MAJOR this reader knows."""
    if not isinstance(value, str) or not _VERSION.match(value):
        raise _refuse("rule 3", f"passport is {value!r}, not a MAJOR.MINOR version")
    if int(value.split(".")[0]) != MAJOR:
        raise _refuse("rule 4", f"version {value} has a MAJOR this reader does not know")
    return value


def check_id(value: object, where: str = "id") -> str:
    """Rule 3 for an id, and rule 7 for a `did:key` that holds anything but Ed25519."""
    if not isinstance(value, str):
        raise _refuse("rule 3", f"{where} is not a string")
    if value.startswith("did:key:"):
        public_from_did(value)  # raises under rule 7
        if not _DID.match(value):
            raise _refuse("rule 7", f"{where} is not an Ed25519 did:key")
        return value
    if _UUID.match(value):
        return value
    raise _refuse("rule 3", f"{where} {value!r} is neither a did:key nor a urn:uuid")


def check_figure(value: object, where: str) -> None:
    if _is_number(value):
        return
    if (
        isinstance(value, list)
        and 2 <= len(value) <= 3
        and _is_number(value[0])
        and isinstance(value[1], str)
        and len(value[1]) <= 16
        and (len(value) == 2 or (isinstance(value[2], str) and len(value[2]) <= 120))
    ):
        return
    raise _refuse(
        "rule 5", f"{where} is neither a number nor [number, confidence] with an optional source"
    )


def validate(doc: object) -> dict[str, Any]:
    """Rules 3, 4, 5 and 7 on a parsed passport, which is returned when it passes."""
    if not isinstance(doc, dict):
        raise _refuse("rule 2", "it is not a JSON object")
    for required in ("passport", "id", "name"):
        if required not in doc:
            raise _refuse("rule 3", f"it has no {required}")
    check_version(doc["passport"])
    check_id(doc["id"])
    _text(doc["name"], "name", 64, rule="rule 3", empty=False)
    for member, fields in (
        ("maker", ("vendor", "model", "serial")),
        ("software", ("name", "version")),
    ):
        if member in doc:
            obj = _object(doc[member], member)
            for name in fields:
                if name in obj:
                    _text(obj[name], f"{member}.{name}", 64)
    if "owner" in doc:
        owner = _object(doc["owner"], "owner")
        if "label" in owner:
            _text(owner["label"], "owner.label", 64)
        if "contact" in owner:
            _text(owner["contact"], "owner.contact", 200)
    if "body" in doc:
        _validate_body(_object(doc["body"], "body"))
    if "skills" in doc:
        for i, skill in enumerate(_array(doc["skills"], "skills", 64)):
            if not isinstance(skill, str) or not _SKILL.match(skill):
                raise _refuse("rule 5", f"skills[{i}] {skill!r} is not a skill name")
    if "courtesy" in doc:
        for i, name in enumerate(_array(doc["courtesy"], "courtesy", 8)):
            _text(name, f"courtesy[{i}]", 32)
    if "cannot" in doc:
        for i, line in enumerate(_array(doc["cannot"], "cannot", 8)):
            _text(line, f"cannot[{i}]", 120, empty=False)
    if "talk" in doc:
        for i, channel in enumerate(_array(doc["talk"], "talk", 8)):
            _validate_channel(_object(channel, f"talk[{i}]"), f"talk[{i}]")
    if "stamps" in doc:
        for i, token in enumerate(_array(doc["stamps"], "stamps", 4)):
            _text(token, f"stamps[{i}]", 1024)
            if not _JWS.match(token):
                raise _refuse("rule 5", f"stamps[{i}] is not a compact JWS")
    if "ext" in doc:
        for key, value in _object(doc["ext"], "ext").items():
            if not _EXT.match(key):
                raise _refuse("rule 5", f"ext key {key!r} is not a reverse-DNS name")
            extension = _object(value, f"ext[{key!r}]")
            if "required" in extension and not isinstance(extension["required"], bool):
                raise _refuse("rule 5", f"ext[{key!r}].required is not true or false")
    if "issued" in doc:
        _check_datetime(doc["issued"])
    if "ttl_s" in doc and (not _is_int(doc["ttl_s"]) or not 60 <= doc["ttl_s"] <= MAX_TTL_S):
        raise _refuse("rule 5", f"ttl_s is {doc['ttl_s']!r}, not an integer from 60 to 86400")
    return doc


def _validate_body(body: dict[str, Any]) -> None:
    for name in ("embodiment", "mobility", "manipulator", "terrain"):
        if name in body:
            _text(body[name], f"body.{name}", 32)
    if "tethered" in body and not isinstance(body["tethered"], bool):
        raise _refuse("rule 5", "body.tethered is not true or false")
    if "sensors" in body:
        for i, sensor in enumerate(_array(body["sensors"], "body.sensors", 32)):
            _text(sensor, f"body.sensors[{i}]", 32)
    for name in FIGURES:
        if name in body:
            check_figure(body[name], f"body.{name}")


def _validate_channel(channel: dict[str, Any], where: str) -> None:
    for required in ("via", "url"):
        if required not in channel:
            raise _refuse("rule 5", f"{where} has no {required}")
    _text(channel["via"], f"{where}.via", 16)
    _text(channel["url"], f"{where}.url", 200)
    if "max_bytes" in channel and (
        not _is_int(channel["max_bytes"]) or channel["max_bytes"] < 1024
    ):
        raise _refuse("rule 5", f"{where}.max_bytes is not an integer of at least 1024")


def _check_datetime(value: object) -> None:
    if not isinstance(value, str) or not _DATETIME.match(value):
        raise _refuse("rule 5", f"issued is {value!r}, not an RFC 3339 date-time")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise _refuse("rule 5", f"issued is {value!r}, not a real date-time") from e


# ── the index ───────────────────────────────────────────────────────────────────────────


def is_index(doc: object) -> bool:
    """An index has `robots` and no `id` (spec 2.7)."""
    return isinstance(doc, dict) and "robots" in doc and "id" not in doc


@dataclass(frozen=True)
class Entry:
    url: str
    name: str | None = None
    id: str | None = None


def validate_index(doc: dict[str, Any]) -> list[Entry]:
    check_version(doc.get("passport"))
    robots = _array(doc["robots"], "robots", 32)
    if not robots:
        raise _refuse("rule 5", "robots is empty")
    entries: list[Entry] = []
    for i, item in enumerate(robots):
        entry = _object(item, f"robots[{i}]")
        if "url" not in entry:
            raise _refuse("rule 5", f"robots[{i}] has no url")
        url = _text(entry["url"], f"robots[{i}].url", 200)
        name = _text(entry["name"], f"robots[{i}].name", 64) if "name" in entry else None
        robot_id = check_id(entry["id"], f"robots[{i}].id") if "id" in entry else None
        entries.append(Entry(url, name, robot_id))
    return entries


# ── a reading ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Figure:
    value: float
    confidence: str | None
    source: str | None


@dataclass(frozen=True)
class Safety:
    """`safety` as a reader takes it: the worst case wherever the passport is unclear."""

    stops_on_silence: bool
    estop: str
    how_to_stop: str | None


@dataclass(frozen=True)
class Reading:
    """A passport that survived rules 1 to 7, and what a reader made of the rest."""

    document: dict[str, Any]
    raw: bytes
    signed: bool
    stamps: tuple[Stamp, ...]
    notes: tuple[str, ...]
    talk_allowed: bool

    @property
    def id(self) -> str:
        return str(self.document["id"])

    @property
    def name(self) -> str:
        return str(self.document["name"])

    @property
    def version(self) -> str:
        return str(self.document["passport"])

    @property
    def digest(self) -> str:
        """The SHA-256 of the exact bytes the passport is (spec 4.3)."""
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def short_id(self) -> str | None:
        return short_id_of(self.id)

    @property
    def provenance(self) -> str:
        """Which step a reader reached (spec rule 11), before any stamp or pin is counted."""
        return "signed by its own key" if self.signed else "unsigned: a claim"

    @property
    def ttl_s(self) -> int:
        value = self.document.get("ttl_s")
        return value if _is_int(value) else DEFAULT_TTL_S

    def body(self) -> dict[str, Any]:
        return _body_view(self.document.get("body"), [])

    def safety(self) -> Safety:
        return _safety_view(self.document.get("safety"), [])

    def skills(self) -> list[str]:
        return list(self.document.get("skills", []))

    def courtesy(self) -> list[str]:
        return [c for c in self.document.get("courtesy", []) if c in COURTESIES]

    def channels(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self.document.get("talk", []) if c.get("via") in VIAS]

    def counted_stamps(self) -> list[Stamp]:
        return [s for s in self.stamps if s.counts]

    def view(self) -> dict[str, Any]:
        """Everything a reader shows, normalised, as plain JSON."""
        doc = self.document
        safety = self.safety()
        return {
            "passport": self.version,
            "id": self.id,
            "short_id": self.short_id,
            "name": self.name,
            "digest": self.digest,
            "signed": self.signed,
            "provenance": self.provenance,
            "maker": dict(doc.get("maker", {})),
            "software": dict(doc.get("software", {})),
            "owner": dict(doc.get("owner", {})),
            "body": self.body(),
            "skills": self.skills(),
            "core_skills": [s for s in self.skills() if s in CORE_SKILLS],
            "courtesy": self.courtesy(),
            "cannot": list(doc.get("cannot", [])),
            "safety": {
                "stops_on_silence": safety.stops_on_silence,
                "estop": safety.estop,
                "how_to_stop": safety.how_to_stop,
            },
            "talk": self.channels(),
            "talk_allowed": self.talk_allowed,
            "stamps": [
                {
                    "iss": s.iss,
                    "rel": s.rel,
                    "exp": s.exp,
                    "counts": s.counts,
                    "reason": s.reason,
                }
                for s in self.stamps
            ],
            "issued": doc.get("issued"),
            "ttl_s": self.ttl_s,
            "notes": list(self.notes),
        }


def _figure(value: Any) -> Figure:
    if _is_number(value):
        return Figure(float(value), None, None)
    confidence = value[1] if value[1] in CONFIDENCES else "estimate"
    return Figure(float(value[0]), confidence, value[2] if len(value) == 3 else None)


def _body_view(body: object, notes: list[str]) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    out: dict[str, Any] = {}
    for name, known in (
        ("embodiment", EMBODIMENTS),
        ("mobility", MOBILITIES),
        ("manipulator", MANIPULATORS),
        ("terrain", TERRAINS),
    ):
        if name in body:
            value = body[name]
            if value not in known:
                notes.append(f"body.{name} {value!r} read as other (rule 8)")
                value = "other"
            out[name] = value
    if "tethered" in body:
        out["tethered"] = body["tethered"]
    if "sensors" in body:
        out["sensors"] = list(body["sensors"])
    for name in FIGURES:
        if name in body:
            f = _figure(body[name])
            out[name] = {"value": f.value, "confidence": f.confidence, "source": f.source}
            if isinstance(body[name], list) and body[name][1] not in CONFIDENCES:
                notes.append(f"body.{name} confidence {body[name][1]!r} read as estimate")
    return out


def _safety_view(safety: object, notes: list[str]) -> Safety:
    """Spec 2.5: anything unclear reads as the worst case, and nothing here refuses."""
    if safety is None:
        return Safety(False, "none", None)
    if not isinstance(safety, dict):
        notes.append("safety is not an object, so all of it reads as the worst case (2.5)")
        return Safety(False, "none", None)
    stops = safety.get("stops_on_silence")
    if "stops_on_silence" in safety and not isinstance(stops, bool):
        notes.append(f"safety.stops_on_silence {stops!r} read as false (2.5)")
    estop = safety.get("estop", "none")
    if estop not in ESTOPS:
        notes.append(f"safety.estop {estop!r} read as none (2.5)")
        estop = "none"
    how = safety.get("how_to_stop")
    if "how_to_stop" in safety and not (isinstance(how, str) and 0 < len(how) <= 120):
        notes.append("safety.how_to_stop is not a sentence of 120 characters or fewer, so absent")
        how = None
    return Safety(stops is True, estop, how)


def _readings(doc: dict[str, Any]) -> tuple[list[str], bool]:
    """Rules 8 to 10: the notes a reader leaves, and whether it may talk to this robot."""
    notes: list[str] = []
    version = doc["passport"]
    if version != VERSION and int(version.split(".")[1]) > int(VERSION.split(".")[1]):
        notes.append(f"version {version} is later than {VERSION}: what is new was ignored")
    for section, known in _MEMBERS.items():
        scope = doc if not section else doc.get(section)
        if isinstance(scope, dict):
            for member in scope:
                if member not in known:
                    where = f"{section}.{member}" if section else member
                    notes.append(f"{where} is not a member this reader knows, so it was ignored")
    _body_view(doc.get("body"), notes)
    _safety_view(doc.get("safety"), notes)
    for i, name in enumerate(doc.get("courtesy", [])):
        if name not in COURTESIES:
            notes.append(f"courtesy[{i}] {name!r} is not one this reader knows, so it was ignored")
    for i, channel in enumerate(doc.get("talk", [])):
        if channel["via"] not in VIAS:
            notes.append(f"talk[{i}] via {channel['via']!r} is unknown, so it was skipped")
    talk_allowed = True
    for key, extension in doc.get("ext", {}).items():
        if extension.get("required") is True and key not in KNOWN_EXTENSIONS:
            talk_allowed = False
            notes.append(f"the required extension {key} is not understood: no talk (2.6)")
    return notes, talk_allowed


def _reading(doc: dict[str, Any], raw: bytes, *, signed: bool, now: int | None) -> Reading:
    notes, talk_allowed = _readings(doc)
    moment = int(time.time()) if now is None else now
    judged: list[Stamp] = []
    for token in doc.get("stamps", []):
        stamp = stamps.check(token, subject=doc["id"], now=moment)
        if stamp.counts and not signed:
            # a stamp vouches for a key, and nothing shows that key wrote an unsigned passport:
            # anybody can copy an id and a stamp into one (spec 4.5)
            stamp = replace(stamp, counts=False, reason=UNSIGNED_STAMP)
        judged.append(stamp)
    return Reading(doc, raw, signed, tuple(judged), tuple(notes), talk_allowed)


def read_json(raw: bytes, *, now: int | None = None) -> Reading:
    """An unsigned passport, from its exact bytes."""
    doc = strict.loads(raw, limit=MAX_BYTES)
    if is_index(doc):
        raise _refuse("2.7", "it is an index of passports, not a passport")
    return _reading(validate(doc), raw, signed=False, now=now)


def read_jws(token: str, *, now: int | None = None) -> Reading:
    """A signed passport, from its compact JWS (spec 4.4)."""
    verified = jws.verify_compact(token, PASSPORT_TYP, rule="4.4", payload_limit=MAX_BYTES)
    doc = validate(strict.loads(verified.payload, limit=MAX_BYTES))
    if not str(doc["id"]).startswith(DID_PREFIX):
        raise _refuse("4.4", "a passport whose id is a urn:uuid cannot be signed")
    if doc["id"] != verified.kid:
        raise _refuse("4.4", "it was signed by a key other than the one its id holds")
    return _reading(doc, verified.payload, signed=True, now=now)


def read(data: bytes | str, *, now: int | None = None) -> Reading | list[Entry]:
    """Whatever a well-known URL served: a signed passport, an unsigned one, or an index."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    text = raw.decode("utf-8", errors="replace")
    if jws.looks_like_jws(text):
        return read_jws(text, now=now)
    doc = strict.loads(raw, limit=MAX_BYTES)
    if is_index(doc):
        return validate_index(doc)
    return _reading(validate(doc), raw, signed=False, now=now)


def sign(doc: dict[str, Any], key: Any) -> str:
    """`doc` as a signed passport: the compact bytes quackd writes, signed by `key`."""
    if doc.get("id") != key.did:
        raise ValueError("a passport is signed by the key its id holds")
    return jws.sign(strict.dumps(doc), key, PASSPORT_TYP)
