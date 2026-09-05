# The quackd daemons for an Open Duck Mini v2

This directory is the only part of quackd that runs on a robot. It is not a Python package,
it is never imported by quackd, and the bridge needs nothing but the standard library and
numpy, which your duck's Pi already has.

Two processes, because they have very different jobs and very different budgets:

| File | What it is | Port |
|---|---|---|
| `quackd_duck_bridge.py` | upstream's own walk loop, with the gamepad it reads replaced by a socket | 9871 |
| `quackd_duck_camd.py` | one JPEG over HTTP, captured on a timer | 9872 |

For what this robot is to quackd, its verbs and what it cannot do, see
[`docs/adapters/open_duck.md`](../../docs/adapters/open_duck.md). For the order to bring one
up in, see [`docs/open-duck-hardware-checklist.md`](../../docs/open-duck-hardware-checklist.md).
This page is the reference for the two daemons themselves.

## Why the bridge exists

The Open Duck Mini v2's runtime has no network control API. Its command source is a local
pygame gamepad and its only socket checks the IMU. So driving it from a laptop needs
something on the robot that turns a socket into that gamepad's seven floats.

It does not reimplement the control loop. It **is** upstream's loop: it rebinds the class
upstream imports to read a gamepad, then runs upstream's own script. Three things follow.

- **The Feetech serial bus keeps exactly one owner**, because there is still exactly one
  process. Do not run this and `v2_rl_walk_mujoco.py` at the same time.
- **Nothing upstream is copied.** `Open_Duck_Mini_Runtime` carries no licence file, so it is
  yours to install and not ours to ship. The bridge imports what you installed.
- **Going limp is unreachable.** The only channel from the network to the body is seven
  floats and a few buttons. There is no method to refuse, because there is no method.

## Safety, in the order it matters

- **The deadman is evaluated by the control loop, not by a timer.** If no command arrives
  for 300 ms the three velocities go to zero, and that check runs inside the call upstream
  makes every tick. A server thread that is starved, wedged or dead still stops the duck.
- **The head holds instead of zeroing.** A velocity dropping to zero is what releasing a
  stick does, and the policy has seen it. A neck snapping to centre is not.
- **Head control is off unless you ask for it**, and then it is clamped to 80 percent of
  upstream's own range and rate limited, because upstream warns that head control can break
  the head. quackd never presses upstream's head-control mode button.
- **`stop` is a zero twist with torque still on.** Stop is not limp.
- **A fall is terminal, and nothing here detects one.** This robot has no get-up policy, and
  the bridge has no fall detection either, so it reports posture as unknown rather than
  guessing standing. Watch it yourself.
- **The only e-stop is the power switch.** Keep a hand near it.
- **Both daemons bind loopback by default.** The bridge walks a robot and the camera shows
  your home. Prefer `ssh -L 9871:127.0.0.1:9871 -L 9872:127.0.0.1:9872 your-pi` over binding
  either one wide.

## Getting the files onto the Pi

`bridge/` ships in the sdist and in the repository, never in the wheel, so `uvx quackd` and
`pip install quackd` do not give you these files. Clone the repo on the Pi, or copy the two
`.py` files across:

```bash
git clone https://github.com/rokbenko/quackd
cd quackd/bridge/open_duck
bash install.sh          # read it first: it checks rather than fixes
```

`install.sh` verifies the runtime and its virtualenv, that nothing else holds the serial
bus, I2C and the BNO055, Wi-Fi power save, and who owns the camera. It installs both daemons
to `/opt/quackd`, writes a token to `/etc/quackd/duck-bridge.token`, and installs both
systemd units without starting them.

## The bridge, flag by flag

```
python quackd_duck_bridge.py serve [flags]
python quackd_duck_bridge.py check          # what this duck would advertise, as JSON
```

