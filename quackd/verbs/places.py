"""The verbs that name places, list them and forget them. None of them moves the body.

A place is where the robot stood when somebody named it, read off the robot's own reading of
where it is (`quackd.space`), never from coordinates a pilot gives. These verbs exist on a body
whose transport says where it is (`quackd.transport.base.spatial_of`), and an adapter that declares
them leaves them out on a connection whose source says nothing.

Kept out of `quackd.verbs.core`, which carries them as core templates, so that the walk to a place
can use core's twist helpers without the two modules importing each other.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quackd.space.places import PLACE_NAME_MAX, PlaceView, place_listing
from quackd.space.pose import where_record, where_summary
from quackd.verbs.registry import Verb, VerbContext, VerbResult

NO_POSE = "this robot does not say where it is, so it has no places"


class MapParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cells_across: int = Field(
        default=40,
        ge=10,
        le=80,
        description="How many columns wide to draw the map, when the robot keeps one.",
    )


class MarkPlaceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        max_length=PLACE_NAME_MAX,
        description="What to call the spot the robot stands on now, e.g. 'sofa' or 'kitchen door'.",
    )
    note: str | None = Field(
        default=None, max_length=200, description="Optional: what is there, in a few words."
    )


class ForgetPlaceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=PLACE_NAME_MAX, description="The place.")


def view_record(v: PlaceView) -> dict[str, Any]:
    """One place as a verb's result carries it, for a pilot over MCP that reads data, not lines."""
    out: dict[str, Any] = {"name": v.place.name, "status": v.status, "kind": v.place.kind}
    if v.distance_m is not None and v.bearing_rad is not None:
        out["distance_m"] = round(v.distance_m, 3)
        out["bearing_rad"] = round(v.bearing_rad, 3)
    if v.reason:
        out["reason"] = v.reason
    if v.place.note:
        out["note"] = v.place.note
    return out


async def map_verb(ctx: VerbContext, p: MapParams) -> VerbResult:
    book = ctx.places
    if book is None:
        return VerbResult.fail(f"map: {NO_POSE}")
    fix = book.source.fix()
    record = where_record(fix)
    views = book.views()
    lines = [f"where: {where_summary(record)}, by {book.source.describe()}"]
    lines += ["places:", *place_listing(views)] if views else ["places: none marked yet"]
    grid = await book.source.grid() if book.source.has_grid else None
    if grid is not None:
        usable = [(v.place.name, v.place.x, v.place.y) for v in views if v.status == "here"]
        robot = fix.pose if fix.localized else None
        lines.append(grid.render_text(cells_across=p.cells_across, robot=robot, places=usable))
    else:
        lines.append(f"no map to draw: {book.source.describe()} keeps none, only a position")
    return VerbResult.success(
        "\n".join(lines), where=record, places=[view_record(v) for v in views]
    )


async def mark_place(ctx: VerbContext, p: MarkPlaceParams) -> VerbResult:
    book = ctx.places
    if book is None:
        return VerbResult.fail(f"mark_place: {NO_POSE}")
    try:
        place, old = book.mark(p.name, p.note)
    except ValueError as e:
        return VerbResult.fail(f"mark_place: {e}")
    said = (
        f"marked '{place.name}' where the robot stands, ({place.x:.2f}, {place.y:.2f}) by its "
        f"{'odometry' if place.kind == 'odometry' else 'map'}"
    )
    if old is not None:
        said += f"; it was at ({old.x:.2f}, {old.y:.2f}) before"
    if place.kind == "odometry":
        said += (
            ". Odometry drifts with every metre walked and begins again if the robot is picked "
            "up or falls, so the place is as good as the walk since it was marked"
        )
    return VerbResult.success(said, place=place.to_json())


async def forget_place(ctx: VerbContext, p: ForgetPlaceParams) -> VerbResult:
    book = ctx.places
    if book is None:
        return VerbResult.fail(f"forget_place: {NO_POSE}")
    old = book.store.forget(p.name)
    if old is None:
        known = ", ".join(place.name for place in book.store.places()) or "none"
        return VerbResult.fail(f"forget_place: no place called '{p.name}' (places: {known})")
    return VerbResult.success(f"forgot '{old.name}'", place=old.to_json())


PLACE_VERBS: dict[str, Verb] = {
    v.name: v
    for v in (
        Verb(
            "map",
            "Say where the robot is, by what, and every place it knows with how far and which "
            "way, or why it cannot be walked to now; with the map drawn when the robot keeps one.",
            map_verb,
            MapParams,
            timeout_s=10,
            read_only=True,
            core=True,
        ),
        Verb(
            "mark_place",
            "Name the spot the robot stands on now, so it can be found again. Marking a name "
            "it already has moves that place here. Moves nothing.",
            mark_place,
            MarkPlaceParams,
            timeout_s=5,
            core=True,
        ),
        Verb(
            "forget_place",
            "Forget a place by name. Moves nothing.",
            forget_place,
            ForgetPlaceParams,
            timeout_s=5,
            core=True,
        ),
    )
}
