# Glossary

Every term the Robot Passport pages use, in a sentence or two, with where it is explained.

**Beacon.** What tells a robot another is near: a version, three flags and a beacon id, in a 31-byte
Bluetooth LE advert. Never a name, an owner or the passport. [discovery.md](discovery.md#the-bluetooth-le-beacon)

**Beacon id.** 8 random bytes in the beacon, tied to no key, replaced every 15 minutes and never in
the middle of a meeting. [discovery.md](discovery.md#the-beacon-id-changes-every-15-minutes)

**Bluetooth link.** A Bluetooth LE connection two robots open when they share no LAN, which carries
their talk in small frames. [discovery.md](discovery.md#the-bluetooth-link)

**Board.** The computer `quackd onboard` runs on: `jetson`, `sim`, or a plugin's. Its kind goes into
the passport as `board.kind`. [compatibility.md](compatibility.md#the-boards)

**Brain.** The language model a robot runs on, named under `brain:` in `robot.yaml`, which holds its
meetings and its goals. [quackd.md](quackd.md#talking)

**Channel.** A way two robots exchange talk: an HTTP inbox on a LAN, or a Bluetooth link.
[talk.md](talk.md#channels)

**Claim.** What a passport is until something proves more, labelled with where it came from.
[trust.md](trust.md#what-a-passport-proves-and-what-it-only-claims)

**Confidence.** How sure a figure's writer is: `official`, `estimate` or `measured`.
[passport.md](passport.md#body)

**Courtesy.** A small, bounded thing a peer may ask a robot to do, `pause` or `identify`, which its
owner's policy decides. [talk.md](talk.md#ask-and-answer)

**did:key.** An identifier that is a public key, resolved with no network. A passport's `id` and its
`owner.id` are each one. [trust.md](trust.md#the-id-is-a-key)

**Digest.** The SHA-256 of a passport's exact bytes, which a reader caches it by. A LAN record
carries its first 16 hex characters. [spec section 4.3](spec.md#43-the-digest)

**Ed25519.** The signature algorithm of every key in the protocol: 32-byte keys, 64-byte signatures.
[trust.md](trust.md#the-id-is-a-key)

**Extension.** A member under `ext`, keyed by reverse DNS. One marked `required` stops a reader that
cannot read it from talking. [passport.md](passport.md#ext)

**Fetch rules.** What a reader may contact on its own initiative: only the host that advertised a
passport, no redirects, 12 KB and 5 seconds at most. [discovery.md](discovery.md#fetch-rules)

**Figure.** A number in a passport's `body`, optionally with a confidence and a source. Absent means
not published, never zero. [passport.md](passport.md#body)

**Global budget.** The one budget every peer draws from when it asks for a courtesy, so making more
keys gains a stranger nothing. [trust.md](trust.md#the-tiers)

**hello.** The three messages that open a session, in which both robots check each other's passport
and prove each holds its key now. [talk.md](talk.md#hello-proving-who-you-are-in-three-steps)

**JWS.** JSON Web Signature, the compact `header.payload.signature` form of a signed passport and of
every talk message. [spec section 4.4](spec.md#44-signed-passports)

**Known.** The tier of a robot a person pinned, or one signed by an owner key a person pinned.
[trust.md](trust.md#the-tiers)

**Meeting.** A short run a robot's brain holds with a robot that came close, while it has nothing
else to do. [quackd.md](quackd.md#meeting-another-robot)

**Near, far.** What a robot becomes once its median signal holds above the threshold for a second,
and once it holds 6 dB under it for 5 seconds or goes unheard for 8. A hint, never proof.
[discovery.md](discovery.md#near-signal-strength)

**Nonce.** 16 random bytes each side contributes to a `hello`, which make a replayed message fail.
[talk.md](talk.md#hello-proving-who-you-are-in-three-steps)

**Onboard service.** `quackd onboard serve`, which systemd runs on the robot as
`quackd-onboard.service`. [quackd.md](quackd.md#the-service)

**Owner.** Whoever looks after a robot and signs its passport, named by the owner key, with a label
and a contact that need no personal data. [privacy.md](privacy.md#what-to-put-in-owner)

**Owner key.** The key that signs an owner's passports, kept on each robot sealed by a passphrase.
Back it up. [trust.md](trust.md#the-owner-key-sealed-on-the-robot)

**Owner signature.** The owner key's signature over every byte of a passport.
[trust.md](trust.md#the-owner-signs-the-passport)

**Passphrase.** What seals the owner key: at least 12 characters, typed when the key is made and each
time a passport is signed. [trust.md](trust.md#the-owner-key-sealed-on-the-robot)

**Passport.** One small JSON document about a robot, signed by its owner. [passport.md](passport.md)

**Pin.** A robot key or an owner key a person decided to trust, kept in `robot.yaml`: trust on first
use, the way SSH treats a server. [trust.md](trust.md#pinning-trust-on-first-use)

**Robot key.** The key a robot signs its talk with, which its passport's `id` names.
[trust.md](trust.md#two-keys-on-every-robot)

**robot.yaml.** The one file the wizard writes: the passport section the owner signs, the brain, the
meeting task, the pins and the radio. [quackd.md](quackd.md#robotyaml-the-one-file)

**Robots nearby.** What a robot's brain sees, in its observation, about the robots near it.
[quackd.md](quackd.md#talking)

**RSSI.** Received signal strength, in dBm, which every advert arrives with.
[discovery.md](discovery.md#near-signal-strength)

**Same owner.** The tier of a robot signed by this robot's own owner key.
[trust.md](trust.md#two-robots-of-the-same-owner)

**Session.** What a `hello` opens: the two ids and both nonces. It ends with `bye`, or after ten
quiet minutes in quackd. [spec section 6.3](spec.md#63-hello)

**Short id.** The first 8 bytes of the SHA-256 of a public key, as 16 hex characters: what a person
names a robot by, and what a LAN record carries. A hint, never proof.
[spec section 4.2](spec.md#42-the-short-id)

**Sim board.** A laptop standing in for a robot's computer, with a folder for the air. Its passports
say `sim`. [quickstart.md](quickstart.md#on-a-laptop)

**Stranger.** The tier of a robot whose passport checked out and that nobody pinned. Its words never
reach a brain. [trust.md](trust.md#the-tiers)

**Talk.** The six messages two robots exchange once both have checked each other. [talk.md](talk.md)

**Tier.** How a robot knows another: stranger, known or same owner, which decides what that robot can
make it do and what its brain hears. [trust.md](trust.md#the-tiers)

**Well-known URL.** `/.well-known/robot-passport.json` and `.jws`, where a robot on a LAN serves its
passport. [discovery.md](discovery.md#the-well-known-url)
