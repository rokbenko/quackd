# The robot manifest (v1)

What a connected robot is and can do, as data. Pydantic in code
(`quackd/adapters/manifest.py`), JSON on the wire (MCP `robot_list`, mDNS by digest, the
flock bus), never YAML on disk. The JSON Schema is exported to
`quackd/adapters/manifest.schema.json` (`python -m quackd.adapters.export`) and
drift-tested. Every adapter's `connect()` returns one; `describe()` returns the static
version without touching the robot.

The model is core and the robots are not. Each adapter is its own package under `adapters/`,
imported as `quackd_<name>` and installed by its own extra, and it reaches this file the way
anyone would: `from quackd.adapters.manifest import RobotManifest`. No module in the core
imports an adapter by name, which is why an adapter quackd does not publish declares its
manifest on exactly the same terms ([architecture.md](architecture.md),
[adapters.md](adapters.md)).

## Fields

| Field | Type | Meaning |
|---|---|---|
| `manifest` | `1` | schema version |
| `id` | slug | unique within a run or a flock: `microduck`, `open-duck-01`, `duck-01`, or the member name from `--robots name=...` |
| `vendor`, `model` | string | who made it and what it is (`huggingface`, `lerobot-so101`) |
| `embodiment` | `biped`, `quadruped`, `wheeled`, `arm`, `humanoid` | the body |
| `mobility` | `none`, `legged`, `wheeled` | whether it can go somewhere |
| `intents` | list of `twist`, `skill`, `gaze`, `sound`, `joint`, `pose`, `gripper` | the command channels the backend accepts |
| `sensors` | list of `camera`, `battery`, `odometry`, `imu`, `tof`, `microphone`, `joint_state` | what it can report |
| `verbs` | list of `VerbSpec` | the vocabulary (see below) |
| `preconditions` | verb → list of condition names | checks the executor runs before a verb; the adapter supplies the predicates by name |
| `safety_authority` | `{native, deadman, heartbeat_hz}` | who stops the body when quackd goes quiet: `native` is `robotd_deadman`, `lease`, `torque_limit`, `estop` or `none`; `deadman` is whether motion zeroes on silence, wherever that code lives. An Open Duck Mini declares `native: none` with `deadman: true`, because the thing doing the zeroing is quackd's own daemon running on the robot — on the `bridge` backend, only when that daemon reported a `deadman_ms` window at connect |
| `frame` | `{reference, note}` | `body`, `head`, `base` or `world`; what bearings are relative to |
| `limits` | name → number | `max_vx`, `max_vy`, `max_wz`, `gaze_yaw_deg`, `gaze_pitch_deg`, `joint_deg`, ...; the core verbs clamp to them. `camera_fov_deg` is the exception: not a clamp but the lens the detector assumes, and without it (or `--fov-deg`) a real camera falls back to the simulator's 90° and every detection is labelled uncalibrated |
| `backend` | string | informational: which backend produced this |
| `blurb` | string | the prompt's one-line intro: "a small biped duck robot (25 cm, 800 g)" |
| `datasheet` | `Datasheet` or absent | the body as numbers, each with how sure quackd is and who says so, plus what it cannot do whatever the task says (see below) |
| `extras` | object | adapter-specific facts (`speech: tones`, `camera_calibrated: false`, `policy: true`) |

