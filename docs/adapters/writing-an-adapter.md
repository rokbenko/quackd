# Adapters: write one in a day

An adapter is how a robot joins quackd. It answers one question, "what is this body and
what can it do", as a `RobotManifest`, and it moves the body through intents the robot's
own controllers execute. Everything else (the loop, the executor, the `.duck` contract,
the MCP server) is shared. quackd ships seven: `microduck`,
`lerobot`, `rosbridge`, `open_duck`, `xlerobot`, `alohamini` and `toddlerbot`. This page is the recipe; [ADR-0017](adr/0017-robot-adapters-and-manifest.md),
[ADR-0018](adr/0018-core-verbs-extensions-aliases.md) and
[ADR-0022](adr/0022-per-adapter-upstream-refs.md) are the reasons.

An adapter is its own distribution. `uv pip install quackd` installs the loop and no robot,
and each body arrives as a package of its own: `quackd-microduck`, `quackd-lerobot`,
`quackd-rosbridge`, `quackd-open-duck`, `quackd-xlerobot`, `quackd-alohamini`,
`quackd-toddlerbot`, each fetched by the extra you already type (`quackd[lerobot]`, and
`quackd[robots]` for all seven). An installed package announces itself through the
`quackd.adapters` entry point group, and that group is the only way the core finds one.

