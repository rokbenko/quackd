# A LeRobot SO-101 arm: the order to try it in

quackd has run on an SO-101 on two afternoons, 2026-09-15 and 2026-09-23, both times on the
same arm, and [adapter-status.md](adapter-status.md) lists exactly what it did and what fell
over. That makes this a robot quackd has worked on, not a robot quackd is tested on. Four of
the questions at the foot of this page came back from the first afternoon still unanswered,
the second added more, and what quackd changed after the second has not run on an arm yet.
This is the order to find out in, written so that each step can only fail in a way you can
recover from.
**Nothing moves until step 10, and from there a hand stays on the power switch.**

This robot is unusual in a quiet way, and the quiet thing is what makes the order matter:
**LeRobot writes a torque and current cap on the gripper and on nothing else.** The five
body joints run with whatever their firmware defaults to, so the elbow has no cap to save
your finger or its own gears. An arm also sweeps a volume rather than occupying a spot, and
a gripper is a pinch hazard at any torque. Read
[adapters/lerobot.md](adapters/lerobot.md) first, or
[lerobot-first-run.md](lerobot-first-run.md) if you have never run quackd or LeRobot at all:
it is the same ground at walking pace, and it hands back to this file the moment anything is
about to move.

## Before you power anything

1. **Know which build you have, and give it the supply its own parts list names.** An
   SO-101 is sourced from a bill of materials rather than bought as one thing, and the
   motors come in more than one variant, with different stall torque and different supplies.
   Upstream's assembly page sends you to
   [TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100) for that list, and
   it is the authority on which supply yours takes. Neither LeRobot nor quackd reads the
   voltage, so neither can warn you: the wrong supply is either an arm that cannot hold
   itself up or an arm with more torque than you were expecting.
2. **Clear the whole sweep**, not the footprint. Take anything fragile out of the gripper and
   off the desk within arm's length, and keep hands out of the volume from here on.
3. **Fit a switch you can reach.** There is no e-stop on an SO-101 and quackd cannot give it
   one: cutting the servo supply is the only thing that stops this arm in every case,
   including the one where the controlling process has died with a goal still standing. Put
   an inline switch on that supply, on the side of the desk you will be standing on.
4. **Install the extra and check both halves are there.** Nothing is energised by this
   step, and it comes before the calibration below because `lerobot-calibrate` is one of the
   commands it installs.

   ```bash
   uv pip install 'quackd[lerobot]'      # Python 3.12 or newer, and it pulls torch
   uv run quackd doctor
   ```

   That extra is two packages rather than one: `quackd-lerobot`, the arm adapter, which
   installs on any Python quackd supports, and through its own `[sdk]` extra, LeRobot. A
   plain `uv pip install quackd` brings neither, and no other robot's extra does either.

   Two rows matter: `lerobot`, and `lerobot (feetech bus)`. The Feetech SDK lives in
   lerobot's own `[feetech]` extra rather than its base dependencies, so a lerobot installed
   without it imports perfectly and then cannot open a serial port. If `lerobot` itself stays
   `not installed` after a successful install, check `python --version`: the SDK half carries
   a `python_version >= '3.12'` marker and resolves to nothing below that, which leaves you
   the adapter, `lerobot:mock` and no arm.

## First power: the port, then the calibration

This is where the arm is energised for the first time, so the sweep from step 2 has to be
clear and the switch from step 3 has to be fitted and within reach before you start.

