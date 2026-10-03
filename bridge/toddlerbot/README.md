# quackd's ToddlerBot daemon

Run this on the robot's Jetson, in upstream's own conda environment. It is the only quackd
code that ever touches a motor on this robot.

## Why it exists

ToddlerBot has no network API of any kind. No socket, no daemon, no IPC: it is a Python
library whose control loop opens serial ports in-process. Something has to be on the robot,
so quackd ships this, the way it ships one for the Open Duck Mini.

Three reasons it is a daemon rather than a thin shim, and the second is the important one.

**A verb is episodic and this robot is not.** `RealWorld.step()` is a no-op, so nothing times
out and nothing re-arms: the last commanded pose is held forever. A humanoid frozen mid-stride
while a language model thinks is a humanoid on the floor. So the fifty hertz loop lives here,
and quackd's intents only nudge what it is already doing.

**Upstream protects nothing, and its shutdown drops the robot.** `set_motor_target` clamps
nothing and never reads the joint limits that exist; the motors are in `extended_position`
mode, so the firmware limits are off too; there is no watchdog, no timeout, no e-stop and no
reset anywhere; a dropped packet returns an all-zeros observation that looks exactly like
every joint at zero; and a C level `atexit` handler disconnects every client on any normal
interpreter exit, which disables torque and drops a standing robot. There is no Python signal
handler anywhere upstream, so `SIGTERM` does not even reach that.

## What it does

Ten things upstream has not got, enumerated in this file's own docstring in
`quackd_toddlerbot_bridge.py` rather than a second time here. They fall into three groups:
**nothing exits without settling first** (signal handlers, excepthooks, a hard deadline
around a `close()` that holds the GIL), **nothing is commanded that cannot be trusted** (no
command at all before the first reading, an all-zeros detector, a clamp, a rate limit, a
refusal of non-finite targets, keyframes paced to what the body can follow), and **nothing
fails silently** (a loop that survives a raising tick instead of dying while the socket
answers healthy, a construction watchdog, dispatch by type rather than by class name).

The deadman is the opposite of the Open Duck Mini's. A duck that stops walking stands still.
A humanoid that stops walking mid-stride falls. So on silence this slews to the safe pose and
**holds**, and it never torque-offs. `stop` means hold the last verified-good pose, because
there is no velocity at this hardware boundary at all.

## The protocol

Line delimited JSON-RPC 2.0 over TCP on port **9873**, which is quackd's own at both ends. The
Open Duck Mini already takes 9871 for its bridge and 9872 for its camera daemon, and
SECURITY.md tells people to tunnel that pair, so this robot starts after both.

The client also sends `bot.keepalive` on its own timer, several times a second, for as long
as it is connected. That is the only thing that stops the deadman firing part way through a
verb: `stand` takes three seconds and the deadman fires after half of one, so without it
every long verb would be cancelled underneath itself by the safe-pose slew. Reading state or
a frame deliberately does **not** count, and neither does a method this daemon has never
heard of, so a client built against a newer protocol cannot hold the deadman off with calls
this one is refusing.

Set `QUACKD_TODDLERBOT_TOKEN` (or pass `--token`) and the daemon refuses unauthenticated
clients. It travels in the handshake and never in the address, because addresses get printed
and land in transcripts.

## Running it

It runs **in upstream's own environment**, which is the third of those reasons. Upstream hard-pins
`numpy==1.26.4`, `jax==0.4.28`, `jaxlib==0.4.28`, `setuptools==75.6.0`,
`moviepy==1.0.3` and `opencv-python==4.9.0.80`. Those cannot share a process with
quackd's own dependencies, and quackd is not going to ask anyone to downgrade numpy
to drive a robot. This runs over there and speaks a socket, so neither side has to
win.

```bash
git clone https://github.com/hshi74/toddlerbot ~/toddlerbot
git -C ~/toddlerbot checkout 84e02d14261292eec5d06f896e3145b35c54856c
pip install -e ~/toddlerbot && pip install 'mujoco==3.3.4' 'scipy>=1.14'
python quackd_toddlerbot_bridge.py --robot toddlerbot_2xc --toddlerbot ~/toddlerbot
```

`mujoco` is not a declared dependency upstream. It arrives transitively and unpinned, so pin
it yourself.

`--camera left|right`, `--walk-policy NAME` and `--gripper` say what this robot has, and
**each is checked rather than believed**. A camera that will not open is logged and the
robot simply has none, so quackd never offers `observe`. A walk checkpoint is a wandb
artifact upstream neither publishes nor checks in, so `--walk-policy` names a directory
under `ckpts/` that you supply, and the daemon refuses to start rather than reaching for
wandb from a robot. The handshake reports what actually loaded, including which motions
were readable, and quackd's manifest is built from that answer.

`--fake` runs the whole daemon and protocol against a simulated body, with no robot and no
upstream installed, which is what CI does. `--once` sets up, reports what it found, and exits.

**The daemon refuses to actuate without a zero calibration** (`motors.yml`, which is
gitignored upstream so a fresh clone has none). Without it every commanded angle is offset by
however that particular robot was assembled. Run upstream's `calibrate_zero` first.

**quackd's host daemon can run beside this one, and quackd stays on the laptop.** quackd
never runs on this board. `bridge/jetson/quackd_jetson_hostd.py` can, and it gives the laptop
the board's health and YOLO on its GPU, which `quackd run --host` reaches on port 9874 while
this daemon keeps 9873 ([`bridge/jetson/`](../jetson/README.md)). Start it with `--camera none`,
because this daemon owns the robot's cameras and two processes cannot own one camera. It runs
on the board's system `python3`, outside upstream's conda environment, needs Python 3.10 or
newer (JetPack 6 ships 3.10), and imports nothing from quackd. A model server can sit beside
both ([docs/guides/jetson.md](../../docs/guides/jetson.md)). What to watch is contention, because a
model server or a detector saturating the board is what can starve the loop this file exists to
protect, and nobody has measured it.

## Rules this file lives by

- **It never imports quackd.** quackd's dependencies do not belong on a robot, and a test
  enforces this by reading the file.
- **It ships in the sdist and never in the wheel**, so `packages` stays `["quackd"]`.
- **It is testable with no hardware.** Everything above the `Robot` boundary is pure and takes
  plain arrays, so the clamp, the rate limit, the dropped-read detector and the slew are all
  unit tested, and `--fake` exercises the loop and the protocol end to end.

## Safety

Read `docs/adapters/toddlerbot/hardware-checklist.md` before the first bring-up and follow its
order. It keeps the feet off the ground until step 13, and the two steps that matter most are 11 and
12, both taken on the stand: pull the network cable mid-move and confirm the deadman slews
rather than drops, then send `SIGTERM` and confirm the same.

This robot cannot get up by itself. There is no get-up policy for this body at this pin, so a
fall ends the run and needs a human. Ask for less than you think.
