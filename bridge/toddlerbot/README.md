# Roboglot's ToddlerBot daemon

Run this on the robot's Jetson, in upstream's own conda environment. It is the only Roboglot
code that ever touches a motor on this robot.

## Why it exists

ToddlerBot has no network API of any kind. No socket, no daemon, no IPC: it is a Python
library whose control loop opens serial ports in-process. Something has to be on the robot,
so Roboglot ships this, the way it ships one for the Open Duck Mini.

Three reasons it is a daemon rather than a thin shim, and the second is the important one.

**A verb is episodic and this robot is not.** `RealWorld.step()` is a no-op, so nothing times
out and nothing re-arms: the last commanded pose is held forever. A humanoid frozen mid-stride
while an LLM thinks is a humanoid on the floor. So the fifty hertz loop lives here,
and Roboglot's intents only nudge what it is already doing.

**Upstream protects nothing, and its shutdown drops the robot.** `set_motor_target` clamps
nothing and never reads the joint limits that exist; the motors are in `extended_position`
mode, so the firmware limits are off too; there is no watchdog, no timeout, no e-stop and no
reset anywhere; a dropped packet returns an all-zeros observation that looks exactly like
every joint at zero; and a C level `atexit` handler disconnects every client on any normal
interpreter exit, which disables torque and drops a standing robot. There is no Python signal
handler anywhere upstream, so `SIGTERM` does not even reach that.

## What it does

Ten things upstream has not got, enumerated in this file's own docstring in
`roboglot_toddlerbot_bridge.py` rather than a second time here. They fall into three groups:
**nothing exits without settling first** (signal handlers, excepthooks, a hard exit that needs
no GIL around the settle and around a `close()` that holds it), **nothing is commanded that
cannot be trusted** (no command at all before the first reading, an all-zeros detector, a clamp,
a rate limit, a refusal of non-finite targets, keyframes paced to what the body can follow),
and **nothing fails silently** (a loop that survives a raising tick instead of dying while the
socket answers healthy, a construction watchdog, dispatch by type rather than by class name).

The deadman is the opposite of the Open Duck Mini's. A duck that stops walking stands still.
A humanoid that stops walking mid-stride falls. So on silence this slews to the safe pose and
**holds**, and it never torque-offs. `stop` means hold the last verified-good pose, because
there is no velocity at this hardware boundary at all. It holds the whole body, the head and
the grippers included, and its answer lists what it stopped.

**Who owns what.** The robot is three kinds of part: the body (a walk, a stand or a motion), the
neck, and each gripper. A part is owned by the connection whose command it is executing, and by
nobody while it holds: after a stop, after its stand or motion has played to its end, or when it
has never been commanded. A connection's keepalive keeps alive only the parts it owns. When an
owner goes quiet for half a second, or disconnects, the parts it owns, and only those, are
brought to rest by the deadman: the body slews to the safe pose, waist first, and the neck and
each gripper to their place in it, all at upstream's own 0.3 rad/s; a head or a gripper's rest
ends once it is there. While a twisted waist untwists, the rest of the body holds the pose it was
last sent, in the deadman's rest, a stand and the shutdown alike: held at the pose measured each
tick, a joint that sags a little under load had its target moved after it, away from home, and
was walked off its pose. A command for one part never takes another over, so a second client's
look or grip leaves a first client's walk to that client, and the walk ends when its client
does. A command that takes a part another connection was driving tells that connection, as a
stop does (below).

