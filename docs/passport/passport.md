# The passport, member by member

> [!NOTE]
> Spec draft 0.1. quackd's implementation is being built for 0.23.0, and
> [roadmap.md](roadmap.md) says what of it is built and what has run.

A passport is one small JSON document a robot publishes about itself: who it is, who vouches for
it, what it can and cannot do, how a person stops it, and how another robot reaches it. It sits at
`/.well-known/robot-passport.json` on the robot's own HTTP server, or anywhere a QR sticker, a
DNS-SD record, a ROS 2 topic or an MQTT broker points ([discovery.md](discovery.md)). This page
explains every member; [the specification](spec.md#2-the-passport-document) is what decides them.

## The smallest passport

Three members are required, so the smallest valid passport is 99 bytes:

```json
{"passport":"0.1","id":"did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd","name":"duck-01"}
```

That is already enough to be met: a reader learns there is a robot called `duck-01`, and the `id`
is a public key, so the robot can later prove it holds the matching private key. Everything else
is optional, and a robot adds what it can say honestly.

## A full one

This is a Microduck, exactly as quackd builds it from its manifest, with an owner, an e-stop and
an inbox added. It is 1,218 bytes of compact JSON, about a sixth of the 7,703 bytes the manifest
itself takes, and it is [vectors/microduck.json](vectors/microduck.json):

```json
{
  "passport": "0.1",
  "id": "did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd",
  "name": "duck-01",
  "maker": {"vendor": "pollen-robotics", "model": "microduck"},
  "software": {"name": "quackd", "version": "0.23.0"},
  "owner": {"label": "Example lab", "contact": "https://example.org/robots/duck-01"},
  "body": {
    "embodiment": "biped",
    "mobility": "legged",
    "manipulator": "beak",
    "terrain": "indoor_flat",
    "tethered": false,
    "mass_kg": [0.8, "official", "the Pollen Robotics README"],
    "height_m": [0.25, "official", "the Pollen Robotics README"],
    "max_speed_mps": 0.3,
    "sensors": ["camera", "battery", "odometry", "imu", "tof"]
  },
  "skills": ["report_state", "stop", "say", "move", "observe", "go_to", "search_scan",
             "approach_and", "sit", "stand", "kick", "grab", "stand_up", "quack", "gaze",
             "watch"],
  "courtesy": ["pause", "identify"],
  "cannot": [
    "carry, hold or push anything: the beak scoops at the floor and nothing else",
    "climb or descend a step",
    "hold a heading for long without a landmark: the IMU has no magnetometer, so heading drifts"
  ],
  "safety": {"stops_on_silence": true, "estop": "power", "how_to_stop": "the power switch"},
  "talk": [{"via": "http", "url": "http://192.0.2.10:8790/robot-passport/inbox"}],
  "ext": {"org.quackd": {"sha": "dc5ab18e68dd6ca5"}},
  "issued": "2026-10-21T00:00:00Z"
}
```

Read top to bottom, it says: this is `duck-01`, a Microduck from Pollen Robotics running quackd
0.23.0, owned by Example lab, who can be reached at that page. It is a legged biped with a beak,
rated for flat indoor floors and running on its battery, 0.8 kg and 25 cm tall by its maker's own
README, and it moves at 0.3 m/s at most. It can see, speak, walk, kick, scoop and look around. It
cannot carry anything, climb a step or hold a heading for long. Its motion stops by itself when
its controller goes quiet, and a person stops it at the power switch. Another robot can ask it to
pause or to identify itself, and can talk to it at that inbox. `org.quackd` carries its quackd
manifest's digest, for another quackd host that has the same adapter.

## Every member

### `passport`

The protocol version, `"0.1"` for this one. Like `openapi` at the top of an OpenAPI file, it names
the document and its version in one place. A reader of 0.1 reads any later 0.x passport too,
ignoring what it does not know, and refuses a MAJOR it has never heard of
([versioning](spec.md#7-versions-of-this-specification)).

### `id`

Who the robot is, as a key rather than a name. It is a `did:key`: `did:key:z6Mk` and 44 more
characters, 56 in all, that encode an Ed25519 public key and resolve without asking anybody. The
robot keeps the matching private key, signs its passport with it, and proves it holds it whenever
another robot asks ([trust.md](trust.md)).

A robot that holds no key, a robot with nothing but a sticker for example, uses a `urn:uuid`
instead. Its passport can be read and shown, and that is all: it cannot be signed, stamped or
pinned, and its robot cannot ask another for anything.

### `name`

For people, up to 64 characters. It need not be unique, and quackd fills it with the robot's
registered name. A reader never treats a name as proof of anything, and never as an instruction.

### `maker` and `software`

`maker` holds `vendor`, `model` and an optional `serial`. `software` holds the `name` and `version`
of what drives the robot. Each is up to 64 characters, and both are claims: until a maker signs
its own stamps ([roadmap.md](roadmap.md)), a reader labels them as said by the robot itself. A
serial number identifies one physical robot for as long as it exists, so [privacy.md](privacy.md)
says to leave it out unless somebody needs it.

### `owner`

A `label` of up to 64 characters and a `contact` of up to 200: a URL, a `mailto:` address or a role
address such as `robots@example.org`. Both are optional and neither needs personal data. A page
that says how to reach whoever looks after the robot does the job, and it can change without the
passport changing. `owner` is also what returns a lost robot, the same way a tag on a collar does.

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

A value a reader does not know reads as `other`, and an unknown sensor stays as a name the reader
shows without interpreting it, so a robot from next year never makes a passport unreadable.

Then the numbers: `mass_kg`, `height_m`, `length_m`, `width_m`, `payload_kg`, `reach_m`,
`endurance_min` and `max_speed_mps`. Each is a **figure**: a plain number, or a number with how
sure the writer is of it, or that and a source:

```json
"mass_kg": 0.8
"mass_kg": [0.8, "official"]
"mass_kg": [0.8, "official", "the Pollen Robotics README"]
```

The confidence is `official` (the maker or a paper says so), `estimate` (somebody worked it out) or
`measured` (somebody measured it). These are the same three words quackd's datasheet has used since
[ADR-0032](../adr/0032-datasheets-and-the-verdict.md), for the same reason: a number without a
source is a rumour. A figure that is absent is **not published**, which is not the same as zero,
and a reader never treats it as zero. Units are SI and live in the member's name.

`max_speed_mps` is the fastest the robot will move as its software is set up now, which is what
another body on the same floor needs. For a quackd robot it is the speed quackd clamps to.

### `skills`

What the robot can do, as names. Eight names mean the same on every robot that lists them:

| Core skill | Meaning |
|---|---|
| `stop` | it stops all its motion |
| `observe` | it sees, and can report what it sees |
| `report_state` | it reports its own state |
| `move` | it moves its body by a velocity |
| `go_to` | it moves itself to something it sees |
| `search_scan` | it looks around for something |
| `approach_and` | it goes to something and acts on it |
| `say` | it makes a sound or speech a person can hear |

They are quackd's core verbs, renamed nowhere. Any other name (`kick`, `gaze`, `pick`) is a label
a reader may show and never interprets, because nothing says what one maker's `kick` means to
another. A robot whose software can stop it should list `stop`.

### `courtesy`

What another robot may ask this one to do: `pause` (stop moving for a few seconds, then carry on)
and `identify` (blink, beep or wiggle, so the people and cameras nearby can tell which robot this
is). It is kept apart from `skills` on purpose. `skills` says what the robot can do; `courtesy`
says what it will consider doing because a peer asked, and whether it actually does is decided by
its owner's policy, never by the asker ([talk.md](talk.md#ask-and-answer)). The word is `pause`
rather than `hold` because one robot quackd drives already has a motion called `hold`.

### `cannot`

Up to eight sentences of up to 120 characters: what the robot cannot do, whatever a task asks.
quackd copies them from the datasheet, where they were written to stop a pilot accepting a task the
body cannot do, and here they stop another robot asking for one.

### `safety`

| Member | Meaning |
|---|---|
| `stops_on_silence` | `true` when the robot's motion stops by itself once its controller goes quiet |
| `estop` | `button` (a stop button on the body), `remote` (a separate stop device), `power` (only by cutting the power), or `none` |
| `how_to_stop` | one sentence for a person standing next to it |

This is the one place a reader reads the worst case. A missing, unknown or garbled value reads as
"does not stop on silence", "no e-stop" and "nobody said how". So a mistake never makes a robot
look safer than its owner said it is, and a passport with a bad `safety` value is still read
rather than refused, because hiding a robot is not safer either.

quackd fills `stops_on_silence` from the manifest's deadman. It leaves `estop` and `how_to_stop`
to the owner, because only the owner knows where the switch is.

### `talk`

Where the robot listens for messages, as a list of channels: `{"via": "http", "url": ...}` or
`{"via": "mqtt", "url": ...}`, with an optional `max_bytes` for the largest message it accepts.
[talk.md](talk.md) is the whole of it. A robot that never talks leaves this out.

### `stamps`

Up to four stamps, each a short signed statement from another key: "this robot is mine", signed
by its owner. [trust.md](trust.md#stamps) explains them, and the specification defines them in
[section 4.5](spec.md#45-stamps).

### `ext`

Extensions, keyed by a reverse-DNS name so two makers' extensions never collide: `org.quackd`,
`com.example.lift`. A reader ignores an extension it does not know, unless it is marked
`"required": true`, and then it may still show the passport but must not talk to the robot,
because something it cannot read is load-bearing.

### `issued` and `ttl_s`

`issued` is when the passport was written, as an RFC 3339 date-time. It is advisory, because many
robots have no clock that survives a reboot. quackd leaves it out of the passports it builds:
stamping the build time would give an unchanged robot a new digest every time, and a reader
caches by digest. `ttl_s` is how long a reader may cache the passport,
counted from the moment the reader fetched it: an hour unless it says otherwise, a day at most.

## The index: several robots behind one host

One quackd host often fronts several robots. Its well-known URL then serves an **index** instead
of a passport, and a reader tells the two apart because an index has `robots` and no `id`:

```json
{
  "passport": "0.1",
  "robots": [
    {"name": "duck-01", "id": "did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd", "url": "/robots/duck-01/robot-passport.json"},
    {"name": "cart-01", "id": "did:key:z6Mkgxj2R3HLtQRpPnvfvpuKEceSqf3tZHBjdmZ3fFz3JHGG", "url": "/robots/cart-01/robot-passport.json"}
  ]
}
```

Every `url` names the same host the index came from, so an index can never send a reader
somewhere else ([discovery.md](discovery.md#fetch-rules)).

## Sizes at a glance

Measured from [the vectors](vectors/README.md).

| What | Size |
|---|---|
| the smallest passport | 99 bytes |
| a Microduck's full passport | 1,218 bytes |
| the same with one owner stamp | 1,713 bytes |
| that, signed, as a `.jws` | 2,518 characters |
| one stamp | 481 characters |
| the most a reader must accept | 8,192 bytes |
| what a writer should stay under | 2,048 bytes |
| the Microduck's quackd manifest, for comparison | 7,703 bytes |

## What a passport leaves out on purpose

- **Live state.** Where the robot is, its battery level, what it is doing now. A passport describes
  the robot, and changes when the robot does; state changes every second and belongs on a channel.
- **Coordinates.** Two robots that have just met share no frame of reference, so a position in one
  robot's frame means nothing to the other ([ADR-0020](../adr/0020-heterogeneous-flocks.md)).
- **Personal data.** Nothing about a person is required, and [privacy.md](privacy.md) says what to
  leave out of the optional members.
- **Secrets.** No token, no password, no private key, ever. A passport is public by design.
- **Free-form descriptions.** There is no "about" paragraph, because text a stranger wrote is text
  another robot's language model might read, and the members are kept to short, bounded strings.

## From a quackd manifest to a passport

quackd builds a passport from a robot's [manifest](../reference/manifest-spec.md), so every body it
drives, and every adapter somebody else writes, has one without anybody typing it.

| Manifest | Passport |
|---|---|
| the registered name, or `id` | `name` |
| `vendor`, `model` | `maker.vendor`, `maker.model` |
| `embodiment`, `mobility`, `sensors` | `body.embodiment`, `body.mobility`, `body.sensors` |
| `datasheet` figures, each with its confidence and source | the `body` figures of the same name |
| `datasheet.manipulator`, `terrain`, `tethered` | `body.manipulator`, `body.terrain`, `body.tethered` |
| `datasheet.cannot`, a line over 120 characters cut to its claim, the part before its explanation | `cannot` |
| the fastest of `limits.max_vx` and `limits.max_vy` | `body.max_speed_mps` |
| every canonical verb name | `skills` |
| `safety_authority.deadman` | `safety.stops_on_silence` |
| `digest()` | `ext.org.quackd.sha` |

What stays in the manifest: `intents`, `preconditions`, `frame`, the verbs' parameter schemas and
safety classes, `blurb`, `backend` and `extras`. They are how quackd drives a body, not what
another robot needs to know about it. The owner, the e-stop and how to stop it are not in the
manifest at all, because they belong to one robot rather than to its model, so they come from the
owner ([quackd.md](quackd.md)).
