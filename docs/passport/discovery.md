# Discovery: how robots find each other

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation. Nothing on this page has run over a real
> Bluetooth radio: the advert, the rotation and the link are tested in the suite and on the sim
> board, and [roadmap.md](roadmap.md) says what that proves.

Before two robots can check each other's passports, each has to know the other is there, and
close. That is the **beacon**'s whole job: a Bluetooth LE advert, a few bytes sent a few times a
second, that says a robot is here and nothing about which, while its signal strength says how near.
The passports cross later, over the LAN or a Bluetooth link, inside the `hello` that checks both
([talk.md](talk.md#hello-proving-who-you-are-in-three-steps)). The specification's own words are in
[section 5](spec.md#5-beacons-and-bindings).

## The Bluetooth LE beacon

<a id="bluetooth-le-later"></a>

A robot sends a legacy advertisement, which holds 31 bytes, and fills it exactly:

| Bytes | What they are |
|---|---|
| 3 | the Flags structure, which BlueZ adds to any advert a peripheral sends |
| 18 | the head of a Service Data structure: its length, its type `0x21`, and the passport's own 128-bit UUID |
| 10 | the beacon: a version byte, `0x01`, then a byte of flags, then the 8-byte beacon id |

The flags say how to reach the robot: `LAN`, look it up on the local network; `LINK`, it accepts a
Bluetooth link now; `BUSY`, it is at a limit on links or sessions. There
is no name in the advert, no transmit power, no owner and no part of any key. The UUID is not one
the Bluetooth SIG assigned: it is a version 5 UUID made from the specification's URL, which anybody
can recompute and which sits outside the range the SIG reserves ([naming.md](naming.md)).

### The beacon id changes every 15 minutes

The **beacon id** is 8 random bytes, drawn fresh and derived from no key. Every 15 minutes, or what
`radio.rotate_min` says in `robot.yaml`, the robot draws a new one, but never while a Bluetooth
link, a talk session or a meeting is open: then it tries again every 30 seconds until nothing is.
The advert, the robot's LAN record and the beacon its `hello` carries all change together.

A listener who wrote an id down an hour ago cannot match it to the robot by the id alone
([privacy.md](privacy.md#tracking)). Robots that meet again know each other by the keys in their
passports, and quackd keeps its cooldowns and refusals by `did:key`, never by beacon id, so a
rotation costs at most one more `hello`.

> [!WARNING]
> The beacon id is designed to rotate, and the rotation is tested against a clock in the suite.
> Nobody has run it over a real radio, or watched a Jetson's adapter send it. The radio's own address
> is a separate matter, which [privacy.md](privacy.md#tracking) covers.

### Who says hello first

The robot with the lower beacon id dials at once, and the other waits 15 seconds, then dials only if
nobody has. A robot that does not advertise dials at once. If both dial, talk keeps the newer session
and the older goes quiet.

## Near: signal strength

Every advert arrives with its received signal strength, RSSI, in dBm. A robot turns
`meet_within_m`, 2 m unless `robot.yaml` says otherwise, into a threshold with the usual
log-distance model: -59 dBm at 1 m, which is `rssi_at_1m`, falling off with a path-loss exponent of
2.2. At 2 m that makes the threshold -65.6 dBm.

- **Smoothed.** It takes the median of what it heard over the last 3 seconds, not the mean, because
  reflections make Bluetooth signal strength dip by 10 to 20 dB on one advertising channel or
  another, and a median shrugs a dip off.
- **Near** once that median holds at or above the threshold for 1 second.
- **Far** once it holds 6 dB below the threshold, -71.6 dBm for 2 m, for 5 seconds, or once nothing
  has been heard for 8 seconds. An open Bluetooth link keeps a quiet robot from going stale, and
  makes it far at once when the link drops.

Near starts the `hello`, and far ends the session: the robot says `bye` and closes the link.

### Near is a hint, not proof

- **Nobody has calibrated it.** The -59 dBm at 1 m is a default nobody has measured on a Jetson, and
  an antenna, a body or a wall moves a reading by more than the 6 dB between near and far. The first
  real run writes the readings at 1, 3 and 10 m down ([roadmap.md](roadmap.md#status)).
- **A relay reads as near.** Somebody can repeat a far robot's advert next to yours, and the `hello`
  that follows proves who holds a key, never where it is.
- **Near is not trusted.** It only starts the `hello`. A robot near is still checked in full, and a
  stranger unless a person pinned it, which bounds what it can ask for
  ([trust.md](trust.md#the-tiers)).

## Reaching each other

Once a robot is near, the one that dials picks the way in this order:

1. **The LAN**, when the advert's `LAN` flag is set, a DNS-SD record on the local network carries
   the same beacon id, and the HTTP inbox it names answers.
2. **A Bluetooth link**, when the advert's `LINK` flag is set.
3. Otherwise neither, and it tries again later, waiting longer each time, up to about a minute.

### DNS-SD on a local network

A robot on a LAN advertises the service type `_robot-passport._tcp` over mDNS
([RFC 6762](https://www.rfc-editor.org/rfc/rfc6762), [RFC 6763](https://www.rfc-editor.org/rfc/rfc6763)),
on the port its passport and its inbox answer on. Its TXT record:

| Key | Value |
|---|---|
| `v` | `2`, the version of this TXT record |
| `id` | the robot key's short id, which never changes |
| `sha` | the first 16 characters of the passport's digest |
| `bt` | the current beacon id, which changes with the advert |
| `path` | the passport's path, `/.well-known/robot-passport.json` when absent |

Every pair stays under 200 bytes, a reader never trusts the instance name, and a `v=1` record, the
0.1 shape, is ignored. The record puts the fixed short id beside the current beacon id, so anybody
on the LAN can tie the two together ([privacy.md](privacy.md#tracking)). A `hello` that arrives over
the LAN is answered `403`, "this robot has not heard you by radio", unless the beacon it carries was
heard over the radio in the last 8 seconds: the LAN carries talk, and the radio decides who is near.
In quackd a Jetson announces this record, and on the sim board a folder stands in for it.

### The Bluetooth link

With no shared network, the robot that dials connects to the other's GATT service, and the messages
that would go over HTTP go in frames, in datagrams of 20 to 512 bytes as the connection allows. The
link
closes if the answer names another beacon id than the advert did. A message is at most 16,384 bytes,
carried strictly in order, and a robot accepts at most 4 links, closes one that brings no `hello`
within 30 seconds, and takes at most 20 requests every 10 seconds on each. The frames and every
limit are in [spec section 5.6](spec.md#56-bluetooth-le).

## The well-known URL

A robot on a LAN also serves its passport over HTTP, at one path:

```
GET /.well-known/robot-passport.json    the passport's JSON, the payload of the signed one
GET /.well-known/robot-passport.jws     the passport as its owner signed it, as application/jose
```

Robots never need it to meet, since a `hello` carries the passport, but a person or a tool can read
it, as `quackd passport check HOST` does. `/.well-known/` is where
[RFC 8615](https://www.rfc-editor.org/rfc/rfc8615) puts such things, and plain HTTP is acceptable on
a LAN, because the owner's signature proves a passport, not its transport.

## Fetch rules

A robot that fetched wherever it was told would be a way to probe its network. So a reader that
fetches on its own initiative follows four rules ([spec section 5.7](spec.md#57-fetch-rules)):

- It fetches only from the host and port that advertised the passport, or from a URL a person typed.
- It follows no redirects.
- It stops reading at 12,288 bytes, which holds the JWS of the largest passport, and gives up after
  5 seconds for the whole exchange, however slowly a host sends.
- It contacts no URL a passport or a message names. A reply to a `hello` goes back only the way the
  `hello` came: to the address it came from, at the port it named, or over the same Bluetooth link.

## Caching

A reader caches a passport only once it has verified it, keyed by its `id` and its digest, and
never past its `expires`. An unsigned one is never cached by its `id`, since anybody can claim any
`id`, and a cache that believed it would let one robot overwrite what a reader knows of another.

## Removed in 0.29.0

<a id="qr-codes-and-nfc-tags"></a>
<a id="mqtt"></a>
<a id="ros-2"></a>

Version 0.1 carried a beacon five ways: the well-known URL, a QR sticker or NFC tag, DNS-SD, an MQTT
broker and a ROS 2 topic, with Bluetooth reserved until its ids could rotate. 1.0 keeps DNS-SD and
the well-known URL and adds Bluetooth LE, now that its ids rotate. The sticker, the broker and the
ROS 2 topic went, since none of them can say whether a robot is near. The page as it was is at the
[v0.28.0 tag](https://github.com/rokbenko/quackd/blob/v0.28.0/docs/passport/discovery.md).
