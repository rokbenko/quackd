"""The model catalogue, and the entries around it that have to agree.

The catalogue is data, and data rots quietly: an id nobody validates is wrong for months, which
is exactly how quackd came to ship `gpt-5`, `grok-4` and `gemini-2.5-pro` as defaults after all
three had been deprecated or retired. Nothing here can tell whether an id is still real, because
that needs the network and a key and neither is available in CI. What it can do is hold the shape:
one default per vendor and it is in its own list, ids unique so a wrong one can be traced to its
owner, a status from the fixed set, and a row in every table a new vendor has to appear in.

`CONTRIBUTING.md` used to say "nothing counts them" about those tables. This file is what
replaced that sentence.
"""

from __future__ import annotations

import importlib
import re
import tomllib
from types import SimpleNamespace as NS
from typing import Any

import pytest

from quackd.agent.providers import catalogue as cat
from quackd.agent.providers.base import (
    ProviderError,
    ProviderMissingKey,
    ProviderNotInstalled,
)
from quackd.agent.providers.factory import (
    CLOUD_NAMES,
    DEFAULT_LLM,
    EXTRA_FOR,
    KEY_ENV,
    LLM_ENV,
    LOCAL_NAMES,
    OPENAI_COMPATIBLE,
    PROVIDER_NAMES,
    SDK_FOR,
    default_model_for,
    find_model,
    make_provider,
    model_ids,
    models_for,
    parse_llm,
    resolve_llm,
    resolve_model,
    vendor_of,
)
from quackd.agent.providers.local import PRESETS
from tests.conftest import REPO

#: Ids quackd shipped, or nearly shipped, that the vendor has since retired or that never existed.
#: Kept as a list rather than a rule because there is no rule: the only thing that stops one of
#: these coming back in a careless edit is a test that names them.
GONE = {
    "gpt-5",  # shutdown announced for 2026-12-11
    "grok-4",  # retired 2026-05-15, and silently answered as grok-4.3 after
    "gemini-2.0-flash",  # shut down
    "deepseek-chat",  # stopped answering after 2026-07-24
    "kimi-k2.5",  # discontinued 2026-08-31, and answers 404
    "mistral-medium-2604",  # never an id: Mistral moved to name-major-minor
    "command-a-vision-07-2025",  # live, but its page says tool use is not supported
    "glm-4.5v",  # a GLM vision model with no function calling
    "claude-mythos-5-1",  # invitation only
    "gpt-5.6-cyber",  # its own approval programme
    # the 2026-09-23 refresh
    "gemini-2.5-pro",  # since 2026-09-18 only callable by users who have used it before
    "gemini-2.5-flash",  # the same
    "gemini-2.5-flash-lite",  # the same
    "grok-4.20-multi-agent-0309",  # takes no client-side function tools, no Chat Completions
    "qwen3-max",  # retires 2026-10-10
    "qwen3-coder-plus",  # retires 2026-10-10
    "qwen3-coder-next",  # retires 2026-10-10
}


#: What each vendor accepts for "you must call a tool", from its own documentation. Not a
#: preference: Mistral's guide documents `any` (its spec lists `required` too), Z.ai documents
#: `auto` as the only value it takes, DeepSeek is asked with `required` and with thinking off,
#: because thinking mode refuses it, and Cohere's compatibility endpoint documents no such
#: parameter, so the field is omitted.
#: Pinned here because the browser's copy of this table is only checked against the vendors the
#: page can reach, and GLM is not one of them.
TOOL_CHOICE = {
    "grok": "required",
    "mistral": "any",
    "deepseek": "required",
    "cohere": None,
    "qwen": "auto",
    "kimi": "auto",
    "glm": "auto",
    "meta": "auto",
    # OpenRouter's default; its Claude rows and the ids it lists that quackd does not carry are
    # asked with `auto` instead (`openrouter.py`, and the tests for it in test_providers.py)
    "openrouter": "required",
}


