# Robot Passport FAQ

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation, and [roadmap.md](roadmap.md) says what of it
> is built and what has run.

**Has it run on a Jetson?**
No, and not over a real Bluetooth radio either. It runs on the sim board, a laptop standing in for a
robot's computer, where two simulated robots meet, check each other and talk. The Bluetooth half is
tested against a fake BlueZ. [roadmap.md](roadmap.md#status) says what the first run on two Jetsons
has to write down.

**Why does it not run on my laptop any more?**
A passport that the laptop driving a robot hands out vouches for the laptop. The robot could not
introduce itself without it, and could not tell which robots were near it. Since 0.29.0 the passport
lives on the robot's own computer, and quackd on a laptop still drives robots the way it always has
([why.md](why.md#why-identity-belongs-to-the-body)).

**Why is there no passport for my SO-101, or my Microduck?**
An arm is fixed in place and will rarely meet another robot, and a toy-sized robot has no computer
on board that can run quackd. Robot Passport is for the robots that will meet strangers, humanoids
and quadrupeds, which nearly all carry a Jetson ([compatibility.md](compatibility.md)).

**Do I need quackd?**
Not to implement the protocol: it is a specification, with schemas and conformance vectors
([spec.md](spec.md)). quackd is its first implementation.

**Does my robot need the internet?**
No. Two robots find each other by Bluetooth and talk over the LAN or a Bluetooth link, and a
signature is checked against keys in the passport itself, with nothing to look up. The brain is the
exception: a cloud model needs the internet, and a model server on the robot or the LAN does not.

**Can a stranger drive my robot?**
No. A peer can ask for two courtesies, a pause of a few seconds or for your robot to identify itself,
out of one small budget shared by every peer, and your policy decides whether to grant either.
Nothing a peer says reaches anything else that moves, and no peer can keep your robot from stopping
([security.md](security.md)).

**Can a stranger talk my robot's brain into something?**
A stranger's words never reach the brain. It is shown the stranger's closed facts, under a name made
from its key, and what it said goes to the log for you. Free talk needs a pin
([security.md](security.md#prompt-injection)).

**Can somebody copy my robot's passport?**
They can copy it, and show it. They cannot change a byte of it without your owner key, and they
cannot finish a `hello` as your robot, because that means signing a fresh nonce with your robot's
key ([talk.md](talk.md#hello-proving-who-you-are-in-three-steps)).

**Can people track my robot with it?**
The beacon's id is random and changes every 15 minutes, and the wizard offers to switch on BlueZ's
address privacy so the radio's own address changes too. Both are designed in and neither has been
tested on a radio. On a LAN, the record a robot announces ties its fixed short id to its current
beacon id ([privacy.md](privacy.md#tracking)).

**What if my robot is stolen?**
The thief holds its robot key and can talk as the robot until its passport expires, and holds your
owner key sealed by your passphrase, which they can try to guess offline. A long passphrase holds the
second; a shorter `--days` limits the first ([trust.md](trust.md#a-robot-is-stolen)).

**What if I forget the passphrase?**
Then the sealed owner key is lost to you as well: nobody can open it, and there is no way back in.
Make a new owner key, sign every robot again, and ask whoever pinned the old one to pin the new
([trust.md](trust.md#the-owner-key-is-lost)).

**Is it encrypted?**
No. Messages are signed, so nobody can forge them, and anybody in range can read them. Say nothing
secret over talk ([security.md](security.md#what-nothing-here-stops)).

**Is "Robot Passport" the same as the Robot Context Protocol?**
No. The protocol started under that name and was renamed because three projects already use it
([naming.md](naming.md)).

**Who governs it?**
For now, quackd's maintainer, in quackd's repository, under quackd's licence, Apache-2.0. It is
written to move to a repository of its own once a second implementation exists or another maker
wants to adopt it ([roadmap.md](roadmap.md#leaving-quackd)).

**How do I put a passport on my robot?**
Three commands on its Jetson, or one on a laptop to try it: [quickstart.md](quickstart.md).
