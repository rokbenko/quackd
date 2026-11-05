# ADR-0062: The model tables are rendered from the catalogues

**Status:** accepted · **Date:** 2026-10-25 · Extends [ADR-0031](0031-model-catalogue.md) (the catalogue is the one source of model names), [ADR-0043](0043-decision-llms-are-a-wire-format-and-a-data-row.md) (a decision LLM is a data row) and [ADR-0060](0060-hugging-face-serves-many-vlas.md) (what the policy server serves is data in the core) · Implemented in `scripts/models_docs.py`, `docs/models/`, `quackd/agent/providers/catalogue.py` (`KEY_FALLBACKS`) and `quackd list-models --kind` in `quackd/cli.py` ([docs/models/](../models/README.md))

## Context

On 2026-10-25 Rok asked for one clear place that says which pilot LLMs, which decision LLMs and
which VLAs quackd supports, and how to use each. What the README and the guides said about
models was spread over about twenty-five places: one vendor table in the README's Usage section,
the decision LLMs in a guide of their own and nowhere a terminal could list them but `quackd
doctor`, and the policies in the policies guide alone. The README, the FAQ and the architecture
page each carried the catalogue's size, 123, typed by hand, and nothing read it back. The Gemini
provider had read `GOOGLE_API_KEY` since 0.1.0, and no page said so.

Each of the three already has a catalogue in code that decides what quackd takes:
`providers/catalogue.py` and `factory.py` for the pilots, `decision/catalogue.py` for the
decision LLMs and `quackd/adapters/policies.py` for the policy types. A table typed beside one of
them is the kind that drifts, as the hand-copied counts show.

## Decision

1. **A `docs/models/` folder beside `docs/adapters/`:** robots there, models here. A page for each
   of the three roles, one for the companies behind them, and an overview. The how-to guides stay
   where they are and are linked, so no page anybody has linked moves.
2. **Every table on those pages is rendered from the catalogues** by `scripts/models_docs.py`,
   between markers the script owns, and `tests/test_docs.py` runs the script on every commit
   and fails when a page is not what it would write. A row is changed in the code and the script
   run, never in a table. A vendor the script has no display name for is an error rather than a
   blank cell.
3. **The README says which models work,** in a section of its own right after which robots
   work, and the Usage section's vendor table moved there whole rather than being copied.
4. **The counts that are still typed are held by tests:** the catalogue's ids, the ones priced and
   the ones not, wherever a living page states them, and the policy types.
5. **`quackd list-models --kind`** prints the decision LLMs and the policy types as well as the
   pilots, from the same data, so the terminal and the pages cannot disagree. Its default prints
   what the command always has.
6. **The second name a vendor's key is read from is one table, `KEY_FALLBACKS`**, which the
   providers, `quackd doctor` and the pages read.

## Why not

**Hand-written tables with a test per row.** The decision LLMs' hub already works that way, and
it is the right shape for a table of install lines and prose. A table of 123 ids and their rates
is the shape a script writes better than a person does, and re-pricing a vendor would then mean
editing the code and the page by hand in step.

**Moving the guides into `docs/models/`.** One home for everything about models, at the price of
every link to the old paths, the PyPI pages of every release among them, which never change.

**A page per vendor.** Twelve cloud vendors, five local presets, eight decision LLMs and six
policy types are thirty-one pages that would each say three things. One page per role carries
them in a table each, and the vendors page turns the same rows around.

## Consequences

- A catalogue change that does not run the script fails `tests/test_docs.py`, with the command to
  run in the failure.
- The pages carry no hand-written row, so a fact a catalogue does not hold, such as a run on
  record, is prose beside the table, and the tests that hold the never-run sentences hold it.
- The script imports the catalogues, so it runs where quackd's own environment does, and it reads
  no environment variable, so a developer's `QUACKD_LLM` or `OPENAI_BASE_URL` changes nothing it
  writes.
