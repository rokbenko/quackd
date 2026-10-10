"""Which decision LLM a run gets, and what reaches it.

`test_decision.py` next door is about the stepper: which turns are a choice, what an answer
has to clear, what the record says. This file is about everything in front of that -- the
flags, the variables, the table of presets, the backends and the plugin hook -- because that is
the half that grew when Jev stopped being the only one. What only the `openai` row does, its
translation and where its key goes, is `test_decisions_api.py`.

The one rule worth stating up front: nothing in this file may need `typesafe_sdk`, `openai` or
`laya` to be installed. All three are optional extras, CI installs none of them, and a suite that
quietly skipped here would be a suite that never tested the thing this release is.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.metadata
import json
import sys
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from quackd.agent.decision import factory
from quackd.agent.decision.base import TIMEOUT_S, DecisionError, client_timeout
from quackd.agent.decision.catalogue import (
    DECISIONS_API,
    ENTRY_POINT_GROUP,
    IN_PROCESS,
    PRESET_NAMES,
    PRESETS,
    SYSTEM_ONE,
)
from quackd.agent.decision.factory import (
    decision_llm_is_available,
    find_preset,
    make_decision_llm,
    parse_decision_llm,
    preset_names,
    resolve_decision_mode,
    resolve_decision_model,
    resolve_decision_price,
    resolve_decision_url,
)
from quackd.agent.decision.stepper import MAX_IN_A_ROW, Call, Stepper, verb_class
from tests import fake_laya, fake_systemone

ROOT = Path(__file__).resolve().parents[1]


async def _advise_with(
    answers: dict[str, Any] | None,
    llm: Any = None,
    *,
    goal: str = "a goal",
    features: dict[str, Any] | None = None,
) -> Any:
    """One turn against a backend that answers exactly what it was handed.

    Built bare rather than through `Stepper.build`, because what is under test is how an
    answer is read and a registry of verbs would only be scenery."""

    class Says:
        name, model, url = "probe", "p", None

        async def decide(self, state: Any, questions: Any) -> Any:
            return {"answers": answers or {}}

    stepper = Stepper(
        mode="on",
        goal=goal,
        llm=llm or Says(),
        name="probe",
        price=resolve_decision_price(PRESETS["local"]),
    )
    stepper.calls["stop"] = Call(name="stop", arguments={}, label="stop")
    stepper.classes["stop"] = "brake"
    stepper.what["stop"] = "the brake"
    stepper.early.add("stop")
    return await stepper.advise(SimpleNamespace(features=features or {}), cleared=True, budget="b")


# ── the table ───────────────────────────────────────────────────────────────────────────


def test_every_preset_row_is_complete() -> None:
    """A row is the whole definition of a decision LLM, so a half-filled one is a preset that
    names itself in `--help` and then fails somewhere further in."""
    for name, spec in PRESETS.items():
        assert spec.name == name, f"{name}: the row and its key disagree"
        assert spec.backend in (SYSTEM_ONE, IN_PROCESS, DECISIONS_API), f"{name}: unknown backend"
        assert spec.summary and not spec.summary.endswith("."), f"{name}: summary is a label"
        assert spec.install, f"{name}: nothing says how to get it running"
        assert spec.sdk, f"{name}: no import to probe, so doctor cannot say if it is here"


def test_only_the_hosted_rows_carry_a_key_or_a_price_and_only_jev_a_model_variable() -> None:
    """The things that belong to a vendor rather than to a format. Two rows are hosted, so two
    carry a key and a published rate, and a third growing either is the table drifting back
    towards one-module-per-vendor. The model variable is the one SDK's own that quackd honours
    where that SDK owns the address, which is `jev` alone: `openai`'s backend always hands its
    SDK an address."""
    for name, spec in PRESETS.items():
        hosted = name in ("jev", "openai")
        assert bool(spec.key_env) is hosted, f"{name}: key_env"
        assert bool(spec.price) is hosted, f"{name}: price"
        assert bool(spec.model_env) is (name == "jev"), f"{name}: model_env"


def test_no_row_may_be_shown_a_picture() -> None:
    """The stepper sends text and nothing else, `openai` included although its API would take
    an image. The day one may see is this set growing, deliberately and nowhere else."""
    assert {name for name, spec in PRESETS.items() if spec.sees} == set()


def test_a_preset_url_carries_no_path() -> None:
    """The SDK appends `/v1/systemone` to whatever it is given, without normalising, so a row
    written in the style of `providers/local.py` -- which stores `http://host:8000/v1` --
    would ask for `/v1/v1/systemone` and get a 404 that named nothing. The `openai` row is held
    to the same rule for the same reason: its backend adds `/v1` itself."""
    for name, spec in PRESETS.items():
        if spec.url is None:
            continue
        assert spec.url.startswith("http://") or spec.url.startswith("https://"), name
        rest = spec.url.split("://", 1)[1]
        assert "/" not in rest, f"{name}: {spec.url} has a path; the SDK adds its own"


def test_only_the_two_rows_that_are_told_an_address_lack_one() -> None:
    """`local` exists to be told and `laya` has none to tell, and everything else listens
    somewhere quackd can name without being asked."""
    assert {name for name, spec in PRESETS.items() if spec.url is None} == {"jev", "local", "laya"}


def test_the_hosted_model_is_pinned_and_the_open_ones_are_named() -> None:
    """A moving alias is a run whose transcript describes a model that is no longer the one
    that answered. And the open servers do not share an id: OpenJev refuses a pinned Jev
    version with a 400, so a shared default would have been wrong there on every request."""
    assert PRESETS["jev"].model == "jev-1.13.0"
    assert PRESETS["openai"].model == "gpt-6-luna"
    assert PRESETS["openjev"].model != PRESETS["jev"].model
    assert PRESETS["local"].model is None


def test_the_extras_the_table_names_exist_and_install_what_it_says() -> None:
    """A row pointing at an extra nobody declared is an error message telling somebody to run
    an install that does nothing."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    for name, spec in PRESETS.items():
        assert spec.extra in extras, f"{name}: quackd[{spec.extra}] is not declared"
        installs = " ".join(extras[spec.extra])
        assert spec.sdk is not None
        assert spec.sdk.replace("_", "-") in installs, f"{name}: {spec.extra} does not install it"


