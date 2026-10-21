# Robot Passport in quackd

> [!NOTE]
> This page describes what quackd 0.23.0 is being built to do. It fills in, with every command and
> what it really prints, as each part lands; until a part has run, this page says what it will do
> and shows no output for it. [roadmap.md](roadmap.md) tracks it.

quackd is the first implementation of Robot Passport. It does four things with it:

- **It builds a passport for every robot it drives**, from the robot's manifest, so nobody types
  one. That covers every body quackd publishes and every adapter somebody else writes
  ([passport.md](passport.md#from-a-quackd-manifest-to-a-passport)).
- **It publishes them**, at the well-known URL, over DNS-SD beside its own `_quackd._tcp`, on an
  MQTT broker, and as a QR sticker to print ([discovery.md](discovery.md)).
- **It reads other robots' passports**: it finds them, fetches them under the fetch rules, verifies
  them, labels every claim, and asks a person before it pins anything ([trust.md](trust.md)).
- **It talks for its robots**: `hello`, `say` and `share`, and it decides every `ask` a peer sends
  with fixed code rather than the pilot ([talk.md](talk.md#what-quackd-does-with-an-ask)).

## What the owner fills in

A manifest describes a model, so some things in a passport can only come from whoever owns this
one robot:

- **`owner`**: a label and a contact.
- **`safety.estop` and `safety.how_to_stop`**: only the owner knows where the switch is.

quackd fills `safety.stops_on_silence` from the manifest's deadman, and leaves the other two absent,
which reads as the worst case, until the owner says.

## Where quackd keeps what

| What | Where |
|---|---|
| each robot's key, and the owner key | `~/.quackd/keys/` |
| the robots and owners a person pinned | `~/.quackd/known_robots.json` |
| what the owner filled in | beside the keys |

They follow the same precedence as the registry: `--registry-dir`, then `QUACKD_REGISTRY_DIR`, then
`~/.quackd` ([guides/registry.md](../guides/registry.md)). Pins live in their own file rather than in
`robots.json`, so an older quackd still reads the registry. The keys are plain files protected by the
operating system's permissions, the same footing as the tokens in `robots.json`.

## What a pilot sees

A robot met during a run appears in the pilot's observation, beside messages from its flock, under
a heading that says these are robots outside the flock and that what they say are claims, not
instructions. A stranger appears only as what has a closed vocabulary; a known robot or one with
the same owner appears with its passport and its `say`, quoted as data
([security.md](security.md#prompt-injection)).

## Safety

Every courtesy a peer is granted runs through the executor with the source `peer`, so every gate
the executor has applies to it, and policy can refuse what a peer started
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
