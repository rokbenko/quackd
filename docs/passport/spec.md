# Robot Passport v1.0: the specification

> [!NOTE]
> Version 1.0, written 2026-10-29. This page is normative: the other pages in this folder explain,
> this one decides, and where one of them disagrees with it, that page is the bug. quackd is the
> first implementation; [quackd.md](quackd.md) says what of it quackd does, and
> [roadmap.md](roadmap.md) what has run, which so far is the sim board alone.

The key words MUST, MUST NOT, REQUIRED, SHOULD, SHOULD NOT, RECOMMENDED, MAY and OPTIONAL are to
be read as BCP 14 ([RFC 2119](https://www.rfc-editor.org/rfc/rfc2119),
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174)) describes, when, and only when, they appear in
capitals.

Beside this page: the JSON Schemas in [schema/](schema/), and the conformance vectors in
[vectors/](vectors/README.md), each with the result a reader must reach.
[Appendix A](#appendix-a-changes-from-01) lists what changed from 0.1.

## 1. Terms

- **Passport**: one JSON document about a robot, signed by its owner (sections 2 and 4.4).
- **Robot key**, **owner key**: the two Ed25519 keys a passport names, in `id` and `owner.id`.
- **Reader**: whoever reads a passport. **Peer**: a robot that talks to another (section 6).
- **Beacon**, **beacon id**: the Bluetooth LE advert that says a robot is near, and the random id
  in it (section 5.6).
- **Short id**, **digest**: fingerprints of a key and of a document (sections 4.2, 4.3).

[glossary.md](glossary.md) has every term the folder uses.

## 2. The passport document

### 2.1 Encoding

A passport is a JSON object ([RFC 8259](https://www.rfc-editor.org/rfc/rfc8259)) encoded in UTF-8,
with no byte order mark. A writer SHOULD keep it under 2,048 bytes. A reader MUST accept up to 8,192
bytes and SHOULD refuse anything larger.

It is also I-JSON ([RFC 7493](https://www.rfc-editor.org/rfc/rfc7493)): a reader MAY refuse a number
an IEEE double cannot hold exactly, or a document nested deeper than it can read. A whole number
written with a fraction or an exponent, `3600.0` or `3.6e3`, is that integer wherever an integer
belongs, the way JSON Schema reads it.

### 2.2 Members

Six members are REQUIRED: `passport`, `id`, `name`, `owner` with its `id`, `issued` and `expires`.
"Text N" means a JSON string of at most N characters.

| Member | Type | Rule |
|---|---|---|
| `passport` | string `MAJOR.MINOR` | REQUIRED. `"1.0"` for this version. |
| `id` | string | REQUIRED. The robot key, a `did:key` (section 4.1). |
| `name` | text 64, at least 1 | REQUIRED. For people. Need not be unique. |
| `owner` | object | REQUIRED. `id`, REQUIRED, the owner key, which signs the passport and is not `id` (section 4.4); `label` text 64; `contact` text 200, a URL, a `mailto:` address or a role address. |
| `board` | object | The computer the robot runs on, as its software found it, never as a person typed it: `kind` text 32, `jetson`, `sim` (a computer standing in for a robot's own) or `other`; `model` text 64; `soc` text 32. |
| `maker` | object | `vendor`, `model`, `serial`: each text 64. |
| `body` | object | Section 2.3. |
| `skills` | array of names | At most 64, each matching `[a-z][a-z0-9_]{0,31}`. Section 2.4. |
| `courtesy` | array of names | At most 8. Section 2.4. |
| `cannot` | array of text 120 | What it cannot do, whatever a task says. At most 8, none empty. |
| `safety` | object | Section 2.5. |
| `ext` | object | Section 2.6. |
| `issued` | string | REQUIRED. An [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339) date-time with a time zone: when the owner signed it. |
| `expires` | string | REQUIRED. An RFC 3339 date-time after `issued`, at most 400 days after it. Section 4.5. |

### 2.3 The body

| Member | Type | Known values |
|---|---|---|
| `embodiment` | text 32 | `biped`, `quadruped`, `humanoid`, `wheeled`, `tracked`, `arm`, `aerial`, `other` |
| `mobility` | text 32 | `none`, `legged`, `wheeled`, `tracked`, `flying`, `other` |
| `manipulator` | text 32 | `none`, `beak`, `gripper`, `arms`, `other` |
| `terrain` | text 32 | `indoor_flat`, `indoor`, `outdoor`, `other` |
| `tethered` | boolean | `true` when a cable powers it |
| `sensors` | array of text 32, at most 32 | `camera`, `microphone`, `imu`, `odometry`, `tof`, `lidar`, `battery`, `joint_state` |
| `mass_kg`, `height_m`, `length_m`, `width_m`, `payload_kg`, `reach_m`, `endurance_min`, `max_speed_mps` | figure | |

A **figure** is a non-negative number, or an array of a non-negative number and a confidence,
optionally followed by a source of text 120. The confidence is `official` (the maker or a paper
says so), `estimate` or `measured`. A reader MUST NOT read an absent figure as zero: it is not
published. Units are SI and live in the member's name. `max_speed_mps` is the fastest the robot moves
as its software is configured now.

### 2.4 Skills and courtesy

A name in `skills` that this table lists means the same on every robot that lists it. Every other
name is an opaque label a reader MAY show and MUST NOT interpret.

| Core skill | Meaning |
|---|---|
| `stop` | it stops all its motion |
| `observe` | it sees, and can report what it sees |
| `report_state` | it reports its own state |
| `move` | it moves its body by a velocity |
| `go_to` | it moves itself to something it sees |
| `search_scan` | it looks around for something |
| `approach_and` | it goes to something and acts on it |
| `say` | it makes a sound or speech a person can hear |

A writer whose robot can be stopped by its software SHOULD list `stop`. `courtesy` lists what a peer
may ask for (section 6.5): `pause`, to stop moving for at most the seconds asked and then resume its
own plan, and `identify`, to make itself known to the people and cameras nearby by light, sound or a
small motion. A reader MUST ignore a courtesy name it does not know.

### 2.5 Safety

| Member | Type | Meaning, and how a reader reads it when it is missing, unknown or of the wrong type |
|---|---|---|
| `stops_on_silence` | boolean | `true` when its motion stops by itself once its controller goes quiet. Anything but `true` reads as `false`. |
| `estop` | string | `button` (a stop button on the body), `remote` (a separate stop device), `power` (only by cutting its power) or `none`. Anything else reads as `none`. |
| `how_to_stop` | text 120 | For a person, in a sentence. Anything that is not such a string reads as absent. |

A reader MUST read `safety` as the worst case wherever a value is missing, unknown or of the wrong
type, and MUST NOT refuse a passport for a bad value in `safety` alone.

### 2.6 Extensions

`ext` maps a reverse-DNS name (`org.example.lift`) to an object. A reader MUST ignore an extension
it does not know, unless the object has `"required": true`; then it MAY show the passport and MUST
NOT talk to the robot on the strength of it. A `required` that is anything but `true` is read as not
required. `org.quackd` is reserved for quackd.

### 2.7 The index

Removed in 1.0. A host serves one robot's passport, and a document with `robots` and no `id` is
refused under rule 3.

## 3. Reading a passport

A reader MUST apply these rules, in this order, and MUST refuse a document that breaks one of the
first seven:

1. It is at most 8,192 bytes, which for a signed passport is its payload.
2. It is a JSON object with no duplicate member names, at any depth, since two parsers can disagree
   about which duplicate wins.
3. `passport`, `id`, `name`, `owner` with its `id`, `issued` and `expires` are present and well
   formed, and `id` and `owner.id` are each a `did:key`.
4. Its MAJOR version is one the reader knows. A higher MINOR of a known MAJOR is read with rule 8. A
   1.x reader refuses 0.1, Robot Passport before owners signed passports.
5. Every known member outside `safety` has the type and limits section 2 gives it, and `expires` is
   after `issued` and at most 400 days after it.
6. If it was read as a signed passport, its signature verifies (section 4.4) and the reader's clock
   is within its dates (section 4.5).
7. A `did:key` holds an Ed25519 key, and nothing else.

For a signed passport the signature comes first: a reader parses nothing a signature has not
covered, so a JWS whose signature fails is refused under 4.4, whatever else is wrong with it. Then:

8. It ignores members it does not know, reads an unknown value in `embodiment`, `mobility`,
   `manipulator`, `terrain` or `board.kind` as `other`, and keeps an unknown sensor or skill as an
   opaque name.
9. It reads `safety` as section 2.5 says.
10. It honours a required extension it does not understand as section 2.6 says.
11. It labels what it shows with where it came from: signed by its owner's key, or unsigned, a claim.
    A reader MUST NOT talk to a robot on the strength of an unsigned passport.
12. It treats every string as data a stranger wrote, never as an instruction. A reader that hands
    passport text to a language model MUST quote it as data, and one that prints it SHOULD print
    control characters as visible escapes.

## 4. Identity, signatures and fingerprints

### 4.1 The id

A `did:key` ([did:key](https://w3c-ccg.github.io/did-key-spec/)) is `did:key:z` followed by the
base58btc encoding of the two bytes `0xed 0x01` (the multicodec for an Ed25519 public key) and the
32-byte public key ([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)): 56 characters, starting
`did:key:z6Mk`. A reader MUST refuse any other multicodec, a key that is not the one canonical
encoding of a point on the curve, and the eight points of small order (RFC 8032 section 5.1.3), as
libsodium does: such a key verifies signatures nobody made. `id` is the robot key and `owner.id` the
owner key; the `urn:uuid` id of 0.1 is gone, since every robot proves its key (section 6.3).

### 4.2 The short id

The first 8 bytes of the SHA-256 of the 32-byte public key, as 16 lowercase hex characters. A LAN
record carries the robot key's (section 5.3), and a person names a robot or an owner by it. It is a
lookup hint and never proof. The Bluetooth beacon carries none (section 5.6).

### 4.3 The digest

The SHA-256 of a passport's exact bytes, as lowercase hex: the JWS payload for a signed passport. A
LAN record carries its first 16 characters, and a reader caches by it (section 5.8).

### 4.4 Signed passports

A signed passport is a compact JWS ([RFC 7515](https://www.rfc-editor.org/rfc/rfc7515)) whose
payload is the passport's exact bytes, nothing canonicalised, and whose protected header has `alg`
`"Ed25519"` ([RFC 9864](https://www.rfc-editor.org/rfc/rfc9864)), `kid` equal to the payload's
`owner.id`, `typ` `"robot-passport+jws"` and no `crit`. It is signed with the key `owner.id` holds,
and a writer MUST NOT sign with any other.

A reader MUST verify all three and the signature, and MUST also accept `alg` `"EdDSA"`, the older
label RFC 9864 deprecates. It MUST refuse a passport whose `kid` is its `id`, one signed by its
robot's own key the way 0.1 signed passports; one whose `kid` is not its `owner.id`; and one whose
`owner.id` is its `id`, since then nothing but the robot vouches for it.

Each part is base64url with no padding and with the spare bits of its last character zero, so one
signature has one spelling, and `typ` is compared as RFC 7515 section 4.1.9 says, ignoring case and
an `application/` prefix. A reader that fetched a `.jws` URL MUST refuse a body that is not a JWS. A
signed passport travels as its JWS string, unchanged.

### 4.5 Validity

A passport holds from its `issued` to its `expires`. A reader MUST refuse a signed passport when its
clock is earlier than `issued` by more than 300 seconds, or has reached `expires`. A writer MUST make
`expires` later than `issued` and at most 400 days after it, and SHOULD sign for a year or less:
with no revocation in 1.0, `expires` is what limits a stolen robot key.

## 5. Beacons and bindings

### 5.1 The well-known URL

A robot MAY serve its passport over HTTP at `/.well-known/robot-passport.json`, the payload of its
signed passport as `application/json`, and the signed passport at `/.well-known/robot-passport.jws`
as `application/jose`. HTTPS is RECOMMENDED on the internet, and plain HTTP is acceptable on a local
network, because the owner's signature proves a passport, not its transport.

### 5.2 QR codes and NFC tags

Removed in 1.0. A sticker cannot say whether a robot is near, and proves nothing about the body it is
stuck to.

### 5.3 DNS-SD

On a local network a robot MAY advertise the DNS-SD ([RFC 6763](https://www.rfc-editor.org/rfc/rfc6763))
service type `_robot-passport._tcp` over mDNS ([RFC 6762](https://www.rfc-editor.org/rfc/rfc6762)),
on a port that serves its passport (section 5.1) and its talk inbox (section 6.2). The TXT record:

| Key | Value |
|---|---|
| `v` | `2`, the version of this TXT record |
| `id` | the robot key's short id |
| `sha` | the first 16 characters of the passport's digest |
| `bt` | the beacon id the robot advertises now (section 5.6), replaced when it rotates |
| `path` | the passport's path, `/.well-known/robot-passport.json` when absent |

Each pair MUST be under 200 bytes. A reader MUST NOT trust the instance name, and MUST ignore a
record of another `v`, the `1` of 0.1 included.

### 5.4 MQTT

Removed in 1.0. A broker says nothing about which robots are near each other.

### 5.5 ROS 2

Removed in 1.0, for the same reason.

### 5.6 Bluetooth LE

<a id="ble-service"></a>
<a id="ble-rx"></a>
<a id="ble-tx"></a>

**The UUIDs.** Three 128-bit UUIDs, each the version 5 UUID
([RFC 9562](https://www.rfc-editor.org/rfc/rfc9562)), in the URL namespace, of this page's URL,
`https://github.com/rokbenko/quackd/blob/main/docs/passport/spec.md#`, and the name. A version 5 UUID
cannot fall in the Bluetooth Base UUID range, so they need no registration.

| Name | UUID | What it is |
|---|---|---|
| `ble-service` | `b68a53ea-46e1-50d4-81a3-039a0500a15d` | the GATT service, and the UUID the advert's service data goes under |
| `ble-rx` | `ba6288b5-e97c-51d2-a7b4-9cd04f5ad64c` | the characteristic a central writes frames to, with write and write without response |
| `ble-tx` | `f5d0526f-21e9-599f-8454-cda57ec39c1b` | the characteristic a peripheral notifies frames on |

**The advert.** A robot that takes part in meetings advertises a legacy advert of exactly 31 bytes:
the Flags structure (3 bytes), then a Service Data structure with a 128-bit UUID, AD type `0x21`:
its length and type (2 bytes), `ble-service` little-endian (16 bytes) and 10 bytes of service data.
It MUST carry no name, no transmit power and nothing else.

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | version, `0x01` |
| 1 | 1 | flags: bit 0 `LAN`, look this robot up by DNS-SD; bit 1 `LINK`, it accepts a link now; bit 2 `BUSY`, it is at a link or session limit. Other bits are sent as 0 and ignored. |
| 2 | 8 | the beacon id |

A reader MUST ignore service data shorter than 10 bytes or of another version, and bytes past the
tenth, which a later minor version may add.

**The beacon id.** A robot MUST draw each beacon id as 8 random bytes from a cryptographically secure
source, derived from no key and no earlier id. It SHOULD draw a new one every 15 minutes, and MUST NOT
while a link, a talk session or a meeting is open. The advert, the TXT record's `bt` (section 5.3)
and the `beacon` its `hello` carries (section 6.3) change together. A reader MUST NOT recognise a
robot by its beacon id past a meeting: a robot is recognised by its keys.

**Near, and who dials.** How strong a signal makes a robot near is the reader's own policy, and
signal strength MUST NOT be taken as proof of identity or place: a relay makes a far robot read as
near. Of two advertising robots that are near, the one whose beacon id is lower, as hex, dials, and
the other SHOULD wait before it dials too. A robot that does not advertise dials.

**The link.** A robot that dials and shares no LAN with the other (section 6.2) connects as a GATT
central to its `ble-service`, subscribes to `ble-tx` before it sends anything, and writes to
`ble-rx`. Each write and notification is one frame, 20 to 512 bytes as the connection allows. A
frame begins with a byte holding the framing version 1 in its high four bits and its kind in the low
four, then a 4-byte tag the central draws at random when it opens the link, never 0, so a central
drops frames notified to another. Numbers are big-endian.

| Kind | Frame | Sent by | After the tag |
|---|---|---|---|
| 1 | OPEN | central | its beacon id (8), the largest datagram it takes (2), the largest message it takes (4) |
| 2 | ACCEPT | peripheral | its beacon id (8), the largest datagram it takes (2), the largest message it takes (4) |
| 3 | REFUSE | peripheral | a reason (1): 1 busy, 2 version, 3 policy |
| 4 | DATA | either | a message id (2), with bit 15 set when the peripheral sent the request, and a sequence number (2). The first frame of a message, sequence 0, adds its role (1), 0 request or 1 response; a status (2), 0 for a request and an HTTP status code for a response; its media (1), 0 none, 1 `application/jose`, 2 `application/json` or 3 text; and its total size (4). Then the message's next bytes. |
| 5 | ABORT | either | a message id (2) and a reason (1): 1 too large, 2 out of order, 3 timed out, 4 overflow, 5 too many |
| 6 | CLOSE | either | a reason (1): 0 done, 1 far, 2 idle, 3 shutdown, 4 protocol |

A central MUST close a link whose ACCEPT names another beacon id than the advert it dialled. A
request carries one talk message and its response the reply and status HTTP would carry
(section 6.2). A robot MUST hold to these bounds, and refuse or abort anything outside them:

- a message of at most 16,384 bytes, refused at its first frame when its stated size is larger;
- the frames of a message in sequence, each within 3 seconds of the last, the whole within 15;
- at most 4 messages half received at once on a link, and at most 4 of a link's requests awaiting
  their answers, the next answered `503`;
- at most 4 links at once on a peripheral;
- a first request, which is a `hello`, within 30 seconds of a link opening;
- at most 20 requests every 10 seconds on one link.

### 5.7 Fetch rules

A reader that fetches a passport, or sends a message, on its own initiative rather than because a
person asked for that URL:

- MUST fetch only from the host and port that advertised it, or from a URL a person typed.
- MUST NOT follow redirects, MUST stop reading a body at 12,288 bytes, which holds the JWS of the
  largest passport, and SHOULD give up after 5 seconds for the whole exchange, however slowly the
  bytes arrive.
- MUST NOT contact any address a passport or a message names. A reply goes back only the way its
  message came: in the HTTP response, to the address a `hello` came from at its `reach.http_port`,
  or over the same Bluetooth link.

### 5.8 Caching

A reader MUST NOT cache an unsigned passport by its `id`, because anybody can claim any `id`. It MAY
cache a verified signed passport, keyed by `id` and digest, and MUST NOT use it past its `expires`.

## 6. Talk

### 6.1 Messages

Every message is a compact JWS of a JSON object, signed by the sender's robot key, whose protected
header has `alg` as section 4.4 gives it, `kid` equal to `from`, and `typ` `"robot-passport-msg+jws"`.
A receiver MUST check `typ`, so a signature made for one purpose is never accepted for another, and
MUST refuse a message that is not signed. A message is at most 4,096 bytes, and a `hello`, which
carries a passport, at most 16,384 ([schema/message.schema.json](schema/message.schema.json)):

| Member | Rule |
|---|---|
| `passport` | the protocol version |
| `type` | `hello`, `say`, `bye`, `ask`, `answer` or `share`. A receiver ignores any other type. |
| `from` | the sender's robot key, its passport's `id` |
| `to` | the receiver's `id`, or `*` when the sender does not know it yet, as in a first `hello` to a robot heard only by radio |
| `msg` | a random token of 16 to 64 base64url characters, unique per sender |
| `re` | the `msg` this one replies to |
| `nonces` | the session's two nonces, the initiator's first (section 6.3) |
| `body` | an object, by type |

### 6.2 Channels

The robot that dials picks one of two:

- **HTTP**, when both are on one LAN: it POSTs one message, as `application/jose`, to
  `/robot-passport/inbox` on the host and port of the DNS-SD record (section 5.3) whose `bt` is the
  beacon id it heard. The response is `200` with one reply message in its body, `204` with none, `400`
  for a malformed message, `403` for one refused by policy, `413` for one too large, or `429` when a
  budget is spent.
- **A Bluetooth link** (section 5.6), when they share no LAN: the same request, reply and status, in
  frames.

### 6.3 hello

Three messages, so that each side checks the other's passport, proves it holds its robot key, and
cannot have that proof replayed. Nonces are 16 random bytes in base64url.

1. A sends `hello` with `body` `{passport, nonce}` and its runtime members: its passport as the JWS
   its owner signed, which travels in the hello since a receiver fetches nothing a message names
   (section 5.7), and its nonce `nA`.
2. B checks A's passport, then replies `hello` with `re` the first `msg`, `nonces` `[nA, nB]` and
   `body` `{passport}` with its own runtime members, signed by B.
3. A checks B's passport, then sends `hello` with `re` the second `msg`, `nonces` `[nA, nB]` and an
   empty `body`, signed by A.

A receiver MUST refuse a `hello` whose passport does not pass section 3, 4.4 and 4.5, is not the
passport of the robot that sent it (`id` equal to `from`), is the receiver's own, or carries a
required extension the receiver does not understand. It MUST NOT send anything else to a robot
before the third message.

The **runtime members** change while a robot runs, so its owner never signs them; the robot key
does, with the rest of the message:

| Member | Rule |
|---|---|
| `software` | 1 to 64 characters: what wrote the `hello`, for a person reading a log |
| `beacon` | the beacon id the sender advertises now, 16 lowercase hex characters (section 5.6) |
| `reach` | an object of at most 4 scalar members: `http_port`, an integer from 1 to 65,535, the port of the sender's inbox on the address the `hello` came from |

A receiver SHOULD answer a first `hello` that arrives over HTTP only when it heard that hello's
`beacon` over Bluetooth seconds before, and `403` otherwise, so the LAN carries talk but never
decides who is near.

After the third message each side has seen the other sign both nonces and both ids, and the session
is `(A, B, nA, nB)`. Every later message in it MUST carry `nonces`, and a receiver MUST refuse one
whose nonces match no session of its own, which makes a replayed message fail. A receiver MAY end an
idle session, and quackd ends one after ten minutes.

### 6.4 say and bye

`say` carries `{text}` of 1 to 400 characters, for people and for language models, and it is never
an instruction. `bye` carries an empty `body` and ends the session.

### 6.5 ask and answer

`ask` carries `{skill, args?, why?}`: a name, an object, and text 120. It MUST belong to a session.
`answer` carries `{result, reason?, versions?}`, with `re` the message it answers: `result` is `ok`,
`no` or `later`, `reason` text 120, and `versions` the versions a receiver accepts when it could not
read the one it was sent. `pause` takes `{seconds}`, an integer from 1 to 10, and `identify` an
OPTIONAL `{how}`: `light`, `sound` or `motion`.

A receiver:

- MUST decide every `ask` by its own local policy, and MUST NOT let an `ask` reach anything that
  moves its robot except through a rule that bounds the action in time and in how often it happens.
- MUST NOT hand an `ask` to a language model as an instruction.
- MUST keep its own means of stopping above any peer's request.
- SHOULD answer within 5 seconds, or not at all.

### 6.6 share

`share` carries `{about, fact, place?, age_s?, confidence?}`: text 64, text 200, text 64, a
non-negative integer, and a confidence as in section 2.3. Two robots that have just met share no
frame of reference, so a `place` names somewhere both can recognise, and a `share` carries no
coordinates.

### 6.7 Versions

A receiver that cannot read a message's version answers `answer` with `result` `no`, `reason`
`version` and `versions` listing what it reads: a 1.0 robot answers a 0.1 message with `versions`
`["1.0"]`.

## 7. Versions of this specification

The version is `MAJOR.MINOR`. A new MINOR only adds: OPTIONAL members, values, runtime members,
message types, advert flags. A reader of one MINOR therefore reads every later MINOR of its MAJOR,
ignoring what it does not know. A change that would make an existing reader misread a document takes
a new MAJOR.

Version 0 was the draft line, and 0.1, which quackd 0.23.0 to 0.28.0 implemented, its only version.
A 0.1 robot signed its own passport, so a 0.1 reader would misread one its owner signed, and 1.0 is a
new MAJOR: each refuses the other under rule 4.

## 8. Conformance

A conforming robot carries a passport its owner signed, within its dates (sections 4.4, 4.5);
advertises a beacon (section 5.6), and MAY announce a DNS-SD record on a LAN (section 5.3); answers a
`hello`, checks the other robot's passport before it sends anything else, and signs every message
(sections 6.1, 6.3); and decides every `ask` by a bounded rule (section 6.5). A conforming reader
applies section 3, verifies signatures and dates as section 4 says, follows the fetch rules of
section 5.7 and caches as section 5.8 allows.

## 9. IANA considerations

The suffixes `robot-passport.json` and `robot-passport.jws` are to be registered in the Well-Known
URIs registry ([RFC 8615](https://www.rfc-editor.org/rfc/rfc8615)), and the service name
`robot-passport` in the Service Name and Transport Protocol Port Number Registry
([RFC 6335](https://www.rfc-editor.org/rfc/rfc6335)). Neither request has been made yet. The
Bluetooth UUIDs need no registration (section 5.6), and this specification defines no media type of
its own.

## 10. Security and privacy considerations

The whole threat model is in [security.md](security.md) and the privacy analysis in
[privacy.md](privacy.md). What this specification requires, in one place:

- A passport is a claim its owner signed, never its robot, and proves who said it, not that it is
  true (rule 11, section 4.4). It holds only between its dates (section 4.5).
- Text in a passport or a message is data, never an instruction (rule 12, section 6.4).
- Every message is signed (section 6.1), both robots check each other first, and a session's nonces
  make replays fail (section 6.3). Signatures are domain-separated by `typ`.
- A reader fetches only what it was pointed at, and replies only the way a message came
  (section 5.7). A key nobody could hold (section 4.1) and duplicate members (rule 2) are refused.
- No `ask` moves a robot except through a bounded rule, or keeps it from stopping (section 6.5).
- The beacon carries a rotating random id and no name, owner or key, signal strength is never proof,
  and a link holds to fixed bounds (section 5.6).

## 11. References

- [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119), [RFC 8174](https://www.rfc-editor.org/rfc/rfc8174): requirement words
- [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259): JSON
- [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339): date-times
- [RFC 4648](https://www.rfc-editor.org/rfc/rfc4648): base64url, section 5
- [RFC 7515](https://www.rfc-editor.org/rfc/rfc7515): JWS
- [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032): Ed25519
- [RFC 9864](https://www.rfc-editor.org/rfc/rfc9864): the `Ed25519` algorithm name
- [RFC 9562](https://www.rfc-editor.org/rfc/rfc9562): UUIDs, version 5 among them
- [RFC 8615](https://www.rfc-editor.org/rfc/rfc8615): well-known URIs
- [RFC 6762](https://www.rfc-editor.org/rfc/rfc6762), [RFC 6763](https://www.rfc-editor.org/rfc/rfc6763): mDNS, DNS-SD
- [RFC 6335](https://www.rfc-editor.org/rfc/rfc6335): service names
- [The did:key method](https://w3c-ccg.github.io/did-key-spec/), a W3C Credentials Community Group draft
- [The Bluetooth Core Specification](https://www.bluetooth.com/specifications/specs/core-specification-6-0/), and its Supplement for the advertising data types

## Appendix A: changes from 0.1

- **The owner signs.** `owner` and its `id` are REQUIRED, and the JWS's `kid` is `owner.id`, never
  the robot's `id` (section 4.4). Stamps, which vouched for a key and never for a passport, are
  gone, with their schema and `typ`.
- **Dates and the board.** `issued` is REQUIRED, `expires` is new (section 4.5), and so is `board`.
- **Removed members:** `software`, now a runtime member of the `hello` (section 6.3), `talk`,
  `stamps` and `ttl_s`, which a 1.0 reader ignores as unknown.
- **Removed:** the index (2.7), `urn:uuid` ids (4.1), QR codes and NFC tags with `#rp=` (5.2), the
  MQTT binding (5.4), the ROS 2 binding (5.5), and unsigned messages (6.1).
- **New:** Bluetooth LE, normative (5.6); DNS-SD TXT version `2`, with `bt` (5.3); both robots
  checking each other in the `hello`, with runtime members (6.3); replies only the way a message
  came (5.7).
