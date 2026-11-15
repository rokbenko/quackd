# PLAN.md — Roboglot

What is still open. Everything that has shipped is in [CHANGELOG.md](CHANGELOG.md), the
[ADRs](docs/adr/) and the git history, which record it better than a task list can.

Legend: 🔨 in progress · ⬜ todo · ⏸ blocked (with reason)

## Only a human can

Nine bring-ups still need hardware Roboglot has never touched, one per body that has not met
any: the other nine of the ten. The tenth happened, an SO-101 arm running
`lerobot:real` on 2026-09-15, which flipped its row in
[`docs/adapter-status.md`](docs/adapters/status.md), and the same arm ran again on 2026-09-23,
registered as `arm-01`. Each of the nine ends the same way: flip that backend's row, and not
before. The industrial arm's starts earlier, with an owner and a date for `rosarm:ws`, which
only Rok can name, and a CI job against a real controller in a container, which can come first:
the job that runs now, `rosarm contract`, meets a real rosbridge and a stand-in for the driver.

- ⏸ **An Open Duck Mini v2**, the most reachable of the eight because you can build it. Run
  `open_duck:bridge` against a duck you built, work through
  [docs/open-duck-hardware-checklist.md](docs/adapters/open_duck/hardware-checklist.md), and confirm
  the deadman by pulling Wi-Fi mid-walk. Then the five numbers at the end of that checklist:
  boot time against the watchdog budget, camd's peak memory against its cap, the observed
  loop-rate floor, the camera's field of view against a tape measure, and the accelerometer
  upright versus on its side. The last one is what would give this robot fall detection, and
  Roboglot deliberately does not guess it, because a wrong fall detector fails as a confident
  "not fallen".
- ⏸ **A Microduck.** Run `--robot microduck:jsonrpc` against a real `robotd` and work through
  [docs/microduck-hardware-checklist.md](docs/adapters/microduck/hardware-checklist.md).
  The path is built and audited:
  read at daemon-v0.16.1 on API v41, a `robotd` on any version from API v23 up served with a
  warning when it is not v41 (the handshake used to refuse every release from daemon-v0.12.0 on),
  posture failing closed on every label but `walk`, `stand` and `sit`, state actually subscribed
  to, and frames from `mediad`'s `GET /frame` or its WebRTC track, since `robotd`'s socket
  carries none. Upstream shipped daemon-v0.16.2 to daemon-v0.17.6 by 2026-10-17, and 0.18.1 read
  what they changed: nothing Roboglot sends or reads changed shape, from daemon-v0.16.2 `robotd` again
  goes limp when it predicts a fall, and from daemon-v0.17.3 it reports API v44, which Roboglot serves
  with a warning. 0.20.0's `watch` has never turned a real duck's head: there it needs `mediad`'s
  `/frame`, since the WebRTC track says nothing of when a frame was taken, and the numbers its head
  is held to are starting values to measure on the duck. 0.21.0's `stand_up` waits up to ten
  seconds for the duck to stand, and how long the policy takes to get a real duck up is the number
  to measure against it. Re-read `robotd` at the release the duck arrives on before its first walk.
  0.27.0's `roboglot drive` has never driven a real duck: on one, the
  milliseconds from a typed line to `robotd` taking its twist, an empty Enter stopping a walk,
  and how far the preset walks and the 45 degree turn really carry it are the numbers to bring
  back. 0.28.0 reads the duck's contact odometry as where it is, so `mark_place` and `go_to_place`
  are offered on it, and reaches `mapd` at `--map-address` once a robotd release carries it
  (UNVERIFIED): how close `go_to_place` brings the duck back to a place marked by odometry is the
  number to bring back, and the checklist says how to read `mapd` off the robot and whether a head
  turn alone is a keyframe. Pre-orders opened 2026-08-27, earliest arrivals estimated around
  Christmas 2026 and later orders four to six months out.