5. **Find the port, then calibrate with upstream's own tool**, which is interactive and which
   quackd never triggers:

   ```bash
   lerobot-find-port
   ```

   It lists the ports, asks you to unplug the arm, and names the one that disappeared. That is
   worth doing even when you are sure, because it is the only answer that is not a guess. On
   Windows the port is `COMx` and shows up by itself under Ports (COM & LPT); if nothing
   appears at all, suspect the cable or the power before you go looking for a driver. On Linux
   it is `/dev/ttyACM0`, and upstream's own fix for permissions is `sudo chmod 666
   /dev/ttyACM0`, with adding your user to that port's group being the version that survives a
   reboot.

   ```bash
   lerobot-calibrate --robot.type=so101_follower --robot.port=/dev/ttyACM0 --robot.id=arm-01
   ```

   It writes `<calibration dir>/robots/so_follower/<id>.json`, where the calibration
   directory is `$HF_LEROBOT_CALIBRATION`, else `$HF_LEROBOT_HOME/calibration`, else
   `$HF_HOME/lerobot/calibration`. **The id has to be the one quackd will use**, which is
   `arm-01` unless you named the robot with `--robots <name>=lerobot:real` or registered it
   with `quackd robot add <name> lerobot:real`. quackd reads every joint's travel out of that
   file and refuses to drive an arm without one, and two arms sharing an id share a file with
   nothing in it to say which arm it came from.

   Upstream will ask you to move every joint through its range **except `wrist_roll`**, and it
   records a full encoder turn for that one rather than anything you swept. That is not a
   mistake you can correct, and it is why the out-of-range refusal you will test in step 12
   works on the other joints and cannot work on that one.

   **Take each joint all the way into the fold you will rest the arm in during that sweep.**
   Calibration writes the travel it records into each servo as two limits, and the servo is
   never driven past them afterwards. A fold outside them is a pose the arm can rest in and
   cannot be driven back to, which is the rest pose of step 6 going wrong before you have
   recorded it: on 2026-09-23 the arm this page was written for had never had its shoulder
   folded all the way back during calibration, and its fold lay past that joint's travel.

## The host, with the arm still

6. **Connect and read the arm back.**

   ```bash
   uv run quackd doctor --robot lerobot:real --address /dev/ttyACM0
   ```

   This one connects, unlike `list-verbs`, which does not. Read four things off it: that the
   calibration file it found is the one you just wrote, that each joint's range looks like
   the travel you swept during calibration, that torque is on, and what the servos say their
   temperature is with the arm cold. That last number is the baseline for every later step.

   **Support the arm before this command finishes, and while it starts.** `configure()` runs
   with torque off, so connecting drops it for a moment, and LeRobot's `disconnect()` disables
   it again by default at the end of every clean session, a `doctor` probe included. An arm
   folded somewhere awkward will fall at either end. The second half of that is what the rest
   pose below is for, and until one is recorded this probe still lets go.

   **Then give the arm a name.** A registered name carries the port, the camera and the rest
   pose, so from step 7 on this page writes `--robot arm-01` where it used to write the pair:

   ```bash
   uv run quackd robot add arm-01 lerobot:real --address /dev/ttyACM0
   ```

   The name has to be the calibration id from step 5. `--robot lerobot:real --address
   /dev/ttyACM0` still works everywhere below and reaches the same arm, with one difference
   that starts mattering here: a rest pose is recorded against a name, so the spec form has
   none and goes on letting go of the arm wherever it stops.

   **Then check that a connect quackd refuses lets go of the arm.** Register the same port once
   more, under a name nothing was calibrated as, point `doctor` at it, and remove the name again:

   ```bash
   uv run quackd robot add arm-uncalibrated lerobot:real --address /dev/ttyACM0
   uv run quackd doctor --robot arm-uncalibrated
   uv run quackd robot remove arm-uncalibrated --yes
   ```

   LeRobot has no calibration under that id, so its connect goes through and switches torque on,
   as every connect does, and quackd then refuses the arm: `the arm is not calibrated`. A refusal
   of quackd's own lets go of the arm on its way out. Since 0.15.0 it has to ask for that, by
   writing the flag LeRobot's disconnect reads just before it disconnects, because the arm is
   otherwise built to keep torque on any disconnect quackd did not ask for. Keep a hand under
   the arm while it energises for a moment, then press gently on the forearm once the refusal is
   printed: it should give, limp, as it did after the first `doctor` above. Report which it did.
   An arm that still resists is the refusal keeping it energised with nothing said: hold it and
   cut its power. This comes before the fold, because pressing on the arm moves it.

   **Then fold it by hand and record where it rests.** Nothing is connected now, so the arm is
   limp. Fold it into the shape you want it to end every run in: low, resting on its own stops
   or on the desk, a shape it holds with torque off and cannot topple out of. Then:

   ```bash
   uv run quackd robot rest-pose arm-01
   ```

   It connects, reads every joint, prints them, and asks before it writes anything. **It drives
   nothing.** That is why it belongs at step 6 rather than after step 10: recording a pose is a
   read. The block below was captured against `lerobot:mock`, with `--yes`, which is the flag
   that skips the question. Those are the mock arm's joints, and yours are whatever you folded
   it to:

   ```
   $ quackd robot rest-pose arm-01 --yes
   arm-01 (lerobot:mock) is at
   shoulder_pan   0.0
   shoulder_lift  -90.0
   elbow_flex     90.0
   wrist_flex     0.0
   wrist_roll     0.0
   gripper        100.0
   ✓ recorded arm-01's rest pose (6 joints)
     quackd run <duck> --robot arm-01 starts from it and returns to it before letting go
   ```

   If you took every joint into the fold during step 5, the fold is inside the travel and the
   command records it and says nothing more. If it is not, the command prints a warning before
   it asks, naming the joint, the angle you folded it to and the edge of its travel, and
   records the pose anyway. A fold outside the travel is what went wrong on 2026-09-23, before
   there was any warning: it lay about 20 degrees past the floor of `shoulder_lift`'s travel,
   the servo would not be driven there, runs aborted before their first model call, every run
   that got to its end kept torque on, and a `stop` hauled the folded shoulder up out of its
   fold. quackd now drives each joint clipped into its travel, counts the edge of the travel as
   reaching a joint recorded past it, lets go there, says once per run which joint is free to
   settle the rest of the way, and never writes a goal for a joint that reads past its travel
   ([adapters/lerobot.md](adapters/lerobot.md#a-pose-past-the-travel)). The fix is still to
   calibrate again folded and record the pose again. Calibrating again at any point moves the
   zero of any joint whose travel it records differently, so the pose you record here is stale
   after it: record it again.

   The gripper is recorded and never driven, for the same reason `stop` leaves it alone.
   Re-sending it would open a hand that is holding something. Only the five body joints move.

   What each command does with the pose from here on:

   | What you run | What it does about the pose |
   |---|---|
   | `quackd run ... --robot arm-01` | drives the arm there before the pilot gets its first turn, and a run that cannot get there aborts before the first LLM call. Returns it there between the final `stop` and the disconnect, on every exit: success, failure, infeasible, budget, abort, an error, Ctrl-C |
   | `quackd doctor --robot arm-01` | probes the arm, returns it there afterwards, and says which in a `rest pose` row |
   | `quackd robot list --probe` | reads the arm and never moves it, so it says `torque left on: not at its rest pose` when it had to keep the arm up, or `torque unknown: the arm did not answer the close` when the arm stopped answering before the probe could tell |
   | `quackd serve-mcp --robot arm-01` | the same at both ends, and it refuses to start if it cannot get there |
   | `--dry-run` | nothing at all: a dry run never moves the arm |

   Torque is released only where the arm is known to be at that pose, or at the edge of its
   travel where the pose lies past it. Anywhere else quackd turns LeRobot's `disconnect()` flag
   off, leaves the arm holding itself up, and says so once:

   ```
   the arm is not at its rest pose (...), so torque was left on and it will not fall as it
   stands: hold it first, because connecting takes torque off every motor for a moment, then
   run quackd robot release arm-01, or quackd doctor --robot arm-01 to park it, or cut its power
   ```

   That line is for an arm that answered the close's own read. An arm that stopped answering
   gets a different one, because nothing can read whether its torque is on:
   `quackd cannot tell whether the arm is holding itself up (...), so it kept whatever torque the arm has: hold it, and cut its power`.
   `robot list --probe` shortens that one to `torque unknown`.

   > [!WARNING]
   > That is a change in behaviour and it is the one to read twice. A probe or a dry run on an
   > arm away from its recorded rest pose now leaves torque **on** where it used to drop it.
   > The arm will not fall as it stands, and it will also not let go until you hold it and run
   > `quackd robot release arm-01`, until a run or `doctor` puts it back, or until you cut its
   > power. Hold it for every one of those, not only the first: a run, `doctor` and the release
   > all begin by connecting, and connecting takes torque off every motor for a moment. A run at
   > a terminal whose rest move missed asks you first: hold the arm and press Enter.


   The `rest pose` row in `doctor` says which of four things happened:

   | Row | What it means |
   |---|---|
   | `none recorded (quackd robot rest-pose <name>)` | nothing is recorded, so this probe let go wherever the arm stood |
   | `at it already` | the arm was there, and torque was released there |
   | `returned to it` | the probe drove it back and let go there |
   | `not reached: <reason>` | it could not get there, so torque is still on |

   For a pose past the travel, `at it already` and `returned to it` mean the edge of the travel,
   and the same sentence the run says comes out under the table as advice. The verdict stays
   green, because the arm reached the pose it can be driven to.

   `quackd robot rest-pose arm-01 --clear` forgets the pose again and says what that costs: a
   run then leaves the arm where it stands, and torque drops there.

   > [!IMPORTANT]
   > Step 6 keeps the promise at the top of this page, because recording reads the arm and
   > drives nothing. What you do next can break it. The arm is now **at** its rest pose, so
   > steps 7 to 9 find it there and drive nothing either. Move it by hand in between and the
   > first thing step 7 does is drive it back, which is motion before step 10. Leave it folded,
   > or record the pose again wherever it now stands.

7. **`lerobot-lookout`.** No verb in it moves a joint.

   ```bash
   uv run quackd run lerobot-lookout --robot arm-01
   ```

   It reads the arm and reports where the joints are, whether torque is on and whether
   anything is hot. This is the first thing to point at a real arm, and it is what the arm on
   the bench ran first on 2026-09-15.

   The rest pose is the only thing in this run that can move the arm. At each end the run prints
   `moving to the rest pose`, before it has read where the arm is, then
   `already at the rest pose` if it found the arm there and drove nothing, or `at the rest pose`
   once it has driven it back. On an arm still folded where you left it in step 6, both ends say
   `already at the rest pose` and nothing moves. On an arm whose pose was recorded past its
   travel, the first end adds the one note from step 6 naming the joint.

8. **Add a camera, if you brought one.** No SO-101 has one built in: it is a USB webcam into
   the laptop, and the arm's own cable carries no video. Find which index it is, which is the
   part nobody can guess for you:

   ```bash
   lerobot-find-cameras opencv
   ```

   It lists every camera it can open and saves a frame from each under
   `outputs/captured_images/`. Open the pictures: on a laptop index 0 is usually the built-in
   webcam, so the one you plugged in is often 1 or 2. On the bench on 2026-09-15 it was
   `opencv://1`, and `opencv://2` after a replug, at 640x480, with no `?backend=` key needed.
   That is one laptop's answer and not a prediction about yours. Then ask quackd for a frame:

   ```bash
   uv run quackd doctor --robot arm-01 --camera-url "opencv://1"
   ```

   Quote the url: a bare `&` is a parse error in PowerShell and backgrounds the command in
   bash. A
   camera you asked for and did not get is a refusal, and it happens before the arm is
   touched, so a wrong index costs you nothing but the message: try another index, add
   `?backend=msmf` if the camera listed and would not open, or drop a size or a rate you
   pinned and let it keep its own mode, which is the default. When it does open, `doctor`
   grows a `camera` row with the frame size in it, or `no frame` if it opened and then gave
   nothing. Then see what the pilot will see, which means an MCP session carrying the same
   url, because a daemon started without it has no `observe` verb at all:

   ```bash
   uv run quackd serve-mcp --robot arm-01 --camera-url "opencv://1"
   ```

   Then `robot_observe` from the client. What comes back is the frame and a line of
   detections, and on a real desk that line is usually nothing: the detector's colour ranges
   are the simulator's, which [the adapter page](adapters/lerobot.md#camera) explains.

   Aim it at what you want watched, and check what it crops. On the bench it framed the
   gripper and cut off the raised arm, so the model ended up verifying its own waves from the
   joint readings rather than from the picture, which is a thing it can do and not a thing you
   should count on.

   A camera is optional: the arm works without one, and `lerobot-lookout` never asks for it.

   **More than one camera.** `--camera-url` repeats, and this arm is the only body that reads
   more than one. Every other body refuses a second one and names who takes several.

   ```bash
   uv run quackd robot edit arm-01 \
       --camera-url "opencv://1?name=top" --camera-url "opencv://2?name=side"
   ```

   | Rule | Why |
   |---|---|
   | with several, every url carries `?name=` and the names are unique | the name is what the model, a pick policy's observation and `frames/NNNN-<name>.png` tell the views apart by |
   | an index may not repeat | two entries for one camera is a typo, not a setup |
   | the **first** url is the primary | it is the camera `--fov-deg` describes, the one the `camera:` detections line reports, and the only one the verbs that steer by sight read: they run at 10 Hz, and fetching every camera there would blow the deadman window |
   | a camera that stalls later costs its own picture and nothing else | `report_state` and `doctor` then name which one, in a `camera <name>` row each |
   | if the **primary** is the one that died, the others still reach the model and the detections line reports nothing seen | a bearing read off a different lens would point somewhere else |
   | a second camera that will not open refuses before the arm is energised | and it lets go of the first, so a wrong index still costs you nothing but the message |

   Get the `?name=` wrong and you get the rule back, before anything is touched. The first part
   of it, the rest being the list of keys a camera url takes:

   ```
   ✗ error: lerobot:real at COM5: lerobot real: --camera-url 'opencv://1': it has no ?name= and 2
   cameras were given. With several, every url names its own camera, opencv://1?name=top
   --camera-url opencv://2?name=side, because the name is what the model, a pick policy and
   frames/NNNN-<name>.png tell them apart by.
   ```

   Every frame reaches the model each step, labelled with its camera name, on Claude, both
   OpenAI APIs, Gemini, and any OpenAI-compatible local server with `--vision` on. **It costs
   what it sounds like.** The last two exchanges keep their images, so two cameras is four
   pictures in every request rather than two, on every step, for the length of the run, and up
   to eighteen on Claude Opus 5.5 and Fable 5.1, whose old frames are trimmed every eight
   exchanges rather than on every one. A local server or a model that takes one image per
   message will refuse outright, and the answer there is a single `--camera-url`.