| Flag | Default | What it does |
|---|---|---|
| `--script` | none | upstream's `v2_rl_walk_mujoco.py`. Required unless `--fake` |
| `--script-arg` | none | repeatable, passed to that script verbatim (this is how the ONNX path gets through) |
| `--bind` | `127.0.0.1` | loopback on purpose: this port walks a robot |
| `--port` | `9871` | |
| `--duck-config` | `~/duck_config.json` | where the capability flags come from |
| `--token-file` | `/etc/quackd/duck-bridge.token` | if the file exists, a client must send that token |
| `--camera-url` | none | the snapshot URL to advertise. **Without it the bridge reports no camera** |
| `--deadman-ms` | `300` | how long silence is tolerated before the velocities go to zero |
| `--max-vx`, `--max-vy`, `--max-vyaw` | `0.15`, `0.2`, `1.0` | your own ceilings, applied on top of upstream's |
| `--enable-head` | off | EXPERIMENTAL. Upstream warns head control can break the head |
| `--head-safety` | `0.8` | the fraction of upstream's head range quackd will use |
| `--fake` | off | a synthetic 50 Hz loop, no robot and no runtime needed |
| `--seconds` | forever | stop after this long. Useful with `--fake` |

`check` prints the handshake this duck would send: its capabilities, its limits, its deadman
window and its safety story. With no `duck_config.json` every capability reads false, which
is worth knowing before you read the output as a hardware fault.

## The camera, flag by flag

```
python quackd_duck_camd.py [flags]
```

| Flag | Default | What it does |
|---|---|---|
| `--bind` | `127.0.0.1` | it serves a live view of wherever your robot is, with no authentication |
| `--port` | `9872` | |
| `--fps` | `1.0` | it captures on a timer, so a slow client cannot stall the capture |
| `--size` | `512` | square, matching upstream's own camera code |
| `--rotate` | `90` | upstream rotates 90 degrees clockwise, so the module is mounted on its side |
| `--no-swap-rb` | off | **almost certainly not what you want.** The default is correct: picamzero hands back RGB-ordered data and the swap turns it into what the JPEG encoder expects, matching upstream's own camera code. Setting this inverts the image, which lands an orange ball inside the *person* hue range and roughly triples its reported distance. Channel order is a property of picamera2's configuration, not of your module or how it is mounted, so point wrong-looking colours at white balance and `--rotate` |
| `--duck-config` | `~/duck_config.json` | used only to refuse when the runtime owns the camera |
| `--fake` | off | a synthetic duck's eye view with a ball on the floor |
| `--seconds` | forever | |

It serves `/snapshot.jpg` and `/healthz`, and answers nothing else. There is no control path
in the file at all, so the camera port cannot move the robot even if you expose it.

**Two processes cannot own one camera.** If `duck_config.json` says
`expression_features.camera` is true, the robot's own runtime constructs a `Cam` and owns the
device, and this server refuses to start. Set that flag false and let quackd serve frames.

## Try the whole thing with no robot

```bash
python quackd_duck_camd.py --fake --size 256 --seconds 300
python quackd_duck_bridge.py serve --fake --enable-head --seconds 300 \
    --camera-url http://127.0.0.1:9872/snapshot.jpg
```

Then from a laptop, against that fake. Note `--camera-url` on **both** sides when you are
tunnelling: the bridge advertises a URL from its own point of view, and passing one to quackd
cannot rescue a bridge that was started without one, because the manifest is narrowed from
what the bridge reports.

```bash
quackd doctor --robot open_duck:bridge --address tcp://127.0.0.1:9871
quackd run open-duck-lookout --robot open_duck:bridge --address tcp://127.0.0.1:9871
quackd run open-duck-scout --robot open_duck:bridge --address tcp://127.0.0.1:9871 \
    --camera-url http://127.0.0.1:9872/snapshot.jpg
```

If the bridge has a token, add `--token <it>` or set `QUACKD_DUCK_TOKEN`. `quackd doctor`
with an address connects and prints what the robot actually reported, which is the only way
to see the difference between the description and your duck.

## What is honestly degraded

- **`say` and `quack` play a random sound.** The only channel the bridge has to the speaker
  is upstream's random-sound button, so the mood quackd picks is logged but selects nothing.
  The reply says `"how": "the pad's sound button"` for exactly this reason.
- **Nothing detects a fall.** Posture reads unknown, never standing.
- **Nothing reports a battery**, so a `battery below N%` abort can never fire.
- **Whether head commands do anything without upstream's mode button is unverified.** quackd
  will not press that button to find out.

## Status

Never run on a physical duck by us. The protocol is exercised end to end in quackd's test
suite, against this daemon, over loopback. That makes "the protocol works" a fact and keeps
"the duck walked" a claim nobody has earned yet. If you run it on yours, please open an
issue: [`docs/open-duck-hardware-checklist.md`](../../docs/open-duck-hardware-checklist.md)
says what to send.
