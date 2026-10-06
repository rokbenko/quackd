"""The models quackd will let you pick, one curated list per cloud vendor.

The model half of `--llm` -- the `gpt-5.6-sol` in `--llm openai:gpt-5.6-sol`, back when it was a
`--model` flag of its own -- used to take any string and hand it straight to the vendor, so a
typo, a model retired last spring and a model belonging to a different vendor all failed the same
way: at the first call, in the vendor's words, after the run had already started. Three of the
four defaults quackd shipped were wrong by the time anyone checked them, which is what a field
nobody validates does.

So the list lives here instead, and a user picks from it: on the CLI, in shell completion, in the
browser demo, and in `quackd list-models`. When a vendor ships a model it is added here and
nowhere else. That is the cost of the promise. The list is only as current as its last edit, and
`quackd list-models` is how anyone sees what this build knows.

What earns a place, checked 2026-09-12 against each vendor's own documentation and again, every
vendor but OpenRouter, on 2026-09-23 (OpenRouter's rows were read off its own model list on
2026-10-06):

- callable that day on the vendor's public API by anyone holding a key,
- a text model that can call function tools, because every verb quackd has is a function tool and
  a model without them is not a degraded pilot, it is no pilot,
- not deprecated, where an announced shutdown date is enough to keep it out,
- not behind an approval programme, because that only buys the reader a 403.

Image, audio, video, embedding and OCR models are not here. Open-weight models are, when the
vendor serves them on its own API rather than only publishing the weights, and so are models a
vendor hosts but did not train, because what matters is that the vendor answers for them.

OpenRouter is the one vendor whose list here is not the whole of what it takes (`OPEN_ENDED`). It
is a router: its ids name other vendors' models (`anthropic/claude-opus-5.5`), hundreds of them,
and the list changes by the week. Its tuple below is a short selection that gives `--llm
openrouter` a default, shell completion and the browser something to offer, and any other id is
checked against OpenRouter's own public list when a run starts rather than refused here (ADR-0050).

The local presets are deliberately absent. They serve whatever you pulled, so the model half of
`--llm` stays free text there (`--llm ollama:llama3:8b`) and quackd asks the server what it has
when none is named (ADR-0014).

Nothing here may import a vendor SDK, pydantic, or anything else heavy: `quackd --help` imports
this module, and so does every press of TAB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, get_args

Status = Literal["current", "legacy", "preview", "specialised", "open"]

#: Every status there is, in the order a list of models should be shown in. `list-models` prints
#: them in this order and the browser demo groups its dropdown by it.
STATUSES: tuple[Status, ...] = get_args(Status)


#: When the rates below were last read off the vendor's own pricing page. A price is data and
#: data rots exactly the way the model ids above did, so the date travels with the numbers: it is
#: written into every `run_start`, and a reader deciding whether to trust a dollar figure can see
#: how old the rate that produced it is.
PRICES_CHECKED = "2026-09-23"

#: When OpenRouter's rows below were read off OpenRouter's own model list. A date of its own,
#: because stamping them with `PRICES_CHECKED` would date them to a day nobody had read them.
OPENROUTER_PRICES_CHECKED = "2026-10-06"

#: Vendors whose tuple is a selection rather than everything `--llm` takes after the colon. An id
#: not listed here is neither refused nor waved through: `factory.resolve_model` checks its shape
#: before a key is read, and the provider checks it against the vendor's own list before the first
#: paid call (`openrouter.admit`).
OPEN_ENDED: tuple[str, ...] = ("openrouter",)


@dataclass(frozen=True, slots=True)
class Price:
    """What a vendor charges for one model, in USD per million tokens.

    Four numbers, because that is the shape of every rate card: a full input rate, an output
    rate, and where there is a prompt cache, a cheaper rate for reading one and sometimes a
    dearer one for writing it.

    `None` means the vendor does not publish that rate, NOT that it is free. `pricing.cost_usd`
    charges an unpublished cache rate at the full input rate, which overstates the bill rather
    than understating it: a cost that is too low is the one that gets believed.
    """

    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None
    source: str = "catalogue"
    """Where this rate came from, for the record: `catalogue`, `fake`, `self-hosted`,
    `published`, `openrouter` (read off OpenRouter's list when the run started), or the name of
    the flag or variable that overrode it."""
    checked: str | None = None
    """The day this rate was read, where that is not `PRICES_CHECKED`: OpenRouter's rows were
    read on a day of their own, and a rate taken from its list at run time on the day of the
    run. None means the catalogue's own date applies, or no date does."""

    def record(self) -> dict[str, Any]:
        """The rate as `run_start` and `summary.json` carry it.

        Written into the run rather than looked up at replay, so `quackd log` prices a run at
        what it cost on the day rather than at whatever this table says months later."""
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "unit": "USD per million tokens",
            "source": self.source,
            # A date for the rates quackd read off a page, and none for a rate somebody
            # handed it: they know where theirs came from, and stamping it with quackd's
            # check date would be quackd vouching for a number it has never seen. The
            # stepper's published rate is re-read whenever this table is, so it shares
            # the date. A rate read on a day of its own says that day instead.
            "checked": self.checked
            or (PRICES_CHECKED if self.source in ("catalogue", "published") else None),
        }


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One model a user may pick, as the vendor spells it."""

    id: str
    """The API id, character for character. This is what goes on the wire."""

    label: str
    """What a human reads in a dropdown or a table."""

    status: Status = "current"
    """`current` is the vendor's headline lineup, `legacy` still served with no end announced,
    `preview` the vendor's own label, `specialised` tuned for one thing (code, a deep reasoning
    tier, vision, multi-agent, a pricing tier), `open` an open-weight model the vendor serves."""

    vision: bool = True
    """Does the vendor document image input? Where it does not, quackd keeps the camera frame to
    itself and sends the detections as text, the way it already does for local models. `--vision`
    and `--no-vision` override this in both directions."""

    api: Literal["chat", "responses"] | None = None
    """OpenAI only, and only when the model needs saying. Some models will not take function tools
    on Chat Completions at all and name `/v1/responses` in the 400. `openai.py` can read that 400
    and move, but it pays a failed call to learn it, and a model that is Responses only answers
    with a different error that the 400 reader does not match. Naming the API here costs nothing
    and skips both problems. `None` means start on Chat Completions, as everything else does."""

    forced_tools: bool = True
    """Anthropic, and OpenRouter's Claude rows: does the model accept a forced tool call,
    `tool_choice` `any` (`required` on OpenRouter)?

    Claude Opus 5.5 and Claude Fable 5.1 answer one with a 400 (`tool_choice: type "tool" and
    "any" are not supported for this model.`), so a row marked `False` is asked with `auto`
    instead, still one call per turn. `anthropic.py` reads the 400 as well and moves a run for
    a model this table does not mark, but it pays a failed call to learn it, the same bargain
    as `api` above. A turn that answers in prose rather than with a call is the loop's to
    handle: it re-prompts once and then ends the run, which `auto` makes reachable and a forced
    call never did.

    Through OpenRouter the same models refuse the same thing in a different place: its Opus 5.5
    migration guide says a forced `tool_choice` "fails at routing with no compatible endpoint
    rather than reaching the provider", and Anthropic lists Sonnet 5.5 beside Opus 5.5 and Fable
    5.1 as refusing forced tool use. `openrouter.py` has no 400 reader for it, because nobody
    here has seen that refusal's wording, so the row is the only thing that moves such a model
    to `auto`."""

    effort: bool = True
    """Anthropic only: does the model take `output_config.effort`?

    Claude Haiku 4.5 and Claude Sonnet 4.5 are missing from the list of supported models on
    Anthropic's effort page, so a row marked `False` is sent no effort at all. There is no 400
    reader behind this one, because that page does not print what the API answers, and a
    reader written against a guessed sentence is exactly how the thinking retry in
    `anthropic.py` came to miss the sentence the 4.5 models really send."""

    binds_thinking: bool = False
    """Anthropic only: does the model refuse a replayed thinking block once anything before it
    has changed?

    Claude Opus 5.5 and Claude Fable 5.1 bind each thinking block to the system prompt, the
    tools and every message that preceded it, and for accounts created on or after 2026-08-31
    a block whose earlier history changed is a 400 naming the block. quackd's loop drops the
    camera frames from all but the last two exchanges to keep the prompt small, which edits an
    earlier message on every call from the third. For a row marked `True` the provider asks the
    API to drop such a block rather than refuse the request (`block_binding` with
    `drop_block`, behind the `thinking-binding-controls-2026-08-01` beta), and the loop trims in
    steps of eight exchanges instead of on every call and, from each trim on, leaves out the
    blocks it invalidated, so the model keeps the reasoning it produced since the last trim.
    Keeping every frame instead would have no ceiling: a long run on a two-camera body passes
    the API's 32 MB request limit."""

    price: Price | None = None
    """What the vendor charges for it, read off the vendor's own page on `PRICES_CHECKED`.

    `None` where no public per-token rate exists, and a run on such a model records
    `cost_usd: null` rather than a zero: a model quackd cannot price is not a free one, and
    printing `$0.00` for a frontier model would be the most expensive kind of wrong."""


