"""Turning tokens into dollars, and the rate card the dollars come from.

quackd counted tokens from its first release and never turned them into money, so every reader
of a transcript did the arithmetic by hand against a rate card they had to go and find.
`quackd/agent/providers/pricing.py` is that arithmetic and this file holds it to the three rules
it promises, because each of them fails silently and expensively when it breaks:

- **An unknown rate is `None`, never zero.** Four Cohere models genuinely have no published
  per-token rate, and one of them is Cohere's default. Printing `$0.00` for a frontier model is
  the failure here that costs somebody real money, so the tests below assert the `None` rather
  than tolerate it.
- **Where a rate is missing but tokens are not, the estimate goes UP.** An unpublished cache rate
  is charged at the full input rate. A bill that is too low is the one that gets believed.
- **A cached prompt is charged once.** `Usage.input_tokens` is the WHOLE prompt with the cached
  slices inside it, so the slices come out before the full rate applies. Reading the field the
  other way doubles the bill on every cache hit, which is the arithmetic bug this module exists
  to stop anyone writing again in a renderer.

The rate parser's refusals get a test each, and each asserts the message names its source. The
same junk arrives from `--price` and from a `QUACKD_PRICE` that a `.env` three directories up
set, and which of the two you are looking at is the whole question when the run has just failed.
"""

from __future__ import annotations

import ast
import datetime as dt

import pytest

from quackd.agent.providers import catalogue as cat
from quackd.agent.providers.catalogue import PRICES_CHECKED, Price
from quackd.agent.providers.pricing import (
    FAKE,
    PRICE_ENV,
    SCRIPTED,
    SELF_HOSTED,
    TurnCost,
    cost_usd,
    fmt_usd,
    parse_price,
    price_for,
    resolve_price,
    turn_cost,
)
from tests.conftest import REPO

#: The rate the real `--price` run was priced at, spelled as a person types it on the CLI.
CARD = "in=3,out=15,cache_read=0.3,cache_write=3.75"


def _refusal(text: str, *, source: str = "--price") -> str:
    with pytest.raises(ValueError) as e:
        parse_price(text, source=source)
    return str(e.value)


# ── reading a rate off a rate card ──────────────────────────────────────────────────────


def test_a_full_rate_card_becomes_the_four_numbers_it_names() -> None:
    assert parse_price(CARD, source="--price") == Price(3.0, 15.0, 0.3, 3.75, source="--price")


def test_in_and_input_and_out_and_output_are_the_same_two_fields() -> None:
    """Vendors' pages spell it both ways and so do people, and neither spelling is worth a
    failed run when the meaning of both is unambiguous."""
    short = parse_price("in=3,out=15", source="--price")
    long = parse_price("input=3,output=15", source="--price")
    assert short == long == Price(3.0, 15.0, source="--price")


def test_the_cache_rates_are_optional_because_not_every_vendor_publishes_them() -> None:
    """xAI publish a cached-input rate and say nothing at all about cache creation, and Cohere
    and Qwen publish neither, so a card with two numbers on it is a complete card."""
    price = parse_price("in=2,out=6,cache_read=0.5", source="--price")
    assert (price.input, price.output, price.cache_read, price.cache_write) == (
        2.0,
        6.0,
        0.5,
        None,
    )


def test_a_rate_pasted_out_of_a_table_survives_its_whitespace() -> None:
    """Copied off a pricing page it arrives with spaces around the signs and often a trailing
    comma, and refusing that would teach nobody anything."""
    assert parse_price("  IN = 3 , Out = 15 ,, ", source=PRICE_ENV) == Price(
        3.0, 15.0, source=PRICE_ENV
    )


def test_whole_numbers_and_fractions_are_both_read_as_rates() -> None:
    """Rates run from $180 per million down to Command R7B's $0.0375, so nothing here may
    quietly be an int."""
    price = parse_price("in=0.0375,out=180", source="--price")
    assert price.input == 0.0375 and price.output == 180.0


# ── what the parser refuses, and what it says when it does ──────────────────────────────


