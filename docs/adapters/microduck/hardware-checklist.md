# Microduck: the first hardware run

Nobody has run quackd against a physical Microduck. If you have one — your own, or somebody
else's for an afternoon — this page is the order to do it in. Every step that can go wrong has an
abort condition, and they are ordered so that the duck's feet do not touch the ground until
step 9.

**This is a robot you are probably borrowing.** Nothing here installs anything on it, stops any
of its services, or needs `sudo`. Step 6 has an alternative that does, and it is marked, and it
is the one to skip unless whoever owns the duck has said yes.

**The gamepad is the real e-stop.** Upstream is explicit that `padd` has authority and quackd
does not arbitrate. Somebody should be holding it. quackd's `stop` is a request over a socket,
and step 8 is where you find out what happens when that socket is not there.

## 0. Rehearse in the physics simulator first

`microduck:mujoco` is upstream's own model walking on upstream's own `alpha_walking.onnx`, so
the two tasks you will run on the robot run against it unchanged:

```bash
uv pip install 'quackd[mujoco,anthropic]'   # the duck's package with its simulator, and your provider
quackd run microduck-lookout --robot microduck:mujoco --llm anthropic
quackd run find-and-kick --robot microduck:mujoco --llm anthropic
```

`quackd[mujoco]` is `quackd-microduck[mujoco]`, the duck's own package plus its physics
simulator, so this one install is also what puts `microduck:jsonrpc` on the laptop for every
step below. A plain `uv pip install quackd` carries no robot at all and would get you to
step 1 with nothing to connect with.

The first run fetches the model and the policies into `~/.quackd/cache`, so do that before you
are standing next to somebody else's robot rather than on their Wi-Fi.

What it rehearses is the pilot, your provider, each task's allowlist, and reading a run's log
while it happens. It touches nothing below: no socket, no `hello` handshake, no `robot.health`,
no camera transport and no deadman. Four skills there are quackd's own stand-ins rather than the
robot's, and say so in `state.extras.assumptions`: `kick` and `grab` use the cartoon's contact
rules, `sit` is refused, and a fall is recovered by resetting the model. The gait floor in the
simulator was measured on one machine with the XML's own actuators, and upstream deploys a
different actuator model and, from daemon-v0.13.0, a different network too: a robot walks and
stands on `velstand.onnx`, where the simulator runs `alpha_walking.onnx` and `alpha_stand.onnx`.
So the speeds that walk there are not the speeds that will walk here.

## 1. Reach the daemon at all

`robotd` listens on a unix socket, not a port, so from a laptop it is an ssh forward:

```bash
ssh -L 9870:/run/robotd.sock radxa@<duck>
```

Windows cannot open a unix socket at all, so this is not optional there — quackd says so and
names this command if you try.

> Abort if ssh does not come up. Everything below is this tunnel.

## 2. What the robot says it is

```bash
quackd doctor --robot microduck:jsonrpc --address tcp://127.0.0.1:9870
```

Read three things: that the handshake passed, what `robot.health` said, and the verb list. The
`robotd` row under health should name the release, the API version and the revision the robot
answered with.

> **Abort if the handshake is refused.** quackd refuses a robotd older than API v23, the oldest
> it has read end to end, and one whose `hello` names no version, and it prints why. A robot on
> any other version is served, and when that version is not the one in
> [`docs/adapters/microduck/README.md`](README.md), `doctor`'s advice says so. If it says the
> robot is newer than what quackd read, go through the robot's own `duck-ipc-proto` version
> notes, above `API_VERSION` in its `lib.rs`, for anything touching the calls quackd makes before
> going on. The field shapes matter more than the number, and a call whose shape moved fails the
> verb that sends it, naming the call.

> Read the `pickup_detection` row too. `False` means this robotd is older than its pick-up
> detector, so nothing tells quackd when somebody is holding the duck: stop it before you pick it
> up, every time.

> Abort if `healthy` is false, or if the battery is low. A duck that browns out mid-walk
> teaches you nothing.

## 3. Does quackd actually see the robot?

```bash
quackd run microduck-lookout --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --llm fake --dry-run --no-memory
```

Then read `report_state` in the transcript. **`posture` must not be `unknown`.**

`unknown` means quackd cannot vouch for the duck's posture, and `extras.posture_reason` says
why. If it says no `robot.state` frame has arrived, that the newest one is old, or that it
carries no safety block, everything that decides whether the duck may walk is reading nothing:
`fallen` is `false` because nobody is looking, not because the duck is upright. Otherwise
robotd is reporting a policy label other than `walk`, `stand` or `sit`, a limp duck, or a duck
somebody is holding, and the reason names which. quackd refuses to walk in either case on
purpose.

> Abort if posture is `unknown`. Check that `robot.subscribe` was accepted — `report_state`
> carries its answer in `extras.subscribed`, including the robot's real skill list.

## 4. A dry run moves nothing

The command above already had `--dry-run`. Watch the duck while it runs: it should not twitch.

> Abort if anything moves. `--dry-run` is the promise the rest of this page rests on.

## 5. Sound, which moves no joints

```bash
quackd run microduck-lookout --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --llm fake --no-memory
```

`say` and `quack` map text to one of seven duck tones. Nothing here moves a leg.

> Abort if the robot does not make a sound and `robot.sound` was accepted — something is
> answering that is not the robot you think.

## 6. Video

Frames are the reason most people are doing this, and upstream serves none over `robotd`'s
socket. The camera is an H.264 WebRTC track from `mediad` and nothing else.