> [!NOTE]
> That entry point is the whole of the contract, so the seven above have no privileges you
> do not have. A robot quackd has never heard of can be published to PyPI by anyone, and
> `quackd list-adapters` finds it the moment it is installed, listed after the seven. The
> rest of this page is written for an adapter that lands in this repository, because that is
> the longer version; [publishing one yourself](#publishing-one-yourself) says which of it
> you can skip.

## The shape

```
adapters/<name>/
  pyproject.toml    # the distribution: its name, the core as a dependency, the entry point
  README.md         # what this body is, in a screen, and the extra that installs it
  src/quackd_<name>/
    __init__.py     # the manifest, the adapter class, and the four functions the factory calls
    verbs.py        # extension verbs and named preconditions (only if the robot has any)
    mock.py         # a backend that runs offline and does what the test says
    <real>.py       # the SDK backend, EXPERIMENTAL until run against the target
    upstream_api.py # every SDK name you spell, VERIFIED or UNVERIFIED, with a pinned link
docs/adapters/<name>.md
tests/test_<name>_adapter.py
```

The import name is `quackd_<name>`, with an underscore, and the distribution name is
`quackd-<name>`, with a hyphen. Nothing is imported as `quackd.adapters.<name>` any more:
that package holds only what every adapter shares, which is `base.py`, `manifest.py`,
`catalogue.py` and `factory.py`.

### The packaging, from a real one

[`adapters/lerobot/pyproject.toml`](../adapters/lerobot/pyproject.toml) is the shortest
complete example, and every line of it is load-bearing:

```toml
[project]
name = "quackd-lerobot"
dynamic = ["version"]
description = "The LeRobot SO-101 arm adapter for quackd. Install it as quackd[lerobot]."
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.11"
dependencies = ["quackd>=0.16.1,<0.17"]

[project.optional-dependencies]
sdk = ["lerobot[feetech]>=0.6; python_version >= '3.12'"]

[project.entry-points."quackd.adapters"]
lerobot = "quackd_lerobot"

[tool.hatch.version]
path = "src/quackd_lerobot/__init__.py"

[tool.hatch.build.targets.wheel]
packages = ["src/quackd_lerobot"]

[tool.uv.sources]
quackd = { workspace = true }
```

| Line | Why it is that way |
|---|---|
| `dependencies = ["quackd>=0.16.1,<0.17"]` | the core is the dependency, never the other way round. The window is narrow because the manifest model and the intent vocabulary are the interface, and they move with the core, only ever in a minor, so an adapter published from somewhere else allows the whole minor, `quackd>=X.Y,<X.Y+1`, and takes every patch of it. quackd's own seven start the window at the release they ship in instead, `quackd>=X.Y.Z,<X.Y+1`, so one from a patch never installs beside a core from before it ([RELEASING.md](../RELEASING.md)) |
| `[project.optional-dependencies] sdk` | the library the real backend imports, and only that backend. A machine without it still gets `lerobot:mock`, still validates a `.duck` against the arm and still prints it in `list-adapters`. `quackd[lerobot]` in the core pins `quackd-lerobot[sdk]`, so the extra a reader types buys both halves. An adapter whose robot side you ship yourself declares no `sdk` at all: `quackd-open-duck` and `quackd-toddlerbot` have none |
| the environment marker | LeRobot needs Python 3.12 and pulls torch. The marker is what keeps the lock solvable on 3.11, where this package still installs and the mock still runs |
| `[project.entry-points."quackd.adapters"]` | `lerobot = "quackd_lerobot"` is the robot's name mapped to the module carrying `describe`, `make`, `implementations` and `conditions`. This is how quackd finds it, and the only way it finds a third party's |
| `[tool.hatch.version] path` | the version lives in `src/quackd_lerobot/__init__.py` as `__version__`, because an adapter's sdist holds only its own source and cannot read the core's. `scripts/set_version.py X.Y.Z` writes all eight at once |
| `[tool.uv.sources] quackd = { workspace = true }` | **in this repository only.** It points the dependency at the checkout instead of PyPI, which is what makes `uv sync --extra dev` install the core and all seven editable. An adapter published from somewhere else deletes this block and depends on the released core |

The `README.md` is the package's PyPI page. Keep it to what the body is, the one command
that installs it, what has actually been run against hardware, and a link to the full page
under `docs/adapters/`.

Some robots have no network API to talk to at all. The Open Duck Mini v2 is one: its
runtime reads a local gamepad and nothing else, so the adapter needed a companion daemon
that runs on the robot, in `bridge/<name>/` at the repository root rather than inside the
adapter package. Three rules if you find yourself there. It
must never import quackd, because quackd's dependencies do not belong on a 512 MB board.
It ships in the core's sdist and in no wheel at all, because a Raspberry Pi copies the one
file it needs rather than installing anything. And it
should be testable with no hardware, which in practice means a `--fake` mode and a pure
core the tests can drive directly.

The four module functions, the same on every adapter package:

```python
# static: no SDK import, no socket
def describe(backend: str, robot_id: str | None = None) -> RobotManifest: ...


# extension verbs and core overrides, keyed by canonical name
def implementations() -> dict[str, Verb]: ...


# named predicates the manifest references
def conditions() -> dict[str, Precondition]: ...


# the backend, imported lazily
def make(
    backend: str,
    *,
    robot_id=None,
    seed=None,
    address=None,
    live=False,
    camera_url=None,  # one url, a sequence of them, or None
    token=None,
    rest_pose=None,  # {joint: degrees} read off the body, or None
) -> RobotAdapter: ...
```

`describe()` is what `quackd validate --robot`, `quackd list-verbs --robot`, `quackd
announce` and `doctor` use, so it must never import an SDK. `make()` imports the backend
module lazily. Declare the entry point, install the package, and the name works everywhere
`--robot` does: `quackd.adapters.factory` reads the group, checks the module is really
importable (an editable install keeps metadata for a module somebody has since moved) and
asks yours for the body.

The seven `make()` keywords are the same on every adapter, because the factory calls every
one of them the same way. Accept all seven even where the body has nothing to do with one:
the Microduck takes `token` and does not use it, since `robotd`'s socket has nothing to
authenticate to. Two of them arrive in a shape your body may not want, and both have a
helper in `quackd/adapters/base.py` that answers for you:

| Keyword | What arrives | What you do |
|---|---|---|
| `camera_url` | whatever `--camera-url` was given, as a tuple, because the flag repeats | `one_camera_url(camera_url, spec=...)` for a body with one camera: it returns the url or refuses the second with a message naming who takes several. A body that genuinely reads more (today that is `lerobot:real` and its simulator `lerobot:mujoco`, and `MULTI_CAMERA_SPECS` is the list) keeps the tuple and implements `get_frames()` |
| `rest_pose` | degrees per joint, from the registry, for a body that parks | drive to it, or `refuse_rest_pose(name, rest_pose)`, which raises when one is present. Never accept it and ignore it |

### Two more the module may declare, both for `doctor`

The four functions above are what the factory needs. `quackd doctor` looks for two more, and
an adapter may leave both out: a body with no upstream to cite and nothing local to probe
simply contributes no rows.

**`UPSTREAMS`** is what this adapter reads from somebody else's project: a tuple of
`(name, its upstream_api module, the doc that explains it, what it has or has not been run against)`.
Doctor walks every installed adapter, collects the rows, and prints each upstream's pin, the
date it was read, how many refs are VERIFIED and how many are not, and the never-run label
verbatim. It lives in the adapter rather than in a table in the core because the list belongs
to whoever wrote the adapter ([ADR-0022](adr/0022-per-adapter-upstream-refs.md)), and the core
cannot carry a row for a package it has never heard of. The import is deferred inside a
function so that naming the upstream costs nothing until doctor asks. The arm's, whole:

```python
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_lerobot import upstream_api

    return (("lerobot", upstream_api, "docs/adapters/lerobot.md", "an arm (the real backend)"),)


UPSTREAMS = _upstream_rows()
```

**`doctor_rows()`** is optional and answers a question about this machine rather than about
the robot: whether the socket is where it should be, whether the host process is answering,
whether upstream shipped the piece a backend needs. Return one
`quackd.doctor.TransportRow(name, status, note, found)` per backend worth probing here and
doctor prints them in its transports section, with `found` deciding whether the note reads as
a finding or as an absence. Import the type inside the function: an adapter must not pull in
a diagnostics command to be describable.

> [!CAUTION]
> Doctor reads both of these inside a `contextlib.suppress(Exception)`, because a
> diagnostics command that crashes on somebody's adapter is worse than one that is quiet
> about it. A row that raises therefore costs you its own line and nothing says so. If your
> upstream pin or your probe is missing from `quackd doctor`, that silence is the bug.

## The manifest decides what exists

A verb that is not in the manifest does not exist: not in the registry, not in the MCP
tool list, not in `.duck` validation, not in the prompt. So the manifest is where honesty
lives. The rules, enforced by the model itself ([manifest-spec.md](manifest-spec.md)):

- **Core verbs need what they need.** `observe` needs a camera; `move` and `go_to` need
  the `twist` intent and mobility; `search_scan` needs a camera and either `twist` or
  `gaze`; `say` needs `sound`. Declare a core verb the body cannot support and the
  manifest refuses to build.
- **Extension verbs are the robot's own** (`kick`, `express`, `move_joints`). Declare
  them with `verb_spec(verb, core=False)` and supply the implementation from
  `implementations()`. Reusing a name another robot uses (`move_joints` on the LeRobot arm
  and on the XLeRobot) is how `requires: [move_joints]` is satisfied by both. The feasibility
  gate then has to know which of them may run before the pilot has judged the task. A verb
  shipped in this repository is classified in `quackd/verdict.py`, as one that looks, speaks
  or brakes (`BEFORE_VERDICT`) or as one that moves the body and waits for a feasible verdict
  (`MOVES_THE_BODY`), and a test refuses to pass until every shipped verb is in one or the
  other. **A verb quackd never ships cannot be put in either set** (that test rejects a name
  no shipped adapter offers), so it waits for the verdict unless its `Verb` carries
  `read_only=True`. That flag is you saying the verb sends no intent, and the gate lets it
  through for the same reason it lets `observe` through: a pilot has to be able to `locate`
  the thing before it can judge whether this body could reach it.
- **`stop` is universal**: present on every manifest, always allowed, never gated.
- **Aliases are not yours to declare.** `get_frame`, `walk_to` and `walk` live in
  `quackd/verbs/aliases.py`; a manifest names the canonical verb.
- **Preconditions are names**, with the predicate supplied by the adapter's
  `conditions()`: `{"kick": ["standing"]}` means the executor asks your `standing(state)`
  before every kick.
- **`safety_authority` says who stops the body when quackd goes quiet.** `native: none,
  deadman: false` is a legitimate answer; a wrong `deadman: true` is not.
- **`limits`** are what the core verbs clamp to (`max_vx`, `max_vy`, `max_wz`,
  `gaze_yaw_deg`); leave one out and the schema bound applies.
- **The `datasheet` is the body as numbers**, so a pilot can refuse a task before anything
  moves: what it weighs, carries and reaches, what it holds with, what it is rated for and
  what it cannot do whatever the task says. Every figure carries a confidence and a source,
  and a figure the maker never published is left out rather than guessed at, because the
  prompt renders an absent one as "not published" and tells the pilot to answer `uncertain`
  where a task turns on it ([manifest-spec.md](manifest-spec.md#the-datasheet)). The same
  sheet describes the body on every backend, which is part of why `digest()` matches across
  them.
- **`digest()`** is the capability fingerprint discovery advertises; it ignores `id` and
  `backend`, so the same robot over `sim2d` and `mock` hashes the same.

> [!WARNING]
> `read_only` is taken on trust, twice. A verb carrying it runs before the pilot has judged
> the task, and runs under `--dry-run` against real hardware, so the flag on a verb that
> actually sends an intent defeats both gates at once. Nothing can check it for you: put it on
> a sensor and on nothing else.

## The adapter class

A `RobotAdapter` is a `DuckTransport` plus self-description. Wrap your backend and
delegate: `connect()` returns the manifest, `disconnect()`/`close()` release it,
`get_state()` returns a `DuckState` (`posture="unknown"` is fine for a body without
postures; `holding` is for grippers), `get_frame()` returns a PIL image or `None`,
`send_intent()` maps an `Intent` to the SDK, `health()` is informational and never
raises, `heartbeat()` is the watchdog and raises `HeartbeatError`. Copy
`adapters/rosbridge/src/quackd_rosbridge/__init__.py` for the smallest complete example.

Two more are optional, and a body without them is the common case. `get_frames()` returns a
`CameraFrame` per camera with exactly one marked `primary=True`, and only a body that reads
several needs it: callers ask through `frames_of(transport)`, which falls back to `get_frame()`
wrapped as a single frame, so one camera and four are read the same way. `go_to_rest()` drives
the body to the pose it was built with and answers a `RestResult` rather than raising, because
every caller is a teardown or the first moment of a run and a teardown that raised would cost
the body its disconnect. A body quackd does not park has neither.

Intents are the whole vocabulary between verbs and backends: `move` (a twist), `look`
(a gaze point), `sound`, `do` (a named skill, `antennas:wiggle`, `policy:pick:cup`),
`joint`, `gripper`, `enable`, `pose`, `stop`. A backend answers each with an `Ack`; a
refusal is data (`accepted=False, reason=...`), never an exception.

## Backends: mock first, then whichever route the robot leaves you

Write `mock` before anything else. It runs offline, records intents, serves a synthetic frame
if the body has a camera, and lets every verb, every executor gate and the detector run in the
test suite.

**A fake must never be kinder than the robot.** If the real backend cannot report a position,
the mock reports `None` too, even though it knows where it is. If a real stop cannot hold the
arms, the mock's cannot either. A mock that is easier than the body is a task that passes here
and fails there, and it is the only kind of bug this repository cannot catch for you.

Then the real backend, and the shape of that depends entirely on what the robot gives you.
Two of the seven adapters import an SDK. The others could not.

| Route | When | Who does it | What it costs |
|---|---|---|---|
| **Import the SDK** | upstream ships an installable package with a client | `lerobot:real`, `rosbridge:ws` | an optional extra, a lazy import inside `connect()`, a lock around a synchronous SDK |
| **Speak its wire** | upstream ships a host process but is not installable, or installing it would drag in torch and a Python floor | `xlerobot:zmq`, `alohamini:zmq` | reading the wire from source rather than the docs, and owning the framing yourself |
| **Ship the robot side** | upstream has no network API of any kind | `open_duck:bridge`, `toddlerbot:bridge` | a daemon in `bridge/`, a protocol you define at both ends, and everything below |

Whichever route: import upstream **inside `connect()`** and raise
`AdapterNotInstalled(name, "quackd[extra]")` on `ImportError`, so a machine without the extra
still validates, lists and mocks the robot. It prints as
`adapter 'lerobot' needs an extra: uv pip install 'quackd[lerobot]'`, which is the whole of
what a reader has to do about it. Serialise access under one lock in a worker thread
with a deadline unless you have read that it is thread-safe. Take injectable clients
(`client=`, `robot=`, `ros=`) so the tests drive the mapping with fakes. The library goes in
your own `[project.optional-dependencies] sdk`; for an adapter quackd publishes, the
`quackd[<name>]` extra in the core's `pyproject.toml` then points at `quackd-<name>[sdk]`, you
run `uv lock`, and the module gets a row in `doctor.py`'s `EXTRAS` so that `quackd doctor`
says whether it is here. An adapter whose
robot side you ship needs no extra at all: `open_duck` and `toddlerbot` declare none.

**And never send the body's go-limp call.** `disable_motors`, `disable_torque`, `relax`, an
XLeRobot `disconnect()`: stop means stop, not collapse. This applies to teardown as much as to
`stop`, and upstream's own `disconnect()` is usually where the trap is — four of these robots
disable torque inside it (three by default, the ToddlerBot always), so `close()` has to stop
and hold rather than delegate.

The one exception is a body that has been put somewhere it can be let go of. A LeRobot arm
with a recorded rest pose is driven there first, or to the edge of its calibrated travel where
the pose lies past it, and only then is upstream's own torque-off allowed to happen; an arm
that did not reach the pose has that flag turned off and is left with whatever torque it has,
with one line saying so ([safety.md](safety.md)). That is the shape any
other body would have to take to earn a `go_to_rest()`: a pose the body holds with the power
off, checked before anything is released, and a refusal to release when it is not there.

The other exception is a person holding the arm who asks. `quackd run --by-hand` releases a
LeRobot arm at its rest pose so the person can set the start, and `quackd robot release`, or
Enter at the offer a run makes when its last rest move missed, releases it wherever it stands.
None of them is a verb, an MCP tool or a method on the `RobotAdapter` protocol, so no pilot can
reach them ([adapters/lerobot.md](adapters/lerobot.md#the-torque-rule)).

### If you speak a wire

You own the framing, so you own the failure modes that come with it.

- **Nothing on a wire is timestamped unless you make it so.** Stamp on arrival, expose the age
  in `extras`, and turn "no observation lately" into a `HeartbeatError`, or a cached reading
  will be served as a fresh one and a stopped robot will look like a moving one.
- **A socket may drop your older message.** ZeroMQ's `CONFLATE` keeps only the newest, so two
  intents in one tick become one. quackd's answer on both ZeroMQ bodies is a single writer
  that re-sends the whole desired action, rather than a mirror of the robot's state.
- **A partial payload can mean something else entirely.** The AlohaMini's driver indexes three
  velocity keys with no `.get()`, so omitting one discards the whole action, arms included.
  Route every verb through one payload builder rather than composing dicts at each call site.
- **Refusal is data.** Whatever the socket raises when the host dies, the pilot should get an
  `Ack(accepted=False)` naming the address, not an exception through the executor's catch-all.

### If you ship the robot side

What the three rules above actually cost, beyond writing it:

- **A handshake that reports what is really there**, and a `connect()` that narrows the
  manifest from the answer. A capability the daemon reports is a verb quackd will offer, so it
  must report what loaded rather than what a flag claimed.
- **A protocol version, and a refusal on mismatch.** The daemon on the robot is one somebody
  installed months ago.
- **Whatever the robot's own runtime does not do.** The Open Duck's daemon feeds a loop that
  already exists. The ToddlerBot's owns the loop, because upstream's `step()` is a no-op, and
  that difference is most of the size difference between the two.
- **A keepalive, if silence means something.** Where the robot's deadman fires on silence,
  something has to say the client is still there while a long verb runs, because the executor
  sends one command and then waits.

### A simulator, if the cartoon world already draws the body

Four of the seven bodies here ship a `sim2d` backend. The arena itself stayed in the core
(`quackd/sim2d/`, with `quackd/transport/mock.py` beside it) when the robots moved out into
their own packages, because neither was ever one robot's: the Microduck uses `Sim2DTransport`
as it is and the other three subclass it, and six of the seven mocks draw their synthetic
frame with `quackd/sim2d/render.py`. It is worth writing when the shared 2D world can represent the
body honestly and the robot has a task worth running end to end; it is not worth writing for a
body the world would have to lie about. It earns a ✅ only with a seeded acceptance sweep that
checks the world's ground truth, not merely a run that does not crash.

There are two physics simulators, neither is a general one, and neither is in the core. The
`sim3d/` package inside `quackd-microduck` holds one arena and a `Body` protocol with two
implementations, a kinematic puppet and the Microduck on its own trained policy. The `sim/`
package inside `quackd-lerobot` is the arm's, `lerobot:mujoco`, and it is built the other way
round: not a world a body is dropped into, but the arm's real backend with a simulated follower
under it, so a task rehearsed there runs through the code that drives the arm
([ADR-0047](adr/0047-the-arms-simulator-runs-the-real-backend.md)). Both fetch their model from
upstream at a pinned commit and hash every file, and CI runs each on a stand-in that needs
nothing fetched. A physics body for a new robot means its MJCF, its own controller and a reason
the cartoon cannot serve, usually that you need to know whether a gait works, or that its real
backend has enough logic of its own to be worth rehearsing. `sim2d` is still what the shared
world and the flock are built on.

A simulator of a real body declares itself: the module lists it in `SIMULATOR_BACKENDS`, and
`quackd.adapters.factory.is_simulator(spec)` reads that before anything is built. That is what
`quackd preflight` refuses anything else by, so a command meant for a model never opens a real
body's port. A module that declares none has none.

## `upstream_api.py`: never guess a name

The traps that recur across bodies, and what each one cost, are collected in
[reading-robots.md](reading-robots.md). Read it before the first adapter you write against
an upstream you did not choose.

Every SDK name you spell lives in one file as an `UpstreamRef(name, status, source, note)`
with a permalink to a pinned commit and line. `VERIFIED` means you read it there;
`UNVERIFIED` means it is your assumption, and the note says what quackd does about it.
`tests/test_upstream_api.py` takes one row per adapter: the module, the files allowed to
touch its UNVERIFIED identifiers (its own `upstream_api.py` and the backend), and the source prefixes
every link must start with. `docs/adapters/<name>.md` must list every ref's name (a test
checks) and carry the pin and the word "never" until someone has run it for real.

## Status is a promise

The README's status table and [adapter-status.md](adapter-status.md) get ✅ only for what
was exercised against its real target by us. A new adapter arrives 🧪 for its SDK backend
and stays 🧪 until a human runs it on hardware and the transcript says so. Nothing in
this repository claims a robot moved unless one did.

One backend has been through that. `lerobot:real` drove an SO-101 on 2026-09-15: the lookout
duck, free-form waves, the gripper and a USB webcam ([lerobot-first-run.md](lerobot-first-run.md)),
and the same arm ran again on 2026-09-23 under a registered name with a rest pose recorded
([adapter-status.md](adapter-status.md)). It is the only body here any of this has been tested
against. Every other adapter is still
🧪 on the backend that reaches its robot, which is the state this page is mostly written for.

## The checklist

Steps 1 to 5 are the adapter. Everything after them is what quackd owes a robot it
publishes, and a package published from anywhere else needs none of it: see the second list.

1. `adapters/<name>/pyproject.toml` with the `quackd.adapters` entry point, the core as a
   dependency, an `sdk` extra if a backend imports a library, and a `README.md` beside it.
2. `adapters/<name>/src/quackd_<name>/__init__.py` with `__version__`, the manifest (its
   datasheet included), the adapter class and the four functions, and each verb of its own
   classified in `quackd/verdict.py`.
   - `make()` **accepts a rest pose or refuses it**, never ignores it: the only way one
     reaches a body that does not park is a hand-edited `robots.json`, and a file that says
     something untrue names itself rather than being quietly dropped. `refuse_rest_pose()` is
     the one-liner for a body that does not park.
   - `make()` calls `one_camera_url()` unless the body genuinely reads several cameras, in
     which case it keeps the whole tuple, implements `get_frames()`, and gives each frame the
     name its url asked for, because that name is what the model, a pick policy observation
     and `frames/NNNN-<name>.png` tell the views apart by.
3. `mock.py`, and a test that runs every verb through an `Executor` on it.
4. `upstream_api.py` with pinned links and `UPSTREAMS` naming it; a row in
   `tests/test_upstream_api.py`.
5. The SDK backend, lazily imported, injectable, with a test on fakes and a test that the
   missing extra names itself.

Then, for one quackd publishes:

6. A row in `OFFICIAL` in `quackd/adapters/catalogue.py`: the name, the backends in order
   (the first is what `--robot <name>` means with no backend after it), the status line
   `list-adapters` prints, a one-line summary, the extra and the SDK to probe for. The
   catalogue is strings and imports nothing, which is what lets the table print the whole
   seven on a machine with none of them installed, each marked "not installed". Append,
   never insert: the tables that print these rows are order sensitive and a test pins them.
7. A `quackd[<name>]` extra in the core's `pyproject.toml` pointing at `quackd-<name>` (with
   `[sdk]` if there is one), the package listed in the `dev` extra and in
   `[tool.uv.sources]` as a workspace member, and `uv lock`. The module gets a row in
   `doctor.py`'s `EXTRAS` if it has an SDK worth probing.
8. `docs/adapters/<name>.md` (every ref name, the pin, "never"), a row in the README status
   table and in `adapter-status.md`, a CHANGELOG entry, a `docs/architecture.md` mention. For
   a backend that reaches hardware, a `<name>-lookout` duck that moves nothing and a
   `docs/<name>-hardware-checklist.md`: the first thing an owner will point at the robot, and
   the order to do it in.
9. The gate: `uv lock --check && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest && uv run quackd validate ducks/*.duck`.

### Publishing one yourself

Steps 6 to 9 are this repository's housekeeping and none of them is yours. Build a package
named whatever you like, depend on `quackd`, declare the entry point, and publish it. On any
machine where it is installed:

| What happens | Why |
|---|---|
| `quackd list-adapters` shows your robot, after the seven and alphabetically among any others | `adapter_names()` reads the entry point group and appends what it does not publish |
| `--robot <yours>:<backend>` builds the body | the factory imports your module and calls its `make()` |
| `quackd list-verbs`, `quackd validate`, the MCP tool list and the prompt all know your verbs | they come from your manifest, the same as everyone's |
| `quackd doctor` prints your upstream pins and your probes | from your `UPSTREAMS` and `doctor_rows()` |
| your adapter is the default when it is the only one installed | a machine with one robot has no ambiguity to resolve |

Your row in `list-adapters` reads module-level `BACKENDS`, `STATUS`, `SUMMARY`, `EXTRA` and
`SDK` if you set them; without them the status cell says "installed here, not published by
quackd". What you do not get is a catalogue entry, so quackd cannot name your robot on a
machine where your package is absent. That is the one thing being in this repository buys.