class FakeOpenAI:
    """Enough of the SDK client to construct a provider without a key or a network."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

        async def create(**kwargs: Any) -> Any:
            self.kwargs = kwargs
            return NS(choices=[], usage=None)

        self.chat = NS(completions=NS(create=create))
        self.responses = NS(create=create)


# ── the catalogue holds its shape ───────────────────────────────────────────────────────


def test_every_cloud_provider_has_models_and_the_first_one_is_its_default() -> None:
    assert set(cat.CATALOGUE) == set(CLOUD_NAMES)
    for name in CLOUD_NAMES:
        ids = model_ids(name)
        assert ids, f"{name} has an empty catalogue, so --llm {name}:MODEL could never be met"
        assert default_model_for(name) == ids[0]
        assert default_model_for(name) in ids


def test_nothing_without_a_catalogue_claims_a_default() -> None:
    """`fake` is scripted and the local presets ask their server, so neither may name a model."""
    for name in (*LOCAL_NAMES, "fake"):
        assert default_model_for(name) is None
        assert models_for(name) == ()


def test_model_ids_are_unique_across_every_vendor() -> None:
    """What makes "that is a grok model, pass --llm grok:grok-4.6" possible, and truthful.

    It is also what lets a bare id name its own vendor: `--llm claude-opus-5` can only infer
    `anthropic` because no second vendor lists that id, and the day one does, this test is
    what says so rather than a reader getting quietly sent to the wrong house."""
    seen: dict[str, str] = {}
    for name in CLOUD_NAMES:
        for model_id in model_ids(name):
            assert model_id not in seen, (
                f"{model_id} is listed by both {seen.get(model_id)} and {name}"
            )
            seen[model_id] = name
    for model_id, name in seen.items():
        assert vendor_of(model_id) == name


def test_every_status_is_one_of_the_five() -> None:
    for name in CLOUD_NAMES:
        for m in models_for(name):
            assert m.status in cat.STATUSES, f"{m.id} has status {m.status!r}"


def test_only_openai_names_an_api_and_only_ever_responses() -> None:
    """The hint exists because OpenAI has two APIs. Nobody else here has a second one to pick."""
    for name in CLOUD_NAMES:
        for m in models_for(name):
            if name == "openai":
                assert m.api in (None, "chat", "responses")
            else:
                assert m.api is None, f"{m.id} names an API, and {name} has only one"
    assert any(m.api == "responses" for m in models_for("openai")), (
        "no OpenAI model is marked responses, so the hint is dead code and the tests for it lie"
    )


def test_only_anthropic_and_openrouter_mark_a_model_that_refuses_a_forced_call() -> None:
    """Every other vendor's `tool_choice` is decided per vendor (TOOL_CHOICE below, and the
    provider classes), so the per-model flag is read by `anthropic.py` and `openrouter.py` alone
    and a row elsewhere setting it would be read by nothing. On OpenRouter only a Claude row may
    carry it: the refusal is Anthropic's, and nobody has seen another upstream make it."""
    for name in CLOUD_NAMES:
        for m in models_for(name):
            if name not in ("anthropic", "openrouter"):
                assert m.forced_tools is True, f"{m.id} sets forced_tools and {name} never reads it"
            elif name == "openrouter" and not m.forced_tools:
                assert m.id.startswith("anthropic/"), f"{m.id} is marked and is not a Claude model"
    for name in ("anthropic", "openrouter"):
        assert any(not m.forced_tools for m in models_for(name)), (
            f"no {name} row is marked, so the flag is dead code there and the tests for it lie"
        )


#: The OpenRouter rows that are the same model as a row a native vendor carries, spelled the
#: way each spells it. Sonnet 5.5 has no twin: the Anthropic tuple does not carry it yet.
TWINS = {
    "openai/gpt-6-sol": ("openai", "gpt-6-sol"),
    "openai/gpt-6-luna": ("openai", "gpt-6-luna"),
    "anthropic/claude-opus-5.5": ("anthropic", "claude-opus-5-5"),
    "google/gemini-3.8-flash": ("gemini", "gemini-3.8-flash"),
    "x-ai/grok-4.7": ("grok", "grok-4.7"),
}


def test_an_openrouter_row_refuses_a_forced_call_wherever_its_native_twin_does() -> None:
    """The same model behind a router is the same model: a Claude row that takes `auto` from
    Anthropic and `required` through OpenRouter would be asked to do what it already refused."""
    for model_id, (vendor, native) in TWINS.items():
        row, twin = find_model("openrouter", model_id), find_model(vendor, native)
        assert row is not None and twin is not None, model_id
        assert row.forced_tools is twin.forced_tools, model_id
        assert row.vision is twin.vision, model_id
    assert set(TWINS) | {"anthropic/claude-sonnet-5.5"} == set(model_ids("openrouter"))


