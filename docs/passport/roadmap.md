# Roadmap and status

The honest state of Robot Passport, kept current. Every other page in this folder points here for
what is built and what has run.

## Status

Nothing in this folder has run on a Jetson or over a real Bluetooth radio.
"Tested on the sim board" means the suite runs it on a laptop, mostly in one process against fakes,
the sim board standing in for a robot's computer and a folder for the air. One test runs two
processes that hear each other and pass a request over a simulated link. No part of it has run
between two machines.

| Part | Specified | Built in quackd | Tested on the sim board | Run on a Jetson | Run over a real radio |
|---|---|---|---|---|---|
| the passport, signed by its owner | 1.0 | yes: the wizard, `sign`, `show`, `quackd passport check` | yes, against the vectors | no | does not apply |
| the robot key, and the owner key sealed by a passphrase | quackd's own | yes | yes | no | does not apply |
| boards: the Jetson, the sim board, plugins | quackd's own | yes | the sim board; Jetson detection against fakes of a board's files | no | does not apply |
| the Bluetooth LE advert and its rotating id | 1.0 | yes, through BlueZ | the advert's bytes and the rotation's clock | no | no |
| near and far by signal strength | quackd's own | yes | yes, with a threshold nobody has measured | no | no |
| the Bluetooth link | 1.0 | yes, through BlueZ | its frames, and one request between two processes | no | no |
| DNS-SD and the inbox on the LAN | 1.0 | yes | the inbox over loopback HTTP, with the sim board's LAN standing in for DNS-SD | no | does not apply |
| `hello` with both robots checked, `say`, `bye` | 1.0 | yes | yes, in one process | no | no |
| `ask`, `answer`, `share`, the courtesy handler | 1.0 | yes | yes | no | no |
| pins and the three tiers | quackd's own | yes: `pin`, `forget`, `met` | yes | no | does not apply |
| the meeting task, goals, robots nearby in a run | quackd's own | yes: the service, `goal`, `stop` | yes, with a scripted pilot | no | no |
| the service under systemd, and BlueZ address privacy | quackd's own | yes: the wizard, `uninstall` | no: the sim board installs neither | no | no |
| the demo | quackd's own | yes: `quackd onboard demo` | yes, on Windows, macOS and Linux | does not apply | does not apply |

"Run on a Jetson" and "Run over a real radio" stay "no" until somebody runs that part on two boards,
writes down what happened, and the record is in the repository, which is the same rule
[guides/lan.md](../guides/lan.md) has always followed. The run 0.29.0 owes is two Jetsons with
Bluetooth: the three commands on each, a reboot that brings the service back, one carried toward the
other with the signal strength written down at 1, 3 and 10 m, once on one Wi-Fi and once with it off,
so the robots meet over the LAN once and over the Bluetooth link once.

## Phases

quackd 0.23.0 shipped phases 1 to 6, 0.29.0 phase 8, and phase 7 was dropped.

1. **The specification and these pages.** The spec, the schemas, the vectors, every page in this
   folder, the README's section and the decision record
   ([ADR-0057](../adr/0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md)).
2. **The passport itself.** quackd built a passport from any robot's manifest, printed it, and
   checked anybody's.
3. **Finding and reading.** Passports the laptop that drove a robot handed out, over DNS-SD, MQTT,
   QR stickers and a ROS 2 topic.
4. **Trust.** Keys, passports signed by the robot's key, owner stamps and pinning.
5. **Talk, first step.** `hello`, `say` and `bye`, robots nearby in a pilot's observation, and the
   MCP tool.
6. **Talk, second step.** `ask`, `answer` and `share`, and the courtesy handler with its tiers and
   global budget.
7. **Reach, dropped.** A single-file server for any Linux robot, an ESP32 library, a ROS 2 node, a
   phone page and translators. ADR-0073 dropped it on 2026-10-29 and nothing of it was built;
   [its note](../design/robot-passport-phase-7.md) is kept as the record.
8. **Onboard, 0.29.0.** The passport moves onto the robot it introduces, `quackd onboard` on a
   Jetson first and the sim board for laptops and CI: the owner's signature in place of stamps,
   Bluetooth LE with a rotating id, the LAN or a Bluetooth link, both robots checked before a word,
   and a brain that meets robots by itself. Passports kept on a laptop, stickers, stamps and the
   MQTT and ROS 2 bindings went ([quackd.md](quackd.md#removed-in-0290)).

## Decisions taken

These were open questions, and the answers are the defaults 1.0 ships with.

- **The owner signs the passport,** and the owner key lives on the robot, sealed by a passphrase
  asked only when signing. A stamp vouched for a key, never for what the passport said.
- **Arms and toys carry none.** An arm is fixed in place and will rarely meet another robot, and a
  toy-sized robot has no computer on board that can run quackd. Humanoids and quadrupeds nearly all
  carry a Jetson that can.
- **Close is Bluetooth LE signal strength,** and the beacon's id is random and changes every 15
  minutes, never in the middle of a meeting, because a fixed id broadcast by radio would let anybody
  follow a robot ([privacy.md](privacy.md#tracking)).
- **A stranger's words never reach a brain,** and free talk needs a pin. A stranger may ask for a
  pause of up to five seconds, out of one budget of a pause a minute shared by every peer, and an
  owner can switch that off. A known robot may ask for ten. No peer can ever `stop` a robot.
- **JSON only.** A CBOR and COSE profile, with the Ed25519 algorithm value `-19`, comes when the first
  implementation on a microcontroller needs it.
- **A stranger cannot join a flock.** Letting one join as a guest, a peer quackd talks to but does
  not pilot, is the natural next step, and it needs its own decision record.

## What 1.0 leaves out on purpose

- **Maker signatures,** until makers hold keys and publish them: `maker` stays what the robot's owner
  says it is.
- **Revocation,** a list of revoked keys at an owner's contact URL. Until then a passport's
  `expires` is the limit on a stolen robot key ([trust.md](trust.md#a-robot-is-stolen)).
- **Encryption** of talk messages, which are signed and readable.
- **A measured threshold.** The 2 m a robot meets within is turned into a signal strength with a
  textbook model and a default nobody has measured on a Jetson.
- **Boards beyond the Jetson,** which join through the plugin interface once somebody runs one
  ([compatibility.md](compatibility.md)).
- **A binary profile,** above.

## Leaving quackd

The protocol starts inside quackd and is written to leave. Its module imports nothing else from
quackd, and its spec uses none of quackd's vocabulary. When a second implementation exists, on another
board or in another language, or when another maker wants to adopt it, whichever comes first, the
spec, the schemas, the vectors and the module move to a repository of their own, with their
history. The Python package there is `robot-passport`, on the spec's own version line rather than
quackd's, and quackd then depends on it like any other library.

Every page under `docs/passport/` stays where it is, pointing at the new home, because a link
published on PyPI can never be changed afterwards.
