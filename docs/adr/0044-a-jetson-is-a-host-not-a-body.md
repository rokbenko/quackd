# ADR-0044: A Jetson is a host, not a body

**Status:** superseded by [ADR-0046](0046-the-jetson-is-reached-not-run-on.md) · **Date:** 2026-09-22 · Extends [ADR-0014](0014-local-llms.md) (a local model is the OpenAI provider at an address, and that address may be loopback on the robot's own board) · Follows from [ADR-0003](0003-three-loops.md) (the loop that calls the model runs two orders of magnitude below the body's, so where it runs is a deployment choice and not an architectural one) · Declines to extend [ADR-0017](0017-robot-adapters-and-manifest.md), which is the whole decision · Implemented in `deploy/jetson/` and the Jetson section of `quackd/doctor.py` ([page](../guides/jetson.md))

**Superseded 2026-09-25 by [ADR-0046](0046-the-jetson-is-reached-not-run-on.md):** quackd no
longer runs on the Jetson. It runs on the laptop and reaches the board with `--host`, through a
daemon it ships for the board. The image and the compose file in `deploy/jetson/`, the arm64
workflow that built the image and ran it, the `.dockerignore` whose only consumer was the
Dockerfile, and `quackd doctor`'s reading of the board it runs on were removed, and the files
are still at the `v0.13.0` tag. What survives is the half of this decision that was about the
board rather than about where quackd runs. A Jetson is still not an adapter or a body. quackd
still carries no CUDA: its core imports no GPU library, and the daemon uses whatever torch and
ultralytics a person installed on the board. The GPU still belongs to what runs on the board,
the model server and now the daemon's detector, and quackd reaches them over the network, where
it used to reach the model server over loopback. Everything below is left as the record of what
0.13.0 shipped.

## Context

The question arrives as "add a Jetson adapter", and the shape of that request is the reason
this ADR exists rather than a page on its own.

An NVIDIA Jetson is a small arm64 computer with a GPU wired to its memory controller. It is
what people reach for when they want the model on the robot rather than in somebody's data
centre, and it is already inside quackd's world in one place: a ToddlerBot carries one, and
`bridge/toddlerbot/` is the daemon quackd ships to run there ([ADR-0028](0028-toddlerbot.md)).

There were two obvious ways to answer, and both are wrong in an instructive way.

**A `jetson` adapter.** The entry point group exists, adding one is a documented afternoon's
work ([ADR-0037](0037-adapters-are-their-own-packages.md)), and it would put the word Jetson
in `quackd list-adapters` where people would look for it. It is also incoherent. An adapter
answers one question, "what is this body and what can it do", as a manifest with an
embodiment, a mobility, intents, sensors and a safety authority. A Jetson has none of those
and there is nothing for `send_intent()` to do on one. The row would have to describe a
compute module as a robot, and every refusal quackd makes is built on that description being
true.

**A CUDA base image.** The received wisdom for anything on a Jetson is to build FROM
`nvcr.io/nvidia/l4t-jetpack`, and the version pinning that follows from it fills most of what
anybody writes on the subject. It is several gigabytes of CUDA, cuDNN and TensorRT, and quackd
calls none of it. The core imports no GPU library at all; the physics extra pins onnxruntime to
its CPU provider by hand in `quackd_microduck.sim3d`; and the only extra that would want a GPU
is `yolo`, which is ultralytics, which is torch, and which is AGPL besides.

## Decision

**A Jetson is a host.** It runs the quackd process, which is the role a laptop plays today. No
entry point, no extra, no adapter, no `--robot jetson:...`, and nothing to install for it. The
noun for a computer that runs quackd is already "your machine" and a Jetson is one.

**The GPU belongs to the model server, and quackd is the CPU half.** This is the sentence the
whole deployment falls out of. Ollama, `llama-server` or vLLM holds the GPU in its own process;
quackd reaches it over loopback through the local presets that have existed since
[ADR-0014](0014-local-llms.md). So the quackd image is a plain Debian Python image with no CUDA
in it, buildable on any machine, and `deploy/jetson/compose.yml` gives `runtime: nvidia` to
Ollama and nothing at all to quackd. The one arrangement this unlocks is the interesting one: a
ToddlerBot's own Jetson holding its control daemon, a model, and quackd between them, three
processes on one board talking over `127.0.0.1`.

