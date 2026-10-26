"""A policy server on a Hugging Face Inference Endpoint: starting one, keeping it, reaching it.

`quackd policy endpoint up NAME --policy REPO@REVISION` rents a GPU from Hugging Face by the
hour and runs `quackd policy serve` on it, the same server a laptop runs, behind Hugging Face's
proxy and its TLS. A run reaches it with `--policy-url hf:NAME`, and the arm checks it as it
checks any policy server before any torque. This module is Hugging Face's API as quackd uses
it (`HFEndpoints`), the record this machine keeps of the endpoints it started
(`EndpointStore`), what an endpoint is created from (`create_payload`), what is checked before
one is (`preflight`), the wait for one to serve (`wait_until_serving`), and the one way a run
finds one (`reach`). Every name of Hugging Face's it spells is read in
`endpoint_upstream_api.py`, and quackd has never created an endpoint: all of this has run
against the test suite's stand-ins on 127.0.0.1 (`tests/fake_hugging_face.py`).

**It imports the standard library and quackd, and nothing else**, as `client.py` does: the
arm's process imports it to reach an endpoint (`reach`), and gains no dependency from it. The
one module of the server's it reads, `pipeline.py`, it imports inside `preflight`, which `up`
runs and the arm's process never does.

**Where each token goes**, which is the reason most of this module is shaped as it is:

- **The account's Hugging Face token** (`hf_token`), found where huggingface_hub finds it
  (`endpoint_upstream_api.TOKEN_LOOKUP`). It goes to `HUB` and `API` for the account's own
  business, and as the bearer to the one address this machine's `up` recorded for an endpoint,
  which the client holds to (`client.is_endpoint_host`), once the API, asked with it, has said
  the endpoint by that name is quackd's and at that address (`confirm`): a record is a file in a
  directory a project's `.env` can name. `GET /v2/provider` and PyPI are asked with no token. It
  never goes into an endpoint: an `authenticated` endpoint admits any account
  (`endpoint_upstream_api.AUTHENTICATED_ADMITS`), and nothing in the container needs it.
- **quackd's token**, `secrets.token_hex(32)` made by `up`, which is the lock on the server
  whatever the proxy does. It goes into the endpoint's secrets as `QUACKD_POLICY_TOKEN`, which
  `quackd policy serve --token-env` reads, and into this machine's record, from which `reach`
  hands it to the client. It is never printed.
- **A Hub token for a gated or private checkpoint**, the value of the variable
  `--hub-token-from` names, which goes into the endpoint's secrets as `HF_TOKEN` and to `HUB`
  to read the checkpoint's config, and is refused when it is the account's own token.

Every address is a module constant (`API`, `HUB`, `PYPI`) that no variable moves, as
`HF_ENDPOINT` moves huggingface_hub's, since a variable that moved one would move where the
account's token goes. Every request is https, or plain http to the loopback literals, which is
where a test's stand-in listens. No redirect is followed and no proxy used (`http.client`),
every reply is bounded in size and held to a deadline however slowly it arrives, and every
error is said in one sentence with every token scrubbed out of it.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import ssl
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from quackd.adapters.base import POLICY_ENDPOINT
from quackd.registry import RegistryError, registry_dir, write_json
from quackd_lerobot import __version__
from quackd_lerobot.policy import client
from quackd_lerobot.policy import endpoint_upstream_api as up
from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy.client import _bounded_reply, _Deadline, clean_bearer

API = "https://api.endpoints.huggingface.cloud"
"""Hugging Face's Inference Endpoints API (`endpoint_upstream_api.ENDPOINT_ROUTES`)."""
HUB = "https://huggingface.co"
"""The Hub: the account a token belongs to, and a checkpoint's own files and visibility."""
PYPI = "https://pypi.org"
"""Where the release an endpoint installs is looked up, with no token."""

TAG = "quackd-policy"
"""The tag every endpoint quackd creates carries, which is how `list` tells them apart."""
STORE_FILE = "endpoints.json"
STORE_VERSION = 1
INSTANCE_SIZE = "x1"
"""One GPU, always: a policy serves one arm, and a session lives in one process."""
CONTAINER_PORT = wire.DEFAULT_PORT
TOKEN_SECRET = wire.TOKEN_ENV
"""The secret quackd's token goes into, the variable `quackd policy serve --token-env` reads."""
HUB_TOKEN_SECRET = "HF_TOKEN"
"""The secret a Hub token for a gated checkpoint goes into, where huggingface_hub reads it."""
TIMEOUT_S = 30.0
"""How long one request to Hugging Face or PyPI may take, its whole reply included."""
MAX_REPLY_BYTES = 8 << 20
"""The most of one reply that is read. The provider listing is about 20 KB, and a page of
endpoints a few hundred KB at most."""
LIST_LIMIT = 100
MAX_LIST_PAGES = 20
LOG_LINES = 12
"""How many of the last lines of an endpoint's log are quoted when it is paused for failing."""
LOG_LINE_CHARS = 300
EXCLUDE_NEWER_AFTER = timedelta(hours=1)
"""How long after the release's last file was uploaded the container's `--exclude-newer` is."""
POLL_S = up.POLL_S
"""How often `up` reads an endpoint while it waits for one: every 5 s, as huggingface_hub does.
A module attribute, so the tests' stand-ins need not be waited on in real seconds."""
NAME = re.compile(up.NAME_PATTERN)
ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,95}$")
"""A Hugging Face account's name, as a path segment of the API takes it."""
SERVING = "running"
FAILED = frozenset({"failed", "updateFailed"})
ASLEEP = "scaledToZero"
PAUSED = "paused"


class EndpointError(ValueError):
    """Anything `quackd policy endpoint` refuses, or Hugging Face refused, in one sentence that
    says what to do and holds no token. `status` is the HTTP status when there was one. A
    ValueError, so the arm's side refuses a run on one as it refuses any bad `--policy-url`."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class EndpointFailed(EndpointError):
    """An endpoint that reached `failed` or `updateFailed` while `up` waited for it."""


class EndpointTimeout(EndpointError):
    """An endpoint that did not serve within the minutes `up` was given to wait."""


class Unconfirmed(EndpointError):
    """An endpoint by a recorded name that Hugging Face's answer does not confirm is the one this
    machine's `up` started: one without quackd's tag, as one made on huggingface.co after
    quackd's was deleted there would be, or without the tag `up` gave it from this machine's
    record (`mark`), as one another machine's `up` made by the same name would be. quackd sends
    it nothing and changes nothing on it, and the refusal says how to forget the record."""


# ── the account's token ─────────────────────────────────────────────────────────────────


def token_path() -> Path:
    """The file huggingface_hub keeps the account's token in: `HF_TOKEN_PATH`, else
    `$HF_HOME/token`, with `HF_HOME` itself `$XDG_CACHE_HOME/huggingface` or
    `~/.cache/huggingface` unless set (`endpoint_upstream_api.TOKEN_LOOKUP`)."""
    named = os.environ.get(up.TOKEN_PATH_VARIABLE)
    if named:
        return Path(os.path.expandvars(os.path.expanduser(named)))
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    home = os.environ.get(up.HOME_VARIABLE) or os.path.join(cache, "huggingface")
    return Path(os.path.expandvars(os.path.expanduser(home))) / "token"


def hf_token() -> tuple[str, str] | None:
    """The account's token and where it was found, or None: `HF_TOKEN`, then
    `HUGGING_FACE_HUB_TOKEN`, then the file `hf auth login` writes (`token_path`), in the order
    huggingface_hub reads them, an empty variable counting as unset. One a header cannot carry
    is a ValueError naming where it came from, never quoting it (`client.clean_bearer`)."""
    for name in up.TOKEN_VARIABLES:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return clean_bearer(value), f"${name}"
    path = token_path()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    cleaned = text.replace("\r", "").replace("\n", "").strip()
    return (clean_bearer(cleaned), str(path)) if cleaned else None


def no_token() -> EndpointError:
    """What every command that needs the account's token says when there is none."""
    return EndpointError(
        "no Hugging Face token: set HF_TOKEN to a token of the account that pays for the "
        f"endpoint, or log in with hf auth login, which writes {token_path()}"
    )


