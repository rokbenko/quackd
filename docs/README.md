# Documentation

The [README](../README.md) installs quackd and runs a first task. These pages are everything after
that, sorted by what you came to do. What has run on a real robot and what has not is on one page,
[adapters/status.md](adapters/status.md), and each robot's own page says it again for that body.

## Start here

- [The first run on an SO-101 arm](adapters/lerobot/first-run.md): from an empty laptop to a
  wave, written for somebody who has never run quackd or LeRobot.
- [FAQ](faq.md): the simulators, models and providers, seeing what happened, real robots,
  driving it from somewhere else, security and privacy, what it can and cannot drive, and the
  name.
- [Safety](concepts/safety.md): read it before anything with a motor.

## Guides

How to do one thing with quackd.

| Page | What it covers |
|---|---|
| [Pilot your robots from Claude](guides/mcp.md) | `quackd serve-mcp`: a robot, or a flock of them, as MCP tools that Claude Code or Claude Desktop pilots |
| [Local and open-source LLMs](guides/local-llms.md) | Ollama, vLLM, llama.cpp, LM Studio and any other OpenAI-compatible server as the pilot |
| [Decision LLMs](guides/decision-llms/README.md) | the optional discrete stepper in front of the model, with a page for each one quackd names: [Jev](guides/decision-llms/jev.md), [Kev](guides/decision-llms/kev.md), [Von](guides/decision-llms/von.md), [OpenJev](guides/decision-llms/openjev.md), [OpenDecision](guides/decision-llms/opendecision.md), [local](guides/decision-llms/local.md) and [Laya](guides/decision-llms/laya.md) |
| [Policies](guides/policies.md) | a learned policy as the arm's executor, served by `quackd policy serve` |
| [Registered robots and flocks](guides/registry.md) | a name for a robot, and for a flock, kept between runs |
| [Memory](guides/memory.md) | what a robot keeps between runs |
| [Flock mode](guides/flock.md) | several robots on one task, as pilots or under a coordinator |
| [Robots on a LAN](guides/lan.md) | discovery and the MQTT bus |
| [An NVIDIA Jetson](guides/jetson.md) | a board quackd reaches with `--host` and never runs on |

## Robots

One folder per body under [adapters/](adapters/). A robot's page is what quackd knows about it:
what it is, what quackd can do with it, and its VERIFIED and UNVERIFIED tables, what quackd read
from upstream at a pinned commit and what it had to assume. Its hardware checklist is the order
to try a real one in.

| Robot | Its page | On a real one |
|---|---|---|
| Microduck | [adapters/microduck/README.md](adapters/microduck/README.md) | [hardware checklist](adapters/microduck/hardware-checklist.md) |
| LeRobot SO-101 arm | [adapters/lerobot/README.md](adapters/lerobot/README.md) | [hardware checklist](adapters/lerobot/hardware-checklist.md), [first run](adapters/lerobot/first-run.md) |
| rosbridge, a wheeled base over ROS 2 | [adapters/rosbridge/README.md](adapters/rosbridge/README.md) | none |
| Open Duck Mini v2 | [adapters/open_duck/README.md](adapters/open_duck/README.md) | [hardware checklist](adapters/open_duck/hardware-checklist.md) |
| XLeRobot | [adapters/xlerobot/README.md](adapters/xlerobot/README.md) | [hardware checklist](adapters/xlerobot/hardware-checklist.md) |
| AlohaMini | [adapters/alohamini/README.md](adapters/alohamini/README.md) | [hardware checklist](adapters/alohamini/hardware-checklist.md) |
| ToddlerBot | [adapters/toddlerbot/README.md](adapters/toddlerbot/README.md) | [hardware checklist](adapters/toddlerbot/hardware-checklist.md) |

- [Adapter status](adapters/status.md): what has run against its real target, and what has not.
- [Writing an adapter](adapters/writing-an-adapter.md): the recipe for a robot quackd does not
  know yet.
- [Reading someone else's robot](adapters/reading-robots.md): the traps that recur when you read
  a robot's code without running it.

## Reference

- [The `.duck` file](reference/duck-spec.md): the task format, spec v0 to v3. It is normative.
- [The robot manifest](reference/manifest-spec.md): what a connected robot is and can do, as data.
- [Licenses](reference/licenses.md): the eight distributions, every upstream they reach, and on
  what terms.

## Concepts

- [Architecture](concepts/architecture.md): the three loops, the modules, a turn, the transcript
  and the log.
- [Safety](concepts/safety.md): the layers and who owns each, the executor's rules, the heartbeat,
  the kill switch and the dry run, what to do on each body's hardware, and what quackd does not
  protect against.
- [Learned verbs](concepts/learned-verbs.md): a reserved extension point. Nothing in it runs yet.

## History

- [adr/](adr/): one record per decision, numbered, each with what was decided and why.
- [design/](design/): the design notes of 0.4, 0.5 and 0.6.

Both say what was true when they were written. A later decision amends an ADR with a note rather
than rewriting it.

## Also here

- [examples/lerobot/](examples/lerobot/README.md): the shoot-day task files for the SO-101 arm.
- [assets/](assets/README.md): the pictures and transcripts these pages show, and how each one
  was made.

How the project itself works is outside this folder: [CONTRIBUTING.md](../CONTRIBUTING.md),
[SECURITY.md](../SECURITY.md), [RELEASING.md](../RELEASING.md) and
[CHANGELOG.md](../CHANGELOG.md).

## Where a new page goes

- `guides/` for how to do one thing with quackd.
- `adapters/<name>/` for anything about one robot: `README.md` is its page, beside it its
  `hardware-checklist.md`.
- `reference/` for something you look up rather than read through: a format, a schema, the
  licences.
- `concepts/` for how quackd works, and why.
- `adr/` for a decision, numbered after the last one.

Then link it from this page. A test fails while any page under `guides/`, `adapters/`,
`reference/` or `concepts/` is missing from it.
