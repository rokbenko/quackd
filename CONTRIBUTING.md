# Contributing to quackd

Thanks for taking a toy duck seriously. Two kinds of contribution matter most: **new
`.duck` files** (the community funnel) and **new verbs** (the vocabulary). Both are small.

## Dev setup

```bash
git clone https://github.com/rokbenko/quackd && cd quackd
uv sync --extra dev            # add --extra anthropic etc. if you want a real provider
uv run pre-commit install
uv run pytest                  # the whole suite, about 80 s, no network, no keys
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Windows, macOS and Linux are all first-class. Tests must never touch the network. Most of
those seconds are the five seeded acceptance sweeps, which CI holds at 10 of 10 by setting
`QUACKD_STRICT_SEEDS=1`; locally they pass at 8 of 10 so a slow machine does not block you.

Touching `bridge/open_duck/`? That is the only code here that runs on a robot, so it plays
by different rules: it must never import quackd (its dependencies do not belong on a 512 MB
Raspberry Pi), it ships in the sdist and never in the wheel, and it stays testable with no
hardware through its `--fake` mode and a pure core the tests drive directly.

Touching `quackd/lan/` or `quackd/flock/mqtt_bus.py`? Neither imports its library at module
level and neither is in the default install, so the tests run them on fakes: a fake zeroconf
registrar and a synchronous fake MQTT broker, no sockets. Keep it that way, and see
[docs/lan.md](docs/lan.md).

## Submit a `.duck`

1. Copy a starter from [`ducks/`](ducks/) and edit the frontmatter + body.
   Spec: [docs/duck-spec.md](docs/duck-spec.md).
2. `uv run quackd validate ducks/your-duck.duck` — it must pass.
3. Run it at least once: `uv run quackd run ducks/your-duck.duck --provider fake`
   (the scripted pilot only knows the starters, so for a new duck use a real provider if
   you have a key, or add a strategy to `quackd/agent/providers/fake.py`).
4. Open a PR. In the description say what it does, which providers you tried, and what
   failed. Ducks that mostly fail are still welcome if the file says so — that is data.

Checklist: `duck: 0` (or `duck: 1` if you use `requires`, `robots` or `flock.roles`) ·
slug name · `allow` lists only verbs the robot provides (`quackd list-verbs --robot ...`)
· `confirm` ⊆ `allow` · at least one `success` line · `abort_when` uses the two enforced
phrasings if you want them enforced · body starts with `# Task` · `quackd validate
your.duck --robot <adapter>:<backend>` passes for the robot you mean.

**Ask for a note.** Since 0.6 every solo starter ends its numbered strategy with a
`remember` and carries a short *Memory* section saying what is worth keeping for next time.
Put the call in the strategy rather than only in a Memory section: a 14B local model read a
prompt-level hint and never wrote to memory, and followed the same instruction on its first
run once it was step 5. `remember` is offered automatically when memory is on and needs
nothing in your `allow` list. Skip it for a smoke test, the way `hello-world` does
([docs/memory.md](docs/memory.md)).

## Add a verb

1. Decide the kind. **Core** (`quackd/verbs/core.py`) = the same on every robot whose
   manifest meets a requirement (a camera, a `twist` intent, a `sound` intent); add its
   `Requirement` to `REQUIREMENTS`. **Extension** = one robot's own behaviour, in that
   adapter's `verbs.py` (Microduck: `quackd/adapters/microduck/verbs.py`; it needs a
   VERIFIED upstream method in the adapter's `upstream_api.py`). **Learned** = v2, see
   [docs/learned-verbs.md](docs/learned-verbs.md). If the thing you are adding never
   touches the body, it is probably not a verb at all: `remember` sits next to
   `declare_success` as a *meta tool* precisely so that the rule "the vocabulary comes from
   the manifest" keeps meaning something ([ADR-0025](docs/adr/0025-memory-between-runs.md)).
2. Write a pydantic params model (`extra="forbid"`, ranges on every number) and an
   `async def my_verb(ctx: VerbContext, p: MyParams) -> VerbResult`. Use
   `ctx.transport.send_intent(...)`, `ctx.transport.sleep(...)`, `ctx.detector`,
   `ctx.manifest` (to pick a strategy per body), and `ctx.on_frame(img, caption)` for the
   GIF. Never call an LLM from a verb.
3. Add a `Verb(...)` template with a one-line LLM-facing description, a `timeout_s`, a
   `safety_class` (`safe` · `confirm` · `dangerous`) and a `done_condition` to `CORE` or to
   the adapter's verb table, then a `VerbSpec` entry in the adapter's manifest (that is
   what makes the verb exist: a verb not in the manifest is not in the registry, the MCP
   tool list, `.duck` validation or the prompt). Preconditions are named in the manifest
   and supplied by the adapter's `conditions()`.
4. Add a test: on `MockTransport` for intent sequences, on `Sim2DTransport` for behaviour.
5. If the verb needs an upstream method we have not verified, add it to the adapter's
   `upstream_api.py` as `UNVERIFIED` with a note and a row in that adapter's page under
   `docs/adapters/` (the Microduck's table is in `docs/adapter-status.md`). Never invent
   one.
6. Mention it in `docs/architecture.md`, the README verb table (a test checks every
   registry name is backticked there) and `CHANGELOG.md` (Unreleased).

Renaming a verb is not a rename: add the new name and keep the old one in
`quackd/verbs/aliases.py`, the only file that may spell an alias.

## Add an adapter

A robot joins quackd as a package under `quackd/adapters/<name>/` that declares a
`RobotManifest` and moves the body through intents its own controllers execute. The
recipe, the rules the manifest enforces and the checklist are in
[docs/adapters.md](docs/adapters.md); the honesty rules are
[ADR-0022](docs/adr/0022-per-adapter-upstream-refs.md). In short: write `mock` first; put
every SDK name in the package's `upstream_api.py` with a pinned link and a row in
`tests/test_upstream_api.py`; import the SDK inside `connect()` behind an extra; never
send the SDK's "go limp" call; write `docs/adapters/<name>.md` listing every ref; and
arrive 🧪 in the status tables until someone runs it against the real thing.

## Working agreements

- **Conventional Commits** (`feat:`, `fix:`, `docs:`, `chore:`, `test:`).
- Consequential decisions get a short ADR in `docs/adr/` (copy the shape of an existing one).
- Every module opens with a docstring saying *why it exists*.
- Keep the default install light: provider SDKs and YOLO stay optional extras.
- No Pollen Robotics assets — no logos, meshes, or videos — ever.
- Tone: confident, playful, honest about status.

## How your PR gets handled

Written down because 0.6 was the first release built on other people's pull requests, and
the way those two were handled is the way the next one will be.

**Your commits stay yours.** A PR is merged with `git merge` into a scratch integration
branch, never squashed, rebased or retyped, so your authorship survives verbatim and your
commits appear on `main` under your name. Not one line of your diff is edited in place.

**Corrections land separately.** Anything that needs fixing on top goes in its own
follow-up commit with its own message, so `git log` keeps the credit and the correction
distinguishable forever. You can read exactly what was changed after you and why, and
disagree with it.

**You get told everything that was found, not a verdict.** The review comment lists every
defect with the reasoning, including the ones that were nobody's fault. If something is
declined, the comment says why.

**You get credited** in the CHANGELOG entry, in the release note, and in the row of faces
in the README, which is generated from the contributor list and orders people by lines
added.

### For whoever is doing the merging

1. **Run the gate on the merged result, not on their branch.** A PR from a fork gets no CI
   here until a maintainer approves the run, so a green checklist in the description is
   usually not evidence about anything. Check how far behind `main` the branch is too: one
   of 0.6's contributions was 67 commits behind, from before robot adapters existed, so its
   ticked boxes had been measured against a repository two releases old.
2. **Check the claims, not only the code.** Both 0.6 contributions were well made and both
   asserted something false in prose. One said the README listed a gap among its
   limitations when it never had, and that sentence was about to ship in a permanent ADR.
   This project's credibility is that it does not say things that are not so, and a PR is
   where that leaks in.
3. **Fix it on top, in named commits.** One commit per theme reads better than one per
   defect and much better than one big one.
4. **Anything no test could see becomes a test.** That is the rule the whole repository
   runs on: a stale count in a docstring, a promise the code does not keep, a key two files
   have to agree on. If the review found it by reading, the next one should find it by
   failing.
5. **Expect the review to surface older breakage.** Auditing 0.6's two contributions turned
   up six claims that had gone stale on `main` before either arrived. Fix those in the same
   release and say so in the CHANGELOG rather than leaving them for later.
6. **Reply properly and say thank you.** Somebody spent their evening on this.

## Reporting bugs and proposing verbs

Use the issue templates. `quackd doctor` output and the relevant `transcript.jsonl` lines
turn a vague bug into a fixable one.