# ── the catalogue ───────────────────────────────────────────────────────────────────────
#
# Insertion order is display order, and THE FIRST ENTRY OF EACH VENDOR IS ITS DEFAULT: the model
# a bare `--llm openai` means, with no `:id` after the vendor. Writing the default as a separate
# field would spell the same id twice and let the two drift.
#
# `price=` is USD per million tokens, read off the vendor's own pricing page on `PRICES_CHECKED`
# and typed from that page rather than from anybody's recollection. Four rules hold across every
# vendor below, and where one of them bites a particular vendor its own comment says so:
#
# - **The standard on-demand rate.** Batch, priority, provisioned and free tiers are ignored:
#   each is a choice the caller makes and quackd cannot see which was made. Off-peak discounts
#   are ignored too, although they are the clock's rather than the caller's; DeepSeek's comment
#   says when its half price applies.
# - **The SHORT context band.** Several vendors charge more above a threshold (200k on xAI and
#   Gemini 3.1 Pro, 272k on ten of OpenAI's) and reprice the WHOLE request when it is crossed
#   rather than just the excess. The multiplier is not one number: xAI double both sides,
#   while Gemini and OpenAI double the input and cache rates and charge 1.5x the output. A
#   long-prompt run is therefore under-costed here, and the vendor comments name the thresholds
#   so a reader knows when to distrust the figure.
# - **The five minute cache-write TTL**, where a vendor sells two. quackd sets no cache itself,
#   and on Anthropic, which caches only what a request marks, that is the whole story. On most
#   of the others it is not. By their own pages OpenAI, Gemini, xAI, DeepSeek, Kimi, Z.ai and
#   Meta cache a repeated prompt without being asked, and so does Alibaba for the models its
#   implicit cache covers, so a run on one of them pays the cached-input rate on ordinary turns.
#   Two of them write a cache unasked and bill the write as well: OpenAI on GPT-5.6 and later,
#   at 1.25x input once a prompt passes 1,024 tokens, and Kimi on K3, at its 5 minute rate. A
#   vendor that sells no cache write at all, billing the tokens that fill a cache at the input
#   rate, carries `None` there rather than 0: 0 would make those tokens free.
# - **`None` is not free.** Four Cohere models have no published per-token rate and carry no
#   `price` at all; a run on one records `cost_usd: null` and says `cost unpriced`. A model whose
#   vendor genuinely charges nothing is `Price(0.0, 0.0, ...)` and says `$0`.

