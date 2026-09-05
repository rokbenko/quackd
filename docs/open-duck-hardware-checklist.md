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

The deadman zeroes the duck after 300 ms of silence — and what has to fit inside that
window is not a ping, it is a **camera snapshot**: `go_to` steers on frames, and a fetch of a
30–60 KB JPEG costs at least two round trips. So measure the thing the loop actually waits on:

```bash
for i in $(seq 20); do
    curl -o /dev/null -s -w "%{time_total}\n" http://127.0.0.1:9872/snapshot.jpg
done | sort -n | tail -3
```

> Abort if p99 ping is above about 100 ms, **or** if a snapshot fetch is regularly above
> about 150 ms. Use Ethernet or a USB gadget link instead, or the duck will stutter and stop
> in the middle of steps. quackd holds its last command for one deadman window while a frame
> is in flight, so a slow camera degrades the steering rate before it breaks the gait — but
> only for that long.

## 5b. Prove the camera agrees with the detector

Put a known orange object on the floor at a tape-measured 1.00 m, in front of the duck, and
ask it what it sees:

```bash
quackd run --goal "look once and report what you can see, then stop" \
    --robot open_duck:bridge --address tcp://127.0.0.1:9871 \
    --camera-url http://127.0.0.1:9872/snapshot.jpg --max-steps 3 --provider anthropic
```

This one step catches three different faults at once: a wrong `--rotate`, an inverted colour
order, and a field of view that is not yours. Detections say `(uncalibrated: distance is a
rough guess)` until you pass `--fov-deg`; a Pi Camera Module 2 is about 62.

> Abort on any label but `ball`, or a distance more than about 30 percent from the tape.
> The detector's default geometry is the *simulator's* 90 degree lens, which reads roughly
> half the true distance — enough for `go_to` to announce it has arrived from half a metre
> away, and for `open-duck-scout` to report success on a run its own criterion failed.

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

This is the first thing that moves a leg. `--provider fake` has no script for a free-form
goal, so this one needs a real model:

```bash
quackd run --goal "walk in place with small forward steps, do not turn, then stop" \
    --robot open_duck:bridge --address tcp://127.0.0.1:9871 \
    --provider anthropic --max-steps 6
```

Watch `loop_hz` in the run's own `report_state`. Anything below 35 Hz fails the heartbeat on
purpose, because a starved Pi walks badly with no other symptom — and a *paused* policy
reports about 10 Hz, which quackd now names as a pause rather than blaming the CPU.

Read it from the run, not from a second `quackd doctor` in another terminal: a second client
is fine to have connected, but the run's own state is what the pilot is acting on.

> Abort if the gait is visibly stuttering. That is the deadman tripping, and step 5 is where
> you find out why.

## 9. Test the deadman before you need it

With the duck walking in place on the stand, pull your laptop's Wi-Fi.

> The duck must stop within about a third of a second — the window `doctor` printed at step
> 4, not necessarily 300 ms. **An untested deadman is not a deadman.** Do not go to step 10
> until you have seen this work.

Then test the other shutdown, still on the stand. With it walking in place:

```bash
sudo systemctl stop quackd-duck-bridge
```

> The duck must decelerate to a stand over about half a second, not freeze mid-stride. It
> stays stiff afterwards: stopping the bridge does not de-energise anything, because
> de-energising a standing duck drops it. The power switch is still the only e-stop.

## 9b. Test the abort you will actually reach for

Start the same walk again and press Ctrl-C.

> The duck must stop within a tick, not at the end of the verb. Press it twice if you want
> the process gone immediately.

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

## Five numbers only you can measure

Everything else about this adapter has been read from upstream's source at a pinned commit.
These five cannot be, and each one is currently a guess that quackd is open about. If you
record any of them, say so in the issue — they are the difference between a default someone
chose and a default someone measured.

1. **How long the bridge takes to reach "listening on".** `--patch-watchdog-s` defaults to
   150 s to cover onnxruntime, the servo bus, a two second settle and the IMU on a cold Pi.
   Nobody has timed it. From `journalctl -u quackd-duck-bridge`:

   ```bash
   journalctl -u quackd-duck-bridge -o short-precise | grep -E "Started|listening on"
   ```

2. **camd's real peak memory**, against the `MemoryMax=140M` in its unit. If this is close,
   the cap is wrong and picamera2 will be OOM-killed on a walking duck:

   ```bash
   systemctl show quackd-duck-camd -p MemoryPeak
   ```

3. **What the loop actually leaves you.** `MIN_LOOP_HZ` is 35 against a 50 Hz policy, and
   that floor is calibrated against nothing. Watch `loop_hz` in `report_state` through a
   whole walking run and report the minimum you saw.

4. **Your camera's field of view and colour**, from step 5b. `--fov-deg` defaults to the
   simulator's 90; a Pi Camera Module 2 is about 62. Report the tape-measured distance and
   what `observe` said, at two distances if you can.

5. **The accelerometer, upright and on its side.** This is the one that would give this
   robot fall detection. quackd will not guess it: the axis that reads gravity depends on
   the IMU's remap, on `imu_upside_down`, and on a tare offset, so a wrong guess means a
   confident "not fallen" — worse than admitting blindness. Hold the duck still in each
   pose and record `accelero`:

   ```bash
   python3 -c "
from mini_bdx_runtime.raw_imu import Imu
import time
imu = Imu(sampling_freq=50)
time.sleep(1)
for _ in range(20):
    print(imu.get_data()['accelero'])
    time.sleep(0.25)
"
   ```

   Run it with the bridge **stopped** — the I2C bus, like the serial bus, has one owner.
