# Privacy: what a robot reveals, and to whom

> [!NOTE]
> Spec draft 0.1. quackd 0.23.0 is its first implementation, and [roadmap.md](roadmap.md) says what
> of it is built and what has run.

A passport is public by design: its job is to tell strangers about a robot. That makes it worth
being careful about what it tells them, because a robot usually belongs to a person, sits in their
home or their workplace, and goes where they go. This page says what each layer reveals, what to
leave out, and how to take part while revealing as little as possible.

## What each layer reveals

| Layer | Who can see it | What they learn |
|---|---|---|
| a QR sticker | anybody who can see the robot | where its passport is, and its short id |
| a DNS-SD beacon | anybody on the same local network | that a robot is here, its short id, its passport's digest |
| an MQTT beacon | anybody on the same broker | the same, and when it comes and goes |
| a ROS 2 beacon | anybody on the same ROS graph | its passport |
| the passport | anybody who can fetch it | whatever its owner chose to put in it |
| talk | the robot it talks to, and anybody who can read the channel | what it says |
| quackd's own records | whoever can read the files on that host | the robots it met, when, and what they said |

A beacon never carries an owner, a name of a person, or a place. What a passport carries beyond its
three required members is the owner's choice, member by member.

## What to put in `owner`

Something that reaches whoever looks after the robot, without saying who they are or where they
live:

- **Good:** `{"label": "Example lab", "contact": "https://example.org/robots/duck-01"}`. A page can
  say whatever its owner wants and change without the passport changing.
- **Good:** a role address, `robots@example.org`, or a form.
- **Not:** a home address, a personal phone number, a person's full name, unless that person
  really wants strangers to have them.
- **Also fine:** nothing at all. `owner` is optional, and a robot without one is still read. A
  flock may still require one before it will talk to a guest.

The point of `owner` is the point of a name tag on a dog's collar: a lost robot gets home, and a
robot doing something wrong has somebody to tell. A contact page does both without giving a
stranger more than that.

## Serial numbers

`maker.serial` identifies one physical robot for as long as it exists, across owners, networks and
years. It is there because some robots, in workplaces mostly, need it. A robot at home should leave
it out.

## Tracking

Anything stable that a robot announces can be used to follow it. A passport is fetched, so it is
seen only by those who ask for it. A beacon is different: it is announced, repeatedly, to whoever
is listening.

- **On a local network** a beacon reaches the people already on that network, the same people who
  could see the robot's network traffic anyway.
- **On a sticker** it reaches anybody who can see the robot, who could also simply look at it.
- **By radio** it would reach anybody within range, in the street, through walls, without being
  seen. That is why 0.1 has no Bluetooth binding ([discovery.md](discovery.md#bluetooth-le-later)).
  A short id that never changes, sent a few times a second, would let anybody with a phone follow a
  robot, and the person it belongs to. Bluetooth solved this for itself with addresses that rotate,
  and a passport beacon over radio waits until its ids can rotate too.

The `id` itself never changes, by design: it is what lets two robots recognise each other again. A
robot that should not be recognised again by strangers should not advertise at all
([below](#taking-part-without-being-discoverable)).

## What quackd keeps about robots it meets

On the host it runs on, and nowhere else:

- **Pins**, in `~/.quackd/known_robots.json`: the robots and owners a person chose to trust, with
  the key, the name the robot gave, and when the pin was made. A pin stays until a person removes
  it ([quackd.md](quackd.md)).
- **Run records**: when a run meets a robot, its transcript and log say so, with the messages that
  passed, the same way a run already records everything else it saw and did
  ([concepts/architecture.md](../concepts/architecture.md)). They stay as long as the run directory
  does.
- **Nothing is sent anywhere.** quackd fetches a passport and talks to a robot only when it was
  pointed at it, and it reports nothing about the robots it meets to anybody.

quackd never sends a camera frame over talk. A `share` carries a sentence about what a robot saw,
never the picture.

## Taking part without being discoverable

Every layer is optional, so a robot can reveal exactly as much as its owner wants:

| You want | Do this |
|---|---|
| people who can see the robot to know whose it is | a QR sticker pointing at a passport on a web page, and nothing else |
| robots that meet it to know what it is, without announcing it | serve the passport but advertise no beacon, and give its URL to whoever should have it |
| no owner on record | leave `owner` out |
| as little as possible | the three required members: a version, an `id` and a name |
| nothing at all | don't publish a passport: nothing in quackd announces a robot unless it is told to |

## A note on the law

In many places an owner's name or a personal contact is personal data, and a robot that publishes
it on a sticker or a network is publishing personal data. Nothing in this protocol requires any,
which is the main thing a protocol can do about it. This page is not legal advice, and an
organisation deploying robots in public spaces should ask somebody whose job that is.
