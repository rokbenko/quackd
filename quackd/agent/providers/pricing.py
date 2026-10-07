"""What a call cost, in dollars, from the tokens it spent and the rate it spent them at.

quackd counted tokens from its first release and never turned them into money, which left
every reader of a transcript doing the same arithmetic by hand against a rate card they had to
go and find. This module is that arithmetic, in one place, with three rules it does not bend:

- **A rate quackd does not know is `None`, never zero.** An unpriced model records
  `cost_usd: null` and the verdict panel says `cost unpriced`. Printing `$0.00` for a frontier
  model is the one failure mode here that costs somebody real money.
- **The rate used is written into the run.** `run_start` and `summary.json` carry it, so
  `quackd log` prices a run at what it cost on the day rather than at whatever the catalogue
  says months later, and a rate that turns out to have been wrong is visible rather than
  silently reapplied to every old run.
- **Where a rate is missing but tokens are not, the estimate goes UP.** An unpublished cache
  rate is charged at the full input rate. A bill that is too low is the one that gets believed.

The catalogue holds the rates (`catalogue.Price`, read off each vendor's own page on
`catalogue.PRICES_CHECKED`); `--price` and `QUACKD_PRICE` override them for a negotiated rate,
a model the catalogue has never heard of, or a paid endpoint behind a local preset.

One vendor also says what each call was billed: OpenRouter's `usage.cost`. Where it does, that
is the turn's cost rather than rate times tokens, because it already knows which endpoint served
the call, at which tier, and what the cache saved (`turn_cost`). A rate a person named still
wins over it: they asked to be obeyed, and they may know something quackd cannot see.

Nothing here may import a vendor SDK or pydantic: `catalogue.py` is imported by `--help` and by
every press of TAB, and this sits beside it.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from typing import Any, NamedTuple

from quackd.agent.providers.catalogue import LOCAL_NAMES, Price, find_model

PRICE_ENV = "QUACKD_PRICE"
"""Override the model's rate for one run. `--price` beats it; both beat the catalogue."""

OVERRIDES = frozenset({"--price", PRICE_ENV})
"""The sources a person names a rate by, as `parse_price` stamps them. A run priced by one of
them is costed at that rate even where the vendor says what it billed."""

FAKE = Price(0.0, 0.0, 0.0, 0.0, source="fake")
"""The scripted pilot calls nothing, so it costs nothing. Not `None`: a run that genuinely cost
nothing should say `$0` rather than refuse to answer."""

SCRIPTED = ("fake", "vla")
"""The pilots that are rules rather than models, priced at `FAKE`: `--llm fake`, and the pilot
`--controller vla` flies (`providers.vla.NAME`, spelled here because that module imports pydantic
and this one may not; a test holds the two together)."""

SELF_HOSTED = Price(0.0, 0.0, 0.0, 0.0, source="self-hosted")
"""A model on your own machine bills you in electricity, not in tokens. A paid OpenAI-compatible
endpoint reached through `--llm local --base-url` is the exception, and `--price` or
`QUACKD_PRICE` is how you say so."""

_ALIASES = {"in": "input", "input": "input", "out": "output", "output": "output"}
_CACHE_KEYS = frozenset({"cache_read", "cache_write"})


def parse_price(text: str, *, source: str) -> Price:
    """`in=3,out=15,cache_read=0.3,cache_write=3.75`, in USD per million tokens.

    `in`/`input` and `out`/`output` are required and the two cache rates are optional, which is
    the shape of the rate card you are reading it off. The error names `source` rather than the
    value alone, because the same text arrives from a flag and from a variable a `.env` three
    directories up set, and which of those you are looking at is the whole question.
    """
    values: dict[str, float] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        key, sep, raw = part.partition("=")
        key = key.strip().lower()
        if not sep:
            raise ValueError(
                f"{source} {text!r} is not a price: {part!r} has no `=`. Write it as "
                "in=3,out=15[,cache_read=0.3,cache_write=3.75], in USD per million tokens."
            )
        field = _ALIASES.get(key) or (key if key in _CACHE_KEYS else None)
        if field is None:
            raise ValueError(
                f"{source} {text!r} has no field called {key!r} "
                "(known: in, out, cache_read, cache_write)"
            )
        if field in values:
            raise ValueError(f"{source} {text!r} names {field} twice")
        try:
            number = float(raw.strip())
        except ValueError:
            raise ValueError(f"{source} {text!r}: {raw.strip()!r} is not a number") from None
        if not math.isfinite(number) or number < 0:
            # `not number >= 0` caught NaN and let `inf` through, and an infinite rate is
            # worse than a refused one: every cost becomes NaN, the panel reads
            # `cost <$0.000001` for it, and `summary.json` stops being valid JSON.
            raise ValueError(f"{source} {text!r}: {field} cannot be {raw.strip()}")
        values[field] = number
    missing = [k for k in ("input", "output") if k not in values]
    if missing:
        names = " and ".join("in" if m == "input" else "out" for m in missing)
        raise ValueError(
            f"{source} {text!r} does not say {names}. An input rate and an output rate are "
            "both required, because every vendor charges for both."
        )
    return Price(
        input=values["input"],
        output=values["output"],
        cache_read=values.get("cache_read"),
        cache_write=values.get("cache_write"),
        source=source,
    )


