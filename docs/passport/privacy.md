# Privacy: what a robot reveals, and to whom

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation. The beacon id is designed to rotate and the
> wizard offers BlueZ address privacy, and neither has been tested on a real radio
> ([roadmap.md](roadmap.md)).

A robot that carries a passport announces itself by radio, which reaches through walls. A robot
usually belongs to a person and goes where they go, so this page says what each layer reveals, what
to leave out, and how to take part while revealing as little as possible.

## What each layer reveals

| Layer | Who can see it | What they learn |
|---|---|---|
| the Bluetooth advert | anybody within radio range, unseen | that a robot speaking Robot Passport is here, a beacon id that changes every 15 minutes, and how to reach it. Never a name, an owner or a key |
| the radio's own address | the same | an address that stays the same unless address privacy is on in BlueZ |
| the LAN record | anybody on the same LAN | the robot key's short id, which never changes, beside the current beacon id and its passport's digest |
| the passport | anybody near enough to say hello, with any passport at all, and anybody on its LAN | whatever its owner put in it, and the owner key that signed it |
| talk | the robot it talks to, and anybody who can read the LAN or listen to the link | what it says |
| the robot's brain | the provider of the model `brain:` names | what the brain is shown of the robots it meets |
| quackd's own records | whoever can read the robot's folder | the robots it met, when, and what they said |

A beacon never carries an owner, a name of a person, or a place. What the passport carries beyond
what the wizard requires is the owner's choice, member by member.

## What to put in `owner`

`owner.id` is the owner key, which says nothing about who you are. The `label` and `contact` beside
it should reach whoever looks after the robot without saying who they are or where they live:

- **Good:** `{"label": "Example lab", "contact": "https://example.org/robots"}`. A page can say
  whatever its owner wants and change without the passport changing.
- **Good:** a role address, `robots@example.org`, or a form.
- **Not:** a home address, a personal phone number, a person's full name, unless that person really
  wants strangers to have them.

The point of `owner` is a name tag on a collar: a lost robot gets home, and a robot doing something
wrong has somebody to tell.

## Serial numbers

`maker.serial` identifies one physical robot for as long as it exists, across owners, networks and
years. It is there because some robots, in workplaces mostly, need it. A robot at home should leave
it out.

## Tracking

Anything stable that a robot broadcasts can be used to follow it. So:

- **The beacon id changes every 15 minutes**, and never in the middle of a meeting. It is 8 random
  bytes tied to no key, so a listener cannot tell from the id alone that the robot it hears now is
  the one it heard an hour ago ([discovery.md](discovery.md#the-beacon-id-changes-every-15-minutes)).
  Fifteen minutes is still long enough to follow a robot down a street: rotation limits tracking
  across hours, not minutes.
- **The radio has an address of its own**, and every advert carries it, beneath the beacon. While
  that address stays the same, rotating the beacon id alone does not stop tracking. BlueZ can rotate
  the address too, with `Privacy = device` in `/etc/bluetooth/main.conf`, and the wizard offers to
  set it: it asks first, backs the file up and restarts bluetoothd. Whether the adapter then really
  changes its address cannot be seen without root, and nobody has watched one do it on a Jetson.
  `quackd onboard status` says plainly when the address is still public.
- **The two change on separate clocks.** Exposure Notification changed a phone's address and its
  identifier at the same moment, so that a listener could not link the old pair to the new
  ([prior-art.md](prior-art.md#exposure-notification)). quackd does not yet: the beacon id rotates
  on quackd's clock and the address on BlueZ's, so a listener who hears one change while the other
  holds can tie the two together.
- **On a LAN**, the DNS-SD record puts the robot's short id, which never changes, beside its current
  beacon id, so anybody on that LAN can tie each new beacon id to the same robot. They are the same
  people who could see the robot's network traffic anyway.
- **The passport itself is stable by design.** Its `id` is what lets two robots recognise each other
  again, and every passport an owner signs names the same owner key, so anybody who reads two of
  your robots' passports knows they are both yours.

## What quackd keeps about robots it meets

On the robot, in its folder, and nowhere else:

- **Pins**, in `robot.yaml` under `trust.pins`: the robots and owners a person chose to trust, with
  the key, the name it was met under, and when. A pin stays until a person removes it.
- **The robots met and refused**, in `met.jsonl`, with why each refusal happened.
- **Run records**, in `runs/`, the newest 200: each meeting and goal, with what was said, a
  stranger's words included, because the record is for a person.

Nothing is sent anywhere else, with one exception you choose: whatever the brain is shown goes to
the model `brain:` names. With a cloud model, its provider sees the facts of the robots met and what
known robots said. A model server you run yourself keeps all of it on your own network.

quackd never sends a camera frame over talk: a `share` is a sentence, never a picture.

## Taking part without being discoverable

| You want | Do this |
|---|---|
| robots near it to meet it, and nobody to follow it far | the rotating beacon id, which is the default, and address privacy, which the wizard offers |
| it to meet only the robots it hears | `radio.advertise: false` in `robot.yaml`: it dials the robots it hears and advertises nothing over the radio |
| no person's name on record | an owner label for a role, a lab or a household, and a contact page rather than an address |
| as little as possible in its passport | only what the wizard asks for: what other robots must know to stay safe around it |
| nothing at all | `quackd onboard uninstall`: the service stops, and nothing advertises |

## A note on the law

In many places an owner's name or a personal contact is personal data, and a robot that broadcasts
it is publishing personal data. Nothing in this protocol requires any, which is the main thing a
protocol can do about it. This page is not legal advice, and an organisation deploying robots in
public spaces should ask somebody whose job that is.
