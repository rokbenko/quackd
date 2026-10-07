# LAUNCH.md — how quackd goes public

Internal. Write it, don't publish it. 0.3's story was one duck kicking one ball. 0.4's was
that the duck is no longer the point. 0.5's was that one of the robots is one you can build.
0.8's was that you can watch it happen without installing anything, and the duck is really
walking. 0.9's was that you register the robots you own, name a flock of them, hand the
flock a goal, and every robot gets its own LLM pilot, talking to the others. 0.10's was that
a real arm ran it, `uv pip install quackd` now installs no robot at all and you choose the
body, and the arm no longer falls over when the run ends. 0.11's was that a run says when
it ran and what it cost, the trace is the log, and the log is the whole screen. 0.12's was
that a decision LLM is a row of data rather than a vendor, the pilot is one flag, and the
trace spellings are gone. 0.13's was that quackd has a path onto an NVIDIA Jetson, where a
Jetson is a host rather than a body, and that a bare `--llm anthropic` and `--llm openai`
now run Claude Opus 5.5 and GPT-6 Sol. 0.14's was that a real SO-101 ran ten of the task files
written for it, the second afternoon quackd has spent on an arm, and that each of the five
faults that afternoon found has an answer: the arm parks where its servos can reach, lets go
when a person asks, connects again after a bad packet, moves at the pace it is given and can
say that its task goes nowhere. 0.15's was that quackd reaches an NVIDIA Jetson from the
laptop and never runs on one, and that a task for an SO-101 is rehearsed at home, through the
arm's own backend on the maker's model of it, before a trip to the lab. 0.16's was that the
arm's pilot hands it to a learned policy one short segment at a time and judges each from a
fresh look, with the policy served from a process of its own while quackd keeps the bus, the
pace and the stops, and that a trained policy has driven the arm's simulator this way and not
yet the arm. **0.17's is that one OpenRouter key reaches models made by many vendors, through
six rows quackd carries and through other ids OpenRouter lists with tool calling, which quackd
checks against that list before the first paid call, though no OpenRouter model has answered a
real quackd request yet.**

A patch has no story of its own, so it gets no sentence here ([RELEASING.md](RELEASING.md)).

**The one sentence:** One CLI for all your robots. Connect them, command them, and let them
work together, each with an LLM for a brain.

**The second:** every robot hands quackd a manifest saying what it is and what it can do,
and the verbs the model is offered are built from that and nothing else.

**The 0.8 sentence:** open a link, type a sentence, and a Microduck walks on Pollen's own
trained policy in real MuJoCo physics — with the keyboard live the whole time, a centimetre
from the box you type in, so you can take the duck back mid-sentence and see for yourself
what the layer is worth.

The keyboard is not something the switch hands over, because it was never taken away. Both
ways of driving are live at once and neither takes turns with the other; a drive key pressed
during a run takes the duck at once and aborts the run and the request to the model with it.
The switch decides one thing only: whether anything on the page reads English. The sharpest
version of that argument is an absence — **there is no key for `say`**. A key carries a
command; a sentence needs something to read it.

> The demo is live at <https://www.quackd.org/simulator>, served from `web/` with no build step and
> fetched onto that path at build time by quackd-web, a separate Vercel project. That is the link to
> give out. Sit with it yourself before any of this goes out — there, or locally with
> `python web/serve.py`, then <http://localhost:8000/simulator/>. The page has
> been opened in a browser twice while it was built: it boots clean and a held `W` walks the
> duck. Nobody has yet watched a model drive a whole run, a key barge in out of one, or the
> Record button work, which is exactly the material every asset below needs.

## Positioning per channel

