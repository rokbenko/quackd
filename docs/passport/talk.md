# Talk: what two robots say once they have met

> [!NOTE]
> Spec draft 0.1. quackd speaks step one, `hello`, `say` and `bye`, over an HTTP inbox, which
> has run between processes on one machine ([quackd.md](quackd.md#talking)). `ask`, `answer`,
> `share` and the courtesy handler are being built for 0.23.0, and [roadmap.md](roadmap.md) says
> what of it is built and what has run.

Reading each other's passports is already an exchange, and for many robots it is all they need.
Talk is the optional layer above it: six small messages for two robots that want to prove who they
are, say something, share what they saw, or ask each other for a courtesy. The specification's
own words are in [section 6](spec.md#6-talk).

Talk adds no transport. A message is a small JSON object, signed when the sender holds a key, and it
travels over a channel both robots already speak: an HTTP inbox, an MQTT topic, and later a ROS 2
topic or an A2A message. Robots that share a richer channel, two quackd hosts on one flock bus for
example, can move to it after their `hello`; the six messages are a floor every robot can stand on,
not a ceiling.

## The six messages

| Type | Carries | For |
|---|---|---|
| `hello` | a passport and a nonce | proving who each side is, in three steps |
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
| `from` | the sender's `id` |
| `to` | the receiver's `id`, or `*` for anyone on a shared channel |
| `msg` | a random token, unique per sender, so a repeat is recognised and dropped |
| `re` | the `msg` this one replies to |
| `nonces` | the session's two nonces, after a `hello` |
| `body` | what the type carries |

A signed message is a compact JWS of that object, with `typ` `robot-passport-msg+jws` in its header.
The `typ` matters: a robot signs passports, stamps and messages with one key, and the three `typ`s
mean a signature made for one of them can never pass as another. A message is at most 4,096 bytes,
or what the receiving channel's `max_bytes` says, except a `hello`, which carries a whole passport
and may be up to 16,384 bytes.

A robot that holds no key sends plain JSON, and only `hello`, `say`, `share` and `bye`. A robot
that holds one signs everything it sends, so an unsigned message naming a `did:key` is refused:
anybody can write an id. An `ask` must be signed, always.

## Channels

A robot's passport lists where it listens, in `talk`.

**HTTP.** The sender POSTs one message to the inbox URL and reads the reply in the response:

| Response | Means |
|---|---|
| `200` | one reply message in the body |
| `204` | received, no reply |
| `400` | malformed |
| `403` | refused by the receiver's policy |
| `413` | too large |
| `429` | a budget is spent |

Because the reply rides back on the response, a robot that runs no server of its own can still
talk to one that does.

**MQTT.** The sender publishes to `robot-passport/<key>/inbox` at QoS 1, not retained, and replies
go to the sender's own inbox ([discovery.md](discovery.md#mqtt)).

**ROS 2 and A2A** are reserved for later versions. A sender skips any channel it does not know.

## hello: proving who you are, in three steps

A signed passport proves a key wrote it. It does not prove that the robot answering right now holds
that key: anybody can replay a passport they fetched. `hello` closes that gap, in three messages:

```
 A                                                     B
 |  1  hello  {passport: A's JWS, nonce: nA}           |
 | --------------------------------------------------> |
 |                                                     |
 |  2  hello  re 1, nonces [nA, nB], {passport: B's}   |   signed by B
 | <-------------------------------------------------- |
 |                                                     |
 |  3  hello  re 2, nonces [nA, nB], {}                |   signed by A
 | --------------------------------------------------> |
 |                                                     |
 |        session (A, B, nA, nB): say, ask, share      |
```

A nonce is 16 random bytes. After step 2, A knows B holds B's key now, because B signed a nonce A
made up a moment ago. After step 3, B knows the same of A. Each signature covers both ids and both
nonces, and the message's `typ` and `type` say exactly what it is, so neither signature can be
replayed into another session or passed off as anything else.

Every later message carries `nonces: [nA, nB]`, and a receiver refuses a message whose nonces
match no session of its own. That is where freshness comes from, and it needs no clock, which
matters because many robots have none that survives a reboot. quackd ends a session after ten
minutes without a message.

A `hello` with a robot that holds no key still opens a session, labelled unauthenticated: its
`say` is shown as a stranger's words, and it can never `ask`.

## say and bye

`say` carries 1 to 400 characters, the same cap quackd's flock messages have always had. It is for
the people watching and for a pilot's language model, and it is never an instruction: a receiver
that shows it to a model shows it as words somebody said, in quotes, never as part of the task
([security.md](security.md#prompt-injection)).

`bye` ends the session. A robot that walks off without one simply times out.

## ask and answer

An `ask` names a skill, its arguments and, optionally, why:

```json
{"skill": "pause", "args": {"seconds": 5}, "why": "passing on your left"}
```

0.1 defines two courtesy skills, the ones a passport lists under `courtesy`:

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

quackd's handler is fixed code, not the pilot. It looks up the asker's tier
([trust.md](trust.md#the-four-tiers)), checks the skill is on that tier's list and that the global
budget allows it, and only then runs it through the executor, with the source `peer`, so every
gate the executor already has still applies. `pause` becomes the executor's own stop, followed by
a short window in which nothing that moves is admitted. `identify` becomes the body's sound when it
has one. Anything else a peer asks for reaches the pilot as information only, the way a passer-by
might mention something, and the pilot's own task decides whether it matters. A peer never calls
`stop`, and nothing a peer asks can outlast or override the robot's own stop.

## share

`share` tells another robot something it saw:

```json
{"about": "ball", "fact": "seen under the table", "place": "kitchen door", "age_s": 12, "confidence": "estimate"}
```

It names a place, never a coordinate. Two robots that have just met share no frame of reference,
so "x 1.2, y 0.4" in one robot's frame means nothing in the other's, which quackd learned building
its own flocks ([ADR-0020](../adr/0020-heterogeneous-flocks.md)). "The kitchen door" means the
same to both, and to the people listening.

## Versions

A receiver that cannot read a message's version answers `answer` with `result` `no`, `reason`
`version` and `versions` listing what it does read, the way MCP servers answer a client that
speaks a version they do not ([spec section 6.7](spec.md#67-versions)).

## Lost and repeated messages

Delivery is at least once. A message can arrive twice, and `msg` is how a receiver drops the
second copy. A message can also not arrive at all, and nothing retries on its own: an `ask` that
gets no `answer` within five seconds was not granted. A session that hears nothing for ten minutes
is over, and the next conversation starts with a new `hello`.
