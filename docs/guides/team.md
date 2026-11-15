# Team mode

Several robots cooperating on one task, one LLM pilot per robot. Labelled experimental.

```bash
# a duck and an arm say hello
uvx --from "roboglot[microduck,lerobot]" roboglot run team-hello --llm fake
```

Every body is its own package, so the install names each robot the run will open: the demo
wants the duck and the arm. `roboglot[lerobot]` pulls torch on Python 3.12 because the arm's SDK
does, and the demo never leaves the two mocks, `microduck:mock` and `lerobot:mock`.

Which robots are in a team is the task file's to say, or the registry's:

- A `team:` block, which needs `glot: 1` or later, names the members in `team.members`, or counts
  them, 2 to 8, and that is the one key a team reads: `team.allocation.method` is `pilots`, its
  default and its only value.
- **`--team NAME`**, a stored team ([guides/registry.md](registry.md)), supplies the members from
  the registry. A task file with no `team:` block, run that way, is a team too.
- A number after `--team` is refused.

<br>

# The pilot team

One LLM pilot per robot, all at once, on wall-clock time, on any backend. Nobody referees.
The members divide the task between them by saying what they are going to do
([ADR-0034](../adr/0034-registered-robots-and-pilot-teams.md)).

```bash
uvx --from "roboglot[microduck,lerobot]" roboglot run team-hello --llm fake
```

That is the bundled demo: a mock duck and a mock arm, no registry, no API key.

## Its own robots

For anything beyond the demo, register the bodies and name the group
([guides/registry.md](registry.md)):

```bash
roboglot robot add duck microduck:mock
roboglot robot add arm lerobot:mock
roboglot robot add cart rosbridge:mock
roboglot team create trio --robot duck --robot arm --robot cart
roboglot run team-hello --team trio --llm fake
```

```
+- team-hello -----------------------------------------------------------+
| provider   fake (scripted:team-hello)                                  |
| team       duck  microduck:mock                                        |
|            arm   lerobot:mock                                          |
|            cart  rosbridge:mock                                        |
| stored as  trio                                                        |
| status     EXPERIMENTAL                                                |
| memory     0 notes across 3 robots  --no-memory to run fresh           |
+- Ctrl-C or q stops every robot. Ctrl-C twice quits at once. -----------+
```

A task file's own `team.members` plus `robots:` or `--robots` works too, with no registry in
sight, which is what `team-hello` does. What a stored team adds is the address, token,
camera and pilot of each body, and a name for the group.

## What each pilot is

A whole `AgentLoop`, the same one `roboglot run` uses for a single robot: its own provider, its
own executor and allowlist, its own budgets, its own heartbeat, its own memory, and its own
feasibility verdict before it moves. Nothing about a member is a special case, which is the
point: what a pilot team can do is bounded by what one pilot can do, times the number of
bodies.

**Each member is handed the part of the contract its own body can answer for.** The task file
allows what the team as a whole needs. `team-hello` allows `say`, which only the duck has,
and `move_joints`, which only the arm has, and each pilot's prompt lists only its own:

```
duck     tools   report_state, observe, stop, say, assess_task, declare_success, declare_failure, tell, remember
cart     tools   report_state, observe, stop, assess_task, declare_success, declare_failure, tell, remember
arm      tools   report_state, observe, stop, move_joints, assess_task, declare_success, declare_failure, tell, remember
```

What the task *requires* is checked against the union of every body before anything connects,
so a task nobody in the team can do is refused with nothing written to disk.

## Talking

`tell` is a tool beside `assess_task`, `declare_success` and `remember`. It moves nothing and
costs no step, one LLM call, and reaches the addressee in its next observation:

```
duck  #  talk    duck -> all: duck here and ready; say hello back
arm              Messages from your team (newest last):
arm              - duck -> all: duck here and ready; say hello back
arm              - cart -> all: cart here and ready; say hello back
arm   #  talk    arm -> all: arm here and ready; say hello back
```

A pilot never hears its own words back. `to` is a member name or `all`. Every message is a
`TALK` on the team's bus, the only kind it carries, so every one of them is in `team.jsonl`:

```jsonc
{"t": 3.561, "kind": "bus", "msg": {"src": "duck", "kind": "TALK", "to": null, "text": "duck here and ready; say hello back"}}
```

The runner speaks too, under the name `team`, when a member's loop ends. Without that, a
pilot waiting on somebody who has already stopped would wait until its budget ran out:

```
team  #  talk    team -> all: duck declared success: said hello and heard back from cart
```

## What each pilot is told about the others

Every pilot's system prompt gains a `## Your team` section naming each peer and giving its
datasheet in **the same paragraph form its own body is described in**. A pilot deciding who
fetches and who holds is reading data rather than guessing:

```
## Your team
You are `duck`, one of 3 pilots on this task file. Each of you is in a different body
and all of you are working at the same time. Nobody is in charge and nothing assigns the work:
you divide it between you by saying what you will do.

The others:
- `arm` (lerobot:mock): lerobot-so101: height 0.53 m (estimate: ...), actuated joints 6 (...), ...
```

