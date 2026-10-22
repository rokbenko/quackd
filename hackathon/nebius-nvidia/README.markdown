# Robot Passport on NVIDIA Nemotron and Nebius

quackd's entry to the **Nebius x NVIDIA Global AI Hackathon 2026**, Physical AI track.

Robots from different makers are starting to share hallways, front doors and warehouse floors,
and none of them can say who it is to a robot it has never met. **Robot Passport**, which quackd
0.23 shipped, is the open answer: a small signed document a robot serves about itself. Its
name, maker and owner, its body with sourced numbers (mass, height, payload, reach), its skills,
what it cannot do, how a person stops it, and where to talk to it. Ed25519 signatures, discovery
over HTTP, DNS-SD, MQTT and QR stickers, published test vectors, and a talk protocol in which a
robot may ask another for a pause or to identify itself.

This edition gives every robot an NVIDIA brain on Nebius. Nemotron on Nebius Token Factory pilots
the robots, explains a passport to the person standing next to the robot, and tells which
passport belongs to which robot in a camera's view.

The property the demo is built around: **a stranger's words never reach your robot's model.**
Two robots that never met see each other's passport in closed words and numbers only. Words flow
once a person has pinned the other robot, the way a delivery booking would. A stranger may ask
for a pause, which fixed code grants within limits and which ends on its own; no robot can order
another to move, or to stop for good.

> [!NOTE]
> Everything here is tested offline against a stand-in for Token Factory and against quackd
> 0.23 itself, with real runs on its simulator. The commands that need a Nebius key are shown
> as commands; their output is added here from real runs before submission.

## How NVIDIA and Nebius are used

| Part | NVIDIA model | Nebius |
|---|---|---|
| The robots' pilots | a Nemotron model with tool calling | Token Factory chat completions, one per turn |
| `explain` | a Nemotron model | Token Factory, answering from passport fields |
| `whois` | an NVIDIA vision model (a Nemotron VL or Omni row) | Token Factory, an image and a JSON schema |
| The doorstep on a server | the same pilots | both robots on one Nebius AI Cloud CPU VM |

No model id is written down anywhere. Every command reads the key's live `GET /v1/models` list
and takes an NVIDIA id only when that list carries it, spelled as the list spells it.

The pilots are quackd's own `quackd run`, unchanged: its `local` provider speaks to any
OpenAI-compatible server, so Token Factory is an address and a key. The key goes to quackd in
its environment, never on a command line.

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
  doorstep  The doorstep: a courier and a home robot that have never met...
