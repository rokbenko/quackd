"""`--llm <vendor>[:<model>]` to an `LLMProvider`, importing vendor SDKs only when asked for.

Kept separate from `__init__` so importing `quackd.agent.providers` never touches a vendor
package, and so `quackd doctor` can ask "which providers could run here?" cheaply.

`make_provider` reads no environment variable at all. `resolve_llm` is the single place that
knows the order of precedence (a flag beats a registered robot beats `QUACKD_LLM` beats the
default), and it hands the factory an answer that is already decided, along with the phrase
naming where that answer came from. When the factory also consulted the environment, two callers
passing identical arguments could get different pilots, and the error a bad id raised could not
say which of the two the reader needed to fix.

The model names themselves live one module further out, in `catalogue`, which imports nothing at
all: the CLI reads it to build `--llm`'s completions and its help, and must not pay for pydantic
to do that.
"""

from __future__ import annotations

import importlib
import os
import re
from typing import Any

from quackd.agent.providers.base import LLMProvider, ProviderError
from quackd.agent.providers.catalogue import CATALOGUE as CATALOGUE
from quackd.agent.providers.catalogue import CLOUD_NAMES as CLOUD_NAMES
from quackd.agent.providers.catalogue import DEFAULT_LLM as DEFAULT_LLM
from quackd.agent.providers.catalogue import LLM_ENV as LLM_ENV
from quackd.agent.providers.catalogue import LOCAL_NAMES as LOCAL_NAMES
from quackd.agent.providers.catalogue import OPEN_ENDED as OPEN_ENDED
from quackd.agent.providers.catalogue import PROVIDER_NAMES as PROVIDER_NAMES
from quackd.agent.providers.catalogue import default_model_for as default_model_for
from quackd.agent.providers.catalogue import find_model as find_model
from quackd.agent.providers.catalogue import model_ids as model_ids
from quackd.agent.providers.catalogue import models_for as models_for
from quackd.agent.providers.catalogue import vendor_of as vendor_of

# The default for each provider, derived from the catalogue so an id is spelled once. Local
# presets have no default: they discover the served model from /v1/models when none is given.
DEFAULT_MODELS: dict[str, str | None] = {
    **{name: default_model_for(name) for name in CLOUD_NAMES},
    **{name: None for name in LOCAL_NAMES},
}

KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "grok": "XAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "cohere": "COHERE_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "glm": "ZAI_API_KEY",
    "meta": "META_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    **{name: "LOCAL_API_KEY" for name in LOCAL_NAMES},
}

# Which `quackd[...]` extra installs the SDK a provider needs. Most of these are the same wheel
# under a different name, because most vendors speak OpenAI's API: the extra exists so the error
# a missing package raises can name the install the reader actually wants.
EXTRA_FOR = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "gemini",
    "grok": "grok",
    "mistral": "mistral",
    "deepseek": "deepseek",
    "cohere": "cohere",
    "qwen": "qwen",
    "kimi": "kimi",
    "glm": "glm",
    "meta": "meta",
    "openrouter": "openrouter",
    **{name: "openai" for name in LOCAL_NAMES},
}

# The module `doctor` imports to decide whether a provider could run here. Only three distinct
# SDKs serve twelve vendors and five local presets.
SDK_FOR = {
    "anthropic": "anthropic",
    "gemini": "google.genai",
    **{name: "openai" for name in CLOUD_NAMES if name not in ("anthropic", "gemini")},
    **{name: "openai" for name in LOCAL_NAMES},
}

# Vendors that are OpenAI's API wearing a different hat: a base URL, a key and a model list.
# Each is one small subclass in `quackd/agent/providers/<name>.py`, imported only when asked for.
OPENAI_COMPATIBLE = {
    "grok": "GrokProvider",
    "mistral": "MistralProvider",
    "deepseek": "DeepSeekProvider",
    "cohere": "CohereProvider",
    "qwen": "QwenProvider",
    "kimi": "KimiProvider",
    "glm": "GLMProvider",
    "meta": "MetaProvider",
    "openrouter": "OpenRouterProvider",
}

