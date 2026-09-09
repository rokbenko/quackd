# docs/assets

| File | What it is | How it was made |
|---|---|---|
| `logo.svg` | The quackd mark + wordmark: a stylised Microduck-like biped (hooded head shell with one big side eye, wide bill, thin neck, rounded body, jointed legs, big feet) in the Lavender colourway. Our own drawing, not an upstream asset. | Hand-written SVG. |
| `social-preview.png` | 1280×640 card for GitHub's *Settings → Social preview* (no API for this — upload it once by hand) and for link previews. | Generated with Pillow from the logo geometry and a `sim2d` frame (script in the commit that added it; regenerate by re-running it). |
| `quackd-on-off.gif` | **The README hero.** One arena, recorded twice: on the left `walk in a square` with quackd, on the right the identical world, robot and walking policy with no quackd at all, where the duck stands because nothing in it reads English. Each pane carries a top-down inset of the path so far, because a chase camera on a 25 cm robot shows a duck walking and not a square. | [`hero3d.py`](hero3d.py) — the left pane is a real run through the real loop with the **scripted pilot** and no API key, and it has to *close*: the pilot measures how far from its starting point four legs left it (0.14 m in this recording) and declares failure rather than success if that is more than one side, so a GIF of four legs walked in a line cannot ship as a square. The right pane is not a worse model, it is no model: the duck is sent no twist, which is what a Microduck gets when nothing is attached to it. **The only file here that is not ours**: it renders Pollen Robotics' Microduck model, whose 3D files are CC BY-NC-SA. See [licenses.md](../licenses.md). |
| `hero.gif` | The README hero until the physics simulator landed, and still the cartoon reference shot: `find-and-kick` in the built-in `sim2d` simulator. Not in the README now, still used by [LAUNCH.md](../../LAUNCH.md). Left: world view. Right: what the duck's camera sees. | `quackd record find-and-kick --provider fake --seed 3 --gif-size 320` — **the scripted pilot, not an LLM** (see ADR-0013). Real sim, real perception, real safety layer; the decisions are a rule. |
| `transcript-example.jsonl` | The transcript of that run: system prompt, each observation, each tool call, each verb result, token counts. Predates the trace kinds ([ADR-0029](../adr/0029-tracing.md)), so it carries `llm`, `verb` and `frame` and none of `llm_request`, `verb_start`, `gate`, `intent`, `verb_end` or `note`. Re-record it with the next hero, and move the replay test that reads it (`tests/test_cli_trace.py`) onto a fixture of its own first: it is the only old-shape transcript in the repository, and `quackd trace` has to keep reading one. | Copied from `runs/<timestamp>/transcript.jsonl`. |
| `flock.gif` | Flock mode: three ducks split the search, auction the kick, the closest one takes it. Left: the shared world. Right: the claimant's camera. | `quackd record flock-kick --provider fake --seed 3` — the **scripted planner and deterministic coordinator**, no LLM in the loop (docs/flock.md). |
| `hetero.gif` | The 0.4 headline demo: a Reachy Mini head (the slate square on the wall) spots the ball and judges the kick from its own camera while a Microduck walks in and kicks it, two bodies under one contract. Left: the shared world. Right: the acting robot's camera. | `quackd run reachy-spots-duck-kicks --provider fake --seed 3 --gif-size 320` — **scripted pilots and a deterministic coordinator**, zero planner LLM calls. That run ended `success`: the spotter judged the ball moved 0.51 m and the simulator's ground truth agreed. `quackd record` is pinned to `microduck:sim2d`, so this one comes from `run`, which also writes `run.gif`. |
| `open-duck.gif` | The 0.5 headline body: an Open Duck Mini v2 finds the ball and walks up to it, because it has no kick. Left: the world from above. Right: the duck's camera. | `quackd run open-duck-scout --robot open_duck:sim2d --provider fake --seed 3 --gif-size 320` — the **scripted pilot**, not an LLM, like every other asset here. |
| `transcripts/qwen2.5-coder-14b-lmstudio-find-and-kick-seed6-memory-read.jsonl` | A real local model piloting `find-and-kick`: Qwen 2.5 Coder 14B through LM Studio, seed 6, success in 8 steps, with the previous run's episode in the prompt and no `remember` call. | `quackd run find-and-kick --provider lmstudio --model qwen/qwen2.5-coder-14b --seed 6`, copied from `runs/<timestamp>/transcript.jsonl` with the contributor's home directory removed from `duck_path`; `memory.path` is relative because the runs used `--memory-dir` next to the checkout, not `~/.quackd`. Read in [docs/local-llms.md](../local-llms.md). |
| `transcripts/qwen2.5-coder-14b-lmstudio-find-and-kick-seed5-remember.jsonl` | The same model and duck once `remember` sat in strategy step 5: seed 5, success in 4 steps, one `remember` call with a fact from the verb results. | Same command with `--seed 5`, same scrubbing. |
| `contributors.svg` | The circle of faces under *Contributing* in the README: everyone who has contributed, humans only, ordered by lines added, every avatar clipped to the same circle. | Generated by [`contributors.py`](contributors.py) from the GitHub contributors API, avatars inlined so the file needs no network. Regenerated by `.github/workflows/contributors.yml` on every push to `main` and weekly, committed only when it changes. |
| *(missing)* `reachy-spotter.gif` | **Not recorded yet.** A head with no legs finding the ball with its gaze, the simplest illustration that the verb list follows the body. | Would be `quackd run reachy-spotter --provider fake --seed 3`. |

Every recording here is driven by the scripted pilot rather than a model, and every caption
says so. For the `quackd record` and `quackd run` assets, replacing one with a real model run is
a matter of swapping `--provider fake` for `--provider anthropic`, copying
`runs/<timestamp>/run.gif` over the file, and dropping the word *scripted* from the caption and
this table.

The hero is different. `hero3d.py` builds the scripted pilot in code and exposes no provider
flag, and the square shape is that pilot's own strategy (`square_strategy` in
`quackd/agent/providers/fake.py`, keyed off the word *square* in the goal). A real model
recording means editing the script to take a provider and letting the model walk the square
itself, which is the more interesting recording and costs a key.

**On upstream's assets.** This directory used to say that no Pollen Robotics asset would ever
live here, and for every file but one that is still true: the logo is our own drawing, and every
`sim2d` recording is our own cartoon. `quackd-on-off.gif` is the exception. It renders
Pollen's Microduck model, whose 3D files upstream's README licenses CC BY-NC-SA, and it is here
because a picture of the real robot walking on its real gait says something a drawing of a box
cannot. It is labelled in the table above, in [licenses.md](../licenses.md) and in the README
caption; it is the only such file, and it is non-commercial and share-alike where the rest of
quackd is Apache-2.0. No upstream mesh, policy, logo or brand asset is committed here in its own
form, and none ever will be: the simulator fetches those at run time (see
[`quackd/sim3d/assets.py`](../../quackd/sim3d/assets.py)).
