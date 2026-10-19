"""Two ways to answer `watch` without a model: at its seam, and where OpenAI's client would be.

`ScriptedEyes` stands where `quackd.agent.decision.eyes.DecisionEyes` stands (`watch.Eyes`) and
answers from a script, which is how a test puts `watch` through one exit at a time on a body that
does exactly what it is told. It records every picture, state and question it is handed.

`BlobEyes` stands where `openai.AsyncOpenAI` stands, behind quackd's own client and its own
eyes, so everything between the verb and the wire runs as it does on a real run: the encoding,
the copy kept, the line in the record, the translation out and back. It decodes the JPEG each
request carries and finds the orange ball in it with quackd's own `ColorBlobDetector`, and
answers where it is as a distribution over the sectors `watch` asks about, so a test can close
the loop on a simulator and read the world's own truth after it. It is a far better perceiver
than any model: what it proves is that the frame sent is the one the head was pointing at, upright
and fresh, and that the head follows what the frame shows, and nothing about how well a decision
LLM sees.
"""

from __future__ import annotations

import asyncio
import base64
import io
import itertools
import random
import time
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from PIL import Image

from quackd.perception.color_blob import ColorBlobDetector
from quackd.verbs.watch import ACROSS, HEIGHT, OUT_OF_VIEW, UP_DOWN, WHERE
from tests import fake_decisions_api as fake_api

# ── answers in the shape `DecisionsAPILLM.decide` hands back ────────────────────────────


def distribution(at: float | None, centres: Mapping[str, float], *, seen: float = 0.9) -> dict:
    """Probabilities over `centres` and `out_of_view` for a thing at `at`, a share of the picture
    from its middle, or out of view at None. `seen` goes to the two sectors either side of `at`,
    split by how near each is, so the sectors' mean is `at` itself, and the rest to out of view."""
    labels = list(centres)
    spread = {label: 0.0 for label in labels}
    if at is None:
        share = (1.0 - seen) / len(labels)
        return {**dict.fromkeys(labels, share), OUT_OF_VIEW: seen}
    points = sorted(centres.items(), key=lambda item: item[1])
    low, high = points[0], points[-1]
    if at <= low[1]:
        spread[low[0]] = seen
    elif at >= high[1]:
        spread[high[0]] = seen
    else:
        for (left, a), (right, b) in itertools.pairwise(points):
            if a <= at <= b:
                w = (at - a) / (b - a)
                spread[left] = seen * (1 - w)
                spread[right] = seen * w
                break
    return {**spread, OUT_OF_VIEW: 1.0 - seen}


def answer(spread: Mapping[str, float], *, confidence: float | None = None) -> dict[str, Any]:
    """One `choice` answer as the client hands it back, its pick the likeliest label."""
    pick = max(spread, key=lambda label: spread[label])
    return {
        "choice": pick,
        "confidence": confidence if confidence is not None else spread[pick],
        "probabilities": dict(spread),
    }


def result(
    where: Mapping[str, float] | None,
    height: Mapping[str, float] | None = None,
    *,
    refused: tuple[str, ...] = (),
) -> dict[str, Any]:
    """A whole look's result, as `decisions_api.to_result` builds one."""
    answers: dict[str, Any] = {}
    if where is not None:
        answers[WHERE] = answer(where)
    if height is not None:
        answers[HEIGHT] = answer(height)
    return {"answers": answers, "refused": list(refused), "model": "gpt-6-luna", "usage": None}


def seen_at(x: float, y: float | None = None) -> dict[str, Any]:
    """A look that saw the thing at `x` across, and at `y` down where a height was asked."""
    return result(
        distribution(x, ACROSS),
        distribution(y, UP_DOWN) if y is not None else None,
    )


def out_of_view() -> dict[str, Any]:
    return result(distribution(None, ACROSS), distribution(None, UP_DOWN))


def unsure() -> dict[str, Any]:
    """Half on the far left and half on the far right: a spread no head can follow."""
    spread = {label: 0.0 for label in ACROSS}
    spread["far_left"], spread["far_right"], spread[OUT_OF_VIEW] = 0.45, 0.45, 0.1
    return result(spread)


# ── at the seam ─────────────────────────────────────────────────────────────────────────


