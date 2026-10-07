"""OpenRouter as the duck's brain: one key, other vendors' models, over OpenAI's API.

OpenRouter is a router rather than a lab. Its ids name another vendor's model
(`anthropic/claude-opus-5.5`), it lists hundreds of them, and it decides per request which
provider serves the call. So it is the OpenAI provider with a different base URL and key, like
every OpenAI-shaped vendor here, plus five things none of the others needs (ADR-0050):

- **Ids it lists and quackd does not.** The catalogue carries six (`OPEN_ENDED`). An id it does
  not carry is refused on its spelling alone where quackd will not take it whatever the list
  says (`factory._open_ended_refusal`), and otherwise checked here against OpenRouter's public
  model list before the first paid call. That list needs no key, and the same entry says whether the
  model takes an image and what it costs.
- **One call per turn, asked for where it can be.** A row quackd carries is told to call a tool
  (`required`), except the Claude rows that refuse a forced call, which are asked (`auto`). An
  id quackd does not carry is asked, or sent no `tool_choice` at all where its entry lists none,
  the bargain ADR-0014 strikes with local models: nobody here has seen how each upstream behind
  OpenRouter words a refusal of a forced call, and a reader written against a guessed sentence
  is how the thinking retry in `anthropic.py` came to miss the real one. `parallel_tool_calls`
  is never sent: 13 of the 465 models on OpenRouter's list when it was first read on
  2026-10-06 named it, and the
  `require_parameters` below would have routed a request carrying it almost nowhere.
- **An endpoint that honours what was asked.** Every request carries
  `provider: {"require_parameters": true}` underneath the caller's own `--extra-body`, so
  OpenRouter routes only to providers that support `tools` and `tool_choice`, rather than to
  one that "will ignore unknown parameters", in its own words.
- **Reasoning handed back as it came.** OpenRouter returns a model's reasoning as
  `reasoning_details`, which carry Claude's signed thinking and Gemini's thought signatures, and
  wants them back unmodified on the assistant turn that produced them. Its Opus 5.5 guide says
  requests through it "are not subject to" Anthropic's thinking-binding enforcement, which is
  why the camera frames here are trimmed on every call rather than in the steps `anthropic.py`
  takes for Opus 5.5.
- **A bill per call.** `usage.cost` is what OpenRouter charged, and it is the turn's cost
  (`pricing.turn_cost`), with the upstream charge added on a bring-your-own-key turn.

It is only ever asked on Chat Completions. OpenRouter has a Responses API as well, but all of
the above is chat-shaped, and the 400 that moves an OpenAI run there would reach this provider
as OpenAI's sentence relayed inside OpenRouter's envelope, which nobody here has seen.

No OpenRouter model has answered a real quackd request. Everything here was written from
OpenRouter's own documentation, read on 2026-10-06, and run against a stand-in.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import http.client
import json
import math
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from quackd import __version__
from quackd.agent.providers.base import (
    Decision,
    Exchange,
    ProviderError,
    ProviderTurn,
)
from quackd.agent.providers.catalogue import Price, default_model_for, find_model
from quackd.agent.providers.openai import (
    OpenAIProvider,
    _wants_the_responses_api,
    parse_response,
    read_usage,
)

BASE_URL = "https://openrouter.ai/api/v1"

ATTRIBUTION: dict[str, str] = {
    "HTTP-Referer": "https://github.com/rokbenko/quackd",
    "X-OpenRouter-Title": "quackd",
}
"""Sent with every request, the model list's included. OpenRouter credits an app on its public
rankings by these two headers: its quickstart calls both optional, and its attribution page says
`HTTP-Referer` is the one an app page needs. They name quackd and say nothing about whoever is
running it. `X-OpenRouter-Title` is the current spelling; `X-Title` is "still supported for
backwards compatibility", and sending both would only say the same thing twice."""

DEFAULT_BODY: dict[str, Any] = {"provider": {"require_parameters": True}}
"""What every request carries unless `--extra-body` says otherwise. Merged UNDER the caller's
body and inside its `provider` object, so `{"provider": {"sort": "price"}}` keeps it and an
explicit `{"provider": {"require_parameters": false}}` turns it off."""

