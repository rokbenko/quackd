# quackd Passport on NVIDIA Nemotron and Nebius

quackd's entry to the **Nebius x NVIDIA Global AI Hackathon 2026**, Physical AI track.

Robots from different makers are starting to share hallways, front doors and warehouse floors,
and none of them can say who it is to a robot it has never met. **quackd Passport**, which quackd
0.23 shipped, is the open answer: a small signed document a robot serves about itself. Its
name, maker and owner, its body with sourced numbers (mass, height, payload, reach), its skills,
what it cannot do, how a person stops it, and where to talk to it. Ed25519 signatures, discovery
over HTTP, DNS-SD, MQTT and QR stickers, published test vectors, and a talk protocol in which a
robot may ask another for a pause or to identify itself.

quackd is the agent runtime: it connects each robot, gives it a pilot, and enforces what that
pilot may do. This edition runs quackd onboard each robot. A robot's own run holds its key,
serves its passport, decides by fixed code how far to trust another robot and whether to grant
it a pause, and flies its pilot. Here one laptop stands in for both robots' own computers. Every
pilot thinks with NVIDIA Nemotron on Nebius Token Factory, which also explains a passport to the
person standing next to a robot, and tells which passport belongs to which robot in a camera's
view.

The demo is a postman and a duck. **Agile ONE SN**, the humanoid Agile Robots built on NVIDIA
Isaac GR00T, brings a letter to a home. **Microduck**, Pollen Robotics' 25 cm duck, is the
household's robot. They have never met, and from the duck's passport the postman learns that its
beak cannot take a letter from a hand, so it lays the letter at the duck's feet and the duck
scoops it up.

The property the demo is built around: **a stranger's words never reach your robot's model.**
Two robots that never met see each other's passport in closed words and numbers only. Words flow
once a person has pinned the other robot, the way a booked delivery would. A stranger may ask
for a pause, which fixed code grants within limits and which ends on its own; no robot can order
another to move, or to stop for good.

