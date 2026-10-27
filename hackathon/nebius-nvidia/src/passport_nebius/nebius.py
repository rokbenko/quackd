"""Nebius Token Factory, as this edition reaches it: an OpenAI-compatible API, one key, and the
live model list, which is the only place a model id is ever taken from. A robot's own model
server, on its own computer, is reached the same way, with a key of its own.

Nebius's own pages disagree on the base URL and third-party catalogues disagree on the case of
the NVIDIA ids, so nothing here names a model. An id is accepted when the list a key can see
carries it, spelled exactly as the list spells it. What the list does not say about a model
(whether it reads images, what it costs) is reported as not said, never guessed.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
KEY_ENV = "NEBIUS_API_KEY"
BASE_URL_ENV = "NEBIUS_BASE_URL"
MODEL_ENV = "NEBIUS_MODEL"
#: The key a robot's own model server takes, when it was started with one.
ONBOARD_KEY_ENV = "ONBOARD_API_KEY"
#: What goes as the key to a server started with none: quackd's local provider's own word for it.
NO_KEY = "not-needed"
NVIDIA_PREFIX = "nvidia/"
TIMEOUT_S = 30.0
#: What a reasoning model may write before its answer, removed before anything reads one.
_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class NebiusError(Exception):
    """Something a model server or the settings for it refused, said in one line."""


def _address(url: str) -> str:
    url = url.rstrip("/")
    if not url.startswith(("https://", "http://")):
        raise NebiusError(f"{url!r} is not an http or https URL")
    return url


def tokenfactory_url(base_url: str | None = None) -> str:
    """Token Factory's address: the one given, else $NEBIUS_BASE_URL, else its documented one."""
    return _address(base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL)


@dataclass(frozen=True)
class Settings:
    """Where a model server is and the key it takes: Token Factory, or a robot's own."""

    base_url: str
    api_key: str = field(repr=False)
    name: str = "Token Factory"
    """What an error calls the server."""
    key_env: str = KEY_ENV
    """Where its key is set, for an error that says to check it."""

    @classmethod
    def from_env(cls, base_url: str | None = None, api_key: str | None = None) -> Settings:
        url = tokenfactory_url(base_url)
        key = api_key or os.environ.get(KEY_ENV)
        if not key:
            raise NebiusError(f"needs {KEY_ENV} (set it in .env or the environment)")
        return cls(url, key)

    @classmethod
    def onboard(cls, base_url: str) -> Settings:
        """A robot's own model server, on its own computer: the address given, and
        $ONBOARD_API_KEY when the server was started with a key. Never the Token Factory key,
        which is for Token Factory alone."""
        key = os.environ.get(ONBOARD_KEY_ENV, "").strip() or NO_KEY
        return cls(_address(base_url), key, name="the onboard server", key_env=ONBOARD_KEY_ENV)


@dataclass(frozen=True)
class Model:
    """One row of the model list, with only what the list itself said."""

    id: str
    name: str | None = None
    modality: str | None = None
    context_length: int | None = None
    features: tuple[str, ...] = ()
    pricing: tuple[tuple[str, str], ...] = ()

    @property
    def nvidia(self) -> bool:
        return self.id.lower().startswith(NVIDIA_PREFIX)

    @property
    def takes_images(self) -> bool | None:
        """Whether the list says this model reads images: the input side of its modality, as
        in `text+image->text`. None when the list gives no modality."""
        if not self.modality:
            return None
        given = self.modality.split("->", 1)[0]
        return "image" in given.lower()


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _model(entry: Any) -> Model | None:
    """A row of `GET /v1/models`, in either shape Token Factory documents: the plain one with
    an id, or the rich one `verbose=true` asks for. A row with no id is no model at all."""
    if not isinstance(entry, dict) or not _text(entry.get("id")):
        return None
    architecture = entry.get("architecture")
    modality = _text(architecture.get("modality")) if isinstance(architecture, dict) else None
    context = entry.get("context_length")
    features = entry.get("supported_features")
    pricing = entry.get("pricing")
    return Model(
        id=str(entry["id"]),
        name=_text(entry.get("name")),
        modality=modality,
        context_length=context if isinstance(context, int) and context > 0 else None,
        features=tuple(f for f in features if isinstance(f, str))
        if isinstance(features, list)
        else (),
        pricing=tuple((str(k), str(v)) for k, v in pricing.items())
        if isinstance(pricing, dict)
        else (),
    )


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is an error here: following one would carry the key to whatever it named."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirects)


