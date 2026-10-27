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

quackd Passport is a small signed document a robot serves about itself: name, maker, owner, its
body with sourced numbers (mass, height, payload), its skills, what it cannot do and how a
person stops it. quackd, my open robot runtime, shipped it in 0.23. This entry runs quackd
onboard each robot, gives both a Nemotron brain on Nebius Token Factory, and plays it out at a
front door.

A postman brings a letter to a home: Agile ONE SN, the humanoid Agile Robots built on NVIDIA
Isaac GR00T. The household's robot is Microduck, Pollen Robotics' 25 cm duck. As strangers,
each sees the other's passport in closed words and numbers only: the postman speaks, quackd
delivers the message but keeps it from the duck's model, and the letter stays for the
letterbox. Once the household has booked the delivery, the two are pinned to each other and
their words flow. The postman's Nemotron reads in the duck's passport that its beak cannot hold
anything and only scoops at the floor, so instead of holding the letter out it lays it at the
duck's feet, and the duck scoops it into its beak. The postman can also ask the duck for a
pause, which brakes it at once; no robot can make another move.

Two commands are for people. `explain` reads a robot's passport from its QR sticker and lets
Nemotron answer a question about it, with how far to trust the robot decided by code first.
`whois` asks an NVIDIA vision model which passport belongs to which robot in a camera's view.

## How I built it

* quackd runs onboard each robot. A robot's own run holds its key, serves its passport, decides
  by fixed code how far to trust the other and whether to grant it a pause, and flies its pilot.
  Here one laptop stands in for both robots' computers.
* Every model call goes to Token Factory. The robots' pilots are quackd's own runs, with its
  OpenAI-compatible provider pointed at Token Factory, so a Nemotron pilot needed no new client
  code. Model ids come only from the key's live model list.
* Agile ONE SN ships in 2027 and quackd has no driver for it, so it joins quackd as a mock body
  through quackd's own adapter entry point, its passport carrying the figures Agile Robots
  published. Microduck runs on quackd's simulator, where its ball stands in for the letter.
* The tests replay both acts with a real quackd run per robot, against stand-ins for Token
  Factory and for a robot's own model server. They check what reaches each model, the postman's
  words and the duck's limit, and that each robot's key reaches only its own server.

## Challenges

Keeping a stranger's text out of every model call. A robot's name is text the stranger wrote,
so a stranger is shown to a model by a name its key makes, and its own sentences go to the
person instead. That is also why, as strangers, the postman never learns what the duck's beak
cannot do: the line saying so is the duck's own words. While testing I also found that the
OpenAI SDK reads gateway credentials from its own environment variables and would send them to
Token Factory; the client is now built so only the Nebius key goes out.

## What's next

A real robot at the door: quackd already has adapters for the SO-101 arm and other bodies, and
the same commands take a real one by its name. The postman's brain on a real Jetson Thor. Then
identify by motion, so a robot can show which body holds its passport.

## New during the Submission Period

quackd's repository starts on 28 August 2026, after the period opened on 26 August.
quackd Passport shipped on 22 October 2026, and this entry was built on it from that day.
