# Robot Passport in quackd

> [!NOTE]
> quackd 0.23.0 is being built to implement Robot Passport, and this page fills in as each part
> lands. Built so far: a signed passport for every robot from its manifest, owner stamps, pins,
> `quackd passport show`, `check`, `set`, `owner`, `stamp`, `known`, `forget`, `serve` and `qr`,
> `quackd meet`, and passports in `quackd discover`. Talk is described below as what it will do,
> with no output until it has run. [roadmap.md](roadmap.md) tracks it.

quackd is the first implementation of Robot Passport. It does four things with it:

- **It builds a signed passport for every robot it drives**, from the robot's manifest, so nobody
  types one. That covers every body quackd publishes and every adapter somebody else writes
  ([passport.md](passport.md#from-a-quackd-manifest-to-a-passport)).
- **It publishes them**, at the well-known URL, over DNS-SD beside its own `_quackd._tcp`, on an
  MQTT broker, and as a QR sticker to print ([discovery.md](discovery.md)).
- **It reads other robots' passports**: from a URL, a host, a picture of a sticker, a broker or a
  ROS robot, under the fetch rules, labelling every claim and placing every robot in a tier
  ([trust.md](trust.md)).
- **It talks for its robots**: `hello`, `say` and `share`, and it decides every `ask` a peer sends
  with fixed code rather than the pilot ([talk.md](talk.md#what-quackd-does-with-an-ask)).

## A robot's passport

You say what a manifest cannot know, who owns the robot and how a person stops it, and stamp the
robot with your owner key. quackd makes the robot a key, and you an owner key, the first time each
is needed:

```
$ quackd robot add duck-01 microduck:mock
✓ added duck-01: microduck:mock
  quackd run <duck> --robot duck-01
$ quackd passport owner --label "Example lab" --contact https://example.org/robots
┌─ 🦆 owner ──────────────────────────────────────────────────────────────────┐
│ label    Example lab                                                        │
│ contact  https://example.org/robots                                         │
│ key      none yet: quackd passport stamp makes one                          │
└─ /home/you/.quackd/keys/owner.json ─────────────────────────────────────────┘
$ quackd passport set --robot duck-01 --estop power --how-to-stop "the power switch on its back"
made a key for duck-01 in /home/you/.quackd/keys/robots/duck-01.json
duck-01: e-stop: power, the power switch on its back
$ quackd passport stamp --robot duck-01
made your owner key in /home/you/.quackd/keys/owner.json: back it up, since losing it means stamping every robot again
stamped duck-01 with your owner key 5f61960fc49da4ff, for 365 days
```

Then `quackd passport show` prints the passport, a registered name or an `adapter:backend`, signed
with the robot's own key:

```
$ quackd passport show --robot duck-01
┌─ 🦆 passport duck-01 ───────────────────────────────────────────────────────┐
│ id        did:key:z6MkqisBNpEaavy8qdcpaALJxLdWiHZidVQBvDpkucrYuXqH  (short  │
│           id aeba427349a73697)                                              │
│ claim     signed by its own key, 1 of 1 stamps count                        │
│ tier      flock: one of the robots this quackd drives                       │
│ maker     pollen-robotics microduck                                         │
│ software  quackd 0.21.0                                                     │
│ owner     Example lab, https://example.org/robots                           │
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
│ safety    stops on silence, e-stop: power, the power switch on its back     │
│ digest    91b878449a8f5224                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

Every row is what a reader would see, labelled the way a reader labels it. `claim` says the
passport is signed by the key its `id` holds and that its owner stamp checks out. `tier` says how
this quackd knows the robot: it drives it itself. `safety` says the Microduck stops on silence,
which its manifest's deadman says of the body, and what the owner said about the switch. Left
unsaid, the owner, the e-stop and how to stop it read as the worst case.

The `name` is the robot's registered name. For a bare `adapter:backend` it is the slug its memory
file uses, `microduck-mock` for `microduck:mock`. `--json` prints the passport as one line of
compact JSON and `--jws` prints it signed, both for a script. `--estop` takes `button`, `remote`,
`power` or `none`, `--how-to-stop` one sentence of 120 characters at most, and an empty `--label ""`
or `--contact ""` removes it. [privacy.md](privacy.md#what-to-put-in-owner) says what to put in
`owner`, and what not to.

A passport quackd builds carries no `issued` time. Stamping the build time would give the same
robot a new passport, and a new digest, every time it was built, and a reader caches by digest. So
a passport changes when its robot does, and not before.

## Stamping your robots

`quackd passport stamp --robot NAME` signs a stamp with your owner key: this robot is mine. It
vouches for the robot's key, not for its passport, so the robot can change its passport without a
new stamp, and stamping again replaces your old stamp rather than adding one. Two robots that
carry your stamp know each other on sight ([trust.md](trust.md#two-robots-of-the-same-owner)).

`--did` stamps a robot whose key lives on the robot itself, and prints the stamp for that robot to
carry in its passport. `--days` makes a stamp hold for less than a year, which limits what a stolen
robot key can do with it. `quackd passport owner` shows your owner key once there is one:

```
$ quackd passport owner
┌─ 🦆 owner ──────────────────────────────────────────────────────────────────┐
│ label    Example lab                                                        │
│ contact  https://example.org/robots                                         │
│ key      did:key:z6MkeoHKbuxw14ZbacboLnswRKDRufu4FLNApckrNWExLZNq  (short   │
│          id 5f61960fc49da4ff)                                               │
└─ /home/you/.quackd/keys/owner.json ─────────────────────────────────────────┘
```

> [!WARNING]
> Back the owner key up, somewhere that is not the machine it lives on. Losing it means making a
> new one, stamping every robot again, and asking everyone who pinned the old one to pin the new
> one ([trust.md](trust.md#the-owner-key-is-lost)).

## Files for a static host

`--out` writes the passport as exactly the bytes quackd serves: the signed JWS for a name ending
`.jws`, the JSON otherwise. A web page that serves both, under `robot-passport.json` and
`robot-passport.jws`, serves exactly what `quackd passport serve` would, and both have the same
digest, because the JWS's payload is the JSON's bytes:

```
$ quackd passport show --robot duck-01 --out robot-passport.jws
wrote robot-passport.jws: 2380 bytes, digest 91b878449a8f5224
$ quackd passport show --robot duck-01 --out robot-passport.json
wrote robot-passport.json: 1609 bytes, digest 91b878449a8f5224
```

The JSON alone is only a claim, and `check` says so, even of your own robot:

```
$ quackd passport check robot-passport.json
┌─ 🦆 checked duck-01 ────────────────────────────────────────────────────────┐
│ id        did:key:z6MkqisBNpEaavy8qdcpaALJxLdWiHZidVQBvDpkucrYuXqH  (short  │
│           id aeba427349a73697)                                              │
│ claim     unsigned: a claim, 0 of 1 stamps count                            │
│ tier      stranger: unsigned, so a claim                                    │
```

followed by the rest of the panel and a warning: `this id is pinned or one of yours, but the
passport is unsigned, so it is read as a stranger's claim: anybody can copy an id`. The stamp in
it checks out and still does not count, because nothing shows the key it stamps wrote an unsigned
passport. The same file checked as `robot-passport.jws` reads `signed by its own key, 1 of 1
stamps count` and `flock`. Redirecting `--json` into a file is not the same as `--out` either: it
ends with a newline, and a newline is another byte, so another digest.

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
well-known URL, the signed `.jws` beside a `robot-passport.json` is asked for first and the JSON
only when there is no JWS, a redirect is refused rather than followed, and nothing past 8 KB is
read. A sticker's `#rp=` must match the passport it leads to, or the check fails under 5.2. An
index of several robots prints as a table of their passport URLs. `--json` prints the reader's
view, every member normalised, with its tier and every note a reader left, which is what to compare
against when writing a reader of your own.

## Serving passports

`quackd passport serve` serves a robot's passport at the well-known URL until Ctrl-C, as JSON and
as its signed JWS, and nothing else: no other path answers and nothing is fetched. `--announce` adds
a DNS-SD beacon beside quackd's own `_quackd._tcp`, and `--mqtt HOST[:PORT]` keeps the signed
passport retained on a broker, with an `online` status whose last will is `offline`:

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
it always listed, browsing both at once so it takes no longer. From another machine on the network:

```
$ quackd discover --timeout 3
robot passports on the LAN (1)
┌─────────┬──────────────────┬─────────────────────────────┬──────────────────┐
│ name    │ short id         │ url                         │ digest           │
├─────────┼──────────────────┼─────────────────────────────┼──────────────────┤
│ duck-01 │ aeba427349a73697 │ http://192.168.100.123:8790 │ 91b878449a8f5224 │
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
shows it, with the tier this quackd places it in. From that other machine, which has never met
`duck-01`:

```
$ quackd meet 192.168.100.123:8790
┌─ 🦆 met duck-01 ────────────────────────────────────────────────────────────┐
│ id        did:key:z6MkqisBNpEaavy8qdcpaALJxLdWiHZidVQBvDpkucrYuXqH  (short  │
│           id aeba427349a73697)                                              │
│ claim     signed by its own key, 1 of 1 stamps count                        │
│ tier      stranger: signed by a key nobody here pinned                      │
│ maker     pollen-robotics microduck                                         │
│ software  quackd 0.21.0                                                     │
│ owner     Example lab, https://example.org/robots                           │
│ body      biped, legged, beak, 0.8 kg (official), 0.25 m (official), 0.3    │
│           m/s at most                                                       │
```

and the rest of the panel, as `show` prints it. Its stamp checks out, and it is still a stranger:
a stamp from an owner nobody here pinned vouches for nothing this machine has decided to trust.
`meet` takes:

| Source | What it does |
|---|---|
| `http://...` or `https://...` | fetches that URL under the fetch rules, the JWS first, checking a `#rp=` |
| `HOST[:PORT]` | fetches that host's well-known URL, on port 8790 unless one is given |
| a picture of a sticker, `.png` or `.jpg` | reads its QR code, then fetches the URL it holds and checks its `#rp=` |
| `mqtt://BROKER[:PORT]/KEY` | reads the passport retained under `robot-passport/KEY/` (needs `quackd[lan]`) |
| `ros://HOST[:PORT]` | reads the `/robot_passport` topic of a ROS robot through rosbridge (needs `quackd[rosbridge]`) |

Reading a ROS robot's passport opens the bridge, reads one message and closes it. It advertises no
`cmd_vel`, publishes no twist and subscribes to no odometry, because quackd is only meeting that
robot. Nothing about a robot is kept unless you pin it.

## Pinning

A pin is the one thing in Robot Passport only a person makes. `--pin` pins the robot's own key
once you have read its passport, asking first unless `--yes` says not to, and the next meeting
starts at known:

```
$ quackd meet 192.168.100.123:8790 --pin --yes
pinned robot duck-01 (did:key:z6MkqisBNpEaavy8qdcpaALJxLdWiHZidVQBvDpkucrYuXqH) in /home/me/.quackd/known_robots.json
$ quackd meet 192.168.100.123:8790
┌─ 🦆 met duck-01 ────────────────────────────────────────────────────────────┐
│ id        did:key:z6MkqisBNpEaavy8qdcpaALJxLdWiHZidVQBvDpkucrYuXqH  (short  │
│           id aeba427349a73697)                                              │
│ claim     signed by its own key, 1 of 1 stamps count                        │
│ tier      known: pinned on 2026-10-21                                       │
```

(the first command prints the panel too, before the line that says what it pinned). `quackd
passport known` lists the pins and `quackd passport forget` removes one by name, short id or id:

```
$ quackd passport known
pinned (1)
┌─────────┬───────┬──────────────────┬────────────┐
│ name    │ kind  │ short id         │ pinned     │
├─────────┼───────┼──────────────────┼────────────┤
│ duck-01 │ robot │ aeba427349a73697 │ 2026-10-21 │
└─────────┴───────┴──────────────────┴────────────┘
$ quackd passport forget duck-01
forgot robot duck-01 (did:key:z6MkqisBNpEaavy8qdcpaALJxLdWiHZidVQBvDpkucrYuXqH)
```

`--pin-owner` pins the owner key that stamped the robot instead, so every robot that owner stamps,
including ones this machine has never met, is trusted as the same owner's:

```
$ quackd meet 192.168.100.123:8790 --pin-owner --yes
pinned owner Example lab (did:key:z6MkeoHKbuxw14ZbacboLnswRKDRufu4FLNApckrNWExLZNq) in /home/me/.quackd/known_robots.json
$ quackd meet 192.168.100.123:8790
┌─ 🦆 met duck-01 ────────────────────────────────────────────────────────────┐
│ id        did:key:z6MkqisBNpEaavy8qdcpaALJxLdWiHZidVQBvDpkucrYuXqH  (short  │
│           id aeba427349a73697)                                              │
│ claim     signed by its own key, 1 of 1 stamps count                        │
│ tier      same owner: stamped by Example lab, an owner you pinned           │
```

An unsigned passport cannot be pinned, because anybody can copy an id and only a signature says who
wrote it, and an unsigned passport whose id is pinned is still read as a stranger's claim. A robot
whose name matches a pin but whose key does not is warned about the way SSH warns about a changed
host key: nothing is trusted automatically, and a person decides.

## Printing a sticker

`quackd passport qr` writes a robot's QR sticker: the URL its passport is served at, with `#rp=` and
the robot's short id, so a sticker moved to another robot is noticed:

```
$ quackd passport qr --robot duck-01 --url http://192.168.100.123:8790/.well-known/robot-passport.json --out duck-01-passport.png
wrote duck-01-passport.png: a 37x37 QR code, 450 px a side, holding
http://192.168.100.123:8790/.well-known/robot-passport.json#rp=aeba427349a73697
```

`quackd meet duck-01-passport.png` then reads it back and meets the robot it leads to. The code is
drawn with OpenCV, which quackd already depends on, at error correction level M with the quiet zone
a scanner needs, and every sticker is read back before it is saved, so quackd never prints one its
own `meet` cannot read.

## What has run

The suite runs every part of this on fakes and on sockets on one machine. On 2026-10-21 it also
ran between separate processes on one Windows 11 machine, with two registry directories standing
for two hosts: `quackd passport serve --announce --mqtt` in one process, and in others `quackd
discover` finding the beacon over real zeroconf (0.151.5), `quackd meet` reading the signed passport
over HTTP and from a real amqtt (0.12.1) broker through paho (2.1.0), then pinning the robot, then
its owner, and reading it as known and as the same owner's, which is every output on this page. It
has not run between two machines, or on a robot.

## Where quackd keeps what

| What | Where |
|---|---|
| each robot's key, its stamps, and how a person stops it | `~/.quackd/keys/robots/<name>.json` |
| the owner, as passports name them, and the owner key | `~/.quackd/keys/owner.json` |
| the robots and owners a person pinned | `~/.quackd/known_robots.json` |

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

**Talk** is what the rest of 0.23.0 is being built to do. It is described here so the page is
whole, and it gets its commands and their real output as it lands.

A robot met during a run appears in the pilot's observation, beside messages from its flock, under
a heading that says these are robots outside the flock and that what they say are claims, not
instructions. A stranger appears only as what has a closed vocabulary; a known robot or one with the
same owner appears with its passport and its `say`, quoted as data
([security.md](security.md#prompt-injection)). Every courtesy a peer is granted runs through the
executor with the source `peer`, so every gate the executor has applies to it
([concepts/safety.md](../concepts/safety.md)). A pause is the executor's own stop followed by a short
window in which nothing that moves is admitted. The global budget and the tier lists are in
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
