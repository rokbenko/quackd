# Robot Passport

> [!NOTE]
> Spec draft 0.1. quackd 0.23.0 is its first implementation, and [roadmap.md](roadmap.md) says what
> of it is built and what has run.

A quackd flock is a list of robots a person put together, and each knows the others from their
manifests. Robot Passport is for every robot that is not on that list: a small open protocol for
two robots that have never met, from any maker and on any software, to say who they are, who
vouches for them, what they can and cannot do, how a person stops them, and how to reach them.

```
BEACON    "I'm here, and this is where my passport is"     a QR sticker, mDNS, MQTT, a ROS 2 topic
   │
PASSPORT  who I am, who vouches for me, what I can and     one small JSON document, optionally signed,
          cannot do, how to stop me, how to reach me       with stamps from its owner
   │
TALK      hello · say · bye · ask · answer · share         small signed messages, never obeyed blindly
```

**Reading needs nothing; asking needs a key.** A passport can be under 100 bytes, a robot with no
software can carry one on a sticker, and nothing a peer says moves a robot except through a short,
bounded rule its owner chose.

## Read in this order

| Page | What it covers |
|---|---|
| [why.md](why.md) | the problem, five meetings it is for, the principles, what it is not, and why not something that exists |
| [how-it-works.md](how-it-works.md) | one meeting step by step, what each side learns, a passport's life, and what happens when something fails |
| [quickstart.md](quickstart.md) | a passport for any robot in five minutes, and for a robot quackd drives |
| [passport.md](passport.md) | the document, member by member, with sizes and what it leaves out |
| [discovery.md](discovery.md) | the beacon, and every way to carry it: the well-known URL, QR, DNS-SD, MQTT, ROS 2, and Bluetooth later |
| [trust.md](trust.md) | keys, signatures, stamps, pinning, the four tiers, and what to do when a key is lost or stolen |
| [talk.md](talk.md) | the six messages, the three-step `hello`, courtesies, and what quackd does with an `ask` |
| [security.md](security.md) | the threat model: every threat, what stops it, and what nothing here stops |
| [privacy.md](privacy.md) | what each layer reveals, what to leave out, tracking, and taking part without being discoverable |
| [compatibility.md](compatibility.md) | the ladder from a sticker to a talking robot, recipes, translators, and A2A |
| [quackd.md](quackd.md) | Robot Passport in quackd: commands, files, what a pilot sees, safety |
| [spec.md](spec.md) | the normative specification, version 0.1 |
| [schema/](schema/passport.schema.json) | the JSON Schemas: [passport](schema/passport.schema.json), [index](schema/index.schema.json), [stamp](schema/stamp.schema.json), [message](schema/message.schema.json) |
| [vectors/](vectors/README.md) | the conformance vectors, each with the result a reader must reach |
| [naming.md](naming.md) | what the name means, why it is not the Robot Context Protocol, and every identifier |
| [prior-art.md](prior-art.md) | every system it was measured against, what it borrowed and what it avoided |
| [glossary.md](glossary.md) | every term, in a sentence or two |
| [faq.md](faq.md) | the questions people ask |
| [roadmap.md](roadmap.md) | the status, the phases, the decisions taken, and leaving quackd |

## Sizes at a glance

| What | Size |
|---|---|
| the minimal passport in passport.md | 99 bytes |
| a Microduck's full passport | 1,218 bytes |
| that, signed, with an owner stamp, as a `.jws` | 2,518 characters |
| the most a reader must accept | 8,192 bytes |
| the most a reader reads of one off the network, signed | 12,288 bytes |
| a talk message, at most | 4,096 bytes |
| a `hello`, which carries a passport, at most | 16,384 bytes |
| a QR code for a passport URL | 37×37 modules |

The decision record behind all of it is
[ADR-0057](../adr/0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md).
