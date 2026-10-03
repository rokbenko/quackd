# docs/assets

| File | What it is | How it was made |
|---|---|---|
| `lerobot.gif` | **The README hero.** A real arm, a real model, no script: an SO-101 follower on `lerobot:real`, told *wave to the camera with an extended arm*, piloted by OpenAI's `gpt-6-astra`, filmed on a phone on 2026-09-15. Run `20260915-145349-goal`: eight steps, ten LLM calls, 79 seconds, shown at ten times speed. The model chose `report_state`, then `move_joints` once to extend (`shoulder_lift` -39, `elbow_flex` 30), four times to roll the wrist (+28, -25, +25, -25) and once more to return it to centre (-4), then `stop`, which is the eight, and declared success off the joint readings because the webcam cropped the raised arm. What a viewer cannot see is written down in the README: the arm fell when the run ended, because that afternoon predates the rest pose; four earlier wave runs from the same afternoon sat in its prompt through memory, so the strategy was recalled as much as improvised; and the prompt told it `40 steps` while `--max-steps 10` was in force, a bug this recording found. | [`lerobot_hero.py`](lerobot_hero.py) `--video`, which shells out to ffmpeg: 640 pixels wide, 10 frames a second, a 128-colour palette chosen from what moves, Bayer dither, a light `hqdn3d`. **The one file here allowed over the 2048 KB cap**, with a cap of its own in that script and an exclude in `.pre-commit-config.yaml` — a render spends bytes on what moved and a photograph of a lab spends them on every pixel of every frame, so this does not fit at a width worth leading a page with. The first recording here with a model in the loop, and the only one made on hardware. quackd's own footage, no upstream licence. The video and the run's frames and transcript are kept outside the repository, beside the other eleven runs of that afternoon, on the machine that drove the arm. |
| `lerobot-what-it-saw.png` | **What the pilot saw**, from the same run as the hero: three of the ten frames `lerobot:real` wrote to `frames/` and sent to the model. `0000.png`, the arm folded before the first call. `0003.png`, after step 2, extended, with the webcam cropping the raised arm at the top of the picture, which is why the model verified its wave from joint angles rather than from this. `0009.png`, after the `stop`, with a hand waving back. One USB webcam at `opencv://2`, 640x480, the only camera the run had. | [`lerobot_hero.py`](lerobot_hero.py) `--run`: each frame downscaled to 392 pixels wide with Lanczos and nothing else done to it, a caption strip under each naming the step. The frames are the model's actual input rather than a phone's view of the bench, which is the point of putting them beside the hero. |
| `quackd-on-off.gif` | **The README's simulator figure, and its hero until the real arm.** One arena, recorded twice: on the left `walk in a square` with quackd, on the right the identical world, robot and walking policy with no quackd at all, where the duck stands because nothing in it reads English. Each pane carries a top-down inset of the path so far, because a chase camera on a 25 cm robot shows a duck walking and not a square. | [`hero3d.py`](hero3d.py) — the left pane is a real run through the real loop with the **scripted pilot** and no API key, and it has to *close*: the pilot measures how far from its starting point four legs left it (0.14 m in this recording) and declares failure rather than success if that is more than one side, so a GIF of four legs walked in a line cannot ship as a square. The right pane is not a worse model, it is no model: the duck is sent no twist, which is what a Microduck gets when nothing is attached to it. **The only file here under terms that are not quackd's**: it renders Pollen Robotics' Microduck model, whose 3D files are CC BY-NC-SA. See [reference/licenses.md](../reference/licenses.md). |
| `lerobot-sim.gif` | **The arm's simulator, and the first cloud model filmed in a simulator here.** A bare `--robot lerobot:mujoco`, the generic arm with no calibration file, told the hero's sentence, *wave to the camera with an extended arm*, and piloted by OpenAI's `gpt-6-sol` on 2026-09-28. Run `20260928-203959-goal`, seed 0: seven steps, nine LLM calls, 69 seconds on the wall and 50 of them waiting on the model, 11.8 seconds of simulated time, $0.0537. The model read the arm with `report_state`, answered `assess_task` feasible, raised the arm with one `move_joints` (`shoulder_pan` -35, `shoulder_lift` -45, and `elbow_flex`, `wrist_flex` and `wrist_roll` 0), held it out level with a second (`shoulder_lift` 15, `shoulder_pan` -40, `elbow_flex` and `wrist_flex` 0), swung `shoulder_pan` to 35, -35 and 35, which the arm reached as 35, -37 and 34, read the arm again and declared success, saying it had waved twice where the frames show three sweeps. Left: a fixed view of the table from in front and to one side, which no camera in the run has. Right: the scene's `front` camera, the run's only one, whose latest frame the model was sent at every turn, and which cuts off the raised arm at the top as the webcam on the bench did. | [`lerobot_sim.py`](lerobot_sim.py) `--llm openai:gpt-6-sol`, run from a scratch directory with memory off, the first of the three tries the recording was allowed. A real run through the real loop, filmed by a tick hook on the simulator's clock every 0.2 s of simulated time, so the model's thinking is not in it and it plays at twice the simulator's speed: 596 pixels wide, 60 frames, one palette of 96 colours for the whole film. Without `--llm` it flies a scripted pilot built in the script, which needs no key. It renders TheRobotStudio's SO-ARM100 model of the SO-101 at the commit the simulator pins, Apache-2.0: see *On upstream's assets* below and [reference/licenses.md](../reference/licenses.md). |
| `hero.gif` | The README hero until the physics simulator landed, and still the cartoon reference shot: `find-and-kick` in the built-in `sim2d` simulator. Not in the README now, still used by [LAUNCH.md](../../LAUNCH.md). Left: world view. Right: what the duck's camera sees. | `quackd record find-and-kick --llm fake --seed 3 --gif-size 320` — **the scripted pilot, not an LLM** (see ADR-0013). Real sim, real perception, real safety layer; the decisions are a rule. |
| `transcript-example.jsonl` | The transcript of that run: system prompt, each observation, each tool call, each verb result, token counts. Predates the log kinds ([ADR-0029](../adr/0029-tracing.md)), so it carries `llm`, `verb` and `frame` and none of `llm_request`, `verb_start`, `gate`, `intent`, `verb_end` or `note`. Re-record it with the next hero, and move the replay test that reads it (`tests/test_cli_log.py`) onto a fixture of its own first: it is the only old-shape transcript in the repository, and `quackd log` has to keep reading one. | Copied from `runs/<timestamp>/transcript.jsonl`. |
| `flock.gif` | Flock mode: three ducks split the search, auction the kick, the closest one takes it. Left: the shared world. Right: the claimant's camera. | `quackd record flock-kick --llm fake --seed 3` — the **scripted planner and deterministic coordinator**, no LLM in the loop (docs/guides/flock.md). |
| `open-duck.gif` | The 0.5 headline body: an Open Duck Mini v2 finds the ball and walks up to it, because it has no kick. Left: the world from above. Right: the duck's camera. | `quackd run open-duck-scout --robot open_duck:sim2d --llm fake --seed 3 --gif-size 320` — the **scripted pilot**, not an LLM, like every simulator recording here but `lerobot-sim.gif`. |
| `transcripts/qwen2.5-coder-14b-lmstudio-find-and-kick-seed6-memory-read.jsonl` | A real local model piloting `find-and-kick`: Qwen 2.5 Coder 14B through LM Studio, seed 6, success in 8 steps, with an earlier run's episode in the prompt and no `remember` call. | `quackd run find-and-kick --llm lmstudio:qwen/qwen2.5-coder-14b --seed 6`, copied from `runs/<timestamp>/transcript.jsonl` with the contributor's home directory removed from `duck_path`; `memory.path` is relative because the runs used `--memory-dir` next to the checkout, not `~/.quackd`. Read in [docs/guides/local-llms.md](../guides/local-llms.md). |
| `transcripts/qwen2.5-coder-14b-lmstudio-find-and-kick-seed5-remember.jsonl` | The same model and duck once `remember` sat in strategy step 5: seed 5, success in 4 steps, one `remember` call with a fact from the verb results. | Same command with `--seed 5`, same scrubbing. |
| `transcripts/qwen3-32b-awq-vllm-find-and-kick-seed1-thinking-on.jsonl` | **Qwen3 deliberating.** Qwen3-32B-AWQ through a self-hosted vLLM, `find-and-kick`, success in 4 steps. All eight LLM calls carry a `thinking` field, its first call is refused by the verdict gate, and its `remember` call writes the note the next file reads. The control half of the only measurement anybody has of what `--extra-body` stops. | `quackd run find-and-kick --llm local:Qwen/Qwen3-32B-AWQ --base-url http://<host>:8011/v1 --robot microduck:sim2d --seed 1`, on `main` at `739ff84` through `uvx`, copied from `runs/<timestamp>/transcript.jsonl`. The contributor made `duck_path` relative and wrote `memory.path` back as its unexpanded default `~/.quackd/memory/...`, because `uvx` had left an absolute Windows cache path in the first and `memory_dir`'s own `expanduser` had left a home directory in the second. Nothing else was touched, and no transcript records its seed, so the 1 is the contributor's word and not the file's. Read in [docs/guides/local-llms.md](../guides/local-llms.md). |
| `transcripts/qwen3-32b-awq-vllm-find-and-kick-seed1-thinking-off.jsonl` | **The same thing told not to think.** Same build, same seed, same server, `--extra-body '{"chat_template_kwargs": {"enable_thinking": false}}'` the only difference: success in 3 steps, not one `thinking` field in the file, and `run_start` carries the `extra_body` object so the run says for itself what it was told. Its prompt holds the note and the episode the run above wrote, which makes the pair the first published chain of quackd's memory. | Same command with the flag added, same scrubbing. |
| `contributors.svg` | The circle of faces under *Contributing* in the README: everyone who has contributed, humans only, ordered by lines added, every avatar clipped to the same circle. | Generated by [`contributors.py`](contributors.py) from the GitHub contributors API, avatars inlined so the file needs no network. Regenerated by `.github/workflows/contributors.yml` on every push to `main` and weekly, committed only when it changes. |

