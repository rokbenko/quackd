# Design: quackd 0.4, from "a brain for the Microduck" to "a brain for any small robot"

**Status:** implemented in 0.4.0 · **Branch:** `feat/multi-robot` · **Shipped:** 2026-09-02

> **Read on 2026-09-13:** this is the record of what 0.4 designed and built, and it is still
> accurate about that. Two things below have since been overtaken. Where it says a flock
> member is a state machine and that every member must be `sim2d`, it is describing the
> **coordinator** flock, which is unchanged; 0.9 added a second kind, a **pilot** flock, where
> each member is a whole `AgentLoop` on wall-clock time on any backend. And the rule at the end
> about a new MCP tool needing its docs in the same commit still holds and has been extended:
> a new CLI command needs its README table row and its `docs/architecture.md` row in the same
> commit, and a test now says so. See [ADR-0034](../adr/0034-registered-robots-and-pilot-flocks.md)
> and [flock.md](../guides/flock.md).

## Why

quackd 0.3 is a working brain for exactly one robot, the Microduck, addressed through
`--transport`. 0.4 generalizes the robot side (adapters that declare a `RobotManifest`) and
the coordination side (heterogeneous flocks) without touching the thesis: **the LLM picks
verbs, the robot's own controllers move, quackd enforces the contract.** No low-level
control, no RL training, no cloud, no telemetry, no renames, no Pollen assets, no new heavy
default dependencies, no fabricated results.

The flagship outcome is one heterogeneous task, *"Reachy spots the ball, the duck kicks
it"*, under one `.duck` contract, in `sim2d`, 10 of 10 seeds, with the Reachy adapter
additionally targeting the real SDK because Reachy Mini hardware exists and Microduck
hardware does not. Nothing has run on hardware and the README keeps saying so.

Decisions with a reason that must not be re-litigated are ADRs:
[0017](../adr/0017-robot-adapters-and-manifest.md) adapters and manifest ·
[0018](../adr/0018-core-verbs-extensions-aliases.md) verbs, extensions, aliases ·
[0019](../adr/0019-duck-spec-v1.md) `.duck` v1 ·
[0020](../adr/0020-heterogeneous-flocks.md) heterogeneous flocks ·
[0021](../adr/0021-lan-discovery-and-mqtt-bus.md) LAN discovery and MQTT bus ·
[0022](../adr/0022-per-adapter-upstream-refs.md) per-adapter upstream refs ·
[0023](../adr/0023-reachy-mini.md) Reachy Mini.

## 1. Module map

### As is (0.3.0, verified by reading)

| Module | Role today |
|---|---|
| `quackd/transport/base.py` | `DuckTransport` Protocol: `connect() -> None`, `close`, `get_frame`, `get_state -> DuckState`, `send_intent(Intent) -> Ack`, `subscribe`, `heartbeat` (raises `HeartbeatError`), `stop`, `now`, `sleep`. `Intent.kind` is `move / stop / do / look / sound / enable / pose`. |
| `quackd/transport/factory.py` | `make_transport(name, *, seed, address, live, camera_url)`; `TRANSPORT_NAMES`, `TRANSPORT_STATUS`. |
| `quackd/transport/{sim2d,mock,jsonrpc_unix,websocket_stub}.py` | the four backends. |
| `quackd/transport/upstream_api.py` | `UpstreamRef(name, status, source, note)`; VERIFIED / UNVERIFIED; `all_refs()`, `refs_by_status()`. |
| `quackd/verbs/registry.py` | `Verb` dataclass, `VerbRegistry` (a dict), `default_registry()` hardcodes builtins plus composites. |
| `quackd/verbs/builtin.py` | `walk stop sit stand kick grab stand_up quack gaze get_frame`; the `_not_fallen` and `_standing` preconditions are attached per verb here, **not** in the executor. |
| `quackd/verbs/composite.py` | `search_scan` (turn in place), `walk_to` (10 Hz steering), `approach_and`. |
| `quackd/safety.py` | `Executor.run_verb`: abort flag, allowlist (`stop` hardwired), registry lookup, params, confirm, budget, battery abort, per-verb preconditions, dry run, timed execute, repeat-failure abort. `Heartbeat`, `KillSwitch`. |
| `quackd/duckfile/schema.py` | `DuckFrontmatter(duck: Literal[0], ...)`, `FlockSection(members 2..4, allocation, safety, search)`, regex-enforced `abort_when`. `schema.json` generated and drift-tested. |
| `quackd/agent/{loop,prompts,transcript}.py` | `AgentLoop`, `build_system_prompt(duck, verbs, transport_name)`, transcript kinds `run_start observation llm enforce verb declare frame run_end`. |
| `quackd/agent/providers/fake.py` | `STRATEGIES` keyed by duck name; emits `walk`, `walk_to`, `search_scan`, `kick`, `quack`. |
| `quackd/flock/*` | `Bus` = `publish` / `subscribe`; pydantic `TaskMsg BidMsg ClaimMsg RoleMsg HbMsg ResultMsg` (discriminator `kind`); Contract Net on `BidMsg.ball_dist_m`; scripted `FlockMember` FSM (SEARCH / KICK / YIELD / STOP); one planner LLM call; ground-truth veto in `run_flock`. |
| `quackd/sim2d/*` | `World.__post_init__` draws duck 0, ball, person, then extra ducks from `default_rng([seed, i])`; `render_duckcam` pinhole; `FlockClock` lockstep. |
| `quackd/mcp_server.py` | `MCPServer` (mcp v2), `DuckSession`, eight `duck_*` tools; `duck_get_frame` and `duck_get_state` bypass the executor. |
| `quackd/cli.py`, `quackd/doctor.py` | `run validate serve-mcp doctor list-verbs record`; `record` calls `_run_impl` positionally. |

### To be (0.4.0)

