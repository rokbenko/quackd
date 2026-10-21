# Quickstart: give a robot a passport

> [!NOTE]
> The first half works today for any robot, with nothing installed on it. The second half is for a
> robot quackd drives, and shows the commands quackd 0.23.0 has for it, with what they really
> print. [roadmap.md](roadmap.md) says what has run, and what comes after 0.23.0.

## Any robot, in five minutes

Nothing needs to be installed on the robot, and quackd is needed only for the last step, and only
if you want it.

**1. Write the passport.** Three members are required. A robot that holds no key uses a UUID as its
`id`: make a fresh one with any UUID generator, or `python -c "import uuid; print(uuid.uuid4())"`,
rather than copying the one below. Add whatever else you can say honestly:

```json
{
  "passport": "0.1",
  "id": "urn:uuid:7d4a2f0e-31c5-4f8a-9b62-0c1e5d8a4b37",
  "name": "vacuum-01",
  "maker": {"vendor": "example-robotics", "model": "sweeper-3"},
  "owner": {"label": "Example lab", "contact": "https://example.org/robots"},
  "body": {"embodiment": "wheeled", "mobility": "wheeled", "manipulator": "none", "max_speed_mps": 0.4},
  "cannot": ["climb or descend a step"],
  "safety": {"estop": "button", "how_to_stop": "the red button on its top"}
}
```

[passport.md](passport.md) explains every member. Leave out anything you are not sure of: an absent
number reads as "not published", which is honest, where a guess is not.

**2. Put it online** wherever a file can be served over HTTPS, as `robot-passport.json`, or at
`/.well-known/robot-passport.json` on a domain you control.

**3. Print its URL as a QR code** with any QR code generator, and stick it on the robot. Adding
`#rp=` and a short id to the URL needs a key, so a robot without one leaves it off.

**4. Check it**, against [schema/passport.schema.json](schema/passport.schema.json) with any JSON
Schema validator, or with quackd, which reads it exactly the way another robot would and also sees
what no schema can, a duplicate member or a file too large:

```
$ quackd passport check vacuum.json
┌─ 🦆 checked vacuum-01 ──────────────────────────────────────────────────────┐
│ id      urn:uuid:7d4a2f0e-31c5-4f8a-9b62-0c1e5d8a4b37  (short id none: it   │
│         holds no key)                                                       │
│ claim   unsigned: a claim                                                   │
│ maker   example-robotics sweeper-3                                          │
│ owner   Example lab, https://example.org/robots                             │
│ body    wheeled, nothing to hold with, 0.4 m/s at most                      │
│ cannot  climb or descend a step                                             │
│ safety  does not stop on silence, e-stop: button, the red button on its top │
│ digest  b292373ac4377183                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

It says `does not stop on silence` because the passport did not say it does, and a reader always
reads `safety` as the worst case. Once the file is online, `quackd passport check` takes its URL
instead. The vectors in [vectors/](vectors/README.md) show what a reader accepts and what it refuses.

That robot is now level 0 on [the ladder](compatibility.md#the-ladder): anybody who scans its
sticker knows what it is, whose it is and how to stop it.

## A robot quackd drives

quackd builds the passport from the robot's manifest and signs it with a key it makes for the
robot, so there is nothing to write. You say only what a manifest cannot know, who owns the robot
and how a person stops it, and stamp it with your owner key:

```
$ quackd robot add duck-01 microduck:mock
$ quackd passport owner --label "Example lab" --contact https://example.org/robots
$ quackd passport set --robot duck-01 --estop power --how-to-stop "the power switch on its back"
$ quackd passport stamp --robot duck-01
$ quackd passport show --robot duck-01 --out robot-passport.jws
wrote robot-passport.jws: 2380 bytes, digest 63ebc3f7acd754cc
$ quackd passport check robot-passport.jws
┌─ 🦆 checked duck-01 ────────────────────────────────────────────────────────┐
│ id        did:key:z6MkrrZYoygPr1cWb7PMqyuQnQ8TrughCzAqXypJqaMGWGTT  (short  │
│           id ac8b2b47ac2ce9c0)                                              │
│ claim     signed by its own key                                             │
│ stamp     owner 9bdc6799888c2473, your own owner key, until 2027-10-21      │
│ tier      flock: one of the robots this quackd drives                       │
│ maker     pollen-robotics microduck                                         │
│ software  quackd 0.21.0                                                     │
│ owner     Example lab, https://example.org/robots                           │
```

The first `set` makes the robot's key and the first `stamp` makes your owner key, which you should
back up. For a web page, write both files, `--out robot-passport.jws` and
`--out robot-passport.json`, and serve them side by side. To publish from the robot's own host
instead, `quackd passport serve --robot duck-01 --announce` serves both at the well-known URL and
advertises them on the local network, and `quackd passport qr` prints the sticker
([quackd.md](quackd.md#serving-passports)). With `--talk` it also listens for robots that say hello,
and tells you what each says, asks and shares, and `quackd meet` with `--as duck-01` says hello to
another robot as one of yours ([quackd.md](quackd.md#talking)).

The rest of that panel, and what every command prints, is in [quackd.md](quackd.md#a-robots-passport).
