# LAUNCH.md — how quackd goes public

Internal. Write it, don't publish it. 0.3's story was one duck kicking one ball. 0.4's was
that the duck is no longer the point. **0.5's is that one of the robots is one you can
build**, which is the first time any of this has been reachable by a stranger with a 3D
printer rather than a stranger with a preorder.

**The one sentence:** every robot hands quackd a manifest saying what it is and what it can
do, and the verbs the model is offered are built from that and nothing else.

**The 0.5 sentence:** the Open Duck Mini v2 costs about EUR 350 in parts, and quackd now
ships the daemon that runs on it, so the only untested thing left is the duck.

## Positioning per channel

| Channel | One line |
|---|---|
| GitHub | Pilot a small robot with any LLM through `.duck` skill files and MCP. Five robots supported, one of them open hardware you can build, a built-in simulator, no hardware needed. |
| Hacker News | A `.duck` file is a SKILL.md for a robot: the frontmatter is enforced, the body is the prompt, the executor never trusts the model. Point it at the wrong robot and it refuses before anything moves. |
| X / Twitter | Give your Microduck a brain. Your Open Duck Mini, your Reachy Mini, your arm and your wheeled base too. Any LLM, one `.duck` file. 🦆🧠 |
| Pollen Discord | We built the brain daemon that was missing from `robotd / mediad / padd / tofd`, and it now drives the Reachy Mini too. We'd like you to tell us what we got wrong about both SDKs. |
| Open Duck Mini builders (the apirrone Discord, the BDX droid crowd) | You printed a duck that walks. quackd is the layer that decides where it walks, from a plain-language goal. It ships the daemon for your Pi, it knows your duck cannot kick and cannot get up, and nobody has run it on real hardware yet, so the first person who does gets a row in the table. |
| Reachy Mini owners (Pollen + HF communities) | Your head already knows how to look and emote. quackd is the layer that decides *when*, from a plain-language goal, and it will never offer it `kick` because its manifest does not have one. |
| LeRobot / Hugging Face robotics | An LLM picks the skill, your policy executes it. `pick` is one intent that hands the arm to its own learned policy; quackd does the deciding, the gating and the transcript, and never writes a controller. |
| ROS folks | Any base that takes a `geometry_msgs/msg/Twist` over rosbridge becomes an LLM-drivable robot. No node to write, no message we invented, no deadman we pretend to have. |
| Robotics / RL folks | Three loops: the body's own reflexes (50 Hz on the duck), 10 Hz steering in Python, ~0.5 Hz LLM deliberation. The registry hook for learned verbs is the v2 story. |
| Local-LLM folks (r/LocalLLaMA, llama.cpp / vLLM / Ollama Discords) | Your own model pilots a robot, no API key: `quackd run find-and-kick --provider ollama`. Weak tool callers get a JSON text fallback. We have not benchmarked local models yet, so a transcript is a contribution. |

## Show HN title candidates

1. **Show HN: One LLM brain, five robots — each one decides what it may be asked to do**
2. Show HN: quackd – a SKILL.md-style file that makes an LLM drive a robot, and refuses the wrong robot
3. Show HN: I gave a $399 robot duck a brain, then taught it to work with a robot head

