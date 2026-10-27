# Microduck: the first hardware run

Nobody has run quackd against a physical Microduck. If you have one — your own, or somebody
else's for an afternoon — this page is the order to do it in. Every step that can go wrong has an
abort condition, and they are ordered so that the duck's feet do not touch the ground until
step 9.

**This is a robot you are probably borrowing.** Nothing here installs anything on it, stops any
of its services, or needs `sudo`, unless the account you log in with is not in the robot's
`robot` group. Step 1 says how to tell, and putting the account in the group is marked as the
one change to make only once whoever owns the duck has said yes.

**Know what stops the duck before anything moves, because the gamepad does not out-rank
quackd.** robotd walks on the newest twist from any client and ranks none above another. A pad
whose sticks nobody touches sends a stop every 100 ms, and quackd re-sends its twist as often,
so letting go of sticks you were not touching changes nothing, and a walk goes on between the
pad's stops. Push a stick and the pad sends on every tick, more often than quackd, so it takes
the walk over at once; quackd stops sending a walk once robotd's state no longer shows it, and
letting go after that stops the duck until the pilot sends its next walk. What ends a quackd walk
outright is quackd's `stop`, which Ctrl-C or `q` in the terminal running it sends, or the pad's
sit button, A unless the robot's owner moved it (`ssh microduck@<duck> robotctl pad bindings`
says), since quackd sends no walk to a duck that is not standing. Somebody should be holding the
pad all the same, and should know which of these they are reaching for. quackd's `stop` is a
request over a socket, and step 8 is where you find out what happens when that socket is not
there.

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

`robotd` listens on a unix socket, not a port, so from a laptop it is an ssh forward, as
`microduck`, the account upstream's own tools log in with:

```bash
ssh -L 9870:/run/robotd.sock microduck@<duck>
```

`<duck>` is the robot's address on your network, which upstream's `duckctl ip` prints off the
robot's Bluetooth advertisement. Windows cannot open a unix socket at all, so this is not
optional there — quackd says so and names this command if you try.

The socket is `root:robot`, mode 0660, and robotd checks nothing else about who connects, so
the forward works only for an account in the `robot` group, or for root. Check it before
anything else:

```bash
ssh microduck@<duck> id -nG
```

Upstream's group file creates the group with nobody in it, and its provisioning scripts add only
whoever ran them with `sudo`. A board provisioned as `microduck`, which is how upstream's own
example runs `scripts/provision-board.sh`, has it in the group, and one set up any other way may
not.

> Abort if ssh does not come up, or if `robot` is not among the groups it prints. Everything
> below is this tunnel, and without the group the socket refuses it. **Only with permission:**
> `sudo usermod -aG robot microduck` on the duck adds the account, from its next ssh login on.
> That is a change to somebody else's robot, so ask whoever owns it first.

## 2. What the robot says it is

```bash
quackd doctor --robot microduck:jsonrpc --address tcp://127.0.0.1:9870
```

Read three things: that the handshake passed, what `robot.health` said, and the verb list. The
`robotd` row under health should name the release, the API version and the revision the robot
answered with.

A robot running `mapd` beside robotd, which no release does yet (the `maploc-v2` branch), has a
socket of its own to forward, and doctor asks it when told where:

```bash
ssh -L 9870:/run/robotd.sock -L 9871:/run/mapd/map.sock microduck@<duck>
quackd doctor --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 --map-address tcp://127.0.0.1:9871
```

The `mapd` row says whether it answered, how many stops its map holds and whether it knows where
the duck is. Given an address nothing answers at, doctor exits 1. Everything quackd reads of
`mapd` is UNVERIFIED, and this is where it is first read off a robot.

`explore` is not offered on a real duck. On odometry alone it keeps no map, and beside `mapd` it
would plan its way round a room by the duck's body radius, which nothing publishes yet. Once one
is cited, it will turn the duck's body for every view, because nothing says whether `mapd` takes
a keyframe when only the head turns. To find out, with the duck standing still, read the `mapd`
row's stops, turn the head with `gaze left`, wait a second and read them again. One more stop
means a head turn alone is a view there, as it is in the simulators, and `explore` could look
with the head on a real duck too: say so in an issue.

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
> up, every time. `stand_up` refuses to run on such a robot, so a duck that falls is yours to
> stand up.

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

Frames are the reason most people are doing this, and `robotd`'s socket carries none. `mediad`
serves them, and the camera to start with is its own snapshot route, `GET /frame` on its console
port, one upright PNG per request:

```bash
ssh -L 8080:127.0.0.1:8080 microduck@<duck>      # in another terminal
quackd doctor --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --camera-url http://127.0.0.1:8080/frame
```

