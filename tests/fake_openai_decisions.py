"""A stand-in for OpenAI's Decisions API on 127.0.0.1, for the tests and for a keyless run by hand.

Standard library only, so it runs wherever the suite does. It records every request it is sent
(method, path, headers, body) beside what it answered, checks each `POST /v1/decisions` against
the request OpenAI's API reference documents, and answers it from a script in the shape that
reference documents: `{answers, model, usage}`, each answer naming the question it answers, a
`predicate` carrying its `probability`, a `choice` its `choice`, a `confidence` and a list of
`{value, probability}`, a `score` the same over its levels, and a `refusal` in place of any
question the script declines. A body the reference would not take is a 400 in OpenAI's error
shape, and any other path is a 404, which is how a stray `/v1/v1/decisions` shows up.

It proves what quackd puts on the wire through the real SDK, and what quackd makes of an answer
shaped like OpenAI's. It proves nothing about OpenAI, and nothing that passes against it has ever
reached `gpt-6-luna`.

By hand, from the repository root:

    python tests/fake_openai_decisions.py --port 8765 --record requests.jsonl --script answer

then point a run at `--decision-llm openai --decision-url http://127.0.0.1:8765`. An address
that is not OpenAI's own is sent the word `local` rather than a key, so it needs none, and the
run's stepper turns are costed at the self-hosted rate.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PATH = "/v1/decisions"
"""The one request it answers. quackd hands the SDK `<address>/v1` and the SDK appends
`/decisions`, so this is where a correct client lands and anything else is a 404."""

# ── what the reference says a request is ────────────────────────────────────────────────
#
# Read from OpenAI's API reference for `POST /v1/decisions` and from openai-python's request
# types (`decision_create_params.py` and the input types beside it) on 2026-10-08. Checked here
# more strictly than OpenAI promise to check it: a field the reference does not list is
# refused, because quackd sending one is a translation gone wrong, whatever the real API would
# make of it. What could not be read again is not checked at all. The reference was once read
# as allowing 2 to 255 options to a choice, each unique, and no source said so a second time
# (`CHOICE_BOUNDS` on docs/guides/decision-llms/openai.md), so a stand-in that refused 256
# would be asserting a rule nobody has seen.

FIELDS = frozenset({"model", "input", "questions", "safety_identifier"})
"""Every field a request may carry. `safety_identifier` is the one optional one, and quackd
never sends it."""

REQUIRED = ("model", "input", "questions")

QUESTION_FIELDS: dict[str, frozenset[str]] = {
    "predicate": frozenset({"type", "name", "instructions"}),
    "choice": frozenset({"type", "name", "instructions", "choices"}),
    "score": frozenset({"type", "name", "instructions", "levels"}),
}
"""The three question types and what each may carry. `name` is optional in the schema; the
guide asks for a unique one on every question, and the API echoes it on the answer."""

DETAILS = frozenset({"low", "high", "auto", "original"})
"""What an image's `detail` may say, besides leaving it out or sending null."""

MAX_IMAGES = 128
"""Images across every message of one request, the reference's own limit."""

MAX_SAFETY_IDENTIFIER = 128

# ── what an answer looks like ───────────────────────────────────────────────────────────

TOP = 0.95
"""The probability the stand-in puts on the option it picks, the figure of the guide's own
example answer."""

CONFIDENCE = 0.93
"""The `confidence` it gives that pick: below the top probability, as the guide's own example
has it (0.93 against 0.95). OpenAI do not define the field, and a reader that took the top
probability for it, or blended the two, would show here as 0.95 where 0.93 belongs."""

ERRORS: dict[int, tuple[str, str | None]] = {
    400: ("invalid_request_error", None),
    404: ("invalid_request_error", None),
    429: ("rate_limit_error", "slow_down"),
    503: ("service_unavailable_error", "server_is_overloaded"),
}
"""An error's `type` and `code` by status. The 429 and 503 rows are as OpenAI's error-code guide
lists them (read 2026-10-08), which documents no error of the Decisions API's own; the type for a
400 or a 404, and `api_error` for a status not here, are the stand-in's own words. The SDK picks
the exception it raises by the status alone (`openai/_client.py`, 3.22.1), so nothing a test
asserts rests on either."""