def test_neither_optional_extra_is_in_all() -> None:
    """`quackd[all]` is the three pilot SDKs. A decision LLM generates nothing and cannot pilot
    a robot, and the in-process one would put torch in there."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    everything = " ".join(pyproject["project"]["optional-dependencies"]["all"])
    assert "typesafe" not in everything and "laya" not in everything


def test_the_openai_row_installs_nothing_the_pilot_does_not() -> None:
    """Its extra is the pilot's own, so naming it adds no package to `quackd[all]` and no line
    to the lock: every requirement it installs is already one `all` carries, word for word."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    assert PRESETS["openai"].extra == "openai"
    assert set(extras["openai"]) <= set(extras["all"])


def test_the_catalogue_costs_nothing_to_import() -> None:
    """`--help` imports this table and so does every press of TAB. Nothing in it may reach for
    a vendor SDK, for torch, or for pydantic.

    Read off the import statements rather than off the text, because the module's own docstring
    names every one of these in the course of promising not to import them."""
    source = (ROOT / "quackd" / "agent" / "decision" / "catalogue.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for heavy in ("typesafe_sdk", "openai", "laya", "torch", "pydantic", "httpx", "importlib"):
        assert heavy not in imported, f"catalogue.py imports {heavy}"


def test_importing_the_package_imports_no_backend() -> None:
    """The loop imports this on every run, including the overwhelming majority that name no
    decision LLM at all."""
    assert "typesafe_sdk" not in sys.modules
    assert "laya" not in sys.modules


# ── the spec ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("spec", "name", "model"),
    [
        ("jev", "jev", None),
        ("jev:jev-latest", "jev", "jev-latest"),
        ("KEV", "kev", None),
        ("  kev  ", "kev", None),
        ("laya:multilingual", "laya", "multilingual"),
        ("local:my-own-build", "local", "my-own-build"),
    ],
)
def test_a_spec_is_a_name_and_maybe_a_model(spec: str, name: str, model: str | None) -> None:
    parsed = parse_decision_llm(spec)
    assert parsed is not None
    found, asked = parsed
    assert (found.name, asked) == (name, model)


@pytest.mark.parametrize("spec", ["off", "OFF", "", "   "])
def test_off_and_blank_are_off(spec: str) -> None:
    """Off is the answer rather than a fallback: nothing here is switched on by being found."""
    assert parse_decision_llm(spec) is None


def test_nothing_named_anywhere_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(factory.ENV_LLM, "")
    assert parse_decision_llm(None) is None


def test_the_variable_names_one_when_the_flag_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(factory.ENV_LLM, "kev")
    parsed = parse_decision_llm(None)
    assert parsed is not None and parsed[0].name == "kev"


def test_the_flag_beats_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(factory.ENV_LLM, "kev")
    parsed = parse_decision_llm("jev")
    assert parsed is not None and parsed[0].name == "jev"


def test_the_flag_can_turn_off_what_the_variable_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A variable in a `.env` three directories up should be arguable with on the command
    line, and `--decision-llm off` is how you argue."""
    monkeypatch.setenv(factory.ENV_LLM, "jev")
    assert parse_decision_llm("off") is None


def test_a_name_nobody_defined_says_so_and_lists_the_ones_that_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(DecisionError) as e:
        parse_decision_llm("nosuch")
    message = str(e.value)
    assert "nosuch" in message and "--decision-llm" in message
    for name in PRESET_NAMES:
        assert name in message


def test_the_refusal_names_the_variable_when_the_variable_is_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag somebody has just typed and a line in a `.env` they have forgotten want
    different sentences, and the second is the one that is hard to find."""
    monkeypatch.setenv(factory.ENV_LLM, "nosuch")
    with pytest.raises(DecisionError, match=factory.ENV_LLM):
        parse_decision_llm(None)


def test_a_model_keeps_its_own_colons() -> None:
    """Split at the first colon only. A checkpoint named like an Ollama tag is still one
    name, and cutting it in half would ask a server for something it does not serve."""
    parsed = parse_decision_llm("local:org/model:v2")
    assert parsed is not None and parsed[1] == "org/model:v2"


