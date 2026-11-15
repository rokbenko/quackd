# ADR-0080: Roboglot is the communication layer between people and robots, and between robots

**Status:** proposed · **Date:** 2026-11-14 · Supersedes [ADR-0002](0002-name.md) and the one-liner in [ADR-0035](0035-one-cli-for-all-your-robots.md), whose other decisions stand · Documented in the README and the [FAQ](../faq.md#the-name)

## Context

[ADR-0035](0035-one-cli-for-all-your-robots.md) set the one-liner *One CLI for all your robots*
on 2026-09-13, when the project was a command line on a laptop. By 0.34.0 three things had
outgrown it:

- **There are five ways in, and one of them is a terminal.** The command line, the page that
  `roboglot ui` serves ([ADR-0069](0069-a-page-drives-roboglots-own-commands-over-json-lines.md)),
  the MCP server Claude pilots robots through, the browser simulator and the task file all reach
  the same contract.
- **Part of it runs on the robot.** `roboglot onboard` and the daemons under `bridge/` run on a
  robot's own computer ([ADR-0073](0073-a-robot-carries-its-own-passport-and-its-owner-signs-it.md)).
- **Robot Passport is about robots that are not yours.** Two robots that never met check each
  other's owner-signed passports before their LLMs talk
  ([ADR-0057](0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md)).

The name had the same problem. It came from the Microduck's daemons, when the project was the brain
daemon that one robot was missing ([ADR-0002](0002-name.md)), and so did the words around it: a
task was a `.duck` file and a group of robots was a flock. The duck mark had already left the
front door in 0.16.1 because it read as a toy.

## Decision

**The one-liner.** The hook is *Talk to your robots. Let them talk to each other.* The sentence
after it says what Roboglot is: *Roboglot is the communication layer between people and robots,
and between robots themselves. Say what you want in your own words, from a terminal, a page or
Claude, and an LLM flies the robot. Robots that have never met check each other's passports, then
divide the work.* The short form, for PyPI, the `roboglot --help` banner and the GitHub About
text, is *The communication layer between people and robots, and between robots. Talk to any robot
in your own words, with an LLM for its brain. Robots that have never met check each other's
passports and share the work.*

**The two parties are always named.** Said bare, "communication layer" is what DDS, Zenoh and
iceoryx are, message transport between processes, and the 2009 paper that introduced ROS calls it
"a structured communications layer". Roboglot carries something else: a person's goal to an LLM
and one verb at a time back to a body, and a beacon, a passport and talk between two robots.
"Between people and robots, and between robots" goes wherever the noun goes.

**The name is Roboglot.** A polyglot speaks many languages, and Roboglot speaks for robots in two
directions: people's own words to every robot, and each robot's manifest and passport to every
other. It is written Roboglot in prose and `roboglot` wherever it is typed or imported: the
command, the PyPI distributions `roboglot` and `roboglot-<adapter>`, the packages `roboglot` and
`roboglot_<adapter>`, file names and the home directory `~/.roboglot`. Environment variables and
constants are `ROBOGLOT_*`. On 2026-11-14 the name was free on PyPI, on npm and as a GitHub
account, its `.dev`, `.ai`, `.org` and `.app` domains were unregistered, and a web search found
nothing of weight that uses it.

**A task file is a `.glot` file, and a group of robots is a team.** A task file is written in the
common tongue Roboglot gives a person and a robot, so it ends in `.glot`, and its first key, the
version of the format, is `glot:`. `.task` and `.brief` are read the same way, for a file that
would rather say what it is in plain English, and the bundled tasks live in `tasks/`. A group of
robots is a team: `roboglot team`, `--team`, a `team:` block in a task file, and a team run that
keeps each robot's record under `robots/`. Team is the plain word for robots that divide the work,
and it carries none of a fleet's sense of one company's identical machines. "Duck" stays where it
is the robot: the Microduck, the Open Duck Mini, a duck in the simulator.

**What was not chosen.** "Operating system" is the most crowded noun in robotics, shared by ROS,
RoboOS, Copper and Dimensional, and the claim Roboglot can least defend, since each robot's own
controllers keep its balance, gait and servos ([ADR-0003](0003-three-loops.md)). dROS, said "the
ROS", was refused: ROS is a registered trademark of Open Robotics, whose rules forbid this use
without written approval, and in lowercase it reads as "dross". "Framework" says nothing about what
the project is for, and "coordination layer" leads with its least proven part.

**Nothing keeps the old name but the repository.** The GitHub repository keeps its name until it
is renamed, because many people know the project by that link and a rename would break it for
them. Everything else changed, and nothing reads the old names. The browser simulator's domain is
roboglot.org.

**ADR-0035's other decisions stand.** Roboglot is never called a brain. The LLM is, one per robot,
and `pilot` is the word for the LLM inside the loop. The word is LLM.

## Consequences

- Everything that carried the old name says Roboglot or `roboglot`: the core and its ten adapter
  distributions, the command, imports and the `roboglot.adapters` entry point, environment
  variables, the home directory, logger names, the MCP server's name, the header the policy server
  checks, the services and units on a robot, and the names on the LAN and the radio. A home
  directory, `.env` variables and an MCP configuration from before need renaming, and a robot or
  a laptop on an earlier release does not discover or talk to one on this release. Robots on this
  release find each other and talk as before.
- PyPI has no rename. `roboglot` and each `roboglot-<adapter>` start as new projects at the first
  release under the name, and the version line carries on from 0.34.0.
- The example logs say Roboglot, their raw records included, so a trace under `docs/examples/`
  shows the new name where the run printed the old one. Recordings and screenshots still show what
  was on the screen.
- Local variables named `duck` that hold a task file keep that name, because the same word names
  the robot in the same files, and no user sees a local variable.
