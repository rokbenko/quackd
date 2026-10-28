# Security: the threat model

> [!NOTE]
> Spec draft 0.1. quackd 0.23.0 is its first implementation, and [roadmap.md](roadmap.md) says what
> of it is built and what has run.

Robot Passport lets a robot read, and talk to, robots it has never met and has no reason to trust.
That is the point of it, and it is also the risk. This page says what is worth protecting, from
whom, every threat considered and what stops it, and, as plainly, what nothing here stops.

The stance underneath all of it: **a passport is a claim, a signature proves who made it, and no
message from a peer can move a robot except through a short, bounded rule its owner chose.** quackd
adds its own: the executor has never trusted the model, and it does not start trusting peers.

## What is worth protecting

- **The people near the robot, and the robot's body.** Nothing a stranger sends may make a robot
  move in a way its owner did not allow, or keep it from stopping.
- **The robot's task.** A peer should not be able to derail what the robot was asked to do.
- **The pilot's prompt.** A language model that reads a stranger's words must not take them as
  its instructions.
- **The reader's network.** Reading a passport must not turn a robot into a way to probe the
  network it sits on.
- **The owner's privacy.** A robot should not broadcast who owns it, or let itself be followed.
- **The trust decisions people made.** A pin or a stamp must not be forgeable.

## Who might attack

- A passer-by with a phone, who can scan a sticker and send messages to an inbox.
- Somebody on the same local network, who can advertise beacons, serve passports and send
  messages.
- A robot, or software pretending to be one, built to mislead.
- Whoever has stolen a robot, or a robot's private key.
- Whoever has stolen an owner's key.
- The operator of an MQTT broker the robots share.

## Every threat, and what stops it

