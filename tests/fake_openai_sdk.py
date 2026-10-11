"""OpenAI's SDK as a pilot reaches it while it builds a client, never leaving the machine.

Installed into `sys.modules` as `openai`, it is the module `openai_headers.sdk_parts` imports and
would otherwise find in whatever state a developer's machine happens to be in, and not at all on
CI, which installs no provider SDK: two different suites. It stands in for exactly what a pilot
touches while it builds its client -- the client class, the `Omit` marker, the HTTP client the SDK
is handed and the release it calls itself -- and for the two things read back off a built client,
its `default_headers` and its `base_url`, and it records what the client class was handed. Sending
a request is not its business: a test that sends one hands the provider a client of its own, or
drives the real SDK (`tests/test_openai_headers_wire.py`).

What one installed SDK does with the headers it is handed is a property of that SDK rather than of
quackd, and only a test that drives the real one can show it, wherever it is installed, which is
the locked release and no other. So this also carries a model of what each kind of release the
extras allow puts on a request (`RELEASES`, `sent`), read from the releases themselves, so that CI
holds quackd's rule to all of them and not only to the one a developer happens to have. The SDK's
own account headers are modelled beside the variable's lines, because what reaches a vendor that
is not OpenAI is the point here.
"""

from __future__ import annotations

import os
import sys
import types
from typing import Any

__version__ = "0.0.0+fake"
"""What the module says its release is, which a refusal that names the release quotes."""


class Omit:
    """The SDK's marker for "send no header by this name", as far as a pilot uses it."""

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "Omit()"


