"""What a run says once about its decision LLM before anything moves, in the words the docs
quote: `quackd run` says it, and so does `quackd drive` for the goals it flies with a stepper."""

from __future__ import annotations

from quackd.agent.decision.frames import MAX_SIDE_PX as DECISION_FRAME_PX
from quackd.command import redacted_url

MODE_ON_UNMEASURED = (
    "--decision-mode on has not been measured against a real robot: no latency, no agreement "
    "rate, and the figures in docs/guides/decision-llms/README.md are estimates, of speed and "
    "cost for jev at TypeSafe, of cost alone for openai at OpenAI, and of neither for a server "
    "you run. Two of the four confidence floors are published by TypeSafe and two are quackd's "
    "own, all four shaped around Jev. --decision-mode shadow records both and changes nothing "
    "about the run."
)
"""What `--decision-mode on` says once, where the person switching it on is looking.

It says what the hub's section on speed and cost says, row by row, because a sentence on the
terminal is read by people who never open the page. It once said the hub had estimates for Jev
and nothing at all for anything else, after the hub had begun working out what an `openai` turn
costs: no latency, since OpenAI publish only a ratio to their own Responses API, and a rate, which
is a cost estimate. A server you run, `jev` and `openai` at an address of your own among them, is
costed at the self-hosted $0 and has no latency anybody has measured, so neither half applies to
it. `tests/test_docs.py` holds the sentence to the rows that carry a published rate, so the next
row given one fails there until this names it."""


def frames_notice(mode: str, url: str | None, *, watch: bool = False) -> str:
    """What `--decision-frames` says once before the robot connects: where a frame goes, and on a
    run whose mode asks the stepper nothing, that the stepper sends none. `watch` is whether the
    task lets the pilot call `watch` on a body that declares it, which sends a frame a look
    whatever the mode, and then the line says so. The address is the client's own, redacted as the
    record writes it."""
    # Never the bare word as a fallback: a string that is exactly one of the SDK's client class
    # names reads to `tests/test_openai_headers.py`'s guard as a way of reaching the SDK.
    where = f" at {redacted_url(url)}" if url else ""
    watching = (
        f"watch, where the robot offers it, sends OpenAI{where} one such frame a look while it "
        "runs, and the run keeps each one as sent/watch-NNNN.jpg"
    )
    if mode == "off" and watch:
        return (
            "--decision-frames: the stepper sends no camera frame, because --decision-mode off "
            f"asks it nothing, and {watching}"
        )
    if mode == "off":
        return (
            "--decision-frames: no camera frame goes to OpenAI on this run, because "
            "--decision-mode off asks the stepper nothing"
        )
    said = (
        f"--decision-frames: the robot's primary camera goes to OpenAI{where} with every turn "
        f"the stepper asks about, as one JPEG at most {DECISION_FRAME_PX} pixels on its longest "
        "side, and the run keeps each one it sends as frames/NNNN-decision.jpg"
    )
    return f"{said}, and {watching}" if watch else said