**There is no logo in this directory, and the README opens with its name.** A duck's head over
the title made quackd look like a toy rather than a tool that drives a real arm, so the README
lost it, and the social preview card built around it was deleted rather than redrawn.

Every *simulator* recording here but one is driven by the scripted pilot rather than a model,
and every caption says which. The one is `lerobot-sim.gif`, OpenAI's `gpt-6-sol` on the arm's
simulator on 2026-09-28, the first time a model has been filmed in a simulator here. Two more
files are not the script either: `lerobot.gif` and `lerobot-what-it-saw.png` are a real arm under
a real cloud model, OpenAI's `gpt-6-astra`, on 2026-09-15. The transcripts are the other real
model runs in this directory: four of them, local models on the duck's `sim2d`, two
contributors, two local servers, no API key and no frame between them. So two cloud models have
been recorded here, one on hardware and one in the arm's simulator, and none yet in the duck's.
For the `quackd record` and `quackd run` assets, replacing one with a real model run is
a matter of swapping `--llm fake` for `--llm anthropic`, copying
`runs/<timestamp>/run.gif` over the file, and dropping the word *scripted* from the caption and
this table.

**The transcripts here predate the rename.** The trace became the log in 0.11 and every one of
them was recorded before that, so the two Qwen3 ones still carry `trace_dropped` in their
`run_end` where a run recorded today writes `log_dropped`. That key stays as it was written:
these are evidence of what a model did on somebody else's machine on a particular day rather
than fixtures to be tidied, and `quackd log` replays them as they are because the reader takes
both spellings. [ADR-0029](../adr/0029-tracing.md) keeps its name for the same reason.