**The image is pinned by `uv.lock`, not by PyPI.** `deploy/jetson/Dockerfile` installs from the
checked out source with `--frozen --no-editable`, so it is reproducible at a commit and it can
be built from a branch that has never been released. That also keeps it honest about the
workspace: the adapter is built as a real wheel rather than linked back into a source tree the
runtime stage does not have.

**`quackd run` is a command and never a service.** It does one task, declares a verdict and
exits. The compose file gives it `restart: "no"` and hides it behind a profile, because a
restart policy on a robot task re-runs that task every time it finishes and again at every
boot. `bridge/open_duck/quackd-duck-bridge.service` already sets `Restart=no` and gives the
longer version of this argument. There is no systemd unit in this repository for quackd on a
Jetson: Ollama's own installer writes one for the thing that is actually a service.

**`quackd doctor` reads the board, and can never fail because of it.** A Jetson section naming
the board, the L4T release, the shared memory, whether the swap is only zram, the GPU device
node, the power mode and Docker's default runtime. Every one of those is informational and none
of them touches `ok`: quackd runs on a board with all of them wrong. It is detected from
`/proc/device-tree/compatible` as well as `/etc/nv_tegra_release`, and it is meant for `doctor`
run on the board itself: `/proc/device-tree` points into `/sys/firmware`, which Docker masks in
any container that is neither privileged nor started with `--security-opt
systempaths=unconfined`, and a plain Python image has no release file, so in the quackd
container the section will most likely not appear at all.

**Nothing is published.** No image is pushed to a registry. `.github/workflows/jetson-image.yml`
is set up to build for `linux/arm64` on a native arm64 runner and then run quackd inside the
result, and to stop there. It first ran green on 2026-09-23. An image with a pull command
beside it is a promise that somebody ran it on the hardware it is named after, and nobody has.

## Why not

**A `quackd[jetson]` extra.** There is nothing for it to install. An extra that pulls no
package and exists to be greppable would be the adapter mistake with a smaller blast radius.

**Publishing to GHCR now.** It is one line in the workflow and it can be added the day somebody
reports a run. Until then the page tells people to build from a checkout, which is also what
makes the `uv.lock` pinning meaningful.

**Pinning the base image by digest.** The Python packages are pinned exactly by the lock, which
is the half that decides what executes. Pinning Debian's patch level too would mean a
deliberate bump for every security update on a file nobody would remember to revisit.

**A hardware report issue template.** Three of the seven bodies have one today, and all seven
have a row in `docs/adapter-status.md` that a hardware run would flip. A Jetson flips no row: it
is not a robot and it changes nothing about which upstream API quackd speaks. The page asks for
a Discussion and a transcript instead, the way [local-llms.md](../guides/local-llms.md) does.

## Consequences

- There is now a container in this repository, which is a kind of artifact it has not had
  before, and a Dockerfile goes stale in ways a Python file does not. The CI job is what
  notices, and it runs only when `deploy/jetson/`, `.dockerignore`, the lock, the packaging or
  the workflow itself changes.
- `quackd doctor` gained its first subprocess. `_run_quiet` refuses to fork a binary that is
  not on `PATH`, closes stdin and gives up after three seconds, because doctor is what people
  run when something is already wrong and is the worst place to add a new way to hang.
- The version table in `docs/jetson.md` and `_JETPACK_FOR_L4T` in `quackd/doctor.py` are the
  same fact written twice, which is a drift waiting to happen.
  `tests/test_deploy_jetson.py` holds them to each other.
- The Microduck's board is a Radxa and the Open Duck Mini's is a Raspberry Pi Zero 2 W. Neither
  is big enough for this, and saying so is part of the page: the arrangement described here is
  available on exactly one of the seven bodies today.
- **Nothing here has been run on a Jetson.** The image was built for arm64 and run under
  emulation on the machine that wrote this. CI is set up to repeat that on a native arm64
  runner with no GPU, and it first ran green on 2026-09-23. Between them they prove quackd
  runs on aarch64 Linux and prove nothing about Ollama, about the NVIDIA container runtime,
  or about whether a model server and a fifty hertz control loop can share one board
  without the loop suffering. That last one is a humanoid
  falling over if it is wrong, and it is the first thing a person with a board should measure.