def test_a_part_with_no_equals_sign_is_refused_and_names_its_source() -> None:
    """`in=3,15` is the shape of a half-remembered card, and taking the 15 as an output rate by
    position would price the run on a guess."""
    message = _refusal("in=3,15")
    assert "--price" in message and "'15' has no" in message and "=" in message


def test_a_field_the_rate_card_does_not_have_is_refused_and_names_its_source() -> None:
    message = _refusal("in=3,out=15,cache=1", source=PRICE_ENV)
    assert PRICE_ENV in message and "no field called 'cache'" in message


def test_a_field_given_twice_is_refused_rather_than_quietly_taking_one() -> None:
    """Which of the two wins is not something a reader should have to know, and either answer
    prices the run at a number the person did not mean."""
    message = _refusal("in=3,input=4,out=5")
    assert "--price" in message and "names input twice" in message


def test_a_rate_that_is_not_a_number_is_refused_and_names_its_source() -> None:
    message = _refusal("in=cheap,out=2", source=PRICE_ENV)
    assert PRICE_ENV in message and "cheap" in message and "not a number" in message


def test_a_negative_rate_is_refused_and_names_its_source() -> None:
    """Nobody is paid to make a call, and a negative rate would net off against real spend and
    hide it."""
    message = _refusal("in=-1,out=2")
    assert "--price" in message and "input cannot be -1" in message


def test_a_rate_of_nan_is_refused_along_with_the_negative_ones() -> None:
    """`float("nan")` parses, compares false against every bound, and would turn the whole
    run's cost into `nan` without raising anything."""
    message = _refusal("in=3,out=nan")
    assert "--price" in message and "output cannot be nan" in message


def test_a_price_with_no_input_rate_is_refused_and_names_its_source() -> None:
    message = _refusal("out=15")
    assert "--price" in message and "does not say in" in message


def test_a_price_with_no_output_rate_is_refused_and_names_its_source() -> None:
    """Every vendor charges for both, so a card with one of them is a card read in a hurry
    rather than a vendor that does not charge for the other half."""
    message = _refusal("in=3", source=PRICE_ENV)
    assert PRICE_ENV in message and "does not say out" in message


def test_the_same_junk_names_the_flag_or_the_variable_it_actually_arrived_in() -> None:
    """The point of the `source` argument: a reader who passed no `--price` must not be sent
    looking for one, and a reader who did must not be sent hunting through `.env` files."""
    assert "--price" in _refusal("nonsense")
    assert "--price" not in _refusal("nonsense", source=PRICE_ENV)
    assert PRICE_ENV in _refusal("nonsense", source=PRICE_ENV)


# ── which rate a run is priced at ───────────────────────────────────────────────────────


def test_the_scripted_pilot_is_free_rather_than_unpriced() -> None:
    """`--llm fake` calls nothing, so it costs nothing and should say `$0`. Answering
    `None` there would print `cost unpriced` for every demo and every test run."""
    price = price_for("fake", "gpt-5.6-sol")
    assert price == FAKE
    assert price is not None and (price.input, price.output) == (0.0, 0.0)
    assert price.source == "fake"


def test_the_vla_pilot_is_free_as_the_scripted_one_is() -> None:
    """`--controller vla` asks no model: a rule hands the policy its instructions and a person
    judges the arm, so it is priced as `--llm fake` is and prints `$0`. The pricing module may
    not import the pilot, which imports pydantic, so the name is spelled in both places and
    held together here."""
    from quackd.agent.providers import vla

    assert vla.NAME in SCRIPTED
    assert price_for(vla.NAME, "scripted:stack-blocks") == FAKE
    assert resolve_price(vla.NAME, "scripted:goal") == FAKE
    assert fmt_usd(cost_usd({"input_tokens": 0, "output_tokens": 0}, FAKE)) == "$0"


def test_every_local_preset_is_free_whatever_model_it_serves() -> None:
    """A local server serves whatever you pulled, so there is no id to look up: the rate follows
    from the provider alone, and the electricity is not billed per token."""
    for name in cat.LOCAL_NAMES:
        price = price_for(name, "some-model-nobody-catalogued:8b")
        assert price == SELF_HOSTED, name
        assert price is not None and price.source == "self-hosted"
        assert (price.input, price.output, price.cache_read, price.cache_write) == (
            0.0,
            0.0,
            0.0,
            0.0,
        )


