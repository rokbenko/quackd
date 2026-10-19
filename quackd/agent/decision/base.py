"""What quackd asks of a decision LLM, and the three ways it can be absent.

One method. A decision LLM is handed the state quackd built and the questions quackd wrote,
and hands back answers; everything else -- which turns are a choice, what the state may
contain, how confident an answer must be before it moves a servo -- is `stepper.py`'s and
stays there whatever answers them.

One decision LLM may also be shown a camera frame beside the state, when a run asks for it
(`SeeingDecisionLLM`, `DecisionFrame`). Which one, and when, is `frames.py`'s question.

The errors are phrased like `providers/base.py`'s, deliberately: a reader who has met
`ProviderNotInstalled` should recognise its neighbour here without being told.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

TIMEOUT_S = 1.0
"""How long one turn may wait for an answer, whoever is answering.

A decision LLM that has not answered in a second is not worth waiting for: the point of it is
that it is quicker than the model, and past this the turn is cheaper spent on the model
directly. The turn escalates and the record says the call timed out.

Enforced at the seam, in `Stepper.advise`, rather than left to each backend. A vendor's client
has a timeout of its own with its own meaning -- TypeSafe's `RetryPolicy.timeout` is the budget
for the whole retry sequence and its client's own clock, ten seconds unless told otherwise, is on
each operation of a request -- a model in this process has none at all, and a plugin has
whatever its author thought of. One bound around `decide` is the only way the number quoted in
the docs is the number that holds.

A starting value, like the floors. It was chosen against Jev's published latency, and a
CPU-only server on your own machine may well need more; `--decision-mode shadow` is how you
find that out before it matters."""


def client_timeout(bound_s: float) -> float:
    """The clock a decision LLM's own client is given for a request that `bound_s` already bounds
    from outside: twice it.

    A second clock set to the same number as the first races it. The stepper's bound and an SDK's
    own timeout, both at `TIMEOUT_S`, come due within moments of each other, and the event loop
    runs the two in whichever turn it reaches them, so one request that ran out of its second was
    recorded at random as quackd's `no answer within 1.0 s` or as the SDK's own timeout type, both
    seen against the same slow server through OpenAI's SDK. A clock a whole budget later can only
    fire on a request the bound has already ended, so the bound is the one clock that decides a
    turn, and a turn that ran out is recorded one way. Doubling keeps the number derived from the
    budget rather than invented beside it, and keeps a clock there at all, of the same order, for
    a caller who calls `decide` with nothing around it."""
    return 2 * bound_s


def state_text(state: Mapping[str, str]) -> str:
    """The state as one JSON object in text, the way a decision LLM is shown it, and the one
    string every measurement of it is taken on.

    Its letters as letters (`ensure_ascii=False`). JSON's default writes every character outside
    ASCII as an escape, `ž` as `\\u017e`, which a JSON reader turns back into the letter and a
    model shown the text does not. OpenAI's `input` is plain text the API never decodes, so a goal
    in Slovenian reached `gpt-6-luna` as six-character escapes, and so did the dash in every `last`
    field, while the size the stepper recorded counted every escape as six characters too.
    Measured on this string, `state_chars` is the size of what OpenAI is sent, letter for letter,
    and the token estimate for every other row starts from the letters rather than the escapes."""
    return json.dumps(dict(state), ensure_ascii=False)


NO_KEY = "local"
"""What goes in the key field for a server that wants none, and for a keyed row reached at an
address that is not its vendor's. A word rather than nothing, because either SDK handed no key
goes and reads its own variable for one, and that is the hosted key this exists to keep at
home; and not the empty string, which TypeSafe's refuses. `local` is the word Kev's own example
uses. Here rather than in either client because both send it, for the same reason: a hosted key
sitting in somebody's `.env` never reaches a server on their own machine."""


class DecisionError(RuntimeError):
    """A decision LLM that cannot be built or cannot be reached. Raised while the CLI is still
    parsing, never mid-run: a turn that fails is a gate on the record, not an exception."""


class DecisionNotInstalled(DecisionError):
    def __init__(self, name: str, extra: str) -> None:
        super().__init__(
            f"--decision-llm {name} needs the optional extra quackd[{extra}] — "
            f'run: uvx --from "quackd[{extra}]" quackd ...  '
            f'or: uv pip install "quackd[{extra}]"'
        )


class DecisionMissingKey(DecisionError):
    def __init__(self, name: str, env_var: str) -> None:
        super().__init__(
            f"--decision-llm {name} needs {env_var} (set it in .env or the environment)"
        )


class UnreadableReply(ValueError):
    """A reply a backend could not read, said in quackd's own words, which the record keeps.

    A turn that fails is recorded by its exception's type and HTTP status and never by its
    message, because a message is the other side's text and a reply can quote the request it
    turned down (`stepper._failure`). That rule would also swallow the sentences quackd writes
    itself about the shape of a reply, which quote nothing: a page that was not JSON and a JSON
    object with no list of answers in it would both read as a bare `ValueError`, and a reader could
    not tell which of the two a server had sent. Raised with such a sentence, this is recorded with
    it. So the sentence has to describe the reply by its shape, the type of what came back, and
    never by what it said, because whatever is written here reaches the transcript.

    A `ValueError`, so whatever caught a reply-shape `ValueError` before still catches this. Not a
    `DecisionError`, which is raised while the CLI is still parsing: this is raised mid-run and is
    a turn on the record rather than a sentence before the robot connects."""


