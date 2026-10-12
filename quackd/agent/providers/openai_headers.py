"""What OpenAI's SDK adds to a request by itself, and quackd's answer to each piece of it.

The SDK reads more than its key out of the environment, and it adds what it reads to every request
whatever address the client was handed. That is harmless while the address is the one those
variables were written for, and it is not harmless in quackd, where the same SDK reaches every
other vendor that speaks OpenAI's API and every model server you run: Grok, Mistral, OpenRouter, an
Ollama on a Jetson over plain HTTP. Three variables are the trouble:

- `OPENAI_CUSTOM_HEADERS`, whose lines the releases that read it add to every request. It is where
  a gateway's own credential lives for somebody who reaches OpenAI through one, an
  `Authorization` line among them, and an authorization line there replaced the vendor's key on
  every request, to every vendor and every server, in 3.22.1, the release the lock carries.
- `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, which every release sends as `OpenAI-Organization` and
  `OpenAI-Project`. They are OpenAI's identifiers for an account, and they went to every address
  as well.

Where they go is decided by one rule, recorded in ADR-0010's amendment of 2026-10-12: the SDK's own
variables follow the SDK's own address variable. All three go, exactly as the SDK sends them by
itself, to OpenAI's own API (`is_openai_host`) and to the address `OPENAI_BASE_URL` gives a pilot
that is asked for no address of its own, which is `--llm openai` without `--base-url`. Whoever set
`OPENAI_BASE_URL` set it for this SDK, beside the lines and the account ids they set for the same
SDK, and a gateway reached that way is the case `OPENAI_CUSTOM_HEADERS` exists for: its own
`Authorization` line goes as the SDK sends it, over the key, with nothing of quackd's named on top.
`sdks_own_address` says which addresses those are, and a client bound for one is built as the SDK
would build it by itself, handed nothing but the HTTP client and a vendor's own headers.

Every other address is one quackd chose, by a preset or a flag, and none of the three goes there:
a vendor's own API, OpenRouter's, a local preset's server, `--base-url`, and a preset moved to a
board by `--host`. There the client is handed headers that answer all three (`request_headers`):
the provider's own key as the one authorization, under every spelling the variable gives one, and
every name the variable carries, and both account headers, omitted, apart from the few headers the
SDK puts on every request by itself, which keep the SDK's own values (`own_headers`), and a vendor's
own headers, which keep the vendor's. It is read back once it is built (`foreign_authorization`,
`foreign_headers`), because what a release does with what it is handed differs across the range the
extras allow (`request_headers` says how).

A local preset that took its address from `OPENAI_BASE_URL` is not the SDK's own address, though
it is the same variable. The local presets read it as one rung of quackd's own address ladder
(`providers/local.py`), which the local models guide documents as the address of a model server on
your own machine, and quackd, not the SDK, chose to read it there: a local preset always hands its
client an address, so the SDK never goes looking for one. It is a server you run, which is sent
`LOCAL_API_KEY` or the word `not-needed` and never a hosted key, and a gateway's credential in
`OPENAI_CUSTOM_HEADERS` has no more business there than `OPENAI_API_KEY` has. It is also where the
leak this module answers was found: a model server on a board over plain HTTP, which a `.env` that
names it in `OPENAI_BASE_URL` would otherwise send the gateway's credential to in the clear. A
gateway that wants the variable's lines is reached with `--llm openai` and `OPENAI_BASE_URL`.

The pilot's clients are built in `openai.py`. The rule is here, apart from them, so that any other
client quackd builds on the same SDK can be held to it: `request_headers` is the rule a client
that sends nothing ambient anywhere is built with, the pilot's at every address but the SDK's own.

Nothing here touches the SDK at import time: the rule is the standard library alone, and
`sdk_parts` imports the SDK when a client is about to be built.
"""

from __future__ import annotations

import contextlib
import importlib
import os
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple
from urllib.parse import urlsplit

SDK = "openai"
"""The SDK's import name."""

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
"""OpenAI's own API: its global host, and the regional hosts OpenAI's data-controls page lists
(https://developers.openai.com/api/docs/guides/your-data, read 2026-10-10), which tells anyone whose
project has data residency configured to add that region's prefix to every request. A host is here
only because OpenAI name it, never because it fits the pattern the others share, so a region OpenAI
add is a host somebody has to read about before it is one.

The SDK's own `data_residency` choice names four of them (`openai/_data_residency.py`, 3.22.1). The
other seven are OpenAI's all the same, and a pilot handed one with `--base-url` is talking to
OpenAI.

A host is matched whole and in lower case (`OPENAI_AUTHORITIES`), so
`api.openai.com.evil.example` is not one and neither is `api.openai.com.` with its trailing dot."""

