# Glossary

Every term the Robot Passport pages use, in a sentence or two, with where it is explained.

**Beacon.** Anything that tells a reader a passport exists and where it is: a QR sticker, a DNS-SD
record, an MQTT topic, a ROS 2 topic. It carries a version, a short id, a digest and a location, and
never the passport itself. [discovery.md](discovery.md)

**Binding.** One way of carrying a beacon or a passport: the well-known URL, QR and NFC, DNS-SD,
MQTT, ROS 2. [spec section 5](spec.md#5-beacons-and-bindings)

**Channel.** A way two robots exchange talk messages, listed in a passport's `talk`: an HTTP inbox or
an MQTT topic in 0.1. [talk.md](talk.md#channels)

**Claim.** What a passport is until something proves more: a statement by whoever wrote it, labelled
with where it came from. [trust.md](trust.md#what-a-passport-proves-and-what-it-only-claims)

**Confidence.** How sure a figure's writer is: `official`, `estimate` or `measured`, the words
quackd's datasheet uses. [passport.md](passport.md#body)

**Conformance level.** How much of the protocol a robot implements: 0 Tag, 1 Beacon, 2 Signed,
3 Talk. [compatibility.md](compatibility.md#the-ladder)

**Courtesy.** A small, bounded thing a peer may ask a robot to do, `pause` or `identify`, which its
owner's policy decides. [talk.md](talk.md#ask-and-answer)

**did:key.** An identifier that is a public key, written as text, resolved with no network. A
passport's `id` is one. [trust.md](trust.md#the-id-is-a-key)

**Digest.** The SHA-256 of a passport's exact bytes. Beacons carry its first 16 hex characters, so a
reader knows when a passport changed. [spec section 4.3](spec.md#43-the-digest)

**Ed25519.** The signature algorithm every key in the protocol uses: 32-byte keys, 64-byte
signatures, fast on small machines. [trust.md](trust.md#the-id-is-a-key)

**Extension.** A member under `ext`, keyed by reverse DNS, that adds something the protocol does
not define. One marked `required` stops a reader that cannot read it from talking.
[passport.md](passport.md#ext)

**Fetch rules.** What a reader may contact on its own initiative: only the host that advertised a
passport, no redirects, 12 KB and 5 seconds at most. [discovery.md](discovery.md#fetch-rules)

**Figure.** A number in a passport's `body`, optionally with a confidence and a source. Absent means
not published, never zero. [passport.md](passport.md#body)

**Global budget.** The one budget every peer draws from when it asks for a courtesy, so that making
more keys gains a stranger nothing. [trust.md](trust.md#the-four-tiers)

**hello.** The three messages that open a session and prove each side holds its key right now.
[talk.md](talk.md#hello-proving-who-you-are-in-three-steps)

**Index.** What a host that fronts several robots serves at its well-known URL instead of one
passport: a list of passport URLs on the same host.
[passport.md](passport.md#the-index-several-robots-behind-one-host)

**JWS.** JSON Web Signature, the compact `header.payload.signature` form a signed passport, a stamp
and a signed message take. [spec section 4.4](spec.md#44-signed-passports)

**Known.** The tier of a robot whose key a person pinned. [trust.md](trust.md#the-four-tiers)

**Nonce.** 16 random bytes each side contributes to a `hello`. A session's two nonces are what make a
replayed message fail. [talk.md](talk.md#hello-proving-who-you-are-in-three-steps)

**Owner.** Whoever looks after a robot. Optional in a passport, and never needs personal data.
[privacy.md](privacy.md#what-to-put-in-owner)

**Owner key.** The key an owner stamps all their robots with. Back it up.
[trust.md](trust.md#the-owner-key-is-lost)

**Passport.** One small JSON document a robot publishes about itself. [passport.md](passport.md)

**Peer.** A robot that talks to another one, whatever its tier.

**Pin.** A key a person decided to trust, kept by a reader, so the next meeting starts at known.
[trust.md](trust.md#pinning-trust-on-first-use)

**Reader.** Whoever fetches and interprets a passport: a robot, a phone, a tool.

**Robots nearby.** What a quackd pilot sees, in its observation, about the robots it met.
[quackd.md](quackd.md)

**Same owner.** The tier of a robot stamped by an owner key a reader pinned, its own included.
[trust.md](trust.md#two-robots-of-the-same-owner)

**Session.** What a `hello` opens between two robots: their ids and both nonces. It ends with `bye`,
or after ten minutes of silence in quackd. [spec section 6.3](spec.md#63-hello)

**Short id.** The first 8 bytes of the SHA-256 of a robot's public key, as 16 hex characters. A
lookup hint, never proof. [spec section 4.2](spec.md#42-the-short-id)

**Stamp.** A small signed statement from another key vouching for a robot's key: "this robot is
mine". [trust.md](trust.md#stamps)

**Stranger.** The tier of any robot a reader has no reason to trust.
[trust.md](trust.md#the-four-tiers)

**Talk.** The optional layer of six messages above the passport. [talk.md](talk.md)

**Tier.** How a reader knows a robot, which decides what that robot can make it do: stranger, known,
same owner, flock. [trust.md](trust.md#the-four-tiers)

**Translator.** A reader's way of building a passport for a robot that publishes something else: a
URDF, a VDA 5050 factsheet, a MassRobotics identity report, an A2A Agent Card.
[compatibility.md](compatibility.md#translators-reading-robots-that-never-heard-of-it)

**Trust on first use.** Pinning a key the first time you meet it, approved by a person, the way SSH
treats a server. [trust.md](trust.md#pinning-trust-on-first-use)

**Well-known URL.** `/.well-known/robot-passport.json`, where a passport lives on any host.
[discovery.md](discovery.md#the-well-known-url)

**Writer.** Whoever produces a passport: the robot, its software, or its owner on a laptop.
