# PLAN.md — quackd

What is still open. Everything that has shipped is in [CHANGELOG.md](CHANGELOG.md), the
[ADRs](docs/adr/) and the git history, which record it better than a task list can.

Legend: 🔨 in progress · ⬜ todo · ⏸ blocked (with reason)

## Only a human can

Six bring-ups still need hardware quackd has never touched, one per body that has not met
any: the other six of the seven. The seventh happened, an SO-101 arm running
`lerobot:real` on 2026-09-15, which flipped its row in
[`docs/adapter-status.md`](docs/adapters/status.md), and the same arm ran again on 2026-09-23,
registered as `arm-01`. Each of the six ends the same way: flip that backend's row, and not
before.

- ⏸ **An Open Duck Mini v2**, the most reachable of the six because you can build it. Run
  `open_duck:bridge` against a duck you built, work through
  [docs/open-duck-hardware-checklist.md](docs/adapters/open_duck/hardware-checklist.md), and confirm
  the deadman by pulling Wi-Fi mid-walk. Then the five numbers at the end of that checklist:
  boot time against the watchdog budget, camd's peak memory against its cap, the observed
  loop-rate floor, the camera's field of view against a tape measure, and the accelerometer
  upright versus on its side. The last one is what would give this robot fall detection, and
  quackd deliberately does not guess it, because a wrong fall detector fails as a confident
  "not fallen".
- ⏸ **A Microduck.** Run `--robot microduck:jsonrpc` against a real `robotd` and work through
  [docs/microduck-hardware-checklist.md](docs/adapters/microduck/hardware-checklist.md), whose step 0
  now rehearses the whole pilot in the physics simulator first. The path is built and audited:
  pinned at a commit and bumped to API v23 (it was v16 against a moving link, so the handshake
  would have refused), state actually subscribed to, and video over `webrtc://` because
  upstream serves no frames on the socket. Pre-orders opened 2026-08-27, earliest arrivals
  estimated around Christmas 2026 and later orders four to six months out.
- ⏸ **A ToddlerBot** on its safety stand, running `bridge/toddlerbot/quackd_toddlerbot_bridge.py`
  through [docs/toddlerbot-hardware-checklist.md](docs/adapters/toddlerbot/hardware-checklist.md). What
  most needs a real robot: whether the safe-pose slew is safe from a crawl, what tilt really
  means fallen, whether the neck axes are what the motor names imply, and whether a calibrated
  zero survives a restart.
- ⏸ **An XLeRobot.** Start the host (it is commented out of upstream's own package `__init__`
  and exits after an hour) and point `xlerobot-lookout` at it. What most needs a real cart: the
  camera colour order, whether `+x` is really forward, and whether the head motors are what
  upstream's agent library implies.
- ⏸ **An AlohaMini.** Start `bridge/alohamini/quackd_alohamini_host.py` rather than upstream's
  host and point `alohamini-lookout` at it. What most needs a real robot: whether `+x` is
  physically forward, the camera colour order, how fast the lift travels in mm/s, and whether
  the wrapper really does leave the arms holding.
- ✅ **An SO-101 arm**, on 2026-09-15: calibrated with upstream's own tool, then
  `lerobot-lookout` and twelve free-form goals, on a USB webcam at `opencv://2`, piloted by
  `gpt-6-astra`. What that afternoon left open, each a bring-up of its own: the rest pose,
  written after that day, which first met the arm on 2026-09-23 and could not reach a fold that
  lay past the calibrated travel
  ([ADR-0045](docs/adr/0045-a-rest-pose-the-calibration-cannot-reach.md)); `pick`, because
  no policy was loaded, which the learned-policy item below carries now; the registry path,
  because the arm was reached by `--address` and never by a registered name, which it was on
  2026-09-23 when its rest pose was recorded; and that checklist's *What to report*, six things
  still chosen against Feetech's documentation rather than measured
  ([ADR-0036](docs/adr/0036-what-the-arm-does-not-say.md)).
