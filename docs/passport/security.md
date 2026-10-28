# Security: the threat model

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation, and [roadmap.md](roadmap.md) says what of it
> is built and what has run. Every Bluetooth row below is designed and tested in the suite and on
> the sim board, and none of it has run over a real radio.

Robot Passport lets a robot meet, and talk to, robots it has never met and has no reason to trust.
That is the point of it, and it is also the risk. This page says what is worth protecting, from
whom, every threat considered and what stops it, and, as plainly, what nothing here stops.

The stance underneath all of it: **a passport is a claim its owner signed, a signature proves who
made it, and no message from a peer can move a robot except through a short, bounded rule its owner
chose.** quackd adds its own: the executor has never trusted the model, and it does not start
trusting peers.

## What is worth protecting

- **The people near the robot, and its body.** Nothing a stranger sends may move a robot in a way its
  owner did not allow, or keep it from stopping.
- **The robot's task, and its brain's prompt.** A peer must not derail the task, and a language model
  must not take a stranger's words as its instructions.
- **The keys.** The robot key speaks for the robot, and the owner key vouches for every robot its
  owner has.
- **The networks it sits on, and the owner's privacy.** Meeting robots must not make a robot a way to
  probe its LAN, or to follow it.
- **The trust decisions people made.** A pin must not be forgeable, and a signature must mean what
  the owner signed.

## Who might attack

Anybody within radio range, who can hear every advert, send their own, repeat somebody else's and
open links. Anybody on the same LAN. A robot, or software pretending to be one, built to mislead.
Whoever has stolen a robot, with its robot key in plain and its owner key sealed. Whoever has learned
an owner's passphrase.

## Every threat, and what stops it

