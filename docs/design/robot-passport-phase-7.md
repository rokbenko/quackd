# Design: Robot Passport, phase 7, reaching robots that do not run quackd

**Status:** dropped by ADR-0073 on 2026-10-29, nothing of it built · **Follows:** 0.23.0, which
shipped phases 1 to 6 · **Written:** 2026-10-22

> **Read on 2026-10-29:** phase 7 is dropped, and none of its six pieces will be built.
> [ADR-0073](../adr/0073-a-robot-carries-its-own-passport-and-its-owner-signs-it.md) moved Robot
> Passport onto the robot it introduces: `quackd onboard` runs on the robot's own computer, an
> NVIDIA Jetson first, the owner signs what the passport says, and robots near each other find
> one another by Bluetooth LE and talk over the LAN or a Bluetooth link. The pieces below were
> ways to reach robots that do not run quackd, and the QR stickers, the MQTT binding and the ROS 2
> binding they built on went in 0.29.0. "First: the run 0.23.0 still owes" and "Also open,
> outside phase 7" are replaced by PLAN.md's item for Robot Passport on two Jetsons with
> Bluetooth. The links below into docs/passport/ still land, most of them on a short note that
> says what went and when. The rest of this note is the plan as it stood on 2026-10-22, left as
> it was.

This is the working plan for Robot Passport after 0.23.0. It is written so that a session that
knows nothing else can pick it up: read this note, then [roadmap.md](../passport/roadmap.md),
[spec.md](../passport/spec.md) and
[ADR-0057](../adr/0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md). It replaces a plan
that lived outside the repository. PLAN.md carries one line per item below and points here.

## Robot Passport, in one paragraph

Robot Passport is a small open protocol for a robot to introduce itself to one it has never met:
who it is, who vouches for it, what it can and cannot do, how a person stops it and how to reach
it. A passport is one JSON document, signed as a compact JWS (Ed25519) over its exact bytes by a
robot that holds a key, with stamps from its owner. A reader finds it at
`/.well-known/robot-passport.json`, over DNS-SD as `_robot-passport._tcp`, retained on an MQTT
broker, on a ROS 2 topic or through a QR sticker. Two robots that have met talk in six small signed
messages (`hello`, `say`, `bye`, `ask`, `answer`, `share`), and a robot met may ask for a courtesy,
a bounded `pause` or an `identify`, which fixed code decides and never a pilot. The specification is
draft 0.1, in [docs/passport/](../passport/README.md), with four JSON Schemas and 31 test vectors.
quackd is its first implementation. The rule underneath: reading needs nothing, asking needs a key.

## What 0.23.0 shipped, and where it lives

0.23.0 was tagged and published on 2026-10-22 (its changelog section carries 2026-10-21, the day
it was cut). Phases 1 to 6 were the specification and its pages, the passport itself, finding and
reading, trust, and talk in two steps.