#: What `--llm openrouter:` will not take even where OpenRouter would, keyed by the part of the id
#: that gives it away, with the reason the refusal prints. All of it is read off the id alone, so
#: it is refused offline, before a key is read or the model list is fetched. The reasons quote
#: OpenRouter's model-variants page as read on 2026-10-06, and the guide quotes this table.
OPENROUTER_REFUSED: dict[str, str] = {
    "~": (
        "a `~` alias always resolves to the newest model of its family, so the model that "
        "answers could change under a run that names it"
    ),
    "openrouter/": (
        "OpenRouter's own routers choose the model per request, so neither the model that "
        "answers nor its price is known before the call"
    ),
    ":batch": (
        "the batch-priced entry is served by OpenRouter's Batch API, and a run asks Chat "
        "Completions every turn"
    ),
    ":nitro": (
        "a routing variant OpenRouter accepts on any id and does not list, and a request a "
        "priority endpoint serves is billed at that endpoint's priority rate; sort providers "
        """with --extra-body '{"provider": {"sort": "throughput"}}' instead"""
    ),
    ":floor": (
        "a routing variant OpenRouter accepts on any id and does not list, and a request a flex "
        "endpoint serves is billed at that endpoint's flex rate; sort providers with "
        """--extra-body '{"provider": {"sort": "price"}}' instead"""
    ),
    ":exacto": (
        "a routing variant OpenRouter accepts on any id and does not list, so there is no "
        "entry to check it against or price it from"
    ),
    ":thinking": (
        "OpenRouter says to use the `reasoning` parameter instead, which --extra-body carries"
    ),
    ":extended": "OpenRouter says no model currently offers it",
    ":online": "OpenRouter says to use its `openrouter:web_search` server tool instead",
}

#: An OpenRouter id: `AUTHOR/MODEL`, optionally `~` in front and one `:variant` behind. Every
#: id on its list on 2026-10-06 had exactly that shape, in lower case. Case is not checked here:
#: a capital is a typo the model list answers with the nearest real id, which is kinder.
_OPENROUTER_ID = re.compile(
    r"(?P<alias>~)?(?P<author>[A-Za-z0-9][A-Za-z0-9._-]*)/(?P<model>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?::(?P<variant>\S*))?"
)


def _open_ended_refusal(model: str) -> str | None:
    """Why an id `--llm openrouter:` does not list cannot be taken, or None when its spelling
    leaves it to OpenRouter's model list. Several refused shapes are on that list (the `~`
    aliases, the `:batch` entries, the routers): they are refused for what they are. Only `:free`
    is taken as a variant: OpenRouter lists free entries as models of their own, with their own
    endpoints and limits."""
    found = _OPENROUTER_ID.fullmatch(model)
    if found is None:
        return "an OpenRouter id is AUTHOR/MODEL, with :free as the only suffix quackd takes"
    if found["alias"]:
        return OPENROUTER_REFUSED["~"]
    if found["author"].lower() == "openrouter":
        return OPENROUTER_REFUSED["openrouter/"]
    variant = found["variant"]
    if variant is None or variant == "free":
        return None
    if not variant:
        return "an OpenRouter id ends at its model or at :free, not at a bare colon"
    return OPENROUTER_REFUSED.get(
        f":{variant}", f"`:{variant}` is not a variant quackd takes: :free is the only one"
    )


def _unknown_model(provider: str, model: str, source: str) -> str:
    """Why an id was refused, and what to pass instead.

    Every part of this earns its place. A model from another vendor is the commonest mistake and
    the hardest to see, because the id looks perfectly valid. The full list of ids is here rather
    than behind a command because the reader is already stopped.
    """
    ids = model_ids(provider)
    default = ids[0] if ids else ""
    listed = ", ".join(f"{i} (default)" if i == default else i for i in ids)
    elsewhere = vendor_of(model)
    if elsewhere and elsewhere != provider:
        whose = f" ({model!r} is a {elsewhere} model: --llm {elsewhere}:{model})"
    elif "/" in model and "openrouter" in OPEN_ENDED:
        # The commonest way to land here with a slash is an OpenRouter id named at the vendor
        # whose model it is: `--llm anthropic:anthropic/claude-opus-5.5`.
        whose = f" (an id with a slash reads as OpenRouter's: --llm openrouter:{model})"
    else:
        whose = ""
    return (
        f"{provider}: unknown model {model!r} from {source}{whose}. "
        f"Valid ids: {listed}. See `quackd list-models --llm {provider}`."
    )


def _refused_open_ended(provider: str, model: str, source: str, why: str) -> str:
    """Why an id an open-ended vendor does not list was refused on its shape alone."""
    listed = ", ".join(model_ids(provider))
    return (
        f"{provider}: {model!r} from {source} is refused: {why}. quackd lists {listed}. "
        f"See `quackd list-models --llm {provider}`."
    )


