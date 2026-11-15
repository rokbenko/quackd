"""What a bridge can be asked about the body on the other side of it, and what that is worth.

`rosbridge` is a name for a transport, not for a robot: it says nothing about what the thing
weighs, how far it reaches or what it can climb. But the robot usually publishes its own
description, and a URDF does say how many joints move and what each link weighs. So this
module reads one, carefully, and turns the two things it can honestly claim into a datasheet:
a mass and a count of moving joints. Everything else stays unknown, payload included, because
a description says nothing about what a gripper can hold.

Nothing here touches the network or imports roslibpy. `parse_urdf` never raises: a description
that is not XML, not a robot, or half expanded from xacro comes back as a summary with the
trouble written into `errors`, because a robot that answered with nonsense is still a robot
that answered and the pilot deserves to be told which.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Literal

from roboglot.adapters.manifest import Datasheet, Figure

JOINT_TYPES = ("planar", "floating", "revolute", "continuous", "prismatic", "fixed")
"""The six a URDF may say, in upstream's own order (`upstream_api.URDF_JOINT_TYPE`)."""

MAX_URDF_BYTES = 8 * 1024 * 1024
"""Bigger than this is refused rather than parsed: the XML parser is stdlib and a bridge is
not a trusted peer. A humanoid's description is tens of kilobytes."""

MAX_JOINT_NOTES = 24
"""How many joints are listed one by one before the note says how many more there are."""


@dataclass(frozen=True)
class UrdfJoint:
    name: str
    type: str
    parent: str | None = None
    child: str | None = None
    lower: float | None = None
    upper: float | None = None
    velocity: float | None = None
    effort: float | None = None

    @property
    def moves(self) -> bool:
        return self.type != "fixed"

    def text(self) -> str:
        parts = [f"{self.name} ({self.type}"]
        if self.lower is not None and self.upper is not None:
            parts.append(f", {self.lower:g} to {self.upper:g}")
        if self.effort is not None:
            parts.append(f", effort {self.effort:g}")
        if self.velocity is not None:
            parts.append(f", velocity {self.velocity:g}")
        return "".join(parts) + ")"


@dataclass(frozen=True)
class UrdfSummary:
    """What one description said, and what went wrong reading it."""

    name: str | None = None
    links: int = 0
    links_with_mass: int = 0
    mass_kg: float | None = None
    joints: tuple[UrdfJoint, ...] = ()
    errors: tuple[str, ...] = ()
    parsed: bool = False
    """Whether this was a `<robot>` at all. False means nothing below is worth reading."""

    @property
    def dof(self) -> int | None:
        """Joints that move. None when there was no description to count them in."""
        return sum(1 for j in self.joints if j.moves) if self.parsed else None


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _tag(element: ET.Element) -> str:
    """The tag without its namespace: a URDF is usually bare, but xacro output may not be."""
    return element.tag.rsplit("}", 1)[-1]


def parse_urdf(xml_text: str) -> UrdfSummary:
    """Read a robot description. Never raises: trouble comes back in `errors`."""
    errors: list[str] = []
    if not xml_text.strip():
        return UrdfSummary(errors=("the description was empty",))
    if len(xml_text.encode("utf-8", "replace")) > MAX_URDF_BYTES:
        return UrdfSummary(errors=(f"the description is over {MAX_URDF_BYTES // 1024} KiB",))
    if "${" in xml_text or "<xacro:" in xml_text:
        errors.append("this looks like an unexpanded xacro, not a URDF; reading what is there")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        return UrdfSummary(errors=(*errors, f"not XML: {e}"))
    if _tag(root) != "robot":
        return UrdfSummary(errors=(*errors, f"the root is <{_tag(root)}>, not <robot>"))

    links = 0
    with_mass = 0
    total = 0.0
    joints: list[UrdfJoint] = []
    for child in root:
        tag = _tag(child)
        if tag == "link":
            links += 1
            inertial = next((e for e in child if _tag(e) == "inertial"), None)
            if inertial is None:
                continue  # optional per link, upstream says so
            mass = next((e for e in inertial if _tag(e) == "mass"), None)
            value = _number(mass.get("value")) if mass is not None else None
            if value is None:
                name = child.get("name") or "an unnamed link"
                errors.append(f"link {name}: no readable mass value")
                continue
            with_mass += 1
            total += value
        elif tag == "joint":
            kind = child.get("type") or ""
            if kind not in JOINT_TYPES:
                errors.append(f"joint {child.get('name') or '?'}: unknown type {kind!r}")
            limit = next((e for e in child if _tag(e) == "limit"), None)
            parent = next((e for e in child if _tag(e) == "parent"), None)
            kid = next((e for e in child if _tag(e) == "child"), None)
            joints.append(
                UrdfJoint(
                    name=child.get("name") or "?",
                    type=kind,
                    parent=parent.get("link") if parent is not None else None,
                    child=kid.get("link") if kid is not None else None,
                    lower=_number(limit.get("lower")) if limit is not None else None,
                    upper=_number(limit.get("upper")) if limit is not None else None,
                    velocity=_number(limit.get("velocity")) if limit is not None else None,
                    effort=_number(limit.get("effort")) if limit is not None else None,
                )
            )
    return UrdfSummary(
        name=root.get("name"),
        links=links,
        links_with_mass=with_mass,
        mass_kg=round(total, 6) if with_mass else None,
        joints=tuple(joints),
        errors=tuple(errors),
        parsed=True,
    )


