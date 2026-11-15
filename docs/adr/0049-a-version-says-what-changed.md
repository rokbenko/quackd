# ADR-0049: A version says what changed

**Status:** accepted · **Date:** 2026-09-29 · Extends [ADR-0037](0037-adapters-are-their-own-packages.md) (eight distributions, one version between them, set by `scripts/set_version.py`: every window now starts at the release it ships in, so a patch raises each floor to itself and a minor moves the whole window) · Documented in [RELEASING.md](../../RELEASING.md), [CONTRIBUTING.md](../../CONTRIBUTING.md#versions-and-releases) and the header of [CHANGELOG.md](../../CHANGELOG.md)

## Context

Roboglot shipped sixteen releases between 2026-08-28 and 2026-09-29, from 0.1.0 to 0.16.0, and
every one was a minor. Some held a new body or a new command. Some broke what people had: 0.10.0
made `uv pip install roboglot` install no robot, which ADR-0037 calls breaking for anybody who
installed the bare package, and 0.15.0 removed the Jetson container and could write a
`robots.json` that 0.12 to 0.14 refuse to read. Twice two of them shipped on one day, 0.12.0
and 0.13.0 on 2026-09-23 and 0.15.0 and 0.16.0 on 2026-09-29. A number that moved the same way
for all of them said that something had shipped and nothing else. It never said the one thing
a person upgrading wants to know, which is whether the upgrade asks anything of them.

The changelog's header said the project adheres to Semantic Versioning, which is true and
promises nothing: while the major is 0, anything may change at any time. The machinery for a
patch had existed since ADR-0037, because `scripts/set_version.py` wrote each window from the
major and the minor alone, and no release had used it.

After 0.16.0, `main` held a docs commit and a few small fixes were on their way. Cutting them
as 0.17.0 would have told every reader that they were as large as the release that handed the
arm to a learned policy. Rok took 0.16.1 for them, and asked for the rule written down: when a
release is a patch, when it is a minor, and when it will be a major.

## Decision

**A version speaks about Roboglot's public surface and about nothing else.** The surface is what
a person, a script or another program relies on without reading the source: the CLI and its
exit codes, task files, what Roboglot keeps between runs, the MCP server, the adapter interface a
third party implements, run records, the wire protocols that carry a version of their own,
packaging and the safety behaviour. The README, the docs, the examples, the images, the tests,
`scripts/` and the web demo are not part of it.

**While Roboglot is 0.x, a patch changes nothing a user has to act on and adds nothing to learn,
and a minor is everything else.** A removal or a rename is a minor, announced a minor ahead
where that is practical. Apart from a safety fix, what worked before a patch works after it,
so a patch may refuse what the release before accepted only when that never did what Roboglot
said it would. A safety fix that tightens what could move a body in a way the docs never
promised is a patch, even when a script relied on the looser behaviour.

**The changelog decides it by its headings,** so nobody has to weigh the entries. Anything under
Added, Changed, Deprecated or Removed makes a minor, and only Fixed and Security make a patch.
Entries about the docs, the examples and the images get a heading of their own,
`Documentation`, which never raises the bump. That heading is new: before it, a bench step
written for the docs sat under Added, and a patch holding one could not be told from a patch
that quietly shipped a feature.

**1.0.0 is Rok's decision, in an ADR of its own,** once every surface is written down as public
and guarded by a test, a body has taken its bench checklist to the end on real hardware, a
deprecation path exists, and the registry and the records carry versions an older file is read
by. After it, Semantic Versioning proper, with the safety exception kept.

**The eight distributions are always released together, and every window starts at the release
it ships in,** `>=X.Y.Z,<X.Y+1`. A patch raises each floor to itself, so an adapter from a patch
never installs beside a core from before it, and a minor moves the whole window.

[RELEASING.md](../../RELEASING.md) holds the rules in full, the surface item by item, the table
of headings, what each 1.0.0 condition still lacks, and the order a release is cut in, which
moved there from PLAN.md. This ADR is why they exist and does not copy them.

## Why not

**Calendar versions.** A date says when a release shipped, which the changelog's own headings
already say, and nothing about whether it asks anything of the person installing it. The
windows between the eight packages need a number that says which releases can be mixed, and a
date cannot say that. Two releases shipped on one day twice already, so a date would need a
counter beside it anyway.

**1.0.0 now.** A 1.0.0 promises that the surface holds until 2.0.0. Much of the surface has no
test that holds a change to it. No body has finished its bench checklist, and the arm has not
run Roboglot since 2026-09-23. The registry carries a version and has never moved it, while
0.15.0 wrote files older releases refuse. And there is no path yet by which something is
deprecated, warned about and then removed. Declaring 1.0.0 now would promise what nothing
checks.

**A minor for every release**, which is what sixteen releases did. It makes a release of fixes
look like a release that asks something of you, so every upgrade has to be read before it is
taken. It also moves the core out of the window a third party's adapter pins: docs/adapters.md
tells its author to allow one minor of the core, so a minor for a fix breaks their window when
nothing in the interface changed, where a patch lets them take the fix with no release of their
own.

## Consequences

- 0.16.1 is the first patch, and its `[Unreleased]` section is filed again under the new
  heading before it ships. The bench steps, the two grasp sidecars and the corrected pages
  written after 0.16.0 sat under Added, Changed and Fixed, and all of them are documentation.
- `tests/test_docs.py` fails when a released section whose version has a patch number above
  zero carries Added, Changed, Deprecated or Removed, or one whose patch number is zero carries
  none of them, when any section uses a heading the rule does not name or one twice, and when a
  release's compare link breaks the file's pattern. No test can tell whether an entry sits
  under the right heading. That is review, and CONTRIBUTING.md says what a pull request files.
- Every window starts at the release it ships in. An adapter from a patch needs the core from
  the same patch or later in its minor, so its fix may need what the core gained in that patch.
  A core from a patch still installs beside an adapter from earlier in its minor, whose window
  admits it, so the core's fix may not break one, and a fix that would is a minor.
  `tests/test_workspace.py` fails on a window that does not start at the release or end before
  the next minor, and runs `scripts/set_version.py` on a copy for a patch and for a minor.
- A patch cut from the last tag, while `main` holds work above patch level, tags its own
  release commit rather than a merge into `main`. `ci.yml` ran only on pushes to `main`, on
  tags and on pull requests, so no run would have seen that commit before its tag was public.
  It now runs on a push to a `release/` branch too, and the release branch is pushed and green
  before it is tagged.
- The release checklist left PLAN.md, which keeps a line pointing at RELEASING.md, and
  ADR-0037 carries a note that points here.
- Nothing in Roboglot's code changed. `scripts/set_version.py` writes each window's floor as the
  whole version where it wrote the major and the minor alone, and reads a window written either
  way, and `ci.yml` gained the release branches.
