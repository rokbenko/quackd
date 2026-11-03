# Which robots carry a passport

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation, and its first board is the NVIDIA Jetson,
> on which nothing of it has run yet ([roadmap.md](roadmap.md)).

A passport is for a robot that will meet robots it does not know, and since 0.29.0 it lives on the
robot it introduces, run by quackd on the robot's own computer.

## Robots that carry one

Bigger mobile robots: humanoids and quadrupeds. They walk the corridors, doorways and pavements other
robots walk, and they meet robots from other makers there. Many carry an NVIDIA Jetson that can run
quackd, and others another Linux computer that can, such as the Qualcomm Dragonwing IQ9 in VinMotion's
Motion 2. One `quackd onboard` on that computer gives the robot its passport, its radio beacon and its
talk ([quackd.md](quackd.md#onboarding)).

## Robots that carry none

- **An arm.** It is fixed in place, and it will rarely meet another robot.
- **A toy-sized robot.** It has no computer on board that can run quackd.

quackd still drives both from a laptop, as it always has. They simply carry no passport, and the
wizard refuses an `arm` embodiment.

## The boards

`quackd onboard` runs on a computer it knows, and calls each kind a board. The board's kind goes
into every passport, `board.kind`, so a robot that meets it knows what it runs on. That is the
computer quackd runs on, and not a controller beside it that keeps the robot walking.

| Board | How quackd knows it | Where it stands |
|---|---|---|
| `jetson` | by itself: the device tree says `nvidia,tegra234`, an Orin, or `nvidia,tegra264`, a Thor, and `/etc/nv_tegra_release` says L4T 36.x, JetPack 6, or 38.x or 39.x, JetPack 7 | the first board, written from NVIDIA's documentation; nothing has run on one |
| `generic` | only when `--board generic` names it, never by itself, and kept once named: what the computer calls itself, from its device tree or DMI, and its passport says `other` | any other robot's Linux computer, written from the kernel's documentation; nothing has run on one |
| `sim` | only when `--board sim` names it, never by itself | a laptop standing in for a robot's computer, for trying it out and for CI; its passport says `sim` |
| another | a plugin, from the `quackd.boards` entry-point group | none exists yet |

A Jetson Xavier, or a Jetson on JetPack 5, is refused, and the refusal says why. Any other computer
is refused unless it is named, and told that `--board sim` runs the same thing as a simulation that
says so, and, on Linux, that `--board generic` names a robot's own computer.

### What a computer needs

Linux and Python 3.11 or newer. BlueZ 5.55 or newer, and 5.62 or newer for a link this robot dials to
write more than 20 bytes at a time. A Bluetooth adapter that can scan, advertise and serve GATT. A
clock no more than 300 seconds behind a passport's `issued` and not yet past its `expires`, which a
computer with no clock battery has only once a network has set it. How fast it is does not matter: a
passport is signatures and Bluetooth, and only a model run on the robot's own computer cares.
`quackd onboard preflight` checks all of it but Python, and the install refuses an older one.

### The generic board

It matches nothing, since nothing can tell a robot's computer from a desk's: `qcom,qcm6490` is a RUBIK
Pi 3 and a Fairphone 5, and an x86 module and a laptop read alike. So it is never found by itself. It
takes the computer's word for its name, `/proc/device-tree/model`, or else the maker and product in
`/sys/class/dmi/id`, and never reads a serial. It refuses a computer whose firmware says it is a
laptop, a tablet or the like, pointing at `--board sim`, and it leaves a Jetson to the Jetson board,
so a Xavier stays refused. A desktop still passes, with its real name in the passport it signs:
`board` is what the software found and the owner signed, and nothing attests it. Named once, it is
kept in the robot's folder, so neither a later command nor the systemd unit needs the flag
([ADR-0074](../adr/0074-a-computer-quackd-cannot-recognise-is-named-and-says-what-it-calls-itself.md)).

### Another board

A board is a small Python object with a name, a way to detect itself that returns its facts or
nothing, a check of its radio, and which radio it uses, BlueZ or the sim. Its facts are a kind, a
model, a system on chip and a release, and the release is shown in `status` and never signed. A
package registers its board under the `quackd.boards` entry-point group, the way an adapter
registers under `quackd.adapters`, and may not call it `jetson`, `sim` or `generic`, which are
quackd's own. A board that must never be guessed, the way `sim` must not, says so, and is then used
only when `--board` names it, and quackd remembers the name unless it is `sim`.

A board quackd would find by itself joins the table above once somebody has run it and written down
what happened. Until then it is a plugin like any other, and quackd claims nothing about it.
`generic` is in the table because `--board` takes it, and it claims nothing either.

## What a reader of its own needs

Somebody writing a reader in another language needs very little: a JSON parser that can report
duplicate members, a base64url decoder, a base58 decoder for the `did:key`, SHA-256 and Ed25519
verification. Every mainstream language has all of them. To take part in meetings it also needs a
Bluetooth LE stack that reports an advert's service data and its signal strength, as phones and
Linux machines with BlueZ do. The [vectors](vectors/README.md) say what the result
must be for each case, and a reader that matches every row reads passports the way quackd does.

## Removed in 0.29.0

<a id="the-ladder"></a>
<a id="translators-reading-robots-that-never-heard-of-it"></a>
<a id="the-other-way-a-passport-inside-an-a2a-agent-card-planned"></a>
<a id="a-raspberry-pi-or-any-linux-robot-planned"></a>
<a id="an-esp32-planned"></a>

Until 0.28.0 this page said any robot could carry a passport. It climbed a ladder from a sticker on a
robot with no software to a robot that talks, and listed recipes for a Raspberry Pi, an ESP32, a ROS
2 robot and an MQTT broker, translators from URDF, VDA 5050, the MassRobotics identity report and
A2A, and a passport inside an A2A Agent Card. The planned ones were never built, and ADR-0073 dropped
them with the rest of phase 7: a passport now lives on a robot that can run quackd itself, and a robot
that cannot carries none. The page as it was is at the
[v0.28.0 tag](https://github.com/rokbenko/quackd/blob/v0.28.0/docs/passport/compatibility.md).
