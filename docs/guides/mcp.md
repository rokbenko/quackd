# Pilot your robots from Claude (MCP)

`roboglot serve-mcp` exposes a robot, or a team of them, as
[Model Context Protocol](https://modelcontextprotocol.io) tools over stdio. Claude Code or
Claude Desktop becomes the pilot; Roboglot's executor still sits between the LLM and every
robot (allowlist, budgets, confirm gates, heartbeat), one executor per robot.

**In MCP mode Roboglot selects no LLM at all.** `serve-mcp` takes no `--llm`, it never consults
the LLM catalogue the CLI ships, and `ROBOGLOT_LLM` is ignored
here even when it is set — because the client's own LLM is the pilot. Whichever LLM you are
chatting with in Claude Code or Claude Desktop is the one flying the robot, and Roboglot contributes
the robot, the verbs and the executor rather than a choice of brain. Everything about `--llm`,
`roboglot list-models` and the catalogue belongs to `roboglot run`.

Works against a mock with no hardware at all: `microduck:mock`, the Microduck's in-memory test
double, which is what the configs below serve. The mock is the Microduck's own, so
`roboglot[microduck]` is the whole install, and a bare `uvx roboglot` has no robot in it to
serve. A mock shows the tools, the contract and the gates working, not how a robot behaves: its
camera sends grey frames, so there is no ball to find, and its pose is scripted.

## Tools

Nine `robot_*` tools. `robot` is the name from `--robots name=<adapter>:<backend>`, or a
registered robot's name when the team came from `--team NAME`; omit it (or pass `null`) to
address the default robot, which is the only robot when there is one, else a stored team's
first member, else the first Microduck, else the first declared.

| Tool | What it does |
|---|---|
| `robot_list` | Every robot this server fronts: name, adapter, backend, vendor, model, embodiment, mobility, manifest id and digest, its `datasheet` as data and as one paragraph of `datasheet_text`, loaded contract, health and which one is the default. Call this first. |
| `robot_list_verbs(robot?)` | That robot's verbs from its own manifest: params, safety class, `canonical` name and `aliases`, whether it is `core`, whether it is `before_verdict` (it looks, speaks or brakes, so it runs before the pilot has judged the task), and whether its current contract allows it. |
| `robot_assess_task(robot?, verdict, reason, limits_consulted?, estimates?, needs?)` | Your verdict on whether that body can do the task, judged against the datasheet in its `robot_list` row: `feasible`, `infeasible` or `uncertain`. Required before the first verb that moves the body, and `robot_run_verb` refuses anything that does until you answer (the `verdict` gate). An `infeasible` answer names, in `could`, the robots here whose datasheets meet what the task needs, so it can be handed over. `uncertain` stays pending: there is no terminal to ask on, so ask the person you are chatting with and answer again. Answering again replaces the earlier verdict, which is how an `uncertain` is cleared once you have asked, and is also why an `infeasible` here is not the end of the session the way it is the end of a `roboglot run`. A `feasible` whose `needs` that robot's own datasheet does not meet, or does not publish, is refused before it is recorded and names the need. The exceptions are the four [concepts/safety.md](../concepts/safety.md#when-a-feasible-verdict-contradicts-itself) lists: a zero and a `none` ask for nothing, and a body that publishes no terrain, or does not move, meets `indoor_flat`. A `work_height_m` is not held against a robot's own verdict when its sheet publishes no working height band, though such a robot is still left out of `could` for a task that names one. When a need is refused: answer `infeasible` if that need decides the task, `uncertain` if a person could know the figure, or correct a need you asked more of than the task turns on. Such a refusal withdraws the verdict that stood before it, and so does an answer refused for its shape, a need outside the datasheet's words or one that is not a number: nothing that moves the body runs until you answer again, and the second kind of refusal ends `the verdict recorded before this one is withdrawn, so nothing that moves the body runs until you assess again` where a verdict stood. Moves nothing and costs no step. |
| `robot_run_verb(robot?, verb, params?)` | Run any verb through that robot's executor (`search_scan`, `go_to` or its alias `walk_to`, `kick`, `gaze`, `express`, …). Refusals come back as `ok: false`, and a verb the manifest does not list is a refusal too. The result carries a `log` list of what happened behind it (see below). |
| `robot_observe(robot?)` | The `observe` verb through the executor (it counts against the budget), returning the camera frame as a PNG image, a one-line detection summary, and the log as a final text block. A robot with several cameras returns one image per camera, each preceded by a `camera <name>:` line, and the detection summary is the primary camera's alone, because a bearing measured through one lens means nothing through another. A camera that gave nothing this step costs its own picture and nothing else. |
| `robot_say(robot?, text)` | The `say` verb, with a `log` like `robot_run_verb`. No robot here has text to speech, so it degrades: one of seven tones on a Microduck, the same seven tags sent to its shim on a Huebot, one of the duck's own sounds on an Open Duck. A robot without a `sound` intent refuses with `ok: false`. |
| `robot_load_glotfile(robot?, path)` | Adopt a `.glot` contract on one robot: its `requires` (or, for `glot: 0`, its allowlist) is checked against that robot's manifest first, then allowlist and budgets are enforced for that robot only; the body is returned as instructions. A file with `play: true` also returns `play`, Roboglot's paragraphs on playing a part, worded for a chat, and its `note` says to read them before the verdict ([guides/play.md](play.md#over-mcp)). Team tasks are refused. `path` is a file, of any name, or a bundled task's name, as `roboglot run` takes one. A file larger than 65536 bytes is refused before it is read, and a frontmatter that is not valid YAML is refused with the error's kind and its line, `frontmatter is not valid YAML: ScannerError at line 2 of the frontmatter, column 23`, and nothing of the file's text. |
| `robot_recall(robot?)` | What that robot remembers from earlier sessions and runs: the notes a pilot saved and how its recent runs ended ([guides/memory.md](memory.md)). On a body that says where it is it adds `places`, every place it knows read against where it stands, and each note saved where it knew says how far that was from them. Costs no step; the server's instructions ask the LLM to call it early. |
| `robot_remember(robot?, text, tags?)` | Keep one short fact for future sessions on that robot. Moves nothing, costs no step; the same sentence twice updates the old note. On a body that says where it is the note keeps where it stood, read off the body. Off with `--no-memory`. |

Without a loaded `.glot`, every verb that is not `dangerous` is allowed and the session runs
on a default budget of 40 verb steps and five minutes, counted from when its robot connected.
An arm's `manipulate` runs segments of 10 s each, 120 s of them in all, the defaults a task
file's `policy` section would otherwise set
([reference/glot-spec.md](../reference/glot-spec.md#policy-v3)). Load one to get the guard rails and
the task's own budget. Contracts, budgets and abort flags are per robot: loading a contract on
`duck` changes nothing for `arm`.

A team can also come from the registry, which is the same thing by another door:

```bash
roboglot serve-mcp --team kitchen
```

Every member comes from the registry with its own address, token and camera, rather than one
value applied to all of them, and each keeps its memory under its registered name
([guides/registry.md](registry.md)). `--team` refuses `--robot`, `--robots`, `--address`, `--token`
and `--camera-url`, because the registry already answers all five.

A team **task file** is still refused here: a pilot team needs one LLM per robot rather than
the one LLM driving this session. Run it with `roboglot run` instead ([guides/team.md](team.md)).

## What the log shows

Every call to `robot_run_verb`, `robot_observe`, `robot_say` and `robot_assess_task` comes back with a `log`:
a short list of plain lines saying what happened behind it, whether or not the call ever
reached the executor. A call the session refused still says why it was refused.
`robot_observe` appends the same thing as a final text block headed `log:`, because that tool
answers with content rather than a dict. The five tools that never touch a robot carry none,
because there is nothing behind the scenes to show.

That key was called `trace` until 0.11, and it is the one place the old spelling went
outright rather than being carried beside the new one for a release. A person who typed an old
flag that release got a yellow line naming the new one, and the command still ran. An LLM gets
no such line: it learns the key from the tool description on every call, which is the only
place it could be told, and a result carrying both spellings would hand it a second copy of up
to thirty lines every time it used a tool, paid for in context on each one. The flags have one
spelling again as well, since 0.12.

Every log opens with a `tool` line and closes with a `done` line. Those are the call's own
envelope, recorded as `tool_call` and `tool_result`: what the client asked for, and what it
cost in seconds and budget.

```
tool    robot_run_verb verb='move', params={'vx': 0.15, 'duration_s': 1} on microduck
verb    move(vx=0.15, duration_s=1) from mcp
->      move x10 over 0.9 s (vx 0.15, vy 0, wz 0)
->      stop
<-      move ok: walked vx=0.15 vy=0.00 wz=0.00 for 1.0s (0.0 s, 11 intents)
done    ok in 0.0 s budget: step 1/40, LLM calls 0/40, 0.0/5 min
```

A `gate` line appears whenever a rule fires, and says which one: `gate allowlist: refused
verb 'kick' is not in this task's allowlist (quack, walk, stop)`. That is the difference
between a refusal you can act on and an `ok: false` you cannot. The executor's gates are
listed in [concepts/architecture.md](../concepts/architecture.md), `verdict` among them: it refuses
every verb that moves the body until `robot_assess_task` has recorded a feasible answer, and loading
a `.glot` starts a new task and shuts it again. Two more belong to the server itself:
`session_aborted` when that robot's session has already aborted, either its heartbeat gave
up or a contract's `abort_when` fired, and every further call except `stop` is refused,
and `no_sound_intent` when `robot_say` reaches a body with nothing to say it with.

Over MCP the pilot is the client, so the LLM's own reasoning and token counts live in
Claude Code or Claude Desktop, not here. So does the LLM itself: Roboglot never chose one, never
read a key for one, and never counted a token against one. There is no cost figure here
either, and for two reasons rather than one: those tokens are billed to whichever subscription
or key is driving the session, which makes them the operator's to account for and not
Roboglot's, and an MCP session has no run boundary to total anything over, since it begins when
the client spawns the server and ends whenever the chat does. Roboglot shows what Roboglot can see.

The seconds on a verb's `<-` line and on its `done` line are the wall clock's. A burst's `over`
and the budget's minutes are counted on the robot's own clock, which on a mock moves only as its
verbs sleep: the burst above spans 0.9 of the mock's seconds, in a call that took under a tenth of
a wall second.

The list is capped at thirty lines per call so a long approach does not fill the LLM's
context. The uncapped version goes to the server's stderr, one block per call written when
that call ends, so two calls at once stay two readable blocks rather than an interleaving.
Anything belonging to no call, such as the heartbeat noticing the link is gone, is written
the moment it happens. Stderr is
`%APPDATA%\Claude\logs\mcp-server-roboglot.log` on Windows and `~/Library/Logs/Claude/` on
macOS. Turn it all off with `--no-log`, or with `ROBOGLOT_LOG=0` in the server's
environment, which is the switch to reach for in a desktop config because it needs no change
to the command line. That drops the `log` key from the four results that carry one and the
blocks from stderr, leaving the executor's own two lines per verb, the call and what came
back. Under `roboglot run` the same flag only decides what you watch, because the run directory
gets its log either way; an MCP session writes no run directory, so here there is no copy
kept anywhere else.

## Claude Code

Verified against the current docs (`code.claude.com/docs/en/mcp`, 2026-08). Two options.

**1. One command** (local scope by default; `--scope project` shares it via `.mcp.json`):

```bash
claude mcp add roboglot -- uvx --from "roboglot[microduck]" roboglot serve-mcp --robot microduck:mock
```

**2. Project file** — commit a `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "roboglot": {
      "command": "uvx",
      "args": ["--from", "roboglot[microduck]", "roboglot", "serve-mcp", "--robot", "microduck:mock"]
    }
  }
}
```


(No `"type"` key: Claude Code reads an entry with `command` as a stdio server.)

`--from "roboglot[microduck]"` is what puts a duck in the environment `uvx` builds. The `roboglot`
on PyPI is the loop and no robot, so a bare `uvx roboglot serve-mcp --robot microduck:mock`
never reaches the tool list: it stops at `adapter 'microduck' needs an extra`. Swap the extra
for the body you own, `roboglot[lerobot]` or `roboglot[rosbridge]` or any of the ten, and name
that robot after `--robot`.

Then in Claude Code: *"List the duck's verbs, then walk it forward for a second and quack."*

> **If you are working on Roboglot itself**, this repo ships its own `.mcp.json`, and it says
> `uv run --no-sync` rather than `uvx` on purpose: it serves the code in your working tree
> instead of the last release, and `--no-sync` keeps the launch from re-syncing the
> environment while the previous server still holds `Scripts/roboglot.exe` open on Windows.
> Run `uv sync --extra dev` once first, which installs the core and all ten adapters as
> editable workspace members. That is also why the repo's file carries no `--from`: every
> robot is already in that environment. A server that is already running keeps the tools it
> started with, so after changing a verb or upgrading Roboglot, restart it (`/mcp` in Claude
> Code, or a new session) or you will be calling the old build.

## Claude Desktop

Edit `claude_desktop_config.json` — Settings → Developer → *Edit Config*:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "roboglot": {
      "command": "uvx",
      "args": ["--from", "roboglot[microduck]", "roboglot", "serve-mcp", "--robot", "microduck:mock"],
      "env": {"ROBOGLOT_LOG": "1"}
    }
  }
}
```

(`ROBOGLOT_LOG` is `1` by default and is shown here because `env` is where you would set it
to `0`. Roboglot reads a `.env` from the folder it is run in and from beside its own install,
and a desktop-spawned server has no shell and is started in whichever directory the client
chose, which is usually not yours. So `env` here is the reliable way to set anything for this
server, and a `.env` may or may not be found depending on the client. It has not been checked
against any particular one.)

Restart Claude Desktop completely. The duck appears under *Connectors → Manage connectors*.

> **Windows note.** Desktop apps often do not see your shell `PATH`. If the server does not
> start, replace `"uvx"` with its absolute path (`where uvx` in a terminal, e.g.
> `C:\\Users\\you\\.local\\bin\\uvx.exe`). Server stderr lands in
> `%APPDATA%\Claude\logs\mcp-server-roboglot.log` (macOS: `~/Library/Logs/Claude/`).

## Why not from my phone yet

Both setups above are *local*. `roboglot serve-mcp` speaks the `stdio` transport and nothing
else (`mcp.run(transport="stdio")` in `roboglot/mcp_server.py`), so the client spawns it as a
subprocess on the same machine and talks to it over stdin and stdout. It lives exactly as
long as that process does.

A Jetson does not change where the server runs. `serve-mcp` never runs on the board: the client
spawns it on your laptop as always, and `--host` names the board, the way it does for
`roboglot run` ([guides/jetson.md](jetson.md)). `serve-mcp` takes `--host`, `--host-token` and
`--detector` as `run` does, so the board's camera joins the robot and the board's detector can
read its frames, and `robot_list` and the server's startup log name the detector in use. A
board whose daemon does not answer refuses the server before it starts. `--host` and
`--detector` are for one robot: with `--robots` or `--team` either is refused, because one
board is one camera and one detector and a team has several bodies. On 2026-11-15
`roboglot serve-mcp --robot microduck:mock --host 127.0.0.1:19874` answered an `initialize`
handshake against Roboglot's daemon serving a board made of files and a fake camera, not a
Jetson, and `robot_list` named `color_blob`, the colour detector on this machine: a mock keeps
it, and that daemon was started with `--no-detect`.

Reaching it from the Claude mobile app would need a different shape: a remote connector,
which is a server that runs persistently somewhere reachable over the network, with its own
address and its own authentication. Roboglot is not that today, and there is no flag that makes
it one. Four things would have to land first:

- an HTTP or SSE transport option in `serve()`, instead of `stdio` only,
- a long-lived process rather than one spawned per client session,
- a reachable address for it (a tunnel, or a small always-on host next to the robot),
- authentication and session isolation, which the server does not have because it assumes
  one trusted local pilot.

None of that is built. It is on the roadmap, and it is worth wanting: the robot is the thing
you would most like to poke at from the sofa.

## Useful flags

```
roboglot serve-mcp --glotfile find-and-kick            # start with a contract loaded
roboglot serve-mcp --glotfile find-and-kick --lock-glotfile   # and refuse a file that asks for more
roboglot serve-mcp --dry-run                           # intents are logged, never sent
roboglot serve-mcp --yes                               # allow confirm-gated verbs (no terminal to ask)
roboglot serve-mcp --robot microduck:jsonrpc --address tcp://127.0.0.1:9870   # real robot, experimental
roboglot serve-mcp --robots duck=microduck:mock,arm=lerobot:mock              # a team: robot_* tools, one executor each
roboglot serve-mcp --team kitchen                                            # the same, from a stored team
roboglot serve-mcp --no-log                            # no result carries a log of what happened
```

A line that names no robot serves the one adapter installed, on its `mock`, and is refused where
several are installed.

`--policy-url` names the policy server a real LeRobot arm, `lerobot:real`, hands its `pick` and
`manipulate` segments to (`roboglot policy serve`, [guides/policies.md](policies.md),
[the arm's page](../adapters/lerobot/README.md#a-policy-in-a-process-of-its-own-roboglot-policy-serve)),
with `--policy-token`, or else `ROBOGLOT_POLICY_TOKEN`, or else the token file the server wrote.
It is asked what it serves as the server starts, and one that does not answer refuses it. So
does a policy the arm's connect finds does not fit it, before any torque, and `serve-mcp` then
says the connect's sentence and exits 1, as `roboglot run` does, rather than printing a traceback
that leaves the client only a closed connection. `--accept-other-frame` lets a policy learned on
an arm calibrated another way connect, with every goal it answers still clipped to this arm's
travel, as on `roboglot run` ([the arm's page](../adapters/lerobot/README.md#whether-the-policy-fits-the-arm)), and
`--no-async-chunks` has the arm wait for every chunk the policy answers with, as there. Both
verbs are confirm gated, so a client can call them only on a server started with `--yes`, and
both are refused with `--robots` or `--team`, since one policy server drives one arm. A task
file loaded with `robot_load_glotfile` holds `manipulate` to its own `policy` section: its listed
instructions, its segment and its total, and a second file loaded after it is held to its own.
The seconds of segments the session has run still count under each file it loads, those run
before the first file was loaded among them. One segment runs at a time: a `pick` or a
`manipulate` sent while one runs is refused, so wait for it or send `stop` first, and a call
the client cancels mid-segment is charged the seconds its segment ran. `--controller` is
refused: over MCP the client is the pilot, and `roboglot run --controller vla` needs a person at
a terminal to say whether the arm did the task
([the arm's page](../adapters/lerobot/README.md#a-scripted-pilot-that-a-person-judges---controller-vla)).

A session's budget counts its minutes from the robot's connect, on the clock the robot keeps,
which on a mock is the mock's own: the `done` lines and every result's budget read them against
`max_minutes`, and the session's verbs are refused once they pass it.

> [!NOTE]
> `--decision-llm` is not among these, on purpose. Over MCP the pilot LLM *is* the client, so
> Roboglot has no think path to put a stepper in front of: it hands out tools and enforces the
> contract, and the deciding happens in Claude. That flag and its two companions,
> `--decision-url` and `--decision-mode`, belong to `roboglot run`, where Roboglot owns the loop
> ([guides/decision-llms/README.md](decision-llms/README.md)).

## Driving a real LeRobot SO-101 arm from Claude

That one has a page of its own, because an arm wants a calibration, a rest pose and a Python
3.12 environment in place before a client is pointed at it, and because a session moves the arm
at both ends on its own:
[Part 2 of the first run](../adapters/lerobot/first-run.md#part-2-from-claude-over-mcp).

## Driving a real Open Duck Mini from Claude

A real Open Duck Mini needs three flags, because its camera is a separate
HTTP service on the robot and its bridge wants a token. Tunnel both ports rather than
exposing them (`ssh -L 9871:127.0.0.1:9871 -L 9872:127.0.0.1:9872 your-pi`), then:

```json
{
  "mcpServers": {
    "duck": {
      "command": "uvx",
      "args": ["--from", "roboglot[open_duck]", "roboglot", "serve-mcp",
               "--robot", "open_duck:bridge",
               "--address", "tcp://127.0.0.1:9871",
               "--camera-url", "http://127.0.0.1:9872/snapshot.jpg"],
      "env": {"ROBOGLOT_DUCK_TOKEN": "the token from /etc/roboglot/duck-bridge.token"}
    }
  }
}
```

Nothing on the duck sees a fall when its runtime reports no fall signal, and it has no
`stand_up`, so `roboglot run` and `roboglot drive` ask whether somebody is watching before it
walks. This server asks nobody and lets Claude walk it as before, and says so instead. Once the
duck has connected, under the start-up line, stderr has:

```
roboglot-mcp WARNING open-duck-01: nothing on this robot detects a fall, and it has no way to get up. Roboglot will not know it is down, no verb will refuse because it is, and this task can make it walk. Keep it on a stand with a hand near the power switch, and watch it. Nobody is asked here whether they are watching it, so somebody must be.
```

The server's instructions are written before the duck connects, so they cannot say it. A
`feasible` or `uncertain` reply to `robot_assess_task`, which every move waits for, carries the
same words in `warning`, ending `tell the person you are chatting with before anything moves
it.` A task that cannot make the duck walk says neither, and a team server says it for each
member whose body is like that.

The verbs Claude is offered come from what that duck reports at connect, not from the
description, so a duck with no camera or no head simply has fewer. Nothing has been run
against a real duck: [adapters/open_duck/README.md](../adapters/open_duck/README.md) and its
[bring-up checklist](../adapters/open_duck/hardware-checklist.md).

## The two-minute script

1. `claude mcp add roboglot -- uvx --from "roboglot[microduck]" roboglot serve-mcp --robot microduck:mock` (≈20 s, first run downloads Roboglot and the duck)
2. Open Claude Code in any folder and ask: **"Use the Roboglot tools. List the verbs, grab a frame, then walk forward for a second and kick. Quack when you're done."**
3. Watch it call `robot_list` → `robot_list_verbs` → `robot_observe` → `robot_assess_task` → `robot_run_verb("move")` → `robot_run_verb("kick")` → `robot_say`. The frame is the mock's grey one, with nothing in it to find.
4. Ask: **"Load tasks/patrol-and-quack.glot and follow it."** — now the allowlist and budgets apply, and the LLM has the task body as instructions.

## Safety in an MCP session

- One heartbeat per robot runs for the whole session; if a robot's transport fails, that
  robot has already been stopped and every later call to it is refused with a
  `session_aborted` gate. `stop` is the exception and is never refused, because an aborted
  session is exactly when a pilot reaches for the brake. The other robots in the team
  carry on.
- A `stop` called while another call is still running a verb ends that verb before its next
  twist, and so does one called while the verb is still reading the body's state. `move`,
  `go_to`, `search_scan` and `approach_and` read it as Roboglot ships them. Each comes back failed,
  saying why, and for a walk or a scan how far it got, and `abort_when` does not count it as a
  failure. MOSS's own verbs read it too, and so do the arm's `move_joints`, `gripper` and `place`,
  and what the XLeRobot, the AlohaMini and the ToddlerBot bring, and `stand_up` on the Microduck and
  the Huebot, and each is marked the same way. So do `kick`, `sit` and `stand` on those two, `grab` on
  the Microduck and `express` on the Open Duck Mini, whose answer says the robot plays out what it was
  sent, since a stop ends no skill on those robots. On the XLeRobot and the AlohaMini a stop holds
  the arm where it stands, where a reading from after the stop began says where that is, and otherwise the
  arm goes on to the goal it was sent, and the answer says which. A verb that moves the body does not
  start at all once a stop has overtaken it, which it can while it reads the body's state
  ([ADR-0067](../adr/0067-a-stop-ends-the-verb-beside-it-and-a-newer-command-ends-the-one-it-finds-moving.md)).
- Every robot connects at startup, in the order given; if one cannot, the server stops
  and disconnects the ones that did, rather than fronting a team with a hole in it.
- A robot with a recorded rest pose, which today means a LeRobot arm, is driven to that pose as
  part of connecting, before the heartbeat starts, so the arm this session is handed is the arm
  the last one put down rather than wherever it was left. An arm that cannot get there is not a
  robot this session fronts: the connect fails with `the arm did not reach its rest pose`, and
  by the rule above the server stops. A pose recorded past the travel the arm's calibration
  recorded is parked at the edge of that travel, which counts as getting there, and the server
  logs once which joint is free to settle the rest of the way
  ([adapters/lerobot/README.md](../adapters/lerobot/README.md#a-pose-past-the-travel)). The same
  move runs again when the session closes, between the `stop` and the disconnect, which is the only
  window where putting the arm down changes whether it falls once torque is released. Where the arm
  is not at that pose, torque is left on and it holds itself up instead of dropping, and
  [concepts/safety.md](../concepts/safety.md) has the line it prints and what to do about it. A
  session started with `--dry-run` moves nothing at either end. A real arm with no pose recorded
  is refused before anything connects, `--dry-run` or not, since at the end there would be
  nothing to fold it to and it would be left holding itself up wherever it stopped, and a mock
  with none ends the way it always did. Recording one is `roboglot robot rest-pose NAME`
  ([guides/registry.md](registry.md)).
- When the session ends, because the client closed it or because the client died with a call in
  flight, every robot is sent its `stop` first, all at once, each waited for as long as that
  robot's own `stop` verb is given and left to go on by itself after that. Only then is each
  robot put down in turn: a pause it granted a robot it met is waited out, an arm is folded, and
  its transport is closed. A stdio client ends the server a fixed grace after it closes the
  server's stdin (the MCP Python SDK's own client waits two seconds), and nothing runs in a
  process killed outright,
  which is why the stops go first: a kill during a pause or a fold finds every robot already
  sent its stop. Run from a terminal, a second Ctrl-C keeps a fold that has not begun from
  starting and stops one that has, holding the arm where it is and saying so, and every robot's
  transport is still closed and its line said.
- MOSS's `explore` runs for up to eight minutes, and a client may wait less than that for one
  tool call: ask for fewer minutes than your client waits, and a `stop` from beside it ends it
  at the check it makes every second. It is confirm-gated, so a server reaches it only with `--yes`.
- Confirm-gated verbs are **refused** unless the server was started with `--yes`, because
  there is no terminal to ask on. The refusal text tells the LLM why. A `dangerous` verb is
  refused even then, since `--yes` is nobody being asked: it runs only from a terminal, where a
  person answers it.
- What stops the body when Roboglot goes quiet is the body's job, not the server's, and it differs per robot. Read [concepts/safety.md](../concepts/safety.md) before an MCP session drives hardware.
