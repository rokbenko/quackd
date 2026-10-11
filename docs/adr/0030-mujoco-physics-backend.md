# ADR-0030: a MuJoCo backend that runs the Microduck's own walking policy

**Status:** accepted, amended · **Date:** 2026-09-07 · Amends [ADR-0007](0007-sim2d-cartoon.md) ·
Implemented in 0.8 (`--robot microduck:mujoco`, and `web/` in a browser)

**Amended 2026-09-27 by [ADR-0047](0047-the-arms-simulator-runs-the-real-backend.md):** this is
no longer the only MuJoCo backend. `lerobot:mujoco` is the SO-101 arm's simulator, in the arm's
own package and behind its own extra, `quackd[lerobot-sim]`: the arm's real backend over a
physics model of the arm, fetched at a pinned commit and hash checked the way the duck's is. It
shares nothing of `sim3d/` and steps its world on the `FlockClock` this ADR generalised, with a
participant per sleep. So `mujoco` is a backend of two bodies now. The prompt's arena note is
keyed on the adapter as well as the backend, and a run records a GIF only of a body with a world
to draw, which the arm's simulator does not offer. Everything below is still the duck's.

## Context

[ADR-0007](0007-sim2d-cartoon.md) chose a cartoon. It was the right call and it says why:
the north-star demo had to run on any laptop in under a minute, what needed testing was the
agent loop rather than contact dynamics, and upstream's simulator "needs a GPU and CC
BY-NC-SA meshes we will not vendor". The cartoon has carried eight adapters since.

It also has a ceiling that the docs have always been honest about: *it will not tell you
whether a gait works*. Every quackd demo so far shows a sprite sliding at exactly the speed
it was asked for. A pilot that has only ever driven that has never met the thing that makes
a real robot hard, which is that it does not do what you asked.

Two of ADR-0007's premises turned out to be wrong by 2026-09, and one was always narrower
than it read.

- **The GPU is for training, not for running.** `microduck_rl` trains on mjlab and MuJoCo
  Warp and needs CUDA to do it, but its own evaluation path is `scripts/infer_policy.py`,
  which imports `mujoco`, `mujoco.viewer` and `onnxruntime` and nothing else. Upstream also
  ships `duck-body`, a plain CPU MuJoCo body served to the real `robotd`. Running a trained
  policy has never needed a GPU.
- **The policies are public and permissive.** The nine ONNX policies a Microduck ships with
  are on the Hugging Face Hub as `pollen-robotics/microduck-policies` under Apache-2.0. Only
  the 3D model files are CC BY-NC-SA. *Amended 2026-10-09 (0.18):* ten since upstream's set
  `v5` added `velstand.onnx`, the walk every robotd from daemon-v0.13.0 loads, and all ten are
  Apache-2.0. quackd pins `v7`, the set daemon-v0.16.1 seeds a robot with.
- **"We will not vendor" is not "we cannot use".** `docs/licenses.md` has said since 0.1
  that a future MuJoCo backend "fetches them from upstream at runtime into a user cache,
  prints the license, and stays optional". That is a design, and it works.

`mjlab` was considered and rejected as the runtime. It is the factory that made the gait:
Isaac-Lab-style managers over MuJoCo Warp, built to step thousands of robots on an NVIDIA
GPU. Its README says an NVIDIA GPU is required for training and macOS is evaluation only;
its Windows support is "preliminary" and "not guaranteed to be stable"; MuJoCo Warp's own
documentation says a single step is *slower* than MuJoCo's because it optimises throughput
rather than latency; and `microduck_rl` pins Python 3.12 exactly with torch and warp behind
it. None of that suits one duck in a 50 Hz loop on a laptop. Plain MuJoCo is a wheel of 17
to 20 MB depending on the platform, with builds for Windows and Linux on x86-64 and macOS on
Apple Silicon, and no GPU. There is no Intel Mac wheel.