def test_a_trailing_colon_is_the_default_model() -> None:
    """Shell completion offers `kev:` as a prefix, and a reader may well press enter on it."""
    parsed = parse_decision_llm("kev:")
    assert parsed is not None and parsed[1] is None


# ── the mode ────────────────────────────────────────────────────────────────────────────


def test_naming_one_is_asking_for_it() -> None:
    """The default once you have named a decision LLM is `on`, not a second flag you also have
    to remember."""
    assert resolve_decision_mode(None, named=True) == "on"
    assert resolve_decision_mode(None, named=False) == "off"


@pytest.mark.parametrize("mode", ["off", "shadow", "on", "SHADOW"])
def test_a_mode_is_taken_as_written(mode: str) -> None:
    assert resolve_decision_mode(mode, named=True) == mode.lower()


def test_the_mode_variable_is_read_and_the_flag_beats_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(factory.ENV_MODE, "shadow")
    assert resolve_decision_mode(None, named=True) == "shadow"
    assert resolve_decision_mode("on", named=True) == "on"


def test_off_wins_over_a_named_decision_llm() -> None:
    """The mode is the switch, so it is the thing that can say no."""
    assert resolve_decision_mode("off", named=True) == "off"


def test_a_mode_nobody_defined_stops_the_run() -> None:
    with pytest.raises(DecisionError, match="maybe"):
        resolve_decision_mode("maybe", named=True)


def test_a_mode_with_nothing_to_run_it_says_which_flag_is_missing() -> None:
    """Silently doing nothing is the one outcome worth refusing here: a script that asked for
    shadow and got a plain run would go on producing plain runs indefinitely."""
    with pytest.raises(DecisionError) as e:
        resolve_decision_mode("shadow", named=False)
    assert "--decision-llm" in str(e.value) and "--decision-mode" in str(e.value)


def test_a_mode_from_a_variable_with_nothing_to_run_it_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(factory.ENV_MODE, "on")
    with pytest.raises(DecisionError, match=factory.ENV_MODE):
        resolve_decision_mode(None, named=False)


# ── the address, the model and the rate ─────────────────────────────────────────────────


def test_the_address_falls_back_from_flag_to_variable_to_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kev = PRESETS["kev"]
    assert resolve_decision_url(kev, "http://gpu:9000") == "http://gpu:9000"
    monkeypatch.setenv(factory.ENV_URL, "http://box:8009")
    assert resolve_decision_url(kev, None) == "http://box:8009"
    assert resolve_decision_url(kev, "http://gpu:9000") == "http://gpu:9000"
    monkeypatch.setenv(factory.ENV_URL, "")
    assert resolve_decision_url(kev, None) == kev.url


def test_the_hosted_row_has_no_address_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its address belongs to the SDK, which has its own variable for moving it."""
    monkeypatch.setenv(factory.ENV_URL, "")
    assert resolve_decision_url(PRESETS["jev"], None) is None


def test_the_model_variable_moves_the_hosted_row_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`TYPESAFE_DEFAULT_MODEL` is the SDK's own, so it is honoured exactly where the SDK owns
    the address too. A stray one must not retarget a server on somebody's own machine."""
    monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "jev-preview")
    assert resolve_decision_model(PRESETS["jev"], None) == "jev-preview"
    assert resolve_decision_model(PRESETS["kev"], None) == PRESETS["kev"].model


def test_a_named_model_beats_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "jev-preview")
    assert resolve_decision_model(PRESETS["jev"], "jev-1.13.0") == "jev-1.13.0"


def test_a_server_you_run_is_free_and_the_hosted_ones_are_not() -> None:
    """Not `None`, which quackd reserves for "there is a rate and nobody here knows it". A
    model on your own machine bills you in electricity, and that is the same `$0` the rest of
    quackd already prices a local pilot at.

    OpenAI's rate was read on a day of its own, and the record says which: without its own
    `checked` it would be stamped with the day the pilots' catalogue was read."""
    assert resolve_decision_price(PRESETS["jev"]).input == 0.042
    openai = resolve_decision_price(PRESETS["openai"])
    assert (openai.input, openai.output, openai.source) == (0.10, 0.0, "published")
    assert openai.record()["checked"] == "2026-10-08"
    for name in ("kev", "von", "openjev", "opendecision", "local", "laya"):
        price = resolve_decision_price(PRESETS[name])
        assert (price.input, price.output) == (0.0, 0.0), name
        assert price.source == "self-hosted", name


def test_the_price_variable_overrides_any_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """For the paid endpoint sitting behind `local`, which quackd has no way to know about."""
    monkeypatch.setenv(factory.PRICE_ENV, "in=1.5,out=0")
    price = resolve_decision_price(PRESETS["local"])
    assert price.input == 1.5 and price.source == factory.PRICE_ENV


# ── is it here at all ───────────────────────────────────────────────────────────────────


def test_the_client_being_absent_names_the_extra_that_installs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    ok, why = decision_llm_is_available(PRESETS["kev"])
    assert not ok and "quackd[decision]" in why


def test_the_in_process_one_names_its_own_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "laya", None)
    ok, why = decision_llm_is_available(PRESETS["laya"])
    assert not ok and "quackd[laya]" in why