```
quackd/
  adapters/                     NEW
    __init__.py                 lazy re-exports only (no eager import of base.py: cycle guard)
    manifest.py                 RobotManifest, VerbSpec, SafetyAuthority, Frame, Health, INTENT_KIND_FOR
    manifest.schema.json        generated by `python -m quackd.adapters.export`, drift-tested
    base.py                     RobotAdapter (Protocol), AdapterError, AdapterNotInstalled, backend_name()
    factory.py                  ADAPTER_NAMES, BACKENDS, ADAPTER_STATUS, ADAPTER_EXTRAS, RobotSpec,
                                parse_robot_spec, parse_robots, resolve_robot, make_adapter, describe,
                                list_adapters, warn_once
    microduck/                  MicroduckAdapter wrapping quackd/transport/*; microduck_manifest(); verbs.py
    reachy_mini/                adapter, manifest, verbs, sim2d (ReachyMiniSim2D), mock, sdk, upstream_api
    lerobot/                    adapter (mock | real), verbs, upstream_api
    rosbridge/                  adapter (mock | ws), upstream_api
  verbs/
    aliases.py                  NEW: the one alias table
    core.py                     NEW: observe report_state stop say move go_to search_scan approach_and + REQUIREMENTS
    registry.py                 alias-aware VerbRegistry, view(), registry_from_manifest(), default_registry()
    builtin.py, composite.py    DELETED (moved to verbs/core.py and adapters/microduck/verbs.py)
    learned.py                  unchanged
  duckfile/
    schema.py                   duck: 0 | 1, requires, robots, FlockRole, flock.roles, flock.frame_hints
    validate.py                 NEW: Problem, validate_duck(duck, manifests)
  flock/
    capability.py               NEW: missing(), eligible_roles()
    auction.py                  + RoleAuction, RoleDecision (Auction untouched)
    messages.py                 + HintMsg, VerdictMsg; additive fields on BID / CLAIM / ROLE / RESULT
    coordinator.py, member.py   role mode next to the legacy path
    mqtt_bus.py                 NEW: MqttBus (same Bus protocol), behind quackd[lan]
  lan/                          NEW: txt.py (pure), announce.py, discover.py, behind quackd[lan]
  sim2d/
    world.py, render.py         StationaryHead, render_headcam, fixed head poses, zero RNG draws
    recorder.py, live.py        camera focus by entity
    __init__.py                 make_sim_flock(specs, seed, live): one world, one clock, ducks and heads
  transport/                    UNCHANGED (the Microduck backend layer); base.py gains two intent kinds
  mcp_server.py                 RobotSession, Fleet, robot_* tools, duck_* aliases
  cli.py, doctor.py             --robot / --robots everywhere, list-adapters, discover, announce
ducks/                          + reachy-spotter.duck, reachy-spots-duck-kicks.duck (both duck: 1)
```

## 2. Where the code disagrees with the brief (the code wins)

1. **Preconditions were never in the executor.** `Executor` loops `verb.preconditions`; the predicates live in `verbs/builtin.py`. The move is builtin to adapter; the manifest references conditions by name.
2. **Liveness is `heartbeat()` raising `HeartbeatError`**, pinned by `safety.Heartbeat`, `MockTransport.fail_heartbeat_after` and tests. `health()` is added as the informational call; `heartbeat()` stays the watchdog contract.
3. **Intent kinds are `move / do / look / sound / enable / pose`**, not `twist / skill / gaze`. The manifest keeps the brief's `intents` enum (it is what other systems read) and one table, `INTENT_KIND_FOR`, maps to `Intent.kind`.
4. **The acceptance tests require at least 8 of 10 today**, not 10 of 10. Phase 1 step 0 records the actual per-seed baseline; `QUACKD_STRICT_SEEDS=1` raises the bar to 10 of 10 in CI.
5. `duck_get_frame` and `duck_get_state` bypass the executor today, although `docs/mcp.md` says otherwise. The aliases keep that path (a test pins `session.calls == 3`); the new `robot_observe` goes through the executor.
6. The README's "eight `duck_*` tools" and the set-equality test are honoured.

## 3. Decisions