| Path | What it is |
|---|---|
| `quackd/passport/` | the protocol, importing nothing else from quackd (`tests/test_passport_isolation.py` holds it to that): `document.py` (reading and writing a passport, the reader's rules in order), `jws.py`, `keys.py` (did:key, short ids), `stamps.py`, `strict.py` (I-JSON parsing that refuses duplicate members), `fetch.py` (the fetch rules), `serve.py` (the HTTP server and the talk inbox), `beacon.py` (DNS-SD TXT), `talk.py` (the envelope, the three-step hello, sessions) |
| `quackd/adapters/passport.py` | `passport_from_manifest()`, a passport for any robot quackd drives |
| `quackd/passport_store.py`, `passport_trust.py` | keys under `~/.quackd/keys/`, pins in `~/.quackd/known_robots.json`, and the four tiers: stranger, known, same owner, flock |
| `quackd/passport_talk.py`, `passport_courtesy.py` | talk for quackd's robots, the Robots nearby lines a pilot sees, and the courtesy handler |
| `quackd/passport_qr.py`, `quackd/lan/passports.py`, `quackd/lan/passport_mqtt.py` | QR stickers, DNS-SD, MQTT |
| `quackd/cli.py` | `quackd passport show\|check\|set\|owner\|stamp\|known\|forget\|serve\|qr`, `quackd meet`, passports in `quackd discover`, `run --talk --meet --talk-port` |
| `quackd/mcp_server.py` | `serve-mcp --talk` and the `robot_nearby` tool |
| `scripts/passport_vectors.py` | writes the vectors with `cryptography` directly, a second implementation of the rules |
| `scripts/passport_readme_ratio.py` | measures the README's Robot Passport section against `docs/passport/` |
| `tests/test_passport_*.py` | bindings, cli, courtesy, document, from_manifest, isolation, mcp, meet, mqtt_talk, nearby, run, schema, store, talk, trust, vectors, wire |

**What has run.** Every part ran on fakes and on sockets in the suite. Between separate processes
on one Windows 11 machine, with two registry directories standing for two hosts, these ran:
passports served and announced, found over real zeroconf, read over HTTP and retained on a real
amqtt broker, a robot and its owner pinned, all six talk messages over HTTP and through the broker,
two `nearby-hello` runs that met, and a pause asked of a running robot and granted. `meet ros://`
has run only against fakes. Nothing has run between two machines or on a robot, and the roadmap's
status table says so part by part. A "yes" in its two-machine or robot columns needs a run that
somebody did and wrote down, never a fake.

## First: the run 0.23.0 still owes

Before any new piece, the two-machine run in PLAN.md ("Robot Passport across two machines, and then
on a robot"). It needs two laptops on one network and nothing else, and each part it proves flips a
row of the roadmap's status table. Write down what ran in `docs/passport/quackd.md`'s "What has run"
with the output it printed, the way the one-machine runs are written. The courtesy test goes to a
running `quackd run nearby-hello --talk`, because `quackd passport serve --talk` drives no robot and
answers every `ask` no. The pause on a robot that is really walking comes after, on whatever body is
in reach first.

## Phase 7, piece by piece

The order below is the recommended one: what needs no hardware first. Each piece says what is
decided already, what is left to decide, where its code goes, what done means and what it needs.

### 1. The translators

A reader builds a passport for a robot that publishes something else, the way quackd's adapters let
quackd drive robots that never heard of it. Four sources, each with its field table already written
in [compatibility.md](../passport/compatibility.md#translators-reading-robots-that-never-heard-of-it):
a URDF read through rosbridge, a VDA 5050 factsheet (version 3.0's names), a MassRobotics identity
report (version 1.0), and an A2A Agent Card.

- **Decided:** a translated passport is never signed, because nobody's key wrote it. Its figures
  carry their source, and a reader labels it translated (spec section 3, rule 11). Anything the
  source does not say stays not published, never guessed. `cargoMaxWeight` is a string in the
  MassRobotics standard, so it becomes `payload_kg` only when it reads as a number.
- **To decide:** how a translated passport says so in the document. The spec names the label but
  defines no member for it. Either an extension (`ext`, keyed by reverse DNS) or an optional member,
  which is a spec minor, 0.2, with its schema and vectors. Also what `id` a translated passport has,
  since it holds no key: a `urn:uuid` made from the source's serial, or one made fresh.
- **Code:** the three that read a document need nothing else, so they go in `quackd/passport/` as
  pure functions from a parsed document to a passport, beside the isolation test. The URDF one needs
  quackd's rosbridge adapter (its `introspect`), so it lives outside, in the adapter or a glue module
  like `quackd/adapters/passport.py`. The CLI is likely
  `quackd passport translate FILE --from vda5050|massrobotics|a2a`, and `quackd meet ros://` falling
  back to the URDF when a robot publishes no passport topic, labelled translated either way.
- **Done means:** each translator reads a real published example of its source (the standards'
  own example files, cited with the date they were read), `quackd passport check` accepts what it
  writes and labels it translated, and compatibility.md loses "planned" with the real output quoted.
- **Needs:** no hardware. The URDF one needs a ROS robot on rosbridge to claim a run, and a fake to
  test.

### 2. A passport inside an A2A Agent Card

A robot that speaks A2A carries its signed passport in its Agent Card, so an agent framework that
knows nothing about robots still finds it. The shape is written in
[compatibility.md](../passport/compatibility.md#the-other-way-a-passport-inside-an-a2a-agent-card-planned):
an entry in `capabilities.extensions[]` with `uri`, `description`, `required: false` and
`params.passport` holding the JWS exactly as it is, so it stays verifiable inside somebody else's
document.

- **To decide:** the extension's `uri`. The page uses the spec's URL on GitHub today, and that URL
  moves when the spec gets its own repository, so pick the lasting one before quackd writes any.
- **Code:** writing, `quackd passport show --robot NAME --a2a`, which prints the extension entry.
  Reading, `quackd meet` given an Agent Card (`/.well-known/agent-card.json`) finds the extension,
  verifies the JWS and reads the passport like any other, under the same fetch rules.
- **Done means:** a round trip, and an A2A SDK's own Agent Card model parsing a card that carries
  the extension without complaint.
- **Needs:** no hardware.

### 3. A single-file server for any Linux robot

A robot that does not run quackd serves its own passport: level 1 of the ladder
([compatibility.md](../passport/compatibility.md#a-raspberry-pi-or-any-linux-robot-planned)). One
file using only Python's standard library, in the spirit of the daemons quackd already puts on
robots under `bridge/`, serving the well-known URL and registering `_robot-passport._tcp` with the
Avahi daemon Raspberry Pi OS already runs.

- **Decided:** the standard library only, so it runs on any Python a robot already has. It reads
  the passport files it is given, `robot-passport.json` and `robot-passport.jws`, written on the
  owner's laptop with `quackd passport show --out`, or by hand for an unsigned one.
- **To decide:** the standard library has no Ed25519, so the server can serve a passport signed
  elsewhere but cannot sign, or answer a `hello`, on its own. Level 2 on the robot means an optional
  import of `cryptography` when it is there. And how it registers with Avahi: a static service file
  under `/etc/avahi/services/`, which needs root once and no Python at all, or `avahi-publish`. A
  systemd unit beside it, as `bridge/moss/` has.
- **Code:** `bridge/passport/robot_passport_server.py` and a README beside it, with the same
  bounds as `quackd/passport/serve.py`: a deadline a read, a cap on connections, and a refusal of a
  body over the limit.
- **Done means:** it runs on a real Pi or any Linux machine, and `quackd discover` on another
  machine lists it and `quackd meet` reads and verifies what it serves, with that output quoted in
  compatibility.md.
- **Needs:** a Linux machine on the same network as a quackd laptop.

### 4. A page that shows a scanned passport to a person

A person points a phone at a robot's sticker and sees whose robot it is and how to stop it, in
words, with what is proven kept apart from what is only claimed, as `quackd passport check` shows it
in a terminal.

- **Decided:** a sticker holds the passport's URL with `#rp=` and the robot's short id
  (`quackd passport qr`), and a reader warns when the passport's key does not match it.
- **To decide, and the hard part:** a page served over HTTPS cannot fetch a passport served over
  plain HTTP on a local network, because the browser blocks mixed content, and that is where most
  robots serve theirs. So either the robot's own host serves a page for people beside its passport
  (`quackd passport serve` and the single-file server answering `text/html` at the well-known URL,
  or a page at `/`), or the page lives on a public site, such as the one the browser demo is served
  from (https://www.quackd.org/simulator), and reads only passports served over HTTPS, which is what
  a level 0 sticker pointing at a static host has. Likely both.
- **Code:** a page under `web/` beside the browser demo, verifying the signature in the browser
  with WebCrypto's Ed25519 where it has it, and plain HTML from the robot's host for the local case.
- **Done means:** a phone scanning a real sticker sees the passport, and the key check, on both
  paths.
- **Needs:** a phone, and a robot host on the network.

### 5. A ROS 2 node

A small node that publishes a robot's passport, as the spec binds it
([discovery.md](../passport/discovery.md#ros-2)): the JWS on `robot_passport`,
`std_msgs/msg/String`, reliability `RELIABLE`, durability `TRANSIENT_LOCAL`. No new message type and
no new dependency. quackd already reads that topic through rosbridge with
`quackd meet ros://HOST[:PORT][/NAMESPACE]`, so this node is also what gives that path its first run
outside the fakes.

- **To decide:** Python (`rclpy`) for the first, since it is one file. Where it reads the passport
  from: a file path parameter. A launch file beside it.
- **Code:** `bridge/ros2/robot_passport_node.py` and a README. Talk over a ROS topic stays reserved
  in the spec and is not part of this.
- **Done means:** on a ROS 2 machine with `rosbridge_server`, `quackd meet ros://` reads what the
  node publishes and verifies it. A container on the same machine counts as between processes, not
  between machines, and the roadmap keeps the difference.
- **Needs:** a ROS 2 install (Humble or Jazzy), or a `ros:jazzy` container for development.

### 6. An ESP32 library

The smallest robots: level 1 on an ESP32 is an HTTP server and ESP-IDF's mDNS component, and Ed25519
fits its memory for level 2
([compatibility.md](../passport/compatibility.md#an-esp32-planned)).

- **Decided:** HTTP first. Bluetooth LE only once beacon ids can rotate, since a stable id over
  radio lets anybody follow a robot and its owner
  ([discovery.md](../passport/discovery.md#bluetooth-le-later)). The space is already counted there:
  a self-chosen 128-bit UUID leaves 13 bytes in a scan response, enough for a version byte, an
  8-byte short id and a 4-byte digest. JSON only, as 0.1 decided. A CBOR and COSE profile, Ed25519
  as algorithm `-19`, comes only if a microcontroller turns out to need it, and is a spec minor.
- **To decide:** Arduino or plain ESP-IDF first, and the Ed25519 library (Monocypher is small and
  has no dependencies). Where the key lives on the board (NVS).
- **Code:** a C library in the repository until the spec gets its own (below), in a top-level
  folder of its own, since the workspace's members are Python packages under `adapters/`.
- **Done means:** `quackd meet` reads a real board's passport and verifies its signature, and a
  `hello` from quackd is answered, with the output quoted.
- **Needs:** an ESP32 board on the network. It is the second implementation that triggers the split.

## After a second implementation: the spec gets its own repository

When a second implementation exists (the ESP32 library or the ROS 2 node, in a language other than
quackd's own) or a maker outside quackd wants to adopt the protocol, whichever comes first:

- Move the spec, the schemas, the vectors and `quackd/passport/` to their own repository with
  `git subtree split`, keeping their history. `tests/test_passport_isolation.py` exists so that this
  is mechanical.
- Publish `robot-passport` on PyPI on the spec's own version line, never quackd's shared one
  ([ADR-0049](../adr/0049-a-version-says-what-changed.md)), which would bump it on every quackd
  minor and break other people's pins. Upload it before the quackd release that depends on it. The
  name was free on 2026-10-20: check it again first.
- quackd then depends on it like any third-party package.
- Leave a pointer page at every `docs/passport/` path. A PyPI release's README links never change
  once it is published, so those paths have to keep answering.

## Also open, outside phase 7

Each of these is written in the pages as planned or not built, and each needs only quackd:

- **Talk over a broker in a run and in `serve-mcp`.** Both talk over HTTP only (`_talk_for_run` in
  `quackd/cli.py`, the talk server in `quackd/mcp_server.py`), while `passport serve --mqtt --talk`
  and `meet mqtt://` already talk over a broker.
- **`--talk` in a flock run,** which is refused today: each member needs its own talker and inbox.
- **A pilot that asks for a courtesy or shares what it saw.** A pilot has `tell` alone. New tools
  would follow the stranger rules `tell` follows: no stranger's words reach a pilot, and what went
  wrong reaches the person and not the pilot.
- **Maker stamps** (`rel: maker`), once makers hold keys and publish them. Until then a passport's
  maker and model are its own claim, and a reader says so.
- **A list of revoked keys** at an owner's contact URL.
- **Guests:** a robot from outside a flock joining it as a peer quackd talks to but does not pilot.
  It needs a decision record of its own before any code.

## Rules that hold for all of it

The project's rules are in CONTRIBUTING.md and RELEASING.md. These are the ones this work has met:

- **Docs quote real output.** Every command a page shows was run, and its output pasted from that
  run. A page shows a command only once it exists, and a planned section keeps "planned" until a
  real run, on the hardware it names.
- **The README's Robot Passport section** is one paragraph per line, with no semicolon, no em or en
  dash, no hyphen between spaces and no GitHub alert (`tests/test_docs.py` holds it to that), and it
  stays between 20% and 30% of `docs/passport/`, counting words outside code blocks
  (`scripts/passport_readme_ratio.py`). `docs/passport/` itself is hard-wrapped and uses alerts.
- **No page says "fleet",** in any form. A docs test refuses it everywhere that is not history.
- **Every change carries its docs** in the same commit, and its CHANGELOG entry under
  `[Unreleased]`. A release follows RELEASING.md.
- **Gates for each commit:** ruff, its formatter, mypy under Python 3.11 and 3.12, the tests the
  change touches, and always the repo-wide guards (`tests/test_docs.py`, `test_pypi_readme.py`,
  `test_openai_headers.py`, `test_workspace.py` and `test_command.py`, which wants every flag
  shaped like a URL or a credential in `quackd/command.py`'s lists). The whole suite once before a
  branch is called done, and a watchdog on any long run left in the background: the 0.23.0 release
  gate hung overnight in a test that passes alone.
- **One review agent for code that handles keys, the wire, or a body that moves,** and its
  findings proved by a probe before they are fixed. The talk review found ten real problems in
  0.23.0's code that a green suite had missed, and a second pass on the fixes found five more.

## Order, and why

0. The two-machine run, since every "no" in the roadmap waits on it.
1. The translators and the A2A embedding, which need no hardware and widen who quackd can read at
   once.
2. The single-file server, and the page for people beside it, on the first Linux box in reach.
3. The ROS 2 node, developed in a container and claimed on a real ROS machine.
4. The ESP32 library, which needs a board and is the second implementation that moves the spec to
   its own repository.