def resolve_model(provider: str, model: str | None, *, source: str = "--llm") -> str | None:
    """The id this provider will be given, or a `ProviderError` saying why not.

    Cloud vendors take an id from the catalogue and nothing else, so a retired id, a typo and
    another vendor's id all stop here, before a key is read or a packet is sent. Everything
    without a catalogue passes straight through: the local presets serve whatever was pulled, and
    `None` there means "ask the server" rather than "use the default" (ADR-0014).

    An open-ended vendor (OpenRouter) is the one in between. Its catalogue is a selection, so
    an id it does not list is checked here for its shape only, which is what can be known
    without the network, and the provider checks it against the vendor's own list before the
    first paid call (ADR-0050).
    """
    if provider not in CATALOGUE:
        return model
    if not model:
        return default_model_for(provider)
    if find_model(provider, model) is not None:
        return model
    if provider in OPEN_ENDED:
        why = _open_ended_refusal(model)
        if why is None:
            return model
        raise ProviderError(_refused_open_ended(provider, model, source, why))
    raise ProviderError(_unknown_model(provider, model, source))


def _unknown_llm(spec: str, head: str, source: str, *, bare: bool) -> str:
    """Why a vendor name was refused, and both shapes of the flag that would have worked.

    Two mistakes land here and they want different words. `--llm hal:gpt-4o` is a typo in the
    vendor half, so the message names the whole spec back: that is what shows the reader which
    half of it quackd could not read. `--llm hal9000` might instead have been meant as a bare
    model id, which is a form that does work (`--llm claude-opus-5`), so that case also says the
    catalogue was searched and came back empty, rather than leaving the reader to wonder whether
    bare ids are allowed at all.
    """
    where = f" in {spec!r}" if spec != head else ""
    searched = ", and no vendor here lists a model of that name" if bare else ""
    example = default_model_for("anthropic")
    # A slash in the vendor half is an OpenRouter id typed without its vendor, and the colon of
    # its `:free` is what made it look like a vendor and a model. `openrouter/anthropic/...` is
    # the same id with a slash where the colon goes, so the suggestion drops that first part;
    # `openrouter/auto` has one slash, is OpenRouter's own id as typed, and is suggested as it
    # is, to be refused there for what it is rather than here for its spelling.
    openrouter = ""
    if "/" in head and "openrouter" in OPEN_ENDED:
        typed = spec
        if head.lower().startswith("openrouter/") and spec.count("/") > 1:
            typed = spec.split("/", 1)[1]
        openrouter = f" It reads as an OpenRouter id: --llm openrouter:{typed}."
    return (
        f"unknown provider {head!r}{where} from {source}{searched}.{openrouter} "
        f"Pass a vendor, or a vendor and a model: --llm anthropic, --llm anthropic:{example}. "
        f"Vendors: {', '.join(PROVIDER_NAMES)}."
    )


def parse_llm(
    spec: str | None, *, source: str = "--llm", check_model: bool = True
) -> tuple[str, str | None]:
    """`--llm` as the (vendor, model) pair the factory takes, or a `ProviderError` saying why not.

    One flag now carries what `--provider` and `--model` used to carry between them, because the
    two were never really independent: a model id means nothing without its vendor, and every
    refusal had to name both anyway. `--llm anthropic` is that vendor's default model,
    `--llm anthropic:claude-sonnet-5` names one, and no `--llm` at all is `fake`.

    The split is at the FIRST colon and no other, because Ollama's own tags contain one:
    `--llm ollama:llama3:8b` is the preset `ollama` serving the model `llama3:8b`, where a split
    on the last colon would have asked it for `llama3`. The vendor half is lowercased, so
    `--llm OpenAI` works; the model half is not, because vendors ship ids like `Qwen/Qwen3-8B`
    and a folded copy of one is a 404.

    A spec with no colon that is not a vendor gets one more chance. Catalogue ids are unique
    across vendors (`vendor_of`, and a test that holds them to it), so `--llm claude-opus-5`
    can infer `anthropic` on its own and spare the reader remembering which house builds what.

    Pass `check_model=False` to learn only which vendor was named, without the catalogue lookup:
    shell completion has to answer while the id after the colon is still half typed.
    """
    if spec is None or not spec.strip():
        return DEFAULT_LLM, None
    text = spec.strip()
    head, colon, tail = text.partition(":")
    head = head.strip()
    vendor = head.lower()
    model = tail.strip() or None
    if vendor in PROVIDER_NAMES:
        # A colon with nothing after it is not a mistake. Shell completion offers `openai:` as a
        # prefix, and a reader who presses enter on it means the vendor's default, not an error.
        if model is not None and check_model:
            resolve_model(vendor, model, source=source)
        return vendor, model
    if not colon:
        inferred = vendor_of(head)
        if inferred is not None:
            return inferred, head
    raise ProviderError(_unknown_llm(text, head, source, bare=not colon))