Reply = Callable[[dict[str, Any]], tuple[int, Any]]
"""How the stand-in answers one request: the request's body in, an HTTP status and what to send
back out. A `str` goes out as text rather than JSON, the way a gateway's error page would."""


def error(status: int, message: str, param: str | None = None) -> dict[str, Any]:
    """OpenAI's error object: `{"error": {message, type, param, code}}`."""
    kind, code = ERRORS.get(status, ("api_error", None))
    return {"error": {"message": message, "type": kind, "param": param, "code": code}}


def _input_problem(given: Any) -> tuple[str, str] | None:
    """Why the reference would not take this `input`, or None. A string, or a list of user
    messages whose content is a string or a list of `input_text` and `input_image` parts; an
    image is an inline data URL, and there are at most `MAX_IMAGES` of them."""
    if isinstance(given, str):
        return None
    if not isinstance(given, list):
        return "input", "'input' is neither a string nor a list of messages"
    images = 0
    for i, message in enumerate(given):
        where = f"input[{i}]"
        if not isinstance(message, dict) or set(message) - {"role", "content", "type"}:
            return where, f"{where} is not a message of role, content and type"
        if message.get("role") != "user":
            return f"{where}.role", "only a user message is accepted"
        if message.get("type", "message") != "message":
            return f"{where}.type", "a message's type is 'message'"
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            return f"{where}.content", "a message's content is a string or a list of parts"
        for j, part in enumerate(content):
            at = f"{where}.content[{j}]"
            kind = part.get("type") if isinstance(part, dict) else None
            if kind == "input_text" and set(part) == {"type", "text"}:
                if not isinstance(part["text"], str):
                    return f"{at}.text", "an input_text part's text is a string"
            elif kind == "input_image" and {"type", "image_url"} <= set(part) <= {
                "type",
                "image_url",
                "detail",
            }:
                if not str(part["image_url"]).startswith("data:"):
                    return f"{at}.image_url", "an image is an inline data URL, never a link"
                if part.get("detail") is not None and part["detail"] not in DETAILS:
                    return f"{at}.detail", f"detail is one of {', '.join(sorted(DETAILS))}"
                images += 1
            else:
                return at, f"{at} is neither an input_text nor an input_image part"
    if images > MAX_IMAGES:
        return "input", f"{images} images, and a request takes at most {MAX_IMAGES}"
    return None


def _options_problem(options: Any, key: str, where: str) -> tuple[str, str] | None:
    """A choice's `choices` or a score's `levels`: a list of `{key, description?}`, where a
    choice's value is a string or a boolean, those being distinct values, and a level's label
    is a string."""
    if not isinstance(options, list):
        return where, f"{where} is not a list"
    typed = key == "value"
    for i, option in enumerate(options):
        at = f"{where}[{i}]"
        if not isinstance(option, dict) or key not in option or set(option) - {key, "description"}:
            return at, f"{at} is not {{{key}, description}}"
        value = option[key]
        if not (isinstance(value, str) or (typed and isinstance(value, bool))):
            return (
                f"{at}.{key}",
                f"{at}.{key} is not a {'string or a boolean' if typed else 'string'}",
            )
        if not isinstance(option.get("description", ""), str):
            return f"{at}.description", f"{at}.description is not a string"
    return None


def _question_problem(question: Any, where: str) -> tuple[str, str] | None:
    """Why the reference would not take this question, or None."""
    kind = question.get("type") if isinstance(question, dict) else None
    if kind not in QUESTION_FIELDS:
        return f"{where}.type", f"{where} is not a predicate, a choice or a score"
    if extra := sorted(set(question) - QUESTION_FIELDS[kind]):
        return f"{where}.{extra[0]}", f"a {kind} carries no field {extra[0]!r}"
    if not isinstance(question.get("instructions"), str):
        return f"{where}.instructions", f"{where} needs its instructions as a string"
    if question.get("name") is not None and not isinstance(question["name"], str):
        return f"{where}.name", f"{where}'s name is a string or null"
    if kind == "choice":
        return _options_problem(question.get("choices"), "value", f"{where}.choices")
    if kind == "score":
        return _options_problem(question.get("levels"), "label", f"{where}.levels")
    return None