OPENAI_AUTHORITIES = frozenset({*OPENAI_HOSTS, *(f"{host}:443" for host in OPENAI_HOSTS)})
"""Every way an address's authority may spell one of `OPENAI_HOSTS`: the host alone, or with the
port https uses anyway. `is_openai_host` compares the whole authority with these, and anything
else in it, a user, another port or a character the host has no place for, makes it somebody
else's."""

CUSTOM_HEADERS_ENV = "OPENAI_CUSTOM_HEADERS"
"""The SDK's own variable for headers to add to every request, one `Name: value` per line."""

ACCOUNT_HEADERS = {"OpenAI-Organization": "OPENAI_ORG_ID", "OpenAI-Project": "OPENAI_PROJECT_ID"}
"""The headers the SDK fills from `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, spelled as it spells
them, each beside the variable it is filled from. Spelled exactly, because a header handed to the
SDK under one of these names replaces that very entry, which is the point away from OpenAI and the
danger at it (`request_headers`)."""

_ACCOUNT_SPELLING = {name.lower(): name for name in ACCOUNT_HEADERS}
"""Each account header by its lower-case name, to find it under whatever spelling a line of
`OPENAI_CUSTOM_HEADERS` gives it."""

SENDS_JSON = "application/json"
"""What the SDK sends as `Accept` and as `Content-Type` on every request it builds, written out in
`BaseClient.default_headers` in every release from 1.66.0 to 3.22.1 that the uv cache held
(`openai/_base_client.py`, read 2026-10-12). Written down here rather than read off a client,
because a client of a release that reads `OPENAI_CUSTOM_HEADERS` already holds the variable's
line under either name, and the SDK's own value is then nowhere to read."""

_SDK_OWN_NAMES = frozenset({"accept", "content-type", "user-agent"})
"""The headers the SDK writes into every request's defaults by itself, by lower-case name, beside
the ones `_SDK_OWN_PREFIX` names."""

_SDK_OWN_PREFIX = "x-stainless-"
"""The prefix of every header the SDK names after the company that generates it: which release,
which platform, which Python, how many retries."""


