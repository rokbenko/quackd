"""OpenAI's Decisions API, behind `--decision-llm openai`: the second wire format quackd speaks.

Every other decision LLM quackd names that sits behind a server speaks System One, which is why
they share `systemone.py` and differ only in their rows. OpenAI's speaks its own.
`POST /v1/decisions` asks the same kind of question -- typed questions about a piece of text,
answered with probabilities rather than prose -- in another shape: the questions go as a list
rather than a mapping, a yes/no question is a `predicate` rather than a `noul`, a choice's
options are a list of values rather than a mapping of label to description, and the answers come
back as a list in which each one names the question it answers. So this is a second transport
rather than a second address, and this module is a translation both ways around one HTTP call.
The stepper above it asks the same four questions it asks everybody and reads the answers the
way it reads everybody's.

The call is the SDK's own generic request, `client.post("/decisions", cast_to=object, ...)`,
rather than its `decisions` resource. That resource arrived in openai 3.26.0 and the lock
carries 3.22.1, so reaching it would mean raising the floor in every extra that installs the
SDK, and making everybody who pilots with an OpenAI-shaped vendor upgrade for a stepper they may
never name. `cast_to=object` hands back the decoded JSON body untouched, which is what a
translator wants anyway. That request, that pass-through, the `Omit` marker and the
`DefaultAsyncHttpxClient` the client is built on are all in 1.66.0, the floor, as well as in
3.22.1 (read 2026-10-09 and 2026-10-10). What a release does with the headers it is handed is
another matter, and is not the same across that range, so nothing below rests on any one
release's way of doing it (`request_headers` says how they differ).

Where the key goes is decided by `is_openai_host` and by nothing else. `OPENAI_API_KEY` is sent
to OpenAI's own API, at one of the hosts OpenAI document as serving this endpoint, over https,
and every other address is sent `NO_KEY`, the word every server you run is sent:
`--decision-url http://127.0.0.1:8765` is a server that speaks this format on your own machine,
and a company's key has no business there. The SDK reads more than the key out of the
environment, and every piece of it is answered here rather than left to its defaults:

- `OPENAI_BASE_URL`, which it follows whenever it is handed no address. quackd's own local
  presets read the same variable as the address of a model server on your own machine
  (`providers/local.py`, and the address order in `docs/guides/local-llms.md`), so it may well
  be set on the very machine that names this row, and the hosted key would follow it there. So
  an address is handed over on every build, the one the row or the flag says, with `/v1` on the
  end because the SDK appends paths to it.
- `OPENAI_CUSTOM_HEADERS`, whose lines it adds to every request, in the releases that read it.
  That is where a gateway's own credential lives for somebody whose pilot goes through one, and
  it is that gateway's, not OpenAI's and not your server's. So every header it names is answered
  here, read the way the SDK reads it: an authorization line, in whatever spelling, with quackd's
  own value under that very spelling, a line at OpenAI that names an account header below with
  what the SDK would have sent under that name, and every other line with nothing. Once the
  client is built its headers are read back, and one that would still carry an authorization
  quackd did not write stops the build, naming the release.
- `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, which it sends as headers. They are identifiers
  rather than secrets, and they are OpenAI's, so they go to OpenAI, as the SDK would send them
  there, and are omitted everywhere else.

And one thing it does with no variable at all: it follows redirects. Followed, a redirect from
anything that answers for the address would carry the account headers, and on a 307 or a 308
the whole request with the robot's state in it, to wherever it pointed, plain http included.
No answer OpenAI document for `POST /v1/decisions` is a redirect, so the client is built to
follow none, and a redirect is a turn that failed.

Two more are a matter of time rather than of where things go. The SDK's own timeout is a limit
on each phase of a request rather than on the whole of it, so the clock that ends a stepper turn
is the stepper's, and the client's sits above it (`base.client_timeout`). And before its first
request the SDK asks the operating system which platform it is running on, which can take a
good part of a second; the client asks it while it is built instead (`warm_platform`), so no
turn ever waits for it.

Imported only when a run has named this row. Nothing at module scope touches the SDK, and the
address rules are the standard library alone, so `factory` asks them on a machine without it.
"""

from __future__ import annotations

import asyncio
import importlib
import math
import os
import platform
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import urlsplit, urlunsplit

from quackd.agent.decision.base import (
    NO_KEY,
    TIMEOUT_S,
    DecisionError,
    DecisionMissingKey,
    DecisionNotInstalled,
    client_timeout,
    state_text,
)
from quackd.command import readable_url, redacted_url