def test_openrouter_ids_name_their_author_and_no_native_id_has_a_slash() -> None:
    """What lets a slash say "this is OpenRouter's" in a refusal, and a bare
    `--llm anthropic/claude-opus-5.5` find its vendor."""
    for name in CLOUD_NAMES:
        for model_id in model_ids(name):
            if name == "openrouter":
                assert re.fullmatch(r"[a-z0-9-]+/[a-z0-9.-]+", model_id), model_id
            else:
                assert "/" not in model_id, f"{model_id} would read as an OpenRouter id"


def test_the_open_ended_vendors_are_catalogue_vendors_with_a_list_of_their_own() -> None:
    assert set(cat.OPEN_ENDED) <= set(CLOUD_NAMES)
    for name in cat.OPEN_ENDED:
        assert models_for(name), f"{name} would have no default"


def test_an_openrouter_row_says_the_day_its_rate_was_read() -> None:
    """Its rows were read on a day of their own, and the record must not date them to the day
    every other vendor's were."""
    for m in models_for("openrouter"):
        assert m.price is not None and m.price.checked == cat.OPENROUTER_PRICES_CHECKED, m.id
        assert m.price.record()["checked"] == cat.OPENROUTER_PRICES_CHECKED


def test_only_anthropic_marks_a_model_by_how_it_takes_effort_or_thinking() -> None:
    """`effort` and `binds_thinking` are Anthropic's alone, like `forced_tools`: every other
    vendor's request is shaped by its provider class, and a row elsewhere setting either would
    be read by nothing."""
    for name in CLOUD_NAMES:
        if name == "anthropic":
            continue
        for m in models_for(name):
            assert m.effort is True, f"{m.id} sets effort and {name} never reads it"
            assert m.binds_thinking is False, f"{m.id} sets binds_thinking and {name} ignores it"


def test_no_retired_id_has_crept_back() -> None:
    listed = {m.id for name in CLOUD_NAMES for m in models_for(name)}
    assert not (listed & GONE), f"the catalogue lists ids known to be gone: {sorted(listed & GONE)}"


def test_labels_say_something_the_id_does_not() -> None:
    """A dropdown of raw ids is a dropdown nobody can read, so every model carries a label."""
    for name in CLOUD_NAMES:
        for m in models_for(name):
            assert m.label and m.label != m.id


# ── resolving what the user asked for ───────────────────────────────────────────────────


def test_an_unknown_id_is_refused_with_the_list_and_where_to_find_it() -> None:
    with pytest.raises(ProviderError) as e:
        resolve_model("openai", "gpt-nope")
    message = str(e.value)
    assert "openai" in message and "gpt-nope" in message
    assert default_model_for("openai") in message and "(default)" in message
    assert "list-models" in message
    for model_id in model_ids("openai"):
        assert model_id in message, f"{model_id} is valid but the refusal does not offer it"


def _refusal(provider: str, model: str) -> str:
    with pytest.raises(ProviderError) as e:
        resolve_model(provider, model)
    return str(e.value)


def test_another_vendors_id_says_whose_it_is() -> None:
    """The mistake that looks like no mistake: a real id, at the wrong vendor. Telling the reader
    only that it is unknown would send them hunting for a typo that is not there."""
    message = _refusal("openai", "grok-4.6")
    assert "is a grok model" in message and "--llm grok:grok-4.6" in message
    # and an id belonging to nobody says nothing about vendors, because there is nothing to say
    assert "is a" not in _refusal("openai", "gpt-nope").split("Valid ids")[0].replace(
        "is a model", ""
    )


def test_the_refusal_names_where_the_id_came_from() -> None:
    """Three places can name a model and the hunt for each is a different hunt, so the refusal
    carries the source with it. The default is `--llm`, because that is where a reader who has
    just typed something wrong almost always typed it."""
    assert "--llm" in str(_refusal("openai", "nope"))
    with pytest.raises(ProviderError, match=LLM_ENV):
        resolve_model("openai", "nope", source=LLM_ENV)
    with pytest.raises(ProviderError, match=r"robot duck-a \(robots.json\)"):
        resolve_model("openai", "nope", source="robot duck-a (robots.json)")


def test_no_model_means_the_default_and_local_presets_are_left_alone() -> None:
    assert resolve_model("openai", None) == default_model_for("openai")
    assert resolve_model("openai", "") == default_model_for("openai")
    # free text, both directions: a local server serves what it serves
    assert resolve_model("ollama", "qwen3:8b") == "qwen3:8b"
    # None here means ask the server, which is not the same as "use the default"
    assert resolve_model("ollama", None) is None
    assert resolve_model("local", "anything at all") == "anything at all"