- ⬜ **The SO-101 again, for everything in 0.14.0, 0.15.0, 0.16.0 and 0.16.1.** The arm last ran
  a build of the code that became quackd 0.13.0, still numbered 0.12.0. The rest pose clipped
  into the travel, `quackd robot release`, the Enter offer at the end of a run whose rest move
  missed, the `--by-hand` refusal over a joint past its travel, the paced `move_joints` and the
  connect retries have run only against a fake arm, `lerobot:mock` and the test suite, and what
  0.15.0 changes in `lerobot:real`, the follower that keeps torque on a disconnect quackd did
  not ask for among it, only against a fake arm or the simulator. What 0.16.0 changes there has
  run only against those too, and most of it is the policy segment the learned-policy item below
  takes to the arm. So has what 0.16.1 changes there: the connect refusing a policy server whose
  declared latency no chunk can carry, the tick a segment asks for its next chunk on, the tick
  its time runs out on, and the travel row of `quackd doctor`. The seven bench steps that would
  settle 0.14.0's, in order, are under *Known limitations* in [CHANGELOG.md](CHANGELOG.md) for
  0.14.0, and the one for 0.15.0's follower, a second Ctrl-C during the fold back to the rest
  pose, is under 0.15.0's. The six other changes that release made in `lerobot:real` have bench
  steps now, and none has run: a connect quackd refuses letting go of the arm (step 6 of
  [docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md)), the deadline a call
  spends only while the bus is busy (steps 9 and 13), a connect that fails once the arm is
  energised keeping its torque (step 13), the close reading its flag back and saying a stalled
  shortfall once (step 14), and the heartbeat's reads kept out of the trace a grasp is judged by
  (step 17). That checklist is the order to take the arm through, with a hand on the switch.
- ⬜ **The SO-101 against its simulator.** `lerobot:mujoco` runs the arm's real backend over the
  maker's model, and what it assumes about the arm only the arm can settle
  ([ADR-0047](docs/adr/0047-the-arms-simulator-runs-the-real-backend.md)). Each of these is a
  bench step of its own, and none of them replaces the seven above:
  - joint signs and zero offsets: nudge each real joint by a small positive angle and check that
    it turns the same way in the simulator, and read the calibrated value at each mechanical stop
    against the model's stop, which also says whether a recorded fold can be represented at all
    (`JOINT_SIGN`, `JOINT_ZERO`). `arm-01`'s recorded fold puts the model's fingers into the
    table and its lower arm into its shoulder, and the simulator starts it settled clear of both,
    and parks it where it settles at the edge of `shoulder_lift`'s travel, with a note, so this
    step also says whether the model's frame or the table's height is what is wrong;
  - the rest move pressing the gripper into the table: in the model, the rest goal the
    calibration clips (`shoulder_lift` clipped to its travel, the elbow and the wrist held where
    they were recorded) puts the gripper below the table top. Before trusting a rest move on the
    arm, watch one from a raised pose with a hand on the switch and check that the gripper stops
    above the bench rather than pressing into it, beside the step above that settles whether the
    model's frame is right at all;
  - the gripper on a real pen: what it reads against the band that infers holding, which the
    simulator's pen cannot say;
  - the front and wrist cameras' placement and field of view, measured, to replace the
    simulator's default mounts (`WRIST_CAMERA_POSE`);
  - the policy loop's achieved rate on the real bus, which the next item measures and the
    simulator's lockstep clock never can.

  Until then the simulator's ✅ says it does what it says, not that it moves like an arm, and it
  never raises `lerobot:real`'s row.
