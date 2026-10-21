# Robot Passport test vectors

These files are the conformance suite of [the specification](../spec.md): passports, signed
passports, stamps and talk messages, each with the result a reader must reach. A reader that gets
every row right reads passports the way quackd does, whatever language it is written in.

`scripts/passport_vectors.py` writes them from the four test keys below, and it signs with the
`cryptography` library directly rather than through quackd's own passport module, so quackd is
checked against a second implementation of the same rules. Ed25519 signatures are deterministic,
so running it again writes the same bytes, and `--check` fails when a file would change.

> [!WARNING]
> The keys below are published. They are for tests, and a robot or an owner that signs with one
> of them has signed with a key the whole world holds.

## The keys

Their seeds, public keys and short ids are in [keys.json](keys.json). Every time-dependent vector
is judged at **2026-10-21T00:00:00Z**.

| Name | Its `did:key` | Short id |
|---|---|---|
| `robot-a` | `did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd` | `56475aa75463474c` |
| `owner` | `did:key:z6MkhFwXNFWosLeugvSf4wcL9t3uuRXueGSFTRgSvHhWj5G2` | `24f6ed6acbfe1009` |
| `robot-b` | `did:key:z6Mkgxj2R3HLtQRpPnvfvpuKEceSqf3tZHBjdmZ3fFz3JHGG` | `03396219237f75a6` |
| `intruder` | `did:key:z6Mkg26jczDiqsPK4momfvhZTTyFefWEyxYiSisFJ2wWJFkg` | `68894d58f18f2c34` |

## Passports

| File | Result | Why |
|---|---|---|
| [minimal.json](minimal.json) | valid | the three required members and nothing else |
| [microduck.json](microduck.json) | valid | the full example from [passport.md](../passport.md) |
| [unknown-member.json](unknown-member.json) | valid | `battery_swap` is unknown, so it is ignored (rule 8) |
| [unknown-enum.json](unknown-enum.json) | valid | `hexapod` reads as `other`, and `sonar` stays an opaque sensor name (rule 8) |
| [unknown-safety.json](unknown-safety.json) | valid | `stops_on_silence: "yes"` reads as `false` and `estop: "teleport"` as `none` (section 2.5) |
| [required-extension.json](required-extension.json) | valid, no talk | a required extension the reader does not understand (section 2.6) |
| [minor-version.json](minor-version.json) | valid | `0.9` is a later MINOR of a known MAJOR, and `aura` is ignored (rule 4) |
| [missing-name.json](missing-name.json) | refused | no `name` (rule 3) |
| [major-version.json](major-version.json) | refused | MAJOR `1` is unknown to a 0.x reader (rule 4) |
| [duplicate-member.json](duplicate-member.json) | refused | `name` appears twice (rule 2) |
| [long-name.json](long-name.json) | refused | a 65-character `name` (rule 5) |
| [oversized.json](oversized.json) | refused | 8,330 bytes, over the 8,192 a reader must accept (rule 1) |
| [bad-id.json](bad-id.json) | refused | a `did:web` id (rule 3, section 4.1) |
| [index.json](index.json) | valid index | two robots on one host (section 2.7) |

## Signed passports

| File | Result | Why |
|---|---|---|
| [microduck.jws](microduck.jws) | valid | `robot-a`'s full passport, signed with `alg` `Ed25519`, with one owner stamp |
| [eddsa-label.jws](eddsa-label.jws) | valid | the older `alg` label `EdDSA` (section 4.4) |
| [tampered.jws](tampered.jws) | refused | the payload was changed after signing |
| [wrong-key.jws](wrong-key.jws) | refused | `kid` and `id` name `robot-a`, but `intruder` signed it |
| [kid-mismatch.jws](kid-mismatch.jws) | refused | signed by `robot-a` over a passport whose `id` is `robot-b` |
| [uuid-signed.jws](uuid-signed.jws) | refused | a `urn:uuid` passport cannot be signed (section 4.1) |

## Stamps

Each is judged as a stamp on `robot-a`'s passport.

| File | Result | Why |
|---|---|---|
| [stamp-owner.jwt](stamp-owner.jwt) | counts | `owner` vouches for `robot-a`, valid for a year from the day before |
| [stamp-expired.jwt](stamp-expired.jwt) | does not count | `exp` is 35 days before the reference time |
| [stamp-wrong-subject.jwt](stamp-wrong-subject.jwt) | does not count | `sub` is `robot-b` |
| [stamp-bad-signature.jwt](stamp-bad-signature.jwt) | does not count | the claims were changed after signing |

## Talk

`robot-a` starts a hello with `robot-b`. The session's nonces are `vfXlXvct7qw_iSDdWldDYg` and
`MZtCrMC_w7RjeuBQcEBeaw`.

| File | Result | Why |
|---|---|---|
| [hello-1.jws](hello-1.jws) | valid | step 1: `robot-a`'s passport and its nonce |
| [hello-2.jws](hello-2.jws) | valid | step 2: `robot-b` signs both nonces |
| [hello-3.jws](hello-3.jws) | valid | step 3: `robot-a` signs both nonces, and the session is open |
| [say-in-session.jws](say-in-session.jws) | valid | a `say` carrying the session's nonces |
| [say-replayed.jws](say-replayed.jws) | refused | its nonces belong to another session (section 6.3) |
| [ask-pause.jws](ask-pause.jws) | valid | `robot-a` asks `robot-b` to pause for five seconds; whether it does is `robot-b`'s policy |
| [ask-unsigned.json](ask-unsigned.json) | refused | an `ask` must be signed (section 6.1) |
