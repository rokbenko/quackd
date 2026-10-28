"""The command somebody typed, written down without the secrets in it.

A run's record said what quackd did and never what it was asked to do. The flags are half the
story of any run: which robot, which model, which budget, whether it was a dry run, what the
task was called. Reading a transcript a month later and having to guess at them is the same
gap the wall clock had, and it is closed the same way, by writing the thing down once at the
top.

Secrets reach that line by more routes than one, and those are the reason this is a module
rather than a line. `--api-key`, `--token`, `--host-token` and `--policy-token` carry one
outright, a URL flag carries one in its userinfo or its query string, and `--extra-body` carries
one in whatever field a vendor asked for. A run directory is pasted into issues, attached to bug
reports and copied off a bench machine, and a key that reaches one is a key that has to be
rotated.
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SECRET_FLAGS = ("--api-key", "--token", "--host-token", "--policy-token")
"""Flags whose VALUE never appears in a record. The flag itself does, so a reader can see
that one was given, which is often the thing they are checking. `--host-token` is the Jetson
host daemon's, which guards a camera and a GPU rather than a body, and is hidden all the same:
a run directory is copied off the bench whatever it holds. `--policy-token` is the policy
server's, whose answers move the arm."""

URL_FLAGS = (
    "--base-url",
    "--address",
    "--map-address",
    "--camera-url",
    "--decision-url",
    "--policy-url",
)
"""Flags that take a URL, which is a second way to type a password.

`https://user:pass@gateway/v1` is how an LLM proxy is reached, `ws://user:pass@host:9090` how
a rosbridge is, and `http://user:pass@host/snapshot.jpg` is the standard way an IP camera's
snapshot is authenticated. A System One server you run yourself is reached the same way, over
`--decision-url`, so a credential sitting in that URL is redacted like any other, and so is a
policy server's `--policy-url`, which refuses one outright but is typed all the same, and a
passport sticker's `--url`, for a passport served behind a password, and a robot's
`--map-address` beside its `--address`. The host is
the useful half of one of these and the credential is never the useful half, so the host stays
and the credential goes. A query string is searched too, because `?api_key=` is the other
place vendors put one.
"""

SECRET_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api-key",
        "key",
        "token",
        "access_token",
        "password",
        "passwd",
        "pwd",
        "secret",
        "auth",
        "authorization",
        "sig",
        "signature",
    }
)
"""Query parameter names whose value is replaced. Matched case-insensitively."""

SECRET_BODY_KEYS = SECRET_QUERY_KEYS
"""The same names, for `--extra-body`, which is a JSON object a vendor asked for and is
therefore exactly where an `authorization` header ends up."""

HIDDEN = "***"


def _query_name(pair: str) -> str:
    """The name half of one `a=b`, folded for comparison. A pair with no `=` is all name."""
    return pair.split("=", 1)[0].lower()


