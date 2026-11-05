# ADR-0022: Every SDK-facing adapter owns its own VERIFIED / UNVERIFIED file

**Status:** accepted, amended · **Date:** 2026-09-02 · Extends ADR-0006 · Implemented in Phases 2 and 5 of 0.4 ([design](../design/multi-robot.md))

**Amended 2026-11-05 by [ADR-0075](0075-an-adapter-is-a-family-a-body-file-and-a-binding.md)
and [ADR-0076](0076-every-backend-has-a-tier-and-bound-code-names-its-owner.md):** a package
that speaks a protocol another package already speaks imports that package's VERIFIED refs by
name rather than copying them, so one re-pin moves both; the UNVERIFIED refs about its own
maker's promises stay in its own file, and the containment test holds them to its own package
as before. "No adapter row gets ✅ unless we exercised it against its real target ourselves" is
now one of four tiers, described, rehearsed, bound and verified, kept as data on every backend.

## Context

ADR-0006 made `quackd/transport/upstream_api.py` the only module allowed to spell a
Microduck upstream name, tagged VERIFIED or UNVERIFIED, with a test proving UNVERIFIED
names are reachable only from the experimental transports. 0.4 adds two more upstreams:
LeRobot and rosbridge (roslibpy). The rule must scale without weakening.

## Decision

- Each adapter that talks to a third-party SDK or wire protocol owns
  `quackd/adapters/<name>/upstream_api.py`, using the same `UpstreamRef` dataclass
  (re-exported from `quackd/transport/upstream_api.py`; it does not move). Every VERIFIED
  ref carries a permalink to the upstream file and line that was read, pinned to a commit
  hash and dated; anything not read from source is UNVERIFIED and says what quackd does
  about it.
- `tests/test_upstream_api.py` is parametrized over `(module, ALLOWED paths, source
  prefix)`. The Microduck row keeps today's ALLOWED set verbatim. A new row's ALLOWED set
  is its `upstream_api.py`, its experimental backend module and `doctor.py`, nothing else.
- Method names are never guessed. If an upstream cannot be read before release, every ref
  stays UNVERIFIED, the backend refuses to connect with a link to the docs page, and the
  README row shows ⏳, never 🧪 or ✅.
- `docs/adapter-status.md` is the human-readable table of every ref file;
  `docs/transport-status.md` becomes a short redirect and its docs test is retargeted in
  the same commit. `quackd doctor` prints the UNVERIFIED list per adapter.
- No adapter row gets ✅ unless we exercised it against its real target ourselves.

## Consequences

- An adapter's honesty is machine-checked: its docs page must list every ref, and its
  UNVERIFIED identifiers cannot leak into shared code.
- LeRobot and roslibpy pins are recorded when their files are written.
