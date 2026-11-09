# Adapter status — what has run against its real target, and what has not

quackd never silently invents an upstream API. Every method name, socket path, topic,
message type, enum or convention it relies on lives in one file per upstream, tagged
**VERIFIED** (read from upstream source on the date given, link given) or **UNVERIFIED**
(designed upstream but not shipped, or an assumption of ours, with what quackd does about
it). A test proves UNVERIFIED names stay inside the backend that needs them.
`quackd doctor` prints every UNVERIFIED list on your machine.

One row below has met hardware: `lerobot:real`, on a LeRobot SO-101 arm, on 2026-09-15 and again
on 2026-09-23. Nine of the ten bodies here have still never been driven by quackd, and the row
that has was driven on one arm for two afternoons, the second on quackd 0.12.0. Nothing changed
since that afternoon has run on it: the rest pose clipped into the travel,
`quackd robot release` with the fold it now makes before it lets go, the two questions a run
asks when its rest move missed, the paced `move_joints` and the connect retries are exercised
only against a fake arm, `lerobot:mock` and the test suite ([CHANGELOG.md](../../CHANGELOG.md),
Known limitations). What the first afternoon did and did not settle is under the table.

Every backend carries a tier, in the catalogue and in the second table below, and
`quackd list-adapters` prints the same words:

| Tier | What it means |
|---|---|
| 🤖 **verified** | The real robot has moved under quackd, and the row names who ran it and the first day it did |
| 🧪 **bound** | Code that reaches a real robot and has never met one. The row names who answers for its first hardware run and a date |
| ✅ **rehearsed** | Runs whole tasks with no robot: in a simulator, or against a scripted mock, in CI |
| 📄 **described** | A body quackd lists and reasons about and drives no part of: one file of its maker's figures. [Described bodies](#described-bodies) lists them. One backend is described too, `rosarm:ws`: bound code nobody has named an owner for, which refuses to start until somebody does |

The rule since 2026-11-05
([ADR-0076](../adr/0076-every-backend-has-a-tier-and-bound-code-names-its-owner.md)) is that a
bound backend enters this repository only with a named owner and a date for its first hardware
run, and a test holds every row to it. The eight bound
backends that were here before the rule say `grandfathered 0.33.0` where an owner would be, with
the day the rule was adopted: nobody has promised a run for any of them. Since 2026-11-08
([ADR-0078](../adr/0078-an-industrial-arm-is-moved-one-confirmed-goal-at-a-time.md)) bound code
may also enter with no owner as `described`, refused at `make()` and at `quackd robot add` in
the rule's words until somebody names a date, and `rosarm:ws` is the one backend that has.

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
| Huebot | `quackd[huebot]` | `quackd-huebot` |
| MOSS | `quackd[moss]` | `quackd-moss` |
| Industrial arm on ROS 2 | `quackd[rosarm]` | `quackd-rosarm` |

So `uv pip install "quackd[open_duck]"` buys the Open Duck Mini and nothing else, and
`quackd[robots]` buys all ten, each with the SDK its real backend needs. Two extras buy the
duck and one heavy thing it can do: `quackd[mujoco]` is `quackd-microduck[mujoco]`, the duck
with its physics simulator, and `quackd[microduck-camera]` is the duck with its WebRTC camera.
One buys the arm's simulator: `quackd[lerobot-sim]` is `quackd-lerobot[sim]`, the arm with
MuJoCo and without LeRobot, so it installs on Python 3.11 as well.

An adapter that is not installed keeps its row in `quackd list-adapters` and in
`quackd doctor`, marked not installed. Naming one anyway refuses with the extra to type:
`adapter 'lerobot' needs an extra: uv pip install 'quackd[lerobot]'`. With nothing installed
at all, every command that needs a body refuses and names all ten.

