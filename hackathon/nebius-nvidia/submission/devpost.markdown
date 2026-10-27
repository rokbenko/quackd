# The Devpost submission

One section per field of the form, in the form's order. Each section is pasted as it is.

## Project name

quackd Passport

## Elevator pitch

Robots from different makers meet at your door, check each other's signed passport, and agree a
handoff, and a stranger's words never reach your robot's brain.

## About the project

### Inspiration

Robots from different makers are about to share front doors, hallways and warehouse floors.
When two of them meet, neither can say who it is, what its body can do, who owns it or how to
stop it. And once robots run on language models, a robot that talks to yours can talk to your
robot's model. I wanted a way for robots that never met to introduce themselves, check each
other, and work together, without one robot's words ever steering another's brain.

### What it does

quackd Passport is a small signed document a robot serves about itself: name, maker, owner, its
body with sourced numbers (mass, height, payload), its skills, what it cannot do and how a person
stops it. quackd, my open robot runtime, shipped it in 0.23. This entry runs quackd onboard each
robot, gives both robots a Nemotron brain on Nebius Token Factory, and plays it out at a front
door.

A postman brings a letter to a home: Agile ONE SN, the humanoid Agile Robots built on NVIDIA
Isaac GR00T. The household's robot is Microduck, Pollen Robotics' 25 cm duck. As strangers, each
sees the other's passport in closed words and numbers only. The postman speaks, quackd delivers
the message but keeps it from the duck's model, and the letter stays for the letterbox. Once the
household has booked the delivery, the two are pinned to each other and their words flow. The
postman's Nemotron reads in the duck's passport that its beak cannot hold anything and only
scoops at the floor, so instead of holding the letter out it lays it at the duck's feet, and the
duck scoops it into its beak. The postman can also ask the duck for a pause, which brakes it at
once. No robot can make another move.

Two commands are for people. `explain` reads a robot's passport and lets Nemotron answer a
question about it, with how far to trust the robot decided by code first. `whois` asks an NVIDIA
vision model which passport belongs to which robot in a picture.

### How I built it

* quackd runs onboard each robot. A robot's own run holds its key, serves its passport, decides
  by fixed code how far to trust the other and whether to grant it a pause, and flies its pilot.
  Here one laptop stands in for both robots' computers.
* Every model call goes to Token Factory. The robots' pilots are quackd's own runs, with its
  OpenAI-compatible provider pointed at Token Factory, so a Nemotron pilot needed no new client
  code. Model ids come only from the key's live model list.
* Agile ONE SN ships in 2027 and quackd has no driver for it, so it joins quackd as a mock body
  through quackd's own adapter entry point, its passport carrying the figures Agile Robots
  published. Microduck runs on quackd's simulator, where its ball stands in for the letter.
* The tests replay both acts with a real quackd run per robot against a stand-in for Token
  Factory, and check what reaches each model, and that each robot's key reaches only its own
  server.
* Then I ran it all live on Token Factory on 27 October: both acts with Nemotron 3 Super piloting
  both robots, `explain`, and `whois` with Nemotron 3.5 Super VL. The README quotes what each
  command printed, and the folder keeps every run's record.

### Challenges

Keeping a stranger's text out of every model call. A robot's name is text the stranger wrote, so
a stranger is shown to a model by a name its key makes, and its own sentences go to the person
instead. That is also why, as strangers, the postman never learns what the duck's beak cannot do:
the line saying so is the duck's own words. While testing I also found that the OpenAI SDK reads
gateway credentials from its own environment variables and would send them to Token Factory, so
the client is now built to send the Nebius key and nothing else.

The live runs found what no stand-in could. On the first one the postman never started: Nemotron
judged its task uncertain, because Agile ONE SN's reach is not published, and quackd asks a
person about that, but nobody sat at the terminal. Now the doorstep answers with quackd's own
`--yes`, which the record names as itself and never as a person, and an infeasible verdict still
stops a robot. On the next run the postman gave up after 12 seconds, before the duck had even
started, so its task now tells it the household's robot can take a minute.

### What I learned

A stand-in pilot proves the plumbing and never the behaviour. Mine always said feasible and
waited forever, and on its first live runs Nemotron did neither.

