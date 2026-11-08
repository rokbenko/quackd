# quackd's AlohaMini host

Run this on the robot **instead of** upstream's own host, if you want the arms to work.

## Why it exists

Upstream's `AlohaMini.configure()` calls `disable_torque()` on both arm buses, and both of its
`enable_torque()` calls are commented out. Nothing else in the driver turns torque back on: the
only `Torque_Enable` write in the whole package is a zero inside `lift_axis.home()`, and
neither `configure_motors` nor `write_calibration` touches it.

So on a stock host the arms are limp. A joint command moves nothing, and a stop could not hold
them even if it wanted to. quackd will not pretend otherwise: without this wrapper it refuses
`move_joints`, `gripper` and `home_arms` and says why. The base, the lift and `stop` work either
way.

## What it does

It wraps upstream's own host loop rather than reimplementing it, because that loop reads the
cameras, the motor currents and the over-current trip, and a transcribed copy would drift.
Three things happen around it:

1. **Arm torque is enabled after connect**, on both buses.
2. **The lift is stopped once, immediately.** `home()` runs during connect, drives the lift down
   at full speed, and the write that would zero that register afterwards is commented out
   upstream. Until something writes a zero, the lift is still travelling.
3. **Every observation gains three fields** so quackd can tell this host from a stock one:
   `quackd_arm_torque`, `quackd_calibrated` and `quackd_host_version`.

## Running it

It needs upstream installed on the robot, in upstream's own environment, at the commit quackd
was read against:

```bash
git clone https://github.com/liyiteng/lerobot_alohamini
cd lerobot_alohamini
git checkout ab4462b713aeb24d0473f1ec6c8812290ab19510
pip install -e ".[all]"
```

A host from upstream's `main` refuses every payload quackd sends without a lift height, its stop
among them, which [the adapter's page](../../docs/adapters/alohamini/README.md) says more of.

Then, from that environment, with this file wherever you like:

```bash
python quackd_alohamini_host.py --robot_model alohamini2
```

Every other argument is passed straight through to upstream's host, so `--no_follower` and
`--profile_timing` work as they always did. `--check` installs the wrappers and exits, which is
a quick way to confirm the imports resolve before you power the arms.

## Rules this file lives by

- **It never imports quackd.** quackd's dependencies do not belong on a robot, and a test
  enforces this by reading the file.
- **It ships in the sdist and never in the wheel**, so `packages` stays `["quackd"]`.
- **It is testable with no hardware.** The two functions that do the work take any object with
  the right attributes, so `tests/test_alohamini_host_wrapper.py` drives them with fakes.

## Safety

Enabling torque makes the arms hold their last commanded position. If an arm is somewhere
awkward when you start this, it will stay there rather than sag, which is usually what you
want and occasionally a surprise. Support anything heavy before you start it, and remember
that upstream's `disconnect()` disables torque again, so a loaded arm falls when the host
exits. The host also exits by itself after 6000 seconds and after twenty consecutive
over-current reads.
