# Naming

> [!NOTE]
> The facts about other projects on this page were checked on 2026-10-20 and can age. Each one
> says where it was checked.

## What the name means

A passport is the document a stranger is shown at a border: who you are, who vouches for that, and
what you may do. A robot's passport does the same job when it meets a robot it does not know, and
the metaphor carries further than the name:

- **It identifies its holder**: the `id`, a key only the robot holds.
- **Somebody vouches for it**: its owner signs it, and one day, perhaps, its maker.
- **It says what its holder may do**: `courtesy`, and what it cannot, `cannot`.
- **It is shown, not broadcast**: the beacon only says a robot is near, and the passport is shown to
  a robot that says hello.

Where the metaphor stops matters as much:

- **Nobody issues it.** Its owner writes and signs it, and its id is a key the robot made itself.
  No authority grants one, and none can take it away.
- **It proves less.** A government passport is evidence the state checked something. A robot's
  passport is evidence of what its writer claimed, and a reader labels it so.
- **It is public.** Anybody may read it, which is why it carries nothing secret and nothing
  personal that its owner did not choose to put in it.

## Why not "Robot Context Protocol"

The protocol began as an idea called the Robot Context Protocol, RCP, after the Model Context
Protocol. That name is taken, three times over:

- **An arXiv paper** titled "Robot Context Protocol (RCP): A Runtime-Agnostic Interface for
  Agent-Aware Robot Control", [2506.11650](https://arxiv.org/abs/2506.11650), dated 2025-06-13.
- **Alibaba DAMO Academy's RynnRCP**, the "Rynnbot Robotics Context Protocol",
  [on GitHub](https://github.com/alibaba-damo-academy/RynnRCP) under Apache-2.0.
- **A `robotcontextprotocol` GitHub organisation**, whose
  [repository](https://github.com/robotcontextprotocol/robotcontextprotocol) describes a "Robot
  Context Protocol For Multimodal LLM".

The letters are taken too. IANA registers the service name `rcp` as the Radio Control Protocol, on
port 469 ([the registry](https://www.iana.org/assignments/service-names-port-numbers/)). The PyPI
name `rcp` belongs to a "Remote Component Protocol". `rcp` is also the BSD remote copy command, and
Eclipse RCP is the Rich Client Platform. A protocol whose name collides in all of those places
could never have one unambiguous identifier, so it was renamed before anything shipped.

## The identifiers, and why each

Every identifier below was free when it was chosen, on 2026-10-20, except the Bluetooth UUIDs, which
0.29.0 added and which are unique by the way they are made.

| Identifier | Where it is used | Why it looks like that |
|---|---|---|
| `/.well-known/robot-passport.json`, `.jws` | the passport's URL | [RFC 8615](https://www.rfc-editor.org/rfc/rfc8615) reserves `/.well-known/` for this. The pair sits beside A2A's `agent-card.json`. Registering the suffixes takes a written specification and an expert's review. |
| `_robot-passport._tcp` | DNS-SD | [RFC 6335](https://www.rfc-editor.org/rfc/rfc6335) allows 15 characters for a service name, letters, digits and hyphens. This is 14. |
| `v`, `id`, `bt` | the keys of the DNS-SD TXT record | short, because a key is wire protocol forever once shipped |
| three 128-bit UUIDs | the Bluetooth service, and its two characteristics | version 5 UUIDs made from the specification's own URL with `#ble-service`, `#ble-rx` and `#ble-tx` after it, which anybody can recompute. The Bluetooth SIG assigns 16-bit UUIDs, which would save 14 bytes of the advert, for a fee, which is not worth paying before anybody else uses the protocol. |
| `robot-passport+jws`, `robot-passport-msg+jws` | the `typ` of each kind of signature | one per purpose, so an owner's signature on a passport can never pass as a robot's on a message, or the other way round |
| `robot-passport` | a PyPI distribution, when the module leaves quackd | the same name everywhere |
| `"passport": "1.0"` | the first member of every document | names the document and its version in one member, the way `openapi` does |
| `org.quackd` | quackd's own extension | reverse DNS, so no two makers' extensions collide |

## The words these pages always use

| Use | Not | Because |
|---|---|---|
| passport | card, profile, certificate | one word for one thing, and an owner's signature is not an X.509 certificate |
| beacon | announcement | a beacon says a robot is near and nothing more; the Bluetooth packet that carries it is an advert |
| owner key | master key, account | it signs passports, and nothing else |
| near | in range, close by | near is a judgement on signal strength, held for a second, and never proof |
| short id | fingerprint, hash | a short id is of a key; a digest is of a document; a beacon id is of nothing |
| courtesy | command, request | a peer asks for a courtesy, and is never obeyed by right |
| peer | client, master | two robots talking are equals |
| flock | group, swarm | quackd's one word for robots working together, and a passport is about the robots outside it |

[glossary.md](glossary.md) defines every term the folder uses.