```

## The doorstep

A courier robot and the home's own robot meet at a front door. They come from different makers
and have different owners, registries and keys, and each has its own Nemotron pilot.

| | Courier | Home robot |
|---|---|---|
| Body | AlohaMini, two arms on a mobile base | ToddlerBot, a 56 cm humanoid |
| Owner | Courier company (demo) | Household (demo) |
| Payload in its passport | 1.0 kg per arm, from the AlohaMini2 README | 1.484 kg, from arXiv:2502.00893 |
| Task | deliver two parcels, never enter the home | take a parcel inside only if it fits |

The parcels are a 0.8 kg book and a 2.5 kg box of tiles. The passports decide what happens: the
book fits the home robot, the tiles fit nobody and stay for a person.

**Act one, strangers.** Nobody has pinned anybody. Each robot sees the other's passport in
closed words and numbers, under a name made from its key. The courier speaks, quackd delivers
the message, and keeps the words from the home robot's model, telling it only that a stranger
spoke. No handoff happens.

**Act two, an appointment.** Each side has pinned the other first, as a booking would record
both robots. Now each is `known` to the other, the courier's words reach the home robot's model,
and the two pilots agree who carries what, citing the payloads in their passports.

```
uv run passport-nebius doorstep run --act stranger
uv run passport-nebius doorstep run --act appointment
```

`run` sets the scene first: both robots registered, their owners named, how to stop each said,
and the pins made or undone for the act. On its own, without a key:

```
$ uv run passport-nebius doorstep setup --act appointment
registered courier-01 as alohamini:sim2d
registered home-01 as toddlerbot:sim2d
home-01 pinned courier-01
courier-01 pinned home-01
ready for the appointment act in runs/doorstep
```

With `--role both` (the default) both runs go to log files under `runs/doorstep/logs`. To watch
them side by side, use two terminals, the courier first:

```
uv run passport-nebius doorstep run --act appointment --role courier
uv run passport-nebius doorstep run --act appointment --role home
```

**The brake.** A peer can ask for a pause, never for a stop. While the home robot walks, the
courier asks it for five seconds, signing the request with its own key; the home robot's owner
policy grants it by fixed code, and the body brakes at once and holds:

```
uv run passport-nebius doorstep pause --seconds 5
```

`run --pause-after 20` does the same twenty seconds after the home robot starts. Every run writes its
transcript and an animation of the simulator under `runs/doorstep/runs`.

## explain

A person at the door scans the courier's QR sticker and asks about it:

```
uv run passport-nebius explain sticker.png --ask "Can it carry my 3 kg box, and how do I stop it?"
```

quackd reads the passport, refuses one that is not the passport the sticker promised, and
decides how far to trust it against the person's own pins. That verdict is printed first, by
code. Nemotron then answers from the passport's fields and says when a field is absent. For a
stranger the model gets closed words and numbers only; the robot's own sentences (its name, what
it cannot do, how to stop it) are printed for the person beside the answer, and the model never
sees them, so a passport cannot carry an instruction to it.

## whois

A signature proves who wrote a passport, never which body in the room holds its key.

```
uv run passport-nebius whois --camera 0 --meet runs/doorstep/courier-01.jws --meet runs/doorstep/home-01.jws
```

One frame and the bodies the passports declare go to an NVIDIA vision model, which says which
passport fits which robot in view, and which fits none. The model sees each passport only as its
body under a label such as `r1`, and what does not hold of its answer is dropped. It is advice
for a person: it pins nothing, stamps nothing and changes no tier.

## On Nebius AI Cloud

The doorstep needs no GPU: the robots run on quackd's 2D simulator and their pilots on Token
Factory. On a fresh Ubuntu VM:

```
curl -LsSfO https://raw.githubusercontent.com/rokbenko/quackd/main/hackathon/nebius-nvidia/deploy/bootstrap.sh
bash bootstrap.sh
```

It installs uv, checks quackd out and syncs this folder, and writes no key. Both robots talk over
the VM's own address, so no port needs to be open to the internet.

## What is proven, and how

The tests run offline in a few minutes, with no key, against a stand-in for Token Factory that
serves its documented model list and answers its chat completions:

```
uv run pytest
```

* Both doorstep acts end to end, with two real quackd 0.23 runs talking over HTTP: in the
  stranger act the courier's words arrive and are kept from the home robot's model, which is
  told so; in the appointment act they reach it, and each robot sees the other by its own name.
* A real `quackd run` piloted through the stand-in: every request carries the NVIDIA id, the
  key and the robot's verbs as tools.
* `explain` with an order written into a passport, a terminal escape in a robot's name, a pinned
  robot, a wrong and a right sticker promise, and a QR picture.
* `whois` with a model that invents a passport, a blind model, and an oversized frame.
* That this folder stays apart from quackd (below).

What is not proven yet: none of it has met Token Factory itself. That run, and its transcripts,
are added here before submission.

## Built during the hackathon

The submission period opened on 26 August 2026. quackd's first commit is from 28 August 2026, so
the whole project was written inside it; Robot Passport shipped in quackd 0.23.0 on 22 October,
and this edition followed.

## This folder

It is a consumer of quackd 0.23 from PyPI and changes nothing in the rest of the repository.
It is not a member of quackd's workspace, so no quackd release builds or publishes it, and its
classifier makes PyPI refuse it. `test/test_separation.py` keeps it that way: nothing in here
may carry a name quackd's own sdist would take, and no tracked file outside this folder may name
it. Its licence is the repository's, Apache-2.0.