def is_openai_host(url: str) -> bool:
    """Whether `url` is OpenAI's own API, the one place the SDK's account headers belong.

    Decided on the address's authority, the whole of what stands between `//` and the path,
    because that string is what the client is handed and so it is where the request goes. It has
    to be one of `OPENAI_AUTHORITIES`, compared whole and in lower case, and the scheme `https`,
    because plain HTTP shows every header to the path between.

    Not on `urlsplit`'s `hostname`, which is that function's own reading of the authority and need
    not be the HTTP client's, and every way the two readings differ is a way to send OpenAI's
    headers somewhere else. Two are known. A user in the address:
    `https://evil.example\\@api.openai.com` is `api.openai.com` with a user called `evil.example\\`
    to `urlsplit`, and `evil.example` to a client that follows the WHATWG URL standard, which ends
    the host at a backslash. And brackets, on Python 3.11.0 to 3.11.3, which `requires-python`
    allows: before 3.11.4 (gh-103848) `urlsplit` never checked what stands between `[` and `]`,
    and read `https://evil.example[api.openai.com]` as the host `api.openai.com`, while the client
    is handed the authority whole. An authority that is exactly one of OpenAI's hosts leaves two
    readings nothing to disagree about. A port other than 443 is refused by the same comparison,
    because `api.openai.com:8443` is no address OpenAI publish and may be somebody's tunnel.

    The whitespace around the address is taken off first, as every caller takes it off before it
    hands the address to a client. `urlsplit` takes off the leading half by itself since 3.11.4,
    and never the trailing half, which is part of the authority wherever an address has no path.
    Anything that is not an address at all is nobody's."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc.lower() in OPENAI_AUTHORITIES


def sdks_own_address(url: str, *, from_its_variable: bool) -> bool:
    """Whether `url` is an address the SDK's own variables go to, exactly as the SDK sends them.

    OpenAI's own API is one, whichever preset or flag led there, because the variables are
    OpenAI's. The other is the address the SDK would have found for itself, `OPENAI_BASE_URL`,
    and `from_its_variable` says the client is bound there because it was asked for no address
    of its own: `--llm openai` without `--base-url`, which is the one pilot whose address the SDK
    would otherwise have looked up itself. A local preset that read the same variable on its own
    ladder is not that, and the module docstring says why."""
    return from_its_variable or is_openai_host(url)


def ambient_headers(raw: str | None) -> list[str]:
    """The header names `OPENAI_CUSTOM_HEADERS` adds to every request, read as the SDK reads it.

    Split on newlines, the name is everything before the first colon with the whitespace around it
    taken off and its case kept, and a line with no colon is not a header (`openai/_client.py`,
    the same in 3.1.0 and in every release read after it, 2026-10-11). The same reading, so that
    each name answered here is the very entry the SDK would have added; reading it more cleverly
    would leave a header the SDK reads and this does not."""
    if raw is None:
        return []
    names: list[str] = []
    for line in raw.split("\n"):
        colon = line.find(":")
        if colon >= 0:
            names.append(line[:colon].strip())
    return names


def is_sdk_own_header(name: str) -> bool:
    """Whether `name`, in any spelling, may be one the SDK puts on every request by itself:
    `Accept`, `Content-Type`, `User-Agent` and its `X-Stainless-` lines. A line of
    `OPENAI_CUSTOM_HEADERS` that names one is answered with the SDK's own value rather than with
    nothing (`own_headers`), so the caller asks for those values only where the variable names one
    of these."""
    lowered = name.lower()
    return lowered in _SDK_OWN_NAMES or lowered.startswith(_SDK_OWN_PREFIX)


def own_headers(client: Any) -> dict[str, str]:
    """The headers the SDK sends on every request by itself, under its own spelling, as far as
    they can be said before a request: `Accept` and `Content-Type` (`SENDS_JSON`), its
    `User-Agent`, and the `X-Stainless-` lines that name the release and the platform.

    The last two are read off `client`, a client of the release that will send them, through the
    two names every release from 1.66.0 to 3.22.1 gives them, `user_agent` and `platform_headers`,
    so the value handed back is that release's own and not a copy of what one release once said.
    Neither depends on anything in the environment. Whatever cannot be read is left out, and a line
    of the variable that names it is then omitted together with the SDK's own entry, which is less
    than the SDK would send and never more. So is `X-Stainless-Async`, which the SDK works out on
    each request from the event loop it finds itself in, and which no value handed at build time
    can match. The count of retries and the read timeout it writes after every merge, so nothing
    handed here can take them away."""
    own = {"Accept": SENDS_JSON, "Content-Type": SENDS_JSON}
    with contextlib.suppress(Exception):
        own["User-Agent"] = str(client.user_agent)
    with contextlib.suppress(Exception):
        own.update({str(name): str(value) for name, value in client.platform_headers().items()})
    return own


def request_headers(
    bearer: str,
    *,
    at_openai: bool,
    omit: Callable[[], Any],
    own: Mapping[str, str] | None = None,
    vendor: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The `default_headers` a client is built with: quackd's own authorization, and an answer for
    every header the SDK would otherwise add by itself.

    `bearer` is `Bearer <key>`, the key the client is built with. `at_openai` is
    `is_openai_host` of the address the client is handed. `omit` makes the SDK's own `Omit`, an
    instance of which under a header's name sends no header by that name. `own` is what the SDK
    sends on every request by itself (`own_headers`), which a line of the variable naming one of
    those headers must not take away. `vendor` is what the client sends on every request of its
    own, a vendor's headers such as OpenRouter's two, which a line of the variable must neither
    replace nor take away.

    The pilot builds a client with these only where the address is not the SDK's own
    (`sdks_own_address`), so it always hands `at_openai=False`. The rule still answers OpenAI's own
    API, for a client that sends nothing ambient anywhere, which is what a client other than the
    pilot's may want.

    The releases the extras allow treat `OPENAI_CUSTOM_HEADERS` four ways, and each rule here holds
    under all four rather than under the locked one alone. Read on 2026-10-10 and 2026-10-11 in the
    eleven releases the uv cache held, from 1.66.0 to 3.22.1; the releases between those were not
    read:

    - 1.66.0 and 1.83.0 do not read the variable at all.
    - 3.1.0 adds every line of it, and lets a header the caller hands it replace a line only when
      the two are spelled exactly alike, so `authorization` and `Authorization` are two headers and
      both go.
    - 3.6.0, and every release read after it, drops every authorization line of the variable when
      the caller hands it an authorization header in any spelling.
    - 3.22.1 also merges every name without regard to case, the last spelling winning, and an
      `Omit` under one spelling takes out every spelling that came before it.

    So, for every line of the variable, read as the SDK reads it (`ambient_headers`):

    - A name that lower-cases to `authorization` carries quackd's own value, under that very
      spelling, so that whichever way a release merges, the value left is quackd's. With such a
      spelling there, `Authorization` itself is omitted, and first, so that a release that keeps
      both spellings sends one line rather than two, and a release that merges them ends on
      quackd's value. With none, `Authorization` carries the value, and naming it is also what
      makes 3.6.0 and the releases read after it drop the variable's own line. The plain rule,
      omitting every name the variable carries, breaks 3.22.1, which merges without regard to
      case: an omitted `authorization` takes quackd's own away with it.
    - A name that is, in any spelling, one of the SDK's own headers in `own` carries the SDK's own
      value, for the same reason: omitted, it took the SDK's `User-Agent` with it in 3.22.1, and
      its `Content-Type` too, so a JSON body went out saying nothing of what it was. Where the
      variable spells it otherwise than the SDK, the SDK's own spelling is omitted, so that a
      release that keeps both spellings sends one line, the SDK's value under the variable's
      spelling. Named in both spellings, both carry the SDK's value.
    - A name that is, in any spelling, one of `vendor`'s carries the vendor's value under every
      spelling the variable gives it. The client keeps the variable's lines in the variable's
      order, a header handed under a spelling the variable gives taking that line's place, and
      3.22.1 merges them in that order, so which spelling comes last is the variable's to say.
      Where the variable named OpenRouter's `X-OpenRouter-Title` in OpenRouter's own spelling and
      then in another, the `Omit` handed under the second took OpenRouter's value away from the
      first, and the header went nowhere. With the vendor's value in every slot, the last one is
      the vendor's in any order. Where the variable gives the name only in other spellings, the
      vendor's own is not handed at all. Beside them, a release that keeps spellings apart would
      send the value once more, and it cannot be omitted there as the SDK's own spelling is: the
      SDK's has a slot of its own ahead of the variable's lines, and the vendor's would come after
      every one of them, where an `Omit` takes the value away again.
    - At OpenAI, a name spelled exactly as the SDK spells an account header would replace the entry
      the SDK fills from `OPENAI_ORG_ID` or `OPENAI_PROJECT_ID` (`ACCOUNT_HEADERS`), and the request
      would be billed to whatever the line said, or to the key's default account where the line
      was omitted. So it carries that variable's value instead, the one the SDK would have sent,
      and is omitted only where the variable is unset, as the SDK omits it. Named only in another
      spelling, the header is a header of its own, omitted, and the SDK's entry is left to say what
      it says. Named in both, every spelling carries the SDK's value, because a release that merges
      without regard to case lets whichever comes last win, and that may be either.
    - Every other name is omitted.

    Away from OpenAI both account headers are omitted as well, whatever the variable says.

    A value is the same under every spelling it is sent in, so a release that keeps two spellings
    sends the same value twice, which is the worst a variable that names one header twice can do
    here. Two cases of it are quackd's own doing, in 1.66.0 and 1.83.0, which never read the
    variable but send every spelling quackd hands them. At OpenAI, a variable that names an
    account header in the SDK's spelling and in another has both carry the variable's value, and
    those two releases send the SDK's own entry and the one quackd handed under the other
    spelling, two lines with one value, where the SDK alone sends one. And a vendor's header the
    variable names in two spellings is handed under both, so those two releases send the vendor's
    value twice where the vendor alone sends it once.
    What a release does that this cannot foresee, the caller reads back off the client it built
    (`foreign_authorization`, `foreign_headers`)."""
    named = ambient_headers(os.environ.get(CUSTOM_HEADERS_ENV))
    spellings = [n for n in named if n.lower() == "authorization" and n != "Authorization"]
    headers: dict[str, Any] = {"Authorization": omit() if spellings else bearer}
    for name in spellings:
        headers[name] = bearer
    # The SDK's own headers by lower-case name, and each one's own spelling where the variable
    # names it only in another, which would keep the SDK's line beside the one handed here.
    sdk_own = {name.lower(): (name, value) for name, value in (own or {}).items()}
    for lowered, (spelled, _value) in sdk_own.items():
        variants = [n for n in named if n.lower() == lowered]
        if variants and spelled not in variants:
            headers[spelled] = omit()
    # The account headers whose own entry a line of the variable would replace, at OpenAI.
    replaced = {name for name in named if name in ACCOUNT_HEADERS} if at_openai else set()
    for name in named:
        lowered = name.lower()
        if lowered == "authorization":
            continue
        if lowered in sdk_own:
            headers[name] = sdk_own[lowered][1]
            continue
        account = _ACCOUNT_SPELLING.get(lowered)
        value = None
        if account is not None and account in replaced:
            value = os.environ.get(ACCOUNT_HEADERS[account])
        headers[name] = value if value is not None else omit()
    if not at_openai:
        for name in ACCOUNT_HEADERS:
            headers[name] = omit()
    # Last, so a vendor's own header wins over whatever the rule above answered for its name.
    for spelled, value in (vendor or {}).items():
        variants = [n for n in named if n.lower() == spelled.lower()]
        for name in variants or [spelled]:
            headers[name] = value
    return headers