def test_an_unknown_provider_is_not_this_functions_business() -> None:
    """`make_provider` raises "unknown provider" for that, and it must keep winning."""
    assert resolve_model("hal", "whatever") == "whatever"


# ── one flag, parsed ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("anthropic", ("anthropic", None)),
        ("anthropic:claude-opus-5", ("anthropic", "claude-opus-5")),
        ("claude-opus-5", ("anthropic", "claude-opus-5")),
        ("grok-4.6", ("grok", "grok-4.6")),
        ("ollama:llama3:8b", ("ollama", "llama3:8b")),
        ("openai:", ("openai", None)),
        ("fake", ("fake", None)),
        ("  OpenAI  ", ("openai", None)),
        ("openrouter:google/gemma-4-31b-it:free", ("openrouter", "google/gemma-4-31b-it:free")),
        ("anthropic/claude-opus-5.5", ("openrouter", "anthropic/claude-opus-5.5")),
        ("openrouter:qwen/qwen3.8-flash", ("openrouter", "qwen/qwen3.8-flash")),
        (None, (DEFAULT_LLM, None)),
        ("", (DEFAULT_LLM, None)),
        ("   ", (DEFAULT_LLM, None)),
    ],
    ids=[
        "a-bare-vendor",
        "a-vendor-and-a-model",
        "a-bare-listed-id-infers-its-vendor",
        "and-so-does-another-vendors",
        "the-split-is-at-the-first-colon",
        "a-trailing-colon-is-the-default",
        "the-scripted-pilot",
        "a-vendor-is-folded-and-trimmed",
        "an-openrouter-free-id-keeps-its-second-colon",
        "a-bare-openrouter-row-infers-openrouter",
        "an-id-openrouter-lists-and-quackd-does-not",
        "nothing-at-all",
        "an-empty-string",
        "blank-counts-as-absent",
    ],
)
def test_every_shape_the_one_flag_takes(spec: str | None, expected: tuple[str, str | None]) -> None:
    """The whole grammar of `--llm` in one table, because it is a grammar now rather than two
    independent strings.

    `ollama:llama3:8b` is the row that is easy to get wrong and expensive to get wrong quietly:
    Ollama's own tags carry a colon, so a split on the last one would ask the server for
    `llama3` and get a different model than the reader named, with nothing anywhere saying so.
    """
    assert parse_llm(spec) == expected


def test_a_bare_word_that_is_neither_a_vendor_nor_a_model_is_refused() -> None:
    """And says the catalogue was searched, because a bare model id IS a legal spec: without
    that sentence the reader cannot tell whether they mistyped or whether bare ids are simply
    not allowed."""
    with pytest.raises(ProviderError) as e:
        parse_llm("hal9000")
    message = str(e.value)
    assert "unknown provider 'hal9000'" in message
    assert "no vendor here lists a model of that name" in message
    example = default_model_for("anthropic")
    assert "--llm anthropic," in message and f"--llm anthropic:{example}" in message


def test_a_bad_vendor_before_a_colon_names_the_whole_spec_back() -> None:
    """`hal:gpt-4o` cannot have been meant as a bare id, so the catalogue is not mentioned. What
    the reader needs instead is the spec quoted back, showing which half was unreadable."""
    with pytest.raises(ProviderError) as e:
        parse_llm("hal:gpt-4o")
    message = str(e.value)
    assert "unknown provider 'hal'" in message and "'hal:gpt-4o'" in message
    assert "no vendor here lists" not in message


def test_the_model_half_is_checked_against_the_vendor_that_was_named() -> None:
    with pytest.raises(ProviderError) as e:
        parse_llm("openai:grok-4.6")
    assert "is a grok model" in str(e.value)


# ── OpenRouter: a list that is not the whole list ───────────────────────────────────────


