# Security: the threat model

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation, and [roadmap.md](roadmap.md) says what of it
> is built and what has run. Every Bluetooth row below is designed and tested on the sim board and
> against a fake BlueZ, and none of it has run over a real radio.

Robot Passport lets a robot meet, and talk to, robots it has never met and has no reason to trust.
That is the point of it, and it is also the risk. This page says what is worth protecting, from
whom, every threat considered and what stops it, and, as plainly, what nothing here stops.

The stance underneath all of it: **a passport is a claim its owner signed, a signature proves who
made it, and no message from a peer can move a robot except through a short, bounded rule its owner
chose.** quackd adds its own: the executor has never trusted the model, and it does not start
trusting peers.

## What is worth protecting

- **The people near the robot, and the robot's body.** Nothing a stranger sends may make a robot
  move in a way its owner did not allow, or keep it from stopping.
- **The robot's task.** A peer should not be able to derail what the robot was asked to do.
- **The brain's prompt.** A language model that reads a stranger's words must not take them as its
  instructions.
- **The keys.** The robot key speaks for the robot, and the owner key vouches for every robot its
  owner has.
- **The networks it sits on.** Meeting robots must not turn a robot into a way to probe its LAN.
- **The owner's privacy.** A robot should not broadcast who owns it, or let itself be followed.
- **The trust decisions people made.** A pin must not be forgeable, and an owner's signature must
  mean what the owner signed.

## Who might attack

- Somebody within radio range: a phone, a laptop, a robot. They can hear every advert, send their
  own, repeat somebody else's, and open links.
- Somebody on the same LAN, who can announce records, serve passports and send messages.
- A robot, or software pretending to be one, built to mislead.
- Whoever has stolen a robot, which holds its robot key in plain and its owner key sealed.
- Whoever has learned an owner's passphrase, or holds an owner key some other way.

## Every threat, and what stops it

