# Roadmap and status

The honest state of Robot Passport, kept current. Every other page in this folder points here for
what is built and what has run.

## Status

| Part | Specified | Built in quackd | Tested on fakes | Run between two machines | Run on a robot |
|---|---|---|---|---|---|
| the passport document, built from a manifest and checked | 0.1 | yes: `passport show`, `check`, `set`, `owner` | yes | does not apply | no |
| the well-known URL and the index | 0.1 | yes: `passport serve`, `meet` | yes, and between processes on one machine | no | no |
| QR stickers | 0.1 | yes: `passport qr`, `meet <picture>` | yes | does not apply | no |
| DNS-SD | 0.1 | yes: `serve --announce`, `discover` | yes, and between processes on one machine | no | no |
| MQTT | 0.1 | yes: `serve --mqtt`, `meet mqtt://` | yes, and between processes on one machine | no | no |
| ROS 2, read through rosbridge | 0.1 | yes: `meet ros://` | yes | no | no |
| signatures and stamps | 0.1 | yes: every passport signed, `passport stamp` | yes, and between processes on one machine | no | no |
| pinning | quackd's own | yes: `meet --pin`, `--pin-owner`, `passport known`, `forget` | yes, and between processes on one machine | no | no |
| `hello`, `say`, `bye` | 0.1 | yes, over HTTP (`serve --talk`, `meet --as`, `run --talk`, `serve-mcp --talk`) and MQTT (`serve --mqtt --talk`, `meet mqtt:// --as`) | yes, and between processes on one machine | no | no |
| `ask`, `answer`, `share`, the courtesy handler | 0.1 | yes: the handler in a run and `serve-mcp`, `meet --ask` and `--share`, owner settings | yes, and between processes on one machine, over HTTP and a broker | no | no |
| Bluetooth LE | reserved | no | no | no | no |

"Run between two machines" stays "no" until somebody runs it on two machines and writes down what
happened, which is the same rule [guides/lan.md](../guides/lan.md) has always followed.

## Phases

quackd 0.23.0 ships phases 1 to 6.

1. **The specification and these pages.** The spec, the schemas, the vectors, every page in this
   folder, the README's section and the decision record ([ADR-0057](../adr/0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md)).
2. **The passport itself.** quackd builds a passport from any robot's manifest, prints it, and checks
   anybody's.
3. **Finding and reading.** quackd serves passports, advertises them over DNS-SD and MQTT, makes QR
   stickers, reads the ROS 2 topic through rosbridge, and meets robots.
4. **Trust.** Keys, signed passports, owner stamps and pinning.
5. **Talk, first step.** `hello`, `say` and `bye`, robots nearby in a pilot's observation, and the
   MCP tool.
6. **Talk, second step.** `ask`, `answer` and `share`, and the courtesy handler with its tiers and
   global budget.
7. **Reach, after 0.23.0.** A single-file Python server for any Linux robot, an ESP32 library, a ROS
   2 node, a page that shows any passport to a person who scanned a sticker, the translators, and a
   passport inside an A2A Agent Card. The working plan for each, and for the move of the spec to its
   own repository after the second implementation, is
   [design/robot-passport-phase-7.md](../design/robot-passport-phase-7.md).

## Decisions taken

These were open questions, and the answers are the defaults 0.1 ships with.

- **A stranger may ask for a pause**, of up to five seconds, out of one global budget of one pause a
  minute shared by every peer, and an owner can switch stranger pauses off. A known robot may ask for
  up to ten seconds. No peer can ever `stop` a robot.
- **`owner` is optional**, and a flock may require it before it talks to a guest.
- **JSON only in 0.1.** A CBOR and COSE profile, with the Ed25519 algorithm value `-19`, comes when
  the first microcontroller implementation needs it.
- **A stranger cannot join a flock in 0.1.** Letting one join as a guest, a peer quackd talks to but
  does not pilot, is the natural next step, and it needs its own decision record.

## What 0.1 leaves out on purpose

- **Bluetooth LE,** until beacon ids can rotate ([discovery.md](discovery.md#bluetooth-le-later)).
- **Maker stamps,** until makers hold keys and publish them ([trust.md](trust.md#stamps)).
- **Revocation lists,** a list of revoked keys at an owner's contact URL.
- **Encryption** of talk messages, which are signed and readable.
- **ROS 2 and A2A as talk channels,** reserved.
- **A binary profile,** above.

## Leaving quackd

The protocol starts inside quackd and is written to leave. Its module imports nothing else from
quackd, and its spec uses none of quackd's words. When a second implementation exists, an ESP32 or a
ROS 2 one, or when another maker wants to adopt it, whichever comes first, the spec, the schemas, the
vectors and the module move to a repository of their own, with their history. The Python package
there is `robot-passport`, on the spec's own version line rather than quackd's, and quackd then
depends on it like any other library.

Every page under `docs/passport/` stays where it is, pointing at the new home, because a link
published on PyPI can never be changed afterwards.
