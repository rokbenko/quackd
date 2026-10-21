# Why Robot Passport exists

> [!NOTE]
> Spec draft 0.1. quackd's implementation is being built for 0.23.0, and
> [roadmap.md](roadmap.md) says what of it is built and what has run.

## The problem

quackd lets robots from different makers work together as a flock. Each member's pilot knows its
peers from their manifests, and they divide a task by talking ([guides/flock.md](../guides/flock.md)).
But a flock is a list a person wrote: every member was registered by hand, and quackd builds each
peer's manifest locally from its own adapter. Discovery on a local network finds other quackd
instances, and deliberately feeds no flock ([ADR-0021](../adr/0021-lan-discovery-and-mqtt-bus.md)).

So a robot outside that list is invisible. When a flock's robot meets one it was never told about,
in a hallway, at a hackathon, at a front door, neither has any way to say who it is, who it belongs
to, what it can do, what it cannot, how a person stops it, or how to reach it. That is true when
the stranger runs quackd too, and more so when it runs anything else.

Robots from different makers share more spaces every year, and each speaks its maker's protocol to
its maker's software. What they lack is the smallest thing two strangers need: a way to introduce
themselves. Robot Passport is that, and nothing more.

## Five meetings it is for

- **Two makers' robots in one corridor.** A delivery cart on ROS 2 and a quackd Microduck meet in a
  hallway. The cart reads that the duck is 25 cm tall, slow and legged, and asks it to pause while
  it passes. The duck's owner decides whether it does.
- **A hackathon table.** Twenty robots from twenty teams on one network. Each team's laptop lists
  every robot on the table, what each can do and whose it is, without anybody registering anything.
- **A home with robots from different makers.** A vacuum, a robot arm and a toy biped, bought over
  three years, all stamped by the same owner. They recognise each other as family the moment they
  meet, and treat every visiting robot as a stranger.
- **A person with a phone.** A robot is wandering a shop floor. A person scans the sticker on its
  back and learns whose it is, how to reach them, and how to stop it.
- **A lost robot.** A robot ends up where it should not be. Its passport's `owner` gets it home the
  way a tag on a collar does, without its owner publishing their address.

## Principles

- **Reading needs nothing; asking needs a key.** Anybody can read a passport, and a robot with no
  software can have one. Proving who you are, and asking for anything, takes a signature.
- **A passport is a claim.** A signature proves who made it, never that it is true, and a reader
  labels every claim with where it came from. This is quackd's datasheet rule, a number without a
  source is a rumour, carried to strangers.
- **Small first.** Three required members. Every layer and every member above them is optional, so
  a robot joins at the level it can afford.
- **Never grant authority.** Nothing in a passport, and nothing a peer says, moves a robot except
  through a short, bounded rule its owner chose. A peer can ask; only the owner's policy decides.
- **Borrow, do not invent.** Every mechanism here already works somewhere else, and
  [prior-art.md](prior-art.md) says where.
- **Say what it cannot do.** Which body holds a key is unsolved, messages are not encrypted, a
  signed lie is still a lie, and the pages say so rather than imply otherwise.

## What it is not

- **Not remote control.** A peer can ask for a pause or for a robot to identify itself, and nothing
  else reaches its body. Driving a robot stays with its owner and its own software.
- **Not a coordinator.** It does not assign tasks or divide work. A quackd flock does that, among
  robots a person put in it; a passport only lets robots outside it be known.
- **Not a benchmark,** or a ranking of robots.
- **Not a replacement** for ROS 2, A2A, VDA 5050 or Matter. Robots keep speaking what they speak,
  and a passport tells a stranger which of those it can use to go further.
- **Not navigation.** It carries no maps and no coordinates.
- **Not encryption.** Messages are signed so they cannot be forged, and anybody on the channel can
  read them.

## Why not use something that exists?

The strongest case against a new format is a fair one, and it deserves an answer.

- **A2A's Agent Card** is signed, sits at a registered well-known path, can rotate its keys, and
  every agent framework reads it. But it requires eight members, one of them an endpoint that a
  robot with only a sticker cannot have, it describes software agents rather than bodies, and it has
  nowhere to say how heavy a robot is or how to stop it.
- **The W3C Web of Things Thing Description** is a W3C Recommendation with discovery already
  registered (`_wot._tcp`, `/.well-known/wot`). But it has no vocabulary for a body or for safety,
  and its required JSON-LD context and security definitions are a lot to ask of a robot that has
  nothing but a sticker.
- **VDA 5050's factsheet** and **the MassRobotics identity report** describe mobile robots well, and
  this borrows from both. But both are messages from a robot to its own operator's management
  software, over a broker or a socket that operator runs, and neither is meant for two robots that
  have never met.
- **ROS 2 discovery** tells a stranger node names and topic types, and nothing about a maker, an
  owner or how to stop the robot, and its security works only inside one certificate authority.
- **Robot Raconteur** has real node discovery and a device-information type. But its certificates
  come from one company, and it has nothing for an owner, a contact or safety.
- **Drone Remote ID** is the closest in spirit, a broadcast that tells strangers what a machine is
  and who operates it. It is built for aircraft, by regulation, and is a model to follow rather than
  a format to reuse.

So the passport stays small and new, but it is made to fit beside those: a robot that speaks A2A
can carry its passport inside its Agent Card, the discovery is shaped like WoT's, and translators
read VDA 5050, MassRobotics and URDF ([compatibility.md](compatibility.md)). A fourth format is a
real cost, and it is paid once, by a format small enough that a sticker can carry it.

## Why it lives in quackd for now

It starts inside quackd because quackd is its first implementation, its maintainer is one person,
and quackd ships no claim it has not run, so the code that proves the spec lives beside it. It is
written to leave: its module imports nothing else from quackd, and its spec uses none of quackd's
words. When a second implementation exists or another maker wants to adopt it, the spec, the
schemas and the vectors move to a repository of their own ([roadmap.md](roadmap.md#leaving-quackd)).