def problem(body: Any) -> tuple[str | None, str] | None:
    """Why the reference would not take `body`, as the field and a sentence, or None."""
    if not isinstance(body, dict):
        return None, "the body is not a JSON object"
    if unknown := sorted(set(body) - FIELDS):
        return unknown[0], f"the reference lists no field {unknown[0]!r}"
    for name in REQUIRED:
        if name not in body:
            return name, f"{name!r} is required"
    if not isinstance(body["model"], str):
        return "model", "'model' is not a string"
    identifier = body.get("safety_identifier")
    if identifier is not None and (
        not isinstance(identifier, str) or len(identifier) > MAX_SAFETY_IDENTIFIER
    ):
        return "safety_identifier", f"a string of at most {MAX_SAFETY_IDENTIFIER} characters"
    if (found := _input_problem(body["input"])) is not None:
        return found
    if not isinstance(body["questions"], list):
        return "questions", "'questions' is not a list"
    for i, question in enumerate(body["questions"]):
        if (found := _question_problem(question, f"questions[{i}]")) is not None:
            return found
    return None


# ── the answers a script can give ───────────────────────────────────────────────────────


def _spread(options: Sequence[Any], chosen: Any) -> list[tuple[Any, float]]:
    """`TOP` on the pick and the rest shared evenly over the others, in the order offered with
    the pick first. A pick that was not on offer is answered all the same, which is how a test
    scripts an answer no real model should give."""
    others = [option for option in options if option != chosen]
    if not others:
        return [(chosen, 1.0)]
    rest = round((1.0 - TOP) / len(others), 6)
    return [(chosen, TOP), *((option, rest) for option in others)]


def _answer(
    question: Mapping[str, Any], refuse: frozenset[str], picks: Mapping[str, Any]
) -> dict[str, Any]:
    """One question's answer, in the reference's shape for its type."""
    name = question.get("name")
    if name in refuse:
        return {"type": "refusal", "name": name}
    kind = question["type"]
    if kind == "predicate":
        return {"type": "predicate", "name": name, "probability": float(picks.get(name, 0.0))}
    if kind == "choice":
        values = [option["value"] for option in question["choices"]]
        spread = _spread(values, picks.get(name, values[0] if values else None))
        return {
            "type": "choice",
            "name": name,
            "choice": spread[0][0],
            "confidence": CONFIDENCE,
            "probabilities": [{"value": value, "probability": p} for value, p in spread],
        }
    labels = [level["label"] for level in question["levels"]]
    shares = dict(_spread(labels, picks.get(name, labels[0] if labels else None)))
    entries = [
        {"value": index, "label": label, "probability": shares.get(label, 0.0)}
        for index, label in enumerate(labels)
    ]
    return {
        "type": "score",
        "name": name,
        # the probability-weighted mean of the level indices, which is how the guide defines it
        "score": round(sum(e["value"] * e["probability"] for e in entries), 6),
        "confidence": CONFIDENCE,
        "probabilities": entries,
    }


