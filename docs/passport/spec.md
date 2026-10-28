# Robot Passport v0.1: the specification

> [!NOTE]
> Version 1.0 (quackd 0.29.0) adds three members: `owner` gains `id`, the key that signs the
> passport; `board` says the computer the robot runs it on, as `jetson`, `sim` or `other`; and
> `expires` says until when the owner's signature holds, at most 400 days after `issued`.

> [!NOTE]
> Draft 0.1, written 2026-10-21. This page is normative: the other pages in this folder explain,
> this one decides, and where one of them disagrees with it, that page is the bug. quackd is the
> first implementation; [quackd.md](quackd.md) says what of it quackd does today, and
> [roadmap.md](roadmap.md) what is built and what has run.

The key words MUST, MUST NOT, REQUIRED, SHOULD, SHOULD NOT, RECOMMENDED, MAY and OPTIONAL are to
be read as BCP 14 ([RFC 2119](https://www.rfc-editor.org/rfc/rfc2119),
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174)) describes, when, and only when, they appear in
capitals.

Beside this page: the JSON Schemas in [schema/](schema/), and the conformance vectors in
[vectors/](vectors/README.md), each with the result a reader must reach.

## 1. Terms

- **Robot**: a machine that moves, or that can be moved by software, whatever its maker.
- **Passport**: one JSON document a robot publishes about itself (section 2).
- **Writer**: whoever produces a passport: the robot, its software, or its owner on a laptop.
- **Reader**: whoever fetches and interprets one: another robot, a person's phone, a tool.
- **Beacon**: anything that tells a reader a passport exists and where (section 5).
- **Stamp**: a statement signed by another key, vouching for a robot's key (section 4.5).
- **Peer**: a robot that talks to another one (section 6).
- **Short id**, **digest**: two fingerprints, of a key and of a document (sections 4.2, 4.3).

[glossary.md](glossary.md) has every term the folder uses.

## 2. The passport document

### 2.1 Encoding

