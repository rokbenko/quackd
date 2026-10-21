# Compatibility: any robot can carry a passport

> [!NOTE]
> Spec draft 0.1. quackd's implementation is being built for 0.23.0, and
> [roadmap.md](roadmap.md) says what of it is built and what has run. The Raspberry Pi, ESP32 and
> ROS 2 recipes and the translators below are planned, not written, and this page says so beside
> each one.

Robot Passport is meant to be the easiest thing a robot ever implements. The smallest version needs
no software on the robot at all, every step up is optional, and robots that never implement any of
it can still be read through a translator.

## The ladder

| Level | The robot does | It needs | Example |
|---|---|---|---|
| **0, Tag** | nothing: a sticker points to a passport somebody else hosts | a URL and a printer | any robot sold today, old ones included |
| **1, Beacon** | advertises and serves its own passport | an HTTP server, and mDNS, MQTT or ROS 2 | a Raspberry Pi, an ESP32, a quackd host |
| **2, Signed** | holds a key, signs its passport, answers a `hello` | Ed25519 and base64url | anything with Python's `cryptography`, or a C library like Monocypher |
| **3, Talk** | accepts `say` and `ask`, and answers | a listener and a policy | quackd pilots, or any robot that maps a courtesy onto its own actions |

Each level includes the ones below it, and a reader treats every level the same way: it reads the
passport, labels it with what it could verify, and moves on. A level 0 robot is as visible as a
level 3 one; it just cannot prove as much.

## Recipes

### Any robot, with no software: a sticker

This works today, for any robot, with nothing installed on it.

1. **Write the passport.** A robot with no software holds no key, so its `id` is a UUID. The three
   required members are enough, and everything else is optional:

   ```json
   {
     "passport": "0.1",
     "id": "urn:uuid:4f1c2a1e-9d4b-4c8e-8f3a-2b6d5e7a9c10",
     "name": "vacuum-01",
     "maker": {"vendor": "example-robotics", "model": "sweeper-3"},
     "owner": {"label": "Example lab", "contact": "https://example.org/robots"},
     "body": {"embodiment": "wheeled", "mobility": "wheeled", "manipulator": "none"},
     "cannot": ["climb or descend a step"]
   }
   ```

   Make your own UUID rather than copying this one. Any UUID generator does it, and so does
   `python -c "import uuid; print(uuid.uuid4())"`.

2. **Host it** anywhere that serves a file over HTTPS: a company web page, a GitHub Pages site, a
   file host. Call it `robot-passport.json`, or put it at `/.well-known/robot-passport.json` if you
   control a domain.

3. **Make a QR code of its URL** with any QR code generator, and stick it on the robot. A URL of
   about 60 characters makes a code small enough for a 25 cm robot.

4. **Check it** against [the JSON Schema](schema/passport.schema.json) with any JSON Schema
   validator, or with quackd ([quackd.md](quackd.md)).

That is a level 0 robot. A person who scans it learns whose robot it is and how to reach them, and
a robot with a camera that reads it learns what it shares the room with.

### A Raspberry Pi, or any Linux robot (planned)

A single-file server using only Python's standard library, in the same spirit as the daemons quackd
already puts on robots, serving the well-known URL and registering it with the Avahi daemon that
Raspberry Pi OS already runs (the daemon that makes `raspberrypi.local` work). It is on the
[roadmap](roadmap.md), and until it has run on a Pi this page shows no code for it.

### An ESP32 (planned)