def usage(body: Mapping[str, Any]) -> dict[str, Any]:
    """The reference's usage block, counting input and no output, because a decision bills only
    what it reads. The count is the stand-in's own, a quarter of the request body's characters,
    which is not the estimate quackd makes for itself, so a turn costed on it was costed on what
    the server said."""
    tokens = max(1, len(json.dumps(body)) // 4)
    return {
        "input_tokens": tokens,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": tokens,
    }


def answering(*, refuse: Iterable[str] = (), picks: Mapping[str, Any] | None = None) -> Reply:
    """Answer every question in the order asked, under the name it was asked by.

    A choice picks `picks[name]`, else its first option, which for quackd's `next_verb` is the
    first call on offer, `escalate` being last; a predicate says `picks[name]`, else 0.0; and
    every name in `refuse` is declined rather than answered."""
    declined = frozenset(refuse)
    chosen = dict(picks or {})

    def reply(body: dict[str, Any]) -> tuple[int, Any]:
        answers = [_answer(question, declined, chosen) for question in body["questions"]]
        return 200, {"answers": answers, "model": body["model"], "usage": usage(body)}

    return reply


def failing(status: int) -> Reply:
    """An error in OpenAI's shape, and no answer at all."""
    return lambda body: (status, error(status, f"the stand-in's script answers {status}"))


def quoting(status: int = 400) -> Reply:
    """An error whose message quotes the request it turned down, and carries a picture as a
    data URL besides. The SDK puts a reply's body in its exception's message, so this is what a
    record that kept that message would end up holding."""
    picture = "data:image/png;base64," + "iVBORw0KGgo" * 40

    def reply(body: dict[str, Any]) -> tuple[int, Any]:
        quoted = error(status, f"Invalid value for 'input': {json.dumps(body['input'])}", "input")
        quoted["error"]["image_url"] = picture
        return status, quoted

    return reply


def text(body: str = "<html><body>502 Bad Gateway</body></html>", status: int = 200) -> Reply:
    """A page that is not JSON, the way a proxy between a client and the API answers."""
    return lambda request: (status, body)


@dataclass(frozen=True)
class Moved:
    """What a redirect sends instead of a body: where the client is told to go."""

    location: str


def redirecting(location: str, status: int = 307) -> Reply:
    """A redirect to `location`, with no body. A 307 is the one that tells a client to send the
    same request again, body and all, to the new place, which is the worst a client that follows
    one could do with a request carrying the robot's state."""
    return lambda body: (status, Moved(location))


@dataclass(frozen=True)
class Paced:
    """An answer the stand-in takes its time over: `wait_s` before the status line, and then
    the body in `pieces`, `gap_s` apart, every piece after the headers that give its whole
    length."""

    reply: Any
    wait_s: float = 0.0
    pieces: int = 1
    gap_s: float = 0.0


def paced(reply: Reply, *, wait_s: float = 0.0, pieces: int = 1, gap_s: float = 0.0) -> Reply:
    """`reply`, sent slowly. A wait is a server that is slow to start answering. Pieces are one
    that answers a little at a time, each piece inside a client's limit for one read and the
    whole of it long past, which is the request a clock per phase never ends and only a bound on
    the whole request does."""

    def slow(body: dict[str, Any]) -> tuple[int, Any]:
        status, answer = reply(body)
        return status, Paced(answer, wait_s=wait_s, pieces=pieces, gap_s=gap_s)

    return slow


SCRIPTS: dict[str, list[Reply]] = {
    "answer": [answering()],
    "refuse": [answering(refuse=("need_human",))],
    "rate-limited": [failing(429)],
    "overloaded": [failing(503)],
    "quoting": [quoting()],
    "not-json": [text()],
}
"""Named scripts, for a run by hand. Request n gets the nth reply, and the last one repeats, so
a run of any length is answered."""


def folded(lines: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Header lines as one value a name, the values of a name sent more than once joined by a
    comma and a space in the order they were sent, which is how HTTP itself folds repeated field
    lines into one (RFC 9110, section 5.3)."""
    by_name: dict[str, str] = {}
    for name, value in lines:
        by_name[name] = f"{by_name[name]}, {value}" if name in by_name else value
    return by_name


@dataclass
class Recorded:
    method: str
    path: str
    """As sent, query and all, so a request that carried one shows it."""
    headers: dict[str, str]
    """Every header, its name lowercased, and a name sent twice under both of its values
    (`folded`). It used to keep the last value alone, so a second `Authorization` sent before
    the expected one left no trace here, and an assertion that the header said `Bearer local`
    passed beside a gateway's credential. Folded, the same assertion fails whichever line came
    first. `lines` keeps every line apart."""
    body: Any
    status: int
    reply: Any
    """What the stand-in sent back: the decoded JSON, or the text, or where a redirect
    pointed."""
    lines: list[tuple[str, str]] = field(default_factory=list)
    """Every header line in the order it arrived, its name lowercased, a name sent twice
    appearing twice: the one place a second `Authorization` beside the first would show."""

    def every(self, name: str) -> list[str]:
        """Every value sent under `name`, in any spelling, in the order sent."""
        return [value for sent, value in self.lines if sent == name.lower()]


@dataclass
class FakeOpenAIDecisions:
    """The stand-in, as a context manager that serves on a free port in a daemon thread."""

    script: str | Sequence[Reply] = "answer"
    record: Path | None = None
    port: int = 0
    """0 picks a free one, which is what a test wants."""
    requests: list[Recorded] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._replies = list(SCRIPTS[self.script] if isinstance(self.script, str) else self.script)
        if not self._replies:
            raise ValueError("a script needs at least one reply")
        self._answered = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        """The address alone, which is what `--decision-url` takes: quackd adds `/v1/decisions`."""
        assert self._server is not None, "start the stand-in first"
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def posts(self) -> list[Recorded]:
        return [r for r in self.requests if r.method == "POST"]

    def _answer(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        if method == "POST" and path.split("?", 1)[0] == PATH:
            if (found := problem(body)) is not None:
                param, message = found
                return 400, error(400, f"the stand-in refused the request: {message}", param)
            with self._lock:
                self._answered += 1
                n = self._answered
            return self._replies[min(n, len(self._replies)) - 1](body)
        return 404, error(404, f"the stand-in has no {method} {path}")

    def __enter__(self) -> FakeOpenAIDecisions:
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = raw.decode("utf-8", "replace")
                status, reply = stand_in._answer(self.command, self.path, body)
                pace = reply if isinstance(reply, Paced) else Paced(reply)
                reply = pace.reply
                lines = [(k.lower(), v) for k, v in self.headers.items()]
                seen = Recorded(
                    self.command,
                    self.path,
                    folded(lines),
                    body,
                    status,
                    {"location": reply.location} if isinstance(reply, Moved) else reply,
                    lines,
                )
                with stand_in._lock:
                    stand_in.requests.append(seen)
                    if stand_in.record is not None:
                        with stand_in.record.open("a", encoding="utf-8") as f:
                            f.write(json.dumps(seen.__dict__) + "\n")
                time.sleep(pace.wait_s)
                self.send_response(status)
                if isinstance(reply, Moved):
                    self.send_header("Location", reply.location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if isinstance(reply, str):
                    payload, kind = reply.encode("utf-8"), "text/html; charset=utf-8"
                else:
                    payload, kind = json.dumps(reply).encode("utf-8"), "application/json"
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                step = max(1, -(-len(payload) // max(1, pace.pieces)))
                try:
                    self.end_headers()
                    for start in range(0, len(payload), step):
                        if start:
                            time.sleep(pace.gap_s)
                        self.wfile.write(payload[start : start + step])
                except OSError:
                    # A client that gave up on a slow answer has closed the connection, which is
                    # what a test of the client's bound wants to happen, and not the stand-in's
                    # error to report.
                    return

            do_GET = do_POST = _serve

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


async def send_once_through_the_sdk(spec: Any = None) -> Recorded:
    """One request through the real `openai` SDK, on a client built the way a run builds one for
    `spec` (the `openai` row when None), to a stand-in of its own on 127.0.0.1, and what that
    stand-in recorded. It needs no key and reaches nothing but this machine.

    For whatever the SDK and the HTTP stack under it do once in a process, which a test that
    times a request, or gives it only the stepper's second, must not pay inside it. Building the
    client takes care of the SDK's look at its platform (`decisions_api.warm_platform`). What is
    left happens on the first request a process sends, whichever client sends it: with the
    locked 3.22.1, anyio choosing its event-loop backend and importing it, which is an import
    from disk inside the request, and whatever else a release leaves for its first request. On a
    quick machine that is a small part of a second; on a slow one, or one busy at the wrong
    moment, it is as long as the disk takes, and a turn has a second. A request sent here first
    leaves a later one only its own connection to open.

    The one function in this file that imports quackd, and lazily, so the stand-in still runs
    by hand on the standard library alone."""
    from quackd.agent.decision.catalogue import PRESETS
    from quackd.agent.decision.factory import make_decision_llm

    with FakeOpenAIDecisions() as stand_in:
        llm = make_decision_llm(spec or PRESETS["openai"], url=stand_in.url)
        await llm.decide(
            {"goal": "nothing: this request warms the SDK"},
            {"done": {"type": "noul", "instructions": "Is it done?"}},
        )
    (sent,) = stand_in.posts()
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--record", type=Path, default=None, help="append each request here")
    parser.add_argument("--script", choices=sorted(SCRIPTS), default="answer")
    args = parser.parse_args()
    stand_in = FakeOpenAIDecisions(script=args.script, record=args.record, port=args.port)
    with stand_in:
        print(
            f"stand-in for OpenAI's Decisions API at {stand_in.url} (Ctrl+C stops it)", flush=True
        )
        with contextlib.suppress(KeyboardInterrupt):
            threading.Event().wait()


if __name__ == "__main__":
    main()