9. **Rehearse the whole thing with `--dry-run`, which sends the arm nothing.** This is the
   last step before anything moves, and it is the one that tells you whether the parts you
   cannot see are working.

   ```bash
   uv run quackd run --goal "roll the wrist ten degrees, then stop" \
       --robot arm-01 --llm anthropic --max-steps 3 --dry-run
   ```

   A dry run connects to the arm for real and holds the connection open. Read-only verbs
   actually run, so `report_state` reads the servos and the heartbeat keeps its round trip
   going the whole time; everything that would move a joint is printed and skipped:

   ```
   [dry-run] would run move_joints({'positions': {'wrist_roll': 10.0}, 'duration_s': 2.0})
   [dry-run] move_joints not sent
   ```

   Read two things off it. That the model reached for the verb you expected, with arguments
   that look sane, rather than for something you had not thought about. And that the arm
   answered every heartbeat for the length of the run, because an arm that drops out here
   would have dropped out mid-move in the next section. This costs an API call or three and
   is the cheapest rehearsal you will get.

   Two dry runs on the bench on 2026-09-15 ended early, and both endings were the machinery
   working rather than failing. One stopped with `the arm did not answer: TimeoutError` after a
   single heartbeat round trip failed, and nothing like it happened again all afternoon: that
   is the abort you want, at the cheapest moment to get it. The other stopped before it began,
   because the pilot answered `uncertain` to the feasibility question and the human at the
   keyboard said no.

   One way that heartbeat failure could have happened was found on the simulator afterwards and
   closed in 0.15.0: a call to the arm spent its deadline on whatever the event loop's thread was
   doing, a pilot's SDK parsing its first response or a frame being encoded among it, so a
   heartbeat the arm had answered in time could stop the run. Now only time the bus is busy
   spends it. Nothing says it is the one that happened, so give this dry run the camera from
   step 8, since the detector that reads its pictures still runs on that thread, as the pilot's
   SDK does, and watch for the run ending on a heartbeat. If one does, the line names the call
   and its budget, as `a LeRobot call (...) has not come back within 1 s`,
   `came back after its 1 s were spent` or `waited 1 s for the bus and never went out`, and
   never ends at a bare `TimeoutError:`. Report that line word for word, and either way the
   `bus_call` row of `final_state.extras.timing` in the run's `summary.json`, its `p99_ms` and
   `max_ms`. That row times each call from when it was asked for until the run saw it end, the
   wait for the bus included, so it counts time the loop's thread spent elsewhere as well as the
   bus's own, and is not how long the bus alone took.

   A dry run never moves the arm, and that includes the rest pose: it neither drives the arm
   there at the start nor puts it back at the end. So an arm that was away from its rest pose
   when you started is still away from it when the dry run finishes, and quackd keeps torque on
   rather than dropping the arm there.

