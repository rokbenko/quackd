# ADR-0074: A computer quackd cannot recognise is named, and says what it calls itself

**Status:** accepted · **Date:** 2026-11-03 · Amends [ADR-0073](0073-a-robot-carries-its-own-passport-and-its-owner-signs-it.md) (a computer that is not a Jetson need not be a plugin) · Implemented in `quackd/boards/generic.py`, `quackd/boards/linux.py` and `quackd/onboard/store.py` · Documented in [docs/passport/compatibility.md](../passport/compatibility.md#the-generic-board)

## Context

ADR-0073 put a passport on the robot it describes, supported one kind of computer, an NVIDIA Jetson,
and left every other to a plugin, on the grounds that nearly all the robots that meet strangers carry
a Jetson. That overstated it. VinMotion's Motion 2 runs a Qualcomm Dragonwing IQ9, and boards for
humanoids now come with Qualcomm, Rockchip, Intel and AMD parts, so a robot's owner could reach only a
sentence telling them to try a simulation.

What Robot Passport asks of a computer is small: Linux and Python 3.11 or newer, BlueZ 5.55 or newer
with an adapter that can scan, advertise and serve GATT, and a clock no more than 300 seconds behind
a passport's `issued` and not past its `expires`. Compute does not come into it, since a passport is
signatures and Bluetooth, and only a model run on the same computer cares.

The computer's identity is the hard part. Linux has the strings. A device tree says `model` and
`compatible`, and x86 and UEFI computers have `/sys/class/dmi/id`. But a string that says what a
computer is does not say what it is for. `qcom,qcm6490` is a RUBIK Pi 3 and it is a Fairphone 5, and
`rockchip,rk3588` is dozens of boards. The DMI strings of an x86 module are not published and read
like a laptop's. No robot's computer says it is one, and quackd's rule is that a laptop never passes
for a robot.

## Decision

**One board for any other Linux computer, found only when it is named.** `--board generic` is
quackd's own, explicit-only as `sim` is, and no plugin may take its name. It matches no string, so
there is no table of boards to go stale and nothing for a person's computer to resemble by accident.

**It takes the computer's word, and says `other`.** `model` is `/proc/device-tree/model`, or else
the maker and product in `/sys/class/dmi/id`, or else the maker and board where firmware left the
product as its template had it, by systemd's list of names that mean nobody filled them in. `soc` is
the last entry of the device tree's `compatible`. Neither is invented: a computer that names itself
nowhere is said to, in a sentence that names no model. The system's name from `/etc/os-release` is
shown and never signed, as L4T is. It never reads a serial or a uuid, which the kernel keeps from
everyone but root, since a passport says what a computer is and not which one.

**It refuses what says it is no robot's, and allows the rest.** A firmware that says laptop, tablet,
handheld or the like, in the device tree's `chassis-type` or the SMBIOS number in
`/sys/class/dmi/id/chassis_type`, is refused and pointed at `--board sim`. Everything else passes,
because no robot's computer says it is a robot's. A desktop passes too, and what stops it passing for
something else is that its passport carries its real name.

**A computer the Jetson board knows stays the Jetson board's.** One it accepts is told to leave out
`--board`, so no Jetson signs `other`, and one it refuses is refused here too, for the same reason, so
a Xavier does not come in by this door. The exception is a chip it supports with no release file at
all, which is no JetPack it can read. Jetson's own detection does not change.

**Named once, it is remembered.** The wizard writes the name to `board` in the robot's folder, and
the three places that ask which board this is read it. The unit starts `quackd onboard serve` with
no flag, every hint says `quackd onboard sign` with no flag, and `status` and the service's own
messages never learn a `--board`, so asking for the flag everywhere could not have worked. `sim` is
never kept, by the wizard or by a file written by hand, so no file can make a robot take itself for a
simulation. A flag still wins. The file picks which board is asked and says nothing about the
computer, so `robot.yaml` still cannot claim a Jetson on a laptop.

**The clock gets looked at.** A board with no clock battery is likelier off a Jetson, and a wrong
clock refuses good passports and signs wrong dates. Preflight warns when `timedatectl` says nothing
has set the clock from a network or the kernel lists no clock that survives a reboot, and signing
refuses a clock before any passport quackd signs.

## Why not

**A new `board.kind` per vendor, in specification 1.1.** Readers only show `kind`, and its one use is
that a brain knows a robot is simulated. `model` and `soc` already name the hardware. A reader of 1.0
would read a new kind as `other` anyway, so this waits at least until somebody has run a board, and it
may never be needed.

**Detecting boards by their device tree strings.** That is `qcom,iq10-rrd` and the like, each cited to
the kernel, and it is the next step for a board somebody has run. Nothing here has been read off one,
and vendor images may ship other strings than the kernel's. A chip's string alone would pass a phone.

**Vendor boards as packages first.** Through `quackd.boards` a plugin can do all this today, and a
package per board is a lot of packaging for what one board reads from any computer. A plugin is the
right place for a board that finds itself, once one has run.

**Allowing a laptop, since its passport would say what it is.** `sim` is what a laptop is for, and its
passport says so. A laptop on `generic` would be neither a simulation nor a robot's computer.

**The name in `robot.yaml`.** ADR-0073 keeps `board` out of the one file so that it can never claim a
computer. A file of its own that picks a board and claims nothing keeps that true.

**Refusing everything with no sign of being a robot's.** Nothing has such a sign, so that is refusing
everything.

## Consequences

- **Nothing in this decision has been run on a robot's computer.** It is written from the kernel's and
  systemd's sources and tested on files. One stock Ubuntu 24.04 container ran the wizard, `sign`,
  `preflight` and `serve` through it, with no device tree, no DMI, no Bluetooth and no systemd. Every
  vendor image's strings, real DMI values, and a real adapter are unmeasured.
- **A passport's `board` is still a claim.** The software found it and the owner signed it, and nothing
  attests it. Anybody can run a changed quackd, so a reader must not trust `board` for anything but
  what it says.
- **A firmware update can rename a computer.** Its passport then wants signing again, and says so.
- **An IGX reads as a Jetson today, if it has the release file.** `jetson.py` keys on the chip and on
  `/etc/nv_tegra_release`, and IGX-SW 2.0 is BSP 38.5.0, so a `nvidia,tegra264` with that file says
  `kind: jetson`, though NVIDIA does not brand it a Jetson. Without the file it is refused as a Thor,
  and `generic` takes it. No NVIDIA page says which, IGX Thor's compatible is not in mainline, and this
  decision leaves it until somebody with one can look.
- **The radio still opens `hci0`.** Preflight checks the first adapter the kernel lists, so a computer
  whose only adapter is `hci1` passes preflight and gets no radio. It is older than this decision.