def resolve_llm(
    flag: str | None, stored: str | None = None, *, robot: str | None = None
) -> tuple[str, str | None, str]:
    """Which pilot to fly, from the three places one can be named, and which place named it.

    The order is the one every other quackd setting uses: what the reader has just typed beats
    what a registered robot remembers, which beats the environment, which beats `fake`. The
    source phrase travels back with the answer so a bad value is refused in the reader's own
    terms. "unknown provider 'hal' from robot duck-a (robots.json)" sends them to a line in a
    file they may have written months ago, while the same text from `--llm` sends them to the
    command still on their screen, and the two are a very different hunt.

    Blank counts as absent at every level: `QUACKD_LLM=` in a `.env` is how a shell says "unset",
    and reading that as a vendor named "" would stop the run instead of falling through to the
    next place.
    """
    where = f"robot {robot} (robots.json)" if robot else "robots.json"
    candidates: tuple[tuple[str | None, str], ...] = (
        (flag, "--llm"),
        (stored, where),
        (os.environ.get(LLM_ENV), LLM_ENV),
    )
    for value, source in candidates:
        if value is None or not value.strip():
            continue
        vendor, model = parse_llm(value, source=source)
        return vendor, model, source
    return DEFAULT_LLM, None, "default"


def _extra_body(text: str | None) -> dict[str, Any] | None:
    """`--extra-body` as the dict a provider takes, parsed here so a bad value names the flag
    and stops before a key is read or a packet is sent. None hands the provider nothing, and it
    reads `QUACKD_EXTRA_BODY` itself: that is how the flag outranks the variable."""
    if text is None:
        return None
    from quackd.agent.providers.openai import parse_extra_body

    return parse_extra_body(text, source="--extra-body")


def make_provider(
    name: str,
    *,
    model: str | None = None,
    source: str = "--llm",
    duck_name: str | None = None,
    goal: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    vision: bool | None = None,
    extra_body: str | None = None,
    host: str | None = None,
) -> LLMProvider:
    name = name.lower()
    # Before the branches, so a typo is refused the same way whichever provider was named,
    # `fake` included. Anthropic and Gemini ignore the value as they ignore `--base-url`,
    # but ignoring a field is not the same as swallowing a mistake, and `--llm fake`
    # is then the cheapest way to find out whether a shell mangled the quoting.
    body = _extra_body(extra_body)
    if name == "fake":
        from quackd.agent.providers.fake import FakeProvider

        # The scripted pilot has no model to pick, so `--llm fake:anything` is not refused here,
        # the model half is ignored. `--vision` it does take: it looks at nothing either way, and
        # it is the only pilot that can carry a picture through the whole loop with no key and no
        # vendor.
        return FakeProvider.for_duck(duck_name or "", goal=goal, vision=vision)
    # No environment is read here: `resolve_llm` has already settled what was asked for, and
    # `source` says where it was asked, so a wrong id names the flag the reader has just typed
    # or the line in `robots.json` they had forgotten, rather than guessing between them.
    model = resolve_model(name, model or None, source=source)
    if name == "anthropic":
        from quackd.agent.providers.anthropic import AnthropicProvider

        return AnthropicProvider(model=model, vision=vision)
    if name == "openai":
        from quackd.agent.providers.openai import OpenAIProvider

        return OpenAIProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
            vision=vision,
            extra_body=body,
        )
    if name == "gemini":
        from quackd.agent.providers.gemini import GeminiProvider

        return GeminiProvider(model=model, api_key=api_key, vision=vision)
    if name in OPENAI_COMPATIBLE:
        module = importlib.import_module(f"quackd.agent.providers.{name}")
        vendor = getattr(module, OPENAI_COMPATIBLE[name])
        # An open-ended vendor checks an id it does not list against its own list, and a
        # refusal there has to name where the id came from, which only this function knows.
        named = {"source": source} if name in OPEN_ENDED else {}
        provider: LLMProvider = vendor(
            model=model,
            api_key=api_key,
            base_url=base_url,
            vision=vision,
            extra_body=body,
            **named,
        )
        return provider
    if name in LOCAL_NAMES:
        from quackd.agent.providers.local import LocalProvider

        # `host` reaches the local presets and nothing else. It moves a preset's localhost to
        # the board `--host` names, and a vendor's API has no localhost to move: OpenAI and
        # the rest are reached at their own address whatever board the robot uses.
        return LocalProvider(
            model,
            preset=name,
            base_url=base_url,
            host=host,
            api_key=api_key,
            vision=vision,
            extra_body=body,
        )
    raise ProviderError(f"unknown provider {name!r}; choose one of {', '.join(PROVIDER_NAMES)}")
