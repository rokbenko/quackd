# ADR-0068: A place belongs to the frame it was marked in, and quackd reads a map rather than making one

**Status:** accepted · **Date:** 2026-10-26 · Builds on [ADR-0025](0025-memory-between-runs.md) (a note can say where it was saved) and keeps [ADR-0020](0020-heterogeneous-flocks.md) (robots share no frame) · Implemented in `quackd/space/` (`pose`, `source`, `grid`, `odometry`, `places`), `quackd/verbs/places.py`, `quackd/transport/base.py` (`spatial_of`, `DuckState.summary`), `quackd/safety.py` (`Executor.places`, `place_book`), `quackd/agent/loop.py`, `quackd/mcp_server.py`, `quackd/memory.py` and the Microduck's `jsonrpc` transport · Documented in [docs/concepts/places.md](../concepts/places.md)

## Context

Pollen published two posts on 23 October 2026: contact odometry for the Microduck, read from its
feet and its IMU, and an onboard mapper, `mapd`, built from it and the head's 8x8 time-of-flight
sensor. robotd has sent the odometry on every state frame since before the release quackd reads,
and quackd passed it through raw and read none of it. `mapd` exists only on an upstream branch,
in no release. quackd had no notion of a position, a place or a map: `go_to` steers on what the
camera sees, `move` and the turns are timed, and the pilot could keep a place only as a sentence
in its memory.

A position means something only in the frame it was read in. Contact odometry is relative to
wherever the robot came up, drifts with every metre walked, and cannot follow a robot lifted off
its feet. A map's frame is its first stop, and "a map has no north". Two robots share neither.

## Decision

1. **quackd makes no map.** It reads where a robot is from what the robot offers, through one
   `SpatialSource` its transport carries for a connection (`spatial_of`), and makes nothing of its
   own from raw sensors. A map is the robot maker's work; what quackd adds is the names, the
   memory and, later, the walk to a name.
2. **Every reading says its kind and its frame.** `map` or `odometry`, and a frame id. An
   odometry frame lasts one connection, and begins again within it when the robot leaves its feet
   (picked up, fallen, riding a fall), when the robot's clock goes back, or on a step longer than
   its body can walk in the time between two readings. The robot's own clock, not message arrival,
   says whether a reading is fresh.
3. **A place is a pose the robot stood at.** `mark_place` takes no coordinates, and memory's
   `where` is read off the robot, never from a pilot's arguments.
4. **A place in another frame is orphaned.** It is kept and listed with the reason, and never
   walked to. No position is ever converted from one frame into another.
5. **`x`, `y` and `theta` stay the simulators' truth.** A real robot's reading goes in
   `extras["where"]` and a `where=` in the state line, because the flock reads `x`, `y` and
   `theta` as one arena every member shares, and a frame no other member is in would be read as
   that arena.
6. **Places live beside memory.** One JSONL file per robot, keyed as memory is, written only where
   memory is, and a note that knows where says how far it was saved from the places and from here.
7. **The place verbs are core and move nothing.** `map`, `mark_place` and `forget_place` need the
   `odometry` sensor, an adapter declares them in its static manifest and leaves them out at
   connect when its transport has no reading, and they run before a verdict and while a pause
   holds.

## Why not

- **A mapper of quackd's own, from the ToF and the odometry.** It would duplicate the robot's own,
  on a link with a frame's worth of latency, and nobody here could check it on a robot. Rok chose
  to read `mapd` when a release carries it and make nothing meanwhile.
- **Filling `x`, `y` and `theta` from odometry.** It wakes the flock's arena logic on a frame no
  other member shares.
- **Places as memory notes.** A sentence cannot be walked to, nor told apart from one marked
  before the robot was picked up.
- **Converting between frames.** There is nothing to convert by, and a guess sends a robot
  somewhere nobody chose.
- **One frame for a flock.** Robots that have just met share no frame of reference, which
  ADR-0020 settled; a site frame would be a decision of its own.

## Consequences

- A Microduck on any robotd quackd serves says where it is by odometry, which a pilot sees in
  every state line, and can name and list places. The simulators, MOSS and rosbridge say nothing
  yet.
- A place marked on odometry lasts one connection at most, and less when the duck is picked up.
  That is the honest life of an odometry frame, and a map that outlives the connection is what
  changes it.
- The memory file gains an optional `where` on a note; old files load unchanged.
