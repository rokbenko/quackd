"""What `watch` looks through: the one decision LLM that may be shown the camera, and the record
of every frame it is handed.

`quackd.verbs.watch` is the reflex and knows nothing of who answers it. This is the other half
of that seam (`watch.Eyes`), built by the loop on a run that asks with `--decision-frames`, and
only through the gate every frame quackd sends passes, `frames.may_send_frames`: quackd's own
client for OpenAI's Decisions API, built to be shown frames, at one of OpenAI's own hosts. Any
other decision LLM, a plugin or a server of your own included, gets no `DecisionEyes`, and a run
without one has no `watch` (ADR-0054).

Each look's frame goes the way the stepper's does (ADR-0053), with one more line in the record:

1. the picture is encoded off the loop by `frames.encode`, an RGB copy no longer than
   `frames.MAX_SIDE_PX` on its longest side, a JPEG at `frames.JPEG_QUALITY`, at
   `frames.DETAIL`, and skipped above `frames.MAX_BYTES` with nothing sent;
2. the JPEG is written beside the transcript as `sent/watch-NNNN.jpg`, and a disk that refuses
   it leaves no partial file behind and records `kept: false`;
3. a `watch_frame` line naming the file, its size and its sha256 goes into the transcript, which
   writes it through at once, so a process that dies with the request out still left a record
   of the frame it had handed over;
4. only then is the request handed to the client, bounded at the look's own `timeout_s`.

Requests are handed over at least `watch.MIN_PERIOD_S` apart on the wall's clock (`_pace`), as
looks are on the robot's: a simulator's clock runs only as fast as it is stepped, and the
promise that no more than two frames a second go out is the wall's.

Whatever then becomes of the request, an answer, a 429, a timeout, a cancel, the look's row
(`watch_tick`, which `watch` writes from a `finally`) carries what it cost and how long it took.
A frame handed to the client is counted as sent, though one whose connection was refused or
timed out first may never have reached OpenAI, and from here the two cannot be told apart.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from quackd.agent.decision.base import (
    DecisionFrame,
    SeeingDecisionLLM,
    UnreadableReply,
    client_timeout,
    state_text,
)
from quackd.agent.decision.frames import (
    DETAIL,
    JPEG_QUALITY,
    MAX_SIDE_PX,
    encode,
    may_send_frames,
    too_large,
    write_jpeg,
)
from quackd.agent.providers.pricing import cost_usd
from quackd.command import redacted_url
from quackd.transport.base import DEFAULT_CAMERA_NAME
from quackd.verbs.watch import MIN_PERIOD_S

if TYPE_CHECKING:
    from PIL import Image

    from quackd.agent.providers.catalogue import Price

SENT_DIR = "sent"
"""Where each frame `watch` hands to the client is kept, beside the transcript: its own directory,
as `images/` is for a task's pictures, since `frames/` is numbered by step and a look is not one."""


class FrameNotSent(Exception):
    """A look whose frame was not handed to the client, in quackd's own words, which the record
    keeps: a decision LLM the gate refused, or a picture too large to send."""


def keep_sent(path: Path, frame: DecisionFrame) -> bool:
    """Write the frame about to be handed to the client to `path`, and say whether the disk took
    it.

    False rather than an exception for a disk that refused, as `frames.keep` is, and with no
    partial file left behind: a truncated JPEG beside a record whose sha256 it does not match
    would be a copy that says it is the frame and is not."""
    try:
        write_jpeg(path, frame.data)
    except OSError:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return False
    return True


def _failure(error: BaseException, timeout_s: float, backstop: asyncio.Timeout | None) -> str:
    """What a look's row says about a request that ended without an answer, by the stepper's rule
    (`stepper._failure`): the type and its HTTP status, never the message, but for quackd's own
    words. Two of them here: the look's own bound, `decide(timeout_s=...)`, which raises the bare
    `TimeoutError` that nothing else in quackd's client raises, and the backstop around it."""
    from quackd.agent.decision.stepper import _failure as failure

    if backstop is not None and backstop.expired():
        return f"TimeoutError: no answer within {client_timeout(timeout_s):.1f} s"
    if type(error) is TimeoutError:
        return f"TimeoutError: no answer within {timeout_s:.1f} s"
    if isinstance(error, FrameNotSent | UnreadableReply) and (said := str(error)):
        return f"{type(error).__name__}: {said}"
    return failure(error)