A safety question needs someone to answer it. A robot nobody watches needs a standing answer,
and the record has to name that answer as what it is, never as a person.

Caching pays for an agent. Every turn sends the same long prompt with a little more on the end,
and 81% of the input tokens in the two acts came from Token Factory's cache. At the list's prices
for Nemotron 3 Super, $0.30 per million tokens in, $0.06 cached and $0.90 out, the two acts cost

$$\frac{0.30\,(232402 - 187408) + 0.06 \cdot 187408 + 0.90 \cdot 4946}{10^{6}} \approx 0.029 \text{ USD}$$

### What's next

A real robot at the door: quackd already has adapters for the SO-101 arm and other bodies, and the
same commands take a real one by its name. The postman's brain on a real Jetson Thor, where
`--onboard` already points a pilot at a model on the robot's own computer. Then identify by
motion, so a robot can show which body holds its passport.

## Built with

python, quackd, nvidia-nemotron, nemotron-3-super, nemotron-3.5-super-vl, nebius,
nebius-token-factory, openai-api, tool-calling, json-schema, ed25519, jws, http, zeroconf,
typer, rich, pillow, opencv, pytest, uv, robotics, multi-robot, prompt-injection, microduck,
agile-one-sn

## "Try it out" links

* https://github.com/rokbenko/quackd/tree/main/hackathon/nebius-nvidia (the entry: start from
  its README)
* https://github.com/rokbenko/quackd/tree/main/hackathon/nebius-nvidia/submission/live-run (the
  records of the live run on Token Factory)
* https://pypi.org/project/quackd/ (quackd, the runtime the entry runs on)

## Demo video URL

The YouTube link, once uploaded.

With no hardware, the rules ask for one continuous minute of the key application modules in
action, so a screen recording of terminals is what the video is. To record it on the laptop:

**Before recording**, in the main checkout, which needs the merged folder first:

```
cd D:\Development\quackd
git pull
cd hackathon\nebius-nvidia
uv sync
```

Then add two lines to `.env`, beside the key, so no command needs `--model` and every run records
its cost:

```
NEBIUS_MODEL=nvidia/nemotron-3-super-120b-a12b
QUACKD_PRICE=in=0.3,out=0.9
```

Two terminals side by side, both in `hackathon\nebius-nvidia`. Each act's postman command first
sets the scene, which takes about a minute on this laptop, so start recording once its run
begins.

**0:00 to 0:15**, one sentence on screen: a postman robot and a household robot from different
makers meet at a door, and neither can say who it is.

**0:15 to 0:30**, in one terminal:

```
uv run passport-nebius models
```

**0:30 to 0:45, the stranger act.** Left terminal first, then the right one once the postman's
run has started:

```
uv run passport-nebius doorstep run --act stranger --role postman
uv run passport-nebius doorstep run --act stranger --role duck
```

Point at the duck's line that ends `"withheld": "a stranger's words go to the log, for a person"`,
and the postman keeping the letter.

**0:45 to 2:00, the unbroken minute: the appointment act**, the same way:

```
uv run passport-nebius doorstep run --act appointment --role postman
uv run passport-nebius doorstep run --act appointment --role duck --pause-after 20
```