A motion moves every joint, the head and the hands too, so it takes the neck and the grippers
with the body, except what its own client holds by a look or a grip: that stays held through
the motion, which plays on the rest without waiting for it, and the answer lists it as `kept`.
So a hand closed on something stays closed. It is refused, with the reason, while another
connection is driving one of them or the deadman is still bringing one to rest, rather than
played round a head it cannot reach. A head's or a gripper's rest that stops closing on its
place in the safe pose (a place outside the joint's limits) ends after as many ticks as a
keyframe that cannot be reached is given, and says so in the log; until then a stop ends it.
Whatever ends a motion (its last keyframe, a fall, another command for the body) lets go of
every part the motion took, except a head or a hand its client has aimed or gripped since the
motion began: that stays the client's, as a look or a grip made outside a motion does, so the
client's going brings it to rest. A stop lets go of every part, as always.

A daemon nobody has commanded holds the robot where it stands, and so does one a stop or a
finished motion left holding: nobody owns the body then, so the deadman moves nothing.

**A stop.** It is honoured from any connection, and it stops every part: the robot holds where
it was last measured, every part is owned by nobody, and the answer lists what it stopped. It
arms nothing, so `roboglot doctor`, which sends a stop as it disconnects, moves nothing on a
robot nobody is driving. Against a run in flight it does stop the run's motion, and the robot
then holds: the run is told at its next keepalive (a `bot.lost` notification) or its next
command (a `lost` field in the answer), and its heartbeat fails with the reason. So run a doctor
check between runs. A stop that lands before the first good reading holds wherever that reading
finds the robot.

**A fall.** When a fall is read, whatever was in flight on the body ends, a walk, a stand, a
motion or the deadman's own slew, and the robot holds where it lies. On a robot lying down rest
is that hold, for every part: the deadman never drives a body on the floor towards standing.
Upstream detects no fall and prescribes nothing for one.

**A motion that goes to the floor is not a fall.** `crawl` and `push_up` take the robot down on
purpose, face down and past the fall threshold, so they play to their end, and the robot lying
face down after one is not read as fallen: its posture reads `unknown` (it is not standing), a
walk or an upright motion is refused until it is up, and `stand` brings it back up. The daemon
cannot tell this from the keyframes, which carry motor targets and no orientation of the trunk,
so `FLOOR_MOTIONS` is a table of those two, by what each is named for.

Down on purpose is one way only. A lean past the threshold any other way, more than
`FLOOR_HEADING_DEG` (45 degrees, half the way to a robot on its side) from face down, is a fall
during the motion, while lying after it, and during the stand back up, and it ends whatever is
running as a fall always does. Which way is face down in the orientation the daemon reads
(`FACE_DOWN`, the world's up along -x in the body's frame) follows from upstream's orientation
being the body's in the world and the IMU's axes being the torso's, and has not met a robot. The
state reports the reading that settles it as `up_in_body`, the world's up in the body's frame,
and step 13 of the hardware checklist reads it with the robot tipped nose down on its stand: x
must be below zero. On a robot where the sign is the other way round, `crawl` and `push_up` are
cut halfway and read as a fall, a fall onto its back during them goes unseen, and nothing outside
the two floor motions is affected.

A stand from the floor counts as done only once the trunk reads upright. If it does not by the
settle's own deadline for that distance, it stops pushing towards standing: the stand ends
holding the robot where it lies, as after a fall, with the legs commanded to the pose measured
when the deadline passed until the next command. The posture still reads `unknown`, health says
the stand did not get the robot up until it is up, and another stand may try again.

Once a shutdown begins, every command that moves the robot is refused with the reason, so a
connected client cannot keep the settle from the safe pose, and `bot.health` says the daemon is
shutting down. A stop is accepted, changes nothing, and its answer carries the note
`the daemon is shutting down and settling the robot to its safe pose`. Shutdown is entered once,
from whatever began it, and a second signal does not cut the settle short. The settle goes on
for as long as it keeps closing on the safe pose, however slowly the loop runs, since its slew
is handed out a tick at a time. A tick that fails on the bus moves nothing (the target is what
the last write that landed carried, so a bus that comes back is handed one step, not a jump) and
is simply waited through; a robot already at its safe pose is there whether its writes land or
not. The settle ends once nothing has come nearer for a hundred ticks that landed (while the
waist untwists, its target or the waist as the daemon reads it; after that, the whole pose): a
place outside a joint's limits, a robot lying on the floor, which rest holds where it lies, or
a waist that has stopped moving. Then `close()` follows. One path does not close: a settle
still short of the safe pose after `SETTLE_MAX_S`
(120 s, which only a loop making under about four ticks a second, or a bus that never answers
again, reaches) leaves the robot torqued and holding where it is, since closing takes torque off
and would drop it. The log says the safe pose was not reached, how far is left, the rate the
loop made and how many of its ticks failed, and ends `the robot is left torqued and holding where
it is, and the daemon exits with 3: support it before you power it off`; the daemon then exits
with 3. A bus that hangs cannot keep the process alive either: a settle tick that does not come
back ends it within thirteen seconds, and so does `close()` five seconds in, with or without a
standard error to write its stacks to.

After ten failing ticks in a row the loop takes no more orders, and settles the robot to the
safe pose for as long as the bus answers; after twenty it stops. Only a restart lets the daemon
take orders again. Until then every command that moves the robot is answered `accepted: false`
with this reason, and a stop is accepted with it as its note:

```
the control loop gave up after 10 failing ticks in a row: it takes no orders, and it settles the robot to its safe pose for as long as the bus answers. Restart the daemon
```

and `bot.health` answers `ok: false`, with the fault while ticks are failing and this reason
once they pass again. State, frames and the keepalive are answered as before.

Health also fails once no tick has ended for half a second, the first tick included, because the
deadman lives in the tick. A walk command is held to the envelope the walk checkpoint was
trained on, whoever sends it.

## The protocol

Line delimited JSON-RPC 2.0 over TCP on port **9873**, which is Roboglot's own at both ends. The
Open Duck Mini already takes 9871 for its bridge and 9872 for its camera daemon, and
SECURITY.md tells people to tunnel that pair, so this robot starts after both.

The client also sends `bot.keepalive` on its own timer, several times a second, for as long
as it is connected. For the parts that connection owns, that is the only thing that stops the
deadman firing part way through a verb: `stand` takes three seconds and the deadman fires after
half of one, so without it every long verb would be cancelled underneath itself by the
safe-pose slew. Another connection's keepalive does not count, and neither does reading state
or a frame, or a method this daemon has never heard of, so a client built against a newer
protocol cannot hold the deadman off with calls this one is refusing. After a stop that got no
answer, or was refused, Roboglot's client stops sending it, so this daemon's deadman trips within
half a second and brings what that client was driving to rest, and the client's heartbeat fails
saying why; the next command the daemon accepts starts it again, and so does the stop's own
answer if it arrives late.

Give the daemon a token and it refuses clients that do not send it. Put it in a file and pass
`--token-file FILE`, or set `ROBOGLOT_TODDLERBOT_TOKEN`. `--token` works too, and warns, because
every account on the robot can read a command line:

```
WARNING a token given with --token can be read by every account on this machine (ps, /proc/<pid>/cmdline); prefer --token-file or ROBOGLOT_TODDLERBOT_TOKEN
```

A token file that is named and missing, unreadable or empty refuses to start, and an empty
token from the flag or the variable is no token. The token travels in the handshake and never
in the address, because addresses get printed and land in transcripts. It crosses the network
in the clear, because there is no TLS: reach the daemon through an ssh tunnel rather than over
a network you do not trust.

The daemon binds `127.0.0.1` unless `--host` says otherwise, and on loopback it starts with no
token, as it always has. On any other address it will not start without one:

```
refusing to listen on 0.0.0.0 with no token: anything that can reach that address could drive this robot. Give it a token with --token-file (or ROBOGLOT_TODDLERBOT_TOKEN), or pass --allow-no-token to serve anyone who can reach it, on purpose.
```

`--allow-no-token` starts it open anyway, and its log says so. Its handshake says whether a
token is checked, and Roboglot warns when a robot it reached over an address that is not
loopback checks none (`roboglot doctor` shows the warning).

## Running it

It runs **in upstream's own environment**, which is the third of those reasons. Upstream hard-pins
`numpy==1.26.4`, `jax==0.4.28`, `jaxlib==0.4.28`, `setuptools==75.6.0`,
`moviepy==1.0.3` and `opencv-python==4.9.0.80`. Those cannot share a process with
Roboglot's own dependencies, and Roboglot is not going to ask anyone to downgrade numpy
to drive a robot. This runs over there and speaks a socket, so neither side has to
win.

```bash
git clone https://github.com/hshi74/toddlerbot ~/toddlerbot
git -C ~/toddlerbot checkout 84e02d14261292eec5d06f896e3145b35c54856c
pip install -e ~/toddlerbot && pip install 'mujoco==3.3.4' 'scipy>=1.14'
python roboglot_toddlerbot_bridge.py --robot toddlerbot_2xc --toddlerbot ~/toddlerbot
```

`mujoco` is not a declared dependency upstream. It arrives transitively and unpinned, so pin
it yourself.

`--camera left|right`, `--walk-policy NAME` and `--gripper` say what this robot has, and
**each is checked rather than believed**. A camera that will not open is logged and the
robot simply has none, so Roboglot never offers `observe`. A walk checkpoint is a wandb
artifact upstream neither publishes nor checks in, so `--walk-policy` names a directory
under `ckpts/` that you supply, and the daemon refuses to start rather than reaching for
wandb from a robot. The handshake reports what actually loaded, including which motions
were readable, and Roboglot's manifest is built from that answer.

`--fake` runs the whole daemon and protocol against a fake body, with no robot and no
upstream installed, which is what CI does. `--once` sets up, reports what it found, and exits.

**The daemon refuses to actuate without a zero calibration** (`motors.yml`, which is
gitignored upstream so a fresh clone has none). Without it every commanded angle is offset by
however that particular robot was assembled. Run upstream's `calibrate_zero` first.

**Roboglot's host daemon can run beside this one, and `roboglot run` stays on the laptop.** Every
Roboglot command but `roboglot onboard`, Robot Passport's service, stays off this board.
`bridge/jetson/roboglot_jetson_hostd.py` runs here, and it gives the laptop the board's health and
YOLO on its GPU, which `roboglot run --host` reaches on port 9874 while this daemon keeps 9873
([`bridge/jetson/`](../jetson/README.md)). Start it with `--camera none`, because this daemon
owns the robot's cameras and two processes cannot own one camera. It runs on the board's system
`python3`, outside upstream's conda environment, needs Python 3.10 or newer (JetPack 6 ships
3.10), and imports nothing from Roboglot. An LLM server can sit beside both
([docs/guides/jetson.md](../../docs/guides/jetson.md)). What to watch is contention, because an
LLM server or a detector saturating the board is what can starve the loop this file exists to
protect, and nobody has measured it.

## Rules this file lives by

- **It never imports Roboglot.** Roboglot's dependencies do not belong on a robot, and a test
  enforces this by reading the file.
- **It ships in the sdist and never in the wheel**, so `packages` stays `["roboglot"]`.
- **It is testable with no hardware.** Everything above the `Robot` boundary is pure and takes
  plain arrays, so the clamp, the rate limit, the dropped-read detector and the slew are all
  unit tested, and `--fake` exercises the loop and the protocol end to end.

## Safety

Read `docs/adapters/toddlerbot/hardware-checklist.md` before the first bring-up and follow its
order. It keeps the feet off the ground until step 14, and the two steps that matter most are 11 and
12, both taken on the stand: pull the network cable mid-move and confirm the deadman slews
rather than drops, then send `SIGTERM` and confirm the same. An exit code of 3 there means the
settle could not reach the safe pose and left the robot torqued and holding: support it before
you power it off.

This robot cannot get up by itself. There is no get-up policy for this body at this pin, so a
fall ends the run and needs a human. Ask for less than you think.
