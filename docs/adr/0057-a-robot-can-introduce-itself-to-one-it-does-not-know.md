# ADR-0057: A robot can introduce itself to one it does not know

**Status:** accepted, amended · **Date:** 2026-10-21 · Extends [ADR-0021](0021-lan-discovery-and-mqtt-bus.md) (discovery carries identity only), [ADR-0032](0032-datasheets-and-the-verdict.md) (a body is figures, each with a confidence and a source) and [ADR-0034](0034-registered-robots-and-pilot-flocks.md) (a flock is a list a person keeps) · Documented in [docs/passport/](../passport/README.md)

**Amended 2026-10-29 by [ADR-0073](0073-a-robot-carries-its-own-passport-and-its-owner-signs-it.md):**
a passport now lives on the robot it describes. `quackd onboard` serves it from the robot's own
computer, an NVIDIA Jetson first, and the owner's key, kept on the robot under a passphrase, signs
its content, where 0.23 signed it with the robot's key and let an owner stamp that key. Stamps, QR
stickers, the MQTT and ROS 2 bindings and passports served from a laptop are gone, phase 7 is
dropped, and the specification is 1.0. Proximity is Bluetooth LE, which this decision reserved
until beacon ids could rotate: quackd now changes them every 15 minutes, which nobody has tested on
a radio, and ADR-0073 says what the beacon carries. One small document read by fixed rules, the
`did:key` id, the six messages, the idea of tiers and the courtesy handler stand.

## Context

A flock is a list of registered names a person wrote, and each member's pilot knows its peers from
manifests quackd builds locally, with each peer's own adapter
([ADR-0034](0034-registered-robots-and-pilot-flocks.md)). Discovery on a local network finds other
quackd instances by a TXT record of identity, and feeds no flock
([ADR-0021](0021-lan-discovery-and-mqtt-bus.md)). Its record advertises port 0, "identity only,
nothing listens": a manifest fetched "out of band" has only ever meant a reader with the same adapter
installed building the same manifest itself.

So a robot that nobody put on the list cannot introduce itself. That is true of another quackd
robot from somebody else's flock, and of every robot that does not run quackd at all. quackd's
reason to exist is robots from many makers learning what each other are, and the moment two of
them meet is where that has nothing to say. There are no keys anywhere in quackd, no owner, no way
to tell a robot you met yesterday from one pretending to be it, and
[SECURITY.md](../../SECURITY.md) calls the LAN surfaces trusted-network only.

The idea arrived as a "Robot Context Protocol". That name is taken by an arXiv paper, by Alibaba
DAMO Academy's RynnRCP and by a GitHub organisation, and `rcp` by IANA and by PyPI
([naming.md](../passport/naming.md)). The formats that exist were measured against it first: the A2A
Agent Card, the W3C Web of Things, VDA 5050, the MassRobotics standard, ROS 2 discovery, Robot
Raconteur and drone Remote ID. None of them is for two robots that have never met, and the reasons
are in [why.md](../passport/why.md#why-not-use-something-that-exists).

## Decision

**A robot publishes a passport**: one JSON document with three required members, `passport` (the
version), `id` and `name`, and optional ones for its maker, its software, its owner, its body as
figures with a confidence, its skills, the courtesies a peer may ask for, what it cannot do, how
it stops, where it listens, stamps and extensions. It lives at `/.well-known/robot-passport.json`. A
reader accepts 8,192 bytes, ignores what it does not know, reads `safety` as the worst case, and
refuses duplicate members. [spec.md](../passport/spec.md) is normative.

**The id is a key.** A `did:key` holding an Ed25519 public key, so nobody issues it and nothing is
looked up to check it. A signed passport is a compact JWS over the passport's exact bytes, with
nothing canonicalised. A stamp is another key vouching for a robot's key, `owner` in 0.1, `maker`
reserved. Trust in a key comes from a person pinning it, the way SSH pins a host, never from the
passport.

**A beacon points, and the passport proves.** The well-known URL, a QR code or NFC tag, DNS-SD
`_robot-passport._tcp` beside quackd's own `_quackd._tcp` rather than instead of it, MQTT retained
with a last will, and a latched ROS 2 topic. Bluetooth is reserved until beacon ids can rotate,
because a fixed id broadcast by radio lets anybody follow a robot. A reader fetches only what it was
pointed at, follows no redirects, and contacts no URL a passport names on its own initiative.

**Talk is six messages over channels robots already speak.** A three-step `hello` whose nonces make
every later message in the session unreplayable, then `say`, `bye`, `share`, `ask` and `answer`. An
`ask` is decided by the receiver: in quackd by fixed code, not the pilot, against four tiers
(stranger, known, same owner, flock), with one global budget because keys cost nothing, through the
executor with the source `peer`. A peer can ask for a bounded pause or for a robot to identify
itself, and no peer can ever stop a robot or keep it from stopping.

**In quackd it is a self-contained module**, `quackd/passport/`, that imports nothing else from
quackd and needs the standard library and `cryptography`, which the default install already has
through `mcp`. quackd builds a passport from any robot's manifest, and the manifest gains no field,
because an owner and an e-stop belong to one robot and not to its model. Pins go in their own
`~/.quackd/known_robots.json`, because `RobotEntry` refuses unknown fields and an older quackd must
still read `robots.json`. Robots met during a run reach a pilot in its observation, quoted, never in
the system prompt, which is built once per run.

**Every part is documented**, in [docs/passport/](../passport/README.md), and the README carries a
section that covers every aspect and links to the pages. The vectors are written by
`scripts/passport_vectors.py`, which signs with `cryptography` directly, so the module is checked
against a second implementation of the rules rather than against itself.

**It is written to leave.** When a second implementation exists or another maker wants to adopt
it, the spec, the schemas, the vectors and the module move to a repository of their own, and the
package is `robot-passport` on the spec's own version line, never on quackd's.

## Consequences

- Every robot quackd drives, and every adapter somebody else publishes, gets a passport without
  anybody writing one.
- `_quackd._tcp` and its TXT keys do not change. They are permanent wire protocol, and the new
  service type sits beside them.
- A peer gains exactly two things it can make a robot do, and both are bounded in time and drawn
  from one budget. Everything else a peer says is information.
- quackd keeps keys for the first time, as plain files under `~/.quackd/keys/`, on the same footing
  as the tokens in `robots.json`, and SECURITY.md says so.
- Talk is signed and not encrypted, Bluetooth is absent, and maker stamps and revocation lists are
  later work. [roadmap.md](../passport/roadmap.md) lists what 0.1 leaves out.
- There is now a fourth way to describe a machine beside A2A, the Web of Things and VDA 5050. The
  cost is paid by keeping it small enough for a sticker and by translating the others.
- Nothing of it has run between two machines or on a robot on the day this is dated.
  [roadmap.md](../passport/roadmap.md)'s status table is where that changes, and it changes only when
  somebody has run it.
