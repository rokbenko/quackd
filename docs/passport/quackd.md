# Robot Passport in quackd

> [!NOTE]
> quackd 0.23.0 is being built to implement Robot Passport, and this page fills in as each part
> lands. Built so far: a passport for every robot from its manifest, `quackd passport show`,
> `check`, `set`, `owner`, `serve` and `qr`, `quackd meet`, and passports in `quackd discover`.
> Signing and stamps, pinning and talk are described below as what they will do, with no output
> until they have run. [roadmap.md](roadmap.md) tracks it.

quackd is the first implementation of Robot Passport. It does four things with it:

- **It builds a passport for every robot it drives**, from the robot's manifest, so nobody types
  one. That covers every body quackd publishes and every adapter somebody else writes
  ([passport.md](passport.md#from-a-quackd-manifest-to-a-passport)).
- **It publishes them**, at the well-known URL, over DNS-SD beside its own `_quackd._tcp`, on an
  MQTT broker, and as a QR sticker to print ([discovery.md](discovery.md)).
- **It reads other robots' passports**: from a URL, a host, a picture of a sticker, a broker or a
  ROS robot, under the fetch rules, labelling every claim ([trust.md](trust.md)).
- **It talks for its robots**: `hello`, `say` and `share`, and it decides every `ask` a peer sends
  with fixed code rather than the pilot ([talk.md](talk.md#what-quackd-does-with-an-ask)).

## A robot's passport

`quackd passport show` prints the passport quackd builds for a robot, a registered name or an
`adapter:backend`, from its manifest and from what its owner said. The first time anything asks
for a robot's passport, quackd makes the robot a key:

```
$ quackd robot add duck-01 microduck:mock
✓ added duck-01: microduck:mock
  quackd run <duck> --robot duck-01
$ quackd passport show --robot duck-01
made a key for duck-01 in /home/you/.quackd/keys/robots/duck-01.json
┌─ 🦆 passport duck-01 ───────────────────────────────────────────────────────┐
│ id        did:key:z6Mkv3hxGH5oXSMGTXerm5dLqrxNzYQ4ZzkTVhuxyJhqFSUo  (short  │
│           id 84032200fad67519)                                              │
│ claim     unsigned: a claim                                                 │
│ maker     pollen-robotics microduck                                         │
│ software  quackd 0.21.0                                                     │
│ body      biped, legged, beak, 0.8 kg (official), 0.25 m (official), 0.3    │
│           m/s at most                                                       │
│ sensors   camera, battery, odometry, imu, tof                               │
│ skills    report_state, stop, say, move, observe, go_to, search_scan,       │
│           approach_and, sit, stand, kick, grab, stand_up, quack, gaze,      │
│           watch                                                             │
│ courtesy  pause, identify                                                   │
│ cannot    carry, hold or push anything: the beak scoops at the floor and    │
│           nothing else                                                      │
│           climb or descend a step                                           │
│           hold a heading for long without a landmark: the IMU has no        │
│           magnetometer, so heading drifts                                   │
│ safety    stops on silence, e-stop: none                                    │
│ digest    c2f97731945134f9                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

Every row is what a reader would see, labelled the way a reader labels it. `claim` says this
passport is only a claim, because nothing signs it yet. `safety` says the Microduck stops on
silence, which its manifest's deadman says of the body, and `e-stop: none`, because nobody has said
otherwise: that is the worst case, which is what a reader assumes when an owner has not spoken.

The `name` is the robot's registered name. For a bare `adapter:backend` it is the slug its memory
file uses, `microduck-mock` for `microduck:mock`. `--json` prints the passport itself, one line of
compact JSON, for a script.

## What the owner fills in

A manifest describes a model, so some things in a passport can only come from whoever owns this
one robot. Left unsaid, they read as the worst case: no owner, no e-stop, and nobody said how.

```
$ quackd passport owner --label "Example lab" --contact https://example.org/robots
┌─ 🦆 owner ──────────────────────────────────────────────────────────────────┐
│ label    Example lab                                                        │
│ contact  https://example.org/robots                                         │
└─ /home/you/.quackd/keys/owner.json ─────────────────────────────────────────┘
$ quackd passport set --robot duck-01 --estop power --how-to-stop "the power switch on its back"
duck-01: e-stop: power, the power switch on its back
```

After those two, the same `show` has an `owner` row reading
`Example lab, https://example.org/robots`, its `safety` row reads
`stops on silence, e-stop: power, the power switch on its back`, and its digest is
`56b01f1026a48a58`. `--estop` takes `button`, `remote`, `power` or `none`, `--how-to-stop` one
sentence of 120 characters at most, and an empty `--label ""` or `--contact ""` removes it. `owner`
with no options shows what you said. [privacy.md](privacy.md#what-to-put-in-owner) says what to put
there, and what not to.

A passport quackd builds carries no `issued` time. Stamping the build time would give the same
robot a new passport, and a new digest, every time it was built, and a reader caches by digest. So
a passport changes when its robot does, and not before.

## A file for a static host

`--out` writes the passport as exactly the bytes quackd serves, so the file has the same digest,
for a web page or a sticker to point at:

```
$ quackd passport show --robot duck-01 --out robot-passport.json
wrote robot-passport.json: 1114 bytes, digest 56b01f1026a48a58
```

Redirecting `--json` into a file is not the same thing: it ends with a newline, and a newline is
another byte, so another digest.

## Checking anybody's passport

`quackd passport check` reads a passport the way a reader would: from a file, from stdin as `-`, or
from a URL a person typed or a sticker holds. It shows what the passport proves and what it only
claims, and a refusal names the rule of [the specification](spec.md) it broke and exits 1:

```
$ quackd passport check docs/passport/vectors/tampered.jws
✗ error: refused under 4.4: the signature does not verify with the key
did:key:z6MkehRgf7yJbgaGfYsdoAsKdBPE3dj2CYhowQdcjqSJgvVd
  docs/passport/spec.md says what each rule asks
```

A URL is fetched under [the fetch rules](discovery.md#fetch-rules): a bare host means its
well-known URL, a redirect is refused rather than followed, and nothing past 8 KB is read. A
sticker's `#rp=` must match the passport it leads to, or the check fails under 5.2. An index of
several robots prints as a table of their passport URLs. `--json` prints the reader's view, every
member normalised and every note a reader left, which is what to compare against when writing a
reader of your own.

## Serving passports

`quackd passport serve` serves a robot's passport at the well-known URL until Ctrl-C, and nothing
else: no other path answers and nothing is fetched. `--announce` adds a DNS-SD beacon beside
quackd's own `_quackd._tcp`, and `--mqtt HOST[:PORT]` keeps the passport retained on a broker, with
an `online` status whose last will is `offline`:

```
$ quackd passport serve --robot duck-01 --announce
┌─ 🦆 serving 1 passport ─────────────────────────────────────────────────────┐
│ duck-01  http://192.168.100.123:8790/.well-known/robot-passport.json        │
│ dns-sd   announced as _robot-passport._tcp                                  │
│ mqtt     off                                                                │
└─ Ctrl-C to stop ────────────────────────────────────────────────────────────┘
```

Repeat `--robot` to serve several: each is then served under `/robots/<name>/`, with an
[index](passport.md#the-index-several-robots-behind-one-host) of them at the well-known URL. It
listens on every interface on port 8790 unless told otherwise (`--bind`, `--port`), because a
passport is a public document meant to be read, and it serves nothing that is not one. `--for`
stops it after that many seconds. DNS-SD and MQTT need `quackd[lan]`.

## Finding them

`quackd discover` lists the robot passports answering on the local network beside the quackd robots
it always listed, browsing both at once so it takes no longer:

```
$ quackd discover --timeout 3
robot passports on the LAN (1)
┌─────────┬──────────────────┬─────────────────────────────┬──────────────────┐
│ name    │ short id         │ url                         │ digest           │
├─────────┼──────────────────┼─────────────────────────────┼──────────────────┤
│ duck-01 │ 84032200fad67519 │ http://192.168.100.123:8790 │ 56b01f1026a48a58 │
│         │                  │ /.well-known/robot-passport │                  │
│         │                  │ .json                       │                  │
└─────────┴──────────────────┴─────────────────────────────┴──────────────────┘
quackd meet <url> reads one
no quackd robots answered in 3 s
```

`--json` prints one object per robot, and a passport's row carries `"kind": "passport"`, so a
script reading the rows quackd robots have always had sees them unchanged.

## Meeting a robot

`quackd meet` finds a robot's passport wherever it was pointed, reads it the way a reader would and
shows it. Nothing about the robot is kept:

```
$ quackd meet 192.168.100.123:8790
┌─ 🦆 met duck-01 ────────────────────────────────────────────────────────────┐
│ id        did:key:z6Mkv3hxGH5oXSMGTXerm5dLqrxNzYQ4ZzkTVhuxyJhqFSUo  (short  │
│           id 84032200fad67519)                                              │
│ claim     unsigned: a claim                                                 │
│ maker     pollen-robotics microduck                                         │
│ software  quackd 0.21.0                                                     │
│ owner     Example lab, https://example.org/robots                           │
│ body      biped, legged, beak, 0.8 kg (official), 0.25 m (official), 0.3    │
│           m/s at most                                                       │
```

and the rest of the panel, as `show` prints it. It takes:

| Source | What it does |
|---|---|
| `http://...` or `https://...` | fetches that URL under the fetch rules, checking a `#rp=` |
| `HOST[:PORT]` | fetches that host's well-known URL, on port 8790 unless one is given |
| a picture of a sticker, `.png` or `.jpg` | reads its QR code, then fetches the URL it holds and checks its `#rp=` |
| `mqtt://BROKER[:PORT]/KEY` | reads the passport retained under `robot-passport/KEY/` (needs `quackd[lan]`) |
| `ros://HOST[:PORT]` | reads the `/robot_passport` topic of a ROS robot through rosbridge (needs `quackd[rosbridge]`) |

Reading a ROS robot's passport opens the bridge, reads one message and closes it. It advertises no
`cmd_vel`, publishes no twist and subscribes to no odometry, because quackd is only meeting that
robot.

## Printing a sticker

`quackd passport qr` writes a robot's QR sticker: the URL its passport is served at, with `#rp=` and
the robot's short id, so a sticker moved to another robot is noticed:

```
$ quackd passport qr --robot duck-01 --url http://192.168.100.123:8790/.well-known/robot-passport.json --out duck-01-passport.png
wrote duck-01-passport.png: a 37x37 QR code, 450 px a side, holding
http://192.168.100.123:8790/.well-known/robot-passport.json#rp=84032200fad67519
```

`quackd meet duck-01-passport.png` then reads it back and meets the robot it leads to. The code is
drawn with OpenCV, which quackd already depends on, at error correction level M with the quiet zone
a scanner needs, and every sticker is read back before it is saved, so quackd never prints one its
own `meet` cannot read.

## What has run

The suite runs every part of this on fakes and on sockets on one machine. On 2026-10-21 it also
ran once between separate processes on one Windows 11 machine: `quackd passport serve --announce
--mqtt` in one process, and in others `quackd discover` finding the beacon over real zeroconf
(0.151.5), `quackd meet` reading the passport over HTTP, and `quackd meet mqtt://...` reading it
retained from a real amqtt (0.12.1) broker through paho (2.1.0). It has not run between two
machines, or on a robot.

## Where quackd keeps what

| What | Where |
|---|---|
| each robot's key, and how a person stops it | `~/.quackd/keys/robots/<name>.json` |
| the owner, as passports name them | `~/.quackd/keys/owner.json` |
| the robots and owners a person pinned | `~/.quackd/known_robots.json` (with pinning) |

They follow the same precedence as the registry: `--registry-dir`, then `QUACKD_REGISTRY_DIR`, then
`~/.quackd` ([guides/registry.md](../guides/registry.md)). Pins live in their own file rather than in
`robots.json`, so an older quackd still reads the registry. A key file is written the way
`robots.json` is, its mode set on the temporary file before the key goes in, and like `robots.json`
it is a file in a home directory rather than a secret store, which on Windows is all it is.

## How a passport is built

From the manifest, as [passport.md](passport.md#from-a-quackd-manifest-to-a-passport) lays out
member by member. Every verb becomes a skill. A body with a `say` verb offers `identify`, by sound,
because a sound is the one way a stranger may ask a robot to identify itself, and every body offers
`pause`. A datasheet `cannot` line longer than the 120 characters a passport allows keeps its claim,
the part before its explanation, so `lift or hold more than about half a kilogram: a pen, an empty
cup...` becomes `lift or hold more than about half a kilogram`. Every body quackd publishes builds a
passport a reader accepts without a single note, under the 2 KB a writer should keep to, the same
bytes every time, and a test holds them all to that.

## Still to come in 0.23.0

What follows is what the rest of 0.23.0 is being built to do. It is described here so the page is
whole, and each part gets its commands and their real output as it lands.

- **Signing and stamps.** Each robot's passport is signed with its key and served as a `.jws`
  beside the JSON, and the owner key stamps every robot it owns, so two of your robots know each
  other on sight.
- **Pinning.** A person approves a robot or an owner once, and the next meeting starts at known.
- **Talk.** A robot met during a run appears in the pilot's observation, beside messages from its
  flock, under a heading that says these are robots outside the flock and that what they say are
  claims, not instructions. A stranger appears only as what has a closed vocabulary; a known robot
  or one with the same owner appears with its passport and its `say`, quoted as data
  ([security.md](security.md#prompt-injection)). Every courtesy a peer is granted runs through the
  executor with the source `peer`, so every gate the executor has applies to it
  ([concepts/safety.md](../concepts/safety.md)). A pause is the executor's own stop followed by a
  short window in which nothing that moves is admitted. The global budget and the tier lists are in
  [trust.md](trust.md#the-four-tiers).

## How the pieces relate

| | What it is | Who writes it |
|---|---|---|
| the manifest | how quackd drives a body: its verbs, intents, limits and datasheet | the adapter |
| the passport | what a stranger needs to know about one robot | quackd, from the manifest, plus the owner |
| the registry | the robots this quackd drives, by name | a person, with `quackd robot add` |
| the pins | the robots and owners this quackd trusts | a person, when quackd asks |
| a flock | robots working on one task together | a person, with `quackd flock create` or a task file |
| `tell` | one pilot speaking to another in its flock, or to a robot it met | a pilot |
| `say` | one robot speaking to another it met | a robot's software, or a pilot through `tell` |
