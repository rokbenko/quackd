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

Every other body says nothing of where it is yet, and the place verbs are not offered on it. A
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
  and why. The line is absent on a body
  with no places, so a run without them reads as it always did.
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
it is.

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

## Memory that knows where

A note a pilot saves with `remember` keeps where the robot stood, read off the robot, when it knew.
When it is recalled, the note says how far that was from the nearest place and from where the
robot stands now, "saved 0.3 m from sofa, 2.1 m from here", or that its frame has ended since. A
note saved before places, or where the robot did not know, reads as it always did.

## What quackd does not do

- **Make a map.** A map is the robot's own work: a Microduck's `mapd`, when a robotd release
  carries it.
- **Share a frame between robots.** Two robots that have just met share no frame of reference
  ([ADR-0020](../adr/0020-heterogeneous-flocks.md)), so their places stay their own.

Why it is built this way: [ADR-0068](../adr/0068-a-place-belongs-to-the-frame-it-was-marked-in-and-quackd-reads-a-map-rather-than-making-one.md).