| Adapter | `--robot` | Tier | Notes | Upstream file | Page |
|---|---|---|---|---|---|
| Microduck | `microduck:sim2d` | ✅ rehearsed | default | | [adapters/microduck/README.md](microduck/README.md) |
| | `microduck:mujoco` | ✅ rehearsed | physics simulator (MuJoCo, `quackd[mujoco]`): `find-and-kick` 10 of 10 seeds on the stand-in, and 9 or 10 of 10 on the trained gait depending on the machine and the run, seed 4 being the marginal one | [`adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py`](../../adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py) | |
| | `microduck:mock` | ✅ rehearsed | | | |
| | `microduck:jsonrpc` | 🧪 bound · grandfathered 0.33.0 · 2026-11-05 | every method VERIFIED, never run on a duck | [`adapters/microduck/src/quackd_microduck/upstream_api.py`](../../adapters/microduck/src/quackd_microduck/upstream_api.py) | |
| LeRobot | `lerobot:mock` | ✅ rehearsed | | | [adapters/lerobot/README.md](lerobot/README.md) |
| | `lerobot:real` | 🤖 verified · rok · 2026-09-15 | **run on a real arm on 2026-09-15 and again on 2026-09-23**, the only row here that has been. On 2026-09-15 it was an SO-101 follower calibrated as `arm-01` and reached as `--robot lerobot:real --address COM3` with no registered name, on Windows 11, Python 3.12.12, lerobot 0.6.1, quackd 0.9.0, piloted by OpenAI `gpt-6-astra`. `lerobot-lookout` ran, once with `--llm fake` as well. Free-form `--goal` runs waved the wrist roll about plus or minus 27 degrees, reached wider with `shoulder_lift` -39 and `elbow_flex` 24 to 30, opened and closed the gripper (commanded 100, reported 98 open and 3 closed with the jaws nearly touching), and one of them mimed a duck quacking with the gripper. A USB webcam answered at `opencv://1`, and at `opencv://2` after a replug, 640x480, with no `?backend=` key needed. **The arm fell at the end of every run that day**, which is the fault the rest pose was written to fix. The rest pose first met that arm on 2026-09-23 and could not reach a fold that lay past the travel its calibration recorded, which is [ADR-0045](../adr/0045-a-rest-pose-the-calibration-cannot-reach.md). That second afternoon was 26 runs on quackd 0.12.0 with the arm registered as `arm-01`: 19 never moved the arm at a pilot's request, three failed at connect, each on one bad status packet, one lost and two garbled, and all 21 that reached their close kept torque on and ended at the power switch, which `quackd robot release` now answers. None of what changed after it has run on the arm. Every LeRobot name is still VERIFIED at a pinned commit, and still exercised with a fake arm (Python 3.12+, [checklist](lerobot/hardware-checklist.md)). An SO-100 is registered with `--robot-type so100_follower` and runs the same code, bound and never run, and a Koch v1.1 is described and registered by nothing ([which arms](lerobot/README.md#which-arms)) | [`adapters/lerobot/src/quackd_lerobot/upstream_api.py`](../../adapters/lerobot/src/quackd_lerobot/upstream_api.py) | |
| | `lerobot:real`, `--robot-type so100_follower` | 🧪 bound · rok · 2026-09-15 | no SO-100 has run: it runs the code that drove the SO-101, through the same LeRobot class | | |
| | `lerobot:real`, `--robot-type koch_follower` | 📄 described | data only: quackd lists a Koch v1.1 and registers none, and `make()` refuses it until an owner names a date | | |
| | `lerobot:mujoco` | ✅ rehearsed | the arm's simulator (MuJoCo, `quackd[lerobot-sim]`): `lerobot:real`'s own code over a physics model of the SO-101, the maker's model fetched at a pinned commit. On that model a grasp driven through the real backend's own verbs lifts a cube clear of the table between both finger pads on 10 of 10 seeds, judged by the world's truth and not by quackd, and the bundled `lerobot-lookout` and a grasp task with a sidecar each pass the rehearsal `quackd preflight` runs, on 10 seeds of 10: the lookout on the generic arm, which has no rest pose to return to, and the grasp task from a rest pose its close has to reach, though the grasp task's seeds move only a pen on the table, never the block it lifts (`tests/test_lerobot_sim_model.py`, by hand on 2026-09-27). The nightly `lerobot-sim-assets` job fetches the model and runs those sweeps, asking ten of ten, and its first run, dispatched on `main` on 2026-09-29 at the commit tagged `v0.16.0`, passed all three ten of ten ([run 36523568197](https://github.com/rokbenko/quackd/actions/runs/36523568197)). CI's gating job runs the simulator's other tests on a primitives-only stand-in, because it fetches nothing. Done means the simulator does what it says, not that it moves like an arm: nothing has compared it against one, and it never raises `lerobot:real`'s status | [`adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py`](../../adapters/lerobot/src/quackd_lerobot/sim/upstream_api.py) | |
| rosbridge | `rosbridge:mock` | ✅ rehearsed | | | [adapters/rosbridge/README.md](rosbridge/README.md) |
| | `rosbridge:ws` | 🧪 bound · grandfathered 0.33.0 · 2026-11-05 | every roslibpy, rosbridge and message name VERIFIED at pinned commits, exercised with fake topics and fake services, including reading the robot's own description off the bridge, never run against a bridge | [`adapters/rosbridge/src/quackd_rosbridge/upstream_api.py`](../../adapters/rosbridge/src/quackd_rosbridge/upstream_api.py) | |
| Open Duck Mini v2 | `open_duck:sim2d` | ✅ rehearsed | `open-duck-scout` 10 of 10 seeds | | [adapters/open_duck/README.md](open_duck/README.md) |
| | `open_duck:mock` | ✅ rehearsed | | | |
| | `open_duck:bridge` | 🧪 bound · grandfathered 0.33.0 · 2026-11-05 | every runtime name VERIFIED at a pinned commit, the protocol exercised against the real daemon over loopback, never run on a duck | [`adapters/open_duck/src/quackd_open_duck/upstream_api.py`](../../adapters/open_duck/src/quackd_open_duck/upstream_api.py) | |
| XLeRobot | `xlerobot:mock` | ✅ rehearsed | | | [adapters/xlerobot/README.md](xlerobot/README.md) |
| | `xlerobot:zmq` | 🧪 bound · grandfathered 0.33.0 · 2026-11-05 | the whole wire format VERIFIED at a pinned commit, the client exercised against a fake host quackd wrote from that source over loopback, never run on a cart | [`adapters/xlerobot/src/quackd_xlerobot/upstream_api.py`](../../adapters/xlerobot/src/quackd_xlerobot/upstream_api.py) | |
| AlohaMini | `alohamini:mock` | ✅ rehearsed | | | [adapters/alohamini/README.md](alohamini/README.md) |
| | `alohamini:sim2d` | ✅ rehearsed | `alohamini-lookout` 10 of 10 seeds | | |
| | `alohamini:zmq` | 🧪 bound · grandfathered 0.33.0 · 2026-11-05 | the whole wire format VERIFIED at a pinned commit, the client exercised against a fake host quackd wrote from that source over loopback, never run on a robot. The arm verbs additionally need quackd's own host wrapper, which nobody has run either | [`adapters/alohamini/src/quackd_alohamini/upstream_api.py`](../../adapters/alohamini/src/quackd_alohamini/upstream_api.py) | |
| ToddlerBot | `toddlerbot:mock` | ✅ rehearsed | | | [adapters/toddlerbot/README.md](toddlerbot/README.md) |
| | `toddlerbot:sim2d` | ✅ rehearsed | `toddlerbot-lookout` 10 of 10 seeds | | |
| | `toddlerbot:bridge` | 🧪 bound · grandfathered 0.33.0 · 2026-11-05 | every upstream name VERIFIED at the commit the v2.0.0 tag points at, the protocol and the daemon's own safety machinery exercised against a fake body over loopback, never run on a robot | [`adapters/toddlerbot/src/quackd_toddlerbot/upstream_api.py`](../../adapters/toddlerbot/src/quackd_toddlerbot/upstream_api.py) | |
| Huebot | `huebot:mock` | ✅ rehearsed | | | [adapters/huebot/README.md](huebot/README.md) |
| | `huebot:jsonrpc` | 🧪 bound · grandfathered 0.33.0 · 2026-11-05 | robotd's protocol, every method VERIFIED at the Microduck's pin, spoken to a shim Huebot's maker is writing for the robot, which nobody has seen. Exercised against a fake shim over loopback, never run on a Huebot. The shim has to answer what [its page](huebot/README.md#what-huebots-shim-must-answer) lists | [`adapters/huebot/src/quackd_huebot/upstream_api.py`](../../adapters/huebot/src/quackd_huebot/upstream_api.py) | |
| MOSS | `moss:mock` | ✅ rehearsed | | | [adapters/moss/README.md](moss/README.md) |
| | `moss:cockpit` | 🧪 bound · grandfathered 0.33.0 · 2026-11-05 | MOSS's own cockpit for the state, a goal, the explorer and the soft stop, and quackd's own bridge on the rover for the twist, the timed frames, the arm cycle and the litter mission's stop. Every upstream name VERIFIED at the commit read, or UNVERIFIED with what quackd does about it. The client is exercised against a fake of the cockpit and against the bridge in its fake mode over loopback, and twice against MOSS's own cockpit in its demo mode, on Windows and in a Linux container, never run against a rover | [`adapters/moss/src/quackd_moss/upstream_api.py`](../../adapters/moss/src/quackd_moss/upstream_api.py) | |
| Industrial arm on ROS 2 | `rosarm:mock` | ✅ rehearsed | the ws transport over a fake rosbridge and a fake arm, for each of the four arms: a UR5e, a KUKA LBR iisy 3 R760, a KUKA LBR iiwa 7 R800 and a Yaskawa MotoMini. Each fake is written from reading that driver's source at a pinned commit, and no job runs the transport against a real controller yet | | [adapters/rosarm/README.md](rosarm/README.md) |
| | `rosarm:ws` | 📄 described | bound code with no owner, so `make()` and `quackd robot add` refuse it in the owner rule's words until somebody names a date for its first hardware run ([ADR-0078](../adr/0078-an-industrial-arm-is-moved-one-confirmed-goal-at-a-time.md)). Every name it sends and reads is read at a pinned commit, VERIFIED or UNVERIFIED with what quackd does about it, and the protocol's through rosbridge's own refs. It has met no controller and no robot | [`adapters/rosarm/src/quackd_rosarm/upstream_api.py`](../../adapters/rosarm/src/quackd_rosarm/upstream_api.py) | |

## Described bodies

A described body is a robot quackd can list, register, describe to a pilot and plan with in a
flock, and drives no part of ([ADR-0077](../adr/0077-a-body-quackd-only-describes-is-one-yaml-in-the-core.md)).
Each is one file under `quackd/adapters/bodies/`, holding what its maker publishes and a source
for every figure, and nothing a body nobody drives could honestly claim: no verbs that move, no
limits, no sensors, no safety authority. Its only verbs are `report_state` and `stop`, and a
pilot handed one is told before it reads a single figure that nothing here moves it. `quackd robot add b2 described:unitree-b2` registers
one like any other robot; `quackd list-verbs --robot described:unitree-b2` shows the two verbs.
An address, a camera, a token and a rest pose are refused, since there is no robot to reach, and
`quackd robot list --probe` says `described: nothing to reach`. Four of them, the UR5e, the two
KUKA LBRs and the MotoMini, are also the arms `rosarm:mock` rehearses, from the same files:
`described:ur5e` is still a body nothing moves.
Every figure below was read from the page its source names, on the day it says. Every body but
the UR7e had a second reader on 2026-11-09, who read each of those pages again and corrected
what was wrong, so a figure of theirs dated that day is one the second read changed, added or
found a new page for. The UR7e was added that day, and its figures have had one reader.
Nothing here has met quackd on hardware, and nothing here moves.

<!-- generated: described-bodies, by scripts/bodies_docs.py from quackd/adapters/bodies/*.yaml. Change a body file and run the script, not this table. -->

| `--robot` | What it is | Mass | Height | Joints | Payload | Reach | Endurance | Figures from |
|---|---|---|---|---|---|---|---|---|
| `described:franka-research-3` | a Franka Research 3, a seven-axis torque-sensing research arm bolted upright to a table, with a two-finger Franka Hand | 18.3 kg (official) | not published | 7 (official) | 3 kg (official) | 0.855 m (official) | mains powered | Franka Research 3 datasheet v2.6, July 2026 (read 2026-11-05) |
| `described:kuka-lbr-iisy-3-r760` | a KUKA LBR iisy 3 R760, a six-axis collaborative arm bolted to a bench and run from its own controller, with torque sensors in every axis | 22.8 kg (official) | not published | 6 (official) | 3 kg (official) | 0.76 m (official) | mains powered | KUKA LBR iisy 3 R760 datasheet 0000-320-854 (read 2026-11-05) |
| `described:kuka-lbr-iiwa-7-r800` | a KUKA LBR iiwa 7 R800, a seven-axis collaborative arm bolted to a bench and run from its own cabinet, with torque sensors in every axis | 23.9 kg (estimate) | not published | 7 (official) | 7 kg (official) | 0.8 m (official) | mains powered | KUKA's 2016 KMR iiwa presentation; kuka.com, LBR iiwa; kuka.com, LBR iiwa 7 R800 (read 2026-11-05) |
| `described:menlo-asimov-1` | a Menlo Asimov-1, an open-source humanoid of 1.2 m with two arms, which walks on two legs and is sold as a kit | 35 kg (official) | 1.2 m (official) | 25 (official) | 5 kg (official, per arm, held; 15 kg at peak, for about three seconds) | not published | not published | menlo.ai, Asimov-1 specifications; menlo.ai/asimov-1, the FAQ on how robust it is (read 2026-11-05, 2026-11-09) |
| `described:noetix-bumi` | a Noetix Bumi, a small humanoid just under 1 m tall, which walks on two legs | 17 kg (official) | 0.98 m (official) | 21 (official) | none to hold with | none to hold with | 120 min (official) | web.noetixrobotics.com/docs, Bumi specifications (read 2026-11-05) |
| `described:rhoban-microban` | a Rhoban Microban, a 30 cm open-hardware humanoid you print and build, with nineteen small servos around a Raspberry Pi Zero 2 W and no hands | not published | 0.3 m (official) | 19 (official) | none to hold with | none to hold with | not published | github.com/Rhoban/microban README (read 2026-11-05) |
| `described:unitree-b2` | a Unitree B2, an industrial quadruped the size of a large dog, with cameras and a LiDAR and nothing to grip with | 60 kg (official) | 0.645 m (official) | 12 (estimate) | none to hold with | none to hold with | 300 min (official, unloaded; over 4 h with a 20 kg load) | unitree.com/b2; docs.mybotshop.de/b2.html, a dealer's B2 page (read 2026-11-05, 2026-11-09) |
| `described:unitree-g1-edu` | a Unitree G1 EDU, a humanoid of 1.3 m with two arms, which walks on two legs | 35 kg (official) | 1.32 m (official) | 23 (official) | 3 kg (official, per arm) | not published | 120 min (official) | unitree.com/g1, G1 EDU column; unitree.com/g1 (read 2026-10-28, 2026-11-09) |
| `described:unitree-go2-edu` | a Unitree Go2 EDU, a small quadruped the size of a medium dog, with a wide-angle camera and a LiDAR and nothing to grip with | 15 kg (official) | 0.4 m (official) | 12 (official) | none to hold with | none to hold with | 120 min (official) | unitree.com/go2 (read 2026-11-05) |
| `described:ur5e` | a Universal Robots UR5e, a six-joint collaborative arm, bolted down any way up and run from its own control box | 20.6 kg (official) | not published | 6 (official) | 5 kg (official) | 0.85 m (official) | mains powered | UR5e datasheet, December 2024 (read 2026-11-05) |
| `described:ur7e` | a Universal Robots UR7e, a six-joint collaborative arm, bolted down any way up and run from its own control box | 20.6 kg (official) | not published | 6 (official) | 7.5 kg (official) | 0.85 m (official) | mains powered | UR7e datasheet, October 2026, universal-robots.com/manuals/latest/en/datasheets/ur7e (read 2026-11-09) |
| `described:yaskawa-motomini` | a Yaskawa MotoMini, a six-axis miniature industrial arm of 7 kg, bolted to a bench and run from its own controller, with no gripper of its own | 7 kg (official) | not published | 6 (official) | 0.5 kg (official) | 0.35 m (official) | mains powered | Yaskawa MotoMini flyer, July 2023 (read 2026-11-05) |

<!-- end generated: described-bodies -->

**The first real run, and what it does not prove.** One SO-101, one bench, one afternoon.
The arm fell at the end of every run, because LeRobot's `disconnect()` disables torque by its
own default and quackd kept that default: `quackd robot rest-pose`, and the parking at both
ends of a run that goes with it, exist because of those falls. One dry run aborted with `the
arm did not answer: TimeoutError` after a single heartbeat round trip failed, and nothing like
it happened again. One dry run aborted because the pilot answered `uncertain` and the human
said no. The camera framed the gripper and cropped the raised arm, so the LLM checked its own
waves against joint readings rather than against the picture. And four questions came back
unanswered: whether the holding band is anywhere near right, what a joint reads after ten
minutes of work, whether a stall is caught when you cause one on purpose, and whether 5 degrees
an action felt right in the room. They are in the list at the foot of
[adapters/lerobot/hardware-checklist.md](lerobot/hardware-checklist.md), which has since grown to
ask whether a slow `move_joints` looks like one motion and arrives when its time is up, whether a
joint let go at the edge of its travel settles onto its fold, and what the hand placed start
and a decision LLM leave open. Those four are why that 🤖 is a robot quackd has worked on
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
robot, and the one step that can need `sudo`, putting the account you log in with in the robot's
`robot` group, is marked as the owner's to allow, because the first Microduck most people touch
will belong to somebody else. `microduck-lookout` is the task to point at it first: nothing in its
allowlist moves a leg.

Ran `--robot open_duck:bridge` against a duck you built, `toddlerbot:bridge` against a
ToddlerBot on its stand, `xlerobot:zmq` against a cart, `alohamini:zmq` against an AlohaMini,
`microduck:jsonrpc` against a real duck, `huebot:jsonrpc` against a Huebot's shim, `moss:cockpit`
against a MOSS rover, or
`rosbridge:ws` against a bridge?
Open an issue with `quackd doctor` output and the first lines of `transcript.jsonl`, whose
`run_start` names the command that was typed and the version that ran it, so a report no longer
rests on anyone remembering either. A run directory holds `terminal.txt` beside it, and that is
not the evidence: it is the screen, a rendering of the same events that `--no-log` shortens. The
transcript is every intent quackd sent and every answer the robot gave back, which is what
somebody else can check. Every row above that flips from 🧪 to 🤖 is one line in an
`upstream_api.py` and one row here. `lerobot:real` is the one that has already flipped, on one
arm on one bench, so a second run against an SO-101 is still worth an issue: it either widens
that row or contradicts it, and the one that contradicts it is worth more.
