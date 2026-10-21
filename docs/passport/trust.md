# Trust: keys, signatures, stamps and tiers

> [!NOTE]
> Spec draft 0.1. quackd's implementation is being built for 0.23.0, and
> [roadmap.md](roadmap.md) says what of it is built and what has run.

A passport is a claim. Anybody can write one, about any robot, saying anything. This page is about
what turns a claim into something a reader can act on, and about how little that is: a signature
proves who said something, never that it is true, and trust in whoever said it comes from outside
the passport, from what a person decided.

## What a passport proves, and what it only claims

| A reader holds | It knows | It does not know |
|---|---|---|
| an unsigned passport | somebody wrote this | who, or whether any of it is true |
| a passport signed by its `id` | the holder of key K wrote this, and nobody changed it since | that K belongs to the robot in front of it, or that the figures are right |
| that, plus a stamp from an owner key it pinned | a key it already trusts vouches that K is its robot | that the robot behaves as its passport says |
| that, after a `hello` | the robot it is talking to holds K right now | which physical body that is ([below](#which-body-holds-the-key)) |

No row makes a mass, a speed or a `cannot` true. Those stay what they are in quackd's datasheet:
figures with a confidence and a source, from whoever wrote them. A reader labels every passport it
shows with which row it is on ([spec rule 11](spec.md#3-reading-a-passport)).

## The id is a key

A robot's `id` is a `did:key`: its Ed25519 public key, written as `did:key:z6Mk` and 44 more
characters. That makes the identifier self-certifying. Nobody issues it, nobody can take it away,
and nothing needs to be looked up to check a signature against it, which matters for two robots
meeting in a corridor with no internet between them.

The short id, the first 8 bytes of the SHA-256 of that key, is what beacons and stickers carry. It
is a hint for recognising a robot, and two beacons can claim the same one, so only a signature
decides.

A key is 32 bytes and a signature 64 ([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)). Ed25519
runs anywhere a robot does: Python's `cryptography` has it, so does every JOSE library, and C
libraries as small as Monocypher fit on a microcontroller.

### Where a robot's key lives

Wherever the robot's own software runs. When quackd puts a daemon on the robot, one of the bridges
under `bridge/`, the key belongs there. Otherwise it lives on the quackd host that fronts the robot,
in `~/.quackd/keys/`, and the passport then really means "the quackd host that drives this robot",
which is the honest thing for it to mean. [quackd.md](quackd.md) says which robots quackd keys where.

## Signing, with nothing to canonicalise

A signed passport is a compact JWS ([RFC 7515](https://www.rfc-editor.org/rfc/rfc7515)) whose
payload is the passport's exact bytes:

```
eyJhbGciOiJFZDI1NTE5Iiwia2lkIjoiZGlkOmtleTp6Nk1rZWhS...   the protected header, base64url
.eyJwYXNzcG9ydCI6IjAuMSIsImlkIjoiZGlkOmtleTp6Nk1rZWhS...  the passport's exact bytes, base64url
.V0johtXIxX-ix0awrTWNyg7vRYtV_MBcazEhU8pcpCrhBTYIGtvP...  the 64-byte Ed25519 signature, base64url
```

That is the start of each part of [vectors/microduck.jws](vectors/microduck.jws).

The header says `alg` `Ed25519`, `kid` the passport's own `id`, and `typ` `robot-passport+jws`.
A reader checks all three, checks the signature with the key the `id` holds, and then uses the
payload. It also accepts the older `alg` label `EdDSA`, which
[RFC 9864](https://www.rfc-editor.org/rfc/rfc9864) deprecates in favour of `Ed25519` and which
most libraries still write.

Signing the exact bytes is a deliberate choice. A signature embedded inside the JSON it signs
needs both sides to agree on one canonical way to write that JSON, which is where signature bugs
live. A2A signs its agent cards that way, over RFC 8785 canonical JSON. Here the bytes are signed
as they are and carried unchanged, in a cache, in a message, on a broker, so there is nothing to
agree on. That is also why a reader refuses a passport with a duplicate member: two parsers that
pick different duplicates would read two documents under one signature.

## Stamps

A signature says who wrote a passport. A **stamp** says who vouches for the key that wrote it. It
is a small JWS, about 480 characters, signed by another key, whose claims are:

| Claim | Meaning |
|---|---|
| `iss` | the `did:key` of whoever stamps |
| `sub` | the `did:key` of the robot stamped |
| `rel` | `owner` in 0.1: whoever holds `iss` owns this robot |
| `iat` | when it was issued |
| `exp` | when it expires: a year at most is recommended |

A stamp vouches for the key, never for the passport's contents. The robot can change its passport
every day without asking its owner again, and the stamp still holds, because what the owner said
is only "this key is my robot". That is also why a stamp counts only on a signed passport: on an
unsigned one, nothing shows the key wrote it, and anybody can copy an id and a stamp into a
document of their own.

An owner holds one owner key and stamps every robot they own with it, from a laptop, in a second:
`quackd passport stamp --robot NAME`, or `--did` for a robot whose key lives on the robot itself
([quackd.md](quackd.md#stamping-your-robots)). A reader that has pinned that owner key then trusts
every robot it stamps, which is the whole point: you decide once about a person, not once per
robot.

A stamp from a maker, "key K is a genuine Model M", would let a reader trust a robot's `maker` the
way Matter devices prove their vendor and product. That needs makers to hold keys and publish them,
so `maker` is reserved and 0.1 readers ignore it ([roadmap.md](roadmap.md)). Until then, `maker`
and `model` are what the robot says about itself, and a reader labels them so.

## Pinning: trust on first use

When a reader meets a signed robot for the first time it can **pin** the robot's key, or its
owner's, so that next time it knows it has met that key before. That is how SSH treats a server's
host key, and for the same reason: there is no authority everyone trusts, and the moment of first
contact, approved by a person, is the best anchor available.

A pin is only ever made by a person. quackd asks before it pins, and keeps pins in their own file,
`~/.quackd/known_robots.json`, so the registry's `robots.json` is untouched and an older quackd
still reads it. `quackd meet SOURCE --pin` pins a robot, `--pin-owner` its owner, and
`quackd passport known` and `forget` list and remove pins ([quackd.md](quackd.md#pinning)).

## The four tiers

What a robot met can make yours do depends on how your robot knows it. These are quackd's
defaults; the specification requires only that every action a peer starts is bounded, and leaves
the rest to each owner ([spec section 6.5](spec.md#65-ask-and-answer)).

| Tier | How a robot gets there | What it can make your robot do | What the pilot sees |
|---|---|---|---|
| stranger | it has any passport at all | `pause` for up to 5 s, and `identify` by light or sound only, both out of one global budget | its closed-vocabulary fields and a slugged name. Its free text goes to the log, for a person. |
| known | a person pinned its key | `pause` for up to 10 s and `identify` by any means, out of the same budget | its passport and its `say`, JSON-quoted |
| same owner | it carries a stamp from an owner key a person pinned, your own included | what its owner listed, run by the same handler | the same |
| flock | a person registered it with `quackd robot add` | what flock members do today, over the flock bus | the same as in any flock |

**The global budget.** New keys cost nothing, so a limit per peer stops nobody: a stranger can make
a thousand keys. Every peer therefore draws from one budget: at most one pause a minute and one
`identify` every ten seconds, across all of them together. An owner can turn stranger pauses off
altogether.

**A peer never stops your robot.** It can ask for a pause, which is bounded and ends on its own.
Your robot's own stop, the executor's, its kill switch, its deadman, stays above anything any peer
asks, always ([talk.md](talk.md#ask-and-answer)).

### Why lists, and not safety classes

quackd's verbs carry a safety class, `safe`, `confirm` or `dangerous`, and it would be tempting to
let a trusted peer ask for anything `safe`. It would also be wrong. In quackd `safe` includes motion:
every one of the Microduck's verbs is `safe`, `move`, `go_to`, `kick` and `grab` among them, because
the class says whether a person must confirm a verb, not whether it moves. And a flock never asks
a person at all: its standing answer is to allow everything or nothing. A tier that granted "safe
skills" would grant driving the robot. So each tier is a short explicit list, and the handler that
runs it is the only path from a peer to anything that moves.

### Two robots of the same owner

They recognise each other with no setup at all. Both carry a stamp from the same owner key, each
host has that key pinned because it is its own, so each robot puts the other in the same-owner tier
the moment they meet. A home with three robots from three makers, all stamped by one person, needs
nothing more.

## When things change

### A key cannot rotate

A `did:key` is its key, so a robot cannot change its key and keep its id. That is the price of an
id nobody issues. What survives a change of key is the owner's key: a robot with a new key and a
fresh stamp from the same owner is the same robot to everybody who pinned that owner.

### A key changes with no stamp

A reader that has pinned a robot and then meets a new key under the same name treats it the way
SSH treats a host key that changed: nothing is trusted automatically, the reader says plainly that
the key is different, and a person decides. It might be a new robot, a reinstalled one, or somebody
pretending.

### A robot's key is stolen

Whoever holds it can sign passports and messages as that robot. Stamps expire, so the thief keeps
the same-owner tier only until the stamp does, which is why a year at most is recommended and
shorter is better. A list of revoked keys, published at the owner's `contact` URL, is planned and
is not in 0.1 ([roadmap.md](roadmap.md)).

### A robot changes hands

The new owner gives it a new key and stamps that. The old owner's stamp is on a key nobody uses any
more, and it expires on its own.

### The owner key is lost

Then nobody can stamp a new robot under it, and every robot already stamped keeps its stamp only
until that stamp expires. The way back is a new owner key, new stamps for every robot, and asking
everyone who pinned the old key to pin the new one. So back the owner key up, somewhere that is not
the laptop it lives on, the way you would a password manager's recovery key.

## Which body holds the key

Everything above proves who holds a key. None of it proves which of the robots in front of you
that is. Two robots in one room, both answering on the network, and the signatures cannot tell you
which is which.

Two ways tie a key to a body:

- **Ask it to identify itself** and watch which one blinks, beeps or wiggles. That needs a camera
  or a person, and it is exactly what the courtesy skill is for.
- **Read the id off the body**: the short id from a QR sticker on it, checked against the passport.

Bluetooth signal strength is weak evidence, and nothing else is better. This is unsolved in
general, and the specification says so rather than pretending otherwise.
