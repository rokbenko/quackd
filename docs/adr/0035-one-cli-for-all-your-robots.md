# ADR-0035: One CLI for all your robots

**Status:** accepted · **Date:** 2026-09-13 · Supersedes the one-liner in [ADR-0002](0002-name.md) (the name itself and the reasons for it stand) · Follows from [ADR-0017](0017-robot-adapters-and-manifest.md) (a robot is an adapter that declares a manifest) and [ADR-0034](0034-registered-robots-and-pilot-teams.md) (a robot has a name, and a team can be N pilots talking) · Documented in the README

**Amended 2026-11-14 by [ADR-0080](0080-roboglot-is-the-communication-layer-between-people-and-robots.md):** the one-liner below is superseded, the project is
now called Roboglot, and its task files are `.glot` files. Its other decisions stand.

## Context

[ADR-0002](0002-name.md) picked the name and, in the same sentence, fixed the one-liner:
*Give your Microduck a brain. Any LLM, one `.glot` file.* It said "one-liner everywhere", and
everywhere is where it went: the README's first line, `pyproject.toml`'s description and
therefore the PyPI page, the `roboglot --help` banner, `roboglot/__init__.py`'s docstring, the
browser demo's title and meta tags, and the launch copy in `LAUNCH.md` written for the
channels where a one-liner is the whole post.

It was accurate for 0.3. Roboglot was a brain for exactly one robot, that robot was a Microduck,
and the interesting part really was that any LLM could drive it from one task file. Three
releases moved underneath it. 0.4 made the robot an adapter that declares a manifest
([ADR-0017](0017-robot-adapters-and-manifest.md)), so the body stopped being a Microduck and
became whatever a manifest describes, and there are seven of those now. 0.9 gave a robot a name
that outlives the process, made a team a list of those names, and made one kind of team N
whole agent loops talking over the bus ([ADR-0034](0034-registered-robots-and-pilot-teams.md)).

What Roboglot is now is the place a person connects the robots they own, the place they command
all of them, and the thing that lets those robots divide a task between themselves. The old
sentence describes one robot and one brain, which is a feature of the thing rather than the
thing. It also puts Roboglot in the wrong role. Roboglot is not the brain. The LLM is the brain,
one per robot, and Roboglot is the command line all of them are reached from.

The tension is worth naming here, because this is the file a sceptic will open first. Not one
of the seven bodies has ever run on real hardware. No team of either kind has ever crossed from
one machine to a second, and there is no `--bus` flag. A pilot team's members are N separate
simulated worlds with no shared arena and no ground truth to check a claimed success against,
and `tell` has been exercised by the scripted pilot and by no real LLM. A one-liner says what
a CLI is for. It is not a status report, and the status tables under it are. That is why the
tagline sits directly above a sub-line that keeps saying every body is simulated or mocked, and
why team mode stays labelled EXPERIMENTAL.

## Decision

**The one-liner, verbatim.** *One CLI for all your robots. Connect them, command them, and let
them work together, each with an LLM for a brain.* It goes everywhere the old one went.

**Roboglot is the CLI. The LLM is the brain.** Roboglot is never called "a brain" in prose again.
One brain per robot, and `pilot` stays the word for the LLM inside the loop, so a team of N
robots is a team of N pilots.

**The word is team.** A group of robots run or served together is a team, and the set a person
owns is "your robots" or "registered robots". "Fleet" is retired from prose and from user-facing
help text. Code identifiers keep it: `Fleet`, `FleetPlan`, `build_fleet_server`,
`fleet_from_flags`, `FLEET_INSTRUCTIONS`, `fleet_default` and the test names that use them are
not renamed, because a rename there is churn no reader ever sees.

**"Small" leaves the positioning.** Not "any small robot", not "your small robot". Any robot can
join. That the seven bodies shipping today are all small is a fact in a table, next to the fact
that none of them has run.

**The way in is not "an API or an SDK".** Two of the seven bodies expose no network API at all, and
a third's own host leaves its arms limp, so for those three Roboglot ships a daemon or a host wrapper
that runs on the robot itself. The
phrasing, where a doc needs it: any robot with a way in, through its SDK, through the protocol
its host already speaks, or through a small daemon Roboglot puts on the robot.

**The Microduck origin stays on the first screen.** It is kept deliberately, as the explanation
of the vocabulary rather than as history. The name `roboglot`, the `.glot` task file and the word
team all come from Pollen Robotics' Microduck, which Roboglot was first written as a brain for. A
reader who is not told that opens a CLI full of ducks for no reason.

## Consequences

- The sentence changes in every place [ADR-0002](0002-name.md) put it: the README, the
  `description` in `pyproject.toml`, the `roboglot --help` banner, `roboglot/__init__.py`'s
  docstring, the browser demo's `<title>` and meta tags, and `LAUNCH.md`'s per-channel copy. The
  opening lines of [architecture.md](../concepts/architecture.md) and [mcp.md](../guides/mcp.md) change with
  them, because both opened by restating the old framing. The docs test that asserts the README
  carries the tagline asserts the new one.
- GitHub's About text, the landing page at <https://www.roboglot.org/> and the uploaded social
  preview all live outside this repository, so they are manual follow-ups that nothing in CI can
  catch, and [PLAN.md](../../PLAN.md) carries them. PyPI keeps showing the old description until
  the next release uploads a new one.
- [team.md](../guides/team.md) now reads pilots-first. The auction is still the default and still what
  the kick demo runs, but a reader arriving from the tagline is looking for robots working
  together, and the pilot team is the part that answers that.
- What deliberately does not change: the CHANGELOG and the earlier ADRs and design notes, which
  record what was true when they were written and stay as written. The LLM system prompts that
  open "You are the brain of" are correct and stay, because there the LLM really is the brain.
  The hero GIF and its caption are of a Microduck in `sim2d`, which is still the one thing anyone
  can watch, and [ADR-0013](0013-hero-gif.md) still governs them.
- The positioning now promises more than any one status table does, which is the cost of naming
  the product rather than the demo. The mitigation is the one already in place: every page that
  carries the tagline carries the adapter status next to it, and nothing anywhere claims a run on
  hardware.
