# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Two hardware paths, each audited against upstream rather than against itself. Still nothing
has run on a robot; what changed is that several things which could not have worked now can,
and several claims that were not true no longer are.

### The Open Duck Mini v2 hardware path

An audit of `open_duck:bridge` against upstream at its pinned commit. A duck set up exactly
the way `install.sh` and the docs instruct could not start the bridge; if it could, it would
have had no camera verbs; and the operator's Ctrl-C would not have stopped it.

**The duck could not be started at all.** The shipped `ExecStart` exits 2 before binding a
socket, because `--script-arg --onnx_model_path` makes argparse refuse a value beginning with
a dash — and it was the only `serve --script` invocation shipped anywhere, so it was the one
an owner would copy. `WorkingDirectory` was one level above the data: upstream opens
`./polynomial_coefficients.pkl` and `../mini_bdx_runtime/assets/` relative to the working
directory, and the first is read inside `RLWalk.__init__` *after* the servo bus is powered, so
the wrong directory is a traceback over fourteen energised joints. A pre-flight now refuses
before the socket and before the servos. And no configuration produced both frames and the
verbs that use them: the camera capability came from `expression_features.camera`, but that
flag says who owns the *device*, and camd refused to start while it was true — so a
correctly configured duck reported no camera and dropped `observe`, `go_to`, `search_scan` and
`approach_and`, making both starter tasks and three checklist steps unreachable.

**The operator's stop did not stop the duck.** An abort never reached the verb that was
moving: `asyncio.wait_for` watches only the clock, so a kill switch, a Ctrl-C or a failed
heartbeat set a flag nothing read until the verb returned on its own, while the verb's own
10 Hz resend kept feeding the daemon's deadman. `stop` was then refused by the very gate that
made it necessary, in both the executor and the MCP session. `duck.stop` lasted one tick and
was undone by the next command. Nothing settled the duck on shutdown — the `finally` that
looked like it did runs after the loop has exited, so its zeros had no reader — and
`systemctl stop` killed the interpreter between two 20 ms ticks with the servos holding their
last goal. Any client's disconnect zeroed a walking duck, including the `doctor` the checklist
tells you to run from a second terminal. And `stop` reported a success it could not know,
because the guard reads `stop_error` off the adapter and no adapter forwarded it.

**It claimed guards it did not have.** The task file told the pilot every moving verb would
refuse if it fell; nothing detects a fall on this backend, so none ever did, and an accepted
`move` read to the model as evidence it was upright. A duck with `start_paused` could never
walk and was misdiagnosed as a starved Pi. A wedged control loop reported itself healthy at
50 Hz forever. A failed state read was dressed up as a healthy duck, with a fabricated pose
for a robot that has no odometry. And the token was unreadable by the service user, so the
bridge ran with authentication silently off while four places in the docs promised otherwise.

**The camera was not safe to steer on.** camd served a frozen frame forever once capture
stopped; its 1 fps default gives a divergent per-frame loop gain against a 10 Hz visual loop;
`doctor` could not probe it at all; `go_to`'s command cadence was frame-fetch-bound against a
300 ms deadman; and every distance was wrong, because the detector assumes the simulator's 90°
field of view where a Pi Camera Module 2 is about 62 — 0.23 m against 0.38 m for the same
ball, enough for `go_to` to announce arrival outside the task's own success criterion.
`--no-swap-rb`, offered in the README as the cure for wrong colours, inverts a correct image
and relabels an orange ball as a person.

**Nine of ten upstream unknowns were closed by reading, not guessing** — the import form the
shim depends on, that `get_last_command` runs at 50 Hz and before the pause check, that `B` is
the sound button, that the antennas are trigger-driven and unclamped, that the head slots are
written unconditionally with no mode button, that the walk loop opens no camera, and that the
IMU in use is `raw_imu.Imu` (a dict of gyro and accelero) rather than `imu.Imu` (a quaternion).
The four head floats turn out to be **offsets** added to the walk policy's own head targets,
not absolute joint angles. Fall detection was deliberately left unimplemented: which axis
reads gravity depends on an axis remap, a config flag and a tare offset, and a fall detector
that is wrong fails as a confident "not fallen".

### The Microduck's hardware path

### Fixed

- **The API version quackd was written against had moved on.** `upstream_api.py` was the one
  adapter ADR-0022 let cite `main` instead of a commit hash, and in the week after it was
  read upstream went from `API_VERSION` 16 to 23. The handshake refuses on mismatch rather
  than guessing — correct behaviour, and it meant the first real Microduck anyone connected
  to would have closed the socket before a single intent was sent. The file now carries `PIN`
  and `READ_ON` like the other four, every ref links to a line at that hash, and a test keeps
  `/blob/main/` out. Re-reading all of them found the rest of the drift is small and additive:
  `Skill` is a free string now (a robot's real list arrives in `robot.subscribe`'s answer),
  and `RobotState.theremin`, `RobotState.chorale` and `HealthResult.cpu_temp_c` are new.
- **`robot.subscribe` was never sent, so nothing was ever known about the robot.** It lived
  in the `subscribe()` generator, which nothing in the CLI, the agent loop, the MCP server or
  the executor iterates, and upstream does not push state until asked. Every fact derived from
  the state frame was therefore empty for the life of a session — and empty read as safe:
  `fallen` was `False` because nobody was looking, so the precondition layer `docs/safety.md`
  advertises was inert on the one backend where a fall is a real robot on a real floor.
  `connect()` now subscribes, frames carry an arrival time and stop being believed when they
  stop arriving, and a duck nobody is watching reads as `unknown` rather than as standing.