| # | Decision | Choice |
|---|---|---|
| D1 | Robot-side abstraction | `RobotAdapter` Protocol in `quackd/adapters/base.py`, a **superset of `DuckTransport`**, so an adapter is a drop-in wherever 0.3 took a transport. |
| D2 | Where the Microduck transports live | **Wrapped, not moved.** `quackd/transport/*` is untouched; `MicroduckAdapter(transport)` delegates. The only mechanical proof of zero behaviour change; keeps the UNVERIFIED containment paths and every `quackd.transport.*` import valid. The user-facing word "transport" is retired; the package is the Microduck backend layer. |
| D3 | Alias mechanism | **Canonical storage plus `view()`.** The registry stores `move / go_to / observe`; `ALIASES` lives in `verbs/aliases.py`; `get` / `__contains__` / `unknown` resolve; `view(name)` returns the verb *as the caller named it*, so tool schemas and prompts show the `.duck`'s spelling. `Executor.allowed` returns the contract list verbatim; `is_allowed` compares canonically. Transcript `verb` events keep `name` (as called) and gain `canonical`. |
| D4 | `quack` vs `say` | Separate verbs, no alias. `say(text: str)` is core (the Microduck implementation is quack's tone mapping); `quack(text: str \| None)` stays a Microduck extension. |
| D5 | Bundled starter ducks | **Stay `duck: 0`** with today's spellings, so `uvx quackd run find-and-kick --llm fake`, the fake strategies, the hello-world golden and the tool names cloud models see are unchanged. For `duck: 0`, `effective_requires == verbs.allow`; that is what makes `validate find-and-kick --robot reachy_mini:mock` say `requires kick, but reachy-01 (reachy-mini) does not provide it`. Only new ducks are v1. |
| D6 | Manifest source of truth | Pydantic in `adapters/manifest.py`, `extra="forbid"`, JSON on the wire, no YAML on disk. Every adapter also exposes a **static** `describe(backend, robot_id)` (no SDK import, no socket) for `validate`, `list-verbs --robot`, `announce`; `connect()` returns the same object enriched. |
| D7 | Manifest hash | `RobotManifest.digest()`: sha256 of canonical sorted-key JSON **excluding `id` and `backend`** (a capability fingerprint), first 16 hex. Used by the mDNS TXT `sha` and `robot_list`. |
| D8 | Verb renames | `get_frame` to `observe`, `walk_to` to `go_to`, `walk` to `move`; aliases are permanent. |
| D9 | `stop` | Core, inserted into every manifest if absent, forced `safety_class="safe"`, always allowed, never confirm-gated (manifest validator, schema validator, executor). |
| D10 | `.duck` v1 keys | `duck: 1`; `requires: [verbs]`; `robots:` (string = solo default `adapter:backend`, mapping member to spec = flock defaults); `flock.roles: {spotter: {requires}, kicker: {requires}}`, names restricted to exactly those two in 0.4 (a requires-only role with no behaviour would be a fabricated capability); `flock.frame_hints: auto \| on \| off`. Any of these under `duck: 0` is an error. |
| D11 | `--robot` syntax | `--robot <adapter>[:<backend>]` on `run validate serve-mcp doctor list-verbs`; `--robots name=spec,...` on `run` (flock) and `serve-mcp`; `--transport X` is `--robot microduck:X` with exactly one stderr deprecation line per process plus a `DeprecationWarning`; both given and disagreeing is an error; neither given falls back to the duck's `robots:` then `microduck:sim2d`. Removed in 0.5. |
| D12 | Reachy `say` | **Owner's call, decided:** `say(text)` stays core on Reachy and degrades like the Microduck's tones: text logged verbatim, mood keyword-mapped to an emotion move with its sound; manifest `extras.speech = "tones"`; `play_sound(name)` always exposed as the VERIFIED primitive. |
| D13 | Reachy `stop` | `cancel_move()`; quackd never calls `disable_motors()` (stop is not limp). `safety_authority.native = "none"`: no client deadman or e-stop was verified. |
| D14 | StationaryHead | Fixed pose table `HEAD_POSES` (first entry: the minus-y wall midpoint facing plus-y, measured 10 of 10 line of sight on seeds 0 to 9), **zero RNG draws**, appended after all ducks, `HEAD_R = 0.08`, slate colour `(150, 150, 155)` (saturation below every detector band, visible to humans in the GIF), head yaw limit 180 degrees, `n_ducks = 0` allowed only with `n_heads >= 1`. The sim head transport lives in `adapters/reachy_mini/sim2d.py` with `name = "sim2d"`, because the prompt's simulator note and the CLI's detector and recorder gating key on the bare backend name. |
| D15 | Spotter protocol | New `VERDICT` bus message (`moved / not_moved / lost`, the spotter's own displacement estimate against its first sighting) and `ResultMsg.status += "kick_done"` (an actor's report, never a claim). The spotter is held for the run; the kicker is re-auctioned per cycle. The runner's ground-truth veto is unchanged. |
| D16 | Frame hints | A hint is the **spotter's arena-frame estimate** (fixed pose plus camera estimate); the receiver localizes itself with its own `get_state()` pose. Hints only choose the pre-turn direction before the receiver's own `search_scan`. `auto` is on iff every member backend is `sim2d`. |
| D17 | MCP tools | The five generic tools from the brief **plus `robot_load_duckfile(robot, path)`**: without it a `.duck` can never be loaded onto a non-default robot. The eight `duck_*` tools stay as two-line aliases targeting `Fleet.default` (the only robot; else the first `microduck`; else the first declared), each description carrying a deprecation note. `duck_quack` maps to `quack`; `duck_get_frame` keeps its direct path; `robot_observe` runs `observe` through the executor. |
| D18 | Discovery | `quackd/lan/txt.py` (pure, no third-party imports): service `_quackd._tcp.local.`, TXT keys `v mid sha adp vend mdl emb nverbs`, each pair self-validated under 200 bytes (zeroconf performs no validation and fails with a bare `ValueError` at 255). Identity only; full manifests are never squeezed into TXT. |
| D19 | MQTT bus | `quackd/flock/mqtt_bus.py::MqttBus` implementing the two-method `Bus` protocol; paho v2 callback API; topics `quackd/<flock_id>/ctl` (QoS 1) and `quackd/<flock_id>/hb` (QoS 0); wire format is `FlockMessage.model_dump_json()`; remote messages are marshalled onto the event loop with `call_soon_threadsafe` before tap and push (the transcript writer is not thread-safe); `Subscription.drain()` becomes an atomic `popleft` loop. Library-only in 0.4 (`run_flock(bus_factory=...)`); no `--bus` CLI flag, because a distributed flock also needs a distributed clock, which is out of scope. |
| D20 | Extras | `reachy = ["reachy-mini>=1.10,<2"]`, `lerobot = ["lerobot>=0.6; python_version >= '3.12'"]` (lerobot is 3.12-only; the marker keeps `uv lock` solvable on the 3.11 floor), `lan = ["zeroconf", "paho-mqtt>=2,<3"]`, `rosbridge = ["roslibpy>=1.7"]`. Floors are re-read from the index at implementation time. `all` stays provider-only. |
| D21 | Upstream refs | Every SDK-touching adapter owns `upstream_api.py` (same `UpstreamRef` dataclass; pinned commit and date in every source link); `tests/test_upstream_api.py` is parametrized over `(module, ALLOWED, source_prefix)`; the Microduck row keeps today's ALLOWED set verbatim. |
| D22 | Docs | `docs/adapters.md`, `docs/adapters/<name>.md`, `docs/manifest-spec.md`, `docs/adapter-status.md` (all vendors), `docs/transport-status.md` becomes a short redirect (its test is retargeted in the same commit), `docs/duck-spec.md` v1, `docs/flock.md`, `docs/mcp.md`, `docs/architecture.md`, `docs/lan.md`. |
| D23 | Version | `0.4.0`; 1.0 stays reserved for hardware validation. |
| D24 | rosbridge | **Owner's call, decided: ships in 0.4** with `mock` and `ws` (roslibpy, injected client, topic names as `UpstreamRef`s), extra `quackd[rosbridge]`, status 🧪, never run against a real bridge by us. |
| D25 | `doctor --robot <spec>` | Added: prints that adapter's static manifest summary and its VERIFIED / UNVERIFIED table. Doctor's probes stay localhost-only; the Reachy probe on `:8000` is labelled "something answered on :8000". |
| D26 | Solo Reachy starter | `ducks/reachy-spotter.duck` (v1) with a scripted pilot ships as the adapter's own end-to-end smoke test (10 of 10 on sim2d, judged by ground-truth bearing). Bundled count 6 to 8. |

## 4. Core (Phase 1)

### 4.1 `RobotManifest` (`quackd/adapters/manifest.py`)

```python
Embodiment = Literal["biped", "quadruped", "wheeled", "arm", "stationary_head", "humanoid"]
Mobility = Literal["none", "legged", "wheeled"]
IntentName = Literal["twist", "skill", "gaze", "sound", "joint", "pose", "gripper"]
Sensor = Literal["camera", "battery", "odometry", "imu", "tof", "microphone", "joint_state"]
SafetyClass = Literal["safe", "confirm", "dangerous"]  # registry.py imports it from here
NativeSafety = Literal["robotd_deadman", "lease", "torque_limit", "estop", "none"]
# manifest intent name -> transport.base.IntentKind
INTENT_KIND_FOR = {
    "twist": "move",
    "skill": "do",
    "gaze": "look",
    "sound": "sound",
    "joint": "joint",
    "pose": "pose",
    "gripper": "gripper",
}


class VerbSpec(BaseModel):
    """One manifest verb. `extra="forbid"`."""

    name: str  # canonical, never an alias (validator)
    core: bool = False
    description: str = ""  # "" means the implementation's default
    params_schema: dict[str, Any] = {}  # informational (MCP, mDNS); from the params model
    safety_class: SafetyClass = "safe"
    timeout_s: float | None = None


class SafetyAuthority(BaseModel):
    native: NativeSafety = "none"
    deadman: bool = False
    heartbeat_hz: float = 2.0


class Frame(BaseModel):
    reference: Literal["body", "head", "base", "world"] = "body"
    note: str = ""


class Health(BaseModel):
    ok: bool = True
    reason: str | None = None
    battery_percent: float | None = None
    extras: dict[str, Any] = {}


class RobotManifest(BaseModel):
    """What a connected robot is and can do. Data only. `extra="forbid"`."""

    manifest: Literal[1] = 1
    id: str  # slug: "microduck", "reachy-01", or the fleet/member name when given
    vendor: str
    model: str
    embodiment: Embodiment
    mobility: Mobility
    intents: list[IntentName]
    sensors: list[Sensor] = []
    verbs: list[VerbSpec]
    # verb -> condition names; the adapter supplies the predicates by name
    preconditions: dict[str, list[str]] = {}
    safety_authority: SafetyAuthority = SafetyAuthority()
    frame: Frame = Frame()
    limits: dict[str, float] = {}  # "max_vx", "gaze_yaw_deg", ...
    backend: str = ""  # informational
    blurb: str = ""  # prompt intro: "a small biped duck robot (25 cm, 800 g)"
    extras: dict[str, Any] = {}  # adapter-specific, e.g. {"speech": "tones"}
```

Validators: `id` is a slug; no duplicate or alias verb names; `stop` present and safe; every core verb's `REQUIREMENTS` are met by the manifest (a `go_to` on `mobility: none` is rejected before any registry exists); every precondition key names a declared verb. Methods: `verb_names()`, `provides(name)` and `verb(name)` (alias-aware), `digest()`, `summary()`.

### 4.2 `RobotAdapter` (`quackd/adapters/base.py`)

```python
class AdapterError(TransportError):
    """Subclass, so every existing `except TransportError` still catches it."""


class AdapterNotInstalled(AdapterError):
    """'adapter reachy_mini needs an extra: uv pip install quackd[reachy]'."""


@runtime_checkable
class RobotAdapter(Protocol):
    """A robot, whatever its body. A superset of `DuckTransport`."""

    name: str  # adapter name: "microduck"
    backend: str  # "sim2d" | "mock" | "jsonrpc" | "websocket" | "sdk" | "real" | "ws"
    manifest: RobotManifest | None  # None until connect()

    async def connect(self) -> RobotManifest: ...

    async def disconnect(self) -> None: ...

    async def close(self) -> None:
        """Same as disconnect(). Kept so an adapter satisfies DuckTransport."""

    async def get_state(self) -> DuckState: ...

    async def get_frame(self) -> Image.Image | None: ...

    async def send_intent(self, intent: Intent) -> Ack: ...

    async def health(self) -> Health:
        """Informational: doctor, robot_list, discovery."""

    async def heartbeat(self) -> None:
        """The watchdog contract, unchanged: raise HeartbeatError."""

    async def stop(self) -> None: ...

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]: ...

    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...

    def preconditions(self) -> dict[str, Precondition]:
        """Condition name -> predicate(DuckState) -> reason or None."""

    def implementations(self) -> dict[str, Verb]:
        """Extension verbs and core overrides, keyed by canonical name."""


def backend_name(t: Any) -> str:
    """'sim2d' for a bare Sim2DTransport and for an adapter over one."""
```

`DuckState` keeps its name. Non-duck robots use `posture = "unknown"`, `holding` for grippers, pose fields `None` when unknown. `transport/base.py` changes are additive only: `IntentKind` gains `joint` and `gripper`, `Intent.do(skill: str)` is widened (the `Skill` Literal stays as the Microduck vocabulary), `Intent.joint(positions, duration_s)` and `Intent.gripper(open)` are added.

### 4.3 Verbs

`verbs/aliases.py` holds `ALIASES = {"get_frame": "observe", "walk_to": "go_to", "walk": "move"}` with `canonical()` and `aliases_of()`. No uppercase `GET_FRAME` constant may exist anywhere: it is a banned UNVERIFIED identifier in the containment test.

`verbs/core.py` (bodies byte-identical to builtin and composite except the name literals):

| Core verb | From | Requires | Notes |
|---|---|---|---|
| `observe` | `get_frame` | camera | `read_only=True`; summary stays `"frame captured; ..."` |
| `report_state` | new | nothing | `read_only=True`; `state.summary()` plus `state.model_dump()` |
| `stop` | builtin | nothing | core, forced safe |
| `say(text)` | new | intent `sound` | generic implementation sends `Intent.sound("say", text)`; Microduck overrides with the tone mapping |
| `move` | `walk` | `twist` and mobility not `none` | `MoveParams` equals `WalkParams` verbatim; the 10 Hz re-send loop is unchanged |
| `go_to` | `walk_to` | twist, mobility, camera | control law unchanged |
| `search_scan` | composite | camera and (twist or gaze) | `scan_mode(manifest)`: `turn` (today's loop, also for bare transports) or `gaze` (sweep from the **current** head yaw, `c, c+s, c-s, c+2s, ...`, clipped to `limits["gaze_yaw_deg"]`, one frame per stop, the head left on the target, result gains `gaze_yaw_deg`) |
| `approach_and(then)` | composite | go_to | calls `ctx.run_verb("go_to", ...)`; result data key `"go_to"` (was `"walk_to"`) |

`VerbContext` gains `manifest: RobotManifest | None = None` (last field). `verbs/registry.py`: `Verb.core: bool = False` (last field), `Verb.tool_schema(name=None)`, `VerbRegistry(manifest=None)` with `canonical / get / view / __contains__ / names (canonical) / aliases / unknown / same_verb`, `register()` rejects alias names, `registry_from_manifest(manifest, adapter=None, *, implementations=None, conditions=None)` (implementation lookup: the adapter first, then `CORE`; `ManifestError` on a verb without an implementation or a precondition without a predicate), `default_registry()` returns the Microduck manifest's registry, whose names are `observe report_state stop say move go_to search_scan approach_and sit stand stand_up kick grab gaze quack`. `register_learned_verb` is untouched.

`safety.py`: `Executor.manifest` (last field), `allowed` verbatim, `is_allowed` canonical, `needs_confirm` returns `False` for `stop` and compares the confirm list canonically, `consecutive_failures` keyed on the canonical name, `context()` passes the manifest. Order and messages unchanged. After Phase 1 no module outside `adapters/microduck/` mentions "fallen" or "sitting"; a test asserts it on `inspect.getsource(quackd.safety)`.

`agent/loop.py` connects **first**: `manifest = await transport.connect()`; the registry is `cfg.registry`, else `registry_from_manifest(...)` when a manifest came back, else `default_registry()`, so bare `MockTransport` and `Sim2DTransport` (which return `None`) leave every existing test path unchanged. `run_start` gains `robot` and `adapter`; `summary.json` gains `robot`; `verb` events gain `canonical`. `prompts.py` uses `manifest.blurb` when present and keys the simulator note on `backend_name()`. `flock/member.py` resolves verb names with `_pick("go_to", "walk_to")` so flock-kick's `flock.jsonl` verb names stay byte-identical. `mcp_server.py` builds `session.registry` from the manifest in the lifespan unless `registry=` was passed.

### 4.4 The Microduck adapter (`adapters/microduck/`)

`verbs.py` receives the seven extensions (`sit stand stand_up kick grab gaze quack`), the `say` override and the helpers, moved verbatim. `microduck_manifest(backend, robot_id="microduck")`: biped, legged, intents `twist skill gaze sound pose`, sensors `camera battery odometry imu tof`, preconditions `{move: [standing], kick: [standing], grab: [standing], sit: [not_fallen], stand: [not_fallen], gaze: [not_fallen]}` (exactly today's attachments), `safety_authority(native="robotd_deadman", deadman=True, heartbeat_hz=2)`, limits `max_vx 0.3`, `max_vy 0.2`, `max_wz 1.5`, the 0.3 prompt sentence as `blurb`, and the 0.3 descriptions for `move` and `go_to` so tool schemas for an old duck are byte-identical. `MicroduckAdapter(transport, *, robot_id)` delegates one line per method and forwards the sim-only attributes the flock and recorder use today: `world`, `clock`, `duck_index`, `add_tick_hook`, `post_sleep`.

### 4.5 `.duck` v1 and `duckfile/validate.py`

```python
DUCK_SPEC_VERSION = 1


class FlockRole(BaseModel):
    requires: list[str]
    count: Literal[1] = 1


class FlockSection(BaseModel):
    # ... existing fields ...
    roles: dict[str, FlockRole] | None = None
    frame_hints: Literal["auto", "on", "off"] = "auto"


class DuckFrontmatter(BaseModel):
    duck: Literal[0, 1]
    # ... existing fields ...
    requires: list[str] = []
    robots: str | dict[str, str] | None = None
```

Validators: v1 keys under `duck: 0` raise "needs duck: 1"; `requires` and every role's `requires` are subsets of `allow` (canonical comparison); role names are exactly `spotter` and `kicker`, both present when given; `members` must be a named list when roles are given; `robots` keys are member names; `VerbsSection` rejects alias duplicates (`'walk' and 'move' are the same verb`) and `stop` in `confirm`. `effective_requires` is `requires` for v1 and `verbs.allow` for v0.

`validate_duck(duck, manifests=(), *, registry=None) -> list[Problem]` with `Problem(field, robot, verb, message)`. Order: unknown verbs in `allow` (wording `unknown verbs: fly` kept), `learned_verbs` (v2 wording kept), flock plus confirm (`y/N` wording kept), then per manifest: each `effective_requires` verb not provided gives `requires <verb>, but <id> (<model>) does not provide it`; each role's requires must be satisfiable by at least one manifest, and every robot maps to at most one role. Used by `quackd validate`, `robot_load_duckfile` and `run_flock`. `validate` without `--robot` uses the duck's `robots:` default, else `default_registry()`, so CI's `quackd validate ducks/*.duck` keeps passing for a Reachy duck that allows `express`. The existing mutation test `duck: 0` to `duck: 1` becomes `duck: 2`.

### 4.6 The adapter factory and the CLI

`adapters/factory.py`: `ADAPTER_NAMES = ("microduck", "reachy_mini", "lerobot", "rosbridge")`, `BACKENDS`, `ADAPTER_STATUS`, `ADAPTER_EXTRAS`, `DEFAULT_ROBOT = "microduck:sim2d"`, `RobotSpec(adapter, backend, name)`, `parse_robot_spec`, `parse_robots` (one parser for `run`, `serve-mcp`, `validate`), `resolve_robot(robot, transport, *, duck_default, warn)`, `make_adapter(spec, *, seed, address, live, camera_url)` (lazy per-adapter import; an SDK `ImportError` becomes `AdapterNotInstalled`), `describe(spec)` (the static manifest), `list_adapters()` (no SDK import; versions via `importlib.metadata`), `warn_once()`. Each adapter package exposes `ADAPTER: AdapterInfo`.

`cli.py`: `--robot / -r`, `--transport / -t` (DEPRECATED, default `None`), `--robots`; `_run_impl` gains keyword-only `robot` and `robots` and `record` switches to keyword arguments; detector and recorder gating on `spec.backend == "sim2d"`; the flock gate keeps the `simulator only` text; `list-adapters`; `validate --robot` (repeatable) and `--robots`; `list-verbs --robot`; `doctor [--robot]`.

## 5. Reachy Mini (Phase 2)

Upstream facts were read from `pollen-robotics/reachy_mini` at commit `da0097361c1567f0daf61310e940616171028fd2` on 2026-09-01. Every entry becomes an `UpstreamRef` with a permalink in `adapters/reachy_mini/upstream_api.py`; the summary lives in [ADR-0023](../adr/0023-reachy-mini.md).

**VERIFIED.** PyPI `reachy-mini` 1.10.0, import `reachy_mini`, Apache-2.0, Python 3.11 or newer, heavy (onnxruntime, a GStreamer bundle, FastAPI and uvicorn, Rust wheels), hence an optional extra that the default path never imports. `ReachyMini(robot_name, host="reachy-mini.local", port=8000, connection_mode, spawn_daemon=False, use_sim=False, timeout=5.0, automatic_body_yaw=True, media_backend)` is a WebSocket client to `ws://host:8000/ws/sdk`. Motion: `set_target`, `goto_target(head, antennas, duration, method)`, `look_at_image(u, v, duration)`, `look_at_world(x, y, z, duration)` in metres with x forward, y left, z up; limits pitch and roll 40 degrees, head yaw 180 degrees, body yaw 160 degrees, 65 degrees head-to-body delta, clamped by the daemon. Camera `media.get_frame()` returns BGR uint8 or `None`; `media_backend="no_media"` avoids the GStreamer import. **Audio out is `media.play_sound(file)` and `push_audio_sample(pcm)`; there is no text-to-speech.** Emotes: `RecordedMoves("pollen-robotics/reachy-mini-emotions-library")` from the Hugging Face Hub, `play_move`, `cancel_move`, `wake_up`, `goto_sleep`. Daemon `reachy-mini-daemon` with `--sim` and `--mockup-sim`; mDNS `_reachy-mini._tcp.local.`, `find_robots()`. Safety: `disable_motors()` is limp; a motor watchdog after 1 s of silence; **no client-disconnect deadman, no e-stop primitive, no battery readout**. `spawn_daemon=True` kills a mismatched daemon, so quackd never passes it.

**UNVERIFIED** (each states what quackd does): `NO_CLIENT_DEADMAN`, `NO_ESTOP` (stop is `cancel_move`), `EXPRESSION_NAMES` (the enum is read at connect from the local Hugging Face cache only, never downloaded; `express` is omitted when unavailable), `CAMERA_INTRINSICS_PATH` (fallback FOV 90 degrees, `extras.camera_calibrated = false`), `CAMERA_YAW_COMPOSITION` (camera heading is body yaw plus head yaw, listed in `extras.assumptions` like jsonrpc's posture inference), `CLOSE_METHOD` (context-manager exit), `THREAD_SAFETY` (every SDK call serialized under one lock via `asyncio.to_thread` with a per-call timeout), `LOOK_AT_WORLD_BLOCKS`, `REST_GOTO_SHAPE` (REST unused).

**Manifest.** `id` is the fleet or member name, else `reachy-01`; `vendor pollen-robotics`, `model reachy-mini`, `stationary_head`, `mobility none`, intents `gaze sound skill` (`skill` carries `express`, `play_sound` and `wake_up` through `Intent.do`), sensors `camera` (plus `microphone`, plus `imu` on a wireless unit), core verbs `observe report_state stop say search_scan`, extensions `gaze express play_sound wake_up`, preconditions `{gaze: [motors_enabled], express: [motors_enabled]}`, `safety_authority(native="none", deadman=False, heartbeat_hz=2)`, `frame(reference="head", note="bearings are camera-relative; body bearing = gaze_yaw_deg + bearing_deg")`, `limits {gaze_yaw_deg: 180, gaze_pitch_deg: 40}`, `extras {speech: "tones", camera_calibrated: false}`. `battery_percent` is always `None`, so a "Battery below N%" abort is unenforceable and `validate` warns.

**Verbs.** Reachy's own `gaze` (bearing 180 degrees either way, pitch 40 degrees, no fall precondition; the same name satisfies the same `requires`), `express(name)` from the enum, `play_sound(name)` matching `^[A-Za-z0-9_.-]+\.wav$` (no path separators), `wake_up` confirm-gated, `say` per D12, `stop` is `cancel_move()`.

**Backends.** `mock` subclasses `MockTransport` (refuses `move`, `look` updates `extras.head_yaw_deg`, a synthetic frame with an orange disc at a configurable bearing). `sim2d` is `ReachyMiniSim2D` over `World.heads[i]` (`pid = "head-{i}"`, the `post_sleep` seam honoured, `camera = ("head", i)` for the recorder). `sdk` is EXPERIMENTAL like jsonrpc: lazy `import reachy_mini` inside `connect()`, `media_backend` default, never `spawn_daemon`. Doctor reads the SDK version through `importlib.metadata` only.

**StationaryHead in sim2d.** `world.py` gains `StationaryHead(x, y, theta, head_yaw, head_pitch, r, busy_until, expressions, speech)`, `World(n_heads=0, heads=[])`; `__post_init__` appends heads from `HEAD_POSES[:n_heads]` **after** colorways with no draws; `relative_to(x, y, *, ox, oy, heading)` is extracted with today's exact expression order and `relative()` becomes a wrapper; `relative_head`, `head_look`, `express`, `head_say`, `head_stop`, `head_snapshot`; head collisions and the ball bounce are guarded by `if self.heads`. `render.py` extracts `_render_cam(...)`, keeps `render_duckcam` as a wrapper (heads appended after the ball, so an empty head list renders byte-identically) and adds `render_headcam`; the top-down view draws a slate square, a gaze tick and an `R<i>` label. `recorder.py` takes a `(kind, index)` focus; `live.py` gains a head-only branch; `clock.py` is unchanged. Golden fixtures (poses after 5 s and a kick for one- and three-duck worlds, sha256 of `render_duckcam` at t=0 and t=5, seeds 0 to 9) are recorded from `main` **before** the first edit.

## 6. Heterogeneous flock (Phase 3)

- `messages.py` (all additions defaulted, nothing renamed): `FlockTask.roles`, `frame_hints`, `judge_margin_m = 0.05`, `judge_timeout_s = 6`; `BidMsg.role: str | None` and `BidMsg.provides: list[str]` (sorted canonical verbs, the capability term); `ClaimMsg.assignments`; `RoleMsg.role` gains `SPOT` and `JUDGE`, plus `flock_role`, `seq`, `hint`, `kicker`; `ResultMsg.status` gains `kick_done`; new `Hint`, `HintMsg`, `VerdictMsg(target, kicker, verdict, moved_m, ref, seen, frames)`.
- `flock/capability.py`: `missing(requires, provides)` and `eligible_roles(roles, provides)`, enforced in the member (bids only for eligible roles) **and** re-checked in the coordinator (a `bid_rejected` transcript line; defence in depth for untrusted LAN bids).
- `auction.py`: `Auction` untouched. `RoleAuction` and `RoleDecision`: most-constrained role first, then role name; the winner is the lowest own distance with the member name as tie-break; per-role hysteresis; held roles skipped; void when unfillable. Every ordering is a sort over strings or `(float, str)`; a unit test shuffles insertion order.
- `coordinator.py`: role mode behind `if self.roles`, the legacy path moved verbatim. Flow: SEARCH to all; bids per eligible role; `auction_decision {..., assignments, costs, role_bids}` (the first five keys keep `assert_one_claimant` valid); CLAIM plus ROLE KICK (with a hint when enabled) plus ROLE SPOT (only when newly assigned) plus YIELD; `kick_done`; ROLE JUDGE to the spotter; VERDICT `moved` is success (`"<spotter> judged the ball moved X m after <kicker>'s kick"`), `not_moved`, `lost` or a deadline is a miss (re-SEARCH everyone except held). `_enforce_separation` skips members with `mobility == "none"`. The runner's ground-truth veto is unchanged.
- `member.py`: `manifest`, `registry`, `provides`, `eligible`, `mobile`, `_pick()`. SEARCH pre-turns only when mobile (toward the hint bearing when enabled; heads get no wedge and sweep fully), then bids per eligible role and optionally publishes a HINT. KICK in role mode runs `go_to`, `kick`, a deterministic sidestep `move(vy=0.15, 1.5 s)` and reports `kick_done`. SPOT gazes at the sighting and observes once. JUDGE sweeps offsets `0, ±15, ±30, ±45, ±60` around the last sighting with a fresh frame each and publishes a VERDICT when `|point - ref| >= success_moved_m + judge_margin_m`, else `not_moved` or `lost`. Zero LLM involvement.
- `planner.py`: roles from the `.duck`; wedges over mobile members only; one sentence added to the prompt; `PLAN_TOOL`, `TUNABLE` and `CLAMPS` unchanged, so the model cannot touch roles; at most one call is preserved by construction and pinned by `summary.planner.llm_calls`.
- `runner.py`: `run_flock(..., robots=None)`; the member spec is `--robots`, else `duck.robots`, else `microduck:sim2d`; every backend must be `sim2d`; `--flock N` with roles is an error; the shared arena is built by `sim2d/__init__.py::make_sim_flock(specs, seed, live)` (one `World(seed, n_ducks=k, n_heads=m)`, one `FlockClock`, `Sim2DTransport(world=, clock=, duck_index=i)` and `make_head_transports(world, clock, m)`, wrapped in adapters); the summary adds `roles`, `assignments`, `spotter`, `verdicts`, `frame_hints`; the CLI prints `spotter=... kicker=...`.
- `ducks/reachy-spots-duck-kicks.duck` (v1): `requires: [observe, stop]`, `allow: [observe, search_scan, gaze, say, go_to, move, kick, stop]`, `robots: {reachy-01: reachy_mini:sim2d, duck-01: microduck:sim2d}`, `flock.members: [reachy-01, duck-01]`, `roles: {spotter: {requires: [observe, gaze]}, kicker: {requires: [go_to, kick]}}`, `frame_hints: auto`, `claim_lease_s: 12`, budgets 80 steps, 10 minutes, 5 LLM calls. The body carries the honesty section, the two roles, the verdict rule and the rally rule.
- Frame of reference (documented in `docs/flock.md`): there is no computable relative frame between two robots on hardware; the Microduck has no absolute localization and quackd does not know where a Reachy is mounted. The spotter judges displacement in its own camera frame, which needs no shared frame at all; that is why a stationary spotter is the honest judge on hardware too. A fixed camera can be occluded by static scenery; the shipped pose clears seeds 0 to 29 but one, documented, never worked around with ground truth.

## 7. Multi-robot MCP, discovery, MQTT (Phase 4)

`mcp_server.py`: `RobotSession` (was `DuckSession`, alias kept) with its own `Executor`, `Heartbeat`, `Budget` and manifest-built registry; `Fleet(sessions, default)`; `build_fleet_server(robots, *, duckfile, dry_run, yes, seed, address)`; `build_server(transport, ...)` kept as the one-robot wrapper with its signature and return type; the lifespan connects sequentially and fails fast; each sim2d robot has its own world (a shared arena over MCP is future work and the docstring says so). Tools: `robot_list`, `robot_list_verbs(robot)`, `robot_run_verb(robot, verb, params)`, `robot_observe(robot)` (`structured_output=False`, image plus summary, through the executor), `robot_say(robot, text)` (refused as data on a robot without `sound`), `robot_load_duckfile(robot, path)` (refuses flock ducks, checks `effective_requires` with the validator's wording). The eight `duck_*` aliases are two-line delegations with a deprecation note. Per-robot allowlist, budget and abort; `--yes` and `--dry-run` are global. `INSTRUCTIONS` gains one paragraph when more than one robot is connected. `serve-mcp --robots`.

`quackd/lan/`: `txt.py` (pure), `announce(manifest, *, instance, port=0, zc=None)`, `discover(timeout_s, *, zc=None) -> list[DiscoveredRobot]`; CLI `quackd announce --robot <spec>` (static manifest, no connection held) and `quackd discover`; lazy imports so the default install never imports zeroconf; the import guard names `quackd[lan]`.

`flock/mqtt_bus.py`: `MqttBus(flock_id, *, host, port, tap, client=None, log)`; `start()` and `close()` sit outside the `Bus` protocol; local fan-out with the broker echo dropped by `src in local_ids`; QoS-1 duplicate tolerance is an invariant stated in the module docstring (today's handlers are idempotent: minimum bid, maximum exclusion, idempotent `last_hb`); `retain=False`. Tests use `FakePahoClient` and `FakeBroker` with no sockets; one `importorskip("paho.mqtt.client")` test pins the v2 constructor argument.

`doctor.py`: an adapters table (name, backends, status, extra, installed) that keeps a line containing "transports", per-adapter UNVERIFIED tables, a lan table, the bundled duck count.

## 8. LeRobot and rosbridge (Phase 5)

`adapters/lerobot/` (`mock | real`, extra `lerobot`, Python 3.12 marker). Upstream facts read from `huggingface/lerobot` main (0.6.x): `from lerobot.robots import Robot` with `connect(calibrate=True)`, `disconnect()`, `get_observation() -> dict` (flat, motor keys `*.pos` and `*.vel`, camera arrays by name), `send_action(dict) -> dict`, `observation_features`, `action_features`, `is_connected`, `is_calibrated`; policies via `PreTrainedPolicy.from_pretrained(...)` and `select_action(batch) -> Tensor`. Manifest: `arm`, `mobility none`, intents `joint pose gripper` (plus `skill` when a policy is loaded), sensors `joint_state` (plus `camera`), `safety_authority(native="torque_limit")`, `frame(reference="base")`, verbs `stop report_state move_joints gripper place` (plus `observe` with a camera, plus `pick` with a policy; `pick` is confirm-class, `place` safe), preconditions `{pick: [torque_on], move_joints: [torque_on], place: [holding]}`. `pick` sends `Intent.do("policy:pick")` and the backend runs the policy's observe-act loop at its own rate: LeRobot's controller moves the arm, quackd only says "pick". `real` wraps LeRobot behind one private bridge and every symbol is an `UpstreamRef`; tests inject `robot=` and `policy=` fakes; `mock` is an in-memory six-joint arm with a gripper and a flat frame. Torch is never imported on the 3.11 CI job.

`adapters/rosbridge/` (`mock | ws`, extra `rosbridge`, ships in 0.4 per D24). Manifest: `wheeled`, `mobility wheeled`, intents `twist`, sensors `odometry` (plus `camera`, `observe`, `go_to`, `search_scan` when an image topic is configured), verbs `stop report_state move`, `safety_authority(native="none")`: rosbridge has no deadman, quackd keeps re-sending `Twist` every 100 ms and publishes a zero `Twist` on `stop`, documented as the only stop authority. `mock` is a small kinematic integrator with sim2d deadman semantics so `validate`, `list-verbs` and `--dry-run` work offline. `ws` uses roslibpy 2.x (`Ros(host, port).run()`, `Topic(client, "/cmd_vel", "geometry_msgs/Twist").publish(Message({...}))`, `sensor_msgs/CompressedImage` whose `data` is a base64 string, `/odom` for the pose) with an injected `ros=` client for tests; topic names and message types are `UpstreamRef`s; `--address ws://host:9090`, `--cmd-vel-topic`, `--image-topic`. Twist limits come from `manifest.limits`; the Microduck values are unchanged. Status 🧪 everywhere; the docs page says nobody has run it against a bridge.

## 9. Migration and deprecation

| Surface | 0.3 | 0.4 | Removed |
|---|---|---|---|
| `run --transport X` | the way to pick a backend | works, one stderr line per process, alias of `--robot microduck:X` | 0.5 |
| `serve-mcp --transport` | same | same; `--robots` added | 0.5 |
| `validate`, `list-verbs`, `doctor` | no robot flag | `--robot` / `--robots`; `--transport` accepted with the same warning | 0.5 |
| `.mcp.json`, README, docs | `--transport sim2d` | `--robot microduck:sim2d`; README keeps one "still works" line | |
| `duck_get_state["transport"]`, `summary.json["transport"]` | `"sim2d"` | kept; `robot` added | |
| eight `duck_*` MCP tools | primary | thin aliases with a deprecation note | 0.5 |
| `quackd.transport.*` imports, `make_transport` | canonical | unchanged: the Microduck backend layer, not deprecated | |
| verb names `get_frame`, `walk_to`, `walk` | canonical | permanent aliases | never |
| `docs/transport-status.md` | the honesty table | short redirect to `docs/adapter-status.md` | |
| internal fields named `transport` (`RunConfig`, `Executor`, `VerbContext`, `DuckSession`, `FlockMember`) | | kept | 0.5 |

## 10. Test plan

Baseline first (Phase 1 step 0, before any edit): per-seed outcomes for `find-and-kick` and `flock-kick` on seeds 0 to 9, the `flock-kick` seed-3 summary values and ordered bus `kind` sequence, sim2d golden fixtures (poses and `render_duckcam` hashes) and sha256 of the six bundled `.duck` files, all committed as tests. `QUACKD_STRICT_SEEDS=1` raises both sweeps' bar from 8 to 10 and CI sets it.

Regression guards that must not change: `test_acceptance_sim2d` (10 of 10), `test_flock_run` (10 of 10; `assert_one_claimant` on `ev.get("canonical", ev["name"]) in ("go_to", "kick")`), `test_sim2d` determinism, `test_loop` GOLDEN_HELLO unchanged, `test_executor` order and needles, `test_mcp_server` TOOLS set (14 only from Phase 4), `state["transport"] == "sim2d"`, `allowed == {"quack", "walk", "stop"}`, `session.calls == 3`, `test_cli` messages, the Microduck row of `test_upstream_api` verbatim, `test_docs`, the bundled set in `test_duckfile`, `test_jsonrpc`, `test_providers`, `test_local_provider`.

Edits required, listed exhaustively: `test_registry` BUILTINS and COMPOSITES sets plus `get("walk").tool_schema()["name"] == "move"` and `view("walk")... == "walk"`; `test_duckfile` mutation `duck: 1` to `duck: 2` and the bundled set 6 to 8; `test_cli` "6 file(s)" to "8" and the list-verbs names; `test_mcp_server` name superset `{"move", "kick", "go_to", "quack"}`; `test_doctor_and_stub` needles plus `adapters`; `test_docs` retargets the transport-status test to `adapter-status.md` and the README backticks the five new names; `test_upstream_api` parametrized; `test_flock_run` claimant tuple.

New tests, about 110 functions (157 to about 265): `test_manifest`, `test_adapters`, `test_validate`, `test_cli` additions, `test_sim2d_head`, `test_reachy_adapter`, `test_reachy_upstream`, `test_acceptance_reachy`, `test_flock_roles`, `test_flock_hetero_run`, `test_mcp_fleet`, `test_lan`, `test_mqtt_bus`, `test_lerobot_adapter`, `test_rosbridge_adapter`, `test_docs` additions, `test_smoke` help list, an import-without-extras test. A conftest autouse fixture refuses `socket.create_connection` except in `test_jsonrpc` (loopback) and `test_doctor_and_stub` (localhost probes).

Same-commit rules: a registry name change lands with the README verb table and the duck-spec, architecture, safety and FAQ docs; a bundled duck lands with the `test_duckfile` set, the `test_cli` count, the README row, `docs/flock.md` and the CHANGELOG; a refs module lands with its `test_upstream_api` row, `docs/adapter-status.md` and the pyproject `E501` per-file ignore; an MCP tool change lands with the TOOLS set, `docs/mcp.md`, `INSTRUCTIONS` and `.mcp.json`; a `DuckFrontmatter` or `RobotManifest` change lands with its regenerated JSON schema and spec doc; a CLI command lands with `test_smoke`, the README usage table and `docs/architecture.md`; every README edit obeys the prose rules.

The gate before every commit is exactly what CI runs: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, `uv run quackd validate ducks/*.duck`. Phase gates add `QUACKD_STRICT_SEEDS=1 uv run pytest tests/test_acceptance_sim2d.py tests/test_flock_run.py`.

## 11. Phases

| Phase | Bar | Commits |
|---|---|---|
| 0 | this document and ADRs 0017 to 0023 | `docs(design): ...` |
| 1 Core | entire existing suite green; find-and-kick 10 of 10; `uvx quackd run find-and-kick --llm fake` unchanged | baselines; alias table; manifest, adapter, Microduck adapter, registry from manifest; executor and loop; `.duck` v1 and validate; `--robot` and factory; ADRs |
| 2 Reachy Mini | `--robot reachy_mini:{sim2d,mock}` runs `reachy-spotter` 10 of 10; sdk refuses cleanly without the extra; containment green | StationaryHead; upstream refs; adapter; starter duck; docs |
| 3 Heterogeneous flock | `reachy-spots-duck-kicks` 10 of 10; every message in `flock.jsonl`; at most one planner call; flock-kick goldens unchanged | capability and RoleAuction; role mode and SPOT/JUDGE; runner and `--robots`; the duck; ADR |
| 4 MCP, discovery, MQTT | `serve-mcp --robots ...` lists 14 tools; `discover` and `announce` run on fakes; the MQTT bus round-trips every kind offline | atomic drain; robot_* tools; zeroconf; MQTT; docs |
| 5 LeRobot and rosbridge | both mocks validate and run offline; `real` and `ws` refuse without their extras and work with injected fakes; nothing heavy on the default path | lerobot; rosbridge; ADR-0022 |
| 6 Docs and release | README status table honest; `docs/adapters.md` complete; wheel smoke test from a temp dir | docs; CHANGELOG; `chore: release v0.4.0` |

## 12. Risks

- Byte-identity of seeded runs rests on discipline: any RNG draw, object-list reorder or new snapshot key on the duck path changes every seed. Goldens are recorded before the first edit and both sweeps run after every Phase 1, 2 and 3 commit.
- The 10 of 10 heterogeneous bar is a target, not yet a measurement: line of sight at t=0 is measured, the post-kick line of sight and the approach path are not. Mitigations are the kicker's sidestep, the judge sweep, rallies with a held spotter and the 0.05 m judge margin. If a seed still fails, the answer is a wider sweep or documenting the seed, never a ground-truth shortcut.
- The Reachy sdk backend cannot be exercised offline and CI never installs the SDK; it ships EXPERIMENTAL like jsonrpc with import-error and name-pinning tests only. A long `express` blocks other SDK calls behind the single-flight lock, so the heartbeat period must tolerate `duration + 2 s`.
- `test_docs` couples the registry to the README and `adapter-status.md` to every ref module; run it before every docs commit.
- The import cycle between `adapters.manifest` and `verbs.core` or `verbs.registry` is avoided only by lazy imports; a test imports each module first in a fresh interpreter.
- Once-per-process deprecation interacts with `CliRunner` tests that run several commands in one process; a fixture resets the set.
- Wall clock: ten heterogeneous seeds and ten Reachy seeds with head-cam renders are added; each must stay under 60 s.
- `lerobot` needs Python 3.12 and pulls torch; the extra marker and an import-without-extras test guard it.
- There is no Windows CI runner while the development platform is Windows; the socket-refusing fixture and the lazy imports must behave on both.

## 13. Acceptance

```bash
uv run pytest                                                              # green, offline, no keys
QUACKD_STRICT_SEEDS=1 uv run pytest tests/test_acceptance_sim2d.py tests/test_flock_run.py tests/test_flock_hetero_run.py tests/test_acceptance_reachy.py
uvx quackd run find-and-kick --llm fake --seed 3                      # unchanged; 10 of 10 on seeds 0-9
uvx quackd run reachy-spots-duck-kicks --llm fake --seed 3            # 10 of 10; flock.jsonl written; spotter= kicker=
uvx quackd validate ducks/find-and-kick.duck --robot reachy_mini:mock      # exit 1: requires kick, but reachy-01 (reachy-mini) does not provide it
uvx quackd list-adapters                                                   # microduck, reachy_mini, lerobot, rosbridge with status
uvx quackd serve-mcp --robots duck=microduck:sim2d,reachy=reachy_mini:mock # six robot_* tools plus eight duck_* aliases
uvx quackd doctor                                                          # adapters, extras, VERIFIED / UNVERIFIED per adapter
uvx quackd run find-and-kick --transport sim2d --llm fake             # still works, one deprecation line
```

## 14. Only a human can do these

Run `reachy_mini:sdk` against a physical Reachy Mini (or `reachy-mini-daemon --mockup-sim`) and attach `quackd doctor` and the transcript; run `microduck:jsonrpc` against a real `robotd` when Microducks ship; run `lerobot:real` against a real arm; run `rosbridge:ws` against a real bridge; run an MQTT flock against a broker; record a real-model hero run; flip 🧪 rows to ✅ only after those.
