# Robot Passport

> [!NOTE]
> Spec 1.0. Since quackd 0.29.0 a passport lives on the robot it introduces: `quackd onboard`
> runs on the robot's own computer, an NVIDIA Jetson first, and the owner's key signs what it says.
> Nothing in this folder has run on a Jetson or over a real Bluetooth radio.
> What has run is the sim board, a laptop standing in for a robot's computer, and
> [roadmap.md](roadmap.md) says exactly how far that goes.

Two robots that meet today cannot work together. Neither knows what the other is, whose it is,
what it cannot do or how a person stops it, and a robot from another maker speaks another maker's
protocol. Robot Passport is a small open protocol for the moment they meet. Each robot carries a
passport its owner signed. When two of them come close, each reads the other's passport, checks
the owner's signature and that the robot in front of it holds its own key, and only then do their
LLM brains talk: over the LAN when they share one, and over a Bluetooth link when they do not.

```
BEACON    "a robot is here", by Bluetooth LE            a random 8-byte id, a new one every 15
   │      close enough is judged by signal strength     minutes, and three flags: never a name
   │
PASSPORT  who I am, whose I am, what I can and          one small JSON document, signed by its
   │      cannot do, how a person stops me              owner's key
   │
TALK      hello · say · bye · ask · answer · share      small signed messages over the LAN or a
          once both robots have checked each other      Bluetooth link, never obeyed blindly
```

**Near first, checked second, trusted by a person.** Two robots say hello only once the radio says
they are close. Neither says a word before both passports and both keys check out. And a stranger's
words never reach a brain: a robot nobody pinned is shown to it as the closed facts of its passport,
and a person's pin is what lets its sentences through.

## Which robots carry one

The robots that will meet strangers: humanoids and quadrupeds that walk the same aisles, doorways
and pavements, and nearly all of them carry an NVIDIA Jetson that can run quackd. An arm carries no
passport, because it is fixed in place and will rarely meet another robot. Neither does a toy-sized
robot, because it has no computer on board that can run quackd. [compatibility.md](compatibility.md)
says which boards `quackd onboard` runs on, and how another board joins.

## Read in this order

| Page | What it covers |
|---|---|
| [why.md](why.md) | the problem, the meetings it is for, the principles, what it is not, and why not something that exists |
| [how-it-works.md](how-it-works.md) | one meeting step by step, what each side learns, a passport's life, and what happens when something fails |
| [quickstart.md](quickstart.md) | the three commands on a robot's own computer, and two simulated robots on a laptop |
| [passport.md](passport.md) | the document, member by member, with sizes and what it leaves out |
| [discovery.md](discovery.md) | the Bluetooth beacon and its rotating id, signal strength, the LAN, and the fetch rules |
| [trust.md](trust.md) | the robot key and the owner key, the owner's signature, pinning, the three tiers, and lost or stolen keys |
| [talk.md](talk.md) | the six messages, the three-step `hello`, the two channels, and what quackd does with an `ask` |
| [security.md](security.md) | the threat model: every threat, what stops it, and what nothing here stops |
| [privacy.md](privacy.md) | what each layer reveals, tracking by radio, and what quackd keeps on the robot |
| [compatibility.md](compatibility.md) | which robots carry a passport, the boards it runs on, and what a reader of its own needs |
| [quackd.md](quackd.md) | Robot Passport in quackd: `quackd onboard`, robot.yaml, the service, meetings, pins and the demo |
| [spec.md](spec.md) | the normative specification, version 1.0 |
| [schema/](schema/passport.schema.json) | the JSON Schemas: [passport](schema/passport.schema.json) and [message](schema/message.schema.json) |
| [vectors/](vectors/README.md) | the conformance vectors, each with the result a reader must reach |
| [naming.md](naming.md) | what the name means, why it is not the Robot Context Protocol, and every identifier |
| [prior-art.md](prior-art.md) | every system it was measured against, what it borrowed and what it avoided |
| [glossary.md](glossary.md) | every term, in a sentence or two |
| [faq.md](faq.md) | the questions people ask |
| [roadmap.md](roadmap.md) | the status, the phases, the decisions taken, and leaving quackd |

## Sizes at a glance

| What | Size |
|---|---|
| the Bluetooth advert, flags included | 31 bytes, 10 of them the beacon's own |
| the minimal passport in passport.md | <!-- capture: wc -c docs/passport/vectors/minimal.json, after C3 --> |
| a mock Unitree G1 EDU's full passport | <!-- capture: wc -c docs/passport/vectors/humanoid.json, after C3 --> |
| that, signed by its owner, as a `.jws` | <!-- capture: wc -c docs/passport/vectors/humanoid.jws, after C3 --> |
| the most a reader must accept | 8,192 bytes |
| the most a reader reads of one off the network, signed | 12,288 bytes |
| a talk message, at most | 4,096 bytes |
| a `hello`, which carries a passport, at most | 16,384 bytes |

The decision record behind all of it is
[ADR-0057](../adr/0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md), amended in 0.29.0
by ADR-0073: a robot carries its own passport, and its owner signs it.