def redacted_url(value: str) -> str:
    """A URL with its credentials taken out and everything a reader needs left in.

    Only the userinfo and the named query parameters go. The scheme, the host, the port and
    the path are the whole reason the flag is in the record: somebody reading a failed run
    needs to see which gateway it was pointed at.

    Anything that does not parse as a URL is returned unchanged rather than guessed at. This
    runs over a string a person typed, and a mangled value in the record would send a reader
    after the wrong bug.
    """
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.netloc:
        return value
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, _, host = netloc.rpartition("@")
        user = userinfo.partition(":")[0]
        # the username stays: it is how somebody recognises which account was used, and it
        # is not the half that has to be rotated
        netloc = f"{user}:{HIDDEN}@{host}" if ":" in userinfo else f"{userinfo}@{host}"
    query = parts.query
    if query:
        # Split by hand rather than through `parse_qsl`, which percent-decodes: rebuilding
        # from its output turns `goal=pick%20up` into a URL with a space in it and, worse,
        # turns a value holding `%26` into two parameters. Every pair but the secret one is
        # carried across as the exact substring it was typed as.
        pairs = query.split("&")
        if any(_query_name(pair) in SECRET_QUERY_KEYS for pair in pairs):
            query = "&".join(
                f"{pair.split('=', 1)[0]}={HIDDEN}"
                if _query_name(pair) in SECRET_QUERY_KEYS
                else pair
                for pair in pairs
            )
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def readable_url(value: str) -> bool:
    """Whether `redacted_url` can read `value`, which is a URL with a scheme and a host, and
    so take its credentials out. What it cannot read it returns unchanged, which is right for
    a record of what was typed and wrong for a refusal quoting it: `http://rok:pw@[::1` has a
    password in it and no host redaction can find, so a sentence about it must not quote it."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return bool(parts.scheme and parts.netloc)


def redacted_body(body: Any) -> Any:
    """An `--extra-body` object with anything credential-shaped in it replaced.

    `--extra-body` is documented as a field the vendor wants and quackd never reads, and an
    `authorization` header is the canonical such field. It reaches the record through
    `run_start`, so it is redacted where it is written rather than where it is parsed, and it
    is walked to the bottom because a vendor's nesting is nobody's to predict.
    """
    if isinstance(body, Mapping):
        return {
            k: HIDDEN if str(k).lower() in SECRET_BODY_KEYS else redacted_body(v)
            for k, v in body.items()
        }
    if isinstance(body, list):
        return [redacted_body(v) for v in body]
    return body


def redacted_argv(argv: Sequence[str]) -> list[str]:
    """The arguments as they were typed, with any secret replaced.

    Both spellings a shell allows: `--api-key sk-...` and `--api-key=sk-...`. A trailing
    `--api-key` with nothing after it is left alone, and so is an `--api-key=` given nothing
    after the sign: there is no secret to hide in either, and writing `***` where nothing was
    typed puts a secret in the record that never existed. The record should show the mistake
    as it was made.

    A secret flag standing where a value was expected is read as the flag, not as the value.
    `--api-key --token sk-live-...` is somebody who lost their place, and the shell's own
    reading of it (the key is the literal text `--token`, and the real secret is a stray
    argument) is the reading that writes a live key into the record in the clear. A flag name
    is not worth hiding and a key always is, so the tie goes to hiding.
    """
    out: list[str] = []
    hide_next = False
    clean_next = False
    for arg in argv:
        flag, sep, value = arg.partition("=")
        secret = flag in SECRET_FLAGS
        url = flag in URL_FLAGS
        if hide_next and not secret and not url:
            out.append(HIDDEN)
            hide_next = False
            continue
        if clean_next and not secret and not url:
            out.append(redacted_url(arg))
            clean_next = False
            continue
        hide_next = clean_next = False
        if secret:
            out.append(f"{flag}={HIDDEN}" if sep and value else arg)
            hide_next = not sep
            continue
        if url:
            out.append(f"{flag}={redacted_url(value)}" if sep and value else arg)
            clean_next = not sep
            continue
        out.append(arg)
    return out


FRONT_FLAG = "--front"
"""The hidden flag the page `quackd ui` starts its runs with (`quackd.front`). Left out of the
command a record names, which is the command to type at a terminal for the same run, and a
terminal given `--front jsonl` answers in JSON lines; `run_start` names the front instead."""


def without_front(args: Sequence[str]) -> list[str]:
    """`args` with `--front X` and `--front=X` taken out, wherever they stand."""
    out: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg == FRONT_FLAG:
            skip = True
            continue
        if arg.startswith(f"{FRONT_FLAG}="):
            continue
        out.append(arg)
    return out


def command_line(argv: Sequence[str] | None = None) -> list[str]:
    """What was run, as a list, redacted, starting with `quackd`.

    `sys.argv[0]` is replaced rather than reported: it is the absolute path of the console
    script that ran, which sits inside a virtualenv on one machine and in a uvx cache
    directory on the next, so it changes with the install and tells a reader of the record
    nothing. What the person typed was `quackd`. The page's `--front` is left out, as the
    command to type at a terminal for the same run (`FRONT_FLAG`).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    return ["quackd", *redacted_argv(without_front(args))]


def command_text(argv: Sequence[str] | None = None) -> str:
    """The same thing as one line, quoted so a reader can see where an argument ends.

    `shlex.join` on every platform, including Windows, where it is not what `cmd` would want
    back. This is a record of what was run and not a script to re-run it: one quoting style
    everywhere means a command copied out of a Linux run directory reads the same as one
    copied off the bench laptop.
    """
    return shlex.join(command_line(argv))