def test_a_catalogued_model_returns_the_rate_the_catalogue_holds() -> None:
    spec = cat.find_model("anthropic", "claude-opus-5")
    assert spec is not None and spec.price is not None
    assert price_for("anthropic", "claude-opus-5") is spec.price


def test_an_id_the_vendor_does_not_list_has_no_rate() -> None:
    """A typo must not inherit some other model's rate, and it must not be free either."""
    assert price_for("openai", "gpt-nope") is None


def test_a_provider_quackd_has_never_heard_of_has_no_rate() -> None:
    assert price_for("hal9000", "whatever") is None


def test_an_explicit_override_beats_the_catalogue_and_says_where_it_came_from() -> None:
    """The rate goes into the run record, so a reader of `summary.json` months later can see
    that the figure came off a flag rather than off this table."""
    price = resolve_price("anthropic", "claude-opus-5", override="in=1,out=2")
    assert price == Price(1.0, 2.0, source="--price")


def test_an_override_beats_fake_so_the_whole_cost_path_runs_without_a_key() -> None:
    """Deliberate rather than an oversight: pricing a scripted run is how the arithmetic, the
    record and the verdict line get exercised end to end with no key and no bill. The real run
    that produced `cost $0.0309` was `--llm fake --price`."""
    price = resolve_price("fake", "anything", override=CARD)
    assert price is not None and price.source == "--price"
    assert price != FAKE and price.input == 3.0


def test_the_environment_variable_is_used_when_no_flag_was_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PRICE_ENV, "in=7,out=21")
    price = resolve_price("anthropic", "claude-opus-5")
    assert price == Price(7.0, 21.0, source=PRICE_ENV)


def test_an_empty_environment_variable_reads_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite sets `QUACKD_PRICE` empty rather than deleting it, because the CLI loads a
    developer's `.env` and `load_dotenv` does not overwrite a name already in the environment.
    Empty therefore has to mean unset here, or every test in the suite would be priced by it."""
    monkeypatch.setenv(PRICE_ENV, "")
    spec = cat.find_model("anthropic", "claude-opus-5")
    assert spec is not None
    assert resolve_price("anthropic", "claude-opus-5") is spec.price
    monkeypatch.setenv(PRICE_ENV, "   ")
    assert resolve_price("anthropic", "claude-opus-5") is spec.price


def test_the_flag_wins_over_the_variable_when_both_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order a person expects to be obeyed: what they just typed beats what a file set for
    them, and the record says which one it was."""
    monkeypatch.setenv(PRICE_ENV, "in=7,out=21")
    price = resolve_price("anthropic", "claude-opus-5", override="in=1,out=2")
    assert price == Price(1.0, 2.0, source="--price")


def test_an_uncatalogued_model_with_no_override_stays_unpriced() -> None:
    """The whole reason `--price` exists: a model quackd has never heard of reports nothing
    rather than reporting nothing owed."""
    assert resolve_price("cohere", "command-a-plus-05-2026") is None


# ── the arithmetic ──────────────────────────────────────────────────────────────────────


def test_the_four_buckets_are_each_charged_at_their_own_rate() -> None:
    """700 tokens of fresh prompt at $3, 200 read from cache at $0.30, 100 written to it at
    $3.75 and 500 generated at $15, per million."""
    usage = {
        "input_tokens": 1000,
        "cache_read_tokens": 200,
        "cache_write_tokens": 100,
        "output_tokens": 500,
    }
    assert cost_usd(usage, parse_price(CARD, source="--price")) == 0.010035