The same section says that `assess_task` judges **your part** of the task rather than all of
it, and that a body with no part in it should say so and then declare success once the others
report done. Without that, the arm in a kicking team would answer `infeasible` and end its
own run for nothing.

## The outcome

Every pilot declares for itself. The team succeeds only when all of them declared success:

```
+- + SUCCESS --------------------------------------------------------------------------------------+
| every member declared success: duck, arm, cart                                                   |
| members 3/3 succeeded - talk 3 - steps 0 - LLM calls 9 - tokens 19537+144 - time 1.2 s - cost $0 |
+--------------------------------------------------------------------------------------------------+
```

That count is from a `--no-memory` run, and it is not the same on every run. The mock arm's calls
to its follower run on threads of their own, as an arm's do, so how long its connect takes
varies, and the others' first messages reach its pilot before its first turn on some runs and
after it on others. One run in eight said `19546+144`. With memory on it climbs by whatever each
robot has remembered, because every note and every past outcome goes into that pilot's next
prompt. The cost is the team's, summed over its members, and it reads `$0` rather than
`unpriced` here because the scripted pilot is priced at nothing rather than left without a rate.

Otherwise the worst outcome wins, in the order `error`, `aborted`, `infeasible`, `budget`,
`failure`, and the reason names every member that did not succeed, worst first. `error` beats
`aborted` because of what that pair usually means together: one member raised and the rest
were stopped **because it did**, so the error is the cause and the aborts are its consequence.

Ctrl-C or `q` stops every body through one kill switch fanned out to every executor. A pilot
mid-verb is cancelled and sent a stop; a pilot waiting on its LLM notices at its next turn,
exactly as a solo run does. The first exception any member raises stops the others with a
reason that names it.

`roboglot run` exits 1 when the team did not succeed, and 3 when the outcome was `infeasible`.

## Reading a pilot run

```
runs/<timestamp>-team-hello/
  team.jsonl          # team_start, every TALK, member_end per robot, team_end
  summary.json         # outcome, reason, run_name, per_member rollup, messages, usage,
                       # cost_usd, wall_elapsed_s
  terminal.txt         # everything that was on the terminal, as plain text, opening with
                       # the command that started it and the version that ran it
  robots/duck/          # a full solo-style transcript.jsonl and frames/ per robot
  robots/arm/
  robots/cart/
```

There is no per-robot `summary.json`. The rollup is the team's, and a member's
directory must not read as a solo run. `terminal.txt` is at the root for the same reason:
every member narrated to one terminal, so there is one file of what was on it. Times in
`team.jsonl` are stamped `t`, in wall seconds since the run started.

`--run-name` works here exactly as it does on a solo run: the slug goes in the directory
name after the task (`runs/20260921-160600-team-hello-demo-2/`) and the text as you typed
it is kept as `run_name` in the summary. Money rolls up the way usage always has. Each
member's block in `per_member` carries its own `cost_usd` beside its `usage`, along with the
`wall_s` and `llm_latency_s` that member spent, and the team's own `cost_usd` is the sum of
them. That total is `null` the moment one member could not be priced, rather than a figure
quietly missing a robot: a team bill short one duck is worse than no team bill. The rate is
each member's own, from the catalogue entry for the LLM that pilot ran. `--price` is
carried into a team and applies to every member at that one rate, because it is a rate for
the run rather than one per robot, and `ROBOGLOT_PRICE` does the same thing per shell. Either is
how you price a team whose LLMs Roboglot has no published rate for.

`roboglot log <run>` replays each member's transcript in turn.

## Watching a pilot run

Each robot gets its own view with its name and colour on every line, and the runner's own
notices print under `team`. `--no-log` or `ROBOGLOT_LOG=0` removes the views and leaves every
record intact: the flag says what you watch, and every member writes its own log either way.

## What this is not

- **N mock members are N separate bodies.** Two `microduck:mock` pilots cannot see each other,
  and there is no ground truth to check a claimed success against.
- **A pilot team costs N budgets and N times the tokens**, since every member is a whole pilot.
- **No team has run on hardware.** The mixed-body case is exercised on `mock` backends,
  and `tell` has been exercised by the scripted pilot and by no real LLM.
- **Nothing makes it reproducible**, because there is no shared clock to fix.
- **There is no `--bus` flag**, so nothing has carried a pilot team between two machines.

## Status and future work

One demo, `team-hello`, on `mock` bodies with the scripted pilot. Mixed adapters work and are
tested, `tell` has been seen by no real LLM, and nothing has run on hardware.

An MQTT bus implementing the same `Bus` protocol exists ([guides/lan.md](lan.md)), library only
and tested on a fake broker, and carries `TALK`, the one kind of message there is. A pilot team
needs no clock across machines and has simply never been tried across two. See
[adapters/status.md](../adapters/status.md) for the wider honesty table.
