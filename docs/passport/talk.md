# Talk: what two robots say once they have met

> [!NOTE]
> Spec 1.0. quackd speaks all six messages over an HTTP inbox on a LAN and over a Bluetooth link,
> and decides every `ask` with its courtesy handler. Both channels have run between two processes
> on the sim board, and neither has run on a Jetson or over a real Bluetooth radio
> ([roadmap.md](roadmap.md)).

Two robots that are near each other and have each checked the other's passport can talk: six small
messages to prove who they are, say something, share what they saw, or ask each other for a
courtesy. The specification's own words are in [section 6](spec.md#6-talk).

Talk adds no transport of its own. A message is a small signed JSON object, and it travels over an
HTTP inbox when the two robots share a LAN, and over the Bluetooth link they opened when they do
not ([discovery.md](discovery.md#reaching-each-other)). The six messages are a floor every robot
can stand on, not a ceiling: two robots that share something richer can move to it after their
`hello`.

## The six messages

| Type | Carries | For |
|---|---|---|
| `hello` | a passport, a nonce, and what the robot is running now | proving who each side is, in three steps |
| `say` | up to 400 characters of text | people, and language models, and never an instruction |
| `bye` | nothing | ending a session |
| `ask` | a skill, its arguments, and why | asking for a courtesy: `pause` or `identify` |
| `answer` | `ok`, `no` or `later`, and a reason | replying to an `ask`, or to a version a robot cannot read |
| `share` | something it saw, where, and how long ago | telling another robot a fact |

Every message has the same envelope:

| Member | What it is |
|---|---|
| `passport` | the protocol version |
| `type` | one of the six |
| `from` | the sender's `id`, its robot key |
| `to` | the receiver's `id` |
| `msg` | a random token, unique per sender, so a repeat is recognised and dropped |
| `re` | the `msg` this one replies to |
| `nonces` | the session's two nonces, after a `hello` |
| `body` | what the type carries |

Every message is a compact JWS of that object, signed by the sender's robot key, with `typ`
`robot-passport-msg+jws`, and an unsigned one is refused whatever it says. An owner key signs
passports and a robot key signs messages, and their two `typ`s keep either signature from passing as
the other. A message is at most 4,096 bytes, and a `hello`, which carries a passport, 16,384.

## Channels

**HTTP, on a LAN.** The sender POSTs one message to `/robot-passport/inbox`, on the host and port
the robot's DNS-SD record names, and reads the reply in the response:

| Response | Means |
|---|---|
| `200` | one reply message in the body |
| `204` | received, no reply |
| `400` | malformed |
| `403` | refused by the receiver's policy, or a first `hello` from a robot its radio has not heard |
| `413` | too large |
| `429` | a budget is spent |

**A Bluetooth link.** The same message goes as one request over the link, in frames, and the reply
comes back over the link with the same status. Either robot can send, whichever dialled.

A reply goes back the way its message came: on the HTTP response, to the address a `hello` came from
at the port it named, or over the same link, and never to an address a message merely mentions
([discovery.md](discovery.md#fetch-rules)).

## hello: proving who you are, in three steps

A passport proves its owner wrote it. It does not prove that the robot answering right now is the
robot it describes: anybody can replay a passport they read. `hello` closes that gap, in three
messages, and both robots check each other before either says anything else:

```
 A                                                       B
 |  1  hello  {passport: A's, nonce: nA, runtime}        |
 | ----------------------------------------------------> |   B checks A's passport
 |                                                       |
 |  2  hello  re 1, nonces [nA, nB], {passport: B's,     |   signed by B's robot key
 |            runtime}                                   |
 | <---------------------------------------------------- |   A checks B's passport
 |                                                       |
 |  3  hello  re 2, nonces [nA, nB], {}                  |   signed by A's robot key
 | ----------------------------------------------------> |
 |                                                       |
 |        session (A, B, nA, nB): say, ask, share        |
```

Each passport is the JWS its owner signed, carried unchanged, and each robot reads the other's
under rules 4.4 and 4.5, its owner's signature and its validity. Then it checks that the passport's
`id` is the key that signed the `hello`, that the passport is not its own, and that it carries no
required extension it cannot read. A robot that fails is refused there, and the refusal is written
down for a person.

A nonce is 16 random bytes. After step 2, A knows B holds B's robot key now, because B signed a nonce
A made up a moment ago, and after step 3 B knows the same of A. Each signature covers both ids and
both nonces, so neither can be replayed into another session.

### What a hello carries beside the passport

Steps 1 and 2 also carry three things that change while a robot runs, and which its owner
therefore never signs. The robot key signs them, with the rest of the message:

| Member | What it is |
|---|---|
| `software` | what the robot runs, `quackd/0.29.0` say |
| `beacon` | the beacon id the robot advertises right now |
| `reach` | how to send to it on a LAN: `http_port`, the port of its inbox |

A first `hello` that arrives over the LAN is answered only when its `beacon` was heard on the radio
in the last 8 seconds, so the LAN can carry talk but never decides who is near. A `hello` over a
Bluetooth link was heard by being there.

### After the hello

Every later message carries `nonces: [nA, nB]`, and a receiver refuses one whose nonces match no
session of its own, so freshness needs no clock. A passport's validity does, which is why a robot
whose clock reads earlier than its own passport's `issued` stops talking. quackd ends a session after
ten quiet minutes, forgets a `hello` not finished in thirty seconds, and holds at most 64 sessions and
64 half-finished hellos, so a flood of hellos from fresh keys is a flood of `429`s, not of memory.

## say and bye

`say` carries 1 to 400 characters, the cap quackd's flock messages have always had. It is never an
instruction: a stranger's `say` never reaches a brain, and a known robot's reaches it in quotes, as
words somebody said ([security.md](security.md#prompt-injection)). `bye` ends the session, and a
robot whose signal falls away says it before it closes the link.

## ask and answer

An `ask` names a skill, its arguments and, optionally, why:

```json
{"skill": "pause", "args": {"seconds": 5}, "why": "passing on your left"}
```

1.0 defines two courtesy skills, the ones a passport lists under `courtesy`:

| Skill | Arguments | What the receiver does if it agrees |
|---|---|---|
| `pause` | `seconds`, 1 to 10 | stops moving for that long, then carries on with its own plan |
| `identify` | `how`, optional: `light`, `sound` or `motion` | makes itself known: a blink, a beep, a small wiggle |

The receiver replies with an `answer`: `ok`, `no` or `later`, and a short reason.

The asker never decides anything. Every `ask` is decided by the receiver's own policy, and the
specification puts four duties on every receiver, whoever made it:

- Nothing that moves the robot may happen because of an `ask`, except through a rule that bounds
  the action in time and in how often it may happen.
- An `ask` is never handed to a language model as an instruction.
- No `ask` can keep the robot from stopping. Its own stop stays above any peer's request.
- It answers within five seconds, or not at all.

### What quackd does with an ask

quackd's handler is fixed code, not the brain. It looks up the asker's tier
([trust.md](trust.md#the-tiers)), checks the skill is on that tier's list and that the global budget
allows it, and only then runs it through the executor with the source `peer`, so every gate the
executor has still applies. `pause` ends whatever verb is moving the body, becomes the executor's own
stop, and opens a short window in which nothing that moves is admitted. `identify` becomes the body's
sound when it has one. Two gates hold a peer: `peer`, which admits no verb a peer started but the
`stop` and `say` a courtesy runs, and `paused`, which admits nothing that moves while a pause holds
([concepts/safety.md](../concepts/safety.md)).

A courtesy needs a goal or a meeting that has the body, so outside one every `ask` is answered `no`.
A peer never calls `stop`, and nothing it asks can outlast the robot's own stop. The handler answers
within the five seconds the specification allows, `later` when the executor has not finished in
four. A brain hears of an `ask` as information only: a stranger's in the words a courtesy is asked
in and no others, never its `why`, and a known robot's as it was sent, JSON-quoted.

## share

`share` tells another robot something it saw:

```json
{"about": "parcel", "fact": "left by the blue door", "place": "the front door", "age_s": 12, "confidence": "estimate"}
```

It names a place, never a coordinate: two robots that have just met share no frame of reference,
which quackd learned building its own flocks ([ADR-0020](../adr/0020-heterogeneous-flocks.md)), and
"the front door" means the same to both. A stranger's `share` is withheld from a brain whole, as its
`say` is.

## Meetings, and a robot that is busy

What a robot says is up to its brain. A robot with nothing to do holds a **meeting** with a robot
that came close: a short run on the standing task in `robot.yaml`, which can only report the robot's
state, stop it, and speak with `tell`. A robot already on a goal does not stop for a newcomer, which
appears in that run's `Robots nearby` instead. Either way the messages are the same six
([quackd.md](quackd.md#meeting-another-robot)).

## Versions

A receiver that cannot read a message's version answers `answer` with `result` `no`, `reason`
`version` and `versions` listing what it does read, the way MCP servers answer a client that speaks
a version they do not ([spec section 6.7](spec.md#67-versions)). A 1.0 robot answers a 0.1 robot
that way, with `versions` `["1.0"]`.

## Lost and repeated messages

Delivery is at least once. A message can arrive twice, and `msg` is how a receiver drops the second
copy. A message can also not arrive at all, and nothing retries on its own: an `ask` that gets no
`answer` within five seconds was not granted. A session that hears nothing for ten minutes is over,
and the next conversation starts with a new `hello`.
