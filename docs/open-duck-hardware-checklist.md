# Open Duck Mini v2: the first hardware run

Nobody has run quackd against a physical Open Duck Mini v2. If you have one, this page is
the order to do it in. Every step has an abort condition, and they are ordered so that the
duck's feet do not touch the ground until step 10.

**Before anything else.** This robot has no get-up-after-fall policy, **and nothing detects a
fall.** The bridge does not read the IMU — that bus has one owner, and it is upstream's own
loop — so `posture` reads `unknown` whether the duck is upright or on its side, and no verb
refuses because it has gone over. **You are the fall detector.** Work with the duck on a
stand, keep a hand near the power switch, and do not skip the stand.

quackd says this about itself rather than leaving you to find out: every observation the
pilot sees carries `fall-blind`, `quackd doctor` prints it, and the first run that could move
a leg on a fall-blind robot asks you to confirm you are watching before it starts.

## 1. Upstream's own teleop works first

Drive the duck with its gamepad, the way its README says, with quackd nowhere in sight.

> Abort if it does not walk. Something in the build, the calibration or the offsets is
> wrong, and adding quackd will only make it harder to see.

## 2. Nothing else owns the serial bus

```bash
pgrep -af v2_rl_walk_mujoco.py
systemctl status quackd-duck-bridge
```

The Feetech bus has exactly one owner, and quackd's daemon **replaces** however you started
the walk script before.

> Abort if anything is still holding `/dev/ttyACM0`.

## 3. What the bridge thinks your duck is

```bash
python /opt/quackd/quackd_duck_bridge.py check
```

Read the `capabilities` block against what you actually soldered, and the `limits` block
against upstream's numbers. Then read the seven element command layout in
[`docs/adapters/open_duck.md`](adapters/open_duck.md) and satisfy yourself it matches what
upstream's teleop sends.

> Abort if the capabilities are wrong. They come from your `duck_config.json`, and they
> decide which verbs exist.

## 4. The protocol, with no robot at all

On the Pi, in two terminals. The camera first, so the bridge has a URL to advertise:

```bash
python /opt/quackd/quackd_duck_camd.py --fake --size 256 --seconds 300
python /opt/quackd/quackd_duck_bridge.py serve --fake --seconds 120 \
    --camera-url http://127.0.0.1:9872/snapshot.jpg
```

`--fake` paints a duck's eye view with a ball on the floor, so `quackd run open-duck-scout`
should complete against it. If it does, everything except the robot itself works.

On your laptop, forwarding **both** ports, because the bridge binds loopback and so does the
camera. Export the token `install.sh` wrote, or pass `--token`:

```bash
ssh -L 9871:127.0.0.1:9871 -L 9872:127.0.0.1:9872 your-pi
export QUACKD_DUCK_TOKEN="$(ssh your-pi sudo cat /etc/quackd/duck-bridge.token)"
quackd doctor --robot open_duck:bridge --address tcp://127.0.0.1:9871
```

`doctor` with an address connects and prints what your duck actually reported: its
capabilities, which verbs it does and does not have, and whether the loop is healthy. Read
that against what you soldered. `quackd list-verbs` will not do: it reads the static
description of a fully built duck, not yours.

> Abort on a protocol mismatch. Update whichever side is older rather than working around it.
> Abort if it asks for a token you do not have: the installer wrote one on the robot.

## 5. Measure the link before you trust it

```bash
ping -c 100 your-pi
sudo iw dev wlan0 set power_save off      # on the Pi, if it was on
```

The deadman zeroes the duck after 300 ms of silence.

> Abort if p99 latency is above about 100 ms. Use Ethernet or a USB gadget link instead, or
> the duck will stutter and stop in the middle of steps.

## 6. Dry run, feet still off the ground

```bash
quackd run open-duck-lookout --robot open_duck:bridge --address tcp://127.0.0.1:9871 \
    --camera-url http://127.0.0.1:9872/snapshot.jpg --dry-run
```

Verbs run, nothing moves. `--camera-url` goes on this side too when you are tunnelling: the
bridge advertises a URL from its own point of view, which is not routable from your laptop.

## 7. The head only, feet still off the ground

```bash
quackd run open-duck-lookout --robot open_duck:bridge --address tcp://127.0.0.1:9871
```

Nothing in this task's allowlist moves a leg. If you started the daemon without
`--enable-head`, `gaze` does not exist and the task drops it and reports what it can see
without moving, which is a perfectly good first result and the one to prefer. Only add
`--enable-head` once everything else works.

> Abort on any servo whine, buzzing or stall. Head control is upstream-flagged as
> experimental and it can break the head.

## 8. Walk in place, feet still off the ground

Watch `loop_hz` while it runs, either in a second terminal with
`quackd doctor --robot open_duck:bridge --address tcp://127.0.0.1:9871` or in the run's own
`report_state`. Anything below 35 Hz fails the heartbeat on purpose, because a starved Pi
walks badly with no other symptom.

## 9. Test the deadman before you need it

With the duck walking in place on the stand, pull your laptop's Wi-Fi.

> The duck must stop within about a third of a second. **An untested deadman is not a
> deadman.** Do not go to step 10 until you have seen this work.

## 10. Feet down

Clear floor, hand on the power switch:

```bash
quackd run open-duck-scout --robot open_duck:bridge --address tcp://127.0.0.1:9871 \
    --camera-url http://127.0.0.1:9872/snapshot.jpg --max-steps 10
```

## What to send

Open an issue with the Open Duck hardware report template, and attach:

1. `quackd doctor --robot open_duck:bridge --address ...`, in full. It carries what your
   duck reported at connect, which is how we know what was actually tested.
2. `python /opt/quackd/quackd_duck_bridge.py check`, in full. It carries your capabilities
   and limits, which is how we know what was actually tested.
3. `git rev-parse HEAD` inside your `Open_Duck_Mini_Runtime` checkout, and which walk policy
   you used.
4. `journalctl -u quackd-duck-bridge` for the run.
5. The first 40 lines of `transcript.jsonl`.
6. Which step you reached, what the duck physically did, and **whether you tested the
   deadman in step 9**.
7. A video or a GIF, if you can.

A report earns a row on this adapter's page and our thanks. Only a run on the maintainer's
own duck flips a status to ✅, which is the same rule every other adapter lives under.