class DefaultAsyncHttpxClient:
    """The HTTP client the SDK offers with its own defaults, as far as a pilot uses it: built with
    whatever it was handed, which a test reads back. The real one follows redirects unless it is
    told not to."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.follow_redirects = bool(kwargs.get("follow_redirects", True))


# ── what a release puts on a request ────────────────────────────────────────────────────────
#
# Read from the eleven releases the uv cache held on 2026-10-11, from 1.66.0 to 3.22.1:
# `AsyncOpenAI.__init__` and its `default_headers` in `openai/_client.py`, and `BaseClient`'s
# `default_headers` and `_build_headers` in `openai/_base_client.py`. Every release that reads
# `OPENAI_CUSTOM_HEADERS` parses it the same way. They differ in what they do next, and in how a
# request's headers are merged, which is all a model here has to say. Each was then run against
# the real releases with the network patched shut, and agreed with all eleven.

RELEASES = ("1.66", "3.1", "3.6", "3.22")
"""Four kinds of release inside the extras' range, by the first cached release of each. `1.66`,
and 1.83.0 with it, never reads the variable. `3.1` adds every line of it and lets a header the
caller names replace a line only when the two are spelled exactly alike. `3.6`, and every cached
release up to 3.19.0, also drops the variable's authorization lines when the caller names one in
any spelling. `3.22`, the locked release, merges a request's headers without regard to case
besides, the last spelling winning."""

READ = {
    "1.66.0": "1.66",
    "1.83.0": "1.66",
    "3.1.0": "3.1",
    "3.6.0": "3.6",
    "3.8.0": "3.6",
    "3.10.0": "3.6",
    "3.13.0": "3.6",
    "3.14.0": "3.6",
    "3.18.0": "3.6",
    "3.19.0": "3.6",
    "3.22.1": "3.22",
}
"""Each release that was read, and the kind of release it is. A release that is not here has
not been read, and nothing says which kind it is."""

BACKWARDS = "backwards"
"""No release at all: the variable's lines over the caller's, which is the SDK a backstop exists
for."""


def variable() -> dict[str, str] | None:
    """`OPENAI_CUSTOM_HEADERS` as every release that reads it parses it: split on newlines, the
    name before the first colon and the value after it, both trimmed, a later line with the same
    name replacing an earlier one. None when the variable is unset, which every release reads as
    no lines at all."""
    raw = os.environ.get("OPENAI_CUSTOM_HEADERS")
    if raw is None:
        return None
    parsed: dict[str, str] = {}
    for line in raw.split("\n"):
        colon = line.find(":")
        if colon >= 0:
            parsed[line[:colon].strip()] = line[colon + 1 :].strip()
    return parsed


def _custom(release: str, handed: dict[str, Any]) -> dict[str, Any]:
    """The custom headers a release keeps: the variable's lines, then the caller's over them."""
    parsed = variable()
    if release == "1.66" or parsed is None:
        return dict(handed)
    if release == BACKWARDS:
        return {**handed, **parsed}
    if release != "3.1" and any(name.lower() == "authorization" for name in handed):
        parsed = {name: value for name, value in parsed.items() if name.lower() != "authorization"}
    return {**parsed, **handed}


def default_headers(release: str, handed: dict[str, Any], api_key: str) -> dict[str, Any]:
    """What a built client's own `default_headers` holds under `release`, in the order the SDK
    builds it, which matters as much as what is in it.

    The base class spreads the custom headers first, after the SDK's own `Accept`, `User-Agent`
    and the like, and in 1.66.0 after the key's `Authorization` as well. The client then names
    `OpenAI-Organization` and `OpenAI-Project`, from `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` or
    `Omit` where either is unset, and spreads the custom headers again. A custom header spelled
    exactly as one of those two therefore takes its place, and one spelled another way keeps the
    early place the first spread gave it, ahead of the SDK's own entry. The headers that cannot
    matter here are left out."""
    custom = _custom(release, handed)
    organization = os.environ.get("OPENAI_ORG_ID")
    project = os.environ.get("OPENAI_PROJECT_ID")
    own = {"Authorization": f"Bearer {api_key}"} if release == "1.66" else {}
    return {
        **own,
        **custom,
        "OpenAI-Organization": organization if organization is not None else Omit(),
        "OpenAI-Project": project if project is not None else Omit(),
        **custom,
    }


def sent(fake: FakeOpenAI, release: str) -> list[tuple[str, str]]:
    """The header lines `release` would put on a request from this client, in order, a name sent
    in two spellings showing twice.

    1.66.0 already holds the key's `Authorization` in its default headers, and merges by exact
    name. Every release after it puts the key's `Authorization` first and the default headers over
    it: by exact name up to 3.19.0, and without regard to case in 3.22.1, where an `Omit` takes
    out whatever came before it under any spelling of its name."""
    api_key = str(fake.kwargs.get("api_key"))
    headers = default_headers(release, fake.handed, api_key)
    key = ("Authorization", f"Bearer {api_key}")
    if release == "3.22":
        lines: dict[str, tuple[str, str]] = {}
        for name, value in [key, *headers.items()]:
            if isinstance(value, Omit):
                lines.pop(name.lower(), None)
            else:
                lines[name.lower()] = (name, value)
        return list(lines.values())
    merged = headers if release == "1.66" else {key[0]: key[1], **headers}
    return [(name, value) for name, value in merged.items() if not isinstance(value, Omit)]


def every(lines: list[tuple[str, str]], name: str) -> list[str]:
    """Every value sent under `name`, in any spelling, in order: a second one beside the first
    shows here."""
    return [value for sent_as, value in lines if sent_as.lower() == name.lower()]


class FakeOpenAI:
    """`openai.AsyncOpenAI`, as far as a pilot uses it while it builds a client.

    `release` is which of `RELEASES` (or `BACKWARDS`) the client's own `default_headers` is merged
    like when it is read back, and None, the default, gives back exactly what it was handed.
    `moves_to` is an address the client reports instead of the one it was handed, for a release
    that would send somewhere else."""

    def __init__(self, *, release: str | None = None, moves_to: str | None = None) -> None:
        self.release = release
        self.moves_to = moves_to
        self.kwargs: dict[str, Any] = {}
        """What the client class was called with, as the SDK's constructor would have been."""
        self.built = 0

    def __call__(self, **kwargs: Any) -> FakeOpenAI:
        """The constructor's shape, so a provider builds this the way it builds the real one, and
        the test keeps hold of what it was handed."""
        self.kwargs = kwargs
        self.built += 1
        return self

    @property
    def handed(self) -> dict[str, Any]:
        """The `default_headers` the client was built with."""
        return dict(self.kwargs.get("default_headers") or {})

    @property
    def default_headers(self) -> dict[str, Any]:
        """What the real client's own `default_headers` holds once it is built."""
        if self.release is None:
            return self.handed
        return default_headers(self.release, self.handed, str(self.kwargs.get("api_key")))

    @property
    def base_url(self) -> str:
        """As the SDK gives it: the address it was built with, ending in a slash."""
        if self.moves_to is not None:
            return self.moves_to
        return str(self.kwargs.get("base_url") or "").rstrip("/") + "/"


def install(monkeypatch: Any, fake: FakeOpenAI, *, without: tuple[str, ...] = ()) -> FakeOpenAI:
    """Put `fake` where `importlib.import_module("openai")` will find it, for one test. `without`
    names what the module should not have, for an SDK too old or too broken to build a client."""
    module = types.ModuleType("openai")
    parts = {
        "__version__": __version__,
        "AsyncOpenAI": fake,
        "Omit": Omit,
        "DefaultAsyncHttpxClient": DefaultAsyncHttpxClient,
    }
    for name, value in parts.items():
        if name not in without:
            setattr(module, name, value)
    monkeypatch.setitem(sys.modules, "openai", module)
    return fake
