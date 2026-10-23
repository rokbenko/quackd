# Draft: the Devpost project description

Track: **Physical AI**. Repository: `github.com/rokbenko/quackd`, folder
`hackathon/nebius-nvidia` (start from its README).

## Inspiration

Robots from different makers are about to share front doors, hallways and warehouse floors.
When two of them meet, neither can say who it is, what its body can do, who owns it or how to
stop it. And once robots run on language models, a robot that talks to yours can talk to your
robot's model. I wanted a way for robots that never met to introduce themselves, check each
other, and work together, without one robot's words ever steering another's brain.

## What it does

Robot Passport is a small signed document a robot serves about itself: name, maker, owner, its
body with sourced numbers (mass, payload, reach), its skills, what it cannot do and how a person
stops it. quackd, my open robot runtime, shipped it in 0.23. This entry gives every robot a
Nemotron brain on Nebius Token Factory and plays it out at a front door.

A courier robot and the home's own robot meet. As strangers, each sees the other's passport in
closed words and numbers only; the courier speaks, and quackd delivers the message but keeps it
from the home robot's model. After a delivery booking pins the two robots to each other, their
words flow, and the two Nemotron pilots agree who carries which parcel by the payloads in their
passports: the 0.8 kg book goes inside, the 2.5 kg box of tiles stays for a person. The courier
can ask the home robot for a pause, which brakes it at once; no robot can make another move.

Two commands are for people. `explain` reads a robot's passport from its QR sticker and lets
Nemotron answer a question about it, with how far to trust the robot decided by code first.
`whois` asks an NVIDIA vision model which passport belongs to which robot in a camera's view.

## How I built it

* Every model call goes to Token Factory. The robots' pilots are quackd's own runs, with its
  OpenAI-compatible provider pointed at Token Factory, so a Nemotron pilot needed no new client
  code. Model ids come only from the key's live model list.
* Both acts run on Nebius AI Cloud as a Serverless AI Job on a CPU preset, with the key in
  MysteryBox.
* The robots are AlohaMini (the courier) and ToddlerBot (the home robot) on quackd's simulator,
  each with its own owner, keys and registry.
* The tests replay both acts with real quackd runs against a stand-in for Token Factory, and
  check that a stranger's words never reach a model, in the doorstep and in `explain`.

## Challenges

Keeping a stranger's text out of every model call. A robot's name is text the stranger wrote,
so a stranger is shown to a model by a name its key makes, and its own sentences go to the
person instead. While testing I also found that the OpenAI SDK reads gateway credentials from
its own environment variables and would send them to Token Factory; the client is now built so
only the Nebius key goes out.

## What's next

A real robot at the door: quackd already has adapters for the SO-101 arm and other bodies, and
the same commands take a real one by its name. Then identify by motion, so a robot can show which
body holds its passport.

## New during the Submission Period

quackd's repository starts on 28 August 2026, after the period opened on 26 August. Robot
Passport shipped on 22 October 2026, and this entry was built on it from that day.