if TYPE_CHECKING:
    from quackd.agent.decision.catalogue import DecisionSpec

SDK = "openai"
"""The client's import name, which is the pilot's own SDK and the row's `sdk`."""

API_PREFIX = "/v1"
"""What the SDK expects on the end of its address. It appends a request's path to whatever it
was given after making sure that ends in a slash, so `https://api.openai.com/v1` and `PATH` reach
`/v1/decisions`, and an address typed with `/v1` already on it would reach `/v1/v1/decisions`,
which is why `check_address` refuses one."""

PATH = "/decisions"
"""The request, relative to `API_PREFIX`: the path the SDK's own `decisions` resource posts to
in the releases that have one."""

OPENAI_HOSTS = frozenset(
    {
        "api.openai.com",
        "us.api.openai.com",
        "eu.api.openai.com",
        "au.api.openai.com",
        "ca.api.openai.com",
        "jp.api.openai.com",
        "in.api.openai.com",
        "sg.api.openai.com",
        "kr.api.openai.com",
        "gb.api.openai.com",
        "ae.api.openai.com",
    }
)
"""Where `OPENAI_API_KEY` may go: OpenAI's global API, which the Decisions guide's own examples
call, and the regional hosts OpenAI's data-controls page lists
(https://developers.openai.com/api/docs/guides/your-data, read 2026-10-10). That page's table of
data-residency regions names each of these hosts and lists `/v1/decisions` among the endpoints
each of them stores in its region, the page says "The Decisions API is available in all
supported API regions", and it tells anyone whose project has data residency configured to add
that region's prefix to every request. So a host left out here is a project that could never
use this row, and a host is here only because OpenAI name it, never because it fits the pattern
the others share.

The SDK's own `data_residency` choice names four of them (`openai/_data_residency.py`, 3.22.1,
read 2026-10-09), and OpenAI say inference runs in the region only in the United States and
Europe: "Data residency and regional processing are supported in the United States and Europe
(EEA + Switzerland)", says the guide, and "Availability in a region does not imply that
inference is performed in that region", says the data-controls page. That is where OpenAI run
the model, which is theirs to decide. Where the key goes is quackd's, and every host here is
OpenAI's own.

A host is matched whole and in lower case (`OPENAI_AUTHORITIES`), so
`api.openai.com.evil.example` is not one and neither is `api.openai.com.` with its trailing
dot."""

OPENAI_AUTHORITIES = frozenset({*OPENAI_HOSTS, *(f"{host}:443" for host in OPENAI_HOSTS)})
"""Every way an address's authority may spell one of `OPENAI_HOSTS`: the host alone, or with
the port https uses anyway. `is_openai_host` compares the whole authority with these, and
anything else in it, a user, another port or a character the host has no place for, makes it
somebody else's."""

MAX_RETRIES = 0
"""None. The SDK's default is two, and the first of them sleeps between three eighths and a
half of a second before it starts, or however long a `Retry-After` asks, up to two minutes: a
good part of `TIMEOUT_S` spent asleep at best. Within one second a 429 or a 5xx is worth more
as a turn the record calls an error than as a retry that cannot finish."""

CUSTOM_HEADERS_ENV = "OPENAI_CUSTOM_HEADERS"
"""The SDK's own variable for headers to add to every request, one `Name: value` per line."""

ACCOUNT_HEADERS = {"OpenAI-Organization": "OPENAI_ORG_ID", "OpenAI-Project": "OPENAI_PROJECT_ID"}
"""The headers the SDK fills from `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, spelled as it spells
them, each beside the variable it is filled from. Spelled exactly, because a header handed to
the SDK under one of these names replaces that very entry, which is the point away from OpenAI
and the danger at it (`request_headers`)."""

_ACCOUNT_SPELLING = {name.lower(): name for name in ACCOUNT_HEADERS}
"""Each account header by its lower-case name, to find it under whatever spelling a line of
`OPENAI_CUSTOM_HEADERS` gives it."""


