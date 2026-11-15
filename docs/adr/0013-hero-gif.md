# ADR-0013: The README hero GIF is a labelled sim recording

**Status:** superseded by [ADR-0038](0038-the-readme-hero-is-a-real-run.md) · **Date:** 2026-08-28

**Amended 2026-11-15:** `docs/assets/hero.gif` is deleted, with the simulator it was recorded in
and `roboglot record`, which recorded it. `docs/assets/transcript-example.jsonl` moved to
`tests/golden/transcript-before-verb-end.jsonl`, because it is the one transcript from before the
log's own kinds, and `roboglot log` still has to read one. See the CHANGELOG's Unreleased entry.

## Context

The brief asks for one recorded real-provider `find-and-kick` run under `docs/assets/`
as the README hero. At build time this machine had no provider API key (no
`ANTHROPIC_API_KEY`, no `ant` CLI profile), and tests must never touch the network.

## Decision

- `docs/assets/hero.gif` is recorded with `roboglot record find-and-kick --provider fake
  --seed 3` — the built-in simulator driven by the **scripted** pilot — and is labelled as
  such in the README caption and in `docs/assets/README.md`.
- `docs/assets/transcript-example.jsonl` is the matching transcript, so readers can see
  the observation → tool call → result rhythm even without an LLM in the loop.
- Regenerating with a real LLM is one command and is tracked as an open item in
  `PLAN.md`: `roboglot record find-and-kick --provider anthropic --seed 3`, then copy
  `runs/<ts>/run.gif` and `transcript.jsonl` over the assets and drop the "scripted" label.

## Consequences

- The README never claims an LLM did something it did not; the demo is still real code,
  real sim, real perception, real safety layer — only the pilot is a rule.
- The first real-provider recording is a small, visible, satisfying PR for whoever has a
  key first (possibly us, the day after launch).