A `VerbSpec`: `name` (canonical, never an alias), `core` (the same verb on every robot
that has it), `description` (LLM-facing; empty means the implementation's default),
`params_schema` (informational JSON schema), `safety_class` (`safe`, `confirm`,
`dangerous`), `timeout_s`.

## Invariants the model enforces

- No duplicate verbs; no alias as a verb name (`walk` is declared as `move`).
- `stop` is inserted when missing, is `core`, and can never be anything but `safe`.
- A core verb must have what it requires (`quackd/verbs/core.py`, `REQUIREMENTS`):

| Core verb | Requires |
|---|---|
| `observe` (alias `get_frame`) | a `camera` sensor |
| `report_state` | nothing |
| `stop` | nothing |
| `say` | the `sound` intent |
| `move` (alias `walk`) | the `twist` intent and mobility |
| `go_to` (alias `walk_to`) | `twist`, mobility and a camera |
| `search_scan` | a camera and either `twist` or `gaze` (it turns in place or sweeps the head) |
| `approach_and` | `twist`, mobility and a camera |

- Every verb in `preconditions` is declared; every condition name has a predicate in the
  adapter's `conditions()` (checked when the registry is built).
- `intents` and `sensors` carry no duplicates.

## The datasheet

The body as numbers, so a pilot can refuse a task before anything moves
([ADR-0032](adr/0032-datasheets-and-the-verdict.md)). Each number is a `Figure`: a `value`,
a `confidence` and a `source`, plus an optional `note` read with it.

| Field | Meaning |
|---|---|
| `mass_kg`, `height_m`, `dof` | what it weighs, how tall it stands, how many joints it actuates |
| `payload_kg` | what one hand, the beak or the whole body can hold; the `note` says which |
| `reach_m` | arm base to fingertips |
| `workspace_height_m` | a `Span`: the band of heights the hands can work at, as `low` and `high` with a `confidence` and a `source` of their own |
| `endurance_min` | minutes on a charge |
| `manipulator` | `none`, `beak`, `gripper` or `arms`: what quackd can command that touches an object |
| `arms`, `tethered`, `terrain`, `not_rated` | how many, mains or battery, what it is rated for (`indoor_flat`, `indoor` or `outdoor`) and what it is not |
| `cannot`, `notes` | sentences: what it cannot do whatever the task says, and what is worth knowing first |

Three rules make it honest:

- **`confidence` is one of `official` (the maker or a paper says so), `estimate` (one vendor,
  a community number, a reading off a photo) or `measured` (somebody measured it and said
  how), and `source` is required.** A figure without a source is a rumour.
- **`None` means not published.** The prompt says so in those words and tells the pilot,
  where a task turns on one, to answer `uncertain` and name it rather than guess, and a
  `feasible` verdict that names it is refused. It is never rendered as a zero, and a figure
  that cannot apply (endurance on a mains-powered arm, payload on a body with no
  manipulator) is not listed as missing either.
- **Speeds are not in it.** `limits` is what quackd clamps to, which is a rule about what
  quackd sends rather than a fact about the body, and the prompt renders those separately as
  clamps.

`manipulator` is the only field a sheet must carry, and three contradictions are refused
when the manifest is built rather than left to a reader: arms on a body whose manipulator is
`none`, a `gripper` or `arms` with no arm to put it on, and a payload or a reach on a body
with nothing to hold with.

The same datasheet describes a body on every backend, which is also what keeps its `digest()`
equal across `sim2d`, `mock` and the real thing. `rosbridge` is the one exception, because it
names a transport rather than a body: its sheet is whatever the bridge answered when asked
([adapters/rosbridge.md](adapters/rosbridge.md)).

The seven sheets quackd publishes, with each figure's confidence:

| Robot | Mass | Height | Joints | Payload | Reach | Endurance | Hands |
|---|---|---|---|---|---|---|---|
| `microduck` | 0.8 kg official | 0.25 m official | 15 official | not published | not published | not published | a beak |
| `open_duck` | not published | 0.42 m official | 14 estimate | not published | not published | not published | none |
| `lerobot` | not published | 0.53 m estimate | 6 official | 0.5 kg estimate | 0.4 m estimate, from the maker's URDF | mains powered | one gripper |
| `rosbridge` | from the URDF | not published | from the URDF | not published | not published | not published | none |
| `xlerobot` | 12 kg official | not published | 17 official | 1.0 kg official, per arm | 0.40 m official | 600 min official | two grippers |
| `alohamini` | not published | not published | 14 official | 1.0 kg official, per arm | 0.52 m official | not published | two grippers |
| `toddlerbot` | 3.4 kg official | 0.56 m official | 30 official | 1.484 kg official, both arms | not published | 19 min official | two arms |

One body carries the eighth figure: the XLeRobot publishes a working height of 0.5 to
1.25 m, official, because its torso does not lift, so its hands work in that band and
nowhere else.

A `.duck` file can correct any of it for the build in front of it
([duck-spec.md](duck-spec.md)), and the prompt labels those numbers as coming from the task
file.

## Intents on the wire

The manifest speaks the vocabulary other systems read; the backends speak the intent kinds
quackd has used since 0.1. One table maps them (`INTENT_KIND_FOR`): `twist → move`,
`skill → do`, `gaze → look`, `sound → sound`, `joint → joint`, `pose → pose`,
`gripper → gripper`.

## The digest

`digest()` is sha256 of the canonical sorted-key JSON **excluding `id` and `backend`**,
first 16 hex characters: a capability fingerprint. The same robot over `sim2d`, `mujoco` and `mock`
hashes the same; a robot with one more verb hashes differently. Discovery advertises it in
TXT (`sha`) so a manifest fetched out of band can be checked against what was announced,
and MCP `robot_list` returns it.

## Examples

The seven manifests quackd publishes, from `quackd list-verbs --robot ...` or `describe()`.
Both read the static manifest without connecting to anything, and both need that body's
package installed: on a machine without it, `quackd list-verbs --robot lerobot:mock` refuses
with `adapter 'lerobot' needs an extra: uv pip install 'quackd[lerobot]'`.

| Robot | embodiment / mobility | intents | verbs |
|---|---|---|---|
| `microduck:sim2d` | biped / legged | twist, skill, gaze, sound, pose | observe, report_state, stop, say, move, go_to, search_scan, approach_and, sit, stand, stand_up, kick, grab, gaze, quack |
| `lerobot:mock` | arm / none | joint, gripper, skill | observe, report_state, stop, move_joints, gripper, place, pick, manipulate |
| `rosbridge:mock` | wheeled / wheeled | twist | observe, report_state, stop, move, introspect, go_to, search_scan, approach_and |
| `open_duck:sim2d` | biped / legged | twist, gaze, sound, skill | report_state, stop, move, observe, go_to, search_scan, approach_and, say, quack, gaze, express |
| `xlerobot:mock` | wheeled / wheeled | twist, joint, gripper | observe, report_state, stop, move, move_joints, gripper, go_to, search_scan, approach_and |
| `alohamini:mock` | wheeled / wheeled | twist, pose, joint, gripper | observe, report_state, stop, move, lift, move_joints, gripper, home_arms, go_to, search_scan, approach_and |
| `toddlerbot:mock` | humanoid / legged | skill, gaze, twist | observe, report_state, stop, stand, perform, look, move, go_to, approach_and, search_scan |

What each body lacks is as important as what it has: the arm cannot `move`, neither the
base nor the cart can `say`, and a `.duck` that `requires` one of those fails
validation against that robot with a field-level message
([duck-spec.md](duck-spec.md)).