def hub_token_from(variable: str, account_token: str) -> str:
    """The Hub token in the variable `--hub-token-from` names, for a gated or private checkpoint
    an endpoint fetches with a token of its own, or a refusal that quotes neither what the
    variable holds nor its name: a token typed where the name belongs is a name as far as the
    rule goes, and is quoted back by nothing. The account's own token is refused there, since it
    would then sit in an endpoint's secrets, where an `authenticated` endpoint's container is no
    place for it."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", variable):
        raise EndpointError(
            "--hub-token-from takes the name of the variable that holds the Hub token, such "
            "as HUB_READ_TOKEN, and what it was given is not a variable's name"
        )
    value = os.environ.get(variable)
    if value is None or not value.strip():
        raise EndpointError(
            "the variable --hub-token-from names is not set: put a read token there that has "
            "been granted the gated repository"
        )
    token = clean_bearer(value)
    if hmac.compare_digest(token.encode("utf-8"), account_token.encode("utf-8")):
        raise EndpointError(
            "the variable --hub-token-from names holds the account's own token, which never goes "
            "into an endpoint: make a fine-grained read token on huggingface.co that can read the "
            "gated repository and nothing else, and name its variable with --hub-token-from"
        )
    return token


# ── the wire ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes
    content_type: str


def _address(base: str) -> tuple[str, str, int]:
    """`base` as (scheme, host, port): https anywhere, or plain http to the loopback literals
    alone, where a test's stand-in listens, or an `EndpointError`."""
    try:
        parts = urlsplit(base)
        port = parts.port
    except ValueError:
        raise EndpointError(f"{base[:80]!r} is not an address quackd can reach") from None
    host = parts.hostname or ""
    if parts.username is not None or parts.password is not None or not host:
        raise EndpointError(f"{base[:80]!r} is not an address quackd sends a token to")
    if parts.scheme == "https":
        return "https", host, port or 443
    if parts.scheme == "http" and host in wire.LOOPBACK:
        return "http", host, port or 80
    raise EndpointError(
        f"{host} is not reached over https, and quackd talks to Hugging Face and PyPI over "
        "https alone: plain http goes only to 127.0.0.1 or ::1"
    )


