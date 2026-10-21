"""Fetching a passport the way spec section 5.7 allows: no redirects, 8 KB, five seconds.

A reader that followed a passport wherever it pointed would turn every robot on a network into
a way to probe that network. This fetches exactly one URL, the one it was given, and nothing a
response names."""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from quackd.passport.document import MAX_BYTES
from quackd.passport.errors import PassportError

WELL_KNOWN_JSON = "/.well-known/robot-passport.json"
WELL_KNOWN_JWS = "/.well-known/robot-passport.jws"
FETCH_TIMEOUT_S = 5.0


class HttpStatus(PassportError):
    """A host that answered, with a status other than 200."""

    def __init__(self, url: str, status: int, reason: str) -> None:
        super().__init__("5.7", f"{url} answered {status} {reason}")
        self.status = status


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> Any:
        raise PassportError("5.7", f"it redirects to {newurl}, and a reader follows no redirects")


@dataclass(frozen=True)
class Target:
    """A URL to fetch, and the short id its `#rp=` fragment promised, if it had one."""

    url: str
    short_id: str | None


def target(text: str) -> Target:
    """A URL as a person typed it or a camera read it (spec 5.2).

    A bare host, `http://10.0.0.5:8790`, means its well-known URL. The fragment never travels
    to the server; `#rp=` in it is the short id the passport must turn out to have."""
    parts = urlsplit(text.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise PassportError("5.7", f"{text!r} is not an http or https URL")
    promised = parse_qs(parts.fragment).get("rp", [None])[0]
    path = parts.path if parts.path not in ("", "/") else WELL_KNOWN_JSON
    return Target(urlunsplit((parts.scheme, parts.netloc, path, parts.query, "")), promised)


def fetch(url: str, *, limit: int = MAX_BYTES, timeout: float = FETCH_TIMEOUT_S) -> bytes:
    """The body at `url`, at most `limit` bytes, or a `PassportError` saying why not."""
    opener = urllib.request.build_opener(_NoRedirects)
    request = urllib.request.Request(
        url, headers={"Accept": "application/jose, application/json", "User-Agent": "quackd"}
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            body: bytes = response.read(limit + 1)
    except PassportError:
        raise
    except urllib.error.HTTPError as e:
        raise HttpStatus(url, e.code, str(e.reason)) from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise PassportError("5.7", f"{url} could not be reached: {reason}") from e
    if len(body) > limit:
        raise PassportError("rule 1", f"{url} sent more than the {limit} bytes a reader accepts")
    return body


def fetch_passport(url: str, *, timeout: float = FETCH_TIMEOUT_S) -> bytes:
    """A passport at `url`, signed when it can be: for a `robot-passport.json`, the `.jws` beside
    it on the same host is asked for first, since a reader that verifies uses the JWS (spec 5.1),
    and the JSON is fetched only when the host has no JWS. Nothing else is ever fetched."""
    parts = urlsplit(url)
    if parts.path.endswith("robot-passport.json"):
        signed = urlunsplit(
            (parts.scheme, parts.netloc, parts.path[: -len("json")] + "jws", parts.query, "")
        )
        try:
            return fetch(signed, timeout=timeout)
        except HttpStatus as e:
            if e.status != 404:
                raise
    return fetch(url, timeout=timeout)
