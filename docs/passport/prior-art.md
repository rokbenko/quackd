# Prior art: what was borrowed, and what was avoided

> [!NOTE]
> Checked on 2026-10-20. Standards move: each entry links the source it was checked against, and a
> version or a status here is only as current as that date.

Nothing in Robot Passport is new on its own. This page names every system it was measured against,
what it took from each, and what it deliberately did not.

## Agent protocols

### A2A Agent Card

A JSON description of an AI agent, served at `/.well-known/agent-card.json` (it was `agent.json`
before version 0.3.0), registered with IANA. It has required members including `name`,
`description`, `version`, `capabilities`, `skills` and the interfaces the agent answers on, an
optional `provider`, extensions declared by URI with a `required` flag, and optional signatures:
JWS over the card canonicalised with RFC 8785. Version 1.0 was released in 2026 and the project is
hosted by the Linux Foundation's Agentic AI Foundation.
([specification](https://a2a-protocol.org/latest/specification/),
[releases](https://github.com/a2aproject/A2A/releases))

- **Borrowed:** a document at a well-known path; extensions by URI, with `required`; room for more
  than one signature.
- **Avoided:** a required endpoint, which a robot with only a sticker cannot have; signing over
  canonical JSON, which is where signature bugs live.

### MCP

The Model Context Protocol's servers describe themselves and their capabilities to a client, and
its specification says plainly that this self-description is self-reported and not to be relied on
for security. A client that speaks a version a server does not gets an error listing the versions
the server does speak. ([specification](https://modelcontextprotocol.io/specification/))

- **Borrowed:** a version on every message, and a refusal that says which versions are accepted;
  self-description labelled as a claim.

### ANP and AGNTCY OASF

Two more efforts to describe agents to each other, with decentralised identifiers and schema-checked
records. ([ANP](https://github.com/agent-network-protocol/AgentNetworkProtocol),
[OASF](https://github.com/agntcy/oasf))

- **Borrowed:** the confirmation that a key-based id and a small described record are the direction
  agent protocols are taking.
- **Avoided:** describing software agents rather than bodies.

## Device and robot description

### W3C Web of Things Thing Description, and WoT Discovery

A W3C Recommendation since 2023 describing a device's properties, actions and events and how to
reach each. WoT Discovery separates a small **introduction** (DNS-SD `_wot._tcp`, `/.well-known/wot`,
a QR code, a beacon) from the **exploration** that fetches the full description.
([Thing Description](https://www.w3.org/TR/wot-thing-description11/),
[Discovery](https://www.w3.org/TR/wot-discovery/))

- **Borrowed:** the split between an introduction that only points and a description fetched after
  it.
- **Avoided:** a required JSON-LD context and security definitions on every document; it also has
  no vocabulary for a body or for safety.

### VDA 5050

The German automotive industry's interface between automated guided vehicles and their master
control, JSON over MQTT. A vehicle publishes a `factsheet` describing itself, kept apart from its
live `state`, and a `connection` topic, retained, with an MQTT last will that reports when it drops
off. The factsheet's `protocolLimits` says the longest strings and arrays the vehicle accepts.
Version 3.0.0 was tagged in 2026.
([repository](https://github.com/VDA5050/VDA5050))

- **Borrowed:** a description kept apart from live state; presence by retained message and last
  will; a robot publishing its own parser limits.
- **Avoided:** a design built around one operator's management software and broker, rather than
  two strangers.

### MassRobotics AMR Interoperability Standard

JSON over WebSockets for mobile robots, version 1.0 from 2021. Its `identityReport` carries a maker,
model and serial number, a maximum speed, the robot's envelope, an emergency contact and a support
vendor. The standard says it is not to be relied on for anything related to safety.
([repository](https://github.com/MassRobotics-AMR/AMR_Interop_Standard))

- **Borrowed:** an emergency contact and the robot's physical envelope as first-class facts.
- **Avoided:** an untyped number (`cargoMaxWeight` is a string).

### Open-RMF

Connects robots from different makers to one shared traffic schedule, through an adapter for each
maker's system, with graded levels of participation down to a robot that only reports its position
and path. It has no exchange between robots that have never met.
([project](https://github.com/open-rmf/rmf))

- **Borrowed:** graded participation, so a robot that can do very little still takes part.

### ROS 2

DDS discovery tells a stranger a participant's GUID, its DDS vendor, its node names and its topic
types. SROS2 adds DDS-Security, with certificates from one keystore's authority.
([ROS 2 security design](https://design.ros2.org/articles/ros2_dds_security.html))

- **Borrowed:** a latched topic (`TRANSIENT_LOCAL`) as a binding, so a ROS robot needs nothing new.
- **Avoided:** trust that works only inside one certificate authority.

### OPC UA for Robotics, IEEE 1872, ISO 22166

Industrial and academic descriptions of robots: OPC 40010 requires a manufacturer, model, product
code and serial number for a motion device; IEEE 1872 is an ontology of robot concepts; ISO 22166
describes modules of service robots. ([OPC 40010](https://reference.opcfoundation.org/specs/OPC-40010-1/),
[IEEE 1872](https://standards.ieee.org/ieee/1872/5354/))

- **Borrowed:** maker, model and serial as the core of a robot's identity.
- **Avoided:** depth: a stranger needs a robot's mass and how to stop it, not an ontology.

### Robot Raconteur

A robot communication framework with real node discovery: a node announces itself every 10 to 15
seconds with an index URL and a nonce that changes when its services do, and a standard
`DeviceInfo` type carries a maker, model and serial number. Its TLS certificates are issued by one
company. ([standards](https://github.com/robotraconteur/robotraconteur_standards))

- **Borrowed:** a fingerprint in the announcement, so a reader refetches only when something changed.
- **Avoided:** one company as the root of trust.

### MAVLink

The drone protocol's components announce a heartbeat, and `COMPONENT_METADATA` points to a JSON
description by URI with a CRC of the file. ([messages](https://mavlink.io/en/messages/common.html))

- **Borrowed:** a pointer plus a checksum, so a description is fetched once and recognised after.

## Identity broadcast to strangers

### ASTM F3411 Remote ID

The closest in spirit: drones broadcast who they are and who flies them, to anybody nearby, in fixed
25-byte messages (a basic id, a location, an operator id, a free-text self id), one per Bluetooth 4
advertisement or several packed into one on bigger radios. The open-source
[opendroneid](https://github.com/opendroneid/opendroneid-core-c) library implements it. The IETF's
DRIP work adds identifiers built on Ed25519 ([RFC 9374](https://www.rfc-editor.org/rfc/rfc9374)).

- **Borrowed:** a tiny broadcast that names a machine and its operator to strangers, and an operator
  id that need not be a person's name.
- **Avoided:** a stable identifier broadcast by radio before ids can rotate.

### AIS

Ships broadcast a static message with their identity number, name, call sign, type and dimensions,
every few minutes, with no authentication at all.
([US Coast Guard](https://www.navcen.uscg.gov/ais-class-a-static-voyage-message-5))

- **Borrowed:** static facts sent rarely, kept apart from position sent often; dimensions as part of
  identity.
- **Avoided:** having no way to check a claim.

## Proving a maker's claim

### Matter

A Matter device proves its vendor and product with a certificate chain its commissioner checks, and
a public ledger lists the roots. It has an Identify cluster, so a person can tell which device they
are setting up. ([certificates](https://developers.home.google.com/matter/test/certificates),
[ledger](https://github.com/zigbee-alliance/distributed-compliance-ledger))

- **Borrowed:** `identify`; trusting a maker's claim only when a maker's key signs it, which is
  what the reserved `maker` stamp is for.
- **Avoided:** a certification body that every maker must join before its robot can be described.

### IETF MUD

A device points, by URL, to a file its maker signed that describes what network access the device
needs. ([RFC 8520](https://www.rfc-editor.org/rfc/rfc8520))

- **Borrowed:** a device that carries a pointer to its description rather than the description.

## Signatures and keys

- **did:key** ([draft](https://w3c-ccg.github.io/did-key-spec/)): an identifier that is a public key,
  resolved with no network. Borrowed as the `id`.
- **JWS** ([RFC 7515](https://www.rfc-editor.org/rfc/rfc7515)) with **Ed25519**
  ([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032), [RFC 9864](https://www.rfc-editor.org/rfc/rfc9864)):
  borrowed as the signature, over exact bytes.
- **COSE** ([RFC 9052](https://www.rfc-editor.org/rfc/rfc9052)), the same thing in CBOR for small
  devices: kept for a later profile, with the Ed25519 algorithm value `-19`.
- **EDHOC** ([RFC 9528](https://www.rfc-editor.org/rfc/rfc9528)), a key exchange for constrained
  devices: not needed while messages are signed and not encrypted.
- **SSH**: trust on first use, and the warning when a pinned key changes. Borrowed whole.

## The other Robot Context Protocols

Three projects already use the name this protocol started with: an arXiv paper, Alibaba DAMO
Academy's RynnRCP, and a `robotcontextprotocol` GitHub organisation. Judging by their titles and
descriptions, they connect language models and agents to robots, which is a different job from
robots introducing themselves to each other. [naming.md](naming.md) says why the name changed.

## At a glance

| | Robot Passport | A2A Agent Card | WoT TD | VDA 5050 factsheet | MassRobotics identity | Remote ID |
|---|---|---|---|---|---|---|
| for | robots meeting robots | agents | devices | a vehicle and its master control | a robot and its operator's software | a drone and anybody nearby |
| transport | any: a URL, mDNS, MQTT, ROS 2 | HTTP | any, by binding | MQTT | WebSocket | Bluetooth, Wi-Fi |
| smallest useful document | under 100 bytes | larger, eight required members | larger, JSON-LD | larger | larger | 25 bytes a message |
| needs a server | no | yes | depends | a broker | a socket | no |
| signed | optional, Ed25519 | optional | no | no | no | optional |
| owner or operator | optional | provider | no | no | emergency contact | operator id |
| body and safety facts | yes | no | no | dimensions, speeds | envelope, speed | position |
