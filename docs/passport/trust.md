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

No row makes a mass, a speed or a `cannot` true: those stay figures with a confidence and a source,
from whoever wrote them. A reader labels every passport it shows with its row
([spec rule 11](spec.md#3-reading-a-passport)), and talk never accepts the first row.

## The id is a key

A robot's `id` is a `did:key`: its Ed25519 public key, written as `did:key:z6Mk` and 44 more
characters. Nobody issues it, nobody can take it away, and nothing needs to be looked up to check a
signature against it, which matters for two robots meeting in a corridor with no internet between
them. An owner is a key in the same way, and `owner.id` is that key.

The short id, the first 8 bytes of the SHA-256 of a key as 16 hex characters, is how a person names a
robot or an owner, and what a robot's LAN record carries. It is a hint, and only a signature
decides. The Bluetooth beacon carries no short id at all, but a random id that changes every 15
minutes ([discovery.md](discovery.md#the-bluetooth-le-beacon)). Keys are 32 bytes and signatures 64
([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)), and every language a robot runs has Ed25519.

## Two keys on every robot

| Key | Signs | Kept as |
|---|---|---|
| the robot key, `id` | every talk message the robot sends | `keys/robot.json`, plain and readable by the robot's user alone, because the service starts after a reboot with nobody there to ask |
| the owner key, `owner.id` | the passport | `keys/owner.json`, sealed with a passphrase and opened only to sign |

The two must differ: a passport whose owner key is its robot key has nothing but the robot vouching
for it, and a reader refuses it.

## The owner signs the passport

<a id="stamps"></a>

The passport's JWS is signed by the owner key, and its header's `kid` is `owner.id`. So the owner
vouches for what the passport says, member by member, how a person stops the robot included, and one
byte changed after signing makes every reader refuse it.

Robot Passport 0.1 did it the other way round. The robot's key signed the passport, and an owner
**stamped** the robot's key: "this key is my robot". A stamp vouched for a key and never for what the
passport said, so whoever held the robot's key could rewrite the passport and keep the stamp. 1.0
drops stamps. Whoever holds only the robot key cannot change a word of the passport now.

A reader refuses a passport under [rule 4.4](spec.md#44-signed-passports) when its robot's own key
signed it, the 0.1 shape, when any key but the one `owner.id` names signed it, or when its owner key
is its robot key. Under [rule 4.5](spec.md#4-identity-signatures-and-fingerprints) it refuses one
whose `issued` and `expires` the reader's clock is outside, with five minutes of grace for a clock a
little ahead. quackd signs for 365 days unless `--days` says otherwise, and never for more than the
400 a reader accepts.

## Signing, with nothing to canonicalise

A signed passport is a compact JWS ([RFC 7515](https://www.rfc-editor.org/rfc/rfc7515)) whose payload
is the passport's exact bytes. Its header says `alg` `Ed25519`, `kid` the owner's `did:key` and `typ`
`robot-passport+jws`, and a reader checks all three and the signature before it reads the payload.
It also accepts the older `alg` label `EdDSA`, which
[RFC 9864](https://www.rfc-editor.org/rfc/rfc9864) deprecates and most libraries still write.

Signing the exact bytes is deliberate. A signature embedded in the JSON it signs needs both sides to
agree on one canonical way to write that JSON, which is where signature bugs live, and A2A signs its
agent cards that way. Here the bytes are carried unchanged, in a file, a cache or a `hello`, so there
is nothing to agree on. That is also why a reader refuses a duplicate member: two parsers that pick
different duplicates would read two documents under one signature.

## The owner key, sealed on the robot

The owner key lives on the robot because the passport is signed there, by the wizard and by
`quackd onboard sign` after an edit. The file holds it encrypted with AES-256-GCM, under a key scrypt
derives from your passphrase, with the owner's `did:key` bound in. scrypt is slow and hungry for
memory on purpose, so every guess at a passphrase costs time and memory.

- **The passphrase** has at least 12 characters. You type it twice, hidden, when the key is made, and
  once each time a passport is signed. A wrong one signs nothing.
- **The service never needs it.** It serves the passport the owner last signed and signs talk with the
  robot key, so a robot that restarts at three in the morning asks nobody for anything.
- **No environment variable carries it.** A script passes `--passphrase-file`, which quackd refuses on
  Linux when its group or anybody else can read it, and warns about when it sits in the robot's
  folder, beside the key it opens.

A second robot of the same owner starts from the same sealed key, with `--owner-key`
([quackd.md](quackd.md#a-second-robot-of-the-same-owner)).

> [!WARNING]
> Keep a copy of the sealed owner key off the robot, and the passphrase somewhere else again, the way
> you would a password manager's recovery key. Nobody can open a sealed key without its passphrase,
> you included ([below](#the-owner-key-is-lost)).

## Both robots check each other

A passport proves its owner wrote it, not that the robot answering is the one it describes: anybody
can replay a passport they read. So in the three-step `hello`, before either says a word, each robot
reads the other's passport under rules 4.4 and 4.5, checks that it names as its `id` the robot key
that signed the `hello`, and has the other sign a nonce it made up a moment before
([talk.md](talk.md#hello-proving-who-you-are-in-three-steps)). A robot that fails is refused, the
refusal is written down for a person, and nothing it said reaches a brain.

## Pinning: trust on first use

When a robot meets another for the first time, a person can **pin** it, its robot key or its owner's
key, so the next meeting starts at known. That is how SSH treats a server's host key, for the same
reason: there is no authority everyone trusts, and first contact approved by a person is the best
anchor available.

Only a person makes a pin. `quackd onboard pin` pins a robot this one met, by the start of its short
id, the name it was met under or its `did:key`, and `--owner` pins the owner key that signed it.
Pins live in `robot.yaml` under `trust.pins`, edited as text so your comments survive, and
`quackd onboard forget` removes one ([quackd.md](quackd.md#pinning)).

## The tiers

<a id="the-four-tiers"></a>

What a robot met can make yours do, and what your robot's brain hears from it, depends on its tier.
These are quackd's defaults: the specification requires only that every action a peer starts is
bounded ([spec section 6.5](spec.md#65-ask-and-answer)).

| Tier | How a robot gets there | What it can make your robot do | What your robot's brain hears |
|---|---|---|---|
| stranger | its passport checked out, and nothing about it is pinned | `pause` for up to 5 s, and `identify` by light or sound | its closed-vocabulary facts, under a name made from its key. Its free text, its name and its sentences included, goes to the log, for a person. |
| known | a person pinned its robot key, or the owner key that signed it | `pause` for up to 10 s, and `identify` by any means | its passport and what it says, JSON-quoted |
| same owner | it is signed by this robot's own owner key | what a known robot can | the same |

A robot meets strangers only when `robot.yaml` says `with_strangers: true` under `meeting:`, and even
then a stranger's words stay in the log. Free talk needs a pin.

**One global budget.** New keys cost nothing, so a limit per peer stops nobody. Every peer draws from
one budget: one pause a minute and one `identify` every ten seconds, across all of them together. An
owner turns stranger pauses off with `strangers_may_pause: false` under `trust:`.

**A peer never stops your robot.** A pause is bounded and ends on its own, and your robot's own stop,
the executor's, its kill switch, its deadman, stays above anything a peer asks
([talk.md](talk.md#ask-and-answer)). A courtesy is granted only while a goal or a meeting has the
body; outside one every `ask` is answered `no`.

### Why lists, and not safety classes

quackd's verbs carry a safety class, `safe`, `confirm` or `dangerous`, and it would be tempting to let
a trusted peer ask for anything `safe`. In quackd `safe` includes motion: the class says whether a
person must confirm a verb, not whether it moves. A tier that granted "safe skills" would grant
driving the robot. So each tier is a short explicit list, and the handler that runs it is the only
path from a peer to anything that moves.

### Two robots of the same owner

They recognise each other with no setup: both passports are signed by the same owner key, which each
robot's own passport names, so each puts the other in the same-owner tier the moment they meet.

## When things change

### A key cannot rotate

A `did:key` is its key, so a robot cannot change its key and keep its id. That is the price of an id
nobody issues. A robot set up again under the same owner key is still that owner's robot to
everybody who pinned the owner, and a new robot to everybody who pinned the old robot key.

### An edit after signing

An edit to the passport section of `robot.yaml` is not a passport until the owner signs it. The
service keeps serving the last signed one, `quackd onboard status` names every unsigned change, and
`quackd onboard sign` signs the edit with your passphrase. From 30 days before `expires` it signs
again even with nothing changed, and a robot whose own passport has expired stops talking until it
is signed.

### A name that looks like another

A person reads names, and a person is easily fooled. quackd compares names the way a person sees them:
case, spaces, characters that show nothing and letters from other scripts drawn like Latin ones are
folded away, so `G1-EDU-01 `, a `g1-edu-01` with a zero-width space in it and one spelled with a
Cyrillic letter all read as `g1-edu-01`. A robot whose name reads like a pinned robot's under another
key is warned about, and so is one whose owner label reads like a pinned owner's, or yours, under
another owner key. Only keys decide a tier; the warning tells a person to look.

### A robot is stolen

<a id="a-robots-key-is-stolen"></a>

Whoever holds it holds its robot key in plain, and its owner key sealed.

- **The robot key** lets the thief talk as that robot until its passport expires, and whoever pinned
  the robot or its owner trusts it until then. There is no revocation in 1.0, so the window is the
  passport's validity, and signing with a shorter `--days` shrinks it.
- **The sealed owner key** lets the thief guess passphrases offline, as fast as their hardware runs
  scrypt. A long passphrase is what holds, and if you think it may fall, treat the owner key as lost.

### A robot changes hands

The new owner sets it up again from the start, with a new robot key and their own owner key. The old
passport names a robot key nothing uses any more, and expires on its own.

### The owner key is lost

Lost, or sealed under a passphrase nobody remembers, it signs nothing again, and every robot it
signed keeps its passport only until that expires. The way back is a new owner key, every robot
signed again under it, and everyone who pinned the old owner key asked to pin the new one. A stolen
owner key with its passphrase is worse, since the thief can sign until every pin of it is removed.

## Which body holds the key

Everything above proves who holds a key, and none of it which robot in front of you that is. Signal
strength helps a little: a strong signal is probably close, but a wall, a body or a relay changes it
([discovery.md](discovery.md#near-is-a-hint-not-proof)). The sure way is still to **ask it to
identify itself** and watch which one blinks, beeps or wiggles, which needs a camera or a person and
is what the courtesy is for. This is unsolved in general, and the specification says so.
