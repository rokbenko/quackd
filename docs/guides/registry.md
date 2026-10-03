# Registered robots and flocks

A name for a robot, kept between runs. Not the *verb* registry
([architecture.md](architecture.md)), which is a robot's vocabulary: this is the other half of
the word, a name for a body plus how to reach it.

## Why

Reaching a real robot took a spec, an address, a token and a camera URL, and one of them was a
secret:

```bash
quackd run fetch --robot open_duck:bridge --address tcp://10.0.0.5:9871 \
  --token 8f2c... --camera-url http://10.0.0.5:9872/snapshot.jpg
```

Every run retyped them, so the token lived in shell history from then on. `--robots
name=<adapter>:<backend>,...` gave a name, and the name died with the process. Register it
once instead:

```bash
quackd robot add scout open_duck:bridge --address tcp://10.0.0.5:9871 --token 8f2c...
quackd run fetch --robot scout
```

`--robot NAME` works wherever a robot is run or inspected: `run`, `validate`, `list-verbs`,
`doctor`, `serve-mcp` and `quackd memory`. Two commands take `--robot` and not a registered
name: `record`, which pins the simulator, and `announce`, which advertises a static manifest
and so wants an `<adapter>:<backend>` spec.

## The commands

```bash
quackd robot add NAME <adapter>[:<backend>]   # register one
quackd robot list [--probe]                   # what is registered, and optionally who answers
quackd robot show NAME                        # everything about one, including what it remembers
quackd robot edit NAME [--field X] [--clear F] # change it
quackd robot rest-pose NAME [--clear]         # read where this arm rests, off the arm
quackd robot release NAME [--yes]             # hold the arm: torque off, wherever it stands
quackd robot remove NAME [--force]            # forget it
quackd robot twin SOURCE [NAME] [--force]     # a simulator of an arm, to rehearse on
```

`add` takes `--address`, `--camera-url`, `--token`, `--host` and `--host-token` (the same five
flags `run` takes), plus `--llm VENDOR[:MODEL]` for the pilot that drives this robot, and
`--note` for a line only people read. `--camera-url` repeats, for the one body that reads more
than one camera. `edit` takes the same flags, and `--clear` empties a field.

```
$ quackd robot add duck-a microduck:mock --note "the cream one"
+ added duck-a: microduck:mock
  quackd run <duck> --robot duck-a
```

```
$ quackd robot list
robots (--robot NAME)
+-------------------------------------------------+
| name   | robot          | pilot | note          |
|--------+----------------+-------+---------------|
| arm    | lerobot:mock   | fake  |               |
| duck-a | microduck:mock |       | the cream one |
| duck-b | microduck:mock |       |               |
+-------------------------------------------------+
```

A column nobody has filled is left out, so the table stays readable on a narrow terminal.
`--json` prints one object per line and nothing else, for a script.

```
$ quackd robot show arm
name        arm
robot       lerobot:mock
body        lerobot-so101 (arm, mobility none) 8 verbs: observe, report_state, stop,
            move_joints, gripper, place, pick, manipulate
address     -
camera      -
rest pose   shoulder_pan 0.0
            shoulder_lift -90.0
            elbow_flex 90.0
            wrist_flex 0.0
            wrist_roll 0.0
            gripper 100.0
token       -
host        -
host token  -
pilot       fake
note        -
flocks      -
added       2026-09-25T10:13:26Z
updated     2026-09-25T10:13:29Z
memory      0 notes, 0 runs  ~/.quackd/memory/arm.jsonl
```

`body` is the robot's static manifest, read without connecting to anything. `rest pose` is the
one field here that was measured rather than typed, and the section below is what it is for.
`updated` is later than `added` because recording that pose is an edit. `memory` prints the
file's full path, which this page writes from your home directory as `~`. `--json` is the same
record for a script, with each token reduced to whether there is one:

```
$ quackd robot show arm --json
{"name": "arm", "spec": "lerobot:mock", "adapter": "lerobot", "backend": "mock", "address": null,
"camera_url": null, "rest_pose": {"shoulder_pan": 0.0, "shoulder_lift": -90.0, "elbow_flex": 90.0,
"wrist_flex": 0.0, "wrist_roll": 0.0, "gripper": 100.0}, "token_set": false, "host": null,
"host_token_set": false, "llm": "fake", "note": null, "added": "2026-09-25T10:13:26Z",
"updated": "2026-09-25T10:13:29Z", "flocks": []}
```

That is one line of output, wrapped here to fit the page.

## Several cameras

