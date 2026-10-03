# AlohaMini: the order to try it in

Nothing in quackd has run on an AlohaMini. This is the order to find out in, written so that
each step can only fail in a way you can recover from.

This robot is unusual, and the unusual thing is what makes the order matter: **as upstream
ships it, the arms are limp.** `configure()` disables torque on both arm buses and both
`enable_torque()` calls are commented out. So a stock host is the safest thing to bring up
first, because the arms cannot hold, drop or pinch anything. quackd's own host wrapper is
what switches torque on, and that is when the arms become a thing to think about. Read
[adapters/alohamini.md](adapters/alohamini.md) first.

## Before you power anything

1. **Clear the lift's whole travel**, top and bottom, and the arms' sweep. The lift is a
   600 mm motorised axis and nothing upstream states its speed in mm/s, so quackd's own
   duration estimate for `lift` is an assumption, not a measurement.
2. **Calibrate first.** The lift's velocity operating mode is set only inside `home()`, and
   `home()` runs only when the robot is calibrated. An uncalibrated robot is one where the
   lift will not behave as this page describes.
3. **Know that `home()` leaves the lift moving.** It drives down at full speed and the write
   that would zero that register afterwards is commented out upstream. Something must send a
   zero. quackd sends `stop` as its very first command after connecting, for exactly this
   reason, but if you drive the host by hand before quackd connects, the lift is still
   travelling.

## On the stock host, arms limp

There is no command that runs one verb. Either drive the daemon from an MCP client
(`quackd serve-mcp --robot alohamini:zmq --address tcp://<host>:5555`, then `robot_run_verb`, which is
what these steps assume) or give a model a goal narrow enough to reach one verb
(`quackd run --goal "..." --robot alohamini:zmq --address tcp://<host>:5555 --llm anthropic
--max-steps 3`). `--llm fake` will not do: it answers a free-form goal with a fixed
script that ignores it.

4. Start upstream's own host. `uv pip install 'quackd[alohamini]'`, which installs the
   `quackd-alohamini` package and pyzmq and nothing of upstream's, then

   ```bash
   uv run quackd doctor --robot alohamini:zmq --address tcp://<host>:5555
   ```

   With no torque on the arms, quackd refuses `move_joints`, `gripper` and `home_arms` and
   says why. The base, the lift and `stop` work. This is the state to learn the robot in.
5. `alohamini-lookout`. It moves no wheel, no arm and no lift.
6. **`lift` to a height you can watch**, then `stop` mid-travel. Confirm it actually stops:
   every payload quackd sends carries a lift key, because an action carrying neither leaves
   the servo travelling while the refreshed watchdog keeps it from firing.
7. **`move` at `vx` 0.1, then pull the network cable.** The base and the lift stop within a
   second. The arms are not covered by that watchdog at all — here they are limp anyway,
   which is exactly why this is the safe moment to learn that the deadman's scope is
   `base_and_lift_only`.

## On quackd's host, arms live

8. **Switch to `bridge/alohamini/quackd_alohamini_host.py`.** From here the arms hold their
   last commanded position. Support anything heavy before you start it: an arm somewhere
   awkward will stay there rather than sag, which is usually what you want and occasionally a
   surprise.
9. **Know how this host ends.** Upstream's `disconnect()` disables torque again, so a loaded
   arm falls when the host exits. It exits by itself after about 6000 seconds, and also after
   twenty consecutive over-current reads on any motor.
10. `move_joints`, one arm, one joint, small. The values are a normalised −100..100 range,
    **not degrees**. Then `gripper`, with nothing fragile in it.

## What to report

Whether the arms really were limp on the stock host, what the lift's travel speed actually is
in mm/s, and whether the over-current trip fired before anything you would have wanted it to
catch.

Open an issue with `runs/<timestamp>-<name>/terminal.txt` from whichever `quackd run` you did.
It is the whole session as plain text, opening with the command that started it and the version
that ran it, so it is usually the one file that answers what happened. Send `transcript.jsonl`
beside it when the question is about one record rather than the session. The steps you drove
from an MCP client have neither, because a session writes nothing run shaped, and there the
record is the chat itself.

Read that first line before you paste it. The values of `--api-key` and `--token` are replaced
there, and so is a password or a credential-named query parameter in `--base-url`, `--address`
and `--camera-url`. Nothing else on the screen is ([SECURITY.md](../SECURITY.md)).
