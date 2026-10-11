"""What OpenAI's SDK adds to a request by itself, and quackd's answer to each piece of it.

The SDK reads more than its key out of the environment, and it adds what it reads to every request
whatever address the client was handed. That is harmless while the address is OpenAI's, and it is
not harmless in quackd, where the same SDK reaches every other vendor that speaks OpenAI's API and
every model server you run: Grok, Mistral, OpenRouter, an Ollama on a Jetson over plain HTTP.
Three variables are the trouble:

- `OPENAI_CUSTOM_HEADERS`, whose lines the releases that read it add to every request. It is where
  a gateway's own credential lives for somebody who reaches OpenAI through one, an
  `Authorization` line among them, and an authorization line there replaced the vendor's key on
  every request, to every vendor and every server, in 3.22.1, the release the lock carries.
- `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, which every release sends as `OpenAI-Organization` and
  `OpenAI-Project`. They are OpenAI's identifiers for an account, and they went to every address
  as well.

So every client quackd builds on the SDK is handed headers that answer all three
(`request_headers`), decided on the address the client is handed (`is_openai_host`), and is read
back once it is built (`foreign_authorization`), because what a release does with what it is
handed differs across the range the extras allow (`request_headers` says how). The pilot's
clients are built in `openai.py`. The rule is here, apart from them, so that any other client
quackd builds on the same SDK can be held to the same one.

Nothing here touches the SDK at import time: the rule is the standard library alone, and
`sdk_parts` imports the SDK when a client is about to be built.
"""

from __future__ import annotations

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
    hands the address to a client. Anything that is not an address at all is nobody's."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc.lower() in OPENAI_AUTHORITIES


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


def request_headers(
    bearer: str,
    *,
    at_openai: bool,
    omit: Callable[[], Any],
    keep_at_openai: bool = False,
) -> dict[str, Any]:
    """The `default_headers` a client is built with: quackd's own authorization, and an answer for
    every header the SDK would otherwise add by itself.

    `bearer` is `Bearer <key>`, the key the client is built with. `at_openai` is
    `is_openai_host` of the address the client is handed. `omit` makes the SDK's own `Omit`, an
    instance of which under a header's name sends no header by that name. `keep_at_openai` says
    what becomes of a line of the variable that names neither an authorization nor an account
    header, at OpenAI's own API: omitted like every other, the default, or left to the SDK, which
    sends it there as it always has, which is what the pilot asks for. Away from OpenAI every such
    line is omitted, whatever this says.

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
    - 3.22.1 also merges every name without regard to case, the last spelling winning.

    So, for every line of the variable, read as the SDK reads it (`ambient_headers`):

    - A name that lower-cases to `authorization` carries quackd's own value, under that very
      spelling, so that whichever way a release merges, the value left is quackd's. With such a
      spelling there, `Authorization` itself is omitted, and first, so that a release that keeps
      both spellings sends one line rather than two, and a release that merges them ends on
      quackd's value. With none, `Authorization` carries the value, and naming it is also what
      makes 3.6.0 and the releases read after it drop the variable's own line. The plain rule,
      omitting every name the variable carries, breaks 3.22.1, which merges without regard to
      case: an omitted `authorization` takes quackd's own away with it.
    - At OpenAI, a name spelled exactly as the SDK spells an account header would replace the entry
      the SDK fills from `OPENAI_ORG_ID` or `OPENAI_PROJECT_ID` (`ACCOUNT_HEADERS`), and the request
      would be billed to whatever the line said, or to the key's default account where the line
      was omitted. So it carries that variable's value instead, the one the SDK would have sent,
      and is omitted only where the variable is unset, as the SDK omits it. Named only in another
      spelling, the header is a header of its own, omitted, and the SDK's entry is left to say what
      it says. Named in both, every spelling carries the SDK's value, because a release that merges
      without regard to case lets whichever comes last win, and that may be either.
    - Every other name is omitted, or at OpenAI left to the SDK where `keep_at_openai` says so.

    Away from OpenAI both account headers are omitted as well, whatever the variable says.

    A value is the same under every spelling it is sent in, so a release that keeps two spellings
    sends the same value twice, which is the worst a variable that names one header twice can do
    here. What a release does that this cannot foresee, the caller reads back off the client it
    built (`foreign_authorization`)."""
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
        if account is None and at_openai and keep_at_openai:
            continue
        value = None
        if account is not None and account in replaced:
            value = os.environ.get(ACCOUNT_HEADERS[account])
        headers[name] = value if value is not None else omit()
    if not at_openai:
        for name in ACCOUNT_HEADERS:
            headers[name] = omit()
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
