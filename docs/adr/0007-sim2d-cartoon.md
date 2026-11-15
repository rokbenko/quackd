# ADR-0007: sim2d is a cartoon with two cameras

**Status:** accepted · **Date:** 2026-08-28 · Amended by [ADR-0030](0030-mujoco-physics-backend.md) (0.8: the GPU in "needs a GPU and CC BY-NC-SA meshes we will not vendor" below was only ever needed to *train* the policy. Running one is CPU MuJoCo and onnxruntime, so `microduck:mujoco` runs upstream's own robot on upstream's own gait. The meshes are still not vendored: they are fetched at run time into a user cache and hash checked, and `sim2d` stays the default)

## Context

The north-star demo must run on a laptop with no hardware and no GPU in under a minute.
Upstream's real simulator (MuJoCo Warp via `microduck_rl`) needs a GPU and CC BY-NC-SA
meshes we will not vendor. What the demo actually exercises is the *agent loop* — search,
approach, act, verify — not contact dynamics.

## Decision

- `roboglot/sim2d/world.py`: a 2 m × 2 m top-down arena at 20 Hz. Duck pose `(x, y, θ)` with
  2 % velocity noise, speed clamps matching the verb limits, walls, a ball with contact push
  and linear friction, a static person marker. `kick` connects only inside 0.30 m and a
  ±35° cone (1.2 m/s, ~0.7 m travel). `ground_pick` succeeds 60 % of the time inside
  0.18 m / ±30° — *deliberately* unreliable, because upstream's scoop is open-loop.
- A **deadman**: velocity zeroes 0.3 s after the last `move`, mirroring `robotd`.
  Verbs that forget to re-send stop the duck in sim exactly as they would on hardware.
- **Two renders** (`render.py`): `render_topdown` for humans and GIFs; `render_duckcam`, a
  first-person view with real perspective (90° FOV, camera at 0.20 m, floor objects project
  below the horizon by `f·h/d`, size ∝ `1/d`). `get_frame()` returns the duck-cam, so the
  same detector and the same distance geometry serve sim and hardware.
- The head yaw (`gaze`) pans the duck-cam, as it would on the robot; composites re-centre
  the gaze before steering.
- `recorder.py` composes world | duck-cam | caption into an animated GIF via Pillow, driven
  by the transport's tick hook so motion *between* LLM turns is visible.
- Deterministic under `--seed` (`numpy.random.default_rng(seed)`); time is simulated, so a
  full run takes seconds of wall-clock.

## Consequences

- It is a cartoon and the docs say so. It will not tell you whether a gait works; it will
  tell you whether your `.glot` strategy and your LLM's judgement work.
- `sim2d` state carries ground truth (`ball_displacement_m`) in `extras`, so tests check
  the LLM's success claim against the world, not just against itself.
- `--live` opens a pygame window (optional `[live]` extra); headless is the default.
