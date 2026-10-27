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
| Microduck over `robotd` (`jsonrpc`), with `mapd` beside it | the duck's own map, `mapd`: its newest pose carried on by robotd's odometry since (UNVERIFIED: `mapd` is in no release yet) | the map's own, known again in a later session by its first stops after its origin, until a wipe or a map with fewer stops |
| Microduck over `robotd` (`jsonrpc`) | contact odometry, `robot.state.odom`, which every robotd quackd serves has sent since before the release quackd reads | one connection, begun again within it when the duck is picked up, falls or rides a fall down, when robotd's clock goes back, or on a step longer than its walking policy could make in the time between two frames |
| Microduck `mock` | the mock's scripted pose | the mock's own, for as long as it runs |
| MOSS (`cockpit`, `mock`) | the cockpit's pose, the stack's odometry, dated on the rover's clock | the stack's run, `odom:moss:<run_id>`, over every connection to it, and begun again by nothing else; its own `go_to_place` drives by the planner |
| rosbridge (`ws`, `mock`) | `/odom`, dated by its header stamp, or by its arrival when the stack stamps nothing | one connection |
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
| `go_to_place` | walks to a place on the robot's own reading of where it is, and turns to face the way it faced there ([below](#walking-to-a-place)) | safe, moves the body |

The first three move nothing, so they run before the pilot's verdict and while a pause a robot
met was granted holds. `mark_place` takes no coordinates: a place is where the robot stood,
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

## Walking to a place

`go_to_place NAME` walks there on the robot's reading of where it is, never on its camera, which
is what `go_to` steers by.

- **The way.** With a map the robot keeps and a size its body is known by, it plans round what
  the map has seen to be solid, keeps the body's width off floor nobody has seen wherever a way
  over seen floor does, and crosses unseen floor only where none does. With neither, it walks a
  straight line.
- **Its stops.** It stops where its front, once it has come to rest, would be on floor its map
  has not seen, unless it has just looked from there that way, and at least once a leg of the
  length its map can follow, about 1.9 m for `mapd`, and waits there for the map to look. A stop
  that shows more, or corrects where the robot is, plans the way again.
- **Its numbers.** It walks at the share of the forward clamp `move` walks at by default, raised
  to the gait floor a body publishes, and turns at `search_scan`'s rate. How closely it follows
  its way is the distance it covers between two readings and a tick, at the share of what it asks
  for that the body says it achieves: the planner keeps that much room beside the body, and it
  turns in place rather than walk an arc that would bulge further. It calls a place reached
  within the body's footprint, or that distance when it is more. None is a number of its own.
- **How far blind.** Over floor nothing has seen, it walks no further than one `move` could,
  ten seconds at the forward clamp, and then stops and says how far is left.
- **What ends it.** Arrived, by the reading after its last look; a stop or a pause from beside
  it; a twist the body refuses; the battery rule, which it reads at every stop and which ends the
  run in the rule's own words; lost; the frame changing under it, a map wiped or odometry begun
  again; a reading that stops moving on, which holds the body still and then ends the walk; a
  reading that jumps further than any walk could between two; no headway, walking or turning in
  place toward its way, judged over twice the time the headway should take and the stale window
  more, since a gait that stood takes a moment to start and no body says how long; another try at
  the place that came less than half its tolerance nearer than the tries before; and its own
  `timeout_s`, two minutes at most. Each stops the body and says which it was. It walks on no
  reading that has not moved on since it began, nor one that has not since a stop, and the time
  it waits for one is not counted against its headway.
- **Facing the way.** At the place it turns to the way it faced there. A reading taken turning
  sways with the gait, so the reading after its last look judges that too; a body at the place
  that turns no nearer on another try is there, and says how far off the way it faces, and how
  near a try before came when one came nearer.
- **Refused before anything moves.** An orphaned place, a robot that does not know where it is,
  no way there, a way longer than its time, and a map that cannot be read. A place its map now
  has something solid at, or too near for the body, it walks to the nearest floor the body fits
  on, a body's width away at most, and says so; with none that near, it refuses.

It says how close it got and by what, `0.07 m from 'far' by its map`, and on odometry that the
reading drifts. It never says the robot is at a place.

## A room for the simulator

`--room` lays a room out in either of the Microduck's simulators, `sim2d` and `mujoco`, on `run`,
`record`, `drive` and `serve-mcp`, for one duck: `arena`, `two-rooms`, `hall`, `sofa-gap`, or a
`.yaml` file of your own. This is `two-rooms`:

```yaml
name: two-rooms
size: [3.0, 2.0]
walls:
  - [0.0, -1.0, 0.0, -0.3]
  - [0.0, 0.3, 0.0, 1.0]
boxes:
  - [1.0, 0.6, 0.4, 0.3]
start: [-1.0, 0.0, 0.0]
ball: {region: [0.3, -0.8, 1.4, 0.3], hidden: true}
person: [-1.2, 0.7]
```

`size` is the outer walls, in metres across and centred on the origin. Each wall runs from `x0, y0`
to `x1, y1`, 4 cm thick unless a fifth number says, and each box is a centre and a size, `cx, cy,
width, depth`. `start` is where the duck stands and which way it faces, and `ball` a spot or a
region the seed draws it in, with `hidden: true` only where no straight line from the start reaches
it, so the duck, turned whichever way it likes where it starts, cannot see it and has to go and
look. `two-rooms` hides it in the far room, and `sofa-gap` behind an armchair, beside a sofa with
a gap behind it narrower than the duck, which a map sees into only from its ends. A mistake in a
file is refused with the file and the key named.

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
