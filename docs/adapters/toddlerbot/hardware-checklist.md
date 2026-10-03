# ToddlerBot: the order to try it in

Nothing in quackd has run on a ToddlerBot. This is the order to find out in, written so that
each step can only fail in a way you can recover from. **Feet off the ground until step 13.**

This robot is not a duck. It weighs about 3 kg, it cannot get up if it falls, and upstream's
own shutdown path disables torque with no lowering and no ramp. Read
[adapters/toddlerbot.md](adapters/toddlerbot.md) before you start.

quackd runs on your laptop and never on the robot. The Jetson on the robot's back holds
quackd's ToddlerBot daemon, and it can also hold a model server and quackd's host daemon, which
`--host` reaches from the laptop ([jetson.md](jetson.md)). The host daemon runs there with
`--camera none`, because the ToddlerBot daemon owns the robot's cameras. A model server or a
detector saturating that board is exactly the load that can starve the fifty hertz loop, so
bring the robot up with nothing else running on it, and add the others once these steps have
passed.

## Before you power anything

1. **Put it on the safety stand.** Upstream ships an aluminium extrusion frame for exactly
   this. Every step below assumes the feet are off the ground.
2. **Calibrate the zeros.** Run upstream's `calibrate_zero` and confirm
   `toddlerbot/descriptions/<robot>/motors.yml` exists. It is gitignored, so a fresh clone
   never has one, and without it every commanded angle is offset by however your robot was
   assembled. quackd's daemon refuses to actuate without it, on purpose.
3. **Know where the power switch is**, and be able to reach it without leaning over the robot.

## The daemon, with no robot attached

4. `python quackd_toddlerbot_bridge.py --robot toddlerbot_2xc --fake --once`
   Confirms the daemon imports and reports its capabilities. Nothing is energised.
5. `python quackd_toddlerbot_bridge.py --robot toddlerbot_2xc --fake` and, from your laptop,
   `uv run quackd doctor --robot toddlerbot:bridge --address tcp://<host>:9873`. The daemon
   binds loopback by default, so `<host>` here and below is `127.0.0.1` through the ssh
   tunnel SECURITY.md recommends, unless you started it with `--host`. That is the daemon's
   own flag, the address it binds, and not quackd's `--host`, which names a board's host
   daemon.
   Confirms the socket, the handshake, the token and the version check, still with no robot.

## The daemon, on the robot, not moving

6. Start the daemon for real. It will construct upstream's `RealWorld`, **which energises
   every motor the moment it returns.** Expect the robot to stiffen. If it hangs here, its
   constructor is busy-waiting on a silent IMU with the motors already live: kill it and check
   the IMU before anything else.
7. `uv run quackd doctor --robot toddlerbot:bridge --address tcp://<host>:9873`
   Read the verbs back, this time from the daemon: the ones it lists beyond the static
   description are what this build has. If `move` is absent, you have no walk checkpoint
   staged, which is the normal state of a fresh install. Everything else should be there.
8. `uv run quackd run toddlerbot-lookout --robot toddlerbot:bridge --address tcp://<host>:9873`
   Nothing in this task's allowlist moves a leg, an arm or the waist. It looks around with the
   head and reports. **This is the first thing to point at a real robot.**

## Moving, still on the stand

There is no command that runs one verb. Either drive the daemon from an MCP client
(`quackd serve-mcp --robot toddlerbot:bridge --address tcp://<host>:9873`, then `robot_run_verb`, which is
what these steps assume) or give a model a goal narrow enough to reach one verb
(`quackd run --goal "..." --robot toddlerbot:bridge --address tcp://<host>:9873 --llm anthropic
--max-steps 3`). `--llm fake` will not do: it answers a free-form goal with a fixed
script that ignores it.

9. `stand`. Watch the whole slew. It should take seconds, not snap. If it snaps, stop and say
   so in an issue: the rate limit is not doing its job.
10. `perform hold`, then `perform kneel`. Keyframes, open loop, on the stand.
11. **Pull the network cable mid-move.** The daemon's deadman should slew the robot to the
    safe pose and hold it there. It must not go limp and it must not freeze mid-pose. This is
    the single most important thing to confirm, because on this body silence means hold
    forever and quackd's daemon is the only thing that makes it mean anything else.
12. **Send the daemon `SIGTERM`.** It should settle to the safe pose first and only then
    release. Upstream's own exit path does not do this, which is why the daemon exists.

## Off the stand

13. Only now, and only with a hand ready to catch it. Feet on the ground, `stand`, then
    `perform hold`.
14. If you have a walk checkpoint staged, `move` at the smallest velocity that does anything,
    for well under a second, with the robot on a mat.

## What to report

Open an issue with `runs/<timestamp>-<name>/terminal.txt` from whichever `quackd run` you did.
It is the whole session as plain text, opening with the command that started it and the
version that ran it, so it is usually the one file that answers what happened. Send
`transcript.jsonl` beside it when the question is about one record rather than the session. The
steps you drove from an MCP client have neither, because a session writes nothing run shaped,
and there the record is the chat itself.

Read that first line before you paste it. The values of `--api-key` and `--token` are replaced
there, and so is a password or a credential-named query parameter in `--base-url`, `--address`
and `--camera-url`. Nothing else on the screen is ([SECURITY.md](../SECURITY.md)).

The four things that most need a real robot:

- **Does the safe-pose slew work from a crawl or a prone start?** Untested and unknown. It is
  the largest remaining unknown in the whole adapter.
- **What tilt angle actually means fallen?** quackd guesses 50 degrees.
- **Are the neck axes what the motor names imply?** yaw and pitch are inferred, not stated.
- **Does a calibrated zero survive a restart?** Reading upstream did not settle whether
  `initialize` re-latches it.

Only flip the `bridge` row in [adapter-status.md](adapter-status.md) once a real robot has
done it, and say in the same commit what it did.