def test_openrouter_takes_a_well_formed_id_it_does_not_list_without_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its shape is all that can be known offline. The list is the provider's to read, when a
    run starts, and nothing here may reach for it: `--help`, TAB and `robot add` all parse."""
    from quackd.agent.providers import openrouter

    def no_network(*a: Any, **kw: Any) -> Any:  # pragma: no cover - the failure it reports
        raise AssertionError("resolve_model fetched OpenRouter's list")

    monkeypatch.setattr(openrouter, "fetch_models", no_network)
    for model_id in ("qwen/qwen3.8-flash", "google/gemma-4-31b-it:free", "Anthropic/Claude-X"):
        assert resolve_model("openrouter", model_id) == model_id
    assert resolve_model("openrouter", None) == "openai/gpt-6-sol"


@pytest.mark.parametrize(
    ("model_id", "why"),
    [
        ("~anthropic/claude-opus-latest", "newest model of its family"),
        ("openrouter/auto", "routers choose the model per request"),
        ("openrouter/free", "routers choose the model per request"),
        ("OpenRouter/auto", "routers choose the model per request"),
        ("openai/gpt-6-sol:batch", "Batch API"),
        ("x-ai/grok-4.7:nitro", "priority rate"),
        ("x-ai/grok-4.7:floor", "flex rate"),
        ("x-ai/grok-4.7:exacto", "does not list"),
        ("anthropic/claude-sonnet-5.5:thinking", "`reasoning` parameter"),
        ("anthropic/claude-sonnet-5.5:extended", "no model currently offers it"),
        ("anthropic/claude-sonnet-5.5:online", "web_search"),
        ("google/gemma-4-31b-it:FREE", ":free is the only one"),
        ("google/gemma-4-31b-it:free:nitro", ":free is the only one"),
        ("google/gemma-4-31b-it:", "bare colon"),
        ("gpt-6-sol", "AUTHOR/MODEL"),
        ("openai/gpt 6", "AUTHOR/MODEL"),
        ("a/b/c", "AUTHOR/MODEL"),
    ],
)
def test_openrouter_refuses_these_shapes_on_their_spelling(
    model_id: str, why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offline and before any key: each of these is refused on its spelling, with the reason
    and with where it came from, so a reader with no key and no network still hears it."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError) as e:
        resolve_model("openrouter", model_id, source="robot duck-a (robots.json)")
    message = str(e.value)
    assert why in message and repr(model_id) in message
    assert "robot duck-a (robots.json)" in message and "list-models --llm openrouter" in message
    # It names the rows quackd carries and points at list-models, and says nothing of other ids.
    # 0.17.0 said any other id with tool calling on the list works too, which the refused shape
    # in this very message contradicts when OpenRouter lists it, as it lists `~` aliases, its
    # routers and `:batch` ids.
    rows = ", ".join(model_ids("openrouter"))
    assert message.endswith(f". quackd lists {rows}. See `quackd list-models --llm openrouter`.")
    assert "works too" not in message and "other id" not in message.lower()
    with pytest.raises(ProviderError, match="is refused"):
        make_provider("openrouter", model=model_id)


def test_every_refusal_reason_is_reachable() -> None:
    """The table the guide quotes has no row the code cannot print."""
    from quackd.agent.providers.factory import OPENROUTER_REFUSED, _open_ended_refusal

    printed = {
        _open_ended_refusal(model_id)
        for model_id in (
            "~a/b",
            "openrouter/auto",
            *(f"a/b{suffix}" for suffix in OPENROUTER_REFUSED if suffix.startswith(":")),
        )
    }
    assert printed == set(OPENROUTER_REFUSED.values())


def test_a_slash_in_the_vendor_half_points_at_openrouter() -> None:
    """`--llm google/gemma-4-31b-it:free` splits at its first colon into a vendor that is not
    one, so the refusal says what it reads as. A vendor typo with no slash says nothing of it."""
    with pytest.raises(ProviderError) as e:
        parse_llm("google/gemma-4-31b-it:free")
    assert "--llm openrouter:google/gemma-4-31b-it:free" in str(e.value)

    with pytest.raises(ProviderError) as e:
        parse_llm("openrouter/anthropic/claude-opus-5.5")
    assert "--llm openrouter:anthropic/claude-opus-5.5." in str(e.value), (
        "a slash where the colon goes is pointed at the colon, not doubled"
    )

    with pytest.raises(ProviderError) as e:
        parse_llm("hal:gpt-4o")
    assert "openrouter" not in str(e.value).split("Vendors:")[0].lower()


def test_an_openrouter_id_named_at_the_vendor_whose_model_it_is_says_whose_it_is() -> None:
    """`--llm anthropic:anthropic/claude-opus-5.5` is the native vendor asked for a router's
    spelling of its own model."""
    listed = _refusal("anthropic", "anthropic/claude-opus-5.5")
    assert "--llm openrouter:anthropic/claude-opus-5.5" in listed
    unlisted = _refusal("anthropic", "anthropic/claude-mythos-9")
    assert "--llm openrouter:anthropic/claude-mythos-9" in unlisted
    # and the converse: a native id named at OpenRouter is refused for its shape
    assert "AUTHOR/MODEL" in _refusal("openrouter", "claude-opus-5-5")


def test_completion_can_ask_which_vendor_without_paying_for_the_lookup() -> None:
    """Shell completion has to answer while the id after the colon is still half typed, so a
    half-finished id must not be refused on the way to naming its vendor."""
    assert parse_llm("openai:gpt-5.6-so", check_model=False) == ("openai", "gpt-5.6-so")
    assert parse_llm("openai:grok-4.6", check_model=False) == ("openai", "grok-4.6")


# ── the factory refuses before it spends anything ───────────────────────────────────────


def test_a_bad_model_is_refused_before_the_key_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Order matters. With the model checked second, a reader with no key would be told about
    the key, fix that, and only then be told the model was wrong all along."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="unknown model"):
        make_provider("openai", model="gpt-nope")


def test_a_pinned_spec_from_the_wrong_vendor_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment is read in exactly one place now, so this is a question for `resolve_llm`.

    It used to be a question for the factory, which read `QUACKD_MODEL` itself. That meant two
    callers passing identical arguments could get different pilots depending on a shell, and a
    refusal could not say which of the two the reader needed to fix. One reader, one answer.
    """
    monkeypatch.setenv(LLM_ENV, "openai:claude-opus-5")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError) as e:
        resolve_llm(None)
    assert LLM_ENV in str(e.value) and "anthropic" in str(e.value)