`doctor` fetches a frame and prints its size. On an alpha, expect a picture taller than wide,
720 by 1280 at `mediad`'s default quality: quackd takes that shape for an alpha's camera and
measures through the lens it assumes for one, which every state then lists in
`extras.assumptions` ([the Microduck page](README.md#getting-a-picture-off-a-real-microduck)).
On a beta, expect a wide one, which gets no lens. It needs no extra, takes no media session, and
installs and stops nothing on the duck. The route arrived in `daemon-v0.13.0`.

> Abort if no frame arrives. quackd waits 5 s for the first one, through the `503` that `mediad`
> answers while it cannot hand a frame out, and `doctor` prints the last failure under the
> camera's row: `mediad`'s own reason when its `503` never cleared, a `404`, which is a robot
> older than the route, or a refused connection.

**WebRTC instead**, for the stream: `mediad`'s H.264 track, which quackd turns upright by the
mount `mediad` reports and measures through the focal length of `mediad`'s own calibration,
taking the principal point for the frame's centre, which on an alpha leaves every bearing about
6° to the left ([the Microduck page](README.md)). It needs `quackd[microduck-camera]`, the same
duck package with its WebRTC camera (`uv pip install 'quackd[microduck-camera]'`), and it takes
the robot's one media session, so the browser console cannot be open at the same time:

```bash
ssh -L 8443:127.0.0.1:8443 microduck@<duck>      # in another terminal
quackd doctor --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --camera-url webrtc://127.0.0.1:8443
```

> Abort if no frame arrives. Check `mediad` registered a producer — if its pipeline never
> reached PLAYING there is nothing to connect to, and quackd says so rather than hanging. A
> `mediad` that never says how its camera is mounted gets no frame handed out, and quackd says
> that too. ICE on a shared office network is the next thing to suspect.

## 7. The head, on a stand

Put the duck on a stand. `gaze` runs upstream's gaze IK through `robot.look`; the answer says
whether it clamped.

> Abort if the head moves somewhere you did not ask for. The IK is upstream's, and a clamp is
> expected — an unexpected direction is not.

A robot on `daemon-v0.16.1` or later glances around on its own once nobody has steered its head
for two seconds and it has then stood still for two more, about four seconds after the last head
command, and each spell of glancing fades in rather than jumping. After a `gaze`, quackd sends the
head where the gaze put it again every second, so the head should stay where it was pointed for as
long as you watch, and `report_state` should show what is held in `extras.gaze_hold`, and about
the bearing you asked for in `extras.head_yaw_deg`. A `stop` ends the hold, at most a second after
the head last went out, so from 3 to 4 s after the stop the head may start to look around, at its
full size 1.5 s later: that is robotd's, not a fault. Pose the head with the gamepad while a gaze
is held, and quackd should let go of it rather than pull it back, with `extras.gaze_released`
saying `overridden`.

> Abort if the head wanders off a gaze while `extras.gaze_hold` says it is held, or if quackd
> keeps pulling the head back from the gamepad.

## 8. Pull the tunnel out, mid-walk, with the pad switched off

**Feet still off the ground, and no pad connected.** A connected pad sends robotd a stop every
100 ms while nobody touches it, and robotd keeps the newest twist, so with a pad on, its stop
halts the legs within 100 ms of quackd's last twist whether or not the deadman works, and this step
would prove nothing about the deadman. Switch the pad off, and expect `padd` to ask robotd to sit
the duck 1 s later, as it does whenever a pad that was driving goes. In an ssh session of its
own, not the tunnel, check that `robotctl pad status` says `padd` is not driving, stand the duck
again if it sat with `robotctl robot do sit_toggle`, the request the pad's sit button sends, and
leave `robotctl monitor` running there: it shows what robotd was asked for beside what it applied
and what limited it. Then start a short walk, and kill the ssh tunnel while it is moving.

What should happen: `robot.move` notifications stop arriving, and 0.5 s after the last one
`robotd`'s deadman zeroes the velocity, which `robotctl monitor` should name as what limited it.
quackd's heartbeat fails, says it is sending a stop, and aborts the run — and it will tell you if
that stop could not be delivered, which is the honest outcome when the link is the thing that
broke.

> **Abort the whole session if the legs keep driving.** The deadman is what stops a walk whose
> link dies while no pad is connected, which is every run over MCP and every run nobody holds a
> pad for. Nothing else quackd does matters if it is not there.

The command, since this step never named one. `--llm fake` answers a free-form goal with a fixed script that ignores it, so it needs a real model:

```bash
quackd run --goal "walk in place with small steps, do not turn, then stop" \
    --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
    --llm anthropic --max-steps 6
```

## 9. Feet down

Now, and only now, put the duck on the floor and run a short walk. Switch the pad back on and
keep it in your hand.

```bash
quackd run find-and-kick --robot microduck:jsonrpc --address tcp://127.0.0.1:9870 \
  --camera-url http://127.0.0.1:8080/frame --llm anthropic --memory-dir ./duck-day
```

The camera is step 6's, through the same tunnel. Start with `--no-memory` for a clean first run,
then `--memory-dir` so the campaign's notes stay out of your simulator's.

With the pad on and its sticks untouched, `padd` sends robotd a stop every 100 ms and quackd
sends its twist as often, so the twist the duck walks on flips between the two, and nobody knows
yet how a duck walks on that. quackd checks each walk against robotd's own `move.requested`,
counting the frames robotd sends after it has answered a call of quackd's, and a `search_scan`
counts its turns together, since the scan's own stop ends each one. Once robotd's state has gone
three frames in a walk without showing quackd's twist, or two past the first of each turn across
a scan's turns, the `search_scan` or `walk_to` sending it stops the duck at its next twist and
fails with `robotd has not taken robot.move`, robotd still saying it was asked for `[0, 0, 0]`.
A `search_scan` that stops says how many of its turns robotd's state showed it took, or that it
showed none, rather than how many quackd sent. Whether that happens depends on how the pad's
stops and quackd's twists fall between robotd's frames, so it may come on some walks and not
others. A scan stops only at a turn it sends after the verdict, so one whose last turn brings the
verdict, or whose turns are too short to, ends not finding what it looked for, and says how many
of its turns robotd's state confirmed: a turn it did not confirm is a view the duck may never have
turned to. A stick you push is sent on every tick, so it takes the walk over, and quackd's verb
soon fails the same way, saying what the stick asked for; let go, and the duck stops until the
pilot sends its next walk. To end the run, Ctrl-C or `q` in the terminal; to sit the duck where it
stands, the pad's sit button.

> Abort and let the gamepad take it if the duck goes over. Nothing quackd sends is known to stand
> a fallen duck up: `stand_up` asks the policy to drive again with `robot.enable {on: true}`,
> only for a duck nobody holds, and calls it upright only if it then reads standing. A robot on
> `daemon-v0.16.1` does nothing about a fall but report it, since `limp_fall` is off by default
> there, so the recovery is the owner's. From `daemon-v0.16.2` robotd goes limp as the duck
> falls while it walks or stands, though not during a kick, a grab or a sit, poses it and
> hands it back to its walking policy, which upstream says gets it up, and quackd sends it no
> walk meanwhile. Nobody has seen that on a duck under quackd, so watch what it does and write
> it down (step 10).

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

- whether `posture` tracked the real robot: whether `policy` said `sit` when it sat, `rise` on
  the way up, and `held` with `safety.picked_up` true while somebody held it, and whether a walk
  was refused then. quackd read those labels in robotd's source, and nobody has seen them on a
  robot
- whether the `microduck` account was in the `robot` group out of the box (step 1)
- what `extras.robotd` said the robot runs, and what `extras.move_echo` said of a walk: how
  long after a twist went out robotd's state showed it, which is the number the echo window
  (three state frames) was set without
- what step 8 showed: whether `robotctl monitor` named the deadman, and how long after the
  tunnel went the legs stopped
- what a pad nobody touched did to quackd's walks in step 9: whether the duck walked on, slowed
  or stuttered, and how often a verb failed with `robotd has not taken robot.move`
- whether a fallen duck got up, how long that took against the ten seconds `stand_up` waits
  for it to read as standing, and how much it moved doing it. `stand_up`'s `robot.enable`
  only sets a flag that is on already when the duck fell while its policy drove it, so what got
  the duck up, if anything did, was that policy. On `daemon-v0.16.2` or later, write down too
  what robotd's own answer to the fall did first: whether `policy` read `limp_fall` and then
  `limp_pose`, and whether the duck was up before `stand_up` was asked
- whether the head held still after a `gaze`, whether `extras.head_yaw_deg` agreed with where the
  camera visibly pointed, and how long the gamepad had the head before quackd let go of it
- what pulling `/frame` cost the duck: how old `extras.camera` said the newest frame was, and the
  `cpu_temp_c` and `cpu_throttle` that `extras.health` carried while it ran. Nobody has measured
  either, and 2 frames a second is a guess at what a duck can afford
- whether a WebRTC frame came out upright beside a `/frame` one, and the `rotate` that `mediad`
  gave, which `extras.camera` carries
- whether `media.detections` arrived on the control datachannel, on a robot whose duck detector
  was turned on, since it is off by default: upstream's source says it does, and nobody has seen
  one
- what `robot.subscribe` listed in `skills`, which is where a robot's real skill set lives now
