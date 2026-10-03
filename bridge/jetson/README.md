# quackd's host daemon for an NVIDIA Jetson

quackd never runs on the Jetson. It runs on your laptop, and `--host` names the board. This
daemon is what answers there: one file, `quackd_jetson_hostd.py`, serving HTTP on port 9874. It
gives the quackd on your laptop three things from the board: a frame from a camera on it,
detections computed on its GPU, and the board's own health (model, L4T release, memory, swap,
power mode, one `tegrastats` line). The fourth thing, the language model on the board's GPU, is
your own model server (Ollama or another), and quackd reaches it through the same `--host`.

For the whole arrangement, which JetPack you have and which model fits which board, see
[`docs/guides/jetson.md`](../../docs/guides/jetson.md). This page is the reference for the daemon
itself.

Three rules it lives by, the same as the other daemons under `bridge/`:

- **It cannot move a robot.** There is no control path in the file. It reads a camera, runs a
  detector on JPEGs it is sent, reads five files and runs two read-only commands. A test reads
  the source and fails if that ever changes.
- **It never imports quackd**, and it imports nothing outside the standard library until it
  needs to. It is written for Python 3.10, which is JetPack 6's system python. OpenCV, Pillow,
  torch and ultralytics are imported where they are used, and a board without one of them
  starts anyway and says what it lacks in `/hello`.
- **It parses nothing.** `/board` ships raw file text and raw command output, and quackd reads
  them with the parsers `quackd doctor` already has.

## Endpoints

Every request on every path needs the token when the daemon has one. It goes in the
`X-Quackd-Token` header and is compared in constant time. A token in the query string is never
read, because URLs get logged. Every JSON reply carries `"ok"`, and every failure carries a
one-sentence `"reason"`.

A request without the right token is answered 401 on its headers alone and its connection
closed: nothing it sends after them is kept, and the daemon stops reading within 2 seconds. And
whatever the token, what a client can make the board hold is bounded: 16 connections at once
(the next is answered 503 and closed), 32 KB of headers, 10 seconds for a request to arrive
whole, and two `/detect` bodies in hand at a time (the next waits up to 5 seconds for its turn,
then is answered 503).

| Path | Method | What it answers |
|---|---|---|
| `/hello` | GET | the protocol and its version, the board's model name, and what started: `camera`, `detect` and `tegra`. Anything that did not start says why in `camera_error` or `detect_error`. quackd refuses `--host` until this answers |
| `/healthz` | GET | always 200. `ok` is whether everything the daemon was asked to run is running: the camera's frame is fresh, the detector's last call worked, and neither was asked for and failed to start. `reason` names whichever is not. `--camera none` and `--no-detect` are not held against it |
| `/board` | GET | raw text: `/proc/device-tree/model` and `compatible` with their NULs removed, `/etc/nv_tegra_release`, `/proc/meminfo`, `/proc/swaps`, the output of `nvpmodel -q`, one `tegrastats` line, and which of `/dev/nvgpu/igpu0`, `/dev/nvhost-ctrl-gpu` and `/dev/nvidia0` exist. Anything that is null has its reason under `errors` |
| `/snapshot.jpg` | GET | the newest frame, with `X-Frame-Age` in seconds. A 503 before the first frame, once the frame is older than `max(1.5, 4 / fps)` seconds, and when there is no camera, each with its reason |
| `/detect` | POST | a JPEG in (at most 8 MB, `?conf=` optional), boxes out in that image's pixels with the model's own class names, plus the model, `cuda` or `cpu`, and `ms`, the time spent in the model. quackd maps the names and does the geometry |

There is no `/detections` endpoint that runs the detector on this board's own camera. quackd
detects on the primary frame, which is the body's own camera whenever it has one, so such an
endpoint would serve one arrangement of two and add a second code path. It is the next step if
`ms` and `X-Frame-Age` show that the round trip is too slow. A run with the board's detector
records both halves of that trip: each `observation` and `verb_end` in its transcript that sent
a frame here has a `detect` block with the laptop's wait for each frame beside the `ms` reported.

## Flags

```
python3 quackd_jetson_hostd.py [flags]
```