- ⏸ **A ToddlerBot** on its safety stand, running `bridge/toddlerbot/roboglot_toddlerbot_bridge.py`
  through [docs/toddlerbot-hardware-checklist.md](docs/adapters/toddlerbot/hardware-checklist.md). What
  most needs a real robot: whether the safe-pose slew is safe from a crawl, what tilt really
  means fallen, whether the neck axes are what the motor names imply, and whether a calibrated
  zero survives a restart.
- ⏸ **An XLeRobot.** Start the host (it is commented out of upstream's own package `__init__`
  and exits after an hour) and point `xlerobot-lookout` at it. What most needs a real cart: the
  camera colour order, whether `+x` is really forward, and whether the head motors are what
  upstream's agent library implies.
- ⏸ **An AlohaMini.** Start `bridge/alohamini/roboglot_alohamini_host.py` rather than upstream's
  host and point `alohamini-lookout` at it. What most needs a real robot: whether `+x` is
  physically forward, the camera colour order, how fast the lift travels in mm/s, and whether
  the wrapper really does leave the arms holding.
- ⏸ **A Huebot**, a kit every order of which its maker's page says ships before Christmas. Its
  own API is closed, and Roboglot speaks the Microduck's protocol to a shim its maker is writing
  for it, which nobody has seen. Once the shim answers `hello`, run `huebot:jsonrpc` through
  [docs/adapters/huebot/hardware-checklist.md](docs/adapters/huebot/hardware-checklist.md), whose
  first steps test the deadman and `robot.stop` with the feet off the ground. What most needs a
  real robot: whether the shim answers what
  [its page](docs/adapters/huebot/README.md#what-huebots-shim-must-answer) asks, the joint order
  and the sign of the head yaw, which skills it lists, and how Roboglot gets a camera frame, which
  is the maker's to say. `gaze` and `watch` wait for `robot.look` on Huebot's side.
- ⏸ **A MOSS rover**, which anyone can print and build from its bill of materials today, and
  whose kits its maker says are coming soon. Run `moss:cockpit` through
  [docs/adapters/moss/hardware-checklist.md](docs/adapters/moss/hardware-checklist.md), which
  starts on blocks as the maker asks. What most needs a real rover: how far one pulse of a turn
  on the spot turns it, whether the stamp on a nose frame is on the rover's clock, whether the
  litter mission's own stop ends it when Roboglot's bridge sends it, and whether the arm holds
  after a cancel.
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
- ⬜ **The SO-101 again, for 0.14.0 to 0.16.1, 0.19.0, 0.24.0 and 0.25.0.** The arm last
  ran a build of the code that became Roboglot 0.13.0, still numbered 0.12.0. The rest pose clipped
  into the travel, `roboglot robot release`, the Enter offer at the end of a run whose rest move
  missed, the `--by-hand` refusal over a joint past its travel, the paced `move_joints` and the
  connect retries have run only against a fake arm, `lerobot:mock` and the test suite, and what
  0.15.0 changes in `lerobot:real`, the follower that keeps torque on a disconnect Roboglot did
  not ask for among it, has never met the arm either. What 0.16.0 changes there has not met it
  either, and most of it is the policy segment the learned-policy item below takes to the arm.
  Nor has what 0.16.1 changes there: the connect refusing a policy server whose
  declared latency no chunk can carry, the tick a segment asks for its next chunk on, the tick
  its time runs out on, and the travel row of `roboglot doctor`. The seven bench steps that would
  settle 0.14.0's, in order, are under *Known limitations* in [CHANGELOG.md](CHANGELOG.md) for
  0.14.0, and the one for 0.15.0's follower, a second Ctrl-C during the fold back to the rest
  pose, is under 0.15.0's. The six other changes that release made in `lerobot:real` have bench
  steps now, and none has run: a connect Roboglot refuses letting go of the arm (step 6 of
  [docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md)), the deadline a call
  spends only while the bus is busy (steps 9 and 13), a connect that fails once the arm is
  energised keeping its torque (step 13), the close reading its flag back and saying a stalled
  shortfall once (step 14), and the heartbeat's reads kept out of the trace a grasp is judged by
  (step 17). That checklist is the order to take the arm through, with a hand on the switch. Nothing
  0.19.0 asks of the arm in a played task file has run on it, and the bench steps that would settle
  that, with what to report from each, are under 0.19.0's *Known limitations* in
  [CHANGELOG.md](CHANGELOG.md). In 0.24.0 `roboglot robot release` folds the arm before it lets
  go, and a fold that misses there or at the end of a run asks twice before anything drops
  ([ADR-0058](docs/adr/0058-letting-go-of-the-arm-means-putting-it-down-first.md)), a real arm
  with no rest pose is kept powered at its close, and `doctor` counts the hands away before its
  fold; none of it has run on the arm, and its nine bench steps are under 0.24.0's
  *Known limitations*. In 0.25.0 a segment asks a policy server for its next chunk while the arm
  still plays the last, whatever latency the server declares, and `--no-async-chunks` plays each
  chunk out first; neither has driven the arm, which the learned-policy item below would settle.
  An arm of Rok's own arrived on 2026-10-30, and `e166` to `e181` in
  [docs/examples/lerobot](docs/examples/lerobot/README.md) put these bench steps on the run sheet.
  The gripper opening all the way at the end of a `--by-hand` run, the count after it, and `q` with
  no Enter behind it are owed by the arm as well, and so, for 0.30.2, is a press again and again
  through the end of such a run, and one during the fold it starts with, which should stop it there.
  `e208` to `e219` put those, and what 0.30.3 changed, on the run sheet. In 0.31.3 a run that loses
  the arm ends on one line and not in a traceback, which `e166` would show on the arm, and a gripper
  that an arm gone silent did not open says to cut its power first. In 0.33.0 `lerobot:real` builds
  its follower from the robot type a registered arm carries, from the class 0.32.0 used for an
  SO-101, and refuses an arm whose bus names other joints than the six before anything is energised.
  Both have run only against a fake arm, and the first connect on 0.33.0 shows whether the SO-101
  still builds. In 0.34.0 it builds over LeRobot 0.7.0's bus and reads its encoder ticks, its heat
  limit and its motor table from its type, and none of that has met the arm either: the item on
  LeRobot 0.7.0 below says what it owes.
- ⬜ **A learned policy on the real SO-101.** `manipulate`, `pick` through a policy server and
  `--controller vla` have run against stand-ins for the arm, and nowhere else
  ([ADR-0048](docs/adr/0048-policies-are-the-arms-executor.md)). Nobody has handed the arm to a
  policy through Roboglot. Besides the tiny random ACT and X-VLA CI builds, one trained ACT from
  the Hub has been served, on the laptop's CPU on 2026-09-28. SmolVLA took minutes a chunk on
  that CPU and wants the rented GPU, and pi05, pi0, GR00T N1.7 and a trained X-VLA have not run,
  on a GPU or anywhere else
  ([docs/policies.md](docs/guides/policies.md#smolvla-and-act)). Step 18 of
  [docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md) is the order: a run
  with `scripted:hold`, then `scripted:sweep`, then a checkpoint checked with
  `roboglot policy check --bench`, with a hand on the switch. Then the number nothing else can
  give: how fast the policy loop runs on the real bus with a server inferring on the same
  laptop, where torch's threads and the bus's worker share one CPU. It is the `hz`,
  `late_ticks`, `starved_ticks` and `round_trip_ms` of the `policy` block in that run's
  `summary.json`, with `extras.timing` beside it, and until somebody sends it back the rate a
  policy is served at is a claim about its training data and not about this arm. Since 0.25.0
  that block's `async_chunks` says which way the run played its chunks, and the checkpoint
  served with no `--latency-s`, run once as it is and once with `--no-async-chunks`, says what
  0.25.0's change is worth on the arm.
- ⬜ **A first real Hugging Face Endpoint.** `roboglot policy endpoint` and `--policy-url hf:NAME`
  have run against the test suite's stand-ins on 127.0.0.1 alone, and no endpoint has been
  created or paid for ([ADR-0061](docs/adr/0061-a-hugging-face-endpoint-is-a-rented-gpu-roboglot-starts.md)).
  The first one should be an ACT, which needs neither a gated tokenizer nor much of a GPU, on an
  `nvidia-t4` at $0.50 an hour, with `--idle-minutes 15`, and paused as soon as it has answered,
  which costs a few cents. It settles what Hugging Face's pages leave out, the UNVERIFIED rows
  of [the endpoint's table](docs/adapters/lerobot/README.md#the-endpoints-upstream-hugging-face-inference-endpoints):
  whether the proxy passes `Authorization` on to the container, which the server's own log says
  once if it does; whether an endpoint pulls uv's image from ghcr.io, or needs the same image
  from Docker Hub; whether the proxy answers 503 or 502 while a replica starts, and what it
  answers once the endpoint is paused; whether the endpoint keeps its address through that pause
  and the resume after it, which `up` and a run hold it to; and whether torch finds the GPU. Then
  `roboglot policy check --policy-url hf:NAME --bench`, from the laptop, for the first round trip
  through an endpoint anybody has timed, and the cold start a wake from zero takes. A second
  account of yours then settles whether the API keeps one account's endpoints from another's
  token, which the refusal of a planted record rests on.
- ⏸ **Any rosbridge base.** `rosbridge:ws` against a bridge. It is the one hardware backend
  with neither a lookout task nor a checklist. A team needs no distributed clock to span two
  machines, and has simply never been tried on two.
- ⬜ **Robot Passport on two Jetsons with Bluetooth.** 0.29.0's `roboglot onboard` has run on the
  `sim` board alone, two simulated robots on one laptop, and nothing of it on a Jetson, over a
  real Bluetooth radio or between two machines
  ([docs/passport/roadmap.md](docs/passport/roadmap.md)). On two boards, each an Orin or a Thor
  with an adapter BlueZ can drive: the three commands on each, the wizard's `robot.yaml`, the
  passphrase, the unit, and a reboot that brings the service back. Then carry one toward the
  other and write down the signal strength at which they meet, once on one Wi-Fi and once with
  it off, so the handshake goes over the LAN once and over the Bluetooth link once. On the way:
  a stranger's words kept from the pilot, a pin with `roboglot onboard pin` and free talk after
  it, an edit to `robot.yaml` served as last signed until `roboglot onboard sign`, the beacon id
  rotating, and whether BlueZ's address privacy took effect. Bring back both services' journals,
  the transcripts, `roboglot onboard status --json` from each, a `tegrastats` line while they
  talk, and the signal strength at 1, 3 and 10 m. Each part flips its row in the roadmap's
  status table, and not before.
- ⬜ **`--board generic` on a computer that is not a Jetson.** It is written from the kernel's
  documentation and has run on nothing. On a Qualcomm, Rockchip or x86 computer a robot carries:
  the three commands with `--board generic`, the unit, and a reboot that brings the service back.
  Bring back what the computer calls itself, `/proc/device-tree/model`, `compatible` and
  `chassis-type`, or `/sys/class/dmi/id/` without its serials, and `/etc/os-release`, `bluetoothctl
  --version` and `roboglot onboard preflight --json`. On an NVIDIA IGX, also whether
  `/etc/nv_tegra_release` is there: with it an IGX reads as a Jetson today, and without it, it is
  refused as a Thor. A board that has run may then find itself, one at a time, through the
  `roboglot.boards` plugin interface.
- ⬜ **The page `roboglot ui` on a phone.** Its phone path has been seen only in a headless browser at
  a phone's width, against the page on loopback. On one Wi-Fi network: `roboglot ui --bind
  0.0.0.0`, the firewall's question on Windows, the printed QR code scanned, a goal and a chat
  line, the floating Stop, the screen locked mid-run and the run found again after, a home-screen
  shortcut that still works after the page restarts, and a wrong token that shows nothing but the
  refusal. Then the same through Tailscale, bound to its address. The phone steps in
  [docs/guides/web-ui.md](docs/guides/web-ui.md) were written from the code, and change with what
  this run finds. Closing the terminal window that started the page is untried too: killing its
  process, as `roboglot.exe` and as `python`, left a run waiting on a question to stop and close its
  record, and a window closed by hand is the case left.

## Open here

- ⬜ **Robot Passport after 0.29.0.** A pilot that asks a robot it met for a courtesy, or shares
  what it saw. Maker signatures beside the owner's. Revocation, so a stolen robot's key stops
  speaking before its passport expires. Guests, which need a decision record of their own first.
  Talk that is encrypted as well as signed. And the signal strength at one metre measured on real
  adapters, so `rssi_at_1m` and the 2 m a robot meets at stop being starting values. Phase 7 is
  dropped ([ADR-0073](docs/adr/0073-a-robot-carries-its-own-passport-and-its-owner-signs-it.md)), and
  its note, [docs/design/robot-passport-phase-7.md](docs/design/robot-passport-phase-7.md), is
  kept as the record of what was planned.
- ⬜ **Nobody has run the discrete stepper against a real decision LLM.** Not TypeSafe's hosted
  API, not OpenAI's Decisions API, not a `kev`, `openjev` or `opendecision` server, not
  `laya` in this process, and not on hardware in any of those shapes. `--decision-llm` ships off
  unless you name one and is exercised against stubs and fakes, so there is no agreement rate and
  no calibration curve, and the only latency figure anywhere is TypeSafe's own, on TypeSafe's own
  task, for Jev alone. The floors every one of the eight names is gated on were shaped around
  Jev, two of them TypeSafe's published numbers and two Roboglot's own, and the other seven inherit
  all four unmeasured, which is a guess wearing a number's clothes until somebody checks it.
  `--decision-mode shadow` is built to produce all three without changing a run, on
  `lerobot:mock` or on a real arm, and it is how this line stops being true. Until somebody runs
  it, the speed and cost figures in
  [the decision-LLM hub](docs/guides/decision-llms/README.md#how-much-faster-and-how-much-cheaper)
  are arithmetic rather than a result. Nor has a frame gone to OpenAI: 0.20.0's `--decision-frames`
  and `watch` have run against stand-ins alone, so what a frame costs on `gpt-6-luna` and how much
  slower it makes a turn are unmeasured too. 0.27.0's `roboglot drive` asks the same decision LLM
  which call a typed line means, held to the stepper's floors, and only stand-ins have answered
  it: a session's `fast` records, with each person's yes or no to a `did you mean` in the
  `routed` row after, are what those floors are measured by once somebody drives with one. A
  pipe's lines, which nobody is asked about, are written declined too, and only their `command`
  row tells them apart.

- ⬜ **Nobody has run the host daemon on a Jetson.** Every Roboglot command but `roboglot onboard`
  stays off the board, and reaches one from the laptop with `--host`, through
  `bridge/jetson/roboglot_jetson_hostd.py` ([docs/jetson.md](docs/guides/jetson.md)). The daemon,
  the client, `roboglot doctor --host`, the board's camera and its detector were all exercised
  in-process against fakes: a board made of files, a fake ultralytics and torch, and a fake of
  the protocol on loopback. That proves Roboglot reads the protocol as written, and says nothing
  about a board. Unmeasured is everything that makes a Jetson one: whether the CSI camera opens
  through its GStreamer pipeline, whether ultralytics on JetPack detects on CUDA, how long a
  snapshot and a detection take over a robot's Wi-Fi inside `go_to`'s steering loop, which
  re-sends its last twist for 0.3 s while it waits and no longer, what the doctor section reads
  off a real board's files, and the one that matters near a robot, whether an LLM server and
  the detector on a robot's own board leave its fifty hertz control loop alone, which on a
  humanoid is a fall if they do not. A ToddlerBot carries a Jetson, and so does a MOSS rover
  built on its Jetson option, so that is where the last one gets answered. What to send back
  from a board is listed at the end of [docs/jetson.md](docs/guides/jetson.md#status).
  `roboglot onboard`, the one part of Roboglot that does run on a robot's own Jetson, is the item on
  two Jetsons with Bluetooth under Only a human can.

- ⏸ **FLUX 3 Action has not run anywhere, and Roboglot does not serve it.** It needs a rented
  Linux machine with an NVIDIA GPU, about 32 GB of it in BF16 by Black Forest Labs' own report,
  LeRobot 0.7.0 or later, the first release with it, and a NATTEN built for that machine's torch
  and CUDA, which had no wheel for the torch 2.15 LeRobot 0.7.0 installs. The spike is to serve
  the official `flux-3-action-so101` there behind `ssh -L`, and it has to show two things
  before Roboglot claims the policy: that `roboglot policy check --bench` holds the checkpoint's rate
  through the tunnel, and that its delta actions stay anchored when Roboglot clips a goal.
  Whatever it shows, passed or not, gets written down, and only a pass gives it refs of its own
  and a place in the server's list of policy types. Until then its page says what running it
  takes and that nobody has ([docs/policies.md](docs/guides/policies.md#flux-3-action)).
- ⏸ **Pi0-FAST is refused, and stays refused while LeRobot loads its tokenizer's own code.**
  Its policy loads the FAST action tokenizer with `trust_remote_code=True`, hard-wired in its
  constructor at LeRobot 0.7.0, so serving one would run the Python its tokenizer's repository
  brings with it, and the server refuses the type on its `config.json`
  ([docs/policies.md](docs/guides/policies.md#pi0-and-why-not-pi0-fast)). What would change that
  is a LeRobot release that loads the tokenizer with code transformers already has, read at its
  pin like every other type before it is served.
- ⏸ **MolmoAct2 is deferred.** Its base model maps its classes to code of its own
  (`auto_map`), which the policy server refuses in every model it loads, so serving it would take
  the first exemption from that rule
  ([ADR-0060](docs/adr/0060-hugging-face-serves-many-vlas.md)). What would change that is
  fine-tunes that need no code of their own and no frame transforms, or a decision to read its
  code and exempt it, either read at LeRobot's pin like every other type before it is served.
- ⬜ **The stepper only shadows `manipulate`.** A `glot: 3` task file's instructions make it a
  choice, and the stepper is offered it and never takes it, in `--decision-mode on` as in
  shadow, because under `--yes` nobody is asked at its confirm gate
  ([ADR-0048](docs/adr/0048-policies-are-the-arms-executor.md)). Every turn that offered one
  writes a `decision_shadow` record beside the pilot LLM's choice. No real decision LLM has run
  beside a policy, so no record holds an agreement rate yet. Promoting it needs one measured from
  real runs, and a decision of its own, not a floor.
- ⬜ **No `cost_usd` Roboglot reports has been checked against an invoice.** Every rate in
  `roboglot/agent/providers/catalogue.py` was read off a vendor's pricing page by hand, every one of
  them again on 2026-11-14, and a rate read by hand is wrong from the day the vendor edits the page
  until somebody reads it again. What would close this is one run on a metered account, reconciled
  against that account's own billing page, on any vendor. Until then `--price` is the answer to a
  disagreement and the catalogue is a table rather than a bill. Since 0.27.0 a Claude run's requests
  are marked for Anthropic's cache, so its `cost_usd` rests on the cache's read and write rates as
  well. On OpenRouter a turn is costed at the `usage.cost` it reports instead, which nobody has yet
  set beside what OpenRouter's activity page charged for the same run.

- ⬜ **No OpenRouter LLM has answered a real Roboglot request.** `--llm openrouter` was written
  from OpenRouter's documentation and run against a stand-in on 127.0.0.1 through the real
  `openai` SDK; only its LLM list check has met OpenRouter itself, with no key. What would
  close this, on a key with credits: a bare `--llm openrouter`, because the default row's
  function tools through Chat Completions are the assumption that rests on the most; a Claude
  row past eight exchanges with frames; a Gemini row; an id Roboglot does not carry, `:free`
  and not; a bring-your-own-key turn; and each run's `cost_usd` beside the charge OpenRouter's
  activity page shows ([docs/guides/openrouter.md](docs/guides/openrouter.md)).

- ⬜ **One local LLM has refused tasks on feasibility grounds, one frontier pilot has refused
  one on a real arm, and nobody has watched an `uncertain` over MCP.** Qwen3-32B-AWQ on vLLM,
  driving `microduck:sim2d`, answered this gate 54 times before the check in #24 and 54 times
  after. Before: 14 `infeasible`, 26 `uncertain`, 14 `feasible`, with a noise floor around 2 per
  six run cell ([@Vallhalen](https://github.com/Vallhalen), #24, written up in
  [docs/local-llms.md](docs/guides/local-llms.md#honest-notes)). It refuses a categorical `cannot`
  reliably, it hedged on one, and the figure nobody published was the hole the check now covers:
  on the eight tasks the check never fired on, the verdicts moved by 1 to 2 either way, which is
  that floor rather than a result. That is one LLM, one quantisation, one simulated body and
  three repeats a cell. On the SO-101 on 2026-09-23, with `gpt-6-sol` and `gpt-6-astra`
  piloting, twelve runs stopped at the y/N question an `uncertain` asks, and one pilot a person
  had just said go to assessed the same doubt again as `infeasible` and ended its run. The gate
  and the prompt changed after that afternoon ([CHANGELOG.md](CHANGELOG.md)), and nobody has yet
  watched a frontier pilot answer the new ones on an arm. Still open: whether a frontier LLM
  uses `uncertain` when it should or reaches for `infeasible` too readily, which the `live_llm`
  tests measure and which needs a key; a pilot that never names the figure its plan hinges on,
  which the check reads nothing about and cannot catch; and `uncertain` over MCP, where the
  verdict stays pending and the LLM is told to ask the person it is chatting with, which no
  real session has been watched doing.
- ⬜ **Whether the words of `assess_task` fixed #25.** `--goal "Find the ball and kick it."`
  stopped at this gate 5 times in 6 on that same LLM where the shipped `find-and-kick` file
  passed 6 of 6, and the allowlist width is the measured cause. The description it read has
  been corrected rather than the allowlist narrowed, and nobody here runs that LLM, so the
  six cells want running again on a build that carries the change (#25).
- ⏸ **No pilot team has been driven by a real LLM, or by a real robot.** `team-hello` runs
  on `mock` bodies with the scripted rule, which cannot reason about a datasheet, so
  what a frontier LLM does with the `Your team` section and `tell` is unknown. Needs a key,
  and then two robots.
- ⏸ **Nobody has asked a real bridge what its robot is.** `rosbridge:ws` reads the topic list
  and the URDF at connect, from the parameter and from the latched topic, and every name is
  VERIFIED at a pin. All of it is exercised with fake services and fake topics. What a real
  bridge does, whether `rosapi` is running, whether the description is where its defaults
  expect, and what a real robot's inertials add up to, is unknown until somebody points it at
  one.

- 🔨 **A transcript from a live local server Roboglot has not seen yet** (Ollama,
  llama.cpp). Still none on the dev machine, and that has not changed. LM Studio was covered
  by #7 and vLLM by #23, which also closed the other half of this item: its two Qwen3-32B-AWQ
  runs are a chain, the note the first one saved is in the second one's `system_prompt`
  verbatim, and the memory counters move by exactly one note and one episode, so nothing ran
  between them. What is still open is Ollama, llama.cpp, a run on this machine, and any task
  harder than the starter task.

- ✅ **Exercise `remember` against a cloud LLM.** `gpt-6-astra` called it in seven of its
  twelve runs on the arm on 2026-09-15, for five distinct notes, and the last run of that
  afternoon read all five back out of its own prompt. Still open for the scripted pilot, which has
  no script for it, so `--llm fake` writes episodes and never a note.
- ⬜ **No asset shows a team of pilots.** The kind of team the README now leads with is two
  different bodies talking, and nothing in [docs/assets](docs/assets/README.md) shows one.
- ⬜ **What the page `roboglot ui` does not do yet.** A chat flies its goals with what a goal takes
  since `roboglot drive` took run's flags for them (ADR-0072), all but a picture, which drive
  does not take. Left out on purpose until somebody needs them: `--by-hand` and the end-of-run
  questions about an arm, which wait for somebody at the arm; teams; a picture with a chat line;
  a lock on a robot across processes, so a run started from a terminal beside the page is not
  seen; HTTPS on a LAN, which Streamlit can serve through its own settings; and an app to install
  on a phone. The JSON front end stays a hidden flag until a second program needs it.
- ⬜ **A VLA pilot in a drive session, on the arm.** `roboglot drive --controller vla` has flown its
  lines on the LeRobot mock's scripted policy alone. Owed: a session on the arm with
  `roboglot policy serve --policy scripted:sweep`, three lines with a brake mid-segment, from the
  keyboard and from the page, then the same with a trained checkpoint.
- ⬜ **LeRobot 0.7.0 on the arm.** `roboglot-lerobot`'s extras take LeRobot 0.7.0 and nothing past it,
  below 0.7.1, with rustypot below 1.14, the motor bus 0.7.0 puts under one `SerialMotorsBus` (its
  #4672). Shipped in 0.34.0. Both `upstream_api.py` files were read at the `v0.7.0` tag, `doctor`
  has a rustypot row, `real.py` names the joints in 0.7.0's words, Dependabot's ignore moved
  with the ceiling, `override-dependencies` is gone and the core's Pillow floor is 13, so
  `roboglot[lerobot]` is off Pillow 12.3.0. Owed: the arm, which has only ever run 0.6.1,
  through the steps CHANGELOG.md's Known limitations lists, with a release over silent motors
  timed against `TORQUE_CALL_S`. The lock moved past the last five Dependabot alerts in the same
  release, to torch 2.15.0, setuptools 84.2.1 and transformers 5.22.2, on Rok's word.

## Open elsewhere

Things no commit in this repository can finish.

- ✅ **The GitHub About text and Topics carry the one-liner**
  ([ADR-0035](docs/adr/0035-one-cli-for-all-your-robots.md)), set by hand because there is no
  commit that can set them. Topics are at the cap of 20, so the next one has to replace one.
- ⏸ **The landing page.** <https://www.roboglot.org/> is built from roboglot-web, a separate
  repository, and it was written around 0.5: Roboglot was a brain for one small robot, there were
  five adapters, and one of the five was the Reachy Mini, which this project removed in 0.9. Its
  copy is being corrected there. The part no commit in either repository can fix is
  `public/og.png`, the card a social network shows for roboglot.org. It is a designed asset with
  no generator, and its headline is set in a display weight of Nunito Sans that Google now
  serves only as a variable font, so it needs whoever made it rather than a script.

## Release checklist

Moved to [RELEASING.md](RELEASING.md), beside the rules for which release is which.
