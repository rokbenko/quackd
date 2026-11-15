# Team mode

Several robots cooperating on one task. Labelled experimental, and there are two kinds of
it, which differ in who decides what each robot does next.

```bash
# the pilots: a duck and an arm say hello
uvx --from "roboglot[microduck,lerobot]" roboglot run team-hello --llm fake
# the coordinator: ducks bid, closest one kicks
uvx --from "roboglot[microduck]" roboglot run team-kick --llm fake --seed 3
```

Every body is its own package, so the install names each robot the run will open: the pilot
demo wants the duck and the arm, the coordinator wants only the duck. `roboglot[lerobot]` pulls
torch on Python 3.12 because the arm's SDK does, and this demo never leaves `lerobot:mock`.

## Two kinds of team

|  | **the pilots** (`pilots`) | **the coordinator** (`auction`) |
|---|---|---|
| Who picks each robot's next verb | that robot's own LLM | one deterministic referee |
| The clock | wall clock, all at once | one lockstep sim clock |
| Bodies | any adapter, any backend, mixed | Microducks on `sim2d` |
| Size | 2 to 8 | 2 to 4 (the arena holds four) |
| LLM calls | one per pilot per turn | at most one, for the whole run |
| How the work is divided | the pilots say what they will do | an auction on camera distance |
| How success is judged | every member declares for itself | the simulator's own telemetry |
| Reproducible from a seed | no | yes |
| Ships since | 0.9 | 0.3 |

Which one runs is the task file's to say, through `team.allocation.method`:

- `auction` is the default, and everything in a `team:` block belongs to it.
- `pilots` needs `glot: 1`, and only `team.members` is read.
- **`--team N`**, a number, is always the coordinator: N simulated ducks.
- **`--team NAME`**, a stored team ([guides/registry.md](registry.md)), supplies the members from
  the registry. A task file with no `team:` block, run that way, is a pilot team.

One half of this page each. The pilots come first, because that is the kind a team of your
own robots runs. The coordinator follows, and everything written about it is still true of it.

<br>

# The pilot team

One LLM pilot per robot, all at once, on wall-clock time, on any backend. Nobody referees.
The members divide the task between them by saying what they are going to do
([ADR-0034](../adr/0034-registered-robots-and-pilot-teams.md)).

```bash
uvx --from "roboglot[microduck,lerobot]" roboglot run team-hello --llm fake
```