@runtime_checkable
class DecisionLLM(Protocol):
    """One decision LLM, however it is reached.

    `decide` is handed a flat mapping of short English strings -- the named state quackd built
    for this turn -- and a mapping of question name to a plain dict in the System One shape:
    `{"type": "choice" | "noul" | "score", "instructions": ..., "criteria": ...}`. Plain dicts
    rather than an SDK's own classes, so the same four questions go to a hosted API, a server
    on this machine and a model in this process without being rebuilt for each. A backend whose
    wire format is another shape translates them itself, both ways, and hands back answers in
    this one (`decisions_api.py`), so the stepper never learns which it was talking to.

    What comes back may be anything `stepper._answer` can read: an object carrying `answers`, or
    a mapping with an `"answers"` key, whose members carry `choice` and `confidence` (and
    optionally `probabilities`), or a bare `noul`, as attributes or as keys. A `choice` is the
    label as a string, and a `confidence` or a `noul` a number from 0 to 1, never a word or a
    flag: anything else, in `next_verb`, `done` or `need_human`, is an answer the stepper cannot
    read, and it hands the turn to the model on `unreadable` (`stepper._probability`). A `usage`
    with `input_tokens` is used where it is there and estimated where it is not, so a backend
    that counts nothing costs the run an estimate rather than an error.

    It may also carry `refused` beside `answers`, the same way: a list of the names of the
    questions it declined to answer rather than answered, each of which then answers nothing.
    Any name there hands the turn to the model on the gate `refused`, and is never read as a
    low probability or as a question that merely went missing (`stepper._refused`).

    Raising is allowed and is not fatal: `Stepper.advise` catches every `Exception`, records the
    turn as `gate: error` and hands it to the model. A decision LLM may never end a run. The
    record keeps the exception's type, and its HTTP status where it carries one, and never its
    message, which can quote what the server sent back, but for an `UnreadableReply`'s, which
    says what shape a reply had in the backend's own words and is kept. An interrupt is not an
    `Exception`, and a second Ctrl-C or a flock's cancel while a request is out ends the run as it
    would anywhere else, with that turn on the record as `interrupted`.
    """

    name: str
    """The preset that built it. What `--decision-llm` took and what the record says."""
    model: str
    """The id it answers as, for the record. Empty where the backend names none."""
    url: str | None
    """Where it was reached, for the record, or `None` where its SDK owns the address (`jev`
    at its default) and for one that runs in this process. Written through
    `command.redacted_url`, never raw."""

    async def decide(
        self, state: Mapping[str, str], questions: Mapping[str, Mapping[str, Any]]
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class DecisionFrame:
    """One camera frame as a decision LLM is shown it: the bytes that go, and what the record
    says about them.

    Made by `frames.encode` from the primary camera's picture, and handed to
    `SeeingDecisionLLM.decide` beside the state and never inside it. The bytes go two places,
    the API and one file beside the transcript, and the record keeps everything else about them
    (`record`): their size and their hash, never the bytes themselves."""

    camera: str
    """Which of the body's cameras took it, by the body's own name for it."""
    media_type: str
    """What the bytes are, `image/jpeg`, which is how the data URL names them."""
    data: bytes
    width: int
    height: int
    detail: str
    """How closely the API is asked to look: `low`, `high`, `auto` or `original`, by OpenAI's
    reference for an image part."""

    @property
    def sha256(self) -> str:
        """The hash of the bytes, so the file kept beside the transcript can be checked against
        the record of the turn that sent it. Worked out from the bytes on every read rather than
        stored beside them, so a frame cannot carry a hash of some other picture."""
        return hashlib.sha256(self.data).hexdigest()

    def data_url(self) -> str:
        """The bytes as an inline data URL, the one form OpenAI's API takes an image in: a link
        or a file id is refused there."""
        return f"data:{self.media_type};base64,{base64.b64encode(self.data).decode('ascii')}"

    def record(self) -> dict[str, Any]:
        """What the record keeps of it: every field but the bytes, and how many there were."""
        return {
            "camera": self.camera,
            "media_type": self.media_type,
            "width": self.width,
            "height": self.height,
            "bytes": len(self.data),
            "detail": self.detail,
            "sha256": self.sha256,
        }


@runtime_checkable
class SeeingDecisionLLM(DecisionLLM, Protocol):
    """A decision LLM that may be shown a camera frame beside the state.

    The same protocol with two things added: `frames`, whether it was built to be shown any, and
    a `decide` that takes them, keyword only, after the state and the questions. Every other row
    and every plugin keeps the two-argument `decide` and is never handed the keyword. Whether a
    frame goes at all is `frames.may_send_frames`'s answer, which is true of quackd's own client
    for OpenAI's Decisions API alone, built with `frames` true, at OpenAI's own address.

    `timeout_s`, where it is given, bounds the whole request, as `DecisionsAPILLM.decide` says.
    The stepper passes none, because its own bound around `decide` is the one clock that ends its
    turn (`TIMEOUT_S`)."""

    frames: bool
    """Whether it was built to be shown frames. A frame handed to one built without is refused
    before anything is sent."""

    async def decide(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        frames: Sequence[DecisionFrame] = (),
        timeout_s: float | None = None,
    ) -> Any: ...