Point at the duck's line in the postman's view ("cannot: carry, hold or push anything: the beak
scoops at the floor and nothing else"), the postman choosing `lay_letter_down`, the pause granted
and the duck holding still, and the duck's `grab`. If a pilot asks "Go ahead anyway?", answer `y`
on camera: that is quackd asking a person, which is the point.

**2:00 to 2:20, `explain`**, straight after the appointment act, while the robots are still pinned
to each other, first as a person who pinned nobody, then with the postman's pins:

```
uv run passport-nebius explain runs/doorstep/duck-01.jws --ask "Can it take a letter from my hand?" --registry-dir runs/person
uv run passport-nebius explain runs/doorstep/duck-01.jws --ask "Can it take a letter from my hand?" --registry-dir runs/doorstep/postman-01-registry
```

**2:20 to 2:35**, the duck's animation from the appointment act, `run.gif` in the newest
`runs\doorstep\runs\...-duck-01-appointment` folder.

**2:35 to 2:45**, the README's section "Tested live on Nebius Token Factory" and the folder.

Say on screen that one laptop stands in for both robots' computers, that Agile ONE SN is a mock
until it ships, and that the duck's ball stands for the letter. No logos and no music. A real
model differs from run to run, so take a second recording if an act goes astray, and if a part
is sped up, say so on screen.

## Public code repository

https://github.com/rokbenko/quackd/tree/main/hackathon/nebius-nvidia

If the form needs the repository itself: https://github.com/rokbenko/quackd, and the entry is the
folder `hackathon/nebius-nvidia`. Where each requirement is met:

* **OSI license:** Apache-2.0, `LICENSE` at the repository's root, linked from the folder's
  README.
* **README with setup instructions:** the folder's `README.markdown`, sections "Requirements" and
  "Setup".
* **How NVIDIA Nemotron is used:** section "How NVIDIA and Nebius are used".
* **Where Token Factory accelerated the workflow:** section "Where Token Factory sped the work
  up".
* **Other Nebius tools or services:** none. Token Factory takes every model call, as "Nebius
  services used" says.
* **The live run:** section "Tested live on Nebius Token Factory".

## Which model(s) did you use, and why did you choose that size/variant?

`nvidia/nemotron-3-super-120b-a12b` pilots both robots and answers `explain`. quackd gives a
pilot a long system prompt and a list of tools, about 4,000 to 7,000 tokens of input a turn, and
asks it for a safety judgement before anything moves: whether this body can do the task at all.
I wanted that judgement from the larger model, and Super is the middle of the line on my key:
Ultra costs more than three times as much per token, and Nano and Lightning cost a fifth of
Super's input price but carry fewer parameters for the calls that matter. With 12B parameters
active per token it answered in 1.7 seconds a turn on average.

`nvidia/Nemotron-3.5-Super-VL` does `whois`, because it is the one NVIDIA model on my key's list
that reads images.

I did not try Nano or Lightning on the acts. Every model id is read from the live list, so trying
one is a change of one id.

## How would you rate Nemotron's output quality for your use case? (1 = Poor, 10 = Excellent)

8.

What worked: all 42 turns of the two live acts were native tool calls, and not one needed the
fallback quackd keeps for models that write a call as text. Once the task files said what they
meant, it followed them, and it read the duck's signed passport line and laid the letter down
instead of holding it out. On its first run it called the postman's task uncertain, because
Agile ONE SN's reach is not published, which is the answer quackd wants.

What fell short: that caution did not hold. On later runs it called the same task feasible, once
saying outright that it assumed the reach. Then patience: the postman declared success after 12
seconds with no robot in sight, reading "no robot yet" as the stranger case, until its task told
it to wait. Once it set a `go_to` parameter below the tool's minimum (0.05 where 0.1 is the
least), which quackd refused and it corrected on the next turn. One `explain` answer came back
with a literal `\n` inside it. And on a top-down frame of the duck's 2D simulator the vision
model found no robot it could match. I did not test multilingual use.

## Did you fine-tune, prompt-engineer, or use Nemotron out of the box? What was your approach?

Out of the box, through Token Factory, with no fine-tuning and no sampling settings: quackd sends
no temperature, so the defaults stand, and the reasoning the models list is left on. The prompt is
quackd's own system prompt, unchanged, plus one task file per robot, the `.duck` files in
`scenarios/`. The prompt engineering was in those task files, and the live runs changed two
things: the postman's task now says the household's robot can take a minute to come to the door
and never to declare before one appears, and the duck's task waits for a stranger to speak before
it declares, so the act shows quackd keeping those words from its model.

## How did Nemotron's performance compare to other models you've used for similar tasks?

I have not run these two acts with another model, so I cannot put numbers side by side. The
closest thing I have is quackd's own bench, which once saw Qwen3-32B judge a 45 minute patrol
feasible six times out of six on a body whose endurance nobody published. Nemotron 3 Super at
least once called a task uncertain over an unpublished figure, the reach, though not every time.

