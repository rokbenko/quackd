# Quickstart: give a robot a passport

> [!NOTE]
> Spec draft 0.1. The first half of this page works today for any robot. The second half, for a
> robot quackd drives, fills in as quackd 0.23.0's implementation lands, and shows no command
> before it has run.

## Any robot, in five minutes, with no quackd

Nothing needs to be installed on the robot for this, and nothing needs quackd.

**1. Write the passport.** Three members are required. A robot that holds no key uses a UUID as its
`id`; make a fresh one with any UUID generator, or `python -c "import uuid; print(uuid.uuid4())"`.
Add whatever else you can say honestly:

```json
{
  "passport": "0.1",
  "id": "urn:uuid:<your fresh UUID>",
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

**4. Check it** with any JSON Schema validator against
[schema/passport.schema.json](schema/passport.schema.json). The vectors in
[vectors/](vectors/README.md) show what a reader accepts and what it refuses.

That robot is now level 0 on [the ladder](compatibility.md#the-ladder): anybody who scans its
sticker knows what it is, whose it is and how to stop it.

## A robot quackd drives

quackd builds a passport from a robot's manifest, signs it with a key it keeps for that robot, and
serves it, so a robot quackd drives needs none of the steps above. The commands, with what they
really print, arrive here as each one is built; [quackd.md](quackd.md) describes what quackd does,
and [roadmap.md](roadmap.md) says how far it has got.