def test_an_unpublished_cache_rate_is_charged_at_the_full_input_rate() -> None:
    """The deliberate overstatement. Charging an unknown cache rate at zero would quietly
    discount a run by whatever fraction of its prompt was cached, and a reader would have no way
    to see that it had happened. Charged at the full rate the figure is too high, which a reader
    can discount on purpose, and it is never lower than the bill that actually arrives.

    Here every token of the prompt lands at the input rate, cached or not, so the whole thousand
    costs exactly what an uncached thousand would."""
    usage = {"input_tokens": 1000, "cache_read_tokens": 600, "cache_write_tokens": 200}
    no_cache_rates = Price(2.5, 10.0)
    assert cost_usd(usage, no_cache_rates) == 0.0025 == 1000 * 2.5 / 1_000_000
    # and with the read rate published, the same usage costs a quarter of that
    assert cost_usd(usage, Price(2.5, 10.0, 0.25)) == 0.00115


def test_a_cached_prompt_is_charged_once_and_not_again_at_the_full_rate() -> None:
    """`input_tokens` is the WHOLE prompt with the cached slice inside it, so the slice comes
    out of the full-rate remainder before the cache rate applies. Adding the two instead would
    bill a fully cached Fable 5.1 prompt at eleven times what it costs."""
    usage = {"input_tokens": 1000, "cache_read_tokens": 1000}
    fable = Price(10.0, 50.0, 0.25, 12.5)
    assert cost_usd(usage, fable) == 0.00025
    assert cost_usd(usage, fable) != (1000 * 10.0 + 1000 * 0.25) / 1_000_000


def test_a_usage_whose_parts_exceed_its_whole_is_floored_rather_than_going_negative() -> None:
    """A vendor that reported its buckets disjoint where quackd expects them nested would drive
    the remainder below zero, and a negative line item would net off against the rest of the run
    and hide spend. The remainder floors at zero and the parts are still charged."""
    usage = {"input_tokens": 100, "cache_read_tokens": 200, "cache_write_tokens": 300}
    cost = cost_usd(usage, parse_price(CARD, source="--price"))
    assert cost == 0.001185
    assert cost >= 0


def test_missing_and_negative_token_counts_are_read_as_zero() -> None:
    """The mapping comes off a transcript as often as off a live turn, and an older line has
    none of the cache keys at all."""
    price = parse_price(CARD, source="--price")
    assert cost_usd({}, price) == 0.0
    assert cost_usd({"input_tokens": None, "output_tokens": None}, price) == 0.0
    assert cost_usd({"input_tokens": -50, "output_tokens": -50}, price) == 0.0
    # an older transcript line: the two token counts and nothing else
    assert cost_usd({"input_tokens": 1000, "output_tokens": 0}, price) == 0.003


def test_the_run_that_printed_nine_cents_still_prices_at_nine_cents() -> None:
    """The real run kept as a fixture: `run find-and-kick --llm fake --seed 3 --price
    'in=3,out=15,cache_read=0.3,cache_write=3.75'` spent 9812 prompt tokens and 96 generated
    ones, recorded `cost_usd` 0.030876, and printed `cost $0.0309` on its verdict line. Both
    halves are here so a change to either the arithmetic or the formatting has to explain
    itself against a figure somebody actually saw."""
    usage = {"input_tokens": 9812, "output_tokens": 96}
    cost = cost_usd(usage, parse_price(CARD, source="--price"))
    assert cost == 0.030876
    assert fmt_usd(cost) == "$0.0309"


@pytest.mark.parametrize("text", ["in=inf,out=1", "in=1,out=infinity", "in=1e400,out=1"])
def test_an_infinite_rate_is_refused_rather_than_making_every_cost_nan(text: str) -> None:
    """`float("inf")` parses, is not NaN, and satisfies `>= 0`, so the original guard let it
    through. What came out the other side was worse than a refusal: every cost became NaN, the
    panel printed an infinite rate as `<$0.000001`, and `summary.json` stopped being valid
    JSON, because NaN and Infinity are not values JSON has."""
    with pytest.raises(ValueError, match="cannot be"):
        parse_price(text, source="--price")