- ⬜ **A learned policy on the real SO-101.** `manipulate`, `pick` through a policy server and
  `--controller vla` have run against a fake arm and on the simulator, and nowhere else
  ([ADR-0048](docs/adr/0048-policies-are-the-arms-executor.md)). Nobody has handed the arm to a
  policy through quackd. Besides the tiny random ACT CI builds, one trained ACT from the Hub has
  been served, on the laptop's CPU on 2026-09-28, and drove the arm's twin on the simulator.
  SmolVLA took minutes a chunk on that CPU and wants the rented GPU, and pi05 has not run
  ([docs/policies.md](docs/guides/policies.md#smolvla-and-act)). Step 18 of
  [docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md) is the order: a run
  with `scripted:hold`, then `scripted:sweep`, then a checkpoint checked with
  `quackd policy check --bench`, each rehearsed on the arm's twin first, with a hand on the
  switch. Then the number nothing else can give: how fast the policy loop runs on the real bus
  with a server inferring on the same laptop, where torch's threads and the bus's worker share
  one CPU. It is the `hz`, `late_ticks`, `starved_ticks` and `round_trip_ms` of the `policy`
  block in that run's `summary.json`, with `extras.timing` beside it, and until somebody sends
  it back the rate a policy is served at is a claim about its training data and not about this
  arm.
- ⏸ **Any rosbridge base.** `rosbridge:ws` against a bridge. It is the one hardware backend
  with neither a lookout task nor a checklist. A coordinator flock across two machines needs a
  distributed clock first; a pilot flock needs none and has simply never been tried across two.

## Open here

- ⬜ **Nobody has run the discrete stepper against a real decision LLM.** Not TypeSafe's hosted
  API, not a `kev`, `von`, `openjev` or `opendecision` server, not `laya` in this process, and
  not on hardware in any of those shapes. `--decision-llm` ships off unless you name one and is
  exercised against a stub, so there is no agreement rate and no calibration curve, and the only
  latency figure anywhere is TypeSafe's own, on TypeSafe's own task, for Jev alone. The floors
  every one of the seven names is gated on are Jev's published numbers, inherited by the other
  six unmeasured, which is a guess wearing a number's clothes until somebody checks it.
  `--decision-mode shadow` is built to produce all three without changing a run, on
  `lerobot:mock` or on a real arm, and it is how this line stops being true. Until somebody runs
  it, the break-even in [docs/decision-llms.md](docs/guides/decision-llms/README.md) is arithmetic rather than
  a result: the break-even there is a stepper answering in under 1.40 seconds, and below that
  it is a net loss.

- ⬜ **Nobody has run the host daemon on a Jetson.** quackd no longer runs on the board. It
  reaches one from the laptop with `--host`, through `bridge/jetson/quackd_jetson_hostd.py`
  ([docs/jetson.md](docs/guides/jetson.md)). The daemon, the client, `quackd doctor --host`, the
  board's camera and its detector were all exercised in-process against fakes: a board made of
  files, a fake ultralytics and torch, and a fake of the protocol on loopback. That proves
  quackd reads the protocol as written, and says nothing about a board. Unmeasured is everything
  that makes a Jetson one: whether the CSI camera opens through its GStreamer pipeline, whether
  ultralytics on JetPack detects on CUDA, how long a snapshot and a detection take over a
  robot's Wi-Fi inside `go_to`'s steering loop, which re-sends its last twist for 0.3 s while it
  waits and no longer, what the doctor section reads off a real board's files, and the one that
  matters near a robot, whether a model server and the detector on a robot's own board leave its
  fifty hertz control loop alone, which on a humanoid is a fall if they do not. A ToddlerBot
  carries the only Jetson any of the seven bodies has, so that is where the last one gets
  answered. What to send back from a board is listed at the end of
  [docs/jetson.md](docs/guides/jetson.md#status).

- ⏸ **FLUX 3 Action has not run anywhere, and quackd does not serve it.** It needs a rented
  Linux machine with an NVIDIA GPU, about 32 GB of it in BF16 by Black Forest Labs' own report,
  LeRobot from its main branch at the commit that added it or later, which is not on PyPI, and a
  NATTEN built for that machine's torch and CUDA. The spike is to serve the official
  `flux-3-action-so101` there behind `ssh -L`, and it has to show three things before quackd
  claims the policy: that `quackd policy check --bench` holds the checkpoint's rate through the
  tunnel, that its delta actions stay anchored when quackd clips a goal, and that the checkpoint
  at a pinned revision drives the simulator end to end, on the generic arm or with the frame
  check accepted. Whatever it shows, passed or not, gets written down, and only a pass gives it
  refs of its own and a place in the server's list of policy types. Until then its page says
  what running it takes and that nobody has ([docs/policies.md](docs/guides/policies.md#flux-3-action)).
- ⬜ **The stepper only shadows `manipulate`.** A `duck: 3` task file's instructions make it a
  choice, and the stepper is offered it and never takes it, in `--decision-mode on` as in
  shadow, because under `--yes` nobody is asked at its confirm gate
  ([ADR-0048](docs/adr/0048-policies-are-the-arms-executor.md)). Every turn that offered one
  writes a `decision_shadow` record beside the model's choice. The only ones outside the test
  suite come from runs by hand on the arm's twin, on 2026-09-28 and on the released 0.16.0 on
  2026-09-29, with the trained ACT served, `gpt-6-sol` piloting and a stub decision LLM loaded
  as a plugin. A stub that always picks `manipulate` chose it on every turn that offered it and
  ended on `gate: shadow_only` each time, with the model taking the turn. No real decision LLM
  has run beside a policy, so those records hold no agreement rate. Promoting it needs one
  measured from real runs, and a decision of its own, not a floor.
- ⬜ **`load_policy()` still builds a policy beside the arm's bus.** 0.16.0 moved checkpoints
  into a policy server of their own and left this helper in the arm's `real.py`, where it
  loads a LeRobot checkpoint in the arm's own process with none of the server's reading before
  it builds and no check at connect that the policy fits. Nothing in quackd calls it and no
  command reaches it, and nothing else keeps it off the bus. Removing it takes its test, its
  `LOAD_POLICY` row and the lines in the docs that name it along, and the release that does it
  says so in its notes
  ([ADR-0048](docs/adr/0048-policies-are-the-arms-executor.md#consequences)).
- ⬜ **No `cost_usd` quackd reports has been checked against an invoice.** Every rate in
  `quackd/agent/providers/catalogue.py` was read off a vendor's pricing page by hand, most
  recently on 2026-09-23, and a rate read by hand is wrong from the day the vendor edits the
  page until somebody reads it again. What would close this is one run on a metered account,
  reconciled against that account's own billing page, on any vendor. Until then `--price` is the
  answer to a disagreement and the catalogue is a table rather than a bill. On OpenRouter a
  turn is costed at the `usage.cost` it reports instead, which nobody has yet set beside what
  OpenRouter's activity page charged for the same run.

- ⬜ **No OpenRouter model has answered a real quackd request.** `--llm openrouter` was written
  from OpenRouter's documentation and run against a stand-in on 127.0.0.1 through the real
  `openai` SDK; only its model list check has met OpenRouter itself, with no key. What would
  close this, on a key with credits: a bare `--llm openrouter`, because the default row's
  function tools through Chat Completions are the assumption that rests on the most; a Claude
  row past eight exchanges with frames; a Gemini row; an id quackd does not carry, `:free`
  and not; a bring-your-own-key turn; and each run's `cost_usd` beside the charge OpenRouter's
  activity page shows ([docs/guides/openrouter.md](docs/guides/openrouter.md)).

- ⬜ **One local model has refused tasks on feasibility grounds, one frontier pilot has refused
  one on a real arm, and nobody has watched an `uncertain` over MCP.** Qwen3-32B-AWQ on vLLM,
  driving `microduck:sim2d`, answered this gate 54 times before the check in #24 and 54 times
  after. Before: 14 `infeasible`, 26 `uncertain`, 14 `feasible`, with a noise floor around 2 per
  six run cell ([@Vallhalen](https://github.com/Vallhalen), #24, written up in
  [docs/local-llms.md](docs/guides/local-llms.md#honest-notes)). It refuses a categorical `cannot`
  reliably, it hedged on one, and the figure nobody published was the hole the check now covers:
  on the eight tasks the check never fired on, the verdicts moved by 1 to 2 either way, which is
  that floor rather than a result. That is one model, one quantisation, one simulated body and
  three repeats a cell. On the SO-101 on 2026-09-23, with `gpt-6-sol` and `gpt-6-astra`
  piloting, twelve runs stopped at the y/N question an `uncertain` asks, and one pilot a person
  had just said go to assessed the same doubt again as `infeasible` and ended its run. The gate
  and the prompt changed after that afternoon ([CHANGELOG.md](CHANGELOG.md)), and nobody has yet
  watched a frontier pilot answer the new ones on an arm. Still open: whether a frontier model
  uses `uncertain` when it should or reaches for `infeasible` too readily, which the `live_llm`
  tests measure and which needs a key; a pilot that never names the figure its plan hinges on,
  which the check reads nothing about and cannot catch; and `uncertain` over MCP, where the
  verdict stays pending and the model is told to ask the person it is chatting with, which no
  real session has been watched doing.
- ⬜ **Whether the words of `assess_task` fixed #25.** `--goal "Find the ball and kick it."`
  stopped at this gate 5 times in 6 on that same model where the shipped `find-and-kick` file
  passed 6 of 6, and the allowlist width is the measured cause. The description it read has
  been corrected rather than the allowlist narrowed, and nobody here runs that model, so the
  six cells want running again on a build that carries the change (#25).
- ⏸ **A flock role can ask for a body, and no coordinator flock can have two different ones.**
  `flock.roles.<role>.needs` validates, and the coordinator matches it against the datasheet a
  bid carries, tested at that level. But `flock/runner.py` still knows only the Microduck, so
  nothing quackd can start exercises the matching end to end. It waits on the same work as the
  rest of heterogeneous coordinator flocks, including the latent bug
  [ADR-0020](docs/adr/0020-heterogeneous-flocks.md) records: the coordinator judges eligibility
  before members report their vocabulary, and `needs` inherits that. A **pilot** flock does put
  two different bodies on one task, and it uses no roles: each pilot reads its own datasheet and
  its peers', and they divide the work by talking ([ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).
- ⏸ **No pilot flock has been driven by a real model, or by a real robot.** `flock-hello` runs
  on `mock` and `sim2d` bodies with the scripted rule, which cannot reason about a datasheet, so
  what a frontier model does with the `Your flock` section and `tell` is unknown. Needs a key,
  and then two robots.
- ⏸ **N simulated pilots are N separate worlds.** Two `microduck:sim2d` members of a pilot flock
  cannot see each other, so nothing checks a claimed success against ground truth the way the
  coordinator's arena does. A shared arena for pilots is unbuilt.
- ⏸ **Nobody has asked a real bridge what its robot is.** `rosbridge:ws` reads the topic list
  and the URDF at connect, from the parameter and from the latched topic, and every name is
  VERIFIED at a pin. All of it is exercised with fake services and fake topics. What a real
  bridge does, whether `rosapi` is running, whether the description is where its defaults
  expect, and what a real robot's inertials add up to, is unknown until somebody points it at
  one.

- 🔨 **Somebody has to drive `web/` in a browser, and record it.** The page itself is no longer
  unopened: it booted clean twice on the machine that wrote it (`8d72a2a`, `a9fea18`), with the
  fonts and the mark loaded and a held `W` walking the duck. What that leaves is everything past
  the boot — a full model-driven run, a key barging in out of one, the Record button, the switch
  thrown mid-run, and any browser or machine but that one. None of it was recorded, so there is
  no asset and nothing anybody else can check. Separately, the four measured claims in
  `web/README.md` come from a scratch harness that is not in the repository, and both files it
  measured have changed since, in the abort path and in the arena's geometry, so nothing here can
  re-run it. Locally it is `python web/serve.py`, then <http://localhost:8000/simulator/>.
- ⬜ The coordinator flock does not know `open_duck` yet (`flock/runner.py` knows one adapter).
  A pilot flock knows all seven. A hardware flock of either kind waits on robots shipping.
- ⏸ **A real model recording**, in either simulator, to replace a scripted-pilot asset and drop
  the label (see [docs/assets](docs/assets/README.md)). Needs a key.
- ⬜ **The browser demo is not at parity with the backend.** Seven of the manifest's fifteen
  verbs and none of the three composites, a contract of its own, an arena that is not upstream's
  scene, geometric perception, no hash check on anything it fetches, a seed that means the same
  distributions and not the same layout, and no scripted pilot. It also has no datasheet and no
  feasibility gate: `web/src/pilot.js` is a second loop with its own hardcoded prompt, so a page
  asked to carry something will try. `web/README.md` holds that list in full and is the one place
  it is kept; this bullet is the reminder that it is a list of open gaps and not just a
  disclosure.
- ⬜ **`GAIT_FLOOR_VY` was never measured.** The forward and turning floors were; the sideways
  one is assumed equal to the training maximum, so every lateral request is sent at full
  scale. The assumption is in `GAIT_THRESHOLD`'s note and in the state's `assumptions`, and
  the fix is the same script that produced the other two.
- 🔨 **A transcript from a live local server quackd has not seen yet** (Ollama,
  llama.cpp). Still none on the dev machine, and that has not changed. LM Studio was covered
  by #7 and vLLM by #23, which also closed the other half of this item: its two Qwen3-32B-AWQ
  runs are a chain, the note the first one saved is in the second one's `system_prompt`
  verbatim, and the memory counters move by exactly one note and one episode, so nothing ran
  between them. Both ends are in `docs/assets/transcripts/`, read in
  [docs/local-llms.md](docs/guides/local-llms.md). What is still open is Ollama, llama.cpp, a run on
  this machine, and any task harder than the starter duck.
- ✅ **Both nightly jobs are green, and 0.10.0 is what made them so.** Neither had ever
  passed a scheduled run: `microduck assets` red since 2026-09-09, `toddlerbot contract` since
  2026-09-07. Four failures, and every one of them was the job telling the truth.
  The gait sweep went 9 of 10 because the physics backend raises a small twist to a gait floor
  measured at 0.22 on MuJoCo 3.12, and the lock resolves 3.13, where 0.22 walks the duck
  thirteen millimetres in ten seconds. A ball dead ahead was unreachable. The floor is 0.23,
  re-measured across all ten seeds, and the achieved fraction with it is 0.38 where it was 0.42.
  The fall-recovery test asserted a heading of π on a pose that is exactly the gimbal
  singularity, so both arguments to the yaw's `atan2` are zero and the sign is the last bit of a
  subtraction: π here, 0.0 on the runner. It asserts the degeneracy itself now. `stand` never
  finished on the ToddlerBot because the slew advanced from the measured pose each tick, which
  pins the position error at one step and starves a position-controlled motor of torque; it
  advances from the last commanded target now and completes in the 5.2 seconds its own
  arithmetic predicts. And the deadman test asked for the daemon's health over the very socket
  it had just killed on purpose.
  The record is worth stating as it is: both jobs went green on a manual run on 2026-09-17,
  the day the fixes landed, and every scheduled run since, seven each from 2026-09-18 to
  2026-09-24, has been green for both.

- ✅ **Exercise `remember` against a cloud model.** `gpt-6-astra` called it in seven of its
  twelve runs on the arm on 2026-09-15, for five distinct notes, and the last run of that
  afternoon read all five back out of its own prompt. Still open in a simulator, where the scripted pilot has no
  script for it, so `--llm fake` writes episodes and never a note.
- ⏸ **No asset shows a flock of pilots.** `flock.gif` is the coordinator: three identical ducks
  auctioning a kick. The kind of flock the README now leads with is two different bodies talking,
  and it has no recording, because a pilot flock writes no GIF (N members are N worlds).

## Open elsewhere

Things no commit in this repository can finish.

- ✅ **The GitHub About text and Topics carry the one-liner**
  ([ADR-0035](docs/adr/0035-one-cli-for-all-your-robots.md)), set by hand because there is no
  commit that can set them. Topics are at the cap of 20, so the next one has to replace one.
- ⏸ **The landing page.** <https://www.quackd.org/> is built from quackd-web, a separate
  repository, and it was written around 0.5: quackd was a brain for one small robot, there were
  five adapters, and one of the five was the Reachy Mini, which this project removed in 0.9. Its
  copy is being corrected there. The part no commit in either repository can fix is
  `public/og.png`, the card a social network shows for quackd.org. It is a designed asset with
  no generator, and its headline is set in a display weight of Nunito Sans that Google now
  serves only as a variable font, so it needs whoever made it rather than a script.
- ✅ **`web/` is on the web.** <https://www.quackd.org/simulator> answers. That address belongs to
  quackd-web, a separate Vercel project serving the landing page, and its build now fetches this
  directory into its own `/simulator` at a pinned commit — so a change here reaches the page on
  that project's next deploy, and `/simulator/source.json` records which commit the live copy came
  from. The landing page points at the demo from five places; the demo's header points back.

## Release checklist

Moved to [RELEASING.md](RELEASING.md), beside the rules for which release is which.
