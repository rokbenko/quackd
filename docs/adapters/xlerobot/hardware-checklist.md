# XLeRobot: the order to try it in

Nothing in quackd has run on an XLeRobot. This is the order to find out in, written so that
each step can only fail in a way you can recover from. **Wheels on blocks until step 9.**

This is a 12 kg cart with two arms and no brakes. The power station's switch is the only
e-stop, and nothing reports a battery, so no run will ever abort on a flat one. Read
[adapters/xlerobot.md](adapters/xlerobot.md) first.

## Before you power anything

1. **Put the cart on blocks** so the three wheels turn freely, and clear the full sweep of
   both arms. Support anything an arm is holding: `stop` zeroes the wheels and leaves the
   arms where they were, which is a hold under torque, not a brake.
2. **Know where the power station's switch is.** It is the only thing that stops everything,
   and it is not on the network.
3. **Uncomment the host.** `XLerobotHost` and its client are commented out of upstream's own
   package `__init__`, so a fresh checkout exposes neither.

## The host, with no robot moving

4. Start the host on the machine wired to the two Feetech buses. **If it seems to hang, it
   has not**: `XLerobot.connect()` blocks on a bare `input()` whenever a calibration file
   exists. Press enter.
5. **Note the clock.** The host exits by itself after 3600 seconds and there is no systemd
   unit, autostart or supervisor anywhere upstream. Its exit is upstream's `disconnect()`, which
   drops arm torque, so whatever an arm holds at the hour mark falls. A session longer than
   an hour needs it restarted, and quackd will report the silence as a heartbeat failure.
6. `uv pip install 'quackd[xlerobot]'`, which installs the `quackd-xlerobot` package and
   pyzmq and nothing of upstream's, then

   ```bash
   uv run quackd doctor --robot xlerobot:zmq --address tcp://<host>:5555
   ```

   Read back which verbs exist. `doctor` connects; `list-verbs` does not, so it describes the
   stock cart, not yours.

## Talking to it, still on blocks

There is no command that runs one verb. Either drive the daemon from an MCP client
(`quackd serve-mcp --robot xlerobot:zmq --address tcp://<host>:5555`, then `robot_run_verb`, which is
what these steps assume) or give a model a goal narrow enough to reach one verb
(`quackd run --goal "..." --robot xlerobot:zmq --address tcp://<host>:5555 --llm anthropic
--max-steps 3`). `--llm fake` will not do: it answers a free-form goal with a fixed
script that ignores it.

7. **Check the signs before you trust anything.** `move` with `wz` only, on blocks, and watch
   which way the wheels turn. Upstream's wire takes **degrees per second** and quackd's `wz`
   is radians per second, so quackd converts: if that conversion were ever wrong the error
   would be 57 times, and on blocks you would see it rather than feel it.
8. **Prove the deadman, and know what it does not cover.** With the wheels turning, pull the
   network cable. The three wheels stop within 500 ms. The fourteen arm and head servos keep
   holding their last goal under torque, because upstream's watchdog calls `stop_base()` and
   nothing else. That is the whole safety authority this robot has.

## On the floor

9. Wheels down, `move` at `vx` 0.1 for under a second, hand on the power switch. Upstream's
   own teleop opens at 0.1 m/s and quackd's shared default is 0.15, so ask for 0.1 explicitly.
10. **Arms last, and one joint at a time.** `move_joints` values are a normalised −100..100
    range, **not degrees**: `use_degrees` is False upstream. A value read as degrees would be
    a different pose entirely.

## The camera, if you have one

11. A stock cart is **blind**: every entry in `xlerobot_cameras_config` is commented out, so
    `observe`, `go_to`, `search_scan` and `approach_and` are not declared and
    `xlerobot-lookout` refuses before a verb runs. To change that you must edit the config,
    and the shipped one points `right_wrist` and `head` at the same `/dev/video2`.
12. Enabling a camera makes the whole robot depend on it — see
    [adapters/xlerobot.md](adapters/xlerobot.md#enabling-a-camera-makes-the-whole-robot-depend-on-it),
    which is the failure that looks exactly like a dead host. If red and blue come out
    swapped, add `?swap_colour=0` to the address.

## What to report

Whether the turn direction and the arm units were right, whether the deadman behaved as
described, and anything that made the cart move in a way this page did not predict. The
adapter page's "How to help" says where that goes.

Attach `runs/<timestamp>-<name>/terminal.txt` from whichever `quackd run` you did. It is the
whole session as plain text, opening with the command that started it and the version that ran
it, so it is usually the one file that answers what happened. Send `transcript.jsonl` beside it
when the question is about one record rather than the session. The steps you drove from an MCP
client have neither, because a session writes nothing run shaped, and there the record is the
chat itself.

Read that first line before you paste it. The values of `--api-key` and `--token` are replaced
there, and so is a password or a credential-named query parameter in `--base-url`, `--address`
and `--camera-url`. A query parameter named for something else survives, so the
`?swap_colour=0` from step 12 comes back as you typed it. Nothing else on the screen is
([SECURITY.md](../SECURITY.md)).