def test_the_hosted_one_needs_a_key_and_says_which(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    ok, why = decision_llm_is_available(PRESETS["jev"])
    assert not ok and "TYPESAFE_API_KEY" in why


def test_a_server_you_run_needs_no_key_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate that used to be "off when the key is missing" would otherwise keep every one
    of these off forever, because none of them has a key to miss."""
    fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    for name in ("kev", "von", "openjev", "opendecision", "local"):
        ok, why = decision_llm_is_available(PRESETS[name])
        assert ok, f"{name}: {why}"


def test_a_hosted_key_in_the_environment_does_not_switch_anything_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Somebody else's project left a key in a `.env`. That is not a request."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-not-a-request")
    monkeypatch.setenv(factory.ENV_LLM, "")
    assert parse_decision_llm(None) is None
    assert resolve_decision_mode(None, named=False) == "off"


# ── building one ────────────────────────────────────────────────────────────────────────


def test_a_server_you_run_gets_a_word_for_a_key_and_its_own_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hosted key must never be sent to a server on somebody's own machine, and the row's
    port must arrive without the SDK having to guess."""
    fake = fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-hosted-and-private")
    monkeypatch.setenv(factory.ENV_URL, "")
    llm = make_decision_llm(PRESETS["kev"])
    assert fake.api_key == "local"
    assert fake.base_url == "http://127.0.0.1:8009"
    assert llm.model == "kev-latest" and llm.name == "kev"


def test_the_hosted_one_is_handed_no_key_and_no_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both belong to the SDK, which reads its own variables for them and validates the key
    better than quackd could."""
    fake = fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-hosted")
    monkeypatch.setenv(factory.ENV_URL, "")
    make_decision_llm(PRESETS["jev"])
    assert fake.api_key is None and fake.base_url is None


def test_the_address_reaches_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    llm = make_decision_llm(PRESETS["local"], url="http://gpu.local:9001", model="my-build")
    assert fake.base_url == "http://gpu.local:9001"
    assert llm.url == "http://gpu.local:9001" and llm.model == "my-build"


def test_the_clients_own_clock_sits_above_the_steppers_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TypeSafe's client hands its `timeout` to httpx2 for each operation of a request, so set to
    the stepper's second it came due together with the stepper's own bound, and one slow turn
    could be recorded as quackd's `no answer within 1.0 s` or as the SDK's own timeout, whichever
    the event loop reached first. Above it, the stepper's bound is the one clock that ends a
    turn. The retry budget only decides whether a retry is started, so it stays at the second,
    and the SDK never sleeps towards a retry the turn would not live to send."""
    fake = fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    make_decision_llm(PRESETS["local"], url="http://gpu.local:9001")
    assert fake.timeout == client_timeout(TIMEOUT_S) > TIMEOUT_S
    assert fake.retry is not None and fake.retry.timeout == TIMEOUT_S


def test_the_row_that_exists_to_be_told_an_address_refuses_without_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused rather than guessed, the way `--llm local` refuses, and the refusal names both
    ways of saying it."""
    fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    monkeypatch.setenv(factory.ENV_URL, "")
    with pytest.raises(DecisionError) as e:
        make_decision_llm(PRESETS["local"])
    assert "--decision-url" in str(e.value) and factory.ENV_URL in str(e.value)


def test_the_client_being_absent_is_a_sentence_rather_than_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    with pytest.raises(DecisionError, match=r"quackd\[decision\]"):
        make_decision_llm(PRESETS["kev"], url="http://127.0.0.1:8009")


# ── the one that runs in this process ───────────────────────────────────────────────────


async def test_the_in_process_one_answers_from_its_own_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = fake_laya.install(
        monkeypatch, fake_laya.FakeRouter(answers=fake_laya.turn("report_state"))
    )
    llm = make_decision_llm(PRESETS["laya"])
    result = await llm.decide({"goal": "look"}, {"next_verb": {"type": "choice"}})
    assert result["answers"]["next_verb"]["choice"] == "report_state"
    assert router.calls[0][0] == {"goal": "look"}


async def test_the_in_process_one_loads_once_and_asks_for_the_decision_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loading costs seconds against a timeout of one, so a second load would be a turn lost
    every turn. And the checkpoint asked for is the decision-tuned one: its plain name is an
    alias for the English model, which is a different thing that would answer anyway."""
    router = fake_laya.install(monkeypatch, fake_laya.FakeRouter(answers=fake_laya.turn("stop")))
    llm = make_decision_llm(PRESETS["laya"])
    await llm.decide({"goal": "a"}, {})
    await llm.decide({"goal": "b"}, {})
    assert router.loads == 1
    assert router.kwargs.get("preload") is True
    assert {model for _s, _q, model in router.calls} == {"typed-decisions"}


async def test_the_in_process_one_reports_a_count_quackd_bills_as_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Laya counts the tokens it actually read, so its turns are measured rather than estimated.

    This was documented backwards for a while, in the module docstring, on the page and in the
    fake, which between them had the suite proving the estimate path for the one backend that
    does not use it. `laya/agent.py` returns `usage={"input_tokens": int(attention_mask.sum()),
    "output_tokens": 0}`, and a positive input count is exactly what `_usage` reads as a
    measurement. The zero output is discarded as a field never filled in and never reaches the
    flag. It still costs nothing: a checkpoint in this process is charged at the self-hosted
    rate.
    """
    fake_laya.install(
        monkeypatch,
        fake_laya.FakeRouter(answers=fake_laya.turn("report_state"), input_tokens=321),
    )
    llm = make_decision_llm(PRESETS["laya"])
    result = await llm.decide({"goal": "look"}, {"next_verb": {"type": "choice"}})
    assert result["usage"]["input_tokens"] == 321, "the count Laya reported has to survive"
    assert result["usage"]["output_tokens"] == 0
    # and the choice carries a distribution, which is what gives these turns a runner-up line
    assert result["answers"]["next_verb"]["probabilities"], "a choice answer carries its spread"


async def test_an_older_in_process_package_still_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The keyword that preloads the right checkpoint is younger than the class it belongs to,
    and quackd would rather load the wrong one once than refuse to run."""
    router = fake_laya.install(
        monkeypatch,
        fake_laya.FakeRouter(answers=fake_laya.turn("stop"), rejects=("default",)),
    )
    llm = make_decision_llm(PRESETS["laya"])
    await llm.decide({"goal": "a"}, {})
    # The construction that named the keyword raised before it built anything, so exactly one
    # router exists, and it is the one built without it.
    assert router.loads == 1
    assert router.kwargs.get("preload") is True
    assert "default" not in router.kwargs and router.kwargs.get("max_loaded") == 2


async def test_a_load_that_failed_is_not_retried_every_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken install should cost one attempt, not one per turn of a twelve-step run."""

    class Boom(RuntimeError):
        pass

    router = fake_laya.FakeRouter()

    def explode(**_kwargs: Any) -> Any:
        router.loads += 1
        raise Boom("no weights here")

    module = fake_laya.install(monkeypatch, router)
    monkeypatch.setattr(sys.modules["laya"], "Router", explode)
    llm = make_decision_llm(PRESETS["laya"])
    for _ in range(3):
        with pytest.raises(Boom):
            await llm.decide({"goal": "a"}, {})
    assert router.loads == 1
    assert module is router


def test_the_in_process_one_needs_no_address(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_laya.install(monkeypatch, fake_laya.FakeRouter())
    assert make_decision_llm(PRESETS["laya"]).url is None


# ── somebody else's ─────────────────────────────────────────────────────────────────────


class _Entry:
    """An `importlib.metadata.EntryPoint`, as far as the loader reads one."""

    def __init__(self, name: str, value: str) -> None:
        self.name, self.value, self.group = name, value, ENTRY_POINT_GROUP


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A third party's decision LLM, announced the only way one can be."""
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: (
            [_Entry("stub", "tests.stub_decision_llm")]
            if kw.get("group") == ENTRY_POINT_GROUP
            else []
        ),
    )
    factory._plugins.cache_clear()
    yield sys.modules.get("tests.stub_decision_llm")
    factory._plugins.cache_clear()