A passport is a JSON object ([RFC 8259](https://www.rfc-editor.org/rfc/rfc8259)) encoded in
UTF-8, with no byte order mark. A writer SHOULD keep it under 2,048 bytes. A reader MUST accept
up to 8,192 bytes and SHOULD refuse anything larger.

It is also I-JSON ([RFC 7493](https://www.rfc-editor.org/rfc/rfc7493)): a reader MAY refuse a
number an IEEE double cannot hold exactly, or a document nested deeper than it can read. A whole
number written with a fraction or an exponent, `3600.0` or `3.6e3`, is that integer wherever an
integer belongs, the way JSON Schema reads it.

### 2.2 Members

Three members are REQUIRED; every other one is OPTIONAL. "Text N" means a JSON string of at most
N characters.

| Member | Type | Rule |
|---|---|---|
| `passport` | string `MAJOR.MINOR` | REQUIRED. The protocol version, `"0.1"` for this one. |
| `id` | string | REQUIRED. A `did:key` (section 4.1), or for an unsigned passport a `urn:uuid` ([RFC 9562](https://www.rfc-editor.org/rfc/rfc9562)). |
| `name` | text 64, at least 1 | REQUIRED. For people. Need not be unique. |
| `maker` | object | `vendor`, `model`, `serial`: each text 64. |
| `software` | object | `name`, `version`: each text 64. |
| `owner` | object | `label` text 64; `contact` text 200, a URL, a `mailto:` address or a role address. |
| `body` | object | The body, section 2.3. |
| `skills` | array of names | At most 64. Each matches `[a-z][a-z0-9_]{0,31}`. Section 2.4. |
| `courtesy` | array of names | What a peer may ask for, section 2.4. At most 8. |
| `cannot` | array of text 120 | What it cannot do, whatever a task says. At most 8, none empty. |
| `safety` | object | Section 2.5. |
| `talk` | array of channels | At most 8. Each `{via, url, max_bytes?}`: `via` text 16, `url` text 200, `max_bytes` an integer of at least 1,024. Section 6.2. |
| `stamps` | array of strings | At most 4 compact JWS stamps, each at most 1,024 characters. Section 4.5. |
| `ext` | object | Extensions, section 2.6. |
| `issued` | string | An [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339) date-time. Advisory. |
| `ttl_s` | integer, 60 to 86,400 | How long a reader may cache it, counted from the fetch. Default 3,600. |

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
says so), `estimate` or `measured`. A figure that is absent is not published, and a reader MUST
NOT read an absent figure as zero. Units are SI and live in the member's name.

`max_speed_mps` is the fastest the robot will move as its software is configured now, which is
what another body sharing the floor needs to know.

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

A writer whose robot can be stopped by its software SHOULD list `stop`.

`courtesy` lists what a peer may ask for (section 6.5). Two names are defined:

- `pause`: it stops moving for at most the number of seconds asked, then resumes its own plan.
- `identify`: it makes itself known to the people and cameras nearby, by light, sound or a small
  motion.

A reader MUST ignore a courtesy name it does not know.

### 2.5 Safety

| Member | Type | Meaning, and how a reader reads it when it is missing, unknown or of the wrong type |
|---|---|---|
| `stops_on_silence` | boolean | `true` when its motion stops by itself once its controller goes quiet. Anything but `true` reads as `false`. |
| `estop` | string | `button` (a stop button on the body), `remote` (a separate stop device), `power` (only by cutting its power) or `none`. Anything else reads as `none`. |
| `how_to_stop` | text 120 | For a person, in a sentence. Anything that is not such a string reads as absent. |

A reader MUST read `safety` as the worst case wherever a value is missing, unknown or of the
wrong type, and MUST NOT refuse a passport for a bad value in `safety` alone. A garbled value
never makes a robot look safer, and never hides one either.

### 2.6 Extensions

`ext` maps a reverse-DNS name (`org.example.lift`) to an object. A reader MUST ignore an extension
it does not know, unless the object has `"required": true`. A reader that does not understand a
required extension MAY show the passport and MUST NOT talk to the robot on the strength of it.
A `required` that is anything but `true` is read as not required.

quackd's own extension is `org.quackd`.

### 2.7 The index

A host that fronts several robots MAY serve an **index** in place of a passport:

```json
{"passport":"0.1","robots":[{"name":"duck-01","id":"did:key:z6Mk...","url":"/robots/duck-01/robot-passport.json"}]}
```

An index has `robots` and no `id`, which is how a reader tells it from a passport. `robots` holds
1 to 32 entries, each with a REQUIRED `url` (text 200) and an OPTIONAL `name` and `id`. Every
`url` MUST name the host the index came from. A reader resolves each against the URL it fetched the
index from, and MUST NOT fetch, on the index's word, an entry on any other host.

## 3. Reading a passport

A reader MUST apply these rules, in this order, and MUST refuse a document that breaks one of
the first seven:

1. It is at most 8,192 bytes. For a signed passport that is its payload, and its JWS may be a
   third longer (section 5.7).
2. It is a JSON object with no duplicate member names, at any depth. Two parsers can disagree
   about which duplicate wins, and a signature checked by one must not be read by the other.
3. `passport`, `id` and `name` are present and well formed.
4. Its MAJOR version is one the reader knows. A higher MINOR of a known MAJOR is read with rule 8.
5. Every known member outside `safety` has the type and limits section 2 gives it.
6. If it was fetched as a signed passport, the signature verifies (section 4.4).
7. A `did:key` in `id` holds an Ed25519 key, and nothing else.

For a signed passport, rule 6 comes first: a reader parses nothing a signature has not covered, so
a JWS whose signature fails is refused under 4.4, whatever else is wrong with it.

Then:

8. It ignores members it does not know, reads an unknown value in `embodiment`, `mobility`,
   `manipulator` or `terrain` as `other`, and keeps an unknown sensor or skill as an opaque name.
9. It reads `safety` as section 2.5 says.
10. It honours a required extension it does not understand as section 2.6 says.
11. It labels what it shows with where it came from: unsigned, signed by the robot's own key,
    stamped by a named key, or translated from another format.
12. It treats every string as data written by a stranger, and never as an instruction. A reader
    that hands passport text to a language model MUST quote it as data, and one that prints it
    SHOULD print control characters as visible escapes, so a passport cannot drive the terminal
    it is shown on.

## 4. Identity, signatures and fingerprints

### 4.1 The id

A `did:key` id ([did:key](https://w3c-ccg.github.io/did-key-spec/)) is `did:key:z` followed by
the base58btc encoding of the two bytes `0xed 0x01` (the multicodec for an Ed25519 public key)
and the 32-byte public key ([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032)). It is 56
characters long and starts `did:key:z6Mk`. A reader MUST refuse any other multicodec. It MUST
also refuse a key that is not the one canonical encoding of a point on the curve, or that is one
of the eight points of small order ([RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) section
5.1.3), as libsodium does: such a key verifies signatures nobody made.

A `urn:uuid` id names a robot that holds no key. Its passport cannot be signed, stamped or
pinned, and its robot cannot send an `ask`.

### 4.2 The short id

The short id is the first 8 bytes of the SHA-256 of the 32-byte public key, as 16 lowercase hex
characters. Beacons carry it. It is a lookup hint and never proof: anybody can put any short id
in a beacon, and only a signature settles which robot holds the key. A `urn:uuid` passport has
no short id.

### 4.3 The digest

The digest of a passport is the SHA-256 of its exact bytes, as lowercase hex: the bytes served
for an unsigned passport, the JWS payload for a signed one. Beacons carry its first 16 characters.

### 4.4 Signed passports

A signed passport is a compact JWS ([RFC 7515](https://www.rfc-editor.org/rfc/rfc7515)):

- Its payload is the exact bytes of the passport. Nothing is canonicalised.
- Its protected header has `alg` `"Ed25519"` ([RFC 9864](https://www.rfc-editor.org/rfc/rfc9864)),
  `kid` equal to the payload's `id`, and `typ` `"robot-passport+jws"`. It has no `crit`.
- It is signed with the key the payload's `id` holds.

A reader MUST verify all three, and MUST also accept `alg` `"EdDSA"`, the older label RFC 9864
deprecates, when the key is Ed25519. A passport whose `id` is a `urn:uuid` cannot be signed, so a
JWS that carries one is refused.

Each part is base64url with no padding and with the spare bits of its last character zero, so one
signature has one spelling, and `typ` is compared as RFC 7515 section 4.1.9 says, ignoring case
and an `application/` prefix. A reader that fetched a `.jws` URL MUST refuse a body that is not a
JWS, or a host could talk it down from a signed passport to a claim without its noticing.

Wherever a signed passport travels, inside a message, in a cache or on a broker, the JWS string
travels unchanged.

### 4.5 Stamps

A stamp is a compact JWS whose protected header has `alg` as section 4.4 gives it, `kid` equal to
`iss`, and `typ` `"robot-passport-stamp+jwt"`, and whose payload is a JSON object:

| Claim | Meaning |
|---|---|
| `iss` | the `did:key` of whoever stamps |
| `sub` | the `did:key` of the robot stamped |
| `rel` | `owner`: `iss` owns the robot. `maker` is reserved for a later version. |
| `iat` | issued at, seconds since the epoch |
| `exp` | expires at, seconds since the epoch, after `iat`. At most a year is RECOMMENDED. |

A stamp says one thing: the holder of `iss` vouches that `sub` is its robot. It says nothing about
the passport's other members, so a robot can change its passport without a new stamp. On an
unsigned passport it proves nothing at all, because nothing shows that key `sub` wrote that
passport: anybody can copy an `id` and a stamp into a document of their own.

A reader counts a stamp only when the passport carrying it is signed, its signature verifies with
the key `iss` holds, `sub` equals the passport's `id`, `rel` is one the reader knows, `exp` is in
the future and `iat` is no more than five minutes ahead of the reader's clock. Whether to trust
`iss` at all is the reader's own decision ([trust.md](trust.md)), and nothing in a passport can
make it. A stamp whose `iss` is its own `sub` is a key vouching for itself, and never counts.

## 5. Beacons and bindings

### 5.1 The well-known URL

Over HTTP a passport is at `/.well-known/robot-passport.json` (an unsigned passport, the payload
of a signed one, or an index) with media type `application/json`, and a signed one is also at
`/.well-known/robot-passport.jws` with media type `application/jose`. HTTPS is RECOMMENDED on the
internet. Plain HTTP is acceptable on a local network, because what proves a passport is its
signature and not its transport.

### 5.2 QR codes and NFC tags

A QR code or an NFC tag (an NDEF URI record) holds a URL of the passport, OPTIONALLY followed by
`#rp=` and the short id. A reader that finds `#rp=` MUST check that the passport's short id
matches, and that the passport is signed: a short id names a key, and only a signature shows that
key wrote the passport. The fragment is never sent to the server, so a static host needs no
configuration.

### 5.3 DNS-SD

On a local network a robot MAY advertise the DNS-SD ([RFC 6763](https://www.rfc-editor.org/rfc/rfc6763))
service type `_robot-passport._tcp` over mDNS ([RFC 6762](https://www.rfc-editor.org/rfc/rfc6762)).
The port is one that serves the passport over HTTP. The TXT record holds:

| Key | Value |
|---|---|
| `v` | `1`, the version of this TXT record |
| `id` | the short id, absent for a `urn:uuid` passport |
| `sha` | the first 16 characters of the digest |
| `path` | the passport's path, `/.well-known/robot-passport.json` when absent |

Each pair MUST be under 200 bytes. A reader MUST NOT trust the instance name.

### 5.4 MQTT

On a broker, under `robot-passport/<key>/`, where `<key>` is the short id, or for a `urn:uuid`
passport its 32 hex digits:

| Topic | QoS | Retained | Payload |
|---|---|---|---|
| `passport` | 1 | yes | the JWS, or the JSON of an unsigned passport |
| `status` | 1 | yes | `online` on connect, and `offline` as the connection's last will |
| `inbox` | 1 | no | talk messages to this robot, section 6.2 |

A reader MUST check that what it reads under `<key>` is that robot's passport: its `id`'s short id
is `<key>`, or for a `urn:uuid`, its 32 hex digits are.

### 5.5 ROS 2

On a ROS 2 graph, the topic `robot_passport` in the robot's namespace, type
`std_msgs/msg/String`, with reliability `RELIABLE`, durability `TRANSIENT_LOCAL` and history
`KEEP_LAST` of depth 1, so a node that subscribes late still receives it. `data` holds the JWS, or
the JSON of an unsigned passport.

### 5.6 Bluetooth LE (reserved)

Version 0.1 defines no Bluetooth binding. A later version is expected to put the version, the
short id and part of the digest in 13 bytes of Service Data under a 128-bit UUID, once beacon ids
can rotate ([discovery.md](discovery.md#bluetooth-le-later)).

### 5.7 Fetch rules

A reader that fetches a passport on its own initiative, rather than because a person asked for
that URL:

- MUST fetch only from the host and port that advertised it, or from a URL a person typed or a
  camera or tag read.
- MUST NOT follow redirects, MUST stop reading a body at 12,288 bytes, which holds the JWS of the
  largest passport, and SHOULD give up after 5 seconds for the whole exchange, headers and all,
  however slowly the bytes arrive.
- MUST NOT contact any URL a passport or an index mentions, other than an index entry on the
  same host, unless a person or the reader's own policy allows that URL.

### 5.8 Caching

A reader MUST NOT cache an unsigned passport by its `id`, because anybody can claim any `id`. It
MAY cache a verified signed passport, keyed by `id` and digest, for `ttl_s` seconds from the fetch
(3,600 when absent, never more than 86,400), and SHOULD fetch again when a beacon carries a
different digest.

## 6. Talk

### 6.1 Messages

A message is a JSON object of at most 4,096 bytes, or what a channel's `max_bytes` allows. A
`hello`, which carries a whole passport, may be up to 16,384 bytes, the most the JWS of the largest
passport takes once it is inside a message. Its members are
([schema/message.schema.json](schema/message.schema.json)):

| Member | Rule |
|---|---|
| `passport` | the protocol version |
| `type` | `hello`, `say`, `bye`, `ask`, `answer` or `share`. A receiver ignores any other type. |
| `from` | the sender's `id` |
| `to` | the receiver's `id`, or `*` for anyone on a shared channel |
| `msg` | a random token of 16 to 64 base64url characters, unique per sender |
| `re` | the `msg` this one replies to |
| `nonces` | the session's two nonces, the initiator's first (section 6.3) |
| `body` | an object, by type |

A signed message is a compact JWS whose payload is that object, and whose protected header has
`alg` as section 4.4 gives it, `kid` equal to `from`, and `typ` `"robot-passport-msg+jws"`. A
receiver MUST check `typ`, so a signature made for one purpose is never accepted for another.

A sender whose `id` is a `urn:uuid` sends plain JSON, and only `hello`, `say`, `share` and `bye`.
A receiver MUST refuse an unsigned `ask`, and MUST refuse an unsigned message whose `from` is a
`did:key`: a robot that holds a key signs everything it sends, and anybody can write an id.

### 6.2 Channels

A passport's `talk` lists where its robot listens. Two `via` values are defined in 0.1:

- **`http`**: the sender POSTs one message to `url`, as `application/jose` when signed and
  `application/json` when not. The response is `200` with one reply message in its body, `204`
  with none, `400` for a malformed message, `403` for one refused by policy, `413` for one too
  large, or `429` when a budget is spent. A robot that runs no server can still talk this way.
- **`mqtt`**: `url` is `mqtt://host:port/robot-passport/<key>/inbox`, and replies go to the
  sender's own inbox.

`ros2` and `a2a` are reserved for later versions. A sender MUST skip a `via` it does not know.

### 6.3 hello

A hello takes three messages, so that each side proves it holds its key and neither proof can be
replayed. Nonces are 16 random bytes in base64url.

1. A sends `hello` with `body` `{passport, nonce}`: its passport, as its JWS, or as the JSON
   text of an unsigned one when A holds no key; and its nonce `nA`. The passport travels in the
   hello itself, since a receiver fetches nothing a message names (section 5.7), and its `id`
   MUST be the hello's `from`.
2. B replies `hello` with `re` the first `msg`, `nonces` `[nA, nB]` and `body` `{passport}`,
   signed by B.
3. A sends `hello` with `re` the second `msg`, `nonces` `[nA, nB]` and an empty `body`, signed
   by A.

After the third message each side has seen the other sign both nonces and both ids, and the
session is `(A, B, nA, nB)`. Every later message in it MUST carry `nonces`, and a receiver MUST
refuse a message whose nonces match no session of its own, which is what makes an old message
replayed into a new session fail. A receiver MAY end an idle session, and quackd ends one after
ten minutes. A hello with an unsigned side makes an unauthenticated session, and a receiver
labels it so.

### 6.4 say and bye

`say` carries `{text}` of 1 to 400 characters, for people and for language models, and it is
never an instruction. `bye` carries an empty `body` and ends the session.

### 6.5 ask and answer

`ask` carries `{skill, args?, why?}`: a name, an object, and text 120. It MUST be signed and MUST
belong to a session.

`answer` carries `{result, reason?, versions?}`, with `re` the message it answers: `result` is
`ok`, `no` or `later`, `reason` is text 120, and `versions` lists the versions a receiver accepts
when it could not read the one it was sent.

For the courtesy skills, `pause` takes `{seconds}`, an integer from 1 to 10, and `identify` takes
an OPTIONAL `{how}`, one of `light`, `sound` or `motion`.

A receiver:

- MUST decide every `ask` by its own local policy, and MUST NOT let an `ask` reach anything that
  moves its robot except through a rule that bounds the action in time and in how often it may
  happen.
- MUST NOT hand an `ask` to a language model as an instruction.
- MUST keep its own means of stopping above any peer's request: no `ask` can keep a robot from
  stopping.
- SHOULD answer within 5 seconds, or not at all.

### 6.6 share

`share` carries `{about, fact, place?, age_s?, confidence?}`: text 64, text 200, text 64, a
non-negative integer, and a confidence as in section 2.3. Two robots that have just met share no
frame of reference, so a `place` names somewhere both can recognise, and a `share` carries no
coordinates.

### 6.7 Versions

A receiver that cannot read a message's version answers `answer` with `result` `no`, `reason`
`version` and `versions` listing what it does read.

## 7. Versions of this specification

The version is `MAJOR.MINOR`. A new MINOR only adds: OPTIONAL members, values, `via`s, message
types. A reader of one MINOR therefore reads every later MINOR of its MAJOR, ignoring what it
does not know. A change that would make an existing reader misread a document takes a new
MAJOR. Version 0 is the draft line, and the same rule holds inside it.

## 8. Conformance

| Level | A conforming robot |
|---|---|
| 0, Tag | publishes a passport at a URL, for example on a sticker |
| 1, Beacon | also advertises it through one binding of section 5 |
| 2, Signed | also signs it (section 4.4) and answers a `hello` (section 6.3) |
| 3, Talk | also accepts `say` and `ask` and answers them (sections 6.4, 6.5) |

A conforming reader applies section 3, verifies signatures and stamps as section 4 says, follows
the fetch rules of section 5.7 and caches as section 5.8 allows.

## 9. IANA considerations

The suffixes `robot-passport.json` and `robot-passport.jws` are to be registered in the Well-Known
URIs registry ([RFC 8615](https://www.rfc-editor.org/rfc/rfc8615)), and the service name
`robot-passport` in the Service Name and Transport Protocol Port Number Registry
([RFC 6335](https://www.rfc-editor.org/rfc/rfc6335)). Neither request has been made yet. This
specification defines no media type of its own.

## 10. Security and privacy considerations

The whole threat model is in [security.md](security.md) and the privacy analysis in
[privacy.md](privacy.md). What this specification requires, in one place:

- A passport is a claim. A signature proves who said it, never that it is true (rule 11).
- Text in a passport or a message is data, never an instruction (rule 12, section 6.4).
- A reader fetches only what it was pointed at, follows no redirects and reads no more than
  12,288 bytes of a body in 5 seconds (section 5.7).
- A key nobody could hold, small-order or no point at all, is refused (section 4.1).
- Duplicate members are refused, so two parsers cannot read two different documents under one
  signature (rule 2).
- Signatures are domain-separated by `typ` (sections 4.4, 4.5, 6.1).
- A session's nonces make replays fail (section 6.3).
- No `ask` moves a robot except through a bounded rule, and none can keep it from stopping
  (section 6.5).
- A beacon carries no owner, and every member that could identify a person is OPTIONAL.

## 11. References

- [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119), [RFC 8174](https://www.rfc-editor.org/rfc/rfc8174): requirement words
- [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259): JSON
- [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339): date-times
- [RFC 4648](https://www.rfc-editor.org/rfc/rfc4648): base64url, section 5
- [RFC 7515](https://www.rfc-editor.org/rfc/rfc7515): JWS
- [RFC 7519](https://www.rfc-editor.org/rfc/rfc7519): JWT claims
- [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032): Ed25519
- [RFC 9864](https://www.rfc-editor.org/rfc/rfc9864): the `Ed25519` algorithm name
- [RFC 9562](https://www.rfc-editor.org/rfc/rfc9562): UUIDs
- [RFC 8615](https://www.rfc-editor.org/rfc/rfc8615): well-known URIs
- [RFC 6762](https://www.rfc-editor.org/rfc/rfc6762), [RFC 6763](https://www.rfc-editor.org/rfc/rfc6763): mDNS, DNS-SD
- [RFC 6335](https://www.rfc-editor.org/rfc/rfc6335): service names
- [The did:key method](https://w3c-ccg.github.io/did-key-spec/), a W3C Credentials Community Group draft
- [MQTT 5.0](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html)
- [ROS 2 quality of service](https://docs.ros.org/en/rolling/Concepts/Intermediate/About-Quality-of-Service-Settings.html)
