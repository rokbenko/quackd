# ADR-0011: How each verb maps to the real robot

**Status:** accepted, amended · **Date:** 2026-08-28

**Amended 2026-09-02 by [ADR-0017](0017-robot-adapters-and-manifest.md) and
[ADR-0018](0018-core-verbs-extensions-aliases.md):** this table was written when quackd had one
robot and one hardcoded map. A verb's route to a body is now the adapter's business, declared in
its manifest, and the skills a robot answers to are read from it rather than assumed. The
signatures here are also the 0.3 spellings: `get_frame`, `walk_to` and `walk` are aliases of
`observe`, `go_to` and `move` today. The live per-method table, VERIFIED against upstream, is
[adapter-status.md](../adapters/microduck/README.md).

## Context

Verbs are the LLM's vocabulary; upstream's `duck-ipc-proto` is the robot's. The two are
close but not identical, and the differences are exactly where a naive mapping would be
unsafe or dishonest.

## Decision

| Verb | Intent | Upstream (VERIFIED unless noted) | Notes |
|---|---|---|---|
| `walk(vx, vy, wz, duration_s)` | `move` ×N then `stop` | `robot.move` notification every 100 ms, then `robot.stop` | Re-sending feeds robotd's deadman; a stalled quackd → robot stops on its own. `wz` → `vyaw`. |
| `stop` | `stop` | `robot.stop` | Zero velocity, **not** limp. quackd never sends `robot.relax`. |
| `kick(leg)` | `do kick_left/right` | `robot.do{skill}` | Result carries `ball_moved_m` only in sim; on hardware it is `None` and the LLM must verify with a frame. |
| `grab` | `do ground_pick` | `robot.do` | Open-loop scoop; `holding` is sim-only telemetry. |
| `sit` / `stand` | `do sit_toggle` | `robot.do` | Toggle, so the verb reads posture first. Posture-from-`robot.state.policy` is **UNVERIFIED**. |
| `stand_up` | `enable on=true` | `robot.enable` | No stand-up RPC exists (UNVERIFIED note); robotd recovers from falls itself. |
| `quack(text)` | `sound{tag, text}` | `robot.sound{tag}` | Upstream has 7 tags and no TTS. `text` is mapped to a tag by a small heuristic and logged. |
| `gaze(direction | bearing_deg)` | `look{x,y,z}` | `robot.look` | Trunk-frame unit vector; robotd runs the gaze IK and reports clamping. |
| `get_frame` | — | **no socket method** (UNVERIFIED) | `jsonrpc` transport returns `None` unless `--camera-url` (HTTP snapshot) is given. |

Heartbeat on hardware = `robot.health` every 500 ms; `healthy == false` or a transport
error → `stop` + abort. `battery.percent` from the same call feeds `abort_when`.

## Consequences

- `find-and-kick` is honest on hardware: the kick result cannot claim displacement, so the
  `.duck` body tells the LLM to verify with a fresh frame.
- The `jsonrpc` transport can be exercised end-to-end against a fake robotd over TCP in CI,
  because every method it uses is VERIFIED and simple.