def test_a_cost_keeps_more_places_than_it_prints_so_a_sum_of_them_is_not_biased() -> None:
    """The precision a figure is SHOWN at and the precision it is ACCUMULATED at are different
    questions, and answering them with one number put a systematic error on every run.

    A stepper question costs about $0.000022. Rounded to the six places `fmt_usd` prints, each
    one gains a fraction of a percent in the same direction, and a run of them carries that
    bias multiplied by its number of turns rather than averaging it out. It was measured at
    just over two percent on a real shadow run, against the one ratio the stepper exists to
    demonstrate. So the arithmetic keeps nine places and the reader is given six."""
    price = Price(0.042, 0.0, source="published")
    one = cost_usd({"input_tokens": 527}, price)
    assert one == 0.000022134, "the real figure, not the printed one"
    assert fmt_usd(one) == "$0.000022", "and six places is still what a person is shown"

    turns = 40
    assert cost_usd({"input_tokens": 527 * turns}, price) == pytest.approx(one * turns, rel=1e-9)
    assert round(0.000022, 6) * turns < one * turns, (
        "the six-place figure undercounts, which is the direction the bias ran in"
    )


def test_a_real_cost_below_the_printing_floor_is_not_recorded_as_zero() -> None:
    """`fmt_usd` has a `<$0.000001` branch for a bill too small to print, and while the
    arithmetic rounded to six places that branch was unreachable: anything smaller had already
    become exactly 0.0 and read as free. One token of a cheap model is a real charge."""
    dust = cost_usd({"input_tokens": 1}, Price(0.042, 0.0, source="published"))
    assert dust > 0
    assert fmt_usd(dust) == "<$0.000001"


# ── printing a figure at the precision it deserves ──────────────────────────────────────


def test_no_price_reads_as_unpriced_rather_than_as_zero() -> None:
    """The distinction the whole module turns on: quackd does not know what this cost, which is
    not the same claim as it having been free."""
    assert fmt_usd(None) == "unpriced"


def test_a_run_that_genuinely_cost_nothing_is_a_plain_dollar_zero() -> None:
    """`--llm fake` and a local model, which are free by what they are. Not `$0.00`: there
    is no column to line up with, this sits beside `steps 7` on one counter line."""
    assert fmt_usd(0) == "$0"
    assert fmt_usd(0.0) == "$0"
    assert fmt_usd(-1.0) == "$0"


def test_a_stepper_sized_amount_keeps_the_digits_that_make_it_visible() -> None:
    """One Jev question is $0.000022, and four decimal places would print `$0.0000`, which
    reads as free."""
    assert fmt_usd(0.000022) == "$0.000022"
    assert fmt_usd(0.000001) == "$0.000001"


def test_a_cent_sized_amount_gets_four_places_and_a_dollar_sized_one_gets_two() -> None:
    assert fmt_usd(0.0312) == "$0.0312"
    assert fmt_usd(0.0001) == "$0.0001"
    assert fmt_usd(2.5) == "$2.50"
    assert fmt_usd(1) == "$1.00"


def test_an_amount_too_small_to_write_says_so_rather_than_rounding_to_free() -> None:
    """A single cheap call can land below a millionth of a dollar, and `$0.000000` would be a
    sixth zero that means something different from the other five."""
    assert fmt_usd(0.0000005) == "<$0.000001"
    assert fmt_usd(1e-12) == "<$0.000001"


# ── the catalogue's rates, read as data ─────────────────────────────────────────────────


def _catalogued_prices() -> list[tuple[str, Price]]:
    return [
        (m.id, m.price)
        for name in cat.CLOUD_NAMES
        for m in cat.models_for(name)
        if m.price is not None
    ]


def test_every_published_rate_is_a_non_negative_number() -> None:
    """A typed minus sign in a table of 115 rows is invisible on review and would net off
    against the rest of a run."""
    for model_id, price in _catalogued_prices():
        for field in ("input", "output", "cache_read", "cache_write"):
            rate = getattr(price, field)
            if rate is not None:
                assert isinstance(rate, float), f"{model_id}.{field} is not a float"
                assert rate >= 0, f"{model_id}.{field} is {rate}"