`--camera-url` can be given more than once, on `add`, on `edit` and on a run:

```
$ quackd robot add arm-01 lerobot:real --address COM5 \
    --camera-url "opencv://1?name=top" --camera-url "opencv://2?name=side"
+ added arm-01: lerobot:real at COM5
  quackd run <duck> --robot arm-01
```

```
$ quackd robot show arm-01     # the camera rows
camera     opencv://1?name=top
           opencv://2?name=side
```

Order is kept, and the first is the primary: the camera `--fov-deg` describes, the one the
detections line reports, and the only one the verbs that steer by sight read. Every frame still
reaches a provider that takes images, each labelled with its camera's name, which is also what
`frames/NNNN-<name>.png` in the run directory is named by.

Only the LeRobot arm reads several, on the desk (`lerobot:real`) or in its simulator
(`lerobot:mujoco`). Every other body refuses a second one where it is registered, rather than
opening the first and dropping the rest:

```
$ quackd robot add duck-a microduck:mock --camera-url a --camera-url b
x error: microduck:mock takes one camera url; only lerobot:real and lerobot:mujoco take several
```

The rules the urls themselves keep, a `?name=` on each, unique names and no index used twice,
are the arm's and are in [adapters/lerobot.md](adapters/lerobot.md): the registry stores what
you gave it and the arm refuses at connect. A second camera also costs what a second camera
costs: the last two exchanges keep their images, so a two-camera run carries four pictures in
every request where a one-camera run carries two. On Claude Opus 5.5 and Fable 5.1, whose old
frames are trimmed every eight exchanges rather than on every one, that is up to eighteen where
one camera is nine.

## A board it uses

`--host` and `--host-token` keep the board this robot's runs reach: a machine quackd uses and
never runs on, such as an NVIDIA Jetson running quackd's host daemon, for the model server,
the camera and the detector on it ([jetson.md](jetson.md)).

```
$ quackd robot add bench lerobot:real --address COM5 --host jetson.local --host-token 3b9a...
+ added bench: lerobot:real at COM5, host jetson.local
  quackd run <duck> --robot bench
```

`robot show` prints the host and only whether a token is set, and `--json` does the same as
`host` and `host_token_set`. `robot list` gains a `host` column once some robot has one.

```
$ quackd robot show bench     # the host rows
host        jetson.local
host token  set
```

A run of `bench` then uses that board with no flag. `--host` on the line beats the stored one,
and the stored one beats `QUACKD_HOST`. The token climbs its own ladder, `--host-token`, then
the stored one, then `QUACKD_HOST_TOKEN`, with one rule the other fields do not need: the
robot's token rides only when the robot stores a board, so it is never sent to a board the
environment named. `--host 127.0.0.1` through an ssh tunnel still carries it, because that is
the same board reached another way. A host token therefore needs a host, and clearing the host
clears its token:

```
$ quackd robot edit bench --clear host
+ updated bench: host, host-token

$ quackd robot edit bench --host-token 3b9a...
x error: bench would have a host token and no host: the token is for one board's daemon, so give
--host too, or --clear host-token
```

Nothing asks the board anything while you register it: `add` and `edit` check that the host
is a machine with an optional port and store it. `quackd doctor --robot bench` is where the
board is asked, through the stored host and token. Both fields are left out of `robots.json`
while they are empty, so a file that never named a board is still one quackd 0.12 to 0.14
reads. A robot that does name a board makes the whole file one those releases refuse, since
they read it with no room for a field they do not know, and two installs on one machine share
`~/.quackd` unless `QUACKD_REGISTRY_DIR` moves one of them.

## The rest pose

A LeRobot arm goes limp the moment it is disconnected, because LeRobot's own `disconnect()`
disables torque by its default, and a clean close with no rest pose recorded asks for exactly
that. On the bench that meant the arm fell at the end of every run, and every run started from
wherever the last one had left it. A rest pose answers both: one pose, kept under the robot's
name, that a run drives the arm to before the pilot gets control and returns it to before torque
is released.

It is **read off the arm, never typed.** Nothing is connected while you set it up, so the arm
is limp. Fold it by hand into a pose it holds with the power off, then record where it ended
up:

```
$ quackd robot rest-pose arm --yes
arm (lerobot:mock) is at
shoulder_pan   0.0
shoulder_lift  -90.0
elbow_flex     90.0
wrist_flex     0.0
wrist_roll     0.0
gripper        100.0
+ recorded arm's rest pose (6 joints)
  quackd run <duck> --robot arm starts from it and returns to it before letting go
```