def _get(settings: Settings, path: str, timeout: float) -> Any:
    request = urllib.request.Request(
        settings.base_url + path,
        headers={"Authorization": f"Bearer {settings.api_key}", "Accept": "application/json"},
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise NebiusError(
                f"{settings.name} refused the key ({e.code}): check {settings.key_env}"
            ) from None
        if 300 <= e.code < 400:
            raise NebiusError(
                f"{settings.base_url}{path} answered a redirect ({e.code}), which is not "
                "followed, since it would carry the key elsewhere"
            ) from None
        raise NebiusError(f"{settings.base_url}{path} answered {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NebiusError(f"could not reach {settings.base_url}: {e}") from None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise NebiusError(
            f"{settings.base_url}{path} answered something that is not JSON"
        ) from None


def list_models(settings: Settings, *, timeout: float = TIMEOUT_S) -> list[Model]:
    """Every model the key can use, as the list says it, in the list's own order."""
    answer = _get(settings, "/models?verbose=true", timeout)
    rows = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        raise NebiusError("the model list has no data array")
    return [m for m in (_model(row) for row in rows) if m is not None]


def nvidia_models(models: Sequence[Model]) -> list[Model]:
    return [m for m in models if m.nvidia]


def resolve(models: Sequence[Model], wanted: str, *, where: str = "this key's model list") -> Model:
    """The row for `wanted`, which must be an NVIDIA model the list carries, spelled exactly as
    the list spells it. A difference only in case is refused with the list's own spelling, since
    the server matches ids exactly and catalogues disagree on case."""
    for m in models:
        if m.id == wanted:
            if not m.nvidia:
                raise NebiusError(
                    f"{wanted} is not an NVIDIA model, and this edition runs on NVIDIA's own"
                )
            return m
    for m in models:
        if m.id.lower() == wanted.lower():
            raise NebiusError(f"the list spells it {m.id}, not {wanted}")
    offered = ", ".join(m.id for m in nvidia_models(models)) or "none"
    raise NebiusError(f"{wanted} is not on {where}; NVIDIA models there: {offered}")


def onboard_model(models: Sequence[Model], wanted: str | None = None) -> Model:
    """The model a robot's own server serves: `wanted` when given, else the one NVIDIA model on
    its list. A server such as vLLM lists the one model it was started with, under the name it
    was given, so the list names it and nothing here has to."""
    if wanted:
        return resolve(models, wanted, where="the onboard server's model list")
    served = nvidia_models(models)
    if len(served) == 1:
        return served[0]
    listed = ", ".join(m.id for m in models) or "nothing"
    if not served:
        raise NebiusError(f"the onboard server serves no NVIDIA model (its list: {listed})")
    raise NebiusError(
        f"the onboard server serves more than one NVIDIA model ({listed}): --onboard-model "
        "names one"
    )


def strip_thinking(text: str) -> str:
    """An answer without the reasoning a model may have written in front of it."""
    return _THINKING.sub("", text).strip()


def sdk_client(settings: Settings) -> Any:
    """An OpenAI SDK client for Token Factory that sends the key and nothing from the
    environment, and follows no redirect.

    Releases of the SDK read `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` by
    themselves, which would put another service's credential or account on a request to Nebius.
    quackd's own rules for that, in `quackd.agent.providers.openai_headers`, answer every header
    the SDK would add; a client that would still send one is refused, as quackd refuses it."""
    import openai

    from quackd.agent.providers.openai_headers import (
        CUSTOM_HEADERS_ENV,
        ambient_headers,
        foreign_authorization,
        foreign_headers,
        is_sdk_own_header,
        own_headers,
        request_headers,
    )

    bearer = f"Bearer {settings.api_key}"
    http = openai.DefaultHttpxClient(follow_redirects=False)
    own: dict[str, str] = {}
    if any(is_sdk_own_header(n) for n in ambient_headers(os.environ.get(CUSTOM_HEADERS_ENV))):
        own = own_headers(
            openai.OpenAI(api_key=settings.api_key, base_url=settings.base_url, http_client=http)
        )
    handed = request_headers(bearer, at_openai=False, omit=openai.Omit, own=own)
    client = openai.OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        default_headers=handed,
        http_client=http,
        timeout=TIMEOUT_S,
        max_retries=2,
    )
    built = dict(client.default_headers)
    if foreign_authorization(built, bearer, openai.Omit) or foreign_headers(
        built, handed, openai.Omit
    ):
        raise NebiusError(
            f"openai {openai.__version__} would send a line of {CUSTOM_HEADERS_ENV}, or "
            "OPENAI_ORG_ID or OPENAI_PROJECT_ID, to Token Factory, which none of them is for: "
            "take them out of the environment for this command"
        )
    return client


def ask(
    settings: Settings,
    model: str,
    messages: list[dict[str, Any]],
    *,
    schema: dict[str, Any] | None = None,
    max_tokens: int = 1200,
) -> str:
    """One chat completion's text. With `schema`, the server is asked for JSON in that shape,
    falling back to plain JSON mode and then to none when it refuses the request: the caller
    validates whatever comes back either way, since no format option is a promise."""
    import openai

    client = sdk_client(settings)
    formats: list[dict[str, Any] | None] = [None]
    if schema is not None:
        formats = [
            {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
            {"type": "json_object"},
            None,
        ]
    for i, response_format in enumerate(formats):
        extra: dict[str, Any] = {}
        if response_format is not None:
            extra["response_format"] = response_format
        try:
            reply = client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0,
                max_tokens=max_tokens,
                **extra,
            )
        except openai.BadRequestError as e:
            if i + 1 < len(formats):
                continue
            raise NebiusError(f"Token Factory refused the request: {e.message}") from None
        except openai.AuthenticationError:
            raise NebiusError(f"Token Factory refused the key: check {KEY_ENV}") from None
        except openai.APIStatusError as e:
            raise NebiusError(f"Token Factory answered {e.status_code}: {e.message}") from None
        except openai.APIConnectionError:
            raise NebiusError(f"could not reach {settings.base_url}") from None
        if not reply.choices:
            raise NebiusError(f"{model} answered with no choices")
        content = reply.choices[0].message.content
        text = strip_thinking(content or "")
        if not text:
            raise NebiusError(f"{model} answered with no text")
        return text
    raise NebiusError("unreachable: every format was tried")  # pragma: no cover


def json_in(text: str) -> Any:
    """The first JSON object in a model's answer, fenced or not."""
    start = text.find("{")
    if start < 0:
        raise NebiusError("the answer holds no JSON object")
    try:
        value, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise NebiusError(f"the answer's JSON does not parse: {e.msg}") from None
    return value