REFUSED_BODY_KEYS: dict[str, str] = {
    "models": (
        "a fallback list lets OpenRouter answer with a model `run_start` does not name, "
        "priced as that model"
    ),
}
"""`--extra-body` keys refused on OpenRouter for the reason `model` is refused everywhere."""

RESPONSES_HINT = (
    "OpenRouter passed on OpenAI's refusal of function tools on Chat Completions for this "
    "model, and quackd does not move an OpenRouter run to the Responses API: pick another "
    "model, `quackd list-models --llm openrouter`."
)

LIST_TIMEOUT_S = 10.0
"""How long the model list may take before the run is refused. It is a public GET a few
hundred kilobytes long; a host that cannot answer it in ten seconds will not answer a turn."""

_LISTINGS: dict[str, list[dict[str, Any]]] = {}
"""The model list, read once per process and base URL. A preflight sweep and a pilot flock build
a provider per run, and none of them needs the same list fetched again."""


@dataclass(frozen=True, slots=True)
class Listed:
    """What OpenRouter's own list says about one id the catalogue does not carry."""

    id: str
    vision: bool
    """Whether the entry names `image` among its input modalities."""
    tool_choice: bool
    """Whether `tool_choice` is among its supported parameters. Without it the field is not
    sent at all, because `require_parameters` would route a request carrying it nowhere."""
    price: Price | None
    """Its base per-token rates as dollars per million, None where the list prices it as
    variable (`-1`) or not at all."""


def _field(obj: Any, name: str) -> Any:
    """One field of a response object, whichever shape the SDK left it in.

    The SDK builds its own models for the fields OpenAI defines and keeps the rest of a
    response as they arrived, so OpenRouter's additions come back as attributes on some objects
    and as plain dicts inside others."""
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _plain(value: Any) -> Any:
    """A response fragment as plain JSON-shaped data, for handing back on a later request."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _plain(dump())
    if hasattr(value, "__dict__"):
        return {k: _plain(v) for k, v in vars(value).items() if not k.startswith("_")}
    return value


def fetch_models(base_url: str, *, timeout_s: float = LIST_TIMEOUT_S) -> list[dict[str, Any]]:
    """`GET {base_url}/models`, OpenRouter's public model list, or a ProviderError saying why not.

    The standard library rather than the SDK's own client, so that a machine without the extra
    still hears that the model was wrong before it hears that the SDK is missing, and so that the
    suite can test it with no SDK at all. It sends no key: the list does not need one, and the
    key has not been read yet. It does send quackd's own User-Agent: OpenRouter answers a request
    that looks like Anthropic's client with a list in Anthropic's shape instead.

    A list it cannot read is a refusal, never an empty list. An empty list would turn "the host
    is down" into "your model does not exist", which sends the reader to fix the wrong thing. So
    is a list in another shape: the one OpenRouter serves an Anthropic client has `data` and
    `id`s too, but no entry in it names `supported_parameters`, which every one of the 465 on
    the real list did when it was first read on 2026-10-06, and read as OpenRouter's it would
    refuse every model there
    for having no tool calling.

    `timeout_s` bounds the whole of it, not each read. A socket timeout alone lets a server that
    sends one byte a second hold a run before it starts for as long as it likes, so the GET runs
    on a thread of its own and the run is refused once the time is up, whatever that thread is
    still waiting on.
    """
    url = base_url.rstrip("/") + "/models"
    host = urllib.parse.urlsplit(url).netloc or url
    got: dict[str, Any] = {}

    def fetch() -> None:
        try:
            got["payload"] = _get(url, timeout_s)
        except BaseException as e:  # handed to the caller, which says what it was
            got["error"] = e

    worker = threading.Thread(target=fetch, name="openrouter-models", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise ProviderError(
            f"openrouter: {host} did not answer the model list within {timeout_s:g} s, so the "
            "model could not be checked against it"
        )
    error = got.get("error")
    if isinstance(error, urllib.error.HTTPError):
        raise ProviderError(
            f"openrouter: {host} answered the model list with HTTP {error.code}, so the model "
            "could not be checked against it"
        ) from error
    if isinstance(error, urllib.error.URLError | TimeoutError | OSError):
        why = getattr(error, "reason", None) or error
        raise ProviderError(
            f"openrouter: could not reach {host} to check the model against its list ({why})"
        ) from error
    if isinstance(error, http.client.HTTPException | ValueError):
        # a reply that is not HTTP at all (a captive portal), one cut short, or an address
        # urllib cannot use, such as a --base-url with no scheme
        raise ProviderError(
            f"openrouter: could not read the model list from {host} "
            f"({type(error).__name__}: {error})"
        ) from error
    if error is not None:
        raise error
    payload = got["payload"]
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProviderError(f"openrouter: {url} did not answer with a model list: not JSON") from e
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not data:
        why = "no models in it"
    elif not all(isinstance(m, dict) and isinstance(m.get("id"), str) for m in data):
        why = "an entry with no id"
    elif not any("supported_parameters" in m for m in data):
        why = "no entry says which parameters it supports"
    else:
        return data
    raise ProviderError(f"openrouter: {url} did not answer with OpenRouter's model list: {why}")


def _get(url: str, timeout_s: float) -> bytes:
    """The bytes of one GET, with quackd's own headers and no key."""
    headers = {"Accept": "application/json", "User-Agent": f"quackd/{__version__}", **ATTRIBUTION}
    request = urllib.request.Request(url, headers=headers)
    # Proxies from the environment only, which is what the SDK's httpx client honours. urllib's
    # own default would also read the Windows registry, and the list and the turns that follow
    # it could then leave the machine by different routes.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(urllib.request.getproxies_environment())
    )
    with opener.open(request, timeout=timeout_s) as response:
        payload: bytes = response.read()
    return payload