That is the bundled demo: a simulated duck and a mock arm, no registry, no API key.

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
duck      tools   report_state, observe, stop, say, assess_task, declare_success, declare_failure, tell, remember
arm       tools   report_state, observe, stop, move_joints, assess_task, declare_success, declare_failure, tell, remember
cart      tools   report_state, observe, stop, assess_task, declare_success, declare_failure, tell, remember
```

What the task *requires* is checked against the union of every body before anything connects,
so a task nobody in the team can do is refused with nothing written to disk.

## Talking

`tell` is a tool beside `assess_task`, `declare_success` and `remember`. It moves nothing and
costs no step, one LLM call, and reaches the addressee in its next observation:

```
duck   #  talk    duck -> all: duck here and ready; say hello back
arm               Messages from your team (newest last):
arm               - duck -> all: duck here and ready; say hello back
arm    #  talk    arm -> all: arm here and ready; say hello back
```

A pilot never hears its own words back. `to` is a member name or `all`. Every message is a
`TALK` on the same bus the coordinator uses, so every one of them is in `team.jsonl`:

```jsonc
{"t": 0.094, "kind": "bus", "msg": {"src": "duck", "kind": "TALK", "to": null, "text": "duck here and ready; say hello back"}}
```

The runner speaks too, under the name `team`, when a member's loop ends. Without that, a
pilot waiting on somebody who has already stopped would wait until its budget ran out:

```
team  #  talk    team -> all: duck declared success: said hello and heard back from arm, cart
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
- `arm` (lerobot:mock): lerobot-so101: height 0.53 m (estimate: ...), one arm with a gripper, ...
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
| members 3/3 succeeded - talk 3 - steps 0 - LLM calls 9 - tokens 19946+144 - time 0.5 s - cost $0 |
+--------------------------------------------------------------------------------------------------+
```

That count is from a `--no-memory` run, and it is not the same on every run. The mock arm's calls
to its follower run on threads of their own, as an arm's do, so how long its connect takes
varies, and the others' first messages reach its pilot before its first turn on some runs and
after it on others. Two runs in eight said `19955+144`. With memory on it climbs by whatever each
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

No `run.gif`, and no per-robot `summary.json`. The rollup is the team's, and a member's
directory must not read as a solo run. `terminal.txt` is at the root for the same reason:
every member narrated to one terminal, so there is one file of what was on it. Times in
`team.jsonl` are stamped `t`, in wall seconds since the run started, where the
coordinator's are `sim_t`.

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

- **N simulated members are N separate worlds.** Two `microduck:sim2d` pilots cannot see each
  other, there is no ground truth to check a claimed success against, and there is no one GIF
  of the run. A shared arena stays a coordinator feature.
- **A pilot team costs N budgets and N times the tokens.** That is what the coordinator was
  built to avoid ([ADR-0015](../adr/0015-team-deterministic-coordinator.md)), and it is why the
  kick demo still runs the coordinator.
- **No team has run on hardware.** The mixed-body case is exercised on `mock` backends,
  and `tell` has been exercised by the scripted pilot and by no real LLM.
- **A seed does not make it reproducible**, because there is no shared clock to fix.
- **There is no `--bus` flag**, so nothing has carried a pilot team between two machines.

<br>

# The coordinator team

Everything from here to [Status and future work](#status-and-future-work) is about the `auction` kind.

## What a team of this kind is

2 to 4 robots in one shared arena on one shared clock, each with its **own** safety
executor enforcing the same `.glot` contract: allowlist, budgets, machine enforced abort
rules, per robot transcript. Ducks come in the four colorways (Cream, Sky, Lavender,
Graphite). A deterministic **coordinator** referees. Add a `team:` block to a `.glot` file
or pass `--team N` to any task file; name the members' robots with `robots:` in the file or
`--robots name=<adapter>:<backend>,...`. Every member acts only through the verbs its own
manifest provides ([ADR-0020](../adr/0020-heterogeneous-teams.md)).

## The bus

All coordination crosses a tiny in process pub/sub bus, one message at a time, and every
message lands in `team.jsonl`. Nine message kinds: `TASK` (the plan), `BID` (a sighting
with the bidder's own camera distance estimate and, with roles, the role it bids for, the
verbs it provides and, in a `glot: 2` file, its datasheet), `CLAIM` (the one kick permit, with the role assignments), `ROLE`
(SEARCH a heading sector, KICK, YIELD, STOP, and with roles SPOT and JUDGE), `HB`
(heartbeat for the watchdog), `RESULT` (kicked, miss, search empty, budget, aborted, and
with roles `kick_done`), `HINT` (an arena frame target estimate, sim only), `VERDICT`
(the spotter's judgement of a kick, which is a different thing from the feasibility verdict
a solo pilot gives before it moves) and `TALK` (one LLM pilot saying something to another,
which an auction never sends and a pilot team sends nothing else). The bus is a small
protocol so a LAN bus (MQTT) can slot in for
real robots. Only the in process implementation is used by default, and nobody ever awaits
the bus, which keeps the shared clock deadlock free.

## The auction, in one paragraph

Contract Net, the same shape RoboCup teams use. The first `BID` opens a window of 0.4 s
of sim time. When it closes, the lowest camera distance wins, ties break on the member
name, and a previous kicker keeps its claim unless a challenger undercuts it by the
hysteresis margin (20 % by default), which stops role oscillation. The claim carries a
lease (6 s), a fixed fuse from the moment it is granted. A miss or an expired lease
releases the claim and the failed duck sits out a cooldown, during which it may keep
searching but cannot bid. A lost heartbeat also releases the claim, but that duck is
presumed dead and excluded for good. Either way everyone re-scans the full circle (the
ball has moved) and the auction runs again. Ducks cannot fall in the 2D
simulator, and this kind of team runs nowhere else, so fall handling is untested. A duck can
fall in `microduck:mujoco`, which only a pilot team can reach and no bundled one uses, so
nothing exercises that path either.

## Roles

- **SEARCH**: orient to your heading sector (if `walk` is allowed), `search_scan` inside
  it, quack on a sighting (the theatrical part), publish a `BID`.
- **KICK**: `walk_to` the target, `kick`, report the result. The contract's criterion is
  total ball displacement, so a rally of short kicks counts.
- **YIELD**: stop, and back away when the coordinator's ground truth check says you are
  inside the minimum separation ring, or as blind courtesy when your own last ball
  estimate was.
- **STOP**: the run is over.

Each role step is one verb through that duck's own executor. A role change mid verb
preempts it cleanly and does not count as a failure.

## Heterogeneous roles (0.4)

A `glot: 1` file may declare `team.roles`, and Roboglot knows exactly two, `spotter` and
`kicker`:

```yaml
team:
  members: [duck-01, duck-02]
  roles:
    spotter: {requires: [observe, gaze]}
    kicker: {requires: [go_to, kick]}
