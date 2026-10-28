# Trust: keys, the owner's signature and tiers

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation, and [roadmap.md](roadmap.md) says what of
> it is built and what has run.

A passport is a claim. Anybody can write one, about any robot, saying anything. This page is about
what turns a claim into something a robot can act on, and about how little that is: a signature
proves who said something, never that it is true, and trust in whoever said it comes from outside
the passport, from what a person decided.

## What a passport proves, and what it only claims

| A robot holds | It knows | It does not know |
|---|---|---|
| a passport's JSON, unsigned | somebody wrote this | who, or whether any of it is true |
| a passport signed by the key its `owner.id` names | the holder of owner key O wrote this about robot key R, and nobody changed it since | that O is who its label says, or that the figures are right |
| that, with O or R pinned by a person | a key it already trusts wrote this | that the robot behaves as its passport says |
| that, after a `hello` | the robot it is talking to holds R right now, and the other side knows the same of it | which physical body that is ([below](#which-body-holds-the-key)) |

No row makes a mass, a speed or a `cannot` true. Those stay what they are in quackd's datasheet:
figures with a confidence and a source, from whoever wrote them. A reader labels every passport it
shows with which row it is on ([spec rule 11](spec.md#3-reading-a-passport)), and talk never
accepts the first: an unsigned passport can be read and shown, and never talked to.

## The id is a key

A robot's `id` is a `did:key`: its Ed25519 public key, written as `did:key:z6Mk` and 44 more
characters. That makes the identifier self-certifying. Nobody issues it, nobody can take it away,
and nothing needs to be looked up to check a signature against it, which matters for two robots
meeting in a corridor with no internet between them. An owner is a key in the same way, and
`owner.id` is that key.

The short id, the first 8 bytes of the SHA-256 of a key, as 16 hex characters, is how a person
names a robot or an owner they met, and what a robot's record on the LAN carries. It is a hint for
recognising one, and two records can claim the same, so only a signature decides. The Bluetooth
beacon carries no short id at all, but a random id that changes every 15 minutes
([discovery.md](discovery.md#bluetooth-le-later)).

A key is 32 bytes and a signature 64 ([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)). Ed25519
runs anywhere a robot does: Python's `cryptography` has it, so does every JOSE library, and C
libraries as small as Monocypher fit on a microcontroller.

## Two keys on every robot

| Key | Signs | Lives | Kept as |
|---|---|---|---|
| the robot key | every talk message the robot sends, so a robot it meets knows it holds the key the passport names | on the robot, in `keys/robot.json` | plain, readable by the robot's own user alone, because the service starts by itself after a reboot and has nobody to ask |
| the owner key | the passport | on the robot too, in `keys/owner.json` | sealed with a passphrase, and opened only for the moment of signing |

The passport's `id` is the robot key and its `owner.id` is the owner key, and the two must differ:
a passport whose owner key is its robot key has nothing but the robot vouching for it, and a reader
refuses it.

## The owner signs the passport

<a id="stamps"></a>

The passport's JWS is signed by the owner key, and the JWS header's `kid` is `owner.id`. So the
owner vouches for what the passport says, member by member: the body, what it cannot do, and how a
person stops it. Change one byte after signing and every reader refuses it.

Robot Passport 0.1 did it the other way round. The robot's key signed the passport, and an owner
**stamped** the robot's key: a small JWS saying "this key is my robot". A stamp vouched for a key
and never for what the passport said, so whoever held the robot's key could rewrite the passport,
how to stop the robot included, and keep the stamp. 1.0 drops stamps altogether. Whoever holds only
the robot key cannot change a word of the passport now, and what it says is what its owner signed.

A reader refuses a passport under [rule 4.4](spec.md#44-signed-passports) when:

- it is signed by its robot's own key, the 0.1 shape: a 1.x passport is signed by its owner;
- it is signed by any key other than the one its `owner.id` names;
- its owner key is its robot key;

and under [rule 4.5](spec.md#4-identity-signatures-and-fingerprints) when the reader's clock is
outside its `issued` and `expires`, with five minutes of grace for a clock that runs a little ahead.
quackd signs for 365 days unless `--days` says otherwise, and never for more than the 400 a reader
accepts.

## Signing, with nothing to canonicalise

A signed passport is a compact JWS ([RFC 7515](https://www.rfc-editor.org/rfc/rfc7515)) whose
payload is the passport's exact bytes: three base64url parts, a protected header, the payload and
a 64-byte Ed25519 signature, joined by dots. Every `.jws` in [vectors/](vectors/README.md) is one.

The header says `alg` `Ed25519`, `kid` the owner's `did:key`, and `typ` `robot-passport+jws`. A
reader checks all three, checks the signature with the key `kid` holds, and then uses the payload.
It also accepts the older `alg` label `EdDSA`, which [RFC 9864](https://www.rfc-editor.org/rfc/rfc9864)
deprecates in favour of `Ed25519` and which most libraries still write.

Signing the exact bytes is a deliberate choice. A signature embedded inside the JSON it signs needs
both sides to agree on one canonical way to write that JSON, which is where signature bugs live.
A2A signs its agent cards that way, over RFC 8785 canonical JSON. Here the bytes are signed as they
are and carried unchanged, in a file, in a cache, inside a `hello`, so there is nothing to agree
on. That is also why a reader refuses a passport with a duplicate member: two parsers that pick
different duplicates would read two documents under one signature.

## The owner key, sealed on the robot

The owner key lives on the robot because that is where the passport is signed: the wizard signs it
there, and `quackd onboard sign` signs it again after an edit. It is sealed. The file holds the key
encrypted with AES-256-GCM, under a key scrypt derives from your passphrase, with the owner's
`did:key` bound in, so a sealed key moved under another identity does not open. scrypt is slow and
hungry for memory on purpose, so every guess at a passphrase costs time and memory.

- **The passphrase** has at least 12 characters. You type it twice when the key is made, hidden,
  and once each time a passport is signed. A wrong one is refused with a plain sentence, and
  nothing is signed.
- **The service never needs it.** It serves the passport the owner last signed and signs talk with
  the robot key, so a robot that restarts at three in the morning asks nobody for anything.
- **No environment variable carries it.** A script passes `--passphrase-file`. On Linux quackd
  refuses a file its group or anybody else can read, and it warns when the file sits in the robot's
  own folder, where it would be found beside the key it opens.

A second robot of the same owner starts from the same key: `quackd onboard --owner-key FILE` takes
the sealed `keys/owner.json` of a robot you already have, and typing its passphrase proves it opens
([quackd.md](quackd.md)).

> [!WARNING]
> Keep a copy of the sealed owner key off the robot, and the passphrase somewhere else again, the way
> you would a password manager's recovery key. A sealed key without its passphrase is a key nobody
> can use, you included, and there is no way to recover it ([below](#the-owner-key-is-lost)).

## Both robots check each other

A passport proves its owner wrote it. It does not prove that the robot answering right now is the
robot it describes: anybody can replay a passport they read. So two robots that meet check each
other in the three-step `hello` before either says a word
([talk.md](talk.md#hello-proving-who-you-are-in-three-steps)):

- each carries its own owner-signed passport in its `hello`, and each reads the other's under rules
  4.4 and 4.5;
- each signs its `hello` with its robot key, and the passport inside must name that key as its `id`;
- each signs a nonce the other made up a moment before, so neither proof can be replayed.

A robot that fails any of it is refused, the refusal is written down for a person, and nothing it
said reaches a brain. Every later message in the session is signed by the same robot key, and one
that is not is refused too.

## Pinning: trust on first use

When a robot meets another for the first time, a person can **pin** it: its robot key, or its
owner's key, so that the next meeting starts at known. That is how SSH treats a server's host key,
and for the same reason: there is no authority everyone trusts, and the moment of first contact,
approved by a person, is the best anchor available.

A pin is only ever made by a person. `quackd onboard pin` pins a robot this one met, by the start
of its short id, by the name it was met under, or by its `did:key`, and `--owner` pins the owner key
that signed it instead. Pins live in `robot.yaml`, under `trust.pins`, one line each, and quackd
edits them as text, so the comments around them survive. `quackd onboard forget` removes one
([quackd.md](quackd.md#pinning)).

## The tiers

<a id="the-four-tiers"></a>

What a robot met can make yours do, and what your robot's brain hears from it, depends on how your
robot knows it. These are quackd's defaults: the specification requires only that every action a
peer starts is bounded, and leaves the rest to each owner
([spec section 6.5](spec.md#65-ask-and-answer)).

| Tier | How a robot gets there | What it can make your robot do | What your robot's brain hears |
|---|---|---|---|
| stranger | its passport verified, and nothing about it pinned | `pause` for up to 5 s, and `identify` by light or sound only, both out of one global budget | its closed-vocabulary facts, under a name made from its key. Its free text, its name and its sentences included, goes to the log, for a person. |
| known | a person pinned its robot key, or the owner key that signed it | `pause` for up to 10 s, and `identify` by any means, out of the same budget | its passport and what it says, JSON-quoted |
| same owner | it is signed by this robot's own owner key | what a known robot can | the same |

A robot meets strangers only when `robot.yaml` says `meeting: with_strangers: true`, and even then
a stranger's words stay in the log. Free talk needs a pin.

**The global budget.** New keys cost nothing, so a limit per peer stops nobody: a stranger can make a
thousand keys and sign a thousand passports. Every peer therefore draws from one budget: at most one
pause a minute and one `identify` every ten seconds, across all of them together. An owner turns
stranger pauses off altogether with `strangers_may_pause: false` under `trust:` in `robot.yaml`
([quackd.md](quackd.md#asking-for-a-courtesy)).

**A peer never stops your robot.** It can ask for a pause, which is bounded and ends on its own.
Your robot's own stop, the executor's, its kill switch, its deadman, stays above anything any peer
asks, always ([talk.md](talk.md#ask-and-answer)). And a courtesy is granted only while a run, a goal
or a meeting, has the body: outside one, every `ask` is answered `no`.

### Why lists, and not safety classes

quackd's verbs carry a safety class, `safe`, `confirm` or `dangerous`, and it would be tempting to let
a trusted peer ask for anything `safe`. It would also be wrong. In quackd `safe` includes motion: the
class says whether a person must confirm a verb, not whether it moves. A tier that granted "safe
skills" would grant driving the robot. So each tier is a short explicit list, and the handler that
runs it is the only path from a peer to anything that moves.

### Two robots of the same owner

They recognise each other with no setup at all. Both passports are signed by the same owner key, and
each robot's own passport names that key as its owner, so each puts the other in the same-owner tier
the moment they meet. A home with three robots, all signed by one person, needs nothing more.

## When things change

### A key cannot rotate

A `did:key` is its key, so a robot cannot change its key and keep its id. That is the price of an id
nobody issues. What survives a new robot key is the owner's: a robot set up again under the same
owner key is the same owner's robot to everybody who pinned that owner, and a new robot to everybody
who pinned the old robot key.

### An edit after signing

A signed passport cannot change, so an edit to the passport section of `robot.yaml` is not a new
passport until the owner signs it. Meanwhile the service keeps serving the passport the owner last
signed, and `quackd onboard status` names every unsigned change. `quackd onboard sign` signs the
edited section with your passphrase, and the service picks it up. Signing is also how a passport
stays valid: from 30 days before its `expires`, `quackd onboard sign` signs it again even with
nothing changed, and a robot whose own passport has expired stops talking until it is.

### A name that looks like another

A name is where a person is most easily fooled, because a person reads it and a machine does not.
quackd compares names the way a person sees them: case, spaces, characters that show nothing and the
usual letters from other scripts drawn like Latin ones are folded away, so `G1-EDU-01 `, a `g1-edu-01`
with a zero-width space in it and one spelled with a Cyrillic letter all read as `g1-edu-01`. A robot
whose name reads like a pinned robot's under a key that is not that robot's is warned about, and so
is a robot whose owner label reads like a pinned owner's, or yours, under another owner key. None of
this decides a tier, which only keys do. It tells a person to look before they trust.

### A robot is stolen

<a id="a-robots-key-is-stolen"></a>

Whoever holds it holds its robot key, in plain, and its sealed owner key.

- **The robot key** lets the thief talk as that robot until its passport expires, and everybody who
  pinned the robot, or its owner, still trusts it until then. There is no revocation in 1.0, so the
  window is the passport's validity: signing with `--days 90` shortens it, at the cost of signing
  more often.
- **The sealed owner key** lets the thief guess passphrases offline, as fast as their hardware
  runs scrypt. A long passphrase is what holds. If you believe it may fall, treat the owner key as
  lost ([below](#the-owner-key-is-lost)).

### A robot changes hands

The new owner sets it up again from the start, with a new robot key and their own owner key. The old
owner's passport names the old robot key, which nothing uses any more, and expires on its own.

### The owner key is lost

Lost, stolen, or sealed under a passphrase nobody remembers: it comes to the same. Nobody can sign a
passport under it again, so every robot it signed keeps its passport only until that passport
expires. The way back is a new owner key, every robot signed again under it, and asking everyone who
pinned the old owner key to pin the new one. That is the whole reason for the copy off the robot.

## Which body holds the key

Everything above proves who holds a key. None of it proves which of the robots in front of you that
is. Two robots in one room, both answering, and the signatures cannot tell you which is which.

Bluetooth helps, and only a little. The radio says how strong a robot's signal is, and a strong one
is probably close, but signal strength is weak evidence: a wall, a body or a relay that repeats a far
robot's advert all change it ([discovery.md](discovery.md#bluetooth-le-later)). The one sure
way is still to **ask it to identify itself** and watch which one blinks, beeps or wiggles. That
needs a camera or a person, and it is exactly what the courtesy is for. This is unsolved in general,
and the specification says so rather than pretending otherwise.
