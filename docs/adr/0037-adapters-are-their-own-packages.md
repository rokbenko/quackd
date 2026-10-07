# ADR-0037: An adapter is its own package, and quackd ships no robot

**Status:** accepted, amended · **Date:** 2026-09-16 · Extends [ADR-0017](0017-robot-adapters-and-manifest.md) (a robot is an adapter that declares a manifest) and [ADR-0022](0022-per-adapter-upstream-refs.md) (each adapter owns its upstream refs) · Amends [ADR-0034](0034-registered-robots-and-pilot-flocks.md), which assumed there was always a body to fall back to · Documented in the README's install section and [CONTRIBUTING.md](../../CONTRIBUTING.md)

**Amended 2026-09-29 by [ADR-0049](0049-a-version-says-what-changed.md):** the eight still carry
one version and are still released together, and a release is now a patch, a minor or a major by
what it changes. `scripts/set_version.py` starts each window this ADR describes at the release
itself, `>=X.Y.Z,<X.Y+1`, so a patch raises every floor to itself and a minor moves the whole
window. The order a release is cut in, which the Consequences below put in PLAN.md, is in
[RELEASING.md](../../RELEASING.md).

## Context

[ADR-0017](0017-robot-adapters-and-manifest.md) said that adding a robot means one package under
`quackd/adapters/` with a manifest, a double, a doctor row and a docs page, and nothing in the
executor, the loop or the prompts. That held from 0.4 to 0.9 and through seven bodies. One word
in it was doing work it could not do: *package* meant *directory*, and a directory in this
wheel is something every user installs.

So `uv pip install quackd` installed seven robots. The extras bought the SDK a real backend
needed, never the adapter, so somebody who wanted the cartoon duck also got the LeRobot arm's
verbs, the rosbridge client, two ZeroMQ clients, two bridge clients and, since
[ADR-0030](0030-mujoco-physics-backend.md), a whole physics simulator for a robot they do not
own. Nothing in that is expensive to download. It is expensive to explain, and the explanation is
the install line: a reader who is told quackd is the loop and the body is something you connect
is then handed an install that connects six bodies they did not ask for.

The default made the same claim from the other end. `--robot` fell back to `microduck:sim2d`
everywhere, so every command that needed a body had one, and the question *which robot is this
for* never had to be answered. That was right when there was one robot. With seven it means a
person can run a task against a simulator they forgot they had and read the transcript as though
it were the robot on their desk. [ADR-0035](0035-one-cli-for-all-your-robots.md) made quackd the
CLI for all your robots; an install that ships all of them and picks one for you says something
else.

The third thing is the one that has no workaround at all. An adapter is a directory in this
repository, so a body nobody here owns can only be supported by a pull request to this
repository, reviewed by us, released on our schedule, and carried in everybody's wheel
afterwards. There was no way to publish an adapter for a robot without asking.

## Decision

**One repository, eight distributions, a uv workspace.** The core stays `quackd`. Each adapter
becomes its own package under `adapters/<name>/`, with its own `pyproject.toml`, its own
`__version__` and its source at `src/quackd_<name>/`: `quackd-microduck`, `quackd-lerobot`,
`quackd-rosbridge`, `quackd-open-duck`, `quackd-xlerobot`, `quackd-alohamini`,
`quackd-toddlerbot`. `[tool.uv.workspace]` and `[tool.uv.sources]` make them members, so one
checkout, one lock and one test suite still cover all eight. An adapter depends on the core with
a narrow window (`quackd>=0.17.0,<0.18`), and the core never imports an adapter.

**The extras are still the front door, and they now buy the robot.** `quackd[microduck]`,
`quackd[lerobot]`, `quackd[rosbridge]`, `quackd[open_duck]`, `quackd[xlerobot]`,
`quackd[alohamini]`, `quackd[toddlerbot]`, and `quackd[robots]` for all seven. Where a real
backend needs a third-party SDK, that SDK is the adapter's own `[sdk]` extra, so
`quackd[lerobot]` is `quackd-lerobot[sdk]` and the arm's Python 3.12 marker stays where the arm
is. `quackd[mujoco]` and `quackd[microduck-camera]` keep the names they had and resolve to
`quackd-microduck[mujoco]` and `quackd-microduck[camera]`. Nobody has to learn a new spelling.

**An installed adapter announces itself through the `quackd.adapters` entry point group.** One
entry per adapter, its name to its module, and that module carries `describe`, `make`,
`implementations` and `conditions`, which is the same surface `factory.py` already imported by
path. Discovery is the whole contract, so an adapter published by somebody else is found the same
way ours are and needs nothing from this repository.

