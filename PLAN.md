# PLAN.md — quackd

The task DAG per milestone. Updated as work lands. Decisions live in `docs/adr/`.

Legend: ✅ done · 🔨 in progress · ⬜ todo · ⏸ blocked (with reason)

## M0 — Identity & scaffold 🔨

- ✅ Verify name: PyPI `quackd` free (404, 2026-08-28); repo `rokbenko/quackd` owned → ADR-0002
- ✅ ADR-0001 language/tooling
- ✅ `pyproject.toml` (uv, hatchling, extras: anthropic/openai/gemini/grok/all/yolo/live/dev; 0.4 adds reachy/lerobot/rosbridge/lan)
- ✅ Package skeleton with "why it exists" docstrings; `quackd --help`, `--version`
- ✅ LICENSE (Apache-2.0), NOTICE, CODE_OF_CONDUCT, SECURITY, CHANGELOG, .env.example, .gitignore
- ✅ CI (ruff · mypy · pytest · validate ducks; 3.11/3.12 × ubuntu/macos), pre-commit, dependabot, issue/PR templates
- ✅ `uv sync --extra dev` green locally; `mcp` tree ≈ 15 MB → stays core (ADR-0009)
- ✅ Five starter `.duck` files written (needed by the wheel's force-include; validated in M1)
- ✅ Commit `chore: scaffold quackd v0.1.0 skeleton (M0)`

## M1 — Contract & registry ✅

- ✅ `duckfile/schema.py` (pydantic), `parser.py`, `schema.json` export (`python -m quackd.duckfile.export`), `quackd validate`
- ✅ `verbs/registry.py`, `builtin.py`, `composite.py` (registered stubs → M2), `learned.py` (interface only)
- ✅ `transport/base.py`, `mock.py`, `upstream_api.py` (VERIFIED/UNVERIFIED, from duck-ipc-proto API v16)
- ✅ `safety.py`: Budget, Executor (allowlist · confirm · dry-run · machine-enforced abort_when), Heartbeat, KillSwitch
- ✅ `agent/loop.py`, `prompts.py`, `providers/{base,fake,factory}.py`, `transcript.py`
- ✅ Five starter `.duck` files validate
- ✅ Tests (61): parser + invalid fixtures, schema sync, registry, learned dummy, executor rules, heartbeat, loop golden, CLI
- ✅ ADR-0003…0006, 0011, 0012
- ✅ ✅-criterion: `quackd run hello-world --provider fake --transport mock` writes a transcript + summary

## M2 — The world ✅

- ✅ `sim2d/world.py` (20 Hz, seeded noise, deadman, kick cone, unreliable scoop), `render.py` (top-down + perspective duck-cam), `recorder.py` (GIF via tick hook), `live.py` (optional pygame)
- ✅ `transport/sim2d.py`; `perception/color_blob.py` (HSV + bearing/distance geometry), `yolo.py` (lazy extra)
- ✅ Composite `search_scan`, `walk_to` (10 Hz closed loop), `approach_and`; FakeLLM find-and-kick strategy
- ✅ `quackd record`, `quackd list-verbs`
- ✅ Acceptance: find-and-kick succeeds on **10/10** seeds 0–9 (ground truth checked), ~1–2 s wall-clock each; `run.gif` in `runs/`
- ✅ ADR-0007, ADR-0008; 83 tests

## M3 — The brain ✅

- ✅ Providers: anthropic (Messages API, adaptive thinking default, one tool call via `tool_choice any + disable_parallel_tool_use`, thinking blocks replayed, refusal handling, server-side fallbacks with SDK-age fallback), openai, grok (xAI endpoint), gemini; lazy imports, clear missing-extra / missing-key errors
- ✅ Prompts carry the contract; confirm gates (`typer.confirm`, `--yes`), budgets, `--dry-run` live in the CLI
- ✅ Offline provider tests against stubbed clients (request mapping, response parsing, refusal, error chain); 98 tests
- ✅ Hero GIF `docs/assets/hero.gif` + `transcript-example.jsonl` — ADR-0013: **scripted-pilot recording, labelled**
- ⏸ Real-provider hero recording — blocked on an API key. Unblock: `quackd record find-and-kick --provider anthropic --seed 3`, copy `run.gif` + `transcript.jsonl` into `docs/assets/`, drop the label
- ⏸ Verify non-Anthropic default model IDs (`gpt-5`, `grok-4`, `gemini-2.5-pro`) against vendor docs; all overridable via `QUACKD_MODEL`

## M4 — The socket ✅

- ✅ `mcp_server.py` (MCP SDK v2 `MCPServer`, stdio, lifespan-managed transport + heartbeat), eight `duck_*` tools through the shared Executor, `duck_get_frame` returns `Image` content; `--yes` for confirm gates
- ✅ `transport/jsonrpc_unix.py` (EXPERIMENTAL: hello handshake with API-version check, NDJSON, `robot.move` notifications, `robot.health` heartbeat, `unix://` + `tcp://` addresses) + fake-robotd TCP tests
- ✅ `transport/websocket_stub.py` (STUB that points at upstream's draft)
- ✅ `quackd doctor` (core deps, providers/keys masked, extras, transports, UNVERIFIED assumptions)
- ✅ `docs/mcp.md` with verified Claude Code (`claude mcp add`, `.mcp.json`) and Claude Desktop config; 2-minute script; Windows note
- ✅ In-process MCP client tests over memory streams (tool list, image content, contract enforcement, budgets, dry-run, confirm gate)

## M5 — The launch surface ✅

- ✅ README per brief §7 (hero GIF, quickstart, three loops + Mermaid, provider matrix, `.duck` in 20 lines, status table, roadmap, credits, safety, disclaimer)
- ✅ LAUNCH.md per §8; CONTRIBUTING.md (add a verb / submit a duck); project `.mcp.json`
- ✅ docs: architecture, duck-spec, transport-status, safety, learned-verbs, licenses, faq, mcp
- ✅ `tests/test_docs.py` keeps transport-status.md and README in sync with the code
- ✅ CHANGELOG 0.1.0; tag `v0.1.0`
- ✅ Definition of done: `uvx quackd run find-and-kick --provider fake` from README alone; `tests/test_upstream_api.py` proves no UNVERIFIED ref is reachable outside `jsonrpc`/`websocket`/`doctor`

## M6 — The first robot you can actually build ✅

- ✅ `open_duck` adapter (ADR-0024): manifest, verbs, `sim2d` and `mock`; `kick` `grab`
  `sit` `stand` `stand_up` never declared, because this robot has none of them
- ✅ `fix(cli)`: `run` validates a `.duck` against its robot before connecting, instead of
  raising a bare `VerbNotFound` mid-run and leaving an empty run directory
- ✅ `open-duck-scout` 10 of 10 seeds, `open-duck-lookout` (moves no legs, for bring-up)
- ✅ `open_duck:bridge` and `bridge/open_duck/`, the first quackd code that runs on a robot:
  upstream's own walk loop with its gamepad class rebound to a socket, a deadman evaluated
  by the control loop, head control off by default, protocol exercised end to end over
  loopback against the real daemon
- ✅ Docs: ADR-0024, `docs/adapters/open_duck.md`, the hardware checklist, the issue
  template, licences and NOTICE for two upstreams (one of which has no LICENSE file)
- ⏸ Only a human can: run `open_duck:bridge` against a duck they built, work the checklist
  in `docs/open-duck-hardware-checklist.md`, and confirm the deadman by pulling Wi-Fi
  mid-walk. Flip the `bridge` row in `docs/adapter-status.md` only after
- ✅ 0.5 docs pass: README leads with the buildable robot and gains a `Which robots work`
  table, SECURITY covers the two on-robot services, three claims that had become false are
  corrected, and every command in the Open Duck docs was run before it shipped
- ✅ `--transport` and the eight `duck_*` MCP tools removed, as 0.4 promised in ten places
- ⬜ Flock mode does not know `open_duck` yet (`flock/runner.py` knows two adapters)

## Open after v0.1.0

- ⏸ Real-model hero recording (needs an API key) — `quackd record find-and-kick --provider anthropic --seed 3`
- ⏸ Verify `gpt-5` / `grok-4` / `gemini-2.5-pro` default IDs against vendor docs
- ⏸ Run `--robot microduck:jsonrpc` against a real Microduck (Christmas 2026) and flip its rows in `docs/adapter-status.md` (see the human-only list below, which covers all five adapters)
- ✅ Published `quackd 0.1.0` to PyPI (2026-08-28); `uvx quackd --version` resolves
- ✅ v0.2.0 (2026-08-29): local and open-source LLM providers, `--goal`, README rewrite, logo
- ⏸ v0.2.0 PyPI publish needs `UV_PUBLISH_TOKEN` again (the line was removed from `.env` after 0.1.0)
- 🔨 First transcript from a live local server (Ollama, vLLM, llama.cpp) — still none on the
  dev machine, but PR #5's contributor reports `find-and-kick` against Qwen 2.5 Coder 14B
  through LM Studio on seeds 5 and 6, both successes with memory read and written. No
  transcript from it is in the repository, so the README says exactly that
- ✅ v0.3.0 (2026-08-31): flock mode — multi-duck sim, lockstep clock, in-process bus, Contract Net auction, one planner LLM call, 10/10 seeded acceptance (ADR-0015/0016); hardened by a 69-agent adversarial review, 24 confirmed findings fixed pre-release
- ⏸ Flock future work: hardware flocks when Microducks ship, real-provider planner recording
- ✅ v0.4.0 (2026-09-02): "a brain for any small robot" — robot adapters and manifests (ADR-0017/0018), `.duck` v1 with `requires` and `robots` (ADR-0019), the Reachy Mini adapter (ADR-0023), heterogeneous flocks with `reachy-spots-duck-kicks` 10/10 (ADR-0020), multi-robot MCP (`--robots`, six `robot_*` tools), zeroconf discovery and an MQTT bus behind `quackd[lan]` (ADR-0021), LeRobot and rosbridge adapters (ADR-0022); 360 tests collected, still offline, four seeded sweeps at 10 of 10
- ✅ Published `quackd 0.4.0` to PyPI (2026-09-02), tagged `v0.4.0` (annotated), GitHub Release `v0.4.0 "adapters"` created on `main` with the wheel and sdist attached, About description and Topics updated for four bodies
- ⏸ Only a human can: run `reachy_mini:sdk` against a Reachy Mini (or `reachy-mini-daemon --mockup-sim`), `lerobot:real` against an SO-101, `rosbridge:ws` against a bridge, `microduck:jsonrpc` against a robotd; a flock across two machines needs a distributed clock first; flip rows in `docs/adapter-status.md` only after
- ✅ Pushed `main` + `v0.1.0`; repo public; About/Topics/homepage set; GitHub Release created
- ✅ v0.5.0 (2026-09-03): the Open Duck Mini v2, the first robot anyone can build (ADR-0024,
  [design](docs/design/open-duck.md)); the first quackd code that runs on a robot; four
  hardware-path blockers fixed; `--transport` and the `duck_*` tools removed as promised;
  457 tests, five seeded sweeps at 10 of 10, still offline
- ⏸ Only a human can: run `open_duck:bridge` against a duck they built, work the checklist
  in `docs/open-duck-hardware-checklist.md`, and confirm the deadman by pulling Wi-Fi
  mid-walk. Flip the `bridge` row in `docs/adapter-status.md` only after
- ✅ Tagged `v0.5.0` (annotated) and pushed `main`, GitHub Release
  `v0.5.0 "open duck"` created on `main` with the wheel and sdist attached (2026-09-03).
  A pre-release audit of the note against the code fixed a half-applied detector fix, two
  commands still advertising `--transport`, and a PyPI summary with no Open Duck in it
- ✅ Published `quackd 0.5.0` to PyPI (2026-09-03), the same two files attached to the
  release; `uvx quackd run open-duck-scout --provider fake` verified from a clean install.
  About description and Topics updated for five bodies (`open-duck-mini` and
  `bipedal-robot` in, `python` and `llama-cpp` out, at GitHub's cap of 20)
- ✅ v0.6.0 (2026-09-04): memory between runs (ADR-0025, [docs/memory.md](docs/memory.md)),
  and the first release assembled out of other people's contributions rather than written
  here. One JSONL file per `adapter:backend` holds the notes a pilot saves with `remember`
  and one line per earlier run, and the newest of both are in the prompt before the first
  observation; `quackd memory show|add|clear`, `--no-memory`, `--memory-dir`, and
  `robot_recall`/`robot_remember` taking the MCP surface to eight tools. Also: `max_minutes`
  is now enforced against a provider that answers after the deadline, the README's 53
  relative links are absolutised at build time so the PyPI page resolves them, and the two
  CI actions move off deprecated Node 20. Reviewing the two contributions found fifteen
  defects, two of them blockers, none of which their green checklists could see. 500 tests,
  five seeded sweeps at 10 of 10, still offline
- ⏸ Only a human can: exercise `remember` against a cloud model. The scripted pilot has no
  script for it, so `--provider fake` writes episodes and never a note, and the only
  evidence a model uses the tool is the contributor's Qwen 2.5 Coder 14B runs
- ✅ Tagged `v0.6.0` (annotated) and pushed `main`, GitHub Release `v0.6.0 "memory"` created
  on `main` with the wheel and sdist attached (2026-09-04). PRs #3 and #5 were merged with
  `git merge`, so both contributors' commits are on `main` under their own names and the
  fifteen corrections sit in six commits after the merges. A pre-release review by 179
  agents found those fifteen, two of them blockers, plus six claims already stale on `main`
- ✅ Published `quackd 0.6.0` to PyPI (2026-09-04), the same two files attached to the
  release (SHA256 checked identical in both places); `uvx --from quackd==0.6.0 quackd run
  find-and-kick --provider fake` verified from a clean install, twice, so the second run
  reads the first one's episode. The PyPI long description now carries 0 relative links
  and 60 rewritten ones, which is the first release whose project page links resolve
- ✅ About description gained "memory between runs" (dropping "Apache 2.0.", which GitHub
  already renders in that sidebar, to stay under the 350 character cap). Topics unchanged
  at GitHub's cap of 20: memory is a feature, not a change to what quackd is
- ✅ The README's Contributing section shows everyone who has contributed, humans only,
  ordered by lines added from `git log`, regenerated by `.github/workflows/contributors.yml`
  and committed only when the people or their avatars change
- ⏸ Upload `docs/assets/social-preview.png` under Settings → Social preview (no API for it)