| Channel | One line |
|---|---|
| GitHub | Connect and command every robot you own from one CLI, each with an LLM for a brain, and a flock of them dividing a task by talking to each other. Seven robots supported, three of them open hardware you can build, a physics simulator, a browser demo, and one of the seven driven on a real arm on camera, so you need no hardware to try it and there is hardware behind it. |
| Hacker News | A `.duck` file is a SKILL.md for a robot: the frontmatter is enforced, the body is the prompt, the executor never trusts the model. Point it at the wrong robot and it refuses before anything moves. |
| X / Twitter | One CLI for all your robots. Connect them, command them, and let them work together, each with an LLM for a brain. Take one out of the flock: type *walk in a square* and watch it happen in your browser, on the robot's own trained gait, then hit `W` mid-run and take the duck off the model, no mode to flip first. Six other bodies, from an Open Duck Mini you can print to a ToddlerBot humanoid. 🦆🧠 |
| Pollen Discord | We built the brain daemon that was missing from `robotd / mediad / padd / tofd`, and it is now the CLI that commands a flock of robots with the Microduck among them. We'd like you to tell us what we got wrong about the SDK. |
| Open Duck Mini builders (the apirrone Discord, the BDX droid crowd) | You printed a duck that walks. quackd is the layer that decides where it walks, from a plain-language goal. It ships the daemon for your Pi, it knows your duck cannot kick and cannot get up, and no Open Duck has run it yet: the one body that has is an arm, so the first person to run a duck gets a row in the table. |
| LeRobot / Hugging Face robotics | An LLM picks the skill, your policy executes it. `pick` is one intent that hands the arm to its own learned policy; quackd does the deciding, the gating and the transcript, and never writes a controller. |
| ROS folks | Any base that takes a `geometry_msgs/msg/Twist` over rosbridge becomes an LLM-drivable robot. No node to write, no message we invented, no deadman we pretend to have. |
| Robotics / RL folks | Three loops: the body's own reflexes (50 Hz on the duck), 10 Hz steering in Python, ~0.5 Hz LLM deliberation. The registry hook for learned verbs is the v2 story. |
| Local-LLM folks (r/LocalLLaMA, llama.cpp / vLLM / Ollama Discords) | Your own model pilots a robot, no API key: `quackd run find-and-kick --llm ollama`. Weak tool callers get a JSON text fallback. We have not benchmarked local models yet, so a transcript is a contribution. |

## Show HN title candidates

1. **Show HN: One CLI for all your robots, an LLM for a brain each, and they talk to each other**
2. Show HN: quackd – a SKILL.md-style file that makes an LLM drive a robot, and refuses the wrong robot
3. Show HN: I gave a $399 robot duck a brain, then built the CLI that commands a whole flock of them

First comment (post immediately): what it is in three sentences, the manifest idea (a verb not
in the manifest does not exist), the three-loop table, the honesty paragraph (one arm has run it,
on two afternoons: on 2026-09-15 it fell at the end of every run, and on 2026-09-23, with a rest
pose recorded, 19 of 26 runs never moved it at a pilot's request and every run that reached its
close kept torque on until the power switch, and the fixes for that have not run on an arm yet;
the other six hardware backends are experimental and have only ever talked to fakes), and the
ask ("add a `.duck` to `ducks/`, or an adapter for the robot on your desk").

## X thread (8 posts)