def is_openai_host(url: str) -> bool:
    """Whether `url` is OpenAI's own API, the only place `OPENAI_API_KEY` is ever sent.

    Decided on the address's authority, the whole of what stands between `//` and the path,
    because that string is what the client is handed (`client_address`) and so it is where the
    request goes. It has to be one of `OPENAI_AUTHORITIES`, compared whole and in lower case, and
    the scheme `https`, because the key is a bearer token and plain HTTP shows it to the path
    between.

    Not on `urlsplit`'s `hostname`, which is that function's own reading of the authority and
    need not be the HTTP client's, and every way the two readings differ is a way to send the key
    somewhere else. Two are known. A user in the address: `https://evil.example\\@api.openai.com`
    is `api.openai.com` with a user called `evil.example\\` to `urlsplit`, and `evil.example` to
    a client that follows the WHATWG URL standard, which ends the host at a backslash. And
    brackets, on Python 3.11.0 to 3.11.3, which `requires-python` allows: before 3.11.4
    (gh-103848) `urlsplit` never checked what stands between `[` and `]`, and read
    `https://evil.example[api.openai.com]` as the host `api.openai.com`, while the client is
    handed the authority whole. An authority that is exactly one of OpenAI's hosts leaves two
    readings nothing to disagree about. A port other than 443 is refused by the same comparison,
    because `api.openai.com:8443` is no address OpenAI publish and may be somebody's tunnel.

    The whitespace around the address is taken off first, as the CLI takes it off and as
    `client_address` does, so a library caller's `"https://api.openai.com "` is OpenAI's here, in
    `factory`'s price and in the key the client is built with alike, rather than OpenAI's to the
    price and somebody else's to the key."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc.lower() in OPENAI_AUTHORITIES


def client_address(url: str) -> str:
    """The address the client is handed: `url`'s scheme and authority, exactly as
    `is_openai_host` read them, with `API_PREFIX` on the end. Built from those parts rather than
    from the string as typed, so the host the key was decided for is the host the client is
    given."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, API_PREFIX, "", ""))


def check_address(url: str) -> None:
    """Refuse an address this backend cannot be handed, before anything is built from it.

    The scheme, the host and a port, and nothing more, because `API_PREFIX` and `PATH` are
    quackd's to add. A path is refused rather than kept or quietly cut: the likeliest one is
    `/v1`, copied from somewhere that writes OpenAI's address with it, and kept it would ask for
    `/v1/v1/decisions` and be told 404 on every turn of the run. A query or a fragment has
    nowhere to go either. `/` alone is the address with a slash on the end and is fine.

    `factory.resolve_decision_address` calls this while the CLI is still reading its flags, so a
    typo stops the run on every machine, with or without the SDK and the key; the client calls
    it again for whoever builds one directly. An address `redacted_url` cannot read is refused
    without being quoted, because it may carry a password redaction could not find."""
    if not readable_url(url):
        raise DecisionError(
            "--decision-url is not an address: give it as https://host or http://host:port, "
            "and quackd adds /v1/decisions itself"
        )
    parts = urlsplit(url)
    shown = redacted_url(url)
    if parts.scheme not in ("http", "https"):
        raise DecisionError(
            f"--decision-url {shown} is not an http:// or https:// address: give it as "
            "https://host or http://host:port, and quackd adds /v1/decisions itself"
        )
    try:
        _ = parts.port  # read for the ValueError a port that is not a number raises
    except ValueError:
        raise DecisionError(
            f"--decision-url {shown} has a port that is not a number: give it as "
            "https://host or http://host:port"
        ) from None
    if not parts.hostname:
        raise DecisionError(
            f"--decision-url {shown} has no host: give it as https://host or http://host:port"
        )
    extra = [
        what
        for what, there in (
            ("a path", parts.path not in ("", "/")),
            ("a query", "?" in url),
            ("a fragment", "#" in url),
        )
        if there
    ]
    if extra:
        alone = urlunsplit((parts.scheme, urlsplit(shown).netloc, "", "", ""))
        raise DecisionError(
            f"--decision-url {shown} has {' and '.join(extra)}: give the address alone, {alone}, "
            f"because quackd adds {API_PREFIX}{PATH} itself"
        )


class _SDK(NamedTuple):
    """The four things this backend takes from the SDK."""

    client: Any
    """`AsyncOpenAI`, the class."""
    omit: Any
    """`Omit`, the class whose instance under a header's name sends no header by that name."""
    http_client: Any
    """`DefaultAsyncHttpxClient`, the HTTP client with the SDK's own defaults, which is how the
    client is told to follow no redirect in every release the extra allows."""
    version: str
    """The release, for a refusal that has to say which one."""


