# ADR-0031: A curated model catalogue instead of free-text model ids

**Status:** accepted, amended · **Date:** 2026-09-12 · Amends [ADR-0010](0010-providers.md) (the four
default ids in its table are what the first release shipped and three were never verified; `--model` for a cloud
vendor is now an id from a list rather than free text; the provider matrix gains seven
OpenAI-compatible vendors; MCP mode selects no model at all)

**Amended 2026-09-22 by [ADR-0043](0043-decision-llms-are-a-wire-format-and-a-data-row.md):** `--provider` and `--model` are one flag now, `--llm
VENDOR[:MODEL]`, and `QUACKD_MODEL` is `QUACKD_LLM` holding a whole spec. Where this page says
`--model X` read `--llm VENDOR:X`, and where it says `--provider X` read `--llm X`; a bare
catalogue id infers its vendor, and shell completion follows what was typed before the colon
rather than a second flag. What the catalogue holds, what it refuses and when it refuses it are
unchanged.

**Amended 2026-09-23:** three defaults moved, and a row can now say how the model has to be
asked. Anthropic and OpenAI both shipped on 2026-09-22: `--llm anthropic` runs `claude-opus-5-5`
rather than `claude-opus-5`, because Anthropic's models overview now says to start with it and
files Opus 5 under legacy, and `--llm openai` runs `gpt-6-sol` rather than `gpt-5.6-sol`, at half
the price, because OpenAI's models page lists it with Astra and Luna as its flagships. `--llm
grok` runs `grok-4.7`, which xAI's model chooser names alone. The rule below is unchanged and is
why each was one edit per vendor: the first entry is the default. What is new is three Anthropic
flags on `ModelSpec`. Claude Opus 5.5, like Claude Fable 5.1 before it, answers a forced tool call
with a 400, so a default nobody had marked would have opened every bare `--llm anthropic` run on
a failed request: a row with `forced_tools=False` is asked with `auto` instead, one call per turn
still, and the provider reads the same 400 for any model the table does not mark, which is the
bargain `api` already makes for OpenAI's Responses models. Both also bind every replayed thinking
block to all that came before it, so a row with `binds_thinking=True` has its old camera frames
trimmed every eight exchanges rather than on every call: from each trim on the loop leaves out
the blocks it invalidated, and the API is asked to drop the latest turn's, which may not be left
out, rather than refuse the request. And a row with `effort=False` is sent no effort. The whole
table was read again against all eleven vendors' pages on 2026-09-23.

**Amended 2026-10-06 by [ADR-0050](0050-a-router-is-a-vendor-whose-list-is-read-on-the-day.md):** one vendor's tuple is a
selection rather than everything `--llm` takes after the colon. OpenRouter carries six rows,
and an id it does not carry is checked for its shape before a key is read and against
OpenRouter's own public model list before the first paid call, instead of being refused.
Its rows name other vendors' models, carry a price date of their own, and are read for
`forced_tools` as Anthropic's are. For every other vendor nothing here changes.

## Context

`--model` took any string and handed it to the vendor. A typo, an id retired last spring and an id
belonging to a different vendor all failed the same way: at the first call, in the vendor's own
words, after the run directory had been made and the robot had connected. Nothing in quackd knew
what any vendor would accept, so the only way to find out was to spend a call and read the 404.

[ADR-0010](0010-providers.md) shipped four defaults and marked three of them "(verify)". It said
plainly why they were unverified — no keys and no network in CI — and that they were
env-overridable. Nobody ever verified them. By 2026-09-12 all three were wrong:

- `gpt-5` has an announced shutdown date, so the default shipped with a deadline on it.
- `gemini-2.5-pro` is legacy.
- `grok-4` was retired in May 2026, and a request for it is answered by `grok-4.3`. That is the
  worst of the three, because nothing fails: the call succeeds, the key is billed, and the
  transcript records a model that did not run.

A default that is wrong is worse than no default, because it is the path everyone takes who has
not yet formed an opinion.

Three other things pointed the same way.

- **The browser demo had a list already and it was decorative.** `web/src/providers.js` declared a
  `models:` array per vendor that nothing read, and the page offered a free-text box for every
  vendor instead. A visitor who came to click one thing had to know a model id to type.
- **Seven more vendors now speak OpenAI's API.** Mistral, DeepSeek, Cohere, Qwen, Kimi, GLM and
  Meta all serve an OpenAI-shaped endpoint, so each is one small `OpenAIProvider` subclass with a
  base URL and a key variable. That is the Grok precedent from ADR-0010, applied seven more times.
  It makes the model id the only thing a reader has to get right, and multiplies by eleven the
  number of ids there are to get wrong.
- **Over MCP quackd has no model of its own.** `quackd serve-mcp` hands tools to a client whose own
  model is the pilot. No provider is constructed and no model is chosen. The docs never said so,
  so a reader who had just learned about `--model` had no way to know it did not apply.

## Decision

- **One ordered tuple per cloud vendor, in `quackd/agent/providers/catalogue.py`.** Eleven vendors,
  115 models. The module imports nothing but the stdlib and must stay that way: `quackd --help`
  imports it, and so does every press of TAB. A `ModelSpec` is an id, a label a human can read, a
  status, and two hints; the id is the only thing that goes on the wire.
- **Five statuses.** `current` is the vendor's headline lineup, `legacy` is still served with no
  end announced, `preview` is the vendor's own word, `specialised` is tuned for one thing (code, a
  deep reasoning tier, vision, a pricing tier), `open` is an open-weight model the vendor serves
  itself. They are a `Literal`, and their order is the display order everywhere.
- **The first entry of each vendor is its default.** A default is not a second place it can be
  written down and fall out of step with. `DEFAULT_MODELS` in `factory.py` is derived.
- **`--model` and `QUACKD_MODEL` are resolved before a key is read or a packet is sent.**
  `resolve_model` raises for an id a cloud vendor does not list, and `make_provider` calls it
  before it imports an SDK or looks at the environment. The refusal lists every valid id, marks the
  default, and points at `quackd list-models`. When the id belongs to another vendor in the
  catalogue it says whose it is and which `--provider` to pass, because that is the commonest
  mistake and the hardest to see: the id looks perfectly valid. The message also names where the id
  came from, `--model` or `QUACKD_MODEL`, since a flag just typed and a forgotten line in a `.env`
  want different answers from the reader.
- **`quackd list-models [--provider NAME]`** prints the whole table, or one vendor's. A notes column
  says `default`, `Responses API` and `no frames`. `--model` also completes in the shell, following
  whichever `--provider` is already on the command line, so the list is reachable without reading
  it.
- **The local presets are exempt and unchanged.** `local`, `ollama`, `vllm`, `llamacpp` and
  `lmstudio` serve whatever was pulled, so `--model` stays free text there and no model at all
  still means "ask the server what it has" ([ADR-0014](0014-local-llms.md)). `fake` ignores the flag
  rather than refusing it: there is no model to pick.
- **Two per-model hints, `vision` and `api`.** `vision=False` marks a vendor that does not document
  image input, and quackd sends the detections as text instead of the camera frame; `--vision`
  overrides. `api="responses"` marks the OpenAI models that will not take function tools on Chat
  Completions, and starts the run on the Responses API. 0.8 learned to read that 400 and move the
  run, which works and costs one failed call every time; the catalogue already knows, so nobody
  pays to find out. The 400 reader stays, because it is what covers a model the catalogue has not
  been told about yet.
- **The browser demo eats the same list.** `web/src/catalogue.js` is generated from `catalogue.py`,
  the dropdown is grouped by status, and a test fails when the generated file has drifted from the
  Python. The decorative `models:` array is gone.
- **MCP selects nothing.** `quackd serve-mcp` takes no `--provider` and no `--model`, the client's
  model is the pilot, and `QUACKD_MODEL` is irrelevant there. Nothing in `mcp_server.py` changed.
  This is written down because it was already true and unstated.
- **The Meta entry is Muse Spark, not Llama.** Meta retired the hosted Llama API in July 2026. Its
  replacement, the Meta Model API, serves Muse Spark, and two of those models are a contributor
  tier: cheaper in exchange for Meta training on your prompts. Their labels say so, because a price
  that is paid in prompts should not be discovered later.

## Consequences

- **This is breaking for anyone passing an id quackd does not list**, which is two of the three
  old defaults. `gpt-5` and `grok-4` are refused by name; `gemini-2.5-pro` is still listed as
  `legacy`, because the vendor still serves it with no end announced, so naming it explicitly
  keeps working and only the default moved. `--provider openai` with no `--model` now runs `gpt-5.6-sol` rather than `gpt-5`,
  `--provider gemini` runs `gemini-3.8-flash` rather than `gemini-2.5-pro`, and `--provider grok`
  runs `grok-4.6` rather than a `grok-4` that was being answered by something else anyway. A
  `QUACKD_MODEL` line that has sat in a `.env` since then now stops the run instead of starting a
  wrong one, and says which vendor the id belongs to if it belongs to one.
- **The catalogue is a dated maintenance surface.** It is only as current as its last edit. A vendor
  can retire an id between quackd releases, and the refusal for a model that is real but new is the
  same refusal as for a typo. `quackd list-models` is how anyone sees what this build knows, the
  docstring carries the date the list was checked, and the honest trade is that a stale list refuses
  a good id where free text let it through.
- **The list was verified rather than guessed.** Eleven parallel checks against the vendors' own
  documentation, one per vendor, then an adversarial pass over the result. The second pass is what
  earned its keep. It caught a Mistral id that never existed, `mistral-medium-2604`, invented by
  pattern-matching the vendor's older date-stamped ids when the real one is `mistral-medium-3-5`; two
  models whose own pages say they cannot call function tools, `command-a-vision-07-2025` and
  `glm-4.5v`, either of which would have been a pilot that cannot pick a verb; and four models that
  had simply been missed. `tests/test_catalogue.py` keeps the ten ids that were removed or rejected
  in a `GONE` set and fails if one creeps back.
- **Which tests hold which seam.** `tests/test_catalogue.py` holds the shape: every cloud vendor has
  models and its first is the default, ids are unique across vendors so `vendor_of` can be trusted,
  every status is one of the five, only OpenAI names an api and only ever `responses`, no `GONE` id
  returns, every vendor has an extra that installs the SDK it imports, every vendor module is
  reachable from the factory, the local presets are exactly the ones the catalogue excuses, and the
  module imports nothing heavy — that last one reads the source rather than importing it, because
  by then the suite has imported half of quackd. It also holds the refusal: the list, the owning
  vendor, the source, and that a bad id stops before the key is read. `tests/test_cli.py` holds the
  user-facing half: the refusal from `--model` and from a pinned `QUACKD_MODEL`, both `list-models`
  forms, and `--model` completing against whichever `--provider` is on the line.
  `tests/test_providers.py` holds that each vendor defaults to its catalogue entry, that an
  `api="responses"` model opens on Responses with no failed call, and that the 400 reader still
  moves a run for a model the catalogue does not mark. `tests/test_web.py` holds the generated
  `web/src/catalogue.js` against the Python.
- **A missing key is still a runtime discovery.** Validating the model earlier does not validate
  anything else: the id is checked against a list in this repository, not against the vendor, so a
  model withdrawn since the last edit fails exactly where it always did.
- **Adding a provider is still one file, one line in `factory.py` and a row in the README matrix**,
  as ADR-0010 said. It is now also one tuple in `catalogue.py`, and that tuple is the part that goes
  stale.
