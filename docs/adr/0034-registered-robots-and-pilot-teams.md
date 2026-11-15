# ADR-0034: A robot has a name, and a team can be N pilots talking

**Status:** accepted, amended · **Date:** 2026-09-13 · Extends [ADR-0020](0020-heterogeneous-teams.md) (bodies in a team) and [ADR-0021](0021-lan-discovery-and-mqtt-bus.md) (the bus carries one more kind) · Amends [ADR-0015](0015-team-deterministic-coordinator.md) (per-duck LLM pilots were out of scope), [ADR-0016](0016-team-lockstep-clock.md) (the lockstep clock is the auction's, not every team's) and [ADR-0025](0025-memory-between-runs.md) (memory is keyed by a registered name where there is one) · Documented in [registry.md](../guides/registry.md) and [team.md](../guides/team.md)

**Amended 2026-11-15:** a team is one kind now. The coordinator team, `auction`, is gone with
the simulators it ran on, so `team.allocation.method` takes `pilots` alone and is that by
default, `--team` takes a stored team's name and refuses a number, and the Consequences about
the auction no longer apply. Registered robots, a pilot team, `tell` and memory by name stand as
below. See the CHANGELOG's Unreleased entry.

## Context

Two separate things had gone as far as they could.

**A robot had no name.** Every command that reached a real body retyped it: `--robot
open_duck:bridge --address tcp://10.0.0.5:9871 --token ... --camera-url ...`, five flags, one
of them a secret, on every invocation and therefore in shell history. `--robots
name=adapter:backend,...` gave a name that died with the process. Nothing on disk mapped a
name to a body, so there was nothing for a team to be a list of, and `RobotMemory` keyed
`adapter:backend` meant two Microducks on one desk shared one file of notes
([ADR-0025](0025-memory-between-runs.md)).

**A team was one choreography.** [ADR-0015](0015-team-deterministic-coordinator.md) built
the auction for a task class it named ST-SR-IA and argued, correctly, that a deterministic
Contract Net is near-optimal there and that per-duck LLM pilots would cost N times the tokens
for no gain. That reasoning holds for finding and kicking a ball. It does not generalise: two
robots whose bodies differ have to decide who does which half, and the auction has no way to
express a half. [ADR-0020](0020-heterogeneous-teams.md) gave roles a vocabulary and
[ADR-0032](0032-datasheets-and-the-verdict.md) gave a body a datasheet to answer with, and
that is what changed the argument. A pilot that can read what its own body cannot do has
something to say to another pilot. Until it could, talking would have been theatre.

Meanwhile `roboglot/team/runner.py` refused anything but a Microduck on `sim2d`, so the role
machinery those two ADRs built had nothing to run on.

## Decision

**A registered robot is a name for a spec plus how to reach it.** `roboglot robot add NAME
<adapter>:<backend>` writes `~/.roboglot/robots.json`: the spec, `address`, `token`,
`camera_url`, an optional `provider` and `model` for the pilot that drives it, and a note.
`--registry-dir` beats `ROBOGLOT_REGISTRY_DIR` beats `~/.roboglot`, the same precedence
`--memory-dir` has. Reads are strict, so a hand-edited typo names the field and stops the
command, where memory's are lenient: this is configuration a person wrote rather than notes an
LLM wrote. Writes go to a temporary file renamed over the old one, unserialised, last rename
wins: the same accepted race as memory, for the same reason, that the file stays one a person
can open. `roboglot robot list --probe` connects to each and says whether it answered.

**A team is a list of those names that you keep.** `roboglot team create NAME --robot A
--robot B` writes `~/.roboglot/teams.json`; with no `--robot` it prints the registered robots
numbered and asks which to include. Members are registered names, so a team is a composition
rather than a command line. Removing a robot a team lists is refused until the team stops
listing it.