def _sdk_parts(spec: DecisionSpec) -> _SDK:
    """What this backend takes from the SDK, or the sentence that says why not.

    A missing SDK and one too old to have these are the same sentence, because the same install
    fixes both: before 1.0 the package had no `AsyncOpenAI` at all, and the extra's floor is
    well past that. Every exception counts as missing and not only `ImportError`, because a
    half-installed package is free to raise anything while it is imported, and this is asked on
    the path of a run that would otherwise go ahead without the stepper."""
    extra = spec.extra or SDK
    try:
        sdk = importlib.import_module(SDK)
    except Exception as e:
        raise DecisionNotInstalled(spec.name, extra) from e
    client = getattr(sdk, "AsyncOpenAI", None)
    omit = getattr(sdk, "Omit", None)
    http_client = getattr(sdk, "DefaultAsyncHttpxClient", None)
    if (
        client is None
        or omit is None
        or http_client is None
        or not callable(getattr(client, "post", None))
    ):
        raise DecisionNotInstalled(spec.name, extra)
    return _SDK(client, omit, http_client, str(getattr(sdk, "__version__", "of no known release")))


def client_problem(spec: DecisionSpec) -> str | None:
    """Why this row's client cannot be built here, in the sentence a run prints, or None.

    `factory.decision_llm_is_available` asks this before the robot connects, so an SDK that
    imports and still lacks the client is the soft "running without it" warning rather than an
    `AttributeError` out of the CLI, which catches only a `DecisionError`."""
    try:
        _sdk_parts(spec)
    except DecisionError as e:
        return str(e)
    return None


def ambient_headers(raw: str | None) -> list[str]:
    """The header names `OPENAI_CUSTOM_HEADERS` adds to every request, read as the SDK reads it.

    Split on newlines, the name is everything before the first colon with the whitespace around
    it taken off and its case kept, and a line with no colon is not a header (`openai/_client.py`,
    3.22.1, read 2026-10-09). The same reading, so that each name omitted here is the very
    entry the SDK would have added; reading it more cleverly would leave a header the SDK reads
    and this does not."""
    if raw is None:
        return []
    names: list[str] = []
    for line in raw.split("\n"):
        colon = line.find(":")
        if colon >= 0:
            names.append(line[:colon].strip())
    return names


def request_headers(bearer: str, *, at_openai: bool, omit: Callable[[], Any]) -> dict[str, Any]:
    """The `default_headers` the client is built with: quackd's own authorization, and an answer
    for every header the SDK would otherwise add by itself.

    The releases the extra allows treat `OPENAI_CUSTOM_HEADERS` four ways, and each rule here
    holds under all four rather than under the locked one alone. Read on 2026-10-10 in the
    eleven releases the uv cache held, from 1.66.0 to 3.22.1; the releases between those were
    not read:

    - 1.66.0 and 1.83.0 do not read the variable at all.
    - 3.1.0 adds every line of it, and lets a header the caller hands it replace a line only when
      the two are spelled exactly alike, so `authorization` and `Authorization` are two headers
      and both go.
    - 3.6.0, and every release read after it, drops every authorization line of the variable
      when the caller hands it an authorization header in any spelling.
    - 3.22.1 also merges every name without regard to case, the last spelling winning.

    So, for every line of the variable, read as the SDK reads it (`ambient_headers`):

    - A name that lower-cases to `authorization` carries quackd's own value, under that very
      spelling, so that whichever way a release merges, the value left is quackd's. With such a
      spelling there, `Authorization` itself is omitted, and first, so that a release that keeps
      both spellings sends one line rather than two, and a release that merges them ends on
      quackd's value. With none, `Authorization` carries the value, and naming it is also what
      makes 3.6.0 and the releases read after it drop the variable's own line.
    - At OpenAI, a name spelled exactly as the SDK spells an account header would replace the
      entry the SDK fills from `OPENAI_ORG_ID` or `OPENAI_PROJECT_ID` (`ACCOUNT_HEADERS`), and
      the request would be billed to whatever the key's default account is. So it carries that
      variable's value instead, the one the SDK would have sent, and is omitted only where the
      variable is unset, as the SDK omits it. Named only in another spelling, the header is a
      header of its own, omitted like any other, and the SDK's entry is left to say what it says.
      Named in both, every spelling carries the SDK's value, because a release that merges
      without regard to case lets whichever comes last win, and that may be either.
    - Every other name is omitted.

    Away from OpenAI both account headers are omitted as well, whatever the variable says.

    A value is the same under every spelling it is sent in, so a release that keeps two
    spellings sends the same value twice, which is the worst a variable that names one header
    twice can do here. What a release does that this cannot foresee, `DecisionsAPILLM` reads
    back off the client it built."""
    named = ambient_headers(os.environ.get(CUSTOM_HEADERS_ENV))
    spellings = [n for n in named if n.lower() == "authorization" and n != "Authorization"]
    headers: dict[str, Any] = {"Authorization": omit() if spellings else bearer}
    for name in spellings:
        headers[name] = bearer
    # The account headers whose own entry a line of the variable would replace, at OpenAI.
    replaced = {name for name in named if name in ACCOUNT_HEADERS} if at_openai else set()
    for name in named:
        if name.lower() == "authorization":
            continue
        account = _ACCOUNT_SPELLING.get(name.lower())
        value = None
        if account is not None and account in replaced:
            value = os.environ.get(ACCOUNT_HEADERS[account])
        headers[name] = value if value is not None else omit()
    if not at_openai:
        for name in ACCOUNT_HEADERS:
            headers[name] = omit()
    return headers