def foreign_authorization(headers: Mapping[str, Any], bearer: str, omit_type: Any) -> bool:
    """Whether `headers`, a built client's own `default_headers`, would still send an authorization
    quackd did not write: any value under a name that lower-cases to `authorization` that is
    neither quackd's own nor an `Omit`. A value nobody can compare counts as foreign, because this
    is the last thing between a gateway's credential and the address the run was given."""
    for name, value in headers.items():
        if str(name).lower() != "authorization" or isinstance(value, omit_type):
            continue
        if value != bearer:
            return True
    return False


def foreign_headers(headers: Mapping[str, Any], handed: Mapping[str, Any], omit_type: Any) -> bool:
    """Whether `headers`, a built client's own `default_headers`, would send a value `handed` did
    not hand under a name `handed` answers: a line of `OPENAI_CUSTOM_HEADERS` that a release put
    back over the `Omit` quackd handed under its name, or an account header that came back with a
    value away from OpenAI. Authorization is `foreign_authorization`'s.

    Compared without regard to case, because a release that merges that way sends whichever
    spelling comes last, and a value is taken where it is one of the values handed under any
    spelling of its name, since the rule hands one value under every spelling it names. A value
    nobody can compare counts as foreign, as it does for the authorization."""
    answered: dict[str, list[Any]] = {}
    for name, value in handed.items():
        lowered = str(name).lower()
        if lowered == "authorization":
            continue
        answered.setdefault(lowered, [])
        if not isinstance(value, omit_type):
            answered[lowered].append(value)
    for name, value in headers.items():
        allowed = answered.get(str(name).lower())
        if allowed is None or isinstance(value, omit_type):
            continue
        try:
            if value not in allowed:
                return True
        except Exception:
            return True
    return False


