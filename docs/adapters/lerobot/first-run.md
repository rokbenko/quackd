# A LeRobot SO-101 arm: from an empty laptop to a wave

This is the long way round, written for somebody who owns or can borrow an SO-101 and has
never run quackd or LeRobot. It starts with an empty laptop and ends with the arm noticing
you on a webcam and waving, driven by whichever model you choose to bring.

Three documents cover this arm and they do different jobs:

- **This one** is the narrative. It assumes nothing and it moves slowly.
- [lerobot-hardware-checklist.md](lerobot-hardware-checklist.md) is the risk ladder, eighteen
  steps in the order that can only fail safely. Once anything is about to move, that file is
  the authority and this one hands over to it.
- [adapters/lerobot.md](adapters/lerobot.md) is the reference: the manifest, every verb, the
  camera query keys, and the full table of what quackd refuses and why.

> [!NOTE]
> One SO-101 has now been down this path. On 2026-09-15 an arm calibrated as `arm-01` ran
> `lerobot-lookout`, and then free-form `--goal` runs, on Windows 11 with Python
> 3.12.12, lerobot 0.6.1 and quackd 0.9.0, piloted by OpenAI's `gpt-6-astra`. It waved by
> rolling the wrist about 27 degrees either way, waved again with `shoulder_lift` at -39 and
> `elbow_flex` between 24 and 30, opened and closed the gripper, and in one run mimed a duck
> quacking with the jaws. A USB webcam on `opencv://1` gave it pictures. It also went limp and
> fell at the end of every one of those runs, which is what [section 07](#07-record-the-rest-pose)
> now exists to fix.
>
> Two things that account does not cover. It was reached as
> `--robot lerobot:real --address COM3`, with no registered name; `arm-01` is both the id that
> run calibrated under and the id a bare `--robot lerobot:real` uses when you have not named the
> robot, which is why the two look alike. And the rest pose in section 07 was written after that
> day. Both were taken on the same arm on 2026-09-23, under a registered name with a rest pose
> recorded, and the pose could not be reached: it lay past the travel the arm's calibration
> recorded, and the servo will not be driven there. That is why
> [section 05](#05-find-the-port-then-calibrate) now asks you to calibrate with the arm folded,
> and [section 07](#07-record-the-rest-pose) says what quackd does when the fold is outside the
> travel anyway. So you are the second person down this path rather than the first, and what
> differs from those accounts is the part worth writing down.
> [What to report](#14-what-to-report) still matters most, whether or not the arm waves.

This page is in two parts, and they are two ways of driving the same arm rather than two
different jobs. **Part 1 is the terminal**, `quackd run` with a model you bring and a key in a
file. **Part 2 is Claude**, where the arm arrives as a set of tools in a chat and the model you
are already talking to is the pilot. Part 1 comes first because it is the path an arm has
actually been down. Part 2 repeats the commands the two share rather than sending you back for
them, so it reads straight through, and links here for the long explanations.

<br>

## Part 1: from the terminal

### 00. Read this first

Three things this task implies that the arm cannot do. Read them now so that nothing at the
bench is a surprise.

**There is no `wave` verb.** This body's whole vocabulary is `report_state`, `move_joints`,
`gripper`, `place` and `stop`, plus `observe` when a camera is configured. A wave has to be
invented by the model out of several `move_joints` calls, live, the first time you ask. That
improvisation is the thing being tested. It is not a feature that already exists.

**The camera cannot look for you.** No SO-101 has one built in, and quackd does not put one
on the arm: it is a USB webcam that plugs into your computer, aimed once and then fixed. This
body has no neck and no `search_scan`. "Finds you" means you are already inside a frame that
never moves, so you position yourself rather than the arm.

**Whether it can see you at all depends on the model you bring.** quackd's bundled detector
is a colour threshold carrying the simulator's own ranges, so on a real desk `person` means
"a saturated blue thing" and nothing else. A model that accepts images sees the actual
webcam frame every step and can simply look. A model that does not gets one line of text.
[Choose your pilot](#03-choose-your-pilot) is where that decision gets made, and
[Wave to me](#12-wave-to-me) is where it pays off or does not.

> [!CAUTION]
> There is no e-stop on an SO-101 and quackd cannot give it one. LeRobot writes a torque and
> current cap on the gripper and on nothing else, so the five body joints run at whatever
> their firmware defaults to. quackd bounds which angles a joint may reach and how fast it
> gets there. It does not bound how hard it pushes. Cutting the servo supply is the only
> thing that stops this arm in every case, including the one where the controlling process
> has died with a goal still standing.

Two reflexes, and they matter more than anything you type:

1. Know which plug or switch kills power before anything is powered on. You are probably a
   guest with somebody else's hardware, so run the supply through a power strip you can
   reach rather than modifying their equipment.
2. Keep fingers out of the gripper's jaws any time torque might be on. A pinch hazard does
   not need a command in flight.

<br>

### 01. What to bring

- **A laptop with quackd already installed.** Section 02 needs real internet and pulls
  torch, so do it the night before rather than on lab wifi.
- **A couple of USB cables for the arm's controller board.** You will not know which port it
  wants until you are in front of it, so bring more than one shape, and a hub if your laptop
  is short on ports.
- **A USB webcam, and something to prop it up and aim it.** A small tripod, a clip, a stack
  of books. The arm cannot move the camera for you.
- **Something strongly blue to wear**, if the model you are bringing does not take images.
  Section 03 explains why, and it is the difference between the arm seeing you and not.
- **A way to cut power fast.** A power strip with its own switch, or a firm decision about
  which plug you are going to pull.
- **Permission, and the arm's own parts list.** Whoever runs the lab should confirm that you
  may connect your own laptop, and which supply that specific arm takes. Motors and supplies
  vary between builds and neither LeRobot nor quackd reads the voltage, so neither can warn
  you.
- **A key for one cloud vendor, or a local model server.** Section 03.

<br>

### 02. Install quackd

Python 3.12 or newer, in a clean environment of its own. quackd's own floor is 3.11, but the
`lerobot` extra carries a `python_version >= '3.12'` marker, and below that floor it resolves
to nothing at all while the install still reports success.

Every command in this guide uses [uv](https://docs.astral.sh/uv/). quackd does not require
it, it is simply what the documented commands assume.

```bash
uv venv --python 3.12
uv pip install "quackd[lerobot]"
```

Add the extra for whichever pilot you picked in the next section, in the same install. The
arm and the model are independent choices:

```bash
uv pip install "quackd[lerobot,anthropic]"   # or openai, gemini, grok, mistral, deepseek, ...
uv pip install "quackd[lerobot,openai]"      # the openai extra also serves every local server
```

Then check the machine:

```bash
quackd doctor
```

Two rows decide whether a serial port can be opened at all:

```
- lerobot                    not installed (quackd[lerobot])
- lerobot (feetech bus)      not installed (quackd[lerobot])
```

Both have to be green or the port will not open. The second one is the trap: the Feetech SDK
lives in LeRobot's own `[feetech]` extra rather than in its base dependencies, so a plain
`pip install lerobot` gives you a package that imports perfectly and then cannot talk to a
motor. `quackd[lerobot]` asks for `lerobot[feetech]` for exactly that reason.

> [!WARNING]
> If `lerobot` still reads `not installed` after an install that succeeded, check
> `python --version` before you reinstall anything. On 3.11 the extra resolves to zero
> packages, silently, and `doctor` has nothing else to complain about.

<br>

### 03. Choose your pilot

quackd never ships a model. You bring one, and the choice is one flag, `--llm VENDOR[:MODEL]`.
Eleven cloud vendors have a name you can pass it, five local presets cover the common
self-hosted servers, and `fake` is a scripted pilot that needs no key and is not a model at
all. The vendor on its own means that vendor's default model, `--llm openai:gpt-6-astra` names
one, and a catalogue id unique to its vendor needs no vendor in front of it, so
`--llm gpt-6-astra` is the same run.

`quackd doctor` prints a row per provider with the extra, the key it found and the model it
would use, and `quackd list-models` prints every model id quackd knows for every vendor.

#### A cloud vendor

Put the key in the environment or in a `.env` file, then name the provider. The key variable
per vendor is in [`.env.example`](../.env.example) and in `quackd doctor`.

```bash
quackd run lerobot-lookout --robot lerobot:real --address COM5 --llm openai
```

> [!NOTE]
> The vendor for Claude is spelled `anthropic`. There is no `--llm claude`, although
> `--llm claude-opus-5` works, because a model id unique to its vendor names that vendor on its
> own. An unknown name is refused before anything connects, and the message says both shapes
> that would have worked and lists every vendor.

#### Where the key goes

This cost time at the lab, so it gets its own subsection. quackd reads a `.env` from the folder
you are standing in when you type the command, and then from beside its own install, walking up
the folders above it, which is how it finds the one a `uv venv` user put in their venv root.
This is the layout that worked, verified on the machine that drove the arm:

```
D:\Development\lerobot-test\
├── .venv\
│   ├── Lib\
│   ├── Scripts\
│   ├── share\
│   ├── .env            <- the only entry here you create yourself
│   ├── .gitignore
│   ├── .lock
│   ├── CACHEDIR.TAG
│   └── pyvenv.cfg
├── outputs\
└── runs\
```

> [!NOTE]
> uv and the packages you install write everything else there, and a venv straight out of
> `uv venv --python 3.12` is shorter than the tree above: `Lib\`, `Scripts\`, `.gitignore`,
> `CACHEDIR.TAG` and `pyvenv.cfg`, and nothing more. `.lock` appears with the first install,
> and `share\` comes from a package that ships files of its own, which lerobot does. So a
> folder with fewer entries than this is the right folder, not a broken one.

and the file itself is one line:

```
OPENAI_API_KEY=sk-...
```

`quackd doctor` prints a row per provider with the key it found, masked down to its first four
and last two characters, which is the quickest way to see whether your file was read at all.

> [!NOTE]
> Either place works, so put the file wherever you will remember it: next to the command you
> type, or in the venv root as above. Neither file overrides a variable that is already in
> your shell, so a key exported by hand wins over both. Keep one file rather than two: the
> folder you are standing in is read first and nothing read afterwards replaces a name it
> already set, so two files that disagree resolve in an order you have to remember.

> [!WARNING]
> The variable name is case sensitive everywhere except Windows. The file at the lab read
> `OPENAI_API_Key`, which Windows happily resolves and macOS and Linux do not, so that same
> file would have found no key at all on either. Copy the name out of
> [`.env.example`](../.env.example) rather than typing it.

#### A local model, no key

Five presets, all of them served by the `openai` extra because they all speak the OpenAI
wire format. Start your server, then name the preset:

```bash
ollama pull qwen3:8b
quackd run lerobot-lookout --robot lerobot:real --address COM5 --llm ollama:qwen3:8b
```

The other presets are `vllm`, `llamacpp`, `lmstudio`, and `local` for anything else, which
takes `--base-url http://host:port/v1`. Servers need their own tool-calling switches turned
on: vLLM wants `--enable-auto-tool-choice --tool-call-parser <family>`, and llama-server
wants `--jinja`. [local-llms.md](local-llms.md) has the per-server detail, including the
JSON text fallback quackd uses when a server is weak at native tool calls.

> [!WARNING]
> If an `OPENAI_API_KEY` is in your environment, a local run sends it to your local server as
> the bearer token, because that is the last fallback before the literal string
> `not-needed`. Set `LOCAL_API_KEY=not-needed` or pass `--api-key not-needed` if that server
> is not yours.

#### The one that decides whether the arm can see you

Whether the camera frame reaches the model at all is a per-provider default:

| Pilot | Frame reaches the model? |
|---|---|
| A cloud vendor, default model | Yes, on every step, for most vendors |
| A cloud model marked `no frames` | No. The text detections go instead, silently |
| Any local preset | **No by default.** `--vision` turns it on |
| `--llm fake` | Never. It is a rule, not a model |

`quackd list-models` prints `no frames` in the notes column for the models that do not take
images, and it is worth reading before you promise yourself the arm will see anything. One
vendor's own default model is marked that way, so a bare `--llm glm` run never sees the
webcam.

`--vision` and `--no-vision` override all of it in both directions. A local pilot that is
meant to look at you needs `--vision` **and** a vision-capable model loaded in the server.

#### The scripted pilot, and its one job

`--llm fake` is the default and needs no key, no extra and no network. It is not a
model: it is a small set of rules that picks its script from the task file's name, or from a
few keywords in a goal.

> [!CAUTION]
> `fake` cannot do the wave, and it will not tell you so. A goal it does not recognise falls
> through to a generic script whose only two moves are `quack` and `search_scan`, and it
> checks the allowlist before reaching for either. An arm has neither, so it goes straight to
> declaring success: `--goal "wave to me" --llm fake` ends with every joint exactly
> where it started, and exits 0, with no refusal printed to warn you. Worse, a goal containing
> the word *person* selects the patrol script, which reaches for verbs this body does not
> have and gets refused one at a time. Use `fake` to prove the wiring in section 08, and a
> real model for anything that has to think.

> [!NOTE]
> There is a third thing you can put in the loop and it is not a pilot. `--decision-llm` adds an
> optional non-generative stepper in front of whichever model you picked, for the turns whose
> answer is a choice among calls this arm already has. It is off unless you name one, it needs
> `quackd[decision]` (or `quackd[laya]`) and, for the hosted [`jev`](decision-llms/jev.md), a
> key of its own, and it never authors a joint angle. Leave it off until the arm has waved: the
> first run is about proving the arm, the port and the camera, and one more moving part between
> you and the arm is the opposite of what a first run wants.
> [Section 15](#15-optional-put-a-decision-llm-in-front-of-the-model) is how to add one
> afterwards, and [decision-llms.md](decision-llms.md) is where they live, one page each.

<br>

### 04. At the lab, before power

Everything here happens with the arm still unplugged.

- Clear the whole sweep the arm can reach, not just the desk footprint. An arm sweeps a
  volume rather than occupying a spot.
- Take anything fragile out of the gripper and off the desk within arm's length.
- Confirm your switch, or the plug you have decided on, is within reach of where you will
  actually be standing.
- Confirm with whoever owns the arm which supply it takes, and that it is already the right
  one.

<br>

### 05. Find the port, then calibrate

This is the step that is easiest to skip and cannot be. quackd refuses to drive an arm that
is not calibrated, because the calibration file is where every joint's travel comes from,
and calibration is LeRobot's own interactive tool which quackd never triggers.

Find the port first:

```bash
lerobot-find-port
```

It lists the ports, asks you to unplug the arm, and names the one that disappeared. That is
worth doing even when you are sure, because it is the only answer that is not a guess. On
Windows the port is `COMx` and also appears under Ports (COM & LPT) in Device Manager; on
Linux it is usually `/dev/ttyACM0`, and upstream's own fix for permissions is
`sudo chmod 666 /dev/ttyACM0`, with adding your user to that port's group being the version
that survives a reboot. If no port appears at all, suspect the cable or the barrel jack
before you go looking for a driver: USB does not power the controller board.

Then calibrate. Nothing moves on its own here. The tool asks *you* to move each joint
through its range.

```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=COM5 --robot.id=arm-01
```

If a calibration file for this id already exists, which it does whenever you calibrate again,
the tool first asks
`Press ENTER to use provided calibration file associated with the id arm-01, or type 'c' and press ENTER to run calibration:`.
Type `c`. Enter keeps the old travel, writes it back to the motors and records nothing. Next it
asks you to move the arm to the middle of its range of motion and press Enter. Only then does it
ask you to
`Move all joints except 'wrist_roll' sequentially through their entire ranges of motion`, and
that prompt is where each joint goes all the way into the fold.

**When it asks you to move every joint through its range, take each one all the way into the
fold you will rest the arm in.** The travel it records is written into each servo as its
limits, and the servo is never driven past them afterwards. A shoulder that never went all the
way back during calibration leaves its own fold outside its travel, which is what happened on
the bench on 2026-09-23, and [section 07](#07-record-the-rest-pose) says what that costs.

> [!IMPORTANT]
> The id has to be the one quackd will use, which is why the command above says `arm-01`:
> [section 06](#06-first-contact) registers this arm under that name, and a registered name
> becomes the manifest id and therefore the calibration id quackd goes looking for. Register
> it as `lab-arm` instead and you have to calibrate as `lab-arm`. Two arms sharing an id share
> one file with nothing in it to say which arm it came from, so if this is not the only SO-101
> in the room today, pick a name nobody else is using and use that name in both places.

It writes `<calibration dir>/robots/so_follower/<id>.json`, where the directory is
`$HF_LEROBOT_CALIBRATION`, else `$HF_LEROBOT_HOME/calibration`, else
`$HF_HOME/lerobot/calibration`. You do not have to hunt for it: `doctor` prints the path it
actually loaded, which is the fastest way to see that you calibrated `arm` and are
connecting as `arm-01`.

> [!NOTE]
> Upstream will ask you to move every joint through its range **except** `wrist_roll`, and it
> records a full encoder turn for that one rather than anything you swept. That is not a
> mistake you can correct. It is why the out of range refusal in section 11 works on the
> other body joints and cannot fire on that one.

<br>

### 06. First contact

The first command that energises the arm. It connects, reads, and moves nothing.

```bash
quackd doctor --robot lerobot:real --address COM5
```

> [!CAUTION]
> Support the arm while this starts. `configure()` runs with torque off, so connecting drops it
> for a moment whatever else is true, and an arm folded somewhere awkward falls at that moment.
> If a packet is lost or garbled at connect, quackd prints a
> `connect attempt 1 of 3 failed on ...` warning and connects again, and each attempt drops
> torque for its own moment. Support it at the end too, for now: until
> [section 07](#07-record-the-rest-pose) has recorded a rest pose there is nothing for quackd to
> put the arm back to, so LeRobot's `disconnect()` disables torque where the arm stands, a
> `doctor` probe included. Once a pose is recorded, `doctor` returns the arm to it and leaves
> torque on if it cannot get there.

Read four things off it:

- The calibration file it found is the one you just wrote.
- Each joint's range looks like the travel you swept.
- Torque is on.
- What the servos report for temperature with the arm cold. Write that number down. It is
  the baseline for everything later.

#### Name it

Every command from here on names the arm rather than respelling the backend and the port, and
the next section needs a name to keep a pose under. Register it with the same id you
calibrated:

```bash
quackd robot add arm-01 lerobot:real --address COM5 --llm openai
```

```
✓ added arm-01: lerobot:real at COM5
  quackd run <duck> --robot arm-01
```

`--llm` is whichever pilot you settled on in section 03, and it becomes this robot's default,
so a run that names no pilot of its own uses it. The registry file is `~/.quackd/robots.json`
and `quackd robot show arm-01` prints everything in it.

> [!IMPORTANT]
> The name you register is the manifest id, and the manifest id is the calibration id quackd
> looks for. `arm-01` here is the reason section 05 calibrated `arm-01`. If you registered a
> different name, calibrate that name instead, or `doctor` will report an arm with no
> calibration file and refuse to drive it.

Then prove the name resolves to the same arm:

```bash
quackd doctor --robot arm-01
```

It should print exactly what the command before it printed, with the address coming from the
registry instead of from your hand.

<br>

### 07. Record the rest pose

An SO-101 has no brake. It holds its own weight up because torque is on, and LeRobot's
`disconnect()` disables torque by its own default, which quackd asked for. So until quackd had a
rest pose, the arm went limp and fell at the end of every clean run, and at the end of every
`doctor` probe: that is what happened on the bench on 2026-09-15, on every run of the day.
Runs also began from wherever the last one left the arm, so no two started from the same
shape.

A rest pose fixes both. It is one folded pose, recorded once, that the arm can hold with
torque off because it is resting on itself or on the desk rather than held up.

**Fold the arm by hand first, with nothing connected.** An arm nobody has connected has no
torque on it, so it is limp and you can move it. Fold it low and compact, into the shape you
would leave it in overnight. Then record it:

```bash
quackd robot rest-pose arm-01
```

It connects, reads every joint, prints them, asks you whether that is the pose, and keeps the
answer in the registry beside the address and the camera. The capture below is the mock arm
with `--yes`, which answers the question for you, so the numbers are the mock's and yours will
be your own folded arm's:

```
arm-01 (lerobot:mock) is at
shoulder_pan   0.0
shoulder_lift  -90.0
elbow_flex     90.0
wrist_flex     0.0
wrist_roll     0.0
gripper        100.0
✓ recorded arm-01's rest pose (6 joints)
  quackd run <duck> --robot arm-01 starts from it and returns to it before letting go
```

> [!NOTE]
> That last line has one exception, and it is a flag rather than a fault. `--by-hand` starts a
> run from a pose you set with your own hands instead of from the recorded one. The recorded
> pose is still where the arm goes first, because it is the only place `--by-hand` will let go
> of it, and it is still where the arm folds back to at the end.
> [Or start from a pose you set by hand](#or-start-from-a-pose-you-set-by-hand), below, is the
> whole of it.

Without `--yes` the same joint table appears and then the question, and nothing is written
until you answer it. `--json` prints the whole registry entry instead and needs `--yes` with
it, because a script cannot answer a prompt: run with no terminal to ask on and the command
refuses with `no terminal to ask on: pass --yes to record it` rather than guessing.

> [!WARNING]
> A pose the arm cannot hold with torque off is a pose it will fall from, and quackd cannot
> tell the difference: it reads the angles you folded the arm into and believes you. Let go of
> the arm before you run the command and watch whether it stays there. If it sags, fold it
> lower and record again.

**The fold has to be inside the travel your calibration recorded**, which is why
[section 05](#05-find-the-port-then-calibrate) asked you to take every joint all the way into
it. A servo on this arm is never driven past the limits calibration wrote into it, so a fold
outside them is a pose the arm can rest in and cannot be driven back to. That is the bench of
2026-09-23. The arm's calibration had never seen the shoulder folded all the way back, the
rest pose lay about 20 degrees past the floor of that joint's travel, and the rest move could
not get there: runs aborted before the first model call, every run that got to its end kept
torque on and finished at the power switch, and the `stop` at the end of a run hauled the
folded shoulder up out of its fold.

If your fold lies outside the travel anyway, `rest-pose` says so as a warning before it asks,
naming the joint, the angle you folded it to and the edge of its travel, and records the pose
all the same. From then on quackd parks the arm at the edge of the travel, counts that as
reaching the pose, lets go of it there, and says once per run which joint is free to settle
the rest of the way ([the table below](#what-a-run-then-does-with-it)). A `stop` never writes a
goal for a joint that reads past its travel, so it does not start a folded joint rising again,
and it says which joints it left alone. It cannot stop a rise a move has already started, since
any goal past the travel is the limit to the servo: that stretch is the power switch's. The fix
is still a calibration that saw the fold: calibrate again, typing `c` at the tool's first
prompt ([section 05](#05-find-the-port-then-calibrate)), and record the pose again.

**Calibrating again makes the pose stale.** A joint's zero in degrees is the middle of the
travel its calibration recorded, so a calibration that records a different travel moves that
zero, and the angles you recorded before it name a different shape after it. Record the pose
again after every `lerobot-calibrate`, and read any task or remembered note that names an angle
as meaning a different pose too.

#### What a run then does with it

| When | What happens |
|---|---|
| The start of a run | the arm is driven to the pose before the pilot is given control, so what a model improvises from is the same arm every time. A run that cannot get there aborts before a single LLM call is made |
| The end of a run | between the `stop` and the disconnect, on every exit path there is: success, failure, infeasible, out of budget, an abort, an error, and Ctrl-C |
| Torque, at the end | released only where the arm is known to have reached the pose. Where it has not, a run at a terminal first offers, for 60 seconds, to release it into your hands, which is Enter while you hold it. Otherwise, or with the offer left unanswered, quackd turns LeRobot's disconnect flag off, leaves the arm holding itself up, and says so in one line that names `quackd robot release arm-01` |
| A fold past the travel | the arm is driven to the edge of the travel instead, which counts as reaching the pose, and torque is released there. A joint already folded past the edge is at rest where it is and is sent nothing. The run says once which joint is free to settle the rest of the way, `doctor` prints the same sentence under its table with the `rest pose` row green, and an MCP session logs it |
| `--dry-run` | nothing. A dry run never moves the arm, at either end, so unless the arm happens to be at the pose already it is let go of with torque on |
| `quackd doctor` | a probe returns the arm to the pose too, and prints a `rest pose` row: `at it already`, `returned to it`, `not reached: ...`, or `none recorded (quackd robot rest-pose <name>)` |
| `quackd robot list --probe` | does not move the arm, but it connects, and connecting takes torque off every motor for a moment. Unlike `doctor` and `robot release` it does not warn first, so hold an arm that is holding itself up before you probe it. It says `torque left on: not at its rest pose` when it had to keep holding it, and `torque unknown: the arm did not answer the close` when it could not tell |
| `quackd run --by-hand` | the rest move still happens first, and torque comes off there instead of the pilot being given control. You lift the arm and set the starting pose yourself, quackd holds what you left, and the end of the run is the ordinary one |

Said plainly, because it is a change in behaviour rather than an addition: a probe or a dry
run on an arm that is away from its recorded rest pose now leaves torque **on**, where it used
to drop it. The arm stays up instead of falling, and it stays energised until you release it
while you hold it, run something that can put it down, or cut its power.

The line when it could not get there reads:

```
the arm is not at its rest pose (...), so torque was left on and it will not fall as it
stands: hold it first, because connecting takes torque off every motor for a moment, then
run quackd robot release arm-01, or quackd doctor --robot arm-01 to park it, or cut its power
```

Something is in the way, or a servo tripped. A fold past the calibrated travel is no longer
one of the reasons, since the edge of the travel counts as there. The arm is still energised
and still holding itself up, so do not walk away from it, and you do not have to reach for the
power switch either. A run at a terminal asks you first, just before that line:

```
the arm did not reach its rest pose (...), so it is holding itself up. Hold it and press Enter
to release torque now. Leave it, and after 60 s it stays that way
```

Hold the arm and press Enter within the 60 seconds, and it lets go into your hands. Leave it, or
press Ctrl-C, and torque stays on. It is never offered on a dry run, in an MCP session, or over
an arm that stopped answering. If you missed it, or the arm was left up by any of those, hold it
and run:

```bash
quackd robot release arm-01
```

It says what is about to happen before it touches anything, asks, and only then connects and
lets go wherever the arm stands. Captured on the mock arm, with `y` typed at the question:

```
⚠ connecting takes torque off every motor for a moment, because LeRobot configures them with it
off, and the release then lets the arm fall from wherever it is: hold it now, and keep hold of it
until it is down
release torque on arm-01? [y/N]: y
arm-01 (lerobot:mock) is at
shoulder_pan   0.0
shoulder_lift  -90.0
elbow_flex     90.0
wrist_flex     0.0
wrist_roll     0.0
gripper        100.0
✓ torque reads off on every joint of arm-01
⚠ the arm is limp and in your hands (torque was taken off where it stood, because you asked for
it): put it down before you let go of it, because nothing is holding it up
```

Keep holding it until it is down: the warning's first half is true of a real arm, whose connect
takes torque off every motor for a moment, and the mock only prints it. The command exits 1
unless every motor read torque off, and names the ones that did not.
`quackd doctor --robot arm-01` is the other way out: it tries the rest move again from wherever
the arm now is and lets go at the pose if it gets there. Hold the arm for that one too, because
it connects as well, and it says so before it does. The power switch is for when neither of them
can reach the arm. What each outcome means is in
[adapters/lerobot.md](adapters/lerobot.md#releasing-it-where-it-stands).

Two details worth knowing before they surprise you:

- **Only the five body joints are ever driven.** The gripper is recorded and printed, and it
  is never commanded, for the same reason `stop` leaves it alone: re-sending it would open a
  hand that is holding something.
- **The pose is sent clipped into the travel, joint by joint.** Where a joint of the pose lies
  past its travel, the goal is the edge of the travel, and that joint is at rest anywhere from
  5 degrees short of the edge out past it on the side of the fold. Every other joint is at rest
  within 5 degrees of the angle you recorded. Captured on the mock arm, whose `shoulder_lift`
  travels -100 to 100, registered with that joint recorded at -118:

  ```
  ·  note    moving to the rest pose
  ·  note    at the rest pose
  ·  note    shoulder_lift is recorded at -118 in the rest pose and this calibration lets its servo be driven to -100 and no further, so it parks there and is let go of there, free to settle the rest of the way on its own. Calibrate again with the arm folded (lerobot-calibrate) and record the pose again (quackd robot rest-pose arm-01) to make the fold reachable
  ```

  Whether a real joint let go at the edge settles onto its fold, and gently, is one of the
  things [section 14](#14-what-to-report) asks.

To forget the pose:

```bash
quackd robot rest-pose arm-01 --clear
```

```
✓ cleared arm-01's rest pose
  a run now leaves the arm where it stands, and torque drops there
```

> [!NOTE]
> Only the LeRobot arm is parked today. Every other body refuses a rest pose rather than
> accepting one and quietly ignoring it: a body with no joints says so, and a body with joints
> that quackd does not drive home says that only the LeRobot arm does this today.

#### Or start from a pose you set by hand

The rest pose is one shape, and every run so far begins there. That is exactly what you want
when the question is what a model does with a known arm, and exactly what you do not want when
the work starts somewhere else: a pencil already between the jaws, the tip already on the
paper, the arm already over the corner of the desk where the task happens. Asking a model to
get there costs several moves, lands somewhere slightly different every time, and cannot put
anything into the gripper at all, because nobody is there to hand it over.

`--by-hand` is the other way in. You put the arm where the run should start, with your own
hands, and quackd holds it there:

```bash
quackd run --goal "draw a small circle" --robot arm-01 --llm openai --by-hand
```

What happens, in order, and the order is the whole of the feature:

1. **The arm drives to its recorded rest pose**, exactly as any other run does.
2. **quackd takes torque off, there and nowhere else.** This is the one call in quackd that
   de-energises a robot, and through this door it refuses anywhere but the recorded pose,
   because an arm held up by torque alone falls the moment torque goes and your hands are not
   on it yet. Where the run printed the note about a joint recorded past its travel, "there" is
   the edge of that joint's travel, and the joint is left to settle the rest of the way into its
   fold on its own the moment torque goes, so keep a hand near it. `quackd robot release` is
   its other door, and it asks you to hold the arm first.
3. **It tells you the arm is yours, and waits.** There is no clock on this wait. The arm is
   resting on itself at the pose you watched it hold when you recorded it, or, where a joint
   was let go at the edge of its travel, wherever that joint settled, so it can sit there while
   you go and find the pencil.
4. **You lift it, load the gripper, close the jaws on whatever it is with your fingers, and
   press Enter.** Lift it clear of the fold first: quackd takes hold only with every joint
   inside its travel, so a joint still lying in a fold past the edge of its travel ends the run
   with the arm limp.
5. **quackd takes hold of what you left.** It writes the position the arm is in as the goal
   *before* it enables torque, writes it again afterwards, waits a tick, reads back, and tells
   you that you can let go. Only if every joint you placed is inside its calibrated travel,
   though. A goal written where a joint past it is lies past the travel too, so torque would
   pull the joint to the end of its travel, and with no goal written its servo may drive it to
   the last goal it had, so quackd leaves torque off, names the joint, where it reads and its
   travel, and ends the run with the arm still in your hands. Nothing takes hold of it after
   that, even once you move the joint back inside, and nothing folds it: put it down, and run
   again. A fold that lies past the travel counts, which is why step 4 lifts the arm clear of
   it: press Enter with the arm still lying there and quackd says the arm is still limp at its
   rest pose and which joint to lift. If quackd asked for torque and could not read back
   what came of it, it says it cannot confirm whether the arm has torque instead: hold the arm
   as though it may move or drop, and cut its power to be sure. If the read found some motors
   on and the rest off, it names the joints that hold, says the rest is limp, and tells you to
   keep hold of the arm and cut its power.
6. **The pilot runs**, from your pose rather than from the fold. The budget clock restarts
   here, so the time you spent finding the pencil is not taken out of the model's minutes.
7. **At the end the arm holds where it ended**, and quackd asks you to take out whatever is in
   the gripper before it opens the gripper, waiting up to two minutes for an answer.
8. **Then the ordinary teardown**: the rest move, and torque released there.

Everything quackd says to you, in the order it says it, with the arm placed at `shoulder_lift`
-20, `elbow_flex` 40, `wrist_flex` 15 and the gripper squeezed to 35:

```
the arm is yours: torque is off at its rest pose, so lift it, put whatever it needs in
the gripper, close the gripper on that, hold it where you want the run to start, and
press Enter
holding the pose you set, you can let go. It is at elbow_flex 40, gripper 35,
shoulder_lift -20, shoulder_pan 0, wrist_flex 15, wrist_roll 0
the run is over and the arm is holding where it ended. Take hold of whatever is in the
gripper and press Enter, and the gripper opens before the arm folds up. Leave it and
the arm folds up with the gripper shut
```

[Section 08](#08-the-first-task) puts a whole run's log around those three lines.

**Read the middle line rather than skimming it.** Those are the angles the arm reported after
torque came back on, not the angles you thought you left. quackd compares them against what it
measured a moment earlier and refuses if any joint has moved more than five degrees, because
the interesting failure here is silent: a heavy forearm sags a little as it takes its own
weight back, and a run that started five degrees below the pose you set would look exactly like
a run that started at it. The refusal names the joint and the gap, the run aborts before the
pilot's first turn, and torque stays on, because an arm that moved is still an arm that is
holding itself up.

**What the pilot is told.** A by-hand run adds a section to the system prompt, so the model is
not left to infer a strange starting shape from the joint angles alone:

```
## Where this run starts
A person placed this body by hand before your first turn, and quackd is holding it exactly
where they left it. This run does **not** start from the recorded rest pose, so do not assume
a folded arm or a known shape: read `report_state` and work from the joint angles it gives you. They
are where somebody decided the work should begin.

The gripper is where their fingers closed it, which is a position and not a grip. Nothing is
reported as held, and nothing should be: closing on an object is what makes this body say it is holding something, so if the task needs a firm hold on what is already between the jaws, call `gripper` to close on it before you lean on it.

When the run ends, the arm is handed back the same way: it holds where you left it, the person
is asked to take whatever is in the gripper, and only then does quackd fold the arm up.
```

**The gripper is a position, not a grip**, and that middle paragraph is the part worth
believing. Taking hold writes every joint's measured position back as its own goal, the
gripper's included, so the jaws are commanded to exactly the width your fingers left them at
and are never squeezed tighter. quackd then calls nothing held, and it is right not to:
holding on this arm is inferred from the gripper having been *told* to close and then settling
short of shut, and a goal of 35 out of 100 is not a close. So `report_state` reads `holding
nothing` with a pencil plainly in the jaws, and the observation the model reads says the same.
That is not a bug to work around. If the task needs a real hold rather than a resting width,
the pilot closes the gripper on what is already between the jaws with the `gripper` verb, and
from that moment the arm reports it as held.

**Ctrl-C while the arm is in your hands** ends the run the way any other abort does, which
means the teardown picks the arm back up before it folds it: torque comes on where you are
holding it, the arm travels to its rest pose, and only there does torque drop. Keep hold of it
and keep your fingers clear of the jaws until it has stopped. The run is recorded as aborted
and the pilot never gets a turn. With a joint outside its travel, a fold you never lifted it
out of included, none of that happens: quackd tells you it did not take hold, naming the joint,
and leaves the arm as it is. [Section 11](#11-prove-the-safety-net) has this window and the
other new one in full.

**Five things `--by-hand` refuses**, none of which leave a run directory behind. The first
three are checked before quackd connects to anything, and the last two before the arm is
released:

```
✗ error: --by-hand is one person placing one arm, and this run has several robots
  drop --flock and --robots
✗ error: --by-hand and --dry-run ask for opposite things: one takes torque off the arm,
the other moves nothing
  rehearse the task with --dry-run, then run it again with --by-hand
✗ error: --by-hand waits for you to press Enter, and there is no terminal to ask on
  run it from a terminal, or drop the flag and start from the rest pose
✗ error: microduck:mock is not a body a person places by hand: only the LeRobot arm is
  quackd list-adapters
✗ error: --by-hand releases the arm at its recorded rest pose, and this arm has none
recorded
  quackd robot rest-pose arm-bare
```

The third one is the one that catches people. `--by-hand` is a conversation, so a run started
from a script, a scheduler or a CI job has nobody to talk to, and quackd says so up front
rather than releasing an arm into an empty room and waiting for an Enter that is never coming.

> [!WARNING]
> Everything in this subsection has been exercised against `lerobot:mock` and in the test
> suite, and not yet on a real arm. The two steps that need real servos are the release, where
> a pose you recorded but never let go of could turn out not to hold, and the re-energising,
> where nothing upstream documents what a servo does with the goal it was last told. Have a
> hand on the arm for both the first time, and see [what to report](#14-what-to-report).

<br>

### 08. The first task

`lerobot-lookout` ships with quackd, moves no joint, and asks only for `report_state`. It is
the first thing to point at an arm nobody has driven, and it is the first thing that ran on
the bench arm on 2026-09-15, both with a real pilot and once with `--llm fake`. The
scripted pilot is enough here, because there is nothing to improvise:

```bash
quackd run lerobot-lookout --robot arm-01 --llm fake
```

Expect one sentence naming where the joints are, whether torque is on, and whether anything
reads hot. A joint at or above 60 degrees Celsius is hot and worth naming; the servo's own
cut-off is 70.

The run also says where the arm is against the pose you recorded, once as it starts and again
as it finishes, so the four lines below are two pairs rather than one. This is the mock arm
again, which was already at its pose both times:

```
·  note    moving to the rest pose
·  note    already at the rest pose
·  note    moving to the rest pose
·  note    already at the rest pose
```

The arm was already folded there, so nothing moved. On a real arm the second line reads `at
the rest pose` when it had to travel to get there.

**The same command with `--by-hand` is the first hand-placed run worth making**, and for the
same reason it is the first run of any kind worth making: `lerobot-lookout` moves no joint of
its own. Whatever pose you put the arm in is the pose it is still in when the pilot has
finished, so the only thing under test is the hand-off itself, and a mistake costs you a
re-fold rather than a collision.

```bash
quackd run lerobot-lookout --robot arm-01 --llm fake --by-hand
```

The whole of it, on the mock arm, with the arm placed at `shoulder_lift` -20, `elbow_flex` 40,
`wrist_flex` 15 and the gripper squeezed to 35:

```
·  note    moving to the rest pose
·  note    already at the rest pose
·  hand    released: torque is off at the rest pose
the arm is yours: torque is off at its rest pose, so lift it, put whatever it needs in
the gripper, close the gripper on that, hold it where you want the run to start, and
press Enter
·  hand    held: holding the pose you set (elbow_flex 40, gripper 35, shoulder_lift -20, shoulder_pan 0, wrist_flex 15, wrist_roll 0)
holding the pose you set, you can let go. It is at elbow_flex 40, gripper 35,
shoulder_lift -20, shoulder_pan 0, wrist_flex 15, wrist_roll 0
▶  verb    report_state()
✓  result  report_state ok: shoulder_pan 0, shoulder_lift -20, elbow_flex 40, wrist_flex 15, wrist_roll 0, gripper 35; torque on; hottest shoulder_pan 30°C; holding nothing (0.0 s, 0 intents)
the run is over and the arm is holding where it ended. Take hold of whatever is in the
gripper and press Enter, and the gripper opens before the arm folds up. Leave it and
the arm folds up with the gripper shut
→  send    stop
·  hand    unloaded: opening the gripper
→  send    gripper(open=true)
·  note    moving to the rest pose
·  note    at the rest pose
```

Two lines there are worth checking against the arm in front of you. `report_state` reads back
the pose you set rather than the fold, which is the whole point of the flag, and it says
`holding nothing` with the gripper at 35, which is the inference described in [section
07](#or-start-from-a-pose-you-set-by-hand) rather than an empty hand.

Every run writes `runs/<timestamp>-<name>/` with the full transcript, every frame quackd
captured, a summary, and `terminal.txt`, which is everything that was on the terminal during
the run as plain text, opening with the command that started it. `quackd log` replays any of
it afterwards, and the summary now says when the run started and ended, how much of it was
spent waiting on the model, and what the model calls cost.

> [!TIP]
> **Name your runs if you are doing more than a few.** `--run-name "example 1"` puts the name
> on the directory, so the afternoon reads back as
> `runs/20260915-145349-goal-example-1/` instead of thirty timestamps you would have had to
> write down at the time. `quackd log example-1` then finds that run by the name you gave it,
> and prefers an exact match over a newer directory that merely contains the text, so
> `example-1` does not hand you `example-19`.

<br>

### 09. Add the camera

Find which OpenCV index your webcam is, which is the part nobody can guess for you:

```bash
lerobot-find-cameras opencv
```

It lists every camera it can open and saves a frame from each under
`outputs/captured_images/`, so you can look at the pictures rather than guess. On a laptop
index 0 is usually the built-in webcam, so a plugged-in one is often 1 or 2. An index is a
scan position and not an identity: it can move when you replug or reboot. On the bench the
plugged-in webcam was `opencv://1` at first and `opencv://2` later, at 640x480, and it needed
no `?backend=` key on Windows.

Then ask quackd for a frame through it:

```bash
quackd doctor --robot arm-01 --camera-url "opencv://1"
```

Quote the url. A bare `&` is a parse error in PowerShell and backgrounds the command in
bash. The url also takes a device path, `opencv:///dev/video2`, and the query keys `name`,
`width`, `height`, `fps`, `fourcc`, `rotation`, `backend` and `fov`. Add `?backend=msmf` if a
Windows camera lists and then will not open.

A camera you asked for and did not get is a refusal at connect, and it happens before the
arm is touched, so a wrong index costs you nothing but the message.

Once the index is the right one, keep it in the registry so no later command has to carry it:

```bash
quackd robot edit arm-01 --camera-url "opencv://1"
```

```
✓ updated arm-01: camera-url
```

Every `--camera-url` on that command replaces the whole stored set, so naming a camera says
where the cameras are today, the way `--address` already does. `quackd robot edit arm-01
--clear camera-url` takes it away again.

> [!TIP]
> Aim the webcam now at wherever you will actually stand, and pass `--fov-deg` for your lens
> once you know it. Without it quackd assumes the simulator's 90 degrees, says so in every
> detection line, and every bearing and distance is scaled wrong.

#### More than one camera

`--camera-url` repeats. One view of a desk is rarely enough to tell whether the gripper is
above the thing or in front of it, so the arm takes a second camera:

```bash
quackd robot edit arm-01 \
  --camera-url "opencv://1?name=top" --camera-url "opencv://2?name=side"
```

The same pair of flags works on `quackd robot add`, `quackd run`, `quackd doctor` and
`quackd serve-mcp`. This arm is the only body that reads more than one: every other robot
quackd drives refuses a second `--camera-url` with a message naming who takes several, rather
than opening the first and dropping the rest. The rules are few, and all of them are enforced
before the arm is energised:

| Rule | Why |
|---|---|
| With several, every url carries `?name=`, and the names differ | the name is the only thing telling two views apart, in what the model is shown, in a pick policy's observation, and in `frames/NNNN-<name>.png` |
| An index may not repeat | two handles on one webcam is not two views, it is a camera that will not open twice |
| The **first** url is the primary | it is the camera `--fov-deg` describes, the one the `camera:` detections line reports, and the only one the verbs that steer by sight read. Those run at 10 Hz, and fetching every camera there would blow the deadman window |
| A second camera that will not open refuses the whole connect | it happens before the arm is energised, and it lets go of the first camera on the way out |

Every frame reaches the model on every step, each one labelled with its camera name, on
Claude, both OpenAI APIs, Gemini, and any OpenAI-compatible local server with `--vision` on.

A camera that stalls later costs its own picture and nothing else: the others keep arriving,
and `report_state` and `doctor` name which one went, in a `camera <name>` row each. If the one
that died is the **primary**, the other views still reach the model, and the detections line
reports nothing seen, because a bearing read off a different lens would point somewhere else.

> [!NOTE]
> Two cameras is twice the pictures, and the bill is larger than that. The last two exchanges
> keep their images, so two cameras means four pictures in every request rather than two, and up
> to eighteen on Claude Opus 5.5 and Fable 5.1, whose old frames are trimmed every eight
> exchanges rather than on every one. Add the second one because you need the view, not because
> it is there.

> [!WARNING]
> A local server, or the model inside it, may accept only one image per message. If a server
> rejects a request that carries two frames, go back to a single `--camera-url`: nothing in
> quackd can make a one-image endpoint take two.

`robots.json` keeps a string when there is one camera and a list when there are several, so a
registry file written by quackd 0.9 loads unchanged.

<br>

### 10. Rehearse with `--dry-run`

`--dry-run` connects to the arm for real and sends it nothing. Read-only verbs actually run,
so `report_state` reads the servos and the heartbeat keeps its round trip going; every other
verb is printed and skipped. The rest move is skipped with them, at both ends, so a dry run
leaves the arm exactly where it found it.

Rehearse the goal you actually intend to give it:

```bash
quackd run --goal "wave to me" --robot arm-01 --llm openai --max-steps 6 --dry-run
```

Every verb that would move a joint is printed and skipped. This is what that looks like,
quoted from the checklist's own narrower rehearsal, whose goal was to roll the wrist ten
degrees rather than to wave:

```
[dry-run] would run move_joints({'positions': {'wrist_roll': 10.0}, 'duration_s': 2.0})
[dry-run] move_joints not sent
```

A wave is several of those in a row, alternating about a neutral pose. Read three things
off it. That the model reached for the verbs you expected, with arguments
that look sane. That the joint goals are small rather than enormous. And that the arm
answered every heartbeat for the length of the run, because an arm that drops out here would
have dropped out mid move in the next section.

This costs a handful of API calls and is the cheapest rehearsal you will get on the arm
itself. Run it more than once if the plan looks odd. The other kind of rehearsal needs no arm at
all, and moves everything in a model of it: [section 16](#16-between-visits-rehearse-on-the-simulator).

Two of the bench's dry runs on 2026-09-15 ended early, and both endings were the rehearsal
doing its job. One aborted with `the arm did not answer: TimeoutError` when a single heartbeat
round trip failed, and it did not happen again that day or at all since. The other aborted
because the pilot answered `assess_task` with `uncertain` and the person at the keyboard
answered no.

**`--dry-run` and `--by-hand` are refused together**, for what they ask of the arm rather than
for how many robots a run has. A dry run's promise is that nothing reaches the arm at either
end, and taking torque off is not a command to the robot but a change to it, so there is no
honest way to do both:

```
✗ error: --by-hand and --dry-run ask for opposite things: one takes torque off the arm,
the other moves nothing
  rehearse the task with --dry-run, then run it again with --by-hand
```

Do what the hint says, in that order. Rehearse from the rest pose, read the verbs the model
reached for, and then make the real run the hand-placed one. The refusal lands before quackd
connects, so typing both out of habit costs you the message and nothing else.

> [!NOTE]
> `--max-steps` counts verb executions, not model calls. `assess_task` and the declarations
> cost no step. `max_llm_calls` and `max_minutes` exist too, but only a `.duck` file can set
> them.

<br>

### 11. Prove the safety net

From here the [hardware checklist](lerobot-hardware-checklist.md) is the authority on order
and on what a hand stays near. What follows is the same five checks expressed as commands.

Drop `--dry-run`, keep `--max-steps` small, and watch the arm rather than the terminal. The
first movement of each of these runs is not the model's: the arm travels to the rest pose you
recorded before the pilot is given control, and returns to it at the end.

> [!CAUTION]
> This is where the arm starts moving, so from here **a hand stays on the power switch**.
> There is no e-stop, and cutting the servo supply is the only thing that stops this arm in
> every case. Keep the sweep clear and your hands out of it for everything below.

**1. The gripper, and which way it goes.**

```bash
quackd run --goal "open the gripper fully, then close it on nothing, then stop" \
  --robot arm-01 --llm openai --max-steps 4
```

quackd assumes 100 is open and 0 is closed, and that is an assumption about how your arm was
assembled and calibrated rather than a fact about the model. The bench arm agreed: commanded
100 it reported 98 and stood open, and closed it settled at 3 with the jaws almost touching.
That is one arm. If yours runs the other way, stop here and say so in an issue: everything
quackd believes about holding something rests on it.

**2. One joint, small, in the middle of its range.**

```bash
quackd run --goal "roll the wrist ten degrees and stop" --robot arm-01 \
  --llm openai --max-steps 3
```

It takes as long as the model asks for in `duration_s`, anywhere from 0.2 to 12 seconds, which
is five seconds when it names none, and then stops. However short the time asked for, the arm
moves at most five degrees per action re-sent ten times a second, so fifty degrees a second and
ten degrees in a fifth of a second, and `QUACKD_LEROBOT_MAX_STEP_DEG` lowers that if it looks
fast in the room. The goal is
walked out a little further each tenth of a second rather than sent at once, and so is a smaller
one: a nudge of two degrees asked to take three seconds takes the three seconds, where it used
to take a tenth of one.

**3. A goal outside the calibrated range.**

```bash
quackd run --goal "move shoulder_pan to 170 degrees" --robot arm-01 \
  --llm openai --max-steps 3
```

It should be refused with the real range in the reason, and nothing should reach the arm.
Use any body joint except `wrist_roll`, whose recorded travel is the whole turn.

> [!NOTE]
> This one maps imperfectly to a goal run, and it is worth knowing why. You are trying to
> test quackd's range gate, but the model chooses the number, so it may talk itself out of
> the attempt first, or pick a different joint. If the refusal you get is the pilot's rather
> than the executor's, you have learned something about the model and nothing about the
> gate. Over MCP you send the number yourself, which is
> [M11](#m11-prove-the-safety-net).

**4. Pull the USB cable mid move.** Start a longer motion, then unplug the arm. The run
should end within about a second saying the arm did not answer. The arm holds its last goal
under torque: it must not sag and it must not carry on. The run's close cannot read the arm
either, so it ends on this line rather than section 07's, with the reason in the brackets, and
no release is offered, because the arm is not answering:

```
quackd cannot tell whether the arm is holding itself up (...), so it kept whatever torque the
arm has: hold it, and cut its power
```

Hold the arm before you plug the cable back in and before the next command connects, because
connecting takes torque off every motor for a moment. Then put it down with
`quackd robot release arm-01` while you hold it, park it with `quackd doctor --robot arm-01`,
or cut its power.

**5. Ctrl-C mid move.** quackd's kill switch sends `stop`, which re-sends the present position
as the goal of every body joint inside its travel. The arm should freeze where it is rather than
sag, and rather than finish the motion it was in the middle of. `q` at the terminal does the
same thing. Then watch what follows, because Ctrl-C is an exit path like any other: the arm goes
to its rest pose before quackd lets go of it, and if it cannot get there it stays energised and
says so rather than dropping. Press Ctrl-C a second time and quackd quits at once, which is what
the hint under the header offers; if that lands while the arm is on its way to the pose, the
close is skipped and the arm holds where it stopped. LeRobot may still disconnect the arm as the
process lets go of it, and that disconnect keeps torque, because quackd builds the arm asking it
to. In 0.14 and before it was built asking for the release, so the arm could fall there instead.
That is the safe direction and it is still a surprise, so expect it rather than pressing twice
out of habit ([safety.md](safety.md)).

A [hand-placed run](#or-start-from-a-pose-you-set-by-hand) opens two more Ctrl-C windows, and
neither behaves like the one above. **During the placement wait**, with the arm limp in your
hands, Ctrl-C ends the run before the pilot has had a turn, and the teardown begins with a
`stop`, which on a released arm means taking hold of it again. Torque comes back on where you
are standing holding it, the arm then travels to its rest pose, and only there does torque
drop. Keep hold of it until it has stopped, and keep your fingers out of the jaws, because from
the arm's side that is an ordinary teardown and nothing about it is slower for being one. With a
joint outside its travel none of that happens: you are told once that quackd did not take hold
and which joint reads where, torque stays off, nothing is written to the arm and nothing folds
it, even if you then move the joint back inside, and the last line says what quackd read: the
arm limp in your hands, or limp at its rest pose where the joint outside its travel is a fold
you never lifted it out of.
**Inside the end-of-run hand-back**, where quackd is asking you to take whatever is in the
gripper, a second Ctrl-C means skip the gripper rather than abandon the run. The jaws stay
where they are, the arm still parks at its rest pose, the transport still closes properly, and
the log says `the gripper was left as it is, and the arm still folds up`. That is a
deliberate exception to the rule in the paragraph above, and it exists because the first
version was not one: a second Ctrl-C there raised straight through the whole teardown, which
skipped the rest move, the close, the run's own end record and the summary, and left an
energised arm holding a pencil with nothing written down about the run that put it there. A
third press lands somewhere without that guard and still quits at once.

> [!CAUTION]
> If any of these five surprises you, stop. Cut power and read
> [When it will not work](#13-when-it-will-not-work) before going further.

<br>

### 12. Wave to me

Stand where the camera can see you, and ask:

```bash
quackd run --goal "wave to me" --robot arm-01 --fov-deg 62 --llm openai --max-steps 12
```

`62` there is an example, not a default: it is the figure for one common camera module. Use
your own lens's horizontal field of view, and if you do not know it, leave the flag off and
read the uncalibrated warning every detection line will carry.

What should happen: the model reads an observation that includes the camera, answers
`assess_task` with a verdict, and then issues several small `move_joints` calls alternating
about a neutral pose, before stopping. The motion is genuinely the arm's own joints doing
something nobody scripted. That is the whole thesis under test.

If the model answers `uncertain`, the run stops and asks you, and no verb moves the arm until
you answer. The question carries the model's reason, here one of the kind a model gives:

```
The pilot is not sure this robot can do the task: The task turns on how wide the gripper
opens, which its datasheet does not publish.
Go ahead anyway? [y/N]:
```

Answer `y` and the model is told that a person read its doubt and said go, and not to assess
again on that doubt, only on something new it sees. Answer `n`, or just press Enter, and the
run ends there. `--yes` on `quackd run` answers go without asking anybody, and the model is
told that instead. The arm's datasheet publishes a reach of 0.4 m, an estimate from the maker's
URDF, and where a task turns on a figure the sheet leaves out, the model is told to answer
`uncertain` and name the figure rather than decline.

It is also the part that has now happened once. On 2026-09-15 `gpt-6-astra` answered a bare
`--goal` with wrist-roll waves of about 27 degrees either side of where the wrist sat, and in
a later run with a wider gesture: `shoulder_lift` at -39 and `elbow_flex` between 24 and 30,
the whole forearm moving rather than the wrist alone. Another run opened and closed the
gripper, and one mimed a duck quacking with it. None of that is a script in quackd. What your
model does with the same sentence is its own.

For a first attempt, a goal that says more gives you a better idea of what is coming:

```bash
quackd run --goal "If you can see a person in the camera, greet them: move wrist_flex, \
shoulder_pan and elbow_flex back and forth a few times, no more than about 20 degrees from \
where each one is now, in several small moves rather than one big one. Keep any wrist_roll \
move especially small. Do not touch the gripper. Then return to the start and stop." \
  --robot arm-01 --llm openai --max-steps 12
```

quackd's own ceilings hold underneath whatever the model decides. It cannot put a joint
outside its calibrated range, and it cannot move faster than the step cap, however the
request is phrased. What quackd does not cap is force, so the sweep still has to be clear and
your hands still have to be out of it.

#### Whether it can actually see you

This is the honest part, and it differs by pilot.

**A model that takes images** receives the webcam frame on every step and can simply look and
decide. Only the last few exchanges keep their image, two on most models and up to nine on
Claude Opus 5.5 and Fable 5.1, so it cannot compare a frame from a dozen steps ago, but it can
see you now.

Aiming is the part that went wrong on the bench, and it is worth learning from. The webcam was
framed on the gripper, which cropped the raised arm out of the picture, so the model checked
its own waves against the joint angles in `report_state` rather than against anything it could
see. It still waved. It could not watch itself do it. Point the camera at the volume the
arm will move through, not at the end of it, or add a second view as in section 09.

**A model that does not** receives one line of text built by quackd's colour detector, and
that detector carries the simulator's ranges. It emits exactly four labels and `person`
means a saturated blue region, not a human. Wear a strong blue top and fill a decent share
of the frame and you will read as `person at bearing 12° left ~1.40 m`. Wear grey and the
line says `nothing detected`. The distance is computed from the *simulated* person's size,
so treat it as a rough guess rather than a measurement.

> [!WARNING]
> `observe` is not reachable on a `--goal` run even with a camera attached. A goal's
> allowlist is built from the arm's static manifest, and that manifest cannot know whether
> you plugged a webcam in, so it claims no camera. The frame and the detections still reach
> the pilot in every observation, which is how "sees a person and waves" works here. Asking
> for a look as a deliberate act needs an MCP client, which is
> [M09](#m09-add-the-camera).

There is a real person detector behind the `yolo` extra, and `--detector yolo` selects it:
YOLO in quackd's own process on the laptop, which reads a person as `person` whatever they are
wearing. Without the extra the run is refused before anything connects, with
`YoloDetector needs ultralytics: uv pip install 'quackd[yolo]'`. Give it `--fov-deg` for your
webcam, or its distances are a guess and the run says so once. Tuning the colour ranges to
your own shirt has no flag: [the FAQ](faq.md) covers it as a Python constructor.

#### Give it a picture

The camera answers what the room looks like now. It cannot answer what the task is about, and
"draw what is in the picture" is not a sentence a robot's own webcam can be asked. `--image`
hands the task a file instead:

```bash
quackd run --goal "draw what is in the picture" --robot arm-01 \
  --llm openai --image sketch.png
```

The flag repeats, so several pictures can come with one task. **What the pilot receives** is the
picture attached to its first observation and to no other, labelled `task picture sketch.png:`
in front of the image itself and ahead of any camera frame in the same message. Nothing trims it
out of the history afterwards, which is the difference that matters: only the last two exchanges
keep their camera frame, or up to nine on Claude Opus 5.5 and Fable 5.1, and a task picture is
still in front of the model on the last step of a long run. The system prompt gains a section
naming which pictures came with the task and saying, in as many words, that they are not what
the robot can see. The log counts them in the request line, so you can tell at a glance that
they are still going:

```
llm>    step 0: 1 messages (1 with image, 1 task picture) to fake scripted:goal
```

**Where the copies are kept.** Every picture is re-encoded to PNG on the way in, brought under
1568 pixels on its longest edge and under one and a half megabytes, and written to
`runs/<timestamp>-<name>/images/00-sketch.png` beside the transcript, with a `task_image` line
in `transcript.jsonl` naming the file, the original name and the byte count. The copy is
therefore what the model was actually sent rather than the file you pointed at, which is what
makes it worth keeping. Two pictures with the same basename are numbered apart rather than
overwriting each other.

**A pilot that cannot see refuses the flag rather than dropping the pictures**, before a run
directory exists:

```
✗ error: fake scripted:goal does not take images, so it cannot be given 1 picture
  quackd list-models marks the models that take no frames; --vision overrides it where
the vendor does take them, and a local model needs --vision
```

Silently dropping them is the failure this refusal is here to prevent. A model handed "draw
what is in the picture" with no picture improvises something plausible, and the only sign of
why would be a drawing that has nothing to do with your sketch. The same rule covers the
pilots this page has already met. `--llm fake` never takes images, so the scripted pilot
needs `--vision` before it will accept one, and it still does nothing with it. A local model
needs `--vision`, or `QUACKD_VISION=1` in the environment, and it needs an actual
vision-capable model loaded in the server behind that flag. A cloud model marked `no frames`
in `quackd list-models` needs `--vision` too, and only where the vendor really does take them.

Both starts take a picture, and the second is the one this flag was written for:

```bash
quackd run --goal "draw what is in the picture on the paper in front of you" \
  --robot arm-01 --llm openai --image sketch.png --by-hand
```

Started from the rest pose, the model has the sketch and an empty gripper, and the first
problem it has to solve is getting hold of a pencil nobody gave it. Started
[by hand](#or-start-from-a-pose-you-set-by-hand), you put the pencil in the jaws yourself and
set the tip on the paper, so the model begins with the sketch, a known contact point and
nothing to improvise except the drawing. Neither of those has been tried on a real arm, and the
second one is where a pencil either stays put through a move or does not.

#### Making it repeatable

A `.duck` file turns the goal into a contract with an allowlist, a budget and a success test
that the model cannot talk its way out of. It is also the provider-agnostic way to carry this
task around, because `providers:` is a tested-with note rather than a restriction:

```markdown
---
duck: 1
name: wave-hello
description: Notice a person on the camera and greet them with a waving motion.
requires: [move_joints, report_state]
verbs:
  allow: [report_state, move_joints, stop]
  confirm: []
budgets: {max_steps: 20, max_minutes: 3, max_llm_calls: 20}
success:
  - You have moved the body joints back and forth several times and returned to the start.
abort_when:
  - Same verb fails 3 times in a row
providers: [anthropic, openai, gemini]
---

# Task

Greet the person in front of you with a wave, using only small joint moves.
```

Check it against this body before you run it, which happens before anything connects:

```bash
quackd validate wave-hello.duck --robot arm-01
```

Note what the allowlist does **not** contain. A task that so much as allows `observe` is
refused on `lerobot:real` for the reason above, so leave it out and let the frame arrive in
the observation.

<br>

### 13. When it will not work

The arm is not touched by anything in the first group: these all happen before or during
connect.

| What you see | What it means | What to do |
|---|---|---|
| `adapter 'lerobot' needs an extra` | the extra is not in this environment | install it, and check `python --version` is 3.12 or newer |
| `lerobot (feetech bus)` missing in `doctor` | LeRobot is installed without its `[feetech]` extra | reinstall `quackd[lerobot]`, which asks for `lerobot[feetech]` |
| `--address must be the arm's serial port` | no address, or it is not port shaped | Device Manager under Ports on Windows, `/dev/ttyACM0` elsewhere |
| `connect failed 3 times: Could not connect on port ...` | wrong port, or something else already owns it | close any teleoperation, recording or serial monitor, then `lerobot-find-port` |
| `connect attempt 1 of 3 failed on <joint> (id <N>): Failed to write 'Lock' ...`, and the run carries on | a status packet on one of the torque writes (`Lock` or `Torque_Enable`) LeRobot's connect makes to every motor was lost or came back garbled. quackd closed the port without writing anything and connected again, up to three attempts, and the run's transcript keeps the line | nothing, once. Support the arm while it connects, since each attempt drops torque for a moment. A joint named run after run is a cable to reseat |
| `connect failed 3 times, the last on <joint> (id <N>): Failed to write ...` | every attempt failed on a status packet lost or garbled, and the arm may be left with some motors holding and others limp | keep a hand under the arm, check that joint's cable and connectors and the servo supply, make sure nothing else has the port open, then run again |
| `connect failed 3 times, the last on <joint> (id <N>): ... Missing motor IDs: - <N> ...` | that servo never answered its ping: a cable out, no power to it, or an error such as an overload. Nothing had been written yet, so nothing is said about torque | check that joint's cable and connectors and the servo supply, then run again |
| `connect failed 3 times: ... Missing motor IDs:` with every motor listed | no servo answered at all, which is what a servo supply switched off looks like, as after a power cut, or a cable out between the board and the first servo. So no joint is named | check that the servo supply is on, then the arm's cables, then run again |
| `connect stopped after attempt <k> of 3, because a stop was asked for` | you pressed Ctrl-C while the connect was failing, so it was not tried again. The attempt's own failure follows | nothing for the stop. Read the failure as the rows above, and keep a hand under the arm if the message says some motors may be left with torque on |
| `connect failed: a LeRobot call (connect) has not come back within 30 s; ...` and `keep a hand under the arm` | the connect ran past its deadline, and it may have stopped anywhere in the torque writes. It is not tried again | keep a hand under the arm, check the USB cable and that nothing else has the port, then run again |
| `the arm is not calibrated` | the motors do not match a calibration | run `lerobot-calibrate` under the id quackd will use |
| `the arm reports no calibration file` | there is no file for this id | the same fix, and check the path `doctor` prints |
| `--camera-url ... did not open` | wrong index, or it will not open under this backend | try another index, add `?backend=msmf`, or drop a pinned size. The arm was not touched |
| `it has no ?name= and 2 cameras were given` | several `--camera-url` and one of them is unnamed | name every url, `opencv://1?name=top --camera-url opencv://2?name=side`. Nothing was opened |

And once it is running:

| What you see | What it means | What to do |
|---|---|---|
| `is outside this arm's calibrated range` | the goal is outside the travel in your calibration file | working as intended. Aim inside it. On `wrist_roll` this can never fire |
| `reads 61°C: let the arm cool` | the heat gate, below the servo's own 70 °C cut-off | let it cool. A joint that trips its own protection goes slack without announcing it |
| `and it has stopped moving` | a stall: five ticks in which no watched joint moved, counted once the move's `duration_s` is up | something is in the way, or a servo tripped. The arm is held first. A joint blocked early in a slow move is only called stalled at the end of it |
| `the camera gave no frame` | the webcam stalled or was unplugged | the arm carries on, and `report_state` starts saying `CAMERA DOWN:` |
| `the arm's torque is off` | torque reads off | no verb can toggle torque either way. A fresh connect re-enables it, so this points at a tripped servo or the supply |
| the run ends saying the arm did not answer, and closes on `quackd cannot tell whether the arm is holding itself up (...)` when the close could not read it either | the heartbeat's round trip failed. The close's line means quackd cannot say whether the arm is held up or limp. Seen once on 2026-09-15, in a dry run, and not since. One other cause was found later, in the simulator and not on an arm: the event loop's thread busy past the deadline while the arm answered in time, which 0.15.0 fixed. It is not known to be what happened that day | hold the arm and cut its power, then check the cable, the power or a tripped servo. Do not reconnect without holding it, because connecting takes torque off every motor for a moment |
| the arm sags when the run ends | no rest pose is recorded, so torque drops where the arm stands | `quackd robot rest-pose arm-01`, with the arm folded by hand first |
| `the arm is not at its rest pose (...), so torque was left on` | it could not get home: something is in the way, or a servo tripped. The run itself says `the arm did not reach its rest pose`, and at a terminal it offered to release the arm first | hold the arm first, whichever way out you take, because both commands connect and connecting takes torque off every motor for a moment. Then run `quackd robot release arm-01`, which lets it go into your hands, then clear whatever stopped it and run again. `quackd doctor --robot arm-01` tries the fold again instead, and the power switch is for when neither can reach the arm. It stays energised until you do one of them. If you calibrated again since you recorded the pose, record it again: the old angles name a different shape now |
| `torque still reads on for <joints>: cut the power` | `quackd robot release` sent the release and those motors kept their torque | hold the arm and cut its power. The motors not named are limp, and the line after it says what the close did: kept torque, or took it off at the rest pose |
| `... is recorded at ... in the rest pose and this calibration lets its servo be driven to ... and no further` | the fold you recorded lies past the travel your calibration recorded. The arm parks at the edge of the travel and is let go of there, and the run carries on | nothing needs doing today. To make the fold itself reachable, calibrate again, typing `c` at the tool's first prompt, with every joint taken all the way into it, then record the pose again |

[adapters/lerobot.md](adapters/lerobot.md) has the full table, including the failures SO-101
owners report that nobody here has reproduced.

<br>

### 14. What to report

[Open a LeRobot hardware report](https://github.com/rokbenko/quackd/issues/new?template=lerobot-hardware-report.yml),
or a plain issue with the transcript and your `quackd doctor` output. A report that says it
did not work is worth as much as one that says it did.

One arm has been down this path, so some of these questions have one answer and none of them
have two. The five that nobody has measured at all:

- **Whether the holding band is anywhere near right.** quackd calls it holding when the
  gripper is told to close, settles, and settles between 8 and 90 of 100. Nothing was held on
  the bench, so the band has never been tested against an object.
- **What a joint reads in degrees Celsius**, cold and after ten minutes of work. Both the 60
  refusal and the 70 cut-off are Feetech's documentation rather than anything measured here,
  and the bench run was too short to warm anything up.
- **Whether a stall is caught on purpose.** Hold a joint gently against its goal and see
  whether the verb fails with where it stopped. It is called once the move's `duration_s` is
  up, so on a slow move the joint pushes that long first. Nobody has deliberately tried it. The
  rest move's own stall check did fire on 2026-09-23, by accident, when it drove a folded
  shoulder into the servo's limit and the joint stopped there, and it said where. A verb's
  check, on a joint held on purpose, is still untried.
- **Whether five degrees an action felt right** in the room. One person has watched this arm
  move, and they did not write down an opinion on the speed.
- **Whether a slow move is smooth.** `move_joints` walks its goal out a tenth of a second at a
  time across the `duration_s` it is given, and no servo has yet been watched following a goal
  that creeps. Say whether a move of several seconds looked like one motion or a staircase, and
  whether it arrived when the time was up.

And the two the bench answered once, where a second answer is what turns one arm's behaviour
into something true of the SO-101:

- **Which end of the gripper's 0..100 range is open.** On the bench, 100 is open: commanded
  100 it reported 98, and closed it settled at 3. Everything quackd believes about holding
  rests on this being the same on your arm.
- **Which model you used, and whether it could tell you were there.** `gpt-6-astra` waved, and
  it verified its own waves from joint readings rather than from the picture, because the
  camera was framed on the gripper. Whether a model can actually see you, on a camera aimed
  properly, is still open.

Two more arrived with [`--by-hand`](#or-start-from-a-pose-you-set-by-hand), and both of them
ask what a servo does rather than what quackd does, so neither the mock arm nor the test suite
can answer either:

- **Whether the arm stays where you put it when torque comes back on.** quackd writes the
  position it just measured as the goal before it enables torque, because nothing upstream
  documents what one of these servos does with the goal it was last told when it is
  re-energised, and the goal it was last told is the fold. It then writes the goal again,
  reads the arm back, and refuses if any joint has moved more than five degrees. Nobody knows
  whether that refusal ever fires on a real arm, or how far a loaded forearm sags in the
  moment it takes its own weight back. Say which joint moved and by how much.
- **Whether a hand-closed gripper keeps a pencil through a drawing move.** The jaws are left
  exactly where your fingers closed them and are never squeezed tighter, so whatever holds the
  object is the friction at the width you left. Whether that survives the arm actually moving,
  or whether the pilot has to close the gripper properly on it first with the `gripper` verb,
  is the difference between `--by-hand` being useful for a task with a tool in it and being a
  way to set a starting shape and nothing more.

One more arrived with the bench of 2026-09-23, and it only applies if your run prints the note
about a joint recorded past its travel:

- **Whether a joint let go at the edge of its travel settles onto its fold.** quackd parks it at
  the edge, releases torque there, and says it is free to settle the rest of the way. Whether
  it does, whether it drops or eases down, and whether a joint folded past the *top* of its
  travel settles at all, are the servo's and the arm's weight's to answer. Say which joint, how
  far the note said it had to go, and what it did. Then calibrate folded, record the pose
  again, and say whether the note went away.

Three more arrived with the fixes that bench led to, and none of them has run on an arm:

- **Whether `quackd robot release` puts the arm down into your hand.** Hold the arm, run it, and
  say which joints it printed and whether it ended on
  `torque reads off on every joint of arm-01`, or which joints it named as still on.
- **Whether the Enter offer works.** If a run's last rest move misses, whether from something
  in the way or a hand held gently against a joint, say whether the offer appeared, whether
  Enter released the arm, and whether, left alone, the arm still had torque after 60 s.
- **Whether a bad packet at connect is tried again.** If you see
  `connect attempt 1 of 3 failed on <joint> (id <N>)`, say which joint and whether the next
  attempt connected.

The webcam question is closed enough to stop asking: the plugged-in camera was `opencv://1`
and later `opencv://2` at 640x480, and it needed no `?backend=` key on Windows. Say so anyway
if yours needed one, because that is the interesting case now.

If your arm does something this one did not, change the row in
[adapter-status.md](adapter-status.md) and say in the same commit what it did. One SO-101 is
one SO-101: six of quackd's seven bodies have still never been near hardware of any kind, so
most of that page is a description rather than a record.

<br>

### 15. Optional: put a decision LLM in front of the model

The last thing to add at the bench, on purpose, and optional on purpose. Everything above is
about proving one arm, one port, one camera and one rest pose, and a first run wants fewer
moving parts between you and the servos rather than more. Once section 12 has waved, this is the
one thing worth adding at the bench, and it changes nothing you have already established: the
executor, the contract, the allowlist, the confirm gates and the rest pose all behave exactly as
they did.

A **decision LLM** is not a second pilot. It generates no text at all: you hand it a state and a
typed question, and it hands back which option and how confident it is. So it can answer *which
verb now* on the turns where the answer is one of the calls this arm already has, and it can
never author a joint angle, because there is nowhere in its answer for a number to come from.
Every pose, every sentence and the feasibility verdict stay with the model you chose in section
03. The full argument is on [decision-llms.md](decision-llms.md); this section is the arm.

#### Install one

Two extras, and neither is part of `quackd[all]`, because a stepper nobody asked for should not
arrive with everything else:

```bash
uv pip install "quackd[decision]"   # every decision LLM that is a server, hosted or your own
uv pip install "quackd[laya]"       # the one that loads into this process instead (pulls torch)
```

`quackd doctor` then prints a row per decision LLM with the extra, the key it wants and where it
listens, the same way it does for pilots. On a machine with neither extra every row says
`missing`, which is the expected state and not a problem.

#### Shadow first, on this arm

**Do not start with `--decision-mode on`.** Start with `shadow`, which asks a decision LLM every
turn, writes down what it would have chosen beside what the model actually chose, and lets
nothing it says reach the arm:

```bash
quackd run lerobot-lookout --robot arm-01 --llm openai \
  --decision-llm jev --decision-mode shadow
```

The run is byte for byte the run it would have been without the flag. What you get extra is a
`decide=` line per turn in the log and a `decision_shadow` record per turn in the transcript,
each carrying what the decision LLM chose, how confident it was, how long it took, and what the
model chose on the same reading:

```
·  decide= report_state 0.97 vs model assess_task: differs (0.00 s against 0.0 s)
·  decide= report_state 0.97 vs model report_state: agrees (0.00 s against 0.0 s)
```

Those two are real lines, from `lerobot-lookout` on the mock arm with a fake standing in for
the server, which is why both clocks read zero: nothing was asked over a network and the
scripted pilot answered instantly. On your arm with a real pilot the second number is seconds
and the first is what you came to find out. Note the second line: agreeing is the common case,
and the first line differing on `assess_task` is the stepper being offered a verb it is not
allowed to author.

That is the whole point of shadow mode on a real arm. Nobody has run any of these against
hardware, so the two numbers that decide whether it is worth switching on, the agreement rate
and the latency, do not exist yet for any body. A `lerobot-lookout` run moves nothing, so it is
the cheapest place there is to produce them.

#### Then, if you want it, on

```bash
quackd run lerobot-lookout --robot arm-01 --llm openai --decision-llm jev
```

Naming a decision LLM makes the mode `on`, so that is one flag rather than two. Now a turn it is
confident enough about is executed from its answer, and the log says where the verb came from:

```
   decide  report_state 0.97 >= 0.60 (0.00 s, ~821 tok ~$0.000034)
▶  verb    report_state() from decision
   decide  repeat, to the model (0.00 s, ~916 tok ~$0.000038)
```

Real lines again, from the same mock arm and the same fake, so read the numbers the way that
makes them true. The `~` in front of the tokens and the cost is quackd saying it counted them
itself, at four characters to the token, because nothing came back with a count of its own. A
hosted decision LLM that does report one prints the figures bare. The `0.00 s` is the fake
answering instantly rather than anything measured.

`from decision` is the attribution that matters when you read a transcript later: who chose a
verb is on the record rather than inferred. The third line is a gate doing its job, the one that
refuses to let a stepper answer the same verb twice running. A turn it is not confident enough
about, or one whose answer is a number, goes to the model exactly as before.

> [!WARNING]
> **The confidence floors were shaped around Jev, and this arm has never tested any of them.**
> A verb that sends an intent has to clear 0.85 and one the `.duck` gated on a person has to
> clear 0.90. The 0.90 is TypeSafe's own number, their "high stakes, proceed with
> confirmation"; the 0.85 is quackd's, set below it because quackd asks the person separately.
> Every other decision LLM computes confidence by a different formula again, so the same 0.87
> does not mean the same thing across rows. This is why shadow comes first, and why
> `--decision-mode on` prints a warning saying so every time you use it.

#### Which of this arm's verbs it may answer

Not a judgement call and not a list anybody maintains: it is computed from each tool's own JSON
schema. A verb whose every parameter is a closed set, an enum, a `const` or a boolean, is a
choice. One with a number, a free string, an object or an array in it is not, and never becomes
one. On this arm that means `report_state`, `stop`, `place`, `gripper(open=true|false)` and,
where a camera is configured, `observe`. `move_joints` is not, because its `positions` is a
free-form map of joint names to degrees, and neither is `pick`, which takes a number too.
[Which of this arm's verbs are a choice](adapters/lerobot.md#which-of-this-arms-verbs-are-a-choice)
is the per-verb table.

#### Which one to point it at

Seven are named, and [the table](decision-llms.md#the-ones-quackd-names) links a page each with
its install line, its address, what was read from its source and what quackd assumes about it.
For a first look from this page:

- [`jev`](decision-llms/jev.md) is hosted, wants `TYPESAFE_API_KEY`, and is the only one with a
  published rate, so it is the one that costs a fraction of a cent rather than a GPU.
- [`laya`](decision-llms/laya.md) needs no server and no key at all, so it is the one to try if
  you would rather nothing left the machine. It downloads its weights on first use.
- [`kev`](decision-llms/kev.md), [`von`](decision-llms/von.md),
  [`openjev`](decision-llms/openjev.md) and [`opendecision`](decision-llms/opendecision.md) are
  servers you start yourself, and `--decision-url` points quackd at one on any address.

If the extra or the key is missing the run says so once, before it connects to the arm, and
carries on without a stepper. The arm is driven either way, because the model is the pilot
either way.

<br>

### 16. Between visits: rehearse on the simulator

Everything above happens at the bench, and the bench is where a mistake costs most: an
afternoon, and somebody else's arm. Most of what went wrong on 2026-09-23 was in the code
between the pilot and the bus rather than in the pilots, and much of it could have been found
without the arm. So between one visit and the next, rehearse on the arm's simulator.

This is not [section 10](#10-rehearse-with---dry-run). A dry run needs the arm, connects to it
for real and moves none of it. The simulator needs no arm and moves all of it, in a model:
`lerobot:mujoco` is the real backend's own code, the connect and its retries, the travel read
off your calibration, the rest move, the refusals and the close, running over a physics model
of the SO-101 in MuJoCo instead of over LeRobot. A task file rehearsed on it goes through the
lines that will drive your arm.

![Two views of a simulated SO-101 arm in MuJoCo, side by side, under a strip naming the verb being run. Left, the arm on a grey table seen from in front and to one side, with a red cube and a dark pen lying in front of it: it starts with the upper arm upright and the forearm level, raises the whole arm on a diagonal, brings the forearm back down level with the upper arm nearly upright, then swings the arm from side to side at the shoulder three times and stops. Right, the scene's front camera, the view the model was sent: the raised arm runs off the top of the frame, then the arm held out level swings across it from one side to the other, pointing straight at the camera as it passes the middle.](assets/lerobot-sim.gif)

*The README hero's goal, `Wave to the camera with an extended arm`, on the simulator's generic
arm, piloted by OpenAI's `gpt-6-sol`, with the front camera the model was sent on the right
([how it was made](assets/README.md)).*

**Install it** into the same environment, or into a 3.11 one, since it needs neither LeRobot nor
torch:

```bash
uv pip install "quackd[lerobot-sim]"
```

**Make a twin of your arm.** Once `arm-01` is calibrated and registered, sections 05 and 06,
this registers a simulator of it on the calibration file its runs read, with the rest pose from
section 07, the pilot and the cameras:

```
$ quackd robot twin arm-01
✓ added arm-01-sim: lerobot:mujoco, a simulator of arm-01 on
/home/you/.cache/huggingface/lerobot/calibration/robots/so_follower/arm-01.json
  copied from arm-01: rest pose, pilot openai:gpt-6-sol, 2 camera urls
⚠ robots.json now holds a lerobot:mujoco robot, and quackd 0.14 and earlier cannot read the file at all: quackd robot remove arm-01-sim before going back to one
  quackd preflight <duck> --robot arm-01-sim
```

An arm registered with a rest pose and neither a pilot nor a camera, as the lab's `arm-01` is,
gets `copied from arm-01: rest pose` and then `arm-01 has no pilot or camera to copy`, and a
hint that ends `--llm VENDOR[:MODEL]`, since its twin has no pilot to rehearse with until you
name one.

Take the warning seriously if an older quackd shares `~/.quackd` with this one: it cannot read
the registry at all while the twin is in it, every robot included, and
[registry.md](registry.md#a-simulator-of-an-arm) says how to keep the two apart.

**Run anything you would run on the arm, on the twin.** The same task files, the same flags and
the same camera urls, which the simulator renders from its scene instead of opening a webcam:

```bash
quackd run lerobot-lookout --robot arm-01-sim --llm fake
```

The first connect fetches the SO-101's model from its makers at a pinned commit, about 16 MB,
checking every file against a recorded hash, and says so once. After that a run reads like one
on the arm, with up to four things the arm never gives. Every run has a paragraph in the pilot's
prompt saying it is on a model of the arm, whose physics nobody measured on an SO-101. A note
names whichever of the `front` and `top` cameras is open, since those views are quackd's rather
than where your cameras stand. With both open:

```
·  note    the front and top cameras are quackd's default views of the table, not where any real camera stands
```

The other two are notes the connect makes only where your rest pose calls for them, each saying
where the simulated arm starts instead: one when the pose puts a joint past one of the model's
stops, and one when it puts the model into its table or into itself. The lab arm's rest pose
does both, and a connect of its twin, here `quackd doctor --robot arm-01-sim`, prints them.
After them comes the advice `doctor` gives the arm itself, because that fold lies past what its
calibration lets `shoulder_lift` be driven to ([section 07](#07-record-the-rest-pose)):

```
the rest pose puts shoulder_lift at -104.7 degrees, past the model's stop at -100.0 degrees, so the simulated arm starts at the stop instead
the rest pose puts gripper 21 mm into the table, moving_jaw_so101_v1 13 mm into the table, lower_arm 10 mm into shoulder and wrist 3 mm into shoulder on the model, and with shoulder_lift at the edge of its travel, where the close's rest move parks the arm, gripper 10 mm into the table, moving_jaw_so101_v1 8 mm into the table, wrist 6 mm into shoulder, gripper 5 mm into shoulder and wrist_camera_mount 1 mm into shoulder, so the simulated arm starts and rests where it settles against them instead, with elbow_flex at 82.9 degrees in place of 96.4 and wrist_flex at 81.9 degrees in place of 72.2. The model's joint zeros and signs are an assumption (JOINT_ZERO, JOINT_SIGN) until the bench checks them, so the pose may be right on the arm and the model's frame wrong
shoulder_lift is recorded at -105 in the rest pose and this calibration lets its servo be driven to -84 and no further, so it parks there and is let go of there, free to settle the rest of the way on its own. Calibrate again with the arm folded (lerobot-calibrate) and record the pose again (quackd robot rest-pose arm-01-sim) to make the fold reachable
```

**Then rehearse the task file properly.** `quackd preflight` connects and closes the twin a few
times, runs the task once per seed with memory off, and says whether every run ended at the rest
pose:

```
$ quackd preflight lerobot-lookout --robot arm-01-sim --llm fake
quackd preflight lerobot-lookout
+--------------------------------------------------------------------+
| seed | outcome | steps | close            | checks | cost | result |
|------+---------+-------+------------------+--------+------+--------|
|    0 | success |     1 | at the rest pose |      - |   $0 | pass   |
|    1 | success |     1 | at the rest pose |      - |   $0 | pass   |
|    2 | success |     1 | at the rest pose |      - |   $0 | pass   |
+--------------------------------------------------------------------+
  no lerobot-lookout.sim.yaml beside it, so only the close was judged
model cost $0 over 3 runs
sim dt 0.01 s
✓ 1 file passed preflight
```

The scripted pilot is enough for the lookout. For a task you mean to hand a real model, rehearse
with that model, `--llm openai` and so on, because what the model reaches for is half of what a
rehearsal is for, and each seed then costs what a run costs. A `<task>.sim.yaml` beside the file
lays out the table and says what has to be so when a run ends, such as a block lifted or a joint
moved, judged by where things really went rather than by what the pilot said:
[adapters/lerobot.md](adapters/lerobot.md#the-sidecar) has the format. `--faults` puts a bus
that drops packets under the connects, so the failed connects the lab met on 2026-09-23, and the
retries quackd now answers them with, happen at home first.

Preflight never rehearses on the arm itself, and says so before it connects to anything:

```
✗ error: arm-01 (lerobot:real) is not a simulator, and preflight runs only on one, since it drives
the robot through every task file seed after seed: quackd robot twin NAME registers a
lerobot:mujoco simulator of a registered arm to rehearse on
  then quackd preflight FILES --robot NAME-sim
```

**`--by-hand` on the twin** releases the arm at its rest pose and waits for Enter, as on the
desk. Nobody can place a simulated arm, so when you press Enter it is left to fall for a second
of sim time, and quackd takes hold of it wherever it landed, or refuses in the words it would
use on the desk. On a twin whose rest pose held the elbow near the end of its travel, the fall
took it past that end:

```
·  hand    hold refused: elbow_flex reads 90.8, outside its calibrated travel of -89.9..89.9, so quackd left torque off: a goal written where that joint is lies past its travel and the servo would pull it to the end of its travel, and with none written the servo may drive it to the last goal it was given, with a hand on the arm either way. quackd takes hold of the arm only with elbow_flex inside its travel
```

That is worth seeing before the bench. It is the refusal a
[hand-placed start](#or-start-from-a-pose-you-set-by-hand) makes over any joint outside its
travel, met here because a limp joint near the end of its travel settled past it. On the desk
you lift the joint back inside before you press Enter. On the twin nobody does, so you meet the
refusal instead, and the run ends there, as it would on the desk.

What the fall rehearses is what quackd says and does with the pose it leaves, and not whether
your arm's rest pose holds itself up. How a limp joint settles is the model's physics, and
which way gravity pulls each joint rests on signs and zeros nobody has checked against an arm,
so the arm itself may fall differently, or not at all. Only the bench says which.

**What the twin cannot tell you** is how your arm moves. The physics is the model's, not a
measurement of an SO-101. Which way each joint turns and where its zero sits are assumed to
match the model, the front and top cameras are quackd's views and not yours, the servos never
warm, and nothing timed on the simulator is a rate. A task that passes preflight has survived
the code and the contract. Whether it survives the arm is still [section 11](#11-prove-the-safety-net)
onwards, with a hand near the switch, and a task that passes on the twin and then does
something else on the arm is exactly what [section 14](#14-what-to-report) asks for.

<br>

### 17. Optional: hand the arm to a learned policy

Everything above has a model choosing every verb, about once every six seconds on this arm.
That is the right rate for deciding what to do next and the wrong one for closing a gripper on
a pen, where what the jaws touch changes in tenths of a second. A learned policy, an ACT you
trained on your own demonstrations or a vision-language-action model such as SmolVLA, reads the
arm and its cameras many times a second and answers with goals for every motor. quackd puts it
under the model rather than in its place: the model hands it one short subtask at a time with
`manipulate`, and judges from a fresh look at the arm whether it did it.

> [!WARNING]
> **Nobody has driven this arm with a policy through quackd yet.** Every segment so far has run
> in the test suite and on the simulator. Rehearse everything below on the twin from section 16
> first, and on the arm keep a hand on the power switch.

The policy runs in a server of its own, and no quackd command loads a checkpoint in the process
that holds the serial bus, because a checkpoint is code
([policies.md](policies.md#a-checkpoint-is-code)). So this takes two terminals.
**Install the server's extra** into a Python 3.12 environment, the arm's own or another:

```bash
uv pip install "quackd[lerobot-vla]"
```

**In a second terminal, start a server.** A scripted policy needs no torch and no checkpoint,
which makes it the one to prove the plumbing with. `scripted:sweep` swings `wrist_flex` 5 degrees
either side of where it started, one swing every 2 s, and `scripted:hold` holds the arm where it
reads:

```bash
quackd policy serve --policy scripted:sweep
```

It prints where it serves, `http://127.0.0.1:9875`, the token file it wrote or read, which is
`~/.quackd/policy.token` and is written the first time, and its rate, and serves until Ctrl+C.
It never prints the token itself: a run on the same machine reads it from that file. **In the
first terminal, point a run at it.** Here with the scripted pilot, on the twin, and with memory
off so a rehearsal leaves nothing in the twin's:

```
$ quackd run --goal "swing the wrist" --robot arm-01-sim --policy-url http://127.0.0.1:9875 --llm fake --no-memory
┌─ 🦆 goal ───────────────────────────────────────────────────────────────────────────────────────┐
│ provider  fake (scripted:goal)                                                                  │
│ robot     arm-01-sim (lerobot:mujoco)                                                           │
│ detector  color_blob on this machine  (once the body reports a camera)                          │
│ policy    http://127.0.0.1:9875  scripted:sweep                                                 │
└─ Ctrl-C or q stops the duck. Press it twice to quit at once. ───────────────────────────────────┘
·  note    the policy at http://127.0.0.1:9875 is scripted:sweep, at 10 Hz from scripted:sweep's own, the verbs' tick
·  note    the front and top cameras are quackd's default views of the table, not where any real camera stands
```

The server was asked what it serves before anything connected, and the header and the record
name it. The scripted pilot never calls `manipulate`, so that is all this run proves. To see a
segment, give the run a model, `--llm openai` and so on, whose prompt then has a section on its
executor: one short subtask a call, and a fresh look after each. A `--goal` run allows
`manipulate` only behind a confirm, so you are asked before every segment when you run it from
a terminal. `--yes`, or a pipe or file on stdin, answers in your place, and nobody is asked.

**On the twin, the first segment is refused until `shoulder_lift` is moved in.** A segment starts
only with every joint inside its calibrated travel, and the twin starts where the arm's rest pose
puts it, settled clear of its table ([the arm's page](adapters/lerobot.md#running-it)). The lab
arm's was recorded folded, past what its calibration lets `shoulder_lift` be driven to
([section 07](#07-record-the-rest-pose)) and past its model's stop too, so the twin starts with
that joint settled just off the stop, outside its travel, and `manipulate` refuses with
`Move shoulder_lift inside its travel first`. A model moves the joint in with `move_joints` when
it reads that, and an MCP client can do the same before its first segment. After a small move in
and two segments, the close parked the joint at the edge of its travel, in the pose the twin
settled to there, and let it go. After a larger move the rest move can set the gripper down on
the table short of that pose, and the close keeps torque on. `--controller vla`, below, only ever
calls `manipulate`, so a vla run on this twin ends in failure, in the verb's own words, before
any segment runs. The lasting fix is to calibrate the arm again folded, the bench item in
[PLAN.md](../PLAN.md) that reads the calibrated value at each of the arm's stops, and record the
rest pose again after it.

**Or take the model out altogether.** `--controller vla` is a scripted pilot that hands the
policy each instruction a `duck: 3` task file lists, or the goal as the only one, and then asks
you `Did the arm do it?`. Only your yes is a success, so it needs you at the terminal and refuses
anything that would answer for you:

```
$ quackd run --goal "swing the wrist" --robot arm-01-sim --policy-url http://127.0.0.1:9875 --controller vla --yes --no-memory
✗ error: --controller vla leaves the verdict on the task to a person who watched the arm, and --yes
answers every question without asking anybody
  drop --yes: a vla run asks you before its first segment and after its last
```

**Then a real policy.** `quackd policy check --policy OWNER/NAME@REVISION --bench` says what a
checkpoint wants and how fast it answers on this laptop before you serve it. Bench it twice, the
second time with the `--latency-s` the first suggests, and serve it with the one a bench says
covers what it timed ([policies.md](policies.md#on-the-laptop-alone)). On this project's laptop
an ACT kept up and SmolVLA did not ([policies.md](policies.md#smolvla-and-act)).
[policies.md](policies.md) is the rest: serving one on the laptop or on a rented GPU through an
ssh tunnel, which policies there are and under what licences, and recording the 50 to 200
episodes a policy for your own task learns from with LeRobot's own tools.

**On the arm**, the same commands with `--robot arm-01`, and step 18 of
[the hardware checklist](lerobot-hardware-checklist.md) in that order: a policy that holds the
arm still, then the sweep, then a checkpoint. A policy moves no joint faster than a verb may,
and a goal past the travel is clipped rather than sent, but a joint that reads past its travel,
once a move has begun lifting it, rises to the end of it at the servo's own speed whatever anybody
sends, which is what the hand on the switch is for.

**What the twin cannot tell you** is how fast the policy's loop runs on the arm's bus while this
laptop infers, since nothing timed on the simulator is a rate, or what a policy trained on real
camera frames does with rendered ones. A policy that grasps on the twin, or fails to, says
nothing about the arm. The `policy` block of a run's `summary.json` says what the loop achieved,
and from the arm it is a number nobody has yet: send it back with the rest of
[section 14](#14-what-to-report).

<br>

## Part 2: from Claude, over MCP

Everything above is the command line. This is the same arm, the same executor and the same
contract, reached from a chat instead. `quackd serve-mcp` hands Claude Code or Claude Desktop
nine `robot_*` tools over a local pipe, and the model you are chatting with picks the verbs.
quackd chooses no model here and reads no key of yours.

> [!NOTE]
> Part 1's [section 15](#15-optional-put-a-decision-llm-in-front-of-the-model) has no mirror
> here, and that is structural rather than an omission. `--decision-llm` puts a stepper in front
> of the model inside quackd's own loop, and over MCP there is no such loop: the model is the
> client, so the deciding happens in Claude and quackd hands out tools and enforces the
> contract. The flag and its two companions belong to `quackd run` ([mcp.md](mcp.md)).
>
> [Section 16](#16-between-visits-rehearse-on-the-simulator) has no mirror either, because it
> works the same from here. `quackd robot twin arm-01` registers the twin, and
> `quackd serve-mcp --robot arm-01-sim` hands Claude the twin instead of the arm. An MCP session
> runs tool calls at once, though, so unlike a run it does not do the same again under one
> seed. `quackd preflight` is the rehearsal that does, and it runs from the terminal.
>
> [Section 17](#17-optional-hand-the-arm-to-a-learned-policy) has no mirror either. Its server is
> the same from here, and `quackd serve-mcp --robot arm-01-sim --policy-url
> http://127.0.0.1:9875 --yes` hands Claude a `manipulate` the policy runs. `--yes` is not
> optional, since both verbs a policy runs are confirm gated. `--controller vla` is refused over
> MCP, because Claude is the pilot here and a vla run needs a person at a terminal to say
> whether the arm did the task ([mcp.md](mcp.md)).

The steps below are numbered `M00` to `M14` and they mirror Part 1's `00` to `14`, so if you
have just walked the terminal path you will recognise every one of them. Where a step is the
same, the command is repeated here and the explanation stays in Part 1 behind a link. Where MCP
differs, and it differs most at the camera, at the feasibility gate and at what happens when a
session ends, that difference is the whole point of the step.

If you are starting here rather than reading through, read
[M00](#m00-what-changes-when-claude-is-the-pilot) and then
[section 00](#00-read-this-first), which is the part about what this arm cannot do and is true
whoever is holding the controls.

<br>

### M00. What changes when Claude is the pilot

The arm does not change. Nothing underneath a tool call changes. What changes is who decides
what happens next, and where you watch it happen. One thing does happen before the first tool
call: spawning the server drives the arm to its rest pose, where one is recorded, before the
model gets a turn, and a session that cannot reach it refuses to open.

**The model in your client is the pilot.** Your client spawns `quackd serve-mcp` as a
subprocess and talks to it over stdio, so the thinking happens in the session you are already
sitting in. quackd chooses no model here and reads no API key. There is no `--llm`,
and `quackd list-models` has nothing to say about an MCP session, because both
belong to the commands that bring a model of their own. [Choosing a pilot](#03-choose-your-pilot)
is a decision you do not make in this part. You made it when you opened Claude Code or Claude
Desktop.

**Nine tools, and one executor under all of them.** A client lists them like this:

```
robot_assess_task
robot_list
robot_list_verbs
robot_load_duckfile
robot_observe
robot_recall
robot_remember
robot_run_verb
robot_say
```

Behind them is the same executor Part 1 describes: the same allowlist, the same budgets, the
same feasibility verdict, the same calibrated range clamp and the same heartbeat. The confirm
gate is the one that is genuinely different. There is nobody to ask over stdio, so a
confirm-gated verb is refused outright unless the server was started as
`quackd serve-mcp --yes`, and that one flag typed at spawn allows every confirm-gated verb for
the rest of the session. On this arm that means `pick` and `manipulate`, present when the
server names a policy server, each of which hands the whole arm to a learned policy for a
segment. A refusal over MCP is the same gate refusing for the same reason,
with a sentence appended about what to do about it here: a verdict refusal ends
`call robot_assess_task(robot='arm-01', verdict=...) first`, a confirm refusal ends by telling
you to start the server with `--yes`, and a budget refusal arrives prefixed `budget exhausted`.
None of those sentences reaches a terminal run, where the confirm gate asks you rather than
refusing. There is no `declare_success` tool. The model says it is finished in the chat, in
prose, and what that is worth is your judgement rather than quackd's.

**What the server tells the model before it has called anything.** This arrives at connect, as
the server's instructions, and it is what the pilot is working from on its first turn:

```
You are piloting one robot through quackd: arm-01, which is a six-joint desktop robot arm with a parallel gripper (an SO-101 class arm driven by LeRobot), bolted to a table.
This body: lerobot-so101: height 0.53 m (estimate: one vendor's listing; reaching straight up), actuated joints 6 (official: the LeRobot SO-101 docs; five joints and a gripper), payload 0.5 kg (estimate: one vendor's listing), reach 0.4 m (estimate: the maker's URDF, TheRobotStudio/SO-ARM100 Simulation/SO101/so101_new_calib.urdf; link lengths from the shoulder to the gripper frame, summed with the arm straight and rounded down), one arm with a gripper, mains powered, so nothing runs down. it does not move: no base and no legs. Not published: mass. Cannot: go anywhere: it is bolted to a table and has no base; lift or hold more than about half a kilogram: a pen, an empty cup or a wooden block weighs far less than that, and a full bottle or a tool may weigh more; reach anything more than about 0.4 m from its shoulder: that is the arm held straight out, and any bent pose reaches less; feel what it holds: nothing reports grip force, so holding is inferred from the gripper stopping short of shut, which an empty hand that binds also does; know its own mass: vendor listings disagree by a factor of three. Clamps: joints within 180 degrees, gripper 0 to 100.
Call robot_list_verbs first: the verbs come from that robot's own manifest, so what it can
do is what it lists and nothing else. Before the first verb that moves the body, call
robot_assess_task with your verdict on whether this body can do the task at all, judged
against that datasheet: feasible, infeasible or uncertain. robot_run_verb refuses anything
that moves the body until you have answered, and loading a .duck starts a new task and
needs a new verdict. Every action is a *verb*; the executor enforces an allowlist, budgets
and confirmation gates, so a refused call is a rule, not a bug. Prefer composite verbs
(search_scan, go_to) over micro-managing velocities. Load a .duck file with
robot_load_duckfile(path) to adopt a task contract; then follow its body as your
instructions. Call robot_recall early: it is what this robot learned in earlier
sessions, and robot_remember(text) keeps one short fact for the next one. Call robot_run_verb(verb="stop") if anything looks wrong.
```

That was captured against the mock arm, registered as `arm-01`. The blurb and the datasheet
come from the adapter rather than from the backend, so a real arm registered under the same
name is handed the same paragraph.

**One sentence in there is generic and wrong for this body.** `Prefer composite verbs
(search_scan, go_to) over micro-managing velocities` is advice for something with a base and a
neck. An arm bolted to a table has neither verb, and never will. The model finds that out from
`robot_list_verbs`, which is exactly why the same paragraph tells it to call that first and
says the manifest is the whole vocabulary. Expect a first turn that lists the verbs and drops
the idea without comment.

**There is no per-step camera frame.** A `quackd run` loop with a model that takes images puts
the webcam picture in front of the pilot on every step, and a model that takes none is refused
the picture rather than handed it. An MCP session does not put a frame anywhere by itself. The
model sees one when it calls `robot_observe` and its contract allows that verb, and at no other
moment, so looking is a deliberate act here rather than something that happens to it. A loaded
`.duck` can take the tool away: under `lerobot-lookout`, the contract Part 2 loads later on this
same page, `observe` comes back `"allowed": false` and the call returns words.

**There is no Ctrl-C kill switch.** Part 1's [section 11](#11-prove-the-safety-net) leans on
one, and it does not exist in a chat window. Three things you can reach for: the model calling
`robot_run_verb(verb="stop")`, you closing the session, and the power switch. The first costs a
budget step like every other verb and is refused once the budget is spent, which five minutes
from the arm's connect leaves you the other two. The second parks the arm on the way out only
where a rest pose is recorded for this robot ([section 07](#07-record-the-rest-pose)), and not
at all under `--dry-run`: with no pose recorded the close releases torque and the arm sags where
it stands. The third releases torque as well, so the arm falls from wherever it was holding.
Support it by hand before you cut, and keep your fingers out of the jaws. The heartbeat is the
one thing here that acts without being asked: one missed ping sends a stop and shuts the session.
A contract's `abort_when` is not the same mechanism. It is checked only when the model runs a
verb, it needs a loaded `.duck` to exist at all, so a bare session has none, and it shuts the
session rather than sending a stop of its own.

**A hard kill parks nothing.** Task Manager, `taskkill /F` or a SIGKILL takes the process away
before any of the shutdown runs, so the arm holds its last goal under torque rather than
falling. Do not leave it there. Nothing is reading temperature once the process is gone, and a
loaded joint heats until it trips its own overload protection, which goes slack without
announcing it. Take the arm's weight, then run `quackd robot release arm-01` at a terminal,
which lets it go where it stands now that the port is free, or reconnect with
`quackd doctor --robot arm-01` and let quackd put it down, or cut the supply. Cutting the supply
releases torque and the arm drops from wherever that goal left it, which in this case is by
definition not its rest pose, so take its weight first and keep your fingers out of the jaws.

**Nothing run-shaped is written to disk.** No `runs/` directory, no `transcript.jsonl`, no
`frames/`, no `terminal.txt`. The record of an MCP session is the chat itself, the server's own log on stderr, and
whatever the model chose to keep with `robot_remember`, which appends one line per note to
`~/.quackd/memory/<name>.jsonl` ([memory.md](memory.md)).

**The budget clock starts when the server has connected the arm.** Without a loaded `.duck` a
session allows every verb that is not `dangerous`, on a default of 40 verb steps and five
minutes, and those five minutes begin as the server starts, once the arm has connected and
before it is driven to its rest pose, rather than at the first verb. Time spent reading,
thinking or talking to you is spent out of them. Running out ends nothing and parks
nothing: every verb after that comes back `ok: false` with `budget exhausted`, `stop` among
them, the session stays open, and the arm holds its last goal under torque. A spent budget ends
a `quackd run` instead, through the same parking teardown as any other exit, so Part 1's
intuition does not carry here.

**An `uncertain` verdict does not end anything here.** In Part 1 it ended a run: one of the
bench's dry runs on 2026-09-15 stopped because the pilot answered `uncertain` and the person at
the keyboard answered no. Over MCP there is no terminal to ask at, so the model asks you in the
chat and then answers `robot_assess_task` again with what you told it. The gate is the same
gate. The question arrives where you are already reading instead.

**The three things this arm cannot do are still those three, and the third changes shape.**
There is no `wave` verb and the camera cannot look for you, whoever is piloting. Part 1's third
one is that whether it can see you at all depends on the model you bring, and over MCP the pilot
takes images by construction, so that half resolves itself and what is left of it is the bundled
detector: a colour threshold carrying the simulator's own ranges rather than a person detector.
[Section 00](#00-read-this-first) has all three in full.

> [!CAUTION]
> There is still no e-stop, and a chat window is not one. quackd bounds which angles a joint
> may reach and how fast it gets there. It does not bound how hard the arm pushes, and no tool
> call can. Cutting the servo supply is the only thing that stops this arm in every case,
> including the one where the client has gone away with a goal still standing. It stops it by
> releasing torque, which means the arm falls, so take its weight before you cut. Have your
> hand near the switch from the moment the server starts, not from the first `move_joints`.

One SO-101 has run quackd, on 2026-09-15 and on 2026-09-23, both times `quackd run` from a
terminal rather than an MCP session. On the second day the rest-pose park ran on it and could
not reach a fold that lay past the calibrated travel. So the parking this part leans on, at both
ends of a session, has met a real arm only in `quackd run`, the parking at the edge of the
travel that answers that day is exercised against `lerobot:mock` and in the test suite alone,
and no MCP session has yet driven a real arm. Every capture quoted in Part 2 came from the mock,
and each one says so where it appears. So going down this path makes you the first, which is
worth knowing before you start rather than afterwards, and it is why
[what to report](#14-what-to-report) matters more here than anywhere else on this page.

<br>

### M01. What to bring

Everything physical in [section 01](#01-what-to-bring) still applies, because the arm does not
know which half of this guide you are reading.

- **A laptop with quackd already installed.** The next section is Part 1's install and it still
  pulls torch, so do it the night before rather than on lab wifi.
- **Claude Code or Claude Desktop, installed and signed in**, on that same laptop. The client
  starts quackd as a subprocess on the machine it runs on, so a client anywhere else cannot
  reach the arm's port.
- **A couple of USB cables for the arm's controller board**, in more than one shape, and a hub
  if your laptop is short on ports.
- **A USB webcam, and something to prop it up and aim it.** A small tripod, a clip, a stack of
  books. The arm cannot move the camera for you.
- **A way to cut power fast.** A power strip with its own switch, or a firm decision about which
  plug you are going to pull. It carries more weight here than it does in Part 1, because an MCP
  session has no Ctrl-C kill switch the way a `quackd run` does, and because once
  [section M07](#m07-record-the-rest-pose) has recorded a rest pose the session drives the arm to
  it twice on its own, with no verb and no model involved: once as the client starts the server,
  before a single tool is offered, and again as you quit the client. Hands clear before either.
- **Permission, and the arm's own parts list.** Whoever runs the lab confirms that you may
  connect your own laptop, and which supply that specific arm takes. Motors and supplies vary
  between builds and neither LeRobot nor quackd reads the voltage, so neither can warn you.

**What drops off the list is the key.** Part 1's last bullet asks for a cloud vendor's key or a
local model server, because `quackd run` has to go and find a model. In MCP mode quackd picks no
model and reads no key: the pilot is the model you are already chatting with, so the account you
signed into the client with is all of it. The blue top goes with it, since that bullet is there
for a pilot that cannot take images and this one can.

<br>

### M02. Install quackd

The same three commands as [section 02](#02-install-quackd), which is where the two `doctor` rows
below are explained. Python 3.12 or newer, in an environment of its own:

```bash
uv venv --python 3.12
uv pip install "quackd[lerobot]"
quackd doctor
```

**`quackd[lerobot]` alone is the whole install this time**, and that is the one difference. Part
1 adds a second extra for the pilot in the same line, `quackd[lerobot,anthropic]` or whichever
vendor it was, and there is no pilot to install here. Two rows still decide whether a serial port
can be opened at all:

```
- lerobot                    not installed (quackd[lerobot])
- lerobot (feetech bus)      not installed (quackd[lerobot])
```

Both have to be green, and that is what a missing install looks like.

**Then note where that venv put `quackd`.** [Section M03](#m03-choose-your-client) hands that
path to the client and it has to be the absolute one:
`D:\Development\lerobot-test\.venv\Scripts\quackd.exe` on Windows, and
`~/lerobot-test/.venv/bin/quackd` on macOS and Linux. Those are this guide's folders, so yours is
the `Scripts\` directory of the venv you just made, or its `bin/` directory elsewhere. It has to
be absolute because the client starts the server itself, from a directory you did not choose and,
on Windows especially, often with none of your shell's PATH.

> [!WARNING]
> If `lerobot` reads `not installed` after an install that succeeded, check `python --version`
> before you reinstall anything. On 3.11 that extra resolves to zero packages, silently, and
> `doctor` has nothing else to complain about. It is the same reason Part 2 points the client at
> this venv's `quackd` rather than at `uvx`: `uvx` builds a second environment on whichever
> interpreter uv picks, and on a 3.11 machine that one has no LeRobot in it. The `uvx` configs in
> [mcp.md](mcp.md) and [adapters/lerobot.md](adapters/lerobot.md) follow the same rule, so they
> need a 3.12 or newer interpreter for the LeRobot half of that extra to resolve.

<br>

### M03. Choose your client

[Section 03](#03-choose-your-pilot) was a flag and a key. This one is neither. quackd picks no
model in MCP mode and reads no key, so there is no pilot to choose: whichever model you are
already chatting with is the one that will move the arm. What you choose here is the client,
and how that client launches the server.

**The shape of the thing.** The client runs `quackd serve-mcp` as a subprocess of its own and
talks to it over stdin and stdout. Nothing is listening on a port, there is no address to
visit, and there is no server to start by hand before you open the client. It lives exactly as
long as the session that spawned it: the client starts it when the session starts, and stops
it when the session ends. The only `--address` this server takes is the arm's serial port, and
by now the registry is carrying that for you. [mcp.md](mcp.md) lists the four things that would
have to land before your phone could reach this, and none of them are built.

> [!WARNING]
> Starting that server moves the arm. Before the client's model can call a single tool, the
> server drives the arm to the rest pose you recorded in
> [section M07](#m07-record-the-rest-pose), and it refuses to start at all if it cannot get
> there. Opening Claude Code or Claude Desktop is therefore the same act as starting a run:
> hands, cables and anything breakable clear of the arm first.
>
> At the other end, an orderly shutdown parks the arm again and releases torque at the rest
> pose, so the arm goes limp folded up. Where the pose was not reached, torque is deliberately
> left on instead, so the arm holds itself rather than dropping. That park is best effort
> inside a graceful stop and there is no signal handler, so a client that kills the subprocess,
> a crashed session or a Task Manager kill parks nothing and leaves the arm energised wherever
> it stopped. Never leave it unattended on the assumption that closing the client put it down.

**The command you are wiring up is the same on every client**, and it is the venv from
[section M02](#m02-install-quackd), named by its absolute path:

```
D:\Development\lerobot-test\.venv\Scripts\quackd.exe serve-mcp --robot arm-01
~/lerobot-test/.venv/bin/quackd serve-mcp --robot arm-01
```

The first is Windows, the second is macOS and Linux. Use your own venv's path rather than
either of those.

> [!WARNING]
> Do not launch this one with `uvx`. `uvx --from "quackd[lerobot]"` builds a second
> environment on whichever interpreter uv happens to pick, and on a machine whose default
> Python is 3.11 the LeRobot half of that extra resolves to nothing at all, silently, exactly
> as in [section 02](#02-install-quackd). The adapter package itself installs fine there and
> its mock backend still works, so what is missing is only the arm's own SDK, and the way you
> meet that is a server that does not come up at all: the connect raises, the client shows the
> server as failed rather than connected, and the stderr log names `lerobot` and the
> `quackd[lerobot]` extra that would buy it. And where uv does pick 3.12, it downloads torch
> all over again. The venv you already built has the right interpreter and the SDK in it, so
> point the client straight at it. [mcp.md](mcp.md) uses `uvx` for the simulated bodies, where
> no SDK with a version floor is involved, and that form does not carry over to the arm.

**Claude Code, the one command.** Everything after the bare `--` is the command and its
arguments, which is what stops `--robot` being read as a flag of `claude mcp add` itself:

```
claude mcp add arm -- D:\Development\lerobot-test\.venv\Scripts\quackd.exe serve-mcp --robot arm-01
```

`-s local|user|project` chooses where the entry is written, and local is the default: private
to you, in this project. `-e KEY=value` sets a variable for the server, and it repeats.

Here are the add, the list and the get from one capture on this machine, against a mock arm.
The `claude mcp remove` at the end of that same capture is under **Changing it later** below:

```
$ claude mcp add arm -- D:\Development\quackd\.venv\Scripts\quackd.exe serve-mcp --robot arm-01 --registry-dir <TMPREG>
Added stdio MCP server arm with command: D:\Development\quackd\.venv\Scripts\quackd.exe serve-mcp --robot arm-01 --registry-dir <TMPREG> to local config
File modified: C:\Users\<you>\.claude.json [project: D:\Development\quackd]

$ claude mcp list
Checking MCP server health…

quackd: uv run --no-sync quackd serve-mcp --robot microduck:sim2d - ⏸ Pending approval (run `claude` to approve)
arm: D:\Development\quackd\.venv\Scripts\quackd.exe serve-mcp --robot arm-01 --registry-dir <TMPREG> - ✔ Connected

$ claude mcp get arm
arm:
  Scope: Local config (private to you in this project)
  Status: ✔ Connected
  Type: stdio
  Command: D:\Development\quackd\.venv\Scripts\quackd.exe
  Args: serve-mcp --robot arm-01 --registry-dir <TMPREG>
  Environment:

To remove this server, run: claude mcp remove arm -s local
```

> [!WARNING]
> `claude mcp list` is not a read of a configuration file. The health check in it spawns every
> server on the list, so with `arm` configured it starts `quackd serve-mcp`, connects to
> whatever `--robot arm-01` resolves to, and drives that arm to its rest pose. Here that was a
> mock in a throwaway registry. On your bench it is the arm. Clear the arm before you run the
> list, and treat `claude mcp get` the same way, since it prints a status of its own.

Three things there are this machine's rather than yours. `<TMPREG>` is a placeholder put in by
hand where a scratch `--registry-dir` was, because that `arm-01` was `lerobot:mock` in a
throwaway registry rather than the arm you registered in [section M06](#m06-first-contact): on
the default registry you pass no `--registry-dir` at all and those lines are shorter. `<you>`
stands in for a home directory the same way, because the file that line names is the client's
own configuration and it lives under yours. And the command path is this repo's own checkout
rather than the `lerobot-test` venv you built. Put your own in all three places and the shape
is unchanged.

**Be precise about what that capture proves.** On 2026-09-21, on Windows, with Claude Code
2.1.270, the server started, connected to a mock arm and answered the client's health check,
which is the `✔ Connected` row. That is the launch path working end to end. It is not an arm.
The row above it, `quackd:`, is this repo's own project-scoped `.mcp.json` waiting for
somebody to approve it in a session, which is what that state looks like when you meet it.

**Claude Code, the project file.** The other form is a `.mcp.json` at the root of the folder
you open Claude Code in:

```json
{
  "mcpServers": {
    "arm": {
      "command": "D:\\Development\\lerobot-test\\.venv\\Scripts\\quackd.exe",
      "args": ["serve-mcp", "--robot", "arm-01"]
    }
  }
}
```

No `"type"` key is needed: an entry with a `command` is read as a stdio server. Backslashes
are doubled because it is JSON, not because of anything quackd does. A server that arrives
this way is project scoped, so it shows as ⏸ Pending approval (run `claude` to approve)
until somebody approves it inside a session, exactly like the row in the capture above.

**Claude Desktop** takes the same three fields in its own file. Settings, then Developer, then
Edit Config opens it:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "arm": {
      "command": "D:\\Development\\lerobot-test\\.venv\\Scripts\\quackd.exe",
      "args": ["serve-mcp", "--robot", "arm-01"],
      "env": {"QUACKD_LOG": "1"}
    }
  }
}
```

Then restart Claude Desktop completely, not just the window. The server appears under
Connectors, Manage connectors. `QUACKD_LOG` is `1` already and is shown here because `env`
is where you would set it to `0`: a desktop-spawned server has no shell and starts in a
directory you did not choose, so `env` is the reliable way to give it a variable, and a `.env`
file may or may not be found. That same missing shell is why the command is an absolute path.
A desktop app usually does not see your `PATH`, so if the server will not start on Windows,
suspect that before anything else.

**Changing it later** is a remove and an add, or an edit to the JSON:

```
$ claude mcp remove arm
Removed MCP server "arm" from local config
File modified: C:\Users\<you>\.claude.json [project: D:\Development\quackd]
```

> [!IMPORTANT]
> A running server keeps the tools, the flags it started with and what the registry gave it at
> launch: the address, the camera and the token. So after you edit a verb, add a camera to the
> registry in [section M09](#m09-add-the-camera), or change anything in that `args` list,
> restart it: `/mcp` in Claude Code, or a new session. In Desktop, restart the app. Nothing
> warns you that you are talking to the old one. The contract is the one thing a live server
> can swap without a restart, and the model is who does it: `robot_load_duckfile` adopts a new
> `.duck` in place, taking the allowlist, the budget and the datasheet corrections with it,
> changing which verbs `robot_list_verbs` reports as allowed, starting a new task and shutting
> the verdict gate again.

> [!NOTE]
> `--llm` and `quackd list-models` play no part here. They belong to `quackd
> run`, and `serve-mcp` takes neither. The consequence is worth spelling out: whichever
> model you are chatting with is flying the arm, and a model that cannot accept images cannot
> use the frame `robot_observe` hands back. [Section M09](#m09-add-the-camera) is where that
> matters.

<br>

### M04. At the lab, before power

Nothing in this step changes when Claude is the pilot, so [section 04](#04-at-the-lab-before-power)
is the whole of it and this is the short form. The arm is still unplugged. Clear the whole sweep
it can reach rather than the desk footprint, take anything fragile out of the gripper and off the
desk within arm's length, and confirm that your switch, or the plug you have decided on, is
within reach of where you will actually be standing.

**Confirm the supply with whoever owns the arm** before anything is powered on. Motors and
supplies vary between builds, and neither LeRobot nor quackd reads the voltage, so neither can
warn you.

<br>

### M05. Find the port, then calibrate

The same two commands as the terminal half. [Section 05](#05-find-the-port-then-calibrate)
explains what each one does, what to do when no port appears at all, and why `wrist_roll` ends up
recorded differently from every other joint.

```bash
lerobot-find-port
```

```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=COM5 --robot.id=arm-01
```

The first lists the ports, asks you to unplug the arm, and names the one that disappeared. The
second moves nothing on its own: it asks *you* to move each joint through its range, and it
writes the file every travel limit later comes from. quackd refuses to drive an arm that has no
such file, over MCP exactly as it does from a terminal.

When it asks you to move each joint through its range, take each one all the way into the fold
you will rest the arm in, the shoulder folded all the way back included. The travel it records
is the most any servo will ever be driven, so a fold left outside it is a pose the arm can rest
in and cannot be driven back to ([section 05](#05-find-the-port-then-calibrate),
[section 07](#07-record-the-rest-pose)). When you calibrate again, the tool first offers the
file it already has: type `c` there, because Enter keeps the old travel.

> [!CAUTION]
> Power goes on before both of these, and the arm is limp for the whole of the second one.
> `configure()` runs `configure_motors()` inside `torque_disabled()`, so `lerobot-calibrate`
> drops torque the moment it connects, and the procedure then keeps torque off while you sweep
> each joint by hand. Fold the arm low, or have a hand on it, before you run that command.

> [!IMPORTANT]
> The id has to be the name you are going to pass after `--robot`, which is why the command above
> says `arm-01`. That name becomes the manifest id, and the manifest id is the calibration id
> quackd goes looking for. Register the arm as `lab-arm` and you have to calibrate as `lab-arm`.
> Two arms sharing an id share one file with nothing in it to say which arm it came from, so if
> this is not the only SO-101 in the room today, pick a name nobody else is using and use that
> name in both places. This one bites harder here than in a terminal, because the name goes into
> a client config file once and then nobody looks at it again.

<br>

### M06. First contact

The first command that leaves torque on, and it is still yours rather than Claude's. It
connects, reads, and moves nothing.

```bash
quackd doctor --robot lerobot:real --address COM5
```

> [!CAUTION]
> Support the arm while this starts. `configure()` runs with torque off, so connecting drops the
> arm for a moment whatever else is true, and that is the moment an arm folded somewhere awkward
> falls. A packet lost or garbled while connecting is a `connect attempt 1 of 3 failed on ...`
> warning and another attempt, with its own moment. Over MCP the same line goes to the server's
> log. Support it at the end too, for now: until [section 07](#07-record-the-rest-pose) has
> recorded a rest pose there is nothing for quackd to put the arm back to, so LeRobot's
> `disconnect()` disables torque where the arm stands, a `doctor` probe included.

[Section 06](#06-first-contact) has the four things to read off that probe: the calibration file
it found, each joint's range, that torque is on, and what the servos report for temperature with
the arm cold. Write the cold number down. It is the baseline for every reading Claude will show
you later in a session.

Then register the arm under the id you calibrated, and check that the address comes back out of
the registry:

```bash
quackd robot add arm-01 lerobot:real --address COM5
quackd doctor --robot arm-01
```

The second should print what the first probe printed, with the address coming from the registry
instead of from your hand. It checks the name as well: `doctor` builds a registered arm under
the name it was registered as, which is the id an MCP session connects under, so a probe of an
arm registered as `lab-arm` loads `lab-arm`'s calibration, exactly as the MCP server you are
about to configure will. The probe of `lerobot:real` before it had no name to go by and used the
adapter's default id, `arm-01`, which is why this page registers the arm under that name.

**`--llm` is optional on `quackd robot add` here, and an MCP session ignores it.** The
registry's stored pilot only matters to `quackd run`. In MCP mode quackd picks no model and reads
no key: the model you are chatting with is the pilot, and nothing in the registry has a say in
that.

**What the registry is carrying for you is the address, the cameras, the token and the rest
pose.** That is why the client configuration in the next sections is `--robot arm-01` and little
else. Put the port in the registry now, at a prompt where you can read the error, rather than
into a JSON file you edit once and then restart an application to test.

<br>

### M07. Record the rest pose

This one is not an MCP command. Fold the arm low by hand first, while nothing has connected to
it and it is limp, and then type the command once at a terminal. It connects, reads every joint
and keeps the answer, and every session afterwards inherits it. Use the venv you built, by
absolute path:

```bash
D:\Development\lerobot-test\.venv\Scripts\quackd.exe robot rest-pose arm-01
```

[Section 07](#07-record-the-rest-pose) is the whole of it: the joint table the command prints,
`--yes`, `--json`, `--clear`, why the pose has to be one the arm holds with torque off, and why
the fold has to lie inside the travel your calibration recorded. If `rest-pose` warns that a
joint is past its travel, the arm still parks, at the edge of that travel, and calibrating again
with the arm folded ([M05](#m05-find-the-port-then-calibrate)) and recording the pose again is
what makes the fold itself reachable. Record it again after every `lerobot-calibrate` in any
case, because a calibration that records a different travel moves that joint's zero. An SO-101
has no brake, so a pose it cannot hold limp is a pose it will fall from.

Part 2's half is what a **session** does with that pose, and sessions end in ways runs do not.

| When | What happens |
|---|---|
| The client spawns the server | the arm is driven to the recorded pose as part of connecting, before the heartbeat starts and before a single tool is offered. Nothing has happened in the chat yet |
| It cannot get there | the server raises and exits. No tools, no session, and the arm keeps its torque. The refusal is below |
| The session closes cleanly | the arm is parked again between the `stop` and the disconnect. A client disconnect, stdin EOF and Ctrl-C in the client unwind this way only where the client ends the server gently instead of killing it, which is the client's behaviour and not quackd's |
| Started with `--dry-run` | no park at either end. `stop` is still sent, and separately the close finds the arm away from its pose and leaves torque on, and says so |
| A hard kill | nothing runs at all. Task Manager, `taskkill /F` or a SIGKILL takes the process before any of that, and the arm holds its last goal under torque |
| `--by-hand` | not a flag here at all. It belongs to `quackd run`, and it needs a terminal to press Enter at, so there is no hand-placed MCP session |

**The connect park, in the server's own log.** The first two lines of the session's stderr,
captured against the mock arm, whose `shoulder_pan` had been left at 45 before the session
started:

```
quackd-mcp INFO arm-01: moved to the rest pose
quackd-mcp INFO quackd MCP server up: robot=arm-01 transport=mock dry_run=False
```

Read the order. The park comes first, so the arm is folded before the client has a tool list at
all. An arm that was there already and had nothing to travel logs `already at the rest pose`
instead.

A pose that lies past the arm's calibrated travel ([section 07](#07-record-the-rest-pose)) is
parked at the edge of the travel, which counts as getting there, and the session says so once,
between the park and the line that says it is up. Captured on the mock arm registered with
`shoulder_lift` at -118, where the mock's travel for that joint ends at -100:

```
quackd-mcp INFO arm-01: moved to the rest pose
quackd-mcp INFO arm-01: shoulder_lift is recorded at -118 in the rest pose and this calibration lets its servo be driven to -100 and no further, so it parks there and is let go of there, free to settle the rest of the way on its own. Calibrate again with the arm folded (lerobot-calibrate) and record the pose again (quackd robot rest-pose arm-01) to make the fold reachable
quackd-mcp INFO quackd MCP server up: robot=arm-01 transport=mock dry_run=False
```

The first `report_state` reads 0 where 45 was. The mock's numbers, yours will be your arm's:

```
shoulder_pan 0, shoulder_lift -90, elbow_flex 90, wrist_flex 0, wrist_roll 0, gripper 100; torque on; hottest shoulder_pan 30°C; holding nothing
```

**The whole feature is one line.** What the mock transport was asked to do across a session
that connected, ran one verb and closed:

```
['rest', 'stop', 'rest', 'close']
```

Park, hold, park, let go. The first `rest` is the connect, the `stop` and the second `rest` are
the teardown in that order, and `close` is the port. Neither end is the model's to skip.

**A dry run is the same line with the parks taken out:**

```
['stop', 'close']
```

The arm is left where it stood, on the mock still `shoulder_pan` 45, and its torque flag read
`torque=True` afterwards. The session closes on this warning, which is
[M10](#m10-rehearse-with---dry-run) keeping its promise rather than a fault:

```
quackd-mcp WARNING arm-01: the arm is not at its rest pose (shoulder_pan is at 45 with a goal of 0), so torque was left on and it will not fall as it stands: hold it first, because connecting takes torque off every motor for a moment, then run quackd robot release arm-01, or quackd doctor --robot arm-01 to park it, or cut its power
```

**When the arm cannot reach the pose, the session never starts.** The server raises before it
offers a tool, and the message names the joint that fell short and what quackd did about it.
This one is the mock arm, handed `shoulder_lift stopped 40 deg short` as a scripted failure, so
that clause is the capture script's wording and not a sentence quackd writes. A real arm that
stalls names the joint and both numbers, in the form `shoulder_lift is at -50 with a goal of
-90, and it has stopped moving`, and one that runs out of time ends the same shortfall with
`when the time ran out` and the seconds:

```
arm-01: the arm did not reach its rest pose: shoulder_lift stopped 40 deg short. the arm is not at its rest pose (shoulder_pan is at 45 with a goal of 0), so torque was left on and it will not fall as it stands: hold it first, because connecting takes torque off every motor for a moment, then run quackd robot release arm-01, or quackd doctor --robot arm-01 to park it, or cut its power
```

The same warning reaches the server's stderr, under one line carrying the mock's scripted
wording: `quackd-mcp INFO arm-01: shoulder_lift stopped 40 deg short`.

**This is the one that will confuse somebody tomorrow.** In the client it does not look like a
safety rule firing. It looks like the server failed to start, which is literally what happened:
the process raised and exited before the handshake, so the client has nothing to connect to and
no sentence of quackd's to show you. The reason is in the stderr log, at the path
[M08](#m08-the-first-session) gives for your client and
[M13](#m13-when-it-will-not-work) repeats. Then, in this order: hold the arm, because it is
energised and holding itself up, run `quackd robot release arm-01` at a terminal (the server has
exited, so the port is free) or cut its power, clear whatever stopped the fold, and start again.

> [!CAUTION]
> A session that ends any way but cleanly leaves an energised arm. Torque comes off only where
> the arm is known to have reached the recorded pose, or the edge of its travel where the pose
> lies past it, which is deliberate: an arm holding itself up is better than an arm on the desk.
> The one exception is written in the stderr note: where it says quackd could not keep torque on
> and the arm was released where it stood, torque is off and nothing is holding the arm up, so
> read that line before you trust the rest of this. And where it says
> `quackd cannot tell whether the arm is holding itself up`, the close could not read the arm at
> all, as when a cable is out or its supply is cut, so it may be held up or limp: hold it, and
> cut its power. A hard kill, a dry run away from the pose and a failed park all end with servos
> under load, and nothing in the chat says so. There is no Ctrl-C kill switch here the way there
> is in `quackd run`, so the power switch is the one that always works. Once the session has
> ended and the port is free, `quackd robot release arm-01` at a terminal lets a held arm go
> without it. No MCP tool can: taking torque off is a command a person types, never a call a
> model makes.


**Three things here were captured against the mock arm and in the test suite.** The park at
both ends, the refusal and the dry run's missing parks: the mock transport records them and
`tests/test_mcp_server.py` holds them to it. The other rows are how the code reads, with no
capture and no test behind them. No MCP session has yet parked a real arm. The bench arm on
2026-09-15 fell at the end of every run of the day and this is the answer to that, written
afterwards. It first met servos with weight in them on 2026-09-23, in `quackd run`, and could
not reach a fold that lay past the arm's calibrated travel, which is what the parking at the
edge above now answers ([section 07](#07-record-the-rest-pose)). Keep a hand near the arm for
the first connect and the first close, and say in [what to report](#m14-what-to-report)
whether the fold held with torque off and whether the park at the end put it back in shape.

<br>

### M08. The first session

The twin of [section 08](#08-the-first-task), and like that one, no verb the model can reach
here moves a joint. The server itself does: it drives the arm to the rest pose as it connects,
and again as the client quits, so have your hands clear when you start the server and when you
quit. This is the first time Claude talks to your arm: reading, listing, one refusal, one note
kept.

**Start the server with the contract already loaded.** `lerobot-lookout` allows `report_state`
and `stop` and nothing else, so no verb the model can call moves a joint even if it talks itself
into trying, which is what you want the first time. `stop` is not quite nothing: on an arm that
is already energised it re-sends the present position as the goal of every body joint inside its
travel, so the arm holds where it is. It does not energise a limp arm. Writing a goal to a servo
whose torque is off changes nothing, and the only thing in quackd that turns torque back on is
the hand-off in [section 07](#or-start-from-a-pose-you-set-by-hand), which belongs to
`quackd run` and has no MCP equivalent. Add `--duckfile lerobot-lookout` to the args you
registered in [M03](#m03-choose-your-client). In Claude Code:

```bash
claude mcp remove arm
claude mcp add arm -- D:\Development\lerobot-test\.venv\Scripts\quackd.exe serve-mcp --robot arm-01 --duckfile lerobot-lookout
```

In Claude Desktop the flag and its value go on the end of `args` in
`claude_desktop_config.json`, which then reads
`["serve-mcp", "--robot", "arm-01", "--duckfile", "lerobot-lookout"]`. The bare name resolves
because that duck ships with quackd. One of your own needs an absolute path, because a relative
one resolves against a working directory nobody chose.

> [!IMPORTANT]
> A running server keeps the contract the flag gave it, so editing the flag changes nothing
> until you restart. Restart it: `/mcp` in Claude Code or a new session, and in Desktop quit
> the app and reopen it. From inside a running session, `robot_load_duckfile` swaps the
> contract in place, which is the route [M09](#m09-add-the-camera) takes.

**What to type into the chat**, and you can paste this one:

> Recall what you remember about this arm, list your verbs, then read the arm back and say where
> its joints are, whether torque is on and whether anything reads hot. Do not move anything.

The server's instructions ask for `robot_list_verbs` first and `robot_recall` early. What
follows takes them in a reader-friendly order instead, and your model may vary it either way.
Everything quoted below was captured against the mock arm, so the numbers are the mock's.

**`robot_recall` comes first here**, because those instructions ask for it early. On a first
session its `summary` reads `nothing remembered yet: this is the first session on this robot`,
with empty `notes` and `episodes`, and a `path` naming the file those notes live in,
`~/.quackd/memory/arm-01.jsonl`, keyed by the name you registered in
[M06](#m06-first-contact). The capture ran with a scratch memory directory, so its own `path`
is a temp folder rather than your home directory.

**`robot_list` is the arm as quackd sees it.** The row is long, so here it is trimmed to the
part to read:

```json
    {
      "name": "arm-01",
      "adapter": "lerobot",
      "backend": "mock",
      "model": "lerobot-so101",
      "manifest_id": "arm-01",
      "contract": "lerobot-lookout",
      "healthy": true,
      "default": true
    }
```

`"backend": "mock"` is the tell that this was not a real arm, and `contract` is the duck you
loaded at startup. The same row carries `vendor`, `embodiment`, `mobility`, a `digest`,
`health_reason` and `aborted`, plus `datasheet` and `datasheet_text`, the structured and
one-paragraph versions of what this body is. Those two are what the model weighs a task against
at `robot_assess_task` in [M10](#m10-rehearse-with---dry-run).

**`robot_list_verbs` is the body's list with the contract's answer on each row.** Eight verbs
come back with their parameters, all of them, though this contract allows two. Two fields carry
the meaning: `allowed` is the contract's answer right now, and `before_verdict` marks the verbs
that run before a feasibility verdict exists.

| Verb | Safety class | `allowed` here | `before_verdict` |
|---|---|---|---|
| `observe` | safe | no | yes |
| `report_state` | safe | yes | yes |
| `stop` | safe | yes | yes |
| `move_joints` | safe | no | no |
| `gripper` | safe | no | no |
| `place` | safe | no | no |
| `pick` | confirm | no | no |
| `manipulate` | confirm | no | no |

Three of those rows are the mock's. A real arm lists `observe` only when `--camera-url` named a
camera that opened, and lists `pick` and `manipulate` only when the server was started with
`--policy-url` naming a policy server ([section 17](#17-optional-hand-the-arm-to-a-learned-policy)).
Those two are also the only confirm-class verbs, which is why `--yes` changes nothing on a real
arm without one.

**`robot_run_verb` with `report_state` is the first verb you send through the executor**, though
it is not the first thing to touch the bus. `robot_list`'s health row is a round trip of its own,
the heartbeat has been pinging the arm since connect, and the connect-time park drove it before
any tool existed. The summary is one line, and these are the mock's angles and the mock's
temperature:

```
shoulder_pan 0, shoulder_lift -90, elbow_flex 90, wrist_flex 0, wrist_roll 0, gripper 100; torque on; hottest shoulder_pan 30°C; holding nothing
```

Yours will be your own arm's, and the temperature is the number worth writing down, for the
reason [section 06](#06-first-contact) gives. The log comes back with it:

```
tool    robot_run_verb verb='report_state', params={} on arm-01
verb    report_state() from mcp
<-      report_state ok: shoulder_pan 0, shoulder_lift -90, elbow_flex 90, wrist_flex 0, wrist_roll 0, gripper 100; torque on; hottest shoulder_pan 30°C; holding nothing (0.0 s, 0 intents)
done    ok in 0.0 s budget: step 1/12, llm calls 0/12, 0.0/3 min
```

Read it downwards. The `tool` line is the call your client made, arguments and all. The `verb`
line is what quackd turned it into, and `from mcp` says a client asked rather than a run loop.
The `<-` line is the result, with the wall time and the count of intents sent to the arm, zero
because reading state sends nothing. The `done` line is the budget, and those numbers are
`lerobot-lookout`'s rather than the default: 12 steps, 12 model calls, 3 minutes. `llm calls`
reads `0/12` all session, because quackd makes none of them. You are the model.

**Then ask it to move something, and watch the contract refuse:**

```
verb 'move_joints' is not in this duck's allowlist (report_state, stop)
```

That is the contract doing its job rather than something broken. It arrives as a result with
`ok: false` rather than as an error, so the model reads it, knows why, and carries on. The
log says it twice, once as `gate    allowlist: refused` and once as the result, and ends
`done    FAIL in 0.0 s`. A refusal at the allowlist gate costs no step, which is why the `done`
line still reads `step 1/12`.

**`robot_remember` is the only call that writes anything.** Ask Claude to keep one short fact
for next time and the summary comes back as `remembered for future sessions: Commanded gripper
100 read back 98; closed on nothing read 3.`, with `"notes": 1`. One line is appended to the
memory file, and that is the whole of what an MCP session puts on disk:

```
{"kind": "note", "text": "Commanded gripper 100 read back 98; closed on nothing read 3.", "ts": 1789994680.374, "duck": "lerobot-lookout"}
```

**Where the record is.** The server's own view goes to stderr, which your client keeps:

```
quackd-mcp INFO arm-01: already at the rest pose
quackd-mcp INFO quackd MCP server up: robot=arm-01 transport=mock dry_run=False
quackd-mcp INFO arm-01: tool    robot_run_verb verb='report_state', params={} on arm-01
```

The first line is the parking from [M07](#m07-record-the-rest-pose), the arm being driven to
its rest pose before a single tool was offered. The mock was already there, which is why it
reads `already at the rest pose` rather than `moved to the rest pose`. An arm that is not there
moves to get there, and moves again when you quit the client. Claude Code writes that stream to
`%LOCALAPPDATA%\claude-cli-nodejs\Cache\<cwd slug>\mcp-logs-<server name>\<timestamp>.jsonl`,
one JSON object per line, each wrapping the text as `{"error":"Server stderr: ..."}`, which is
the client's framing rather than a sign anything went wrong. Claude Desktop writes
`%APPDATA%\Claude\logs\mcp-server-<name>.log`, and `~/Library/Logs/Claude/` on macOS.
`--no-log`, or `QUACKD_LOG=0` in the environment, turns the log off, both these stderr lines
and the block that rides back on `robot_run_verb`, `robot_observe`, `robot_say` and
`robot_assess_task`. Those four are the ones that carry it; the other five never reach the
arm, so there is nothing behind them to show and nothing to drop. On a `quackd run` that flag
decides only what you watch, because the run directory gets its log either way. Here there is
no run directory, so it is the whole record, which is the note below.

> [!IMPORTANT]
> This is the one place where MCP gives you less than the terminal. `quackd run` writes
> `runs/<timestamp>-<name>/` with the transcript, every frame it captured, a summary and the
> terminal it printed to, and `quackd log` replays it later. An MCP session writes none of
> that: no `runs/` directory, no `transcript.jsonl`, no `frames/`, no `terminal.txt`, no
> episode kept at the end. The record is the chat, the stderr log above, and whatever
> `robot_remember` kept. If you want it, keep the chat.

<br>

### M09. Add the camera

The camera is found and registered exactly as in [section 09](#09-add-the-camera), which keeps the
index hunt, the query keys and the rules for a second camera. Three commands, from the same
terminal as before:

```bash
lerobot-find-cameras opencv
quackd doctor --robot arm-01 --camera-url "opencv://1"
quackd robot edit arm-01 --camera-url "opencv://1?fov=62"
```

**The middle one moves the arm**, and this is the step where you are standing at the bench with
your hands near it aiming a webcam. `doctor` connects, which drops torque for a moment as
`configure()` opens the port, and then drives the arm back to its recorded rest pose before it
lets go. Hands and the webcam clear of the sweep before you run it. The first and the third touch
nothing.

**The lens goes on the url, because there is no flag for it here.** `quackd run` takes
`--fov-deg` and `quackd serve-mcp` does not, so the field of view rides on the url as `?fov=62`.
Without it quackd assumes the simulator's 90 degrees, scales every bearing and distance wrong,
and says so on every detection line. `62` is one camera module's figure: use your own lens's.

> [!IMPORTANT]
> Restart the server after that `robot edit`. A running one keeps the registry it started with, so
> the session already open has no camera and `robot_list_verbs` still does not list `observe`.
> `/mcp` in Claude Code reconnects it, and in Claude Desktop you restart the app. Both move the
> arm. Closing the session drives it to its recorded rest pose and then releases torque, and the
> reconnect drives it to that pose again before the client's model gets a verb, so nobody should be
> in reach when you press it. This is the most likely "why has nothing changed" moment in Part 2.

> [!IMPORTANT]
> Take `--duckfile lerobot-lookout` off the server's command before the rest of this section.
> That duck allows `report_state` and `stop` and nothing else, so with it still loaded every
> `robot_observe` below is refused by the allowlist rather than returning a frame. In Claude Code
> that is a `claude mcp remove arm` and an add without the flag, and in Desktop it is that entry
> gone from `args` and a restart. Both restarts move the arm, as the note above says. The other
> way round is to leave the flag alone and load `arm-look.duck` with `robot_load_duckfile`, which
> this section introduces further down, since that contract does allow `observe`.

**`robot_observe` is a look the model asked for.** In a `quackd run` the frame arrives in every
observation whether the task asked for it or not. Over MCP nothing arrives unless the model asks,
and asking is `robot_observe`: one call, one frame, at the moment the model decided it wanted to
see. On a real SO-101 that is the only way to ask on purpose, because the static manifest
`quackd run` validates a task against cannot know you plugged a webcam in, so `observe` cannot be
in the allowlist there at all.

What comes back from a one-camera arm with the log on is three content blocks rather than one,
`text, image, text`. A text summary, the frame itself as an image block carrying
`mime_type=image/png`, and the log as a final text block. A second camera names each picture and
adds a block per lens, and a step where no frame arrived is one text block with no image in it. A
rehearsal is not one of those cases: `observe` is read-only, so `--dry-run` really opens the
camera and really hands the picture back, the same way `report_state` really reads the servos.
Captured against the mock arm, the first and the last of the three read:

```
arm-01 camera: ball at bearing 30° left ~0.58 m
```

```
log:
tool    robot_observe  on arm-01
verb    observe() from mcp
<-      observe ok: frame captured; ball at bearing 30° left ~0.58 m (0.0 s, 0 intents)
done    ok in 0.0 s budget: step 1/40, llm calls 0/40, 0.0/5 min
```

> [!NOTE]
> That ball is the mock's synthetic scene and nothing on a real desk will produce that line. The
> detector behind it is a colour threshold carrying the simulator's own ranges, so on your desk it
> will usually report nothing, and say so. The picture is still the picture, and a model that takes
> images can ignore the sentence. [Section 12](#12-wave-to-me) has what it can and cannot see.

**A `.duck` that allows `observe` loads here, and the same contract is refused by `quackd run` on
`lerobot:real`.** `robot_load_duckfile` checks the contract against the manifest of the arm
**already connected**, which knows whether a camera opened. A command-line run is checked against a
static manifest instead, and on `lerobot:real` that manifest claims no camera until connect. The
mock's static manifest always declares its camera, so the file used below, which names
`robots: lerobot:mock`, is one `quackd run` would take as well. The difference is the backend, not
the tool that loaded it.

The file is `arm-look.duck`, and it allows `observe`, `report_state` and `stop`, eight steps, three
minutes. Ask the client to load it by absolute path, which is the model calling
`robot_load_duckfile`, and the `note` that comes back reads, on the mock arm again:

```
"note": "The executor now enforces this contract for every call to arm-01. This is a new task: assess it with robot_assess_task before anything moves. What this session already spent still counts: step 0/8, llm calls 0/8, 0.0/3 min."
```

The first contract a session adopts starts its budget fresh, whatever that last sentence says.
The numbers are the proof: `step 0/8` in a session whose `robot_observe` had already counted
`step 1/40`. The carry-over line is boilerplate the tool appends whenever a budget object existed,
which on a first adoption it always has. A second and later load is where it is true, and there
the spent steps, the llm calls and the clock all cross into the new contract, so its budget lands
part spent from the moment it arrives.

**Read "first" as the first adoption rather than the first `robot_load_duckfile`**, because a
`--duckfile` on the command line is adopted as the server starts. The capture above came from a
server started without one. Start it the way [M08](#m08-the-first-session) did, with
`--duckfile lerobot-lookout`, and the contract you load with the tool a minute later is the
*second*, so it carries the spend across instead of starting at zero.

> [!WARNING]
> `--duckfile` on the `serve-mcp` command line is checked against the static manifest instead, so a
> duck that allows `observe` can be refused there and load cleanly through the tool a minute later.
> Pass an absolute path either way, because a server a desktop app spawned starts in a directory you
> did not choose. A bundled name like `lerobot-lookout` resolves by name and needs no path, though
> that one allows no `observe` at all. Its allowlist is `report_state` and `stop`, and it is named
> here only for how the resolution works.

> [!TIP]
> Aim the camera at the volume the arm moves through rather than at the gripper, and aim it before
> you start the server. A session holds the arm under torque from connect until close, so moving a
> webcam while one is open means reaching in beside a live arm: close the session and let torque
> drop first. That was the mistake on the bench on 2026-09-15: the webcam framed the gripper,
> cropping the raised arm out of the picture, so the pilot checked its waves against joint angles
> rather than against a picture.

<br>

### M10. Rehearse with `--dry-run`

**`--dry-run` is a flag on the server, not something you ask for in the chat.** It belongs in
the arguments the client spawns quackd with, and a running server keeps the contract it started
with, so turning a rehearsal on means starting the server again. In Claude Code that is a
remove and an add, then `/mcp` to reconnect, or a fresh session:

```bash
claude mcp remove arm
claude mcp add arm -- D:\Development\lerobot-test\.venv\Scripts\quackd.exe serve-mcp --robot arm-01 --dry-run
```

In Claude Desktop, add `"--dry-run"` to that server's `"args"` array in
`claude_desktop_config.json` and restart the app completely. [M03](#m03-choose-your-client) has
both files in full.

**The `--duckfile lerobot-lookout` of [M08](#m08-the-first-session) is gone from that line on
purpose.** That duck allows `report_state` and `stop` and nothing else, so with it still loaded a
request to move a joint is refused by the allowlist gate long before it reaches either of the
gates this section is about. The rehearsal server starts with no contract, and everything below
was captured that way.

**A dry run still connects to the arm for real.** The port opens, torque comes on, the
heartbeat keeps its round trip going, and the read-only verbs genuinely read the servos. What
is withheld is every verb that is not marked read-only, which is wider than the ones that move a
joint. `stop` carries no such flag and neither does `say`, so both are skipped as well: a `stop`
asked for during a rehearsal reaches no servo, and the only thing that actually stops the arm
while you are rehearsing is cutting its power. So ask the model for `report_state` first,
because that reading is the proof there is something real on the other end. Captured against the
mock arm, so the numbers are the mock's and yours will be your own arm's:

```
shoulder_pan 45, shoulder_lift -90, elbow_flex 90, wrist_flex 0, wrist_roll 0, gripper 100; torque on; hottest shoulder_pan 30°C; holding nothing
```

**Then ask for something that moves.** The feasibility gate below applies to a rehearsal too, so
on a freshly started server that request is refused until a verdict has been recorded. The
capture recorded `feasible`, with the reason `A rehearsal moves nothing.`, and only then asked
for the move. The tool answers as though it had worked:

```
[dry-run] move_joints not sent
```

The log that comes back with the call, and the server's own stderr, carry the whole of it:

```
tool    robot_run_verb verb='move_joints', params={'positions': {'wrist_roll': 10}, 'duration_s': 2} on arm-01
verb    move_joints(positions={'wrist_roll': 10}, duration_s=2) from mcp
gate    dry_run: skipped would run move_joints, sent nothing (positions={'wrist_roll': 10.0}, duration_s=2)
<-      move_joints ok: [dry-run] move_joints not sent (0.0 s, 0 intents)
done    ok in 0.0 s budget: step 2/40, llm calls 0/40, 0.0/5 min
```

The `gate dry_run` line is the place the call stopped. Everything above it happened: the tool
arrived, the verb resolved, and the arguments were parsed into the numbers the arm would have
been sent. Nothing below it reached a servo, which is what `0 intents` says. The step is spent
all the same, `step 2/40` after the `report_state` that took the first, so a rehearsal costs
the same budget a real session does.

> [!WARNING]
> A dry run does not park the arm, at either end. Connecting skips the rest move and so does
> closing, and the teardown is a `stop` and then the close, with no rest move between them. A
> rehearsal therefore leaves the arm exactly where it found it, and the close is what decides
> whether it is still held: torque stays on only where the arm is not at the recorded rest pose,
> where a joint recorded past its travel counts as there at the edge of that travel or anywhere
> beyond it, and then the server says so on its way out. At the rest pose, or on a server
> started against an arm with no rest pose recorded at all, the close releases torque where the
> arm stands and says nothing. [M07](#m07-record-the-rest-pose) has that line and what to do
> about it, which is to hold the arm and run `quackd robot release arm-01` at a terminal once
> the session has ended, or cut its power, rather than walk away from it.

**The feasibility gate is the thing most likely to surprise you here.** A verb that moves the
body is refused until `robot_assess_task` has recorded a verdict the gate accepts, which means
`feasible`, or an `uncertain` that a person has cleared, and a `feasible` whose `needs` this
arm's own datasheet does not meet is refused as well, further down. `uncertain` and
`infeasible` are recorded verdicts too and motion stays refused on both. Loading a `.duck`
starts a new task and wants a new verdict. Asked to move a joint before it has judged, the
model is handed this:

```
move_joints moves the body, and no feasibility verdict has been recorded for this task yet: record a verdict first (feasible, infeasible or uncertain): call robot_assess_task(robot='arm-01', verdict=...) first
```

```
tool    robot_run_verb verb='move_joints', params={'positions': {'wrist_roll': 10}} on arm-01
verb    move_joints(positions={'wrist_roll': 10}) from mcp
gate    verdict: refused move_joints moves the body, and no feasibility verdict has been recorded for this task yet: record a verdict first (feasible, infeasible or uncertain)
<-      move_joints REFUSED: move_joints moves the body, and no feasibility verdict has been recorded for this task yet: record a verdict first (feasible, infeasible or uncertain) (0.0 s, 0 intents)
done    FAIL in 0.0 s budget: step 0/40, llm calls 0/40, 0.0/5 min
```

That pair, and the `uncertain` blocks further down, come from a plain session with no
`--dry-run` and no duck loaded, which is why the counter reads `step 0/40` here after the
rehearsal's `step 2/40` above rather than carrying on from it. The gate is the same one either
way.

`step 0/40` is worth noticing for its own sake. A refusal at this gate costs no step, so a model
that stalls here is not burning the session down while it works out what to do. That holds for
this gate and the ones above it rather than for refusals in general: the step is counted before
a verb's preconditions and its joint ranges are checked, so a refusal from below that point does
spend one, and [M11](#m11-prove-the-safety-net) has one of those.

**Which verbs get through anyway.** On the mock arm, `stop`, `observe` and `report_state`, the
three marked `before_verdict` in [M08](#m08-the-first-session)'s verb list. `observe` is on that
list because the mock ships a camera, and on a real SO-101 it does not appear at all until the
camera of [M09](#m09-add-the-camera) has been registered. The set behind it is wider than this
body's vocabulary, nine names: `stop`, `observe`, `report_state`, `say`, `quack`, `express`,
`gaze`, `look`, `introspect`. The gate is wider still, because a verb the adapter itself marks
read-only under a name quackd does not count as motion runs before a verdict without being in
the set at all. The point of it is order: a pilot has to be able to look at the thing, read the
joints and reach the brake before it judges whether the arm can do what you asked. That is the
verdict gate's reasoning, not the dry run's, and it is why the brake is worth saying twice: in a
rehearsal `stop` is skipped like every other verb that is not read-only.

**Then `uncertain`, which is the interesting one.** It records, and it clears nothing. The tool
answers `"pending": true` and this note:

```
recorded as uncertain, which does not clear verbs that move the body. There is no terminal here: ask the person you are chatting with, then call robot_assess_task again with feasible on your own responsibility, or with infeasible.
```

Meanwhile a verb that moves the body gets the refusal with the model's own reason quoted back
at it:

```
move_joints moves the body, and the verdict is uncertain and nobody has cleared it (The task turns on how wide the gripper opens, which its datasheet does not publish.): record a verdict first (feasible, infeasible or uncertain): call robot_assess_task(robot='arm-01', verdict=...) first
```

Compare that with the terminal. On the bench on 2026-09-15 one dry run ended right here,
because [section 10](#10-rehearse-with---dry-run) asks the person at the keyboard and they said
no, and a no ends the run. Over MCP nothing ends that way. You are in the conversation, so the
model asks you, you answer in the chat, and it calls `robot_assess_task` again. On `feasible`
the gate opens and the session carries on from where it stopped. On `infeasible` it does not:
the tool answers that nothing on that arm will move for this task, names any robot in the flock
that meets what the task needs, and the gate stays shut for the rest of that task.

**A `feasible` can be refused too.** When the verdict names `needs` that this arm's own
datasheet does not meet, the gate stays shut, or shuts again over a verdict that had opened it,
and the call answers this, captured on the mock arm with `needs` of `mobility` any, `terrain`
indoor_flat and a `work_height_m` of 0:

```
this body does not meet what you said the task needs: mobility = any (has none). A feasible verdict cannot rest on a need its own datasheet does not meet, and nothing moves until you answer again. Call robot_assess_task: infeasible if that need decides the task, feasible with the need corrected if you asked for more than the task turns on, or uncertain to put it to a person, who can answer it or publish the figure in the task file's own datasheet block
```

`any` asks for some kind of mobility, and an arm bolted to a table has none. For a task that
goes nowhere `mobility` is `none`, which asks for nothing, and the same verdict with `none` in
it opens the gate. A zero asks for nothing too, which is why the `work_height_m` of 0 was not
held against it.

> [!NOTE]
> A model handed a long verb list and a vague sentence answers `uncertain` more often than one
> given a narrow contract, which is why [M08](#m08-the-first-session) started with a `.duck`
> loaded rather than with a free-form goal. If a session keeps stalling at the gate, load a
> contract with `robot_load_duckfile` and ask again.

<br>

### M11. Prove the safety net

> [!CAUTION]
> This is where the arm starts moving, so from here **a hand stays on the power switch**. There is
> no e-stop, and cutting the servo supply is the only thing that stops this arm in every case. Keep
> the sweep clear and your hands out of it for everything below. The [hardware
> checklist](lerobot-hardware-checklist.md) is the authority on the order these happen in and on
> where a hand stays. What follows is the same five checks, asked of Claude rather than typed.

Take `--dry-run` off the server command and restart it, because a running server keeps the flags it
started with: `/mcp` in Claude Code, or restart Desktop. The first movement is still not the
model's, because connecting parks the arm at the pose you recorded in [section
M07](#m07-record-the-rest-pose). That park has two conditions. If no rest pose was ever recorded
there is nothing to park at, quackd skips the move, and the first movement is the model's after
all. If a pose was recorded and the arm cannot reach it, `connect()` closes the transport and
refuses to start the server at all, saying the arm did not reach its rest pose. Every block below
was captured against the mock arm, so the numbers are the mock's and yours will be your own arm's.

**The clock is already running.** The session gets five wall-clock minutes, and they start when the
server has connected the arm rather than when you start check 1. The `done` lines below count
them out. Five checks on a real arm, with a cable to pull and a server to restart, goes past five
minutes easily, and when it does verbs start coming back `budget exhausted` naming `max_minutes`.
That is the clock running out rather than any of the checks below catching something, so do not
read it as one of them. Restart the server and the five minutes start again.

**Nothing below moves until a verdict is on the record.** Ask for `robot_assess_task` with
`feasible` and a reason, and read the `note` field of what comes back:

```
"note": "verbs that move the body now run.",
```

**1. The gripper, and which way it goes.** Ask for
`robot_run_verb(verb="gripper", params={"open": true})`, then the same call with `false`:

```
gripper open (stopped at 100/100)
gripper closed on nothing (stopped at 0/100)
```

The mock lands exactly on its goal, which a real arm does not. The bench arm on 2026-09-15 was
close rather than exact: commanded 100 it reported 98 and stood open, and closed it settled at 3
with the jaws nearly touching. That 100 is open is an assumption about how your arm was assembled,
and everything quackd believes about holding rests on it. If yours runs the other way, stop here
and say so in an issue.

**2. One joint, small, in the middle of its range.** Ask for
`robot_run_verb(verb="move_joints", params={"positions": {"wrist_roll": 10}, "duration_s": 2})`.
The chat gets one line, `moved wrist_roll=10`, and the call's log has the whole of it:

```
tool    robot_run_verb verb='move_joints', params={'positions': {'wrist_roll': 10}, 'duration_s': 2} on arm-01
verb    move_joints(positions={'wrist_roll': 10}, duration_s=2) from mcp
->      joint x21 over 2.0 s (positions {'wrist_roll': 0.0}/{'wrist_roll': 0.5}/{'wrist_roll': 1.0}..., duration_s 2)
<-      move_joints ok: moved wrist_roll=10 (0.0 s, 21 intents)
done    ok in 0.0 s budget: step 3/40, llm calls 0/40, 0.0/5 min
```

Read the `->` line rather than the others. It is every intent that actually reached the arm,
folded into one line: 21 goals across the two seconds asked for, the first three shown, each
half a degree further along than the last, so that the wrist arrives when the time is up rather
than as fast as the step cap allows. The rest are the model asking, the executor answering, and
the budget after the call. The mock's move takes no time, `0.0 s`, and an arm's takes the two
seconds. The server's stderr carries these same lines with a `quackd-mcp INFO arm-01: ` prefix
on each, as the heartbeat block in check 4 shows.

`duration_s` is five seconds when the call names none, and goes up to 12. A longer one is
refused before anything is sent, with
`invalid params for move_joints: duration_s: Input should be less than or equal to 12`. The step
cap, 5 degrees a tick, is the ceiling on speed, so a time too short for the distance runs at the
cap.

**3. A goal outside the calibrated range.** Ask for
`robot_run_verb(verb="move_joints", params={"positions": {"shoulder_pan": 170}})`, and nothing
should reach the arm:

```
move_joints: shoulder_pan=170.0 is outside this arm's calibrated range -100.0..100.0; LeRobot does not clamp a degrees goal, so quackd refuses it
```

**This is the check [Part 1](#11-prove-the-safety-net) could not make properly.** On a `--goal` run
the model picks the number, so a refusal that came from the pilot rather than the executor teaches
you about the model and nothing about the gate. Here you send 170 yourself and the executor
answers, which is the clearest reason there is to be driving this arm from a client.

`-100.0..100.0` is the mock's range, and a real arm's comes off the calibration file you wrote
in [section M05](#m05-find-the-port-then-calibrate). Use any body joint except `wrist_roll`:
upstream records a full turn for that one rather than anything you swept, so nothing you can
name is outside it. The log shows what the chat does not, which is that nothing went to the arm
at all. Its middle two lines:

```
verb    move_joints(positions={'shoulder_pan': 170}) from mcp
<-      move_joints FAIL: move_joints: shoulder_pan=170.0 is outside this arm's calibrated range -100.0..100.0; LeRobot does not clamp a degrees goal, so quackd refuses it (0.0 s, 0 intents)
```

There is no `->` line and the count is `0 intents`: the verb checks the goal against the travel
the manifest publishes and refuses before it reads the arm or sends anything, in the same words
the backend would refuse with. In 0.13.0 it sent the goal for the backend to refuse and then
held the arm with a `stop`.

**4. Pull the USB cable mid move.** Ask for a long, slow one,
`robot_run_verb(verb="move_joints", params={"positions": {"wrist_roll": 60}, "duration_s": 10})`,
then unplug the arm while it is moving. The heartbeat's round trip fails and the session is
finished as a session: every later call gets this line back rather than reaching the arm.

```
session aborted: the heartbeat failed (mock heartbeat failure (scripted)); restart quackd. `stop` still works and is worth sending.
```

`mock heartbeat failure (scripted)` is the mock's wording, because the mock fails on cue. On a real
arm the reason names the timeout. `stop` is the exception that line names, and it does still
answer, `stopped (velocity zeroed)`. That is deliberate: an aborted session is exactly the moment a
pilot reaches for the brake, so the one verb worth keeping is the one that is kept. Read that
answer for exactly what it is. That `stopped (velocity zeroed)` is the mock's, and the mock's hold
always lands. On a real arm with the cable out the hold cannot reach the bus, the transport
records why, and the verb refuses to claim success: it comes back `ok: false` reading `stop could
not be delivered: the hold did not reach the arm: ...`, ending `otherwise use the hardware
switch`. Either answer proves the session kept the verb. Only a real success says the hold was
written, and even then the arm is still energised on the last goal that did reach it. The server
does not wait to be asked either, and its stderr shows it sending `stop` the moment the heartbeat
failed:

```
quackd-mcp WARNING arm-01: heartbeat failed: mock heartbeat failure (scripted) — sending stop
quackd-mcp INFO arm-01: note    heartbeat failed: mock heartbeat failure (scripted) — sending stop
quackd-mcp INFO arm-01: ->      stop
```

That one travels the same unplugged cable, so it says the server tried and nothing more. The arm
is still energised on the last goal that did reach it, which is what should happen: it must not
sag and it must not carry on. Until the cable is back or the supply is off, nothing in software
is touching this arm, which is why the hand stays on the switch. When that server shuts down,
its close reads the arm through the port it lost and gets no answer, so its stderr ends on
`quackd cannot tell whether the arm is holding itself up (...), so it kept whatever torque the arm has: hold it, and cut its power`.
Hold the arm before you plug the cable back in and restart the server, because the restart
connects and connecting takes torque off every motor for a moment. The new session then parks
the arm on its way up like any other, or refuses to start and says the arm did not reach its
rest pose.

**5. Stopping, and the three ways there are.** There is no Ctrl-C here. Part 1's kill switch is a
terminal running `quackd run`, and an MCP session has no terminal of its own:

- **Ask for `robot_run_verb(verb="stop")`.** It re-sends the present position as the goal of
  every body joint inside its travel, so the arm holds where it is rather than sagging. It
  answers `stopped (velocity zeroed)` on the mock. **It is not a mid-move brake.** Part 1's
  Ctrl-C is: the kill switch sets the executor's abort, which cancels the verb that is running.
  The `stop` verb sets nothing, cancels nothing, and a `move_joints` already in flight sends its
  next goal ten times a second, so it overwrites the hold within a tenth of a second and
  finishes the motion, however long it was asked to take. In practice the model cannot call it
  mid-move anyway, because it is still waiting for that `move_joints` tool call to return.
  `stop` is what you reach for between verbs, and cutting power is what you reach for during
  one.
- **End the session.** A client disconnect, stdin closing, or quitting the client unwinds the
  server, which stops, parks and disconnects every robot it holds. The disconnect releases torque,
  which is LeRobot's default and what quackd asks for over an arm at its rest pose, so the
  ordinary end leaves the arm limp at the rest pose. A rest pose your arm does not hold by
  itself is a falling arm, which is what the bench saw at the end of every run on 2026-09-15
  before the rest pose existed. quackd keeps torque on when the park did not get there, and
  that fallback has fired on hardware: on 2026-09-23, on every run that got to its end, because
  the fold lay past the arm's calibrated travel, and the arm held itself up until its power was cut.
- **Cut power.** Still the only thing that works in every case, including the one where the process
  holding the goal has died.

> [!WARNING]
> A hard kill runs none of the teardown. Ending the client or the server from Task Manager, with
> `taskkill /F`, or with a SIGKILL skips the `stop`, the rest move and the disconnect alike. The
> arm holds its last goal under torque, so it does not fall while the servos hold. That hold is
> not indefinite: nothing is reading temperature once the process is gone, and a loaded joint
> heats until it trips its own overload protection and goes slack without announcing it. Do not
> leave it there. Take the arm's weight, then run `quackd robot release arm-01` at a terminal,
> which lets it go where it stands now that the port is free, or reconnect with
> `quackd doctor --robot arm-01` and let quackd put it down, or cut its supply. There is no
> second Ctrl-C to think about here, because there was no first one.

**`--yes` changes nothing on a bare real arm.** `pick` and `manipulate` are the only
confirm-class verbs on the LeRobot arm, and a real SO-101 with no policy server named does not
list them, so there is no gate for the flag to open until a loaded `.duck` puts a verb under
`confirm:`. Name one with `--policy-url` and both are listed and gated like any other confirm
verb, and other adapters gate more verbs than this one does. With nobody at a terminal to ask, a
gated verb answers this, on the mock arm, which lists `pick` because it carries a scripted
policy:

```
human declined pick: this verb needs human confirmation; start `quackd serve-mcp --yes` to allow it
```

> [!CAUTION]
> If any of these five surprises you, stop. Cut power and read [when it will not
> work](#m13-when-it-will-not-work) before going further.

<br>

### M12. Wave to me

Over MCP the payoff is a sentence typed into the chat, and the server it runs against is the bare
one.

**No `--duckfile`, and by now nothing to take off.** [M08](#m08-the-first-session) and
[M09](#m09-add-the-camera) ran under a duck, and a duck is a short allowlist with a success test
attached. That duck left the command line in [M10](#m10-rehearse-with---dry-run), `--dry-run` left
it again in [M11](#m11-prove-the-safety-net), so the server you are running is already the one
this section wants. If you have registered something else since, this is the line, keeping the
camera from [M09](#m09-add-the-camera):

```bash
claude mcp remove arm
claude mcp add arm -- D:\Development\lerobot-test\.venv\Scripts\quackd.exe serve-mcp --robot arm-01 --camera-url "opencv://1?fov=62"
```

In Desktop the same arguments live in `claude_desktop_config.json`, and a change to them takes a
restart of the app. A running server keeps the contract it started with, and a duck loaded with
`robot_load_duckfile` stays loaded for the rest of that session, so dropping either one is a
restart as well. A restart moves the arm by itself: the server parks it at the rest pose on its
way out, and the next one parks it again on its way up, before your client is offered a single
tool. Stand out of reach until it is up, then stand where the camera can see you. Ask for the
robot list in the chat and read two lines of the answer, captured against the mock arm:

```json
      "contract": null,
      "healthy": true,
```

**What `null` buys you is every verb that is neither `confirm` nor `dangerous`.** The confirm gate
is still shut without `--yes`, and on the mock arm `pick` is the verb behind it, which
[M11](#m11-prove-the-safety-net) ends on. `null` also takes the success test away, and there is no
`declare_success` tool over MCP in any case, so the wave ends with the model saying it waved.
Nothing checks that, and the only record of what ran is the chat and the server's stderr log
that [M08](#m08-the-first-session) locates.

**What it costs is a budget you did not choose.** A bare session runs on 40 verb steps and five
minutes, and the `done` line of every call carries both, as
`budget: step 1/40, llm calls 0/40, 0.0/5 min`. The middle column stays at zero because quackd
calls no model here. The five minutes starts when the server the client spawned has connected
the arm, not at your first verb.

> [!WARNING]
> A session you set up slowly can reach the arm with most of its clock gone. Reading back through
> [M11](#m11-prove-the-safety-net) and aiming the webcam again both count. If it runs out before
> the wave does, load a duck rather than restart. The first duck loaded into a session that never
> had one swaps the ceilings for the contract's own and starts its steps and its clock at zero,
> though its `note` still says `What this session already spent still counts` and then prints a
> counter reading zero, as [M09](#m09-add-the-camera) captured. That sentence is true of a second
> load, which does carry the spend over, and wrong about the first. Loading is also a new task, so
> the verdict gate shuts until the model answers for it. Restarting gives you a fresh clock as
> well, and walks the arm to the rest pose twice on the way, so step out of reach first.

**Then type a sentence.** This works:

```
Wave to me with the arm.
```

That is the honest test. A sentence that says more gives you a better idea of what is coming:

```
Look through the camera first. If you can see a person, greet them: move wrist_flex, shoulder_pan
and elbow_flex back and forth a few times, no more than about 20 degrees from where each one is
now, in small moves of about a second each. Keep wrist_roll especially small, do not touch the
gripper, then return to the start and stop.
```

**What should happen** is that the model lists the verbs, reads the arm with `report_state`,
looks if it has a camera, answers `robot_assess_task` with a verdict because nothing that moves
the body runs before it does, and then issues several small `move_joints` calls alternating
about a neutral pose. Nobody wrote a wave. That is the thesis, and [section 12](#12-wave-to-me)
has what the bench arm did with it on 2026-09-15. No MCP session has yet driven a real arm, so
what your client's model does with that sentence is the open part.

quackd's ceilings hold underneath whatever it decides: no joint outside its calibrated range,
nothing faster than the step cap. Force is not capped, so the sweep has to be clear and your hands
out of it. Your brake between verbs is `stop` asked for in the chat, and there is no Ctrl-C,
which [M11](#m11-prove-the-safety-net) covers along with the thing `stop` is not, which is a way
to interrupt a move already running. `stop` spends a budget step like any other verb, so once
the 40 steps or the five minutes are gone it is refused too, and the last resort is the one the
rest of this page uses, which is to hold the arm and cut its power.

**Whether it can see you** differs in one way that matters. The frame reaches the model only
when it calls `robot_observe`, so ask for a look or it will work from joint readings alone.
That is what happened on the bench, where the webcam was framed on the gripper and cropped the
raised arm out of the picture. [M09](#m09-add-the-camera) has the aiming and the detector.

**Giving it a picture** is shorter here than on the command line. There is no `--image` on
`serve-mcp`. You attach the picture to your chat message and it reaches the model directly
without passing through quackd, which therefore neither sees it, resizes it nor records it.
That is also why there is no copy afterwards. `--image` and `--vision` are `quackd run` flags.

**Making it repeatable** is the same `.duck` file Part 1 ends on, loaded with
`robot_load_duckfile` once the session is up. Give it an absolute path,
`D:\Development\lerobot-test\wave-hello.duck` or the full path from `/` elsewhere, because a
desktop-spawned server starts in a directory you did not choose. Bundled names like
`lerobot-lookout` resolve by name. And the bonus from [M09](#m09-add-the-camera) applies: the
file is checked against the manifest of the arm already connected, camera included, which is
something a `quackd run` cannot do for the same file. Part 1 leaves `observe` out of that duck's
`allow` and its `requires` on purpose, so adding it to both is what makes the bonus worth
anything here.

<br>

### M13. When it will not work

Part 1's [13. When it will not work](#13-when-it-will-not-work) is still the table for
anything the arm itself says, because the arm does not know which pilot it has. What changes
is where those sentences arrive. A wrong port, a goal outside the calibrated range, a stall,
a camera that gave no frame: over MCP each of those comes back inside a result's `summary`,
in more detail in the `log` block under it, and in the server's stderr log. None of it
prints on a terminal you are watching. So read that table there when the arm is the thing
that complained, and read this one here, which is only the failures belonging to the client,
the server and the session.

| What you see | What it means | What to do |
|---|---|---|
| no quackd tools in the client at all | the client never spawned the server, or spawned it and it died at once | Claude Code: `claude mcp list`, then `/mcp` inside a session. Desktop: Connectors, Manage connectors, and restart the app completely ([M03](#m03-choose-your-client)) |
| the server listed as pending approval | it came from a project `.mcp.json`, and nobody has approved it in a session yet | run `claude` in that directory and approve it there. You are asked once per project, not once per session |
| the server will not start, and the log says the command was not found | the path is wrong, or a desktop app cannot see your shell `PATH` | give the absolute path from [M02](#m02-install-quackd). A desktop client has no shell and starts in a directory you did not choose |
| `adapter 'lerobot' needs an extra` | the client launched a different environment from the one you installed into | check the command is your venv's own `quackd`, and that the same venv's `python --version` is 3.12 or newer |
| the session refuses to start, and the log says `the arm did not reach its rest pose` | it could not get home, so not one tool was ever offered | hold the arm and run `quackd robot release arm-01` at a terminal, or cut its power, then clear whatever stopped it and start the session again. The refusal carries the closing note, which says whether torque was left on or the arm is limp in your hands ([M07](#m07-record-the-rest-pose)) |
| a verb refused with `no feasibility verdict has been recorded for this task yet` | the verdict gate, shut until the model answers for this body | ask it to call `robot_assess_task` first ([M10](#m10-rehearse-with---dry-run)) |
| the same verb refused with `the verdict is uncertain and nobody has cleared it` | uncertain does not clear anything that moves the body, and there is no terminal here to ask on | answer the model in the chat, and let it record again on its own responsibility ([M10](#m10-rehearse-with---dry-run)) |
| a `feasible` verdict refused with `this body does not meet what you said the task needs` | the model's own `needs` asked for more than this arm's sheet has, most often `mobility` any for a task that goes nowhere, and the gate stays shut | ask it to correct the need, `none` for a task that goes nowhere, and answer again, or to answer `uncertain` ([M10](#m10-rehearse-with---dry-run)) |
| `verb 'move_joints' is not in this duck's allowlist` | the loaded contract does not allow it, which is the contract doing its job | widen the `.duck` and load it again, or ask for a verb it does allow ([M08](#m08-the-first-session)) |
| a verb refused with `this verb needs human confirmation` | a confirm-class verb, and there is no terminal here to ask on. On this arm `pick` and `manipulate` are, whenever `--policy-url` names a policy server | restart the server with `--yes` in its command, which is the only way to answer that gate from a chat client |
| `budget exhausted` | the session has spent its steps or its minutes | the paragraphs under this table are about this row |
| every verb except `stop` refused with `session aborted` | the heartbeat gave up, or a contract's `abort_when` fired | send `stop`, which is refused by nothing except an exhausted budget, then restart the server. Nothing in an aborted session recovers on its own ([M11](#m11-prove-the-safety-net)) |
| a flock `.duck` refused as not available over MCP | an MCP session is one pilot, and a flock needs a coordinator this process does not run | run that file with `quackd run` from a terminal, which is what the refusal tells you |
| `file not found (also not a bundled starter duck)` for a file that is plainly there | a relative path resolved against the server's working directory, which you did not choose | pass an absolute path. Bundled names like `lerobot-lookout` resolve by name and need none |
| tools, verbs or a camera that do not match what you changed a minute ago | a running server keeps the tools and the contract it started with | restart it: `/mcp` in Claude Code, or a new session. In Desktop, restart the app |
| a summary you cannot act on | the log in a result is capped at thirty lines, and the whole block goes to stderr | read the server's log, which is the last paragraph here |

**The budget row is the one that will bite tomorrow.** Without a loaded `.duck`, a session's
allowlist holds every verb that is not `dangerous`, and the session runs on a default budget
of 40 verb steps and five minutes. The allowlist is the only gate open that wide: a
confirm-class verb still needs `--yes` on the server's command, which is the confirm row
above. That clock starts when the server the client spawned has connected the arm, not when
you asked for the first verb. A session you opened, then spent ten minutes talking through, is
out of minutes before the arm has moved at all.

This is what it looks like, captured against the mock arm on a contract whose budgets were
one step, four model calls and three minutes, so the numbers are small enough to watch. The
first call went through and returned the state:

```
shoulder_pan 0, shoulder_lift -90, elbow_flex 90, wrist_flex 0, wrist_roll 0, gripper 100; torque on; hottest shoulder_pan 30°C; holding nothing
```

That pose and that temperature are the mock's, and yours will be your own arm's. The second
call, the same verb again, came back:

```
budget exhausted: max_steps (1) reached
```

Nothing reached the arm for that one. The budget gate closes before the verb goes anywhere
near the transport, so an exhausted session leaves the arm exactly where the last verb left
it, still under torque, still holding its goal. It is a refusal, not a shutdown.

The budget is also the one gate `stop` does not walk through. `stop` is exempt from the
allowlist, the confirm gate, the verdict gate and the abort gate, but it charges a step like
any other verb, so a session with nothing left refuses the brake with that same sentence.
What you have then is an energised arm holding its goal and a tool surface with no brake left
on it. The way out is to end the server, whose closing path stops the arm and walks it back
to the rest pose before it lets any torque go, or to cut the arm's power.

**Loading a `.duck` restarts the clock**, the first time. `robot_load_duckfile` on a session
that has not adopted a contract yet hands the executor the contract's budget and starts
counting from zero, because a task's five minutes should be the task's rather than whatever
was left of the session's. A second load refunds nothing already spent: the steps, the model
calls and the clock carry across from the first contract. The limits do not. They become the
new contract's, so a pilot that has spent a narrow budget can load a wider file and carry on,
with the spend it already has counted against the wider ceiling.

> [!NOTE]
> After the first contract, no tool zeroes those counters. `robot_load_duckfile` can still raise
> the ceiling, because the loaded contract's budgets become the session's, but the steps and the
> minutes already spent stay spent. So a session that has never adopted a contract has one free
> reset in it, which is the first load, and after that a fresh clock means a fresh server:
> `/mcp` in Claude Code, or restarting Desktop.

**Where the logs are.** Claude Code writes the server's stderr to
`%LOCALAPPDATA%\claude-cli-nodejs\Cache\<cwd slug>\mcp-logs-<server name>\<timestamp>.jsonl`,
one JSON object per line. The server's lines are the objects with an `error` key, wrapped as
`{"error":"Server stderr: ..."}`, and the rest are the client's own `debug` entries for the
connection, the capabilities it negotiated and the teardown. Every object carries a
`timestamp`, a `sessionId` and a `cwd` as well, so grep the file for `Server stderr` rather
than reading it top to bottom. The word `error` there is the client's name for the stream
rather than a verdict on the line, so an ordinary log block reads back as a run of those
objects, and the only escaping an ordinary line shows is the `\r\n` at the end of it. Claude
Desktop writes `%APPDATA%\Claude\logs\mcp-server-<name>.log` on Windows and
`~/Library/Logs/Claude/` on macOS, as plain text. Either one holds the uncapped log, the
heartbeat's own lines, and the reason a session refused to start, which is the one failure
the chat cannot show you because no tool ever appeared. `--no-log`, or `QUACKD_LOG=0` in
the config's `env` block, turns the log off and leaves everything else.

> [!IMPORTANT]
> Nothing quoted in this section came from a real arm. No MCP session has yet driven one, so
> these are the failures quackd knows how to produce on purpose, against the mock and in the
> test suite. The one it could not rehearse is yours, and
> [M14](#m14-what-to-report) is where it goes.

<br>

### M14. What to report

[Open a LeRobot hardware report](https://github.com/rokbenko/quackd/issues/new?template=lerobot-hardware-report.yml),
or a plain issue with the chat pasted into it. A report that says it did not work is worth as
much as one that says it did, and this half of the page is the half with the least behind it.

**What you attach is different here.** An MCP session writes nothing run shaped: no `runs/`
directory, no `transcript.jsonl`, no `frames/`, no `terminal.txt`. Four things stand in for
that.

- **The chat itself.** Every tool call and every result is in it, which is the whole record of
  what the pilot asked for and what the executor answered. Copy it from the first `robot_` call
  to the end.
- **The server's stderr log**, which is where the uncapped log went. Claude Code keeps it under
  `%LOCALAPPDATA%\claude-cli-nodejs\Cache\<cwd slug>\mcp-logs-<server name>\`, one JSON object
  per line. Claude Desktop keeps `%APPDATA%\Claude\logs\mcp-server-<name>.log`, and
  `~/Library/Logs/Claude/` on macOS. [M13](#m13-when-it-will-not-work) has the longer version.
- **The memory file**, unless you passed `--no-memory`, because memory is on by default on
  `serve-mcp`: `~/.quackd/memory/arm-01.jsonl`, one JSON object per line. It is the one thing an
  MCP session leaves on disk, and it is what the next session reads back.
- **A `doctor` run afterwards**, by the same absolute path you gave the client. Run it with the
  sweep clear and the arm free to rest at its pose, because the probe connects, parks the arm
  again on its way through, and then lets go of it:

```bash
D:\Development\lerobot-test\.venv\Scripts\quackd.exe doctor --robot arm-01
```

**Four questions that are still open.** One SO-101 has run quackd, on 2026-09-15 and again on
2026-09-23, both times `quackd run` from a terminal rather than a session like this one. On
2026-09-23 the rest-pose park ran on it and could not reach a fold that lay past the calibrated
travel. Parking at the edge of the travel, which answers that, is exercised only against the
mock arm and in the test suite, and a session's park at both ends only against the mock and in
`tests/test_mcp_server.py`, which is what leaves the first three open.
`quackd run` brackets a run with the same park, so the first two are not questions a session
alone can answer. What is particular here is that the client's lifecycle is what triggers them.
The fourth is open for a different reason: the verdict gate has fired on hardware, once on
2026-09-15, when a dry run ended because the pilot answered `uncertain` and the person at the
keyboard said no, and in twelve runs on 2026-09-23 that stopped at its y/N question, always at a
terminal. What has never happened is a chat client's model answering it.

- **Did the connect-time park work?** Before the client was offered a single tool, the server
  drove the arm to the pose you recorded in [M07](#m07-record-the-rest-pose). Say whether it got
  there, whether the path it took was a sensible one to watch, and whether the log said so.
- **Did the close-time park work?** Ending the session should stop the arm, fold it, and then
  let go, in that order. Say whether the arm was at its pose when torque dropped, and whether it
  stayed where it was once torque was off. If the log said a joint is recorded past its travel,
  say whether that joint settled onto its fold after it was let go at the edge.
- **What did an unclean exit leave?** Kill the server process from Task Manager, the `quackd.exe`
  the client launched rather than the client itself, with the arm somewhere away from its pose.
  None of the teardown runs in that case. What should happen is that the arm holds its last goal
  under torque and does not fall. Say whether it did, and say so either way. Killing the *client*
  is a different test and nobody knows which way it goes, so treat it as its own report: if your
  client leaves the server to notice stdin close, the server unwinds and the arm **moves** to its
  rest pose with nobody expecting it, and if the client kills the subprocess instead, nothing
  parks and the arm stays energised where it stopped. Which one your client does is its own
  behaviour and quackd cannot promise either. Stand clear, watch the arm and the stderr log,
  and say which one you saw.
- **Did the feasibility gate help, or get in the way?** `robot_assess_task` has to be answered
  before anything moves. Say how often the model answered `uncertain` before you cleared it, and
  whether the verdict it wrote matched what the arm then did.

> [!CAUTION]
> The unclean exit is a test you run on purpose, so treat it as one. The arm is left energised
> with its last goal standing. Take the arm's weight first, whichever way you end that.
> `quackd robot release arm-01` at a terminal lets it go where it stands now that the port is
> free, cutting the servo supply is the immediate way, and a fresh connect, the `doctor` run in
> the bullet above, also takes the arm back to its pose and puts it down. Nothing is reading
> temperature once the process is gone, so the hold is not indefinite: a loaded joint heats
> until it trips its own protection, and a joint that trips its own protection goes slack
> without announcing it. End the test in seconds rather than minutes, keep the arm unloaded,
> keep a hand near the switch, and do it with the arm somewhere it can hold safely rather than
> halfway through a reach.

**The rest of the questions are the terminal half's**, because they are about the arm rather
than about how you are talking to it: the holding band, what a joint reads after ten minutes of
work, whether a stall is caught, whether five degrees an action felt right in the room, and
which end of the gripper's range is open. [Section 14](#14-what-to-report) writes each one out,
and an answer from an MCP session counts the same as an answer from a run.

**If your arm did something this one did not**, change the `lerobot:real` row in
[adapter-status.md](adapter-status.md) in the same commit, and say that an MCP session did it.
That row describes two afternoons at a terminal, so whichever way yours went, it is the first
of its kind on that page.