def listing(base_url: str) -> list[dict[str, Any]]:
    """The model list behind `base_url`, fetched at most once per process. A failed fetch is
    not kept, so the next provider built asks again."""
    key = base_url.rstrip("/")
    if key not in _LISTINGS:
        _LISTINGS[key] = fetch_models(key)
    return _LISTINGS[key]


def _rate(value: Any) -> float | None:
    """One of the list's per-token rates as dollars per million tokens, None if it is not one.

    `Decimal`, because the list writes rates as strings like `"0.0000001"` and the float
    product of that and a million is 0.09999999999999999, not 0.1. A negative rate is how the
    list says "variable", and it is not a rate."""
    if value is None or isinstance(value, bool):
        return None
    try:
        exact = Decimal(str(value))
    except InvalidOperation:
        return None
    if not exact.is_finite() or exact < 0:
        return None
    return float(exact * 1_000_000)


def listed_price(pricing: Any, *, checked: str) -> Price | None:
    """An entry's base rates as a `Price`, or None where its prompt or completion rate is not
    a rate. The `overrides` some entries carry for long prompts are left out, the short-band
    rule the catalogue keeps for every vendor."""
    if not isinstance(pricing, Mapping):
        return None
    prompt, completion = _rate(pricing.get("prompt")), _rate(pricing.get("completion"))
    if prompt is None or completion is None:
        return None
    return Price(
        prompt,
        completion,
        _rate(pricing.get("input_cache_read")),
        _rate(pricing.get("input_cache_write")),
        source="openrouter",
        checked=checked,
    )


def admit(
    model: str,
    *,
    base_url: str = BASE_URL,
    source: str = "--llm",
    today: _dt.date | None = None,
) -> Listed:
    """Check an id the catalogue does not carry against OpenRouter's own list, before any key.

    Three refusals, each naming where the id came from: not on the list (with the nearest ids
    that are, when any come close), on it without tool calling, and an `expiration_date` of
    today or earlier. A future expiry is taken: the model answers today, and the run is
    today."""
    models = listing(base_url)
    entry = next((m for m in models if m.get("id") == model), None)
    if entry is None:
        ids = [str(m["id"]) for m in models]
        near = difflib.get_close_matches(model.lower(), ids, n=3, cutoff=0.6)
        nearest = f" Nearest on the list: {', '.join(near)}." if near else ""
        raise ProviderError(
            f"openrouter: {model!r} from {source} is not on OpenRouter's model list.{nearest} "
            "See `quackd list-models --llm openrouter` for the ids quackd carries."
        )
    supported = entry.get("supported_parameters")
    supported = supported if isinstance(supported, list) else []
    if "tools" not in supported:
        raise ProviderError(
            f"openrouter: {model!r} from {source} is on OpenRouter's list without tool calling, "
            "and every verb quackd has is a function tool."
        )
    day = today or _dt.datetime.now(_dt.UTC).date()
    expires = entry.get("expiration_date")
    if isinstance(expires, str) and expires.strip():
        try:
            when = _dt.date.fromisoformat(expires.strip()[:10])
        except ValueError:
            when = None
        if when is not None and when <= day:
            raise ProviderError(
                f"openrouter: {model!r} from {source} expired on OpenRouter on {when.isoformat()}."
            )
    architecture = entry.get("architecture")
    modalities = architecture.get("input_modalities") if isinstance(architecture, dict) else None
    return Listed(
        id=model,
        vision=isinstance(modalities, list) and "image" in modalities,
        tool_choice="tool_choice" in supported,
        price=listed_price(entry.get("pricing"), checked=day.isoformat()),
    )


