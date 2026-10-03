# ADR-0046: The Jetson is reached, not run on

**Status:** accepted · **Date:** 2026-09-25 · Supersedes [ADR-0044](0044-a-jetson-is-a-host-not-a-body.md) (which put quackd on the board) · Extends [ADR-0014](0014-local-llms.md) (a local model is the OpenAI provider at an address, and a preset's address can now move to the board) · Follows from [ADR-0003](0003-three-loops.md) (the model's loop and the steering loop are quackd's and the fifty hertz loop is the robot's, so nothing requires quackd to sit beside the robot's loop) · Implemented in `bridge/jetson/`, `quackd/host.py`, `quackd/adapters/host_camera.py`, `quackd/perception/host.py` and doctor's host section ([page](../guides/jetson.md))

## Context

0.13.0 answered "add a Jetson adapter" with [ADR-0044](0044-a-jetson-is-a-host-not-a-body.md):
a Jetson is a host, not a body. It read "host" as the computer that runs quackd, so quackd
went onto the board. `deploy/jetson/` held a Dockerfile that built quackd onto a plain Python
image and a compose file that gave the GPU to Ollama beside it.
`.github/workflows/jetson-image.yml` built that image for arm64 and ran it. `quackd doctor`
gained a section that read the board it was running on: its device tree, its release file, its
memory and swap, `nvpmodel` and Docker's default runtime.

Two days later that is the wrong direction, for two reasons.

The first is what a Jetson is for. On its own the board is already a robot's computer and its
GPU. What quackd adds is using the board, and what is on it, from the laptop where the person
running the task already is: the model on its GPU, the board's health, frames from a camera on
it, and detections computed on its GPU. None of that needs quackd on the board. The onboard work
gave the first two only to a quackd running on the board, and a local preset could already reach
a model server on the board from anywhere with `--base-url`.

The second is the arrangement the image existed for. ADR-0044 called it the interesting one: a
ToddlerBot's own Jetson holding its control daemon, a model server and quackd, three processes
on one board. It is also the risky one. A model server saturating the board's CPU and memory bus
is exactly the load that can starve a fifty hertz control loop, and on a humanoid a starved loop
is a fall. ADR-0044's Consequences named that as the first thing a person with a board should
measure, and nobody here has a board. Moving quackd off the board does not end that contest,
because a model server or a detector can still share a robot's board, but it takes quackd out of
it and makes the sharing a choice rather than the page's headline.

## Decision

**quackd stays on the laptop, and `--host` names the board.** `--host HOST[:PORT]` names a
machine quackd uses and never runs on. `--robot` still names the body, and the flag is the same
whatever the body is: no adapter, no extra, no `--robot jetson:...`. It is on `quackd run`,
`serve-mcp`, `doctor`, `robot add` and `robot edit`, with `--host-token` beside it, and
`QUACKD_HOST` and `QUACKD_HOST_TOKEN` are the rung below. The board is settled once, in
`quackd.host.resolve_host`, in the order every quackd setting uses: the flag, then the host a
registered robot stores, then `QUACKD_HOST`. The token climbs a ladder of its own, so
`--host 127.0.0.1` through an ssh tunnel still carries the token of the board the robot was
registered with, and a robot's token rides only when that robot stores a board, so it is never
sent to the environment's. `robots.json` keeps `host` and `host_token` for a robot, and
`quackd robot edit NAME --clear host` forgets both.

**One daemon answers on the board, and quackd ships it.** `bridge/jetson/quackd_jetson_hostd.py`
is to the Jetson what the camera daemon in `bridge/open_duck/` is to the Open Duck Mini's Pi. It
serves HTTP on port 9874, binds loopback unless told otherwise, and checks an optional token in
the `X-Quackd-Token` header, in constant time, on every path. It answers `/hello` (the protocol,
and what started: a camera, a detector, whether the board is a Tegra, and why anything did not),
`/healthz`, `/board`, `/snapshot.jpg` and `POST /detect`, and nothing else. It has no control
path, and a test that reads its source fails if one appears. It is written for Python 3.10,
which is JetPack 6's system Python, imports nothing from quackd, and imports OpenCV, Pillow,
torch and ultralytics only where it uses them, so a board without one of them starts anyway and
says in `/hello` what it lacks. The model server is still the person's own install, Ollama or
another, as it was under ADR-0044.

**The daemon ships raw text, and quackd does the reading.** `/board` is five files, three device
nodes, the output of `nvpmodel -q` and one line of `tegrastats`, as the board wrote them, and
`quackd doctor` reads them with the parsers it already had for the board it used to run on.
`/detect` answers with YOLO's own class names and pixel boxes, and quackd turns them into
labels, bearings and distances with `detections_from_boxes`, the function its in-process
`YoloDetector` uses, so the same boxes are the same detections on either machine. The daemon is
a file somebody copies onto a board by hand, and quackd is a release. A reading that is wrong on
a board nobody here has seen is then fixed in the release, and the daemon never has to learn
quackd's labels or its lens.

**HTTP for everything, and no ssh inside quackd.** quackd reaches the daemon and the model
server over plain HTTP and holds no login to the board. ssh stays what it was in 0.13, the
tunnel a person opens, now with the daemon's port in it, after which `--host 127.0.0.1` reaches
both through it. `bridge/jetson/README.md` has the line.

**A local preset's model server moves to the board.** `ollama`, `vllm`, `llamacpp` and
`lmstudio` keep their own port and path, and their `localhost` becomes the machine the host
names. The port in `--host` is the daemon's, and it is dropped. `LocalProvider`'s docstring is
the order:

> Where the server is, the first rung that is set wins, and nothing is probed:
>
> 1. `base_url`, which is `--base-url`: a URL given for this run is used exactly as given.
> 2. `host`, which is `--host` or the host a registered robot was stored with: the preset's
>    address moved to that machine (`on_host`), port and path kept.
> 3. `QUACKD_BASE_URL`, then 4. `OPENAI_BASE_URL`, each used exactly as given.
> 5. `QUACKD_HOST`: the preset's address moved to that machine.
> 6. The preset's own address, on localhost.
>
> A URL given anywhere is used as given, and a host only ever moves a preset. That is why
> `local`, which has no preset address, refuses a host on its own and asks for
> `--base-url`. The two hosts sit on different rungs on purpose. One typed for this run or
> registered with this robot is a decision about this run, and beats a `.env` line naming a
> model server's URL; `QUACKD_HOST` is the board you usually use, and must not.

`make_provider` hands the host to the local provider and to no vendor, and decision LLMs are not
moved: `--decision-url` already reaches a server on the board.

**The board's camera joins whatever body the run drives, and is primary only for a body with
none.** `HostCameraAdapter` wraps any built adapter the way `LoggedTransport` wraps a transport,
and `with_host_camera` adds the camera to the manifest before the task file is validated, so a
camera task on a blind body is not refused for a camera the run will have. A blind body gains
`camera` and the core verbs a camera unlocks for it: `observe` always, and `go_to`,
`search_scan` and `approach_and` where its mobility and intents allow. A body with a camera of
its own keeps its primary view, because its bearings are calibrated for that lens and a board on
a bench says nothing about which way the robot faces. The board's frame is then an extra view
named `host`, which the model is shown and the detector never reads. A frame older than two
seconds is dropped, and a snapshot that fails costs the picture and never the run, which is the
Open Duck camera's rule.

**The board's detector is used by itself on a real body, never on a simulator, and never
swapped out mid-run.** `HostDetector`, named `yolo@host`, sends the primary frame to
`POST /detect` as one JPEG. With no `--detector`, a run uses it when the daemon can detect and
the body is real. A simulated or mock backend keeps the colour detector, because YOLO does not
see a cartoon ball and the colour detector is tuned for what a simulator draws.
`--detector color` opts out. `--detector host` asks for the board's by name, on a simulator
too, and is refused before anything connects when no board is named or its daemon cannot
detect. `--detector yolo` is YOLO in this process, which needs `quackd[yolo]`.

A call that fails gives that frame no detections and keeps the reason, and the run keeps the
detector. The colour detector does not label the same things on a real camera, so a quiet
switch to it would change what `go_to` steers at with nothing in the record saying so, and the
header would name a detector the run had stopped using. Nor is a frame the board could not read
taken for an empty room. Read as the target out of view, it had `go_to` turn toward the last
bearing on every such frame, and a board that hangs costs each frame two seconds, so the count
of empty frames that would have stopped it never ran out before the verb's own timeout did.
`go_to` and `search_scan` stop the body on the first such frame instead and fail with the
board's reason, and `observe` reports nothing seen with the reason beside it. The loop writes
one `note` per outage rather than one per frame. The call runs in a worker thread, so `go_to`
keeps re-sending its last twist while the board answers, for one deadman window (`HOLD_TTL_S`,
0.3 s), and then sends a zero twist: a rosbridge base has no deadman, and a ToddlerBot's is fed
by its adapter's own keepalive, so neither would stop by itself.

**A run says which detector it used.** The run header gains a `detector` row, which names the
board's with its address, model and device, or the colour one "on this machine", and a `host`
row with the daemon's version and what it has. `run_start` records `detector` and, with a
board, a `host` object with the role the board's camera took at connect. A run keeps one
detector from start to end, so that one field answers for every detection in the transcript.

**A fleet is refused a board.** A host is one camera, one detector and one board's health, and a
fleet is several bodies. `--host`, or a host stored with a member, is refused for `--robots`,
`--flock`, a task file with a `flock:` block and several robots alike, in `run` and in
`serve-mcp`, before anything connects. `QUACKD_HOST` is not refused. It is the board you usually
use rather than a claim about these bodies, and for a fleet it moves only the local presets'
model server. `--detector` is refused for a fleet as well.

**A board that does not answer refuses the run.** `run` and `serve-mcp` ask the daemon for
`/hello` before anything is built, and refuse while nothing is powered when it does not answer,
because a run given `--host` was promised a camera or a detector it would not have. Somebody who
wants only the model on the board keeps `--base-url`, which asks nothing of the daemon.

**`quackd doctor` reads the board over the network, and never this machine's files.** The
section appears only when a host is named, by the flag, by the robot `--robot` names or by
`QUACKD_HOST`. It shows the daemon, its camera, its detector and the device the detector runs
on, the daemon's health, and, parsed from `/board`, the board's model, the L4T release and which
JetPack that is, the memory it shares with the GPU, whether the only swap is zram, the GPU
device node, the power mode and the GPU's load from one `tegrastats` line. The four local
presets are probed at the host, and an Ollama that answers is asked `/api/ps` where each loaded
model sits: all on the GPU, a share of it, or on the CPU, which on a Tegra is the generic arm64
build's pitfall. A host that does not answer fails the report the way a bad `--address` does,
and nothing the board says ever changes the verdict. doctor no longer opens `/proc` or runs a
subprocess on the machine it runs on, and a test that parses `doctor.py` keeps it that way. In
`--json` all of it is under `host`, the parsed board as `host.jetson` and the presets asked
there as `host.servers`, and the top-level `jetson` key 0.13 added is gone.

## Why not

**Keeping the container beside the new flag.** It would have kept the arrangement this decision
moves away from as a supported path, with a Dockerfile to keep from going stale and a workflow
to keep green, for a configuration nobody here had run. The `v0.13.0` tag keeps all of it, and
the 0.13.0 changelog entry links there.

**A Jetson adapter.** ADR-0044's reason stands. A board has no embodiment, no mobility and no
intents, and there is nothing for `send_intent()` to do on one. `--host` is one flag for every
body, which is what lets a board's camera join an arm and a biped alike.

**ssh from inside quackd.** quackd would hold a login to the board, and everything it asked
would run with that account's reach. A daemon that answers five paths and can do nothing else
is a smaller thing to let a laptop reach, and the tunnel a person opens keeps the wire private
without quackd holding a key.

**A daemon that parses.** Two parsers for the same files are two answers that can disagree, and
the one on the board is a file copied by hand that no upgrade of quackd reaches. Raw text also
lets doctor show the `tegrastats` line whole, which is the line people with a board are asked to
send.

**A `/detections` endpoint that runs YOLO on the board's own camera.** quackd detects on the
primary frame, which is the body's own camera whenever it has one, so such an endpoint would
serve one of the two arrangements and add a second code path. It is the next step if the
daemon's `ms` and a frame's `X-Frame-Age` show that the round trip is too slow.

**Falling back to the colour detector when the board stops answering.** It would keep
detections flowing and make them mean something else, which is worse than none.

**The board's detector on a simulator by default.** YOLO does not see the simulator's cartoon
ball. Asked for by name it runs, and the header row says it was asked for on a simulator.

## Consequences

- **Nothing in this decision has been run on a Jetson by this project.** The daemon was driven
  in-process, against a board made of files, a fake ultralytics and torch with CUDA and
  without, a stubbed OpenCV capture and a one-line stand-in for `tegrastats`. Its source is
  parsed with Python 3.10's grammar and has not been executed under 3.10. The client, doctor,
  the host camera and the host detector were driven against a fake of the protocol on loopback
  (`tests/fake_jetson_hostd.py`), and `tests/test_jetson_host_contract.py` runs the real daemon
  against the real client and doctor, which is what catches the two halves drifting apart. All
  of it proves the protocol is read as written, and none of it proves anything about a board.
- **What that leaves unmeasured is everything a board would say.** Whether the CSI pipeline
  opens and delivers the IMX219's whole field of view. Whether ultralytics on JetPack, with
  NVIDIA's torch, detects on CUDA, and how long a detection takes there. How long a snapshot
  and a detection take over a robot's Wi-Fi, inside `go_to`'s steering loop, which re-sends its
  last twist for 0.3 s while it waits and no longer, so a slower round trip lets the body stop
  between frames. Whether a model server and the detector on a robot's own board leave its fifty
  hertz loop alone. The unit runs the daemon at `Nice=10`, under `MemoryMax=2G` and first in
  line for the OOM killer (`OOMScoreAdjust=500`), which is a precaution and not a measurement.
  And whether a real board's files and `tegrastats` line look like the fixtures, which were
  written from NVIDIA's documentation.
- **The contention caveat changes subject.** It was about quackd sharing a robot's board with a
  model server. quackd is off the board now, and the caveat is about a model server or the
  detector on a robot's own board. Of the seven bodies, the ToddlerBot is still the only one
  that carries a Jetson.
- **A fleet cannot use a board.** `--host`, or a host stored with any member, refuses a fleet
  before anything connects. A fleet that wants the model server on a board still reaches it
  through `QUACKD_HOST` or `--base-url`.
- **`quackd doctor --json` moved the board.** It is `host.jetson` now, beside the hello, the
  health and the presets asked at the host. A script that read 0.13's top-level `jetson` key
  finds no such key.
- **`robots.json` gains `host` and `host_token`,** written only while they hold something, so
  a registry that never named a board stays readable by 0.12 to 0.14, which refuse unknown
  keys. One robot stored with a host makes the whole file unreadable to them, every robot in it
  included, until this release clears it with `quackd robot edit NAME --clear host`.
  `--host-token` joins `--api-key` and `--token` among the flags whose value never reaches a
  run record's command line.
- **A daemon on the board is a new surface.** Port 9874 joins 9871 and 9872 (the Open Duck
  Mini) and 9873 (the ToddlerBot), and the tunnel line gains it. `/snapshot.jpg` is a live view
  of wherever the board is, and `POST /detect` is a GPU anyone who can reach it can keep busy.
  That is why the daemon binds loopback by default, warns when it is bound anywhere else with no
  token, and refuses to start when a named token file is missing or empty.
- **A body with the board's camera describes itself differently.** `manifest.digest()` hashes
  the whole manifest apart from its id and backend, and the board's camera is written into it:
  as `extras.host_camera` for every body, and as `camera` and the verbs it unlocks for a blind
  one. So serve-mcp's `robot_list` reports a different digest for a body with the board's camera
  than for the same body without one, whether or not the body has a camera of its own.
  `quackd announce` takes no `--host`, and announces a body as its adapter describes it. Memory
  keys are `adapter:backend` or a registered name, so nothing a robot remembers moves.
- **The JetPack table is still one fact written twice,** in `docs/jetson.md` and in
  `_JETPACK_FOR_L4T` in `quackd/doctor.py`. The test that holds them together moved to
  `tests/test_docs.py` when `tests/test_deploy_jetson.py` went with the image.
