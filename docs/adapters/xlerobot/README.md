# XLeRobot

A dual-arm mobile manipulator on an IKEA cart: two five-joint arms with grippers, a two-motor
head, and a three-omniwheel base, all Feetech STS3215 servos. It is the first body in quackd
with both wheels and arms — everything before it was one or the other.

Upstream: [Vector-Wangel/XLeRobot](https://github.com/Vector-Wangel/XLeRobot), Apache-2.0.
Read on **2026-09-04**, pinned at
[`3d14695`](https://github.com/Vector-Wangel/XLeRobot/tree/3d14695e40c9c68229c0aacffca6053c75cd3eb6)
(`main`, 2026-07-22). The repository has **zero git tags and zero releases**, so a commit hash
is the only honest pin there is. Every name quackd spells lives in
[`adapters/xlerobot/src/quackd_xlerobot/upstream_api.py`](../../adapters/xlerobot/src/quackd_xlerobot/upstream_api.py).

**Nothing here has ever run on a cart.** The `zmq` backend is exercised against a fake host
over loopback sockets and against nothing else.

```bash
uv pip install 'quackd[xlerobot]'     # quackd's own adapter package, plus pyzmq

# offline, the default
quackd run xlerobot-lookout --robot xlerobot:mock --llm fake

# a real cart, once its host is running (see "Before it will answer" below)
quackd run xlerobot-lookout --robot xlerobot:zmq --address tcp://192.168.1.42:5555
```

## Backends

| `--robot` | What it is | Status |
|---|---|---|
| `xlerobot:mock` | a holonomic base and two arms in memory, with a synthetic camera | ✅ every verb runs offline in the test suite |
| `xlerobot:zmq` | the real cart, over the ZeroMQ host it already ships | 🧪 wire format VERIFIED at the pin, exercised against a fake host over loopback, **never run on a cart** |

## Why quackd does not import XLeRobot

XLeRobot is **not an installable package**: no PyPI entry, no `pyproject.toml`, no `setup.py`,
and it is absent from upstream `huggingface/lerobot`. Its documented install is copying files
into an existing lerobot source tree, which is not a dependency quackd can express.

It does, however, already ship a network API. So quackd speaks that wire directly, and
`quackd[xlerobot]` is `quackd-xlerobot`, quackd's own Apache-2.0 adapter package, plus `pyzmq`
and nothing else: no `lerobot`, no torch, no Python 3.12 floor. It installs on the 3.11 floor
and on Windows, and it still pulls nothing from upstream.

Two other routes were considered and rejected. `lerobot:real` refuses any `robot_type` that is
not an SO-101 follower and declares `mobility="none"`, which would delete every verb a mobile
base exists for. A companion daemon like `bridge/open_duck/` is what a robot with *no* network
API needs; this one has one.

## What this robot cannot do

- **`say` — there is no speaker and no microphone in the bill of materials**, and no audio code
  anywhere in the repository. The `sound` intent is not declared, so `say` does not exist here.
- **`gaze` — the head is on the wire but its axes are not documented.** `head_motor_1` and
  `head_motor_2` are ids 7 and 8, and which is yaw and which is pitch is stated nowhere. quackd
  therefore never commands the head at all, and `search_scan` turns the whole cart instead.
- **A battery reading.** The power station in the bill of materials has no data link, so
  `battery_percent` is permanently `None` and `battery` is not in `sensors`. A
  "battery below N%" abort can never fire on this robot.
- **A pose.** There is no odometry anywhere in the observation — velocities only — so `go_to`
  closes the loop on the camera alone and `report_state` never claims a position.
- **Cartesian reach, `pick`, `place`.** There is no inverse kinematics on the robot class and no
  policy ships with it.

Its datasheet, which the pilot is shown and told to judge a task against before anything moves ([manifest-spec.md](../manifest-spec.md)):

| | |
|---|---|
| Mass | 12 kg (official: the XLeRobot docs) |
| Actuated joints | 17 (official: the XLeRobot BOM; two five-joint arms with grippers, a two-axis head, three wheels) |
| Payload | 1 kg (official: the XLeRobot docs; per arm; 0.6 to 1.0 kg depending on the pose, and 1.0 kg is the limit) |
| Reach | 0.4 m (official: the XLeRobot docs and community measurements) |
| Endurance | 600 min (official: the XLeRobot docs; a 288 Wh power station, ten hours or more) |
| Working height | 0.5 to 1.25 m (official: the XLeRobot docs; the torso does not lift, so the hands work in this band and nowhere else) |
| Not published | height |

And what it cannot do whatever the task says, which is the half a refusal usually turns on, in the words the pilot is shown:

- lift more than 1 kg in one hand
- work lower than about 0.5 m or higher than about 1.25 m off the floor: the torso height is fixed
- move fast, catch, or manipulate a thing in one hand: no dynamic motion and no in-hand dexterity, in the maintainer's words
- go up or down a step: it is a 12 kg cart on three omniwheels

A figure nobody published is listed as not published, and the pilot is told to answer `uncertain` and name it, rather than guess, where a task turns on it. A `.duck` file can correct any of it for the build in front of you ([duck-spec.md](../duck-spec.md)).

## The manifest

| Field | Value |
|---|---|
| `embodiment` / `mobility` | `wheeled` / `wheeled` |
| `intents` | `twist`, `joint`, `gripper` |
| `sensors` | `joint_state`, and `camera` only once one has been seen on the wire |
| `verbs` | `report_state`, `stop`, `move`, `move_joints`, `gripper`, plus `observe`, `go_to`, `search_scan`, `approach_and` with a camera |
| `limits` | `max_vx` 0.3, `max_vy` 0.2, `max_wz` 1.5, `joint_norm` 100 |
| `safety_authority` | `native: none`, `deadman: true`, `heartbeat_hz: 2` |

The robot's own fast tier is 0.3 m/s and 90 deg/s, but quackd's schema caps `vy` at 0.2 and
`wz` at 1.5 rad/s and `limits` may only narrow, so on two of the three axes the schema binds
rather than the robot.

`move`'s own default `vx` is quackd's shared 0.15 m/s, which sits between upstream's
slow tier (0.1 m/s, 30 deg/s) and its medium one, and upstream's teleop opens at slow
with `speed_index` at zero. quackd does not vary a core verb's defaults per robot, so
the verb's description tells the model to ask for 0.1 explicitly on a first run. On a
12 kg cart that difference is worth knowing about before you find out.

**`safety_authority` is `native: none` with `deadman: true`.** The host's 500 ms watchdog is
real, but it calls `stop_base()`, which zeroes the three wheels and nothing else: the fourteen
arm and head servos keep holding their last goal. That is not an e-stop, not a torque limit and
not a `robotd_deadman`, so `native` stays `none` and `extras.deadman_scope` says `base_only`.

## Units, and the two that are easy to get wrong

- **`theta.vel` on the wire is degrees per second.** quackd's `wz` is rad/s, so the adapter
  converts in both directions. A pass-through would be a 57× error on a 12 kg cart.
- **Arm positions are normalised, not degrees.** `use_degrees` defaults to `False`, so a body
  joint is −100..100 and a gripper is 0..100. This is a *different contract* from the SO-101
  arm in [lerobot.md](lerobot.md), which sets degrees: the same number means a different angle,
  so `move_joints` here validates against `joint_norm` and never against `joint_deg`.
- `x.vel` and `y.vel` are m/s and pass through unchanged. `+x` is forward, `+y` is left.

## Cameras

**A stock XLeRobot is blind.** `xlerobot_cameras_config` returns a dict with every entry
commented out, so it evaluates to `{}` and the observation carries no image keys at all. That
is a config default its owner lifts, not a missing capability, and not something quackd can fix.

So the manifest is built from what the first observation actually carried: with no camera,
`observe`, `go_to`, `search_scan` and `approach_and` are not declared, by `REQUIREMENTS` rather
than by any branching of quackd's own. When several cameras are present a head camera wins over
a wrist one, whose bearing means nothing for navigation, and the choice is recorded in
`extras.camera_key`.

Note that the commented config has `right_wrist` and `head(RGDB)` both pointing at
`/dev/video2`, so an owner enabling cameras has to edit it regardless.

### Enabling a camera makes the whole robot depend on it

This is the hazard to know before you uncomment that block, because the symptom looks nothing
like the cause.

Once a camera is configured, the host's `is_connected` is `all(cam.is_connected for cam in
self.cameras.values())`, so a device that is not there stops the whole host rather than one
sensor. Worse, `async_read()` raises `TimeoutError` at **200 ms**, and the host reads every
camera inside the same loop that publishes the observation. A single stalled or flaky USB
camera therefore takes down the entire observation stream, including the joint state.

From quackd's side that is indistinguishable from the host being gone: the observations stop,
nothing on the wire is timestamped, and the heartbeat fires. The message you get will talk
about the host not answering and its one hour lifetime, because that is the far more common
cause. **If a cart goes quiet shortly after you enabled a camera, unplug the camera before you
debug anything else.**

The watchdog does not handle the robot end of this: it runs in the loop that just died. What
runs instead is the host's `finally`, which is upstream's `disconnect()`: the wheels are
zeroed, then torque is disabled and the arms go limp, dropping whatever they held. The same
`finally` runs when the hour is up.

### Colour order

quackd assumes the frames arrive BGR and swaps them, which is OpenCV's own order and what
upstream's capture path produces. It is listed UNVERIFIED because nobody has held a red ball in
front of a real cart. If your frames come back with red and blue reversed, pass
`?swap_colour=0` on the address:

```
--address tcp://xlerobot.local:5555?swap_colour=0
```

The detector `observe`, `go_to` and `search_scan` steer by is a colour blob detector, so a
swapped frame does not fail loudly. It quietly stops finding the thing you asked for.

## Before it will answer

The host is **commented out of upstream's own package `__init__`** and must be uncommented, then
started on the machine wired to the two Feetech buses. It **exits by itself after 3600 seconds**
and there is no systemd unit, autostart or supervisor anywhere in the repository, so a session
longer than an hour needs it restarted. When it stops answering, quackd's heartbeat says so.

**Nothing authenticates this wire.** Upstream's host binds its two ZeroMQ ports and accepts
whatever arrives: there is no handshake, no token and no `--token` here, because quackd is a
client of someone else's protocol rather than the author of one. Anything that can reach the
port can drive the robot. Bind it to loopback and reach it through an ssh tunnel.

## VERIFIED (read from upstream source on 2026-09-04, at `3d14695`)

| Thing | Value | Used for |
|---|---|---|
| Command port | `5555` | the host binds `PULL`, quackd connects `PUSH` |
| Observation port | `5556` | the host binds `PUSH`, quackd connects `PULL` |
| Conflation | `zmq.CONFLATE = 1` | on both sockets: only the newest message survives |
| Framing | `json.dumps / recv_string` | one JSON object per message, as a string |
| Host loop | `30` Hz | one observation published per cycle |
| Watchdog | `500` ms | then `robot.stop_base()` — the wheels, and nothing else |
| Host lifetime | `3600` s | then it disconnects the robot and exits |
| No client attached | `zmq.Again` | the host drops observations rather than queueing them |
| Package init | `# from .xlerobot_host import XLerobotHost` | the host is commented out by default |
| State keys | `XLerobot._state_ft` | the seventeen flat dotted float keys |
| Action space | `XLerobot.action_features` | identical to the state keys |
| Observation space | `XLerobot.observation_features` | the state keys plus one per camera |
| Partial actions | `XLerobot.send_action` | prefix and suffix filters, each bus write guarded |
| Base always written | `_body_to_wheel_raw(...)` | so an action with no velocity keys stops the base |
| No refusal channel | `return {**left_arm_pos, ...}` | quackd synthesises its own `Ack` |
| Observation | `XLerobot.get_observation` | four `sync_read`s plus one read per camera |
| Stop | `XLerobot.stop_base` | `Goal_Velocity` zeroed on the three wheels, `num_retry=5` |
| Disconnect | `XLerobot.disconnect` | disables torque: the arms go limp. quackd never sends it |
| Connect blocks | `input(...)` | on a bare prompt whenever a calibration file exists |
| Turn rate unit | `theta_cmd : Rotational velocity (deg/s)` | quackd converts from rad/s |
| A wrong docstring | `A dict (x.vel, y.vel, theta.vel) all in m/s` | it is m/s **and deg/s**; the trailing comment is right |
| Joint units | `use_degrees: bool = False` | so positions are normalised −100..100 |
| Gripper units | `MotorNormMode.RANGE_0_100` | 0..100 whatever `use_degrees` says |
| Speed tiers | `[{xy: 0.1, theta: 30}, {xy: 0.2, theta: 60}, {xy: 0.3, theta: 90}]` | the ceiling, and the slow tier to open at |
| Wheel cap | `max_raw: int = 3000` | all three scale down proportionally past it |
| Arm step clamp | `max_relative_target: int | None = None` | there is none unless the owner sets one |
| Camera config | `xlerobot_cameras_config` | every entry commented out: a stock cart is blind |
| Device clash | `/dev/video2` | two commented entries name the same device |
| Frame encoding | `cv2.IMWRITE_JPEG_QUALITY, 90` | base64 JPEG inside the same JSON object |
| Encode failure | `last_observation[cam_key] = ""` | the key stays, the frame does not |

## UNVERIFIED, and what quackd does about it

| Name | The assumption | What quackd does |
|---|---|---|
| `CAMERA_KEY_SHAPE` | a camera is any observation key whose value is a string | the seventeen state keys are floats, so a string is the marker; the chosen key is in `extras.camera_key` |
| `CAMERA_COLOR_ORDER` | the host's JPEG holds BGR | quackd swaps to RGB. One photograph of a red object retires this |
| `CAMERA_FOV_DEG` | upstream names no field of view | the detector's 90° is the simulator's camera, not this one; bearings are approximate and `extras.assumptions` says so |
| `HEAD_AXES` | which head motor is yaw is undocumented. Upstream's own agent library RoboCrew drives id 7 as yaw and id 8 as pitch, but that is second-hand and its pitch range is not centred on zero | quackd never commands the head, and does not declare `gaze` |
| `BASE_VARIANT` | the three-omniwheel base is the default | quackd cannot tell the bases apart on the wire, so the owner says which with `?variant=diff2` or `?variant=mecanum` on the address; those declare `max_vy: 0.0`, so a strafe is clamped and reported rather than silently ignored |
| `OBSERVATION_STALENESS` | nothing on the wire is timestamped | quackd stamps on arrival and calls a reading older than the watchdog window a heartbeat failure, instead of serving a cache like upstream's own client does |
| `THREAD_SAFETY` | the sockets' thread safety is undocumented | every send and receive is serialised under one lock in a worker thread |
| `BATTERY` | nothing reports a battery | `battery_percent` is `None` and the sensor is not declared |

## How to help

If you have built an XLeRobot, the useful thing is a first run. Work through
[xlerobot-hardware-checklist.md](../xlerobot-hardware-checklist.md) in order: it keeps the
wheels on blocks until step 9, and the first thing it asks for is `quackd doctor --address`,
which connects, rather than `list-verbs`, which does not.

Note that `xlerobot-lookout` needs a camera. It requires `observe`, and a stock cart is blind,
so on an unmodified cart it refuses before a verb runs. Report `doctor` and a `report_state`
instead, or enable a camera first.

What most needs a real cart: the camera colour order, the base sign convention (`+x` really
forward?), and whether the head motors are what RoboCrew implies. Open an issue with the
transcript.
