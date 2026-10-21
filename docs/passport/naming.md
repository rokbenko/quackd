# Naming

> [!NOTE]
> The facts about other projects on this page were checked on 2026-10-20 and can age. Each one
> says where it was checked.

## What the name means

A passport is the document a stranger is shown at a border: who you are, who vouches for that, and
what you may do. A robot's passport does the same job when it meets a robot it does not know, and
the metaphor carries further than the name:

- **It identifies its holder**: the `id`, a key only the robot holds.
- **Somebody can vouch for it**: a stamp from the owner's key, later from the maker's.
- **It says what its holder may do**: `courtesy`, and what it cannot, `cannot`.
- **It is shown, not broadcast**: a beacon points to it, and a reader fetches it.

Where the metaphor stops matters as much:

- **Nobody issues it.** A robot writes its own passport, and its id is a key it made itself. No
  authority grants one, and none can take it away.
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

Every identifier below was free when it was chosen, on 2026-10-20.

| Identifier | Where it is used | Why it looks like that |
|---|---|---|
| `/.well-known/robot-passport.json`, `.jws` | the passport's URL | [RFC 8615](https://www.rfc-editor.org/rfc/rfc8615) reserves `/.well-known/` for this. The pair sits beside A2A's `agent-card.json`. Registering the suffixes takes a written specification and an expert's review. |
| `_robot-passport._tcp` | DNS-SD | [RFC 6335](https://www.rfc-editor.org/rfc/rfc6335) allows 15 characters for a service name, letters, digits and hyphens. This is 14. |
| `robot-passport/<key>/` | MQTT topics | the same words, as a topic prefix nothing else uses |
| `robot_passport` | the ROS 2 topic | ROS names cannot contain a hyphen, so the hyphen becomes an underscore |
| `robot-passport+jws`, `robot-passport-stamp+jwt`, `robot-passport-msg+jws` | the `typ` of each kind of signature | one per purpose, so a signature made for one can never pass as another |
| `robot-passport` | a PyPI distribution, when the module leaves quackd | the same name everywhere |
| `"passport": "0.1"` | the first member of every document | names the document and its version in one member, the way `openapi` does |
| `org.quackd` | quackd's own extension | reverse DNS, so no two makers' extensions collide |

## The words these pages always use

| Use | Not | Because |
|---|---|---|
| passport | card, profile, certificate | one word for one thing, and a stamp is not an X.509 certificate |
| beacon | announcement, advert | a beacon only points; it never carries the passport |
| stamp | attestation, endorsement, signature | a signature is what any key makes; a stamp is one key vouching for another |
| short id | fingerprint, hash | a short id is of a key; a digest is of a document |
| courtesy | command, request | a peer asks for a courtesy, and is never obeyed by right |
| peer | client, master | two robots talking are equals |
| flock | group, swarm | quackd's one word for robots working together, and a passport is about the robots outside it |

[glossary.md](glossary.md) defines every term the folder uses.
