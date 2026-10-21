# How it works

> [!NOTE]
> Spec draft 0.1. quackd's implementation is being built for 0.23.0, and
> [roadmap.md](roadmap.md) says what of it is built and what has run.

Robot Passport is three layers, each optional on top of the one below:

```
BEACON    "I'm here, and this is where my passport is"     a QR sticker, mDNS, MQTT, a ROS 2 topic
   │
PASSPORT  who I am, who vouches for me, what I can and     one small JSON document, optionally signed,
          cannot do, how to stop me, how to reach me       with stamps from its owner
   │
TALK      hello · say · bye · ask · answer · share         small signed messages, never obeyed blindly
```

**Reading needs nothing; asking needs a key.** Anybody can read a passport, and a robot with
nothing but a sticker can have one. Proving who you are, and asking another robot for anything,
takes a key and a signature.

## One meeting, step by step

`duck-01` is a Microduck driven by quackd and owned by Example lab. `cart-01` is a wheeled cart
from another maker, on ROS 2, owned by somebody else. They have never met, and they end up in the
same hallway on the same network.

```
 duck-01 (quackd)                                                   cart-01 (another maker)
     │                                                                  │
     │  1  discover: an mDNS answer for _robot-passport._tcp            │  advertises its beacon
     │ <──────────────────────────────────────────────────────────────  │
     │                                                                  │
     │  2  fetch: GET /.well-known/robot-passport.jws                   │
     │ ──────────────────────────────────────────────────────────────>  │
     │ <──────────────────────────────────────────────────────────────  │  its signed passport
     │                                                                  │
     │  3  verify the signature, check the stamps, pick a tier          │
     │  4  show the pilot what it may see, and log the rest             │
     │  5  a person may pin it, so next time it is known                │
     │                                                                  │
     │  6  hello (3 messages), then say, share, ask                     │
     │ <─────────────────────────────────────────────────────────────>  │
     │                                                                  │
     │  7  bye, or ten minutes of silence                               │
```

1. **Discover.** `cart-01` advertises `_robot-passport._tcp` on the local network, with its short id
   and its passport's digest in the TXT record ([discovery.md](discovery.md)). duck-01's quackd host
   hears it. It could equally have read a QR sticker on the cart, or found it on a shared MQTT broker.
2. **Fetch.** quackd fetches the passport from the host and port that advertised it, follows no
   redirects and reads no more than 8 KB ([fetch rules](discovery.md#fetch-rules)).
3. **Verify and place.** It checks the signature against the key in the passport's `id`, counts any
   stamps from keys it has pinned, and decides the cart's tier: a stranger, a known robot, one with
   the same owner, or a member of its own flock ([trust.md](trust.md#the-four-tiers)).
4. **Show.** duck-01's pilot sees the cart in its next observation, under robots nearby. For a
   stranger that is only what has a closed vocabulary: a wheeled cart, no manipulator, how fast it
   goes, what it can do. Its free text goes to the log, for a person ([security.md](security.md#prompt-injection)).
5. **Remember.** If a person decides the cart can be trusted, they pin its key, and the next meeting
   starts at "known".
6. **Talk.** Either robot can start a `hello`. After three messages each knows the other holds its
   key right now, and the session is open: `say`, `share`, and an `ask` for a courtesy such as
   "pause for five seconds while I pass" ([talk.md](talk.md)). Whether the cart pauses is the cart
   owner's policy, never duck-01's.
7. **Part.** `bye` ends the session, or ten minutes of silence does.

Steps 1 to 5 are the whole of version 0.1's first two layers, and they are already a complete
exchange: each robot that reads the other's passport has learned its name, its body, its owner and
how to stop it. Step 6 is for robots that want more.

## What each side learns, and when

| After | duck-01 knows | It does not yet know |
|---|---|---|
| discovery | something at that address claims a short id and a digest | anything about it, or whether the claim is true |
| the fetch | what the passport says | whether anybody vouches for it |
| verification | key K wrote this passport, and whether a key it pinned stamped K | whether the robot at that address holds K right now |
| `hello` | the robot it is talking to holds K right now | which physical body in the hallway that is |
| `identify` | which body it is: the one that just beeped | anything the passport did not say |

## What a person sees

- **Scanning a sticker**, today: the passport's JSON in the phone's browser. A page that renders any
  passport for a person is on the [roadmap](roadmap.md).
- **On a quackd host**: `quackd meet` shows a passport the way a reader sees it, every claim labelled
  with where it came from, and asks before it pins anything ([quackd.md](quackd.md)).
- **In a run's log**: each robot met, each message that passed, and every `ask` with what the
  handler decided, the same way the log already shows everything else a run did.

## The life of a passport

| Moment | What happens |
|---|---|
| created | quackd builds it from the robot's manifest, or somebody writes the JSON by hand |
| signed | the robot's key signs its exact bytes, and the result is a `.jws` |
| stamped | the owner's key adds a stamp: "this key is my robot", for a year at most |
| published | it is served at the well-known URL, printed on a sticker, put on a broker or a topic |
| updated | the robot changes: a new passport, a new digest, readers refetch it; the stamp still holds |
| expired | a reader's cached copy ages out after `ttl_s`; a stamp ages out at its `exp` |
| handed on | the robot changes owners: a new key, the new owner's stamp, the old stamp left to expire |
| retired | it stops advertising and its passport comes down; pins of it stay until a person removes them |

## When something goes wrong

| What fails | What a reader does |
|---|---|
| the fetch times out or the host refuses | nothing: no passport, no meeting, and it tries again on the next beacon |
| the signature does not verify | refuses the passport, and logs that somebody served a bad one |
| the version's MAJOR is unknown | refuses it, and says which version it saw |
| a required extension it does not understand | shows the passport, and talks to nobody on its strength |
| a garbled `safety` value | reads the worst case, and still shows the passport |
| the robot's clock is wrong | nothing: freshness comes from nonces, caches count from the reader's own clock |
| the key changed since it was pinned | trusts nothing automatically, and asks a person, the way SSH does |
| an `ask` gets no `answer` within five seconds | treats it as refused |
| the network drops in the middle of a `hello` | the session never opens, and the next attempt starts over |
| two robots share a name | nothing: names are for people, and keys are what tell robots apart |
