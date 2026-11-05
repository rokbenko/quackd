# ADR-0023: Reachy Mini: `say` degrades to mood sounds, `stop` is never limp

**Status:** superseded · **Date:** 2026-09-02 · Implemented in Phase 2 of 0.4 ([design](../design/multi-robot.md))

**Superseded 2026-09-12:** the Reachy Mini adapter was removed from quackd. Everything below
is left as the record of what 0.4 through 0.8 actually shipped; none of it applies to the
current tree. See the CHANGELOG's 0.9.0 entry for the removal and
[ADR-0020](0020-heterogeneous-flocks.md)'s amendment for what the removal did and did not take
with it.

## Context

The Reachy Mini SDK (`reachy-mini` 1.10.0, read from upstream source at commit
`da00973` on 2026-09-01) verifiably offers: a WebSocket client to a local daemon, head
and antenna motion (`goto_target`, `look_at_world`, `look_at_image`), a camera
(`media.get_frame()`), a microphone, recorded emotion moves with their own sounds
(`play_move`), `wake_up`, `goto_sleep`, `enable_motors`, `disable_motors`, and audio out
via `media.play_sound(file)` or raw PCM. It has **no text-to-speech** (the upstream TTS
example calls an external Hugging Face Space), **no battery readout**, **no
client-disconnect deadman** and **no e-stop primitive** that we could find. The owner had
to decide what quackd's speaker verb means on such a robot.

## Decision

- **`say(text)` stays core on Reachy and degrades the way the Microduck already does**
  with its seven tones: the text is logged verbatim, the mood is keyword-mapped to a
  recorded emotion move (`?` to `curious1`, greetings to `welcoming1`, joy to
  `cheerful1`, sadness to `sad1`, `!` to `surprised1`, default `attentive1`) and the
  backend plays that move with its sound. The manifest says `extras.speech: "tones"` so
  the prompt tells the LLM the robot cannot pronounce text. `play_sound(name)` is also
  exposed as the VERIFIED primitive (bundled asset names only, no path separators). The
  alternatives, no `say` at all or a local TTS extra, were rejected: the first makes a
  duck-and-reachy dialogue impossible, the second adds a heavy dependency and untested
  audio routing.
- **`stop` is `cancel_move()`. quackd never calls `disable_motors()`**, which makes the
  head limp; the same principle as never sending `robot.relax` on the Microduck. The
  manifest's `safety_authority.native` is `none` because no client deadman or e-stop was
  verified; quackd's own 2 Hz heartbeat and short gaze moves are the only client-side
  authority, and the docs say so.
- **`express`'s enum is built from the manifest at connect time**, from the local Hugging
  Face cache only, never downloaded; if the library is unavailable the verb is omitted and
  a `.duck` that requires it fails validation against that robot. Sim and mock use a fixed
  curated tuple.
- **`wake_up` ships as a confirm-gated extension** (it moves every joint); `goto_sleep`
  and `disable_motors` are not exposed.
- **The `sdk` backend is EXPERIMENTAL** with the same label as `jsonrpc`: verified names
  at a pinned commit, never run against a robot by us. It never passes `spawn_daemon=True`
  (that kills a mismatched daemon), serializes every SDK call under one lock with a
  per-call timeout, and is never imported by the default path; `quackd doctor` reads its
  version through package metadata only.
- Reachy's `gaze` is its own verb (bearing 180 degrees either way, pitch 40 degrees, no
  fall precondition) under the same name, so a `.duck` that requires `gaze` is satisfied
  by both robots and the Microduck builtin stays byte-for-byte unchanged.

## Consequences

- A "Battery below N%" abort is unenforceable on Reachy; `quackd validate` warns.
- Bearings are camera-relative on every robot; on a head the body bearing is
  `gaze_yaw_deg + bearing_deg`, reported by `search_scan` and documented.
- Flipping the `say` policy is one constant in the manifest builder if the owner changes
  their mind.