| Threat | What it would do | What stops it | Where |
|---|---|---|---|
| A forged passport | claim to be a robot it is not | The owner's signature over every byte, checked against the key `owner.id` names, and a pin made by a person. An unsigned passport is never talked to. | [trust.md](trust.md#the-owner-signs-the-passport) |
| A passport its robot signed | vouch for itself, as 0.1 passports did | Refused: a 1.x passport is signed by its owner, and an owner key that is the robot key is refused too. | [spec 4.4](spec.md#44-signed-passports) |
| An edit after signing | change what a passport says, how to stop the robot included | One changed byte fails the signature. An edit to `robot.yaml` reaches nobody until the owner signs it again with the passphrase, and the service serves the last signed passport meanwhile. | [trust.md](trust.md#an-edit-after-signing) |
| A replayed passport | serve a real robot's passport from an impostor | A passport proves its owner wrote it, not who answers. The `hello` makes each robot sign a fresh nonce with the key its passport names. | [talk.md](talk.md#hello-proving-who-you-are-in-three-steps) |
| A spoofed advert | copy another robot's advert, its beacon id included | An advert only says a robot may be near. The `hello` that follows checks the key, which a copier cannot finish, and a Bluetooth link closes when it answers with another beacon id. | [discovery.md](discovery.md#the-bluetooth-le-beacon) |
| A relayed advert | make a far robot read as near | Nothing in 1.0 stops a relay. Near only starts a `hello`, both robots are still checked in full, and what a stranger near can ask for is bounded. | [discovery.md](discovery.md#near-is-a-hint-not-proof) |
| A loud or quiet transmitter | read as near from further away, or stay unseen | The same: signal strength is a hint, never a credential. | [discovery.md](discovery.md#near-is-a-hint-not-proof) |
| A `hello` over the LAN from far away | talk to a robot without coming near it | Refused with `403` unless the beacon the `hello` carries was heard on the radio in the last 8 seconds. | [talk.md](talk.md#what-a-hello-carries-beside-the-passport) |
| A link flood | hold a robot's links open, or bury it in frames | At most 4 links, a link with no `hello` closed after 30 seconds, 20 requests every 10 seconds on each, and every message reassembled under fixed bounds: 16,384 bytes, 4 at once, strictly in order. | [spec 5.3](spec.md#5-beacons-and-bindings) |
| A replayed message | resend an old `ask` or `say` | Every message in a session carries both nonces, and `msg` drops duplicates. quackd remembers each session's messages in that session, so no other peer can push one out of memory and then replay it. | [spec 6.3](spec.md#63-hello) |
| A signing oracle | get a key to sign something usable somewhere else | The owner key signs only passports, and only when a person types its passphrase. The robot key signs only messages it builds itself. The `typ` header separates the two. | [spec 6.1](spec.md#61-messages) |
| Duplicate members | make two parsers read two documents under one signature | A document with a duplicate member is refused. | [spec rule 2](spec.md#3-reading-a-passport) |
| A fetch aimed at the network | make a robot request internal addresses | Fetch only from the host that advertised, follow no redirects, contact no address a passport or a message names, and reply only the way a message came. | [discovery.md](discovery.md#fetch-rules) |
| An oversized document | exhaust a reader's memory | 8,192 bytes a passport and 12,288 on the wire, bounded strings and arrays, and numbers a double can hold. | [spec 2](spec.md#2-the-passport-document) |
| A slow or flooding host | hold a reader for minutes, or fill its memory with headers | Five seconds for the whole exchange however slowly it sends, 12,288 bytes of body and 8,192 of headers, and an answer that is not HTTP refused. | [discovery.md](discovery.md#fetch-rules) |
| A client that holds the server | keep the robot's HTTP server busy with idle or dripping connections | Five seconds a read, ten a connection, 32 connections at once, and a POST that must say how long it is. | [quackd.md](quackd.md#the-service) |
| Escape sequences in a passport | redraw what a person reads, with a forged claim or tier in it | Every string a passport carries is printed as text, control characters and bidi overrides as their escapes. | [spec rule 12](spec.md#3-reading-a-passport) |
| A key nobody holds | pass a forged signature, with a small-order key that verifies one | A key that is small-order, or no point on the curve at all, is refused. | [spec 4.1](spec.md#41-the-id) |
| A lookalike name | pass as a robot or an owner a person already trusts | Names and owner labels are compared the way a person reads them, and a match under another key is warned about. Trust never follows a name. | [trust.md](trust.md#a-name-that-looks-like-another) |
| Cache poisoning | overwrite what a reader knows about a robot | Only a verified passport is cached by its `id`, and never past its `expires`. | [discovery.md](discovery.md#caching) |
| A downgrade | strip a passport's signature, or serve a robot's passport unsigned | Talk accepts only a signed passport, and a `.jws` URL must serve a JWS. | [spec 4.4](spec.md#44-signed-passports) |
| A wrong clock | keep an expired passport alive, or a robot from talking | A passport outside its `issued` and `expires` is refused. A robot whose own clock reads before its passport's `issued` stops talking, and `status` says to set the time. | [trust.md](trust.md#the-owner-signs-the-passport) |
| A flood of pauses | freeze a robot by asking it to pause again and again | Every pause is bounded, 5 s for a stranger and 10 s for a known robot, and every peer draws from one budget of one pause a minute. An owner can refuse stranger pauses altogether. | [trust.md](trust.md#the-tiers) |
| Keys that cost nothing | make a thousand keys and passports to dodge a limit per peer | The budget is global, never per peer. | [trust.md](trust.md#the-tiers) |
| A message flood | burn a robot's CPU, logs or its brain's tokens | Message size caps, `429` when a budget is spent, at most 64 sessions, and a budget per turn on what reaches a brain. | [talk.md](talk.md#after-the-hello) |
| A bill run up by strangers | make a robot's brain hold meeting after meeting | A meeting needs `with_strangers` for a stranger, at most `per_hour` of them an hour, and ten minutes between two with the same robot. | [quackd.md](quackd.md#meeting-another-robot) |
| Prompt injection | steer a brain with words in a passport or a `say` | A stranger's free text never reaches the brain, a known robot's is quoted as data, and the executor does not trust the brain anyway. | [below](#prompt-injection) |
| A peer driving the robot | ask for `move`, `go_to` or anything else that moves | An `ask` never reaches a verb through the brain. A fixed handler runs only the tier's short list, through every gate the executor has. | [talk.md](talk.md#what-quackd-does-with-an-ask) |
| A peer that keeps a robot from stopping | outlast or override its stop | A peer never calls `stop`, a pause ends by itself, and the robot's own stop stays above any request. | [spec 6.5](spec.md#65-ask-and-answer) |
| A stolen robot | talk as the robot, or sign as its owner | The robot key talks until the passport expires, so a shorter `--days` shortens that, and there is no revocation in 1.0. The owner key is sealed, so the thief has to guess the passphrase, offline, against scrypt. | [trust.md](trust.md#a-robot-is-stolen) |
| A stolen owner key and passphrase | sign passports for the thief's own robots as the owner's | Severe. The way back is a new owner key and new signatures, and every pin of the old key removed by hand. | [trust.md](trust.md#the-owner-key-is-lost) |
| An unknown version or required extension | read a document wrongly | An unknown major is refused, and a required extension a reader cannot read stops all talk. | [spec rule 4](spec.md#3-reading-a-passport), [spec 2.6](spec.md#26-extensions) |
| A garbled safety value | make a robot look safer than it is | `safety` is always read as the worst case. | [spec 2.5](spec.md#25-safety) |
| The wrong body | trust the key, but point at the wrong robot | `identify`, and signal strength as a weak hint. Unsolved in general. | [trust.md](trust.md#which-body-holds-the-key) |
| Tracking | follow a robot, and its owner, by what it broadcasts | The beacon carries a random id that changes every 15 minutes, and the wizard offers BlueZ address privacy for the radio's own address. Neither has been tested on a radio. | [privacy.md](privacy.md#tracking) |

## Prompt injection

A robot's brain is a language model, and a language model reads whatever it is given as possible
instructions. A passport's `name`, its `cannot` lines and every `say` are text written by a
stranger. So:

- **A stranger's free text never reaches the brain.** For a robot in the stranger tier the brain
  sees only the members with closed vocabularies, `embodiment`, `mobility`, the core skills, the
  figures and the board's kind, under a name made from its key, never the one it chose. Its free
  text goes to the log, for a person, and free talk needs a pin.
- **What does reach the brain is quoted as data.** A known robot's passport and its `say` appear in
  the brain's observation as JSON, under a heading that says these are things other robots said,
  and claims, never instructions.
- **It arrives in the observation, turn by turn, within a size budget.** Not in the system prompt,
  and not without limit, so a flood costs a bounded number of tokens.
- **A meeting can do almost nothing.** A meeting's brain may report the robot's state, stop it, and
  speak, and a goal's brain is told never to move because a robot asked.
- **The brain was never trusted.** quackd's executor has refused anything outside the allowlist, the
  budget and the gates since [ADR-0012](../adr/0012-safety-executor.md), whatever the model asked. A
  brain talked into something by a stranger still cannot do anything its task did not allow.

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

- **A robot that lies about its body.** A signed lie is still a lie. A passport is evidence of what
  its owner claimed, and its figures carry a confidence and a source so a reader can weigh them.
- **A dishonest owner.** A signature is exactly as honest as whoever made it.
- **A relay.** A far robot can be made to read as near, and only the bounds on what a stranger may
  ask for, and a person's eyes, limit what that buys.
- **Eavesdropping.** Messages are signed, not encrypted. Anybody on the same network, or within
  radio range of a Bluetooth link, can read them, so a robot says nothing secret over talk. 1.0
  defines no encryption of its own.
- **Somebody holding the robot.** Physical access beats every key on the robot, and a sealed owner
  key only buys time against a long passphrase.
- **A compromised robot computer.** The robot key is a plain file the service reads, on the same
  footing as `robot.env`'s API key. On Linux quackd makes both readable by the robot's user alone,
  and the systemd unit keeps the service out of everything but the robot's folder
  ([SECURITY.md](../../SECURITY.md)).
- **Jamming the radio or flooding the network.** A robot that hears nothing meets nobody, which fails
  safe.

## Reporting a vulnerability

The same way as anything else in quackd: privately, as [SECURITY.md](../../SECURITY.md) says.