## Decision

- **A fifth Microduck backend, `microduck:mujoco`**, behind the same `DuckTransport`
  protocol as the other four. `quackd/sim3d/` is to `quackd/sim2d/` what physics is to a
  drawing: same arena, same seeded spawn order, same 0.3 s deadman, same kick cone, same
  unreliable scoop, and the same `extras` keys, so a `.duck` written for one runs on the
  other unchanged.

  *Since 0.8:* the arenas are no longer the same in one respect. The cartoon's person marker
  was removed from the physics world, and from the browser demo with it, so nobody stands in
  either 3D arena. The cartoon draws its person from the RNG after the duck and the ball, so
  dropping that draw leaves every seeded duck and ball position bit-identical between the two
  and the parity above still holds for everything they share. What it costs is one starter:
  `follow-me` is a task about following somebody and cannot succeed on `microduck:mujoco`.
  `people` has left the 3D `extras`; the cartoon still publishes it.
- **The robot is upstream's, all the way down.** `robot_walk.xml` and its 38 meshes come
  from `microduck_rl` at a pinned commit; `alpha_walking.onnx` and `alpha_stand.onnx` come
  from the Hub at a pinned revision; the 50 Hz loop around them is `infer_policy.py`'s,
  cited line by line in `quackd/sim3d/upstream_api.py`. quackd supplies a twist and a head
  pose, which is what a gamepad supplies on the real robot. It writes no gait.
- **Assets are fetched at run time, verified, and never shipped.** The first run downloads
  upstream's tarball into `~/.quackd/cache`, checks every file against the sha256 it was
  read at, and writes the licence notice beside it. `QUACKD_MICRODUCK_ASSETS` points at a
  checkout instead. Nothing CC BY-NC-SA enters the wheel, the repository or a CI fixture.
- **A stand-in body for the tests.** `body="puppet"` is a kinematic block that moves exactly
  as the cartoon does inside the same MuJoCo scene. It needs no download and no policy, so
  CI exercises every intent, the recorder and a seeded acceptance sweep offline, and the
  tests that need the real duck skip when the cache is empty.
- **The gait floor is handled in the open.** Under the model's own PD actuators the walking
  policy does not step below about 0.23 m/s or 1.0 rad/s, and it achieves about 0.38 of
  what it is asked. `move` defaults to 0.15 m/s, so passing a command through unchanged
  would give a duck that reports walking and stands still, which is the worst failure a
  simulator can have. A non-zero twist is scaled bodily up to the floor, keeping the ratio
  between its axes so an arc stays an arc; a twist below a third of the floor is dropped to
  zero rather than amplified into a lurch; and the floor, the commanded twist and the twist
  actually sent are all in the state, in the prompt and in `extras.assumptions`.
- **Four skills are named stand-ins.** `kick` and `grab` use the cartoon's contact rules,
  because upstream's episodic `ball_kick_*` and `ground_pick` policies did nothing from a
  standing pose when they were tried; `sit` is refused, because `alpha_sitstand` put the
  model on its back; and a fall is recovered by standing the model up again, because
  upstream ships no get-up policy. Each is listed in `state.extras.assumptions`, so a
  transcript never implies more than happened.
