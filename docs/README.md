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
- [Models](models/README.md): which models quackd can fly a robot with, which can decide for
  it and which can drive the arm, and how to name each one.

## Guides

How to do one thing with quackd.

| Page | What it covers |
|---|---|
| [Driving a robot](guides/drive.md) | `quackd drive`: one robot driven a command at a time from the keyboard or a pipe, with an empty Enter as the brake |
| [The page: quackd ui](guides/web-ui.md) | a goal or a chat from a browser or a phone, with every option of `run` and `drive`, the robot's questions and a Stop, and how a phone reaches it |
| [Pilot your robots from Claude](guides/mcp.md) | `quackd serve-mcp`: a robot, or a flock of them, as MCP tools that Claude Code or Claude Desktop pilots |
| [Local and open-source LLMs](guides/local-llms.md) | Ollama, vLLM, llama.cpp, LM Studio and any other OpenAI-compatible server as the pilot |
| [OpenRouter](guides/openrouter.md) | one key for other vendors' models as the pilot: which ids quackd takes and refuses, where a prompt goes, and what a turn is billed |
| [Decision LLMs](guides/decision-llms/README.md) | the optional discrete stepper in front of the model, with a page for each one quackd names: [Jev](guides/decision-llms/jev.md), [OpenAI's Decisions API](guides/decision-llms/openai.md), [Kev](guides/decision-llms/kev.md), [Von](guides/decision-llms/von.md), [OpenJev](guides/decision-llms/openjev.md), [OpenDecision](guides/decision-llms/opendecision.md), [local](guides/decision-llms/local.md) and [Laya](guides/decision-llms/laya.md) |
| [Policies](guides/policies.md) | a learned policy as the arm's executor, served by `quackd policy serve` |
| [Registered robots and flocks](guides/registry.md) | a name for a robot, and for a flock, kept between runs |
| [Memory](guides/memory.md) | what a robot keeps between runs |
| [Exploring](guides/explore.md) | a duck in a simulated room exploring it and finding the ball, with the `find-the-ball` and `favourite-spot` starters, and checking a task against a room |
| [Places](guides/places.md) | naming a spot a robot stands on and walking back to it, in a simulated room with its own map or on a real duck's odometry, and `quackd map` and `quackd places` from a shell |
| [Playing a part](guides/play.md) | a task that gives the pilot a part to play, an animal, a person or a character, acted out with the body it has |
| [Flock mode](guides/flock.md) | several robots on one task, as pilots or under a coordinator |
| [Robots on a LAN](guides/lan.md) | discovery and the MQTT bus |
| [An NVIDIA Jetson](guides/jetson.md) | a board quackd reaches with `--host`, and the one `quackd onboard`, Robot Passport's service, runs on |

## Models

Robots have [adapters/](adapters/), and models have [models/](models/README.md): a page for
each of the three roles a model can have in a run and one for the companies behind them.
Their tables are rendered from the code by `scripts/models_docs.py`, so they say what this
build takes.

| Page | What it covers |
|---|---|
| [Models](models/README.md) | the three roles side by side, how one run uses all three, and how to choose |
| [Pilot LLMs](models/pilot-llms.md) | every vendor `--llm` takes and every model id, with its extra, its key, its address and its rate |
| [Decision LLMs](models/decision-llms.md) | every decision LLM `--decision-llm` names, where it runs, its key and its rate |
| [VLAs and the other learned policies](models/vlas.md) | the policy types the arm's policy server serves, where they run, and what is not served and why |
| [Who makes what](models/vendors.md) | each company and project, the roles it fills, and the key and the extra it needs |

How to set each one up is a guide: [local LLMs](guides/local-llms.md),
[OpenRouter](guides/openrouter.md), [decision LLMs](guides/decision-llms/README.md) and
[policies](guides/policies.md).

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
| Huebot | [adapters/huebot/README.md](adapters/huebot/README.md) | [hardware checklist](adapters/huebot/hardware-checklist.md) |
| MOSS, a tracked rover that picks up litter | [adapters/moss/README.md](adapters/moss/README.md) | [hardware checklist](adapters/moss/hardware-checklist.md) |

- [Adapter status](adapters/status.md): what has run against its real target, and what has not.
- [Writing an adapter](adapters/writing-an-adapter.md): the recipe for a robot quackd does not
  know yet.
- [Reading someone else's robot](adapters/reading-robots.md): the traps that recur when you read
  a robot's code without running it.

## Reference

- [The `.duck` file](reference/duck-spec.md): the task format, spec v0 to v4. It is normative.
- [The robot manifest](reference/manifest-spec.md): what a connected robot is and can do, as data.
- [Licenses](reference/licenses.md): the ten distributions, every upstream they reach, and on
  what terms.

## Concepts

- [Architecture](concepts/architecture.md): the three loops, the modules, a turn, the transcript
  and the log.
- [Safety](concepts/safety.md): the layers and who owns each, the executor's rules, the heartbeat,
  the kill switch and the dry run, what to do on each body's hardware, and what quackd does not
  protect against.
- [Places](concepts/places.md): where a robot says it is, the places it keeps under names, and
  why a place belongs to the frame it was marked in.
- [Learned verbs](concepts/learned-verbs.md): a reserved extension point. Nothing in it runs yet.

## Robot Passport

A small open protocol for two robots that have never met, from any maker, to say who they are, who
owns them, what they can and cannot do and how a person stops them, before their brains say a
word. Since 0.29.0 it runs on the robot it introduces, as `quackd onboard` on the robot's own
computer. It has a folder of its own, [passport/](passport/README.md), because it is written to
leave quackd one day, whole.

| Page | What it covers |
|---|---|
| [Robot Passport](passport/README.md) | the hub: the three layers (a Bluetooth beacon judged by signal strength, a passport its owner signs, talk over the LAN or a Bluetooth link), the status, and every page in reading order |
| [Why](passport/why.md) | the problem, the meetings it is for, why a robot carries its own passport, the principles, and why not something that exists |
| [How it works](passport/how-it-works.md) | one meeting step by step, what each side learns, a passport's life from the wizard to its expiry, and what happens when something fails |
| [Quickstart](passport/quickstart.md) | the three commands on a robot's own computer, and the demo on a laptop |
| [The passport](passport/passport.md) | the document, member by member, as 1.0 has it |
| [Discovery](passport/discovery.md) | the Bluetooth advert, its rotating beacon id and signal strength, the LAN when two robots share one, a Bluetooth link when they do not, and the fetch rules |
| [Trust](passport/trust.md) | the owner's signature, the owner key sealed on the robot, pinning and the tiers |
| [Talk](passport/talk.md) | the six messages, the three-step `hello` and its runtime block, courtesies, and the meeting task |
| [Security](passport/security.md) | the threat model |
| [Privacy](passport/privacy.md) | what each layer reveals, tracking and the radio's address, and what quackd keeps on the robot |
| [Which robots carry one](passport/compatibility.md) | the robot's own computer, a Jetson first, the board plugins and the `sim` board, and why arms and toys carry none |
| [In quackd](passport/quackd.md) | `quackd onboard`: the robot's file, the service, meetings, pins, the demo, and what has run |
| [Specification](passport/spec.md) | the normative text, version 1.0, with its [schemas](passport/schema/passport.schema.json) |
| [Test vectors](passport/vectors/README.md) | the conformance suite, each vector with its expected result |
| [Naming](passport/naming.md) | what the name means, and every identifier, the Bluetooth UUIDs among them |
| [Prior art](passport/prior-art.md) | what it borrowed and what it avoided |
| [Glossary](passport/glossary.md) | every term |
| [FAQ](passport/faq.md) | the questions people ask |
| [Roadmap](passport/roadmap.md) | the status, part by part: built, run on the `sim` board, run on a Jetson and run over a real radio, the phases and the decisions taken |

## History

- [adr/](adr/): one record per decision, numbered, each with what was decided and why.
- [design/](design/): the design notes of 0.4, 0.5 and 0.6, and Robot Passport's phase 7 plan,
  which 0.29.0 dropped with nothing of it built.

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
- `models/` for which models quackd names, a page for each role and one for the companies.
  Their tables are rendered from the code by `scripts/models_docs.py`, so a row is changed
  there and never in a table. How to set a model up is a guide.
- `passport/` for anything about Robot Passport, the protocol and quackd's part in it, kept in one
  folder so it can leave quackd whole.
- `adr/` for a decision, numbered after the last one.

Then link it from this page. A test fails while any page under `guides/`, `adapters/`,
`reference/`, `concepts/`, `models/` or `passport/` is missing from it.