def foreign_authorization(headers: Mapping[str, Any], bearer: str, omit_type: Any) -> bool:
    """Whether `headers`, a built client's own `default_headers`, would still send an
    authorization quackd did not write: any value under a name that lower-cases to
    `authorization` that is neither quackd's own nor an `Omit`. A value nobody can compare
    counts as foreign, because this is the last thing between a gateway's credential and the
    address the run was given."""
    for name, value in headers.items():
        if str(name).lower() != "authorization" or isinstance(value, omit_type):
            continue
        if value != bearer:
            return True
    return False


_UNSET = object()
"""What `getattr` hands back for an attribute a client does not have, which no answer can be."""


def warm_platform(client: Any) -> None:
    """Ask now the one question the SDK asks before its first request: which platform it is on.

    Every release the extra allows starts a client's first request with `self._platform = await
    asyncify(get_platform)()` while it has no answer, in a worker thread (`openai/_base_client.py`,
    each of the eleven releases from 1.66.0 to 3.22.1 that uv had cached, read 2026-10-10).
    `get_platform` reads `platform.system()` and `platform.platform()`, and on Linux the
    distribution's own file, and on Windows `platform.platform()` starts `cmd /c ver` in a
    subprocess. CPython keeps the answer for the life of the process, so it is paid once, and it
    was paid inside the first stepper turn and against that turn's second: on a loaded machine
    most of the second, and now and then all of it, when the turn was cancelled before anything
    was sent and was recorded and billed as a request that had left. Reading the built client's
    headers back, as `DecisionsAPILLM` does, happens to ask it as well, through `default_headers`,
    but leaves the client without the answer, so its first request still stopped to ask again.

    So it is asked here, on purpose, while the client is built and the robot is not yet connected,
    where a slow answer costs a moment of start-up and nothing else. The SDK's own `get_platform` is
    asked rather than the standard library's functions alone, so whatever a release reads gets
    read, and its answer is handed to the client where the client keeps one, which spares the
    first request even the worker thread it would have started to ask again; under a release
    that kept it elsewhere the caches are warm all the same. Where the SDK has no such function,
    the standard library's two are asked instead, which is most of the cost on every platform.
    Nothing here may stop the build, because this only saves a turn some time: a client left to
    ask for itself is the client as it was."""
    try:
        probe = getattr(importlib.import_module(f"{SDK}._base_client"), "get_platform", None)
    except Exception:
        probe = None
    try:
        if not callable(probe):
            platform.system()
            platform.platform()
            return
        found = probe()
        if getattr(client, "_platform", _UNSET) is None:
            client._platform = found
    except Exception:
        return


# ── the translation ─────────────────────────────────────────────────────────────────────


def _field(question: Any, name: str) -> Any:
    """One field of a question, whether it is a dict or somebody's object: the tolerance
    `stepper._read` applies to what goes out, applied here to the same thing."""
    if isinstance(question, Mapping):
        return question.get(name)
    return getattr(question, name, None)


def to_input(state: Mapping[str, str]) -> str:
    """The state as OpenAI's `input`: the same JSON object the stepper measures as
    `state_chars`, character for character, so that number is the size of what was sent.

    With its letters as letters (`base.state_text`). The `input` is text the API hands its model as
    it is and never decodes, so JSON's escapes went to `gpt-6-luna` as written, a goal in Slovenian
    as runs of `\\u017e` and every `last` field's dash as `\\u2014`, longer to bill and stranger
    to read than the letters the state holds."""
    return state_text(state)