Those joints are the mock arm's; a real SO-101 reports its own. Without `--yes` the same
readings are printed and the command asks before writing them, and where there is no terminal
to ask on it says so instead of guessing. `--address` reaches an arm the registry has no
address for yet. There is no flag that takes a pose as numbers, because a pose nobody watched
the arm hold is a pose that may not hold.

`--clear` forgets it, and `quackd robot edit NAME --clear rest-pose` is the same thing by the
other door:

```
$ quackd robot rest-pose arm --clear
+ cleared arm's rest pose
  a run now leaves the arm where it stands, and torque drops there
```

Only the LeRobot arm is parked today, and a body that cannot hold a pose refuses to keep one
rather than keeping it and ignoring it:

```
$ quackd robot rest-pose duck --yes
x error: duck (microduck:mock) has no joints, so there is no rest pose to record
  a rest pose is for an arm: quackd list-adapters

$ quackd robot rest-pose cart --yes
x error: cart (xlerobot:mock) has joints, and quackd does not drive it to a rest pose yet: only the
LeRobot arm does today
```

What a recorded pose then changes is [safety.md](safety.md): a run drives the arm to it before
the pilot gets control and back to it on every exit path there is, an MCP session does the same
at both ends and refuses to start if it cannot get there, and `quackd doctor` returns the arm
it probed. The arm's own side of it, including what happens to a pose recorded past the travel
the arm's calibration recorded, is
[adapters/lerobot.md](adapters/lerobot.md#a-pose-past-the-travel).

A pose is kept exactly as it was read, and it is only as good as the calibration it was read
under. A new calibration moves the zero of any joint whose travel it records differently, so
the angles kept here name a different shape after one: record the pose again whenever the arm
is calibrated again.

> [!WARNING]
> One rest pose has been recorded off a real arm, and it could not be reached. The SO-101 that
> fell at the end of every run on 2026-09-15, which is the reason this exists, ran again on
> 2026-09-23 with a pose recorded, and that pose lay past the travel the arm's calibration
> recorded, where its servos will not be driven
> ([ADR-0045](adr/0045-a-rest-pose-the-calibration-cannot-reach.md)). Calibrate with every
> joint taken all the way into the fold before you record one
> ([lerobot-first-run.md](lerobot-first-run.md#07-record-the-rest-pose)). Every joint value on
> this page is a mock's.

### Releasing the arm where it stands

An arm that could not get back to its rest pose keeps its torque at the end of a run, holding
itself up rather than falling, and the line the run ends on says so. `quackd robot release`
is how a person holding that arm takes the torque off without reaching for the power switch:

```
$ quackd robot release arm --yes
! connecting takes torque off every motor for a moment, because LeRobot
configures them with it off, and the release then lets the arm fall from
wherever it is: hold it now, and keep hold of it until it is down
arm (lerobot:mock) is at
shoulder_pan   0.0
shoulder_lift  -90.0
elbow_flex     90.0
wrist_flex     0.0
wrist_roll     0.0
gripper        100.0
+ torque reads off on every joint of arm
! the arm is limp and in your hands (torque was taken off where it stood,
because you asked for it): put it down before you let go of it, because nothing
is holding it up
```

The warning comes before anything connects, because connecting is the first thing that takes a
real arm's torque off. Without `--yes` the question comes next, `release torque on arm? [y/N]`,
and nothing connects until it is answered. With no terminal and no `--yes` it refuses rather
than guessing, and a body that is never handed to a person refuses by name:

```
$ quackd robot release arm
x error: no terminal to ask on: pass --yes to release it
  hold the arm, then quackd robot release arm --yes

$ quackd robot release duck --yes
x error: duck (microduck:mock) is not a body quackd takes torque off: only the
LeRobot arm is
  quackd list-adapters
```

No terminal means a standard input that is not a terminal, such as a pipe. On Windows a
standard input redirected from `NUL` counts as a terminal, so `< NUL` in `cmd`, and
`< /dev/null` in Git Bash too, gets the warning and the question instead of that refusal.
Nothing answers the question, so the command aborts and exits 1, and that is as safe as the
refusal, because it has not connected yet:

```
$ quackd robot release arm < /dev/null
! connecting takes torque off every motor for a moment, because LeRobot
configures them with it off, and the release then lets the arm fall from
wherever it is: hold it now, and keep hold of it until it is down
release torque on arm? [y/N]: Aborted.
```

It uses the registered rest pose and no camera, and it exits 1 unless every motor read torque
off afterwards. Those joints are the mock arm's, and its connect takes nothing off, so on the
mock the warning is only printed. What each other ending means, and the same offer a run makes
at its own terminal when its rest move missed, is
[adapters/lerobot.md](adapters/lerobot.md#releasing-it-where-it-stands).

## A simulator of an arm

`quackd robot twin SOURCE [NAME]` registers NAME, `SOURCE-sim` unless you give one, as
`lerobot:mujoco`: the real backend's own code over a physics model of the arm. Its address is
the calibration file SOURCE's runs read, found where LeRobot keeps it under SOURCE's name, so
the simulated arm has the travel the real one has. SOURCE's rest pose and pilot are copied
with it, and so is each camera the simulator renders, which is one named front, top or wrist.
Any other camera is left out and named in the output, because the simulator would refuse it on
every run. Its memory is its own, kept under NAME.

```
$ quackd robot twin arm-01
+ added arm-01-sim: lerobot:mujoco, a simulator of arm-01 on
/home/you/.cache/huggingface/lerobot/calibration/robots/so_follower/arm-01.json
  copied from arm-01: rest pose, pilot openai:gpt-6-sol, 2 camera urls
! robots.json now holds a lerobot:mujoco robot, and quackd 0.14 and earlier cannot read the file at all: quackd robot remove arm-01-sim before going back to one
  quackd preflight <duck> --robot arm-01-sim
```

An arm registered with a rest pose and neither a pilot nor a camera, as the lab's `arm-01` is,
gets `copied from arm-01: rest pose` and then `arm-01 has no pilot or camera to copy`, and a
hint that ends `--llm VENDOR[:MODEL]`, since its twin has no pilot to rehearse with until you
name one.

That warning is the one cost of a twin. quackd 0.14 and earlier check every robot in the file
against the backends they know, and `lerobot:mujoco` is not one of them, so one of those
installs sharing `~/.quackd` refuses every command that reads the registry until every
`lerobot:mujoco` robot in it is removed, not only the twin just made. Where the file holds more
than one, the warning names them all. To try a twin while an older quackd still reads
`~/.quackd`, give this one a registry of its own with `QUACKD_REGISTRY_DIR` or `--registry-dir`,
and register the arm there as well.

`--address PATH` names the calibration file instead, for an arm calibrated under another id or
on another machine. A source that is not registered, is not a LeRobot arm or has no
calibration file is refused, and so is a NAME that is the source's own. For every other LeRobot
robot the address is the serial port, so a port given here, such as `COM5` or `/dev/ttyACM0`, is
refused on its shape before anything opens it or looks for it. The simulator does the same with
a `lerobot:mujoco` robot registered on a port by `robot add`, as `run`, `preflight` or any other
command builds it. A NAME that is already
registered is refused as `add` refuses it. `--force` replaces it, and only where it is already a
`lerobot:mujoco` robot, so no real arm is ever overwritten by its own simulator.
`quackd preflight` is what the twin is for: it rehearses task files on it, and refuses any robot
that is not a simulator.

## Probing

`--probe` connects to every registered robot, asks how it is, and closes.

```
$ quackd robot list --probe
robots (--robot NAME)
+---------------------------------------------------------------------+
| name   | robot          | pilot | note          | reachable         |
|--------+----------------+-------+---------------+-------------------|
| arm    | lerobot:mock   | fake  |               | + ok              |
| duck-a | microduck:mock |       | the cream one | + ok, battery 88% |
| duck-b | microduck:mock |       |               | + ok, battery 88% |
+---------------------------------------------------------------------+
```

A robot that did not answer shows the reason and the command exits 1, so a script can branch
on it. `--timeout` is per robot and they are probed at once. `microduck:mujoco` is skipped:
connecting to it downloads a model, which is not a liveness check. Every backend named `mock`
always answers, which is what the rows above are.

A probe reads and lets go: it never drives an arm to its rest pose. So an arm that is not at
that pose ends a probe holding itself up rather than sagging, and the same row says so: `+ ok,
torque left on: not at its rest pose`. `quackd doctor` is the other way round and parks the arm
it probed ([safety.md](safety.md)). A probe's connect still takes torque off every motor for a
moment, like any connect, and it prints no warning first, so hold an arm that is holding itself
up before you probe it.

Two rarer endings read differently. `torque unknown: the arm did not answer the close` is an arm
that stopped answering before the close could read it, which is also how an arm whose power was
cut at the switch looks, so hold it as though it may be limp.
`torque could NOT be kept: the arm was released where it stood` is an arm quackd could not keep
powered, so check whether it is still where you left it.

No registered robot has been probed on hardware. The one real robot quackd has driven, the
SO-101 arm, was reached on 2026-09-15 as `--robot lerobot:real --address COM3` on the command
line, before it had a name here at all ([lerobot-first-run.md](lerobot-first-run.md)). On
2026-09-23 the same arm ran 26 runs registered as `arm-01`, with a rest pose recorded under that
name, so a name and a rest pose have met hardware. A probe and a stored camera are still
mock-only.

## Names

A name is a slug: lowercase letters, digits and hyphens, starting with a letter or digit, 64
characters at most. Three kinds of name are refused, because each already means something
else on a command line:

| Refused | Because |
|---|---|
| any number (`3`, `42`, `007`) | `--flock 3` already means three simulated ducks |
| any adapter name (`microduck`, `lerobot`, ...) | `--robot microduck` already means `microduck:sim2d` |
| any `adapter-backend` slug (`microduck-sim2d`, ...) | that is the memory file an unregistered run of that body already opens |

A colon is what tells the two vocabularies apart: `--robot duck-a` is a name, `--robot
microduck:mock` is a spec, and a bare word that is neither says so in one line.

## Where it lives

| | |
|---|---|
| Directory | `--registry-dir`, else `$QUACKD_REGISTRY_DIR`, else `~/.quackd` |
| Files | `robots.json`, and `flocks.json` for [flocks](flock.md) |
| Format | one JSON object, the name of each robot as its key |

```jsonc
{
  "version": 1,
  "robots": {
    "arm": {
      "spec": "lerobot:mock",
      "address": null,
      "token": null,
      "camera_url": null,
      "rest_pose": {
        "shoulder_pan": 0.0,
        "shoulder_lift": -90.0,
        "elbow_flex": 90.0,
        "wrist_flex": 0.0,
        "wrist_roll": 0.0,
        "gripper": 100.0
      },
      "llm": "fake",
      "note": null,
      "added": "2026-09-25T10:13:26Z",
      "updated": "2026-09-25T10:13:29Z"
    }
  }
}
```

Three of those fields carry more than one shape:

| Field | Shape |
|---|---|
| `camera_url` | `null`, one url as a string, or several as a list in the order given, the first being the primary. One camera is stored as a string, so a `robots.json` written by 0.9 reads back unchanged |
| `rest_pose` | `null`, or degrees per joint as `quackd robot rest-pose` read them off the arm. `null` on every body quackd does not park, which is every body but the LeRobot arm |
| `llm` | `null`, a vendor on its own (`"fake"`, `"anthropic"`), or a vendor and a model (`"anthropic:claude-opus-5"`). One key, in the shape `--llm` takes, and stored canonically: a bare catalogue id is written back with its vendor in front |

**This was two keys until 0.11.** `provider` and `model` were separate, and a file written by
0.11 or earlier still has them, because nobody re-saves `robots.json` on upgrade. It reads back
folded into the new shape, so `{"provider": "anthropic", "model": "claude-opus-5"}` is the same
robot as `"llm": "anthropic:claude-opus-5"`, and the next write of that robot, a
`quackd robot edit` or anything else that saves the file, stores the new key. A file carrying
both `llm` and one of the old pair is refused by name instead: it says two different things
about which pilot this robot uses, and guessing which half was meant is worse than stopping and
letting you delete the line you did not want.

A file that says something untrue names itself rather than being trimmed to fit: two camera
urls under a body that reads one are refused when the file is read, and a rest pose under a
body that does not park is refused when that robot is built.

It is a file you can open. Each write goes to a temporary file renamed over the old one, so a
reader never sees half of it, and nothing is serialised: two commands writing at the same
instant are a race and the later one wins. That is the same bargain
[memory](memory.md) makes, for the same reason.

Reads are stricter than memory's. A note a model wrote is worth skipping when it is malformed;
a robot's address is not, so an unknown field or a broken file names itself and stops the
command rather than being quietly dropped.

**Tokens are stored in plain text.** `robots.json` is a file in your home directory, not a
secret store, and it holds a robot's `token` and its board's `host_token` alike. quackd masks
both in everything it prints, including `--json`, which says only whether each is set.
`SECURITY.md` says the same.

## What a name changes

**Memory is keyed by it.** An unregistered run keys its notes by `adapter:backend`, so two
Microducks on one desk shared one file. A registered robot keys by its name, so `duck-a` and
`duck-b` keep separate notes ([memory.md](memory.md),
[ADR-0034](adr/0034-registered-robots-and-pilot-flocks.md)).

**The manifest id is it.** `quackd validate hello-world --robot duck-a` reports the robot as
`duck-a`, which is what `--robots name=spec` has always done for a flock.

**The pilot can be it.** `quackd robot add scout open_duck:bridge --llm anthropic` means
`quackd run fetch --robot scout` uses Claude without a flag. `--llm` on the line still wins,
and the stored one still beats `QUACKD_LLM`.

**Endpoints come from it.** `--address`, `--token`, `--camera-url`, `--host` and
`--host-token` on the line each override the stored one, field by field, because reaching the
same robot through a tunnel today is not renaming it. `--camera-url` overrides as a set rather
than one url at a time: pass it twice and the two you passed are the cameras for that run,
stored ones included.

**The rest pose does not.** There is no flag for it on `run`, `doctor` or `serve-mcp`. The
others are addresses and their tokens, and an address is a route to the same robot; a rest
pose is a measurement of the arm in front of you, so it changes by being recorded again or
cleared (`quackd robot rest-pose NAME`), never by a number typed on a command line.

## Flocks

A flock is a list of registered robot names that you keep, so several robots can be handed one
task without retyping who they are. `quackd flock` is its CRUD:

```bash
quackd flock create NAME [--robot A --robot B] [--description "..."]
quackd flock list
quackd flock show NAME
quackd flock edit NAME [--add R] [--remove R] [--description "..."] [--rename NEW]
quackd flock delete NAME
```

```
$ quackd flock create kitchen --robot duck-a --robot arm --description "the two by the sink"
+ created flock kitchen: duck-a, arm (2 robots)
  quackd run <duck> --flock kitchen
```

With no `--robot` it prints what you have registered, numbered, and asks:

```
$ quackd flock create pair
robots you have registered
+------------------------------------------------+
| # | name   | robot          | note             |
|---+--------+----------------+------------------|
| 1 | arm    | lerobot:mock   |                  |
| 2 | duck-a | microduck:mock | the cream one    |
| 3 | duck-b | microduck:mock |                  |
+------------------------------------------------+
which robots? (numbers or names, comma separated, empty to cancel): 2, 3
+ created flock pair: duck-a, duck-b (2 robots)
```

Numbers and names can be mixed, a bad answer says what was wrong and asks again up to three
times, and an empty answer cancels. Where there is no terminal to ask on, which is any script,
it says so and tells you to pass `--robot` instead.

```
$ quackd flock list
flocks (--flock NAME)
+-------------------------------------------------------+
| name    | robots       | status | description         |
|---------+--------------+--------+---------------------|
| kitchen | duck-a - arm | ok     | the two by the sink |
+-------------------------------------------------------+
```

Order is kept, because it is the order the members are listed and coloured in when the flock
runs. A flock stores 1 to 8 robots and **runs** with 2 to 8, so a flock you are still building
is stored and marked rather than refused.

```jsonc
{
  "version": 1,
  "flocks": {
    "kitchen": {
      "members": ["duck-a", "arm"],
      "description": "the two by the sink",
      "created": "2026-09-13T13:19:58Z",
      "updated": "2026-09-13T13:19:58Z"
    }
  }
}
```

### When a member goes missing

The one broken state either file can be in is a flock naming a robot nobody registered.
`quackd robot remove` will not create it: it refuses while a flock lists the robot, names the
flocks, and `--force` drops it from them instead.

Hand-editing the file can still create it, so every read reports it and running refuses:

```
$ quackd flock list
flocks (--flock NAME)
+---------------------------------------------------------------+
| name    | robots          | status                 | description |
|---------+-----------------+------------------------+-------------|
| kitchen | ! duck-a - arm  | ! duck-a not registered|             |
+---------------------------------------------------------------+
a robot marked as not registered was removed by hand: quackd robot add it back, or
quackd flock edit NAME --remove it
```

Either repair works. quackd will not quietly run the smaller flock, because a member you put
there on purpose going missing is not a detail.

## See also

- [flock.md](flock.md) for what happens when a flock runs
- [memory.md](memory.md) for what each robot remembers between runs
- [mcp.md](mcp.md) for `quackd serve-mcp --robot NAME`
- [safety.md](safety.md) for what a recorded rest pose does at the end of a run, and what
  happens when the arm cannot reach it
- [ADR-0034](adr/0034-registered-robots-and-pilot-flocks.md) for why any of this exists