- **`sit` and `stand` could do the opposite of what they said.** Both send upstream's single
  `sit_toggle`, and posture was the only thing telling them apart, so with posture permanently
  unknown `stand` would sit a standing duck down and report success. They refuse rather than
  fire a toggle nobody can aim. `stand_up` no longer reports "upright" when nothing reports
  falls.
- **One dropped camera frame ended the run.** `get_frame` raised beneath a comment promising
  it would not, and `AgentLoop._observe` calls it every step and catches nothing. Frames are
  now pulled on a timer and served from memory, `get_frame` never raises, and `camera_health()`
  carries the failure.
- **`doctor` accepted `--camera-url` and never fetched a frame**, so a wrong snapshot URL
  passed preflight and failed at the first `observe`. It now fetches one and prints its size.
- **The manifest claimed a camera on every backend.** Upstream serves no frames over
  `robotd`'s socket, so on a real duck `--camera-url` is the whole camera; `observe`, `go_to`,
  `search_scan` and `approach_and` were advertised while only able to answer "this transport
  has no camera". They are absent unless something is serving frames.
- **The heartbeat logged a stop it had not delivered.** `stop()` swallowed every error. It
  still never raises — it is called from the paths that run because the socket died — but it
  records why it failed and says so, and the heartbeat no longer asserts the outcome.
- An unknown sound tag became a chirp silently; JSON-RPC errors lost their code, so `BUSY` and
  `PERMISSION_DENIED` were the same thing to a caller.
- **Loading a second `.duck` over MCP refunded the budget.** `robot_load_duckfile` is a tool
  the model itself holds, and adopting a contract built a fresh `Budget` and cleared the
  consecutive-failure tallies, so a pilot that had spent its steps or been refused a verb
  could load a wider duck and start counting from zero. The limits still become the new
  contract's; the steps, the llm calls, the clock and the failure counts now stay the
  session's, and the tool's reply says what carried over.

### Added

- **Video off a real Microduck** (`--camera-url webrtc://host:8443`, `quackd[microduck-camera]`).
  There is no camera method in `duck-ipc-proto`, no snapshot route in `mediad` and no camera
  subcommand in `robotctl`, so a picture means being a WebRTC peer. It runs on your machine and
  writes nothing to the robot — the alternative needs `mediad` stopped, because its `v4l2src`
  holds `/dev/video0`. Signalling is read off `mediad`'s own web client at the pin and tested
  through a fake socket; the H.264 and the ICE are not tested and have never met a duck.
- [docs/microduck-hardware-checklist.md](docs/microduck-hardware-checklist.md), an issue
  template, and `microduck-lookout` — a bring-up task whose allowlist moves no legs, which
  copes with having no camera and stops to say so when posture reads `unknown`. The checklist
  assumes the duck is borrowed: nothing in it installs anything or needs `sudo`.
- CI runs on Windows. `robotd` speaks over a unix socket, Windows cannot open one, and the test
  covering quackd's `ssh -L` answer only runs there — so it had never run anywhere.

## [0.6.0] — 2026-09-04

