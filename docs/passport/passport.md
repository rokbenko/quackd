# The passport, member by member

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation, and [roadmap.md](roadmap.md) says what of it
> is built and what has run.

A passport is one small JSON document about a robot, signed by its owner: who it is, whose it is,
the computer it runs on, what it can and cannot do, how a person stops it, and until when its owner
vouches for all of it. It travels inside the `hello` two robots open with, and on a LAN it is also
at the well-known URL ([discovery.md](discovery.md)). This page explains every member;
[the specification](spec.md#2-the-passport-document) is what decides them.

## The smallest passport

Six members are required, and this is all of them, [vectors/minimal.json](vectors/minimal.json):

```json
{
  "passport": "1.0",
  "id": "did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd",
  "name": "g1-edu-01",
  "owner": {
    "id": "did:key:z6MkhFwXNFWosLeugvSf4wcL9t3uuRXueGSFTRgSvHhWj5G2"
  },
  "issued": "2026-10-28T00:00:00Z",
  "expires": "2027-10-28T00:00:00Z"
}
```

It is 240 bytes written compactly, the way quackd signs it. A reader learns there is a robot called
`g1-edu-01`, that its key is the `id`, that the key in `owner.id` vouches for it, and for how long.
Everything else is optional, and a robot adds what its owner can say honestly.

## A full one

This is a humanoid the way the sim board portrays it: a mock Unitree G1 EDU, with two of the figures
Unitree publishes, read on 2026-10-28, an owner, and the `board` a laptop writes. It is
[vectors/humanoid.json](vectors/humanoid.json), and 920 bytes as its owner signed it:

```json
{
  "passport": "1.0",
  "id": "did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd",
  "name": "g1-edu-mock",
  "maker": {"vendor": "unitree", "model": "g1-edu"},
  "owner": {
    "id": "did:key:z6MkhFwXNFWosLeugvSf4wcL9t3uuRXueGSFTRgSvHhWj5G2",
    "label": "Example lab",
    "contact": "https://example.org/robots/g1-edu-mock"
  },
  "board": {"kind": "sim", "model": "a computer standing in for a robot's own"},
  "body": {
    "embodiment": "humanoid",
    "mobility": "legged",
    "manipulator": "arms",
    "terrain": "indoor",
    "tethered": false,
    "mass_kg": [35, "official", "unitree.com/g1, read 2026-10-28"],
    "height_m": [1.32, "official", "unitree.com/g1, read 2026-10-28"],
    "sensors": ["camera", "lidar", "microphone", "battery"]
  },
  "skills": ["report_state", "stop"],
  "courtesy": [],
  "cannot": ["move: a mock on a laptop, and no G1 is here"],
  "safety": {"stops_on_silence": false, "estop": "none", "how_to_stop": "Simulated: stop the process."},
  "issued": "2026-10-28T00:00:00Z",
  "expires": "2027-10-28T00:00:00Z"
}
```

Read top to bottom: this is `g1-edu-mock`, a Unitree G1 EDU by its maker's model name, owned by
Example lab, reachable at that page. It runs on a computer standing in for a robot's own, so it is a
simulation, and every robot that reads it knows. It is a legged humanoid with arms, rated for
indoors, 35 kg and 1.32 m tall by Unitree's own page. It can report its state and stop, and nothing
else, because it is a mock, and it says so in `cannot`. Its owner signed it on 28 October 2026 for a
year.

## Every member

### `passport`

The protocol version, `"1.0"` for this one. Like `openapi` at the top of an OpenAPI file, it names
the document and its version in one place. A 1.0 reader reads any later 1.x passport too, ignoring
what it does not know, and refuses a MAJOR it has never heard of: a 0.1 passport, from before owners
signed passports, among them ([versioning](spec.md#7-versions-of-this-specification)).

### `id`

The robot key: a `did:key`, `did:key:z6Mk` and 44 more characters that encode an Ed25519 public key
and resolve without asking anybody. The robot keeps the matching private key and proves it holds it
in every `hello` ([trust.md](trust.md)).

### `name`

For people, up to 64 characters. It need not be unique, and a reader never treats a name as proof
of anything, or as an instruction.

### `owner`

Required, with its `id`: the owner key, the one that signs the passport, never the robot's own. A
`label` of up to 64 characters and a `contact` of up to 200, a URL, a `mailto:` address or a role
address, say who looks after the robot without needing personal data
([privacy.md](privacy.md#what-to-put-in-owner)). A label is the owner's own word, which is why a
robot that pins an owner pins its key.

### `board`

The computer the robot runs its passport on. `kind` is `jetson`, `sim` or `other`, read as `other`
when a reader does not know it; `model` names the board, up to 64 characters; `soc` names its chip,
up to 32. The robot's software writes it from the board it found, and no owner types it, so a
passport written on a laptop says `sim` and never `jetson`, and a brain that meets a simulated robot
is shown that it is one.

### `maker`

`vendor`, `model` and an optional `serial`, each up to 64 characters. They are the owner's claims
about the robot until a maker signs its own ([roadmap.md](roadmap.md)). A serial number identifies
one physical robot for as long as it exists, so [privacy.md](privacy.md) says to leave it out unless
somebody needs it.

### `body`

What the robot is made of, as data another robot can plan around.

| Member | Values |
|---|---|
| `embodiment` | `biped`, `quadruped`, `humanoid`, `wheeled`, `tracked`, `arm`, `aerial`, `other` |
| `mobility` | `none`, `legged`, `wheeled`, `tracked`, `flying`, `other` |
| `manipulator` | `none`, `beak`, `gripper`, `arms`, `other` |
| `terrain` | `indoor_flat`, `indoor`, `outdoor`, `other`: what it is rated for |
| `tethered` | `true` when a cable powers it, so there is a cable on the floor |
| `sensors` | `camera`, `microphone`, `imu`, `odometry`, `tof`, `lidar`, `battery`, `joint_state`, or any other name |

A value a reader does not know reads as `other`, and an unknown sensor stays a name the reader shows
without interpreting it, so a robot from next year never makes a passport unreadable.

Then the numbers: `mass_kg`, `height_m`, `length_m`, `width_m`, `payload_kg`, `reach_m`,
`endurance_min` and `max_speed_mps`. Each is a **figure**: a plain number, or a number with how sure
the writer is of it, or that and a source, `[35, "official", "unitree.com/g1, read 2026-10-28"]`.
The confidence is `official` (the maker or a paper says so), `estimate` or `measured`, the three
words quackd's datasheet has used since [ADR-0032](../adr/0032-datasheets-and-the-verdict.md), for
the same reason: a number without a source is a rumour. An absent figure is **not published**, never
zero. Units are SI and live in the member's name, and `max_speed_mps` is the fastest the robot moves
as its software is set up now.

### `skills`

What the robot can do, as names. Eight mean the same on every robot that lists them, quackd's core
verbs: `stop`, `observe`, `report_state`, `move`, `go_to`, `search_scan`, `approach_and` and `say`
([spec section 2.4](spec.md#24-skills-and-courtesy)). Any other name is a label a reader may show and
never interprets. A robot whose software can stop it should list `stop`.

### `courtesy`

What another robot may ask this one to do: `pause` (stop moving for a few seconds, then carry on)
and `identify` (blink, beep or wiggle, so the people and cameras nearby can tell which robot this
is). It is apart from `skills` on purpose: `skills` says what the robot can do, `courtesy` what it
will consider doing because a peer asked, and its owner's policy decides
([talk.md](talk.md#ask-and-answer)). The mock above offers none.

### `cannot`

Up to eight sentences of up to 120 characters: what the robot cannot do, whatever a task asks. They
stop another robot asking for what this body cannot do, the way quackd's datasheet stops a pilot
accepting it.

### `safety`

| Member | Meaning |
|---|---|
| `stops_on_silence` | `true` when the robot's motion stops by itself once its controller goes quiet |
| `estop` | `button` (a stop button on the body), `remote` (a separate stop device), `power` (only by cutting the power), or `none` |
| `how_to_stop` | one sentence for a person standing next to it |

This is the one place a reader reads the worst case. A missing, unknown or garbled value reads as
"does not stop on silence", "no e-stop" and "nobody said how", so a mistake never makes a robot look
safer than its owner said, and the passport is still read rather than refused, because hiding a
robot is not safer either.

### `ext`

Extensions, keyed by a reverse-DNS name so two makers' extensions never collide, `com.example.lift`
say. A reader ignores one it does not know, unless it is marked `"required": true`, and then it may
still show the passport but must not talk to the robot.

### `issued` and `expires`

When the owner signed the passport, and until when the signature holds: RFC 3339 date-times, both
required, `expires` after `issued` and at most 400 days later. quackd signs for 365 days unless
`--days` says otherwise. A reader refuses a passport whose `expires` has passed, or whose `issued` is
more than five minutes ahead of its own clock, so a robot needs a clock that is roughly right
([trust.md](trust.md#the-owner-signs-the-passport)). With no revocation in 1.0, `expires` is also
what limits a stolen robot key.

### Members 1.0 removed

`software`, which changes with every upgrade, moved into the `hello`, where the robot key signs it
([talk.md](talk.md#what-a-hello-carries-beside-the-passport)). `talk` went, since a robot is reached
the way its beacon says. `stamps` went, since the owner signs the passport itself. `ttl_s` went, since
a reader caches a passport until it `expires`. A 1.0 reader ignores all four as unknown members.

## Sizes at a glance

Measured from [the vectors](vectors/README.md).

| What | Size |
|---|---|
| the minimal passport above, compact | 240 bytes |
| the mock G1 EDU's full passport, as its owner signed it | 920 bytes |
| that, signed, as a `.jws` | 1,461 characters |
| what a writer should stay under | 2,048 bytes |
| the most a reader must accept | 8,192 bytes |
| the most a reader reads of one off the network, signed | 12,288 bytes |

## What a passport leaves out on purpose

- **Live state.** Where the robot is, its battery, what it is doing now. A passport describes the
  robot and changes when the robot does. What changes while it runs, its software and its beacon id,
  travels in the `hello`.
- **Coordinates.** Two robots that have just met share no frame of reference
  ([ADR-0020](../adr/0020-heterogeneous-flocks.md)).
- **Personal data.** Nothing about a person is required, and [privacy.md](privacy.md) says what to
  leave out.
- **Secrets.** No token, no password, no private key, ever. A passport is shown to any robot that
  says hello.
- **Free-form descriptions.** There is no "about" paragraph: text a stranger wrote is text another
  robot's language model might read, so every member is short and bounded.

## From the robot's file

<a id="from-a-quackd-manifest-to-a-passport"></a>

quackd builds the passport from the passport section of the robot's `robot.yaml`, which the wizard
wrote and the owner signs ([quackd.md](quackd.md#robotyaml-the-one-file)):

| `robot.yaml` | Passport |
|---|---|
| `passport.name` | `name` |
| `passport.maker` | `maker` |
| `passport.owner`, its label and contact | `owner.label`, `owner.contact` |
| `passport.body`, every figure as `[value, confidence, source]` | `body` |
| `passport.skills`, `passport.cannot`, `passport.safety` | `skills`, `cannot`, `safety` |
| the robot key the wizard made | `id` |
| the owner key that signs | `owner.id` |
| the board quackd found | `board` |
| the moment of signing, and `--days` | `issued`, `expires` |

When `robot.adapter` names a body quackd drives, the adapter's manifest supplies the skills, the way
quackd built passports from manifests since 0.23.0, and a `cannot` line longer than 120 characters
keeps its claim, the part before its explanation. A body with no adapter, which only talks, offers
no courtesy.