| Flag | Default | What it does |
|---|---|---|
| `--bind` | `127.0.0.1` | loopback on purpose. Bound anywhere else with no token, it warns and starts |
| `--port` | `9874` | the Open Duck Mini takes 9871 and 9872 and the ToddlerBot daemon 9873, so a board running both never collides |
| `--camera` | `none` | `csi`, a V4L2 index such as `0`, a GStreamer pipeline of your own (anything containing `!`, ending in an appsink), or `fake` |
| `--fps` | `5` | the capture rate, at least 1. It captures on a timer, so a slow client cannot stall it, and `go_to` steers on these frames. quackd refuses a frame older than 2 s as a camera that has stopped, so a slower rate would have it refuse a working one |
| `--size` | `640x480` | the box a frame is shrunk into. The aspect ratio is kept, so the horizontal field of view is the lens's, and a frame is never enlarged. A 16:9 camera comes out 640x360 |
| `--fov-deg` | unset | the lens's horizontal field of view, passed on in `/hello`. Unset, quackd treats the lens as uncalibrated rather than guess |
| `--yolo-model` | `yolov8n.pt` | anything ultralytics' `YOLO()` loads |
| `--no-detect` | off | no detector at all, and no ultralytics import |
| `--conf` | `0.4` | the confidence floor, the same as quackd's own `YoloDetector`, unless a request sends `?conf=` |
| `--token` | `$QUACKD_HOST_TOKEN` | the token clients must send. Empty means none |
| `--token-file` | unset | read the token from a file. If the file is named and cannot be read, or is empty, the daemon refuses to start rather than run with authentication silently off |
| `--once` | off | print what `/hello` would say, as JSON, and exit. A quick check that the camera opens and the model loads |

## Install

`bridge/` ships in quackd's sdist and in the repository, never in the wheel, so `pip install
quackd` does not give you this file. Clone the repository on the board, or copy the two files
across, then from this directory:

```bash
sudo install -m 0755 -D quackd_jetson_hostd.py /opt/quackd/quackd_jetson_hostd.py
openssl rand -hex 32 | sudo install -m 0640 -o root -g jetson -D /dev/stdin /etc/quackd/jetson-hostd.token
sudo test -s /etc/quackd/jetson-hostd.token || echo "no token was written: fix the line above first"
sudo install -m 0644 quackd-jetson-hostd.service /etc/systemd/system/quackd-jetson-hostd.service
sudo systemctl daemon-reload
sudo systemctl enable --now quackd-jetson-hostd
```

`jetson` is a placeholder. Substitute the account you created on the board, as the token file's
group in the second command and as `User=` in the unit before the fourth. The token file is
readable by that group and nobody else, and `/etc/quackd` stays traversable, which is the
mistake the Open Duck Mini's installer once made: a token the service user could not read.

The third command is there because the second writes a file even when `openssl` is missing:
a pipe succeeds when its last command does, and `install` is happy to copy nothing. It prints
a line only when the token file is missing or empty. The daemon refuses an empty token file
too, so skipping the check costs a unit that fails at start, not one that runs open.

Then, on the laptop, read the token once and tunnel the ports:

```bash
export QUACKD_HOST_TOKEN="$(ssh <board> cat /etc/quackd/jetson-hostd.token)"
ssh -L 9874:127.0.0.1:9874 -L 11434:127.0.0.1:11434 <board>
```

After which `--host 127.0.0.1` reaches this daemon, and the model server on the board's port
11434 (Ollama's), through the tunnel. `quackd doctor --host 127.0.0.1` shows what the board
reported. Binding the daemon wide instead is for a network you trust, and keep the token.

The unit runs the system `/usr/bin/python3` rather than a virtualenv, because the OpenCV that
JetPack installs for it is built with GStreamer and the CSI camera needs GStreamer. It runs at
`Nice=10` with `MemoryMax=2G` and `OOMScoreAdjust=500`, so it is the process the board loses
first (see the ToddlerBot note below).

## From the laptop

`--host` is how the quackd on your laptop names this daemon, on `quackd doctor`, `quackd run`
and `quackd serve-mcp`, and on `quackd robot add` and `edit`, which keep it with a robot. Leave
the port off and it is 9874. Without the flag a command uses the host the robot keeps, then
`QUACKD_HOST`, and the token comes from `--host-token`, the robot's own, then
`QUACKD_HOST_TOKEN`. One board is one camera and one detector, so `--host` is refused for a
fleet: `--robots`, `--flock` or a flock duck.

**`quackd doctor --host <board>`** reads `/hello`, `/healthz` and `/board`, parses the board's
files on the laptop, and probes the four local model presets on the board rather than on the
laptop:

```
Jetson at 127.0.0.1:61332 (the board the daemon runs on) ──────────────────────────────────
✓ daemon      quackd-jetson-hostd 0.1.0, protocol 1, on orin-nano
✓ camera      640x480 at 5 fps from csi, field of view 62.2 degrees
✓ detector    yolov8n.pt on cuda
✓ health      ok
· board       NVIDIA Jetson Orin Nano Developer Kit
· L4T         36.4.3 (JetPack 6.2)
· memory      7.3 GiB, 4.9 GiB available, shared with the GPU
⚠ swap        1.0 GiB, all zram: it compresses RAM rather than adding any (docs/guides/jetson.md)
· GPU device  /dev/nvgpu/igpu0
· power mode  15W (nvpmodel -q)
· GPU busy    0% (GR3D_FREQ in tegrastats)
· tegrastats  09-24-2026 10:15:32 RAM 2467/7471MB (lfb 2x4MB) SWAP 0/994MB (cached 0MB) CPU
              [2%@729,1%@729,0%@729,0%@729,1%@729,0%@729] EMC_FREQ 0%@2133 GR3D_FREQ
              0%@[305] NVDEC off NVJPG off NVJPG1 off VIC off OFA off APE 200 cpu@47.5C
              soc2@46.8C soc0@46.4C gpu@46.2C tj@47.5C soc1@46.6C VDD_IN 4462mW/4462mW
              VDD_CPU_GPU_CV 480mW/480mW VDD_SOC 1400mW/1400mW