A run stops starting from nothing. Every release so far built a pilot with no past: the
transcript recorded each prompt, tool call and frame, and the next run never read a line of
it, so a duck that had found the ball behind the sofa three times searched the whole room a
fourth time. Now each robot keeps a small file of notes the pilot chose to save and one
line per earlier run, and the newest of both are in the prompt before the first observation.
This is also the first release built on other people's pull requests: memory (#5) and the
`max_minutes` fix (#3) are both theirs. Design: `docs/adr/0025-memory-between-runs.md`,
with the release's own notes in [docs/design/memory.md](docs/design/memory.md).

Still nothing has run on a robot of any kind. What memory adds is tested end to end offline,
and no cloud model has ever written a note.

### Added

- **Memory between runs** (`quackd/memory.py`, [docs/memory.md](docs/memory.md)). Every
  run used to start from nothing. Now each robot, keyed `adapter:backend`, has a JSONL
  file under `~/.quackd/memory/` that holds the notes the pilot saved with the new
  `remember` tool and an episode line quackd writes at the end of every non-dry run
  (outcome, reason, the last few verb results). The newest of both are rendered into the
  system prompt at the next run. `remember` costs an LLM call but no step, and a repeated
  sentence refreshes the old note. `quackd run --no-memory` / `--memory-dir`,
  `quackd memory show|add|clear`, and over MCP `robot_recall` / `robot_remember` (eight
  tools now). A simulated body never inherits a real one's notes. Thanks to
  [@Bayway](https://github.com/Bayway) (#5), whose design and implementation this is.
- **The solo starter ducks ask for a note.** `find-and-kick`, `fetch`, `follow-me`,
  `patrol-and-quack`, `open-duck-scout`, `open-duck-lookout` and `reachy-spotter` carry
  `remember` in their last strategy step and a *Memory* section saying what is worth
  keeping, and `--goal` runs get the same line. A prompt-level hint alone was ignored by a
  14B local model; the strategy step is followed. The v0 duck goldens were regenerated
  for this. `hello-world` and the flock ducks are unchanged.
- **A test that the PyPI project page's links resolve** (`tests/test_pypi_readme.py`),
  because the rewrite below is invisible when it breaks: nothing in a normal run reads the
  built metadata, and the only symptom is dead links on a page the maintainer rarely opens.

### Changed

- CI runs on `actions/checkout@v7` and `astral-sh/setup-uv@v7`. `checkout@v4` targets
  Node 20, which GitHub deprecated and now force-runs on Node 24. Thanks to Dependabot
  (#1, #2).

### Fixed

- **`max_minutes` could be beaten by a slow provider.** The time budget was checked in
  `note_llm_call()`, *before* `provider.step()`, and nothing looked at the clock again
  before the answer was processed, so a provider that replied after the deadline still had
  its `declare_success` honoured and the run recorded a success it had no right to. The
  loop now re-checks the clock the moment a turn comes back, and a late declaration ends
  the run as `budget`. Thanks to [@r0jin](https://github.com/r0jin) (#3).
- **Every relative link in the README 404'd on the PyPI project page.** `README.md` is the
  long description, and its 60 relative links are correct on GitHub, where a relative link
  follows the branch you are reading, and meaningless anywhere else. A hatchling metadata
  hook (`hatch_build.py`) absolutises them at build time, in both syntaxes this README
  uses: 56 Markdown links and four raw `<a href>` in the centred HTML blocks. The first cut
  of the hook handled only Markdown, so the test now checks both and would have caught it.
  The repository's own README is unchanged, images were already absolute, and in-page
  anchors are left alone.
- **A hand-edited memory file could take the whole command down.** The file is documented
  as one you may edit, and a line that is not JSON is skipped. A line that *was* JSON but
  not an object (`"a note"`, `17`, `[]`) raised `AttributeError` out of every reader,
  because only `JSONDecodeError` was caught. Now every such line is skipped, as promised.
- **`--dry-run` wrote permanent notes.** The episode at the end of a run was guarded and
  the `remember` tool was not, so a run that sent nothing to the robot could still leave a
  permanent conclusion drawn from verb results the dry run itself invented. It now refuses
  and says so, like every other intent a dry run declines to send.
- **A refreshed note sorted as the oldest.** Repeating a sentence refreshes its timestamp
  and used to leave it where it was in the file, and both the prompt's "newest notes" window
  and the 400-entry cap read file order. So the note a model had just repeated was the first
  one dropped. A refresh now moves the note to the newest position.
- **`quackd memory show --raw` did not print the file as is.** It went through Rich, which
  ate anything in square brackets: a note reading `the ball is [bold]behind[/bold] the sofa`
  printed as `the ball is behind the sofa`. A note is text a model wrote, so it can contain
  anything.
- Tests set `QUACKD_MEMORY_DIR` to a temporary directory, so a test run no longer writes
  the developer's own `~/.quackd/memory`.
- `quackd memory show|add|clear --robot <bad spec>` printed a Python traceback. Every other
  command taking `--robot` answers in one line, and now these do too.
- With `--no-memory` the MCP server still told the pilot to call `robot_recall` early, a
  tool that would answer "memory is off". Those two sentences are now omitted.
- The prompt told the model `remember` "is free". It costs an LLM call, which is a budget it
  shares with every other turn, and the ADR and docs both said so.
- **Claims that had gone stale before this release, found while auditing it.**
  `docs/architecture.md` still described the `duck_*` MCP aliases 0.5 deleted, and had not
  been updated for memory at all (no `memory` command, no `remember` tool, no `memory`
  transcript event). The README's architecture diagram listed four adapters and omitted
  `open_duck` and its `bridge` backend, which the adapter-count guard could not see because
  it reads the phrase "N adapters" and not a list. The README, `docs/architecture.md` and
  the FAQ each said one quackd daemon runs on a robot, while `bridge/open_duck/` has shipped
  two since 0.5, and `docs/architecture.md` contradicted itself about it. `docs/duck-spec.md`
  called `max_minutes` a cap without saying a verb already running is not interrupted.
  `_deprecated()` in `quackd/cli.py` was dead from the 0.5 `--transport` removal. `quackd
  --help` still introduced the tool as a way to "pilot a Microduck", five adapters later.
  `_pick_default` justified its rule with the `duck_*` aliases. `tests/test_goldens.py`
  dated its goldens to 0.3.0 after four of the six duck hashes were regenerated here, and
  ADR-0025 filed learned verbs under ADR-0019, which is the `.duck` spec v1.
- **The README said no live local server had been run.** One now has, by this release's
  contributor, which is the first local-model evidence this project has had. The sentence
  says whose machine it was and that no transcript from it is in the repository.
- `test_cap_drops_old_episodes_before_notes` wrote 405 entries one at a time, each one
  re-reading and rewriting the whole file. It took 14.6 s here, on a matrix that runs it
  four times, to prove a property a cap of 12 proves in 0.16 s. It now also asserts *which*
  entries survive, which is what the test was named for.
- Guards for the class of mistake this release's own review kept finding. A living document
  *or a Python string a user reads* may not claim a number of `robot_*` tools the server
  disagrees with, nor still describe the `duck_*` aliases 0.5 deleted: 0.5's guard proved
  nothing still *offers* a removed thing and could not see one still being *described*, and
  the stale count turned out to be in a `--robots` help text and two docstrings as well as
  two README sentences. The key the CLI computes for a robot's memory file must equal the
  key the MCP server computes, for all five adapters and all fourteen backends, because
  "a note saved from Claude Desktop is read by the next `quackd run`" is otherwise an
  intention. And the built wheel's long description must contain no relative links.
- The `quackd memory` command group, and the memory flags on `run`, now have tests. They
  shipped with none, which is how two of the three subcommands were wrong.
- One assertion in the new memory tests was `A or B` where `A` was never true (the sound in
  the highlight is the robot's own tone, not the text), so it checked almost nothing. It is
  one assertion now.

### Known limitations

- **The scripted pilot never calls `remember`**, so `--provider fake` accumulates run
  outcomes and never writes a note. Notes have been exercised by one local model
  (Qwen 2.5 Coder 14B through LM Studio, `find-and-kick`, seeds 5 and 6) on one machine,
  and by no cloud model at all.
- **`--no-memory` does not silence the ducks.** The seven starter tasks above ask for a
  `remember` inside their strategy, and that text is in the prompt whether or not memory is
  on. A pilot that follows it gets a clear refusal that costs an LLM call and no step.
- Memory is a file, not a memory system: newest wins, there is no embedding and no search,
  nothing is shared between bodies, and the executor never reads it. A note is text a model
  wrote.

## [0.5.0] — 2026-09-03

The first robot you can actually build. Every hardware backend before this one targeted a
robot you could not buy or had not assembled: the Microduck ships around Christmas 2026, and
the Reachy, LeRobot and rosbridge backends have only ever talked to fakes. The Open Duck
Mini v2 is open hardware people are printing at home today, so for the first time a stranger
can follow these instructions all the way to a walking robot. That also makes this the first
release where quackd ships code that runs **on** a robot. Design: `docs/adr/0024-open-duck-mini.md`.

Still nothing has run on a duck. What is new is that everything except the duck is tested.

### Added

- **The Open Duck Mini v2 adapter** (`--robot open_duck:sim2d`, `open_duck:mock`,
  `open_duck:bridge`): the
  first robot quackd supports that anyone can build today. It is an open hardware 3D
  printed biped that walks on its own 50 Hz ONNX policy on a Raspberry Pi Zero 2 W. Its
  manifest is a strict subset and says so: this robot has no beak, no gripper, no kick
  policy, no sit policy and no get-up-after-fall policy, so `kick`, `grab`, `sit`, `stand`
  and `stand_up` are never declared and therefore do not exist for it anywhere. A duck
  built without a camera or a speaker loses exactly the verbs that need them. Velocities
  are clamped to the ranges read from the robot's own runtime (0.15 m/s forward, 0.2 m/s
  sideways, 1.0 rad/s turning), and a fallen duck refuses to move with a message saying a
  human must stand it up, because nothing quackd can call will.
- **A bridge daemon that runs on the robot** (`bridge/open_duck/`), which is a first for
  quackd: every other adapter talks to someone else's daemon, and this robot has none. It
  does not reimplement the 50 Hz control loop, it *is* that loop, with the class upstream
  imports to read a gamepad rebound to read a socket instead. One process, so the servo bus
  keeps one owner, and nothing of upstream's is copied. Going limp is unreachable rather
  than forbidden: the only channel from the network to the body is seven floats and a few
  buttons. The deadman is quackd's own, it runs on the robot, and it is evaluated by the
  control loop rather than a timer, so a server thread that is starved, wedged or dead still
  stops the duck. Standard library plus numpy, so it installs on a 512 MB Pi, and
  `--fake` runs the whole protocol on a laptop with no robot at all.
- **A camera server for the duck's Pi** (`bridge/open_duck/quackd_duck_camd.py`), in its
  own process because encoding a JPEG inside a 20 ms control tick is not affordable on a Pi
  Zero 2 W. It captures on a timer so a slow client cannot stall it, serves the newest frame
  over HTTP, and has no control path at all: it reads a camera and answers GET. Without it
  the bridge advertises no camera and the verbs that need one do not exist rather than exist
  and fail. `--fake` paints a duck's eye view, so the whole chain runs with no hardware.
- **Two starter tasks**, `open-duck-scout` (find the ball, walk up, report, 10 of 10 seeds)
  and `open-duck-lookout`, whose allowlist moves no legs at all and which exists to be the
  first thing anyone points at a physical duck.
- ADR-0024, `docs/adapters/open_duck.md`, and a hardware checklist and issue template for
  the first person to run this on a duck they built.
- **`--camera-url`** on `run`, `serve-mcp` and `doctor`, for a robot whose camera is an HTTP
  snapshot rather than something the control socket can serve.
- **`--token` and `QUACKD_DUCK_TOKEN`** for a robot that wants authentication. The Open Duck
  bridge's own installer writes one, so until now a duck set up by the book refused every
  client and no document explained why.
- **`quackd doctor --robot X --address Y` connects.** Everything else in `doctor` is offline
  and reads the static manifest, which describes a fully built robot. This prints what your
  robot actually reported: its capabilities, which verbs it does and does not have, and
  whether its control loop is healthy. It is the only way to see that difference before a
  run does.

### Removed

- **`--transport X`**, the 0.4 alias of `--robot microduck:X`, along with `resolve_robot`'s
  transport branch and the warn-once machinery that existed only to support it. 0.4 said in
  ten places that it would go in 0.5.
- **The eight `duck_*` MCP tools**, 0.3 aliases pinned to the default robot. Omitting the
  `robot` argument to a `robot_*` tool does the same thing. `duck_get_frame` has no exact
  replacement by design: `robot_observe` does the same job but goes through the executor, so
  frames are now budgeted and logged like every other verb.
- Removing them broke the MCP system prompt, which named three of the removed tools and
  hardcoded "a small biped duck robot (25 cm, 800 g)" for every body. It now names the robot
  and takes its description from the manifest's own `blurb`, so a lone Open Duck introduces
  itself as the duck that cannot pick anything up and cannot get back up if it falls.

### Fixed

- **Perception was attached only for the simulator.** Every hardware backend with a camera
  ran blind: it fetched frames, detected nothing because nothing was detecting, and reported
  that it could not see. The detector now follows the camera rather than the backend, which
  also fixes `microduck:jsonrpc` and `rosbridge:ws`. Both entry points ask one function,
  `perception.detector_for`, and both ask it at *connect*: the first cut of this fix keyed
  `quackd run` on the camera and left `serve-mcp` keyed on the backend, so every hardware
  body over MCP still ran blind, and deciding from the static description would still have
  missed a `rosbridge:ws` base, which only reports its camera once it has connected.
- **A robot that reports fewer capabilities at connect than its description claims** used to
  crash the run. `validate` checks the static manifest, which describes a fully built robot,
  so a duck with no camera got past it and then raised a bare `VerbNotFound` when the agent
  loop built its tools. A verb the task *requires* now refuses in the validator's words, and
  one it merely *allows* is dropped with a line in the log, which is what a v1 task allowing
  more than it needs is for.
- `quackd run` now checks a `.duck` against its robot before connecting, the way
  `serve-mcp` always has. Pointing a task at a robot that lacks one of its verbs used to
  reach the agent loop and raise a bare `VerbNotFound` with the robot already connected and
  an empty run directory already written. It now refuses up front with the validator's own
  sentence and writes nothing.
- **Two commands still told users to pass `--transport`**, the flag this release removes: a
  `doctor` table title and the `TransportError` the WebSocket stub raises. The guard that
  swept the documentation for it could not see a Python string, and now reads
  `quackd/**/*.py` too. Six other user-visible strings were still dated "in 0.4", and
  `doctor` printed the extra as `quackd` because rich ate the `[lan]` as markup.
- **The PyPI summary and keywords never mentioned the Open Duck Mini**, the robot this
  release is named for, and no test had ever read `pyproject.toml`. One now does. The
  `Framework :: Robot Framework` classifier is also gone: that is the test-automation tool
  of the same name, not robotics, and it filed quackd under the wrong ecosystem.
- `quackd doctor` reported `?` for opencv and Pillow, which are not optional and were plainly
  installed. An import name is not a distribution name, so it now asks the installer which
  distribution provides the module before giving up.
- The Open Duck bring-up checklist keeps the feet off the ground through step 9 and puts
  them down at step 10. Three documents said step 8.
- `mypy` failed on Python 3.12 (the opencv stubs that resolve there type only the array
  overload of `cv2.inRange`, so the tuple bounds in `perception/color_blob.py` matched no
  variant). Bounds are now `uint8` arrays. OpenCV accepts both, so nothing about detection
  changes: the seeded goldens are byte-identical and all five sweeps still pass 10 of 10.
  This landed just after the v0.4.0 tag, so the tagged commit and the 0.4.0 files on PyPI
  still carry it. It is a type-check-only issue and does not affect the released package at
  runtime.

## [0.4.0] — 2026-09-02

From "a brain for the Microduck" to "a brain for any small robot". The thesis does not
change: the LLM picks verbs, the robot's own controllers move, quackd enforces the
contract. Four adapters (Microduck, Reachy Mini, a LeRobot arm, any base over rosbridge),
one `.duck` contract across bodies, a head and a duck completing one task together in the
simulator, and nothing claimed on hardware. Design: `docs/design/multi-robot.md`.

### Added

- **Robot adapters and manifests** (`quackd/adapters/`): every robot is an adapter that
  returns a `RobotManifest` from `connect()`, and the verb registry is built from that
  manifest rather than hardcoded. A verb that is not in the manifest does not exist. The
  Microduck is the first adapter and wraps the four existing transports with zero
  behaviour change; `manifest.schema.json` is generated and drift-tested. (ADR-0017)
- **Core verbs and aliases**: `observe`, `report_state`, `stop`, `say`, `move`, `go_to`,
  `search_scan` and `approach_and` exist on any robot that meets their requirements;
  `get_frame`, `walk_to` and `walk` are permanent aliases, listed once in
  `quackd/verbs/aliases.py`. `search_scan` sweeps the head on a robot that can only look.
  Preconditions are named in the manifest and supplied by the adapter; the executor spells
  none. (ADR-0018)
- **`.duck` v1**: `requires`, `robots`, `flock.roles` and `flock.frame_hints`; v0 files
  parse unchanged. `quackd validate --robot <adapter>:<backend>` checks a task against a
  robot's manifest with field-level errors such as `requires kick, but reachy-01
  (reachy-mini) does not provide it`. (ADR-0019)
- **`--robot <adapter>:<backend>`** on `run`, `validate`, `serve-mcp`, `doctor` and
  `list-verbs`; `--robots name=spec,...` on `run` and `validate`; `quackd list-adapters`;
  `doctor --robot` shows one manifest; a `.duck` may declare its default robot.
- Transcripts: `verb` events carry `canonical`; `run_start` and `summary.json` carry the
  robot's manifest and id. `duck_list_verbs` entries gain `canonical`, `aliases` and
  `core`; `duck_get_state` gains `robot`.
- Goldens recorded from 0.3.0 (`tests/golden/`) prove seeded worlds, the starter ducks and
  a `flock-kick` conversation are unchanged; CI runs both seeded sweeps at 10 of 10
  (`QUACKD_STRICT_SEEDS=1`).
- **Reachy Mini adapter** (`--robot reachy_mini:sim2d | mock | sdk`, extra `quackd[reachy]`
  for the SDK): a stationary head with a camera, a 180° neck, expressions and a speaker.
  Its manifest carries `observe`, `report_state`, `stop`, `say`, `search_scan` (a gaze
  sweep), `gaze`, `express`, `play_sound` and a confirm-gated `wake_up`; no locomotion
  verbs exist on it. `say(text)` is voiced as the closest expressive sound because the SDK
  has no text-to-speech; `stop` is `cancel_move` and `disable_motors` is never sent. Every
  SDK name is VERIFIED in `quackd/adapters/reachy_mini/upstream_api.py` against a pinned
  commit and the 1.10.0 wheel; the `sdk` backend has never been run on a robot.
  (ADR-0022, ADR-0023)
- **`StationaryHead`** in `sim2d`: a fixed camera on a wall with zero RNG draws, so every
  world without a head is byte-identical to 0.3; the recorder and the live window can
  focus a head camera.
- **`reachy-spotter` starter duck** (`duck: 1`, `robots: reachy_mini:sim2d`): find the
  ball with your gaze and say where it is; 10 of 10 seeds with the scripted pilot, judged
  by ground truth.
- **Heterogeneous flocks** (ADR-0020): members are adapters sharing one arena and one
  lockstep clock; bids carry a capability term so a robot bids only for a role its
  manifest can fill; one auction fills every role (most constrained first, lowest own
  distance, member-name tie-break, per-role hysteresis; the spotter is held for the run).
  With roles the kicker reports `kick_done` and the spotter judges from its own fresh
  frames (`VERDICT`); only `moved` is a success and the ground-truth veto stays on top.
  Frame hints (`HINT`, arena frame, sim only) choose the kicker's pre-turn; the
  frame-of-reference limitation is documented in `docs/flock.md`. `run --robots
  name=<adapter>:<backend>,...`.
- **`reachy-spots-duck-kicks` starter duck**: a Reachy Mini head spots the ball, a
  Microduck kicks it, the head judges the kick. 10 of 10 seeds with scripted pilots,
  every message in `flock.jsonl`, zero planner calls with the fake provider.
- **Multi-robot MCP**: `quackd serve-mcp --robots duck=microduck:sim2d,reachy=reachy_mini:mock`
  fronts a fleet with `robot_list`, `robot_list_verbs`, `robot_run_verb`, `robot_observe`,
  `robot_say` and `robot_load_duckfile`; every robot has its own executor, budget,
  heartbeat and contract, and `robot_load_duckfile` checks the contract's `requires`
  against that robot's manifest before adopting it. The eight `duck_*` tools stay as
  aliases of the default robot (deprecated, removed in 0.5). Simulated robots over MCP
  each get their own world; a shared arena over MCP is future work.
- **LAN discovery** (`quackd discover`, `quackd announce`, ADR-0021): zeroconf service
  `_quackd._tcp.local.` with an identity-only TXT record (manifest id, digest, adapter,
  body, verb count), every pair validated under 200 bytes before zeroconf sees it. Behind
  `quackd[lan]`, imported lazily, tested on fakes; exercised once for real between two
  processes on one machine, never between two machines.
- **MQTT flock bus** (`quackd.flock.mqtt_bus.MqttBus`, `run_flock(bus_factory=)`): the
  same two-method `Bus` protocol over a broker, `quackd/<flock_id>/ctl` at QoS 1 and
  `/hb` at QoS 0, never retained, the `FlockMessage` JSON as payload. Broker echo is
  dropped, the tap fires exactly once per message per node, remote messages are
  marshalled onto the event loop, and duplicates are tolerated by the coordinator's
  idempotent handlers. Library only: a flock across machines also needs a clock across
  machines, so there is no `--bus` flag. Tested on a fake broker; exercised once for real
  against a local `amqtt` broker with paho 2.1 (all eight kinds, one machine).
  `Subscription.drain()` is now an atomic `popleft` loop. `doctor` lists both LAN
  libraries.
- **LeRobot adapter** (`--robot lerobot:mock|real`, ADR-0022): an SO-101 class desktop arm
  with `move_joints`, `gripper`, `place` and, when a policy is available, `pick` as one
  skill intent that the arm's own learned policy executes. No locomotion, no voice, no
  gaze in its manifest. `real` sits behind `quackd[lerobot]` (Python 3.12 or newer, torch
  never imported on the default path), passes `calibrate=False`, refuses an uncalibrated
  arm, holds position on stop and never disables torque; every LeRobot name is pinned
  and line-linked in `quackd/adapters/lerobot/upstream_api.py`; never run on an arm.
- **rosbridge adapter** (`--robot rosbridge:mock|ws`): any wheeled base that takes a
  `geometry_msgs/msg/Twist` over `rosbridge_server`. The address carries the topics
  (`ws://host:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/compressed`); with an image
  topic the base also gets `observe`, `go_to`, `search_scan` and `approach_and`. There is
  no deadman: quackd re-sends the Twist at 10 Hz and zeroes it on stop, and the manifest
  says so. `ws` sits behind `quackd[rosbridge]` (roslibpy 2.x); every roslibpy, rosbridge
  protocol and message name is pinned and line-linked; never run against a bridge.
- **Speed limits come from the manifest**: `move`, `go_to` and the turn used by
  `search_scan` clamp to `limits.max_vx/max_vy/max_wz` when a manifest names them; the
  Microduck's limits equal the old schema bounds, so its runs are unchanged.
- **Docs**: `docs/adapters.md` (write an adapter in a day), `docs/manifest-spec.md`,
  `docs/adapter-status.md` (every adapter's honesty table, the Microduck's rows moved
  there unchanged), `docs/lan.md`, one page per adapter under `docs/adapters/`, and
  ADRs 0017 to 0023. `docs/safety.md` says what stops each body; `docs/faq.md` answers
  "can it drive something that is not a duck".

### Changed

- `default_registry()` is the Microduck manifest's registry; its names are canonical
  (`move`, `go_to`, `observe`) and every entry point accepts the old spellings. The
  bundled starter ducks keep their 0.3 spellings and stay at `duck: 0`.
- The agent loop connects before writing `run_start`, because the vocabulary comes from
  the connected robot.
- `docs/transport-status.md` is a redirect to `docs/adapter-status.md`; the docs test
  that keeps the Microduck's upstream table in sync now reads the new page.
- **The README was rewritten for four bodies**: the tagline and intro name the other
  robots, a new "Any small robot" section puts all four side by side with what each gets
  and what has actually run, the verb table gains a row per body, both architecture
  diagrams show the adapter layer, and the status table states per feature what was
  exercised against its real target and what was not.
- Test suite: 360 tests collected, no network and no keys, with four seeded sweeps CI holds
  at 10 of 10 (`find-and-kick`, `flock-kick`, `reachy-spotter`, `reachy-spots-duck-kicks`).
- Eight starter `.duck` files ship, up from six.

### Deprecated

- `--transport X` is an alias of `--robot microduck:X` that prints one warning per
  process; it is removed in 0.5. The `quackd.transport` package is not deprecated: it is
  the Microduck backend layer.
- The eight `duck_*` MCP tools (`duck_list_verbs`, `duck_run_verb`, `duck_get_frame`,
  `duck_get_state`, `duck_set_velocity`, `duck_stop`, `duck_quack`, `duck_load_duckfile`)
  are aliases of the six `robot_*` tools on the default robot. Each carries a deprecation
  note in its description and all eight are removed in 0.5.

### Fixed

- A role auction is complete only when every role can be filled by a *different* member,
  so a single robot that satisfies both roles can no longer deadlock a heterogeneous
  flock.
- `Subscription.drain()` is an atomic `popleft` loop rather than copy-then-clear, so a
  producer on another thread (the MQTT bus, before a message reaches the event loop)
  cannot have its message cleared unseen.

### Known limitations

- **Nothing has run on hardware, on any of the four adapters.** `microduck:jsonrpc`,
  `reachy_mini:sdk`, `lerobot:real` and `rosbridge:ws` spell every upstream name from
  upstream source (the three new ones at pinned commits) and have only ever talked to
  fakes. `microduck:websocket` is a stub waiting on upstream.
- LAN discovery and the MQTT bus were each exercised once, on one machine: zeroconf
  between two processes, MQTT against a local `amqtt` broker. Neither has crossed to a
  second machine, and a flock across machines also needs a clock across machines, which
  does not exist.
- Flock mode stays simulator only, with two choreographies and exactly two roles.
- A manifest can be smaller than the robot: `lerobot:real` claims no camera and no `pick`
  until it connects, and `rosbridge:ws` has no camera verbs unless the address names an
  image topic.
- The MCP server speaks `stdio` only, so it is a local subprocess of Claude Code or
  Claude Desktop and cannot be reached from a phone. A network transport is roadmap, not
  shipped.

## [0.3.0] — 2026-08-31

Multiple simulated Microducks cooperate: split the search, hold an auction, the closest
one kicks. Everything is on the record.

### Added

- **Flock mode** (`flock:` block in the `.duck`, or `--flock N` on `run`/`record`): 2 to 4
  ducks share one arena and an in-process message bus. A deterministic Contract Net
  auction picks the kicker (bid = each duck's own camera distance estimate, 20 %
  hysteresis, 6 s claim lease, duck-id tie-break, one-claimant lock), heading sectors
  split the search, misses trigger a full-circle re-search and re-auction, and a sim-time
  watchdog drops silent ducks. Every message, bid, claim and role change lands in
  `flock.jsonl`; the outcome is judged from sim ground truth, not a model claim.
  Guide: `docs/flock.md`. (ADR-0015)
- **Multi-duck simulator**: `World(n_ducks=…)` with per-duck deadman, noise streams, kick
  counters, duck-duck collisions and the four Microduck colorways (Cream, Sky, Lavender,
  Graphite); per-duck cameras render teammates, and the detector gained four `duck`
  targets. Sim time is governed by a lockstep clock, so the world freezes while any pilot
  thinks and single-duck runs stay bit-identical per seed. (ADR-0016)
- The planner makes **at most one** LLM call per flock run (parameters validated and
  clamped, deterministic fallback); `--provider fake` computes the plan as a pure
  function. Per-duck LLM pilots are deliberately out of scope.
- Duck to duck separation is watched from world ground truth while a claim is live: the
  coordinator orders an intruding non-kicker to retreat, and the retreat still runs
  through that duck's own executor.
- Starter `ducks/flock-kick.duck`; `runs/<ts>-flock-…/` layout with per-duck transcripts;
  flock demo GIF in the README. Scripted 3-duck acceptance: 10 of 10 seeds.
- The flock shipped through an adversarial review (69 agents, 24 confirmed findings, all
  fixed before release): deadlock guards around the shared clock's tick hooks and around
  member connect failures, per-duck `max_minutes` enforcement, a heartbeat watchdog floor
  above the longest verb sleep, cooldown gating at bid time, per-field planner clamping,
  `--max-steps` honoured on flock runs, `flock.search.restart_s` honoured, and
  `one_claimant: false` rejected instead of silently ignored.

### Changed

- `.duck` spec v0 gains the optional `flock:` block (`docs/duck-spec.md`, `schema.json`
  regenerated). Files using it need quackd 0.3.0 or newer; older versions refuse them
  loudly. `quackd validate` reports flock size and rejects flock + `verbs.confirm`.
- `quackd doctor` notes flock status; `serve-mcp` refuses flock ducks with a clear
  message.

## [0.2.0] — 2026-08-29

Local and open-source LLMs can pilot the duck. No API key needed.

### Added

- **Local providers** `ollama`, `vllm`, `llamacpp`, `lmstudio` and `local --base-url …`
  for any OpenAI-compatible server: no key, model discovery from `/v1/models`,
  `tool_choice=auto` and no `parallel_tool_calls` field for picky servers
  (`QUACKD_TOOL_CHOICE` overrides), vision opt-in with `--vision`, and a JSON text
  fallback for models that cannot call tools natively (marked `text_fallback` in the
  transcript). `quackd doctor` probes the four default local addresses.
  Guide: `docs/local-llms.md`. (ADR-0014)
- `quackd run --goal "…"`: a plain-language goal instead of a `.duck` file (ad-hoc contract:
  every `safe` verb, default budgets, standard abort rules). The scripted `fake` pilot picks
  a strategy from the goal's keywords.
- `--base-url`, `--api-key`, `--vision/--no-vision`, and `--gif-size` on `run`/`record`.
- Logo (`docs/assets/logo.svg`, a Microduck-like biped in the Lavender colourway) and a
  social-preview card.

### Changed

- README rewritten for people who know nothing about robots or LLMs first, developers
  second: what it does today vs. where it is going, Mermaid architecture diagrams, usage,
  configuration, performance and limitations sections. Images use absolute URLs so the
  PyPI page renders them. Providers are named by company ("OpenAI"), not model family.
- The hero GIF is recorded at 320 px panes.

### Fixed

- Rich markup ate `quackd[extra]` in CLI error hints.
- mypy on Python 3.12 (numpy's PEP 695 stubs) in CI.

## [0.1.0] — 2026-08-28

First release: sim-first, honest about hardware.

### Added

- **`.duck` spec v0**: strict pydantic frontmatter, generated `schema.json`,
  `quackd validate` with fail-fast field-level errors; five starter ducks
  (`hello-world`, `find-and-kick`, `patrol-and-quack`, `follow-me`, `fetch`) bundled in the
  wheel and resolvable by name.
- **Verb registry**: built-ins mapping 1:1 to shipped robot behaviours (`walk`, `sit`,
  `stand`, `kick`, `grab`, `stand_up`, `stop`, `quack`, `gaze`, `get_frame`), composites
  (`search_scan`, `walk_to`, `approach_and`), and the reserved learned-verb interface.
- **Safety executor**: allowlist, confirm gates, budgets, dry-run, machine-enforced
  `abort_when` (battery, consecutive failures), heartbeat, kill switch (Windows-safe).
- **Agent loop** with one tool call per turn, `runs/<ts>/transcript.jsonl`, frames,
  `summary.json`, and `run.gif` on the simulator.
- **`sim2d`**: built-in 2D simulator (deterministic under `--seed`, deadman, kick cone,
  unreliable open-loop scoop), top-down + first-person duck-cam renders, GIF recorder,
  optional `--live` window.
- **Perception**: `ColorBlobDetector` (HSV, bearing + distance from apparent size) and a
  lazy `YoloDetector` extra.
- **Providers**: `anthropic` (adaptive thinking, refusal handling, thinking-block replay),
  `openai`, `grok` (xAI endpoint), `gemini`, and the scripted `fake`; all vendor SDKs are
  optional extras.
- **`quackd serve-mcp`**: the duck as MCP tools for Claude Code / Claude Desktop through
  the same executor; `docs/mcp.md` with verified client config; project `.mcp.json`.
- **Transports**: `sim2d` (default), `mock`, experimental `jsonrpc` for the real robot
  (verified `duck-ipc-proto` v16 vocabulary, fake-robotd tests), `websocket` stub.
- **`quackd doctor`**, `quackd list-verbs`, `quackd record`.
- Docs: architecture, duck spec, transport status, safety, learned verbs (v2), licenses,
  FAQ, MCP; 13 ADRs; LAUNCH.md; CONTRIBUTING.md; hero GIF (scripted pilot, labelled).

### Known limitations

- The hardware transport has never run on a Microduck (hardware ships Christmas 2026).
- The README hero is a scripted-pilot recording; a real-model recording needs an API key.
- Non-Anthropic default model IDs are unverified; override with `QUACKD_MODEL`.

[Unreleased]: https://github.com/rokbenko/quackd/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/rokbenko/quackd/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/rokbenko/quackd/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/rokbenko/quackd/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/rokbenko/quackd/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/rokbenko/quackd/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rokbenko/quackd/releases/tag/v0.1.0
