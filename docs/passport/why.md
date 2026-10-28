# Why Robot Passport exists

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation, and [roadmap.md](roadmap.md) says what of it
> is built and what has run.

## The problem

Two robots that meet today cannot work together. A humanoid stacking shelves and a visitor's robot
in the same aisle, a courier's robot at a front door and the robot that lives there: each speaks its
maker's protocol to its maker's software, and neither has any way to say who it is, who it belongs
to, what it can do, what it cannot, or how a person stops it.

quackd lets robots from different makers work together as a flock, but a flock is a list a person
wrote: every member was registered by hand, and each peer's manifest is built from its own adapter
([guides/flock.md](../guides/flock.md)). A robot that nobody put on the list is invisible, and the
robots that will meet strangers, humanoids and quadrupeds walking where other robots walk, meet
nothing but robots nobody put on a list.

What they lack is the smallest thing two strangers need: a way to introduce themselves, and to know
whether to believe the introduction. Robot Passport is that, and nothing more.

## Meetings it is for

- **Two makers' humanoids in one aisle.** Each reads that the other is a legged humanoid of about
  35 kg that cannot climb stairs, and how a person stops it. Neither brain hears a word the other
  says until a person decides it should.
- **A courier at a door.** The household pinned the courier company's owner key once, so its robot
  knows every robot that company signs. The two brains talk, and agree where the parcel goes.
- **A quadruped meeting a stranger.** It learns what the stranger is and grants it, at most, a pause
  of a few seconds, out of a budget every stranger shares.
- **One owner, several robots.** Robots signed by the same owner key recognise each other the moment
  they meet, with no setup at all.
- **A lost robot.** A robot ends up where it should not be. The first robot that meets it learns
  whose it is and how to reach them, the way a tag on a collar works, without its owner publishing
  their address.

## Why identity belongs to the body

Until 0.29.0 a robot's passport lived on the laptop that drove it. The laptop held the robot's key,
served its passport and talked for it, so a robot without its laptop could not introduce itself, and
its passport really meant "the computer that drives this robot". A robot that walks out of the room
leaves that laptop behind.

So the passport moved onto the robot. Its key lives on its own computer, its owner signs what its
passport says, and it introduces itself wherever it goes. The robots that meet strangers can carry
that: humanoids and quadrupeds nearly all have an NVIDIA Jetson on board that runs quackd. An arm is
fixed in place and will rarely meet another robot, and a toy-sized robot has no computer on board
that can run quackd, so neither carries one ([compatibility.md](compatibility.md)).

## Principles

- **The owner signs.** What a passport says, how to stop the robot included, is what its owner
  signed, byte for byte. A robot cannot change its own story.
- **Near before talk.** Robots meet because the radio says they are close, not because they found
  each other's address on a network.
- **Checked before a word.** Both robots check the other's passport and key before either says
  anything.
- **A stranger's words never reach a model.** A brain hears a robot nobody pinned as the closed
  facts of its passport, and free talk needs a person's pin.
- **A passport is a claim.** A signature proves who made it, never that it is true, and a reader
  labels every claim with where it came from. This is quackd's datasheet rule, a number without a
  source is a rumour, carried to strangers.
- **Never grant authority.** Nothing in a passport, and nothing a peer says, moves a robot except
  through a short, bounded rule its owner chose. A peer can ask; only the owner's policy decides.
- **Borrow, do not invent.** Every mechanism here already works somewhere else, and
  [prior-art.md](prior-art.md) says where.
- **Say what it cannot do.** Which body holds a key is unsolved, signal strength is a hint, messages
  are not encrypted, a signed lie is still a lie, and nothing of it has run on a Jetson yet. The
  pages say so rather than imply otherwise.

## What it is not

- **Not remote control.** A peer can ask for a pause or for a robot to identify itself, and nothing
  else reaches its body. Driving a robot stays with its owner and its own software.
- **Not a coordinator.** It does not assign tasks or divide work. A quackd flock does that, among
  robots a person put in it; a passport only lets robots outside it be known.
- **Not a benchmark,** or a ranking of robots.
- **Not a replacement** for ROS 2, A2A, VDA 5050 or Matter. Robots keep speaking what they speak.
- **Not navigation.** It carries no maps and no coordinates.
- **Not encryption.** Messages are signed so they cannot be forged, and anybody in range can read
  them.

## Why not use something that exists?

The strongest case against a new format is a fair one, and it deserves an answer.

- **A2A's Agent Card** is signed, sits at a registered well-known path, can rotate its keys, and
  every agent framework reads it. But it requires eight members, it describes software agents rather
  than bodies, and it has nowhere to say how heavy a robot is or how to stop it.
- **The W3C Web of Things Thing Description** is a W3C Recommendation with discovery already
  registered (`_wot._tcp`, `/.well-known/wot`). But it has no vocabulary for a body or for safety,
  and its required JSON-LD context and security definitions are a lot to carry for an introduction.
- **VDA 5050's factsheet** and **the MassRobotics identity report** describe mobile robots well, and
  this borrows from both. But both are messages from a robot to its own operator's management
  software, over a broker or a socket that operator runs, and neither is meant for two robots that
  have never met.
- **ROS 2 discovery** tells a stranger node names and topic types, and nothing about a maker, an
  owner or how to stop the robot, or whether it is near. Its security works only inside one
  certificate authority.
- **Robot Raconteur** has real node discovery and a device-information type. But its certificates
  come from one company, and it has nothing for an owner, a contact or safety.
- **Drone Remote ID** is the closest in spirit: a radio broadcast that tells strangers nearby what a
  machine is and who operates it. It is built for aircraft, by regulation, and is a model to follow
  rather than a format to reuse.

So the passport stays small and new, and borrows from each: a document at a well-known path and
extensions marked `required` from A2A, an introduction that only points from WoT, a description kept
apart from live state from VDA 5050, and a short broadcast to strangers from Remote ID
([prior-art.md](prior-art.md)). A new format is a real cost, and it is paid once, by a format small
enough to fit in a `hello`.

## Why it lives in quackd for now

It starts inside quackd because quackd is its first implementation, its maintainer is one person,
and quackd ships no claim it has not run, so the code that proves the spec lives beside it. It is
written to leave: its module imports nothing else from quackd, and its spec uses none of quackd's
words. When a second implementation exists or another maker wants to adopt it, the spec, the
schemas and the vectors move to a repository of their own ([roadmap.md](roadmap.md#leaving-quackd)).
