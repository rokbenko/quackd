# ADR-0071: A duck explores what its map has not seen, and stops so the map can look

**Status:** accepted · **Date:** 2026-10-27 · Builds on [ADR-0068](0068-a-place-belongs-to-the-frame-it-was-marked-in-and-quackd-reads-a-map-rather-than-making-one.md) (quackd reads a map rather than making one), [ADR-0054](0054-a-reflex-may-look-through-a-decision-llm.md) (a reflex with named ways out) and [ADR-0067](0067-a-stop-ends-the-verb-beside-it-and-a-newer-command-ends-the-one-it-finds-moving.md) (a stop ends the verb beside it) · Implemented in `quackd/space/frontier.py`, `quackd/verbs/explore.py`, `quackd/verbs/navigate.py` and the Microduck's `verbs.py` and manifest · Documented in [docs/adapters/microduck/README.md](../adapters/microduck/README.md#explore-walking-to-what-its-map-has-not-seen)

## Context

`mapd`, the Microduck's onboard mapper, and the simulators' stand-in for it take a keyframe only
when the duck stands still, and see a room a stop at a time. ADR-0068 gave quackd a reading of
where the duck is, places, and a walk to a place on the map. What the map has not seen, nothing
walked to: a pilot could only `move` and hope, and a map grew where a person happened to drive
the duck. Pollen's post calls exploring a possibility, and its own design names planning as
unowned.

## Decision

1. **Exploring is a verb, `explore(minutes)`, on the same engine a walk to a place uses.** It finds
   the floor the map has seen beside floor it has not, walks to the nearest stretch of it by the
   way the map knows, stops, looks, and goes again. It calls the walk as a function, as
   `search_scan` turns by `_turn` rather than calling `move`, so a task that allows `explore`
   needs nothing else in its allowlist and no confirm is asked inside it.
2. **It goes where the map can look, and looks only where that teaches it something.** A stretch is
   walked to only when the opening it looks into is as wide as the body, nearest by the walk the
   map knows, unseen floor never crossed to get there. The first stop looks all the way round, in
   views a step apart by the rule `search_scan` turns a camera by, and every later stop only the
   ways a look would see as many cells nobody has seen as the body is wide.
3. **A look is the map's keyframe.** Each waits until the map has taken one, for the map's own
   settle at most. One the head turned for first waits for a keyframe more than the map had, for
   the head's settle and the map's at most: a head that says where it got to has not moved when
   it is first read. The head turns for a look where the map takes its keyframe from the head,
   and the body where it does not. There, a body turn that lands off its heading by more than the
   views overlap is finished by the head, so the views meet. Where only the body turns, a view
   lands within the walk's own turning tolerance of its heading, which can be wider than the
   overlap.
4. **What it cannot see past, it gives up.** A stretch it walked to and looked at that is still
   unseen is not walked to again in that map for as long as the robot stays connected. A spot it
   could not get to, lost, stalled or refused on the way, is walked at once more after the rest
   has been, and then the floor within a body's width of it is left for the rest of the call, not
   the connection: a person in a doorway moves, a map not read once is read the next time, and
   behind a pane the map sees through and the body walks into, each spot is walked at twice a
   call at most. The rest of the stretch it was part of is still walked to.
5. **What it walks, turns and waits by is a sensor's, a clamp's or upstream's.** The view is
   the map's own, a walk never runs further between stops than a look reaches, and a lost map is
   found again as the map says it finds itself: a keyframe standing, then a view's turn at a time
   with a keyframe each, one whole turn at most.
6. **Every way it ends is named, and stops the body.** `done` and `minutes` succeed; a stop, the
   run's budget, a map it cannot find itself in, a twist refused and a body it may move no more
   (`held`, `fallen`, `sitting`, `blind`) fail, in words of their own. The run's rules are read at
   every stop.
7. **`confirm`, not `dangerous`.** robotd's deadman zeroes a twist half a second old by default,
   so a lost link is not a runaway, and every walk inside it is the walk ADR-0068's
   `go_to_place` already is. But it walks into rooms nobody has looked at, and a sensor that faces
   forward cannot see a step down, so a person is asked.
8. **Offered only where the reading keeps a map and the body's size is known.** A way round a
   room keeps the body's radius from everything solid. A duck on odometry alone, and a real duck
   beside `mapd`, whose radius nothing publishes, are not offered it, and say why beside the body,
   where the pilot judges a task, and in a run's refusal.

## Why not

- **Mapping while walking.** `mapd` maps nothing while the duck moves, and neither does its
  stand-in.
- **A random walk.** It maps a room in no time anyone can bound and walks the same floor twice.
- **Scoring views by information gain.** A weight nobody measured, where the rule above needs
  none: a view sees enough unseen floor or it is not taken.
- **A decision LLM's frame at every look.** A look is a keyframe, and nothing about where to go
  needs a model: a frame at every stop would add a model's round trip to each, for nothing the
  map does not already say.
- **Waiting out a carry.** A duck carried and put down knows nothing until two stops agree, and
  standing still never gives it a second stop.
- **`dangerous`.** It would run only from a terminal, and a duck exploring is what a pilot over
  MCP is for, the deadman under it.

## Consequences

- A simulated duck in a room maps it without anybody driving it, and marks `start` where it
  began, so `go_to_place start` brings it back. A real duck does not explore until somebody cites
  its body radius, `mapd` or not.
- On a real duck, once it explores, the body turns for every view: whether `mapd` takes a
  keyframe from where the head looks is not something the branch quackd read says, and the
  hardware checklist asks.
- A pilot over MCP may give `explore` more minutes than its client waits for one tool call; `stop`
  from beside it ends it at once.
