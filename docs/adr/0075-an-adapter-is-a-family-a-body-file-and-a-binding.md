# ADR-0075: An adapter is a family, a body file and a binding

**Status:** accepted · **Date:** 2026-11-05 · Extends [ADR-0017](0017-robot-adapters-and-manifest.md) (a robot is an adapter that declares a manifest) and [ADR-0037](0037-adapters-are-their-own-packages.md) (an adapter is its own package) · Amends [ADR-0022](0022-per-adapter-upstream-refs.md) (a package may import another package's VERIFIED refs when it speaks that package's protocol) · Implemented in 0.33.0 by the changes that follow this record: the Huebot body in `quackd-microduck`, the arm's robot types in `quackd-lerobot`, and the tiers and described bodies of [ADR-0076](0076-every-backend-has-a-tier-and-bound-code-names-its-owner.md) and [ADR-0077](0077-a-body-quackd-only-describes-is-one-yaml-in-the-core.md) · Documented in [docs/adapters/writing-an-adapter.md](../adapters/writing-an-adapter.md#three-layers-four-tiers-one-rule)

## Context

Nine robots join quackd through nine adapters, each its own package, and fifteen more are
wanted. The question that prompted this record was whether fewer adapters could cover more
robots, for instance one adapter for everything that connects over USB. The answer is that
connectivity is the wrong axis: a USB adapter could not tell an SO-101 from an Open Duck,
and the knowledge a pilot needs never lived in the connection. It lives in the manifest and
the datasheet an adapter builds in `describe()`, which the pilot reads and the wire never sees.

What an adapter carries today was measured on `main` at `ad0bdb75` on 2026-11-04. Each
package holds three things at once: the body as data, the behaviour every body of its kind
needs, and a binding to one SDK or wire. Only the first differs per robot.

- The arm's package is 22,183 lines, and under 2,000 of them call LeRobot or know the SO-101.
  The generic layer touches 11 members of LeRobot's `Robot` and 7 of its `MotorsBus`, which
  the tests' `FakeArm` and the simulator's `SimFollower` already implement. The rest is the
  rest pose, the fold before a release, the hand-off, the travel clip, paced joint moves,
  stall detection, cameras, clocks, the policy server and the simulator, none of it the SO-101's.
- Huebot speaks the Microduck's `robotd` protocol, and its adapter is the Microduck's copied
  and renamed on 2026-10-20, on purpose, "since the two robots may diverge": 2,530 non-blank
  lines of which 1,591, 63 percent, are verbatim. Two fixes of 2026-11-03 that concerned every
  body had to land in both packages by hand, and what the Microduck gained since the copy,
  odometry, places, a map, `explore` and `find`, Huebot did not.
- Eight of the nine bodies have never met hardware. Their real backends are bound to upstream
  names read at pinned commits, held to the same standard as the arm's, and nobody can run them.
- The Reachy Mini came in 0.4 and went in 0.9.0, the one adapter removed so far.

Prior art agrees on one shape. Viam's component APIs are standard interfaces by kind, with a
module per piece of hardware behind them and a JSON block of attributes per device.
ros2_control separates hardware components from the controllers that drive them through a
description in the robot's URDF. LeRobot drives thirteen robot types from one `Robot` base over
shared motor buses, chosen by `--robot.type`. phosphobot drives seven arms through one
`BaseManipulator` over seven servo-level methods each robot implements.

## Decision

**An adapter is three layers, and the three are named.** Only the first is one per robot.

1. **A body is data.** The manifest's fields, the datasheet with a confidence and a source per
   figure, the verbs the body offers and the verbs it withholds with the reason each is
   withheld (`extras.not_offered`), its limits, its safety authority, its rest pose. Inside a
   package that drives bodies of one kind, each body is a record the package loads rather than
   code it runs: the Microduck's `Body`, of which Huebot is the second. Where nothing drives the
   body, it is one YAML in the core ([ADR-0077](0077-a-body-quackd-only-describes-is-one-yaml-in-the-core.md)).
   `describe()` loads a body; it does not build one.
2. **A family is the code every body of a kind needs**, shared by the bodies that declare it,
   and it is extracted only where two bodies already share it. In 0.33.0 that is the biped on
   a runtime, the Microduck and Huebot, and the servo arm, whose family is the arm's package
   made generic over LeRobot's own robot types: the binding under it is LeRobot's `Robot` and
   `MotorsBus` surface, so `so100_follower` is the same class as the SO-101's and
   `koch_follower` is a Dynamixel bus with the same methods. The arm's mock, which repeats the
   real backend's hand-off by hand, becomes the real backend over a fake follower in 0.34.0.
   Nothing is extracted for a robot nobody has: a family of one is a package as it is today.
3. **A binding is one of three routes and nothing else**: an SDK quackd imports, a wire quackd
   speaks, or a bridge quackd ships for the robot's own computer. Transport, the serial port,
   the socket, the radio, carries bytes and knows nothing about the body.

**A package over a family.** A robot that speaks a protocol another package already speaks is
a body record in that package plus a thin distribution of its own, which keeps its name, its
extra, its entry point and its catalogue row, so nothing a user types changes and no
`Deprecated` entry is owed. It depends on the family's package in the same version window the
core uses. Its `upstream_api.py` keeps the refs that are its own, the UNVERIFIED ones about what
its maker promised, and imports the protocol's VERIFIED refs from the family's module by name,
so a re-pin moves both by construction. ADR-0022's containment holds as it did: an UNVERIFIED
name is used only inside its own package.

## Consequences

- The next robot's cost depends on its route and not on its name. A robot in a covered
  ecosystem (ROS 2 over rosbridge, a LeRobot robot type) costs a body file and a hardware
  checklist run. A robot that speaks a protocol quackd already speaks costs a body record and a
  `not_offered` list. A robot with a network API of its own costs a wire binding in the nearest
  family. A robot with no API costs a bridge on the robot. None of them costs a package of
  2,000 to 9,000 lines again.
- The Huebot fold took its package from 3,093 lines to about 650, and the Microduck's transport adopted
  the hardening Huebot's copy had grown: a camera fetch bounded in size and time, a battery that
  is not an object read as no battery, a safety flag that is not a boolean read as unreadable.
  Every description either body could give hashed the same before and after, 456 of them, and
  `tests/golden/manifest_digests.json` holds that.
- No hardware run comes with this record. The SO-100 runs the SO-101's code through the same
  LeRobot class and has never run; the Koch is data until somebody names a date
  ([ADR-0076](0076-every-backend-has-a-tier-and-bound-code-names-its-owner.md)); the eight bound
  backends are as unproven as they were. A layer makes the second robot of a kind cheaper to
  write and to keep, not cheaper to prove.
- The README's Architecture section draws the four layers from the catalogue, so a tenth
  adapter appears in the picture by naming its family and route.
- [docs/adapters/writing-an-adapter.md](../adapters/writing-an-adapter.md) says what a body over
  a family looks like, and [CONTRIBUTING.md](../../CONTRIBUTING.md) what a contributor owes.
