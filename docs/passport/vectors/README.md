# Robot Passport test vectors

These files are the conformance suite of [the specification](../spec.md), version 1.0:
passports, passports their owners signed, and talk messages, each with the result a reader must
reach. A reader that gets every row right reads passports the way quackd does, whatever language
it is written in.

`scripts/passport_vectors.py` writes them from the five test keys below, and it signs with the
`cryptography` library directly rather than through quackd's own passport module, so quackd is
checked against a second implementation of the same rules. Ed25519 signatures are deterministic,
so running it again writes the same bytes, and `--check` fails when a file would change.

> [!WARNING]
> The keys below are published. They are for tests, and a robot or an owner that signs with one
> of them has signed with a key the whole world holds.

## The keys

Their seeds, public keys and short ids are in [keys.json](keys.json), with the runtime members
each robot's hellos carry. Every time-dependent vector is judged at **2026-10-29T00:00:00Z**, and
the good passports were signed on 2026-10-28 for a year.

| Name | Its `did:key` | Short id |
|---|---|---|
| `robot-a` | `did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd` | `56475aa75463474c` |
| `owner` | `did:key:z6MkhFwXNFWosLeugvSf4wcL9t3uuRXueGSFTRgSvHhWj5G2` | `24f6ed6acbfe1009` |
| `robot-b` | `did:key:z6Mkgxj2R3HLtQRpPnvfvpuKEceSqf3tZHBjdmZ3fFz3JHGG` | `03396219237f75a6` |
| `intruder` | `did:key:z6Mkg26jczDiqsPK4momfvhZTTyFefWEyxYiSisFJ2wWJFkg` | `68894d58f18f2c34` |
| `owner-b` | `did:key:z6MktFovzcapNZyZBWzFJpCXf26B8XLKdXtwfwnXXFebPgzM` | `519cae93e93ad011` |

## Passports

| File | Result | Why |
|---|---|---|
| [minimal.json](minimal.json) | valid | the six required members and nothing else |
| [humanoid.json](humanoid.json) | valid | the full example from [passport.md](../passport.md): a humanoid as a simulation portrays it |
| [unknown-member.json](unknown-member.json) | valid | `battery_swap` is unknown, so it is ignored (rule 8) |
| [unknown-enum.json](unknown-enum.json) | valid | `hexapod` and the board `fpga-cluster` read as `other`, and `sonar` stays an opaque sensor name (rule 8) |
| [unknown-safety.json](unknown-safety.json) | valid | `stops_on_silence: "yes"` reads as `false` and `estop: "teleport"` as `none` (section 2.5) |
| [required-extension.json](required-extension.json) | valid, no talk | a required extension the reader does not understand (section 2.6) |
| [minor-version.json](minor-version.json) | valid | `1.9` is a later MINOR of a known MAJOR, and `aura` is ignored (rule 4) |
| [missing-name.json](missing-name.json) | refused | no `name` (rule 3) |
| [missing-owner.json](missing-owner.json) | refused | no `owner`, so nobody vouches for it (rule 3) |
| [missing-expires.json](missing-expires.json) | refused | no `expires` (rule 3) |
| [major-version.json](major-version.json) | refused | MAJOR `2` is unknown to a 1.x reader (rule 4) |
| [old-version.json](old-version.json) | refused | `0.1`, Robot Passport before owners signed passports (rule 4) |
| [duplicate-member.json](duplicate-member.json) | refused | `name` appears twice (rule 2) |
| [long-name.json](long-name.json) | refused | a 65-character `name` is not well formed (rule 3) |
| [oversized.json](oversized.json) | refused | 8,492 bytes, over the 8,192 a reader must accept (rule 1) |
| [bad-id.json](bad-id.json) | refused | a `urn:uuid` id: an id names a key (rule 3) |
| [too-long-validity.json](too-long-validity.json) | refused | `expires` is 426 days after `issued`, over 400 (rule 5) |

## Signed passports

| File | Result | Why |
|---|---|---|
| [humanoid.jws](humanoid.jws) | valid | `robot-a`'s full passport, signed by `owner` with `alg` `Ed25519` |
| [eddsa-label.jws](eddsa-label.jws) | valid | the older `alg` label `EdDSA` (section 4.4) |
| [tampered.jws](tampered.jws) | refused | the name was changed after `owner` signed it (section 4.4) |
| [wrong-key.jws](wrong-key.jws) | refused | `kid` names `owner`, but `intruder` signed it (section 4.4) |
| [kid-mismatch.jws](kid-mismatch.jws) | refused | `owner-b` signed a passport whose `owner.id` is `owner` (section 4.4) |
| [robot-signed.jws](robot-signed.jws) | refused | signed by the robot's own key, as 0.1 signed them (section 4.4) |
| [same-key.jws](same-key.jws) | refused | its owner key is its robot key, so nothing but the robot vouches for it (section 4.4) |
| [expired.jws](expired.jws) | refused | its owner's signature ran out on 2026-01-01 (section 4.5) |
| [not-yet-valid.jws](not-yet-valid.jws) | refused | issued on 2026-11-01, ahead of the reader's clock by more than five minutes (section 4.5) |

## Talk

`robot-a` (owned by `owner`) starts a hello with `robot-b` (owned by `owner-b`). Each hello
carries the sender's passport signed by its owner, and the runtime members from keys.json. The
session's nonces are `vfXlXvct7qw_iSDdWldDYg` and `MZtCrMC_w7RjeuBQcEBeaw`.

| File | Result | Why |
|---|---|---|
| [hello-1.jws](hello-1.jws) | valid | step 1: `robot-a`'s passport, its nonce and its runtime members |
| [hello-2.jws](hello-2.jws) | valid | step 2: `robot-b`'s passport, and `robot-b` signs both nonces |
| [hello-3.jws](hello-3.jws) | valid | step 3: `robot-a` signs both nonces, and the session is open |
| [say-in-session.jws](say-in-session.jws) | valid | a `say` carrying the session's nonces |
| [say-replayed.jws](say-replayed.jws) | valid message, refused in the session | its nonces belong to another session (section 6.3) |
| [ask-pause.jws](ask-pause.jws) | valid | `robot-a` asks `robot-b` to pause for five seconds; whether it does is `robot-b`'s policy |
| [hello-foreign-passport.jws](hello-foreign-passport.jws) | valid message, refused as a hello | `robot-a` signed it, and it carries `robot-b`'s passport (section 6.3) |
| [unsigned-say.json](unsigned-say.json) | refused | every message is signed by its sender's robot key (section 6.1) |
