# Robot Passport FAQ

> [!NOTE]
> Spec draft 0.1. quackd 0.23.0 is its first implementation, and [roadmap.md](roadmap.md) says what
> of it is built and what has run.

**Do I need quackd?**
No. A passport is a JSON file and a specification, and any robot can publish one with no quackd
anywhere near it, even a robot with no software at all ([compatibility.md](compatibility.md)).
quackd is the first implementation, and the one that reads, verifies and talks for you.

**Does my robot need the internet?**
No. Two robots on the same local network find each other over mDNS, and a signature is checked
against the key in the passport itself, with nothing to look up. A sticker needs no network on the
robot at all.

**Can a stranger drive my robot?**
No. A peer can ask for two courtesies, a pause of a few seconds or for your robot to identify itself,
out of one small budget shared by every peer, and your policy decides whether to grant either.
Nothing a peer says reaches anything else that moves, and no peer can keep your robot from
stopping ([security.md](security.md)).

**Can a stranger stop my robot?**
Not stop, only pause: a few seconds at most, and then it carries on with what it was doing. You can
refuse stranger pauses altogether ([trust.md](trust.md#the-four-tiers)).

**Can somebody copy my robot's passport?**
They can copy it, and serve it, and a reader will read it. They cannot sign a new one without your
robot's private key, and they cannot complete a `hello` as your robot, because that means signing
fresh nonces with that key ([talk.md](talk.md#hello-proving-who-you-are-in-three-steps)).

**Can I keep my name off it?**
Yes. `owner` is optional, and when you use it, a page or a role address does the job without your
name ([privacy.md](privacy.md#what-to-put-in-owner)).

**Can people track my robot with it?**
A beacon on your local network reaches only people on that network, and a sticker only people who
can see the robot anyway. Version 0.1 has no Bluetooth binding precisely because a radio beacon with
a fixed id would let anybody nearby follow it ([privacy.md](privacy.md#tracking)).

**Why JSON and not something smaller?**
Because every language reads it, people can read it, and a passport can be under 100 bytes anyway.
A binary profile (CBOR, with COSE signatures) is planned for microcontrollers that need it.

**Why not just use A2A's Agent Card?**
An Agent Card needs an endpoint a robot with only a sticker cannot have, it describes software
rather than a body, and it has nowhere to say how heavy a robot is or how to stop it. A robot that
speaks A2A can carry its passport inside its Agent Card ([why.md](why.md#why-not-use-something-that-exists)).

**Why not ROS 2 discovery?**
It tells a stranger node and topic names, and nothing about a maker, an owner, a body or how to
stop it. A ROS 2 robot publishes its passport on a latched topic and needs nothing new
([discovery.md](discovery.md#ros-2)).

**Is it encrypted?**
No. Messages are signed, so nobody can forge them, and anybody on the same channel can read them.
Say nothing secret over talk ([security.md](security.md#what-nothing-here-stops)).

**What if my robot's key is stolen?**
Give it a new key and a new stamp from your owner key. The thief keeps the same-owner tier only
until the old stamp expires ([trust.md](trust.md#a-robots-key-is-stolen)).

**What if I lose my owner key?**
Make a new one, stamp your robots again, and ask the people who pinned the old one to pin the new
one. Back the owner key up so that this never happens ([trust.md](trust.md#the-owner-key-is-lost)).

**Is "Robot Passport" the same as the Robot Context Protocol?**
No. The protocol started under that name and was renamed because three projects already use it
([naming.md](naming.md)).

**Who governs it?**
For now, quackd's maintainer, in quackd's repository, under quackd's licence, Apache-2.0. It is
written to move to a repository of its own once a second implementation exists or another maker
wants to adopt it ([roadmap.md](roadmap.md#leaving-quackd)).

**What runs today?**
[roadmap.md](roadmap.md) keeps the honest answer, phase by phase: what is specified, what is built,
what has run on fakes, between two machines, and on a robot.

**How do I add my robot?**
Write a passport and put it where others can read it: [quickstart.md](quickstart.md) does it in five
minutes. If your robot runs quackd, quackd builds one for it ([quackd.md](quackd.md)).