**There are two kinds of team, and the task file says which.** `team.allocation.method`
takes `auction`, the default and unchanged (the 0.3 coordinator, sim2d Microducks,
deterministic, at most one LLM call), or `pilots`. `--team N` is always the auction,
`--team <name>` runs a stored team, and a task file with no `team:` block run under a name
is pilots. An auction is 2 to 4 members because its arena holds four. A pilot team is 2 to 8
because nothing is shared and the bound is what one terminal can show.

**A pilot team is N agent loops on wall-clock time over the bus that already existed.** Each
member gets its own `AgentLoop`: its own provider, executor, allowlist trimmed to what that
body actually provides, budget, heartbeat, memory and verdict. There is no `TeamClock` and no
coordinator, so a seed does not make a pilot team reproducible, and nothing arbitrates
because the members do. Any backend, same bodies or different ones.

**`tell` is a meta tool and `TALK` is a bus message.** `tell(to, text)` sits beside
`assess_task`, `declare_success` and `remember`: it moves nothing, costs no step and one LLM
call, and reaches the addressee in its next observation under "Messages from your team". The
system prompt gains a "Your team" section naming every peer with the one-paragraph form of
its datasheet, so a pilot deciding who does which half is reading data rather than guessing.
`tell` is not a verb, appears in no manifest, and no robot ever executes one.

**Every pilot judges its own part, and the team's outcome is the members' own claims.**
`assess_task` asks whether this body can do its part, and a body with no part says so and
declares success once the others report done. The team succeeds only when every member
declared success. Otherwise the worst outcome wins, in the fixed order error, aborted,
infeasible, budget, failure, and the reason names every member that did not succeed, worst
first. `error` outranks `aborted` because of what that pair usually means together: one
member raised and the rest were stopped because it did, so the error is the cause and the
aborts are its consequence; when a person really does press something, every member aborts
and nothing errors. Ctrl-C or `q` stops every body through one kill switch fanned out to
every executor, and the first exception any member raises aborts the others with a reason
that names it.

**Memory follows the name.** A robot run by its registered name keys its memory file by that
name, so two registered Microducks keep separate notes. An ad-hoc `adapter:backend` run keys
as it always did. A registered name may not collide with an adapter name or with an existing
`adapter-backend` memory slug, so the two schemes cannot meet in one file.

**`serve-mcp --team <name>` is the same fleet by another door.** A stored team's members
become the MCP sessions, each with its own address, token and camera from the registry rather
than one global value for all of them. No new MCP tools.

## Consequences

- The auction is untouched, and the seed-3 golden proves it: the same summary, the same bus
  conversation, the same six v0 task hashes. Its members are still state machines with no
  pilot to ask, which is why [ADR-0012](0012-safety-executor.md)'s verdict gate is still off
  for them and on for every pilot.
- **N simulated members are N separate worlds.** A pilot team has no shared arena, so two
  `microduck:sim2d` pilots cannot see each other, there is no ground-truth veto on a claimed
  success, and there is no single GIF of the run. A shared arena stays an auction feature.
  That is the honest cost of running the same code against hardware and against a simulator.
- **A pilot team costs N budgets and N times the tokens.** That is exactly what
  [ADR-0015](0015-team-deterministic-coordinator.md) said it would cost, and it is why the
  auction is still what the kick demo runs.
- Nothing here has run on hardware. The mixed-body case is exercised on mock backends, and
  `tell` has been exercised by the scripted pilot and by no real LLM.
- No distributed clock, so no `--bus` flag for either kind. `MqttBus` carries `TALK` like every
  other kind, and nothing has carried a team between machines.
- Tokens sit in plain text in `~/.roboglot/robots.json`, masked in everything Roboglot prints.
  That is a file in a home directory, not a secret store, and `SECURITY.md` says so.
- A registered robot's manifest id becomes its registered name, which is what a fleet and a
  team already did with `--robots`.
- Older Roboglot refuses `allocation.method: pilots` rather than ignoring it, which is the
  correct failure for a file that asks for behaviour it does not have.