robots:
  duck-01: microduck:sim2d
  duck-02: microduck:sim2d
```

### Roles by data

A `glot: 2` role may also say what the body has to **be**, not only what it has to know. The
words are the datasheet's own (`payload_kg`, `carries_kg`, `reach_m`, `manipulator`, `terrain` and the rest),
and each one is checked its own way: the rule per key is one table in
[reference/glot-spec.md](../reference/glot-spec.md#needs--the-datasheet-vocabulary-v2), and what the
words mean is [reference/manifest-spec.md](../reference/manifest-spec.md).

```yaml
team:
  members: [eye, arm]
  roles:
    spotter: {requires: [observe, gaze]}
    kicker:
      requires: [go_to, kick]
      needs: {payload_kg: 1.0, manipulator: gripper}
```

A figure the robot's maker never published counts as **not met**, because a robot that
cannot say what it carries is not the one to ask to carry something. The check runs three
times, and says the same thing each time: `roboglot validate --robots` refuses a role no
robot in the team can fill, before a run starts; a member only bids for roles its own
datasheet satisfies; and the coordinator re-checks every bid from what the bid itself
carried, so a robot it does not run is held to the same standard. A rejected bid is a
`bid_rejected` line naming exactly what was short, in the same words a pilot's own refusal
uses: `payload_kg >= 1 (has 0.5)`, `manipulator = gripper (has beak)`.

Those three checks are also the *only* feasibility judgement in a coordinator team. Its
member is a state machine with no pilot to ask, so it is never sent through the `assess_task`
gate a solo run opens with ([concepts/safety.md](../concepts/safety.md), [ADR-0032](../adr/0032-datasheets-and-the-verdict.md)).
A [pilot team](#the-pilot-team) member is a whole pilot and is asked exactly as a solo run
is, about its own part of the task.

- **Capability aware bids.** A robot bids only for a role whose `requires` its manifest
  satisfies (aliases count: `get_frame` satisfies `observe`). The coordinator checks every
  bid again before counting it, so a bid from a robot we do not run cannot claim a role it
  cannot do. The cost stays the robot's own camera distance, so no shared map is needed.
- **One auction, several roles.** Roles are filled most constrained first (fewest eligible
  bidders, then role name), lowest own distance wins, ties break on the member name, and a
  previous kicker keeps its claim under the same hysteresis rule. The **spotter is held
  for the run** (its reference frame must not change between kicks); the kicker is
  re auctioned every cycle. A duck can spot too, so two ducks make a valid spotter and
  kicker pair, which is the only pairing Roboglot ships an adapter for today ([ADR-0020](../adr/0020-heterogeneous-teams.md)'s amendment).
- **SPOT**: gaze at the sighting, take a fresh frame, keep the target in view. The
  spotter's first sighting is its reference point.
- **KICK, with roles**: `go_to`, `kick`, step aside, and report `kick_done`. The actor
  never evaluates success.
- **JUDGE**: the spotter sweeps its gaze around the last sighting with a fresh frame at
  each look and publishes a `VERDICT`: `moved` when the target is more than the contract's
  0.3 m plus a judge margin from the reference, `not_moved`, or `lost`. The judge margin
  (0.15 m by default) exists because the size based distance estimate quantises in about
  0.2 m steps beyond 1.5 m: a strict spotter costs a re kick, a lenient one would let the
  world's veto fail the run. Only `moved` is a success; anything else sends the kicker
  back to search and kick again against the same reference, so a rally adds up.

The spotter judges, the world vetoes: `summary.json` only says `success` when the
spotter's verdict and the simulator's `ball_displacement_m` agree. No bundled starter
exercises this path today: it shipped alongside a stationary-head adapter that no longer
exists, so the mechanism above is real and tested at the unit level
([tests/test_team_roles.py](../../tests/test_team_roles.py)) but currently undemonstrated
end to end. Nor can you simply write one: a `.glot` like the one above **validates** (`roboglot
validate` reports a team of 2) and then **fails at startup** with `no live robot can take the
spotter role`, because the coordinator checks who can fill each role before any member has
finished connecting and reported its vocabulary. Nothing in the shipped tree reaches the
role and auction path end to end, and that is the honest state of it.

## Frame of reference

There is no computable relative frame between two robots on hardware: the Microduck has
no absolute localisation, and Roboglot does not assume two robots share a coordinate frame
in general. Two consequences shape the design. The spotter judges displacement in its
**own camera frame**, against its own first sighting, which needs no shared frame at all;
that is why a stationary spotter would be the honest judge on hardware too. And **frame
hints** (`HINT` messages) are the spotter's arena frame estimate of the target, which only
exist in the simulator where every robot knows its pose: a receiver uses one solely to
choose which way to turn before its own `search_scan`, every approach and every kick uses
the kicker's own camera. `team.frame_hints: auto` turns them on only when every member
runs in `sim2d`; on hardware they are off.

## What the LLM does, and does not do

At most **one** LLM call per run: the planner may tune task parameters (target label,
approach distance, scan step, timeout) through a single tool call, asked for the way that
vendor's provider asks for every call: forced where it can force one, and only asked for where
the vendor or the LLM refuses a forced call. Numeric
parameters are clamped into the schema's ranges, an invalid field is dropped on its own
(the valid ones survive), and a missing or broken call falls back to deterministic
defaults, logged. With `--llm fake` even that call is skipped and the plan is a pure
function. The auction, the roles and the steering are deterministic code. `summary.json`
records `planner.llm_calls` (0 or 1) as proof, and `planner.cost_usd` beside it, priced from
the planner's own LLM exactly as a solo run's turn is. That is the whole bill for this kind
of team: `per_duck` carries no cost because a coordinator member is a state machine that
never calls an LLM. `--run-name` names the run here too, and the name you typed is kept as
`run_name` in the summary.

Per robot LLM pilots do cost N times the tokens and the latency, which is why this is still
what the kick demo runs. They are no longer out of scope: they are the
[other kind of team](#the-pilot-team), and they exist because a pilot that can read a
datasheet has something to say to another pilot, which was not true when
[ADR-0015](../adr/0015-team-deterministic-coordinator.md) ruled them out.

## Ground truth

The outcome is judged by the coordinator from sim telemetry (`ball_displacement_m`), not
from any LLM's claim. A member reporting a kick the world did not record turns the run
into a failure. Duck to duck safety separation is watched from world ground truth: while
a claim is live the coordinator measures every other duck's true distance to the kicker
and orders an intruder to retreat, with the motion still running through that duck's own
executor. The kicker's ball approach uses perception only, exactly like a solo run.

## Reading a team run

```
runs/<timestamp>-team-kick/
  team.jsonl          # the coordinator's own record: every bus message, auction, verb
  summary.json         # outcome, run_name, kicker, auctions, bids, planner proof, per duck
                       # rollup; with roles also robots, roles, assignments, spotter, verdicts
  run.gif              # world view | the claimant's own camera, with phase captions
  terminal.txt         # everything that was on the terminal, as plain text, opening with
                       # the command that started it and the version that ran it
  robots/duck-0/        # per robot transcript.jsonl and frames/ (no summary.json on purpose)