def _options(criteria: Any, key: str, name: str) -> list[dict[str, str]]:
    """A question's criteria as OpenAI's list of options, in the order they were written.

    A mapping is label to description, which is what `build_questions` writes; a list is labels
    alone. Every label goes as a string: values are typed on this API, so the string `"true"`
    and the boolean `true` are different answers, and every label quackd offers is a string."""
    if isinstance(criteria, Mapping):
        return [{key: str(label), "description": str(text)} for label, text in criteria.items()]
    if isinstance(criteria, list | tuple):
        return [{key: str(label)} for label in criteria]
    raise ValueError(f"the question {name!r} has no criteria to offer")


def to_questions(questions: Mapping[str, Any]) -> list[dict[str, Any]]:
    """quackd's questions in OpenAI's shape, in the order they were asked, each one named.

    Named every time although the schema makes a name optional, because the name is the only
    thing an answer is matched back by (`to_result`).

    - A `choice` keeps its criteria as `choices`, in their order, so `escalate` is last because
      `build_questions` put it last.
    - A `noul` is a `predicate`, the same question by another name: the probability that a
      statement is true.
    - A `score` keeps its criteria as `levels`, in the order they were written.

    Anything else raises a `ValueError` naming it, which `Stepper.advise` records as the turn's
    error, and nothing is sent."""
    out: list[dict[str, Any]] = []
    for name, question in questions.items():
        kind = _field(question, "type")
        asked: dict[str, Any] = {
            "name": str(name),
            "instructions": str(_field(question, "instructions") or ""),
        }
        if kind == "choice":
            asked = {"type": "choice", **asked}
            asked["choices"] = _options(_field(question, "criteria"), "value", str(name))
        elif kind == "noul":
            asked = {"type": "predicate", **asked}
        elif kind == "score":
            asked = {"type": "score", **asked}
            asked["levels"] = _options(_field(question, "criteria"), "label", str(name))
        else:
            raise ValueError(f"POST {API_PREFIX}{PATH} has no question of type {kind!r} ({name!r})")
        out.append(asked)
    return out


def _spread(entries: Any, key: str) -> dict[str, float]:
    """A distribution as a mapping, which is how the stepper reads one.

    OpenAI's is a list of `{value, probability}`, or `{label, probability}` on a score, and the
    stepper calls `.items()` on what it is given, so a list there would cost every turn as an
    error. Numbers only: an entry whose probability is not one is dropped rather than carried
    into a record that is read as a distribution, and a boolean is not a number here, for the
    reason `stepper._finite` gives. Nor is a NaN or an infinity, which the SDK's JSON reader
    takes from a server as readily as a number and a record cannot hold: `json.dumps` writes it
    as a bare `NaN` that no strict JSON reader accepts."""
    spread: dict[str, float] = {}
    if not isinstance(entries, list | tuple):
        return spread
    for entry in entries:
        if not isinstance(entry, Mapping) or key not in entry:
            continue
        probability = entry.get("probability")
        if isinstance(probability, bool) or not isinstance(probability, int | float):
            continue
        if not math.isfinite(probability):
            continue
        spread[str(entry[key])] = float(probability)
    return spread


def _kept(item: Mapping[str, Any], *fields: str) -> dict[str, Any]:
    """The fields of an answer that are there. One that is missing stays missing rather than
    arriving as `None`, so the stepper reads it exactly as it reads a field a System One server
    left out."""
    return {field: item[field] for field in fields if field in item}


def _one_answer(item: Mapping[str, Any], kind: str) -> dict[str, Any] | None:
    """One answer in the shape the stepper reads, or None where it answers nothing: an answer of
    another type than the one its question went out as, or one without the field its type
    answers with.

    Each of those used to come back as something it was not. A `score` under `next_verb` and a
    `choice` with no `choice` in it both carried a confidence, and the stepper filled the
    missing choice in with `escalate`, so a malformed answer was recorded as a confident
    decision to hand the turn back. A `choice` renamed in the beta would have read that way on
    every turn, hidden from anybody counting gates or measuring agreement in shadow mode. A
    choice has to be a string because every value quackd offers is one: the API types its
    values, so a `true` or a `null` is no label quackd sent."""
    if item.get("type") != kind:
        return None
    if kind == "predicate":
        return {"noul": item["probability"]} if "probability" in item else None
    if kind == "choice":
        if not isinstance(item.get("choice"), str):
            return None
        return {
            **_kept(item, "choice", "confidence"),
            "probabilities": _spread(item.get("probabilities"), "value"),
        }
    if kind == "score":
        if "score" not in item:
            return None
        return {
            **_kept(item, "score", "confidence"),
            "probabilities": _spread(item.get("probabilities"), "label"),
        }
    return None


