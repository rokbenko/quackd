# Huebot: the first hardware run

Nobody has run quackd against a Huebot, and nobody has seen the shim its maker is writing for it. If
you have one, this page is the order to do it in. Every step that can go wrong has an abort
condition, and the first two that send a twist test what stops the robot, with its feet off the
ground: the deadman, then `robot.stop`. It lies on a mat at step 9, and its feet touch the floor
at step 10 and not before.

**Know what stops Huebot before anything moves.** quackd's own `stop` is a `robot.stop` over the
socket, which Ctrl-C or `q` in the terminal running quackd sends. When the socket is the thing
that broke, what stops the legs is the robot's own deadman: the velocity goes to zero once
`robot.move` stops arriving for 500 ms, which Huebot's shim is asked to keep
([what the shim must answer](README.md#what-huebots-shim-must-answer)) and which nobody has
seen it do. Step 3 is where you find out. Whether Huebot has a stop of its own, a button or its
maker's app, is the maker's to say: know it, and keep it in reach.

## 0. Rehearse on the mock

```bash
uv pip install 'quackd[huebot,anthropic]'   # Huebot's package, and your provider
quackd run hello-world --robot huebot:mock --llm fake --no-memory
```

`huebot:mock` is the core's scripted body under Huebot's manifest, so it rehearses the pilot,
each task's allowlist and reading a run's log while it happens. It touches nothing below: no
socket, no handshake, no deadman. There is no simulator of Huebot's body or gait.

## Before step 1: Huebot's own behaviours off

Huebot runs behaviour code of its own, the maker wrote on 2026-10-15: needs, an LED face, coming to
your hand. That code can move the robot without quackd, and nothing quackd reads tells that motion
from its own: a robot that turned to your hand in the middle of step 3 would look like a deadman
that failed. quackd assumes the code is off whenever the shim is driven.

Switch the behaviours off the way the maker says, then see that they are off before anything
connects. With quackd not running, put Huebot on its stand, watch it for a minute, and hold a
hand out in front of it. Nothing should move.

> Abort if anything moves, and ask the maker the two questions on
> [its page](README.md#huebots-own-behaviours): how the behaviour code is suspended while the
> shim is driven, and how its motion appears in `robot.state`.

## 1. Reach the shim, and ask it what it is

The shim listens on a unix socket or on a port of the robot's own loopback, never on the
network, so from a laptop it is an ssh forward:

```bash
ssh -L 9870:/path/to/the/shim.sock <user>@<huebot>    # or 9870:127.0.0.1:<port> for a port
quackd doctor --robot huebot:jsonrpc --address tcp://127.0.0.1:9870
```

Read four rows of what the robot itself reported:

- `connected` should say `yes`, and `health` `ok`.
- `shim` names the API version, the build and the revision the shim answered `hello` with.
- `verbs` says how many of the description's verbs this robot has, and `not on this robot`
  names the rest: the four that need a camera, without `--camera-url`, and any of `sit`,
  `stand` and `kick` whose skills the shim did not list.
- `pickup_detection` should say `True`.

> Abort if the handshake is refused: quackd prints why. A shim on an API version other than 41
> is served, and `doctor`'s advice says so. One that has no `robot.subscribe` is refused as not
> robotd.

> Abort if `pickup_detection` is `False`. The shim does not report `safety.picked_up`, so nothing
> tells quackd when somebody is holding Huebot, and walks would go out to a robot in your hands.
> Ask the maker for it before going on.

> Abort if `healthy` is false, or if the battery is low.

## 2. Does quackd see the robot? A dry run moves nothing

```bash
quackd run hello-world --robot huebot:jsonrpc --address tcp://127.0.0.1:9870 \
  --llm fake --dry-run --no-memory
```

Watch the robot while it runs: it should not twitch. Then read `report_state` in the transcript.
**`posture` must not be `unknown`.** `unknown` means quackd cannot vouch for the robot's
posture, and `extras.posture_reason` says why: no `robot.state` has arrived, the newest is older
than a second, a frame carries no `safety` block, or the shim reports a `policy` other than
`walk`, `stand` or `sit`. quackd refuses to walk in every one of those cases, on purpose.

> Abort if anything moves, or if posture is `unknown`. `report_state` carries the subscribe
> answer in `extras.subscribed`, with the skills the shim listed.

## 3. The deadman, on a stand: freeze quackd, then pull the tunnel out

**Feet off the ground.** Put Huebot on a stand where its legs can move freely. This step cuts
quackd off in the middle of a walk twice, two ways. Pulling the tunnel out closes the socket, and
a shim might stop the legs when its socket closes and have no deadman at all. Freezing quackd
leaves the socket open with nothing more on it, which only a deadman stops, so it goes first.

The walk has to be long enough to interrupt, and `--llm fake` answers a free-form goal with a
fixed script that ignores it, so this needs a real model:

```bash
quackd run --goal "walk forward slowly for ten seconds, then stop" \
    --robot huebot:jsonrpc --address tcp://127.0.0.1:9870 \
    --llm anthropic --max-steps 6 --no-memory
```

**Freeze quackd.** Once the legs are moving, suspend the process running quackd from another
terminal. Its socket stays open, and nothing more is written to it:

- Linux or macOS: `kill -STOP <pid>`, with the pid from `pgrep -f "quackd run"`.
- Windows: `pssuspend <pid>`, from Sysinternals, or Resource Monitor: right-click the process on
  its CPU tab, then Suspend Process. quackd runs in a `python.exe`, and Task Manager's Details
  tab shows each one's command line once you add that column.

Suspending the ssh client that carries the tunnel does the same, and on Windows it is the easier
process to find: `ssh.exe`.

What should happen: the legs stop within about half a second of the last `robot.move`, with the
socket still open. That is the deadman, and nothing else.

Then cut the link before anything resumes. Kill the ssh tunnel, a suspended one too, with
`kill -9 <pid>` or End task, and only then resume quackd if it was quackd you froze, with
`kill -CONT <pid>`, `pssuspend -r <pid>` or Resume Process. It finds the link gone, says it is
sending a stop that could not be delivered, and ends the run. Resuming a frozen tunnel instead
would hand the robot every twist quackd wrote while it was frozen.

> **Abort the whole session if the legs keep driving while quackd is frozen.** A shim that stops
> the legs only when its socket closes leaves a robot walking whenever quackd stalls with the
> link up: a process that hangs, a laptop that sleeps with the tunnel open. Tell the maker, and
> go no further.

**Pull the tunnel out.** Bring the tunnel back, and run the same walk. Once the legs are moving,
kill the ssh tunnel.

What should happen: `robot.move` notifications stop arriving, and 0.5 s after the last one the
shim's deadman zeroes the velocity, and the legs stop. quackd's heartbeat fails, says it is
sending a stop, and aborts the run, and it says when that stop could not be delivered, which is
the honest outcome when the link is the thing that broke.

> **Abort the whole session if the legs keep driving.** The deadman is what stops a walk whose
> link dies: a tunnel that drops, a laptop that sleeps, every run over MCP. Nothing else quackd
> does matters if it is not there. Tell the maker, and go no further.

Both halves passing on a Huebot is what lets quackd's description of `huebot:jsonrpc` claim a
deadman. Until somebody reports that they did (step 11), it claims none, and quackd relies on
the shim's all the same.

## 4. `stop`, mid-walk, on a stand

Bring the tunnel back, and run the same walk, still on the stand. Once the legs are moving,
press `q` in the terminal running quackd.

What should happen: quackd sends `robot.stop`, the shim answers it, the legs stop at once, and
the next state frame's `move.requested` reads zero. The run ends there.

> Abort if the legs go on after the stop, or stop only half a second later, which is the
> deadman stopping them rather than the stop.

## 5. Sound, which moves no joints

`hello-world` on the robot, with no dry run, quacks, walks a second and quacks again, so do it
on the stand:

```bash
quackd run hello-world --robot huebot:jsonrpc --address tcp://127.0.0.1:9870 \
  --llm fake --no-memory
```

`quack` and `say` map text to one of seven tags, and the text never goes on the wire.

> Abort if `robot.sound` was accepted and the speaker made no sound: something is answering
> that is not the robot you think.

## 6. Lift it off the stand

With the legs still, lift Huebot off the stand by its body, hold it in the air, and run the same
`hello-world`:

```bash
quackd run hello-world --robot huebot:jsonrpc --address tcp://127.0.0.1:9870 \
  --llm fake --no-memory
```

What should happen: both quacks play, and the walk between them is refused with `somebody is
holding Huebot`, before any `robot.move` goes out. The fake pilot declares success whatever
happened, so read the refusal on the screen. The state in the transcript says why: `posture` is
`unknown`, `extras.posture_reason` is `somebody is holding Huebot (safety.picked_up)`, and
`extras.picked_up` is true. Put Huebot back on the stand once the run has ended.

> Abort if the legs move while you hold it, or if the walk was not refused. `safety.picked_up`
> is what keeps every walk from going out to a robot in somebody's hands. Tell the maker.

## 7. `stand_up` and each skill, on the stand

Before the feet touch the floor, run `stand_up` once, and each verb whose skill the shim listed
in step 1, still on the stand:

```bash
quackd run --goal "run stand_up, sit, stand, then kick left and kick right, then stop" \
    --robot huebot:jsonrpc --address tcp://127.0.0.1:9870 \
    --llm anthropic --max-steps 8 --no-memory
```

What should happen: `stand_up` answers `already upright` and sends nothing, since the robot reads
standing on its stand. `sit` and `stand` each send `sit_toggle`, and answer `now sitting` and
`now standing` when the state shows it 2 s later. `kick` sends `kick_left` and then `kick_right`,
and the leg it names swings. While a skill runs, the shim's `policy` names it, and the robot reads
`unknown` until it is done. A verb whose skill the shim did not list is not offered.

> Abort if a skill moves anything its name does not say, or if `posture` reads `standing` while
> a skill runs: a walk would then be sent to a robot in the middle of a kick.

## 8. The camera, if the maker says how

The shim's socket carries no picture. Once the maker says where Huebot's camera answers with a
still image, forward it the same way and pass it:

```bash
ssh -L 8080:127.0.0.1:<port> <user>@<huebot>      # in another terminal
quackd doctor --robot huebot:jsonrpc --address tcp://127.0.0.1:9870 \
  --camera-url http://127.0.0.1:8080/<path>
```

`doctor` fetches a frame and prints its size. quackd waits 5 s for the first one.

> Abort if no frame arrives: `doctor` prints the last failure under the camera's row. Without a
> camera, `observe`, `go_to`, `search_scan` and `approach_and` are not offered, and every other
> verb still is.

## 9. `stand_up` from its back, on a mat

Step 7's `stand_up` found the robot standing, answered `already upright` and sent nothing. So
the first `robot.enable` a Huebot gets would otherwise go to a robot that has fallen on the
floor. Send it here instead: lay Huebot on its back on a soft mat, with nothing within its reach
and a hand ready to take hold of it, and run `stand_up` on its own:

```bash
quackd run --goal "run stand_up once, then report your state and stop" \
    --robot huebot:jsonrpc --address tcp://127.0.0.1:9870 \
    --llm anthropic --max-steps 4 --no-memory
```

What should happen: the state reads the robot as fallen, with nobody holding it, and `stand_up`
sends `robot.enable {on: true}`. It answers `upright after` so many seconds once the robot reads
standing, or says after 10 s that it may need a hand. Write down what the robot did, and how
long it took against the 10 s quackd waits.

> Abort and take hold of it if it thrashes: legs driving against the mat without getting it
> anywhere, or anything moving harder than getting up needs. Stop it the way its maker says, and
> tell the maker before anything walks.

## 10. Feet down

Now, and only now, put Huebot on the floor and run a short walk, with a hand ready to catch it.
Its maker says the walk's balance is still being tuned.

```bash
quackd run hello-world --robot huebot:jsonrpc --address tcp://127.0.0.1:9870 \
  --llm fake --no-memory
```

quackd checks each walk against the shim's own `move.requested`: once the state goes three
frames after the shim answered a later call without showing quackd's twist, the verb sending it
stops the robot at its next twist and fails with `Huebot's shim has not taken robot.move`.

> Abort and catch it if Huebot goes over. `stand_up` asks the shim to drive again with
> `robot.enable {on: true}`, only for a robot nobody holds, and calls it upright only if it
> reads standing within 10 s. Whether Huebot gets up from a fall is not published.

## 11. Tell everyone

`huebot:jsonrpc` is 🧪 in [`docs/adapters/status.md`](../status.md) because nobody has done this.
What flips it is a `quackd doctor` output and `runs/<timestamp>-<name>/terminal.txt`, in an
issue: that file is the whole session as plain text, opening with the command that started it
and the version that ran it. Send `transcript.jsonl` beside it when the question is about one
record rather than the session. Read the first line before you paste it: the values of
`--api-key` and `--token` are replaced there, and so is a password or a credential-named query
parameter in `--base-url`, `--address` and `--camera-url`. Nothing else on the screen is
([SECURITY.md](../../../SECURITY.md)).

What is most worth writing down, because it is what nobody can check without a Huebot:

- what step 3 showed: how long after the last `robot.move` the legs stopped, with quackd
  frozen and with the tunnel pulled
- what step 4 showed: whether `robot.stop` stopped the legs at once
- whether `posture` tracked the real robot: whether `policy` said `sit` when it sat, and whether
  `safety.picked_up` was true while somebody held it and a walk was refused then
- what `extras.move_echo` said of a walk: how long after a twist went out the shim's state
  showed it, which is the number the echo window (three state frames) was set without
- what the shim listed in `skills`, and what each skill did
- what step 9 showed: what Huebot did after `stand_up` sent `robot.enable` to it on its back,
  and how long it took against the 10 s quackd waits
- whether `extras.head_yaw_deg` agreed with where the head visibly pointed