```

Three annotated lines from a real `team.jsonl`:

```jsonc
{"sim_t": 2.4, "kind": "bus", "msg": {"kind": "BID", "src": "duck-1", "ball_dist_m": 0.62}}
{"sim_t": 2.8, "kind": "auction_decision", "kicker": "duck-1", "bids": {"duck-1": 0.62}, "tie": false}
{"sim_t": 8.6, "kind": "bus", "msg": {"kind": "RESULT", "src": "duck-1", "status": "kicked", "ball_moved_m": 0.59}}
```

## Watching a team run

A team is logged like a solo run, and narrated as it happens by default. Each robot gets its
own view with its name on every line, so three robots moving at once stay three readable
columns rather than one interleaving, and the coordinator's own decisions print under `team`.

```
duck-2  ▶  verb    search_scan(target='ball', step_deg=45, max_steps=3)
duck-2  →  send    look(x=1, y=0, z=0)
duck-2  ✓  result  search_scan ok: ball found: ball at bearing 28° left ~0.81 m (after 2 turn steps) (1.8 s sim, 0.1 s wall, 19 intents)
team   ◆  auction first bid duck-2 0.81 m
team   ◆  claim   duck-2 (0.81 m)
duck-0  •  result  search_scan PREEMPTED: duck-0: role change to YIELD (2.2 s sim, 0.1 s wall, 23 intents)
duck-2  ■  end     stopped after 5 steps
```

That is a real `--seed 3` run, trimmed. The duck that wins the claim keeps searching, and the
two that lose are preempted mid-verb and yield, which is the moment a team is hardest to
read from `team.jsonl` alone. `PREEMPTED` is its own outcome rather than an error, because
a role change is the coordinator working, not a fault, and it wears the glyph for *ended early
on purpose* rather than the one for a failure.

The `auction`, `claim` and `verdict` lines are the words the GIF captions use, so a line on
screen and a frame in `run.gif` say the same thing about the same moment.

Each robot's `robots/<name>/transcript.jsonl` is its own record and gets every event whether
or not anyone is watching, exactly as a solo run's transcript does. `team.jsonl` keeps the
coordinator's story as it always has, under its own names, so nothing is written twice.
`roboglot log <run>` replays those records afterwards, one member's transcript in full
after another rather than interleaved, and it does not read `team.jsonl`, so no `team`
line appears in a replay. The screen is kept once, at the run root, as `terminal.txt`: the
columns in the order they actually arrived, which is the one reading a replay cannot give
back.

With a real provider the planner's one LLM call is narrated under `team` and recorded
in `team.jsonl` as `llm_request` and `llm`. With `--llm fake`
there is nothing to narrate: the planner short circuits before it reaches an LLM.

`--no-log` or `ROBOGLOT_LOG=0` removes the views and leaves every record intact.

## The shared clock

Sim time is a shared resource: the world advances one tick only while every participant
(each duck and the coordinator) is asleep, and it freezes while anyone thinks. A slow LLM
therefore costs zero sim time, and with `--llm fake` and a fixed seed a team run is
reproducible. Wall clock heartbeat scheduling is the one nondeterministic input, and it
only influences failure path timing, as in solo runs.

## Which robots can join

The coordinator knows the **Microduck** on `sim2d`. Any other adapter is refused when the run
starts, with the names it does know, and so is a stored team whose members are not all
`microduck:sim2d`. That is a limit of `roboglot/team/runner.py`, not of the robots. It also
needs that duck installed at all. `--team N` is N simulated Microducks and nothing else, so
without `roboglot-microduck` the run refuses before anything connects, says that is what a
coordinator team is, and names `roboglot[microduck]` as the install.

So a role with physical `needs` validates and its matching is tested here, but no coordinator
team Roboglot can start has two different bodies in it to match. A team of two different bodies
is what the [pilots](#the-pilot-team) are for, and that kind uses no roles.

## Status and future work

**The pilots**: one demo, `team-hello`, on `mock` and `sim2d` bodies with the scripted pilot.
Mixed adapters work and are tested; `tell` has been seen by no real LLM; nothing has run on
hardware.

**The coordinator**: sim only, one choreography, `team-kick` (ducks), 10 of 10 seeds with
scripted pilots and ground truth checks. The acoustic channel stays theatrical (a quack marks
the sighting; Wi Fi would carry the real data).

**Both**: nothing multi robot has run on hardware. An MQTT bus implementing the same `Bus`
protocol exists ([guides/lan.md](lan.md)), library only and tested on a fake broker, and carries
`TALK` like every other kind. A coordinator team across machines also needs a clock across
machines, which is future work; a pilot team needs no such clock and has simply never been
tried across two. See [adapters/status.md](../adapters/status.md) for the wider honesty table.