Source = Literal["param", "topic", "mock"]


@dataclass(frozen=True)
class Introspection:
    """Everything one look at a bridge turned up."""

    topics: dict[str, str] = field(default_factory=dict)
    urdf_name: str | None = None
    mass_kg: float | None = None
    dof: int | None = None
    links: int = 0
    links_with_mass: int = 0
    joints: tuple[UrdfJoint, ...] = ()
    source: Source | None = None
    where: str | None = None
    errors: tuple[str, ...] = ()

    @property
    def discovered(self) -> bool:
        return bool(self.topics) or self.source is not None

    @classmethod
    def from_urdf(
        cls,
        summary: UrdfSummary | None,
        *,
        topics: dict[str, str] | None = None,
        source: Source | None = None,
        where: str | None = None,
        errors: tuple[str, ...] = (),
    ) -> Introspection:
        if summary is None:
            return cls(topics=dict(topics or {}), errors=errors)
        return cls(
            topics=dict(topics or {}),
            urdf_name=summary.name,
            mass_kg=summary.mass_kg,
            dof=summary.dof,
            links=summary.links,
            links_with_mass=summary.links_with_mass,
            joints=summary.joints,
            source=source if summary.parsed else None,
            where=where if summary.parsed else None,
            errors=(*errors, *summary.errors),
        )

    def origin(self) -> str:
        """Where the description came from, for a figure's source line."""
        if self.source == "mock":
            return "the mock's canned URDF"
        return f"the URDF on the bridge ({self.where})" if self.where else "the URDF on the bridge"

    def urdf_note(self) -> str | None:
        if self.source is None:
            return None
        name = f"URDF {self.urdf_name}" if self.urdf_name else "the URDF"
        moving = sum(1 for j in self.joints if j.moves)
        return (
            f"{name}: {self.links} links ({self.links_with_mass} with inertials), "
            f"{len(self.joints)} joints, {moving} of them moving, read from {self.origin()}"
        )

    def summary(self) -> str:
        """One line for the pilot."""
        if not self.discovered:
            return "nothing discovered on the bridge"
        parts = [f"{len(self.topics)} topics"]
        if self.source is not None:
            mass = f"{self.mass_kg:g} kg" if self.mass_kg is not None else "mass unknown"
            parts.append(
                f"a URDF ({self.urdf_name or 'unnamed'}): {mass}, {self.dof} moving joints"
            )
        else:
            parts.append("no description")
        if self.errors:
            trouble = "problem" if len(self.errors) == 1 else "problems"
            parts.append(f"{len(self.errors)} {trouble}")
        return ", ".join(parts)

    def payload(self) -> dict[str, Any]:
        """What the `introspect` verb hands back."""
        urdf = None
        if self.source is not None:
            urdf = {
                "name": self.urdf_name,
                "mass_kg": self.mass_kg,
                "dof": self.dof,
                "links": self.links,
                "links_with_mass": self.links_with_mass,
                "joints": [
                    {
                        "name": j.name,
                        "type": j.type,
                        "parent": j.parent,
                        "child": j.child,
                        "lower": j.lower,
                        "upper": j.upper,
                        "velocity": j.velocity,
                        "effort": j.effort,
                    }
                    for j in self.joints
                ],
            }
        return {
            "discovered": self.discovered,
            "source": self.source,
            "where": self.where,
            "topics": dict(self.topics),
            "urdf": urdf,
            "errors": list(self.errors),
        }


def datasheet_from_introspection(intro: Introspection | None, *, base: Datasheet) -> Datasheet:
    """The adapter's own sheet, plus the two things a description can honestly settle.

    `base` carries what is true of any base driven this way: no manipulator Roboglot can
    command, and the caveat that a bridge is software. A mass and a count of moving joints
    come from the robot's own file, so they are tagged official and sourced to it. Payload,
    reach and terrain stay unknown, because a URDF says nothing about them."""
    if intro is None or not intro.discovered:
        why = "; ".join(intro.errors) if intro is not None and intro.errors else "nothing answered"
        return base.model_copy(
            update={"notes": [*base.notes, f"nothing discovered on the bridge: {why}"]}
        )
    notes = list(base.notes)
    if (urdf := intro.urdf_note()) is not None:
        notes.append(urdf)
    moving = [j for j in intro.joints if j.moves]
    for joint in moving[:MAX_JOINT_NOTES]:
        notes.append(f"joint {joint.text()}")
    if len(moving) > MAX_JOINT_NOTES:
        notes.append(f"and {len(moving) - MAX_JOINT_NOTES} more joints not listed here")
    if any(word in j.name.lower() for j in intro.joints for word in ("gripper", "finger")):
        notes.append(
            "some joints are named like a gripper, but Roboglot commands a velocity on this "
            "adapter and nothing else, so no manipulator is claimed from a name"
        )
    notes.extend(intro.errors)
    update: dict[str, Any] = {"notes": notes}
    if intro.mass_kg is not None:
        update["mass_kg"] = Figure(
            value=intro.mass_kg,
            confidence="official",
            source=intro.origin(),
            note=f"the sum of {intro.links_with_mass} link inertials",
        )
    if intro.dof is not None:
        update["dof"] = Figure(
            value=float(intro.dof),
            confidence="official",
            source=intro.origin(),
            note="joints whose type is not fixed",
        )
    return base.model_copy(update=update)
