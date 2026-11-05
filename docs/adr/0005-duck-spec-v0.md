# ADR-0005: The `.duck` file is YAML frontmatter + Markdown, SKILL.md-shaped

**Status:** accepted · **Date:** 2026-08-28

## Context

People who write agent skills already write `SKILL.md`: frontmatter for the machine, prose
for the LLM. A duck task has exactly that split — a contract the executor enforces and
instructions the LLM interprets.

## Decision

- File = `---` YAML frontmatter `---` Markdown body. Leading `#` comment lines above the
  first fence are allowed (so a file can carry a human note like "EXPERIMENTAL").
- Frontmatter is `DuckFrontmatter` (`quackd/duckfile/schema.py`), pydantic v2,
  `extra="forbid"`. `duck: 0` is the only spec version. `schema.json` is generated from the
  model (`python -m quackd.duckfile.export`) and a test keeps it in sync.
- **The executor enforces the frontmatter; the LLM is never trusted to self-police.**
  `verbs.allow`, `verbs.confirm` (must be ⊆ allow), and `budgets` are hard rules.
- `abort_when` is a list of strings. Two phrasings are machine-enforced — `Battery below
  N%` and `Same verb fails N times in a row` — everything else is handed to the LLM as an
  instruction. The spec says so explicitly rather than pretending prose is policy.
- `success` is prose the LLM judges itself against via `declare_success(reason)`. In sim the
  run summary also records ground truth (`final_state.extras`) so tests can check the claim.
- `learned_verbs` is parsed and must be empty in v0.1 (`quackd validate` rejects otherwise).
- Starter ducks live in `ducks/` at the repo root (the community funnel) and are bundled
  into the wheel, so `quackd run find-and-kick` works without a checkout.

## Consequences

- `quackd validate` fails fast with a path and a field-level reason; CI validates `ducks/*`.
- A future `duck: 1` can change the body's role (e.g. structured strategy) without breaking
  v0 files, because the version is explicit and the parser is strict.