- **The same demo runs in a browser.** `web/` is a static page: MuJoCo compiled to
  WebAssembly, the same policy in onnxruntime-web, the same verbs and the same contract in
  six modules of plain JavaScript with no build step, and the model and the policy fetched from
  the same pinned upstreams. It exists so that trying quackd costs nobody an install, and it carries a
  switch that removes the quackd layer and hands the visitor the keyboard instead.

  *Since 0.8:* the last clause is not how it works any more. The switch still removes the quackd
  layer, and only that layer — with it off, a typed sentence gets the honest answer the page
  prints, that this robot understands a twist, three numbers, and a walking policy that turns
  them into steps, and has never seen the words. What the switch
  never removed, and does not gate now, is the keyboard: the cockpit is permanent, and
  `tests/test_web.py` fails if the keydown handler reads the toggle at all. `runtime.manual`
  is a lease on the twist rather than a mode, re-asserted by `Runtime.start` every 50 Hz
  control tick immediately before the physics reads it, which is what lets a key take the
  robot mid-run without cancelling anything first. A key that would *move* the robot aborts
  the run and the model request in flight — the signal reaches `fetch`, so no answer arrives
  after the duck was taken back; what the abort cannot promise is the bill, because these are
  plain non-streaming POSTs and a vendor may already have generated the turn — and
  the transcript records the handover naming the key; a key that only reads, `O` and the two
  camera keys, does not interrupt. There is deliberately no key for `say`, which is the
  argument: a key carries a command, and a sentence needs something to read it. The claim to
  read into the bullet above is therefore both hands on the same duck rather than either/or.
  Underneath, either way, are two learned policies — `alpha_walking` and `alpha_stand` — with
  the kick a scripted impulse of quackd's own, and none of the three reads English.
- **`sim2d` stays the default.** It starts in a second, needs no network, and is what eight
  adapters share. The physics backend is an extra, `quackd[mujoco]`, imported only inside
  `connect()`.

## Consequences

- The one thing the cartoon could never show is now on the table: `find-and-kick` succeeds
  on 10 of 10 seeds with the scripted pilot **and the duck walking on its own trained
  policy**, ground truth checked. *Amended 2026-09-15 (0.9):* that 10 of 10 is not reproducible.
  The nightly job returned it on each of its first five runs and 9 of 10 on 2026-09-14, and by
  hand on the developer's laptop it is 9 of 10, seed 4 going in both cases and on the `mujoco`
  and `onnxruntime` versions either side of this release's bump. The failing seed ends on the
  duck's own *same verb fails 3 times in a row* rule with the duck standing and the ball
  unmoved, so seed 4 is marginal and platform dependent rather than the gait being broken. The
  shipped threshold outside `QUACKD_STRICT_SEEDS` is 8, which it does clear. That sweep is `test_find_and_kick_on_the_real_duck`; it runs
  only where upstream's model is already cached, and the sweep beside it runs the same ten
  seeds on the puppet. A pilot that works here has met a robot that undershoots.
- Rendering is the cost. On an Intel iGPU a head-camera frame is 4 ms with the shell hidden
  and the over-the-shoulder view about 110 ms with 431k triangles in it, so shadows are off,
  the recorder samples half as often as the cartoon's, and `--live` uses MuJoCo's own viewer.

  *Since:* the arena is upstream's own scene, from the `scene*.xml` wrappers in `microduck_rl`
  — the blue-grey checker with edge marks, the gradient skybox, the haze, the headlight, the
  directional light and the viewer's azimuth and elevation. Shadows stay off by default and
  `QUACKD_MUJOCO_SHADOWS=1` turns them on for a recording. What the scene cost is the head
  camera: that floor and that sky are the same blue as quackd's person marker, at hue 105 and
  114 with saturation and value overlapping too, so the detector read a person 0.12 m ahead in
  every frame of every heading. The head camera therefore renders a colourless copy of the
  same checker and no skybox, in a geom group MuJoCo hides everywhere else. It is a stand-in
  and it is in `extras.assumptions` with the rest. The defence of it is that upstream's blue
  tiles are a viewer texture and upstream's policies are blind: nothing in `microduck_rl` ever
  looks at its own floor, and a real Microduck's camera sees a room rather than a scene file.

  *Since 0.8:* the person marker is gone from this arena, and the colourless floor stayed. It
  is not dead code and it must not be cleaned up as such. The detector keeps its person hue
  band because the cartoon still has a person to find, and that band is what upstream's blue
  checker forges — so with nobody here to see, every person the pretty floor could produce
  would be a phantom and there would be no true one to weigh it against. The test that guards
  it got stricter rather than retiring: it now counts a phantom at any range.