def test_the_factory_reads_no_environment_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """A variable that would stop `resolve_llm` dead must not reach into a call that names its
    own pilot. `make_provider` is handed an answer that is already decided."""
    monkeypatch.setenv(LLM_ENV, "openai:claude-opus-5")
    assert make_provider("fake", duck_name="hello-world").name == "fake"


@pytest.mark.parametrize(
    ("flag", "stored", "env", "expected", "source"),
    [
        ("gemini", "openai:gpt-5.6-sol", "anthropic", ("gemini", None), "--llm"),
        (None, "openai:gpt-5.6-sol", "anthropic", ("openai", "gpt-5.6-sol"), "robot duck-a"),
        (None, None, "anthropic:claude-opus-5", ("anthropic", "claude-opus-5"), LLM_ENV),
        (None, None, None, (DEFAULT_LLM, None), "default"),
        # blank is how a shell and a `.env` both say "unset", so it falls through rather than
        # being read as a vendor named ""
        (" ", " ", " ", (DEFAULT_LLM, None), "default"),
    ],
    ids=["the-flag-wins", "then-the-robot", "then-the-environment", "then-fake", "blank-is-absent"],
)
def test_where_a_pilot_may_be_named_and_which_place_wins(
    flag: str | None,
    stored: str | None,
    env: str | None,
    expected: tuple[str, str | None],
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the reader has just typed beats what a robot remembers, which beats the environment,
    which beats `fake`. The source travels back with the answer because "unknown provider 'hal'
    from robot duck-a (robots.json)" and the same words from `--llm` send the reader on very
    different hunts: one to a file they wrote months ago, one to the line still on screen."""
    if env is None:
        monkeypatch.delenv(LLM_ENV, raising=False)
    else:
        monkeypatch.setenv(LLM_ENV, env)
    vendor, model, where = resolve_llm(flag, stored, robot="duck-a")
    assert (vendor, model) == expected
    assert where.startswith(source), where


def test_the_source_phrase_says_which_robot_when_one_was_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flock resolves a pilot per member, so "robots.json" on its own would leave the reader
    to work out which of four entries is the broken one."""
    monkeypatch.delenv(LLM_ENV, raising=False)
    assert resolve_llm(None, "anthropic", robot="duck-a")[2] == "robot duck-a (robots.json)"
    assert resolve_llm(None, "anthropic")[2] == "robots.json"


def test_the_scripted_pilot_ignores_a_model_rather_than_refusing_it() -> None:
    """`--llm fake` has no model to pick, and failing a demo over an unused half is rude."""
    assert make_provider("fake", model="gpt-nope", duck_name="hello-world").name == "fake"


def test_an_unknown_provider_still_says_unknown_provider() -> None:
    with pytest.raises(ProviderError, match="unknown provider"):
        make_provider("hal9000")


def test_the_local_branch_is_handed_exactly_what_it_was_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Free text reaches a local preset untouched, including an id no cloud vendor would take."""
    recorded: dict[str, Any] = {}

    def _record(self: Any, model: str | None = None, **kwargs: Any) -> None:
        recorded["model"] = model
        recorded.update(kwargs)
        self.name = "vllm"
        self.model = model

    monkeypatch.setattr("quackd.agent.providers.local.LocalProvider.__init__", _record)
    make_provider("vllm", model="Qwen/Qwen3-8B", base_url="http://gpu:8000/v1", vision=True)
    assert recorded == {
        "model": "Qwen/Qwen3-8B",
        "preset": "vllm",
        "base_url": "http://gpu:8000/v1",
        "api_key": None,
        "vision": True,
        "extra_body": None,
        # the board `--host` names, which moves a preset's localhost; nothing here named one
        "host": None,
    }


# ── one file and the entries that have to agree ─────────────────────────────────────────


def test_every_provider_appears_in_every_table() -> None:
    for name in PROVIDER_NAMES:
        if name == "fake":
            continue
        assert name in KEY_ENV, f"{name} has no key variable"
        assert name in EXTRA_FOR, f"{name} has no extra"
        assert name in SDK_FOR, f"{name} has no SDK"
    assert set(CLOUD_NAMES) <= set(cat.CATALOGUE)
    assert set(LOCAL_NAMES) == set(PRESETS)
    assert {"anthropic", "openai", "gemini"} | set(OPENAI_COMPATIBLE) == set(CLOUD_NAMES), (
        "a cloud provider is either hand written or OpenAI shaped, and this one is neither"
    )


def test_only_three_sdks_serve_all_of_them() -> None:
    assert set(SDK_FOR.values()) == {"anthropic", "openai", "google.genai"}


def test_every_extra_exists_in_the_packaging_and_installs_the_right_sdk() -> None:
    """An extra named in an error message but missing from `pyproject.toml` sends the reader to
    an install that fails."""
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    distribution = {"anthropic": "anthropic", "openai": "openai", "google.genai": "google-genai"}
    for name in PROVIDER_NAMES:
        if name == "fake":
            continue
        extra = EXTRA_FOR[name]
        assert extra in extras, f"quackd[{extra}] is promised for {name} and is not declared"
        wanted = distribution[SDK_FOR[name]]
        assert any(req.startswith(wanted) for req in extras[extra]), (
            f"quackd[{extra}] does not install {wanted}, which {name} imports"
        )


def test_a_model_that_takes_no_image_is_not_sent_one() -> None:
    """The camera frame is the one thing a catalogue entry changes about a running provider.

    A model whose vendor documents no image input gets the detections as text instead, the way a
    local model already does, rather than a 400 on the first turn. The flag is a default, not a
    verdict: `--vision` and `--no-vision` still decide when a reader has an opinion.
    """
    from quackd.agent.providers.mistral import MistralProvider

    assert MistralProvider(model="codestral-2508", client=FakeOpenAI()).supports_vision is False
    assert MistralProvider(model="mistral-large-2512", client=FakeOpenAI()).supports_vision is True
    forced = MistralProvider(model="codestral-2508", client=FakeOpenAI(), vision=True)
    assert forced.supports_vision is True, "--vision must still win over the catalogue"
    refused = MistralProvider(model="mistral-large-2512", client=FakeOpenAI(), vision=False)
    assert refused.supports_vision is False, "--no-vision must still win over the catalogue"


def test_every_vendor_agrees_with_itself_about_images() -> None:
    """Whatever a vendor's entry claims, the provider it builds must report the same thing, or
    `quackd list-models` and the run would disagree about what gets sent."""
    for name in sorted(OPENAI_COMPATIBLE):
        module = importlib.import_module(f"quackd.agent.providers.{name}")
        vendor = getattr(module, OPENAI_COMPATIBLE[name])
        for m in models_for(name):
            assert vendor(model=m.id, client=FakeOpenAI()).supports_vision is m.vision, m.id


@pytest.mark.parametrize("name", sorted(OPENAI_COMPATIBLE))
def test_each_openai_shaped_vendor_is_wired_the_same_way(name: str) -> None:
    module = importlib.import_module(f"quackd.agent.providers.{name}")
    vendor = getattr(module, OPENAI_COMPATIBLE[name])
    assert vendor.name == name
    assert vendor.key_env == KEY_ENV[name]
    assert vendor.extra == EXTRA_FOR[name]
    assert vendor.base_url and vendor.base_url.startswith("https://"), (
        "a key goes to this address, so it is not going over plain http"
    )
    assert vendor.default_tool_choice == TOOL_CHOICE[name]
    assert vendor(client=FakeOpenAI()).model == default_model_for(name)


@pytest.mark.parametrize("name", sorted(OPENAI_COMPATIBLE))
def test_each_openai_shaped_vendor_names_its_own_key(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for env in set(KEY_ENV.values()) | {"CO_API_KEY", "MODEL_API_KEY"}:
        monkeypatch.delenv(env, raising=False)
    module = importlib.import_module(f"quackd.agent.providers.{name}")
    vendor = getattr(module, OPENAI_COMPATIBLE[name])
    with pytest.raises(ProviderMissingKey, match=KEY_ENV[name]):
        vendor()


@pytest.mark.parametrize(
    ("name", "fallback"), [("cohere", "CO_API_KEY"), ("meta", "MODEL_API_KEY")]
)
def test_the_two_vendors_with_a_second_key_name_accept_it(
    name: str, fallback: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both vendors' own examples export a different variable than their docs head with, and a
    reader who has one exported should not be told they have none.

    Built with no `client=`, deliberately. `OpenAIProvider` reads a key only when it has to make
    one, so passing a stub skips `_fallback_key` entirely and this test would pass with both
    overrides deleted. Without the SDK installed the constructor then gets as far as the import
    and raises `ProviderNotInstalled`, which is itself the proof: it got past the key.
    """
    monkeypatch.delenv(KEY_ENV[name], raising=False)
    monkeypatch.setenv(fallback, "sk-test")
    module = importlib.import_module(f"quackd.agent.providers.{name}")
    vendor = getattr(module, OPENAI_COMPATIBLE[name])
    try:
        vendor()
    except ProviderMissingKey:  # pragma: no cover - the failure this test is here to catch
        pytest.fail(f"{name} has {fallback} exported and still says {KEY_ENV[name]} is unset")
    except ProviderNotInstalled:
        pass


@pytest.mark.parametrize("name", sorted(OPENAI_COMPATIBLE))
def test_a_vendor_with_neither_key_name_still_says_which_one_it_wants(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: with nothing exported, the error names the variable the docs head with."""
    for env in set(KEY_ENV.values()) | {"CO_API_KEY", "MODEL_API_KEY"}:
        monkeypatch.delenv(env, raising=False)
    module = importlib.import_module(f"quackd.agent.providers.{name}")
    with pytest.raises(ProviderMissingKey, match=KEY_ENV[name]):
        getattr(module, OPENAI_COMPATIBLE[name])()


def test_the_catalogue_costs_nothing_to_import() -> None:
    """`quackd --help` and every press of TAB import this module, so it may not drag in pydantic
    or an SDK. Read as source rather than by importing, because by the time this test runs the
    suite has imported half of quackd already."""
    source = (REPO / "quackd" / "agent" / "providers" / "catalogue.py").read_text(encoding="utf-8")
    for forbidden in ("import pydantic", "from pydantic", "import openai", "import anthropic"):
        assert forbidden not in source


def test_every_vendor_file_is_reachable_from_the_factory() -> None:
    """A provider module nobody dispatches to is a vendor that silently does not exist.

    `vla` is plumbing rather than a vendor: `--controller vla` builds it, never `--llm`."""
    directory = REPO / "quackd" / "agent" / "providers"
    modules = {p.stem for p in directory.glob("*.py")}
    plumbing = {
        *("__init__", "base", "catalogue", "factory", "fake", "local", "openai", "pricing"),
        "vla",
    }
    assert modules - plumbing - {"anthropic", "gemini"} == set(OPENAI_COMPATIBLE)


def test_the_local_presets_are_the_ones_the_catalogue_excuses() -> None:
    """The catalogue and `local.py` keep separate lists, and they must name the same servers."""
    assert set(cat.LOCAL_NAMES) == set(PRESETS)
    for name in cat.LOCAL_NAMES:
        assert models_for(name) == ()
