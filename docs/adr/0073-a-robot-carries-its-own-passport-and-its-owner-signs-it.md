# ADR-0073: A robot carries its own passport, and its owner signs it

**Status:** accepted · **Date:** 2026-10-29 · Amends [ADR-0057](0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md) (a passport is served by the robot it describes, signed by its owner, found by Bluetooth LE and read over the LAN or a Bluetooth link) and [ADR-0046](0046-the-jetson-is-reached-not-run-on.md) (`quackd onboard` runs on a robot's own Jetson) · Drops phase 7 of [design/robot-passport-phase-7.md](../design/robot-passport-phase-7.md) · Implemented in `quackd/onboard/`, `quackd/boards/` and `quackd/radio/` · Documented in [docs/passport/](../passport/README.md)

## Context

Robot Passport shipped in 0.23.0 so that any robot quackd drives could carry one, a Microduck or
an SO-101 as much as anything else ([ADR-0057](0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md)).
The robot was not where its passport lived. A laptop running quackd built the passport from the
robot's manifest, kept the robot's key under `~/.quackd/keys/`, served the passport and talked for
the robot. Without that laptop the robot could not say who it was. Everything that ran, ran
between processes on one machine, and nothing ran between two machines or on a robot.

Read again, three parts of that design did not hold.

**The owner never signed what the passport said.** The robot's own key signed the JWS, and the
owner key only stamped the robot key. Whoever held the robot key could change the passport, its
`safety` and how a person stops the robot included, and keep the owner's stamp, so a reader that
trusted the owner trusted the change. A stamp vouched for a key, never for what the key signed.

**Nothing said two robots were near each other.** DNS-SD finds what is on a network and an MQTT
broker what is on the broker, so a robot across the building and one in the doorway looked the
same. ADR-0057 reserved Bluetooth LE, the one beacon that is about distance, until beacon ids
could rotate, because a fixed id broadcast by radio lets anybody follow a robot and the person it
belongs to.

**The surface had grown past anything that had run.** QR stickers, an MQTT binding and a ROS 2
binding shipped, and phase 7 planned six more pieces: the translators, a passport inside an A2A
Agent Card, a single-file server, a page that shows a scanned passport to a person, a ROS 2 node
and an ESP32 library. No robot read a QR code: `quackd meet` read a picture that was a file on the
laptop.

The robots the protocol is for are not the ones it was built around either. An arm is fixed in
place and will rarely meet another robot. A toy-sized robot has no computer on board that can run
quackd. The robots that will meet strangers, in a corridor, at a front door, in a warehouse aisle,
are bigger mobile robots, humanoids and robots on four legs, and nearly all of them carry an
NVIDIA Jetson that can run quackd itself.

ADR-0046 kept quackd off the Jetson for reasons about driving: the deciding half of quackd is
better on the laptop where the person is, and a model server beside a fifty hertz control loop can
starve it. Neither reason is about identity, and identity belongs to the body, which is the one
thing that is there when two robots meet.

## Decision

**A passport lives on the robot it describes.** `quackd onboard` runs on the robot's own computer,
and on a laptop only as a stand-in for one. It sets the robot up once, serves its passport, listens
for robots that come close, checks theirs, and runs the robot's brain when they talk, driving the
robot's body from there when the robot's file names an adapter. An NVIDIA Jetson is the first
computer it supports, an Orin or a Thor on JetPack 6 or 7, recognised from its device tree and its
release file, and a Xavier or JetPack 5 is refused with the reason. A `sim` board stands in for one
on a laptop and in the test suite, only when it is named, and the passport it signs says
`"kind": "sim"` under `board`, so no reader takes a simulation for a robot. Other boards come in
through a plugin interface, the `quackd.boards` entry point group, the way adapters come in through
`quackd.adapters`, and a plugin may not take the names `jetson` or `sim`.

**Arms and toys carry none.** An arm is fixed in place and will rarely meet another robot, and a
toy-sized robot has no computer on board that can run quackd. The robot's file refuses an `arm`
embodiment. quackd drives the SO-101 and the Microduck as it did, and neither has a passport.

**The owner signs what the passport says.** In version 1.0 the JWS is signed by the owner's key,
its `kid` is `owner.id`, and `id` is the robot's own key, so a passport names two keys and the
owner's vouches for every member. A reader refuses a passport its robot signed, one signed by any
key but the one `owner.id` names, one whose owner key is its robot key, and one read outside
`issued` to `expires`, which are at most 400 days apart and 365 unless the owner says otherwise.
The owner key lives on the robot sealed with a passphrase, by scrypt and AES-256-GCM, and is
opened only while the owner signs: on the first `quackd onboard`, and with `quackd onboard sign`
after an edit or before the passport expires. The service never needs it. The robot's own key is a
plain file the service reads, mode 0600, because nobody is there to type a passphrase when a robot
reboots. A second robot of the same owner is signed by the same owner key, given with
`--owner-key`.

**Both robots verify each other before either says a word.** Every talk message is signed by its
sender's robot key. The three-step `hello` is unchanged, and its first step carries the sender's
owner-signed passport and a small runtime block its robot key signs: its software, the beacon id
it advertises now and the port it answers on. A receiver checks the size, the signature, the
JSON, the version, that the sender is the key it claims to be, that it heard the sender by radio,
that the passport passes every reading rule, that the passport is the sender's and that it is not
this robot's own, in that order, and refuses at the first that fails. A reply goes back only the
way the hello came.

**Near is Bluetooth LE, judged by signal strength.** The advert quackd builds is 31 bytes: 10 bytes
of service data under a 128-bit UUID minted from the specification's URL, holding a version, three
flags and an 8-byte beacon id drawn at random and derived from no key. The beacon id is replaced
every 15 minutes, never while a link, a session or a meeting is open, and the robot's DNS-SD record
and its hello change with it. The advert carries no name and no transmit power. A robot is near
when the median of what was heard of it over three seconds stays at or above a threshold for a
second: 2 m by default, which -59 dBm at one metre and a path loss exponent of 2.2 put at about
-65.6 dBm, none of it measured. A rotating beacon id does not stop tracking while the radio's own
address stays the same, so the wizard offers to switch on BlueZ's address privacy. That is the
condition ADR-0057 set for Bluetooth, met in the design, and nobody has seen either half work on a
radio.

**Talk goes over the LAN when the two robots share one, and over a Bluetooth link when they do
not.** On a network a robot's `_robot-passport._tcp` record, TXT `v=2`, carries its static short
id and its current beacon id, so a robot heard by radio is matched to the address it answers on.
Without a shared network the robot with the lower beacon id opens a link to the other, and the same
signed messages travel over it in frames. A first hello that arrives over the LAN is refused
unless the receiver heard the sender's beacon by radio in the last eight seconds, so being near is
the condition for talking either way.

**Trust is still a person's pin.** Three tiers: a stranger, a known robot (one a person pinned, or
any robot an owner they pinned signed), and the same owner (signed by this robot's own owner key).
The flock tier goes, since one quackd is one robot now. A stranger's passport reaches the brain in
its closed members only, under a name made from its key, and its sentences never reach a model.
Free talk needs a pin. The courtesy handler stands as ADR-0057 made it.

**An idle robot meets, and a busy one notices.** A verified robot near an idle one starts a
meeting: a run of the standing meeting task in the robot's file, at most twelve steps, with
`report_state` and `stop` allowed and `tell` to speak, started only when a brain is configured,
under an hourly cap, and with a stranger only when the file allows it. A robot already running a
goal does not stop for a meeting, and the newcomer appears in that run's Robots nearby. A goal
reaches the service from `quackd onboard goal`, whose terminal shows the run and asks its
questions.

**One file, written by a wizard, and a systemd unit.** `robot.yaml` holds what the passport says,
the body quackd drives if there is one, the brain, the meeting task, the pins and the radio's
settings, and `robot.env` holds the brain's key. The wizard asks what only the owner knows, writes
both, makes the keys, signs, offers address privacy, which restarts bluetoothd, and prints the sudo
lines that install and start `quackd-onboard.service` before it runs them. Onboarding is three
commands: install `uv`, then `uv tool install "quackd[onboard]"`, then `quackd onboard`.

**What goes.** Passports served from a laptop, QR stickers, the MQTT and ROS 2 bindings, owner
stamps, the multi-robot index, `quackd meet`, talk in `quackd run` and `quackd serve-mcp` with the
`robot_nearby` tool, and phase 7. Its design note stays as the record of what was planned.

**The name and the wire names stay, and the specification moves to 1.0.** Its section 7 says a
change an existing reader would misread takes a new major, and moving the signature from the
robot's key to the owner's is that change. A 0.x reader refuses a 1.0 passport at the version
rule, and a 1.0 reader refuses a 0.1 passport with a sentence that names the releases that wrote
it.

## Why not

**Laptop passports beside onboard ones.** Two places a robot's identity can live are two answers
to who vouches for it, and the laptop's answer is the one that leaves when the laptop does.

**A deprecated minor first.** ADR-0049 and RELEASING.md announce a removal a minor ahead where
that is practical. Nothing that goes ever ran between two machines, so no two robots stop meeting,
and the files 0.23 to 0.28 wrote are left on disk unread rather than deleted.

**Stamps beside the owner's signature.** A stamp vouches for a key and the signature for what the
key signed. Keeping both keeps the hole the signature closes.

**The owner key off the robot.** That is a second machine at onboarding and at every signing, and
nothing to sign with when the owner's laptop is somewhere else. Sealed, the key on the robot is no
use to a thief without the passphrase, and offline guessing against scrypt is the price, which the
Consequences name.

**Discovery on the LAN alone.** DNS-SD says a robot is on the network and nothing about whether it
is in the room, and a meeting is about being near.

**Passports for arms and toys.** The reason is above, and a passport served for them from a laptop
is the design this decision leaves.

**Phase 7.** The translators, the A2A card, the file server, the page for a scanned passport, the
ROS 2 node and the ESP32 library were each a way to reach robots that will not run quackd, planned
before anything had reached one that does.

**MQTT and ROS 2 as bindings.** Neither says anything about being near, and each kept a robot's
passport on infrastructure somebody else runs.

**A fixed beacon id.** It is the static short id broadcast by radio, which is what ADR-0057
refused and why Bluetooth waited.

## Consequences

- **Nothing in this decision has been run on a Jetson or over a real Bluetooth radio by this
  project.** It runs on the `sim` board, where two robots on one computer share a simulated air and
  a simulated network, and the radio's code runs against fakes, while its BlueZ half has never met
  bluetoothd. Everything a board and a radio would say is unmeasured: the advert's length and
  interval on BlueZ, the GATT tree, the negotiated MTU, both roles at once on an Orin's or a Thor's
  controller, RSSI at any distance, and whether address privacy takes effect.
- **ADR-0046 holds for every command but one.** `quackd run`, `serve-mcp` and `doctor` stay on the
  laptop and reach a board with `--host`, `bridge/jetson/` is unchanged, and doctor reads nothing
  of the machine it runs on. `quackd onboard` is the exception, and the contention caveat now
  covers it: a Bluetooth scanner, a pilot's requests and, when the robot's file names an adapter, a
  run driving the body, all beside a robot's fifty hertz control loop, which nobody here has
  measured. The unit runs the service at `Nice=10`, under `MemoryMax=1G` and with
  `OOMScoreAdjust=300`, which is a precaution and not a measurement.
- **What 0.23 to 0.28 kept is left on disk and read by nothing:** the robot keys and the owner key
  under `~/.quackd/keys/`, and the pins in `~/.quackd/known_robots.json`. The changelog says how to
  delete them.
- **The specification is 1.0.** The stamp and index schemas go, the passport and message schemas
  are rewritten, and the vectors are written again by the same script, which still signs with
  `cryptography` directly.
- **Near is a hint, not proof.** RSSI is weak evidence and the threshold is uncalibrated, and an
  advert relayed from elsewhere reads as near. What a robot met can make another do stays as
  bounded as ADR-0057 left it.
- **A robot needs a clock.** A board with no clock that survives a reboot and no network time
  refuses every passport, its own included, and `quackd onboard status` says why.
- **A stolen robot carries its sealed owner key.** A thief can guess passphrases offline against
  scrypt, and the robot's key speaks until its passport expires. There is no revocation yet, so a
  shorter `--days` is the only lever.
- **Strangers cost model calls.** Meetings are capped per hour, a meeting with a robot met in the
  last ten minutes does not start again, and the robot's file can keep strangers out of meetings.
- **`quackd[onboard]` is a new extra:** zeroconf and an OpenAI-compatible client everywhere, bleak
  and dbus-fast on Linux alone, so the Bluetooth half runs on Linux and nowhere else. It is in
  neither `dev` nor `all`, and CI tests it in a job of its own.
- **quackd still drives nine robots.** A robot quackd onboards without driving it is described by
  its file and moves nothing. It is no adapter, so no count, badge or catalogue changes.
