# ADR-0035: One CLI for all your robots

**Status:** accepted · **Date:** 2026-09-13 · Supersedes the one-liner in [ADR-0002](0002-name.md) (the name itself and the reasons for it stand) · Follows from [ADR-0017](0017-robot-adapters-and-manifest.md) (a robot is an adapter that declares a manifest) and [ADR-0034](0034-registered-robots-and-pilot-flocks.md) (a robot has a name, and a flock can be N pilots talking) · Documented in the README

## Context

[ADR-0002](0002-name.md) picked the name and, in the same sentence, fixed the one-liner:
*Give your Microduck a brain. Any LLM, one `.duck` file.* It said "one-liner everywhere", and
everywhere is where it went: the README's first line, `pyproject.toml`'s description and
therefore the PyPI page, the `quackd --help` banner, `quackd/__init__.py`'s docstring, the
browser demo's title and meta tags, and the launch copy in `LAUNCH.md` written for the
channels where a one-liner is the whole post.

It was accurate for 0.3. quackd was a brain for exactly one robot, that robot was a Microduck,
and the interesting part really was that any LLM could drive it from one task file. Three
releases moved underneath it. 0.4 made the robot an adapter that declares a manifest
([ADR-0017](0017-robot-adapters-and-manifest.md)), so the body stopped being a Microduck and
became whatever a manifest describes, and there are seven of those now. 0.9 gave a robot a name
that outlives the process, made a flock a list of those names, and made one kind of flock N
whole agent loops talking over the bus ([ADR-0034](0034-registered-robots-and-pilot-flocks.md)).

What quackd is now is the place a person connects the robots they own, the place they command
all of them, and the thing that lets those robots divide a task between themselves. The old
sentence describes one robot and one brain, which is a feature of the thing rather than the
thing. It also puts quackd in the wrong role. quackd is not the brain. The model is the brain,
one per robot, and quackd is the command line all of them are reached from.

The tension is worth naming here, because this is the file a sceptic will open first. Not one
of the seven bodies has ever run on real hardware. No flock of either kind has ever crossed from
one machine to a second, and there is no `--bus` flag. A pilot flock's members are N separate
simulated worlds with no shared arena and no ground truth to check a claimed success against,
and `tell` has been exercised by the scripted pilot and by no real model. A one-liner says what
a CLI is for. It is not a status report, and the status tables under it are. That is why the
tagline sits directly above a sub-line that keeps saying every body is simulated or mocked, and
why flock mode stays labelled EXPERIMENTAL.

## Decision

**The one-liner, verbatim.** *One CLI for all your robots. Connect them, command them, and let
them work together, each with an LLM for a brain.* It goes everywhere the old one went.

**quackd is the CLI. The LLM is the brain.** quackd is never called "a brain" in prose again.
One brain per robot, and `pilot` stays the word for the LLM inside the loop, so a flock of N
robots is a flock of N pilots.

**The word is flock.** A group of robots run or served together is a flock, and the set a person
owns is "your robots" or "registered robots". "Fleet" is retired from prose and from user-facing
help text. Code identifiers keep it: `Fleet`, `FleetPlan`, `build_fleet_server`,
`fleet_from_flags`, `FLEET_INSTRUCTIONS`, `fleet_default` and the test names that use them are
not renamed, because a rename there is churn no reader ever sees.

**"Small" leaves the positioning.** Not "any small robot", not "your small robot". Any robot can
join. That the seven bodies shipping today are all small is a fact in a table, next to the fact
that none of them has run.

**The way in is not "an API or an SDK".** Two of the seven bodies expose no network API at all, and
a third's own host leaves its arms limp, so for those three quackd ships a daemon or a host wrapper
that runs on the robot itself. The
phrasing, where a doc needs it: any robot with a way in, through its SDK, through the protocol
its host already speaks, or through a small daemon quackd puts on the robot.

**The Microduck origin stays on the first screen.** It is kept deliberately, as the explanation
of the vocabulary rather than as history. The name `quackd`, the `.duck` task file and the word
flock all come from Pollen Robotics' Microduck, which quackd was first written as a brain for. A
reader who is not told that opens a CLI full of ducks for no reason.

## Consequences

- The sentence changes in every place [ADR-0002](0002-name.md) put it: the README, the
  `description` in `pyproject.toml`, the `quackd --help` banner, `quackd/__init__.py`'s
  docstring, the browser demo's `<title>` and meta tags, and `LAUNCH.md`'s per-channel copy. The
  opening lines of [architecture.md](../concepts/architecture.md) and [mcp.md](../guides/mcp.md) change with
  them, because both opened by restating the old framing. The docs test that asserts the README
  carries the tagline asserts the new one.
- GitHub's About text, the landing page at <https://www.quackd.org/> and the uploaded social
  preview all live outside this repository, so they are manual follow-ups that nothing in CI can
  catch, and [PLAN.md](../../PLAN.md) carries them. PyPI keeps showing the old description until
  the next release uploads a new one.
- [flock.md](../guides/flock.md) now reads pilots-first. The auction is still the default and still what
  the kick demo runs, but a reader arriving from the tagline is looking for robots working
  together, and the pilot flock is the part that answers that.
- What deliberately does not change: the CHANGELOG and the earlier ADRs and design notes, which
  record what was true when they were written and stay as written. The LLM system prompts that
  open "You are the brain of" are correct and stay, because there the model really is the brain.
  The hero GIF and its caption are of a Microduck in `sim2d`, which is still the one thing anyone
  can watch, and [ADR-0013](0013-hero-gif.md) still governs them.
- The positioning now promises more than any one status table does, which is the cost of naming
  the product rather than the demo. The mitigation is the one already in place: every page that
  carries the tagline carries the adapter status next to it, and nothing anywhere claims a run on
  hardware.
