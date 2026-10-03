# quackd and an NVIDIA Jetson

A Jetson is small enough to ride on a robot and has a GPU, and on its own it is already a
robot's computer. quackd never runs on it. quackd stays on your laptop, and `--host` names the
board, which gives the quackd there four things: the model on the board's GPU, the board's
health, frames from a camera on the board, and detections computed on its GPU. The first is
your own model server, installed on the board. The other three come from one small daemon quackd
ships for the board, [`bridge/jetson/`](../bridge/jetson/README.md), the way it ships a camera
daemon for the Open Duck Mini's Pi.

Nothing on this page has been run on a Jetson by this project. It is written from NVIDIA's own
documentation, and quackd's side of it, the daemon, the client, the detector and the camera,
was exercised against fakes on a laptop. The [Status](#status) section at the end says exactly
what that does and does not prove, and what to send back if you run it on yours.

> [!NOTE]
> A Jetson is not a robot. It never appears in `quackd list-adapters` and there is no
> `--robot jetson:...`. An adapter is a body with a manifest and intents. A Jetson is a
> computer, and `--host` names it beside the body `--robot` names
> ([ADR-0044](adr/0044-a-jetson-is-a-host-not-a-body.md) decided the first half,
> [ADR-0046](adr/0046-the-jetson-is-reached-not-run-on.md) the second).

## Reached, not run on

The arrangement is two machines. The laptop runs quackd: the loop, the executor, the gates and
the record. The board runs what needs the board: the model server on its GPU (Ollama, or
`llama-server` or vLLM), quackd's host daemon, and, when the board is a robot's own computer,
that robot's daemon. quackd reaches the model server through the local presets that already
exist ([local-llms.md](local-llms.md)), and the host daemon over plain HTTP on port 9874.

`--host HOST[:PORT]` names the board on `quackd run`, `serve-mcp`, `doctor`, `robot add` and
`robot edit`, and `--host-token` goes with it. The port is the daemon's, 9874 unless you changed
it. `--robot` still names the body, so the flag is the same whatever the body is. Without the
flag, a command uses the host a registered robot keeps, then `QUACKD_HOST`. The token is found
in the same order, `--host-token`, then the robot's, then `QUACKD_HOST_TOKEN`, and a robot's
token is only used when that robot keeps a board. A `--host-token` typed where nothing names a
board refuses a run or a server, as `robot add` refuses one without `--host`, and fails
`doctor`'s host section, while a `QUACKD_HOST_TOKEN` with no board to go to is left unread.
When the daemon refuses a token, the refusal says how to change the one that was sent. For a
token the robot keeps, that is `quackd robot edit NAME --host-token`, because the robot's
token outranks `QUACKD_HOST_TOKEN` and setting the variable changes nothing. A run asks the
daemon's `/hello` before anything connects to the body, and refuses when it does not answer:

```
✗ error: --host 127.0.0.1:1 did not answer: 127.0.0.1:1 did not answer /hello within 2s: is
the board up, and is quackd-jetson-hostd running on it?
  quackd doctor --host 127.0.0.1:1 shows what the board says; drop --host to run without it
```

A host is one camera and one detector, so a flock is refused with `--host`, and with a host
stored with one of its members. `QUACKD_HOST` does not stop a flock: it is the board you usually
use rather than a claim about these bodies, and for a flock it moves only a local preset's model
server. A robot keeps a board the way it keeps an address:

```bash
quackd robot add duck open_duck:bridge --address tcp://127.0.0.1:9871 --host 127.0.0.1
```

```
✓ added duck: open_duck:bridge at tcp://127.0.0.1:9871, host 127.0.0.1
  quackd run <duck> --robot duck
```

`quackd robot edit duck --clear host` forgets the board and its token together.

Three things about the board are worth knowing before anything else.

**The CPU and the GPU share one pool of memory.** There is no separate VRAM figure to look up.
On an 8 GB Orin Nano the model weights, the KV cache, the operating system, your desktop if you
left one running, and the host daemon's detector all spend the same 8 GB. It is the reason the
model size table below matters more here than on a desktop with a discrete card.

**`nvidia-smi` is not the tool.** It is the front end for NVML, and NVIDIA's CUDA for Tegra
application note puts NVML under unsupported features: it is supported on Thor and later
only. Use `tegrastats`, whose `GR3D_FREQ` field is the GPU, or `jtop` from `jetson-stats`
for the same numbers with a screen around them. The host daemon reads one `tegrastats` line
for `quackd doctor --host`.

**One of quackd's seven bodies already carries a Jetson.** A ToddlerBot has one on its back,
and `bridge/toddlerbot/` is the daemon that runs there
([adapters/toddlerbot.md](adapters/toddlerbot.md)). That is the case where the host daemon,
and perhaps a model server, share a board with a robot's control loop, and it has a section of
its own below. The Microduck's onboard computer is a Radxa and the Open Duck Mini's is a
Raspberry Pi Zero 2 W, and neither is this. For those, a Jetson is a second machine on the
bench, lending its model and its detector to a robot that has neither.

## Which JetPack

Be on JetPack 6.2 or later in the 6.x line. That is where the wheels, the containers and the
Ollama bundles all exist today.

| L4T | JetPack | Ubuntu | Python | Modules |
|---|---|---|---|---|
| `r39.2.1` | 7.2.1 | 24.04 | 3.12 | Thor and Orin |
| `r39.2.0` | 7.2 | 24.04 | 3.12 | Thor and Orin |
| `r38.4.0` | 7.1 | 24.04 | 3.12 | Thor |
| `r38.2.1` | 7.0 | 24.04 | 3.12 | Thor |
| `r38.2.0` | 7.0 | 24.04 | 3.12 | Thor |
| `r36.5.2` | 6.2.3 | 22.04 | 3.10 | Orin |
| `r36.5.0` | 6.2.2 | 22.04 | 3.10 | Orin |
| `r36.4.4` | 6.2.1 | 22.04 | 3.10 | Orin |
| `r36.4.3` | 6.2 | 22.04 | 3.10 | Orin |
| `r36.4.0` | 6.1 | 22.04 | 3.10 | Orin |
| `r36.3.0` | 6.0 | 22.04 | 3.10 | Orin |

The L4T column is off NVIDIA's JetPack archive and the Ubuntu column off each release's own
download page, both read on 2026-09-22. **NVIDIA publishes no Python version at all**: that
column is Ubuntu's own default `python3` for the release beside it. All of it is version
sensitive by nature, so check it against the board in front of you. `quackd doctor --host`
names your board's JetPack out of this same table in `quackd/doctor.py`, and
`tests/test_docs.py` holds the two copies to each other so the page and the command cannot
drift apart. The releases are spelled in three parts here, which is the form the parser
normalises to, so JetPack 6.1 appears as `36.4.0` where NVIDIA's own table writes `36.4`.

quackd's own Python floor does not reach the board, because the quackd package is not
installed there. The board runs only the host daemon, one file under JetPack's own `python3`,
written for Python 3.10 with nothing but the standard library at import, and each thing it
serves asks for a little more:

- **Health** needs nothing installed. The daemon reads five files and runs `nvpmodel -q` and
  `tegrastats`, which JetPack ships.
- **The camera** needs OpenCV, and a CSI camera needs JetPack's own, which is built with
  GStreamer. A pip `opencv-python` is built without it, and pip installs one along with
  ultralytics.
- **Detections** need ultralytics, and a torch built for the board's GPU. With the torch pip
  installs by default they run on the CPU, and `/hello` says which.

JetPack 5 (L4T 35.x, Ubuntu 20.04, Python 3.8) is older than the Python the daemon is written
for, so this page stops at 6. JetPack 7 brought the Orin family onto Ubuntu 24.04 and Python
3.12, and its wheel ecosystem is younger: treat it as forward looking and keep 6.2 as the one
this page is written for.

## Find out what you have

```bash
cat /etc/nv_tegra_release        # R36 (release), REVISION: 4.3 is L4T r36.4.3
apt-cache show nvidia-jetpack    # the JetPack number itself, which that file never names
cat /proc/device-tree/model      # the board: NVIDIA Jetson Orin Nano Developer Kit
free -h                          # one pool, shared with the GPU
df -h /                          # models are gigabytes each: this wants to be NVMe
sudo tegrastats                  # live: RAM, GR3D_FREQ (the GPU), temperatures, power rails
```

Those run on the board. Once the host daemon is up, `quackd doctor --host` answers most of it
from the laptop in one screen, and that screen is the output to paste into an issue.

## Set up the board

Two things go on the board: a model server, which is your own install, and quackd's host
daemon. Neither needs the other, so a board can run one without the other.

### The model server

Ollama is the shortest path, and its installer knows about Jetsons:

```bash
# Ollama, which reads /etc/nv_tegra_release and fetches the JetPack build for your L4T
curl -fsSL https://ollama.com/install.sh -o ollama-install.sh
less ollama-install.sh && sh ollama-install.sh

ollama pull qwen3:4b             # 8 GB board. On 16 GB or more, qwen3:8b
```

Downloading an installer and reading it before running it is the same stance
`bridge/open_duck/install.sh` takes about itself, and for the same reason: this one writes a
systemd unit and a user on a machine with a robot attached to it.

Ollama listens on `127.0.0.1:11434` unless it is told otherwise, and it authenticates nothing.
Leave it on loopback and reach it through the ssh tunnel below, which is the arrangement this
page recommends. On a network you trust, Ollama's own documentation has you set `OLLAMA_HOST`
to `0.0.0.0:11434` in its service's environment (`sudo systemctl edit ollama`, then restart it).
`--host jetson.local` then reaches it with no tunnel, and so does everybody else on that
network.

### quackd's host daemon

The daemon is one file, `bridge/jetson/quackd_jetson_hostd.py`, run by systemd under the
board's own `/usr/bin/python3`. It is not in quackd's wheel, so clone the repository on the
board or copy the file and its unit across. Its install is written out, with the reason for
each step, in [bridge/jetson/README.md](../bridge/jetson/README.md#install), and this page does
not repeat it: it installs the file, writes a random token readable by the service's group,
and enables the unit. The unit starts the daemon on `127.0.0.1:9874` with the CSI camera
and that token. A USB camera is `--camera 0` in its place, and a board whose robot daemon owns
the cameras is `--camera none` ([the camera notes](../bridge/jetson/README.md#the-camera)).
Detections need ultralytics installed for that same `python3`
([detection on the GPU](../bridge/jetson/README.md#detection-on-the-gpu)), and a daemon
without it serves everything else and says why in `/hello`. Installing it also installs pip's
`opencv-python`, which that `python3` then loads ahead of JetPack's, and the CSI camera stops
opening until it is removed. That section has the order to install in and the lines that
remove it.

### From the laptop

Read the token once, then tunnel both ports:

```bash
export QUACKD_HOST_TOKEN="$(ssh <board> cat /etc/quackd/jetson-hostd.token)"
ssh -L 9874:127.0.0.1:9874 -L 11434:127.0.0.1:11434 <board>
```

While the tunnel is up, `127.0.0.1` on the laptop is the board for both ports.
`--host 127.0.0.1` reaches the daemon on its default 9874, and `--llm ollama --host 127.0.0.1`
moves the preset's `http://localhost:11434/v1` to `http://127.0.0.1:11434/v1`, which the tunnel
carries to the board's Ollama. The port in `--host` is always the daemon's and a preset keeps
its own, so there is no second port to type. The `export` above already put the token where
`run`, `serve-mcp` and `doctor` look for it.

Where the model server is has an order of its own, because a URL can name it as well as a host,
and a host only ever moves a preset. The local provider takes the first rung that is set, and
probes none of them:

1. `--base-url`: a URL given for this run is used exactly as given.
2. `--host`, or the host a registered robot was stored with: the preset's address moved to that
   machine, port and path kept.
3. `QUACKD_BASE_URL`, used exactly as given.
4. `OPENAI_BASE_URL`, used exactly as given.
5. `QUACKD_HOST`: the preset's address moved to that machine.
6. The preset's own address, on localhost.

So `QUACKD_HOST=127.0.0.1` in `.env` saves typing the flag, unless `QUACKD_BASE_URL` or
`OPENAI_BASE_URL` is set as well. Either of those outranks it for the model server, so the
preset stays at that URL while the host daemon is still reached on the board. `--host`
outranks both, on purpose: a board typed for this run, or registered with this robot, is a
decision about this run, and a `.env` line naming the board you usually use must not beat one
naming a model server's exact address ([local-llms.md](local-llms.md) has the same ladder).

Binding the daemon wide instead (`--bind 0.0.0.0`, with the token kept) lets
`--host jetson.local` reach it with no tunnel. That is for a network you trust: the daemon is
plain HTTP, so the token and every frame cross that network in the clear, and anybody who can
reach `POST /detect` can keep the board's GPU busy ([SECURITY.md](../SECURITY.md)).

## Which model fits which board

Single user decode on these boards is bound by memory bandwidth, so the practical question is
what fits beside everything else rather than what is fastest.

| Board | Memory | A sensible default |
|---|---|---|
| Orin Nano 8 GB | 8 GB shared | `qwen3:4b`, 2.5 GB on disk |
| Orin NX 16 GB | 16 GB shared | `qwen3:8b`, 5.2 GB on disk |
| AGX Orin 32 GB or 64 GB | 32 GB or 64 GB shared | 8B comfortably, and room to go further |

Download sizes are what Ollama's own library page printed on 2026-09-22 and they are not the
runtime figure: add the KV cache and the runner, and leave headroom for the operating system
and for the host daemon's detector. **No speed is quoted here on purpose.** Nobody has timed
quackd's loop on any Jetson, and a tokens-per-second number copied from a benchmark of a
different prompt on a different quantisation would be a guess wearing a number's clothes.

The model has to support tool calling, because that is how quackd offers the robot's verbs. If
yours does not, quackd falls back to asking for JSON in the text and retries once, which works
and is worse ([local-llms.md](local-llms.md)). Qwen3 thinks by default, which costs tokens on
every turn; `--extra-body` turns that off and that page shows how.

## Run it from your laptop

Start with doctor, which asks the board what it is before anything else does:

```bash
quackd doctor --host 127.0.0.1
```

```
Jetson at 127.0.0.1:9874 (the board the daemon runs on) ───────────────────────────────────
✓ daemon      quackd-jetson-hostd 0.1.0, protocol 1, on orin-nano
✓ camera      640x480 at 5 fps from csi, field of view 62.2 degrees
✓ detector    yolov8n.pt on cuda
✓ health      ok
· board       NVIDIA Jetson Orin Nano Developer Kit
· L4T         36.4.3 (JetPack 6.2)
· memory      7.3 GiB, 4.9 GiB available, shared with the GPU
⚠ swap        1.0 GiB, all zram: it compresses RAM rather than adding any (docs/jetson.md)
· GPU device  /dev/nvgpu/igpu0
· power mode  15W (nvpmodel -q)
· GPU busy    0% (GR3D_FREQ in tegrastats)
· tegrastats  09-24-2026 10:15:32 RAM 2467/7471MB (lfb 2x4MB) SWAP 0/994MB (cached 0MB) CPU
              [2%@729,1%@729,0%@729,0%@729,1%@729,0%@729] EMC_FREQ 0%@2133 GR3D_FREQ
              0%@[305] NVDEC off NVJPG off NVJPG1 off VIC off OFA off APE 200 cpu@47.5C
              soc2@46.8C soc0@46.4C gpu@46.2C tj@47.5C soc1@46.6C VDD_IN 4462mW/4462mW
              VDD_CPU_GPU_CV 480mW/480mW VDD_SOC 1400mW/1400mW
```

That block came from a fake board, not a Jetson. It is what doctor printed on Windows against
`tests/fake_jetson_hostd.py` listening on port 9874, a fake of the daemon's protocol whose
answers describe an Orin Nano with a camera and a CUDA detector, and whose board files and
`tegrastats` line are the ones in `tests/jetson_fixtures.py`.
[What quackd doctor shows](#what-quackd-doctor-shows) goes through it row by row.

Then the cartoon duck, with the model on the board. It needs no robot, and it is the honest
first test of the arrangement: if the duck kicks the ball, the model on the board is answering
the quackd on your laptop.

```bash
quackd run find-and-kick --robot microduck:sim2d --llm ollama:qwen3:4b --host 127.0.0.1
```

> [!IMPORTANT]
> The `openai` extra is the client every local preset speaks through, and `microduck` is a
> robot, because `uv pip install quackd` installs the core and no body at all, so this run
> wants `quackd[openai,microduck]`. `--llm ollama` means `http://localhost:11434/v1` on its
> own, and `--host` moves it to the board with its port kept, so there is no address to pass.

The header says what the run will use. This is the one that command printed against the same
fake board, with its memory row left out because that row names a path on the machine that
printed it:

```
┌─ 🦆 find-and-kick ──────────────────────────────────────────────────────────────────────┐
│ provider  ollama (qwen3:4b)                                                             │
│ robot     microduck:sim2d                                                               │
│ detector  color_blob on this machine                                                    │
│ host      127.0.0.1:9874  daemon 0.1.0  camera as an extra view, detect, tegra          │
└─ Ctrl-C or q stops the duck. Press it twice to quit at once. ───────────────────────────┘
```

No model server was listening there, so that run stopped at its first model call with
`ollama: APIConnectionError: Connection error.` The header still holds. The detector is the
colour one on the laptop, because the body is a simulator. The board's camera joined as an
extra view named `host`, and the run directory keeps its frames beside the simulator's, as
`frames/0000-host.png` and on.

Then a real body. The Open Duck Mini makes a good first one, because it brings a camera and
the board brings the model and a detector. With the duck's own tunnel up, as its
[hardware checklist](open-duck-hardware-checklist.md) describes, and the board's beside it:

```bash
quackd run open-duck-lookout --robot open_duck:bridge --address tcp://127.0.0.1:9871 \
  --host 127.0.0.1 --llm ollama:qwen3:4b
```

```
┌─ 🦆 open-duck-lookout ──────────────────────────────────────────────────────────────────┐
│ provider  ollama (qwen3:4b)                                                             │
│ robot     open_duck:bridge                                                              │
│ detector  yolo@host  127.0.0.1:9874  yolov8n.pt on cuda                                 │
│ host      127.0.0.1:9874  daemon 0.1.0  camera as an extra view, detect, tegra          │
└─ Ctrl-C or q stops the duck. Press it twice to quit at once. ───────────────────────────┘
```

That came from the same fake board with no duck at the address, so the run stopped at connect,
and the memory row is left out again. The detector is now the board's YOLO, chosen with no
flag, because the body is real and the daemon said it can detect. It reads the duck's own
frames, which stay the primary view because the bearings are calibrated for the duck's lens,
and the board's camera is an extra view. Before the header, the run warned that no camera
field of view was given for a bridge camera, so the host detector uses 62 degrees and
distances will be out by tens of percent. `--fov-deg` is how you say what the lens really is.

The rule for the detector is short. With `--host` and no `--detector`, a real body uses the
board's detector, `yolo@host`, whenever the daemon says it can detect. A simulated body
(`sim2d`, `mujoco` or `mock`) never does by itself, because YOLO does not see a cartoon ball and
the colour detector is tuned for what a simulator draws. `--detector color` opts out and keeps
the colour detector on the laptop. `--detector host` asks for the board's even on a simulator,
and is refused before anything connects when the daemon cannot detect. `--detector yolo` is
YOLO on the laptop and needs `quackd[yolo]`. Whichever it is, the run keeps it: a board that
stops answering mid-run gives each frame no detections and a reason, never the colour
detector in its place. The two do not label the same things on a real camera, and a quiet
switch would change what `go_to` steers at with nothing in the record saying so. Nor is a frame
the board could not read taken for a target out of view. `go_to` and `search_scan` stop the body
on the first one and fail with what the board said, as in
`go_to: yolo@host failed, so the body stopped:` and then the board's reason, so the pilot hears
that the detector failed rather than that the ball is gone. `approach_and` fails with `go_to`
and never runs its second verb. `observe` reports nothing seen with the reason beside it, and
the log gets a `note` when the detector starts failing and another when it answers again. A
board that answers slowly stops the body too: while `go_to` waits for a frame it re-sends its
last twist for 0.3 s and then sends a zero one, so a body with no deadman of its own, such as a
rosbridge base, does not drive on in the meantime. The run header and the log's `run_start`
event both name the detector, and each `observation` and `verb_end` record that sent the board
a frame has a `detect` block: how many frames went, how long the laptop waited for each, and the
time the board says it spent in the model ([architecture.md](architecture.md#transcript-format)).

`quackd serve-mcp` takes `--host`, `--host-token` and `--detector` the same way, so a chat
client drives the body with the board's camera and detector ([mcp.md](mcp.md)).

A body described without a camera of its own, such as a ToddlerBot on its bridge, gains a
board's camera, and `observe` with it, before a run judges the task file. `quackd validate`
and `quackd list-verbs` ask no board anything, so they judge that body without the board's
camera, and say so when a board is named for the robot:

```
⚠ validate does not ask the host 127.0.0.1 from robot tb (robots.json), so the board's camera
is not counted: a run adds it to tb (toddlerbot:bridge), with the verbs a camera unlocks, when
the board has one; quackd doctor --host 127.0.0.1 shows whether it does
```

If all you want from the board is its model, none of this is needed:
`--base-url http://jetson.local:11434/v1` asks nothing of the daemon.

## A decision LLM on the board

0.12.0 put an optional discrete stepper in front of the pilot, and several of the decision
LLMs it can name run on a machine of your own rather than on somebody else's
([decision-llms.md](decision-llms.md)). On a board where one pool of memory serves
everything, that is another tenant rather than a free lunch, so it belongs in the same
budget as the table above.

`--host` does not move a decision LLM. `--decision-url` does, and it already reaches a server
anywhere. `von` is the one that fits without asking for anything. It is an encoder rather than
a generator, it wants no GPU and no key, and it is small beside the model doing the piloting.
On the board:

```bash
pip install von-sdk
von serve --host 127.0.0.1 --port 8000     # von's own --host: where it listens
```

Add `-L 8000:127.0.0.1:8000` to the tunnel and `--decision-llm von` finds it where it already
looks, `http://127.0.0.1:8000`. Bound wide on a network you trust, it is
`--decision-llm von --decision-url http://jetson.local:8000`.

`laya` runs inside quackd's own process, and that process is on the laptop, so it has nothing
to do with the board.

Neither has been run on a Jetson, and the gap is wider than this page: nobody has run the
stepper against a real decision LLM on any machine, which [PLAN.md](../PLAN.md) records as
an open item of its own.

## Prepare the board

None of this is required by the host daemon. All of it matters once a model is resident.

**Put the models on NVMe.** A microSD card has neither the space nor the read speed, and a
model is loaded from disk every time the server restarts.

**Swap, on the NVMe, instead of zram.** JetPack enables zram by default, which compresses RAM
rather than adding any, so it cannot hold what memory could not. `quackd doctor --host` warns
when the only swap the board has is zram.

```bash
sudo systemctl disable nvzramconfig
sudo fallocate -l 16G /ssd/16GB.swap
sudo chmod 600 /ssd/16GB.swap
sudo mkswap /ssd/16GB.swap && sudo swapon /ssd/16GB.swap
echo '/ssd/16GB.swap none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Give it the power budget.** Mode ids are a property of the flash configuration rather than
of the board, so a number copied out of a blog post is the one thing here that can quietly
do the opposite of what you meant. Read the list instead:

```bash
sudo nvpmodel -q                 # what the modes are on THIS board, and which one is active
sudo nvpmodel -m <id>            # persists across reboots
sudo jetson_clocks               # pins clocks to that mode's maximum; does NOT persist
```

Two traps. `MAXN_SUPER` exists only on an Orin Nano or NX flashed with the super
configuration, so an Orin Nano that was upgraded to JetPack 6.2 rather than reflashed has no
such mode, and `nvpmodel -m 0` there selects 15 W rather than the maximum. And the highest
mode is not automatically the best for a server answering all day: it lifts the power cap
and not the thermal limit, so a board with modest cooling can end up slower than one a step
below it. Pick by the name `nvpmodel -q` prints, not by an id, and measure yours with
`tegrastats` running beside a real workload.

NVIDIA's r36.4 developer guide documents `nvpmodel` but says nothing about whether
`jetson_clocks` survives a reboot. The workaround its own forum recommends is a boot time
unit, so assume it does not.

**Drop the desktop if the board is headless.** `sudo systemctl set-default multi-user.target`
gives the model back whatever the graphical session was holding, and
`graphical.target` puts it back.

## Beside a robot's own daemon

This is where the board does the most. A ToddlerBot carries a Jetson, and `bridge/toddlerbot/`
is quackd's own daemon for it, owning the fifty hertz control loop that upstream has none for
and listening on port 9873. quackd stays on the laptop here too, and the board holds up to
three processes: the daemon that moves the robot, a model server if you put one there, and the
host daemon started with `--camera none`, because the ToddlerBot's daemon owns the robot's
cameras and two processes cannot own one camera. quackd takes the frames from the robot's
daemon, sends them back to the board's YOLO to be detected, and reads the board's health from
the host daemon.

Every boundary stays where it was: the robot's daemon still owns the body, quackd still owns
the deciding and the gating, and the model still only ever picks one verb. The ToddlerBot's
daemon binds loopback too, so its port joins the tunnel, and doctor shows the board and the
body in one screen:

```bash
ssh -L 9873:127.0.0.1:9873 -L 9874:127.0.0.1:9874 -L 11434:127.0.0.1:11434 <board>
quackd doctor --robot toddlerbot:bridge --address tcp://127.0.0.1:9873 --host 127.0.0.1
```

Against a fake board started without a camera, the host section of that doctor printed these
two rows, and the body's section an error, since no ToddlerBot was there:

```
· camera      none: started with --camera none
✓ detector    yolov8n.pt on cuda
```

> [!CAUTION]
> A model server or a detector saturating the CPU and the memory bus is exactly the load that
> can starve a fifty hertz control loop, and on a humanoid a starved control loop is a fall.
> The robot daemon's deadman is what protects the robot there, and it is doing that job for
> real rather than as a formality. Nobody has measured this contention on any board. Put the
> robot on a stand the first time, watch `tegrastats` while a model answers, and consider
> pinning the model server off the cores the loop runs on.

That is why the host daemon's systemd unit runs it at `Nice=10`, under `MemoryMax=2G` and with
`OOMScoreAdjust=500`: it is the process the board should lose first. Losing it costs quackd a
camera, detections and the board's health. Losing the control loop mid-step costs the robot.

## What quackd doctor shows

With a host named, by `--host`, by the robot `--robot` names or by `QUACKD_HOST`, doctor grows
a section above the usual ones, titled for what the daemon said. It is `Jetson at HOST` when
the daemon or the board's own files say the board is a Tegra, and `host at HOST` otherwise, so
a daemon on a laptop for a test is not called a Jetson. The block under
[Run it from your laptop](#run-it-from-your-laptop) is that section against the fake board.
Its first four rows are the daemon: its version and protocol, the camera with its size, rate,
source and field of view, the detector with the device it really runs on, and `/healthz`. The
rest is the board, sent by the daemon as raw file text and command output and parsed by doctor
on the laptop: the board's name, the L4T release and the JetPack it belongs to, memory shared
with the GPU, swap, the GPU device node, the power mode from `nvpmodel -q`, and the GPU's load
from one `tegrastats` line, with the line itself below it.

One warning there, and it is not fatal, which is the point: nothing the board reports can
change the exit code. The zram warning says the board's only swap is zram, which compresses RAM
rather than adding any. A board with no swap at all gets a warning of its own, that a model
which does not fit in memory cannot load. A detector on `cpu` would be a warning too, saying
the board's torch sees no CUDA. A missing camera or detector is only a note, because
`--camera none` and `--no-detect` are choices.

What does fail the report is a daemon that does not answer, the way a bad `--address` does:

```
host at 127.0.0.1:1 ───────────────────────────────────────────────────────────────────────
✗ error: 127.0.0.1:1 did not answer /hello within 2s: is the board up, and is quackd-jetson-hostd running on it?
  tried GET http://127.0.0.1:1/hello
```

Below the usual sections, the servers table probes the four presets at the host rather than on
the laptop, and asks an Ollama that answers where it put each loaded model (`GET /api/ps`).
This is that part beside the same fake board, with a stub standing in for Ollama on 11434 that
reported one model and none of it in GPU memory:

```
LLM servers, the presets on 127.0.0.1 (GET /v1/models, 1.5 s timeout) ─────────────────────
┌──────────┬───────────────────────────────────┬─────────────┐
│ preset   │ base url                          │ status      │
├──────────┼───────────────────────────────────┼─────────────┤
│ local    │ set QUACKD_BASE_URL or --base-url │             │
│ ollama   │ http://127.0.0.1:11434/v1         │ up qwen3:4b │
│ vllm     │ http://127.0.0.1:8000/v1          │ not running │
│ llamacpp │ http://127.0.0.1:8080/v1          │ not running │
│ lmstudio │ http://127.0.0.1:1234/v1          │ not running │
└──────────┴───────────────────────────────────┴─────────────┘
  where ollama put its models (GET /api/ps)
⚠ qwen3:4b  on the CPU: the generic arm64 build of Ollama has no Tegra CUDA, and the
            official installer picks the JetPack build (docs/jetson.md)
```

A model all on the GPU reads `all on the GPU` with its size, and a split one says what share
of it is there.

`--json` carries all of it under a `host` key: the daemon's `hello` and `healthz`, the presets
probed at the host under `servers`, and the board in a nested `jetson` block. The top-level
`jetson` key of 0.13 is gone. With no host named anywhere there is no board section at all,
because doctor never reads the files of the machine it runs on.

## Pitfalls

**Ollama answers off the CPU.** `ollama ps` names the processor for a loaded model. The generic
arm64 build has no Tegra CUDA in it; the JetPack build does, and the official installer picks
it by reading `/etc/nv_tegra_release`. If you installed from a tarball by hand, that is the
thing to redo. `quackd doctor --host` asks Ollama the same question and warns when a model sits
on a Tegra's CPU.

**`no kernel image is available for execution on the device`** means a CUDA binary built for a
different GPU architecture. Orin is `sm_87`. Whatever produced that binary needs rebuilding or
replacing with the Jetson artifact.

**llama.cpp fails to allocate with memory apparently free.** It is the unified pool.
`GGML_CUDA_ENABLE_UNIFIED_MEMORY=1` in the server's environment lets an oversized KV cache
spill instead of failing. Build with CUDA on and the architecture set to Orin's, then point
quackd at it with `--llm llamacpp` and the same `--host`, which asks port 8080 on the board,
and add `-L 8080:127.0.0.1:8080` to the tunnel.

**vLLM is not the easy option here.** It is worth it on a big board serving several clients at
once, and on an 8 GB Orin Nano a source build is more likely to exhaust memory than to finish.
For one pilot driving one robot, Ollama or llama.cpp is the right default.

**Rootless Docker and the integrated GPU do not get along.** If the model server is in a
container, run that container under the ordinary root daemon until you have proved otherwise on
your own board.

## Status

**Nothing on this page has been run on a Jetson by this project.** What has been done:

- The host daemon has run in-process in quackd's test suite, against a board built from files,
  a fake ultralytics and torch (with CUDA and without), a stubbed OpenCV capture and a Python
  one-liner standing in for `tegrastats`. Its source is parsed with Python 3.10's grammar and
  checked for names 3.10 lacks, and it has never run under 3.10 itself.
- The client, doctor's host section, the host detector and the host camera have run against the
  fake daemon in `tests/fake_jetson_hostd.py`, and a contract test runs the real daemon against
  the real client and doctor, which is what would catch the two sides of the protocol drifting
  apart.
- Every block of quackd output on this page was printed on Windows. Where a board answered, it
  was that fake daemon, and the Ollama in the servers table was a stub.
- The JetPack table and the model download sizes were read from NVIDIA's and Ollama's own
  pages on 2026-09-22. Everything version sensitive here goes stale on somebody else's
  release schedule, so check it against the board rather than trusting the date.

What that leaves unproven is everything about the board: the CSI pipeline, a USB camera, CUDA
detection, how long a frame takes to go from the laptop to the board's YOLO and back inside
`go_to`'s loop, Ollama on the Orin GPU, `nvpmodel`, what doctor reads off a real board's files
and a real `tegrastats` line, and whether a model server or a detector can share a robot's
Jetson without its control loop suffering.

If you run it, please open a Discussion or an issue with:

- `quackd doctor --host <board> --json` from the laptop, which carries the whole `host` block
- what the daemon's `/hello` says, fetched through the tunnel with
  `curl -s -H "X-Quackd-Token: $QUACKD_HOST_TOKEN" http://127.0.0.1:9874/hello`
- what its `/board` says, fetched the same way from `http://127.0.0.1:9874/board`: the
  board's own files, `nvpmodel -q` and a `tegrastats` line as raw text, most of which
  doctor's `--json` carries only as the values it parsed out of them
- one `tegrastats` line captured on the board while the model was answering
- `runs/<timestamp>-<name>/terminal.txt` and `transcript.jsonl` from a real run, whose
  `detect` blocks are the round trip inside `go_to`'s loop, split into the board's share
  and the rest
- which board, which JetPack, and which camera

A transcript is the most useful thing of all, and
[`docs/assets/transcripts/`](assets/transcripts/) is where the contributor ones live.