def test_a_cache_read_is_never_dearer_than_the_full_input_rate() -> None:
    """Reading a cache is the discount every vendor sells it as, so a read rate above its own
    input rate is a transcription error rather than a pricing model: it is the shape of two
    columns copied out of a table in the wrong order."""
    for model_id, price in _catalogued_prices():
        if price.cache_read is not None:
            assert price.cache_read <= price.input, (
                f"{model_id} charges more to read its cache than to send a fresh prompt"
            )


def test_every_catalogued_model_carries_a_source_of_catalogue() -> None:
    """`source` is what a reader of an old `summary.json` uses to tell a rate off this table
    from one somebody passed on the day."""
    for model_id, price in _catalogued_prices():
        assert price.source == "catalogue", f"{model_id} claims its rate came from somewhere else"


def test_the_rate_record_carries_the_checked_date_only_for_a_catalogued_rate() -> None:
    """The date is a claim about this table and nothing else. Stamping it onto a `--price` the
    user typed, or onto the scripted pilot's zero, would tell a reader that quackd had verified
    a number it was simply handed."""
    catalogued = Price(5.0, 25.0, 0.5, 6.25).record()
    assert catalogued["checked"] == PRICES_CHECKED
    assert catalogued["unit"] == "USD per million tokens"
    assert catalogued["source"] == "catalogue"
    assert (catalogued["input"], catalogued["output"]) == (5.0, 25.0)
    assert (catalogued["cache_read"], catalogued["cache_write"]) == (0.5, 6.25)
    for other in (FAKE, SELF_HOSTED, parse_price(CARD, source="--price")):
        assert other.record()["checked"] is None, other.source


def test_the_date_the_rates_were_checked_is_a_real_date() -> None:
    """It is written into every `run_start`, and a reader deciding whether to trust a dollar
    figure needs to be able to work out how old the rate behind it is."""
    checked = dt.date.fromisoformat(PRICES_CHECKED)
    assert checked.year >= 2025


def test_four_cohere_models_are_the_only_unpriced_ones() -> None:
    """The case `None` was built for, rather than four rows somebody forgot.

    Cohere sell Command A, A+, A Reasoning and North Mini Code as dedicated instances by the
    hour and publish no per-token rate for any of them, which cannot honestly be turned into
    one. The pricing page shows A+ and North as "Free", $0 against "API key" with no key type
    named, and its own FAQ bills production-key calls pay as you go at a rate it does not
    state. Everything else in the catalogue is priced off its vendor's own page, so
    this list is the exception and stating it here is what stops a future edit quietly filling
    the gap with a zero."""
    unpriced = {m.id for name in cat.CLOUD_NAMES for m in cat.models_for(name) if m.price is None}
    assert unpriced == {
        "command-a-plus-05-2026",
        "command-a-03-2025",
        "command-a-reasoning-08-2025",
        "north-mini-code-1-0",
    }


def test_coheres_default_model_is_one_of_the_unpriced_ones() -> None:
    """Which is why no test anywhere may assert that every vendor default has a price: `quackd
    run --llm cohere` with no model half reports `cost_usd: null` and says `cost unpriced`,
    and that is the correct answer rather than a gap to be closed."""
    default = cat.default_model_for("cohere")
    assert default == "command-a-plus-05-2026"
    assert price_for("cohere", default) is None
    spec = cat.find_model("cohere", default)
    assert spec is not None and spec.price is None


def test_a_model_that_is_genuinely_free_is_priced_at_zero_rather_than_left_unpriced() -> None:
    """The other half of the same distinction. Several GLM Flash models and Mistral's Leanstral
    really are free, and they carry a `Price(0, 0)` so a run on one prints `$0` instead of
    refusing to answer."""
    for provider, model_id in (("glm", "glm-4.7-flash"), ("mistral", "labs-leanstral-1-5")):
        price = price_for(provider, model_id)
        assert price is not None, f"{model_id} is free, which is not the same as unpriced"
        assert (price.input, price.output) == (0.0, 0.0)
        assert fmt_usd(cost_usd({"input_tokens": 100_000, "output_tokens": 5000}, price)) == "$0"