## Moving, one joint at a time

There is no command that runs one verb. Either drive the daemon from an MCP client
(`quackd serve-mcp --robot arm-01`, then `robot_run_verb`, which is what these steps assume.
The camera comes with the name if you stored it in step 8, and a session with no camera has no
`observe` verb at all) or give a model a goal narrow enough to reach one verb
(`quackd run --goal "..." --robot arm-01 --llm anthropic --max-steps 3`).
`--llm fake` will not do: it answers a free-form goal with a fixed script that ignores it.

Both of those drive the arm to its rest pose before you get a turn, and back to it before they
let go. An MCP session refuses to start at all if it cannot get there, which is the same rule
as the run's, moved to the moment the daemon comes up.

[Part 2 of the first run](lerobot-first-run.md#part-2-from-claude-over-mcp) is that MCP session
at walking pace: which client to configure and how, what each tool answers, what every refusal
means, and which moments move the arm without anybody asking for it.

10. **`gripper` open, then closed on nothing, and watch which way it goes.** quackd assumes
    100 is open and 0 is closed, and that is an assumption about how your arm was assembled
    and calibrated, not a fact about the model. If yours runs the other way, stop here and
    say so in an issue: everything quackd believes about holding something rests on this, and
    it would be believing the opposite. The bench arm on 2026-09-15 ran the way quackd assumes:
    commanded 100 it reported 98 and stood open, commanded closed it reported 3 with the jaws
    nearly touching. That is one arm, built and calibrated by one person, which is why this
    step is still here.
11. **One joint, small, in the middle of its range.** `move_joints` with
    `{"wrist_roll": 10}`. It takes the `duration_s` it is given, five seconds when none is,
    and stops. However short that time, the arm moves at most 5 degrees per action re-sent
    ten times a second, so 50 degrees a second, and `QUACKD_LEROBOT_MAX_STEP_DEG` lowers that
    if it looks fast in the room. Try it once with `"duration_s": 0.2`, which for ten degrees
    is the cap, and once with a few seconds, and check the second is visibly slower. For scale,
    the free-form goals on the bench on 2026-09-15 came out as wrist-roll waves of about plus
    or minus 27 degrees, and as wider poses with `shoulder_lift` at -39 and `elbow_flex`
    between 24 and 30. Nobody wrote down whether 50 degrees a second looked right standing
    next to it, which is why that question is still at the foot of this page.
12. **Ask for something out of range.** A goal of 170 on a joint whose travel is about 100
    either way. It is refused with the range in the reason and nothing reaches the arm. This
    is worth doing deliberately, because LeRobot does not clamp a degrees goal and the servo
    is the only thing downstream of it. Use any body joint **except `wrist_roll`**: that one
    is recorded as a full turn at calibration, so its range is -180..180 and this refusal
    cannot fire on it.
13. **Pull the USB cable mid-move.** The run should end within about a second, saying the arm
    did not answer. The arm holds its last goal under torque: it must not sag and it must not
    carry on. There is no rest pose in this one, because quackd cannot drive an arm it cannot
    reach: the arm stays where it stopped, holding itself up, which is the safe half of the two
    ways this could end. The note the run closes with cannot say so: it says quackd cannot tell
    whether the arm is holding itself up, and to hold it and cut its power, and no release is
    offered, because the arm did not answer. Before you plug it back in, put a hand under the
    arm: every command that reconnects, a run, `doctor` or `quackd robot release arm-01`, takes
    torque off every motor for a moment. Either cut its power with your hand under it, or hold
    it and run `quackd robot release arm-01`, or `quackd doctor --robot arm-01` to park it,
    before the next step.

    Report the line the run ended on word for word. Since 0.15.0 a call that ran out of time
    names itself and its budget, as in step 9, and one that failed says what failed.

    The reconnect is where the other half of 0.15.0's change to the connect would show, if it
    shows at all. A connect can fail after LeRobot's own connect has switched torque on and
    before quackd has read the arm, when that first read is not answered. Such a connect used to
    be left to a later disconnect, which took torque off wherever the arm stood. It now closes
    the port with torque kept and says so, `connect failed once the arm was energised`, ending
    `hold the arm, and cut its power`. The window is the few reads between the two, too short to
    time a pull into by hand, and this step does not ask you to try. If it ever happens, here or
    at any later connect, keep your hand under the arm, check that it is still holding once
    quackd has exited, and report the line and whether it held.
14. **Ctrl-C mid-move.** quackd's kill switch sends `stop`, which re-sends the present
    position as the goal. The arm should freeze where it is rather than sag, and rather than
    finish the motion it was in the middle of. Then, with a rest pose recorded in step 6, the
    freeze is not the end of it: quackd folds the arm back to that pose and lets go there,
    because that is the one place letting go is safe. Expect that motion, and keep the hand on
    the switch through it. The one joint the freeze does not hold is one the move was lifting
    out of a fold past its travel: every goal past the travel is the edge to the servo, so that
    joint keeps rising to the edge whatever the stop does, and the switch is the only thing that
    stops it there.

    Then do it once more, and this time press a second Ctrl-C during the fold back to the rest
    pose, with a hand under the arm and the other on the switch. The second press skips
    quackd's close, so nothing lets go on purpose. The arm should still hold where it stood once
    quackd has exited, rather than fall when the process lets go of it. Before 0.15.0 that exit
    could take torque off and drop it, which is why the hand goes under it first. Hold it and run
    `quackd robot release arm-01`, or cut its power, before the next step.

    Then a third time, one Ctrl-C, and this time let the fold back to the rest pose stall: as it
    folds, lay a flat hand on the forearm and hold it back gently, the other hand on the switch.
    The rest move should stop where your hand stops it and hold the arm there. At a terminal the
    run offers the release (step 6). Let its 60 s run out, so torque stays on, and take your
    hand away: the arm should stay where it stopped. The close's line then says the arm is not
    at its rest pose, names the joint furthest from it with where it reads and its goal, says it
    has stopped moving, or that the time ran out, and goes on
    `so torque was left on and it will not fall as it stands`. Until 0.15.0 it said that joint
    and its angles twice. Once is right, and so is twice with two different angles, which is the
    close's own read finding the joint somewhere other than where the move stopped it. Report
    the line as it came, and whether the arm moved after your hand left it.

    That line is also the close reading back what it asked the disconnect to do, where it used
    to assume it. The arm should still be holding once quackd has exited. A line that says
    instead that quackd `could not keep torque on, so the arm was released where it stood` means
    the disconnect let go after all, which an arm quackd built should never do: report it, and
    whether the arm dropped. Then hold it and run `quackd robot release arm-01`, or cut its
    power.

## Handing the arm over, and taking it back

`--by-hand` is one of the two ways a person can have quackd take torque off a robot, and it
takes it off at the one pose the arm is known to hold without any: the fold you recorded in
step 6. The other, `quackd robot release` and the offer at the end of a run that missed its
fold, takes it off wherever the arm stands, and only after telling you to hold the arm.
Everything below rests on that pose being right, which is why these two steps come after the
ones that drove the arm there and back rather than before them. The flag wants the registered
name from step 6 with a pose against it, and a terminal, because somebody has to press Enter.
Without the pose it refuses before anything is touched, and there is no rehearsing this one with
`--dry-run`, because a dry run moves nothing and this takes torque off an arm:

```
✗ error: --by-hand releases the arm at its recorded rest pose, and this arm has none
recorded
  quackd robot rest-pose arm-bare
```

15. **Set the starting pose yourself with `--by-hand`.** Run the lookout for this one. No verb
    in it moves a joint, so the only things that move the arm in the whole step are your own
    hands and the rest move at each end, which is as small as this can be made.

    ```bash
    uv run quackd run lerobot-lookout --robot arm-01 --by-hand
    ```

    The arm is driven to the rest pose first, exactly as in step 7, and then torque comes off
    it there. **Watch that moment with a hand under the arm.** The fold is the shape you chose
    in step 6 because the arm holds it limp, so it should settle and stay where it is. An arm
    that sags or drops further the instant torque goes is telling you the recorded pose is not
    one it holds on its own, and the answer is to fold it somewhere it does and record that
    instead. The one expected exception is a joint the run named as recorded past its travel:
    it is let go at the edge of the travel rather than in the fold, and it is free to drop the
    rest of the way, so have your hand under that joint in particular. This is the only moment
    the hand-off releases it, because that release is refused anywhere but that pose. The
    other way to have torque taken off is one you ask for by name while you hold the arm,
    wherever it stands: `quackd robot release arm-01`, or Enter at the offer a run at a terminal
    makes when its last rest move missed (step 6).

    Then it waits for you, and says what it is waiting for:

    ```
    the arm is yours: torque is off at its rest pose, so lift it, put whatever it needs in
    the gripper, close the gripper on that, hold it where you want the run to start, and
    press Enter
    ```

    That prompt does not say one thing: every body joint has to read inside its calibrated
    travel when you press Enter, and above all a joint the run named as recorded past its
    travel. That one was let go at the edge and may have settled back into the fold, so lift it
    clear first. Press Enter with a joint still past its travel and quackd does not take hold:
    torque stays off, the run stops, and it names the joint to lift inside its travel, saying
    the arm is still limp at its rest pose if you had not lifted it at all.

    Lift the arm, put something in the gripper, squeeze the jaws shut on it with your fingers,
    hold the arm where the work should start, and press Enter. quackd writes the pose you are
    holding as the goal **before** it switches torque back on, writes it again afterwards, and
    reads every joint back, because nothing upstream documents what a servo does with the goal
    it was last told when it is re-energised, and the goal this one was last told is the fold.
    What it read back is printed, and that line is your cue:

    ```
    holding the pose you set, you can let go. It is at elbow_flex 40, gripper 35,
    shoulder_lift -20, shoulder_pan 0, wrist_flex 15, wrist_roll 0
    ```

    **Let go, and watch the arm rather than the terminal.** Whether it stays where you put it
    is the first of the two questions the hand placed start adds at the foot of this page:
    those angles came off `lerobot:mock`, which holds whatever it is told. A joint that moved
    more than five degrees between the two reads is refused by name and the run ends before the
    model gets a turn, so anything quackd accepted moved less than that, and the angles it
    printed are what to read against where you thought you left the arm. A joint you placed
    outside its calibrated travel is refused before torque comes back on at all: the refusal
    names it, where it reads and its travel, the arm stays limp in your hands, and the run ends.
    Check on the arm that nothing after that line moves it or puts torque on it, not the stop,
    not the fold, and not moving the joint back inside while the teardown runs, and that the
    last line says it is limp in your hands. If the refusal instead says quackd could not
    confirm whether the arm has torque, hold the arm as though it may move or drop and cut its
    power; nothing folds it then either.

    The pilot's clock starts when you press Enter rather than at the rest move, so a minute
    spent finding a pencil is not a minute out of `--max-minutes`.

    At the other end the arm is holding whatever pose the run ended in, and you are asked
    before anything opens:

    ```
    the run is over and the arm is holding where it ended. Take hold of whatever is in the
    gripper and press Enter, and the gripper opens before the arm folds up. Leave it and
    the arm folds up with the gripper shut
    ```

    Take hold of what is in the jaws first and then press Enter: the gripper opens while you
    have it, and only then does the arm fold. That question stands for two minutes and then
    answers itself by leaving the gripper shut, so an arm nobody came back to folds up holding
    what it was given rather than dropping it on the bench.

    Once that has worked, do it again with a task that moves: a pencil in the gripper, the arm
    set down on the paper, and a goal short enough to read in one line. That is the second of
    the two the hand placed start adds down there, and nobody has an answer to it either.

16. **Ctrl-C while the arm is still in your hands**, which is the one window quackd has where
    a robot is limp and the software is waiting. Do it on the lookout run again, with the arm
    lifted and held.

    Every ending begins with a stop, and a stop on an arm somebody is holding takes hold of it
    first: torque comes back on where your hand has it rather than where it was released,
    because a goal sent to a limp servo would be a stop that stopped nothing. Then the arm
    folds to its rest pose and lets go there, which is the same motion step 14 ends with.
    Only with every joint lifted inside its travel. With one outside it, a fold recorded past
    the travel that you did not lift the arm out of included, the stop does not take hold:
    you are told so once, naming the joint and where it reads, torque stays off, nothing
    folds, and the arm is yours to put down.

    > [!WARNING]
    > The arm energises under your fingers and then drives itself down. **Keep hold of it
    > through that fold**, and keep your other hand on the switch. It is the one Ctrl-C on this
    > page that starts with the arm in somebody's grip.

    Nothing asks you about the gripper on this path, because the run never reached the pilot.
    The fold drives the five body joints and never the gripper, so whatever you closed the jaws
    on folds up with the arm: take it out before you press Ctrl-C rather than after. The run is
    recorded as an abort like any other, with one `hand` line in its log saying the arm was
    released and nothing after it saying anybody took it, or, where the stop's take-hold was
    refused, a second saying so.

## The gripper, and only then a policy

17. **Close the gripper on something soft and forgiving.** A foam block, not a cup. It should
    stop short of shut, `report_state` should say it is holding, and `stop` should not drop
    it: a hold deliberately leaves the gripper's goal alone. Then `place` to let go.

    Since 0.15.0 that holding is judged on the reads the verbs make and on none of the
    heartbeat's, which reads the arm twice a second whether or not anything is moving, because
    where its reads land among a verb's is chance. The `gripper` verb should say
    `gripper closed on something`. Then call `report_state` straight away, and again after ten
    seconds with nothing sent, and report what the verb said and what each read said about
    holding, with the gripper's value. A grip that stopped between 8 and 90, the band at the foot
    of this page, and still ended the verb on
    `gripper did not close: gripper is at ... with a goal of 0, and it has stopped moving`, or a
    read after it that said it was not holding, is the verbs' own reads being too few to call
    the gripper settled, which is the one thing this change could have broken here: report it.
18. **Hand the arm to a learned policy, and only after every step above.** `pick` and
    `manipulate` reach this arm through a policy server, a process of its own that no
    checkpoint ever leaves: `quackd policy serve` in a second terminal, with
    `quackd[lerobot-vla]` in a Python 3.12 environment, and `--policy-url
    http://127.0.0.1:9875` on the run ([policies.md](policies.md)). Rehearse each command on
    the arm's twin first ([section 17 of the first run](lerobot-first-run.md#17-optional-hand-the-arm-to-a-learned-policy)),
    then run it on the arm. Start with a scripted policy that needs no torch and moves nothing,
    `quackd policy serve --policy scripted:hold`, which holds the arm where it reads, and a
    pilot that asks no model:

    ```bash
    quackd run --goal "hold the arm where it is" --robot arm-01 --policy-url http://127.0.0.1:9875 --controller vla
    ```

    It asks you three times: whether the arm should try at all, since its verdict is always
    `uncertain`, whether to start the segment, since a goal asks about each one, and at the end
    `Did the arm do it?`. The segment should end on a stall within a second or two, with the arm
    where it was. Then `scripted:sweep`, which swings `wrist_flex` 5 degrees either side of where
    it started, one swing every 2 s, and only then a checkpoint you have checked with
    `quackd policy check --bench`.

    What holds the arm while a policy drives it is quackd's, not LeRobot's. It moves no joint
    faster than a verb may, the verbs' 50 degrees a second at the default step, whatever the
    policy's rate. A goal past the travel is clipped and counted rather than refused, and a
    joint reading outside its travel is left out of every goal. It stops a `pick`'s policy the
    moment something is held, and holds the arm on a hot joint, torque off, a dead camera, a
    goal that is not a number, a goal held past the travel, a policy that stops answering or a
    stop from anywhere. **A joint that reads past its travel is the exception** to the speed
    cap: the servo would take any goal for it as the end of the travel and drive there at its
    own speed, which is why a policy's goals leave it out, and a rise a move had already started
    goes on whatever quackd sends. Keep a hand on the switch.

    **Then measure the one number only this bench can give**: how fast the policy's loop runs
    on the real bus while the same laptop infers. `summary.json` of the run has a `policy` block
    with it, `hz` beside `late_ticks`, `starved_ticks` and `round_trip_ms`, and the arm's state
    carries `extras.timing` for every bus call and every tick. Send those back with what
    `quackd policy check --bench` said on the same laptop, and say which policy it was.

## What to report

[Open a LeRobot hardware report](https://github.com/rokbenko/quackd/issues/new?template=lerobot-hardware-report.yml),
which asks for exactly the list below, or a plain issue with `quackd doctor` output and
`runs/<timestamp>-<name>/terminal.txt` from the run. That file is the whole session as plain
text, opening with the command that started it and the version that ran it, so it is usually
the one that answers what happened. Send `transcript.jsonl` beside it when the question is
about one record rather than the session, such as what `report_state` read back off the joint
that moved. The steps you drove from an MCP client have neither, because a session writes
nothing run shaped, and [M14 of the first run](lerobot-first-run.md#m14-what-to-report) lists
the four things that stand in for it there. A report that says it did not work is worth as much
as one that says it did.

The command line at the top of that file has the values of `--api-key`, `--token`,
`--host-token` and `--policy-token` replaced, and a password or a credential-named query
parameter taken out of `--base-url`, `--address`, `--camera-url`, `--decision-url` and
`--policy-url`. Nothing else on the screen is, so read it before you paste it
([SECURITY.md](../SECURITY.md)).

**Four things one afternoon on one bench did not answer**, and which still need a real arm:

- **Whether the holding band is anywhere near right.** quackd calls it holding when the
  gripper is told to close, settles, and settles between 8 and 90 of 100. Nobody has yet seen
  what a real grasp reads. The gripper on 2026-09-15 closed on air, not on an object.
- **What a joint reads in degrees Celsius**, cold and after ten minutes of work. quackd
  refuses to move at 60, below the servo's own 70 cut-off, and both numbers are Feetech's
  documentation rather than anything measured here. Nothing on the bench ran long enough to
  find out what the second number is.
- **Whether 5 degrees an action felt right.** The figure is quackd's own choice for a first
  run, not anything upstream recommends for this arm, and nobody has said whether it looked
  right standing next to the arm.
- **Whether a stall is caught.** Hold a joint gently against its goal and see whether the verb
  fails with where it stopped. It is called once the move's `duration_s` is up, so on a slow
  move the joint pushes that long first. Nobody has done this on purpose yet. It has happened
  once by accident: on 2026-09-23 the rest move drove a folded shoulder into its servo's own
  limit, the joint stopped there, and the rest move's stall check said where. That is the rest
  move's check and not a verb's, so the question is answered for one and still open for the
  other.

**And one the discrete stepper brought with it**, which nobody has any answer to either.
`--decision-llm` is off unless you name one and postdates that afternoon, so leave it off for
every step of this checklist: a first run is about proving the arm, and one more moving part
between you and it is the opposite of what that wants. Afterwards,
`--decision-llm jev --decision-mode shadow` changes nothing about a run and records what the
decision LLM would have chosen on each turn beside what the model actually chose.
Two numbers come out of it that exist nowhere yet, for [`jev`](decision-llms/jev.md) and for
every other decision LLM quackd names alike: how long one takes to answer a real arm's state,
and how often it agrees with the model on one. Each one's page asks for exactly those two
under *How to help*. The task
built for it is `arm-grip-check`, which also happens to be the one that asks the holding-band
question above ([decision-llms.md](decision-llms.md)).

**And two the hand placed start brought with it**, which nobody has any answer to: `--by-hand`
postdates that afternoon and has been exercised against `lerobot:mock` and in the test suite,
and not yet on a real arm.

- **Whether the arm stays where you put it when torque comes back on.** quackd writes the pose
  you are holding as the goal before it enables torque, writes it again after, reads every
  joint back and refuses the run if one of them moved more than five degrees, because nothing
  upstream documents what a servo does with the goal it was last told when it is re-energised.
  Whether a real SO-101 holds, twitches or sags in that second is the thing only somebody
  standing over one can say. Name the joint that moved and how far, and say whether a loaded
  arm behaved differently from an empty one.
- **Whether a gripper you closed with your fingers keeps a pencil through a drawing move.** The
  squeeze your hand left is written back as the gripper's goal when torque returns, and a
  position is not a grip: nothing on this arm reports force, and quackd says it is holding
  nothing on a hand placed start, because closing on an object is what makes this body say
  otherwise. Put a pencil in it, run something that draws, and say whether it was still there
  at the end.

**And one the bench of 2026-09-23 brought with it**, which applies only if a run named a joint
as recorded past its travel:

- **Whether a joint let go at the edge of its travel settles onto its fold.** quackd parks it at
  the edge, releases torque there, and says it is free to settle the rest of the way, which is
  a statement about what quackd does and not about what the joint will do. Whether it drops or
  eases down, and whether one folded past the *top* of its travel settles at all, is for the
  arm's weight and its servos to answer. Say which joint, how far the note said it had to go,
  and what it did, then calibrate folded, record the pose again, and say whether the note went
  away.

**And one the paced `move_joints` brought with it**, which postdates both afternoons and has run
only against `lerobot:mock` and the test suite:

- **Whether a slow move is smooth.** `move_joints` walks its goal out a tenth of a second at a
  time across the `duration_s` it is given, and nothing has watched a servo follow a goal that
  creeps. Say whether a move of several seconds looked like one motion or a staircase, and
  whether it arrived when the time was up.

**And four the simulator and the policy server brought with them**, which only an arm can
answer. Each is an open item in PLAN.md:

- **Whether each joint turns the way the simulator's does.** Nudge each joint a few degrees in
  the positive direction on the arm and on its twin, and say whether they agree. Then read the
  calibrated value at each mechanical stop, which is what says whether a fold recorded past the
  travel can be put on the simulator at all.
- **What the gripper reads on a real pen**, against the band that infers holding. The
  simulator's pen is a shape in a model and says nothing about that band.
- **Where your front and wrist cameras really are**, their placement and field of view, which
  would replace the simulator's default views.
- **How fast a policy's loop runs on the real bus** with the server inferring on the same
  laptop, step 18's number.

**And two with one answer each, from one arm on one laptop.** A second answer is what turns
either of them from an anecdote into a fact:

- **Which end of the gripper's 0..100 range is open.** quackd assumes 100. That arm agreed
  (step 10), and which end yours opens at is still a question about how it was built and
  calibrated rather than about the model.
- **Which OpenCV index the camera turned out to be**, if you brought one, and whether it
  needed `?backend=msmf`. That laptop's webcam was `opencv://1`, then `opencv://2` after a
  replug, at 640x480, and it needed no `?backend=` key. Nobody can guess yours.

The `real` row in [adapter-status.md](adapter-status.md) was flipped on 2026-09-15, and it says
what that arm did and what fell over afterwards. The next report either widens that row or
contradicts it, and the one that contradicts it is worth more.