def _number(value: Any) -> float | None:
    """A non-negative finite number, or None. A bool is not a number here, and nor is text."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def billed_usd(usage: Any) -> float | None:
    """What OpenRouter says a call cost, in dollars, or None where it does not say.

    `usage.cost` is OpenRouter's charge. On a bring-your-own-key call that is only OpenRouter's
    fee, and the upstream provider bills the caller's own key for the rest, which OpenRouter
    reports as `cost_details.upstream_inference_cost`, so the two are added. A BYOK call that
    reports no upstream figure is not billed at the fee alone, which would understate it by the
    whole of the model's charge: it falls back to the rate. An upstream figure on a call that
    is not BYOK is not added either, or the same tokens would be charged twice."""
    cost = _number(_field(usage, "cost"))
    if cost is None:
        return None
    if _field(usage, "is_byok") is True:
        upstream = _number(_field(_field(usage, "cost_details"), "upstream_inference_cost"))
        return None if upstream is None else round(cost + upstream, 9)
    return round(cost, 9)


def _describe(error: Any) -> str:
    """OpenRouter's error object as one line: its code, its message, which provider it came
    from and the start of what that provider said, which is where the real reason usually is."""
    code = _field(error, "code")
    message = _field(error, "message") or "no message"
    metadata = _field(error, "metadata")
    upstream = _field(metadata, "provider_name")
    raw = _field(metadata, "raw")
    text = f"{code}: {message}" if code is not None else str(message)
    if upstream:
        text += f" (from {upstream})"
    if raw:
        text += f": {str(raw)[:300]}"
    return text


def _as_object(value: Any) -> Any:
    """A usage block as attributes, whichever shape the SDK left it in: a response it could not
    build its own models for keeps OpenRouter's fields as plain dicts."""
    if isinstance(value, Mapping):
        return SimpleNamespace(**{str(k): _as_object(v) for k, v in value.items()})
    return value


def _billed_error(text: str, response: Any) -> ProviderError:
    """A ProviderError carrying what the failed call was billed and what it used, if it says."""
    error = ProviderError(text)
    usage = _field(response, "usage")
    error.billed_usd = billed_usd(usage)
    if usage is not None:
        error.usage = read_usage(_as_object(usage))
    return error


def parse_openrouter(response: Any) -> ProviderTurn:
    """One Chat Completions response from OpenRouter, read the way its errors are shaped.

    OpenRouter can answer `200` with an `error` object and no `choices` ("Check the body for an
    `error` field even on a `200`", its errors page), and a choice can end with
    `finish_reason: "error"` when the provider failed partway. Both are a ProviderError in
    OpenRouter's own words rather than a turn with no call, which the loop would re-prompt. A
    refusal (`message.refusal`) is a turn with no call that says so. What `parse_response` does
    not read is added here: the `reasoning_details` to hand back next turn, and the bill.

    An error can still arrive with a bill, for what the provider used before it gave out, so
    the ProviderError carries the bill and the tokens and the loop counts them like any call's."""
    error = _field(response, "error")
    if error:
        raise _billed_error(f"openrouter answered with an error: {_describe(error)}", response)
    choices = _field(response, "choices")
    if not choices:
        raise _billed_error("openrouter answered with no choices and no error", response)
    choice = choices[0]
    failed = _field(choice, "error")
    if failed or _field(choice, "finish_reason") == "error":
        raise _billed_error(
            "openrouter: the provider failed partway through the answer: "
            f"{_describe(failed or {})}",
            response,
        )
    turn = parse_response(response)
    message = _field(choice, "message")
    updates: dict[str, Any] = {"billed_usd": billed_usd(_field(response, "usage"))}
    refusal = _field(message, "refusal")
    if isinstance(refusal, str) and refusal.strip():
        updates["tool_calls"] = []
        updates["text"] = f"[refusal] {refusal.strip()}"
    details = _field(message, "reasoning_details")
    if isinstance(details, list | tuple) and details:
        updates["raw"] = {"reasoning_details": _plain(details)}
    return turn.model_copy(update=updates)