def test_pricing_costs_nothing_to_import() -> None:
    """`quackd --help` imports the catalogue and every press of TAB does too, and this module
    sits beside it and is imported by the CLI on the way to building a run directory. Read as
    source rather than by importing, because by the time this test runs the suite has imported
    half of quackd already and an accidental dependency would be satisfied from the cache.

    Stricter than its sibling in `test_catalogue.py`: every import root is checked against the
    stdlib names this module is allowed, so a new heavy dependency fails here whether or not
    anybody thought to add its name to a list."""
    source = (REPO / "quackd" / "agent" / "providers" / "pricing.py").read_text(encoding="utf-8")
    for forbidden in ("import pydantic", "from pydantic", "import openai", "import anthropic"):
        assert forbidden not in source
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= {"__future__", "collections", "math", "os", "quackd", "typing"}, (
        f"pricing.py has picked up {sorted(roots)}, and --help pays for every one of them"
    )


# ── a vendor that says what it billed, and a rate read on a day of its own ─────────────


def test_a_rate_that_carries_its_own_check_date_records_that_date() -> None:
    """OpenRouter's rows were read on a day of their own, and a rate read off its list when a
    run starts was read that day. Neither may be stamped with the day the rest were read."""
    assert Price(2.0, 10.0, checked="2026-10-06").record()["checked"] == "2026-10-06"
    listed = Price(0.1, 0.47, source="openrouter", checked="2026-10-07")
    assert listed.record()["checked"] == "2026-10-07"
    assert Price(0.1, 0.47, source="openrouter").record()["checked"] is None, (
        "a rate from the list with no date is not vouched for by the catalogue's"
    )


def test_every_openrouter_row_was_read_off_openrouters_list_on_its_own_date() -> None:
    for name in cat.CLOUD_NAMES:
        for m in cat.models_for(name):
            if m.price is None:
                continue
            if name == "openrouter":
                assert m.price.checked == cat.OPENROUTER_PRICES_CHECKED, m.id
            else:
                assert m.price.checked is None, f"{m.id} carries a date of its own"
    assert dt.date.fromisoformat(cat.OPENROUTER_PRICES_CHECKED) >= dt.date.fromisoformat(
        PRICES_CHECKED
    )


def test_a_rate_the_person_gave_beats_the_bill_and_the_bill_beats_the_rate_card() -> None:
    """`--price` and `QUACKD_PRICE` are a person saying what this costs them, a negotiated
    rate or a key quackd cannot see; the bill is what the vendor charged; the rate card is
    quackd's own arithmetic, and the last resort."""
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}
    card = Price(2.0, 10.0)
    assert turn_cost(usage, card, billed=0.25) == TurnCost(0.25, True)
    assert turn_cost(usage, card) == TurnCost(2.0, False)
    for source in ("--price", PRICE_ENV):
        named = parse_price("in=3,out=15", source=source)
        assert turn_cost(usage, named, billed=0.25) == TurnCost(3.0, False), source
    assert turn_cost(usage, None, billed=0.25) == TurnCost(0.25, True), "a bill needs no rate"
    assert turn_cost(usage, None) == TurnCost(None, False), "no rate and no bill is unpriced"
    assert turn_cost(usage, card, billed=0.0) == TurnCost(0.0, True), "a free call is $0, billed"


def test_a_listed_rate_is_used_only_where_the_catalogue_has_none() -> None:
    """The rate OpenRouter's list gives an id the catalogue does not carry. It must not replace a
    rate the catalogue holds, and a rate a person named beats it as it beats the catalogue."""
    listed = Price(0.15, 0.47, source="openrouter", checked="2026-10-06")
    carried = resolve_price("openrouter", "openai/gpt-6-sol", listed=listed)
    assert carried is not None and carried.source == "catalogue" and carried.input == 2.0
    assert resolve_price("openrouter", "qwen/qwen3.8-flash", listed=listed) is listed
    named = resolve_price("openrouter", "qwen/qwen3.8-flash", override="in=1,out=2", listed=listed)
    assert named is not None and named.source == "--price"
    assert resolve_price("openrouter", "qwen/qwen3.8-flash") is None
