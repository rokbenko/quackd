# Prior art: what was borrowed, and what was avoided

> [!NOTE]
> Checked on 2026-10-20, and the two radio privacy entries on 2026-10-28. Standards move: each entry
> links the source it was checked against, and a version or a status here is only as current as that
> date.

Nothing in Robot Passport is new on its own. This page names the systems it was measured against,
what it took from each, and what it deliberately did not. The three projects that already use its
first name, the Robot Context Protocol, are in [naming.md](naming.md).

## Agent protocols

### A2A Agent Card

A JSON description of an AI agent at `/.well-known/agent-card.json`, registered with IANA, with
required members including `name`, `description`, `version`, `capabilities`, `skills` and the
interfaces the agent answers on, extensions declared by URI with a `required` flag, and optional
JWS signatures over the card canonicalised with RFC 8785. Version 1.0 was released in 2026 under the
Linux Foundation's Agentic AI Foundation.
([specification](https://a2a-protocol.org/latest/specification/),
[releases](https://github.com/a2aproject/A2A/releases))

- **Borrowed:** a document at a well-known path; extensions with `required`.
- **Avoided:** a card about software, with nowhere for a body; signing over canonical JSON, which is
  where signature bugs live.

### MCP

The Model Context Protocol's servers describe themselves to a client, and its specification says
this self-description is self-reported and not to be relied on for security. A client that speaks a
version a server does not gets an error listing the versions the server does speak.
([specification](https://modelcontextprotocol.io/specification/))

- **Borrowed:** a version on every message, a refusal that lists the versions accepted, and
  self-description labelled as a claim.

## Device and robot description

### W3C Web of Things Thing Description, and WoT Discovery

A W3C Recommendation since 2023 describing a device's properties, actions and events. WoT Discovery
separates a small **introduction** (DNS-SD `_wot._tcp`, `/.well-known/wot`, a QR code, a beacon)
from the **exploration** that reads the full description.
([Thing Description](https://www.w3.org/TR/wot-thing-description11/),
[Discovery](https://www.w3.org/TR/wot-discovery/))

- **Borrowed:** an introduction that only says something is there, apart from the description.
- **Avoided:** a required JSON-LD context and security definitions on every document, and no
  vocabulary for a body or for safety.

### VDA 5050

The German automotive industry's interface between automated guided vehicles and their master
control, JSON over MQTT. A vehicle's `factsheet` describes it, kept apart from its live `state`, and
its `protocolLimits` say the longest strings and arrays it accepts. Version 3.0.0 was tagged in 2026.
([repository](https://github.com/VDA5050/VDA5050))

- **Borrowed:** a description kept apart from live state; a robot publishing its own parser limits.
- **Avoided:** a design built around one operator's management software, rather than two strangers.

### MassRobotics AMR Interoperability Standard

JSON over WebSockets for mobile robots, version 1.0 from 2021. Its `identityReport` carries a maker,
model and serial number, a maximum speed, the robot's envelope and an emergency contact, and the
standard says it is not to be relied on for anything related to safety.
([repository](https://github.com/MassRobotics-AMR/AMR_Interop_Standard))

- **Borrowed:** an emergency contact and the robot's physical envelope as first-class facts.
- **Avoided:** an untyped number (`cargoMaxWeight` is a string).

### Open-RMF

Connects robots from different makers to one shared traffic schedule, through an adapter for each
maker's system, down to a robot that only reports its position and path. It has no exchange between
robots that have never met. ([project](https://github.com/open-rmf/rmf))

- **Not borrowed:** participation by a robot that can do almost nothing. A passport is for a robot
  that can run quackd itself.

### ROS 2

DDS discovery tells a stranger a participant's GUID, its node names and its topic types, and SROS2
adds DDS-Security, with certificates from one keystore's authority.
([ROS 2 security design](https://design.ros2.org/articles/ros2_dds_security.html))

- **Borrowed, then dropped:** 0.1 carried a passport on a latched topic; 1.0 removed it, since a ROS
  graph says nothing about which robots are near each other.
- **Avoided:** trust that works only inside one certificate authority.

### OPC UA for Robotics, IEEE 1872, ISO 22166

Industrial and academic descriptions of robots: OPC 40010 requires a manufacturer, model, product
code and serial number for a motion device, IEEE 1872 is an ontology of robot concepts, and ISO 22166
describes modules of service robots. ([OPC 40010](https://reference.opcfoundation.org/specs/OPC-40010-1/),
[IEEE 1872](https://standards.ieee.org/ieee/1872/5354/))

- **Borrowed:** maker, model and serial as the core of a robot's identity.
- **Avoided:** depth: a stranger needs a robot's mass and how to stop it, not an ontology.

### Robot Raconteur

A robot communication framework whose nodes announce themselves every 10 to 15 seconds with a nonce
that changes when their services do, and whose `DeviceInfo` type carries a maker, model and serial
number. Its TLS certificates are issued by one company.
([standards](https://github.com/robotraconteur/robotraconteur_standards))

- **Borrowed:** a fingerprint of the document in the announcement, the digest a reader caches by.
- **Avoided:** one company as the root of trust.

## Identity broadcast to strangers

### ASTM F3411 Remote ID

The closest in spirit: drones broadcast who they are and who flies them, to anybody nearby, in fixed
25-byte messages, one per Bluetooth 4 advertisement or several in one on bigger radios. The
open-source [opendroneid](https://github.com/opendroneid/opendroneid-core-c) library implements it,
and the IETF's DRIP work adds identifiers built on Ed25519
([RFC 9374](https://www.rfc-editor.org/rfc/rfc9374)).

- **Borrowed:** a tiny Bluetooth broadcast that strangers nearby hear without connecting, and an
  operator id that need not be a person's name.
- **Avoided:** a stable identifier: a passport's beacon id is random, names nobody, and changes every
  15 minutes.

### AIS

Ships broadcast their identity number, name, call sign, type and dimensions every few minutes, with
no authentication at all.
([US Coast Guard](https://www.navcen.uscg.gov/ais-class-a-static-voyage-message-5))

- **Borrowed:** dimensions as part of identity, kept apart from position.
- **Avoided:** having no way to check a claim.

## Radio privacy

### Exposure Notification

Apple's and Google's Bluetooth protocol of 2020 for COVID-19 exposure notification. A phone
broadcast a Rolling Proximity Identifier under the 16-bit service UUID `0xFD6F`. The specification
says the identifier "changes about every 15 minutes to prevent wireless tracking of the device",
that the advertiser address rotates every 10 to 20 minutes, and that the two "shall be changed
synchronously so that they cannot be linked". Each advert also carried the phone's transmit power,
encrypted, for a better guess at distance.
([Bluetooth specification v1.2](https://covid19-static.cdn-apple.com/applications/covid19/current/static/contact-tracing/pdf/ExposureNotification-BluetoothSpecificationv1.2.pdf))

- **Borrowed:** an identifier that changes about every 15 minutes; distance guessed from signal
  strength, with a calibration.
- **Not yet matched:** changing the radio address and the identifier at the same moment. quackd
  rotates its beacon id on its own clock and leaves the address to BlueZ, so a listener who hears one
  change while the other holds can tie the two together ([privacy.md](privacy.md#tracking)).

### Bluetooth LE address privacy

Bluetooth LE lets a device advertise from a resolvable private address that changes periodically,
every 15 minutes by the specification's recommendation, which only a device holding its Identity
Resolving Key, exchanged when two devices bond, can link back to it.
([Novel Bits](https://novelbits.io/bluetooth-address-privacy-ble/))

- **Borrowed:** the radio's own address changing, which the wizard offers to switch on in BlueZ.
- **Avoided:** depending on bonding. Two robots that meet are strangers and never bond, so the beacon
  id rotates with no shared key, and a robot recognises another by the keys in its passport.

## Proving a maker's claim

### Matter

A Matter device proves its vendor and product with a certificate chain its commissioner checks, and
a public ledger lists the roots. Its Identify cluster lets a person tell which device they are
setting up. ([certificates](https://developers.home.google.com/matter/test/certificates),
[ledger](https://github.com/zigbee-alliance/distributed-compliance-ledger))

- **Borrowed:** `identify`; trusting a maker's claim only when a maker's key signs it, which a later
  version could add.
- **Avoided:** a certification body that every maker must join before its robot can be described.

## Signatures and keys

- **did:key** ([draft](https://w3c-ccg.github.io/did-key-spec/)): an identifier that is a public key,
  resolved with no network. Borrowed as the `id` and the `owner.id`.
- **JWS** ([RFC 7515](https://www.rfc-editor.org/rfc/rfc7515)) with **Ed25519**
  ([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032), [RFC 9864](https://www.rfc-editor.org/rfc/rfc9864)):
  borrowed as the signature, over exact bytes.
- **COSE** ([RFC 9052](https://www.rfc-editor.org/rfc/rfc9052)), the same thing in CBOR for small
  devices: kept for a later profile, with the Ed25519 algorithm value `-19`.
- **SSH**: trust on first use, and the warning when a pinned key changes. Borrowed whole.

## At a glance

| | Robot Passport | A2A Agent Card | WoT TD | VDA 5050 factsheet | MassRobotics identity | Remote ID |
|---|---|---|---|---|---|---|
| for | robots meeting robots | agents | devices | a vehicle and its master control | a robot and its operator's software | a drone and anybody nearby |
| transport | a Bluetooth LE beacon, then the LAN or a Bluetooth link | HTTP | any, by binding | MQTT | WebSocket | Bluetooth, Wi-Fi |
| signed | always, by its owner, Ed25519 | optional | no | no | no | optional |
| owner or operator | required, as a key | provider | no | no | emergency contact | operator id |
| identifier on the air | random, changes every 15 minutes | not broadcast | not broadcast | not broadcast | not broadcast | usually a serial number |
| body and safety facts | yes | no | no | dimensions, speeds | envelope, speed | position |
