# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **A pilot is held to its own datasheet, the way a bid already is.** `missing_needs`
  compared a `needs` against a sheet at the coordinator, where it judges another robot's
  bid, and nothing called it when a pilot judged its own body, so a `feasible` whose
  `needs` named a figure nobody published passed through and the body moved. `_assess` now
  runs the same function against the pilot's own manifest and refuses such a verdict the
  way it refuses one carrying `human`, naming the unmet need so the pilot can assess again.
  `uncertain` is left alone, because it asks a person and a reachable person knows things a
  sheet does not, and `infeasible` ends the run anyway. Measured on Qwen3-32B-AWQ over 54
  runs: a 45 minute patrol on a body whose endurance is not published came back `feasible`
  six times out of six and walked until the step budget, twice with
  `needs: {"endurance_min": 45}` recorded in the same row. On the patched build the check
  fired three times, all on that task, none on the other eight, and the body moved once
  instead of six times. What it does not do: the pilot came back `uncertain` rather than
  `infeasible` each time, and one run declared no endurance at all and so had nothing to be
  checked against. The check reads what a pilot declares, so it rewards honesty and cannot
  catch silence. Thanks to [@Vallhalen](https://github.com/Vallhalen), who measured it.


## [0.9.0] — 2026-09-15

A robot has a name now. `quackd robot add scout open_duck:bridge --address ... --token ...`
writes it once to `~/.quackd/robots.json`, and `--robot scout` then means the same thing in
every command that runs or inspects a robot, so reaching a real body stops being four flags, one
of them a secret, retyped into shell history on every run. A flock is a list of those names you keep, and it can
be a second kind of thing now: run a task file that asks for pilots, or one with no `flock:`
block at all, as `quackd run <duck> --flock <name>` and every member gets a whole
pilot of its own, with its own provider, executor, allowlist, budgets, heartbeat, memory and
feasibility verdict, all at once on wall-clock time and on any mix of bodies, dividing the work
by talking to each other rather than by an auction a referee runs. A file that asks for an
auction still gets the 0.3 coordinator, whatever it is started with. And every robot carries a
datasheet: what it weighs, what it can carry and reach, the band of heights its hands work at,
how long it runs, and the things it cannot do whatever the task says, with a confidence label
and a source on every number and "not published" where the maker never said. Nothing that
moves the body runs until the pilot has read that sheet and recorded a verdict, and `infeasible`
is its own outcome rather than a failure: nothing moved, the exit code is 3, and the reason
names which shipped bodies could have done it. The CLI got a house style in the same release,
which is where `--json`, `--no-color`, grouped `--help`, `-h`, the panels and `doctor --json`
come from.

Two things are breaking. `--model` and `QUACKD_MODEL` now take an id from a curated catalogue,
115 of them across eleven vendors, so an unlisted id is refused before a key is read rather
than by the vendor after the robot has connected — and the three defaults that had quietly
gone wrong, `gpt-5`, `gemini-2.5-pro` and a `grok-4` that was being silently answered by
`grok-4.3`, are replaced. And the Reachy Mini adapter is gone, which is why every count here
reads seven bodies where 0.8 read eight.

Still nothing has run on a robot, on any of the seven adapters. The SO-101 arm got more work
than any other body in this release, a camera, a checklist, a lookout task and an audit that
stopped it taking the arm's word for four things, and no arm was driven for any of it. Of the
eleven cloud vendors, two have answered a real request: OpenAI here, and Gemini on a
contributor's machine, which is where both of this release's Gemini bugs came from. No real
model has yet refused a task on feasibility grounds, no pilot flock has been driven by a real
model or by a robot, and the two nightly jobs that watch upstream have been red since before
0.8.0 shipped. Known limitations, below, says what each of those leaves open.

### Added

- **A robot has a name now, and quackd keeps it: `quackd robot add|list|show|edit|remove`.**
  Reaching a real body took four flags, one of them a secret: `--robot open_duck:bridge
  --address tcp://10.0.0.5:9871 --token ... --camera-url ...`, retyped on every run and
  therefore in shell history from then on. `--robots name=<adapter>:<backend>,...` gave a name
  that died with the process. `quackd robot add scout open_duck:bridge --address ... --token
  ...` writes it once to `~/.quackd/robots.json`, and `--robot scout` then means the same thing
  in `run`, `validate`, `list-verbs`, `doctor`, `serve-mcp` and `quackd memory`. Two commands are
  outside it: `record`, which pins the simulator, and `announce`, which advertises a static
  manifest and so takes an `<adapter>:<backend>` spec and nothing else. An
  entry may also name the provider and model that pilot that robot, so a real duck can default
  to Claude and the simulator to the scripted rule without a flag; a flag on the line still
  wins, field by field, because reaching the same robot through a tunnel today is not renaming
  it. `quackd robot list` is static, because it is what you run to remember a name, and
  `--probe` connects to each robot and says whether it answered, exiting 1 if any did not.
  `--registry-dir` beats `QUACKD_REGISTRY_DIR` beats `~/.quackd`, which is the precedence
  `--memory-dir` already has. A name may not be a number, an adapter name, or an
  `adapter-backend` memory slug, because each of those already means something else on a
  command line. The honest part: the token is stored in plain text in a file in your home
  directory, quackd masks it in everything it prints, and `SECURITY.md` says so
  ([docs/registry.md](docs/registry.md), [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **A flock is a list of names you keep: `quackd flock create|list|show|edit|delete`.**
  Members are robots registered with `quackd robot add`, so a flock is a composition rather
  than a command line, and `~/.quackd/flocks.json` remembers it between runs. `create` with no
  `--robot` prints what you have registered, numbered, and asks which to include; numbers and
  names can be mixed, a bad answer says what was wrong and asks again, and where there is no
  terminal to ask on it says so and tells you to pass `--robot` instead, because a script must
  never hang on a prompt. Order is kept, because it is the order the members are listed and
  coloured in when the flock runs. A flock stores up to 8 robots and runs with 2 to 8, so one
  you are still building, or one edited down to nothing, is stored and marked rather than refused. The one broken state either
  file can be in is a flock naming a robot nobody registered: `quackd robot remove` refuses
  while a flock lists it and names the flocks, `--force` drops it from them, and a flock that a
  hand edit left dangling is marked in every listing and refuses to run rather than quietly
  running smaller ([docs/registry.md](docs/registry.md),
  [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **A flock can be N pilots talking, not only a coordinator refereeing: `quackd run <duck> --flock <name>`.**
  The 0.3 flock is one deterministic referee and N state machines in one simulated arena on a
  lockstep clock, which is the right machine for finding and kicking a ball and the wrong one
  for two robots whose bodies differ, because an auction has no way to express half a task.
  A pilot flock is the other kind: one whole `AgentLoop` per body, all at once, on wall-clock
  time, on any adapter and backend including mixed ones, 2 to 8 of them. Each member keeps its
  own provider, executor, allowlist, budgets, heartbeat, memory and feasibility verdict, so what
  a pilot flock can do is close to what one pilot can do times the number of bodies. What a
  member gives up is what N concurrent pilots cannot have: nobody prompts a person, so a task
  with a `verbs.confirm` needs `--yes` and an `uncertain` verdict cannot be put to anyone, and
  there is one live view and one rollup for the flock rather than one per member. Each member is handed the part of the contract its own body can
  answer for, so an arm in a walking flock is not turned away at the door for having no legs,
  while what the task *requires* is still checked against the union of every body before
  anything connects. Every pilot declares for itself and the flock succeeds only when all of
  them did; otherwise the worst outcome wins, with `error` above `aborted` because one member
  raising and the rest being stopped because it did makes the error the cause and the aborts
  the consequence. Ctrl-C or `q` fans one kill switch out to every executor. `ducks/flock-hello.duck`
  is the bundled demo, a duck and an arm saying hello, and it runs with `--provider fake` on a
  fresh checkout with no key and no registry. The honest part, which is also in the docs: N
  simulated members are N separate worlds with no shared arena and nothing to check a claimed
  success against, a seed does not make it reproducible, it costs one budget and one model call
  per member per turn, and nothing here has run on hardware
  ([docs/flock.md](docs/flock.md#the-pilot-flock), [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **Pilots talk to each other: a `tell` tool and a `TALK` message on the flock bus.**
  `tell(to, text)` sits beside `assess_task`, `declare_success` and `remember`: it moves
  nothing, costs no step and one model call, and whatever was said arrives in the addressee's
  next observation under "Messages from your flock". `to` is a member name or `all`, a pilot
  never hears its own words back, and every message is a `TALK` in `flock.jsonl` like any other
  bus kind. It is not a verb, it is in no manifest, and no robot ever executes one. Each
  pilot's system prompt also gains a `## Your flock` section naming every peer and giving the
  short one-paragraph form of its datasheet, which says less than the bulleted sheet a pilot gets
  for its own body and says so out loud, so a pilot
  deciding who fetches and who holds is reading data rather than guessing, and saying that
  `assess_task` judges its own part rather than the whole task. The runner speaks too, under
  the name `flock`, when a member's loop ends, because otherwise a pilot waiting on somebody
  who has already stopped would wait until its budget ran out
  ([ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **`flock.allocation.method` takes `pilots`, the second value it has ever had.**
  The task file says which kind of flock it wants. `auction` is the default and everything it
  always was, and `--flock N` is always that one. `pilots` needs `duck: 1` and reads only
  `flock.members`. A coordinator flock is still 2 to 4 members, because its arena holds four; a
  pilot flock is 2 to 8, because nothing is shared and the bound is what one terminal can show.
  `flock.roles` stays a coordinator feature and is refused on a pilots file, which splits the
  work by talking instead. An older quackd refuses the value rather than ignoring it, which is
  the correct failure for a file asking for behaviour it does not have
  ([docs/duck-spec.md](docs/duck-spec.md), [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **`quackd serve-mcp --flock <name>` fronts a stored flock as the MCP fleet, and adds no tools.**
  `--robots name=<adapter>:<backend>,...` already served several robots from one process, with
  one executor, budget and heartbeat each. What it could not do is give each of them its own
  address, token and camera: those three flags were one value applied to every robot, which is
  fine for three simulators and wrong for three machines. A stored flock takes all three from
  the registry per member, keys each member's memory by its registered name, and makes the
  flock's own first member the default robot rather than whichever Microduck came first.
  `--flock` refuses `--robot`, `--robots` and the three endpoint flags, because the registry
  already answers all five. The nine `robot_*` tools are untouched, and a flock **task file**
  is still refused over MCP, whichever kind it is ([docs/mcp.md](docs/mcp.md),
  [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **Every robot carries a datasheet, and the pilot is told to check the task against it before anything moves.**
  A pilot used to be told one line about the body it was driving and a list of verbs, and nothing
  numeric: no payload, no reach, no working height, no endurance, and no way to say "this body
  cannot do that at all". Asked to carry a laundry basket on a 25 cm duck it had no ground to
  refuse on. Every manifest now carries a `datasheet`: what it weighs, how tall it is, how many
  joints it actuates, what it can carry and reach, the band of heights its hands work at, how long
  it runs, what it holds with, what it is rated for, and the things it cannot do whatever the task
  says. Every number states how sure quackd is of it (`official`, `estimate` or `measured`) and
  who says so, because a figure without a source is a rumour. A figure the maker never published
  is rendered as "not published: decline any task that hinges on it" and never as a zero. Speeds
  stay out of it: `limits` is what quackd clamps to, which is a rule about what quackd sends
  rather than a fact about the body, and the prompt renders those separately as clamps. The same
  sheet describes a body on every backend, which is what keeps a robot's digest equal across
  sim2d, mock and the real thing. `rosbridge` is the deliberate exception, and this release is
  what made it one: its sheet is whatever the bridge answered, so `mock` and `ws` describe
  different bodies on purpose, and a test asserts that they differ. The numbers were read from the makers' pages, repositories and
  one paper on 2026-09-13, and none of them were measured here, which is what the confidence
  labels are for ([ADR-0032](docs/adr/0032-datasheets-and-the-verdict.md)).

- **Nothing moves until the pilot has said the body can do the task.**
  The only way out of a task was `declare_failure`, which means "I tried and could not". A new
  meta tool, `assess_task` (`robot_assess_task` over MCP, the ninth `robot_*` tool and the first
  new one since 0.6), records a verdict first: `feasible`,
  `infeasible` or `uncertain`, with the reason, the datasheet fields it read, the estimates it
  made about the world and what the task would need. The executor gains a `verdict` gate that
  refuses every verb that moves the body until a feasible verdict exists; looking, speaking and
  the brake run before it, because that is how a pilot works out what it has been asked to do.
  `infeasible` is its own outcome, not a failure: the run ends where it stands, `quackd run` exits
  3, and the run's reason names which shipped bodies could do it by their own datasheets. On the
  first call, which is where the gate forces it, that means nothing moved. A pilot may record a
  verdict again later, so a run that drove and then declared `infeasible` ends the same way with
  the distance already travelled. `uncertain` asks
  the person at the terminal, and a no ends the run the way the kill switch does; over MCP, where
  there is no terminal, it stays pending and the model is told to ask the person it is chatting
  with, because a reachable human is a better answer than a flag. A model cannot clear its own
  doubt: the tool has no field for it. The two kinds of flock member differ here: a coordinator
  member is a state machine with no pilot at all, so there is no gate to ask with, while a pilot
  flock member has the gate and no terminal, so an `uncertain` there is told that nobody is
  present to answer for the human. The scripted pilot answers the gate as a rule and says so in its
  reason, which keeps every keyless example and all ten acceptance seeds running as before
  ([ADR-0032](docs/adr/0032-datasheets-and-the-verdict.md)).

- **A base over rosbridge reads its own description, and a flock role can ask for a body that can carry.**
  `rosbridge` names a transport, not a robot, so the honest static answer was that nothing is
  known. At connect it now asks the bridge for the topic list and the robot's own URDF, from the
  `robot_description` parameter first and the latched topic second, and two things a description
  can honestly settle go into the datasheet tagged official and sourced to the file: what the
  links weigh and how many joints are not fixed. Everything else stays unknown, payload above
  all. One deadline covers the whole look and it never fails a connect, so a bridge without
  `rosapi` costs that and leaves the sheet saying nothing was discovered, with the reason. A new
  verb, `introspect`, asks again for a robot that was still booting. Separately, a `duck: 2` flock
  role may state physical `needs` in the same vocabulary a verdict uses, checked by `validate`,
  by the member before it bids, and by the coordinator from the datasheet the bid itself carries,
  so a robot quackd does not run is held to the same standard. An unpublished figure counts as
  not met ([ADR-0032](docs/adr/0032-datasheets-and-the-verdict.md)).

- **A `duck: 2` file can correct a robot's datasheet for the build in front of you.**
  A vendor says an SO-101 lifts 500 g; your printed gripper holds 300. A `datasheet:` block in
  the frontmatter replaces any figure with your own, and the prompt renders it as coming from the
  task file so a reader can tell the maker's numbers from yours. The sentence lists extend and
  never delete: a task file can add something a body cannot do, and can never remove one. A
  correction the body contradicts, a payload on a robot with nothing to hold with, is refused by
  `quackd validate` before anything connects. `duck: 2` also unlocks `flock.roles.<role>.needs`
  ([docs/duck-spec.md](docs/duck-spec.md)).

- **quackd carries a list of the models it will let you pick, and `--model` picks from it.**
  `--model` used to take any string and hand it to the vendor, so a typo, an id retired last spring
  and an id belonging to a different vendor all failed the same way: at the first call, in the
  vendor's own words, after the run directory had been made and the robot had connected. There is
  now one curated list per cloud vendor in `quackd/agent/providers/catalogue.py` — eleven vendors,
  115 models, each with the id as the vendor spells it, a label a human can read, one of five
  statuses (`current`, `legacy`, `preview`, `specialised`, `open`) and two hints about what the
  vendor will accept. The first entry of a vendor is its default, so a default is no longer a second
  place the id gets written down and falls out of step. `quackd list-models` prints the whole table
  and `--provider NAME` narrows it to one vendor; the notes column marks the default, the OpenAI
  models that open on the Responses API, and the individual models whose vendor does not document
  image input, where
  quackd sends the detections as text instead of the camera frame and `--vision` overrides. That
  last one is a per-model fact rather than a per-vendor one: several vendors carry both kinds.
  `QUACKD_MODEL` is checked exactly as `--model` is, and the refusal says which of the two the id
  came from, because a flag you just typed and a line you forgot in a `.env` want different answers.
  `--model` also completes in the shell, following whichever `--provider` is already on the command
  line. The local presets are deliberately outside all of this: `local`, `ollama`, `vllm`,
  `llamacpp` and `lmstudio` serve whatever you pulled, so `--model` is still free text there and
  passing none still means "ask the server what it has". `quackd serve-mcp` picks no model at all —
  the client's own model is the pilot and `QUACKD_MODEL` is irrelevant there — which was already
  true and is now written down ([ADR-0031](docs/adr/0031-model-catalogue.md)).

- **`quackd list-models`**, the command that prints what `--model` will accept. Five columns per
  id: the vendor, the id as that vendor spells it, a label a human can read, its status, and a
  notes column that marks each vendor's default, the OpenAI ids that open on the Responses API,
  and the individual ids whose vendor does not document image input. `--provider NAME` narrows it to one vendor
  and `--json` prints one object per line. It is the answer to a question the catalogue's refusal
  raises, so the refusal names it.

- **Seven more cloud vendors: Mistral, DeepSeek, Cohere, Qwen, Kimi, GLM and Meta.** All seven serve
  an OpenAI-shaped endpoint, so each is one small `OpenAIProvider` subclass with a base URL and a
  key variable, exactly as Grok has been since the first release. `--provider mistral` reads `MISTRAL_API_KEY`,
  `deepseek` reads `DEEPSEEK_API_KEY`, `cohere` reads `COHERE_API_KEY` (or `CO_API_KEY`, which is
  what Cohere's own examples export), `qwen` reads `DASHSCOPE_API_KEY`, `kimi` reads
  `MOONSHOT_API_KEY`, `glm` reads `ZAI_API_KEY`, and `meta` reads `META_API_KEY` (or
  `MODEL_API_KEY`). Each has its own extra — `quackd[mistral]`, `quackd[deepseek]` and so on — and
  every one of them installs the same `openai>=1.50` wheel that `quackd[openai]` and `quackd[grok]`
  do. Nine vendors, one package: the extra exists so that a missing-SDK error can name the install
  the reader actually wants rather than a package name they will not connect to the vendor they
  asked for. Meta here is not Llama — Meta retired the hosted Llama API in July 2026, and its
  replacement, the Meta Model API, serves Muse Spark; two of those are a contributor tier, cheaper
  because Meta trains on your prompts, and the label in `list-models` says so. The honest part: of
  the eleven cloud vendors, two have answered a real request. OpenAI on the machine that wrote
  this, and Gemini from a contributor's, which is how the two Gemini bugs under Fixed were found.
  The other nine are wired from their own published documentation and held by tests that stub
  the SDK client. For the eight that speak OpenAI's API that proves the base URL, the key
  variable, the extra and the default model; Anthropic sets no base URL of its own, so for that
  one it proves the extra and the request shape. None of it proves that any vendor answers.

- **The browser demo picks its model from a dropdown.** The page declared a `models:` array per
  vendor that nothing ever read, and offered a free-text box instead, so a visitor who came to click
  one thing had to already know a model id to type. The dropdown is now fed by the same catalogue
  through `web/src/catalogue.js`, generated from the Python and grouped by status, with a test that
  fails when the generated file has drifted from `catalogue.py`.

- **A bring-up checklist and a lookout task for the LeRobot arm, which had neither.**
  Every other body quackd ships a lookout for had both, and this file has said the arm did not
  since 0.7. [docs/lerobot-hardware-checklist.md](docs/lerobot-hardware-checklist.md)
  is the order to try an SO-101 in, with nothing moving until step 10 and a hand on the power
  switch from there, because this arm has no e-stop. `ducks/lerobot-lookout.duck` is the task
  to point at a real arm first: it moves no joint, and it asks for `report_state` rather than
  `observe`, because a `.duck` is checked against the static manifest, which cannot know
  whether a webcam is plugged in
  ([docs/adapters/lerobot.md](docs/adapters/lerobot.md)).
  One body still has neither, and it is the one that names a transport rather than a robot: a
  `rosbridge:ws` base gets no lookout task and no checklist in this release either, so it goes
  on blocks and a person reads [docs/adapters/rosbridge.md](docs/adapters/rosbridge.md) instead.

- **A camera on the LeRobot arm: `--camera-url opencv://N`.** No SO-101 has a camera in it,
  whatever a kit's listing says: the arm is six servos and a serial board, and every camera on
  one is a USB webcam plugged into the computer. `lerobot:real` now opens one, so `observe`
  exists on a real arm for the first time. The url is the OpenCV index
  (`lerobot-find-cameras opencv` prints them and saves a frame from each, which is the only
  honest way to tell which is which) or a device path, `opencv:///dev/video2`, with `?width`,
  `?height`, `?fps`, `?fourcc`, `?rotation`,
  `?name`, `?fov` and `?backend=msmf` for the Windows camera that lists and then will not open.
  Nothing is asked of the camera by default, because a mode it cannot do is a refusal at
  connect and the webcam in a lab drawer is unknown. An unknown key or a bad value is refused
  with the shape, before LeRobot is imported.
  quackd builds the camera itself rather than handing it to the follower, and that is the
  whole design: a follower's `is_connected` is the bus **and** every camera, and `send_action`
  and `disconnect()` are gated on it, so one webcam coming unplugged would have made every
  move and every hold raise while the arm was perfectly fine. Beside the follower, a camera
  asked for and not opened refuses at connect naming the url, before the arm is touched at
  all, and a camera that dies later costs `observe` and a `pick` in flight and nothing else:
  the heartbeat still reads the arm, the joints still move, `stop` still holds. `observe` now says what the camera said rather than "this transport has
  no camera", `quackd doctor` gates its verdict on a real frame as it does on every body whose
  transport reports camera health, which is the Microduck over robotd and the Open Duck's bridge
  and not the four that report none, and `?fov=` travels with the camera into `limits.camera_fov_deg` so bearings are
  calibrated over MCP too, where there is no `--fov-deg`
  ([docs/adapters/lerobot.md](docs/adapters/lerobot.md#camera), step 8 of
  [the checklist](docs/lerobot-hardware-checklist.md)).

- **The LeRobot pages rewritten for someone who owns the arm rather than someone who wrote
  the adapter, and five things they said that were not true.** An SO-101 owner is the likeliest
  first external user of quackd, and the two pages assumed a reader who already knew what
  quackd was for. [docs/adapters/lerobot.md](docs/adapters/lerobot.md) now opens with what
  LeRobot already does for you and what quackd deliberately does not touch (teleoperation,
  recording, training), the three properties of this body that shape every guard, and a
  starting path that begins with the mock and no arm at all. It gained the install trap in
  full (the `[feetech]` extra, and the `python_version >= '3.12'` marker that makes an install
  on 3.11 resolve to nothing while `doctor` keeps saying `not installed`), a section on the
  calibration id, which is the name you give the robot and the one thing that silently breaks
  a connection after a calibration you watched succeed, the three ways to drive the arm with
  the MCP config written out, `--dry-run` as a rehearsal that connects for real and sends
  nothing, and a troubleshooting section whose every row quotes a refusal from the code that
  raises it, beside what to do about it, followed by a short list of failures SO-101 owners report that nobody
  here has verified, labelled as such.
  The five corrections: a task that allows `observe` is refused on **every** real arm rather
  than only on one without a camera, because `quackd run` checks the allowlist as well as
  `requires` against the static manifest, and the place it does work is MCP, where
  `robot_load_duckfile` validates against the robot already connected, so the same task loads
  on a session started with `--camera-url`; the pilot gets detections every step whether or
  not `observe` is allowed, which makes `--vision` the picture rather than the sight;
  `load_policy()` imports
  `lerobot.configs.policies.PreTrainedConfig`, which had no ref, so "every name quackd spells
  lives in `upstream_api.py`" was false until this commit added it; and `pick` is not reachable
  from the CLI or from MCP at all, because `make()` has no policy parameter and `load_policy()`
  has no caller outside a test, which the checklist had presented as something to try at the bench.
  The checklist could not be followed as written: step 4 ran `lerobot-calibrate`, which step 5
  installed. Installing now comes first, finding the port with upstream's own `lerobot-find-port`
  comes with the calibration, and the claim that Windows needs a CH340 or CP210x driver is gone,
  because the arm enumerates as a USB CDC device and no primary source names that chip. It also
  gained the `--dry-run` rehearsal as step 9, so nothing moves until step 10, and a
  [hardware report template](.github/ISSUE_TEMPLATE/lerobot-hardware-report.yml) that asks for
  exactly what *What to report* asks for. Two things in the code changed because writing the
  pages found them, and they are under Fixed: a camera that dies mid-run used to be invisible
  on a `quackd run`, and `doctor` advised about verbs an arm does not have.

  One thing neither page knew: **upstream's calibration does not sweep `wrist_roll`.** It
  prints *move all joints except 'wrist_roll'* and records a full encoder turn for it, so that
  joint's travel comes out as -180..180 and quackd's out-of-range refusal, which is real on the
  other four body joints, cannot catch anything on that one. Both pages now say so, and the
  checklist's out-of-range step says which joint not to test it on.

- **A body field the server wants and quackd never sends: `--extra-body` and
  `QUACKD_EXTRA_BODY`.** One JSON object, merged into the top of every request body on every
  provider that speaks OpenAI's API, which is nine of the eleven cloud vendors and all five
  local presets, and sent on Chat Completions and Responses both, so it keeps working when a
  run moves from one to the other mid-flight. The case that asked for it: Qwen3 on vLLM thinks
  before it answers unless the request body says `{"chat_template_kwargs": {"enable_thinking":
  false}}`, and that switch is a chat template argument rather than a sampling parameter, so on
  a server somebody else runs there was nowhere to say it. One reported step spent 150 s and
  1717 output tokens deliberating before a decision that was correct anyway. The flag beats the
  variable, an empty object sends nothing, and a value that is not one JSON object is refused
  before a robot is connected, naming the flag or the variable it came from. Six keys are
  refused because they are quackd's to send — `model`, `messages`, `input`, `instructions`,
  `tools` and `stream`, the fourth being the system prompt on Responses the way the second is
  on Chat Completions — and everything else replaces what quackd would have sent, `tool_choice`
  included, because overriding it is the point. `run_start` records the object, since a run
  whose model was told not to think reads nothing like one that was. Anthropic and Gemini
  ignore it, as they already ignore `--base-url`. It is in `.env.example` with the others,
  where single quotes matter: double ones make python-dotenv drop the line without a word. If
  you run the server yourself, vLLM's own `--default-chat-template-kwargs` does the same thing
  once at serve time, and the docs now name both. Thanks to
  [@Vallhalen](https://github.com/Vallhalen) (#12), who measured it and proposed the
  passthrough ([docs/local-llms.md](docs/local-llms.md#knobs)).

- **Two transcripts of `find-and-kick` piloted by Qwen 2.5 Coder 14B on LM Studio**, seeds
  5 and 6, land in `docs/assets/transcripts/` with a table reading them in
  `docs/local-llms.md`, from the contributor whose memory feature they were recorded for.
  The README, `local-llms.md` and PLAN.md no longer say "no transcript in this
  repository". The pair is not a chain: they ran against different memory directories,
  so one shows the write and the other the read, and neither shows a note surviving
  from one run into the next. Thanks to [@Bayway](https://github.com/Bayway) (#7),
  whose runs and whose transcripts these are.

- **Two transcripts from Qwen3-32B-AWQ on vLLM, and the first numbers anybody has for what
  `--extra-body` actually stops.** Same build (`739ff84`), same server, and the same seed on the
  contributor's word, since no transcript records one. The flag is the difference the pair was
  built around.
  Every test in this repository proves the object reaches the SDK call and not one of them proves
  the thinking stops, so until now that flag shipped on a mechanism nobody had watched work. With
  it off, all eight LLM calls deliberate in the open, 599 to 1,446 characters of it in each row's
  `thinking` field; with it on, not one of the five carries that field at all. Neither run needed
  the JSON text fallback, on a server nothing here had ever talked to.
  **The drop is 4.9x, not the 7.8x the totals show.** The two runs took different paths, so
  2,049 against 263 is not a comparison. Five decisions are common to both, `assess_task`,
  `search_scan`, `walk_to`, `kick` and `declare_success`, and on those it is 1,290 against 263.
  The rest is path, and the path is not the flag's doing either: the quiet run skipped `remember`
  because its prompt already held the fact it would have saved and the duck tells it to skip one
  that is already there, and it skipped the `quack` its persona asks for, which is an instruction
  missed rather than a step saved. The thinking run's own first call asked for `search_scan`
  before recording a verdict and the gate refused it, so one of its eight bought nothing.
  Its expensive first call is the verdict, not a warm-up: `assess_task`'s `reason` argument is a
  five sentence paragraph, and at the 8.1 to 10.2 tokens a second every call in that run decodes
  at, 130 tokens is about 13 of its 16 seconds. `reasoning_tokens` stays 0 in both, which is a
  property of a server running without `--reasoning-parser` rather than evidence of a model that
  did not reason: the server leaves the thinking in `content`, billed as output, and quackd then
  splits it into the transcript's own `thinking` field before the text fallback can read a verb
  out of a discarded thought.
  **And the pair is the first published chain of quackd's memory**, which the PR delivered
  without claiming. The thinking run's `remember` saved *"The ball was found at 43° left, ~0.85 m
  during initial scan."* and that sentence is in the other run's `system_prompt` verbatim, beside
  the episode quackd wrote from the same run, with the counters moving from 1 note and 2 episodes
  to 2 and 3, so nothing ran between them. A note written by one run and read by the next, both
  ends in this repository, which no transcript here had shown and which
  [PLAN.md](PLAN.md) had carried as open since #7. The machine is an `aarch64` NVIDIA GB10,
  which nothing here had run on before. Thanks to
  [@Vallhalen](https://github.com/Vallhalen) (#12, #23), who asked for the flag, was told the
  tests could not prove it worked, and went and measured it on the only machine that could.

### Changed

- **The README and the social card wear the duck head quackd.org uses, and `logo.svg` is
  gone.** The README opened with a flat biped and a wordmark that set `quack` in yellow and
  the `d` in purple. It opens with the 3D duck head instead, the same drawing quackd.org
  shows in a browser tab, over a plain `<h1>` in whatever colour the reader's GitHub theme
  uses for a heading. The two marks had been diverging since the landing page shipped, and a
  project whose front door and whose website do not look like the same project is paying for
  two identities and getting neither. The head was already in this repository at
  `web/assets/duck-mark.png`, vendored for the browser demo in 0.8, so the README, the card
  and the demo now read one file. `docs/assets/social_preview.py` stops transcribing
  `logo.svg`'s geometry into Pillow and pastes that PNG instead, after the resample rather
  than before, so the art is never resized, and it measures the mark's own ink and the type's
  own height to place them rather than trusting a coordinate, because `_FACES` resolves to a
  different typeface on every machine. The wordmark loses the two-colour split for the same
  white the headline uses. `docs/assets/logo.svg` is deleted, because a second mark nothing
  renders is a second mark that goes stale. What kept the two in step before was nothing, so
  `tests/test_pypi_readme.py` now asserts that the file the README opens with and the file the
  card pastes are the same one, and `web/README.md` records that `web/assets/` stopped being
  the browser demo's private chrome the moment two other things started reading it. The
  uploaded social preview is a manual follow-up as always: GitHub has no API for it, and
  PLAN.md carries it.

- **The LeRobot arm adapter stops taking the arm's word for four things it never said.**
  Re-reading upstream at the pinned commit found that `is_connected` is only the serial
  port's open flag, that `get_observation()` reads positions and nothing else, that a degrees
  goal past the calibrated travel is written unclamped, and that one action may slew a joint
  its whole travel. So the heartbeat now reads the arm; torque and each servo's temperature
  are read off the bus by register; every joint's range comes from the arm's own calibration
  file and a goal outside it is refused; and one action moves a joint at most a capped step,
  set by `QUACKD_LEROBOT_MAX_STEP_DEG`. What a pilot notices: `move_joints` and `gripper`
  re-send the goal and watch the measurement, so they can now fail with where the arm stopped
  and `duration_s` is a budget rather than a wait; a new `not_hot` precondition refuses
  `move_joints` and `pick` when one of the five body joints reads 60 °C or more, the gripper
  being left out on purpose because opening it is how you put down what it is holding;
  `report_state` carries `torque` as measured, `temperature_c`, `hot`, `out_of_range` and
  `step_deg`, and the manifest of a connected arm gains `joint_range_deg`, `calibration_file`
  and `torque_limit_scope`; `holding` is inferred from the gripper settling short of shut; `stop`
  holds the five body joints and leaves the gripper's goal alone, so a failed verb never drops
  what is held; and the datasheet no longer claims a mass, because vendor listings disagree by
  a factor of three. Why each of these is the way it is:
  [ADR-0036](docs/adr/0036-what-the-arm-does-not-say.md).

- **The one-liner changed, and every place that carried the old one followed.**
  It was *Give your Microduck a brain. Any LLM, one `.duck` file.* It is now *One CLI for all your
  robots. Connect them, command them, and let them work together, each with an LLM for a brain.*
  That sentence was accurate for 0.3, when quackd was a brain for exactly one robot. 0.4 made the
  robot an adapter that declares a manifest, and the registry and the pilot flock in this same
  section made a robot a name you keep and a flock a list of those names, so what quackd is is the
  place you connect the robots you own, the place you command all of them, and where they divide a
  task between themselves, each with an LLM for a brain. quackd is the CLI, not the brain. The new
  sentence is in the README, in `pyproject.toml`'s description, which is what PyPI shows from this
  release on, and in its keywords, where `reachy-mini` gave way to nine vendor names and to
  `multi-robot` and `robot-fleet`, the one place the retired word stays on
  purpose, in the `quackd --help` banner, in `quackd/__init__.py`'s docstring and in `LAUNCH.md`.
  The demo's title and meta tags, [docs/architecture.md](docs/architecture.md) and
  [docs/mcp.md](docs/mcp.md) did not take the sentence itself: they lost the retired tagline and
  had the new positioning written into their own words. "Fleet" is retired
  from prose and from help text in favour of "flock", while the code identifiers keep it, because
  renaming `build_fleet_server` would be churn no reader sees. [docs/flock.md](docs/flock.md) now
  reads pilots-first. Nothing new ships here: the features this describes landed in this same
  release, every body is still simulated or mocked, and flock mode is still EXPERIMENTAL
  ([ADR-0035](docs/adr/0035-one-cli-for-all-your-robots.md)).

- **A registered robot keys its memory by its name, not by its body.**
  Memory was keyed `adapter:backend` so that a simulated duck never inherited a real one's
  notes, which was right and also meant two real ducks on one desk shared one file. A robot
  registered with `quackd robot add` now keys by the name you gave it, so `duck-a` and `duck-b`
  keep separate notes, while an unregistered `--robot microduck:sim2d` run keys exactly as it
  did. A registered name may not collide with an `adapter-backend` slug, so a run can never be
  ambiguous about which file it is writing. Amends
  [ADR-0025](docs/adr/0025-memory-between-runs.md)
  ([docs/memory.md](docs/memory.md), [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **Breaking. `--model` and `QUACKD_MODEL` now take a catalogue id, and three of the four defaults moved.**
  [ADR-0010](docs/adr/0010-providers.md) shipped four defaults with the first release and marked
  three of them "(verify)". Nobody ever did, and by 2026-09-12 all three were wrong: `gpt-5` has
  an announced shutdown date, `gemini-2.5-pro` is legacy, and `grok-4` was retired in May 2026 and
  is silently answered by `grok-4.3` — which is the worst of the three, because nothing fails, the
  key is billed and the transcript records a model that did not run. The defaults are now
  `gpt-5.6-sol` for `openai`, `gemini-3.8-flash` for `gemini` and `grok-4.6` for `grok`;
  `claude-opus-5` for `anthropic` is unchanged. This is breaking for anyone passing an id quackd
  does not list, and that is the point: the refusal arrives
  before a key is read or a packet is sent, and it says what to pass instead. Two of the three
  old defaults are in that position, `gpt-5` and `grok-4`, both of which the catalogue refuses by
  name. The third is not: `gemini-2.5-pro` is still listed as `legacy`, because the rule for
  being listed is that the vendor still serves it with no end announced, so a run that names it
  explicitly keeps working and only the default moved out from under it.

      error: openai: unknown model 'gpt-5' from --model. Valid ids: gpt-5.6-sol (default),
      gpt-6-astra, gpt-5.6-terra, ... See `quackd list-models --provider openai`.

  When the id belongs to another vendor the refusal names it — `('grok-4.6' is a grok model: pass
  --provider grok)` — because that is the commonest mistake and the hardest to see, the id looking
  perfectly valid. A `QUACKD_MODEL` line that has sat in a `.env` since then now stops the run rather
  than starting a wrong one, and `quackd doctor` shows the id each provider would actually be given
  instead of guessing at it. One more thing rides on the same list: five OpenAI entries are marked
  as Responses-API models, `gpt-6-astra`, the three pro ids and `gpt-5.3-codex`, so a run with one
  of them opens on `/v1/responses`
  instead of paying a failed Chat Completions call to find that out. The 400 reader 0.8 added stays,
  because it is what covers a model the catalogue has not been told about yet.

- **The CLI has a house style, and a way out of it.** quackd's colours had grown inline: a
  `[green]` in one command, a `Table` in another, an emoji in a third, with nothing saying
  which green meant *this worked* and which meant *this is installed*. `quackd/ui.py` is now
  that vocabulary in one place, and three rules run through it. Text a model, a robot or a
  manifest wrote is never read as markup, so a note saying the ball is `[behind]` the sofa
  and an error naming `quackd[anthropic]` both arrive intact. Glyphs come from a table with
  an ASCII half chosen from the stream's own encoding, and the panels pick their half when
  they are drawn rather than when they are built, because a replay goes to stdout, a run
  narrates to stderr, and a test hands round a buffer of its own. And chrome goes to stderr
  while answers go to stdout, so `quackd list-adapters > adapters.txt` gets the table and
  nothing else. On a Windows pipe, where every decoration used to arrive as a question mark,
  the adapter roster now reads `[ok] built-in: sim2d` and `[exp] jsonrpc`.

- **`--no-color`, and `--json` on `validate`, `list-verbs`, `list-adapters`, `list-models` and the
  four registry listings.** The tables are for a person. `--json` prints one object per line on stdout and nothing else, keeping
  the exit code it would have had, so `quackd validate ducks/*.duck --json` still exits 1 on
  a failure and a script can read which file and why. `--no-color` sets `NO_COLOR` as well as
  quackd's own consoles, because Typer builds a console of its own for every `--help` it
  renders. `FORCE_COLOR=1` is the other direction, for a pipe you are colouring on purpose.
  `quackd list-models` arrived in the same release from the other direction and wears the
  house style too: its model ids still fold rather than elide, and none of the five columns
  goes through Rich's markup on the way, so a label with a bracket in it survives.

- **`-h` works**, everywhere `--help` does, and `--help` groups what it shows. `quackd run`
  offered twenty seven flags in one flat list. They are sorted into Task, Model, Robot,
  Output and Memory now, the commands are grouped the same way, and the root help ends with
  three worked examples.

- **A line saying what the run is waiting for.** A run spends nearly all its wall clock
  inside two calls, a model deciding and a verb steering a robot, and said nothing until
  each of them finished. With `--no-trace` it said nothing at all between the header and the
  verdict, however long that took. A transient line at the bottom of stderr now names what
  is being waited on, which step it is, and how many seconds it has been there. It reads the
  same event stream the trace does, it exists only when stderr is a terminal somebody is
  watching, and it steps out of the way for a confirmation prompt, because a live region
  redirects stdout and would otherwise swallow the question until after it was answered.
  `quackd doctor`, `quackd discover`, `quackd announce` and GIF encoding get a spinner for
  the same reason.

- **A run opens and closes with a panel.** The header used to be one line of middle dots and
  the verdict three lines under it, and on a long run the two ends of the story were the two
  things hardest to find in a screenful of trace. They are now bordered, the verdict is
  coloured by its outcome, and the run directory and the GIF are links where the terminal
  allows it. `quackd trace` prints the same verdict from the transcript, so a replay still
  ends the way the run did.

- **`quackd memory show` is two tables**, notes and recent runs, rather than the block of
  dim text the model is given. The outcome has its own column and its own colour, and an
  episode no longer repeats the duck and the outcome inside the sentence that follows them.

- **`quackd doctor --json`**, and a verdict at the end of the human version. doctor knew
  whether this machine could run anything and said so only through its exit code, which
  nobody reads off a screen. It now closes with one line: what works here, how many extras
  are installed, how many assumptions are unverified, and which cloud providers are one key
  away from working. The `--json` half exists because `collect` and `render` are now two
  functions rather than one: the collector answers in dataclasses with no styling in them,
  and it could not have been serialised before, because every cell it produced *was* a
  markup string.

- **The trace is drawn for the person reading it.** The arrow is a glyph in a gutter now and
  the column says the word it stood for, so `-> sound(...)` reads `→  send    sound(...)`
  and a result is `✓` or `✗` or, for a handover that ended a verb early on purpose, `•`.
  Each step is ruled off with the budget line lifted out of the observation it was buried
  in, and said once rather than twice. The system prompt is an indented block between two
  rules instead of forty lines of the same dim colour. A flock gives each member a colour as
  well as a name. [ADR-0033](docs/adr/0033-terminal-theme.md) records the decision and
  amends [ADR-0029](docs/adr/0029-tracing.md), whose "lines are ASCII first" rule now
  applies where it was earned: the MCP tool result carries exactly the bytes it always did,
  frozen case by case by a new golden, and the terminal asks the stream it is writing to
  which half of the glyph table it can carry. A redirected stderr on Windows still gets
  `->`, `+` and `x`, and the degree signs, section signs and dashes that used to arrive as
  question marks now arrive as ASCII quackd chose rather than as the terminal's guess.

- **A closed pipe is not an error.** `quackd list-verbs | head` answered with a wall of
  traceback about a broken pipe printed on top of the output that was asked for. The console
  entry point is now `quackd.cli:main`, which leaves quietly, and Typer's pretty exceptions
  are off because they print local variables and this process's locals hold an API key, a
  robot's address and its bridge token. A Rich traceback without locals is installed instead,
  so a real crash still reads well.

- **`quackd validate` counts in English** (`12 files valid`, `1 of 3 files failed`), names
  where to look next when it fails, and no longer lets a long file path squeeze the column
  that carries the answer down to nothing.

- **`quackd doctor` reads as a report rather than a wall.** Fourteen tables arrived stacked
  with nothing between them, and eight of them were per-upstream lists of unverified
  assumptions, each followed by its own dim footer. Those sixteen blocks are now two: one
  sectioned table of assumptions, and one table of pins that puts the eight upstreams side
  by side where they can be compared, with the doc paths on a line under it. The sections
  are ruled off and named, and the five local LLM servers are probed behind a spinner
  rather than ten seconds of silence.

- **Extras in `--help` keep their brackets.** `--live` advertised an install called `quackd`
  rather than `quackd[live]`, because Rich had read the extra as markup and eaten it. Same
  for `quackd[microduck-camera]` and `quackd[lan]`.

### Removed

- **Breaking. The Reachy Mini adapter.** `--robot reachy_mini:{sim2d,mock,sdk}`, `quackd[reachy]`,
  the `reachy-spotter` and `reachy-spots-duck-kicks` starters, the `reachy-mini` GitHub topic and
  the PyPI keyword are gone, along with its doc page, its `hetero.gif` and every mention that
  described it as a robot quackd still drives. A command line or a `.duck` naming that adapter
  fails at `--robot` now, which is the correct failure for a body that is not there. The ADRs
  that document it keep their text, marked superseded or amended. The design records under
  `docs/design/` keep their text unmarked, `multi-robot.md` still describing the adapter in the
  present tense, because those are the record of what 0.4 designed rather than of what ships. quackd ships seven adapters now: Microduck, Open Duck Mini v2, LeRobot arm, any rosbridge
  base, XLeRobot, AlohaMini and ToddlerBot. Reachy Mini was the only "stationary head"
  embodiment quackd ever carried, so the machinery that existed solely to pair one with a
  Microduck goes with it: `StationaryHead` and the fixed head poses in `sim2d/world.py` and
  `sim2d/render.py`, and the reachy-only branch of `flock/runner.py`'s `make_sim_flock`. The
  capability-based role auction itself (`flock/auction.py`'s `RoleAuction`,
  `flock.roles` in a `.duck` file) is separate, still-generic machinery and is untouched —
  [ADR-0020](docs/adr/0020-heterogeneous-flocks.md) always said a two-Microduck spotter/kicker
  flock is valid on its own; the auction simply has no second embodiment to pair a Microduck
  against today. [ADR-0023](docs/adr/0023-reachy-mini.md) is marked Superseded rather than
  deleted, and [ADR-0020](docs/adr/0020-heterogeneous-flocks.md) is amended in place, both left
  readable as the record of what 0.4 through 0.8 actually shipped. Nothing about the other
  seven adapters changed.

- **`docs/assets/logo.svg`**, the flat biped and the two-colour wordmark the README opened with
  until this release. The mark entry under Changed says what replaced it and why a second mark
  nothing renders is a second mark that goes stale.

### Fixed

- **The trained gait's 10 of 10 is not reproducible, and four documents stated it as a
  property.** 0.8 shipped that number in this file, in the README three times, in
  [docs/adapter-status.md](docs/adapter-status.md) and in
  [ADR-0030](docs/adr/0030-mujoco-physics-backend.md). What the evidence supports is narrower.
  The nightly job that runs the sweep had 10 of 10 on each of its first five runs, 2026-09-09
  to 09-13, and 9 of 10 on 09-14, seed 4 aborting. Run by hand on the machine that cut this
  release it is 9 of 10, seed 4 again, and it is 9 of 10 there on the `mujoco` and
  `onnxruntime` versions 0.8 shipped as well as the ones this release bumps to, so the bump is
  not what moved it. The failing seed is not a fall: the duck is standing, the ball has not
  moved, and the run ends on the duck's own *same verb fails 3 times in a row* rule after three
  steps. So seed 4 is marginal and platform dependent rather than the gait being broken, and
  the honest claim is that the sweep has returned 10 of 10 and does not always. The shipped
  threshold outside `QUACKD_STRICT_SEEDS` is 8, which it clears every time. Every one of those
  four documents now says what was measured instead of a round number, the ADR by dated
  amendment rather than by rewriting what it decided. The kinematic stand-in is untouched and
  still gates every push at 10 of 10.

- **A camera that died mid-run was invisible on a `quackd run`.** `camera_error` was read only
  by `observe` and by `doctor`, and `observe` cannot be in a `.duck`'s allowlist on the arm, so
  on the one body where a camera is a thing you plugged in yourself the frames simply stopped
  and nothing said why. The camera's health now rides in the arm's own state beside the policy
  and the register errors, which puts it in the transcript and in front of an MCP client, and
  `report_state` says `CAMERA DOWN:` with the reason when a read has actually failed, staying
  quiet for a camera that is merely unread.

- **`quackd doctor` advised about verbs the robot in front of it does not have.** Its no-frame
  advisory named `go_to`, `search_scan` and `approach_and`, none of which exist on an arm bolted
  to a table. It names that body's own camera verbs now. A `camera_health()` proxy on the adapter
  went with it: `doctor` reaches the transport's own method, as it does on every other body, so
  the proxy was called by nothing but the tests that were meant to be covering `doctor`.

- **`lerobot-lookout` asked the pilot for three things it could never see.** A pilot reads a
  verb's summary text and never its data: the dump goes to the transcript and to an MCP client,
  and the observation the model is handed carries the summary. The core `report_state`
  summarises a posture and a policy name, which on a bolted-down arm is two facts it has not got
  and none of the four it has, so the lookout task asked a real model to report where the joints
  are, whether torque is on and whether anything is too hot, none of which had ever reached it.
  The scripted pilot passed the task because it reads the feature dict instead, which no LLM
  gets. The arm supplies its own `report_state` now, and a test pins it through the observation
  text the model actually receives.

- **quackd's log records no longer stop at quackd's own handler.** `install_logging` set
  `propagate = False`, which is the obvious way to stop a record being printed twice and also
  cuts every quackd logger off from the root: an application embedding quackd saw nothing
  through its own `logging.basicConfig`, and `caplog` caught nothing in a test. Propagation is
  left alone now, and the double print is prevented where it starts instead.

- **The duck's chin was missing from the mark, and nothing recorded how to put it back.**
  `web/assets/duck-mark.png` and `web/assets/favicon-96.png` had both been exported with the
  crop window about twenty rows too high, so the head ran off the bottom of its own canvas and
  took the chin, the lower jaw and the orange mouth with it. Opaque pixels sat on the last row
  of each file, which is the tell. The README has opened with that mark since earlier in this
  release and the social card is built around it, so the same slice showed up in three places
  at once. Both are re-cut from quackd-web's `src/assets/duck-source.png`, the only original
  there is, by a new `web/make_mark.py` that exists because the answer to "how was this made"
  was nothing at all. It keeps the old scale deliberately, recovering it by measuring the dark
  visor panel in both images rather than trusting a number, so the mark's weight on the README
  and on the card is unchanged and only the missing part is new. It drops the body at the neck,
  lifts out the heart speech bubble, and refuses to write a file whose ink reaches an edge,
  which is the check that would have caught this. `web/assets/apple-touch-icon.png` was already
  whole and is left alone. quackd-web carries its own copies of the two bad exports and needs
  the same fix, which is a separate repository and a manual follow-up.

- **`quackd[lerobot]` installed a LeRobot that could not open a serial port.**
  At the pinned commit the Feetech SDK and pyserial live in lerobot's own `[feetech]` extra
  rather than in its base dependencies, so `uv pip install 'quackd[lerobot]'` gave you a
  lerobot that imports perfectly and then cannot reach an arm. The extra now asks for
  `lerobot[feetech]`, and `quackd doctor` has a row for the SDK by name, because a failure
  that arrives at `connect()` rather than at import is one a diagnostics command should be
  the first to find.

- **The system prompt no longer promises every body a verb only a duck has.**
  It opened by telling the pilot that composite verbs like `walk_to` close their own loops on
  the camera, falling back to naming `search_scan` when it found neither `walk_to` nor `go_to`.
  A robot that provides none of the three was therefore told about one it does not have, in the
  same prompt whose allowlist does not list it, and a bolted-down arm was told its controllers
  handle balance and gait. It now names a composite verb only when the body actually provides
  one, and says "the motion" for a body that does not move itself. A Microduck's prompt is
  unchanged, byte for byte. Found by reading the arm's real prompt in the new `flock-hello`
  demo, which is what a bundled task on a second body is for.

- **A registered robot's model no longer follows `--provider` to another vendor.**
  `quackd robot add duck-a ... --provider anthropic --model claude-opus-5` then `quackd run
  <duck> --robot duck-a --provider openai` carried the Claude id into OpenAI's catalogue, where
  it was refused with a message blaming a `--model` nobody typed. A stored model now applies
  only to the provider it was stored against, and an explicit `--model` is always taken as
  typed.

- **The browser demo can drive a model that will not take function tools on Chat
  Completions.** 0.8 taught the Python provider to read that 400, move the whole run to the
  Responses API and stay there. The demo at <https://www.quackd.org/simulator> has an OpenAI
  client of its own — the page calls the vendor straight from the browser and there is no
  server here to proxy it through — and that client never learned any of it. So picking
  `gpt-6-astra` in the page ended the run at the first call with the raw vendor message in
  the transcript, on a demo whose whole job is to take one step in front of whoever clicked
  it. The browser now does what Python does: Chat Completions first, and on the 400 that
  names both function tools and Responses, the rest of the run goes to `/v1/responses` with
  that API's shapes — flat tools, the system prompt as `instructions`, and each replayed
  turn as `function_call` and `function_call_output` items keyed by `call_id`. It matches on
  what the API said rather than on a model name, so an unrelated 400 still reaches the
  transcript intact and never moves the run. Both halves are now tested against the same
  quoted 400, the browser's under `node` with a stubbed `fetch` rather than by reading its
  source. A guard runs the two predicates over the same seven vendor messages and fails if
  they ever disagree, which reading them for the right words could not do: that passes
  happily on an `and` quietly turned into an `or`, and an `or` would move a run on any 400
  that said `responses`. The Chat Completions path the rewrite lifted out and re-keyed is
  still every run on a model the catalogue does not mark Responses, and every local server, and it
  now has the guard it never had.

- **Gemini 3 as the pilot.** Two things stood between the provider and a current Gemini
  model, found by pointing a duck at `gemini-3.5-flash`. The schema cleaner did not strip
  `exclusiveMinimum` / `exclusiveMaximum` — pydantic writes `gt=0` that way, and `move` and
  `go_to` are core verbs that both do, so this was every robot that walks or drives, six of the
  seven, rather than some of them; the arm, which provides neither verb, never sent a schema
  the validator could refuse — and
  google-genai 2.x validates the declaration and refuses the keyword; the executor still
  enforces the bound on the way in. And Gemini 3 signs each function call with a
  `thought_signature` the
  next turn must hand back on that same call, or it is refused with a 400; `ToolCall` carries
  it as base64 text (`signature`, empty for every other provider) and `render_contents` puts
  the bytes back on the part. Both are on the default path: the catalogue's first Gemini entry
  is a Gemini 3 model. Verified with a two-step run to success on `gemini-3.5-flash`
  with thought summaries on. The first of the two is older than Gemini 3 and wider than it:
  google-genai has validated function declarations since 2.x, so the schema bug refused every
  verb call on every one of those six robots for every Gemini model, and it sat there because 0.8.0's own
  "non-Anthropic default model IDs are unverified" bullet was literally true — Gemini's default
  had never been sent a request from here. A `--provider` nobody runs is a `--provider` nobody
  finds the bugs in. Thanks to [@Bayway](https://github.com/Bayway) (#13), who ran it.

### Known limitations

- **Nothing has run on hardware, on any of the seven adapters.** 0.8 said eight, and the
  difference is a removal rather than a robot. The arm had the most attention of any body in
  this release, a camera, a checklist, a lookout task and an audit that stopped it taking the
  arm's word for four things, and no SO-101 was driven for any of it. The rosbridge base is the
  one body with neither a lookout task nor a checklist, so it goes on blocks first. The
  Microduck's own hardware ships around Christmas 2026.

- **The trained gait does not reliably do 10 of 10 seeds.** Seed 4 is the one that goes, and
  the entry under Fixed says on which machines, with which physics versions, and what the
  shipped threshold actually asks for. Nobody has worked out why that seed is marginal. The
  other nine walk, and the cartoon's own sweeps are unaffected and still gate every push.

- **No real model has ever refused a task on feasibility grounds here.** The gate, the
  `infeasible` outcome, the hint and the flock handoff are exercised with scripted verdicts and
  an in-process MCP client. Whether a frontier model reaches for `uncertain` when it should, or
  for `infeasible` too readily, is unknown. There is one `live_llm` test waiting for a key. Over
  MCP an `uncertain` verdict stays pending and the model is told to ask the person it is
  chatting with, which has never been watched happening either.

- **No pilot flock has been driven by a real model, or by a real robot.** `flock-hello` runs a
  duck and an arm on the scripted rule, which cannot reason about a datasheet, so what a real
  model does with the `Your flock` section and with `tell` is unknown. N simulated pilots are
  also N separate worlds: nothing checks one member's claimed success against ground truth the
  way the coordinator's shared arena does, a seed does not make a pilot flock reproducible, and
  it costs one budget and one model call per member per turn. No asset shows a pilot flock,
  because N members are N worlds and there is no single GIF to record.

- **The coordinator flock still knows one adapter.** `flock/runner.py` builds Microducks, so
  `flock.roles.<role>.needs`, which validates and is matched against the datasheet a bid
  carries, has nothing heterogeneous to be exercised against end to end. The latent ordering bug
  [ADR-0020](docs/adr/0020-heterogeneous-flocks.md) records is inherited with it: the
  coordinator judges eligibility before members report their vocabulary.

- **Two of the eleven cloud vendors have answered a real request.** OpenAI from the machine that
  wrote this and Gemini from a contributor's. The other nine are built from their own published
  documentation and held by tests that stub the SDK client, which proves the base URL, the key
  variable, the extra and the default id, and proves nothing about whether the vendor answers.
  The catalogue is a hand-read snapshot of 2026-09-12 rather than a live list, so a vendor that
  renames or retires an id between releases leaves this build refusing something real.

- **The browser demo is not at parity with the backend, and nobody has watched it finish.**
  Seven of the manifest's fifteen verbs and none of the three composites, a contract and a
  prompt of its own, an arena that is not upstream's scene, geometric perception, no hash check
  on what it fetches, and a seed that means the same distributions rather than the same layout.
  It has no datasheet and no feasibility gate, so a page asked to carry something will try. The
  page has been booted, a held `W` walks the duck and a real model has answered once, all on the
  machine that wrote it. A run reaching its own end, the recording, the switch thrown mid-run and
  any other browser or machine are still unwatched. The model dropdown and the Responses
  fallback this release adds are tested under `node` with a stubbed `fetch`, which is not a
  browser.

- **Both nightly jobs are red, and always have been.** `microduck assets` fails the trained-gait
  sweep described above and a fall-recovery heading check that passes on the developer's laptop
  and not on the runner. `toddlerbot contract` fails a `stand` that never reports settling and
  loses the deadman test's connection. Neither gates anything by construction, which their own
  headers say in as many words, and `ci` is the job that gates and is green on this tag. Nothing
  in this release touched those code paths. They are three separate things to go and look at.

- **The physics backend is still measured on one machine**, `follow-me` is cartoon only because
  nobody stands in the physics arena, a coordinator flock member must be `sim2d` so the physics
  backend does not fly in one, `GAIT_FLOOR_VY` was never measured but assumed equal to the
  training maximum, so a lateral request that clears the dead zone is sent at full scale while
  one below it is dropped entirely, and `--live` on macOS needs `mjpython` because MuJoCo's
  viewer must own the main thread.

- **Outside this repository.** The social preview GitHub serves is a version behind, and there
  is no API that would let a commit here replace it. quackd-web carries its own copies of the
  two mark files this release re-cut, so the chinless duck is still live there, and its landing
  copy was written around 0.5.

## [0.8.0] — 2026-09-09

Two things, mainly. A run narrates itself now: the system prompt once, then per turn the
observation the model was given, what it reasoned, the verb it chose, every executor gate that
fired, every intent that actually reached the robot and what came back — on the terminal as it
happens, in a `trace` list on every MCP result, and replayable afterwards with `quackd trace`.
And there is a physics simulator, `--robot microduck:mujoco`, which is MuJoCo with upstream's
own Microduck in it: its meshes, its trained walking policy, its 50 Hz loop and now its scene,
with quackd supplying a twist and a head pose and writing no gait at all. A third strand landed
in the last day: the browser demo is live at <https://www.quackd.org/simulator>, rebuilt in the
quackd-web design language, with the keyboard and a model driving one duck at the same time and
neither taking turns with the other.

Still nothing has run on a robot, on any of the eight adapters, and the Microduck's own hardware
ships Christmas 2026. The physics is a simulator walking on upstream's policy, not a duck, and
nobody is in either 3D arena any more, which makes `follow-me` a cartoon-only starter. The
browser demo has been booted in a browser, a held `W` walks the duck, a real model has been
handed it, and a key has taken the duck back out of a live run — all of it on the machine that
wrote it. What nobody has watched there is a run through to its own end, or the recording.
Known limitations, below, says what each of those leaves open.

### Added

- **quackd narrates itself now, on both surfaces, on by default.** Ask it to walk in a circle and
  the terminal used to print a header, an outcome and a run directory. It now shows the whole
  conversation as it happens: the system prompt once, then per turn the observation the model was
  given, what it reasoned, the tool it chose with its parameters, the tokens and the latency,
  every executor gate that fired and why, every intent that actually went to the robot, and what
  came back. A steering loop's burst becomes one line with real ranges (`-> move x26 over 0.5 s
  (vx 0.1..0.2, wz -0.01..0.88)`), because `go_to` recomputes its twist every 100 ms and a line
  per intent would be two hundred lines. Over MCP, where the pilot is the client and its reasoning
  is not quackd's to see, every call that reaches an executor comes back with a `trace` list of
  the same lines, capped at thirty, with the uncapped version on the server's stderr. `--no-trace`
  or `QUACKD_TRACE=0` turns the views off, and `QUACKD_TRACE_THINKING` caps how much reasoning
  each turn prints — 2000 characters by default, `all` for everything, `0` for none — because on a
  thinking model the reasoning is otherwise longer than everything else on the screen put
  together. Every knob this release adds is in `.env.example` — the tracing
  ones, the physics ones, and `QUACKD_OPENAI_API` and `QUACKD_OPENAI_REASONING_EFFORT`, which
  the Responses entry under Fixed explains — bar three that only the test suite reads:
  `QUACKD_REQUIRE_GL`, `QUACKD_LIVE_LLM` and `QUACKD_LIVE_LLM_MODEL`. The transcript is unaffected either
  way: it is the record, and it now carries `llm_request`, `verb_start`, `gate`, `intent`,
  `verb_end` and `note` alongside the kinds it always had ([ADR-0029](docs/adr/0029-tracing.md),
  [docs/architecture.md](docs/architecture.md#trace)).
- **A flock narrates itself too, one robot per column.** `docs/flock.md` promised a per robot
  transcript and the file held nothing but frames: a member built its executor with no tracer,
  so `--trace` on a flock did nothing at all. Each member now records into its own
  `ducks/<name>/transcript.jsonl` exactly as a solo run records into its own, and the terminal
  gives each one a view with its name on every line, so three robots moving at once are three
  readable columns rather than one interleaving. The coordinator's decisions and the planner's
  one model call print under `flock`, in the same words the GIF captions use. `flock.jsonl` is
  unchanged, and so is `--verbose` ([ADR-0029](docs/adr/0029-tracing.md) amended,
  [docs/flock.md](docs/flock.md)).
- **`quackd trace` replays a finished run.** The transcript held every line the console
  printed and there was no way to read it back except with `jq`. With no argument it replays
  the newest run under `runs/`, and it takes a run name, a timestamp prefix, a duck name or a
  transcript file. `--from-step N` starts part way in, `--no-prompt` drops the system prompt,
  `--thinking all|N` sets how much reasoning to show, and `--frames` adds a line per camera
  frame. It prints on stdout, because a replay is what you pipe. A flock run replays every
  member under its own name.
- **A switch for the system prompt.** It is forty to seventy lines — measured across the
  fourteen shipped ducks on `microduck:sim2d`, and longer again with memory lines or on
  `microduck:mujoco`, where ten assumptions and a longer note about the arena join it — worth
  reading once and tiresome on the fiftieth run of an afternoon. `--no-trace-prompt` or
  `QUACKD_TRACE_PROMPT=0` drops it and keeps everything else; the transcript has it either way.
- **A long burst is shown as it happens.** A twenty second `go_to` used to print nothing until
  it ended, because a burst of intents is coalesced into one line and the line was only written
  when something else happened. A burst still going after two seconds is now flushed as it
  stands and the next line continues it.
- **The robot's own clock, beside the wall clock.** On a simulator a verb that took 4.3
  seconds of robot time and 0.1 seconds of yours now says both, and intents carry the robot's
  clock too. On hardware there is one clock and one number, as before.
- **The scripted pilot says which rule it followed.** `--provider fake` returns no reasoning,
  because there is no model, but it now reports what it saw, how the last verb ended and the
  verb that fell out of that, marked `[scripted]` so it can never be mistaken for a model's own
  words. A run with no API key shows the shape of a real one.
- **A count of what a broken console dropped.** An observer that raises never ends a run, which
  meant a console that raised on every event produced a silent trace and no sign of it.
  `trace_dropped` is in `summary.json`, in the `run_end` record and on the last line of the
  run when it is not zero.
- **The providers return what the model thought.** `ProviderTurn.thinking`, filled from
  Anthropic's thinking blocks, from `reasoning_content` or `reasoning` on an OpenAI-compatible
  server, from Gemini's thought parts, and from an inline `<think>` block a local server did
  not separate. Every one degrades on its own: a model that rejects the request parameter gets
  one retry without it and the run carries on with no thinking text. Reasoning token counts
  ride along in `usage` where the vendor reports them.
- **A physics simulator, with the Microduck's own legs in it** (`--robot microduck:mujoco`,
  needs `quackd[mujoco]`). The cartoon in `sim2d` has always said what it is: it tests the
  agent loop, not the robot, and it will never tell you whether a gait works. `sim3d` is
  MuJoCo, and the duck in it is upstream's: `robot_walk.xml` and its 38 meshes from
  `microduck_rl` at a pinned commit, the `alpha_walking` and `alpha_stand` ONNX policies from
  the Hugging Face Hub at a pinned revision, and upstream's own 50 Hz loop around them. quackd
  supplies a twist and a head pose, which is what a gamepad supplies on the real robot, and
  writes no gait at all. `find-and-kick` succeeds on 10 of 10 seeds with the scripted pilot
  while the duck walks on its trained policy, ground truth checked, and on the same ten seeds
  with the kinematic stand-in. Both are named tests, and both run in CI on the two schedules
  under Changed. Design: `docs/adr/0030-mujoco-physics-backend.md`.
- **Nothing of upstream's is shipped.** The 3D model files are CC BY-NC-SA, so the first run
  downloads them into `~/.quackd/cache`, checks every file against the sha256 it was read at,
  and writes the licence notice beside them. `QUACKD_MICRODUCK_ASSETS` points at your own
  checkout instead, and `QUACKD_MUJOCO_BODY=puppet` runs a kinematic stand-in that needs no
  download and is the body the tests build. `QUACKD_CACHE_DIR` puts the cache somewhere other
  than `~/.quackd`.
- **The gait floor is in the open.** Under the model's own actuators the walking policy does
  not step below about 0.22 m/s or 1.0 rad/s and achieves about 0.42 of what it is asked,
  while `move` defaults to 0.15 m/s. A twist that would produce nothing is scaled up bodily,
  keeping the ratio between its axes so an arc stays an arc; one below a third of the floor is
  dropped rather than amplified into a lurch; and the floor, what was asked and what was sent
  are all in the state, in the system prompt and in `extras.assumptions`. Four skills are
  named stand-ins there too: `kick` and `grab` use the cartoon's contact rules, `sit` is
  refused, and a fall is recovered by standing the model up, because upstream's episodic
  policies did nothing from a standing pose and it ships no get-up policy.
- **You can watch the physics, and record it.** `--gif` on `microduck:mujoco` writes the same
  two-pane recording the cartoon writes, except both panes are MuJoCo renders and the recorder
  samples half as often, because drawing a physics world is what costs. `--gif-size` stopped
  being a sim2d-only flag — its help says "Simulators" now, and it is bounded 64 to 1024 against
  the offscreen buffer the scene actually allocates. `--live` opens MuJoCo's own passive viewer
  rather than the pygame window: orbit, pan and the contact overlay, kept in step with the
  robot's clock, its close button raising the same interrupt. On macOS that viewer must own the
  main thread, so it fails with a sentence telling you to run the same command under `mjpython`
  instead of `python`.
- **A real model driving the physics simulator, opt-in twice.** This is the first time an actual
  LLM has been put in front of this arena at all, which is why it sits here rather than among the
  verification work under Changed. Everything else in the suite fakes the brain: `FakeProvider`
  scripts the verbs and proves the loop, the executor and the world, and nothing showed that a
  model handed this arena gets anywhere in it. `tests/test_llm_in_the_simulator.py` runs one
  against MuJoCo: `hello-world` end to end, and a goal asking for the person who is no longer
  there — which is the one worth the tokens, because without the arena note in the prompt the
  honest reading of `search_scan(target="person")` is to keep scanning, and the run spends its
  whole budget on a lap of an empty room. The live tests cost money and need the network, so
  they are opt-in twice, by a `live_llm` marker and `QUACKD_LIVE_LLM=1`; CI sets neither and a
  bare `pytest` runs neither. They read `.env` the way the CLI does, and `QUACKD_LIVE_LLM_MODEL`
  is what points them at a second model, which is how both of the OpenAI API paths under Fixed
  get exercised. The third test, that the prompt tells the model the arena is empty, needs no key
  and no network — and it is gated behind the `mujoco` extra and named in no CI job, so today it
  runs on a developer's machine and nowhere else. None of this makes a real model part of a
  build: no job in this repository sends a request to a provider, and the release is still tested
  with a scripted pilot.
- **`walk in a square` and `walk in a circle` need no API key, on the command line.** The
  scripted pilot learned two shapes, and both close the loop on the pose the robot reports
  rather than on a stopwatch, so a body that delivers half of what it was asked still walks the
  shape. That is also the correction a model makes, which is the point of them being here.
- **A browser demo, so trying quackd costs nobody an install** (`web/`, a static page with no
  build step, live at `www.quackd.org/simulator`). quackd-web, the separate project that owns
  that domain, builds it by fetching this directory at a pinned commit, and `vercel.json` here
  rewrites `/simulator/*` so this project also answers on the mount if it is ever deployed on
  its own. Locally it is `python web/serve.py`, then <http://localhost:8000/simulator/> — a
  stdlib server that mounts the directory the way the deploy does. `python -m http.server
  --directory web` no longer serves it: every local reference in `index.html` is absolute under
  the mount, so a root server hands over the HTML and 404s the stylesheet and the script.
- **The same physics and the same two policies, in a tab.** MuJoCo's official WebAssembly build
  and onnxruntime-web run `alpha_walking` and `alpha_stand`, with seven of the verbs, a contract
  of its own and the one-tool-per-turn loop in six modules of plain JavaScript. Bring your own
  key for Anthropic, OpenAI or Gemini, or point it at Ollama and keep everything on your
  machine. A run can be recorded from the canvas and shared.
- **Both ways of driving are live at once, and neither takes turns with the other.** The
  keyboard writes the twist the hardware actually takes — `W`/`S` walk, `A`/`D` turn, `Shift`
  strafes, `Q`/`E` look, `G` centres the head, `Space` stops, `K` kicks, `R` stands it up —
  while the box above it hands the same robot to a model. A drive key pressed during a run
  takes the duck back at once, aborts the run and the request in flight with it, and leaves the
  key that did it in the transcript; `O` and the camera keys read without interrupting, and
  `Esc` hands the keyboard back to the arena from wherever focus is. The switch keeps the one
  job that is the demo's whole argument — whether anything here reads English — and no longer
  decides whether you may drive at all.
- **The page wears quackd's own mark, and points back at what it is a demo of.** It is in the
  quackd-web design language rather than an emoji and a default stylesheet. The brand lockup is
  the way home, and "What is this?" answers with the product page rather than a build guide.
  Both are absolute `https://www.quackd.org/` URLs rather than a bare `/`, because this file is
  fetched into another project's build and `web/serve.py` redirects the local root straight back
  to `/simulator/`.
- **The demo is one screen, and its bays have names.** A first-time visitor landed, saw the
  arena, typed a sentence, pressed Run and got an error about a credential the page had never
  shown them: the key box and the key map were both below the fold, and no wording fixes that.
  The switch and the key box are a full-width band across the top now, the arena takes what is
  left of the viewport rather than a fixed fraction of it, and the keycaps run down its right
  side, so both ways of driving are on screen from the first frame. Checked by resizing one
  browser on the machine that wrote it, at 1920x1080, 1440x900, 1366x768, 1366x641 and
  1280x720, with no horizontal scroll down to 360. The numbered bays 01-04 are headings that
  say what they are: `Your API key`, `Command the duck`, `Drive the duck` and `Transcript`.

### Changed

- **Claude runs now ask for a thinking display.** Adaptive thinking is the model's default on
  Opus 5, but the blocks come back with empty text unless the request says
  `display: "summarized"`, so "what it thought" would have been a blank line on every turn.
  Display changes what is shown, never what is thought or billed, and the raw chain of thought
  is never returned by any model. `QUACKD_THINKING_DISPLAY=omitted` opts out.
- **Gemini's thought parts no longer land in the answer.** They were appended to `text`
  regardless, so with thoughts on they would have been replayed to the model next turn as
  things it had said. `QUACKD_GEMINI_THOUGHTS=0` stops quackd asking for them at all.
- **The transcript flushes on events, not on every intent.** The flush was a syscall on the
  event loop between two deadman resends of a steering verb. Loss on a hard kill is now bounded
  by the text buffer and ends at every `verb_end`.
- **A verb ended by another layer keeps its own word.** A flock role change preempts an
  in-flight composite verb, and the executor had no branch for that, so every handover in a
  three robot run printed a red `ERROR`, which the docs define as a bug in quackd. It reads
  `PREEMPTED` now, in yellow.
- **`--verbose` is the compact view, not a second one.** With the trace on, the executor's own
  one-line-per-verb log would say every verb twice, so it stands down: `--verbose` is what you
  get with `--no-trace`, and on the MCP server the executor's log drops to DEBUG. Nothing lost
  its `log` callback, which the flock's member records and several tests read.
- **A verb that starts always ends.** `Executor.run_verb` now emits exactly one start and one
  end with an outcome, through a `finally`. Seven exits used to leave nothing behind, among
  them the two that matter most: a verb cancelled mid-flight by a kill switch or a heartbeat
  failure, and the repeat-failure abort.
- **The arena is upstream's own scene, and the head camera is the one place it cannot go.**
  `sim3d` no longer draws a world quackd invented. It builds upstream's: the blue-grey checker
  with edge marks, the gradient skybox, the haze, the headlight at ambient 0.3 and the
  directional light overhead, and the viewer's own azimuth 160 and elevation -20, all taken from
  the `scene*.xml` wrappers in `microduck_rl`, so a duck here stands where a duck there stands.
  Shadows are upstream's default and the largest render cost there is, so they are a switch,
  `QUACKD_MUJOCO_SHADOWS`, rather than a constant. The head camera is the exception, for a
  measured reason under Fixed: it keeps the same checker with the colour taken out, in a geom
  group hidden from every human view including the live viewer, and no skybox. That split is a
  stand-in like the others, so it is a ninth line in `extras.assumptions` and in
  [ADR-0030](docs/adr/0030-mujoco-physics-backend.md), and it is worth saying plainly that the
  model's camera does not see what any recording of the same run shows. The README hero was
  re-recorded in the new scene at the same seed, and again once the arena emptied so that the
  picture shows the world this release ships: 2.72 m walked, the square closed 0.14 m from where
  it started, both times. `MicroduckBody` draws nothing from the world's shared generator, so
  removing the person moved neither number.
- **Nobody is in the 3D arena, and the cartoon is the only simulator with a person in it.** The
  marker is gone from both 3D worlds — the MuJoCo one in `quackd/sim3d/` and the browser demo —
  while `sim2d` keeps its own, so this is the single place the two arenas differ. What it costs
  is a starter: `follow-me` is a task about following somebody and cannot succeed on
  `microduck:mujoco`, and `patrol-and-quack` still runs there with its person clause gone
  vacuous, leaving three walk legs. Both `.duck` files are untouched, because their hashes are
  pinned in the goldens and they are right for the cartoon they were written for, so nothing
  stops you pointing `follow-me` at the physics backend and nothing pretends it will get
  anywhere; the README and `docs/faq.md` say which starter is now cartoon-only. What it does not
  cost is the seed: the cartoon draws its person from the RNG after the duck and the ball, so
  dropping that draw leaves every seeded duck and ball position bit-identical between the two
  simulators, checked at all ten seeds, and the parity test was not loosened for it: it still
  holds both arenas to the same duck pose and the same ball, the ball to the millimetre it always
  allowed. What did move is everything downstream of that draw on the shared generator — the
  puppet's gait noise, the kick skew, the scoop coin-flip — so the ten-seed `find-and-kick`
  sweep was re-run under `QUACKD_STRICT_SEEDS=1` on both bodies and still passes 10 of 10. The
  model is told outright rather than left to infer it: the mujoco branch of the system prompt
  says nobody is in the arena and that a `person` detection is scenery misread, and the demo's
  own contract says the same. `search_scan` still offers `person` as a target and the head
  camera's detector still carries the person hue band, because one verb registry and one
  detector serve the cartoon, YOLO on a real camera and this, which is why the colourless floor
  under Fixed stays and why its guard got stricter. `people` has left the 3D snapshot and
  `extras`, the `person` flag has left the world's constructor, and a tenth line joins
  `extras.assumptions` ([ADR-0030](docs/adr/0030-mujoco-physics-backend.md) and
  [docs/faq.md](docs/faq.md) amended).
- **`quackd doctor` reports the physics backend's upstream too.** Every run of the command now
  ends with a `microduck_rl` table: the UNVERIFIED count with each one's note, the `microduck_rl`
  pin and the policy revision with the date they were read at, the VERIFIED count, and the line
  that the model and the policies are fetched at run time and never shipped. It is the one place
  that limit is stated in the tool rather than in a document — and it is a different limit from
  the hardware adapters', because this upstream is measured here, on one machine, rather than
  never run at all. `mujoco` joins doctor's list of optional extras, and the `microduck` row in
  `list-adapters` gained the `mujoco` backend with the extra named in its status line.
- **A documentation pass over everything 0.7.0 shipped, for fewer words rather than more.** The
  README lost about a fifth of its length: the table that listed all eight bodies a third time is
  gone, the bullet list that repeated the status table is gone, the "since 0.4" release narration
  is gone, and the tagline no longer names four bodies out of eight. Every count and claim in the
  living docs was then read against the code, twice, and the second read is where the value was.
  What it found that would have stopped a reader dead — four commands that could not run and two
  safety claims printed the wrong way round — is under Fixed, with the other things that were
  broken rather than merely wordy.
- **The rest of what reading found.** The README said the AlohaMini's arm verbs appear only with
  quackd's host wrapper, when they exist and refuse without it, which is the distinction the
  README's own Open Duck row exists to make. The Open Duck's camera capability comes from
  `--camera-url` and not from `duck_config.json`, so the checklist's abort note stopped an owner
  at a correctly built duck; `FALL_SIGNAL` described an IMU read the daemon does not do; the
  sound row said the bridge resolves a mood to a file when it presses the pad's random-sound
  button. `bridge/open_duck/README.md` was missing three flags from a table that claims to list
  them all, and still said a velocity ceiling above upstream's is "applied on top" when 0.7
  refuses to start. The ToddlerBot page called itself the second body whose robot side quackd
  ships (it is the third) and credited the daemon with a walk clamp that quackd applies. The
  ToddlerBot checklist never mentioned that the daemon binds loopback, so every `<host>` in it
  would have been refused. `docs/memory.md` and `CONTRIBUTING.md` said every solo starter asks
  for a `remember` when the three 0.7 lookouts and `hello-world` do not, `docs/architecture.md`
  gave the Open Duck a stand-up policy it does not have, `docs/lan.md` documented a `--json`
  flag that `list-verbs` never had, the FAQ named only the duck's token variable for two
  daemons, `docs/duck-spec.md` promised a `validate` warning nothing emits and three flock
  ranges that included a zero the schema rejects, `docs/safety.md` put the consecutive-failure
  abort at the wrong stage of the executor, `docs/reading-robots.md` counted three upstreams
  that de-torque on disconnect when there are four, and `SECURITY.md` claimed a version
  handshake and a `doctor` read-out that the AlohaMini wrapper and `doctor` do not do. The rest
  is release-history narration deleted from pages that describe quackd as it is now.
- **A second reading, and the three claims a user would have acted on.** `docs/mcp.md` told an
  operator that after a transport failure every later call to that robot is refused. `stop` is
  exempt and still runs, which the refusal text itself says, so the page took the brake away at
  exactly the moment a pilot reaches for it. `docs/flock.md` offered a duck falling in
  `microduck:mujoco` as the way to exercise fall handling, when both the CLI and the runner
  refuse a flock member that is not `sim2d`, so the route landed the reader on an error naming
  that very page — a flock is still simulator-only and still cartoon-only, and the physics
  backend does not fly in one. And the README's status table said the trained-gait sweep runs on
  a developer's machine when it runs nightly in CI, which undersold what is actually verified.
  Fourteen smaller ones behind them, across eleven files: the loop's fifth outcome (`error`)
  missing from two documents; `trace` still absent from `docs/architecture.md`'s command list, an
  edit written during the tracing work that silently did nothing because the script asserted on
  the aggregate rather than per replacement; the README intro promising the cartoon to "the other
  seven bodies" when the LeRobot arm, a rosbridge base and the XLeRobot ship a mock and nothing
  else; the replay row never saying stdout while the configuration row two sections down says the
  trace is on stderr, which points a reader at `2>` and an empty file; and a prose list of the
  transcript's kinds above `docs/architecture.md`'s own table of them, drifted by six kinds.
- **CI ends a hung test run in minutes, with every thread's stack.** A 20 minute job timeout,
  pytest's own `faulthandler_timeout` at 300 s, and an exit watchdog in `tests/conftest.py`
  that dumps every thread and forces the exit if the interpreter has not gone two minutes after
  pytest is done, flushing first so the failure report survives. The first hang it caught had
  run for six hours three times and named nothing; with it, the same hang failed in five
  minutes with the frame in the log.
- **CI runs the physics backend, and the browser demo has a floor under it.** A `physics` job
  installs `quackd[mujoco]` and runs the two sim3d modules against a software rasteriser, so
  36 tests that skipped on all six runners now execute somewhere. It sets
  `QUACKD_REQUIRE_GL=1`, because a job whose purpose is to run tests that skip has to fail
  when they skip. A nightly `microduck-assets` job fetches upstream's real model the way a
  user's first run does, into a runner that is then destroyed, and runs the trained-gait
  sweep; it caches nothing, because `docs/licenses.md` says no CI fixture carries a byte of
  those meshes. `tests/test_web.py` checks the browser demo without a browser: the ids the
  script looks up, the rule that was hiding nothing, static CDN imports, key storage, the pins
  and gait numbers shared with Python, and `node --check` on each module.
- **The demo's deployment moved off GitHub Pages, and a merge to `web/` does not reach a visitor
  by itself.** `.github/workflows/pages.yml` is deleted. It was the one thing on this repository
  that stayed permanently red, failing at configure-pages on a repository where Pages was never
  enabled, and it was never the deployment this project uses: the address it promised was a
  github.io one that would have 404ed. `vercel.json` replaces it, with the install and the build
  stubbed out because there is nothing to build — every dependency in that directory is a CDN URL
  the page fetches at run time. And because quackd-web fetches this directory at build time, a
  push to `main` touching `web/` pings a deploy hook
  (`.github/workflows/refresh-the-simulator.yml`) asking that site to rebuild. It is inert until
  `VERCEL_DEPLOY_HOOK` exists — a missing secret says so and exits zero, rather than showing a
  fork a red mark for a deployment nobody configured — it runs only on this repository, and
  `curl --fail` is deliberate, so a hook that has quietly died cannot look like a working one.
  That last part is the one a contributor needs: for this directory, merging is not shipping.
- **The 10 of 10 claim has a test.** `test_find_and_kick_on_the_real_duck` runs the same ten
  seeds on upstream's trained gait rather than the stand-in, and asserts the body really is
  the trained one first, because a silent fall back to the puppet passing it is the point.
  Measured: 10 of 10, so the claim was true and simply unbacked. `assets.py` has eighteen
  tests where it had none, including the tarball allowlist `SECURITY.md` makes a claim about,
  and the gait floor moved into a module that imports no `mujoco`, so the arithmetic deciding
  whether a duck moves or only reports moving is checked on every runner.

### Fixed

- **The physics backend crashed, hung and misreported, on paths its tests never reached.**
  Reading the state of a duck lying on its back raised a `ValueError` out of every
  `get_state()`, because the tilt's `acos` was clamped on one side only: the reading a fall is
  exactly when a pilot needs it. A world that could not step killed the clock advancer, which
  is a task nobody awaits, so every parked verb waited on a future that would never resolve
  and the reason was collected by the garbage collector; the failure is now raised into every
  sleeper and out of the heartbeat. A subscription running beside a verb deadlocked both,
  because the clock keeps one parked waiter per participant and the second silently overwrote
  the first; that collision is an error naming the id now. A non-finite twist walked through
  `np.clip` and the gait floor's dead zone to arrive at the servos, and MuJoCo answers a
  non-finite state by resetting the world rather than raising, so a run could carry on
  reporting poses from a world that had quietly restarted. And a headless machine got an
  OpenGL traceback where `docs/faq.md` promises a sentence naming `MUJOCO_GL`.
- **The state said `walk` while the duck stood still.** `policy` was read from the twist quackd
  commanded rather than from what the body did with it, and a body is free to decline one:
  below the gait floor it sends nothing and stands. That is the exact failure the gait floor
  exists to prevent, and it reached the model while the body's own truthful `extras["policy"]`
  did not. `head_yaw` reported the angle a `look` asked for, on a body whose neck is a servo
  that lags, while bearings already came from the achieved pose.
- **`stand_up` stood the duck up facing backwards.** The heading came from the trunk
  quaternion, which is arbitrary at the gimbal degeneracy where a face-down duck lies:
  measured on the real model, a duck facing +x that goes onto its nose reads as yaw 3.14. It
  now comes from the trunk's own forward axis, the duck is pushed clear of anything it landed
  on, and the twist that put it down is cleared rather than resumed.
- **The duck reported a person standing in front of it, in every single frame.** Upstream's floor
  and the person marker that was then in the arena could not share a camera. Measured off a
  rendered frame, the checker sits at hue 105 with saturation to 185 and value to 229 and the
  marker at hue 114, saturation 185, value 197: they overlapped on all three channels, so no
  threshold separated them, and the skybox does the same thing above an 8 cm wall. With the scene
  as upstream ships it the colour detector reported somebody standing 0.12 m ahead in 48 frames
  out of 48, whichever way the duck faced. The head camera now looks at the same checker with the
  colour taken out, in a geom group hidden from every human view, and at no sky: phantoms went 48
  of 48 to 0 of 32 on the sweep that now guards it, and both seeded sweeps still pass 10 of 10.
  Nothing caught it before because the parity test only ever asserted about the ball; three tests
  do now, one sweeping four seeds by eight headings and counting every person reported at any
  range, where it used to excuse anything beyond a duck's own radius, one pinning which floor the
  camera collides with, and one holding this arena to having nobody in it. That the marker left
  the physics world in the same pass is why the two floors stay and why the guard could tighten:
  upstream's checker is still the blue the detector calls a person, that band still has to be
  there for the cartoon, and with nobody left to weigh a detection against, every person reported
  from here is scenery.
- **The stand-ins were told to the transcript and not to the model.** `extras.assumptions` is
  what a backend says quackd stands in for, and only `FakeProvider` ever read it: every real
  provider sends `obs.text`, built from `DuckState.summary()`, and neither had a branch for it.
  ADR-0030's claim that a transcript never implies more than happened was true of the
  transcript and false of the model. The list now goes into the system prompt in the robot's
  own words, and a pointer into `summary()`, because the MCP server has no system prompt at all
  and a Claude Desktop pilot reads tool results only.
- **An interrupted download left a cache the next run blamed upstream for.** `assets.py` wrote
  extracted files straight to their final path with no lock, so a Ctrl-C or a second terminal
  left a half-written model that the next run reported as a pin mismatch, asking the user to
  report that upstream had moved the archive. Files now land in a scratch directory, are
  verified there and renamed into place under a lock. `fetch` caught `OSError` only, so a
  truncated body and a captive portal's HTML both escaped as bare tracebacks; the cache
  variable never expanded a tilde, though `.env.example` suggests one; and the licence notice,
  the file that makes the licence claim true on disk, was written only on a fresh fetch.
- **A square was four legs and a hope.** `square_strategy` declared success on the leg count
  alone, and that outcome is what `docs/assets/hero3d.py` publishes the README hero on. It
  measures the distance back to where it started now and says it either way. Both shape
  strategies also sampled the heading once per move and accumulated it with `abs(wrap(...))`,
  so a long turn discarded whole revolutions.
- **The browser demo could not load, and a stale key could leave the machine.** A CSS rule kept
  the loading panel over the canvas for good, a blocked CDN or a machine without WebGL left a
  dead page with no message, any physics error stopped the loop silently and hung every verb,
  and Space stopped activating every button on the page. Every verb reported success, including
  a kick that missed; nothing validated a model's arguments, so `duration_s: 1e6` hung the tab
  and `duration_s: "soon"` reported success having done nothing; and `stand_up` restarted the
  whole episode, which made the time budget permanently negative. Switching the provider to a
  local server disabled the key field without clearing it, and a disabled input's value is
  still readable, so a key pasted for one vendor went out as a bearer token to whatever host
  was typed in the free-text box.
- **Two calls at once no longer report each other's work.** The per verb intent tally was one
  stack on the executor, so an MCP `quack` that overlapped a `move` reported the move's intents
  as its own and the move reported neither its resends nor its stop. Each verb now counts in a
  context variable, which asyncio copies into a task at creation, so a nested verb still rolls
  up into its parent and two concurrent calls never cross.
- **A cancelled call cancels its verb and sends a stop.** An MCP client that cancelled, or a
  second Ctrl-C, returned at once and left the legs moving with nothing to stop them. The verb
  is cancelled, a stop goes out, and the trace says `gate cancelled` so the record shows why.
- **A Ctrl-C at a y/N prompt is a denial, not a traceback.** The confirm gate recorded `asked`
  and never the answer, and a prompt that raised ended the run with an empty reason and no
  gate. It now records `allowed` or `denied`, and names the exception when the prompt itself
  failed.
- **A state read that fails still sends a stop**, and a record sink that raises can no longer
  stop the heartbeat from aborting the run.
- **An interrupted run ends as `aborted`.** A `KeyboardInterrupt` or a cancellation was
  recorded as "loop exited unexpectedly". The summary is written and the transcript closed
  whatever happens on the way out, and writing to a closed transcript is a no-op rather than an
  exception inside a task nobody awaits.
- **A local model no longer runs a verb it only contemplated.** An inline `<think>` block is
  split out before the JSON fallback reads the answer, so a tool call the model was reasoning
  about inside its thinking is not executed. An unterminated `<think>` is thinking to the end
  rather than the answer.
- **A model's own words cannot break the terminal.** Rich read `[/think]` in a reason or a verb
  description as markup and raised. Everything the model wrote prints as text.
- **A malformed provider response is a `ProviderError`.** An empty `choices` or a usage field
  that is a string reached the user as a traceback from inside the SDK.
- **The three thinking fallbacks match the errors they were written for.** A 400 about a
  replayed thinking block disabled thinking and retried the same request, and Gemini retried
  any error whose text happened to contain the word.
- **A model that will not take function tools on Chat Completions can still drive the robot.**
  Some reasoning models refuse function tools on `/v1/chat/completions` and say so in a 400 that
  offers two remedies, of which only one exists: `reasoning_effort="none"` comes back from
  `gpt-6-astra` as an unsupported value for that model, and the tools are refused at `low`,
  `medium`, `high` and `xhigh` alike and with no effort field at all. Probed directly,
  chat/completions with tools fails four ways there while `/v1/responses` with tools returns a
  clean `function_call`. Every verb quackd has is a function tool, so for such a model Chat
  Completions is not a degraded path, it is no path: before this, pointing quackd at one ended
  the run at the first call with a `ProviderError` and nothing but a different model to try. The
  provider now reads that 400, moves the whole run to the Responses API and stays there rather
  than paying a failed call every turn, and it matches on what the API said rather than on a
  model name, so an unrelated 400 still surfaces. The two APIs agree on almost no field name, so
  Responses gets its own renderer and parser beside the existing pair: tools go flat, the system
  prompt becomes `instructions`, history becomes `function_call` and `function_call_output` items
  keyed by `call_id` — the item `id` is a 400 — images are `input_image`, and the usage counts
  live under different names. `QUACKD_OPENAI_API=responses` starts there without waiting to be
  told; `QUACKD_OPENAI_REASONING_EFFORT` sets the effort on either API and is sent only when set,
  so `gpt-5` and every OpenAI-compatible server are untouched. Verified on the physics simulator
  end to end rather than at the first call: `gpt-6-astra` runs `hello-world` to
  `declare_success` and gives up correctly on the person who is no longer in the arena, with
  images and tool results flowing over Responses throughout.
- **The MCP server logs each call as one block when it ends**, so two calls at once are two
  readable blocks rather than an interleaving, and the heartbeat's own note and stop reach
  stderr the moment they happen instead of being attributed to whichever call was open. A
  session refusing calls because the link died now says so.
- **A renderer bug never turns a tool result into an internal error**, a failed write keeps the
  burst for the next flush, and a gate shows a parameter the model left unset, which on a dry
  run is exactly what you are checking.
- **A run that failed outside the safety layer said `loop exited unexpectedly`.** A bad key, a
  429, a dropped connection or a dead camera is not a `SafetyStop`, so nothing caught it, the
  summary recorded a default string, and the CLI printed a traceback. The call that failed is
  now in the transcript with its error and its latency, `run_end` says what happened, and the
  CLI answers in one line like every other failure.
- **Three documented commands could not run, all the same way.** `quackd list-verbs` takes only
  `--robot`, so the one line on `docs/adapters/lerobot.md` and on `docs/adapters/rosbridge.md`
  that reaches a real robot exits 2 on an unknown option, and step 7 of the ToddlerBot checklist
  did too. Worse than failing, that step could not have done its job even without the flag:
  `list-verbs` builds its table from the static description, so `move` is absent whether or not
  a walk checkpoint is staged. All three are `quackd doctor --robot X --address Y` now, which is
  the command that connects and reports what the robot itself said.
- **A fourth could not run, for a different reason.** The sixty-second block's
  `uvx --from "quackd[anthropic]" ... --robot microduck:mujoco` is the one line in the repository
  joining bring-your-own-key to the trained gait, which is the thing in this release most worth
  trying, and it installs the SDK and no physics: the extras are independent, so the backend
  refuses before anything happens. It asks for both now. The provider table below it had the same
  gap — every row ran the cartoon, because no row named a robot — and it now says how to put the
  same model on the physics simulator.
- **Two safety claims were the wrong way round.** `docs/adapters/xlerobot.md` told an owner that
  when a camera takes down the observation stream "the arms keep holding their last goal under
  torque, which is what you want". Upstream's host at the pin calls `get_observation()` outside
  its inner `try`, so that exception leaves the loop and reaches `finally: robot.disconnect()`,
  which is the torque-off path: the arms go limp and drop what they hold, and the watchdog never
  fires because the loop that checks it is gone. The hour-mark exit takes the same path, which
  the XLeRobot checklist now says at the step about the clock. And the README claimed a verb
  timeout aborts the run; the executor stops the robot and returns a failed result, which the
  model then sees.
- **`--dry-run` never showed the intents it promised.** `docs/safety.md` has always said it
  prints every intent a model would send; the dry-run branch logged a verb name, and only
  under `--verbose`. The trace now names the verb and the parameters it would have sent.
- **A failed ZeroMQ test could hold the interpreter's exit forever.** pyzmq's `Context.__del__`
  closes each surviving socket with its own linger, which defaults to forever, and a test that
  fails never reaches its `close()`, so one flaky assertion held three macOS CI jobs for six
  hours with the last line of dots still unflushed. Both ZeroMQ clients and both fakes set
  `LINGER` to 0 when a socket is created now, not only on the close path, so a context torn
  down by anything but the happy path never waits on a peer that is gone. The flaky test
  itself, `test_a_stale_reading_is_a_heartbeat_failure_not_a_reading`, used a 50 ms stale limit
  a loaded runner cannot keep and could read the fake's last observation as fresh; it uses the
  host's own window now and drains the socket once after the host stops, so the silence it
  asserts is silence.
- **Two SVGs shipped in every published package.** The sdist excluded `docs/assets/*.gif` and
  `*.png` under a comment saying no published artefact needs those images, and `contributors.svg`
  and `logo.svg` matched neither pattern, so both went out in every release so far. Only the GIF
  is licence-critical — it renders upstream's CC BY-NC-SA model, and the published package has to
  stay wholly Apache-2.0 — but that exclude is the rule keeping it out and nothing tested the
  rule. Every image under `docs/assets` now has to be matched by a pattern, with the reason in
  the failure message. The two scripts living beside those images, one of which builds the README
  hero, were outside ruff and mypy for the same reason and are under both now.
- **mypy on Python 3.12 rejected the ToddlerBot daemon's fake body.** The lock resolves numpy
  2.2 there, whose stubs infer a fixed one-dimensional shape from `np.full` and refuse the
  `asarray(...).copy()` stored into the same attribute, so it is annotated shape-free now. The
  release gate ran under 3.11 and never saw it, which is why the `v0.7.0` tag's own CI run is
  red on three jobs while the published files are unaffected.
- **The ToddlerBot's shutdown could torque off a robot that was still moving.** `settle()` gave
  the slew to the safe pose a fixed 8 seconds, but the slew is handed out one control tick at a
  time, so a joint 1.5 rad away needs 5 seconds of slew and closer to 9 of wall time. It ran
  out, logged `did not settle`, and `shutdown()` went on to disable torque on a standing
  humanoid, which is the fall the method exists to get in front of. The deadline is now sized
  to the distance the body actually has to cover, with a floor for one already there and a cap
  so a jammed joint is not waited on forever. Found because the test that covers this settles a
  full-scale slew inside a wall-clock budget, and a loaded macOS runner is slow enough to miss
  it; that test now uses the real default and a second one checks the rule arithmetically.

### Known limitations

- **Nothing has run on hardware, on any of the eight adapters.** Unchanged since 0.7.0, which
  lists each one and the lookout task to point at a body first. The Microduck's own hardware
  ships Christmas 2026. What the physics backend adds is a simulator, not a robot: `sim3d` walks
  on upstream's trained policy and matches upstream's own numbers, but a duck in MuJoCo is still
  a duck in MuJoCo.
- **The browser demo starts, the hand works, and the loop has now been watched once.** The page
  has been booted in a browser on the machine that wrote it, and a held `W` walks the duck. A
  real model has been handed the page and answered: `gpt-5` read the arena and returned
  `move(vx=0.26, duration_s=0.6)` about seven seconds in, one request, no page errors. `W` then
  took the duck back out of that live run, and the transcript named the key that did it and
  ended the run as aborted. Both were watched once, headless, on that same machine. Nobody has
  watched a run reach its own end, the recording, the switch thrown mid-run, or the page on any
  other browser, screen or machine. `tests/test_web.py` is the floor under that, and
  a floor is all it is: it runs without a browser, so it cannot watch anything render. The demo
  also loads exactly two learned policies, `alpha_walking` and `alpha_stand`; its kick is a
  scripted impulse in the cartoon's cone, not a policy; and it has no scripted pilot, so the
  pre-filled goal still needs a key or a local server before anything happens.
- **The physics backend's upstream is measured here, on one machine.** `quackd doctor` prints
  that table rather than leaving it in a document, because it is a different claim from the
  hardware adapters': not "never run" but "run once, by one person, on one laptop". Four edges
  come with it. The head camera deliberately sees a floor no human view shows, so what the model
  perceives and what the GIF records are not the same picture — and the GIF is one change behind
  besides: `docs/assets/hero3d.py` stopped drawing a person in this release and
  `quackd-on-off.gif` was not re-rendered after it, so the animation the README leads with still
  shows a blue marker in both of its top-down insets, in an arena this release says has nobody in
  it. Nobody is in the physics arena, so `follow-me` is a cartoon-only starter and
  `patrol-and-quack` on `microduck:mujoco` is a patrol with nobody to announce. A flock member
  must be `sim2d`, so the physics backend does not fly in a flock. And `--live` on macOS needs
  the command run under `mjpython`, because MuJoCo's viewer must own the main thread.
- **Non-Anthropic default model IDs are unverified.** Override with `QUACKD_MODEL`. One
  qualification this release earns and no more: `gpt-5`, the OpenAI default, drove whole runs on
  the physics simulator while the Responses path above was being written, on one machine on one
  afternoon. That is enough to know the ID answers and takes tools, and not enough to move it out
  of this bullet; the README's configuration table still lists all three as unverified. Gemini's
  default and Grok's have never been sent a request.

## [0.7.0] — 2026-09-07

A sixth, a seventh and an eighth robot, two hardware paths audited against upstream rather
than against themselves, and an abort that finally reaches the verb it is aborting. Two of the
new robots quackd drives without importing anything from them, because neither is an
installable package, and the third has no network API at all, so quackd ships the loop it runs
on. The Open Duck Mini's daemon and installer were read against a duck set up exactly the way
the docs instruct, and it could not start the bridge; if it could, it would have had no camera
verbs; and the operator's Ctrl-C would not have stopped it. The Microduck's transport was read
against an API that had moved on while nobody was looking. Still nothing has run on a robot;
what changed is that several things which could not have worked now can, and several claims
that were not true no longer are.

### Added

- **The XLeRobot: a dual-arm mobile manipulator on an IKEA cart, about $660 to build**
  (`--robot xlerobot:mock` or `xlerobot:zmq`). Two five-joint arms with grippers on a
  three-omniwheel base that really can drive sideways, so `move` here carries a `vy` that
  means something for the first time. It is also the first body quackd talks to **without
  importing anything from it**: XLeRobot is not a package — no PyPI entry, no `pyproject.toml`,
  and its documented install is copying files into an existing lerobot tree — but it already
  ships a ZeroMQ host, so quackd speaks that wire and its extra is `pyzmq` and nothing else.
  That keeps the 3.11 floor and works on Windows, unlike `quackd[lerobot]`. Design:
  `docs/adr/0026-xlerobot.md`, with the page at
  [docs/adapters/xlerobot.md](docs/adapters/xlerobot.md).
- **The whole wire format is exercised against a fake host over loopback**, on every CI
  platform. Upstream ships no test, no CI and no simulator that runs — its ManiSkill host
  imports `xlerobot_single`, which is defined nowhere in the repository — so the other end of
  the protocol is written from upstream's source at a pinned commit and quackd's real client is
  driven against it. That caught a bug no reading would have: `stop` rebuilt its hold from the
  latest observation, which is several cycles behind and carries no timestamp, so stopping
  would have commanded an arm back towards zero. A stop that moves an arm is the failure this
  project exists to prevent. `stop` now zeroes the wheels and leaves every arm goal exactly
  where it already was.
- **`xlerobot-lookout`**, the task to point at a real cart first: nothing in its allowlist
  moves a wheel or an arm. It is the thinnest starter quackd ships, and honestly so — without
  a head to turn and without a voice, a task that moves nothing can only look and report.
- **The AlohaMini: two arms on a motorised lift, on a wheeled base** (`--robot alohamini:mock`,
  `sim2d` or `zmq`). quackd's second bimanual body and its first with a vertical axis. Like the
  XLeRobot it is reached by speaking its ZeroMQ host protocol rather than importing it, and for
  a stronger reason: upstream is a fork of LeRobot that *calls itself* `lerobot`, is not on
  PyPI, and installs only from a large git clone on Python 3.12 with torch. Speaking the wire is
  also more correct than importing would have been, because upstream's own client throws away
  the camera list the host sends and its robot-model default disagrees with the host's, with
  nothing cross-checking either. quackd reads both off the wire, so neither can be silently
  wrong. Design: `docs/adr/0027-alohamini.md`, with the page at
  [docs/adapters/alohamini.md](docs/adapters/alohamini.md).
- **A host wrapper for the robot, because upstream's own host leaves the arms limp.**
  `configure()` disables arm torque and both of its `enable_torque()` calls are commented out;
  nothing else in the driver turns it on. So `bridge/alohamini/` holds a wrapper that runs
  upstream's loop with torque enabled and the lift stopped, and adds three fields so quackd can
  tell it from a stock host. Without it, `move_joints`, `gripper` and `home_arms` refuse and say
  why, rather than commanding joints that would not move.
- **A stop that actually stops this robot.** Three upstream behaviours conspire against one.
  All three velocity keys are mandatory in every payload, because `send_action` indexes them
  with no default and would otherwise discard the whole action, arms included, without
  refreshing its own watchdog. The lift latches, so a payload that says nothing about it leaves
  it travelling. And the lift's two keys are not symmetric, so a payload carrying both freezes
  it instead. One function builds every payload and holds all three invariants, and the fake
  host reproduces the bugs so the tests mean something: `test_stop_zeroes_the_lift` fails
  against the naive three-key stop that the shape of the driver invites.
- **`alohamini-lookout`**, the task to point at a real AlohaMini first: nothing in its
  allowlist moves a wheel, an arm or the lift. It is the same shape as `xlerobot-lookout` and
  for the same reason, since this body has no head and no voice either: `observe`,
  `report_state` and `stop` are the whole allowlist, a human aims the camera, and the answer
  goes in the reason the pilot declares success with. The budget is twelve steps and three
  minutes. The scripted pilot takes one frame and answers, and on `alohamini:sim2d` it succeeds
  on ten of ten seeds with the world's ground truth checked that the base never moved. The
  AlohaMini checklist runs it at step 5, on upstream's stock host with the arms still limp.
- **The ToddlerBot: a small open source humanoid, and the first body here that can hurt
  itself** (`--robot toddlerbot:mock`, `sim2d` or `bridge`). Two arms, two legs, a two joint
  neck and thirty servos, on a machine with no network API of any kind, so quackd ships the
  daemon that runs on it. That is the Open Duck Mini's shape, but for a second reason that
  matters more: a verb is episodic and this body is not. Its `step()` is a no-op, so nothing
  times out and nothing re-arms, and a humanoid frozen mid-stride while a model thinks is a
  humanoid on the floor. The daemon runs the fifty hertz loop and quackd's intents steer what
  it is already doing. Design: `docs/adr/0028-toddlerbot.md`, with the page at
  [docs/adapters/toddlerbot.md](docs/adapters/toddlerbot.md).
- **Seven things upstream does not do, because reading it at the pin said so.** It clamps
  nothing and never reads the joint limits that exist, on motors in multi-turn mode where the
  firmware limits are off too. A dropped packet returns an all-zeros reading indistinguishable
  from every joint at zero, which fed to a position controller commands a full-scale move to
  zero. A controller fault arrives as a bare `KeyError`. There is no reset anywhere, no
  watchdog, no timeout and no e-stop. So the daemon carries a clamp, a rate limit, an
  all-zeros detector, a fault guard, a safe-pose slew, signal handlers and a construction
  watchdog, and every one of them is exercised in CI against a fake body over a real socket.
- **A shutdown that does not drop the robot.** Upstream's does: a C level `atexit` handler
  disconnects every client and disconnecting disables torque, so any unhandled exception or
  plain Ctrl-C de-torques a standing humanoid with no lowering and no ramp. `SIGTERM` does not
  even reach that handler, and upstream installs no Python one, so systemd stopping it leaves
  the robot fully torqued holding its last target instead. quackd's daemon settles to a safe
  pose first, then closes under a hard deadline, because `close()` holds the GIL and retries
  forever on a dead bus.
- **A deadman that is a trajectory rather than a message.** On a duck, silence is safe and
  zero velocity is a stop. Here the command is an absolute pose, so holding the last target,
  jumping to a new one and going limp are the only three options and none of them is safe. The
  daemon slews to upstream's own default pose at upstream's own rate, waist first as its own
  reset does, and holds.
- **`toddlerbot-lookout`**, the task to point at a real humanoid first, on its safety stand:
  nothing in its allowlist moves a leg, an arm or the waist.
- **[docs/toddlerbot-hardware-checklist.md](docs/toddlerbot-hardware-checklist.md), the order
  to try a real ToddlerBot in.** Fourteen steps on the safety stand upstream ships, feet off
  the ground until step 13. Step 2 is `calibrate_zero`, because the daemon refuses to actuate
  without a `motors.yml`. It runs the daemon with `--fake --once` and `quackd doctor` against
  port 9873 before a motor is powered, points `toddlerbot-lookout` at the robot at step 8, and
  holds steps 11 and 12 (pull the network cable mid-move, then send `SIGTERM`) as the two that
  matter, because on this body silence means hold forever and the daemon is the only thing that
  makes it mean anything else. It ends with the four things only a real robot can answer, the
  safe-pose slew from a crawl or a prone start first. There is no issue template for it, so
  open a plain issue with the transcript.
- **`QUACKD_TODDLERBOT_TOKEN` and `TODDLERBOT_ROOT`**, the two environment variables the
  ToddlerBot's adapter and daemon read. The token is the ToddlerBot's own and not
  `QUACKD_DUCK_TOKEN`, and `--token`'s help says so now: `quackd/adapters/toddlerbot/bridge.py`
  reads it when `--token` is absent and sends it in `bot.hello`, and
  `bridge/toddlerbot/quackd_toddlerbot_bridge.py` reads the same name as the default for its
  own `--token`, compares it with `hmac.compare_digest`, and refuses every other method until a
  hello carries it. A daemon started with no token accepts everyone, and nothing writes one for
  you, because this daemon has no installer. `TODDLERBOT_ROOT` is the daemon's alone: it is the
  default for `--toddlerbot` (otherwise `.`), the upstream checkout the daemon changes
  directory into and imports from, because every path upstream's `Robot` builds is relative,
  and `--fake` never opens it. The nightly contract job sets it to its own sparse checkout of
  upstream.
- **Video off a real Microduck** (`--camera-url webrtc://host:8443`, `quackd[microduck-camera]`).
  There is no camera method in `duck-ipc-proto`, no snapshot route in `mediad` and no camera
  subcommand in `robotctl`, so a picture means being a WebRTC peer. It runs on your machine and
  writes nothing to the robot — the alternative needs `mediad` stopped, because its `v4l2src`
  holds `/dev/video0`. Signalling is read off `mediad`'s own web client at the pin and tested
  through a fake socket; the H.264 and the ICE are not tested and have never met a duck.
- [docs/microduck-hardware-checklist.md](docs/microduck-hardware-checklist.md), an issue
  template, and `microduck-lookout` — a bring-up task whose allowlist moves no legs, which
  copes with having no camera and stops to say so when posture reads `unknown`. The checklist
  assumes the duck is borrowed: nothing in it installs anything or needs `sudo`.
- CI runs on Windows. `robotd` speaks over a unix socket, Windows cannot open one, and the test
  covering quackd's `ssh -L` answer only runs there — so it had never run anywhere.
- **Bring-up checklists for the XLeRobot and the AlohaMini**
  ([docs/xlerobot-hardware-checklist.md](docs/xlerobot-hardware-checklist.md),
  [docs/alohamini-hardware-checklist.md](docs/alohamini-hardware-checklist.md)). The two
  bodies here you can buy today were the two without one. They are not copies of each other:
  the cart's hazard is that its watchdog stops the wheels and leaves fourteen arm servos
  holding, so it stays on blocks until step 9; the AlohaMini's is the opposite, that its arms
  are limp until quackd's own host turns torque on, which makes upstream's stock host the
  safest place to learn the base and the lift first.
- **[docs/reading-robots.md](docs/reading-robots.md), the traps by pattern rather than by
  robot.** quackd drives eight bodies and has run on none, so everything it does came from
  reading upstream closely enough to be safe without executing it, and the same shapes kept
  recurring: a number that looks like a different number, a default pose that is not neutral,
  a stop that is not a stop, a partial message that means something else, a capability that is
  only a claim, a name that exists on the wrong class, a line somebody commented out. The next
  robot will not have the AlohaMini's bug; it will have one of the AlohaMini's shape.

### Changed

- **The ToddlerBot's manifest is mostly absences, and every one of them is upstream's.** There
  is no text to speech at this pin, so `say` does not exist. There is no get-up policy, so
  `stand_up` is not declared and a fall ends the run with a message that names no verb and
  asks for a human. Nothing reports a battery to Python, so a battery abort cannot fire. And
  the walk policy is an ONNX checkpoint from a wandb artifact that upstream neither publishes
  nor checks in, so on a bare install **there is no locomotion at all**: `move`, `go_to` and
  `approach_and` are not gated off, they do not exist, and `mobility` reads `none` until the
  daemon reports a checkpoint staged.
- **No raw joint verb on the humanoid.** Thirty unclamped radians from a language model, on a
  machine in multi-turn mode with no current limit and no position limit, is the exact failure
  this project exists to prevent. quackd offers named moves and the daemon owns every
  trajectory.
- **The XLeRobot's manifest says no to more than it says yes to, and each no is upstream's.**
  There is no speaker and no microphone in the bill of materials, so the `sound` intent is not
  declared and `say` does not exist here. Which head motor is yaw is stated nowhere upstream,
  so quackd never commands the head and `search_scan` turns the whole cart. The power station
  has no data link, so `battery_percent` is permanently `None`. There is no odometry, so
  `go_to` closes the loop on the camera alone. And a stock cart ships with every camera
  commented out of its config, so `observe`, `go_to`, `search_scan` and `approach_and` exist
  only once a camera has actually been seen on the wire.
- Arm positions on **both** new robots are **normalised −100..100, not degrees**, because their
  `use_degrees` defaults to False. That is a different contract from the SO-101 arm next door,
  which sets degrees: the same number means a different angle, so `move_joints` validates
  against `joint_norm` and never against `lerobot`'s `joint_deg`. Turn rate is converted on both
  too, because each wire is deg/s while quackd is rad/s, and a pass-through would be a 57× error
  on a robot heavy enough to hurt someone.
- `pyzmq` joins the `dev` extra, and is the extra for both new robots. CI installs only
  `dev`, and the fake-host tests are what the two `zmq` backends' 🧪 rest on, so a status claim
  CI could not check would not have been honest.
- **`quackd run` asks "Are you watching the robot right now?" once before a fall-blind robot
  walks.** The question comes at the start of the run, before any verb, when the task allows
  `move`, `go_to`, `search_scan` or `approach_and` on a robot that reports `fall_detection`
  false and has no `stand_up`, which today is `open_duck:bridge` alone, because the Microduck
  can get up and no other body reports the flag. The default answer is no, and no aborts the
  run with exit 1 before a leg moves, so a script that started the bridge walking without
  `--yes` in 0.6.0 now stops at the question until you add `--yes`. Over MCP nothing asks: the
  pilot sees `fall-blind=nothing-detects-falls` in `report_state` and every observation,
  `quackd doctor` prints the warning, and that is all it gets. This is the guard that stands in
  for the ones "It claimed guards it did not have" above says were never there
  ([docs/open-duck-hardware-checklist.md](docs/open-duck-hardware-checklist.md)).
- **`quackd serve-mcp` with no `.duck` loaded now runs on a default budget of 40 verb steps and
  five minutes, counted from when the server started.** In 0.6.0 that session had no `Budget`
  at all, so `quackd serve-mcp --robot open_duck:bridge` would have handed an MCP client
  unlimited and uncounted control of a physical biped. The numbers are the frontmatter
  defaults, `max_steps: 40` and `max_minutes: 5`, and every `robot_run_verb` and
  `robot_observe` counts, `stop` included, so from the 41st call or once five minutes have
  passed every verb answers `budget exhausted` until `robot_load_duckfile` starts the
  contract's own budget. The clock is wall time on `open_duck:bridge` and `microduck:jsonrpc`
  and the simulator's own clock on `sim2d`, and there is no flag to turn the budget off
  ([docs/mcp.md](docs/mcp.md)).
- **One Ctrl-C stops the robot, and a second one quits.** The first Ctrl-C, or `q`, cancels the
  verb that is running and sends `stop`, the fix described under "The operator's stop did not
  stop the duck" above. New here is the second: the kill switch hands `SIGINT` back before it
  fires, so another Ctrl-C is a plain `KeyboardInterrupt`, where 0.6.0 swallowed every Ctrl-C
  after the first for the rest of the run and left you holding a key on a robot you could not
  be sure had heard. The `kill switch: Ctrl-C` line now prints to stderr on every `quackd run`
  rather than only with `--verbose`, and the banner says `Ctrl-C or q stops the duck. Press it
  twice to quit at once.` A flock run keeps its old banner, and its kill-switch line is still
  `--verbose` only.
- **The bridge refuses to start when `--max-vx`, `--max-vy` or `--max-vyaw` is above upstream's
  own clamp, or `--head-safety` is outside (0, 1].** The three velocity flags, documented in
  [bridge/open_duck/README.md](bridge/open_duck/README.md), were never checked, so the number
  in the unit went straight into the clamp the bridge applies and into the limits it advertises
  in the hello, and a unit asking for more than the 0.15, 0.2 and 1.0 that upstream's own
  gamepad allows would have been honoured. Narrowing them is the obvious first power-on
  precaution, and widening them past the pad is not something quackd should do quietly on your
  behalf, so the bridge exits with the bound in the message rather than silently clamping.
  `--head-safety` is the fraction of upstream's head range quackd will use, not a multiplier on
  it, so 0 and anything above 1 are refused the same way.
- **The bridge's systemd unit has no start rate limit any more (`StartLimitIntervalSec=0`).**
  It was 3 starts in 120 s, and with `Restart=no` the only thing a burst limit could throttle
  is you editing a path and running `systemctl start` again, which is exactly what bring-up is.
  The fourth attempt failed with an error about none of the problems being debugged and needed
  `systemctl reset-failed`, which appeared nowhere in the docs.
- **camd captures at 5 fps and 256 px by default, from 1 fps and 512.** `go_to` and
  `search_scan` close a visual loop at 10 Hz, and holding one steering correction for a whole
  second gives a per-frame loop gain above 1, a weave that swings the target back out of frame.
  The smaller frame pays for the faster rate on a Pi Zero 2 W, and `quackd-duck-camd.service`
  now passes `--fps 5 --size 256` explicitly. A 0.6.0 unit left in place still passes `--fps 1`
  and nothing for `--size`, so it keeps the slow loop at the new size until you re-run
  `install.sh`.
- **mypy type-checks `bridge/` now, the code that actually runs on the robot.** `[tool.mypy]`
  had `packages = ["quackd"]`, so the daemons on the robot's side were the only Python in the
  repository nothing type-checked. It is `files = ["quackd", "bridge"]` now, because mypy takes
  packages or files and not both, and CI's bare `uv run mypy` enforces it. The two Open Duck
  files were clean when the scope widened, and the gate earned its keep at the merge that
  brought the three new robots in: the ToddlerBot daemon's `int(getattr(robot, "nu", ...))` was
  typed `Any | None` because `robot` is optional there, and it now falls back to the frame's
  own width instead of crashing on a body nobody passed.
- **Nine of ten upstream unknowns were closed by reading, not guessing** — the import form the
  shim depends on, that `get_last_command` runs at 50 Hz and before the pause check, that `B`
  is the sound button, that the antennas are trigger-driven and unclamped, that the head slots
  are written unconditionally with no mode button, that the walk loop opens no camera, and that
  the IMU in use is `raw_imu.Imu` (a dict of gyro and accelero) rather than `imu.Imu` (a
  quaternion). The four head floats turn out to be **offsets** added to the walk policy's own
  head targets, not absolute joint angles. Fall detection was deliberately left unimplemented:
  which axis reads gravity depends on an axis remap, a config flag and a tare offset, and a
  fall detector that is wrong fails as a confident "not fallen".

### Fixed

- **The ToddlerBot's `search_scan` sweeps its head rather than turning its body.** `scan_mode`
  turns any robot with mobility and the twist intent, which is right for a duck and wrong for
  a humanoid with no get-up policy: with a walk checkpoint staged the shared verb would have
  pirouetted 3 kg of fall-prone robot to look for a ball. quackd supplies its own for this
  body, and it waits for the neck to arrive before taking the frame, because the daemon rate
  limits every joint and the shared sweep looks after a tenth of a second.
- **The transport keeps the link alive, so a long verb is not cancelled by its own deadman.**
  The daemon's deadman fires after half a second of silence and `stand` takes three, and the
  executor sends one command and then only polls while it waits. quackd's own `Heartbeat`
  cannot be that signal, because its period is a run setting rather than the manifest's and
  defaults to the same half second. The ToddlerBot transport now sends `bot.keepalive` on its
  own timer, and reading state or a frame deliberately does not count as being alive.
- **Every ToddlerBot disconnect used to stall for the full request timeout.** `close()`
  cancelled the read loop and then asked for a final stop, which waits on a future only that
  read loop could resolve.
- **A walk checkpoint that cannot move the robot is no longer offered as locomotion.** An
  envelope of zero on every axis clamps every velocity to nothing, so `move`, `go_to` and
  `approach_and` would have accepted every command and moved nothing.
- **The mocks and the simulators stopped reporting a pose their robots do not have.** Neither
  ZeroMQ wire carries a position, so the real backends report None and the offline doubles
  were dead-reckoning one. A double that is easier than the robot is a task that passes here
  and fails there.
- **The ToddlerBot daemon stopped claiming four things it could not do.** An audit of the
  three new adapters against their own plan found the same shape of bug three times, and it
  is the shape this project exists to prevent: a capability flag the operator sets, a
  manifest that promises verbs because of it, and a daemon with no implementation behind it.
  `--camera` declared `observe`, `search_scan`, `go_to` and `approach_and` while the frame
  handler read an attribute that was never defined, so every frame came back empty and
  `toddlerbot-lookout`, the one task shipped for the first hardware day, could not have run.
  `--walk` declared locomotion and returned `accepted: True` for every command while the
  policy attribute it consulted was never assigned, so the robot would have stood still and
  reported success. And `perform` refused every motion because the keyframe library was
  initialised empty and never filled. A later pass found a fourth of the same shape: `grip`
  answered accepted and commanded nothing at all, on a capability read from the command line
  rather than from the body. All four are now real, and **the handshake reports what actually
  loaded rather than what was asked for**: a camera that will not open means the camera verbs
  never appear, a walk checkpoint is a file you supply rather than a claim you make, and the
  gripper capability follows the motors.
- **`policy.step_target()` never existed upstream.** It was quackd's invention, which is the
  exact failure ADR-0022 is written to prevent. The real interface takes the whole
  observation and the sim and answers with a pair, and it is now cited at a pinned line
  along with sixteen other names the daemon needed and did not have: that motions carry a
  per-variant suffix, that upstream ships no loader at all, that `cartwheel` cannot be
  replayed because its file holds no action array, that `walk_zmp` is a lookup table rather
  than a motion, and that `Camera.get_jpeg` hands RGB to an encoder that wants BGR and so
  returns a picture with red and blue swapped.
- **The walk envelope comes from the checkpoint now.** `command_range` is read at connect off
  the policy that is actually loaded, rather than hardcoded from a gin file, so a robot whose
  gait was trained tighter than quackd's caps gets the tighter number. It can only ever
  narrow: a checkpoint trained wider does not get to widen `limits`.
- **A daemon fault no longer looks like a healthy robot.** A raising tick used to kill the
  control thread while the socket went on answering `ok`. It is now caught: the deadman is
  forced, `bot.health` reports the fault so quackd's heartbeat trips, and a bus that never
  comes back settles and stops rather than failing fifty times a second forever. The
  `sys.excepthook` and `threading.excepthook` that Part C of the plan asked for are installed
  too, because upstream's C level `atexit` disables torque on any interpreter exit and would
  otherwise drop a standing robot before Python got a say.
- **The ToddlerBot daemon moved off port 9872 to 9873.** The Open Duck Mini already had it:
  9871 for its bridge and 9872 for its camera daemon, and `SECURITY.md` tells people to
  tunnel that pair. The comment justifying the old choice named only the bridge. Its token is
  also compared with `hmac.compare_digest` now, as the Open Duck's always was, rather than a
  plain `!=` that returns as soon as two bytes differ.
- **Two simulator rows earned the tick they were claiming.** `alohamini:sim2d` and
  `toddlerbot:sim2d` were marked ✅ while their own text admitted no seeded sweep, on a page
  where ✅ means exactly that. Both now run their lookout task on ten of ten seeds in CI, with
  the ground truth checked: the AlohaMini never moves a wheel, an arm or the lift, and the
  ToddlerBot never takes a step or turns its body. The `alohamini:zmq` row was also sitting
  under the ToddlerBot's heading rather than its own robot's.
- **`quackd[alohamini]` was never locked.** The extra shipped in `pyproject.toml` and never
  reached `uv.lock`, so `uv sync --locked` refused it. `NOTICE` also credited every other
  upstream and none of the three new ones, including the fact that ToddlerBot's design files
  are non-commercially licensed and quackd therefore distributes none of them.
- **The five-to-eight sweep reached the places the tests do not police.** `test_docs.py`
  checks three exact phrases, so everything phrased differently had gone stale: `SECURITY.md`
  counted five bodies and scoped on-robot code to the Open Duck's two daemons, `docs/safety.md`
  had five rows in the table that answers "what stops this body when quackd goes quiet",
  `docs/faq.md` listed five adapters, and the README said five in four more places while
  saying eight in a fifth. The architecture pages also still said quackd hosts a control loop
  on one body when it now does on two, and in very different ways.
- **The duck could not be started at all.** The shipped `ExecStart` exits 2 before binding a
  socket, because `--script-arg --onnx_model_path` makes argparse refuse a value beginning with
  a dash — and it was the only `serve --script` invocation shipped anywhere, so it was the one
  an owner would copy. `WorkingDirectory` was one level above the data: upstream opens
  `./polynomial_coefficients.pkl` and `../mini_bdx_runtime/assets/` relative to the working
  directory, and the first is read inside `RLWalk.__init__` *after* the servo bus is powered,
  so the wrong directory is a traceback over fourteen energised joints. A pre-flight now
  refuses before the socket and before the servos. And no configuration produced both frames
  and the verbs that use them: the camera capability came from `expression_features.camera`,
  but that flag says who owns the *device*, and camd refused to start while it was true — so a
  correctly configured duck reported no camera and dropped `observe`, `go_to`, `search_scan`
  and `approach_and`, making both starter tasks and three checklist steps unreachable.
- **The operator's stop did not stop the duck.** An abort never reached the verb that was
  moving: `asyncio.wait_for` watches only the clock, so a kill switch, a Ctrl-C or a failed
  heartbeat set a flag nothing read until the verb returned on its own, while the verb's own 10
  Hz resend kept feeding the daemon's deadman. `stop` was then refused by the very gate that
  made it necessary, in both the executor and the MCP session. `duck.stop` lasted one tick and
  was undone by the next command. Nothing settled the duck on shutdown — the `finally` that
  looked like it did runs after the loop has exited, so its zeros had no reader — and
  `systemctl stop` killed the interpreter between two 20 ms ticks with the servos holding their
  last goal. Any client's disconnect zeroed a walking duck, including the `doctor` the
  checklist tells you to run from a second terminal. And `stop` reported a success it could not
  know, because the guard reads `stop_error` off the adapter and no adapter forwarded it.
- **It claimed guards it did not have.** The task file told the pilot every moving verb would
  refuse if it fell; nothing detects a fall on this backend, so none ever did, and an accepted
  `move` read to the model as evidence it was upright. A duck with `start_paused` could never
  walk and was misdiagnosed as a starved Pi. A wedged control loop reported itself healthy at
  50 Hz forever. A failed state read was dressed up as a healthy duck, with a fabricated pose
  for a robot that has no odometry. And the token was unreadable by the service user, so the
  bridge ran with authentication silently off while four places in the docs promised otherwise.
- **The camera was not safe to steer on.** camd served a frozen frame forever once capture
  stopped; its 1 fps default gives a divergent per-frame loop gain against a 10 Hz visual loop;
  `doctor` could not probe it at all; `go_to`'s command cadence was frame-fetch-bound against a
  300 ms deadman; and every distance was wrong, because the detector assumes the simulator's
  90° field of view where a Pi Camera Module 2 is about 62 — 0.23 m against 0.38 m for the same
  ball, enough for `go_to` to announce arrival outside the task's own success criterion.
  `--no-swap-rb`, offered in the README as the cure for wrong colours, inverts a correct image
  and relabels an orange ball as a person.
- **`express` on an Open Duck was a guaranteed no-op, and `droop` moved nothing even when it
  played.** `duck.antennas` does not refresh the command timestamp and every verb is its own
  tool call with an LLM turn between, so the command was always stale by the time a gesture
  arrived and the deadman rested the antennas before it played, while `express` reported
  success. Two 9 g servos on a GPIO pin are not motion, so a gesture now plays through a stale
  command for `GESTURE_S` (1.0 s) and then rests on its own, and `duck.stop` still rests it at
  once. `droop` sent 0.0, which upstream maps to the antennas' exact resting position, so it
  now sends `DROOP_POSITION`, -0.6 on upstream's -1..1 scale, a value no gamepad trigger can
  produce and a place nobody has watched these two servos go on a real duck, which is why it
  stops short of -1.
- **A second connection could drive the duck with no handshake at all.** The bridge kept its
  `greeted` flag on the core rather than on the connection, so on a bridge with no token, which
  the token bug above made every duck 0.6.0's `install.sh` set up, once any client had said
  `duck.hello` another socket could send `duck.command` straight away and skip the protocol and
  version checks on a socket that walks a robot. `authed` is per connection now, and anything
  but `duck.hello` on a fresh connection is refused with `say duck.hello first` whether or not
  a token is set.
- **camd served its last frame forever once capture stopped.** The handler read the capture
  timestamp and threw it away, so after one good frame the only 503 was unreachable: a ribbon
  working loose on a walking duck left `/snapshot.jpg` answering 200 with the same JPEG,
  `observe` reporting a confident detection and `go_to` steering on a photograph. Every
  snapshot now carries an `X-Frame-Age` header, and one older than four capture periods, never
  less than 1.5 s, answers 503 with the reason in the body (`/healthz` reports the threshold as
  `stale_after_s`). On the laptop `get_frame` reads the header, returns no frame instead of
  raising and keeps the reason where `doctor` can see it, treats a stamped frame over 2 s old
  as stale whatever the server's own threshold was (a server that does not stamp its frames is
  not checked), and gives up on a fetch after 1 s rather than 3, because `go_to` sends its next
  command only when the fetch returns and the deadman is 300 ms.
- **camd refused to start when `duck_config.json` had `expression_features.camera` true.** That
  flag says who owns the device, and camd exited 2 to avoid fighting the runtime for it, while
  the 0.6.0 bridge took its camera capability from the same flag, so the one setting under
  which the bridge advertised a camera was the one under which nothing served frames. Reading
  upstream at the pin, the walk loop the bridge runs opens no camera at all, so the collision
  could not happen in that process. camd now logs a warning and starts, and if you do run one
  of upstream's own camera scripts alongside it the contention shows up as failing captures on
  `/healthz` and expiring snapshots rather than a frozen frame, though setting the flag false
  is still the tidier setup.
- **Every distance on a real camera was wrong, and nothing let you correct it.** The detector
  assumed the simulator's 90 degree field of view where a Pi Camera Module 2 is about 62, so on
  hardware distances came out about 40 percent short, and [docs/faq.md](docs/faq.md) told you
  to set `fov_deg=62` in code. `quackd run` now takes `--fov-deg`, a manifest can carry
  `camera_fov_deg` under `limits` ([docs/manifest-spec.md](docs/manifest-spec.md)), and the
  flag wins over the key. Until one of them is set, every detection on a real backend ends in
  `(uncalibrated: distance is a rough guess)` and a warning names the flag, while `sim2d` and
  `mock` stay calibrated because their camera is the one the geometry assumes.
- **The API version quackd was written against had moved on.** `upstream_api.py` was the one
  adapter ADR-0022 let cite `main` instead of a commit hash, and in the week after it was
  read upstream went from `API_VERSION` 16 to 23. The handshake refuses on mismatch rather
  than guessing — correct behaviour, and it meant the first real Microduck anyone connected
  to would have closed the socket before a single intent was sent. The file now carries `PIN`
  and `READ_ON` like the other four, every ref links to a line at that hash, and a test keeps
  `/blob/main/` out. Re-reading all of them found the rest of the drift is small and additive:
  `Skill` is a free string now (a robot's real list arrives in `robot.subscribe`'s answer),
  and `RobotState.theremin`, `RobotState.chorale` and `HealthResult.cpu_temp_c` are new.
- **`robot.subscribe` was never sent, so nothing was ever known about the robot.** It lived
  in the `subscribe()` generator, which nothing in the CLI, the agent loop, the MCP server or
  the executor iterates, and upstream does not push state until asked. Every fact derived from
  the state frame was therefore empty for the life of a session — and empty read as safe:
  `fallen` was `False` because nobody was looking, so the precondition layer `docs/safety.md`
  advertises was inert on the one backend where a fall is a real robot on a real floor.
  `connect()` now subscribes, frames carry an arrival time and stop being believed when they
  stop arriving, and a duck nobody is watching reads as `unknown` rather than as standing.
- **`sit` and `stand` could do the opposite of what they said.** Both send upstream's single
  `sit_toggle`, and posture was the only thing telling them apart, so with posture permanently
  unknown `stand` would sit a standing duck down and report success. They refuse rather than
  fire a toggle nobody can aim. `stand_up` no longer reports "upright" when nothing reports
  falls.
- **One dropped camera frame ended the run.** `get_frame` raised beneath a comment promising
  it would not, and `AgentLoop._observe` calls it every step and catches nothing. Frames are
  now pulled on a timer and served from memory, `get_frame` never raises, and `camera_health()`
  carries the failure.
- **`doctor` accepted `--camera-url` and never fetched a frame**, so a wrong snapshot URL
  passed preflight and failed at the first `observe`. It now fetches one and prints its size.
- **The manifest claimed a camera on every backend.** Upstream serves no frames over
  `robotd`'s socket, so on a real duck `--camera-url` is the whole camera; `observe`, `go_to`,
  `search_scan` and `approach_and` were advertised while only able to answer "this transport
  has no camera". They are absent unless something is serving frames.
- **The heartbeat logged a stop it had not delivered.** `stop()` swallowed every error. It
  still never raises — it is called from the paths that run because the socket died — but it
  records why it failed and says so, and the heartbeat no longer asserts the outcome.
- An unknown sound tag became a chirp silently; JSON-RPC errors lost their code, so `BUSY` and
  `PERMISSION_DENIED` were the same thing to a caller.
- **Loading a second `.duck` over MCP refunded the budget.** `robot_load_duckfile` is a tool
  the model itself holds, and adopting a contract built a fresh `Budget` and cleared the
  consecutive-failure tallies, so a pilot that had spent its steps or been refused a verb
  could load a wider duck and start counting from zero. The limits still become the new
  contract's; the steps, the llm calls, the clock and the failure counts now stay the
  session's, and the tool's reply says what carried over.
- **`quackd doctor` and `quackd validate` crashed on a Windows pipe.** Python uses the ANSI
  codepage when stdout is not a console there, and quackd prints ✓, 🦆 and the status emoji in
  `doctor`, so on cp1252 the first two commands
  [docs/microduck-hardware-checklist.md](docs/microduck-hardware-checklist.md) puts in front of
  a Windows user died with `UnicodeEncodeError`, and so did the last step of CI's own Windows
  job. Both streams now replace what the codepage cannot carry, because a lost glyph costs a
  character and the raise cost the command.

### Known limitations

- **Nothing has run on hardware, on any of the eight adapters.** `microduck:jsonrpc`,
  `open_duck:bridge`, `reachy_mini:sdk`, `lerobot:real`, `rosbridge:ws`, `xlerobot:zmq`,
  `alohamini:zmq` and `toddlerbot:bridge` spell every upstream name from upstream source at a
  pinned commit and have only ever talked to fakes, the daemons quackd ships have only ever run
  with `--fake` over loopback or, for the ToddlerBot's, against upstream's MuJoCo body in a
  nightly job, and `microduck:websocket` is still a stub. If you have a body, the first thing
  to point at it is the task that moves nothing: `open-duck-lookout` with the feet off the
  ground until step 10 of
  [docs/open-duck-hardware-checklist.md](docs/open-duck-hardware-checklist.md),
  `microduck-lookout` on a duck you are probably borrowing with someone holding the gamepad
  ([docs/microduck-hardware-checklist.md](docs/microduck-hardware-checklist.md), feet off until
  step 9), `xlerobot-lookout` with the wheels on blocks until step 9, `alohamini-lookout` on
  upstream's stock host with the arms limp, before quackd's wrapper ever turns torque on, and
  `toddlerbot-lookout` on the safety stand with the feet off until step 13. The Reachy Mini,
  the LeRobot arm and the rosbridge base have no lookout task and no checklist, and the base
  has no verified deadman, so it goes on blocks first.
- **An Open Duck set up by 0.6.0 needs `bridge/open_duck/install.sh` run again on the robot
  before the new bridge will start.** 0.6.0's installer wrote the token `0600 root:root` inside
  a `0700` directory while the unit ran as `pi`, and the new daemon refuses to start on a token
  file it cannot read rather than run with authentication silently off, which is what the old
  one did. Nothing is lost by re-running it: the 0.6.0 unit could never start the bridge
  anyway, because argparse exits 2 on `--script-arg --onnx_model_path` before a socket is
  bound. The new `install.sh` keeps an existing token, makes it group readable by the service
  user and checks that it is, so the `--token` or `QUACKD_DUCK_TOKEN` your laptop already has
  stays valid.
- **Update the laptop and the robot together, because the handshake cannot tell 0.6.0 from
  0.7.0.** `PROTOCOL_VERSION` is still 1 on both ends, so a 0.6.0 laptop pairs with an updated
  bridge without a word and then sends every `duck.command` without the `epoch` the bridge now
  uses to tell a deliberate command from one that was already in flight when a stop landed. A
  client that sends none is held to the blunt window instead: for `--deadman-ms` (300 by
  default) after every stop its commands are silently dropped, and since `_turn` ends every
  `search_scan` step with a stop, the opening commands of the next step (up to three at 10 Hz)
  are swallowed and the duck under-rotates the scan. The other way round is quieter and no
  better: a 0.7.0 laptop drives a 0.6.0 bridge, which ignores `epoch`, but that bridge has none
  of the fixes above.
- **`--fov-deg` is a `run` option only, and no shipped manifest sets `camera_fov_deg`.**
  `record` and `doctor` do not take the flag and `serve-mcp` reads only the manifest key, so an
  MCP session on hardware carries the uncalibrated label with no way to clear it short of
  editing the adapter's manifest in source. The label is text the pilot reads, not a gate:
  nothing in the verbs checks `calibrated`, so an uncalibrated `go_to` still drives on the
  number.
- **`quackd[microduck-camera]` is a heavy extra, and the default install is unchanged.** It is
  `aiortc`, `av` and `websockets`, and `aiortc` brings `aioice`, `pylibsrtp`, `pyee`,
  `google-crc32c`, `cryptography` and `pyopenssl` with it, while `av` carries FFmpeg in a wheel
  of 18 to 39 MB depending on the platform. Nothing imports any of it unless `--camera-url`
  starts with `webrtc://`, and a missing extra is one error naming the `pip install` and the
  HTTP snapshot alternative that needs none. Nobody who is not pointing quackd at a real
  Microduck needs it, and the H.264 and the ICE inside it have never met a duck.

## [0.6.0] — 2026-09-04

A run stops starting from nothing. Every release so far built a pilot with no past: the
transcript recorded each prompt, tool call and frame, and the next run never read a line of
it, so a duck that had found the ball behind the sofa three times searched the whole room a
fourth time. Now each robot keeps a small file of notes the pilot chose to save and one
line per earlier run, and the newest of both are in the prompt before the first observation.
This is also the first release built on other people's pull requests: memory (#5) and the
`max_minutes` fix (#3) are both theirs. Design: `docs/adr/0025-memory-between-runs.md`,
with the release's own notes in [docs/design/memory.md](docs/design/memory.md).

Still nothing has run on a robot of any kind. What memory adds is tested end to end offline,
and no cloud model has ever written a note.

### Added

- **Memory between runs** (`quackd/memory.py`, [docs/memory.md](docs/memory.md)). Every
  run used to start from nothing. Now each robot, keyed `adapter:backend`, has a JSONL
  file under `~/.quackd/memory/` that holds the notes the pilot saved with the new
  `remember` tool and an episode line quackd writes at the end of every non-dry run
  (outcome, reason, the last few verb results). The newest of both are rendered into the
  system prompt at the next run. `remember` costs an LLM call but no step, and a repeated
  sentence refreshes the old note. `quackd run --no-memory` / `--memory-dir`,
  `quackd memory show|add|clear`, and over MCP `robot_recall` / `robot_remember` (eight
  tools now). A simulated body never inherits a real one's notes. Thanks to
  [@Bayway](https://github.com/Bayway) (#5), whose design and implementation this is.
- **The solo starter ducks ask for a note.** `find-and-kick`, `fetch`, `follow-me`,
  `patrol-and-quack`, `open-duck-scout`, `open-duck-lookout` and `reachy-spotter` carry
  `remember` in their last strategy step and a *Memory* section saying what is worth
  keeping, and `--goal` runs get the same line. A prompt-level hint alone was ignored by a
  14B local model; the strategy step is followed. The v0 duck goldens were regenerated
  for this. `hello-world` and the flock ducks are unchanged.
- **A test that the PyPI project page's links resolve** (`tests/test_pypi_readme.py`),
  because the rewrite below is invisible when it breaks: nothing in a normal run reads the
  built metadata, and the only symptom is dead links on a page the maintainer rarely opens.

### Changed

- CI runs on `actions/checkout@v7` and `astral-sh/setup-uv@v7`. `checkout@v4` targets
  Node 20, which GitHub deprecated and now force-runs on Node 24. Thanks to Dependabot
  (#1, #2).

### Fixed

- **`max_minutes` could be beaten by a slow provider.** The time budget was checked in
  `note_llm_call()`, *before* `provider.step()`, and nothing looked at the clock again
  before the answer was processed, so a provider that replied after the deadline still had
  its `declare_success` honoured and the run recorded a success it had no right to. The
  loop now re-checks the clock the moment a turn comes back, and a late declaration ends
  the run as `budget`. Thanks to [@r0jin](https://github.com/r0jin) (#3).
- **Every relative link in the README 404'd on the PyPI project page.** `README.md` is the
  long description, and its 60 relative links are correct on GitHub, where a relative link
  follows the branch you are reading, and meaningless anywhere else. A hatchling metadata
  hook (`hatch_build.py`) absolutises them at build time, in both syntaxes this README
  uses: 56 Markdown links and four raw `<a href>` in the centred HTML blocks. The first cut
  of the hook handled only Markdown, so the test now checks both and would have caught it.
  The repository's own README is unchanged, images were already absolute, and in-page
  anchors are left alone.
- **A hand-edited memory file could take the whole command down.** The file is documented
  as one you may edit, and a line that is not JSON is skipped. A line that *was* JSON but
  not an object (`"a note"`, `17`, `[]`) raised `AttributeError` out of every reader,
  because only `JSONDecodeError` was caught. Now every such line is skipped, as promised.
- **`--dry-run` wrote permanent notes.** The episode at the end of a run was guarded and
  the `remember` tool was not, so a run that sent nothing to the robot could still leave a
  permanent conclusion drawn from verb results the dry run itself invented. It now refuses
  and says so, like every other intent a dry run declines to send.
- **A refreshed note sorted as the oldest.** Repeating a sentence refreshes its timestamp
  and used to leave it where it was in the file, and both the prompt's "newest notes" window
  and the 400-entry cap read file order. So the note a model had just repeated was the first
  one dropped. A refresh now moves the note to the newest position.
- **`quackd memory show --raw` did not print the file as is.** It went through Rich, which
  ate anything in square brackets: a note reading `the ball is [bold]behind[/bold] the sofa`
  printed as `the ball is behind the sofa`. A note is text a model wrote, so it can contain
  anything.
- Tests set `QUACKD_MEMORY_DIR` to a temporary directory, so a test run no longer writes
  the developer's own `~/.quackd/memory`.
- `quackd memory show|add|clear --robot <bad spec>` printed a Python traceback. Every other
  command taking `--robot` answers in one line, and now these do too.
- With `--no-memory` the MCP server still told the pilot to call `robot_recall` early, a
  tool that would answer "memory is off". Those two sentences are now omitted.
- The prompt told the model `remember` "is free". It costs an LLM call, which is a budget it
  shares with every other turn, and the ADR and docs both said so.
- **Claims that had gone stale before this release, found while auditing it.**
  `docs/architecture.md` still described the `duck_*` MCP aliases 0.5 deleted, and had not
  been updated for memory at all (no `memory` command, no `remember` tool, no `memory`
  transcript event). The README's architecture diagram listed four adapters and omitted
  `open_duck` and its `bridge` backend, which the adapter-count guard could not see because
  it reads the phrase "N adapters" and not a list. The README, `docs/architecture.md` and
  the FAQ each said one quackd daemon runs on a robot, while `bridge/open_duck/` has shipped
  two since 0.5, and `docs/architecture.md` contradicted itself about it. `docs/duck-spec.md`
  called `max_minutes` a cap without saying a verb already running is not interrupted.
  `_deprecated()` in `quackd/cli.py` was dead from the 0.5 `--transport` removal. `quackd
  --help` still introduced the tool as a way to "pilot a Microduck", five adapters later.
  `_pick_default` justified its rule with the `duck_*` aliases. `tests/test_goldens.py`
  dated its goldens to 0.3.0 after four of the six duck hashes were regenerated here, and
  ADR-0025 filed learned verbs under ADR-0019, which is the `.duck` spec v1.
- **The README said no live local server had been run.** One now has, by this release's
  contributor, which is the first local-model evidence this project has had. The sentence
  says whose machine it was and that no transcript from it is in the repository.
- `test_cap_drops_old_episodes_before_notes` wrote 405 entries one at a time, each one
  re-reading and rewriting the whole file. It took 14.6 s here, on a matrix that runs it
  four times, to prove a property a cap of 12 proves in 0.16 s. It now also asserts *which*
  entries survive, which is what the test was named for.
- Guards for the class of mistake this release's own review kept finding. A living document
  *or a Python string a user reads* may not claim a number of `robot_*` tools the server
  disagrees with, nor still describe the `duck_*` aliases 0.5 deleted: 0.5's guard proved
  nothing still *offers* a removed thing and could not see one still being *described*, and
  the stale count turned out to be in a `--robots` help text and two docstrings as well as
  two README sentences. The key the CLI computes for a robot's memory file must equal the
  key the MCP server computes, for all five adapters and all fourteen backends, because
  "a note saved from Claude Desktop is read by the next `quackd run`" is otherwise an
  intention. And the built wheel's long description must contain no relative links.
- The `quackd memory` command group, and the memory flags on `run`, now have tests. They
  shipped with none, which is how two of the three subcommands were wrong.
- One assertion in the new memory tests was `A or B` where `A` was never true (the sound in
  the highlight is the robot's own tone, not the text), so it checked almost nothing. It is
  one assertion now.

### Known limitations

- **The scripted pilot never calls `remember`**, so `--provider fake` accumulates run
  outcomes and never writes a note. Notes have been exercised by one local model
  (Qwen 2.5 Coder 14B through LM Studio, `find-and-kick`, seeds 5 and 6) on one machine,
  and by no cloud model at all.
- **`--no-memory` does not silence the ducks.** The seven starter tasks above ask for a
  `remember` inside their strategy, and that text is in the prompt whether or not memory is
  on. A pilot that follows it gets a clear refusal that costs an LLM call and no step.
- Memory is a file, not a memory system: newest wins, there is no embedding and no search,
  nothing is shared between bodies, and the executor never reads it. A note is text a model
  wrote.

## [0.5.0] — 2026-09-03

The first robot you can actually build. Every hardware backend before this one targeted a
robot you could not buy or had not assembled: the Microduck ships around Christmas 2026, and
the Reachy, LeRobot and rosbridge backends have only ever talked to fakes. The Open Duck
Mini v2 is open hardware people are printing at home today, so for the first time a stranger
can follow these instructions all the way to a walking robot. That also makes this the first
release where quackd ships code that runs **on** a robot. Design: `docs/adr/0024-open-duck-mini.md`.

Still nothing has run on a duck. What is new is that everything except the duck is tested.

### Added

- **The Open Duck Mini v2 adapter** (`--robot open_duck:sim2d`, `open_duck:mock`,
  `open_duck:bridge`): the
  first robot quackd supports that anyone can build today. It is an open hardware 3D
  printed biped that walks on its own 50 Hz ONNX policy on a Raspberry Pi Zero 2 W. Its
  manifest is a strict subset and says so: this robot has no beak, no gripper, no kick
  policy, no sit policy and no get-up-after-fall policy, so `kick`, `grab`, `sit`, `stand`
  and `stand_up` are never declared and therefore do not exist for it anywhere. A duck
  built without a camera or a speaker loses exactly the verbs that need them. Velocities
  are clamped to the ranges read from the robot's own runtime (0.15 m/s forward, 0.2 m/s
  sideways, 1.0 rad/s turning), and a fallen duck refuses to move with a message saying a
  human must stand it up, because nothing quackd can call will.
- **A bridge daemon that runs on the robot** (`bridge/open_duck/`), which is a first for
  quackd: every other adapter talks to someone else's daemon, and this robot has none. It
  does not reimplement the 50 Hz control loop, it *is* that loop, with the class upstream
  imports to read a gamepad rebound to read a socket instead. One process, so the servo bus
  keeps one owner, and nothing of upstream's is copied. Going limp is unreachable rather
  than forbidden: the only channel from the network to the body is seven floats and a few
  buttons. The deadman is quackd's own, it runs on the robot, and it is evaluated by the
  control loop rather than a timer, so a server thread that is starved, wedged or dead still
  stops the duck. Standard library plus numpy, so it installs on a 512 MB Pi, and
  `--fake` runs the whole protocol on a laptop with no robot at all.
- **A camera server for the duck's Pi** (`bridge/open_duck/quackd_duck_camd.py`), in its
  own process because encoding a JPEG inside a 20 ms control tick is not affordable on a Pi
  Zero 2 W. It captures on a timer so a slow client cannot stall it, serves the newest frame
  over HTTP, and has no control path at all: it reads a camera and answers GET. Without it
  the bridge advertises no camera and the verbs that need one do not exist rather than exist
  and fail. `--fake` paints a duck's eye view, so the whole chain runs with no hardware.
- **Two starter tasks**, `open-duck-scout` (find the ball, walk up, report, 10 of 10 seeds)
  and `open-duck-lookout`, whose allowlist moves no legs at all and which exists to be the
  first thing anyone points at a physical duck.
- ADR-0024, `docs/adapters/open_duck.md`, and a hardware checklist and issue template for
  the first person to run this on a duck they built.
- **`--camera-url`** on `run`, `serve-mcp` and `doctor`, for a robot whose camera is an HTTP
  snapshot rather than something the control socket can serve.
- **`--token` and `QUACKD_DUCK_TOKEN`** for a robot that wants authentication. The Open Duck
  bridge's own installer writes one, so until now a duck set up by the book refused every
  client and no document explained why.
- **`quackd doctor --robot X --address Y` connects.** Everything else in `doctor` is offline
  and reads the static manifest, which describes a fully built robot. This prints what your
  robot actually reported: its capabilities, which verbs it does and does not have, and
  whether its control loop is healthy. It is the only way to see that difference before a
  run does.

### Removed

- **`--transport X`**, the 0.4 alias of `--robot microduck:X`, along with `resolve_robot`'s
  transport branch and the warn-once machinery that existed only to support it. 0.4 said in
  ten places that it would go in 0.5.
- **The eight `duck_*` MCP tools**, 0.3 aliases pinned to the default robot. Omitting the
  `robot` argument to a `robot_*` tool does the same thing. `duck_get_frame` has no exact
  replacement by design: `robot_observe` does the same job but goes through the executor, so
  frames are now budgeted and logged like every other verb.
- Removing them broke the MCP system prompt, which named three of the removed tools and
  hardcoded "a small biped duck robot (25 cm, 800 g)" for every body. It now names the robot
  and takes its description from the manifest's own `blurb`, so a lone Open Duck introduces
  itself as the duck that cannot pick anything up and cannot get back up if it falls.

### Fixed

- **Perception was attached only for the simulator.** Every hardware backend with a camera
  ran blind: it fetched frames, detected nothing because nothing was detecting, and reported
  that it could not see. The detector now follows the camera rather than the backend, which
  also fixes `microduck:jsonrpc` and `rosbridge:ws`. Both entry points ask one function,
  `perception.detector_for`, and both ask it at *connect*: the first cut of this fix keyed
  `quackd run` on the camera and left `serve-mcp` keyed on the backend, so every hardware
  body over MCP still ran blind, and deciding from the static description would still have
  missed a `rosbridge:ws` base, which only reports its camera once it has connected.
- **A robot that reports fewer capabilities at connect than its description claims** used to
  crash the run. `validate` checks the static manifest, which describes a fully built robot,
  so a duck with no camera got past it and then raised a bare `VerbNotFound` when the agent
  loop built its tools. A verb the task *requires* now refuses in the validator's words, and
  one it merely *allows* is dropped with a line in the log, which is what a v1 task allowing
  more than it needs is for.
- `quackd run` now checks a `.duck` against its robot before connecting, the way
  `serve-mcp` always has. Pointing a task at a robot that lacks one of its verbs used to
  reach the agent loop and raise a bare `VerbNotFound` with the robot already connected and
  an empty run directory already written. It now refuses up front with the validator's own
  sentence and writes nothing.
- **Two commands still told users to pass `--transport`**, the flag this release removes: a
  `doctor` table title and the `TransportError` the WebSocket stub raises. The guard that
  swept the documentation for it could not see a Python string, and now reads
  `quackd/**/*.py` too. Six other user-visible strings were still dated "in 0.4", and
  `doctor` printed the extra as `quackd` because rich ate the `[lan]` as markup.
- **The PyPI summary and keywords never mentioned the Open Duck Mini**, the robot this
  release is named for, and no test had ever read `pyproject.toml`. One now does. The
  `Framework :: Robot Framework` classifier is also gone: that is the test-automation tool
  of the same name, not robotics, and it filed quackd under the wrong ecosystem.
- `quackd doctor` reported `?` for opencv and Pillow, which are not optional and were plainly
  installed. An import name is not a distribution name, so it now asks the installer which
  distribution provides the module before giving up.
- The Open Duck bring-up checklist keeps the feet off the ground through step 9 and puts
  them down at step 10. Three documents said step 8.
- `mypy` failed on Python 3.12 (the opencv stubs that resolve there type only the array
  overload of `cv2.inRange`, so the tuple bounds in `perception/color_blob.py` matched no
  variant). Bounds are now `uint8` arrays. OpenCV accepts both, so nothing about detection
  changes: the seeded goldens are byte-identical and all five sweeps still pass 10 of 10.
  This landed just after the v0.4.0 tag, so the tagged commit and the 0.4.0 files on PyPI
  still carry it. It is a type-check-only issue and does not affect the released package at
  runtime.

## [0.4.0] — 2026-09-02

From "a brain for the Microduck" to "a brain for any small robot". The thesis does not
change: the LLM picks verbs, the robot's own controllers move, quackd enforces the
contract. Four adapters (Microduck, Reachy Mini, a LeRobot arm, any base over rosbridge),
one `.duck` contract across bodies, a head and a duck completing one task together in the
simulator, and nothing claimed on hardware. Design: `docs/design/multi-robot.md`.

### Added

- **Robot adapters and manifests** (`quackd/adapters/`): every robot is an adapter that
  returns a `RobotManifest` from `connect()`, and the verb registry is built from that
  manifest rather than hardcoded. A verb that is not in the manifest does not exist. The
  Microduck is the first adapter and wraps the four existing transports with zero
  behaviour change; `manifest.schema.json` is generated and drift-tested. (ADR-0017)
- **Core verbs and aliases**: `observe`, `report_state`, `stop`, `say`, `move`, `go_to`,
  `search_scan` and `approach_and` exist on any robot that meets their requirements;
  `get_frame`, `walk_to` and `walk` are permanent aliases, listed once in
  `quackd/verbs/aliases.py`. `search_scan` sweeps the head on a robot that can only look.
  Preconditions are named in the manifest and supplied by the adapter; the executor spells
  none. (ADR-0018)
- **`.duck` v1**: `requires`, `robots`, `flock.roles` and `flock.frame_hints`; v0 files
  parse unchanged. `quackd validate --robot <adapter>:<backend>` checks a task against a
  robot's manifest with field-level errors such as `requires kick, but reachy-01
  (reachy-mini) does not provide it`. (ADR-0019)
- **`--robot <adapter>:<backend>`** on `run`, `validate`, `serve-mcp`, `doctor` and
  `list-verbs`; `--robots name=spec,...` on `run` and `validate`; `quackd list-adapters`;
  `doctor --robot` shows one manifest; a `.duck` may declare its default robot.
- Transcripts: `verb` events carry `canonical`; `run_start` and `summary.json` carry the
  robot's manifest and id. `duck_list_verbs` entries gain `canonical`, `aliases` and
  `core`; `duck_get_state` gains `robot`.
- Goldens recorded from 0.3.0 (`tests/golden/`) prove seeded worlds, the starter ducks and
  a `flock-kick` conversation are unchanged; CI runs both seeded sweeps at 10 of 10
  (`QUACKD_STRICT_SEEDS=1`).
- **Reachy Mini adapter** (`--robot reachy_mini:sim2d | mock | sdk`, extra `quackd[reachy]`
  for the SDK): a stationary head with a camera, a 180° neck, expressions and a speaker.
  Its manifest carries `observe`, `report_state`, `stop`, `say`, `search_scan` (a gaze
  sweep), `gaze`, `express`, `play_sound` and a confirm-gated `wake_up`; no locomotion
  verbs exist on it. `say(text)` is voiced as the closest expressive sound because the SDK
  has no text-to-speech; `stop` is `cancel_move` and `disable_motors` is never sent. Every
  SDK name is VERIFIED in `quackd/adapters/reachy_mini/upstream_api.py` against a pinned
  commit and the 1.10.0 wheel; the `sdk` backend has never been run on a robot.
  (ADR-0022, ADR-0023)
- **`StationaryHead`** in `sim2d`: a fixed camera on a wall with zero RNG draws, so every
  world without a head is byte-identical to 0.3; the recorder and the live window can
  focus a head camera.
- **`reachy-spotter` starter duck** (`duck: 1`, `robots: reachy_mini:sim2d`): find the
  ball with your gaze and say where it is; 10 of 10 seeds with the scripted pilot, judged
  by ground truth.
- **Heterogeneous flocks** (ADR-0020): members are adapters sharing one arena and one
  lockstep clock; bids carry a capability term so a robot bids only for a role its
  manifest can fill; one auction fills every role (most constrained first, lowest own
  distance, member-name tie-break, per-role hysteresis; the spotter is held for the run).
  With roles the kicker reports `kick_done` and the spotter judges from its own fresh
  frames (`VERDICT`); only `moved` is a success and the ground-truth veto stays on top.
  Frame hints (`HINT`, arena frame, sim only) choose the kicker's pre-turn; the
  frame-of-reference limitation is documented in `docs/flock.md`. `run --robots
  name=<adapter>:<backend>,...`.
- **`reachy-spots-duck-kicks` starter duck**: a Reachy Mini head spots the ball, a
  Microduck kicks it, the head judges the kick. 10 of 10 seeds with scripted pilots,
  every message in `flock.jsonl`, zero planner calls with the fake provider.
- **Multi-robot MCP**: `quackd serve-mcp --robots duck=microduck:sim2d,reachy=reachy_mini:mock`
  fronts a fleet with `robot_list`, `robot_list_verbs`, `robot_run_verb`, `robot_observe`,
  `robot_say` and `robot_load_duckfile`; every robot has its own executor, budget,
  heartbeat and contract, and `robot_load_duckfile` checks the contract's `requires`
  against that robot's manifest before adopting it. The eight `duck_*` tools stay as
  aliases of the default robot (deprecated, removed in 0.5). Simulated robots over MCP
  each get their own world; a shared arena over MCP is future work.
- **LAN discovery** (`quackd discover`, `quackd announce`, ADR-0021): zeroconf service
  `_quackd._tcp.local.` with an identity-only TXT record (manifest id, digest, adapter,
  body, verb count), every pair validated under 200 bytes before zeroconf sees it. Behind
  `quackd[lan]`, imported lazily, tested on fakes; exercised once for real between two
  processes on one machine, never between two machines.
- **MQTT flock bus** (`quackd.flock.mqtt_bus.MqttBus`, `run_flock(bus_factory=)`): the
  same two-method `Bus` protocol over a broker, `quackd/<flock_id>/ctl` at QoS 1 and
  `/hb` at QoS 0, never retained, the `FlockMessage` JSON as payload. Broker echo is
  dropped, the tap fires exactly once per message per node, remote messages are
  marshalled onto the event loop, and duplicates are tolerated by the coordinator's
  idempotent handlers. Library only: a flock across machines also needs a clock across
  machines, so there is no `--bus` flag. Tested on a fake broker; exercised once for real
  against a local `amqtt` broker with paho 2.1 (all eight kinds, one machine).
  `Subscription.drain()` is now an atomic `popleft` loop. `doctor` lists both LAN
  libraries.
- **LeRobot adapter** (`--robot lerobot:mock|real`, ADR-0022): an SO-101 class desktop arm
  with `move_joints`, `gripper`, `place` and, when a policy is available, `pick` as one
  skill intent that the arm's own learned policy executes. No locomotion, no voice, no
  gaze in its manifest. `real` sits behind `quackd[lerobot]` (Python 3.12 or newer, torch
  never imported on the default path), passes `calibrate=False`, refuses an uncalibrated
  arm, holds position on stop and never disables torque; every LeRobot name is pinned
  and line-linked in `quackd/adapters/lerobot/upstream_api.py`; never run on an arm.
- **rosbridge adapter** (`--robot rosbridge:mock|ws`): any wheeled base that takes a
  `geometry_msgs/msg/Twist` over `rosbridge_server`. The address carries the topics
  (`ws://host:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/compressed`); with an image
  topic the base also gets `observe`, `go_to`, `search_scan` and `approach_and`. There is
  no deadman: quackd re-sends the Twist at 10 Hz and zeroes it on stop, and the manifest
  says so. `ws` sits behind `quackd[rosbridge]` (roslibpy 2.x); every roslibpy, rosbridge
  protocol and message name is pinned and line-linked; never run against a bridge.
- **Speed limits come from the manifest**: `move`, `go_to` and the turn used by
  `search_scan` clamp to `limits.max_vx/max_vy/max_wz` when a manifest names them; the
  Microduck's limits equal the old schema bounds, so its runs are unchanged.
- **Docs**: `docs/adapters.md` (write an adapter in a day), `docs/manifest-spec.md`,
  `docs/adapter-status.md` (every adapter's honesty table, the Microduck's rows moved
  there unchanged), `docs/lan.md`, one page per adapter under `docs/adapters/`, and
  ADRs 0017 to 0023. `docs/safety.md` says what stops each body; `docs/faq.md` answers
  "can it drive something that is not a duck".

### Changed

- `default_registry()` is the Microduck manifest's registry; its names are canonical
  (`move`, `go_to`, `observe`) and every entry point accepts the old spellings. The
  bundled starter ducks keep their 0.3 spellings and stay at `duck: 0`.
- The agent loop connects before writing `run_start`, because the vocabulary comes from
  the connected robot.
- `docs/transport-status.md` is a redirect to `docs/adapter-status.md`; the docs test
  that keeps the Microduck's upstream table in sync now reads the new page.
- **The README was rewritten for four bodies**: the tagline and intro name the other
  robots, a new "Any small robot" section puts all four side by side with what each gets
  and what has actually run, the verb table gains a row per body, both architecture
  diagrams show the adapter layer, and the status table states per feature what was
  exercised against its real target and what was not.
- Test suite: 360 tests collected, no network and no keys, with four seeded sweeps CI holds
  at 10 of 10 (`find-and-kick`, `flock-kick`, `reachy-spotter`, `reachy-spots-duck-kicks`).
- Eight starter `.duck` files ship, up from six.

### Deprecated

- `--transport X` is an alias of `--robot microduck:X` that prints one warning per
  process; it is removed in 0.5. The `quackd.transport` package is not deprecated: it is
  the Microduck backend layer.
- The eight `duck_*` MCP tools (`duck_list_verbs`, `duck_run_verb`, `duck_get_frame`,
  `duck_get_state`, `duck_set_velocity`, `duck_stop`, `duck_quack`, `duck_load_duckfile`)
  are aliases of the six `robot_*` tools on the default robot. Each carries a deprecation
  note in its description and all eight are removed in 0.5.

### Fixed

- A role auction is complete only when every role can be filled by a *different* member,
  so a single robot that satisfies both roles can no longer deadlock a heterogeneous
  flock.
- `Subscription.drain()` is an atomic `popleft` loop rather than copy-then-clear, so a
  producer on another thread (the MQTT bus, before a message reaches the event loop)
  cannot have its message cleared unseen.

### Known limitations

- **Nothing has run on hardware, on any of the four adapters.** `microduck:jsonrpc`,
  `reachy_mini:sdk`, `lerobot:real` and `rosbridge:ws` spell every upstream name from
  upstream source (the three new ones at pinned commits) and have only ever talked to
  fakes. `microduck:websocket` is a stub waiting on upstream.
- LAN discovery and the MQTT bus were each exercised once, on one machine: zeroconf
  between two processes, MQTT against a local `amqtt` broker. Neither has crossed to a
  second machine, and a flock across machines also needs a clock across machines, which
  does not exist.
- Flock mode stays simulator only, with two choreographies and exactly two roles.
- A manifest can be smaller than the robot: `lerobot:real` claims no camera and no `pick`
  until it connects, and `rosbridge:ws` has no camera verbs unless the address names an
  image topic.
- The MCP server speaks `stdio` only, so it is a local subprocess of Claude Code or
  Claude Desktop and cannot be reached from a phone. A network transport is roadmap, not
  shipped.

## [0.3.0] — 2026-08-31

Multiple simulated Microducks cooperate: split the search, hold an auction, the closest
one kicks. Everything is on the record.

### Added

- **Flock mode** (`flock:` block in the `.duck`, or `--flock N` on `run`/`record`): 2 to 4
  ducks share one arena and an in-process message bus. A deterministic Contract Net
  auction picks the kicker (bid = each duck's own camera distance estimate, 20 %
  hysteresis, 6 s claim lease, duck-id tie-break, one-claimant lock), heading sectors
  split the search, misses trigger a full-circle re-search and re-auction, and a sim-time
  watchdog drops silent ducks. Every message, bid, claim and role change lands in
  `flock.jsonl`; the outcome is judged from sim ground truth, not a model claim.
  Guide: `docs/flock.md`. (ADR-0015)
- **Multi-duck simulator**: `World(n_ducks=…)` with per-duck deadman, noise streams, kick
  counters, duck-duck collisions and the four Microduck colorways (Cream, Sky, Lavender,
  Graphite); per-duck cameras render teammates, and the detector gained four `duck`
  targets. Sim time is governed by a lockstep clock, so the world freezes while any pilot
  thinks and single-duck runs stay bit-identical per seed. (ADR-0016)
- The planner makes **at most one** LLM call per flock run (parameters validated and
  clamped, deterministic fallback); `--provider fake` computes the plan as a pure
  function. Per-duck LLM pilots are deliberately out of scope.
- Duck to duck separation is watched from world ground truth while a claim is live: the
  coordinator orders an intruding non-kicker to retreat, and the retreat still runs
  through that duck's own executor.
- Starter `ducks/flock-kick.duck`; `runs/<ts>-flock-…/` layout with per-duck transcripts;
  flock demo GIF in the README. Scripted 3-duck acceptance: 10 of 10 seeds.
- The flock shipped through an adversarial review (69 agents, 24 confirmed findings, all
  fixed before release): deadlock guards around the shared clock's tick hooks and around
  member connect failures, per-duck `max_minutes` enforcement, a heartbeat watchdog floor
  above the longest verb sleep, cooldown gating at bid time, per-field planner clamping,
  `--max-steps` honoured on flock runs, `flock.search.restart_s` honoured, and
  `one_claimant: false` rejected instead of silently ignored.

### Changed

- `.duck` spec v0 gains the optional `flock:` block (`docs/duck-spec.md`, `schema.json`
  regenerated). Files using it need quackd 0.3.0 or newer; older versions refuse them
  loudly. `quackd validate` reports flock size and rejects flock + `verbs.confirm`.
- `quackd doctor` notes flock status; `serve-mcp` refuses flock ducks with a clear
  message.

## [0.2.0] — 2026-08-29

Local and open-source LLMs can pilot the duck. No API key needed.

### Added

- **Local providers** `ollama`, `vllm`, `llamacpp`, `lmstudio` and `local --base-url …`
  for any OpenAI-compatible server: no key, model discovery from `/v1/models`,
  `tool_choice=auto` and no `parallel_tool_calls` field for picky servers
  (`QUACKD_TOOL_CHOICE` overrides), vision opt-in with `--vision`, and a JSON text
  fallback for models that cannot call tools natively (marked `text_fallback` in the
  transcript). `quackd doctor` probes the four default local addresses.
  Guide: `docs/local-llms.md`. (ADR-0014)
- `quackd run --goal "…"`: a plain-language goal instead of a `.duck` file (ad-hoc contract:
  every `safe` verb, default budgets, standard abort rules). The scripted `fake` pilot picks
  a strategy from the goal's keywords.
- `--base-url`, `--api-key`, `--vision/--no-vision`, and `--gif-size` on `run`/`record`.
- Logo (`docs/assets/logo.svg`, a Microduck-like biped in the Lavender colourway) and a
  social-preview card.

### Changed

- README rewritten for people who know nothing about robots or LLMs first, developers
  second: what it does today vs. where it is going, Mermaid architecture diagrams, usage,
  configuration, performance and limitations sections. Images use absolute URLs so the
  PyPI page renders them. Providers are named by company ("OpenAI"), not model family.
- The hero GIF is recorded at 320 px panes.

### Fixed

- Rich markup ate `quackd[extra]` in CLI error hints.
- mypy on Python 3.12 (numpy's PEP 695 stubs) in CI.

## [0.1.0] — 2026-08-28

First release: sim-first, honest about hardware.

### Added

- **`.duck` spec v0**: strict pydantic frontmatter, generated `schema.json`,
  `quackd validate` with fail-fast field-level errors; five starter ducks
  (`hello-world`, `find-and-kick`, `patrol-and-quack`, `follow-me`, `fetch`) bundled in the
  wheel and resolvable by name.
- **Verb registry**: built-ins mapping 1:1 to shipped robot behaviours (`walk`, `sit`,
  `stand`, `kick`, `grab`, `stand_up`, `stop`, `quack`, `gaze`, `get_frame`), composites
  (`search_scan`, `walk_to`, `approach_and`), and the reserved learned-verb interface.
- **Safety executor**: allowlist, confirm gates, budgets, dry-run, machine-enforced
  `abort_when` (battery, consecutive failures), heartbeat, kill switch (Windows-safe).
- **Agent loop** with one tool call per turn, `runs/<ts>/transcript.jsonl`, frames,
  `summary.json`, and `run.gif` on the simulator.
- **`sim2d`**: built-in 2D simulator (deterministic under `--seed`, deadman, kick cone,
  unreliable open-loop scoop), top-down + first-person duck-cam renders, GIF recorder,
  optional `--live` window.
- **Perception**: `ColorBlobDetector` (HSV, bearing + distance from apparent size) and a
  lazy `YoloDetector` extra.
- **Providers**: `anthropic` (adaptive thinking, refusal handling, thinking-block replay),
  `openai`, `grok` (xAI endpoint), `gemini`, and the scripted `fake`; all vendor SDKs are
  optional extras.
- **`quackd serve-mcp`**: the duck as MCP tools for Claude Code / Claude Desktop through
  the same executor; `docs/mcp.md` with verified client config; project `.mcp.json`.
- **Transports**: `sim2d` (default), `mock`, experimental `jsonrpc` for the real robot
  (verified `duck-ipc-proto` v16 vocabulary, fake-robotd tests), `websocket` stub.
- **`quackd doctor`**, `quackd list-verbs`, `quackd record`.
- Docs: architecture, duck spec, transport status, safety, learned verbs (v2), licenses,
  FAQ, MCP; 13 ADRs; LAUNCH.md; CONTRIBUTING.md; hero GIF (scripted pilot, labelled).

### Known limitations

- The hardware transport has never run on a Microduck (hardware ships Christmas 2026).
- The README hero is a scripted-pilot recording; a real-model recording needs an API key.
- Non-Anthropic default model IDs are unverified; override with `QUACKD_MODEL`.

[Unreleased]: https://github.com/rokbenko/quackd/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/rokbenko/quackd/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/rokbenko/quackd/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/rokbenko/quackd/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/rokbenko/quackd/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/rokbenko/quackd/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/rokbenko/quackd/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/rokbenko/quackd/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/rokbenko/quackd/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rokbenko/quackd/releases/tag/v0.1.0
