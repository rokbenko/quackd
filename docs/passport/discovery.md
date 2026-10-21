# Discovery: how robots find each other's passports

> [!NOTE]
> Spec draft 0.1. quackd's implementation is being built for 0.23.0, and
> [roadmap.md](roadmap.md) says what of it is built and what has run.

Before one robot can read another's passport it has to know there is one, and where. That is the
**beacon**'s whole job. A beacon carries almost nothing, a pointer and two fingerprints, so it
fits wherever a robot can put a few bytes: a sticker, a local-network advertisement, a ROS topic,
a broker. The passport it points to carries everything else, and is what proves it
([trust.md](trust.md)).

The specification's own words are in [section 5](spec.md#5-beacons-and-bindings).

## One beacon, several ways to carry it

Every beacon carries the same four things, whatever carries it:

| Part | What it is |
|---|---|
| a version | so a reader knows how to read the rest |
| the short id | a fingerprint of the robot's key: the first 8 bytes of its SHA-256, as 16 hex characters |
| the digest | a fingerprint of the passport: the first 16 hex characters of its SHA-256 |
| where | a URL, a host and port, a topic |

The short id lets a reader recognise a robot it has met before without fetching anything. The
digest lets it skip a fetch when it already holds that exact passport. Neither proves anything: a
beacon is a claim anybody can make, and only the passport's signature settles which robot holds
the key.