```

That is the board's section of `quackd doctor --host 127.0.0.1:61332`, whole, run against
quackd's test fake of this daemon, `tests/fake_jetson_hostd.py`. The fake serves the board
files the daemon's own tests build, and an `nvpmodel` answer and a `tegrastats` line written to
the shape of published ones rather than captured from a board. It is not a Jetson. The last row
is the raw `tegrastats` line the GPU load was read from. Against this daemon itself, started on
Windows with `--camera fake` and a board made of files, the section read the same board and
said what that machine lacked: no field of view given, no detector because ultralytics is not
installed, health not ok for that reason, and the power mode and GPU load unknown because
`nvpmodel` and `tegrastats` are not on its PATH. `--json` carries all of it under `host`, the
board's facts under `host.jetson`. A daemon that does not answer fails the report, and nothing
the board reports changes the verdict.

**`quackd run --host <board>`** asks `/hello` before anything connects, and refuses the run when
the daemon does not answer. The camera joins whatever body the run drives. It is the primary
view for a body with no camera of its own, and otherwise an extra view named `host`, which the
model is shown and the detector does not read. The detector is this daemon's YOLO, `yolo@host`,
on a real body when `/hello` says `detect`, and never on a simulator by itself.
`--detector color` keeps the colour detector on the laptop, and `--detector yolo` runs YOLO on
the laptop instead. `--detector host` asks for this one by name, even on a simulator, and is
refused before anything connects when the daemon cannot detect. A call to `/detect` that fails
gives that frame no detections and a note in the run's log, `go_to` and `search_scan` stop the
body on it and fail with the reason, and the run never switches detector. The header names both:

```
┌─ 🦆 find-and-kick ──────────────────────────────────────────────────────────────────────┐
│ provider  fake (scripted:find-and-kick)                                                 │
│ robot     microduck:sim2d                                                               │
│ detector  color_blob on this machine                                                    │
│ host      127.0.0.1:19874  daemon 0.1.0  camera as an extra view, tegra                 │
└─ Ctrl-C or q stops the duck. Press it twice to quit at once. ───────────────────────────┘
```

That was the cartoon duck against this daemon started with `--camera fake` on Windows, with a
board made of files and no ultralytics. A simulator keeps the colour detector, and the duck has
a camera of its own, so the board's is an extra view. `quackd serve-mcp` takes `--host`,
`--host-token` and `--detector` the same way, for one robot.

## Try it on a laptop first

No board, no camera, no GPU:

```bash
python quackd_jetson_hostd.py --camera fake --no-detect
curl -s http://127.0.0.1:9874/hello
curl -s -o frame.jpg http://127.0.0.1:9874/snapshot.jpg
```

`/hello` says `"tegra": false` there, honestly, and `/board` ships whatever the laptop has
(a Linux one has `/proc/meminfo` and `/proc/swaps`) and null with a reason for the rest.
`--camera fake` paints an orange ball on a pale floor, which quackd's own colour
detector sees. It needs no OpenCV, and without Pillow it serves a 2 by 2 grey JPEG instead.

## The camera

- **`--camera csi` is untested.** It opens an `nvarguscamerasrc` pipeline through GStreamer and
  asks for 1640 by 1232, the IMX219's binned full-sensor mode, so that the frame keeps the
  lens's whole field of view rather than a crop of its middle. It needs `nvargus-daemon`
  running and JetPack's own OpenCV. A pip `opencv-python` is built without GStreamer, and the
  daemon says so in `camera_error` when it can tell. Any other sensor or mode is a pipeline
  string passed to `--camera` directly.
- **A USB camera is its index**: `--camera 0` is `/dev/video0` through V4L2. The service user is
  in the `video` group for this.
- **The daemon reads one frame at start**, so a camera that opens and delivers nothing is
  reported in `/hello` rather than advertised and discovered at the first snapshot. A camera
  that was asked for and did not open keeps `/healthz` from saying ok, and the daemon serves
  everything else. It does not retry: `sudo systemctl restart quackd-jetson-hostd` once the
  camera is there.
- **A password in a pipeline stays on the board.** A pipeline of your own may carry one, as
  `rtsp://user:pass@...`, as a query parameter such as `?pwd=` or `?token=`, or as a property
  such as `user-pw=` or `extra-headers=`. The capture gets the real string; `/hello`, every
  reason that names the camera and the log show it as `***`. The masking goes by name, as
  quackd's own does, so a credential under a name it does not know, such as a stream key in a
  URL's path, is shown as it is.