First comment (post immediately): what it is in three sentences, the manifest idea (a verb not
in the manifest does not exist), the three-loop table, the honesty paragraph (simulator and
mocks now, every hardware backend experimental and never run), and the ask ("add a `.duck` to
`ducks/`, or an adapter for the robot on your desk").

## X thread (7 posts)

1. **Hook + GIF.** "A robot head spots a ball. A robot duck kicks it. Neither could do the other's half, and they're both following the same contract. Simulator, runs in 60 seconds. 🧵" *(hetero.gif)*
2. **What.** quackd: pilot a small robot with any LLM. One `.duck` file per task, any provider, MCP so Claude Code/Desktop can drive it. Five robots today: Microduck, an Open Duck Mini v2 you can print and build, Reachy Mini, an SO-101 class arm via LeRobot, any base over rosbridge. Apache-2.0.
3. **The manifest.** "Every robot hands over a manifest: this is my body, these are my intents, these are my verbs. The model is only ever offered what's in it. A head is never offered `kick`. An arm is never offered `move`." *(the five-body table from the README's Which robots work)*
4. **The `.duck` file.** Screenshot of `find-and-kick.duck` plus the refusal: `quackd validate find-and-kick --robot reachy_mini:sim2d` → `requires kick, but reachy-01 (reachy-mini) does not provide it`, exit 1, before anything connects.
5. **MCP demo.** Short screen capture: `claude mcp add quackd -- uvx quackd serve-mcp --robots duck=microduck:sim2d,reachy=reachy_mini:mock`, then "list my robots and make the duck find the ball". One executor, budget and heartbeat per robot.
6. **Roadmap tease.** "v2: learned verbs. An LLM writes a reward (DrEureka-style), the training stack produces a policy, and it registers as one more verb. The hook exists today; the loop doesn't. Yet." Plus: an HTTP transport so the MCP server is a remote connector and you can poke the robot from your phone.
7. **CTA.** "Simulator-first and honest about it: nothing here has run on hardware, on any of the five bodies, and the README says so in a table. The Open Duck Mini is the one you can build, so it is the one most likely to change that. If you write a `.duck`, PR it to `ducks/`. If you own a robot we don't support, an adapter is a manifest and a mock. Repo: github.com/rokbenko/quackd"

## Pollen Discord post (draft)

> Hi all — long-time fan, still the duck-brain author. **quackd** is an unofficial "brain
> daemon": any LLM drives a robot through a small verb vocabulary defined in a `.duck` file,
> with a built-in 2D sim so it works before hardware ships. Since 0.4 it drives **two of your
> robots**, the Microduck and the Reachy Mini, and since 0.5 an Open Duck Mini v2 too. Demo GIF attached (sim, scripted pilots; a
> head spots and judges, a duck kicks).
>
> Three things I'd really value from the people who built the real things:
> 1. **Microduck socket assumptions.** I read `duck-ipc-proto` (API v16) and mapped verbs to
>    `robot.move` (as notifications, feeding the deadman), `robot.do{skill}`, `robot.look`,
>    `robot.sound{tag}`, `robot.health` as the heartbeat. Everything I couldn't verify is
>    tagged UNVERIFIED in one file — mainly: how to read posture from `robot.state.policy`,
>    and that there's no socket-level camera snapshot yet.
> 2. **Reachy Mini SDK assumptions.** Every name I use is read from a pinned commit and
>    tagged VERIFIED or UNVERIFIED in `adapters/reachy_mini/upstream_api.py`. The ones I'd
>    most like checked: that there's no client-disconnect deadman and no e-stop primitive
>    (so I treat `cancel_move` as `stop` and never send `disable_motors`), how camera heading
>    composes from body yaw and head yaw, and whether `look_at_world` blocks for its duration.
>    I have never run it against a robot — `reachy_mini:sdk` is experimental and says so.
> 3. **The WebSocket agent surface** from architecture.md §5.3 — I have a stub waiting for it.
>    If the design changes, I'd rather track it than guess.
>
> No Pollen assets are used (no meshes, no logos), Apache-2.0 like upstream, and the README
> says "unofficial" up top. Thank you for building the robots. Repo: <link>

Post this **before** HN/X. Maintainers first, publicity second. Worth a parallel note in the
Hugging Face LeRobot community for the arm adapter, with the same "here is what I assumed,
please correct it" framing.

## GIF shot list

1. **hetero (sim).** ✅ Recorded: `docs/assets/hetero.gif`, from
   `quackd run reachy-spots-duck-kicks --provider fake --seed 3 --gif-size 320`. This is the
   lead asset now. The head is the slate square on the wall; the duck does the walking.
2. **find-and-kick (sim).** ✅ `docs/assets/hero.gif`, scripted pilot, still the identity shot.
   Re-record with `--provider anthropic` once a key is available and drop the "scripted" label.
3. **Claude Desktop piloting a fleet via MCP.** Screen capture: connector listed → "list my
   robots" (`robot_list` shows a duck and a head) → "make the duck find the ball" → the run's
   frames. Crop to the chat and the GIF side by side. Not recorded yet.
4. **validate refusing the wrong robot.** Terminal:
   `quackd validate find-and-kick --robot reachy_mini:sim2d` → the red field-level error naming
   `kick` → swap to `--robot microduck:sim2d` → green. 10 s. This is the single clearest
   demonstration of the manifest idea. Not recorded yet.
5. (Optional) `quackd doctor` on a machine with no keys, showing the honesty tables.
6. (Optional) `reachy-spotter` alone: a head with no legs sweeping its gaze to find the ball.

## Timing

- **Now: simulator-first launch.** Discord post → 24 h → Show HN (Tue–Thu, 8–10 am ET) → X
  thread the same hour.
- **Second beat, and it no longer waits for Christmas.** Reachy Mini hardware, SO-101 arms and
  rosbridge bases all exist today, so `reachy_mini:sdk`, `lerobot:real` and `rosbridge:ws` can
  each flip from 🧪 to ✅ as soon as one person runs one of them. That is a post per body:
  "it works on the real thing", with `quackd doctor` output and a transcript.
- **Third beat: when Microducks arrive**, which is around Christmas 2026 for the earliest
  pre-orders and four to six months out for later ones, so this beat lands per person rather
  than on one date: a hardware run of the five Microduck
  starters, `jsonrpc` flipped to ✅, and the WebSocket backend if upstream shipped it. That's
  the launch that earns v1.

## Metrics that matter

- Stars are vanity.
- `.duck` PRs from strangers are the real KPI. Second: **a new adapter from someone who owns a
  robot we don't support** — that is the thesis proving itself. Third: issues that correct
  an UNVERIFIED row (that means a maintainer read `adapter-status.md`), and MCP-session
  screenshots.
- Track: PRs to `ducks/` per week, adapters contributed, unique authors, time-to-first-response
  (< 24 h).
