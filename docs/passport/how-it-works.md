# How it works

> [!NOTE]
> Spec 1.0. quackd 0.29.0 is its first implementation. The meeting below has run on the sim board,
> as the demo's first act, and not on a Jetson or over a real Bluetooth radio
> ([roadmap.md](roadmap.md)).

Robot Passport is three layers, each needed by the one above it:

```
BEACON    "a robot is here", by Bluetooth LE            a random 8-byte id, a new one every 15
   │      close enough is judged by signal strength     minutes, and three flags: never a name
   │
PASSPORT  who I am, whose I am, what I can and          one small JSON document, signed by its
   │      cannot do, how a person stops me              owner's key
   │
TALK      hello · say · bye · ask · answer · share      small signed messages over the LAN or a
          once both robots have checked each other      Bluetooth link, never obeyed blindly
```

## One meeting, step by step

`courier-01` is a humanoid that delivers parcels, with quackd on its Jetson, owned by a courier
company. `home-01` is a humanoid that lives in a flat, with quackd on its Jetson, owned by the
household. They have never met, and they end up at the same front door, where the household's
Wi-Fi is not the courier's.

```
 courier-01                                                        home-01
     │                                                                │
     │  1  hear: an advert, a beacon id and the flag LINK             │  advertises
     │ <────────────────────────────────────────────────────────────  │
     │  2  near: its signal holds above the threshold for a second    │
     │  3  dial: no shared LAN, so a Bluetooth link                   │
     │ ═══════════════════════════════════════════════════════════>   │
     │  4  hello, three messages: both passports, both keys checked   │
     │ <═══════════════════════════════════════════════════════════>  │
     │  5  tier: each a stranger to the other                         │
     │  6  meeting: each brain shown the other's closed facts         │
     │  7  far: the signal falls away, bye, the link closes           │
```

1. **Hear.** `home-01` advertises a beacon: a random id that changes every 15 minutes and three
   flags, and nothing that says which robot it is ([discovery.md](discovery.md)). `courier-01` hears
   it, with its signal strength.
2. **Near.** The median signal over the last 3 seconds holds above the threshold `meet_within_m`
   makes, -65.6 dBm for 2 m, for a second. Near is a hint, not proof: a relay can make a far robot
   read as near ([discovery.md](discovery.md#near-is-a-hint-not-proof)).
3. **Dial.** The lower beacon id dials, and here that is `courier-01`. No LAN record carries
   `home-01`'s beacon id, so it opens a Bluetooth link instead.
4. **Check both.** Three `hello` messages. Each carries its robot's passport, signed by its owner,
   and each signs a nonce the other made up a moment before. Each robot reads the other's passport:
   the owner's signature, its validity, and that it names the robot key that signed the `hello`.
   Either robot that fails any of it is refused, and nothing it said goes further
   ([talk.md](talk.md#hello-proving-who-you-are-in-three-steps)).
5. **Place.** Neither robot pinned the other, or its owner, so each is a stranger to the other
   ([trust.md](trust.md#the-tiers)).
6. **Meet.** Both robots are idle, and both owners let their robots meet strangers, so each brain
   holds a short meeting. It is shown the other robot's closed facts, a legged humanoid, its mass and
   height, what it can do, under a name made from its key. What the other says goes to the log, for
   a person, and never to the brain ([security.md](security.md#prompt-injection)).
7. **Part.** The courier walks off. Once the signal holds 6 dB below the threshold for 5 seconds,
   `courier-01` says `bye` and closes the link.

If the household had pinned the courier company's owner key, step 5 would say **known**, and step 6
would carry what each said: the two brains could agree where the parcel goes. On one Wi-Fi, step 3
would go over the LAN instead, and nothing else would change.

## What each side learns, and when

| After | `courier-01` knows | It does not yet know |
|---|---|---|
| the advert | something nearby is a robot, with this beacon id | which robot, whose, or how far it really is |
| near | its signal is strong enough | whether a relay is making it so |
| the `hello` | `home-01`'s passport, signed by the household's owner key, and that the robot on the link holds `home-01`'s robot key now | which body at the door that is |
| the tier | how much of what `home-01` says its brain may hear | anything a person has not decided |
| an `identify` | which body it is: the one that just blinked | anything the passport did not say |

## What a person sees

- **On the robot**, over ssh or at its own screen: `quackd onboard status` shows the robots near
  now, with their tier and signal, and the last meetings, and `quackd onboard met` the robots met
  and the ones refused, and why ([quackd.md](quackd.md#status-preflight-and-show)).
- **In a meeting's record**, kept with the robot's runs: each robot met, each word that passed and
  each `ask`, with what the handler decided, the stranger's own words included, for a person.
- **In the service's journal**: every refusal, with the rule it broke.

## The life of a passport

| Moment | What happens |
|---|---|
| written | the wizard writes the passport section of `robot.yaml` from your answers |
| signed | your owner key signs it, with your passphrase, valid for 365 days unless `--days` says otherwise |
| served | the service carries it in every `hello`, and serves it at the well-known URL on a LAN |
| edited | the passport section changes: `quackd onboard status` names the change, and the old passport is served until `quackd onboard sign` signs the new one |
| beacon rotated | every 15 minutes the beacon gets a new id, and the passport stays what it is |
| renewed | within 30 days of `expires`, `quackd onboard sign` signs it again |
| expired | every robot refuses it, and the robot itself stops talking until it is signed again |
| handed on | a new owner sets the robot up from the start: a new robot key, their own owner's signature |
| retired | `quackd onboard uninstall` stops the service, and nothing advertises; pins of it stay until a person removes them |

## When something goes wrong

| What fails | What happens |
|---|---|
| no Bluetooth adapter, or BlueZ is down | nothing is heard and nothing advertised, the LAN half keeps serving, and `status` says why |
| a passport's signature fails, or its robot signed it | the `hello` is refused under 4.4, written to `met.jsonl` and the journal, and that robot is not tried again for an hour |
| a passport has expired, or is not valid yet | refused under 4.5; a robot whose own clock reads earlier than its own passport's `issued` stops talking, and `status` says to set the time |
| a version whose major it does not know, a 0.1 robot's for one | refused, and answered with the versions this robot reads |
| a required extension it does not understand | shown to a person, and never talked to |
| a garbled `safety` value | read as the worst case, and the passport still shown |
| a pinned robot shows up with a new key | it is a stranger again until a person pins the new key, and a name that reads like a pinned one is warned about |
| a `hello` over the LAN from a robot the radio never heard | `403`, "this robot has not heard you by radio" |
| the link drops in the middle of a `hello` | the session never opens, and the next time the robot is near starts over |
| an `ask` gets no `answer` within five seconds | it was not granted |
| two robots share a name | nothing: names are for people, and keys tell robots apart |