- **Two processes cannot own one camera.** If something else on the board has it, use
  `--camera none`.

## Detection on the GPU

Detection needs ultralytics installed for the system `python3` that the unit runs, and pip
installs three packages along with it that are the wrong builds for a Jetson:

- **torch and torchvision.** The ones pip picks are not built for the Jetson's GPU, and
  detection then runs on the CPU. For the GPU, install ultralytics first and NVIDIA's torch
  and torchvision wheels for your JetPack after it, over the ones it brought, which is the
  order Ultralytics' own Jetson guide uses. The other order undoes itself: ultralytics
  requires torchvision, every torchvision on PyPI requires one exact torch release, and
  NVIDIA's torch is not that release, so pip replaces it. Or run this file inside Ultralytics'
  JetPack container, which has both.
- **OpenCV.** ultralytics requires pip's `opencv-python`, which is built without GStreamer,
  and pip puts it where `python3` finds it before JetPack's own. The CSI camera then stops
  opening, and `camera_error` names the OpenCV that was loaded. Remove pip's copy once
  ultralytics is in, and check that `python3` loads JetPack's again:

  ```bash
  sudo python3 -m pip uninstall -y opencv-python    # a copy pip installed with sudo
  python3 -m pip uninstall -y opencv-python         # a copy it installed under ~/.local
  python3 -c "import cv2; print(cv2.getBuildInformation())" | grep GStreamer
  ```

  The last line has to say `YES`; pip's build says `NO`. Upgrading ultralytics brings pip's
  copy back, so run these again after one. A USB camera opens with either, through V4L2.

**`/hello`'s `detect.device` says which torch you got**: `cuda` or `cpu`, from
`torch.cuda.is_available()` on the board, not from what anyone intended.

The first start with the default `yolov8n.pt` downloads it into the working directory, which
the unit sets to `/var/lib/quackd-jetson-hostd`. On a board with no internet, put the file
there first, or pass `--yolo-model` a full path. A model that does not load, or whose first
prediction fails, is reported in `detect_error`, and the daemon serves everything else. The
same goes for a board with no ultralytics at all. Either way `/healthz` is not ok until the
detector starts or the daemon runs with `--no-detect`.

**ultralytics is AGPL-3.0.** It runs on the board as your own install. This file imports it
only when it is there, and quackd itself imports none of it and ships none of it. Read its
licence before you build a service on it.

## Beside a ToddlerBot

On a ToddlerBot the Jetson is the robot's own computer, and quackd's ToddlerBot daemon
([`bridge/toddlerbot/`](../toddlerbot/README.md)) runs the fifty hertz loop there and owns the
robot's cameras. Run this one with `--camera none` beside it, which means changing the unit's
`--camera csi` before you install it. quackd, on the laptop, still gets frames from the robot's
daemon and sends them here to be detected, by itself, because a ToddlerBot is a real body.
`--detector color` keeps the colour detector on the laptop instead. The board's health still
comes from here.

What to watch is contention. A model server or a detector saturating this board can starve
the control loop, and nobody has measured by how much. That is why the unit runs this daemon at
a lower priority, under a memory ceiling, and first in line for the OOM killer: losing it costs
quackd a camera, detections and the board's health, and losing the control loop mid-step costs
the robot.

## Why there is no install.sh

The Open Duck Mini's `install.sh` exists to check hardware preconditions on a robot that takes
commands: that nothing else holds the serial bus, that the IMU answers, that Wi-Fi power save
is off. This daemon moves nothing, and whatever it could fail to find it reports in `/hello`.
Six commands you can read replace a script you would have to.

## Status

Nothing on this page has been run on a Jetson by this project.

What was exercised: `tests/test_jetson_hostd.py` drives the real server in-process, in
quackd's test suite, against a board built from files, a fake ultralytics and torch (with CUDA
and without), a stubbed OpenCV capture, and a Python one-liner standing in for `tegrastats`.
`tests/test_jetson_host_contract.py` runs quackd's own client and `quackd doctor` against the
real server on the same kind of board, which is what catches the two sides drifting apart.
Never opened: the CSI pipeline, a USB camera, CUDA, a real model. The source is parsed with
Python 3.10's grammar and checked for names 3.10 lacks, but it has not been executed on 3.10:
quackd's own floor is 3.11.

If you run it on a board, please open an issue with what the Status section of
[docs/guides/jetson.md](../../docs/guides/jetson.md#status) asks for, which is one list for the
daemon and quackd together and includes the raw `/board` this daemon serves.