def to_result(data: Mapping[str, Any], asked: Mapping[str, str]) -> dict[str, Any]:
    """OpenAI's answers in the shape `stepper._answer` reads: a mapping keyed by question name.

    `asked` is each question's name and the type it went out as (`to_questions`). Answers are
    read strictly by name and never by position. The API returns answers in the order the
    questions were asked and its schema makes the name on each answer optional, and of those
    two only the name cannot attribute `need_human`'s probability to `done`. So an answer with
    no name, or a name quackd did not ask, is dropped.

    Every answer under a name is gathered before any of them is read, because the first one
    used to win and each case that rule settled was settled wrongly. A refusal among them
    declines the question, wherever it came: kept first, an answer ahead of it hid it. Two
    answers or more, none of them a refusal, answer nothing: OpenAI echo the unique names quackd
    sends, so a second answer is a server that does not, and keeping the first settled a
    contradiction, a `done` of 0.0 and then 0.99, in whichever direction came first, which could
    be the one that lets a verb through. One answer is kept only if it is the type its question
    went out as and carries the field that type answers with (`_one_answer`).

    - A `predicate` becomes `{"noul": probability}`.
    - A `choice` becomes `{"choice", "confidence", "probabilities"}`, the distribution turned
      from a list into a mapping. The vendor's `confidence` is carried as the confidence, field
      to field, as Jev's is: OpenAI do not define it, and their own examples show it is not the
      top probability, so it is neither replaced with that nor blended into a number nobody
      published.
    - A `score` becomes `{"score", "confidence", "probabilities"}`, keyed by level label.
    - A `refusal` answers nothing. Its name goes in `refused`, beside the answers rather than
      among them, so a question the vendor declined can be told from one that went missing.

    What every number says is the stepper's to judge, for this row as for every other
    (`stepper._probability`), so the numbers pass through as they came. A question with nothing
    kept for it is a question nobody answered. Where the stepper routes on it, `next_verb`,
    `done` or `need_human`, that hands the turn to the model on `unreadable`; `feasible` it only
    records, so a missing `feasible` is recorded as nothing and the turn goes on, as it does on
    every row. `usage` and `model` are passed through untouched: `stepper._usage` already reads
    `input_tokens` off a mapping, which is the field OpenAI bills by."""
    answers_in = data.get("answers")
    if not isinstance(answers_in, list):
        raise ValueError(
            f"POST {API_PREFIX}{PATH} answered without a list of answers "
            f"({type(answers_in).__name__})"
        )
    under: dict[str, list[Mapping[str, Any]]] = {name: [] for name in asked}
    for item in answers_in:
        name = item.get("name") if isinstance(item, Mapping) else None
        if isinstance(name, str) and name in under:
            under[name].append(item)
    answers: dict[str, Any] = {}
    refused: list[str] = []
    for name, given in under.items():
        if any(item.get("type") == "refusal" for item in given):
            refused.append(name)
        elif len(given) == 1 and (answer := _one_answer(given[0], asked[name])) is not None:
            answers[name] = answer
    return {
        "answers": answers,
        "refused": refused,
        "model": data.get("model"),
        "usage": data.get("usage"),
    }


# ── the client ──────────────────────────────────────────────────────────────────────────