**The next transcript published here needs one more field scrubbed.** A run recorded from 0.11
writes the command that started it into `run_start.command`. What quackd takes out of it is in
[SECURITY.md](../../SECURITY.md): the values of `--api-key` and `--token`, and the password and
any credential-named query parameter in a `--base-url`, an `--address` or a `--camera-url`.
Everything else is there as typed, so a `--runs-dir`, a bare host and a duck path off somebody's
machine all land in the record.
`tests/test_transcript_assets.py` reads every string in every row and fails on a home directory,
a host or an IP address wherever it finds one, so it catches this, and a contributor who has just
had it fail on `command` is reading the right paragraph: make the paths relative or take them out
by hand, the way the four files above had `duck_path` and `memory.path` taken out.

`quackd-on-off.gif` is different again. `hero3d.py` builds the scripted pilot in code and exposes no provider
flag, and the square shape is that pilot's own strategy (`square_strategy` in
`quackd/agent/providers/fake.py`, keyed off the word *square* in the goal). A real model
recording means editing the script to take a provider and letting the model walk the square
itself, which is the more interesting recording and costs a key.

The hero is different from both. It is a phone pointed at a bench, so there is no script that
re-records it, only the one that re-cuts it: `lerobot_hero.py` takes a video and a run
directory and neither is in this repository. Re-recording it means an arm, a model and an
afternoon, and the next person to spend one is welcome to send a better clip.

**On upstream's assets.** This directory used to say that no Pollen Robotics asset would ever
live here, and for every file but one that is still true: every `sim2d` recording is our own
cartoon. `quackd-on-off.gif` is the exception. It renders
Pollen's Microduck model, whose 3D files upstream's README licenses CC BY-NC-SA, and it is here
because a picture of the real robot walking on its real gait says something a drawing of a box
cannot. It is labelled in the table above, in [reference/licenses.md](../reference/licenses.md) and
in the README caption; it is the only file under those terms, and it is non-commercial and
share-alike where the rest of quackd is Apache-2.0. It is no longer the only file that renders an
upstream model: `lerobot-sim.gif` renders TheRobotStudio's model of the SO-101 from SO-ARM100, at
the commit the arm's simulator pins, which that repository licenses Apache-2.0 as quackd is, and it
is labelled in the table above and in [reference/licenses.md](../reference/licenses.md). No upstream
mesh, policy, logo or brand asset is committed here in its own form, and none ever will be: the
simulators fetch those at run time (see
[`adapters/microduck/src/quackd_microduck/sim3d/assets.py`](../../adapters/microduck/src/quackd_microduck/sim3d/assets.py)
and [`adapters/lerobot/src/quackd_lerobot/sim/assets.py`](../../adapters/lerobot/src/quackd_lerobot/sim/assets.py)).
