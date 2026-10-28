# Glossary

Every term the Robot Passport pages use, in a sentence or two, with where it is explained.

**Advert.** The Bluetooth LE packet a robot sends a few times a second: 31 bytes that carry the
beacon. [discovery.md](discovery.md#the-bluetooth-le-beacon)

**Beacon.** What tells another robot that a robot is near: a version, three flags and the beacon id,
in an advert. It never carries a name, an owner or the passport. [discovery.md](discovery.md)

**Beacon id.** 8 random bytes in the beacon, tied to no key, replaced every 15 minutes and never in
the middle of a meeting. [discovery.md](discovery.md#the-beacon-id-changes-every-15-minutes)

**Binding.** One way of carrying a beacon or a passport: Bluetooth LE, DNS-SD and the well-known
URL. [spec section 5](spec.md#5-beacons-and-bindings)

**Bluetooth link.** A Bluetooth LE connection two robots open when they share no LAN, which carries
their talk in small frames. [discovery.md](discovery.md#the-bluetooth-link)

**Board.** The computer `quackd onboard` runs on: `jetson`, `sim`, or a plugin's. Its kind goes into
the passport as `board.kind`. [compatibility.md](compatibility.md#the-boards)

**Brain.** The language model a robot runs on, named in `robot.yaml`'s `brain:`, which holds its
meetings and its goals. [quackd.md](quackd.md#talking)

**Channel.** A way two robots exchange talk messages: an HTTP inbox on a LAN, or a Bluetooth link.
[talk.md](talk.md#channels)

**Claim.** What a passport is until something proves more: a statement by whoever wrote it, labelled
with where it came from. [trust.md](trust.md#what-a-passport-proves-and-what-it-only-claims)

**Confidence.** How sure a figure's writer is: `official`, `estimate` or `measured`, the words
quackd's datasheet uses. [passport.md](passport.md#body)

**Courtesy.** A small, bounded thing a peer may ask a robot to do, `pause` or `identify`, which its
owner's policy decides. [talk.md](talk.md#ask-and-answer)

**did:key.** An identifier that is a public key, written as text, resolved with no network. A
passport's `id` and its `owner.id` are each one. [trust.md](trust.md#the-id-is-a-key)

**Digest.** The SHA-256 of a passport's exact bytes, which a reader caches it by.
[spec section 4.3](spec.md#43-the-digest)

**Ed25519.** The signature algorithm every key in the protocol uses: 32-byte keys, 64-byte
signatures, fast on small machines. [trust.md](trust.md#the-id-is-a-key)

**Extension.** A member under `ext`, keyed by reverse DNS, that adds something the protocol does not
define. One marked `required` stops a reader that cannot read it from talking.
[passport.md](passport.md#ext)

**Far.** What a robot becomes once its signal holds 6 dB under the threshold for 5 seconds, or is
not heard for 8. [discovery.md](discovery.md#near-signal-strength)

**Fetch rules.** What a reader may contact on its own initiative: only the host that advertised a
passport, no redirects, 12 KB and 5 seconds at most. [discovery.md](discovery.md#fetch-rules)

**Figure.** A number in a passport's `body`, optionally with a confidence and a source. Absent means
not published, never zero. [passport.md](passport.md#body)

**Global budget.** The one budget every peer draws from when it asks for a courtesy, so that making
more keys gains a stranger nothing. [trust.md](trust.md#the-tiers)

**hello.** The three messages that open a session, in which both robots check each other's passport
and prove each holds its key right now. [talk.md](talk.md#hello-proving-who-you-are-in-three-steps)

**JWS.** JSON Web Signature, the compact `header.payload.signature` form a signed passport and every
talk message take. [spec section 4.4](spec.md#44-signed-passports)

**Known.** The tier of a robot a person pinned, or one signed by an owner key a person pinned.
[trust.md](trust.md#the-tiers)

**Meeting.** A short run a robot's brain holds with a robot that came close, while it has nothing
else to do, on the standing task in `robot.yaml`. [quackd.md](quackd.md#meeting-another-robot)

**Near.** What a robot becomes once its median signal holds at or above the threshold for a second:
a hint that it is close, never proof. [discovery.md](discovery.md#near-is-a-hint-not-proof)

**Nonce.** 16 random bytes each side contributes to a `hello`. A session's two nonces are what make a
replayed message fail. [talk.md](talk.md#hello-proving-who-you-are-in-three-steps)

**Onboard service.** `quackd onboard serve`, run by systemd as `quackd-onboard.service`: the radio,
talk, meetings and goals, in one process on the robot. [quackd.md](quackd.md#the-service)

**Owner.** Whoever looks after a robot, named in its passport by the owner key, with a label and a
contact that need no personal data. [privacy.md](privacy.md#what-to-put-in-owner)

**Owner key.** The key that signs an owner's passports, kept on each robot sealed by a passphrase.
Back it up. [trust.md](trust.md#the-owner-key-sealed-on-the-robot)

**Owner signature.** The owner key's signature over every byte of a passport, without which nothing
talks to it. [trust.md](trust.md#the-owner-signs-the-passport)

**Passphrase.** What seals the owner key: at least 12 characters, typed when the key is made and each
time a passport is signed, never needed by the service. [trust.md](trust.md#the-owner-key-sealed-on-the-robot)

**Passport.** One small JSON document about a robot, signed by its owner. [passport.md](passport.md)

**Peer.** A robot that talks to another one, whatever its tier.

**Pin.** A robot key or an owner key a person decided to trust, kept in `robot.yaml`, so the next
meeting starts at known. [trust.md](trust.md#pinning-trust-on-first-use)

**Reader.** Whoever reads and interprets a passport: a robot, or a tool such as
`quackd passport check`.

**Robot key.** The key a robot signs its talk with, which its passport's `id` names. It lives on the
robot, readable by the robot's user alone. [trust.md](trust.md#two-keys-on-every-robot)

**robot.yaml.** The one file the wizard writes: the passport section the owner signs, the brain, the
meeting task, the pins and the radio. [quackd.md](quackd.md#robotyaml-the-one-file)

**Robots nearby.** What a robot's brain sees, in its observation, about the robots near it.
[quackd.md](quackd.md#talking)

**RSSI.** Received signal strength, in dBm, which every advert arrives with, and which near and far
are judged on. [discovery.md](discovery.md#near-signal-strength)

**Same owner.** The tier of a robot signed by this robot's own owner key.
[trust.md](trust.md#two-robots-of-the-same-owner)

**Session.** What a `hello` opens between two robots: their ids and both nonces. It ends with `bye`,
or after ten minutes of silence in quackd. [spec section 6.3](spec.md#63-hello)

**Short id.** The first 8 bytes of the SHA-256 of a public key, as 16 hex characters: what a person
names a robot by, and what a LAN record carries. A hint, never proof.
[spec section 4.2](spec.md#42-the-short-id)

**Sim board.** A laptop standing in for a robot's computer, with a folder for the air, for trying
quackd onboard out and for CI. Its passports say `sim`. [quickstart.md](quickstart.md#on-a-laptop)

**Stranger.** The tier of a robot whose passport checked out and that nobody pinned. Its words never
reach a brain. [trust.md](trust.md#the-tiers)

**Talk.** The six messages two robots exchange once both have checked each other. [talk.md](talk.md)

**Tier.** How a robot knows another, which decides what that robot can make it do and what its brain
hears: stranger, known, same owner. [trust.md](trust.md#the-tiers)

**Trust on first use.** Pinning a key the first time you meet it, approved by a person, the way SSH
treats a server. [trust.md](trust.md#pinning-trust-on-first-use)

**Well-known URL.** `/.well-known/robot-passport.json` and `.jws`, where a robot on a LAN serves its
passport. [discovery.md](discovery.md#the-well-known-url)

**Writer.** Whoever produces a passport: its owner, through the wizard and `quackd onboard sign`.