def with_default_body(given: Mapping[str, Any] | None) -> dict[str, Any]:
    """The caller's `--extra-body` over `DEFAULT_BODY`, merged one level into `provider`."""
    body = dict(given or {})
    asked = body.get("provider")
    if asked is None or isinstance(asked, Mapping):
        body["provider"] = {**DEFAULT_BODY["provider"], **(asked or {})}
    return body


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"
    key_env = "OPENROUTER_API_KEY"
    extra = "openrouter"
    base_url = BASE_URL
    default_tool_choice = "required"
    send_parallel_flag = False
    default_headers = ATTRIBUTION
    switches_api = False
    refused_body_keys = REFUSED_BODY_KEYS
    bills_per_call = True
    """The loop starts a run's total at $0 for a provider that bills per call, even where no
    rate resolved, because a bill may still arrive with every turn."""

    def __init__(
        self,
        model: str | None = None,
        *,
        source: str = "--llm",
        listed: Listed | None = None,
        vision: bool | None = None,
        tool_choice: str | None = None,
        reasoning_effort: str | None = None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        chosen = model or default_model_for(self.name) or ""
        spec = find_model(self.name, chosen)
        # Before the key is read: a model OpenRouter does not list is the reader's to fix
        # first, and the list needs no key to say so. `listed` is how a test hands one in.
        if spec is None and listed is None:
            listed = admit(chosen, base_url=base_url or self.base_url, source=source)
        if vision is None and listed is not None:
            vision = listed.vision
        # Chat Completions only, whatever `QUACKD_OPENAI_API` says: see the module docstring.
        kwargs.pop("api", None)
        super().__init__(
            chosen,
            base_url=base_url,
            vision=vision,
            tool_choice=tool_choice,
            reasoning_effort=reasoning_effort,
            api="chat",
            **kwargs,
        )
        # The base reads `QUACKD_OPENAI_REASONING_EFFORT` for every OpenAI-shaped vendor. Here a
        # stray parameter is worse than ignored: with `require_parameters` it can leave no
        # endpoint that serves the model. Reasoning settings go through `--extra-body`.
        self.reasoning_effort = reasoning_effort
        if tool_choice is None:
            if spec is not None:
                self.tool_choice = "required" if spec.forced_tools else "auto"
            elif listed is not None:
                self.tool_choice = "auto" if listed.tool_choice else None
        self.extra_body = with_default_body(getattr(self, "extra_body", None))
        self.listed = listed
        self.listed_price: Price | None = listed.price if listed is not None else None
        """The rate read off OpenRouter's list for an id the catalogue does not carry, which
        the loop prices the run at when nothing outranks it (`pricing.resolve_price`)."""

    def _assistant_fields(self, decision: Decision) -> Mapping[str, Any]:
        raw = decision.raw
        if isinstance(raw, Mapping) and isinstance(raw.get("reasoning_details"), list):
            return {"reasoning_details": raw["reasoning_details"]}
        return {}

    def _parse_chat(self, response: Any) -> ProviderTurn:
        return parse_openrouter(response)

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        try:
            return await super().step(system, history, tools)
        except ProviderError as e:
            # No retry and no move: the run stays on Chat Completions. The words are added
            # because the refusal itself names an API this provider will not use.
            cause = e.__cause__
            if isinstance(cause, Exception) and _wants_the_responses_api(cause):
                raise ProviderError(f"{e} {RESPONSES_HINT}") from cause
            raise