class DecisionEyes:
    """`watch.Eyes` through a decision LLM that may be shown the camera, with the record of every
    frame it is handed and the bill for every look.

    Built by `eyes_for` alone, and refused here too for any decision LLM the gate refuses, so a
    library caller cannot hand a frame to a plugin either. The gate is asked again before every
    frame, as the stepper asks it before every turn."""

    def __init__(
        self,
        llm: Any,
        price: Price,
        *,
        emit: Callable[..., None],
        run_dir: Path,
    ) -> None:
        if not may_send_frames(llm):
            raise ValueError(
                f"{getattr(llm, 'name', 'this decision LLM')} may not be shown the camera: only "
                "quackd's own client for OpenAI's Decisions API, built with frames=True at "
                "OpenAI's own address, may be (frames.may_send_frames)"
            )
        self.llm = llm
        self.name: str = llm.name
        self.model: str = llm.model
        self.url: str | None = redacted_url(llm.url) if getattr(llm, "url", None) else None
        self.price = price
        self._emit = emit
        self.sent_dir = run_dir / SENT_DIR
        self._numbered = 0
        """The last number a file under `sent/` was given."""
        self.calls = 0
        self.looks = 0
        self.sent = 0
        """Frames handed to the client, each counted as it was, before any answer."""
        self.seen = 0
        self.errors = 0
        self.timeouts = 0
        self.refusals = 0
        self.stale = 0
        self.latency_s = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.estimated = False
        self.ended: Counter[str] = Counter()
        self._handed_at: float | None = None
        """When the newest request was handed to the client, on the wall's clock (`_pace`)."""

    # ── a call ──

    def begin(self) -> int:
        self.calls += 1
        return self.calls

    def end(self, ended: str) -> None:
        self.ended[ended] += 1

    # ── a look ──

    async def ask(
        self,
        image: Image.Image,
        state: Mapping[str, str],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        timeout_s: float,
        row: dict[str, Any],
    ) -> Any:
        """One look's request, with `image` beside `state`: encoded, kept, named in the record,
        counted, and only then handed to the client. Fills `row` with the frame before the request
        and with what the request cost and how long it took however it ended, and raises whatever
        ended it without an answer."""
        await self._pace()
        if not may_send_frames(self.llm):
            raise _not_sent(row, "the decision LLM may not be shown the camera")
        try:
            frame = await asyncio.to_thread(encode, image, DEFAULT_CAMERA_NAME)
        except Exception as e:
            raise _not_sent(row, f"the picture could not be encoded: {type(e).__name__}") from e
        if (too := too_large(frame)) is not None:
            raise _not_sent(row, too)
        self._numbered += 1
        name = f"watch-{self._numbered:04d}.jpg"
        path = self.sent_dir / name
        block = {**frame.record(), "path": f"{SENT_DIR}/{name}", "kept": keep_sent(path, frame)}
        try:
            # Written through before the request is handed over: a process that dies with the
            # request out has this line on disk naming the frame (`Transcript.write`).
            self._emit("watch_frame", call=self.calls, n=row.get("n"), frame=block)
        except BaseException:
            # no line, so nothing goes, and no copy is left that no line names
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            raise
        self.sent += 1
        row["frame"] = block
        chars = len(state_text(state)) + _question_chars(questions)
        result: Any = None
        failed: BaseException | None = None
        backstop: asyncio.Timeout | None = None
        started = time.perf_counter()
        self._handed_at = time.monotonic()
        try:
            # The look's own bound is the client's, `timeout_s` on the whole request. This one,
            # a whole bound later, can only end a request that outlived it (`base.client_timeout`).
            async with asyncio.timeout(client_timeout(timeout_s)) as backstop:
                seeing = cast(SeeingDecisionLLM, self.llm)
                result = await seeing.decide(state, questions, frames=(frame,), timeout_s=timeout_s)
            return result
        except BaseException as e:
            failed = e
            raise
        finally:
            took = round(time.perf_counter() - started, 3)
            self.latency_s += took
            row["latency_s"] = took
            try:
                row.update(self._bill(result if failed is None else None, chars))
            except Exception:
                row.update(self._bill(None, chars))
            if failed is not None:
                row["error"] = _failure(failed, timeout_s, backstop)

    async def _pace(self) -> None:
        """Hold a request back until `MIN_PERIOD_S` has passed on the wall's clock since the one
        before it was handed over. `watch` spaces its looks on the robot's clock, which on a robot
        is the wall's and makes this a wait of nothing, while a simulator's runs only as fast as
        it is stepped, and frames sent to OpenAI are counted on the wall's."""
        if self._handed_at is not None:
            wait = self._handed_at + MIN_PERIOD_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)

    def _bill(self, result: Any, chars: int) -> dict[str, Any]:
        """What a look asked for and what it cost: the count the API reported where it reported
        one, and otherwise the text sent at four characters to the token, marked as an estimate,
        as the stepper bills a turn (`Stepper._bill`). The picture is not in the estimate, because
        what one costs on this model is unpublished, so an estimated look is a low one."""
        from quackd.agent.decision.stepper import _usage

        measured, out_tokens = _usage(result) if result is not None else (None, 0)
        estimated = measured is None
        in_tokens = chars // 4 if measured is None else measured
        cost = cost_usd({"input_tokens": in_tokens, "output_tokens": out_tokens}, self.price)
        self.input_tokens += in_tokens
        self.output_tokens += out_tokens
        self.cost_usd += cost
        self.estimated = self.estimated or estimated
        return {
            "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
            "usage_estimated": estimated,
            "cost_usd": cost,
        }

    def tick(self, row: Mapping[str, Any]) -> None:
        """One look's row, `watch_tick`, counted into the summary."""
        self.looks += 1
        gate = row.get("gate")
        if gate == "seen":
            self.seen += 1
        elif gate in ("error", "unreadable"):
            self.errors += 1
            if str(row.get("error") or "").startswith("TimeoutError"):
                self.timeouts += 1
        elif gate == "refused":
            self.refusals += 1
        elif gate == "stale":
            self.stale += 1
        self._emit("watch_tick", call=self.calls, look=self.looks, **row)

    # ── the record ──

    def record(self) -> dict[str, Any]:
        """Who `watch` looks through and what each frame is, for `run_start.watch`."""
        return {
            "llm": self.name,
            "model": self.model,
            "url": self.url,
            "frame": {"max_side_px": MAX_SIDE_PX, "quality": JPEG_QUALITY, "detail": DETAIL},
            "kept_as": f"{SENT_DIR}/watch-NNNN.jpg",
        }

    def summary(self) -> dict[str, Any]:
        """The `watch` block of the summary's `decision` block: how many calls and looks, what each
        look came to, how each call ended, and what it all cost."""
        return {
            "calls": self.calls,
            "looks": self.looks,
            "sent": self.sent,
            "seen": self.seen,
            "errors": self.errors,
            "timeouts": self.timeouts,
            "refusals": self.refusals,
            "stale": self.stale,
            "latency_s": round(self.latency_s, 3),
            "usage": {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens},
            "cost_usd": round(self.cost_usd, 6),
            "cost_estimated": self.estimated,
            "ended": dict(self.ended),
        }


def _not_sent(row: dict[str, Any], why: str) -> FrameNotSent:
    """A look whose frame is not handed over, said on its row, and the exception that says so."""
    error = FrameNotSent(why)
    row["error"] = f"{type(error).__name__}: {why}"
    return error


def _question_chars(questions: Mapping[str, Any]) -> int:
    from quackd.agent.decision.stepper import _question_chars as measured

    return measured(questions)


def eyes_for(
    llm: Any,
    price: Price | None,
    *,
    frames: bool,
    emit: Callable[..., None],
    run_dir: Path,
) -> DecisionEyes | None:
    """The eyes a run looks through, or None: on a run that asked for frames, with a decision LLM
    the gate allows (`frames.may_send_frames`) and the rate it is costed at. Every other run has
    none, and `watch` is withdrawn from it."""
    if not frames or llm is None or price is None or not may_send_frames(llm):
        return None
    return DecisionEyes(llm, price, emit=emit, run_dir=run_dir)