class SDKParts(NamedTuple):
    """What a client built on the SDK takes from it, beside the client itself."""

    client: Any
    """`AsyncOpenAI`, the class."""
    omit: Any
    """`Omit`, the class whose instance under a header's name sends no header by that name."""
    http_client: Any
    """`DefaultAsyncHttpxClient`, the HTTP client with the SDK's own defaults, which is how a
    client is told to follow no redirect in every release the extras allow: the request option
    that says it is not in 1.66.0, which drops an option it does not know without a word."""
    version: str
    """The release, for a refusal that has to say which one."""


def sdk_parts() -> SDKParts | None:
    """The four things a client takes from the SDK, or None where it cannot have them.

    A missing SDK and one too old to have these are the same answer, because the same install fixes
    both, and every caller turns it into the sentence that names its own extra. Every exception
    counts as missing and not only `ImportError`, because a half-installed package is free to raise
    anything while it is imported. All four are in 1.66.0, the extras' floor, and in 3.22.1, the
    locked release."""
    try:
        sdk = importlib.import_module(SDK)
    except Exception:
        return None
    client = getattr(sdk, "AsyncOpenAI", None)
    omit = getattr(sdk, "Omit", None)
    http_client = getattr(sdk, "DefaultAsyncHttpxClient", None)
    if client is None or omit is None or http_client is None:
        return None
    version = str(getattr(sdk, "__version__", "of no known release"))
    return SDKParts(client, omit, http_client, version)
