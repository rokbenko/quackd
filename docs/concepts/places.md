# Places

A robot that says where it is can name the spots it stood on, and its pilot can find them in
its list again. quackd makes no map of its own. It reads where a robot is from what the robot
itself offers, keeps the names a pilot or a person gave, and says how far each one is and which
way. How much a position can be trusted differs from one body to the next, and every reading says
which kind it is.

## Where a robot says where it is

A body's transport carries a reading, its `spatial` source, for the life of one connection
(`quackd.space.source`). Today that is:

| Body | Reading | Its frame |
|---|---|---|
| Microduck over `robotd` (`jsonrpc`) | contact odometry, `robot.state.odom`, which every robotd quackd serves has sent since before the release quackd reads | one connection, begun again within it when the duck is picked up, falls or rides a fall down, when robotd's clock goes back, or on a step longer than its walking policy could make in the time between two frames |
| Microduck `mock` | the mock's scripted pose | the mock's own, for as long as it runs |
| Microduck `sim2d` or `mujoco` in a room, with `--room` | quackd's stand-in for the duck's own map, `mapd`, kept to the numbers quackd read of it ([below](#a-room-for-the-simulator)) | the room's own: the same room is the same frame from one session to the next, until a wipe |

Every other body says nothing of where it is yet, and the place verbs are not offered on it. The
simulator without a room is one of them. A
robotd from before the estimator sends no `odom`, and one whose odometry stays at zero through a
walk its own state showed it taking (`robotd --fake`, which has no servos to read) gives no
reading either.

Odometry is the robot's own sense of how far it has gone, with nothing to correct it: it drifts a
little with every metre walked. Pollen measured the Microduck's at about 2.4 % of the distance
walked and about 2 degrees of heading, over 47.5 m of walks on their own floor
([Pollen's odometry post](https://pollen-robotics.com/microduck/blog/microduck-odometry/), 23
October 2026), figures quackd has not measured. That is why its frame is kept short, and why a
pilot is told how far the duck has walked in it.

## What the pilot sees

- **The state line** says where the robot is and how it knows, as `where=odometry(x, y, yaw rad,
  walked N m)` or `where=lost(why)`, beside `extras.where`, the same reading as data:
  `{kind, frame, x, y, yaw, walked_m}`. A Microduck's `x`, `y` and `theta` stay empty: those are
  the simulators' plain truth, which a flock reads as one arena every member shares.
- **Every observation** lists the nearest places as `places: kitchen 1.2 m +38°, door 0.6 m -95°`,
  each with how far it is and which way, in degrees from where the robot faces, positive to its
  left, or `door here` for the one it stands on, then the names of the places it cannot reach now
  and why. The line is absent on a body with no places, so a run without them reads as it always
  did.
- **The system prompt** gains a `## Places` section when a place verb is allowed, which says how
  far to trust each kind of reading.

## The verbs

| Verb | What it does | Class |
|---|---|---|
| `map` | where the robot is and by what, and every place with how far and which way, or why it cannot be walked to now; the map itself, when the robot keeps one | safe, reads only |
| `mark_place` | names the spot the robot stands on now; marking a name it has moves that place here | safe |
| `forget_place` | drops a name | safe |

None of them moves the body, so all three run before the pilot's verdict and while a pause a
robot met was granted holds. `mark_place` takes no coordinates: a place is where the robot stood,
read off the robot, never a spot a pilot named. It is refused while the robot does not know where
it is. A map that looks only when the robot stops says when it has not looked from the stop the
robot stands at yet, and `map` and `mark_place` wait for that look, up to the map's own settle
time, so what they read is what the stop saw.

## A place belongs to the frame it was marked in

A position means something only in the frame it was read in. A place marked in a frame the robot
is no longer in is **orphaned**: kept and listed, with the reason, and never walked to. Each place
reads as one of:

- `here`: in the frame the robot is in now, with how far and which way;
- waiting: the robot does not know where it is at the moment (it is held up, or its stream went
  quiet);
- orphaned: "it was marked on odometry that has started again since", or "its map was wiped".

quackd never converts a position from one frame into another. Two frames share no origin and no
heading to convert by, and a guess would send a robot somewhere nobody chose.

## Where places are kept

In `<memory dir>/<robot slug>.places.jsonl`, beside the robot's memory file and keyed as it is
([guides/memory.md](../guides/memory.md)), one JSON record a line: the places, and the frames
they were marked in. Like memory, every write goes to a temporary file renamed over the old one,
and nothing is locked. A robot keeps at most 100 places, a name is 1 to 32 letters, digits, spaces,
hyphens or apostrophes, names are the same in any case, and `start` is a name quackd keeps for
itself. With `--no-memory`, places last as long as the run or the session and nothing is written.
A dry run reads the places a robot has and changes none, as it runs no `mark_place` or
`forget_place`. `quackd drive` keeps them as a run does.

## A room for the simulator

`--room` lays a room out in either of the Microduck's simulators, `sim2d` and `mujoco`, on `run`,
`record`, `drive` and `serve-mcp`, for one duck: `arena`, `two-rooms`, `hall`, or a `.yaml` file
of your own. This is `two-rooms`:

```yaml
name: two-rooms
size: [3.0, 2.0]
walls:
  - [0.0, -1.0, 0.0, -0.3]
  - [0.0, 0.3, 0.0, 1.0]
boxes:
  - [1.0, 0.6, 0.4, 0.3]
start: [-1.0, 0.0, 0.0]
ball: {region: [0.3, -0.8, 1.4, 0.3]}
person: [-1.2, 0.7]
```

`size` is the outer walls, in metres across and centred on the origin. Each wall runs from `x0, y0`
to `x1, y1`, 4 cm thick unless a fifth number says, and each box is a centre and a size, `cx, cy,
width, depth`. `start` is where the duck stands and which way it faces, and `ball` a spot or a
region the seed draws it in. A mistake in a file is refused with the file and the key named.

In a room, a wall holds a walking duck where it is and counts a bump, and the ball rolls back off
it. The duck's camera sees the walls and the furniture, in greys no detector takes for anything,
and nothing behind them. On `mujoco` they are solid geoms 30 cm tall: the puppet is held off
them as the cartoon's duck is, and the walking duck meets them with its own feet, each touch
counted as a bump. And the duck keeps a map of the room the way its own `mapd` keeps one,
kept to the numbers quackd read of `mapd` ([the Microduck's page](../adapters/microduck/README.md#mapd-the-onboard-map-unverified-an-upstream-branch-in-no-release)):
it looks only from a stop, once the duck has stood still for 0.4 s, as far as the head's ToF sees,
45° across and 2 m out, and it is lost at the start and after a bump or a fall until two stops
agree. `map` draws it. Every state says the map is quackd's stand-in, so a walk in a room is no
measure of the real map's errors. Without `--room` the simulator is the arena it always was, and
says nothing of where the duck is.

## Memory that knows where

A note a pilot saves with `remember` keeps where the robot stood, read off the robot, when it knew.
When it is recalled, the note says how far that was from the nearest place and from where the
robot stands now, "saved 0.3 m from sofa, 2.1 m from here", or that its frame has ended since. A
note saved before places, or where the robot did not know, reads as it always did.

## What quackd does not do

- **Make a map.** A map is the robot's own work: a Microduck's `mapd`, when a robotd release
  carries it. The simulator's map of a room is a stand-in for that one, and knows the room's walls
  exactly.
- **Share a frame between robots.** Two robots that have just met share no frame of reference
  ([ADR-0020](../adr/0020-heterogeneous-flocks.md)), so their places stay their own.

Why it is built this way: [ADR-0068](../adr/0068-a-place-belongs-to-the-frame-it-was-marked-in-and-quackd-reads-a-map-rather-than-making-one.md).