**The core keeps a catalogue of the seven it publishes, and imports none of them.**
`quackd/adapters/catalogue.py` is strings: name, backends, status, summary, extra, SDK probe.
`quackd list-adapters` and `quackd doctor` render the whole table on a machine with no adapter
installed, each row marked `not installed`, and `parse_robot_spec` accepts a name quackd
publishes whether or not it is here, so a robot can be registered, listed and shown on a machine
that cannot build it. A third party's adapter has no catalogue row and is asked about itself
instead, which costs an import and is safe, because it is installed or the question fails anyway.

**There is no default body, with two exceptions that are about ambiguity rather than about
ducks.** With nothing installed, any command that needs a body refuses and names the install.
With exactly one adapter installed, that one is the default: a machine with one robot has no
ambiguity to resolve, and making its owner type the name is ceremony. With several installed and
the Microduck among them, `microduck:sim2d` is the default, because the six `duck: 0` starters
carry no `robots:` line and have always meant the cartoon. With several and no Microduck, quackd
lists what is installed and refuses to guess.

**What moved is what was only ever the duck's.** The `robotd` JSON-RPC transport, the WebSocket
stub, the MuJoCo backend, the transport factory, the `sim3d` physics simulator and the
`upstream_api.py` of [ADR-0006](0006-upstream-api-single-file.md) all live in `quackd-microduck`.
What stayed is the 2D cartoon arena of [ADR-0007](0007-sim2d-cartoon.md) (`quackd/sim2d/`) and
the mock transport (`quackd/transport/mock.py`), because they were never the duck's either: four
of the seven bodies run in that arena, the Microduck's mock backend is `MockTransport` itself,
and the other six subclass it and draw their frames with the 2D renderer. `UpstreamRef` moves to
`quackd/upstream.py`, because [ADR-0022](0022-per-adapter-upstream-refs.md) gave every adapter
its own refs file and left the type in the Microduck's, so citing an upstream meant importing a
duck.

## Consequences

- A release is eight wheels and eight sdists instead of one and one. They carry one version, set
  by `scripts/set_version.py X.Y.Z` in all eight places at once, and PyPI takes the core first,
  because every adapter depends on it and a resolver that meets `quackd-lerobot` before `quackd`
  has nothing to resolve against. [PLAN.md](../../PLAN.md) carries the order.
- Two CI gates hold what prose cannot. `uv lock --check` fails a job on a lock that has drifted,
  which is the failure that let `quackd[alohamini]` ship unlocked once. The `packaging` job
  builds every package, installs the core wheel alone, asserts `doctor` reports seven adapters
  and none of them installed, asserts a run refuses with `no robot adapter is installed`, then
  installs one adapter wheel and asserts that body's verbs appear. It runs against built wheels
  rather than the working tree, because a wheel is what somebody actually installs.
- **This is breaking for anybody who installed the bare package.** `uv pip install quackd`
  followed by `quackd run find-and-kick` was the first thing the README taught, and it now
  refuses. The migration is one word, `quackd[microduck]`, and the refusal says it.
- A third party can publish an adapter without a pull request here, which is the point and also
  the part with no evidence behind it: nobody outside this repository has written one yet, so the
  entry point group is a contract we have only exercised on our own seven.
- Contributors run `uv sync --extra dev`, which installs the core and all seven adapters as
  editable workspace members and not one robot SDK. An adapter's tests stay in `tests/`, because
  the suite drives every body against fakes and a mock and splitting it would buy nothing.
- A fork or an out-of-tree patch against `quackd.adapters.<name>` breaks: the import is
  `quackd_<name>` now. Nothing a user types changes, and no manifest, verb, datasheet or safety
  rule moved with the code.
- [ADR-0034](0034-registered-robots-and-pilot-flocks.md) is amended rather than replaced. A
  registered robot still names an `<adapter>:<backend>` spec and still parses on a machine that
  cannot build it, but a command with no `--robot` at all no longer resolves to a body by default,
  and a flock whose members name an adapter that is not installed fails before a run directory
  exists, with the extra to install named.
- Six of the seven bodies have still never run on hardware. Exactly one has, a LeRobot SO-101 arm
  on 2026-09-15. Splitting the wheels changed nothing about that, and it is worth saying here
  because a packaging decision is the kind of change that can look like progress on the robots.