CATALOGUE: dict[str, tuple[ModelSpec, ...]] = {
    # platform.claude.com models overview and model-deprecations. Claude Mythos is invitation only
    # (Project Glasswing). Opus 4.1 and everything 4.0 or older is retired. Three of these ids are
    # aliases onto a dated snapshot (haiku-4-5, opus-4-5, sonnet-4-5); the rest are dateless ids
    # that are a pinned snapshot themselves, with no dated form to pin to.
    #
    # Three things about how a model has to be asked differ by row. Opus 5.5 and Fable 5.1 will
    # not be forced to call a tool: they answer `tool_choice` `any` with a 400, so they carry
    # `forced_tools=False` and are asked with `auto`. Haiku 4.5 and Sonnet 4.5 are missing from
    # the effort page's list of supported models, so they carry `effort=False` and are sent none.
    # And the three 4.5 models take only the older extended thinking, which `anthropic.py` learns
    # from the 400 on the first turn and goes without for the rest of the run. Opus 5.5 and Fable
    # 5.1 also bind every thinking block to all that came before it, so they carry
    # `binds_thinking=True`: the loop trims their old frames every eight exchanges rather than on
    # every call and leaves out the blocks each trim invalidates from that call on, and the API is
    # asked to drop the latest turn's, which may not be left out.
    #
    # Prices: cache writes are the 5 minute TTL rate (1.25x input); the 1 hour rate is 2x input
    # and is not here. Cache reads are a tenth of input on every model except three Anthropic
    # footnote: Fable 5.1 and Mythos 5.1 at 0.025x (only the first has a row here, Mythos being
    # invitation only) and Opus 5.5 at 0.05x. So a Fable 5 read costs four times what a Fable 5.1
    # read does, and an Opus 5 read two and a half times what an Opus 5.5 read does: do not copy
    # one row of either pair onto the other. Neither pricing page prints an API id, but the models
    # overview carries a Claude API ID row under each current model's label and price, and every
    # model's own page prints its id beside all four rates used here.
    "anthropic": (
        ModelSpec(
            "claude-opus-5-5",
            "Claude Opus 5.5",
            forced_tools=False,
            binds_thinking=True,
            price=Price(4.0, 20.0, 0.2, 5.0),
        ),
        ModelSpec(
            "claude-fable-5-1",
            "Claude Fable 5.1",
            forced_tools=False,
            binds_thinking=True,
            price=Price(10.0, 50.0, 0.25, 12.5),
        ),
        ModelSpec("claude-sonnet-5", "Claude Sonnet 5", price=Price(2.0, 10.0, 0.2, 2.5)),
        ModelSpec(
            "claude-haiku-4-5", "Claude Haiku 4.5", effort=False, price=Price(1.0, 5.0, 0.1, 1.25)
        ),
        ModelSpec("claude-fable-5", "Claude Fable 5", "legacy", price=Price(10.0, 50.0, 1.0, 12.5)),
        ModelSpec("claude-opus-5", "Claude Opus 5", "legacy", price=Price(5.0, 25.0, 0.5, 6.25)),
        ModelSpec(
            "claude-opus-4-8", "Claude Opus 4.8", "legacy", price=Price(5.0, 25.0, 0.5, 6.25)
        ),
        ModelSpec(
            "claude-opus-4-7", "Claude Opus 4.7", "legacy", price=Price(5.0, 25.0, 0.5, 6.25)
        ),
        ModelSpec(
            "claude-opus-4-6", "Claude Opus 4.6", "legacy", price=Price(5.0, 25.0, 0.5, 6.25)
        ),
        ModelSpec(
            "claude-opus-4-5", "Claude Opus 4.5", "legacy", price=Price(5.0, 25.0, 0.5, 6.25)
        ),
        ModelSpec(
            "claude-sonnet-4-6", "Claude Sonnet 4.6", "legacy", price=Price(3.0, 15.0, 0.3, 3.75)
        ),
        ModelSpec(
            "claude-sonnet-4-5",
            "Claude Sonnet 4.5",
            "legacy",
            effort=False,
            price=Price(3.0, 15.0, 0.3, 3.75),
        ),
    ),
    # developers.openai.com models and deprecations. The gpt-5 and o-series generation has
    # shutdown dates in 2026, so none of it is here. The one exception is `gpt-5-search-api`,
    # still priced with no shutdown, whose own model page no longer loads, so nothing here can
    # say it takes function tools. The Daybreak, Cyber and Rosalind models need their own
    # approval. `gpt-oss-*` are weights, not an endpoint. The three GPT-6 models open on the
    # Responses API: Sol and Luna take function tools on Chat Completions only at
    # `reasoning_effort` none and default to medium, and OpenAI's Astra release note says its
    # tool calling requires the Responses API.
    #
    # Prices: the short-context band. A prompt over 272k tokens is billed at 2x input and cache
    # rates and 1.5x output for the WHOLE request, and nothing here sees that coming. That band
    # is not only the flagships: the page carries long-context columns for ten of these ids, the
    # six 5.6-and-later models plus gpt-5.5, gpt-5.4, gpt-5.5-pro and gpt-5.4-pro, while
    # gpt-5.2-pro has none. Cache writes are 1.25x input on GPT-5.6 and later. Before it there is
    # no write rate at all (a dash on the page, and "No additional cache-write charge" in the
    # caching guide), so those rows carry None and a written token is charged at the input rate,
    # which is what OpenAI bills. gpt-5.6-sol's rate is promotional, and OpenAI promise it only
    # "at least through November 21, 2026".
    "openai": (
        ModelSpec("gpt-6-sol", "GPT-6 Sol", api="responses", price=Price(2.0, 10.0, 0.2, 2.5)),
        ModelSpec(
            "gpt-6-astra", "GPT-6 Astra", api="responses", price=Price(10.0, 50.0, 1.0, 12.5)
        ),
        ModelSpec("gpt-6-luna", "GPT-6 Luna", api="responses", price=Price(0.1, 0.5, 0.01, 0.125)),
        ModelSpec("gpt-5.6-sol", "GPT-5.6 Sol", price=Price(4.0, 20.0, 0.4, 5.0)),
        ModelSpec("gpt-5.6-terra", "GPT-5.6 Terra", price=Price(2.0, 12.0, 0.2, 2.5)),
        ModelSpec("gpt-5.6-luna", "GPT-5.6 Luna", price=Price(0.2, 1.2, 0.02, 0.25)),
        ModelSpec("gpt-5.5", "GPT-5.5", "legacy", price=Price(5.0, 30.0, 0.5)),
        ModelSpec("gpt-5.4", "GPT-5.4", "legacy", price=Price(2.5, 15.0, 0.25)),
        ModelSpec("gpt-5.4-mini", "GPT-5.4 mini", "legacy", price=Price(0.75, 4.5, 0.075)),
        ModelSpec("gpt-5.4-nano", "GPT-5.4 nano", "legacy", price=Price(0.2, 1.25, 0.02)),
        ModelSpec("gpt-5.2", "GPT-5.2", "legacy", price=Price(1.75, 14.0, 0.175)),
        ModelSpec("gpt-5.1", "GPT-5.1", "legacy", price=Price(1.25, 10.0, 0.125)),
        ModelSpec("gpt-4.1", "GPT-4.1", "legacy", price=Price(2.0, 8.0, 0.5)),
        ModelSpec("gpt-4.1-mini", "GPT-4.1 mini", "legacy", price=Price(0.4, 1.6, 0.1)),
        ModelSpec("gpt-4o", "GPT-4o", "legacy", price=Price(2.5, 10.0, 1.25)),
        ModelSpec("gpt-4o-mini", "GPT-4o mini", "legacy", price=Price(0.15, 0.6, 0.075)),
        ModelSpec(
            "gpt-5.5-pro", "GPT-5.5 Pro", "specialised", api="responses", price=Price(30.0, 180.0)
        ),
        ModelSpec(
            "gpt-5.4-pro", "GPT-5.4 Pro", "specialised", api="responses", price=Price(30.0, 180.0)
        ),
        ModelSpec(
            "gpt-5.2-pro", "GPT-5.2 Pro", "specialised", api="responses", price=Price(21.0, 168.0)
        ),
        ModelSpec(
            "gpt-5.3-codex",
            "GPT-5.3 Codex",
            "specialised",
            api="responses",
            price=Price(1.75, 14.0, 0.175),
        ),
        ModelSpec(
            "chat-latest",
            "ChatGPT Instant (moves with ChatGPT)",
            "specialised",
            price=Price(5.0, 30.0, 0.5),
        ),
    ),
    # ai.google.dev models and deprecations. Gemini 3.1 Flash-Lite has a shutdown date already
    # (2027-05-07), and so does Robotics ER 1.6 (2026-08-31), while the 2.0 family and the
    # Gemini 3 Pro preview are gone. ER 2 is here because quackd drives robots and that is its
    # subject. The 2.5 family is out as well, although Google has not deprecated it: since
    # 2026-09-18 Google is "limiting access to the 2.5 models to users who have actively used
    # them in the past", and a model a new user cannot call does not belong here.
    # `gemini-3.1-pro-preview-customtools` is 3.1 Pro preview at the same price under its own id,
    # which Google's page says is "better at prioritizing your custom tools", and every quackd
    # verb is one. The models page
    # calls 3.5 Flash "our legacy Flash model" and 3.7 and 3.6 Flash "our previous-generation
    # Flash model", and names 3.8 Flash and 3.5 Flash-Lite as its latest, so the three older
    # Flash models sit in legacy. The pricing page words it differently, calling 3.5 Flash "our
    # earlier Flash model" and giving "our legacy Flash model" to Gemini 3 Flash Preview, which
    # stays a preview because that is Google's own label for it.
    #
    # Prices: 3.8, 3.7 and 3.6 Flash and Robotics ER 2 are on an introductory rate that DOUBLES
    # on 2027-01-01: the pricing page prints each of their rates as good "through December 31,
    # 2026" beside a rate twice as high after it. From that date those four under-report by
    # half until somebody re-reads it. Both 3.1 Pro rows are the short band; above 200k their
    # input and cache rates double and their output goes to 1.5x, and the rest are flat.
    # Google charge nothing to create a cache, billing storage by the hour instead, which is not
    # a per-token rate and is not costed here. Every model here publishes a cached-input rate,
    # 3.5 Flash-Lite's $0.03 included: it sits in the paid column beside a free-tier "Not
    # available", which is easy to read as no rate at all.
    "gemini": (
        ModelSpec("gemini-3.8-flash", "Gemini 3.8 Flash", price=Price(0.75, 3.75, 0.075, 0.0)),
        ModelSpec(
            "gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", price=Price(0.3, 2.5, 0.03, 0.0)
        ),
        ModelSpec(
            "gemini-3.1-pro-preview",
            "Gemini 3.1 Pro (preview)",
            "preview",
            price=Price(2.0, 12.0, 0.2, 0.0),
        ),
        ModelSpec(
            "gemini-3.1-pro-preview-customtools",
            "Gemini 3.1 Pro, custom tools (preview)",
            "preview",
            price=Price(2.0, 12.0, 0.2, 0.0),
        ),
        ModelSpec(
            "gemini-3-flash-preview",
            "Gemini 3 Flash (preview)",
            "preview",
            price=Price(0.5, 3.0, 0.05, 0.0),
        ),
        ModelSpec(
            "gemini-robotics-er-2-preview",
            "Gemini Robotics ER 2 (preview)",
            "preview",
            price=Price(1.0, 5.0, 0.1, 0.0),
        ),
        ModelSpec(
            "gemini-3.7-flash", "Gemini 3.7 Flash", "legacy", price=Price(0.75, 3.75, 0.075, 0.0)
        ),
        ModelSpec(
            "gemini-3.6-flash", "Gemini 3.6 Flash", "legacy", price=Price(0.75, 3.75, 0.075, 0.0)
        ),
        ModelSpec(
            "gemini-3.5-flash", "Gemini 3.5 Flash", "legacy", price=Price(1.5, 9.0, 0.15, 0.0)
        ),
    ),
    # docs.x.ai. grok-4 and the whole grok-3 and grok-4-fast line were retired on 2026-05-15 and
    # now answer as grok-4.3 without saying so, which is the kind of silence a catalogue exists
    # to stop; grok-code-fast-1 went the same day and answers as grok-build-0.1. All seven here
    # take an image and all seven do function calling. Grok 4.20 multi-agent is not here even
    # though its model card says function calling: xAI's multi-agent guide says it takes no
    # client-side function tools and does not work on Chat Completions, which is the API quackd
    # calls. Grok 4.7 Fast is sold only through Cursor and Grok Build, not on the public API.
    # xAI's model chooser names Grok 4.7 alone for code and chat, so it is the default; 4.6
    # charges the same, and 4.5 the same apart from a cheaper cached read. xAI now calls Chat
    # Completions a legacy endpoint, with no shutdown date, and `grok.py` still rides it.
    #
    # Prices: the band below 200k prompt tokens. Above it xAI charge 2x for the whole request.
    # They publish a cached-input rate and bill the request that sets up a cache as ordinary
    # prompt tokens at the full price, so `cache_write` is None rather than 0, which charges any
    # such tokens at the full rate too.
    "grok": (
        ModelSpec("grok-4.7", "Grok 4.7", price=Price(2.0, 6.0, 0.5)),
        ModelSpec("grok-4.6", "Grok 4.6", price=Price(2.0, 6.0, 0.5)),
        ModelSpec("grok-4.5", "Grok 4.5", price=Price(2.0, 6.0, 0.3)),
        ModelSpec("grok-4.3", "Grok 4.3", price=Price(1.25, 2.5, 0.2)),
        ModelSpec(
            "grok-4.20-0309-reasoning",
            "Grok 4.20 (reasoning)",
            "legacy",
            price=Price(1.25, 2.5, 0.2),
        ),
        ModelSpec(
            "grok-4.20-0309-non-reasoning",
            "Grok 4.20 (non-reasoning)",
            "legacy",
            price=Price(1.25, 2.5, 0.2),
        ),
        ModelSpec("grok-build-0.1", "Grok Build 0.1", "specialised", price=Price(1.0, 2.0, 0.2)),
    ),
    # docs.mistral.ai. Magistral, Devstral, Pixtral, and Medium 3 and 3.1 are all past the
    # retirement dates Mistral's table of deprecated and retired models gives them. Medium 3.5, both
    # GLM rows and Leanstral do not follow the dated pattern: the lifecycle page has moved to
    # name-major-minor (`mistral-medium-3-5`), Public Preview ids use it too, and Labs ids add a
    # `labs-` prefix. The two GLM rows are Z.ai's open-weight models, which Mistral lists as
    # third-party hosted and serves "without Mistral modifications" at its own address under its own
    # ids. Leanstral is Mistral's own, from its Leanstral team.
    #
    # Prices: the standard tier, with cached input a tenth of input across the board. Batch is
    # half and priority is 1.75x, and neither is here. Mistral publish no cache-write rate and
    # bill the tokens that fill a cache at the standard input price, so `cache_write` is None and
    # such tokens are charged at the input rate. Leanstral is listed at Free with no end date
    # attached, which is a real zero rather than a missing rate, and also the kind of zero that
    # can stop being one without an announcement.
    "mistral": (
        ModelSpec("mistral-medium-3-5", "Mistral Medium 3.5", price=Price(1.5, 7.5, 0.15)),
        ModelSpec("mistral-large-2512", "Mistral Large 3", price=Price(0.5, 1.5, 0.05)),
        ModelSpec("mistral-small-2603", "Mistral Small 4", price=Price(0.15, 0.6, 0.015)),
        ModelSpec("ministral-14b-2512", "Ministral 3 14B", "open", price=Price(0.2, 0.2, 0.02)),
        ModelSpec("ministral-8b-2512", "Ministral 3 8B", "open", price=Price(0.15, 0.15, 0.015)),
        ModelSpec("ministral-3b-2512", "Ministral 3 3B", "open", price=Price(0.1, 0.1, 0.01)),
        ModelSpec(
            "codestral-2508",
            "Codestral 25.08",
            "specialised",
            vision=False,
            price=Price(0.3, 0.9, 0.03),
        ),
        ModelSpec(
            "zai-glm-5-3",
            "Z.ai GLM 5.3 (hosted by Mistral)",
            "preview",
            vision=False,
            price=Price(1.4, 4.4, 0.14),
        ),
        ModelSpec(
            "zai-glm-5-2",
            "Z.ai GLM 5.2 (hosted by Mistral)",
            "preview",
            vision=False,
            price=Price(1.4, 4.4, 0.14),
        ),
        ModelSpec(
            "labs-leanstral-1-5",
            "Leanstral 1.5 (Lean 4 proofs, Mistral labs)",
            "preview",
            price=Price(0.0, 0.0, 0.0),
        ),
    ),
    # api-docs.deepseek.com. The API reference lists exactly these two ids. `deepseek-v4-flash`
    # and `deepseek-v4-flash-vision-exp` are legacy names the API still accepts but only routes to
    # V4.1 Flash for now, so they are not listed, and `deepseek-chat` and `deepseek-reasoner`
    # stopped answering after 2026-07-24. DeepSeek's release post says it is phasing V4 Pro out
    # and announced it would be routed to Flash from 2026-09-14; its change log then kept serving
    # it at its own rate and said it "will provide further notice should there be any changes".
    # So it stays here as `legacy`, served with no end date and ranked below V4.1 Flash by
    # DeepSeek itself. Both think by default, and thinking mode refuses `tool_choice` `required`
    # and wants every earlier turn's reasoning sent back, so `deepseek.py` turns thinking off.
    #
    # Prices are the PEAK band: 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday, except on
    # Chinese public holidays. Every other hour, weekends and those holidays included, costs
    # half, so an off-peak run costs half of what this says. `input` is their cache-miss price
    # and `cache_read` their cache-hit one. They sell no cache write: the tokens that fill the
    # cache are billed at the cache-miss rate, which is what `None` charges them at.
    "deepseek": (
        ModelSpec("deepseek-flash", "DeepSeek V4.1 Flash", price=Price(0.3, 1.2, 0.006)),
        ModelSpec(
            "deepseek-v4-pro",
            "DeepSeek V4 Pro",
            "legacy",
            vision=False,
            price=Price(1.32, 3.96, 0.044),
        ),
    ),
    # docs.cohere.com, reached through the OpenAI compatibility endpoint. Command A Vision is out
    # because its own page says "tool use isn't supported with this model", and Command A
    # Translate is out as a translation model whose page says nothing about tool use at all.
    # North Small Translate and the Aya models are out for the same silence. North Mini Code is
    # in as a specialised coding model: Cohere's own model card says it "has been specifically
    # trained with tool-use capabilities for agentic coding", and it is served on the Chat API,
    # which the models page offers "for evaluation".
    #
    # Prices: THE COMMAND A FAMILY HAS NO PUBLISHED PER-TOKEN RATE, the default included, and
    # neither does North Mini Code. Every per-token generative price on Cohere's page belongs to an
    # older model. The three rates below are its Command R line (Command R and R7B on the page's
    # cards, Command R+ 08-2024 only in a legacy FAQ headed "For existing customers"), and the rest
    # are the original Command and Command Light and Aya Expanse, none of them here. Cohere's
    # pricing doc says the Command models are "priced on a per-token basis" and publishes no rate
    # for them, and its Model Vault docs sell the Command A family and North as dedicated instances
    # by the hour, which cannot be turned into a per-token figure. Command A+ and North Mini Code
    # are on the pricing page as "Free", $0 against "API key" with no key type named, while the
    # page's own FAQ says a production key is billed pay as you go at a rate it does not state, and
    # the rate-limits page puts newer models such as Command A Reasoning under trial-key terms even
    # on a production key. Nothing there settles what a production call to either costs, so both are
    # left unpriced rather than recorded as free. A default Cohere run therefore reports `cost_usd:
    # null` and says `cost unpriced`, and `--price` is how you tell quackd your own rate. This is
    # the case the null was built for.
    "cohere": (
        ModelSpec("command-a-plus-05-2026", "Command A+"),
        ModelSpec("command-a-03-2025", "Command A", vision=False),
        ModelSpec(
            "command-a-reasoning-08-2025", "Command A Reasoning", "specialised", vision=False
        ),
        ModelSpec("north-mini-code-1-0", "North Mini Code", "specialised", vision=False),
        ModelSpec(
            "command-r-plus-08-2024", "Command R+", "legacy", vision=False, price=Price(2.5, 10.0)
        ),
        ModelSpec("command-r-08-2024", "Command R", "legacy", vision=False, price=Price(0.15, 0.6)),
        ModelSpec(
            "command-r7b-12-2024", "Command R7B", "legacy", vision=False, price=Price(0.0375, 0.15)
        ),
    ),
    # Alibaba Cloud Model Studio. Only the rolling ids are here: the dated snapshots behind them
    # (`qwen3.7-plus-2026-05-26` and the rest) are a pinning mechanism, not a menu, and Alibaba
    # gives them a month of notice against three for a rolling id. `qwen3-max`,
    # `qwen3-coder-plus` and `qwen3-coder-next` are still on the price list and are not here:
    # Alibaba retire all three on 2026-10-10. Qwen3.7 Max is `legacy` because the text generation
    # page files it, alone of the 3.7 models, under "Legacy models"; 3.7 Plus and Flash stay
    # `current` because that
    # page and the visual understanding page both keep them under "Recommended models", though
    # the visual understanding page also lists them under its own Legacy heading. Model Studio
    # also serves DeepSeek, Kimi and GLM models, some under those vendors' own ids, which are
    # listed under those vendors here because an id may belong to one vendor only, and some under
    # ids of Alibaba's own, which are not listed anywhere yet. `qwen3.8-omni-flash`
    # (2026-09-17) takes function tools and is not here: no Omni model ever has been, and whether
    # one belongs is a decision rather than a refresh.
    #
    # Prices: the Singapore international catalogue at the FIRST input-length tier, which is the
    # sharpest under-report in this file. `qwen3-coder-flash` goes from $0.3/$1.5 to $1.6/$9.6
    # above 256k, more than six times the output rate, `qwen3.7-flash` more than triples both
    # rates past just 32k, and the China region is priced differently again. Where a model splits
    # its output rate by thinking mode this is the non-thinking one (`qwen-plus` is $1.2 against
    # $4 thinking). Alibaba publish no per-model cache rate for any model here, only rules: an
    # implicit cache, on by itself, whose hits cost 20% of input on most of the models it lists
    # but a rate Alibaba leave to the console on three rows here, `qwen3.8-max`, `qwen3.8-flash`
    # and `qwen3.8-2.4t-a95b`, and an explicit one quackd never asks for. A rate derived from a
    # rule is not a rate read off a page, so every cache field here is None, and an implicit hit
    # is charged at the full input rate: an overstatement, up to five times on the cached part on
    # the 20% rows and by an unpublished factor on those three, never an understatement.
    "qwen": (
        ModelSpec("qwen3.8-max", "Qwen3.8 Max", price=Price(2.0, 6.0)),
        ModelSpec("qwen3.8-flash", "Qwen3.8 Flash", price=Price(0.15, 0.47)),
        ModelSpec("qwen3.7-plus", "Qwen3.7 Plus", price=Price(0.4, 1.6)),
        ModelSpec("qwen3.7-flash", "Qwen3.7 Flash", price=Price(0.03, 0.13)),
        ModelSpec("qwen3.7-max", "Qwen3.7 Max", "legacy", vision=False, price=Price(2.5, 7.5)),
        ModelSpec("qwen3.6-plus", "Qwen3.6 Plus", "legacy", price=Price(0.5, 3.0)),
        ModelSpec("qwen3.6-flash", "Qwen3.6 Flash", "legacy", price=Price(0.25, 1.5)),
        ModelSpec("qwen3.5-plus", "Qwen3.5 Plus", "legacy", price=Price(0.4, 2.4)),
        ModelSpec("qwen3.5-flash", "Qwen3.5 Flash", "legacy", price=Price(0.1, 0.4)),
        ModelSpec("qwen-max", "Qwen Max", "legacy", vision=False, price=Price(1.6, 6.4)),
        ModelSpec("qwen-plus", "Qwen Plus", "legacy", vision=False, price=Price(0.4, 1.2)),
        ModelSpec("qwen-flash", "Qwen Flash", "legacy", vision=False, price=Price(0.05, 0.4)),
        ModelSpec(
            "qwen3-coder-flash",
            "Qwen3 Coder Flash",
            "specialised",
            vision=False,
            price=Price(0.3, 1.5),
        ),
        ModelSpec("qwen3.8-27b", "Qwen3.8 27B", "open", price=Price(0.5, 3.0)),
        ModelSpec(
            "qwen3.8-2.4t-a95b", "Qwen3.8 2.4T-A95B", "open", vision=False, price=Price(2.0, 6.0)
        ),
        ModelSpec("qwen3.6-35b-a3b", "Qwen3.6 35B-A3B", "open", price=Price(0.375, 2.25)),
        ModelSpec("qwen3.6-27b", "Qwen3.6 27B", "open", price=Price(0.6, 3.6)),
        ModelSpec("qwen3.5-397b-a17b", "Qwen3.5 397B-A17B", "open", price=Price(0.6, 3.6)),
        ModelSpec("qwen3.5-122b-a10b", "Qwen3.5 122B-A10B", "open", price=Price(0.4, 3.2)),
        ModelSpec("qwen3.5-27b", "Qwen3.5 27B", "open", price=Price(0.3, 2.4)),
        ModelSpec("qwen3.5-35b-a3b", "Qwen3.5 35B-A3B", "open", price=Price(0.25, 2.0)),
    ),
    # platform.kimi.ai. Everything before K2.6 is discontinued: kimi-k2.5 and every moonshot-v1
    # model on 2026-08-31, which now answer 404, the kimi-k2 series on 2026-05-25, and
    # kimi-latest and kimi-thinking-preview before that. Only K3 accepts `required`: the other
    # three return an error for it. Naming a single function is refused whenever thinking is on,
    # which is always on K3 and K2.7 Code and by default on K2.6, which is why `kimi.py` asks
    # rather than insists.
    #
    # Prices: `input` is cache-miss and `cache_read` cache-hit. Only K3 publishes a cache-write
    # rate, at the 5 minute TTL; the K2 table has no such column, so those three are None. Kimi
    # writes that 5 minute cache by default when a request does not say otherwise, so K3's write
    # rate is charged on ordinary runs, and it equals K3's input rate.
    "kimi": (
        ModelSpec("kimi-k3", "Kimi K3", price=Price(3.0, 15.0, 0.3, 3.0)),
        ModelSpec("kimi-k2.6", "Kimi K2.6", price=Price(0.95, 4.0, 0.16)),
        ModelSpec("kimi-k2.7-code", "Kimi K2.7 Code", "specialised", price=Price(0.95, 4.0, 0.19)),
        ModelSpec(
            "kimi-k2.7-code-highspeed",
            "Kimi K2.7 Code (high speed)",
            "specialised",
            price=Price(1.9, 8.0, 0.38),
        ),
    ),
    # docs.z.ai. GLM-5.3-Flash, GLM-5.3-FlashX and the `v` models take images, and the rest are
    # text only. GLM-4.5V is not here: the API reference gives `tools` only to the GLM-5.3-Flash
    # series, the GLM-4.6V series and AutoGLM-Phone among the vision models, and AutoGLM-Phone is
    # a phone-automation agent with no row on the pricing page. GLM-5.2 stays `legacy` although
    # the pricing page still lists it among its latest: the overview's own Latest Models are
    # GLM-5.3 and GLM-5.3-Flash, and Z.ai's migration guide moves callers off 5.2.
    #
    # Prices are flat, with no context bands on the page today, not even for the 1M-context
    # GLM-5.3 line, though earlier GLM generations had them and a refresh should look again.
    # Three Flash models are genuinely free (GLM-4.7-Flash, GLM-4.5-Flash and GLM-4.6V-Flash);
    # GLM-5.3-Flash is not, whatever its name suggests. No cache-creation rate is published for
    # any of them, and the "Cached Input Storage" column reads "Limited-time Free", which is a
    # storage promotion rather than a write rate and is deliberately not mapped onto one.
    # `glm-4-32b-0414-128k` has no cached-input rate either, so its cache reads are charged at
    # the full input rate.
    "glm": (
        ModelSpec("glm-5.3", "GLM-5.3", vision=False, price=Price(1.4, 4.4, 0.26)),
        ModelSpec("glm-5.3-flash", "GLM-5.3 Flash", price=Price(0.15, 0.5, 0.03)),
        ModelSpec("glm-5.3-flashx", "GLM-5.3 FlashX", price=Price(0.37, 1.25, 0.075)),
        ModelSpec("glm-5.2", "GLM-5.2", "legacy", vision=False, price=Price(1.4, 4.4, 0.26)),
        ModelSpec("glm-5.1", "GLM-5.1", "legacy", vision=False, price=Price(1.4, 4.4, 0.26)),
        ModelSpec("glm-5", "GLM-5", "legacy", vision=False, price=Price(1.0, 3.2, 0.2)),
        ModelSpec("glm-4.7", "GLM-4.7", "legacy", vision=False, price=Price(0.6, 2.2, 0.11)),
        ModelSpec(
            "glm-4.7-flash",
            "GLM-4.7 Flash",
            "legacy",
            vision=False,
            price=Price(0.0, 0.0, 0.0, 0.0),
        ),
        ModelSpec(
            "glm-4.7-flashx", "GLM-4.7 FlashX", "legacy", vision=False, price=Price(0.07, 0.4, 0.01)
        ),
        ModelSpec("glm-4.6", "GLM-4.6", "legacy", vision=False, price=Price(0.6, 2.2, 0.11)),
        ModelSpec("glm-4.5", "GLM-4.5", "legacy", vision=False, price=Price(0.6, 2.2, 0.11)),
        ModelSpec("glm-4.5-x", "GLM-4.5 X", "legacy", vision=False, price=Price(2.2, 8.9, 0.45)),
        ModelSpec(
            "glm-4.5-air", "GLM-4.5 Air", "legacy", vision=False, price=Price(0.2, 1.1, 0.03)
        ),
        ModelSpec(
            "glm-4.5-airx", "GLM-4.5 AirX", "legacy", vision=False, price=Price(1.1, 4.5, 0.22)
        ),
        ModelSpec(
            "glm-4.5-flash",
            "GLM-4.5 Flash",
            "legacy",
            vision=False,
            price=Price(0.0, 0.0, 0.0, 0.0),
        ),
        ModelSpec(
            "glm-4-32b-0414-128k", "GLM-4 32B (128k)", "legacy", vision=False, price=Price(0.1, 0.1)
        ),
        ModelSpec("glm-4.6v", "GLM-4.6V", "specialised", price=Price(0.3, 0.9, 0.05)),
        ModelSpec(
            "glm-4.6v-flash", "GLM-4.6V Flash", "specialised", price=Price(0.0, 0.0, 0.0, 0.0)
        ),
        ModelSpec(
            "glm-4.6v-flashx", "GLM-4.6V FlashX", "specialised", price=Price(0.04, 0.4, 0.004)
        ),
    ),
    # Meta retired the hosted Llama API in July 2026. What replaced it is the Meta Model API,
    # which is OpenAI shaped and serves Muse Spark. The contributor tier is cheaper because Meta
    # trains on what you send it, which is why the label says so rather than the release notes.
    # Meta price per TIER rather than per model, so 1.1 costs exactly what 1.3 does; if they
    # ever split those rows these constants go stale in silence. There is no long-context
    # premium on the 1M window, which is unusual enough to be worth writing down.
    "meta": (
        ModelSpec("muse-spark-1.3", "Muse Spark 1.3", price=Price(1.25, 4.25, 0.15)),
        ModelSpec("muse-spark-1.2", "Muse Spark 1.2", "legacy", price=Price(1.25, 4.25, 0.15)),
        ModelSpec("muse-spark-1.1", "Muse Spark 1.1", "legacy", price=Price(1.25, 4.25, 0.15)),
        ModelSpec(
            "muse-spark-1.3-contributor",
            "Muse Spark 1.3 (contributor tier, Meta trains on your prompts)",
            "specialised",
            price=Price(0.1, 0.2, 0.002),
        ),
        ModelSpec(
            "muse-spark-1.2-contributor",
            "Muse Spark 1.2 (contributor tier, Meta trains on your prompts)",
            "specialised",
            price=Price(0.1, 0.2, 0.002),
        ),
    ),
    # OpenRouter is a router, not a lab, and this is not its whole list (`OPEN_ENDED`): any
    # other id it lists with tool calling is taken when a run starts, checked and priced against
    # that list (ADR-0050). What earns one of these six rows is narrower than the rule at the
    # top: a model from a vendor quackd already drives natively, served by its own maker, whose
    # entry in OpenRouter's `GET /api/v1/models` on 2026-10-06 named `tools` and `tool_choice`
    # among its supported parameters, took `image` input and carried no `expiration_date`.
    #
    # Rates are OpenRouter's own per-token strings times a million, read off that list on
    # `OPENROUTER_PRICES_CHECKED`. They are the base rates: the GPT-6 and Grok entries also
    # carry `overrides` that charge more past a long-prompt threshold, the SHORT band rule
    # above. Gemini 3.8 Flash's cache write is OpenRouter's `input_cache_write` of
    # 0.0000000416666666666667 a token, where Google's own page lists none; it is copied as
    # listed rather than rounded. A turn on OpenRouter is normally costed at what OpenRouter
    # says it billed (`usage.cost`), and these rates are what it falls back to.
    #
    # The two Claude rows will not be forced to call a tool (see `forced_tools`), so they are
    # asked with `auto`. None of them names an `api`: OpenRouter is only ever asked on Chat
    # Completions, whatever OpenAI's own API wants for the same model.
    "openrouter": (
        ModelSpec(
            "openai/gpt-6-sol",
            "GPT-6 Sol (via OpenRouter)",
            price=Price(2.0, 10.0, 0.2, 2.5, checked=OPENROUTER_PRICES_CHECKED),
        ),
        ModelSpec(
            "openai/gpt-6-luna",
            "GPT-6 Luna (via OpenRouter)",
            price=Price(0.1, 0.5, 0.01, 0.125, checked=OPENROUTER_PRICES_CHECKED),
        ),
        ModelSpec(
            "anthropic/claude-sonnet-5.5",
            "Claude Sonnet 5.5 (via OpenRouter)",
            forced_tools=False,
            price=Price(2.0, 10.0, 0.2, 2.5, checked=OPENROUTER_PRICES_CHECKED),
        ),
        ModelSpec(
            "anthropic/claude-opus-5.5",
            "Claude Opus 5.5 (via OpenRouter)",
            forced_tools=False,
            price=Price(4.0, 20.0, 0.2, 5.0, checked=OPENROUTER_PRICES_CHECKED),
        ),
        ModelSpec(
            "google/gemini-3.8-flash",
            "Gemini 3.8 Flash (via OpenRouter)",
            price=Price(0.75, 3.75, 0.075, 0.0416666666666667, checked=OPENROUTER_PRICES_CHECKED),
        ),
        ModelSpec(
            "x-ai/grok-4.7",
            "Grok 4.7 (via OpenRouter)",
            price=Price(2.0, 6.0, 0.5, checked=OPENROUTER_PRICES_CHECKED),
        ),
    ),
}