- CI never fetches the model, so the `microduck:mujoco` row's ✅ rests on the puppet's sweep
  plus tests that skip where the cache is empty. The real duck's numbers in this ADR were
  measured on one machine, and `GAIT_THRESHOLD` is tagged UNVERIFIED for that reason.

  *Since:* both halves of that changed. A `physics` job installs the extra and runs the
  stand-in's sweep against OSMesa on every push, failing rather than skipping when it cannot
  make a context. A nightly job fetches the model into a runner it then destroys — no cache
  entry, because a keyed one is restorable by any run including a fork's, and `licenses.md`
  says no CI fixture carries a byte of these meshes — and runs the trained gait's sweep there.
  The sweep is a named test now rather than a memory, and it asserts the body really is the
  trained one, because a silent fall back to the puppet passing it is the point. What that
  named test has actually returned under strict seeds is 10 of 10 on some machines and runs and
  9 of 10 on others, per the amendment above.
- Two upstreams now have to be tracked rather than one, both pinned, both in
  `quackd/sim3d/upstream_api.py`. A new export from either is a new pin and a new sha256.
- Flock mode stays `sim2d` only. `FlockClock` was generalised to any world with a `t` and a
  `step(dt)` so a MuJoCo arena could hold several ducks later, but nothing promises it.
- **Nothing in `web/` has been run in a browser.** `microduck.js` and `pilot.js` were exercised
  under Node against the real model and the real policy, and the rendering, the DOM and the
  recording were read rather than run. No CI job touches `web/`, and GitHub Pages was not enabled
  on the repository when this was written, so the page is not live. The first person to open it
  is the test.

  *Since:* `tests/test_web.py` runs in the ordinary suite and holds what can be held without a
  browser — the ids, the classes, the rule that was hiding nothing, the pins and gait numbers
  shared with Python, `node --check` on each module, and the argument validator executed under
  Node. The page is deployed from `web/` by Vercel at www.quackd.org rather than by Pages, and
  `vercel.json` serves the directory with no build step. The sentence that still stands is the
  last one: nobody has opened it.

  *Since 0.8 — this supersedes the note above on all three counts, the address, the test list
  and the last sentence:* that address and that list have both moved on. The page is mounted at `/simulator`
  rather than at a domain root — `vercel.json` rewrites `/simulator/*` into `web/`, still with
  no build step, and quackd-web serves the mount from its own build, which fetches this
  directory at a pinned commit — so every local reference in `index.html` is absolute, because a
  relative one resolves off the mount and 404s. It answers at www.quackd.org/simulator, and
  `/simulator/source.json` records the commit the deployed copy was cut from.
  Locally it runs under `python web/serve.py`,
  which serves `web/` under the same prefix the deploy uses; `python -m http.server --directory
  web` no longer works, because nothing answers on the mount. `tests/test_web.py` has grown
  with the page and now also holds the mount and the deploy that answers on it, that every
  asset the page asks for is a file in this repository, that the header wears the vendored mark
  rather than an emoji, that the keydown handler reads nothing about the switch, that a motor
  key barges in while a read-only key does not, that a focused control keeps its own keys, that
  the abort reaches the request in flight, that nobody is in the arena — no `person` body, no
  `person` label, no copy promising one — and that the page claims no more policies than it
  downloads. The last sentence no
  longer stands as written: the page was opened in a real browser while this work was done, and
  it boots clean, wears its fonts and its mark, and walks the duck under a held `W`. What has
  still never been watched is a model driving a whole run, a key barging in out of one, and the
  recording — so the bullet's claim survives for everything downstream of a model answering,
  and not for the page itself.
- None of this makes a hardware claim. It is a better simulator, not a robot: the Microduck
  rows in `docs/adapter-status.md` that say "never run on a duck" still say it.
