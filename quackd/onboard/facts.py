"""The passport robot.yaml describes, as the dict its owner signs, and what changed since.

A robot quackd drives takes its body, skills and courtesies from its adapter's manifest, the way
every passport quackd built before did (`quackd.adapters.passport`), with its name, owner and
safety from the file. A robot quackd only talks for takes all of it from the file. Either way the
`board` is what quackd found the robot runs on, never what the file says, so a laptop's passport
says `sim` however it is edited.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from quackd.adapters.manifest import RobotManifest
from quackd.adapters.passport import passport_from_manifest
from quackd.onboard.config import RobotFile
from quackd.passport import strict
from quackd.passport.document import VERSION, Reading, validate

#: Members that change with every signature and say nothing a person edited.
_DATED = ("issued", "expires")


def passport_document(
    file: RobotFile,
    *,
    robot_did: str,
    owner_did: str,
    board: Mapping[str, str],
    issued: datetime,
    expires: datetime,
    manifest: RobotManifest | None = None,
) -> dict[str, Any]:
    """The 1.0 passport for this robot, as its owner will sign it (spec 2)."""
    section = file.passport
    owner = {"id": owner_did, "label": section.owner.label}
    if section.owner.contact:
        owner["contact"] = section.owner.contact
    if manifest is not None:
        doc = passport_from_manifest(
            manifest,
            robot_id=robot_did,
            name=section.name,
            owner=owner,
            board=board,
            issued=issued,
            expires=expires,
            estop=section.safety.estop,
            how_to_stop=section.safety.how_to_stop,
        )
        doc["maker"] = section.maker.model_dump(exclude_none=True)
        if section.cannot:
            doc["cannot"] = list(section.cannot)
        doc["safety"]["stops_on_silence"] = (
            doc["safety"]["stops_on_silence"] or section.safety.stops_on_silence
        )
    else:
        doc = {
            "passport": VERSION,
            "id": robot_did,
            "name": section.name,
            "maker": section.maker.model_dump(exclude_none=True),
            "owner": owner,
            "board": dict(board),
            "body": _body(file),
            "skills": list(section.skills),
            # a body quackd only talks for moves nothing of quackd's doing, so it offers no
            # courtesy a peer could ask of quackd: its own software decides how it moves
            "courtesy": [],
        }
        if section.cannot:
            doc["cannot"] = list(section.cannot)
        doc["safety"] = section.safety.model_dump(exclude_none=True)
        doc["issued"] = _moment(issued)
        doc["expires"] = _moment(expires)
    validate(strict.loads(strict.dumps(doc), limit=len(strict.dumps(doc))))
    return doc


def _body(file: RobotFile) -> dict[str, Any]:
    body = file.passport.body
    out: dict[str, Any] = {
        "embodiment": body.embodiment,
        "mobility": body.mobility,
        "manipulator": body.manipulator,
    }
    if body.terrain is not None:
        out["terrain"] = body.terrain
    if body.tethered is not None:
        out["tethered"] = body.tethered
    if body.sensors:
        out["sensors"] = list(body.sensors)
    for name in (
        "mass_kg",
        "height_m",
        "length_m",
        "width_m",
        "payload_kg",
        "reach_m",
        "endurance_min",
        "max_speed_mps",
    ):
        value = getattr(body, name)
        if value is not None:
            out[name] = [value[0], value[1], value[2]]
    return out


def _moment(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def unsigned_changes(
    file: RobotFile,
    signed: Reading,
    *,
    board: Mapping[str, str],
    manifest: RobotManifest | None = None,
) -> list[str]:
    """The members robot.yaml now says differently from the passport its owner signed, by name,
    so a person knows to sign again. The dates and the keys are the signed passport's own."""
    now = passport_document(
        file,
        robot_did=signed.id,
        owner_did=signed.owner_id,
        board=board,
        issued=datetime.fromtimestamp(signed.issued, UTC),
        expires=datetime.fromtimestamp(signed.expires, UTC),
        manifest=manifest,
    )
    was = signed.document
    names = sorted(set(now) | set(was))
    return [n for n in names if n not in _DATED and now.get(n) != was.get(n)]
