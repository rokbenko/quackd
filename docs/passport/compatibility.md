# Which robots carry a passport

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation, and its first board is the NVIDIA Jetson,
> on which nothing of it has run yet ([roadmap.md](roadmap.md)).

A passport is for a robot that will meet robots it does not know, and since 0.29.0 it lives on the
robot it introduces, run by quackd on the robot's own computer.

## Robots that carry one

Bigger mobile robots: humanoids and quadrupeds. They walk the corridors, doorways and pavements other
robots walk, they meet robots from other makers there, and nearly all of them carry an NVIDIA Jetson
that can run quackd. One `quackd onboard` on that Jetson gives the robot its passport, its radio
beacon and its talk ([quackd.md](quackd.md#onboarding)).

## Robots that carry none

- **An arm.** It is fixed in place, and it will rarely meet another robot.
- **A toy-sized robot.** It has no computer on board that can run quackd.

quackd still drives both from a laptop, as it always has. They simply carry no passport, and the
wizard refuses an `arm` embodiment.

## The boards

`quackd onboard` runs on a computer it knows, and calls each kind a board. The board's kind goes
into every passport, `board.kind`, so a robot that meets it knows what it runs on.

| Board | How quackd knows it | Where it stands |
|---|---|---|
| `jetson` | by itself: the device tree says `nvidia,tegra234`, an Orin, or `nvidia,tegra264`, a Thor, and `/etc/nv_tegra_release` says L4T 36.x, JetPack 6, or 38.x or 39.x, JetPack 7 | the first board, written from NVIDIA's documentation; nothing has run on one |
| `sim` | only when `--board sim` names it, never by itself | a laptop standing in for a robot's computer, for trying it out and for CI; its passport says `sim` |
| another | a plugin, from the `quackd.boards` entry-point group | none exists yet |

A Jetson Xavier, or a Jetson on JetPack 5, is refused, and the refusal says why. Any other computer
is refused too, and told that `--board sim` runs the same thing as a simulation that says so.

### Another board

A board is a small Python object with a name, a way to detect itself that returns its facts or
nothing, a check of its radio, and which radio it uses, BlueZ or the sim. Its facts are a kind, a
model, a system on chip and a release, and the release is shown in `status` and never signed. A
package registers its board under the `quackd.boards` entry-point group, the way an adapter
registers under `quackd.adapters`, and may not call it `jetson` or `sim`, which are quackd's own. A
board that must never be guessed, the way `sim` must not, says so, and is then used only when
`--board` names it.

A new board joins the table above once somebody has run it and written down what happened. Until
then it is a plugin like any other, and quackd claims nothing about it.

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