def test_a_plugin_is_found_and_named(plugin: Any) -> None:
    assert "stub" in preset_names()
    spec = find_preset("stub")
    assert spec.name == "stub" and spec.summary == "a decision LLM that exists only in this test"
    assert spec.sdk is None  # it was found through its own metadata, so it is installed


async def test_a_plugin_is_built_and_asked(plugin: Any) -> None:
    import tests.stub_decision_llm as stub

    stub.built.clear()
    llm = make_decision_llm(find_preset("stub"), url="http://elsewhere:1234")
    assert stub.built and stub.built[-1] is llm
    assert llm.url == "http://elsewhere:1234" and llm.model == "stub-1"
    await llm.decide({"goal": "x"}, {"next_verb": {"type": "choice"}})
    assert llm.calls[0][0] == {"goal": "x"}


def test_a_plugin_is_named_in_a_refusal(plugin: Any) -> None:
    with pytest.raises(DecisionError, match="stub"):
        parse_decision_llm("nosuch")


def test_a_plugin_cannot_take_a_name_quackd_ships(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise a package on an index could quietly become the thing `--decision-llm jev`
    reaches, which is the whole supply-chain question in one line."""
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [_Entry("jev", "tests.stub_decision_llm")],
    )
    factory._plugins.cache_clear()
    try:
        assert find_preset("jev") is PRESETS["jev"]
    finally:
        factory._plugins.cache_clear()


def test_an_entry_point_whose_module_is_gone_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """An editable install keeps the metadata it was built with, so a module moved or removed
    since is still announced. Believing it turns a missing plugin into an ImportError from
    somewhere far away."""
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [_Entry("ghost", "tests.no_such_module_at_all")],
    )
    factory._plugins.cache_clear()
    try:
        assert "ghost" not in preset_names()
    finally:
        factory._plugins.cache_clear()


# ── what an adversarial pass found ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "answers",
    [
        {"next_verb": {"choice": "stop", "confidence": 0.99}},
        {"next_verb": {"choice": "stop", "confidence": 0.99}, "done": {"noul": 0.0}},
        {
            "next_verb": {"choice": "stop", "confidence": 0.99},
            "done": {"noul": 0.0},
            "need_human": {},
        },
        {
            "next_verb": {"choice": "stop", "confidence": 0.99},
            "done": 0.0,
            "need_human": 0.0,
        },
    ],
    ids=["only-a-verb", "one-noul-missing", "a-noul-with-nothing-in-it", "nouls-as-bare-floats"],
)
async def test_a_question_nobody_answered_is_not_an_answer_of_no(answers: dict[str, Any]) -> None:
    """The two Nouls stop a run and ask for a person, and read as zero they say the opposite.

    A backend that answers only `next_verb` -- a plugin under construction, a server that
    dropped a field, a model with no Nouls at all -- used to have both gates default to 0.0,
    which is "certainly not finished, certainly nobody needed", and those are exactly the two
    answers that let a verb through. A question nobody answered hands the turn to the model.
    """
    advice = await _advise_with(answers)
    assert advice.record["gate"] == "unreadable"
    assert advice.call is None
    assert advice.record["done"] is None or advice.record["need_human"] is None


async def test_a_confidence_spelled_as_a_boolean_clears_nothing() -> None:
    """`float(True)` is 1.0, which is above every floor in the table, so a backend that wrote
    a confidence as a boolean was believed absolutely rather than doubted."""
    advice = await _advise_with(
        {
            "next_verb": {"choice": "stop", "confidence": True},
            "done": {"noul": 0.0},
            "need_human": {"noul": 0.0},
        }
    )
    assert advice.record["gate"] == "unreadable" and advice.call is None


def _takes_stop(**over: dict[str, Any]) -> dict[str, Any]:
    """Three answers the gates would take `stop` on, with `over` in place of whichever it names."""
    answers: dict[str, Any] = {
        "next_verb": {"choice": "stop", "confidence": 0.99},
        "done": {"noul": 0.0},
        "need_human": {"noul": 0.0},
    }
    return {**answers, **over}


@pytest.mark.parametrize(
    ("question", "field", "said"),
    [
        ("next_verb", "confidence", "0.99"),
        ("next_verb", "confidence", b"0.99"),
        ("next_verb", "confidence", 5.0),
        ("next_verb", "confidence", 60),
        ("next_verb", "confidence", -0.5),
        ("done", "noul", "0"),
        ("done", "noul", -1.0),
        ("need_human", "noul", -1.0),
        ("need_human", "noul", 1.5),
    ],
)
async def test_a_number_that_is_no_probability_is_unreadable_on_every_row(
    question: str, field: str, said: Any
) -> None:
    """A gate reads a probability, a number from 0 to 1, and nothing else. `float()` used to
    read whatever it could, so a confidence written as text cleared its floor, a percentage or a
    5 cleared every floor, a `done` of -1 said certainly not, and each moved the body; a
    `need_human` of 1.5 asked a person on a number no backend meant. Every one is the answer
    nobody could read, and the record keeps `null` where it was."""
    advice = await _advise_with(_takes_stop(**{question: {**_takes_stop()[question], field: said}}))
    assert advice.record["gate"] == "unreadable" and advice.call is None
    recorded = {"next_verb": "confidence", "done": "done", "need_human": "need_human"}[question]
    assert advice.record[recorded] is None


async def test_the_state_is_measured_and_shown_with_its_letters_as_letters() -> None:
    """A letter outside ASCII is one character, to the measure and to the model. `state_chars`
    used to count JSON's six-character escape for it, so a goal in Slovenian measured longer than
    the same goal in ASCII, and the flock's chatter, written into the state as JSON of its own,
    reached the backend as escapes."""
    shown: list[dict[str, str]] = []

    class Keeps:
        name, model, url = "probe", "p", None

        async def decide(self, state: Any, questions: Any) -> Any:
            shown.append(dict(state))
            return {"answers": _takes_stop()}

    flock = {"features": {"flock": {"duck": "žoga je pod mizo"}}}
    slovenian = await _advise_with(None, llm=Keeps(), goal="poberi žogo", **flock)
    ascii_only = await _advise_with(None, llm=Keeps(), goal="poberi zogo", **flock)
    assert slovenian.record["state_chars"] == ascii_only.record["state_chars"]
    assert shown[0]["goal"] == "poberi žogo"
    assert "žoga je pod mizo" in shown[0]["flock"] and "\\u" not in shown[0]["flock"]


async def test_a_probability_may_be_certain_and_may_be_a_whole_number() -> None:
    """Both ends of 0 to 1 are probabilities, and JSON may well send them as `0` and `1`."""
    advice = await _advise_with(
        _takes_stop(next_verb={"choice": "stop", "confidence": 1}, done={"noul": 0})
    )
    assert advice.record["gate"] == "taken" and advice.call is not None


@pytest.mark.parametrize(
    "next_verb",
    [
        {"confidence": 0.99},
        {"choice": None, "confidence": 0.99},
        {"choice": 3, "confidence": 0.99},
        {"value": "stop", "confidence": 0.99},
    ],
    ids=["no choice", "a null choice", "a number", "the choice under another name"],
)
async def test_a_next_verb_with_no_choice_in_it_is_unreadable_and_never_an_escalate(
    next_verb: dict[str, Any],
) -> None:
    """A missing choice used to default to `escalate`, so an answer that carried a confidence and
    no choice was recorded as a confident decision to hand the turn back, the gate `escalate`,
    rather than as the answer nobody could read that it was."""
    advice = await _advise_with(_takes_stop(next_verb=next_verb))
    assert advice.record["gate"] == "unreadable" and advice.call is None


@pytest.mark.parametrize(
    ("feasible", "recorded"),
    [
        (
            {"choice": "feasible", "confidence": float("nan")},
            {"choice": "feasible", "confidence": None},
        ),
        (
            {"choice": "feasible", "confidence": float("inf")},
            {"choice": "feasible", "confidence": None},
        ),
        ({"choice": float("nan"), "confidence": 0.9}, {"choice": None, "confidence": 0.9}),
    ],
    ids=["a NaN confidence", "an infinite confidence", "a NaN choice"],
)
async def test_the_record_holds_no_number_json_cannot_spell(
    feasible: dict[str, Any], recorded: dict[str, Any]
) -> None:
    """`transcript.jsonl` is JSON, and a NaN or an infinity in a distribution, or anywhere in
    `feasible`, which nothing gates on, reached it as a bare `NaN` or `Infinity`: Python reads
    the line back, and `jq` and `JSON.parse` refuse it. The distribution leaves such a number
    out, and `feasible` records `null` for a confidence that is no probability and for a choice
    that is no label. The turn itself is read as it would have been."""
    answers = _takes_stop(
        next_verb={
            "choice": "stop",
            "confidence": 0.99,
            "probabilities": {"stop": 0.99, "escalate": float("nan"), "other": float("-inf")},
        },
        feasible=feasible,
    )
    advice = await _advise_with(answers)
    json.dumps(advice.event(), allow_nan=False)
    assert advice.record["gate"] == "taken"
    assert advice.record["probabilities"] == {"stop": 0.99}
    assert advice.record["feasible"] == recorded


async def test_a_backend_that_never_answers_costs_one_turn_and_not_the_run() -> None:
    """The page promises a second. The SDK's own per-request default is ten, its retry budget
    is a different clock again, and a model in this process has no timeout at all, so the only
    place the promise can be kept is the seam."""

    class Hangs:
        name, model, url = "hangs", "h", None

        async def decide(self, state: Any, questions: Any) -> Any:
            await asyncio.sleep(30)

    started = time.perf_counter()
    advice = await _advise_with(None, llm=Hangs())
    assert time.perf_counter() - started < TIMEOUT_S * 3
    assert advice.record["gate"] == "error" and advice.call is None
    assert str(TIMEOUT_S) in advice.record["error"], "the record should say what it waited"


def test_the_address_variable_never_retargets_the_hosted_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`QUACKD_DECISION_URL` is the line somebody leaves in a `.env` after an afternoon with a
    server on their own machine. Applied to the hosted row it would send that vendor's key --
    which its client reads for itself -- to whatever is listening on localhost."""
    monkeypatch.setenv(factory.ENV_URL, "http://127.0.0.1:8009")
    assert resolve_decision_url(PRESETS["jev"]) is None
    assert resolve_decision_url(PRESETS["kev"]) == "http://127.0.0.1:8009"


def test_a_hosted_row_at_an_address_you_typed_is_a_server_you_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--decision-llm jev --decision-url http://localhost:8009` means Jev's model id at my
    address, and a company's API key has no business going there."""
    fake = fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-hosted-and-private")
    make_decision_llm(PRESETS["jev"], url="http://127.0.0.1:8009")
    assert fake.api_key == "local"


def test_a_hosted_row_at_an_address_you_typed_needs_no_key_and_is_costed_as_yours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sent the word rather than `TYPESAFE_API_KEY` at a `--decision-url` (the test above), so a
    run there was switched off for want of a key it would never send, and with the key set its
    turns were costed at TypeSafe's rate for a server on the bench. The rule `openai` keeps at an
    address that is not OpenAI's, now kept by both, because one rule (`factory.sends_key`)
    answers both questions for every row."""
    fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    jev, url = PRESETS["jev"], "http://127.0.0.1:8009"
    assert decision_llm_is_available(jev, url=url) == (True, "")
    price = resolve_decision_price(jev, url=url)
    assert (price.input, price.output, price.source) == (0.0, 0.0, "self-hosted")
    ok, why = decision_llm_is_available(jev)
    assert not ok and "TYPESAFE_API_KEY" in why, "with no address it is TypeSafe's, and wants it"
    assert resolve_decision_price(jev) == jev.price


def test_a_run_of_jev_at_an_address_you_typed_keeps_its_stepper_without_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command line's end of the rule above. No TypeSafe key here, `--decision-url` on the
    line, and the run used to say "running without it: --decision-llm jev needs
    TYPESAFE_API_KEY" and drop the stepper. It keeps it now, sends the word, and records the
    self-hosted rate. Empty rather than deleted, because an empty variable survives the `.env`
    the CLI loads."""
    import json

    from typer.testing import CliRunner

    from quackd.cli import app

    fake = fake_systemone.install(
        monkeypatch, fake_systemone.FakeDecisionLLM(answers=fake_systemone.turn("stop"))
    )
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    result = CliRunner().invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--robot",
            "lerobot:mock",
            "--llm",
            "fake",
            "--decision-llm",
            "jev",
            "--decision-url",
            "http://127.0.0.1:9",
            "--decision-mode",
            "shadow",
            "--no-log",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "running without it" not in result.output
    assert fake.api_key == "local" and fake.calls, "the stepper was not asked"
    (run_dir,) = tmp_path.iterdir()
    records = [
        json.loads(line)
        for line in (run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    start = next(r for r in records if r.get("kind") == "run_start")
    assert start["decision_price"]["source"] == "self-hosted"


@pytest.mark.parametrize("url", [None, "http://127.0.0.1:8009"], ids=["no address", "an address"])
@pytest.mark.parametrize("name", ["jev", "kev", "laya"])
def test_the_rule_for_the_key_is_what_the_client_sends(
    name: str, url: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`factory.sends_key` answers before any client exists, for the availability check and the
    price, and each client decides what it sends for itself. Held to each other here, so the two
    cannot drift: a System One client that sends its key hands the SDK nothing and lets it read
    the variable, and one that does not hands it the word. `openai`'s half is in
    `tests/test_decisions_api.py`, beside the hosts."""
    fake = fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    fake_laya.install(monkeypatch, fake_laya.FakeRouter())
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-hosted-and-private")
    monkeypatch.setenv(factory.ENV_URL, "")
    spec = PRESETS[name]
    make_decision_llm(spec, url=url)
    if spec.backend == SYSTEM_ONE:
        assert (fake.api_key is None) is factory.sends_key(spec, url)
    else:
        assert factory.sends_key(spec, url) is False, "nothing in this process takes a key"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_address_is_a_shell_saying_unset(
    blank: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read as an address it defeated the one refusal `local` exists to make, and then errored
    once per turn, each of them billed."""
    monkeypatch.setenv(factory.ENV_URL, blank)
    assert resolve_decision_url(PRESETS["local"]) is None


def test_a_blank_mode_flag_does_not_discard_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--decision-mode ""` is what a wrapper script produces from an unset variable of its
    own. Taken as a spoken answer it threw away a `QUACKD_DECISION_MODE=shadow` and ran the
    stepper for real, which is the one direction this must never fail in."""
    monkeypatch.setenv(factory.ENV_MODE, "shadow")
    assert resolve_decision_mode("", named=True) == "shadow"


def test_saying_off_on_the_line_is_not_a_run_with_nothing_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--decision-llm off` is how one command opts out of a `.env`, and refusing it because
    that same `.env` also set a mode answers "I do not want this" with a demand to name one."""
    monkeypatch.setenv(factory.ENV_MODE, "shadow")
    assert resolve_decision_mode(None, named=False, refused=True) == "off"


def test_a_plugin_that_cannot_be_imported_does_not_take_doctor_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`find_spec("pkg.mod")` imports `pkg` to ask it, and a stranger's package may raise
    anything at all. This runs on the path of `quackd doctor` and of every press of TAB."""
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [_Entry("ghost", "tests.no_such_module_at_all")],
    )
    factory._plugins.cache_clear()
    try:
        assert "ghost" not in preset_names()
    finally:
        factory._plugins.cache_clear()


def test_a_plugin_cannot_take_a_backends_name_either(monkeypatch: pytest.MonkeyPatch) -> None:
    """A built-in name is guarded; a *backend* name was not, so a plugin calling itself
    `systemone` had quackd's own HTTP client built for it and its `make` never called."""
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [_Entry("systemone", "tests.stub_decision_llm")],
    )
    factory._plugins.cache_clear()
    try:
        import tests.stub_decision_llm as stub

        stub.built.clear()
        llm = make_decision_llm(find_preset("systemone"))
        assert stub.built and stub.built[-1] is llm
    finally:
        factory._plugins.cache_clear()


def test_a_streak_can_never_be_longer_than_half_the_run() -> None:
    """`MAX_IN_A_ROW` is an absolute number and a budget is not. `hello-world` allows five
    steps, so a limit of eight never binds and a stepper could own the whole run, which is the
    failure the constant's own docstring says it prevents."""
    assert Stepper(mode="on", goal="g", max_steps=0).streak_limit == MAX_IN_A_ROW
    assert Stepper(mode="on", goal="g", max_steps=5).streak_limit == 2
    assert Stepper(mode="on", goal="g", max_steps=1).streak_limit == 1
    assert Stepper(mode="on", goal="g", max_steps=100).streak_limit == MAX_IN_A_ROW


def test_a_verb_the_task_file_gated_answers_to_the_confirm_floor() -> None:
    """`Executor.needs_confirm` reads the `.duck`'s own `verbs.confirm` as well as the
    manifest's safety class. A floor that could not see it let a verb its author gated on a
    person through at 0.85 instead of 0.90, and under `--yes` nothing else would have noticed.
    """
    verb = SimpleNamespace(name="quack", safety_class="safe", read_only=False, kind="core")
    assert verb_class(verb, "quack") == "motion"
    assert verb_class(verb, "quack", frozenset({"quack"})) == "confirm"
    brake = SimpleNamespace(name="stop", safety_class="safe", read_only=False, kind="core")
    assert verb_class(brake, "stop", frozenset({"stop"})) == "brake", "the brake is never gated"