```bash
ssh -L 8443:127.0.0.1:8443 radxa@<duck>          # in another terminal
quackd doctor --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --camera-url webrtc://127.0.0.1:8443
```

`doctor` fetches a frame and prints its size. Needs `quackd[microduck-camera]`, which is the
same duck package with its WebRTC camera: `uv pip install 'quackd[microduck-camera]'`.

This takes the robot's one media session, so the browser console cannot be open at the same
time. It installs nothing on the duck.

> Abort if no frame arrives. Check `mediad` registered a producer — if its pipeline never
> reached PLAYING there is nothing to connect to, and quackd says so rather than hanging.
> ICE on a shared office network is the next thing to suspect.

**The alternative, only with permission:** a snapshot server on the duck itself needs
`sudo systemctl stop mediad` first, because `mediad` holds `/dev/video0` for the life of its
process. That stops their console and their WebRTC. Do not do this to a borrowed robot without
asking.

## 7. The head, on a stand

Put the duck on a stand. `gaze` runs upstream's gaze IK through `robot.look`; the answer says
whether it clamped.

> Abort if the head moves somewhere you did not ask for. The IK is upstream's, and a clamp is
> expected — an unexpected direction is not.

A robot on `daemon-v0.16.1` or later glances around on its own once it has stood still for two
seconds with nobody steering its head. After a `gaze`, quackd sends the head where the gaze put it
again every second, so the head should stay where it was pointed for as long as you watch, and
`report_state` should show what is held in `extras.gaze_hold`, and about the bearing you asked for
in `extras.head_yaw_deg`. A `stop` ends the hold, and two seconds later the head may start to look
around: that is robotd's, not a fault. Pose the head with the gamepad while a gaze is held, and
quackd should let go of it rather than pull it back, with `extras.gaze_released` saying
`overridden`.

> Abort if the head wanders off a gaze while `extras.gaze_hold` says it is held, or if quackd
> keeps pulling the head back from the gamepad.

## 8. Pull the tunnel out, mid-walk

**Feet still off the ground.** Start a short walk, and kill the ssh tunnel while it is moving.

What should happen: `robot.move` notifications stop arriving and `robotd`'s deadman zeroes the
velocity. quackd's heartbeat fails, says it is sending a stop, and aborts the run — and it will
tell you if that stop could not be delivered, which is the honest outcome when the link is the
thing that broke.

> **Abort the whole session if the legs keep driving.** The deadman is the protection every
> step below this one depends on. Nothing else quackd does matters if it is not there.

The command, since this step never named one. `--llm fake` answers a free-form goal with a fixed script that ignores it, so it needs a real model:

```bash
quackd run --goal "walk in place with small steps, do not turn, then stop" \
    --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
    --llm anthropic --max-steps 6
```

## 9. Feet down

Now, and only now, put the duck on the floor and run a short walk. Hand on the gamepad.

```bash
quackd run find-and-kick --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --camera-url webrtc://127.0.0.1:8443 --llm anthropic --memory-dir ./duck-day
```

Start with `--no-memory` for a clean first run, then `--memory-dir` so the campaign's notes stay
out of your simulator's.

The pad in your hand fights every walk quackd sends, even while nobody touches it: `padd` tells
robotd to stand still at least ten times a second while a pad is connected and its sticks are
still, and robotd keeps the newest twist from anyone. quackd checks each walk against robotd's
own `move.requested`, so a walk the pad keeps stopping should end with
`robotd has not taken robot.move` and robotd still saying it was asked for `[0, 0, 0]`. That is
the pad's authority, which quackd does not arbitrate. Write down how often it happens: nobody
has measured it.

> Abort and let the gamepad take it if the duck goes over. Nothing quackd sends stands a fallen
> duck up on its own: `stand_up` is `robot.enable {on: true}` and it checks `safety.fallen`
> afterwards rather than claiming upright, so the recovery is `robotd`'s or it is the owner's.

## 10. Tell everyone

`microduck:jsonrpc` is 🧪 in [`docs/adapters/status.md`](../status.md) because nobody has
done this. What flips it is a `quackd doctor` output and `runs/<timestamp>-<name>/terminal.txt`,
in an issue: that file is the whole session as plain text, opening with the command that
started it and the version that ran it, so it is usually the one that answers what happened.
Send `transcript.jsonl` beside it when the question is about one record rather than the
session, which is what step 3 has you open it for. The memory file is an index of what you
tried, not evidence.

Read that first line before you paste it. The values of `--api-key` and `--token` are replaced
there, and so is a password or a credential-named query parameter in `--base-url`, `--address`
and `--camera-url`. Nothing else on the screen is ([SECURITY.md](../../../SECURITY.md)).

What is most worth writing down, because it is what nobody can check without a duck:

- whether `posture` tracked the real robot, and whether `policy` said `sit` when it sat
  (quackd assumes it does, and says so in `extras.assumptions`)
- what `extras.robotd` said the robot runs, and what `extras.move_echo` said of a walk: how
  long after a twist went out robotd's state showed it, which is the number the echo window
  (three state frames) was set without
- whether `robot.enable` brought a limp robot up, and how much it moved doing it
- whether the head held still after a `gaze`, whether `extras.head_yaw_deg` agreed with where the
  camera visibly pointed, and how long the gamepad had the head before quackd let go of it
- whether `media.detections` arrived on the control datachannel — upstream's source says it
  does and upstream's design doc says it does not, and one of them is out of date
- what `robot.subscribe` listed in `skills`, which is where a robot's real skill set lives now
