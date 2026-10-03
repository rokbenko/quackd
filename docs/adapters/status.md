# Adapter status — what has run against its real target, and what has not

quackd never silently invents an upstream API. Every method name, socket path, topic,
message type, enum or convention it relies on lives in one file per upstream, tagged
**VERIFIED** (read from upstream source on the date given, link given) or **UNVERIFIED**
(designed upstream but not shipped, or an assumption of ours, with what quackd does about
it). A test proves UNVERIFIED names stay inside the backend that needs them.
`quackd doctor` prints every UNVERIFIED list on your machine.

One row below has met hardware: `lerobot:real`, on a LeRobot SO-101 arm, on 2026-09-15 and again
on 2026-09-23. Six of the seven bodies here have still never been driven by quackd, and the row
that has was driven on one arm for two afternoons, the second on quackd 0.12.0. Nothing changed
since that afternoon has run on it: the rest pose clipped into the travel,
`quackd robot release`, the paced `move_joints` and the connect retries are exercised only
against a fake arm, `lerobot:mock` and the test suite ([CHANGELOG.md](../../CHANGELOG.md), Known
limitations). What the first afternoon did and did not settle is under the table.

> [!IMPORTANT]
> `uv pip install quackd` installs the core and no robot at all. Every body below is its own
> distribution, and the extra that names it is what installs it.

| Adapter | Extra | Distribution |
|---|---|---|
| Microduck | `quackd[microduck]` | `quackd-microduck` |
| LeRobot | `quackd[lerobot]` | `quackd-lerobot` |
| rosbridge | `quackd[rosbridge]` | `quackd-rosbridge` |
| Open Duck Mini v2 | `quackd[open_duck]` | `quackd-open-duck` |
| XLeRobot | `quackd[xlerobot]` | `quackd-xlerobot` |
| AlohaMini | `quackd[alohamini]` | `quackd-alohamini` |
| ToddlerBot | `quackd[toddlerbot]` | `quackd-toddlerbot` |

So `uv pip install "quackd[open_duck]"` buys the Open Duck Mini and nothing else, and
`quackd[robots]` buys all seven, each with the SDK its real backend needs. Two extras buy the
duck and one heavy thing it can do: `quackd[mujoco]` is `quackd-microduck[mujoco]`, the duck
with its physics simulator, and `quackd[microduck-camera]` is the duck with its WebRTC camera.
One buys the arm's simulator: `quackd[lerobot-sim]` is `quackd-lerobot[sim]`, the arm with
MuJoCo and without LeRobot, so it installs on Python 3.11 as well.

An adapter that is not installed keeps its row in `quackd list-adapters` and in
`quackd doctor`, marked not installed. Naming one anyway refuses with the extra to type:
`adapter 'lerobot' needs an extra: uv pip install 'quackd[lerobot]'`. With nothing installed
at all, every command that needs a body refuses and names all seven.