#: Vendors with a catalogue. Derived, so adding a vendor above is the whole edit.
CLOUD_NAMES: tuple[str, ...] = tuple(CATALOGUE)

#: Servers that speak OpenAI's API, usually on your own machine. They serve whatever you pulled,
#: so they have no catalogue and the model half of `--llm` stays free text there (ADR-0014).
#: `local.PRESETS` holds the addresses, and a test pins the two to the same set.
LOCAL_NAMES: tuple[str, ...] = ("local", "ollama", "vllm", "llamacpp", "lmstudio")

PROVIDER_NAMES: tuple[str, ...] = ("fake", *CLOUD_NAMES, *LOCAL_NAMES)

DEFAULT_LLM = "fake"
"""The pilot when nothing names one: no key, no network, a rule that plays the starters.

A registered robot may name its own and `QUACKD_LLM` may name another, and `--llm` beats both.
It lives here rather than next to the CLI flag because this module is the import-light one:
`quackd --help` and every press of TAB read it, and neither should pay for pydantic to learn
what the default pilot is."""

LLM_ENV = "QUACKD_LLM"
"""The variable `--llm` falls back to, holding a whole spec (`anthropic:claude-opus-5`) rather
than the bare model id the old `QUACKD_MODEL` held. One flag, one variable, one spelling."""