| Threat | What it would do | What stops it | Where |
|---|---|---|---|
| A forged passport | claim to be a robot it is not | The owner's signature over every byte, a pin made by a person, and no talk with an unsigned passport. | [trust.md](trust.md#the-owner-signs-the-passport) |
| A passport its robot signed | vouch for itself, as 0.1 passports did | Refused: a 1.x passport is signed by its owner, with an owner key that is not the robot key. | [spec 4.4](spec.md#44-signed-passports) |
| An edit after signing | change what a passport says, how to stop the robot included | One changed byte fails the signature, and an edit to `robot.yaml` reaches nobody until the owner signs it with the passphrase. | [trust.md](trust.md#an-edit-after-signing) |
| A replayed passport | serve a real robot's passport from an impostor | The `hello` makes each robot sign a fresh nonce with the key its passport names. | [talk.md](talk.md#hello-proving-who-you-are-in-three-steps) |
| A spoofed advert | copy another robot's advert, its beacon id included | An advert only starts a `hello`, which a copier cannot finish, and a link whose answer names another beacon id closes. | [discovery.md](discovery.md#the-bluetooth-le-beacon) |
| A relayed advert, or a loud transmitter | make a far robot read as near | Nothing in 1.0 stops it. Near only starts a `hello`, both robots are still checked in full, and what a stranger can ask for is bounded. | [discovery.md](discovery.md#near-is-a-hint-not-proof) |
| A `hello` over the LAN from far away | talk to a robot without coming near it | `403` unless the beacon it carries was heard on the radio in the last 8 seconds. | [talk.md](talk.md#what-a-hello-carries-beside-the-passport) |
| A link flood | hold a robot's links open, or bury it in frames | 4 links at most, 30 seconds to say hello, 20 requests every 10 seconds on each, and fixed bounds on every message. | [spec 5.6](spec.md#56-bluetooth-le) |
| A replayed message | resend an old `ask` or `say` | Both nonces in every message of a session, `msg` to drop duplicates, and a memory of them per session that no other peer can flush. | [spec 6.3](spec.md#63-hello) |
| A signing oracle | get a key to sign something usable elsewhere | The owner key signs only passports, when a person types its passphrase. The robot key signs only messages it builds. `typ` keeps the two apart. | [spec 6.1](spec.md#61-messages) |
| Duplicate members | make two parsers read two documents under one signature | Refused. | [spec rule 2](spec.md#3-reading-a-passport) |
| A fetch aimed at the network | make a robot request internal addresses | Fetch only from the host that advertised, no redirects, no address a passport or message names, and replies only the way a message came. | [discovery.md](discovery.md#fetch-rules) |
| An oversized document, or a slow host | exhaust a reader's memory, or hold it for minutes | 8,192 bytes a passport, 12,288 on the wire, 8,192 of headers, and 5 seconds for the whole exchange. | [discovery.md](discovery.md#fetch-rules) |
| A client that holds the server | keep the robot's HTTP server busy | 5 seconds a read, 10 a connection, 32 connections at once. | [quackd.md](quackd.md#the-service) |
| Escape sequences in a passport | redraw what a person reads | Every string printed as text, control characters and bidi overrides as escapes. | [spec rule 12](spec.md#3-reading-a-passport) |
| A key nobody holds | pass a forged signature with a small-order key | Refused, as is any key that is no point on the curve. | [spec 4.1](spec.md#41-the-id) |
| A lookalike name | pass as a robot or an owner a person trusts | Names and owner labels compared the way a person reads them, a match under another key warned about, and trust never by name. | [trust.md](trust.md#a-name-that-looks-like-another) |
| Cache poisoning | overwrite what a reader knows about a robot | Only a verified passport is cached by its `id`, and never past `expires`. | [discovery.md](discovery.md#caching) |
| A downgrade | strip a passport's signature | Talk accepts only a signed passport, and a `.jws` URL must serve a JWS. | [spec 4.4](spec.md#44-signed-passports) |
| A wrong clock | keep an expired passport alive | A passport outside `issued` and `expires` is refused, and a robot whose clock reads before its own `issued` stops talking. | [trust.md](trust.md#the-owner-signs-the-passport) |
| A flood of pauses, from keys that cost nothing | freeze a robot | Every pause bounded, 5 s for a stranger and 10 s for a known robot, out of one budget for every peer, never one per peer. | [trust.md](trust.md#the-tiers) |
| A message flood | burn a robot's CPU, logs or tokens | Size caps, `429` when a budget is spent, 64 sessions at most, and a budget per turn on what reaches a brain. | [talk.md](talk.md#after-the-hello) |
| A bill run up by strangers | make a brain hold meeting after meeting | Strangers met only with `with_strangers`, `per_hour` meetings at most, and ten minutes between two with one robot. | [quackd.md](quackd.md#meeting-another-robot) |
| Prompt injection | steer a brain with a passport or a `say` | A stranger's free text never reaches a brain, a known robot's is quoted as data, and the executor never trusted the brain. | [below](#prompt-injection) |
| A peer driving the robot | ask for `move`, `go_to` or anything that moves | A fixed handler runs only the tier's short list, through every gate the executor has. | [talk.md](talk.md#what-quackd-does-with-an-ask) |
| A peer that keeps a robot from stopping | outlast or override its stop | A peer never calls `stop`, a pause ends by itself, and the robot's own stop stays above any request. | [spec 6.5](spec.md#65-ask-and-answer) |
| A stolen robot | talk as the robot, or sign as its owner | The robot key talks only until the passport expires, with no revocation in 1.0. The owner key is sealed, so the passphrase has to be guessed offline against scrypt. | [trust.md](trust.md#a-robot-is-stolen) |
| A stolen owner key and passphrase | sign the thief's robots as the owner's | Severe: a new owner key, every robot signed again, and every pin of the old key removed by hand. | [trust.md](trust.md#the-owner-key-is-lost) |
| An unknown version or required extension | read a document wrongly | An unknown major is refused, and an unread required extension stops all talk. | [spec rule 4](spec.md#3-reading-a-passport) |
| A garbled safety value | make a robot look safer | `safety` read as the worst case. | [spec 2.5](spec.md#25-safety) |
| The wrong body | point at the wrong robot | `identify`, and signal strength as a weak hint. Unsolved in general. | [trust.md](trust.md#which-body-holds-the-key) |
| Tracking | follow a robot by what it broadcasts | A random beacon id that changes every 15 minutes, and BlueZ address privacy, which the wizard offers. Neither tested on a radio. | [privacy.md](privacy.md#tracking) |

## Prompt injection

A robot's brain is a language model, and it reads whatever it is given as possible instructions. A
passport's `name`, its `cannot` lines and every `say` are text a stranger wrote. So:

- **A stranger's free text never reaches the brain.** It sees a stranger's closed-vocabulary members
  only, its `embodiment`, `mobility` and `manipulator`, its mass, height and top speed, its core
  skills and its board's kind, under a name made from its key. The free text goes to the log, for a
  person, and free talk needs a pin.
- **What does reach the brain is quoted as data**, as JSON under a heading that says these are claims
  other robots made, never instructions, turn by turn and within a size budget.
- **A meeting can do almost nothing.** Its brain may report the robot's state, stop it, and speak, and
  a goal's brain is told never to move because a robot asked.
- **The brain was never trusted.** quackd's executor has refused anything outside the allowlist, the
  budget and the gates since [ADR-0012](../adr/0012-safety-executor.md), whatever the model asked.

## How the passport sits on quackd's executor

The passport layer and the executor do different jobs, and neither does the other's:

```
a peer's ask ──> passport layer: is this peer allowed to ask this, now?   (tier, list, budget)
                     │ yes
                     v
                 executor: may this verb run, now?   (allowlist, peer, paused, verdict, confirm, budget, preconditions)
                     │ yes
                     v
                 the body, under its own deadman and quackd's kill switch
```

The passport layer, quackd's courtesy handler, can only say no, or pass a request on. The executor
then applies every gate it always has, and two that read the source `peer` on the request: `peer`,
which admits only the `stop` a pause begins with and the `say` an identify is, and `paused`, which
admits nothing that moves any part of the body while a granted pause holds, whoever asks
([concepts/safety.md](../concepts/safety.md)). Below both, a robot whose `safety` says it stops on
silence stops when its controller goes quiet. A peer can sit above all of that and never beneath
any of it.

## What nothing here stops

- **A robot that lies about its body.** A signed lie is still a lie, and its figures carry a
  confidence and a source so a reader can weigh them.
- **A dishonest owner.** A signature is exactly as honest as whoever made it.
- **A relay.** A far robot can be made to read as near, and only the bounds on what a stranger may
  ask for, and a person's eyes, limit what that buys.
- **Eavesdropping.** Messages are signed, not encrypted, so anybody on the LAN or in range of a link
  can read them. Say nothing secret over talk.
- **Somebody holding the robot.** Physical access beats every key on it, and a sealed owner key only
  buys time against a long passphrase.
- **A compromised robot computer.** The robot key is a plain file, like the brain's key in
  `robot.env`. On Linux both are readable by the robot's user alone, and the systemd unit keeps the
  service out of everything but the robot's folder ([SECURITY.md](../../SECURITY.md)).
- **Jamming the radio or flooding the network.** A robot that hears nothing meets nobody, which fails
  safe.

## Reporting a vulnerability

The same way as anything else in quackd: privately, as [SECURITY.md](../../SECURITY.md) says.