class DecisionsAPILLM:
    """OpenAI's Decisions API, or a server of your own that answers it, reached where the run
    says.

    Built while the CLI is still parsing rather than on the first turn, as `SystemOneLLM` is:
    constructing a client can fail, and that is a sentence before the robot connects rather than
    a phantom cost on a turn that never left the machine. The address and the key are settled
    here, once, so nothing a turn does can move either, and so is the one thing the SDK would
    otherwise do on the first turn before sending anything (`warm_platform`).
    """

    def __init__(
        self, spec: DecisionSpec, *, model: str | None = None, url: str | None = None
    ) -> None:
        self.name = spec.name
        self.model: str = model or spec.model or ""
        if not self.model:
            raise DecisionError(
                f"--decision-llm {spec.name} names no model: say which, as {spec.name}:MODEL"
            )
        # The whitespace around it taken off, as the CLI takes it off, and blank read as unset,
        # as `factory.resolve_decision_url` reads it: the address every rule below decides on
        # is then the one string the price and the key are both decided on.
        address = (url or "").strip() or (spec.url or "").strip()
        if not address:
            # The row always has one, and `factory` always resolves it. Refused rather than
            # guessed for somebody building this directly from a spec of their own.
            raise DecisionError(
                f"--decision-llm {spec.name} has no address to send to: give one with "
                "--decision-url https://host"
            )
        check_address(address)
        self.url: str | None = address
        sdk = _sdk_parts(spec)
        at_openai = is_openai_host(address)
        if at_openai:
            key = os.environ.get(spec.key_env) if spec.key_env else None
            if not key:
                raise DecisionMissingKey(spec.name, spec.key_env or "OPENAI_API_KEY")
        else:
            key = NO_KEY
        bearer = f"Bearer {key}"
        try:
            self._client: Any = sdk.client(
                api_key=key,
                base_url=client_address(address),
                # Above the stepper's own second rather than equal to it, so the stepper's
                # `wait_for` is the one clock that ends a turn (`base.client_timeout`).
                timeout=client_timeout(TIMEOUT_S),
                max_retries=MAX_RETRIES,
                default_headers=request_headers(bearer, at_openai=at_openai, omit=sdk.omit),
                # Told here rather than per request, because the request option that says it
                # is not in every release the extra allows (1.66.0 has none) and a release
                # without it drops it silently. Every release reads the HTTP client's own.
                http_client=sdk.http_client(follow_redirects=False),
            )
            built = dict(self._client.default_headers)
        except Exception as e:
            # The type and not the message: the message may quote the address, and this one
            # can carry a password the redacted spelling beside it has already taken out.
            raise DecisionError(
                f"--decision-llm {spec.name} could not build its client for "
                f"{redacted_url(address)}: {type(e).__name__}"
            ) from e
        if foreign_authorization(built, bearer, sdk.omit):
            # Read back rather than trusted, because `request_headers` answers the releases
            # somebody read, and the extra allows ones nobody has. The value is never quoted:
            # it is the credential this exists to keep at home.
            raise DecisionError(
                f"--decision-llm {spec.name} would send an authorization header from "
                f"{CUSTOM_HEADERS_ENV} beside its own with openai {sdk.version}, so it is not "
                f"built: take that line out of {CUSTOM_HEADERS_ENV} for this run, or upgrade the "
                'SDK (uv pip install -U "quackd[openai]")'
            )
        warm_platform(self._client)

    async def decide(
        self,
        state: Mapping[str, str],
        questions: Mapping[str, Mapping[str, Any]],
        *,
        timeout_s: float | None = None,
    ) -> Any:
        """One request carrying every question, translated out and the answers translated back.

        `timeout_s`, where it is given, bounds the whole request, connecting, sending and every
        byte of the answer, with a `wait_for` of its own, and a request that runs out raises
        `TimeoutError`. The SDK's timeout could not be that bound: httpx2 applies it to each phase
        of a request on its own, the connect, each read, each write, the wait for a pooled
        connection, so a server that sends its answer a piece at a time, each piece inside the
        limit, or a slow connect followed by a slow first byte, holds a request open well past it.
        So the SDK's clock for that one request is set above the bound (`base.client_timeout`),
        where it can only fire after the bound has ended the request.

        Without `timeout_s` nothing in here bounds the whole request, and the client's own clock,
        per phase and above `TIMEOUT_S`, is a backstop. That is the stepper's case: its `wait_for`
        around this call, at `TIMEOUT_S`, is the bound, and the one clock that ends its turn."""
        asked = to_questions(questions)
        body = {"model": self.model, "input": to_input(state), "questions": asked}
        if timeout_s is None:
            data = await self._post(body, None)
        else:
            data = await asyncio.wait_for(self._post(body, client_timeout(timeout_s)), timeout_s)
        if not isinstance(data, Mapping):
            # The SDK hands back the body as text when it is not JSON. Not quoted: a page of
            # somebody's HTML is not a reason a reader of the record needs to wade through.
            raise ValueError(
                f"POST {API_PREFIX}{PATH} answered {type(data).__name__} rather than a JSON object"
            )
        return to_result(data, {question["name"]: question["type"] for question in asked})

    async def _post(self, body: Mapping[str, Any], timeout_s: float | None) -> Any:
        """The one place a request leaves, so a test can stand where the network would be.

        `timeout_s` is the SDK's own clock for this one request, per phase as the SDK keeps it,
        or None for the one the client was built with. `decide` hands it the clock above its
        bound, never the bound itself."""
        options: dict[str, Any] = {} if timeout_s is None else {"timeout": timeout_s}
        return await self._client.post(PATH, cast_to=object, body=body, options=options)