| Threat | What it would do | What stops it | Where |
|---|---|---|---|
| A forged passport | claim to be a robot it is not | A signature by the `id`'s key, and a pin made by a person. An unsigned passport is labelled as a claim. | [spec 4.4](spec.md#44-signed-passports), [trust.md](trust.md#pinning-trust-on-first-use) |
| A spoofed beacon | send a reader to a fake passport | The beacon is only a pointer. The passport it leads to is checked like any other. | [spec 5](spec.md#5-beacons-and-bindings) |
| A short-id collision | pose as a robot by copying its short id | A short id is a lookup hint and never proof. Only the key and its signature count. | [spec 4.2](spec.md#42-the-short-id) |
| A replayed passport | serve a real robot's signed passport from an impostor | A passport proves a key wrote it, not who answers. `hello` proves the robot answering holds the key now. | [talk.md](talk.md#hello-proving-who-you-are-in-three-steps) |
| A replayed message | resend an old `ask` or `say` | Every message in a session carries both nonces, and `msg` drops duplicates. quackd remembers each session's messages in that session, so no other peer can push one out of memory with messages of its own and then replay it. | [spec 6.3](spec.md#63-hello) |
| A signing oracle | get a robot to sign something usable somewhere else | A robot signs only messages it builds itself, and the `typ` header separates passports, stamps and messages. | [spec 6.1](spec.md#61-messages) |
| Duplicate members | make two parsers read two documents under one signature | A document with a duplicate member is refused. | [spec rule 2](spec.md#3-reading-a-passport) |
| A fetch aimed at the network | make a reader request internal addresses | Fetch only from the advertising host or a URL a person or camera read, follow no redirects, contact no URL a passport names. | [discovery.md](discovery.md#fetch-rules) |
| An oversized document | exhaust a reader's memory | 8,192 bytes a passport and 12,288 on the wire, bounded strings and arrays, and numbers a double can hold. | [spec 2](spec.md#2-the-passport-document) |
| A slow or flooding host | hold a reader for minutes, or fill its memory with headers | Five seconds for the whole exchange however slowly it sends, 12,288 bytes of body and 8,192 of headers, and an answer that is not HTTP refused. | [spec 5.7](spec.md#57-fetch-rules) |
| A client that holds a server | keep `quackd passport serve` busy with idle or dripping connections | Five seconds a read, ten a connection, 32 connections at once, and a POST that must say how long it is. | [quackd.md](quackd.md#the-service) |
| Escape sequences in a passport | redraw the panel a person reads, with a forged claim or tier in it | Every string a passport carries is printed as text, control characters and bidi overrides as their escapes. | [spec rule 12](spec.md#3-reading-a-passport) |
| A key nobody holds | pass a forged signature, with a small-order key that verifies one | A key that is small-order, or no point on the curve at all, is refused. | [spec 4.1](spec.md#41-the-id) |
| A lookalike name | pass as a robot or an owner a person already trusts | Names are compared the way a person reads them, with every pin and every robot this quackd drives, and a match under another key is warned about. Trust never follows a name. | [trust.md](trust.md#a-name-that-looks-like-another) |
| Cache poisoning | overwrite what a reader knows about a robot | Only a verified passport is cached by its `id`. | [spec 5.8](spec.md#58-caching) |
| A downgrade | strip a robot's signature and serve its passport unsigned | What a `.jws` URL serves must be a JWS, a sticker's short id is kept only by a signed passport, and a reader that pinned a robot says so when it gets less. | [spec 4.4](spec.md#44-signed-passports), [trust.md](trust.md#pinning-trust-on-first-use) |
| A flood of pauses | freeze a robot by asking it to pause again and again | Every pause is bounded (5 s for a stranger, 10 s for a known robot) and every peer draws from one budget of one pause a minute. An owner can refuse stranger pauses altogether. | [trust.md](trust.md#the-four-tiers) |
| Keys that cost nothing | make a thousand keys to dodge a limit per peer | The budget is global, never per peer. | [trust.md](trust.md#the-four-tiers) |
| A message flood | burn a host's CPU, logs or a pilot's tokens | Message size caps, `429` when a budget is spent, and a per-turn budget on what reaches a pilot. | [talk.md](talk.md#channels) |
| Prompt injection | steer a pilot with words in a passport or a `say` | Text is quoted as data, a stranger's free text never reaches the pilot, and the executor does not trust the pilot anyway. | [below](#prompt-injection) |
| A peer driving the robot | ask for `move`, `go_to` or anything else that moves | An `ask` never reaches a verb through the pilot. A fixed handler runs only the tier's short list, through every gate the executor has. | [talk.md](talk.md#what-quackd-does-with-an-ask) |
| A peer that keeps a robot from stopping | outlast or override its stop | A peer never calls `stop`, a pause ends by itself, and the robot's own stop stays above any request. | [spec 6.5](spec.md#65-ask-and-answer) |
| A stolen robot key | sign as the robot | Stamps expire, so the thief keeps the same-owner tier only until the stamp does. The owner gives the robot a new key and a new stamp. | [trust.md](trust.md#a-robots-key-is-stolen) |
| A stolen owner key | stamp the thief's own robots as the owner's | Keep the owner key off the robots and backed up, and keep stamps short-lived. This one is severe, and the revocation list that would end it is not in 0.1. | [trust.md](trust.md#the-owner-key-is-lost) |
| An unknown version or required extension | read a document wrongly | An unknown MAJOR is refused, and a required extension a reader cannot read stops all talk. | [spec rule 4](spec.md#3-reading-a-passport), [spec 2.6](spec.md#26-extensions) |
| A garbled safety value | make a robot look safer than it is | `safety` is always read as the worst case. | [spec 2.5](spec.md#25-safety) |
| The wrong body | trust the key, but point at the wrong robot | `identify`, or the short id read off a sticker on the body. Unsolved in general. | [trust.md](trust.md#which-body-holds-the-key) |
| Tracking | follow a robot, and its owner, by its id | Beacons carry no owner, and Bluetooth waits for rotating ids. | [privacy.md](privacy.md#tracking) |
| A broker in the middle | drop or replay what it carries | It can drop and delay, but it cannot forge a signature or a session's nonces. | [discovery.md](discovery.md#mqtt) |

## Prompt injection

A robot's pilot is a language model, and a language model reads whatever it is given as possible
instructions. A passport's `name`, its `cannot` lines and every `say` are text written by a
stranger. So:

- **A stranger's free text never reaches the pilot.** For a robot in the stranger tier the pilot
  sees only the members with closed vocabularies, `embodiment`, `mobility`, the core skills, the
  figures, under a name made from its key, never the one it chose. Its free text goes to the
  log, for a person.
- **What does reach the pilot is quoted as data.** A known robot's passport and its `say` appear in
  the pilot's observation as JSON, under a heading that says these are things other robots said,
  and claims, never instructions.
- **It arrives in the observation, turn by turn, within a size budget.** Not in the system prompt,
  which quackd builds once per run, and not without limit, so a flood costs a bounded number of
  tokens.
- **The pilot was never trusted.** quackd's executor has refused anything outside the allowlist, the
  budget and the gates since [ADR-0012](../adr/0012-safety-executor.md), whatever the model asked.
  A pilot talked into something by a stranger still cannot do anything its task did not allow.

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
([concepts/safety.md](../concepts/safety.md)). Below both, a robot whose
`safety` says it stops on silence stops when quackd goes quiet, and quackd's kill switch reaches
every executor. A peer can sit above all of that and never beneath any of it.

## What nothing here stops

- **A robot that lies about its body.** A signed lie is still a lie. A passport is evidence of what
  its writer claimed, and its figures carry a confidence and a source so a reader can weigh them.
- **A dishonest owner.** A stamp is exactly as honest as whoever signed it.
- **Eavesdropping.** Messages are signed, not encrypted. Anybody on the same network or broker can
  read them, so a robot says nothing secret over talk. HTTPS and MQTT over TLS hide them where a
  channel offers them, and 0.1 defines no encryption of its own.
- **Somebody holding the robot.** Physical access beats every key on the robot.
- **A compromised quackd host, or one others can read.** Keys under `~/.quackd/keys/`, the
  robots' and the owner key alike, are plain files on the same footing as the tokens in
  `robots.json`, which [SECURITY.md](../../SECURITY.md) already says plainly. On Linux and macOS
  quackd makes them readable by their owner alone. On Windows a file takes the permissions of its
  folder, so a home directory other people can read gives them the keys.
- **Jamming or flooding the network itself.** A robot that cannot reach anything simply meets
  nobody, which fails safe.
- **Bluetooth.** 0.1 has no Bluetooth binding, so it has no Bluetooth threats, and the ones a later
  version adds will be written here first.

## Reporting a vulnerability

The same way as anything else in quackd: privately, as [SECURITY.md](../../SECURITY.md) says.