Against the stand-in my tests use, the difference was the point of running it live: the stand-in
always says feasible and waits forever, and Nemotron doubted, then gave up waiting, and both
changed the entry. Its tool calls were clean, every turn a real call, and at 1.7 seconds a turn
it kept up with two robots talking.

## Which Nebius platform capabilities were most valuable to your project and how?

Token Factory, in four ways:

* **An OpenAI-compatible API.** quackd's existing `local` provider pointed at
  `https://api.tokenfactory.nebius.com/v1` was the whole integration: no new client code for the
  pilots, and the OpenAI SDK for `explain` and `whois`.
* **The live model list.** `GET /v1/models?verbose=true` gives each row's modality, features,
  context and prices, so the code takes a model id only from the list, refuses one it does not
  carry, and knows which row reads images.
* **Prompt caching.** 187,408 of the 232,402 input tokens in the two acts came from the cache,
  which took them from $0.0742 at the full input price to $0.0292.
* **Tool calling and vision on the NVIDIA rows**, behind one key.

There was no GPU instance and no deployment to run: serverless inference through the API, both
robots' pilots calling at once, with no rate limit hit. I also built a version that served
Nemotron with vLLM on one H100 in a Serverless AI Job, standing in for the robot's Jetson Thor,
and dropped it to keep the entry simple, since Token Factory alone covered every model call.

## How likely are you to recommend running Nemotron on Nebius to other developers? (1 = Unlikely, 10 = Highly Likely)

9.

An existing OpenAI client was the whole setup, the live list made a wrong model id impossible to
use by accident, and caching makes multi turn agents cheap: both robots' runs in both acts cost
under ten cents. What kept it from 10 is the list itself: its prices carry no unit, so I had to
work out that `0.0000003` means dollars per token, and its ids follow three conventions side by
side (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`, `nvidia/Nemotron-3_5-Lightning`,
`nvidia/nemotron-3-super-120b-a12b`).

## How would you rate your experience running Nemotron inference on Nebius compared to previous cloud or local development environments? (1 = Poor, 10 = Excellent)

9.

Locally this was never an option: my laptop has no NVIDIA GPU, so a 120B model is out of the
question and even a 4B model on its CPU would be slow. Token Factory answered in 1.7 seconds a
turn on average, and setting it up was a base URL and a key, the same as any OpenAI compatible
service I have used. It loses a point to the docs: when I read them, their examples used Llama
3.1 and Qwen2 VL rather than Nemotron, and two pages gave two base URLs without saying which to
use.

## What additional features or improvements would have made the Nemotron on Nebius experience more effective for your project?

* A documented unit for the model list's prices, and the cost of each request in its response.
* One spelling convention for model ids, since the API matches them exactly.
* Nemotron examples in the docs for function calling, vision and structured output.
* A line in the quickstart about the OpenAI SDK's own environment variables:
  `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` ride along on a client built
  the quickstart's way, and one of them put a gateway credential in place of the Nebius key.
* NVIDIA's physical AI models on Token Factory. My key lists five NVIDIA rows, all Nemotron, and
  no Cosmos Reason or GR00T, so a Physical AI entry reaches for Nemotron or hosts the rest itself.
* For AI Cloud, which I designed for and did not use: a Nebius CLI for Windows, a default CPU
  quota above 0 in eu-north1, and one name for the interactive environments, since the rules say
  DevPods and the docs say Devlabs.

## What do you most hope to see from the Nemotron team next?

A small Nemotron trained to pilot robots: long tool lists, a safety judgement before anything
moves, and patience in a loop that waits for something to appear, the one thing my postman got
wrong. The same behaviour from the cloud row and the build that runs on a Jetson Thor, so what I
test on Token Factory holds on the robot. And a vision model that knows robots, in photos and in
simple renders.

## Which AI tools have you leveraged while working on this project?

Claude Code, with Claude Opus 5.5, as my coding agent: it wrote most of this folder's code and
tests at my direction, read the Nebius and NVIDIA docs, ran the live tests on Token Factory, and
kept the README's quotes true to the runs. Inside the project itself, NVIDIA Nemotron on Token
Factory pilots the robots and answers `explain` and `whois`.