| Adapter | `--robot` | Status | Upstream file | Page |
|---|---|---|---|---|
| Microduck | `microduck:sim2d` | ✅ default | | [adapters/microduck/README.md](microduck/README.md) |
| | `microduck:mujoco` | ✅ physics simulator (MuJoCo, `quackd[mujoco]`): `find-and-kick` 10 of 10 seeds on the stand-in, and 9 or 10 of 10 on the trained gait depending on the machine and the run, seed 4 being the marginal one | [`adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py`](../../adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py) | |
| | `microduck:mock` | ✅ | | |
| | `microduck:jsonrpc` | 🧪 experimental: every method VERIFIED, never run on a duck | [`adapters/microduck/src/quackd_microduck/upstream_api.py`](../../adapters/microduck/src/quackd_microduck/upstream_api.py) | |
| | `microduck:websocket` | ⏳ stub: raises with a link until upstream ships it | | |
| LeRobot | `lerobot:mock` | ✅ | | [adapters/lerobot/README.md](lerobot/README.md) |
| | `lerobot:real` | ✅ **run on a real arm on 2026-09-15 and again on 2026-09-23**, the only row here that has been. On 2026-09-15 it was an SO-101 follower calibrated as `arm-01` and reached as `--robot lerobot:real --address COM3` with no registered name, on Windows 11, Python 3.12.12, lerobot 0.6.1, quackd 0.9.0, piloted by OpenAI `gpt-6-astra`. `lerobot-lookout` ran, once with `--llm fake` as well. Free-form `--goal` runs waved the wrist roll about plus or minus 27 degrees, reached wider with `shoulder_lift` -39 and `elbow_flex` 24 to 30, opened and closed the gripper (commanded 100, reported 98 open and 3 closed with the jaws nearly touching), and one of them mimed a duck quacking with the gripper. A USB webcam answered at `opencv://1`, and at `opencv://2` after a replug, 640x480, with no `?backend=` key needed. **The arm fell at the end of every run that day**, which is the fault the rest pose was written to fix. The rest pose first met that arm on 2026-09-23 and could not reach a fold that lay past the travel its calibration recorded, which is [ADR-0045](../adr/0045-a-rest-pose-the-calibration-cannot-reach.md). That second afternoon was 26 runs on quackd 0.12.0 with the arm registered as `arm-01`: 19 never moved the arm at a pilot's request, three failed at connect, each on one bad status packet, one lost and two garbled, and all 21 that reached their close kept torque on and ended at the power switch, which `quackd robot release` now answers. None of what changed after it has run on the arm. Every LeRobot name is still VERIFIED at a pinned commit, and still exercised with a fake arm (Python 3.12+, [checklist](lerobot/hardware-checklist.md)) | [`adapters/lerobot/src/quackd_lerobot/upstream_api.py`](../../adapters/lerobot/src/quackd_lerobot/upstream_api.py) | |
| | `lerobot:mujoco` | ✅ the arm's simulator (MuJoCo, `quackd[lerobot-sim]`): `lerobot:real`'s own code over a physics model of the SO-101, the maker's model fetched at a pinned commit. On that model a grasp driven through the real backend's own verbs lifts a cube clear of the table between both finger pads on 10 of 10 seeds, judged by the world's truth and not by quackd, and the bundled `lerobot-lookout` and a grasp task with a sidecar each pass the rehearsal `quackd preflight` runs, on 10 seeds of 10: the lookout on the generic arm, which has no rest pose to return to, and the grasp task from a rest pose its close has to reach, though the grasp task's seeds move only a pen on the table, never the block it lifts (`tests/test_lerobot_sim_model.py`, by hand on 2026-09-27). The nightly `lerobot-sim-assets` job fetches the model and runs those sweeps, asking ten of ten, and its first run, dispatched on `main` on 2026-09-29 at the commit tagged `v0.16.0`, passed all three ten of ten ([run 36523568197](https://github.com/rokbenko/quackd/actions/runs/36523568197)). CI's gating job runs the simulator's other tests on a primitives-only stand-in, because it fetches nothing. Done means the simulator does what it says, not that it moves like an arm: nothing has compared it against one, and it never raises `lerobot:real`'s status | [`adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py`](../../adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py) | |
| rosbridge | `rosbridge:mock` | ✅ | | [adapters/rosbridge/README.md](rosbridge/README.md) |
| | `rosbridge:ws` | 🧪 every roslibpy, rosbridge and message name VERIFIED at pinned commits, exercised with fake topics and fake services, including reading the robot's own description off the bridge, never run against a bridge | [`adapters/rosbridge/src/quackd_rosbridge/upstream_api.py`](../../adapters/rosbridge/src/quackd_rosbridge/upstream_api.py) | |
| Open Duck Mini v2 | `open_duck:sim2d` | ✅ `open-duck-scout` 10 of 10 seeds | | [adapters/open_duck/README.md](open_duck/README.md) |
| | `open_duck:mock` | ✅ | | |
| | `open_duck:bridge` | 🧪 every runtime name VERIFIED at a pinned commit, the protocol exercised against the real daemon over loopback, never run on a duck | [`adapters/open_duck/src/quackd_open_duck/upstream_api.py`](../../adapters/open_duck/src/quackd_open_duck/upstream_api.py) | |
| XLeRobot | `xlerobot:mock` | ✅ | | [adapters/xlerobot/README.md](xlerobot/README.md) |
| | `xlerobot:zmq` | 🧪 the whole wire format VERIFIED at a pinned commit, the client exercised against a fake host quackd wrote from that source over loopback, never run on a cart | [`adapters/xlerobot/src/quackd_xlerobot/upstream_api.py`](../../adapters/xlerobot/src/quackd_xlerobot/upstream_api.py) | |
| AlohaMini | `alohamini:mock` | ✅ | | [adapters/alohamini/README.md](alohamini/README.md) |
| | `alohamini:sim2d` | ✅ `alohamini-lookout` 10 of 10 seeds | | |
| | `alohamini:zmq` | 🧪 the whole wire format VERIFIED at a pinned commit, the client exercised against a fake host quackd wrote from that source over loopback, never run on a robot. The arm verbs additionally need quackd's own host wrapper, which nobody has run either | [`adapters/alohamini/src/quackd_alohamini/upstream_api.py`](../../adapters/alohamini/src/quackd_alohamini/upstream_api.py) | |
| ToddlerBot | `toddlerbot:mock` | ✅ | | [adapters/toddlerbot/README.md](toddlerbot/README.md) |
| | `toddlerbot:sim2d` | ✅ `toddlerbot-lookout` 10 of 10 seeds | | |
| | `toddlerbot:bridge` | 🧪 every upstream name VERIFIED at the commit the v2.0.0 tag points at, the protocol and the daemon's own safety machinery exercised against a fake body over loopback, never run on a robot | [`adapters/toddlerbot/src/quackd_toddlerbot/upstream_api.py`](../../adapters/toddlerbot/src/quackd_toddlerbot/upstream_api.py) | |

**The first real run, and what it does not prove.** One SO-101, one bench, one afternoon.
The arm fell at the end of every run, because LeRobot's `disconnect()` disables torque by its
own default and quackd kept that default: `quackd robot rest-pose`, and the parking at both
ends of a run that goes with it, exist because of those falls. One dry run aborted with `the
arm did not answer: TimeoutError` after a single heartbeat round trip failed, and nothing like
it happened again. One dry run aborted because the pilot answered `uncertain` and the human
said no. The camera framed the gripper and cropped the raised arm, so the model checked its own
waves against joint readings rather than against the picture. And four questions came back
unanswered: whether the holding band is anywhere near right, what a joint reads after ten
minutes of work, whether a stall is caught when you cause one on purpose, and whether 5 degrees
an action felt right in the room. They are in the list at the foot of
[adapters/lerobot/hardware-checklist.md](lerobot/hardware-checklist.md), which has since grown to
ask whether a slow `move_joints` looks like one motion and arrives when its time is up, whether a
joint let go at the edge of its travel settles onto its fold, and what the hand placed start
and a decision LLM leave open. Those four are why that ✅ is a robot quackd has worked on
rather than a robot quackd is tested on.

**Pilot flocks** (`flock.allocation.method: pilots`, `--flock NAME`) run one LLM pilot per body
on wall-clock time, on any adapter and backend including mixed ones, and have run on `mock` and
`sim2d` bodies and on no hardware. **Coordinator flocks** (`--flock N`, `flock.roles`) run N
in-process views of one simulated world on one lockstep clock, Microducks only. The MQTT bus
implements the same `Bus` protocol and was exercised once against a local broker
([guides/lan.md](../guides/lan.md)); a coordinator flock across machines also needs a clock across
machines, which does not exist yet, and a pilot flock needs no such clock and has never been tried
across two.

Each adapter's upstream tables, what quackd read and what it assumes, are on the page its
row links.

## How to help

**Built an Open Duck Mini v2?** That is the row most likely to flip this year, because it
is one of three bodies here you can build from scratch, and one of three whose robot side quackd
ships and already exercises.
[adapters/open_duck/hardware-checklist.md](open_duck/hardware-checklist.md) is the order to try it
in, and there is an issue template waiting for the result.

**Got your hands on a Microduck?**
[adapters/microduck/hardware-checklist.md](microduck/hardware-checklist.md) is the order to try it
in, and there is an issue template waiting for the result. Nothing in it installs anything on the
robot or needs `sudo`, because the first Microduck most people touch will belong to somebody else.
`microduck-lookout` is the task to point at it first: nothing in its allowlist moves a leg.

Ran `--robot open_duck:bridge` against a duck you built, `toddlerbot:bridge` against a
ToddlerBot on its stand, `xlerobot:zmq` against a cart, `alohamini:zmq` against an AlohaMini,
`microduck:jsonrpc` against a real duck, or `rosbridge:ws` against a bridge?
Open an issue with `quackd doctor` output and the first lines of `transcript.jsonl`, whose
`run_start` names the command that was typed and the version that ran it, so a report no longer
rests on anyone remembering either. A run directory holds `terminal.txt` beside it, and that is
not the evidence: it is the screen, a rendering of the same events that `--no-log` shortens. The
transcript is every intent quackd sent and every answer the robot gave back, which is what
somebody else can check. Every row above that flips from 🧪/⏳ to ✅ is one line in an
`upstream_api.py` and one row here. `lerobot:real` is the one that has already flipped, on one
arm on one bench, so a second run against an SO-101 is still worth an issue: it either widens
that row or contradicts it, and the one that contradicts it is worth more.