An HTTP server and the ESP-IDF's mDNS component are all a level 1 ESP32 needs, and Ed25519 fits in
its memory for level 2. The library is on the [roadmap](roadmap.md), HTTP first and Bluetooth once
beacon ids can rotate ([privacy.md](privacy.md#tracking)).

### A ROS 2 robot (planned)

One publisher on `robot_passport`, `std_msgs/msg/String`, `TRANSIENT_LOCAL`
([discovery.md](discovery.md#ros-2)). It needs no new message type and no new dependency. A small
node that publishes a passport from a file is on the [roadmap](roadmap.md). quackd itself reads the
topic through rosbridge when it is connected to a ROS robot.

### A robot on an MQTT broker

Publish the passport retained at QoS 1 on `robot-passport/<key>/passport`, publish `online` retained
on `robot-passport/<key>/status`, and set `offline` as the connection's last will
([discovery.md](discovery.md#mqtt)). Any MQTT client does this.

## Translators: reading robots that never heard of it

A reader can build a passport for a robot that publishes something else, the same idea as quackd's
adapters: a robot does not have to change for quackd to read it. A translated passport is always
labelled as translated, its figures carry the source they came from, and it is never signed,
because nobody's key wrote it. All four translators below are planned.

### URDF, through rosbridge

A ROS robot's URDF is the closest thing it has to a description of itself, and quackd's rosbridge
adapter already reads it ([adapters/rosbridge](../adapters/rosbridge/README.md)).

| Source | Passport |
|---|---|
| the URDF's `robot` element's `name` | `name` |
| the sum of every link's inertial mass in the URDF | `body.mass_kg`, with the bridge's URDF as its source |
| quackd's rosbridge manifest | `body.embodiment` and `body.mobility` `wheeled`, and its verbs as `skills` |

The rest stays not published, which is what quackd's rosbridge adapter already says about the same
robot.

### A VDA 5050 factsheet

The factsheet an automated guided vehicle sends its master control. These are version 3.0's names.

| VDA 5050 | Passport |
|---|---|
| `manufacturer`, from the header | `maker.vendor` |
| `typeSpecification.seriesName` | `maker.model` |
| `serialNumber`, from the header | `maker.serial` |
| `physicalParameters.maximumSpeed` | `body.max_speed_mps` |
| `physicalParameters.length`, `width`, `maximumHeight` | `body.length_m`, `body.width_m`, `body.height_m` |
| `typeSpecification.maximumLoadMass` | `body.payload_kg` |
| `typeSpecification.mobileRobotKinematics` | `body.mobility` `wheeled` |

### A MassRobotics identity report

The identity message of the MassRobotics AMR Interoperability Standard, version 1.0.

| MassRobotics | Passport |
|---|---|
| `manufacturerName` | `maker.vendor` |
| `robotModel` | `maker.model` |
| `robotSerialNumber` | `maker.serial` |
| `maxSpeed` | `body.max_speed_mps` |
| `baseRobotEnvelope` | `body.length_m` and `body.width_m`, as its bounding box |
| `emergencyContactInformation` | `owner.contact` |

`cargoMaxWeight` is a string in that standard, so it becomes `payload_kg` only when it reads as a
number.

### An A2A Agent Card

| A2A | Passport |
|---|---|
| `name` | `name` |
| `provider.organization` | `owner.label` |
| `provider.url` | `owner.contact` |
| `skills[].id`, where it is one of the core skill names | `skills` |

## The other way: a passport inside an A2A Agent Card (planned)

A robot that already speaks A2A can carry its passport in its Agent Card as an extension, so an
agent framework that knows nothing about robots still finds it:

```json
{
  "capabilities": {
    "extensions": [
      {
        "uri": "https://github.com/rokbenko/quackd/blob/main/docs/passport/spec.md",
        "description": "Robot Passport v0.1",
        "required": false,
        "params": {"passport": "<the signed passport, as its JWS>"}
      }
    ]
  }
}
```

The JWS goes in as it is, so the passport stays verifiable inside somebody else's document.

## What a reader of its own needs

Somebody writing a reader in another language needs very little: a JSON parser that can report
duplicate members, a base64url decoder, a base58 decoder for the `did:key`, SHA-256 and Ed25519
verification. Every mainstream language has all of them. The [vectors](vectors/README.md) say what
the result must be for each case, and a reader that matches every row reads passports the way
quackd does.
