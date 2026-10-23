"""whois: which passport belongs to which robot in a camera's view, asked of an NVIDIA vision
model on Token Factory.

A signature proves who wrote a passport, never which body in the room holds its key. This
compares what a camera sees with the bodies the passports nearby declare, and says which fits
which, or that none does. It is advice for a person: it never pins, stamps or changes a tier,
which stay with keys and with what a person decided. The model sees every passport as closed
words and numbers under a label this module gives it, the view quackd gives a stranger, so
nothing a robot wrote about itself can steer the answer.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .nebius import NebiusError, Settings, ask, json_in

#: The longest side of the picture the model is sent, in pixels.
MAX_SIDE = 1024
CONFIDENCES = ("low", "medium", "high")
#: How much of a model's free text is kept for a person, per field.
KEPT = 200

SYSTEM = (
    "You look at one camera frame and a list of robot passports' declared bodies, each under a "
    "ref such as r1. For every robot you can see, say in a few words what it looks like and "
    "where it is in the frame, then which passport it fits, by ref, or null when none does. A "
    "body fits when its embodiment, mobility and manipulator agree with what you see; height "
    "and mass only help. Use only refs from the list. Do not guess: when unsure, say low. "
    "Answer with JSON only, in the shape asked for."
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "robots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "seen": {"type": "string"},
                    "where": {"type": "string"},
                    "match": {"type": ["string", "null"]},
                    "confidence": {"type": "string", "enum": list(CONFIDENCES)},
                    "why": {"type": "string"},
                },
                "required": ["seen", "where", "match", "confidence", "why"],
            },
        }
    },
    "required": ["robots"],
}


@dataclass(frozen=True)
class Match:
    """One robot the model said it saw, and the passport ref it fits, if any."""

    seen: str
    where: str
    ref: str | None
    confidence: str
    why: str
    dropped: str | None = None
    """Why a ref the model gave was not kept: it named no passport on the list."""


def body_of(reading: Any) -> dict[str, Any]:
    """A passport's declared body in closed words and numbers, nothing its writer worded."""
    from quackd.passport.document import CORE_SENSORS

    body = reading.body()
    view: dict[str, Any] = {}
    for member in ("embodiment", "mobility", "manipulator", "terrain"):
        if member in body:
            view[member] = body[member]
    for figure in ("height_m", "length_m", "width_m", "mass_kg"):
        if figure in body:
            view[figure] = body[figure]["value"]
    if "sensors" in body:
        view["sensors"] = [s for s in body["sensors"] if s in CORE_SENSORS]
    return view


def candidates(readings: list[Any]) -> dict[str, dict[str, Any]]:
    """Each passport's body under the ref the model will answer with: r1, r2, and so on."""
    return {f"r{i}": body_of(reading) for i, reading in enumerate(readings, 1)}


def jpeg(image: Any) -> bytes:
    """An OpenCV image as a JPEG no larger than `MAX_SIDE` on its longest side."""
    import cv2

    height, width = image.shape[:2]
    scale = MAX_SIDE / max(height, width)
    if scale < 1:
        image = cv2.resize(image, (int(width * scale), int(height * scale)))
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise NebiusError("the picture could not be encoded as a JPEG")
    return bytes(encoded.tobytes())


def from_file(path: Path) -> bytes:
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise NebiusError(f"{path} is not a picture this can read")
    return jpeg(image)


def from_camera(index: int) -> bytes:
    """One frame from a camera on this machine, after a few to let its exposure settle."""
    import cv2

    camera = cv2.VideoCapture(index)
    try:
        if not camera.isOpened():
            raise NebiusError(f"camera {index} would not open")
        frame = None
        for _ in range(5):
            ok, grabbed = camera.read()
            if ok:
                frame = grabbed
        if frame is None:
            raise NebiusError(f"camera {index} gave no frame")
        return jpeg(frame)
    finally:
        camera.release()


def messages(picture: bytes, refs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    listed = json.dumps(refs, ensure_ascii=True, indent=1)
    shape = json.dumps(SCHEMA["properties"]["robots"]["items"]["properties"], indent=1)
    url = "data:image/jpeg;base64," + base64.b64encode(picture).decode("ascii")
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Passports nearby, by ref:\n```json\n{listed}\n```\nAnswer as "
                    f'{{"robots": [...]}}, each robot with these fields:\n{shape}',
                },
                {"type": "image_url", "image_url": {"url": url}},
            ],
        },
    ]


def _text(value: Any) -> str:
    return str(value)[:KEPT] if value is not None else ""


def matches(answer: Any, refs: dict[str, dict[str, Any]]) -> list[Match]:
    """The model's answer, kept only as far as it holds: a ref not on the list becomes no match,
    and an unknown confidence becomes low."""
    robots = answer.get("robots") if isinstance(answer, dict) else None
    if not isinstance(robots, list):
        raise NebiusError("the model's answer has no list of robots")
    kept: list[Match] = []
    for robot in robots:
        if not isinstance(robot, dict):
            continue
        ref = robot.get("match")
        dropped = None
        if ref is not None and ref not in refs:
            dropped = f"named {_text(ref)!r}, which is no passport here"
            ref = None
        confidence = robot.get("confidence")
        kept.append(
            Match(
                seen=_text(robot.get("seen")),
                where=_text(robot.get("where")),
                ref=ref,
                confidence=confidence if confidence in CONFIDENCES else "low",
                why=_text(robot.get("why")),
                dropped=dropped,
            )
        )
    return kept


def whois(
    settings: Settings, model: str, picture: bytes, refs: dict[str, dict[str, Any]]
) -> list[Match]:
    text = ask(settings, model, messages(picture, refs), schema=SCHEMA, max_tokens=900)
    return matches(json_in(text), refs)


def lan_sources(beacons: list[Any]) -> list[str]:
    """What each robot announcing on this network says its passport is, as a source `explain`
    reads, with the short id its beacon claims as the promise the passport must keep."""
    sources = []
    for beacon in beacons:
        url = beacon.url()
        sources.append(f"{url}#rp={beacon.short_id}" if beacon.short_id else url)
    return sources