def models_for(provider: str) -> tuple[ModelSpec, ...]:
    """Every model a provider offers, in display order.

    Empty for `fake`, for the local presets and for anything unknown, all of which pick their
    model some other way. A caller that gets `()` must not conclude the provider is broken.
    """
    return CATALOGUE.get(provider, ())


def model_ids(provider: str) -> tuple[str, ...]:
    return tuple(m.id for m in models_for(provider))


def default_model_for(provider: str) -> str | None:
    """The model a bare `--llm <vendor>` means, or None where quackd does not choose one."""
    models = models_for(provider)
    return models[0].id if models else None


def find_model(provider: str, model_id: str) -> ModelSpec | None:
    for m in models_for(provider):
        if m.id == model_id:
            return m
    return None


def vendor_of(model_id: str) -> str | None:
    """Which vendor lists this id, if any.

    Ids are unique across the catalogue (a test holds them to it), so this is what turns "unknown
    model" into "that is a grok model, pass --llm grok:grok-4.6", which is the mistake worth
    catching. It is also what lets `--llm grok-4.6` work with no vendor at all: a bare id names
    exactly one vendor, so there is nothing to disambiguate.
    """
    for provider, models in CATALOGUE.items():
        if any(m.id == model_id for m in models):
            return provider
    return None