class ScriptedEyes:
    """`watch.Eyes` from a script. Each item is a result to hand back, an exception to raise, a
    number of seconds to wait on the wall first as `(seconds, item)`, or a function of the picture
    that makes one. Past the end, `default` answers. Every frame is counted as sent, its row given
    a frame block, before the item is read, as the real eyes count one before the request."""

    name = "scripted"
    model = "scripted"

    def __init__(self, script: list[Any] | None = None, *, default: Any = None) -> None:
        self.script = list(script or [])
        self.default = default if default is not None else out_of_view()
        self.images: list[Image.Image] = []
        self.states: list[dict[str, str]] = []
        self.questions: list[dict[str, Any]] = []
        self.timeouts: list[float] = []
        self.rows: list[dict[str, Any]] = []
        self.calls = 0
        self.sent = 0
        self.ended: Counter[str] = Counter()
        self.on_ask: Callable[[], None] | None = None

    def begin(self) -> int:
        self.calls += 1
        return self.calls

    def end(self, ended: str) -> None:
        self.ended[ended] += 1

    async def ask(
        self,
        image: Image.Image,
        state: Mapping[str, str],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        timeout_s: float,
        row: dict[str, Any],
    ) -> Any:
        self.images.append(image)
        self.states.append(dict(state))
        self.questions.append({name: dict(q) for name, q in questions.items()})
        self.timeouts.append(timeout_s)
        self.sent += 1
        row["frame"] = {"path": f"sent/watch-{self.sent:04d}.jpg", "kept": True}
        if self.on_ask is not None:
            self.on_ask()
        item = self.script.pop(0) if self.script else self.default
        if callable(item) and not isinstance(item, BaseException):
            item = item(image)
        started = time.perf_counter()
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], int | float):
            seconds, item = item
            await asyncio.sleep(seconds)
        row["latency_s"] = round(time.perf_counter() - started, 3)
        if isinstance(item, BaseException):
            row["error"] = type(item).__name__
            raise item
        return item

    def tick(self, row: Mapping[str, Any]) -> None:
        self.rows.append(dict(row))


# ── where OpenAI's client would be ──────────────────────────────────────────────────────


def picture_of(body: Mapping[str, Any]) -> Image.Image:
    """The JPEG a `POST /v1/decisions` body carries, decoded: the `input_image` part's data URL
    in the one user message, the shape `decisions_api.to_input` builds."""
    (message,) = body["input"]
    (image,) = [part for part in message["content"] if part["type"] == "input_image"]
    url = str(image["image_url"])
    head, _, data = url.partition(",")
    assert head == "data:image/jpeg;base64", head
    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")


class BlobEyes(fake_api.FakeOpenAI):
    """OpenAI's client, answering where the orange ball is by finding it in the JPEG it was handed
    with quackd's own `ColorBlobDetector`: 0.9 on the sectors either side of where the ball's
    middle is, split so their mean is that place, give or take `noise`, and 0.9 on `out_of_view`
    when there is no ball. `delay_s` holds each answer back on the wall's clock, and `on_post` is
    called with each request's body before it is answered, where a test can look at the run as
    it stood the moment the request was handed over."""

    def __init__(
        self,
        *,
        noise: float = 0.02,
        seed: int = 0,
        delay_s: float = 0.0,
        on_post: Callable[[Mapping[str, Any]], None] | None = None,
        hold: asyncio.Event | None = None,
    ) -> None:
        super().__init__()
        self.detector = ColorBlobDetector()
        self.noise = noise
        self.rng = random.Random(seed)
        self.delay_s = delay_s
        self.on_post = on_post
        self.hold = hold
        self.images: list[Image.Image] = []
        self.truth: list[tuple[float, float] | None] = []
        """Where the ball's middle was in each picture, as `watch` measures it, or None."""

    def _where(self, image: Image.Image) -> tuple[float, float] | None:
        balls = [d for d in self.detector.detect(image) if d.label == "ball"]
        if not balls:
            return None
        return balls[0].cx - 0.5, balls[0].cy - 0.5

    async def _send(self, path: str, cast_to: Any, body: Any, options: dict[str, Any]) -> Any:
        self.posts.append({"path": path, "cast_to": cast_to, "body": body, "options": options})
        if self.on_post is not None:
            self.on_post(body)
        if self.hold is not None:
            await self.hold.wait()
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if WHERE not in {question["name"] for question in body["questions"]}:
            # the stepper's own turn, on a run that has one beside `watch`: handed back
            return fake_api.decision(fake_api.turn("escalate"))
        image = picture_of(body)
        self.images.append(image)
        found = self._where(image)
        self.truth.append(found)
        answers = []
        for question in body["questions"]:
            name = question["name"]
            centres = ACROSS if name == WHERE else UP_DOWN
            at = None
            if found is not None:
                at = (found[0] if name == WHERE else found[1]) + self.rng.gauss(0.0, self.noise)
            spread = distribution(at, centres)
            pick = max(spread, key=lambda label: spread[label])
            answers.append(
                fake_api.choice(
                    name,
                    pick,
                    spread[pick],
                    {label: p for label, p in spread.items() if label != pick},
                )
            )
        return fake_api.decision(answers)
