# MOSS: the order to try it in

quackd has never driven a MOSS rover. This is the order to find out in, written so that each
step can only fail in a way you can recover from. The maker's own rule comes first: "Put it on
blocks for the first run". **Tracks off the ground until step 6, and back on blocks for the
arm.**

MOSS is a prototype, 4.2 kg by the maker's figure for the V0.3 build with the V0.6 hull not yet
weighed, with a lithium battery and, in the maker's words, no certified protection. A turn on the
spot runs both tracks at full power whatever quackd asked, and the arm's torque goes off whenever
its run ends. Read [the adapter's page](README.md) first, the stop contract above all.

## Before you power anything

- **Put the rover on blocks** so both tracks turn freely, and clear the arm's whole sweep. The
  maker's cycle moves the arm out over the front and back over the bin, and nothing checks the
  scene on the way.
- **Know where the emergency stop button is** (the LA16 in MOSS's BOM), and what it cuts on your
  build. Nothing in quackd can tell you that.
- **Know what drops the arm.** `tools/moss-fly.sh stop` ends every dimOS run, the arm's included,
  and the arm's adapter turns its torque off as its run ends. The cockpit runs it when you stop
  the stack from its page, and its idle stop runs it after 300 s idle. quackd never does. You
  may have to, so support the arm first. quackd sends nothing while one of its prompts waits,
  the cockpit's idle keepalive included, so stow the arm before you leave a prompt open.

## The cockpit and the bridge, with nothing moving

On the rover, start a stack first, `moss-explore` for now and without `EXPLORE=1`, then the arm's
coordinator, then quackd's bridge, as [the adapter's page](README.md#before-it-will-answer) and
[`bridge/moss/README.md`](../../../bridge/moss/README.md) say. On the laptop, the token and the
tunnel:

```bash
export QUACKD_MOSS_TOKEN="$(ssh <rover> cat /etc/quackd/moss-bridge.token)"
ssh -L 8904:127.0.0.1:8904 -L 9876:127.0.0.1:9876 <rover>
```

1. **The cockpit answers, and quackd connects.**

   ```bash
   quackd doctor --robot moss:cockpit --address http://127.0.0.1:8904
   ```

   `doctor` connects, `list-verbs` does not. Read back which verbs exist, the stack it found and
   whether the bridge was counted. A refusal names its fix. It starts nothing, so its close
   sends no stop, and on `moss-litter` the mission runs on.
2. **The bridge's own word.** Through the same tunnel:

   ```bash
   curl -s -H "X-Quackd-Token: $QUACKD_MOSS_TOKEN" http://127.0.0.1:9876/hello
   ```

   Check that `publisher.loaded` is true and names `/tele_cmd_vel` on `moss-explore`, that
   `arm.answering` and `arm.cycle` are true, that `track_m` is your rover's track width, and that
   `cameras.nose.heard` is true. `fov_deg` is null unless the stack publishes `camera_info`.

## Talking to it, still on blocks

There is no command that runs one verb. Either drive quackd from an MCP client
(`quackd serve-mcp --robot moss:cockpit --address http://127.0.0.1:8904`, then
`robot_run_verb`, with `--yes` for a verb of the `confirm` class), which is what these steps
assume, or give a model a goal narrow enough to reach one verb.

3. **`moss-lookout`, which moves nothing.**

   ```bash
   quackd run moss-lookout --robot moss:cockpit --address http://127.0.0.1:8904 --llm fake
   ```

   It should say what the nose camera sees and stop.
4. **A soft stop mid goal.** `drive_to` with `x_m` 0.5. On blocks the tracks turn and the rover
   goes nowhere, so the planner keeps driving. Send `stop`: the tracks stop within the ramp,
   half a second with MOSS's launcher, and the cockpit's page shows the goal gone.
5. **Kill quackd mid `move`.** `move` with `vx` 0.1 for ten seconds, then kill the quackd process
   outright (`kill -9`, or End task), so no clean stop goes out. The bridge's own deadman stops
   the tracks 0.3 s after the last twist, plus the ramp. If they keep turning, stop here.

## On the floor

Tracks down, and a hand on the emergency stop for every step that moves the base.

6. **`search_scan` through a full circle, on tape.** Mark the floor at the rover's nose and count
   the steps it takes to come round. Each step is pulses of a full power turn, and how far one
   pulse turns the rover nobody has measured: this is the number most worth sending back.
7. **`drive_to` 0.5 m forward.** The planner's own `goal_reached` ends it. Measure where the
   rover actually stopped.
8. **`explore` for one minute,** in a room. The launcher keeps the explorer within 4 m of where
   exploring started unless you set `MOSS_EXPLORE_RADIUS_M`.
9. **Kill quackd mid `explore`.** The bridge halts the exploration three missed keepalives
   after the last one, within three seconds, since every keepalive declares the bridge's own
   1 s cap: `stop_explore_cmd`, `stop_movement` and a zero. A quackd alive but wedged sends no
   keepalive either, and is halted the same way. Without the bridge nothing would, and the
   explorer would go on to its radius.

## The arm, in the simulator, then on blocks

10. **`pick_and_drop` in MuJoCo, then on the real arm.** First run the arm's coordinator as
    `.venv/bin/dimos --simulation mujoco run moss-dimos.moss-arm`, which the bridge sees, and it
    passes `--sim` on to the maker's script. The cycle takes about 37 s and ends in the drive
    pose. Then the real arm, the rover back on blocks, its sweep clear and a hand ready under it.
11. **Stop it mid cycle.** Send `stop` while the arm is out. The bridge stops the script, cancels
    the trajectory and reads the joints twice: the arm should hold where it was, under torque.
    `stow_arm` then brings it home along the cycle's own return.
12. **Kill quackd mid cycle.** The bridge cancels the cycle that session started three missed
    keepalives later, and the arm holds. Then `stow_arm` from a fresh session, or support the
    arm and stop its run.

## The litter mission, last

13. **`collect_litter`, then stop it.** Start the stack with `tools/moss-fly.sh litter`, without
    `EXPLORE=1`, with the arm's coordinator running. `collect_litter` is `dangerous`, so it
    needs a `.duck` of your own that allows it, run in a terminal where you answer its confirm
    yourself. Give it a minute with one can in view, then press `q`, or Ctrl-C, in the terminal
    the run is in: quackd's kill switch sends `stop`. The mission's stop goes out first and the
    base stops at once, then its own pick script is ended and the arm holds, and the run ends
    with a note that the mission is stopped and runs again only after the stack restarts. If the
    note says the stop was not confirmed, stop the stack on the rover with the arm supported.

## What to report

How far one pulse turned the rover in step 6, where `drive_to` stopped in step 7, whether the
deadman and the bridge's halt behaved as described in steps 5 and 9, whether the arm held in
steps 11 and 12, and whether the mission's stop was confirmed in step 13. And anything that made
the rover or the arm move in a way this page did not predict. The adapter page's "How to help"
says where that goes.

Attach `runs/<timestamp>-<name>/terminal.txt` from whichever `quackd run` you did. It is the
whole session as plain text, opening with the command that started it and the version that ran
it. Send `transcript.jsonl` beside it when the question is about one record rather than the
session. The steps you drove from an MCP client have neither, and there the record is the chat
itself. The value of `--token` is replaced in that first line, and the bridge's token never goes
in an address, so neither is in what you send.