def price_for(provider: str, model: str) -> Price | None:
    """The catalogue's rate for one model, or None where quackd has never had one.

    The scripted pilots and the local presets are free by what they are rather than by any
    table, so they answer before the catalogue is consulted and answer for whatever `--model`
    they were given.
    """
    if provider in SCRIPTED:
        return FAKE
    if provider in LOCAL_NAMES:
        return SELF_HOSTED
    spec = find_model(provider, model)
    return spec.price if spec is not None else None


def resolve_price(
    provider: str,
    model: str,
    *,
    override: str | None = None,
    listed: Price | None = None,
) -> Price | None:
    """The rate this run is priced at, in the order a person expects to be obeyed.

    `--price`, then `QUACKD_PRICE`, then the provider and the catalogue, then `listed`: the rate
    the provider read off the vendor's own list for a model the catalogue does not carry
    (`--llm openrouter:` an unlisted id). An override beats `fake` as well as the catalogue,
    and that is deliberate rather than an oversight: pricing a scripted run is how the whole
    cost path gets exercised end to end with no key and no bill.
    """
    text = override if override is not None else os.environ.get(PRICE_ENV)
    if text is not None and text.strip():
        return parse_price(text, source="--price" if override is not None else PRICE_ENV)
    priced = price_for(provider, model)
    return priced if priced is not None else listed


class TurnCost(NamedTuple):
    """What one call cost, and whether that is the vendor's own bill or quackd's arithmetic."""

    usd: float | None
    billed: bool


def turn_cost(
    usage: Mapping[str, Any], price: Price | None, billed: float | None = None
) -> TurnCost:
    """One call's cost: a person's rate, else the vendor's bill, else rate times tokens.

    `billed` is what the vendor says the call cost (`ProviderTurn.billed_usd`), None from every
    vendor that only reports tokens. It wins over the catalogue's rate because it already
    accounts for what a rate card cannot: which endpoint served the call, its tier, the cache.
    It loses to `--price` and `QUACKD_PRICE`, which a person set on purpose. None in, None out:
    no rate and no bill is an unpriced call, never a free one.
    """
    if price is not None and price.source in OVERRIDES:
        return TurnCost(cost_usd(usage, price), False)
    if billed is not None:
        return TurnCost(round(billed, 9), True)
    return TurnCost(cost_usd(usage, price) if price is not None else None, False)


def cost_usd(usage: Mapping[str, Any], price: Price) -> float:
    """What one call cost, from a `Usage` as the record carries it.

    Takes the mapping rather than the model, because the same arithmetic has to work on
    `turn.usage.model_dump()` during a run and on a line read back out of a transcript.

    `input_tokens` is the whole prompt (`providers.base.Usage`), so the cached parts come out
    of it before the full rate applies and are charged at their own. A vendor that reported the
    parts larger than the whole would otherwise drive this negative, so the remainder is floored
    at zero rather than trusted.
    """
    read = max(int(usage.get("cache_read_tokens") or 0), 0)
    write = max(int(usage.get("cache_write_tokens") or 0), 0)
    prompt = max(int(usage.get("input_tokens") or 0), 0)
    full = max(prompt - read - write, 0)
    out = max(int(usage.get("output_tokens") or 0), 0)
    # an unpublished cache rate is charged at the full input rate: an overstatement a reader can
    # discount, rather than a discount they cannot see
    read_rate = price.cache_read if price.cache_read is not None else price.input
    write_rate = price.cache_write if price.cache_write is not None else price.input
    dollars = (
        full * price.input + read * read_rate + write * write_rate + out * price.output
    ) / 1_000_000
    # Nine places, not six. Six is a tenth of a cent, which is the right precision to SHOW and
    # the wrong one to accumulate: a stepper question costs about $0.000022, so rounding each
    # one before anything sums them put a fixed bias of a couple of percent on the run total,
    # and it grew with the number of turns rather than averaging out. The one ratio the
    # stepper exists to demonstrate is the one it corrupted. Rounding for a reader happens
    # once, at the end, in `fmt_usd` and in the summary.
    return round(dollars, 9)


def fmt_usd(amount: float | None) -> str:
    """A dollar figure at the precision that figure deserves.

    A run of a frontier model is `$0.1502` and one decision-LLM question at Jev's published rate
    is `$0.000022`, and rounding the second to four places would print `$0.0000`, which reads as
    free. Nothing here is padded to a fixed width: these go in a counter line beside `steps 7`,
    not in a column.
    """
    if amount is None:
        return "unpriced"
    if amount <= 0:
        return "$0"
    if amount >= 1:
        return f"${amount:.2f}"
    if amount >= 0.0001:
        return f"${amount:.4f}"
    if amount >= 0.000001:
        return f"${amount:.6f}"
    return "<$0.000001"