def _request(
    method: str,
    base: str,
    path: str,
    *,
    bearer: str | None = None,
    body: Any = None,
    timeout_s: float = TIMEOUT_S,
) -> Reply:
    """One request and its whole reply, or an `EndpointError`. `bearer` rides as
    `Authorization: Bearer`, and the callers send one only to `API` and `HUB` and to an
    endpoint's recorded address. The reply is cut off past `MAX_REPLY_BYTES`, and its whole
    read is held to `timeout_s` however slowly it arrives (`client._BoundedReader`). Nothing
    raised here holds a token, since nothing raised here quotes a header."""
    scheme, host, port = _address(base)
    deadline = _Deadline()
    conn: http.client.HTTPConnection
    if scheme == "https":
        conn = http.client.HTTPSConnection(
            host, port, timeout=timeout_s, context=ssl.create_default_context()
        )
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
    conn.response_class = _bounded_reply(deadline)
    headers = {"Accept": "application/json", "User-Agent": f"quackd/{__version__}"}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    data = None
    if body is not None:
        data = json.dumps(body, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        deadline.at = time.monotonic() + timeout_s
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        chunks: list[bytes] = []
        size = 0
        while chunk := response.read(64 << 10):
            size += len(chunk)
            if size > MAX_REPLY_BYTES:
                raise EndpointError(
                    f"{host} sent more than {MAX_REPLY_BYTES} bytes for {path}, more than any "
                    "answer quackd asks for comes near"
                )
            chunks.append(chunk)
        kind = response.getheader("Content-Type") or ""
        return Reply(int(response.status), b"".join(chunks), kind)
    except EndpointError:
        raise
    except TimeoutError:
        raise EndpointError(f"{host} did not answer {path} within {timeout_s:g} s") from None
    except ValueError:
        # http.client refuses a header it cannot carry with one that quotes the header,
        # which could be a token; every token is checked before it gets here
        raise EndpointError(f"{host} was not asked {path}: a header could not be sent") from None
    except (OSError, http.client.HTTPException) as e:
        raise EndpointError(
            f"{host} could not be reached for {path}: {type(e).__name__}. Is this machine on "
            "the internet?"
        ) from None
    finally:
        conn.close()


def _printable(text: str, scrub: Sequence[str], limit: int) -> str:
    for secret in scrub:
        if secret:
            text = text.replace(secret, "<token>")
    flat = "".join(c if c.isprintable() else " " for c in text).strip()
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _listed(value: Any, what: str) -> list[Any]:
    """`value` where Hugging Face documents a list, none being an empty one, or a refusal saying
    `what` is not the shape it documents: a number or a string there would otherwise crash the
    loop that reads it with a TypeError, and say nothing a person could act on."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise EndpointError(f"Hugging Face's {what} is not the shape it documents")
    return value


def _said(reply: Reply, scrub: Sequence[str]) -> str:
    """What Hugging Face said with a refusal, made safe for a terminal: the `error` or the
    `message` of a JSON answer, or its text, every token scrubbed out, at most 200 characters."""
    text = reply.body.decode("utf-8", "replace")
    try:
        said = json.loads(text)
    except ValueError:
        said = None
    if isinstance(said, dict):
        for key in ("error", "message", "detail"):
            if isinstance(said.get(key), str) and said[key].strip():
                text = said[key]
                break
    return _printable(text, scrub, 200)


# ── the API ─────────────────────────────────────────────────────────────────────────────


class HFEndpoints:
    """Hugging Face's Inference Endpoints and the Hub, as quackd asks them, with the account's
    `token`. Each method is one request, or a few for a paged list, and answers what it read
    or an `EndpointError` that says what Hugging Face refused and holds no token. The addresses
    are the module's own (`API`, `HUB`, `PYPI`), read as each request goes, so a test points
    them at its stand-ins."""

    def __init__(self, token: str) -> None:
        self._token = clean_bearer(token)
        self.scrub: list[str] = [self._token]
        """Every token an answer or an error must never show: the account's, and any other a
        caller hands over (`also_scrub`)."""

    def also_scrub(self, *tokens: str | None) -> None:
        self.scrub.extend(t for t in tokens if t)

    def printable(self, text: str, limit: int = 300) -> str:
        """`text` from Hugging Face, an endpoint's message above all, as a terminal may show it:
        every token this client scrubs taken out, nothing a terminal would act on, and cut."""
        return _printable(text, self.scrub, limit)

    @property
    def token(self) -> str:
        return self._token

    # ── the transport ───────────────────────────────────────────────────────────────────

    def _json(self, reply: Reply, what: str) -> Any:
        if not reply.body.strip():
            return {}
        try:
            return json.loads(reply.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise EndpointError(
                f"Hugging Face answered {what} with something that is not JSON"
            ) from None

    def _refusal(self, reply: Reply, what: str) -> EndpointError:
        """What Hugging Face's refusal to `what`, a phrase such as `create arm`, means, in one
        sentence with what it said, every token taken out of it."""
        status = reply.status
        said = _said(reply, self.scrub)
        if status in (401, 403):
            return EndpointError(
                f"Hugging Face refused the token when asked to {what} (HTTP {status}: {said}): "
                "check that it is the one meant, in HF_TOKEN or the file hf auth login writes, "
                "and that it may do that",
                status=status,
            )
        if 300 <= status < 400:
            return EndpointError(
                f"Hugging Face answered {what} with a redirect, which was not followed",
                status=status,
            )
        if status == 402:
            return EndpointError(
                f"Hugging Face would not {what} without a way to pay (HTTP 402: {said}): add a "
                "payment method to the account on huggingface.co",
                status=status,
            )
        return EndpointError(
            f"Hugging Face would not {what} (HTTP {status}): {said}", status=status
        )

    def _call(
        self,
        method: str,
        base: str,
        path: str,
        what: str,
        *,
        token: str | None = None,
        body: Any = None,
        missing_ok: bool = False,
    ) -> Any:
        reply = _request(method, base, path, bearer=token, body=body)
        if missing_ok and reply.status == 404:
            return None
        if not 200 <= reply.status < 300:
            raise self._refusal(reply, what)
        return self._json(reply, what)

    def _api(self, method: str, path: str, what: str, **kw: Any) -> Any:
        return self._call(method, API, path, what, token=self._token, **kw)

    # ── the account and the provider ────────────────────────────────────────────────────

    def whoami(self) -> dict[str, Any]:
        """The account the token belongs to (`endpoint_upstream_api.WHOAMI`)."""
        said = self._call("GET", HUB, up.WHOAMI_PATH, "say whose token it is", token=self._token)
        if not isinstance(said, dict) or not isinstance(said.get("name"), str):
            raise EndpointError("Hugging Face did not say whose token it is")
        return said

    def provider(self) -> dict[str, Any]:
        """Every vendor, region and compute, with its price, asked with no token
        (`endpoint_upstream_api.PROVIDER_LISTING`)."""
        said = self._call("GET", API, up.PROVIDER_PATH, "list its GPUs and their prices")
        if not isinstance(said, dict) or not isinstance(said.get("vendors"), list):
            raise EndpointError("Hugging Face's list of GPUs is not the shape it documents")
        return said

    def quotas(self, namespace: str) -> dict[str, Any]:
        path = f"{up.QUOTAS_PATH}/{_segment(namespace)}"
        said = self._api("GET", path, f"say {namespace}'s quota")
        return said if isinstance(said, dict) else {}

    # ── endpoints ───────────────────────────────────────────────────────────────────────

    def _path(self, namespace: str, name: str | None = None, verb: str | None = None) -> str:
        path = f"{up.ENDPOINT_PATH}/{_segment(namespace)}"
        if name is not None:
            path += f"/{_segment(name)}"
        return path + (f"/{verb}" if verb else "")

    def create(self, namespace: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        said = self._api(
            "POST", self._path(namespace), f"create {payload.get('name')}", body=payload
        )
        return said if isinstance(said, dict) else {}

    def get(self, namespace: str, name: str) -> dict[str, Any] | None:
        """The endpoint as Hugging Face has it now, or None when it has none by that name."""
        said = self._api("GET", self._path(namespace, name), f"say how {name} is", missing_ok=True)
        return said if isinstance(said, dict) or said is None else {}

    def list_endpoints(self, namespace: str) -> list[dict[str, Any]]:
        """Every endpoint of `namespace`'s that carries quackd's tag, a page at a time, and no
        more pages than `MAX_LIST_PAGES`."""
        found: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_LIST_PAGES):
            query = {"tags": TAG, "limit": str(LIST_LIMIT)}
            if cursor:
                query["cursor"] = cursor
            path = f"{self._path(namespace)}?{urlencode(query)}"
            said = self._api("GET", path, f"list {namespace}'s endpoints")
            items = _listed(
                said.get("items") if isinstance(said, dict) else None,
                f"list of {namespace}'s endpoints",
            )
            found.extend(i for i in items if isinstance(i, dict))
            cursor = said.get("nextCursor") if isinstance(said, dict) else None
            if not cursor or not items:
                break
        return found

    def pause(self, namespace: str, name: str) -> dict[str, Any]:
        said = self._api("POST", self._path(namespace, name, "pause"), f"pause {name}")
        return said if isinstance(said, dict) else {}

    def resume(self, namespace: str, name: str) -> dict[str, Any]:
        """Resume `name`, or read it again when it is already running, which Hugging Face answers
        with a 400 (`endpoint_upstream_api.RESUME_RUNNING`)."""
        reply = _request("POST", API, self._path(namespace, name, "resume"), bearer=self._token)
        if reply.status == 400 and b"already running" in reply.body:
            return self.get(namespace, name) or {}
        if not 200 <= reply.status < 300:
            raise self._refusal(reply, f"resume {name}")
        said = self._json(reply, f"resume {name}")
        return said if isinstance(said, dict) else {}

    def delete(self, namespace: str, name: str) -> bool:
        """Delete `name`, and say whether there was one to delete."""
        said = self._api("DELETE", self._path(namespace, name), f"delete {name}", missing_ok=True)
        return said is not None

    def logs(self, namespace: str, name: str, tail: int = LOG_LINES) -> list[str]:
        """The last `tail` lines of `name`'s log, each scrubbed of every token and cut, read as a
        JSON list of entries when the answer is one and as text when it is not
        (`endpoint_upstream_api.LOGS_BODY`). An answer that cannot be had is no lines: these are
        quoted beside a refusal, never in place of one."""
        path = f"{self._path(namespace, name, 'logs')}?{urlencode({'tail': str(tail)})}"
        try:
            reply = _request("GET", API, path, bearer=self._token)
        except EndpointError:
            return []
        if not 200 <= reply.status < 300:
            return []
        text = reply.body.decode("utf-8", "replace")
        lines: list[str] = []
        try:
            said = json.loads(text)
        except ValueError:
            said = None
        if isinstance(said, list):
            lines = [str(e.get("line", "")) for e in said if isinstance(e, dict)]
        else:
            lines = text.splitlines()
        kept = [_printable(line, self.scrub, LOG_LINE_CHARS) for line in lines]
        return [line for line in kept if line][-tail:]

    # ── the Hub and PyPI ────────────────────────────────────────────────────────────────

    def model(self, repo: str, revision: str, *, token: str) -> dict[str, Any] | None:
        """A repository at a revision, with whether it is private or gated, or None when the Hub
        has none by that name that `token` can see (`endpoint_upstream_api.MODEL_INFO`)."""
        path = f"/api/models/{_repo(repo)}/revision/{_segment(revision)}"
        said = self._call("GET", HUB, path, f"say what {repo} is", token=token, missing_ok=True)
        return said if isinstance(said, dict) or said is None else {}

    def raw(self, repo: str, revision: str, filename: str, *, token: str) -> bytes:
        """One file of a repository at a revision (`endpoint_upstream_api.RAW_FILE`)."""
        path = f"/{_repo(repo)}/raw/{_segment(revision)}/{_segment(filename)}"
        reply = _request("GET", HUB, path, bearer=token)
        if not 200 <= reply.status < 300:
            raise self._refusal(reply, f"hand over {repo}'s {filename}")
        return reply.body

    def release(self, project: str, version: str) -> dict[str, Any] | None:
        """`project` `version` as PyPI has it, asked with no token, or None when PyPI has no such
        release (`endpoint_upstream_api.PYPI_RELEASE`)."""
        path = f"/pypi/{_segment(project)}/{_segment(version)}/json"
        said = self._call(
            "GET", PYPI, path, f"say whether {project} {version} is released", missing_ok=True
        )
        return said if isinstance(said, dict) or said is None else {}

    # ── the endpoint itself ─────────────────────────────────────────────────────────────

    def health(self, url: str) -> bool:
        """Whether the server at an endpoint's `url` answers `GET /v1/health` with ready, which
        is the request that wakes an endpoint scaled to zero too. The account's token goes there
        as the bearer only when `url` is wholly an endpoint's (`client.is_endpoint_host`), and a
        caller hands over only the address `up` recorded. Not ready yet is False, and so is a
        404, which a proxy may answer before it has a route to a replica that is starting:
        whether the endpoint is gone is the API's to say, on the wait's next read of it. A token
        Hugging Face refuses is an `EndpointError`."""
        if not client.is_endpoint_host(url):
            raise EndpointError(
                f"{url[:80]} is not a Hugging Face Endpoint's https address, so the Hugging Face "
                "token was not sent there"
            )
        try:
            reply = _request("GET", url.strip().rstrip("/"), wire.HEALTH_PATH, bearer=self._token)
        except EndpointError:
            return False  # not answering yet is what a starting endpoint does
        if reply.status in (401, 403):
            raise self._refusal(reply, "reach the endpoint")
        if reply.status != 200:
            return False
        try:
            said = json.loads(reply.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False
        return isinstance(said, dict) and said.get("ready") is True


def _segment(text: str) -> str:
    return quote(text, safe="")


def _repo(repo: str) -> str:
    owner, _, name = repo.partition("/")
    return f"{_segment(owner)}/{_segment(name)}"


# ── this machine's record of its endpoints ──────────────────────────────────────────────


@dataclass(frozen=True)
class EndpointRecord:
    """One endpoint `up` started from this machine, as it is kept in `endpoints.json`: its name
    and the account it is on, what it serves and how (`policy`, `pins`, `flags`, each as the
    container is given it), where it runs, how long it idles before it scales to zero, quackd's
    token for its server, when it was created and by which quackd, the address Hugging Face gave
    it once it had one, and the variable `--hub-token-from` named, never what it held."""

    name: str
    namespace: str
    policy: str
    instance: str
    vendor: str
    region: str
    idle_minutes: int
    token: str = field(repr=False)
    created: str
    version: str
    pins: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    url: str | None = None
    hub_token_from: str | None = None

    @property
    def endpoint(self) -> str:
        return f"hf:{self.name}"

    def public(self) -> dict[str, Any]:
        """The record as `list --json` says it: everything but the token."""
        said = asdict(self)
        said.pop("token")
        said["pins"], said["flags"] = list(self.pins), list(self.flags)
        return said


_FIELDS = {
    "name": str,
    "namespace": str,
    "policy": str,
    "instance": str,
    "vendor": str,
    "region": str,
    "idle_minutes": int,
    "token": str,
    "created": str,
    "version": str,
}


def _mark_of(token: str) -> str:
    return f"{TAG}-{hashlib.sha256(token.encode('utf-8')).hexdigest()[:12]}"


def mark(record: EndpointRecord) -> str:
    """The tag that ties an endpoint to this machine's record of it, beside quackd's own: `TAG`,
    a hyphen, and the first 12 hex digits of the SHA-256 of the record's token. `up` creates an
    endpoint with both, so one by the same name that another machine's `up` made carries that
    machine's, one made on huggingface.co carries neither, and a record names one endpoint and
    no other, whether or not it holds an address yet. It says nothing of the token: 12 hex
    digits of a hash of 64 random ones."""
    return _mark_of(record.token)


class EndpointStore:
    """`endpoints.json` under the registry's directory, `--registry-dir`, else
    `$QUACKD_REGISTRY_DIR`, else `~/.quackd`: the endpoints this machine's `up` started, by
    name. It holds each one's token, so it is written through the registry's own write
    (`registry.write_json`), readable by its owner alone where the OS allows, and on Windows
    with the permissions of the profile it sits in. Read strictly: a record that is not what
    `up` writes is refused, naming it, rather than half used, and a recorded address that is not
    wholly an endpoint's is refused, so an edited file cannot send the account's token
    anywhere else."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.path = registry_dir(base_dir) / STORE_FILE

    def read(self) -> dict[str, EndpointRecord]:
        """Every record, each checked, or an `EndpointError` naming the first that is not what
        `up` writes."""
        return {name: self._record(name, raw) for name, raw in sorted(self._load().items())}

    def _load(self) -> dict[str, Any]:
        """The records as the file holds them, unchecked but for the file being one: what
        `save` and `forget` rewrite, so one bad record never stops another being kept or
        forgotten, and `delete` can still forget the bad one."""
        if not self.path.exists():
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as e:
            raise EndpointError(
                f"{self.path} cannot be read ({type(e).__name__}): fix it or move it aside"
            ) from None
        version = loaded.get("version") if isinstance(loaded, dict) else None
        if not isinstance(version, int) or version > STORE_VERSION:
            raise EndpointError(
                f"{self.path} is not a record this quackd reads (version {version!r}): upgrade "
                "quackd, or move it aside"
            )
        section = loaded.get("endpoints") or {}
        if not isinstance(section, dict):
            raise EndpointError(f"{self.path}: endpoints must be an object of name -> record")
        return section

    def _record(self, name: str, raw: Any) -> EndpointRecord:
        def bad(why: str) -> EndpointError:
            return EndpointError(
                f"{self.path}: {name[:40]!r} {why}: quackd policy endpoint delete {name[:40]} "
                "forgets it"
            )

        if not isinstance(raw, dict) or not NAME.match(name) or raw.get("name") != name:
            raise bad("is not a record quackd policy endpoint up wrote")
        for key, kind in _FIELDS.items():
            if not isinstance(raw.get(key), kind) or isinstance(raw.get(key), bool):
                raise bad(f"has no {key}")
        pins, flags = raw.get("pins", []), raw.get("flags", [])
        if not all(isinstance(x, str) for x in [*pins, *flags]):
            raise bad("has pins or flags that are not text")
        url = raw.get("url")
        if url is not None and (not isinstance(url, str) or not client.is_endpoint_host(url)):
            raise bad("holds an address that is not a Hugging Face Endpoint's")
        hub = raw.get("hub_token_from")
        try:
            wire.clean_token(raw["token"], f"{self.path}'s record of {name}")
        except ValueError:
            raise bad("holds no token its server could take") from None
        return EndpointRecord(
            name=name,
            namespace=raw["namespace"],
            policy=raw["policy"],
            instance=raw["instance"],
            vendor=raw["vendor"],
            region=raw["region"],
            idle_minutes=raw["idle_minutes"],
            token=raw["token"],
            created=raw["created"],
            version=raw["version"],
            pins=tuple(pins),
            flags=tuple(flags),
            url=url,
            hub_token_from=hub if isinstance(hub, str) else None,
        )

    def get(self, name: str) -> EndpointRecord | None:
        raw = self._load().get(name)
        return None if raw is None else self._record(name, raw)

    def located(self, name: str) -> tuple[str | None, str | None]:
        """The account `name`'s record says its endpoint is on, and the tag `up` gave that
        endpoint from the record's token (`mark`), each read without checking the rest of the
        record, for `delete` to remove an endpoint whose record went wrong, held to that tag
        first (`confirmed`). None for either the record does not hold as `up` writes it, and for
        both with no record."""
        raw = self._load().get(name)
        if not isinstance(raw, dict):
            return None, None
        space, token = raw.get("namespace"), raw.get("token")
        return (
            space if isinstance(space, str) and ACCOUNT.match(space) else None,
            _mark_of(token) if isinstance(token, str) and token.strip() else None,
        )

    def recorded(self, name: str) -> bool:
        return name in self._load()

    def _write(self, section: Mapping[str, Any]) -> None:
        payload = {"version": STORE_VERSION, "endpoints": {k: section[k] for k in sorted(section)}}
        try:
            write_json(self.path, payload, private=True)
        except RegistryError as e:
            raise EndpointError(str(e)) from None
        except OSError as e:
            raise EndpointError(f"cannot write {self.path}: {e}") from None

    def save(self, record: EndpointRecord) -> None:
        section = self._load()
        section[record.name] = asdict(record)
        self._write(section)

    def forget(self, name: str) -> bool:
        section = self._load()
        gone = section.pop(name, None) is not None
        if gone:
            self._write(section)
        return gone


def new_record(
    name: str,
    *,
    namespace: str,
    policy: str,
    instance: str,
    vendor: str,
    region: str,
    idle_minutes: int,
    pins: Sequence[str] = (),
    flags: Sequence[str] = (),
    hub_token_from: str | None = None,
) -> EndpointRecord:
    """A record for an endpoint about to be created, with quackd's own token for its server,
    made here and nowhere else: 64 hex digits from `secrets`, as `quackd policy serve` writes
    one."""
    return EndpointRecord(
        name=name,
        namespace=namespace,
        policy=policy,
        instance=instance,
        vendor=vendor,
        region=region,
        idle_minutes=idle_minutes,
        token=secrets.token_hex(32),
        created=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        version=__version__,
        pins=tuple(pins),
        flags=tuple(flags),
        hub_token_from=hub_token_from,
    )


# ── what an endpoint is created from ────────────────────────────────────────────────────


def serve_flags(
    *,
    fps: float | None = None,
    cameras: str | None = None,
    latency_s: float | None = None,
    threads: int | None = None,
    jpeg_quality: int | None = None,
) -> tuple[str, ...]:
    """The serve flags `up` was given, as the container's `quackd policy serve` is given them:
    each that was given, in this order, and its value as text, which the serve command reads
    back to the same value."""
    given = (
        ("--fps", fps),
        ("--cameras", cameras),
        ("--latency-s", latency_s),
        ("--threads", threads),
        ("--jpeg-quality", jpeg_quality),
    )
    return tuple(part for flag, value in given if value is not None for part in (flag, str(value)))


def container_command(version: str, exclude_newer: str) -> list[str]:
    """What the container runs: uv's `uvx`, installing this quackd's release of the server's
    extra from PyPI with nothing uploaded after `exclude_newer`, so every start of the endpoint
    resolves what its first did (`endpoint_upstream_api.EXCLUDE_NEWER`), then `quackd policy
    serve`. Every part a POSIX string, since the container is Linux whatever this machine is."""
    return [
        "uvx",
        "--python",
        "3.12",
        "--exclude-newer",
        exclude_newer,
        "--from",
        f"quackd[lerobot-vla]=={version}",
        "--with",
        f"quackd-lerobot[vla]=={version}",
        "quackd",
        "policy",
        "serve",
    ]


def container_args(record: EndpointRecord) -> list[str]:
    """`quackd policy serve`'s arguments in the container: the policy, its pins and the serve
    flags as `up` was given them, then the server bound for the proxy in front, on the port the
    image names, its token from the secret quackd put it in."""
    pins = [part for pin in record.pins for part in ("--pin", pin)]
    return [
        "--policy",
        record.policy,
        *pins,
        *record.flags,
        "--bind",
        "0.0.0.0",
        "--port",
        str(CONTAINER_PORT),
        "--behind-tls",
        "--token-env",
        TOKEN_SECRET,
    ]


def create_payload(
    record: EndpointRecord,
    *,
    accelerator: str,
    exclude_newer: str,
    hub_token: str | None = None,
) -> dict[str, Any]:
    """The body `record`'s endpoint is created from (`endpoint_upstream_api.CREATE_BODY`):
    `authenticated` always, one GPU and one replica at most, since a session lives in one
    process, scaled to zero after `idle_minutes` with no request and to nothing more, uv's image
    by its digest running `container_command` with `container_args`, quackd's token in the
    secrets, a Hub token beside it only for a gated checkpoint, and quackd's tags, its own and
    the one this record gives it (`mark`). The account's token is never in it. The repository
    and revision are the checkpoint's, which Hugging Face mounts at `/repository` and the
    server never reads: the pipeline fetches what it loads through its own checks."""
    repo, _, revision = record.policy.partition("@")
    model_secrets = {TOKEN_SECRET: record.token}
    if hub_token is not None:
        model_secrets[HUB_TOKEN_SECRET] = hub_token
    return {
        "name": record.name,
        "type": up.CREATED_TYPE,
        "provider": {"vendor": record.vendor, "region": record.region},
        "compute": {
            "accelerator": accelerator,
            "instanceType": record.instance,
            "instanceSize": INSTANCE_SIZE,
            "scaling": {
                "minReplica": 0,
                "maxReplica": 1,
                "scaleToZeroTimeout": record.idle_minutes,
            },
        },
        "model": {
            "repository": repo,
            "revision": revision,
            "framework": "custom",
            "image": {
                "custom": {
                    "url": up.IMAGE,
                    "port": CONTAINER_PORT,
                    "healthRoute": wire.HEALTH_PATH,
                }
            },
            "env": {"PYTHONUNBUFFERED": "1"},
            "secrets": model_secrets,
            "command": container_command(record.version, exclude_newer),
            "args": container_args(record),
        },
        "tags": [TAG, mark(record)],
    }


def create_endpoint(
    api: HFEndpoints, store: EndpointStore, record: EndpointRecord, payload: Mapping[str, Any]
) -> str | None:
    """Create `record`'s endpoint from `payload`, with the record kept first, so an endpoint that
    exists is never one this machine holds no token for. None once Hugging Face has answered.

    A refusal, a 4xx, created nothing, and the record is forgotten again. Any other failure, no
    answer within the deadline, a connection that dropped or a 5xx, may have come after the
    endpoint was made, and forgetting the record then would leave one billing that this machine
    has no token for and cannot reach. So the endpoint is read: one that is there, with the tags
    this record gave it, is taken as created, the record kept, and what became of the answer
    comes back for `up` to say; one by that name without them is another's, so this create made
    nothing and the record is forgotten; and when there is none yet, or it cannot be read, the
    record is kept, since Hugging Face may still be making it, and the refusal says `up` finds
    out and `delete` forgets it."""
    api.also_scrub(record.token)
    store.save(record)
    try:
        api.create(record.namespace, payload)
        return None
    except EndpointError as e:
        if e.status is not None and 400 <= e.status < 500:
            store.forget(record.name)
            raise
        lost = e
    try:
        found = api.get(record.namespace, record.name)
        if found is not None:
            confirmed(found, namespace=record.namespace, name=record.name, mark=mark(record))
    except Unconfirmed:
        store.forget(record.name)  # another's by that name, so this create made nothing
        raise lost from None
    except EndpointError:
        raise EndpointError(
            f"{str(lost).rstrip('.')}, and whether {record.name} was created could not be read "
            "either, so this machine keeps its record and its token: quackd policy endpoint up "
            f"{record.name} finds out, and delete {record.name} removes it"
        ) from None
    if found is None:
        raise EndpointError(
            f"{str(lost).rstrip('.')}, and Hugging Face has no {record.name} yet, which it may "
            "still be making, so this machine keeps its record and its token: quackd policy "
            f"endpoint up {record.name} finds out, and delete {record.name} forgets it"
        ) from None
    return str(lost)


# ── before anything is created ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Compute:
    """One compute of the provider listing, as `up` reads it: the accelerator it has, its price
    an hour in USD, read on the day, and its GPU's memory."""

    vendor: str
    region: str
    instance: str
    accelerator: str
    price_per_hour: float
    gpu_memory_gb: int | None = None


def find_compute(listing: Mapping[str, Any], vendor: str, region: str, instance: str) -> Compute:
    """`instance` x1 in `vendor` `region` as the provider listing has it, or a refusal naming
    what there is instead. A compute that is not a GPU is refused, an Inferentia `neuron` above
    all, since the server's torch build has no backend for one, and so is one Hugging Face does
    not offer now."""
    gpus_listed = "list of GPUs"
    listed = _listed(listing.get("vendors"), gpus_listed)
    vendors = {v.get("name"): v for v in listed if isinstance(v, dict)}
    found = vendors.get(vendor)
    if found is None:
        raise EndpointError(
            f"Hugging Face has no vendor called {vendor}: --vendor is one of "
            f"{', '.join(sorted(str(v) for v in vendors))}"
        )
    in_vendor = _listed(found.get("regions"), gpus_listed)
    regions = {r.get("name"): r for r in in_vendor if isinstance(r, dict)}
    place = regions.get(region)
    if place is None:
        raise EndpointError(
            f"{vendor} has no region {region} on Hugging Face: --region is one of "
            f"{', '.join(sorted(str(r) for r in regions))}"
        )
    computes = [c for c in _listed(place.get("computes"), gpus_listed) if isinstance(c, dict)]
    gpus = sorted({str(c.get("instanceType")) for c in computes if c.get("accelerator") == "gpu"})
    match = next(
        (
            c
            for c in computes
            if c.get("instanceType") == instance and c.get("instanceSize") == INSTANCE_SIZE
        ),
        None,
    )
    if match is None:
        raise EndpointError(
            f"{vendor} {region} has no {instance} {INSTANCE_SIZE} on Hugging Face: the GPUs "
            f"there are {', '.join(gpus) or 'none'}"
        )
    if match.get("accelerator") != "gpu":
        raise EndpointError(
            f"{instance} is a {match.get('accelerator')} instance, and quackd policy endpoint "
            f"rents a GPU: one of {', '.join(gpus) or 'none here'}. A policy that wants no GPU "
            "runs on the laptop with quackd policy serve"
        )
    if match.get("status") not in ("available", "low_availability"):
        raise EndpointError(
            f"{instance} in {vendor} {region} is {match.get('status')} on Hugging Face today: "
            f"try another --region or --instance ({', '.join(gpus)})"
        )
    price = match.get("pricePerHour")
    if isinstance(price, bool) or not isinstance(price, int | float) or price < 0:
        raise EndpointError(f"Hugging Face's listing gives {instance} no price an hour")
    memory = match.get("gpuMemoryGb")
    return Compute(
        vendor=vendor,
        region=region,
        instance=instance,
        accelerator="gpu",
        price_per_hour=float(price),
        gpu_memory_gb=memory if isinstance(memory, int) and not isinstance(memory, bool) else None,
    )


def exclude_newer(*releases: Mapping[str, Any]) -> str:
    """The container's `--exclude-newer`: an hour after the last file of `releases` was
    uploaded, as PyPI's JSON says (`endpoint_upstream_api.PYPI_RELEASE`), as RFC 3339 in UTC.
    A `urls` that is not a list holds no upload time."""
    times = []
    for release in releases:
        files = release.get("urls")
        for file in files if isinstance(files, list) else []:
            when = file.get("upload_time_iso_8601") if isinstance(file, dict) else None
            if isinstance(when, str):
                try:
                    times.append(datetime.fromisoformat(when.replace("Z", "+00:00")))
                except ValueError:
                    continue
    if not times:
        raise EndpointError("PyPI gave no upload time for quackd's release")
    return (max(times) + EXCLUDE_NEWER_AFTER).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Preflight:
    """What `up` found before it created anything: the namespace it creates in and the account
    the token is, the compute and its price on the day, the container's `--exclude-newer`, and
    the repositories that are gated or private, which the endpoint fetches with the Hub token."""

    namespace: str
    account: str
    compute: Compute
    exclude_newer: str
    restricted: tuple[str, ...] = ()


def preflight(
    api: HFEndpoints,
    *,
    policy: str,
    pins: Sequence[str],
    instance: str,
    vendor: str,
    region: str,
    namespace: str | None = None,
    hub_token: str | None = None,
    version: str = __version__,
) -> Preflight:
    """Everything that can be checked before an endpoint exists to be billed for, cheapest
    first, or an `EndpointError` that says what to do instead:

    1. this quackd is a release on PyPI, quackd and quackd-lerobot both, since the container
       installs it by its version, so a build from a checkout is refused, and the container's
       `--exclude-newer` is read off the release;
    2. the instance is a GPU Hugging Face offers in that vendor's region, and its price;
    3. the account the token is, which is the namespace unless one is named;
    4. the account has room for one more of that GPU, where the quota listing names it;
    5. the checkpoint and every `--pin` exist at their revisions, and any that is gated or
       private is refused without a Hub token for it (`--hub-token-from`);
    6. the checkpoint's `config.json`, read at its revision, is one the server serves on a GPU
       (`pipeline.check_config`), which is the refusal a container would otherwise reach minutes
       and money later."""
    releases = []
    for project in ("quackd", "quackd-lerobot"):
        release = api.release(project, version)
        if release is None:
            raise EndpointError(
                f"{project} {version} is not a release on PyPI, and an endpoint installs quackd "
                "from PyPI by its version: a build from a checkout cannot start one. Run a "
                "released quackd"
            )
        releases.append(release)
    after = exclude_newer(*releases)
    compute = find_compute(api.provider(), vendor, region, instance)
    me = api.whoami()
    account = str(me.get("name"))
    if namespace is None:
        if me.get("type") != "user":
            raise EndpointError(
                f"the token is {account}'s, which is not a person's account: name the "
                "organisation the endpoint goes on with --namespace"
            )
        namespace = account
    if not ACCOUNT.match(namespace):
        raise EndpointError(f"--namespace {namespace[:40]!r} is not a Hugging Face account's name")
    _check_quota(api.quotas(namespace), namespace, vendor, instance)
    checkpoint, _, revision = policy.partition("@")
    restricted: list[str] = []
    for spec in (policy, *pins):
        repo, _, rev = spec.partition("@")
        info = api.model(repo, rev, token=api.token)
        if info is None:
            raise EndpointError(
                f"the Hub has no {repo} at {rev} that this token can see: check the name and the "
                "revision, a commit or a tag"
            )
        if info.get("private") is True or info.get("gated") not in (None, False):
            restricted.append(spec)
    if restricted and hub_token is None:
        named = ", ".join(restricted)
        raise EndpointError(
            f"{named} {'is' if len(restricted) == 1 else 'are'} gated or private on the Hub, and "
            "an endpoint fetches what it serves with a token of its own: give --hub-token-from "
            "VAR, naming a variable that holds a read token granted it"
        )
    reader = hub_token if policy in restricted and hub_token is not None else api.token
    raw = api.raw(checkpoint, revision, "config.json", token=reader)
    try:
        config = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise EndpointError(f"{policy}'s config.json is not JSON") from None
    from quackd_lerobot.policy import pipeline  # the server's module, and never the arm's

    try:
        pipeline.check_config(config, policy, gpu=True)
    except pipeline.PipelineRefused as e:
        raise EndpointError(str(e)) from None
    return Preflight(namespace, account, compute, after, tuple(restricted))


def _check_quota(quotas: Mapping[str, Any], namespace: str, vendor: str, instance: str) -> None:
    """Refuse an instance type the quota listing names with no room left for one more, and leave
    one it does not name to Hugging Face (`endpoint_upstream_api.QUOTA_LISTING`). A listing
    that is not the shape documented is refused, rather than taken for room."""
    listed = f"quota listing for {namespace}"
    for entry in _listed(quotas.get("vendors") if isinstance(quotas, Mapping) else None, listed):
        if not isinstance(entry, dict) or entry.get("name") != vendor:
            continue
        for quota in _listed(entry.get("quotas"), listed):
            if not isinstance(quota, dict) or quota.get("instanceType") != instance:
                continue
            most, used = quota.get("maxAccelerators"), quota.get("usedAccelerators")
            if isinstance(most, int) and isinstance(used, int) and most - used < 1:
                raise EndpointError(
                    f"{namespace} has no {instance} left in its quota on {vendor} ({used} of "
                    f"{most} in use): pause or delete one, or ask Hugging Face for more on its "
                    "Quotas page"
                )


_ASKED = {
    "start": ("starts", "Start it?"),
    "resume": ("resumes", "Resume it?"),
    "wake": ("wakes", "Wake it?"),
}


def confirmation(
    record: EndpointRecord, check: Preflight | Compute, *, doing: str = "start"
) -> str:
    """The question `up` asks before it creates an endpoint, resumes a paused one or wakes one
    that scaled to zero, each of which bills from then on, with the price read from Hugging
    Face's listing on the day (`endpoint_upstream_api.BILLING`). `doing` is `start`, `resume`
    or `wake`."""
    compute = check.compute if isinstance(check, Preflight) else check
    verb, start = _ASKED[doing]
    return (
        f"This {verb} a Hugging Face Inference Endpoint on {record.namespace}'s account: "
        f"{record.name}, serving {record.policy} on {record.instance} {INSTANCE_SIZE} in "
        f"{record.vendor} {record.region}. It bills ${compute.price_per_hour:.2f} an hour while "
        "it runs (Hugging Face's price, read just now), scales to zero after "
        f"{record.idle_minutes} minutes with no request, and bills nothing then or while "
        f"paused. {start}"
    )


# ── whether an endpoint is the one recorded ─────────────────────────────────────────────


def _same_address(a: str, b: str) -> bool:
    return a.strip().rstrip("/").lower() == b.strip().rstrip("/").lower()


def confirmed(found: Mapping[str, Any], *, namespace: str, name: str, mark: str | None) -> None:
    """Refuse `found`, an endpoint as the API answered a read of `name` on `namespace`, unless it
    is the one this machine's `up` started: it carries quackd's tag, as every endpoint `up`
    creates does, and the record's own (`mark`), which no endpoint but the one created from
    this record carries, wherever the record's token can be read, which is every record but a
    broken one `delete` reads. A record outlives what it records: an endpoint deleted on
    huggingface.co leaves this machine's record behind, and another by that name, made there or
    by another machine's `up`, would otherwise be resumed, paused or deleted in its place, or
    sent the account's token. The address is not what tells them apart: a record has none
    until Hugging Face gives one (`hold_address` holds it to that one after)."""
    tags = found.get("tags") if isinstance(found, Mapping) else None
    tags = tags if isinstance(tags, list) else []
    if TAG not in tags:
        raise Unconfirmed(
            f"{namespace}'s endpoint called {name} carries no {TAG} tag, so quackd did not "
            f"start it and leaves it as it is: this machine's record of hf:{name} is out of "
            f"date, and quackd policy endpoint delete {name} --forget forgets the record"
        )
    if mark is not None and mark not in tags:
        raise Unconfirmed(
            f"{namespace}'s endpoint called {name} carries quackd's tag but not the one this "
            f"machine's up gave hf:{name}, so another machine's up started it, and quackd leaves "
            f"it as it is: quackd policy endpoint delete {name} --forget forgets the record"
        )


def confirm(api: HFEndpoints, record: EndpointRecord) -> dict[str, Any] | None:
    """`record`'s endpoint as Hugging Face has it now, read with the account's token, or None when
    it has none by that name, and `Unconfirmed` when the one it has is not the one `up` started
    here (`confirmed`). What `up` and `pause` read before they change anything, and what a run
    reads before the account's token goes to the recorded address as the bearer (`reach`)."""
    found = api.get(record.namespace, record.name)
    if found is not None:
        confirmed(found, namespace=record.namespace, name=record.name, mark=mark(record))
    return found


def hold_address(store: EndpointStore, record: EndpointRecord, url: str | None) -> EndpointRecord:
    """`record` held to the address Hugging Face gives its endpoint now, `url`, before the
    account's token goes there as the bearer: recorded, when the record holds none and it is
    wholly an endpoint's (`EndpointStore`), and the record as it is when `url` is the recorded
    one or there is none. Another than the one recorded is refused, and is sent nothing: an
    endpoint keeps its address for its life (`endpoint_upstream_api.ADDRESS_KEPT`), and one
    that came back with another would be one this machine cannot tell from a copy of it."""
    if url is None:
        return record
    if record.url is None:
        if not client.is_endpoint_host(url):
            raise EndpointError(
                f"Hugging Face gave {record.endpoint} an address that is not an endpoint's, "
                f"{url[:80]!r}, and quackd sends the account's token to no other"
            )
        record = replace(record, url=url)
        store.save(record)
        return record
    if not _same_address(url, record.url):
        raise EndpointError(
            f"Hugging Face gives {record.endpoint} another address than the one this machine "
            "recorded for it, and the account's token was not sent there"
        )
    return record


# ── waiting for it to serve ─────────────────────────────────────────────────────────────


def state_of(endpoint: Mapping[str, Any]) -> tuple[str, str | None, str]:
    """An endpoint as the API answers it, as (state, url, message)."""
    status = endpoint.get("status") if isinstance(endpoint, Mapping) else None
    status = status if isinstance(status, Mapping) else {}
    state = status.get("state")
    url = status.get("url")
    message = status.get("errorMessage") or status.get("message") or ""
    return (
        str(state) if isinstance(state, str) else "unknown",
        url if isinstance(url, str) and url else None,
        str(message),
    )


def wait_until_serving(
    api: HFEndpoints,
    record: EndpointRecord,
    *,
    minutes: float,
    store: EndpointStore,
    said: Callable[[str, float], None] = lambda state, seconds: None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> EndpointRecord:
    """Read the endpoint every `POLL_S` until it is `running` with an address and its server
    answers ready on `/v1/health` there, as huggingface_hub waits for one
    (`endpoint_upstream_api.WAIT_POLL`), and say each state it passes with the seconds since the
    wait began. Every read is held to the record first (`confirmed`): an endpoint by that name
    without the tags `up` gave it is `Unconfirmed`, and is sent nothing. The address is
    recorded the moment Hugging Face gives it, before the account's token goes there as the
    bearer, and another one later is refused (`hold_address`). One that scaled to zero is woken
    by the first health request. An endpoint that fails is `EndpointFailed`, one paused or deleted
    meanwhile an `EndpointError`, and one that does not serve within `minutes`
    `EndpointTimeout`. The record, with its address, comes back."""
    api.also_scrub(record.token)
    start = clock()
    deadline = start + 60.0 * minutes
    last: str | None = None
    while True:
        endpoint = api.get(record.namespace, record.name)
        if endpoint is None:
            raise EndpointError(
                f"Hugging Face has no endpoint called {record.name} any more: quackd policy "
                f"endpoint delete {record.name} forgets the record"
            )
        confirmed(endpoint, namespace=record.namespace, name=record.name, mark=mark(record))
        state, url, message = state_of(endpoint)
        if state != last:
            said(state, clock() - start)
            last = state
        if state in FAILED:
            why = _printable(message, api.scrub, 300)
            raise EndpointFailed(f"{record.endpoint} reached {state}" + (f": {why}" if why else ""))
        if state == PAUSED:
            raise EndpointError(
                f"{record.endpoint} was paused while quackd waited for it: quackd policy endpoint "
                f"up {record.name} resumes it"
            )
        record = hold_address(store, record, url)
        if url is not None and state in (SERVING, ASLEEP) and api.health(url):
            return record
        if clock() >= deadline:
            raise EndpointTimeout(
                f"{record.endpoint} did not serve within {minutes:g} minutes, and was last {state}"
            )
        sleep(POLL_S)


def pause_and_quote(api: HFEndpoints, record: EndpointRecord) -> tuple[list[str], str]:
    """Pause `record`'s endpoint, which stops its bill, and hand back the last lines of its log,
    scrubbed of every token, for the refusal to quote, with a clause saying how its bill stands
    now: paused, gone from Hugging Face, or not paused, and why. A pause that fails is no
    reason to hide the log, and is said beside it."""
    api.also_scrub(record.token)
    lines = api.logs(record.namespace, record.name)
    try:
        api.pause(record.namespace, record.name)
    except EndpointError as e:
        if e.status == 404:
            return lines, "and Hugging Face has no such endpoint any more, so it bills nothing"
        return lines, (
            f"and it could not be paused, so it may still bill: {str(e).rstrip('.')}. Pause it "
            f"with quackd policy endpoint pause {record.name}, or on huggingface.co"
        )
    return lines, "so it was paused, and bills nothing now"


# ── how a run reaches one ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reached:
    """An endpoint as the arm's client reaches it: the address `up` recorded, quackd's token for
    its server, the account's token as the bearer for the proxy, and its `hf:NAME`."""

    url: str
    token: str = field(repr=False)
    bearer: str = field(repr=False)
    endpoint: str


def reach(
    given: str, *, policy_token: str | None = None, registry_dir: str | Path | None = None
) -> Reached | None:
    """What a run's `--policy-url` reaches, when it is an endpoint this machine started: for
    `hf:NAME`, the record `up` kept of NAME, and for an https address, the record whose address
    it is. None for any other address, which the client reaches as it reaches any server.

    The token is `--policy-token` when one was typed, else the record's, and never
    `QUACKD_POLICY_TOKEN` or `~/.quackd/policy.token`, which are other servers'. The bearer is
    the account's token (`hf_token`). `hf:NAME` with no record, or with no address yet, or
    with no account token to send, is a ValueError that says what to do, before anything
    connects. A record that cannot be read refuses `hf:NAME`, and leaves an https address to be
    reached as any other.

    Then the endpoint is read from the API with the account's token, one request, and the
    record is bound to what Hugging Face answers before the token goes anywhere else: the
    endpoint by that name on the recorded account has to carry the tags `up` gave it, quackd's
    and the record's own (`confirm`), at the recorded address. The record is a file in the
    registry's directory, which a `.env` in the working directory can name with
    `QUACKD_REGISTRY_DIR`, so a project cloned from somebody else could plant one naming an
    endpoint of theirs, and a record outlives an endpoint deleted on huggingface.co. Either is
    refused here, in a sentence, and nothing is sent to the address it holds. That the API
    answers a read of an account's endpoints only to that account and its organisations'
    members is what this trusts (`endpoint_upstream_api.NAMESPACE_READS`)."""
    text = given.strip()
    match = POLICY_ENDPOINT.fullmatch(text)
    store = EndpointStore(registry_dir)
    if match is not None:
        name = match.group(1)
        record = store.get(name)
        if record is None:
            raise EndpointError(
                f"no endpoint called {name} is recorded on this machine: start it with quackd "
                f"policy endpoint up {name} --policy REPO@REVISION, and quackd policy endpoint "
                "list says what there is"
            )
        if record.url is None:
            raise EndpointError(
                f"{text} has no address yet: quackd policy endpoint up {name} waits until it "
                "has one"
            )
    else:
        if not text.lower().startswith("https://"):
            return None
        try:
            records = store.read()
        except EndpointError:
            return None
        found = [r for r in records.values() if r.url is not None and _same_address(r.url, text)]
        if not found:
            return None
        record = found[0]
    account = hf_token()
    if account is None:
        raise EndpointError(
            f"no Hugging Face token to reach {record.endpoint} with: set HF_TOKEN to a token of "
            f"the account it runs on, {record.namespace}, or log in with hf auth login"
        )
    token = record.token
    if policy_token is not None and policy_token.strip():
        token = wire.clean_token(policy_token, "--policy-token")
    assert record.url is not None
    api = HFEndpoints(account[0])
    api.also_scrub(record.token, token)
    live = confirm(api, record)
    if live is None:
        raise EndpointError(
            f"Hugging Face has no endpoint called {record.name} on {record.namespace}'s account, "
            f"so nothing was sent to the address this machine recorded for {record.endpoint}: "
            f"quackd policy endpoint delete {record.name} forgets the record"
        )
    state, url, _ = state_of(live)
    if url is None:
        raise EndpointError(
            f"Hugging Face gives {record.endpoint} no address while it is {state}, so nothing was "
            f"sent to the one this machine recorded: quackd policy endpoint up {record.name} "
            "waits until it has one"
        )
    if not _same_address(url, record.url):
        raise EndpointError(
            f"Hugging Face gives {record.endpoint} another address than the one this machine "
            f"recorded, so nothing was sent to either: quackd policy endpoint delete {record.name} "
            "removes it, and up starts it afresh"
        )
    return Reached(record.url, token, account[0], record.endpoint)


__all__ = [
    "API",
    "HUB",
    "PYPI",
    "TAG",
    "Compute",
    "EndpointError",
    "EndpointFailed",
    "EndpointRecord",
    "EndpointStore",
    "EndpointTimeout",
    "HFEndpoints",
    "Preflight",
    "Reached",
    "Unconfirmed",
    "confirm",
    "confirmation",
    "confirmed",
    "container_args",
    "container_command",
    "create_endpoint",
    "create_payload",
    "exclude_newer",
    "find_compute",
    "hf_token",
    "hold_address",
    "hub_token_from",
    "mark",
    "new_record",
    "no_token",
    "pause_and_quote",
    "preflight",
    "reach",
    "serve_flags",
    "state_of",
    "token_path",
    "wait_until_serving",
]
