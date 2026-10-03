# AlohaMini

Two follower arms on a motorised vertical lift, on a three-omniwheel holonomic base. Despite
the name it is architecturally much closer to a LeKiwi than to an ALOHA: its own bill of
materials says so. It is quackd's second bimanual body and its first with a lift.

Upstream: [liyiteng/lerobot_alohamini](https://github.com/liyiteng/lerobot_alohamini),
Apache-2.0, pinned at
[`ab4462b`](https://github.com/liyiteng/lerobot_alohamini/tree/ab4462b713aeb24d0473f1ec6c8812290ab19510)
(`main`, 2026-07-24; read 2026-09-05). There are no tags and no releases, so a commit hash is
the only pin there is. The hardware repository
[liyiteng/AlohaMini](https://github.com/liyiteng/AlohaMini) (`17c6a98d`) holds the CAD and the
BOM and no software quackd uses. Every name quackd spells lives in
[`adapters/alohamini/src/quackd_alohamini/upstream_api.py`](../../adapters/alohamini/src/quackd_alohamini/upstream_api.py).

**Nothing here has ever run on a robot.**

```bash
uv pip install 'quackd[alohamini]'    # quackd's own adapter package, plus pyzmq

# offline
quackd run alohamini-lookout --robot alohamini:sim2d --llm fake

# a real robot, once quackd's host is running on its Raspberry Pi
quackd run alohamini-lookout --robot alohamini:zmq --address tcp://192.168.1.50:5555
```

## Backends

| `--robot` | What it is | Status |
|---|---|---|
| `alohamini:mock` | base, lift and arms in memory, with a synthetic camera | ✅ every verb runs offline in the test suite |
| `alohamini:sim2d` | the cartoon world, which already integrates a holonomic base, plus a lift | ✅ `alohamini-lookout` 10 of 10 seeds |
| `alohamini:zmq` | the real robot, over the ZeroMQ host it ships | 🧪 wire format VERIFIED at the pin, exercised against a fake host over loopback, **never run on a robot** |

## Why quackd does not import it

The software repository is **a fork of LeRobot that calls itself `lerobot`**. It is not
published to PyPI, so it installs only from a 133 MB git clone, on Python 3.12, pulling torch,
under a distribution name that collides with HuggingFace's own package. That is not a
dependency quackd can express at all.

It does ship a network API, so quackd speaks that, and `quackd[alohamini]` is
`quackd-alohamini`, quackd's own Apache-2.0 adapter package, plus `pyzmq` and nothing else.
Speaking the wire also buys something importing would not: upstream's own client discards the
host's `_images` list and trusts its own config instead, and its `robot_model` default
disagrees with the host's. Reading the wire makes both impossible to get wrong.

**Nothing authenticates this wire.** Upstream's host binds its two ZeroMQ ports and accepts
whatever arrives: there is no handshake, no token and no `--token` here, because quackd is a
client of someone else's protocol rather than the author of one. Anything that can reach the
port can drive the robot. Bind it to loopback and reach it through an ssh tunnel.

## What this robot cannot do

- **`say` — there is no speaker.** Nothing in the driver produces audio, so the `sound` intent
  is not declared and `say` does not exist here.
- **`gaze` — there is no head.** No pan, no tilt, nothing. `search_scan` turns the whole base.
- **A battery reading.** Nothing reports one, so `battery_percent` is always `None` and a
  "battery below N%" abort can never fire on this robot.
- **A position.** The observation carries `x.vel`, `y.vel` and `theta.vel` and no pose at all,
  so `go_to` closes the loop on the camera alone and a lost target has no fallback.

Its datasheet, which the pilot is shown and told to judge a task against before anything moves ([manifest-spec.md](../manifest-spec.md)):

| | |
|---|---|
| Actuated joints | 14 (official: the AlohaMini2 README; six joints and a gripper per arm; the lift and the base are on top of that) |
| Payload | 1 kg (official: the AlohaMini2 README; per arm) |
| Reach | 0.52 m (official: the AlohaMini2 README) |
| Not published | mass, height, endurance |

And what it cannot do whatever the task says, which is the half a refusal usually turns on, in the words the pilot is shown:

- hold more than 1 kg in one hand
- reach further than about half a metre from an arm's base
- see depth: five colour cameras, no depth sensor and no lidar

A figure nobody published is listed as not published, and the pilot is told to answer `uncertain` and name it, rather than guess, where a task turns on it. A `.duck` file can correct any of it for the build in front of you ([duck-spec.md](../duck-spec.md)).

## The arms are limp, and that is upstream's own state

`configure()` calls `disable_torque()` on both arm buses, and **both of its `enable_torque()`
calls are commented out**. Nothing else in the driver turns torque on: the only `Torque_Enable`
write in the whole package is a zero inside `lift_axis.home()`, and neither `configure_motors`
nor `write_calibration` touches it. So on a stock host a joint command moves nothing and a stop
could not hold an arm even if it wanted to.

quackd ships [`bridge/alohamini/quackd_alohamini_host.py`](../../bridge/alohamini/quackd_alohamini_host.py),
a small wrapper that runs upstream's own host loop with torque enabled, the lift stopped, and
three `quackd_` fields added to every observation. Run it **instead of** upstream's host:

```bash
# on the robot, in upstream's environment
python quackd_alohamini_host.py --robot_model alohamini2
```

Without it, `arm_torque` is false on the wire and `move_joints`, `gripper` and `home_arms`
refuse with a message naming the fix. The base, the lift and `stop` are unaffected.

## The stop, which is the whole difficulty

```python
{"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, "lift_axis.vel": 0}
```

Every element is load-bearing, and three separate upstream behaviours conspire here:

1. **All three velocity keys are mandatory in every payload.** `send_action` indexes
   `base_goal_vel["x.vel"]` with no `.get()` and no default, and it does so *before* the lift
   is touched and before any bus write. Omit one and the whole action is discarded, arms
   included, and `last_cmd_time` is never refreshed.
2. **The lift latches.** `LiftAxis.apply_action` is two independent `if key in action` blocks
   with no `else` and no unconditional write. A payload carrying neither lift key writes
   nothing, so the servo keeps its last `Goal_Velocity` and keeps travelling, while the command
   itself refreshes the watchdog that would have stopped it.
3. **The two lift keys are not symmetric.** The height branch runs first and the velocity
   branch second, so a payload carrying *both* ends with velocity winning and the lift frozen.

Hence quackd's rule, enforced in one function: **every payload carries all three velocity keys
and exactly one lift key** — the height while deliberately driving the lift, the velocity zero
the rest of the time. Two tests pin it, and a third proves the fake host really does discard a
payload that breaks rule 1.

Two more, for completeness. `home()` runs during `connect()` and leaves full-speed descent in
the lift's register, because the write that would zero it afterwards is commented out, so **the
first thing quackd says to this robot is a stop**. And `stop_base` writes with `num_retry=0` on
a bus it shares with the lift, so quackd sends its stop more than once.

## Units

- **`theta.vel` on the wire is degrees per second**; quackd's `wz` is rad/s and the adapter
  converts both ways.
- **Arm positions are normalised, not degrees**: `use_degrees` defaults to `False`, so a body
  joint is −100..100 and a gripper is 0..100. A host built with `use_degrees=True` would make
  every number mean something else, and nothing on the wire reports which it is.
- **The lift is absolute millimetres**, 5 to 600. The floor is 5, not 0, because upstream
  refuses downward motion below it rather than clamping.

## Which robot it is

Three SKUs. `alohamini1` has six joints per arm; `alohamini2` and `alohamini2pro` have seven,
inserting `wrist_yaw`. quackd derives which from the observed key set, never from config,
because the host defaults to `alohamini2` while upstream's own client defaults to `alohamini1`
and nothing cross-checks them. A `--no_follower` host runs the base and the lift alone. quackd detects that from the
first observation's key set and drops `move_joints`, `gripper` and `home_arms`, leaving
eight verbs rather than eleven: `report_state`, `stop`, `move`, `lift`, `observe`, `go_to`,
`search_scan` and `approach_and`. The lift survives because it is not an arm.

## VERIFIED (read from upstream source on 2026-09-05, at `ab4462b`)

| Thing | Value | Used for |
|---|---|---|
| Command port | `5555` | the host binds PULL, quackd connects PUSH |
| Observation port | `5556` | the host binds ROUTER, quackd connects DEALER |
| Command conflation | `zmq.CONFLATE = 1` | newest command wins; quackd merges into one payload |
| Request window | `3` | SNDHWM and RCVHWM on the ROUTER |
| Request/reply | `recv_multipart / send_multipart` | a token out, `[token, state, name, jpeg, ...]` back |
| Reply shape | `build_observation_multipart` | state as JSON, then two frames per camera |
| Encoding | `"_image_encoding": "jpeg"` | quackd refuses any other value |
| Camera list | `"_images"` | what the host actually sent; upstream's client discards it |
| Frame quality | `cv2.IMWRITE_JPEG_QUALITY, 70` | quackd never sees a raw sensor frame |
| Watchdog | `1000` ms | then `robot.stop_motion()` |
| Watchdog action | `robot.stop_motion()` | the base and the lift, and never the arms |
| Host lifetime | `6000` s | then it disconnects and exits |
| Host loop | `30` Hz | single threaded |
| Client poll | `200` ms | must exceed one host cycle |
| Connect timeout | `5` s | there is no hello: a handshake is the first observation |
| State keys | `AlohaMini._state_ft` | both arms, three velocities, the lift height |
| Not a state key | `#"lift_axis.vel"` | commented out, so quackd injects it |
| Arm key prefixes | `startswith("arm_left_")` | and `arm_right_`, both with a `.pos` suffix |
| Mandatory keys | `base_goal_vel["x.vel"]` | no `.get()`: omit one and the action is discarded |
| Base always written | `self.left_bus.sync_write("Goal_Velocity", base_wheel_goal_vel)` | every accepted action commands the wheels |
| Joint sets | `ARM_PROFILE_JOINTS` | six per arm on the 5dof profile, seven on the others |
| SKU table | `ROBOT_SPECS` | lead screw, wheel radius and motors per model |
| Key format | `f"{prefix}_{joint}.pos"` | with prefixes `arm_left` and `arm_right` |
| Default disagreement | `robot_model: str = "alohamini2"` | the host's default; the client's is alohamini1 |
| The lift's two ifs | `LiftAxis.apply_action` | no else, no unconditional write |
| Homing leaves it running | `#self._bus.write("Goal_Velocity", name, 0)` | commented out |
| Lift stop | `LiftAxis.stop` | writes `Goal_Velocity` 0 |
| The zero reaches the bus | `self._bus.write("Goal_Velocity", self.cfg.name, v * self.cfg.dir_sign)` | outside the try that wraps the height read |
| Cast before the try | `v = int(action[key_v])` | so a bad type raises and the stop does nothing |
| Lift limits | `soft_min_mm 0.0, soft_max_mm 600, descent_floor_mm 5.0` | the floor is a refusal |
| Lift gains | `kp_vel 300, v_max 1300, on_target_mm 1.0` | one step per command received |
| Lift mode is only set by homing | `#self.lift.configure()` | so an uncalibrated robot's lift means nothing |
| Stop is not retried | `num_retry=0` | quackd repeats its stop |
| Stop skips the arms | `AlohaMini.stop_motion` | base plus lift, nothing else |
| Disconnect goes limp | `AlohaMini.disconnect` | quackd never sends it |
| Torque is never enabled | `#self.left_bus.enable_torque()` | and the right arm's, both commented out |
| Over-current kills the host | `sys.exit(1)` | after 20 consecutive reads over the limit |
| Current limits | `gripper 500 mA, joint 1800 mA, global 2000 mA` | raw counts scale by 6.5 |
| Turn rate unit | `theta_cmd  : Rotational velocity (deg/s)` | quackd converts from rad/s |
| Sign convention | `velocity_vector = np.array([-x, -y, theta_rad])` | negated on both paths, so they agree with each other |
| Joint units | `use_degrees: bool = False` | normalised −100..100, not degrees |
| Speed tiers | `[{"xy": 0.15, "theta": 45}, {"xy": 0.2, "theta": 60}, {"xy": 0.25, "theta": 75}]` | the ceiling |
| Cameras | `alohamini_cameras_config` | five declared, two uncommented |

One more, kept out of the table because a pipe character cannot live in one: the arms have
no per-step clamp at all unless the owner sets one, because the config declares
`max_relative_target: int | None = None`. It never applies to the base or the lift either,
so quackd validates every goal against the manifest before it is sent.

## UNVERIFIED, and what quackd does about it

| Name | The assumption | What quackd does |
|---|---|---|
| `BASE_SIGN_CONVENTION` | that a positive `x.vel` drives forward | no source read settles it; the bring-up step is to drive +x briefly and watch |
| `CAMERA_COLOUR_ORDER` | the bytes hold RGB in stored order | the chain swaps twice and most likely lands back on RGB, so quackd does not swap again. One photograph of a red object retires this |
| `LIFT_TRAVEL_SPEED` | nothing states mm/s | the verb is gated and generously timed rather than pretending to know |
| `USE_DEGREES_IS_INVISIBLE_TO_A_CLIENT` | the host was built with normalised units | nothing on the wire reports the flag; the manifest declares normalised and says so |
| `ARM_TORQUE_NEEDS_THE_QUACKD_HOST` | the wrapper's own field means torque is on | quackd asserts it from a field it puts there itself, and the arm verbs are unavailable without it |
| `OBSERVATION_STALENESS` | nothing is timestamped | quackd stamps on arrival and only surfaces a reading whose sequence advanced |
| `THREAD_SAFETY` | the sockets' thread safety is undocumented | every send and receive is serialised under one lock |
| `BATTERY` | nothing reports a battery | `battery_percent` is None and no battery sensor is declared |
| `ODOMETRY` | there is no pose | `go_to` closes on the camera alone |

## How to help

If you have built an AlohaMini, the useful thing is a first run. Work through
[alohamini-hardware-checklist.md](../alohamini-hardware-checklist.md) in order. It starts on
upstream's **stock** host rather than quackd's, because with the arms limp the base and the
lift can be exercised with no arm risk at all, and only then switches to the wrapper that
turns torque on. What most needs a real robot: whether `+x` is physically forward, the
camera colour order, how fast the lift actually travels, and whether the wrapper really does
leave the arms holding.
