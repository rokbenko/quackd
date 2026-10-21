"""A robot's passport, built from its manifest, so no robot quackd drives needs one written.

`docs/passport/passport.md` has the mapping as a table. What a manifest cannot know, because it
belongs to one robot rather than to its model, comes from the owner: who owns it, and how a
person stops it. Left out, those read as the worst case, which is what the protocol asks a
reader to assume when nobody said."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from quackd.adapters.manifest import Figure, RobotManifest
from quackd.passport.document import ESTOPS, VERSION

#: A `cannot` line is at most this long in a passport (spec 2.2).
CANNOT_CHARS = 120
#: And a passport carries at most this many.
CANNOT_LINES = 8
#: Datasheet figures carried into a passport's `body`, under the same names.
_FIGURES = ("mass_kg", "height_m", "payload_kg", "reach_m", "endurance_min")


def passport_from_manifest(
    manifest: RobotManifest,
    *,
    robot_id: str,
    name: str,
    software_version: str,
    owner: Mapping[str, str] | None = None,
    estop: str | None = None,
    how_to_stop: str | None = None,
    talk: Sequence[Mapping[str, Any]] | None = None,
    issued: datetime | None = None,
) -> dict[str, Any]:
    """The passport for one robot with this manifest, as the dict quackd serializes.

    `robot_id` is the robot's own key as a `did:key`, `name` its registered name, and the rest
    is what its owner said. Members with nothing honest to put in them are left out."""
    if estop is not None and estop not in ESTOPS:
        raise ValueError(f"estop {estop!r} is not one of {', '.join(ESTOPS)}")
    doc: dict[str, Any] = {"passport": VERSION, "id": robot_id, "name": name}
    doc["maker"] = {"vendor": manifest.vendor, "model": manifest.model}
    doc["software"] = {"name": "quackd", "version": software_version}
    if owner:
        kept = {k: owner[k] for k in ("label", "contact") if owner.get(k)}
        if kept:
            doc["owner"] = kept
    doc["body"] = _body(manifest)
    doc["skills"] = [verb.name for verb in manifest.verbs]
    doc["courtesy"] = courtesy(manifest)
    cannot = [
        cannot_line(line) for line in (manifest.datasheet.cannot if manifest.datasheet else [])
    ]
    if cannot:
        doc["cannot"] = cannot[:CANNOT_LINES]
    safety: dict[str, Any] = {"stops_on_silence": manifest.safety_authority.deadman}
    if estop is not None:
        safety["estop"] = estop
    if how_to_stop:
        safety["how_to_stop"] = how_to_stop
    doc["safety"] = safety
    if talk:
        doc["talk"] = [dict(channel) for channel in talk]
    doc["ext"] = {"org.quackd": {"sha": manifest.digest()}}
    moment = issued if issued is not None else datetime.now(UTC)
    doc["issued"] = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return doc


def courtesy(manifest: RobotManifest) -> list[str]:
    """What a peer may ask this body for. Every body quackd drives moves, so every one can
    pause; only a body with a voice can identify itself, by sound, which is the one way
    strangers are allowed to ask for (`docs/passport/trust.md`)."""
    names = ["pause"]
    if any(verb.name == "say" for verb in manifest.verbs):
        names.append("identify")
    return names


def cannot_line(line: str) -> str:
    """A datasheet `cannot` line, cut to what a passport carries.

    The datasheets write a claim and then its explanation, `lift or hold more than about half a
    kilogram: a pen, an empty cup...`. A line too long for a passport keeps the claim, and a
    claim still too long is cut at a word."""
    if len(line) <= CANNOT_CHARS:
        return line
    claim = line.split(": ", 1)[0]
    if len(claim) <= CANNOT_CHARS:
        return claim
    return claim[: CANNOT_CHARS - 1].rsplit(" ", 1)[0] + "…"


def _figure(figure: Figure | None) -> list[Any] | None:
    if figure is None:  # not published, which a passport says by leaving it out
        return None
    source = figure.source if len(figure.source) <= CANNOT_CHARS else None
    return (
        [figure.value, figure.confidence, source] if source else [figure.value, figure.confidence]
    )


def _body(manifest: RobotManifest) -> dict[str, Any]:
    body: dict[str, Any] = {"embodiment": manifest.embodiment, "mobility": manifest.mobility}
    sheet = manifest.datasheet
    if sheet is not None:
        body["manipulator"] = sheet.manipulator
        if sheet.terrain is not None:
            body["terrain"] = sheet.terrain
        if sheet.tethered is not None:
            body["tethered"] = sheet.tethered
        for name in _FIGURES:
            figure = _figure(getattr(sheet, name))
            if figure is not None:
                body[name] = figure
    speeds = [manifest.limits[k] for k in ("max_vx", "max_vy") if manifest.limits.get(k)]
    if speeds:
        # what quackd clamps to, which is what another body sharing the floor needs: a
        # configured limit rather than a measured fact, so it carries no confidence
        body["max_speed_mps"] = max(speeds)
    if manifest.sensors:
        body["sensors"] = list(manifest.sensors)
    return body
