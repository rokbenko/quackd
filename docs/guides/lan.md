# Robots on a LAN: discovery and the MQTT bus

Both ship in 0.4 behind one extra and are imported lazily, so the default install never
loads them and the test suite runs on fakes with no network and no broker
([ADR-0021](../adr/0021-lan-discovery-and-mqtt-bus.md)).

The MQTT bus is the team bus of [guides/team.md](team.md) across machines, a robot that
answers discovery is registered by hand with `roboglot robot add` ([guides/registry.md](registry.md)),
and discovery feeds no team by itself.

```bash
uv pip install 'roboglot[lan,microduck]'   # zeroconf + paho-mqtt, and a robot to announce
```

## Discovery (`_roboglot._tcp.local.`)

A Roboglot instance can advertise the robot it fronts, and another can list what answers:

```bash
roboglot announce --robot microduck:mock --name duck-01          # until Ctrl-C (or --for 30)
roboglot discover --timeout 3                                    # a table of what answered
```

`announce` advertises a **static** manifest's identity and holds no robot connection. The
TXT record carries identity only; a manifest is never squeezed into TXT, it is obtained
out of band (for now: the adapter's `describe()`) and verified against the advertised digest.

| TXT key | Meaning |
|---|---|
| `v` | record version, `1` |
| `mid` | manifest id (`duck`, `arm-01`, ...) |
| `sha` | manifest digest: sha256 of the canonical sorted-key JSON excluding `id` and `backend`, first 16 hex, a capability fingerprint |
| `adp` | adapter name (`microduck`, `lerobot`, ...) |
| `vend`, `mdl`, `emb` | vendor, model, embodiment |
| `nverbs` | how many verbs the manifest lists |

Every pair is checked under 200 bytes before zeroconf sees it (the protocol caps a pair at
255 and the library does not check). These keys are wire protocol once shipped.

From Python:

```python
from roboglot.adapters.factory import RobotSpec, describe
from roboglot.lan.announce import announce
from roboglot.lan.discover import discover

ann = announce(describe(RobotSpec("microduck", "mock", "duck")), adapter="microduck")
for robot in discover(timeout_s=3.0):
    print(robot.manifest_id, robot.adapter, robot.addresses, robot.matches(some_manifest))
ann.close()
```

`announce(..., zc=, info_factory=)` and `discover(..., zc=, browse=)` take fakes, which is
how the tests run. Roboglot's own advertisement is always `_roboglot._tcp.local.`; if an
upstream daemon happens to advertise itself separately under its own service name, Roboglot
does not rename it or merge the two, it only ever advertises the Roboglot side.

**Status.** The record format and both commands are tested on fakes in the suite. The real
zeroconf path was exercised once by us on one Windows 11 machine between two processes
(announce a mock manifest in a child, discover it from the parent, digest matched); it has
not been exercised between two machines.

## Robot passports (`_robot-passport._tcp.local.`)

Beside its own record there is one more, and it belongs to Robot Passport. A robot that runs
`roboglot onboard` on its own computer announces its [Robot Passport](../passport/README.md) as
`_robot-passport._tcp`, a record for anything at all, Roboglot or not, where `_roboglot._tcp` speaks
to other Roboglot instances. Its TXT record says `v=2` and carries `id`, the robot's static short
id, and `bt`, the Bluetooth beacon id it advertises now, which changes every 15 minutes. Its port
is one that really serves the passport at the well-known URL and takes talk at its inbox, which
`_roboglot._tcp`'s port 0 never did. A robot that heard another by radio looks for that beacon id
in a `bt`, so two robots on one network talk over it, and over a Bluetooth link when they share
none. `roboglot discover` lists both kinds of record and passes over a passport record at `v=1`,
and `roboglot passport check HOST:PORT` reads the passport it finds.
[docs/passport/discovery.md](../passport/discovery.md) has the whole binding, and
[docs/passport/roboglot.md](../passport/roboglot.md) the commands.

**Status.** The `v=2` record is tested on fakes in the suite, and robots on the `sim` board find
each other through a simulated network on one machine. It has not run between two machines.

## The MQTT team bus

`roboglot.team.mqtt_bus.MqttBus` implements the same two-method `Bus` protocol as the
in-process bus (`publish`, `subscribe`) over a broker, and keeps the team's one hard rule:
nobody ever awaits the bus. paho runs its own network thread, `publish` is a synchronous
local fan-out plus a non-blocking hand-off, and members still `drain()` between their turns.

| | |
|---|---|
| Topics | `roboglot/<team_id>/ctl`, the one topic, carrying `TALK`, the one kind of message, at QoS 1 with `retain=False` |
| Payload | the pydantic `TeamMessage` JSON, exactly what `team.jsonl` records |
| Echo | a broker sends your own publications back; a message whose `src` is one of this node's subscribers is dropped and counted |
| Duplicates | QoS 1 is at least once, so a remote message can arrive twice, and a pilot hears a repeated `TALK` as said twice |
| Threads | remote messages are marshalled onto the event loop with `call_soon_threadsafe` before they are tapped and pushed, because the transcript writer is not thread-safe |
| Tap | fires exactly once per message per node: on publish for local messages, on receive for remote ones, so `team.jsonl` on the node that owns the run directory carries every message once |

Library-only:

```python
from roboglot.team import run_pilot_team
from roboglot.team.mqtt_bus import MqttBus

result = await run_pilot_team(
    duck,
    roster,
    providers=providers,
    bus_factory=lambda tap: MqttBus("desk-1", host="broker.local", tap=tap),
)
```

That call hands the team nobody to ask. A member whose body cannot see a fall, on a task that
can make it walk, is then taken as watched by nobody, and the team ends before any body moves,
every member `aborted`, whatever `yes` says: `yes` is the team's standing answer to its confirm
gates and its pilots' doubts, and never to that question. `acknowledge=` is who answers it, a
function of the member's name and the warning that says whether somebody is watching that
member. `roboglot run` hands in its terminal's question, or under `--yes` an answer of yes for
every member. A solo `AgentLoop` given no `acknowledge` walks after a note instead.

The runner starts the bus inside its event loop and closes it at the end of the run. There is
still no `--bus` flag on purpose. A pilot team has no shared clock to distribute, so nothing
stands in its way except that nobody has run one across two machines, and a flag would imply
somebody had. `MqttBus(..., client=)` takes a fake client, which is how the tests run: two
nodes on a synchronous fake broker with zero sockets.

**Status.** Round-trips of `TALK`, echo, threading and a full `team-hello` run over the bus are
tested on a fake broker in the suite. The real path was exercised once by us, before `TALK`
existed: two `MqttBus` nodes with real paho 2.1 clients against an `amqtt` 0.12 broker on
`localhost:1883` (one Windows 11 machine), every message delivered in order, echo dropped, tap
once per node. `TALK` has crossed a fake broker in the suite and no real one. It has not
carried a team between two machines, for the reason above.