1. **Hook + GIF.** "You type *wave to the camera with an extended arm*. A model picks one of the arm's own verbs at a time, quackd checks each against the contract, and a real SO-101 waves. No controller written, no script: a phone pointed at a bench. The simulator version needs no arm and runs in 60 seconds. 🧵" *(lerobot.gif)*
2. **What.** quackd: one CLI for all your robots. Connect them, command them, and let them work together, each with an LLM for a brain. One `.duck` file per task, any provider, MCP so Claude Code/Desktop can drive it. Seven robots today: Microduck, an Open Duck Mini v2 you can print and build, an SO-101 class arm via LeRobot, any base over rosbridge, an XLeRobot dual-arm cart, an AlohaMini with two arms on a lift and a ToddlerBot humanoid. Apache-2.0.
3. **Both hands on the same duck.** "The demo puts a sentence box and a live keyboard on one robot, a centimetre apart, and neither takes turns with the other. Press `W` mid-run and you have the duck: the run aborts, the request to the model aborts with it so no answer arrives after you took it back, and the transcript names the key that did it. `O` and the camera keys read without interrupting anything. There is no key for `say` — a key carries a command, a sentence needs something to read it." *(browser session, shot 3)*
4. **The manifest.** "Every robot hands over a manifest: this is my body, these are my intents, these are my verbs. The model is only ever offered what's in it. An arm is never offered `move`. A wheeled base is never offered `kick`." *(the seven-body table from the README's Which robots work)*
5. **The `.duck` file.** Screenshot of `find-and-kick.duck` plus the refusal: `quackd validate find-and-kick --robot lerobot:mock` → `requires kick, but arm-01 (lerobot-so101) does not provide it`, exit 1, before anything connects.
6. **MCP demo.** Short screen capture: `claude mcp add quackd -- uvx --from "quackd[microduck,lerobot]" quackd serve-mcp --robots duck=microduck:sim2d,arm=lerobot:mock`, then "list my robots and make the duck find the ball". One executor, budget and heartbeat per robot. `--flock <name>` serves the same set from the registry, so you name the robots once.
7. **Roadmap tease.** "v2: learned verbs. An LLM writes a reward (DrEureka-style), the training stack produces a policy, and it registers as one more verb. The hook exists today; the loop doesn't. Yet." Plus: an HTTP transport so the MCP server is a remote connector and you can poke the robot from your phone.
8. **CTA.** "One of the seven bodies has run on hardware, an SO-101 arm on 2026-09-15 and again on 2026-09-23. It fell at the end of every run the first day, and the second day's rest pose could not reach its fold, which the README says in a table right beside the wave. The other six have only ever talked to fakes. The Open Duck Mini is the one you can build, so it is the one most likely to be next. If you write a `.duck`, PR it to `ducks/`. If you own a robot we don't support, an adapter is a manifest and a mock. Repo: github.com/rokbenko/quackd"

## Pollen Discord post (draft)

> Hi all — long-time fan, still the duck-brain author. **quackd** started as an unofficial
> "brain daemon" and is now the CLI that commands a flock of robots: any LLM drives a robot
> through a small verb vocabulary defined in a `.duck` file, with a built-in 2D sim so it
> works before a Microduck ships, and one real arm behind it already. Since 0.5 it drives an Open Duck Mini v2 too, alongside the
> Microduck. Demo GIF attached (a real SO-101 under a real model; the duck clips are sim, scripted pilot).
>
> One thing I'd really value from the people who built the real thing:
> 1. **Microduck socket assumptions.** I read `duck-ipc-proto` (API v23, pinned) and mapped verbs to
>    `robot.move` (as notifications, feeding the deadman), `robot.do{skill}`, `robot.look`,
>    `robot.sound{tag}`, `robot.health` as the heartbeat. Everything I couldn't verify is
>    tagged UNVERIFIED in one file — mainly: how to read posture from `robot.state.policy`,
>    and that there's no socket-level camera snapshot yet.
> 2. **The WebSocket agent surface** from architecture.md §5.3 — I have a stub waiting for it.
>    If the design changes, I'd rather track it than guess.
>
> No Pollen assets are used (no meshes, no logos), Apache-2.0 like upstream, and the README
> says "unofficial" up top. Thank you for building the robot. Repo: <link>

Post this **before** HN/X. Maintainers first, publicity second. Worth a parallel note in the
Hugging Face LeRobot community for the arm adapter, with the same "here is what I assumed,
please correct it" framing.

## GIF shot list

1. **A real arm, waving.** ✅ Recorded: `docs/assets/lerobot.gif`, a phone pointed at the
   bench on 2026-09-15, run `20260915-145349-goal`, `gpt-6-astra` on `lerobot:real`. The lead
   asset and the README hero: a model choosing the arm's own verbs, one at a time, and no
   controller written for the wave. Beside it `docs/assets/lerobot-what-it-saw.png`, three of
   the frames the model was actually sent, the last with a hand waving back.
2. **quackd on and off, side by side.** ✅ Recorded: `docs/assets/quackd-on-off.gif`, from
   `docs/assets/hero3d.py`. The simulator's lead asset, and the README hero until the arm
   ran. One arena, the same sentence, and the duck on the right standing still because
   nothing in it reads English. This is the simulator pitch in one loop.
3. **A browser session**, which the demo's own Record button produces. Type the goal, let it
   walk, then take the duck mid-run with `W` as the second beat: the run and the request in
   flight both abort and the transcript prints the handover line naming the key. That is the
   beat, not flipping the switch. The switch does change the page — the label, the note, the
   accent colour, the key bay, Run becoming Send — but nothing in the arena moves when you flip
   it, and a clip whose second beat is a recolour is a clip about a checkbox. Then Share on X,
   which the page writes the post for. Not recorded: the page boots and the keyboard works, but
   nobody has run the Record button.
4. **find-and-kick (sim).** ✅ `docs/assets/hero.gif`, scripted pilot, still the cartoon shot.
   Re-record with `--llm anthropic` once a key is available and drop the "scripted" label.
5. **Claude Desktop piloting a flock via MCP.** Screen capture: connector listed → "list my
   robots" (`robot_list` shows two robots) → "make the duck find the ball" → the run's
   frames. Crop to the chat and the GIF side by side. Not recorded yet.
6. **A pilot flock.** Two members' terminal views side by side: the duck and the arm telling
   each other what they are, and each declaring for itself whether it got there.
   EXPERIMENTAL, and two simulated pilots are two separate worlds with no shared arena, so
   the clip is two transcripts rather than one scene. `tell` has been exercised by the
   scripted pilot and by no real model. Not recorded yet.
7. **validate refusing the wrong robot.** Terminal:
   `quackd validate find-and-kick --robot lerobot:mock` → the red field-level error naming
   `kick` → swap to `--robot microduck:sim2d` → green. 10 s. This is the single clearest
   demonstration of the manifest idea. Not recorded yet.
8. (Optional) `quackd doctor` on a machine with no keys, showing the honesty tables.
9. (Optional) `open-duck-scout` alone: a printed duck finding the ball and walking up to it.

## Timing

- **Before any of it: open the page yourself.** Every line above invites somebody to follow a
  link that now answers — <https://www.quackd.org/simulator> is live, and quackd-web's build
  refreshes it from `web/` on each deploy. Watch a model drive a whole run there, barge in on one
  with a key, and then post.
- **Now: one real arm on camera, and a simulator for everyone without one.** Discord post → 24 h → Show HN (Tue–Thu, 8–10 am ET) → X
  thread the same hour.
- **Second beat, and one of its two posts has happened.** SO-101 arms and rosbridge bases
  all exist today. `lerobot:real` flipped to ✅ on 2026-09-15 and is a post of its own, with
  the recording, the `quackd doctor` output and the transcript, and `rosbridge:ws` can flip
  the same way as soon as one person runs it against a bridge. That is still a post per
  body: "it works on the real thing".
- **Third beat: when Microducks arrive**, which is around Christmas 2026 for the earliest
  pre-orders and four to six months out for later ones, so this beat lands per person rather
  than on one date: a hardware run of the six Microduck
  starters, `jsonrpc` flipped to ✅, and the WebSocket backend if upstream shipped it. That's
  the launch that earns v1.

## Metrics that matter

- Stars are vanity.
- `.duck` PRs from strangers are the real KPI. Second: **a new adapter from someone who owns a
  robot we don't support** — that is the thesis proving itself. Third: issues that correct
  an UNVERIFIED row (that means a maintainer read an adapter's page under `docs/adapters/`),
  and MCP-session screenshots.
- Track: PRs to `ducks/` per week, adapters contributed, unique authors, time-to-first-response
  (< 24 h).
