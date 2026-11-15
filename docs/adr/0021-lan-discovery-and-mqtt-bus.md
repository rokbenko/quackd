# ADR-0021: LAN discovery over zeroconf and a team bus over MQTT, both extras

**Status:** accepted, amended · **Date:** 2026-09-02 · Implemented in Phase 4 of 0.4 ([design](../design/multi-robot.md))

**Amended 2026-11-15:** the coordinator team is gone with the simulators it ran on, and with it
the `hb` topic and every message kind but `TALK`. The bus carries one pilot's words to another
on `ctl` at QoS 1, a `TALK` that arrives twice is heard twice where the coordinator's handlers
were idempotent, and nothing needs a distributed clock now. Discovery is unchanged, and there is
still no `--bus` flag. See the CHANGELOG's Unreleased entry.

**Amended 2026-09-13 by [ADR-0034](0034-registered-robots-and-pilot-teams.md):** the bus
carries a ninth kind, `TALK`, which is what one pilot says to another. It rides `ctl` at QoS 1
like every other control message and needs nothing new from `MqttBus`. The reason there is
still no `--bus` flag has changed for one of the two kinds: an auction across machines still
needs a distributed clock, but a pilot team has no shared clock to distribute, so what is
missing there is simply that nothing has ever carried one between machines and Roboglot does not
ship a claim it has not run. Discovery is still identity only and still feeds no team: a
stored team is a list of names a person wrote, not a list of what answered an mDNS query.

## Context

A team in 0.3 is N views of one process. Robots on a desk are on a LAN. Two things are
needed to cross the room: finding robots (which Roboglot instance is which body) and
carrying the six team message kinds between processes. Neither may add a default
dependency, and the test suite must stay green with no network and no broker.

## Decision

- **Discovery** uses `zeroconf` with service type `_roboglot._tcp.local.`. The TXT record
  carries identity only: `v` (record version), `mid` (manifest id), `sha` (the manifest
  digest: sha256 of the canonical sorted-key JSON excluding `id` and `backend`, a
  capability fingerprint), `adp` (adapter name), `vend`, `mdl`, `emb`, `nverbs`. Each
  key-value pair is self-validated under 200 bytes because zeroconf performs no
  validation and fails with a bare `ValueError` at 255. A full manifest is never squeezed
  into TXT; it is obtained out of band and verified by the hash. The wire format is a pure
  module (`roboglot/lan/txt.py`) with no third-party imports so it is unit-tested without
  the extra. `roboglot announce --robot <spec>` advertises a static manifest and holds no
  robot; `roboglot discover` lists what answers.
- **The MQTT bus** (`roboglot/team/mqtt_bus.py::MqttBus`) implements the same two-method
  `Bus` protocol as the in-process bus and preserves its hard rule from ADR-0016: nobody
  ever awaits the bus. paho runs its own network thread; `publish` is non-blocking; remote
  messages are marshalled onto the event loop with `call_soon_threadsafe` before they are
  tapped and pushed, because the team transcript writer is not thread-safe.
  `Subscription.drain()` becomes an atomic `popleft` loop so one class serves both buses.
  Topics are `roboglot/<team_id>/ctl` (TASK, BID, CLAIM, ROLE, RESULT, HINT, VERDICT at QoS
  1) and `roboglot/<team_id>/hb` (HB at QoS 0); the payload is the pydantic
  `TeamMessage` JSON; `retain=False` everywhere. At-least-once delivery is tolerated
  because the coordinator's handlers are idempotent (minimum bid per source, maximum
  exclusion, idempotent heartbeats); this invariant is stated in the module docstring and
  pinned by a test.
- **In-process stays the default everywhere.** The MQTT bus is library-only in 0.4
  (`run_team(bus_factory=...)`); there is no `--bus` CLI flag because a distributed team
  also needs a distributed clock, which is out of scope, and a flag would imply otherwise.
- Both live behind the `roboglot[lan]` extra, imported lazily inside the command or
  constructor, with injectable clients (`zc=`, `client=`) so every test runs on fakes:
  a fake zeroconf that records registrations, a fake paho client plus a synchronous fake
  broker that routes between two `MqttBus` nodes with zero sockets.

## Consequences

- `team.jsonl` still carries every message on the node that owns the run directory: the
  tap fires exactly once per message per node, on publish for local messages and on
  receive for remote ones.
- TXT keys are permanent wire protocol once shipped; they are short on purpose.
- An integration test against an embedded broker (`amqtt`) is documented, not part of the
  default suite.
