# ADR-0076: Every backend has a tier, and bound code names its owner

**Status:** accepted · **Date:** 2026-11-05 · Follows [ADR-0075](0075-an-adapter-is-a-family-a-body-file-and-a-binding.md) (an adapter is a family, a body file and a binding) · Amends [ADR-0022](0022-per-adapter-upstream-refs.md), whose "no row gets ✅ unless we exercised it" becomes one of four tiers · Implemented in 0.33.0 in `quackd/adapters/catalogue.py` (the `Tier` on every backend) and the tests that hold it · Documented in [docs/adapters/status.md](../adapters/status.md) and [CONTRIBUTING.md](../../CONTRIBUTING.md#add-an-adapter)

## Context

What a backend has and has not been run against is the most-read fact about it, and it is kept
as one free-text string per adapter in the catalogue, with a mark in front: ✅ for a simulator
or a mock, 🧪 for a backend whose names were read and never run, 🤖 for the one body that met
hardware. The marks' meanings live in the README's legend and nowhere a test can read; no test
asserts any status string; and one string per adapter cannot say that `lerobot:mock` is
rehearsed while `lerobot:real` is verified, which is the fact a reader wants.

Eight of nine bodies were added by reading upstream alone, each a real backend nobody could
run, and the standard ADR-0022 set, every name VERIFIED at a pin, was met for all of them. The
cost of that was invisible in the tables and real in the tree: eight bound backends to keep
honest when upstream moves, and nobody to say whether a change broke a robot. With fifteen
more robots wanted, the same path would add fifteen more.

## Decision

**Every backend carries a tier, as data in the catalogue row.** The four tiers:

- **described**: a body quackd lists, registers and reasons about from one YAML, and drives
  no part of ([ADR-0077](0077-a-body-quackd-only-describes-is-one-yaml-in-the-core.md)).
- **rehearsed**: a mock or a simulator of a body, run in the suite.
- **bound**: a backend that reaches a real robot, whose upstream names were read at a pin, and
  that has never met one.
- **verified**: run on hardware, by a named person, on a date.

`Tier(name, owner, date, note)`: the owner and the date are who answers for a bound backend's
first hardware run and when, or who ran a verified one and when; the note is what the status
string used to say of that backend alone. The marks stay and are derived from the tier, 📄 ✅
🧪 🤖, so a ✅ can never sit on a bound backend, and every table that prints a status prints
the tier: [docs/adapters/status.md](../adapters/status.md), `quackd list-adapters`, `quackd
doctor` and their `--json`. The README's table keeps its finer words, simulator, physics and
mock under rehearsed, daemon and names under bound, each behind its tier's mark, and its
legend says which tier each belongs to. A third party's adapter may declare `TIERS`, `FAMILY`
and `ROUTE` on its module and is listed as it is when it does not.

**Bound code enters this repository only with a named owner and a date for its first hardware
run.** A test holds it: every official backend has a tier, a bound or verified one carries an
owner and an ISO date, and a verified date is not in the future. A robot nobody here can run
enters as a described body, or is published out of tree as ADR-0037 allows, where it needs
nothing from this repository. The eight bound backends of 0.32.0's nine adapters are
grandfathered: they carry the owner "grandfathered 0.33.0" and the date this record was
accepted, which is a statement that nobody has named a date, not a date.

**Why not a fifth tier, "planned".** A plan is not a state of the code. A body on order is a
described body until the day its backend is written, and its backend is bound from that day
with the order date as the owner's date, which is the one case the rule names an exception for.

## Consequences

- `list-adapters --json` and `doctor --json` gain the keys `kind`, `tiers`, `family` and
  `route` on every row, which is new surface and a minor.
- The README's badge counts three things, adapters, bodies verified on hardware and bodies
  described, in place of "bodies" and "on hardware".
- A contributor adding a backend that reaches a robot names a person and a date in the
  catalogue row, or writes a YAML instead. The one who names a date owes the hardware
  checklist run, which is what the date is for.
- What changes for the eight bound backends: nothing but their rows, which now say in a word
  what the legend said in a paragraph.
- The arm's `so100_follower` is bound with the SO-101's owner and dates, since it runs the exact
  code that drove the SO-101; `koch_follower` has no owner and is refused at `make()` with this
  record's words until somebody names one.