| Binding | What it reaches | What the robot needs | In 0.1 |
|---|---|---|---|
| [the well-known URL](#the-well-known-url) | anything that speaks HTTP | an HTTP server, or a static host | yes |
| [a QR code or an NFC tag](#qr-codes-and-nfc-tags) | phones, and any robot with a camera | nothing at all | yes |
| [DNS-SD over mDNS](#dns-sd-on-a-local-network) | anything on the same local network | avahi, Bonjour or zeroconf | yes |
| [MQTT](#mqtt) | anything on the same broker | a broker connection | yes |
| [ROS 2](#ros-2) | anything on the same ROS graph | one publisher | yes |
| [Bluetooth LE](#bluetooth-le-later) | phones and robots in radio range | a BLE radio | later |

## The well-known URL

The passport itself lives at one path, on any host:

```
GET /.well-known/robot-passport.json    the passport, or an index, as application/json
GET /.well-known/robot-passport.jws     the signed passport, as application/jose
```

A signed robot serves both. The `.json` is for reading, and the `.jws` is for verifying: its
payload is the very same bytes, so a reader that verifies uses the payload and never needs the
`.json` at all. `/.well-known/` is the place [RFC 8615](https://www.rfc-editor.org/rfc/rfc8615)
reserves for exactly this, the same place A2A puts an agent's card.

Because it is just a file, the robot need not serve it. A passport on a static host, a GitHub
Pages site or a company web page works the same, and a robot whose owner did that has a passport
without running anything. HTTPS is right on the internet. Plain HTTP is acceptable on a local
network, because a passport is proved by its signature rather than by its transport.

A host that fronts several robots, a quackd laptop driving three of them for example, serves an
[index](passport.md#the-index-several-robots-behind-one-host) at the well-known URL, listing a
passport URL per robot, each on the same host.

## QR codes and NFC tags

The simplest beacon is printed. A QR code, or an NFC tag holding an NDEF URI record, holds the
passport's URL, optionally followed by the robot's short id:

```
https://example.org/robots/duck-01/robot-passport.json#rp=56475aa75463474c
```

That is about 60 bytes, which fits a 37×37 QR code (version 5), small enough for a sticker on a
25 cm robot. A reader that finds `#rp=` checks that the passport it fetched has that short id, so a
swapped sticker or a moved file is noticed. The part after `#` is never sent to the server, so a
static host needs nothing configured.

The sticker carries the short id and not the digest on purpose. A digest changes every time the
passport does, so a printed one would go stale on the first edit, while the key, and so the short
id, lasts as long as the robot does.

What a person sees when they scan it depends on their phone: today, the JSON itself.
[roadmap.md](roadmap.md) lists the page that will render any passport URL for a person. A robot
with a camera can read the same sticker, and quackd's `meet` reads a QR code from an image
([quackd.md](quackd.md)).

## DNS-SD on a local network

On a local network a robot advertises the service type `_robot-passport._tcp` over mDNS
([RFC 6762](https://www.rfc-editor.org/rfc/rfc6762), [RFC 6763](https://www.rfc-editor.org/rfc/rfc6763)),
on a port that really serves its passport over HTTP. Its TXT record:

| Key | Value |
|---|---|
| `v` | `1`, the version of this TXT record |
| `id` | the short id, absent for a robot that holds no key |
| `sha` | the first 16 characters of the passport's digest |
| `path` | the passport's path, `/.well-known/robot-passport.json` when absent |

Every pair stays under 200 bytes, the bound quackd's own `_quackd._tcp` record has kept since
[ADR-0021](../adr/0021-lan-discovery-and-mqtt-bus.md), and the whole record is a small fraction of
the 400 bytes RFC 6763 suggests keeping a TXT record under. The keys are short because they are
wire protocol forever once shipped. A reader never trusts the instance name, which is just a label
the advertiser chose.

quackd advertises this beside its own `_quackd._tcp` rather than instead of it. The quackd record
says "a quackd instance fronts a body with this manifest digest" to other quackd instances; this
one says "a robot with this passport is here" to anything at all, quackd or not.

## MQTT

A robot already on a broker publishes under `robot-passport/<key>/`, where `<key>` is its short id,
or for a robot that holds no key, the 32 hex digits of its UUID:

| Topic | QoS | Retained | Payload |
|---|---|---|---|
| `robot-passport/<key>/passport` | 1 | yes | the JWS, or the JSON of an unsigned passport |
| `robot-passport/<key>/status` | 1 | yes | `online` on connect, and `offline` as the connection's last will |
| `robot-passport/<key>/inbox` | 1 | no | messages to this robot ([talk.md](talk.md)) |

Retained means a subscriber that arrives later still gets the passport at once. The last will
means the broker itself says `offline` when the robot drops off without saying goodbye. Both are
the pattern VDA 5050 uses for automated guided vehicles, borrowed because it works
([prior-art.md](prior-art.md)).

## ROS 2

On a ROS 2 graph the robot publishes the topic `robot_passport` in its own namespace, type
`std_msgs/msg/String`, with reliability `RELIABLE`, durability `TRANSIENT_LOCAL` and history
`KEEP_LAST` of depth 1. `TRANSIENT_LOCAL` is what ROS 1 called latched: a node that subscribes an
hour later still receives the last passport published. `data` holds the JWS, or the JSON of an
unsigned passport.

A ROS 2 robot needs no new message type and no new dependency for this, which is the point. quackd
reads the topic through rosbridge when it is connected to a ROS robot ([quackd.md](quackd.md)), and
a small ROS 2 node that publishes it is on the [roadmap](roadmap.md).

## Bluetooth LE, later

Version 0.1 defines no Bluetooth binding, for two reasons.

**It would fit.** A legacy advertisement is 31 bytes. Service Data under a self-chosen 128-bit UUID
uses 18 of them, which leaves 13: one byte of version, the 8-byte short id and 4 bytes of digest.
That is in a scan response or a non-connectable advertisement; a connectable one also carries the
3-byte Flags field and leaves 10. A 16-bit UUID would leave far more room, but the Bluetooth SIG
assigns those for a fee, which is not worth paying before anybody uses the protocol.

**It would track.** A short id that never changes, broadcast by radio a few times a second, lets
anybody with a phone follow a robot, and so its owner, from street to street. Bluetooth itself
solved this for devices with addresses that rotate, and a passport beacon over Bluetooth should
wait until its ids can rotate too ([privacy.md](privacy.md#tracking)). It also needs hardware to
prove, and quackd ships no claim it has not run.

## Fetch rules

A beacon tells a reader where to look, and a careless reader would look wherever it is told. A
passport that names `http://192.168.1.1/admin`, or an index pointing at another machine, would
turn every reader on a network into a way to probe that network. So a reader that fetches on its
own initiative follows four rules ([spec section 5.7](spec.md#57-fetch-rules)):

- It fetches only from the host and port that advertised the passport, or from a URL a person
  typed or a camera or tag read.
- It follows no redirects.
- It stops reading at 8,192 bytes, and gives up after 5 seconds.
- It contacts no other URL a passport mentions, its `talk` channels included, unless a person or
  its own policy allows that URL. quackd's policy allows a `talk` URL only on the host the passport
  came from.

## Caching

A reader caches a passport only once it has verified it, keyed by its `id` and its digest, for
`ttl_s` seconds from the fetch: an hour unless the passport says otherwise, a day at most. When a
beacon carries a different digest, the reader fetches again.

An unsigned passport is never cached by its `id`. Anybody can claim any `id`, so a cache that took
an unsigned passport at its word would let one robot overwrite what a reader knows about another.
The cache counts time from the fetch rather than from `issued`, because a reader's own clock is
the only one it can trust, and a robot's clock may never have been set at all.
