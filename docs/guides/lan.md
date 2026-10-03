# Robots on a LAN: discovery and the MQTT bus

Both ship in 0.4 behind one extra and are imported lazily, so the default install never
loads them and the test suite runs on fakes with no network and no broker
([ADR-0021](adr/0021-lan-discovery-and-mqtt-bus.md)).

The MQTT bus is the flock bus of [flock.md](flock.md) across machines, a robot that
answers discovery is registered by hand with `quackd robot add` ([registry.md](registry.md)),
and discovery feeds no flock by itself.

```bash
uv pip install 'quackd[lan,microduck]'   # zeroconf + paho-mqtt, and a robot to announce
```

## Discovery (`_quackd._tcp.local.`)

A quackd instance can advertise the robot it fronts, and another can list what answers:

```bash
quackd announce --robot microduck:sim2d --name duck-01         # until Ctrl-C (or --for 30)
quackd discover --timeout 3                                    # a table of what answered
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
from quackd.adapters.factory import RobotSpec, describe
from quackd.lan.announce import announce
from quackd.lan.discover import discover

ann = announce(describe(RobotSpec("microduck", "sim2d", "duck")), adapter="microduck")
for robot in discover(timeout_s=3.0):
    print(robot.manifest_id, robot.adapter, robot.addresses, robot.matches(some_manifest))
ann.close()
```

`announce(..., zc=, info_factory=)` and `discover(..., zc=, browse=)` take fakes, which is
how the tests run. quackd's own advertisement is always `_quackd._tcp.local.`; if an
upstream daemon happens to advertise itself separately under its own service name, quackd
does not rename it or merge the two, it only ever advertises the quackd side.

**Status.** The record format and both commands are tested on fakes in the suite. The real
zeroconf path was exercised once by us on one Windows 11 machine between two processes
(announce a mock manifest in a child, discover it from the parent, digest matched); it has
not been exercised between two machines.

## The MQTT flock bus

`quackd.flock.mqtt_bus.MqttBus` implements the same two-method `Bus` protocol as the
in-process bus (`publish`, `subscribe`) over a broker, and keeps the flock's one hard rule:
nobody ever awaits the bus. paho runs its own network thread; `publish` is a synchronous
local fan-out plus a non-blocking hand-off; members still `drain()` between sim sleeps.

| | |
|---|---|
| Topics | `quackd/<flock_id>/ctl` (TASK, BID, CLAIM, ROLE, RESULT, HINT, VERDICT, TALK) at QoS 1, `quackd/<flock_id>/hb` (HB) at QoS 0, `retain=False` everywhere |
| Payload | the pydantic `FlockMessage` JSON, exactly what `flock.jsonl` records |
| Echo | a broker sends your own publications back; a message whose `src` is one of this node's subscribers is dropped and counted |
| Duplicates | QoS 1 is at least once; the coordinator's handlers are idempotent (minimum bid per source, maximum exclusion, idempotent heartbeats), so a duplicate changes no decision, and a test pins that |
| Threads | remote messages are marshalled onto the event loop with `call_soon_threadsafe` before they are tapped and pushed, because the transcript writer is not thread-safe |
| Tap | fires exactly once per message per node: on publish for local messages, on receive for remote ones, so `flock.jsonl` on the node that owns the run directory carries every message once |

Library-only:

```python
from quackd.flock.mqtt_bus import MqttBus
from quackd.flock.runner import run_flock

result = await run_flock(
    duck,
    provider=provider,
    bus_factory=lambda tap: MqttBus("desk-1", host="broker.local", tap=tap),
)
```

Either runner starts the bus inside its event loop and closes it at the end of the run. There
is still no `--bus` flag on purpose, for a different reason per kind. A coordinator flock
across machines also needs a distributed clock, because it runs on one lockstep simulator
clock, and that is out of scope. A pilot flock has no shared clock to distribute, so nothing
stands in its way except that nobody has run one across two machines, and a flag would imply
somebody had. `MqttBus(..., client=)` takes a fake client, which is how the tests run: two
nodes on a synchronous fake broker with zero sockets.

**Status.** Round-trips of every message kind, echo, duplicates, threading and a full
`flock-kick` run over the bus are tested on a fake broker in the suite. The real path was
exercised once by us: two `MqttBus` nodes with real paho 2.1 clients against an `amqtt`
0.12 broker on `localhost:1883` (one Windows 11 machine), all eight message kinds that
existed that day delivered in order, echo dropped, tap once per node. `TALK`, the ninth,
rides `ctl` like the rest and has crossed a fake broker in the suite and no real one. It has
not carried a flock of either kind between two machines, for the reasons above.