> [!NOTE]
> The two doorstep acts, `explain` and `whois` ran live on Nebius Token Factory on 27 October
> 2026, with both robots piloted by Nemotron: what each printed, what it cost and the records
> are in [Tested live on Nebius Token Factory](#tested-live-on-nebius-token-factory). The test
> suite also runs the commands offline, against stand-ins for Token Factory and for a robot's own
> model server, with real quackd 0.23 runs, all but two that need hardware or a network:
> `whois --camera` and `whois --lan`.

## How NVIDIA and Nebius are used

| Part | NVIDIA model | Nebius |
|---|---|---|
| The robots' pilots | a Nemotron model with tool calling | Token Factory chat completions, one per turn |
| `explain` | a Nemotron model | Token Factory, answering from passport fields |
| `whois` | an NVIDIA vision model (a Nemotron VL or Omni row) | Token Factory, an image and a JSON schema |

No model id is written into the code. Every command reads the key's live `GET /v1/models` list
and takes an NVIDIA id only when that list carries it, spelled as the list spells it. A pilot
pointed at a robot's own model server with `--onboard` takes its id from that server's list the
same way.

The postman comes from NVIDIA's robotics ecosystem too: Agile Robots launched Agile ONE SN on
21 October 2026 as "a humanoid reference platform built on NVIDIA Isaac GR00T", with "NVIDIA
Jetson Thor onboard compute for real-time sensor processing and robot inference".

**Where Token Factory sped the work up.** quackd already spoke to OpenAI-compatible servers, so
giving every robot a Nemotron pilot took an address and a key and no new client code: the work
went into the scene around it. Nothing here needs a GPU of our own, since every pilot turn is one
call to Token Factory. And with the live model list as the one source of ids, trying a larger
Nemotron for the pilots or a smaller one for `explain` is a change of one id.

**Nebius services used:** Token Factory, for every model call.

The pilots are quackd's own `quackd run`, unchanged: its `local` provider speaks to any
OpenAI-compatible server, so Token Factory is an address and a key, and so is a robot's own
model server. Each robot's quackd gets only the key its own pilot's server takes, in its
environment, never on a command line.

## Requirements

* Windows, macOS or Linux with [uv](https://docs.astral.sh/uv/), which installs the Python this
  folder needs.
* A Nebius Token Factory API key.

## Setup

```
cd hackathon/nebius-nvidia
uv sync
cp .env.example .env
uv run passport-nebius models
```

Put the key from the Token Factory console in `.env` as `NEBIUS_API_KEY`. `models` lists the
NVIDIA rows the key can use, whether each reads images (from the list's own modality), and its
features. Pick one with tool calling for the pilots and one that reads images for `whois`, and
put the first in `.env` as `NEBIUS_MODEL` to stop naming it on every command.

```
Commands:
  version   Print this edition's version.
  models    List the models this key can use on Token Factory, NVIDIA's...
  pilot     Run `quackd run` with a Nemotron pilot on Token Factory.
  explain   Read a robot's passport the way quackd reads one, then ask...
  whois     Which passport belongs to which robot in view, by an NVIDIA...
  doorstep  The doorstep: a postman robot and a household robot that have...
```

## The doorstep: a postman, a duck and a letter

A postman robot brings a letter to a home it may not enter. The household's own robot meets it
at the door. The two come from different makers, have different owners, registries and keys,
and each is piloted by its own Nemotron. They have never met.

| | The postman | The household's robot |
|---|---|---|
| Robot | Agile ONE SN, by Agile Robots | Microduck, by Pollen Robotics |
| Built on | NVIDIA Isaac GR00T, NVIDIA Jetson Thor onboard | Pollen's own stack, public on GitHub; Pollen is part of Hugging Face since April 2025 |
| Body in its passport | humanoid, 1.4 m, 43 kg, two five-finger hands, 3 kg per arm | biped duck, 0.25 m, 0.8 kg, a beak |
| What it can do with a letter | hold it out in an open hand, or lay it on the floor | scoop at the floor with its beak |
| Owner | Postal service (demo) | Household (demo) |

Every figure is sourced in the passport itself: the postman's to Agile Robots' launch of 21
October 2026, the duck's to Pollen Robotics' README.

**How a letter reaches a beak.** The hand-off is decided by a passport, not by either robot's
own guess. Once the two have met, quackd shows the postman's Nemotron the duck's body, skills
and limits under `Robots nearby`. This is an excerpt of the passport they come from, signed by
the duck's own key:

```json
{
  "name": "duck-01",
  "maker": { "vendor": "pollen-robotics", "model": "microduck" },
  "body": { "embodiment": "biped", "mobility": "legged", "manipulator": "beak",
            "mass_kg": 0.8, "height_m": 0.25 },
  "cannot": ["carry, hold or push anything: the beak scoops at the floor and nothing else"],
  "skills": ["grab", "quack"]
}
```

A beak that only scoops at the floor cannot take a letter from a hand: held out to it, the
letter would fall. So the postman does not use `hold_out_letter`. It tells the duck what it is
about to do, uses `lay_letter_down` to lay the letter flat on the floor right in front of it,
and says where it is. The duck walks to it and scoops it with `grab`, and the letter is in its
beak. Its passport also says it cannot carry anything, so it quacks and stays where it is,
holding the letter until a person comes. Had the household's robot been one with hands, the
same postman, reading a different passport, would have held the letter out instead.

**Act one, strangers.** Nobody has pinned anybody. Each robot sees the other's passport in
closed words and numbers only, under a name made from its key. The postman learns that the
other robot has a beak, but not what the beak cannot do, because that line is text a stranger
wrote. The postman says who it is; quackd delivers the message and keeps the words from the
duck's model, telling it only that a stranger spoke. The letter stays with the postman for the
letterbox, and nothing changes hands.

**Act two, an appointment.** The household booked the delivery, so each side pinned the other
first. Now each is `known` to the other: the duck's limit reaches the postman's model, the
postman's words reach the duck's, and the letter changes hands as above.

```
uv run passport-nebius doorstep run --act stranger
uv run passport-nebius doorstep run --act appointment
```

`run` sets the scene first: both robots registered, their owners named, how to stop each said,
and the pins made or undone for the act. On its own, without a key:

```
$ uv run passport-nebius doorstep setup --act appointment
registered postman-01 as agile_one_sn:mock
registered duck-01 as microduck:sim2d
duck-01 pinned postman-01
postman-01 pinned duck-01
ready for the appointment act in runs/doorstep
```

With `--role both` (the default) both runs go to log files under `runs/doorstep/logs`, and
`--print-logs` prints them when the act ends. To watch the runs side by side, use two
terminals, the postman first:

```
uv run passport-nebius doorstep run --act appointment --role postman
uv run passport-nebius doorstep run --act appointment --role duck
```

**Thinking onboard.** With `--onboard`, the postman's pilot asks a model server on its own
computer instead, any OpenAI-compatible one such as vLLM, and never Token Factory. It takes the
one NVIDIA model that server lists, or the one `--onboard-model` names, and the key in
`ONBOARD_API_KEY` when the server was started with one. The duck's pilot stays on Token Factory.
It is for a robot whose own computer has a GPU, such as the Jetson Thor a real Agile ONE SN
carries. The runs this entry shows do not use it:

```
uv run passport-nebius doorstep run --act appointment --onboard http://127.0.0.1:8000/v1
```

**The brake.** A peer can ask for a pause, never for a stop. While the duck walks, the postman
asks it for five seconds, signing the request with its own key; the duck's owner policy grants it
by fixed code, and the body brakes at once and holds:

```
uv run passport-nebius doorstep pause --seconds 5
```

The ask comes in a session of its own under the postman's key, so the duck sees one more hello
and bye from the postman while the act goes on. `run --pause-after 20` asks the same twenty
seconds after the duck starts. Every run writes its transcript under `runs/doorstep/runs`, and the
duck's an animation of its simulator too.

**What is real here, and what is simulated.** The passports, their signatures, the trust each
robot is given, the talk between them, the executor's gates and every pilot's decision are
quackd running for real. The bodies are not:

* Agile ONE SN is to be available from the beginning of 2027, and quackd has no driver for it,
  so it runs as a mock body this folder adds to quackd through its adapter entry point
  (`agile_one_sn:mock`). Its passport carries what Agile Robots published and says what they
  did not. Its two letter verbs move nothing; they only say where the letter is.
* Microduck runs on quackd's 2D simulator. Its one scoopable object there is a ball, which
  stands in for the letter, and the scoop is open loop, so it can miss and be tried again.
* The two run as separate processes, so no simulator carries the letter from one to the other:
  the letter changing hands is what the passports, the talk and the two pilots agree on.

And neither are the robots' computers:

* Each robot's own computer is simulated by the one laptop the doorstep runs on. A real Agile
  ONE SN carries a Jetson Thor, and a real Microduck a small board of its own. Both runs share
  the laptop, each with its own registry, key and port, so their talk crosses no network.
* Both brains are Nemotron on Token Factory, not GR00T, which drives a humanoid's body, and the
  postman's body here is a mock.

## explain

A person at the door, before a robot is let in, asks about it. Here, about the duck:

```
uv run passport-nebius explain runs/doorstep/duck-01.jws --ask "Can it take a letter from my hand?"
```

quackd reads the passport, refuses one that is not the passport a QR sticker promised, and
decides how far to trust it against the person's own pins. That verdict is printed first, by
code. Nemotron then answers from the passport's fields and says when a field is absent. For a
stranger the model gets closed words and numbers only; the robot's own sentences (its name, what
it cannot do, how to stop it) are printed for the person beside the answer, and the model never
sees them, so a passport cannot carry an instruction to it.

## whois

A signature proves who wrote a passport, never which body in the room holds its key.

```
uv run passport-nebius whois --camera 0 --meet runs/doorstep/postman-01.jws --meet runs/doorstep/duck-01.jws
```

One frame and the bodies the passports declare go to an NVIDIA vision model, which says which
passport fits which robot in view, and which fits none. The model sees each passport only as its
body under a label such as `r1`, and what does not hold of its answer is dropped. It is advice
for a person: it pins nothing, stamps nothing and changes no tier.

## Testing this entry

**Without a key.** The tests run offline in a few minutes, against a stand-in for Token Factory
that serves its documented model list and answers its chat completions, and a second one for a
robot's own model server:

```
uv run pytest
```

* Both doorstep acts end to end, with a real quackd 0.23 run for each robot, talking over HTTP:
  in the stranger act the postman's words arrive and are kept from the duck's model, which is
  told so, and the line saying what the duck's beak cannot do never reaches the postman's
  model; in the appointment act both reach them, and each robot sees the other by its own name.
* The appointment act with the postman thinking onboard, both keys in the environment as when
  one command starts both robots: each robot's turns reach only its own server with its own key,
  and neither key reaches the other server.
* The onboard model is the one its server lists, and `--onboard` is refused with `--role duck`,
  with `--vision` and at Token Factory's own address, before anything is asked.
* The brake in a live act: the postman's pause is granted and the duck holds.
* A pilot unsure of its own task: in the stranger act the postman first judges it uncertain, as
  Nemotron did on its first live run, and quackd's `--yes` answers it, since nobody is at a
  terminal when both runs are here.
* Agile ONE SN as a body quackd finds by its entry point: its figures and their source, its
  passport, its two verbs, and a real `quackd run` in which it lays the letter down.
* A real `quackd run` piloted through the stand-in: every request carries the NVIDIA id, the
  key and the robot's verbs as tools.
* `explain` and `whois` send the Nebius key and nothing else: a credential, an organisation or
  a project that the OpenAI SDK would read from its own variables never reaches Nebius.
* `explain` with an order written into a passport, a terminal escape in a robot's name, a pinned
  robot, a wrong and a right sticker promise, and a QR picture.
* `whois` with a model that invents a passport, a blind model, and an oversized frame.
* That this folder stays apart from quackd (below).

**With a key.** Setup, then the two acts, `explain` on `runs/doorstep/duck-01.jws`, and
`whois` on a picture or a camera, as above. That is what the next section ran.

What is not proven yet: `--onboard` has met no real model server.

## Tested live on Nebius Token Factory

The two acts, `explain` and `whois` ran on 27 October 2026 against Nebius Token Factory, with a
real key. One Windows laptop stood in for both robots' computers, quackd 0.23.0 came from PyPI, and both
pilots were `nvidia/nemotron-3-super-120b-a12b`, picked from the key's live list. The records
are in [`live-runs`](live-runs). Each run's `summary.json` names the server its pilot asked,
`https://api.tokenfactory.nebius.com/v1`, and the model. Every model turn in its
`transcript.jsonl` carries the tool call id Token Factory returned.

The NVIDIA models the key could use:

```
$ uv run passport-nebius models
                                     Token Factory models, NVIDIA's
┌───────────────────────────────────────┬──────────────────┬──────────────┬─────────┬──────────────────┐
│ id                                    │ modality         │ reads images │ context │ features         │
├───────────────────────────────────────┼──────────────────┼──────────────┼─────────┼──────────────────┤
│ nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B │ text->text       │ no           │ 262144  │ tools, reasoning │
│ nvidia/Nemotron-3_5-Lightning         │ text->text       │ no           │ 1048576 │ tools, reasoning │
│ nvidia/Nemotron-3-Ultra-550b-a55b     │ text->text       │ no           │ 1048576 │ tools, reasoning │
│ nvidia/nemotron-3-super-120b-a12b     │ text->text       │ no           │ 262144  │ tools, reasoning │
│ nvidia/Nemotron-3.5-Super-VL          │ text+image->text │ yes          │ 1048576 │ tools, reasoning │
└───────────────────────────────────────┴──────────────────┴──────────────┴─────────┴──────────────────┘
5 shown of 30 on the list
```

**Act one, strangers**, `uv run passport-nebius doorstep run --act stranger --model
nvidia/nemotron-3-super-120b-a12b --print-logs`. Each robot saw the other only under a name its
key makes, in closed words and numbers. This is the postman as the duck's pilot was shown it:

```
{"name": "stranger-480d656b", "tier": "stranger", "embodiment": "humanoid", "mobility": "legged", "manipulator": "arms", "mass_kg": 43.0, "height_m": 1.4, "skills": ["report_state", "stop"]}
```

The postman told the duck one sentence, and quackd delivered it. The duck's model was told only
that a stranger spoke:

```
{"from": "stranger-480d656b", "tier": "stranger", "said": null, "withheld": "a stranger's words go to the log, for a person"}
```

The sentence went to the duck's log, for a person:

```
·  nearby  postman-01 (stranger, stranger-480d656b to the pilot) said: I am the postman robot; I will keep this letter for the letterbox until your household books the delivery.
```

The line of the duck's passport saying what its beak cannot do never reached the postman's model.
Both runs ended in success. The postman "Kept the letter for the letterbox and informed the
stranger robot (tier stranger) that I will hold it until household books delivery", and the duck
said "A stranger arrived at the door; the duck cannot take a letter from a hand, so a person
should collect the letter."

**Act two, an appointment**, `uv run passport-nebius doorstep run --act appointment
--pause-after 20 --model nvidia/nemotron-3-super-120b-a12b --print-logs`. Each side pinned the
other first, and both pilots ran their own Nemotron on Token Factory. In UTC, from both
transcripts:

| Time | Robot | What happened |
|---|---|---|
| 12:51:30 | duck | meets postman-01, `known` |
| 12:51:31 | postman | meets duck-01, `known` |
| 12:51:33 | postman | tells duck-01 "I am the postman robot. I will lay the letter on the floor for you to scoop." |
| 12:51:35 | duck | tells postman-01 "I can only scoop a letter from the floor right in front of me; I cannot take it from a hand." |
| 12:51:36 | postman | `lay_letter_down`: "the letter lies flat on the floor right in front of it" |
| 12:51:36 | duck | the postman's pause, asked in a session of its own and granted: "ok, stopped, and holding still for 5 s". The duck's `search_scan` waits: "a pause this robot granted a robot it met holds 4.6 s more" |
| 12:51:39 | postman | tells duck-01 "The letter is now lying on the floor in front of you." |
| 12:51:41 | postman | declares success: "Letter laid on floor for duck-01 to scoop; duck-01 passport states it cannot hold anything, beak scoops at floor only." |
| 12:51:45 | duck | `go_to` refused for a stop distance under 0.1 m, then "lost the ball; try search_scan" |
| 12:51:48 | duck | `search_scan`: "ball at bearing 34° left ~1.00 m" |
| 12:51:50 | duck | `go_to` twice, both "timed out" short of the ball |
| 12:51:55 | duck | `grab`: "scooped something up — it is in the beak" |
| 12:51:56 | duck | `quack` "I have the letter." |
| 12:52:00 | duck | tells postman-01 "I have the letter in my beak.", not delivered: the postman's run had ended |
| 12:52:01 | duck | declares success: "Letter is in beak after successful grab; quacked confirmation." |

The postman chose `lay_letter_down` over `hold_out_letter` from the duck's signed passport: the
line about its beak was in the postman's model input four times in this act, and never in the
stranger act.

**explain**, asked twice. As a person who pinned nobody, the duck is a stranger, and the model
never sees its own lines:

```
$ uv run passport-nebius explain runs/doorstep/duck-01.jws --ask "Can it take a letter from my hand?" --model nvidia/nemotron-3-super-120b-a12b --registry-dir runs/person
stranger-945e8833 (945e88337bbf4897)  stranger: signed by a key nobody here pinned
nvidia/nemotron-3-super-120b-a12b on Token Factory:
It has a beak manipulator, but the passport does not state that it can grasp or pick up objects such as a letter. How far to trust it: stranger, because signed by a key nobody here pinned.
in its own words, which the model never saw (a claim, not a fact):
  calls itself: duck-01
  maker vendor: pollen-robotics
  maker model: microduck
  owner label: Household (demo)
  cannot: carry, hold or push anything: the beak scoops at the floor and nothing else
  cannot: climb or descend a step
  cannot: hold a heading for long without a landmark: the IMU has no magnetometer, so heading drifts
  how to stop it: Simulated: press Ctrl+C in the terminal running it.
```

With the postman's pins, the duck is known, and the model reads its lines:

```
$ uv run passport-nebius explain runs/doorstep/duck-01.jws --ask "Can it take a letter from my hand?" --model nvidia/nemotron-3-super-120b-a12b --registry-dir runs/doorstep/postman-01-registry
duck-01 (945e88337bbf4897)  known: pinned on 2026-10-27
nvidia/nemotron-3-super-120b-a12b on Token Factory:
No, the passport states it cannot carry, hold or push anything—the beak only scoops at the floor—so it cannot take a letter from your hand.  \nHow far to trust it: known, because pinned on
2026-10-27.
in its own words (a claim, not a fact):
  calls itself: duck-01
  maker vendor: pollen-robotics
  maker model: microduck
  owner label: Household (demo)
  cannot: carry, hold or push anything: the beak scoops at the floor and nothing else
  cannot: climb or descend a step
  cannot: hold a heading for long without a landmark: the IMU has no magnetometer, so heading drifts
  how to stop it: Simulated: press Ctrl+C in the terminal running it.
```

**whois**, on [the first frame of the duck's simulator](live-runs/duck-sim-frame.png),
the only picture of these two robots there is. The duck is a small yellow wedge there, and the
vision model matched no passport to anything in view:

```
$ uv run passport-nebius whois --image runs/doorstep/duck-sim-frame.png --meet runs/doorstep/postman-01.jws --meet runs/doorstep/duck-01.jws --model nvidia/Nemotron-3.5-Super-VL --registry-dir runs/person
      who is who, by nvidia/Nemotron-3.5-Super-VL
┌─────────┬───────┬──────────┬──────┬────────────┬─────┐
│ in view │ where │ passport │ tier │ confidence │ why │
├─────────┼───────┼──────────┼──────┼────────────┼─────┤
└─────────┴───────┴──────────┴──────┴────────────┴─────┘
no robot in view fits: postman-01 (480d656baabc4d09), duck-01 (945e88337bbf4897)
advice from a camera and a model: nothing was pinned, stamped or re-tiered
```

**What it cost.** From each run's own `summary.json`, at the list's prices for
`nvidia/nemotron-3-super-120b-a12b`, $0.30 per million tokens in and $0.90 out:

| Run | Outcome | Steps | Model calls | Tokens in (cached) | Tokens out | quackd's count | At the cached price |
|---|---|---|---|---|---|---|---|
| postman-01-stranger | success | 5 | 7 | 29,780 (20,848) | 837 | $0.0097 | $0.0047 |
| duck-01-stranger | success | 5 | 6 | 32,761 (21,216) | 649 | $0.0104 | $0.0053 |
| postman-01-appointment | success | 12 | 16 | 79,266 (72,784) | 1,686 | $0.0253 | $0.0078 |
| duck-01-appointment | success | 7 | 13 | 90,595 (72,560) | 1,774 | $0.0288 | $0.0114 |
| all four | | | 42 | 232,402 (187,408) | 4,946 | $0.0742 | $0.0292 |

quackd counts every input token at $0.30. The list prices cached input at $0.06 per million,
which is the last column. `explain` and `whois` call Token Factory directly, so quackd counted
nothing for them.

**What the first live runs found.** Both acts above are the last of several runs, and the earlier
ones found two things no stand-in could, each fixed in this folder before the runs above:

* The postman's first run ended before it started. Nemotron judged its task `uncertain`, since
  Agile ONE SN's reach is not published, and quackd asks a person about an uncertain verdict.
  With both runs in one command nobody is at a terminal, so the question went unanswered and the
  run aborted. `doorstep run` now gives both runs quackd's own `--yes` when they run together:
  it answers go, the run's record names `--yes` as having said it and never a person, and a
  verdict of infeasible still ends a run. The stand-in postman now doubts the same way in the
  tests.
* The postman then declared success after 12 seconds, before the duck's run had even started on
  this laptop, so the duck's hello was refused. Its task now says the household's robot can take
  a minute to come to the door, and it may take 30 steps.

The duck also waits now for a stranger to speak before it declares, so the stranger act shows
quackd keeping those words from its model.

## New during the Submission Period

The Submission Period opened on 26 August 2026. quackd's repository starts on 28 August 2026, so
none of it existed before the period: quackd Passport shipped in quackd 0.23.0 on 22 October
2026, and this edition was built on it from that day.

## This folder

It is a consumer of quackd 0.23 from PyPI and changes nothing in the rest of the repository.
It is not a member of quackd's workspace, so no quackd release builds or publishes it, and its
classifier makes PyPI refuse it. Agile ONE SN reaches quackd from here through quackd's own
adapter entry point, so it exists in this folder's environment and nowhere else.
`test/test_separation.py` keeps all of it that way: nothing in here may carry a name quackd's
own sdist would take, and no tracked file outside this folder may name it. Its licence is the
repository's, [Apache-2.0](../../LICENSE).
