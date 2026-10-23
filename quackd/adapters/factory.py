"""`--robot <adapter>:<backend>` -> a `RobotAdapter`, plus everything the CLI needs to talk
about adapters without connecting to one (static manifests, the status table).

Adapter packages are imported lazily, so listing adapters never imports an SDK.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from quackd.adapters.base import (
    POLICY_SPECS,
    AdapterError,
    AdapterNotInstalled,
    PolicyChoice,
    RobotAdapter,
    camera_urls,
)
from quackd.adapters.catalogue import BY_NAME, ENTRY_POINT_GROUP, OFFICIAL, AdapterInfo
from quackd.adapters.manifest import RobotManifest
from quackd.verbs.registry import VerbRegistry, core_registry, registry_from_manifest

if TYPE_CHECKING:
    from quackd.host import HostClient

DEFAULT_ROBOT = "microduck:sim2d"
"""The body a command falls back to when nothing named one and several are installed.

It survives because `find-and-kick` and the other v0 starters carry no `robots:` line and a
reader who types `quackd run find-and-kick` means the cartoon. Where exactly one adapter is
installed, that one is the default instead, and where none is, there is no default at all."""


@lru_cache(maxsize=1)
def _installed() -> dict[str, str]:
    """Every adapter that can be built here, name to the module that builds it.

    Two ways in, and they are the same question asked before and after the adapters became
    their own distributions. An installed adapter announces itself through the entry point
    group, which is how a third party's is found and the only way one can be. An adapter
    that still lives inside the core wheel is found by looking, because it is installed by
    virtue of being here at all, and asking the metadata about it would answer only whether
    somebody had reinstalled since the entry points were declared.

    Cached: `entry_points()` walks the whole environment, and this is on the path of every
    `doctor`. Nothing installs an adapter mid-process."""
    found: dict[str, str] = {}
    for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        # an editable install keeps the metadata it was built with, so a module that has been
        # moved or removed since is still announced here. Believing that turns a missing
        # adapter into an ImportError from somewhere far away.
        with contextlib.suppress(ImportError, ValueError):
            if importlib.util.find_spec(ep.value) is not None:
                found[ep.name] = ep.value
    for name in BY_NAME:
        if name in found:
            continue
        module = f"quackd.adapters.{name}"
        if importlib.util.find_spec(module) is not None:
            found[name] = module
    return found


def adapter_names() -> tuple[str, ...]:
    """Every adapter quackd can talk about: the nine it publishes, in their fixed order,
    then anything else installed here, alphabetically. A name quackd publishes keeps its
    place whether or not it is installed, because the tables that list them are a catalogue
    rather than an inventory."""
    third_party = sorted(name for name in _installed() if name not in BY_NAME)
    return tuple(BY_NAME) + tuple(third_party)


def info(name: str) -> AdapterInfo:
    """What quackd knows about this adapter without importing it.

    A third party's adapter has no catalogue row, so its own module is asked instead, which
    means importing it. That is fine: it is installed, or this raises anyway."""
    if (known := BY_NAME.get(name)) is not None:
        return known
    module = _module(name)
    return AdapterInfo(
        name=name,
        backends=tuple(getattr(module, "BACKENDS", ())),
        status=str(getattr(module, "STATUS", "installed here, not published by quackd")),
        summary=str(getattr(module, "SUMMARY", "a robot quackd does not publish")),
        extra=getattr(module, "EXTRA", None),
        sdk=getattr(module, "SDK", None),
    )


def is_installed(name: str) -> bool:
    return name in _installed()


def is_official(name: str) -> bool:
    return name in BY_NAME


ADAPTER_NAMES = tuple(BY_NAME)
BACKENDS = {i.name: i.backends for i in OFFICIAL}
ADAPTER_STATUS = {i.name: i.status for i in OFFICIAL}
ADAPTER_EXTRAS = {i.name: i.extra for i in OFFICIAL if i.extra}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class RobotSpec:
    adapter: str
    backend: str
    name: str | None = None
    """The member or fleet name (`duck=microduck:sim2d`), which becomes the manifest id."""

    @property
    def key(self) -> str:
        return f"{self.adapter}:{self.backend}"

    @property
    def robot_id(self) -> str | None:
        """The manifest id to ask for: the fleet name, or the adapter's own default."""
        return self.name


def parse_robot_spec(text: str) -> RobotSpec:
    """`microduck:sim2d`, or `microduck` (its first backend). Unknown names list the choices.

    A name quackd publishes parses whether or not it is installed here, so a robot can be
    registered, listed and printed on a machine that cannot build it. Asking for the body
    itself is what refuses."""
    text = text.strip().lower()
    adapter, _, backend = text.partition(":")
    known = adapter_names()
    if adapter not in known:
        raise AdapterError(f"unknown adapter {adapter!r}; choose one of {', '.join(known)}")
    backends = info(adapter).backends
    backend = backend or backends[0]
    if backend not in backends:
        raise AdapterError(
            f"unknown backend {backend!r} for {adapter}; choose one of {', '.join(backends)}"
        )
    return RobotSpec(adapter, backend)


def parse_robots(text: str) -> list[RobotSpec]:
    """`duck=microduck:sim2d,arm=lerobot:mock` -> named specs, order preserved."""
    specs: list[RobotSpec] = []
    for item in [part.strip() for part in text.split(",") if part.strip()]:
        name, sep, spec_text = item.partition("=")
        if not sep or not _NAME_RE.match(name.strip()):
            raise AdapterError(f"{item!r} is not name=<adapter>:<backend> (name is a slug)")
        if any(s.name == name.strip() for s in specs):
            raise AdapterError(f"duplicate robot name {name.strip()!r}")
        spec = parse_robot_spec(spec_text)
        specs.append(RobotSpec(spec.adapter, spec.backend, name.strip()))
    if not specs:
        raise AdapterError("--robots needs at least one name=<adapter>:<backend>")
    return specs


class NoRobotNamed(AdapterError):
    """Nothing said which body, and quackd will not pick one for you."""


def default_spec() -> RobotSpec:
    """The body a command means when nothing named one, or a refusal saying why there is none.

    Four answers, and which one you get depends only on what is installed here. With nothing
    installed there is no robot to default to and the message says what to install. With
    exactly one adapter installed that one is the default, because a machine with one robot
    has no ambiguity to resolve and making its owner type the name would be ceremony. With
    several including the Microduck, `microduck:sim2d` is still the default, because the
    starter task files that name no robot have always meant the cartoon and a machine that
    installed the duck on purpose is not surprised by it. With several and no Microduck among
    them, quackd refuses to guess and lists what is here, rather than reaching for whichever
    body happens to sort first."""
    usable = [name for name in adapter_names() if is_installed(name)]
    if not usable:
        publishes = ", ".join(n for n in BY_NAME if n != "microduck")
        raise NoRobotNamed(
            'no robot adapter is installed: uv pip install "quackd[microduck]" '
            f"(or {publishes}; quackd[robots] installs all nine), "
            "then --robot <adapter>:<backend>"
        )
    if len(usable) == 1:
        only = usable[0]
        return RobotSpec(only, info(only).backends[0])
    if DEFAULT_ROBOT.split(":", 1)[0] in usable:
        return parse_robot_spec(DEFAULT_ROBOT)
    raise NoRobotNamed(
        "no robot named: --robot <adapter>:<backend> or a registered name "
        f"(quackd robot list); installed here: {', '.join(usable)}"
    )


def resolve_robot(robot: str | None, *, duck_default: str | None = None) -> RobotSpec:
    """`--robot` wins; without it, the duck's own `robots:` default, then whatever is here."""
    if robot:
        return parse_robot_spec(robot)
    if duck_default:
        return parse_robot_spec(duck_default)
    return default_spec()


def _module(adapter: str) -> Any:
    """The module that builds this robot, or a refusal naming what to install.

    Imported here and nowhere earlier, so listing adapters, parsing a spec and printing a
    registry all work on a machine where the adapter is not installed at all."""
    where = _installed().get(adapter)
    if where is None:
        if (row := BY_NAME.get(adapter)) is not None:
            raise AdapterNotInstalled(adapter, row.extra or f"quackd[{adapter}]")
        known = ", ".join(adapter_names())
        raise AdapterError(f"unknown adapter {adapter!r}; installed here: {known}")
    module = importlib.import_module(where)
    _same_release(adapter, module)
    return module


def _release_of(version: str) -> tuple[str, ...]:
    """The major and minor of a version, the part an adapter and the core must share."""
    return tuple(version.split(".")[:2])


def _same_release(adapter: str, module: Any) -> None:
    """Refuse one of quackd's own adapters that is from another release than this core.

    They are released together, each carrying the core's own `__version__`, and each allows a
    core of its own minor and no other (RELEASING.md). pip still lets a mix happen: upgrading
    quackd on its own leaves the adapter where it was and only warns. The core then reads
    declarations the older adapter does not make, `REST_POSE_BACKENDS` among them, and reads
    each missing one as none, so a run it would refuse on a real arm with no rest pose went
    ahead (ADR-0058). Refused here, where everything that describes, builds or asks about a
    body passes, so the refusal says what to reinstall before anything connects. A module with
    no `__version__` of its own is inside the core wheel, and a third party's adapter is not
    quackd's to version: neither is asked."""
    from quackd import __version__ as core

    theirs = getattr(module, "__version__", None)
    if adapter not in BY_NAME or not isinstance(theirs, str):
        return
    if _release_of(theirs) == _release_of(core):
        return
    extra = BY_NAME[adapter].extra or f"quackd[{adapter}]"
    raise AdapterError(
        f"quackd-{adapter} {theirs} is from another release than quackd {core}, and the two are "
        f'released together: reinstall them together: uv pip install -U "{extra}"'
    )


def describe(spec: RobotSpec, *, policy: PolicyChoice | None = None) -> RobotManifest:
    """The static manifest: no SDK import, no socket. What `validate` and `announce` use.

    `policy` is the server a run hands the arm's segments to (`--policy-url`), passed only when
    one was given, so a body described without one is described exactly as it always was. With
    one, the arm's static manifest carries `manipulate` and `pick`, which its connect would
    otherwise be the first to claim, so a task that allows them is judged before anything
    connects. A body that runs no policy is refused (`_takes_policy`)."""
    module = _module(spec.adapter)
    if policy is None:
        return module.describe(spec.backend, spec.robot_id)
    _takes_policy(spec, module.describe)
    return module.describe(spec.backend, spec.robot_id, policy=policy)


def _takes_policy(spec: RobotSpec, fn: Any) -> None:
    """Refuse a policy for a body whose adapter has no `policy` parameter where it is asked for
    one, rather than calling it with a keyword it does not know. By name, never through
    `**kwargs`: an adapter that swallows every keyword would take a policy and drop it, and a
    run told a policy drives the arm would then be driving it with nothing but the pilot."""
    try:
        takes = "policy" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        takes = False
    if not takes:
        raise AdapterError(
            f"{spec.key} runs no policy, so --policy-url has nothing to hand it: only the "
            f"LeRobot arm does ({' and '.join(POLICY_SPECS)})"
        )


def is_simulator(spec: RobotSpec | str) -> bool:
    """Whether this robot is a simulator of a real body rather than the body, asked before
    anything is built: the adapter module's own `SIMULATOR_BACKENDS`, and a module that
    declares none has none. `lerobot:mujoco` is one. What a command that must never touch a
    real arm refuses by, and not `perception.is_simulated`, which counts a mock too."""
    if isinstance(spec, str):
        spec = parse_robot_spec(spec)
    backends = getattr(_module(spec.adapter), "SIMULATOR_BACKENDS", ())
    return spec.backend in tuple(backends)


def needs_rest_pose(spec: RobotSpec | str) -> bool:
    """Whether this robot must have a recorded rest pose before a run, an MCP session or a
    flock may connect it, asked before anything is built: the adapter module's own
    `REST_POSE_BACKENDS`, and a module that declares none needs none, as with
    `SIMULATOR_BACKENDS` above.

    The LeRobot arm's real backend is the one (ADR-0058): with no rest pose there is nothing
    to fold the arm to at the end of a run, a dry one included, and the close keeps its torque
    rather than let it fall where it stands, so the run would leave it holding itself up
    wherever it stopped. Its mock and its simulator are not, having no arm to drop. `doctor`
    and `rest-pose` do not ask: one is a probe that moves nothing, and the other is how a pose
    comes to be recorded at all.

    A spec that does not parse, or an adapter that is not installed or does not import, needs
    none here and fails where it always did, when it is built."""
    try:
        if isinstance(spec, str):
            spec = parse_robot_spec(spec)
        backends = getattr(_module(spec.adapter), "REST_POSE_BACKENDS", ())
    except Exception:
        return False
    return spec.backend in tuple(backends)


def deprecated_backend(spec: RobotSpec | str) -> str | None:
    """The one line a backend on its way out is announced with, or None for any other.

    Read off the adapter module's own `DEPRECATED_BACKENDS`, which maps a backend's name to that
    line: what to use instead, and the release the backend goes in. A module that declares none
    has none, as with `SIMULATOR_BACKENDS` above. `quackd run`, `doctor`, `serve-mcp` and
    `robot list --probe` ask this just before they connect a robot, and print the line then,
    which is the warning at run time RELEASING.md asks of a removal announced a minor ahead.

    It is a mapping where `SIMULATOR_BACKENDS` is a tuple, because the line is the adapter's own
    to write. A declaration in any other shape, or a line that is not a string, raises TypeError
    naming the module, rather than being read as nothing: a backend its author meant to retire
    would then be connected through with no word said, which is the one failure this exists to
    prevent, and the author meets the error the first time one of those commands asks about a
    robot on any backend of theirs.

    A spec that does not parse, an adapter that is not installed, and an adapter whose module
    fails to import in any way have no line, and this returns None rather than raise. An import
    can fail with more than ImportError: a top level that raises RuntimeError, or an extension
    whose DLL will not load, which is an OSError. Those are the body's failures rather than the
    notice's, and they are not raised here: a command meets them where it always did, when it
    describes or builds the body, in whatever words that has always taken. Each of those
    commands asks only about a body it has already built, whose module imported as it was built,
    so for them this is a guard rather than a path."""
    try:
        if isinstance(spec, str):
            spec = parse_robot_spec(spec)
        module = _module(spec.adapter)
    except Exception:
        return None
    declared = getattr(module, "DEPRECATED_BACKENDS", None)
    if declared is None:
        return None
    where = getattr(module, "__name__", spec.adapter)
    if not isinstance(declared, Mapping):
        raise TypeError(
            f"{where}.DEPRECATED_BACKENDS is a {type(declared).__name__}, and quackd reads it "
            "as a mapping of a backend's name to the one line that announces it, such as "
            "{'old': 'adapter:old is deprecated and is removed in X.Y.0: use adapter:new'}"
        )
    line = declared.get(spec.backend)
    if line is None:
        return None
    if not isinstance(line, str):
        raise TypeError(
            f"{where}.DEPRECATED_BACKENDS[{spec.backend!r}] is a {type(line).__name__}, and "
            "quackd prints it as the one line that announces the backend, so it is a string"
        )
    return line or None


def registry_for(spec: RobotSpec, manifest: RobotManifest | None = None) -> VerbRegistry:
    """The vocabulary of a robot that is not connected (`list-verbs --robot`, `--goal`).

    `manifest` is the static one changed by something the body does not know about, which today
    is only the camera on a `--host` board (`quackd.adapters.host_camera`); None describes the
    body as its adapter does."""
    module = _module(spec.adapter)
    return registry_from_manifest(
        manifest if manifest is not None else describe(spec),
        implementations=module.implementations(),
        conditions=module.conditions(),
    )


def make_adapter(
    spec: RobotSpec | str,
    *,
    seed: int | None = None,
    address: str | None = None,
    live: bool = False,
    camera_url: str | Sequence[str] | None = None,
    token: str | None = None,
    rest_pose: Mapping[str, float] | None = None,
    host: HostClient | None = None,
    faults: str | None = None,
    scene: Sequence[Mapping[str, Any]] | None = None,
    policy: PolicyChoice | None = None,
) -> RobotAdapter:
    """Build a robot. `camera_url` may name several cameras; every `make()` is handed the
    tuple and decides whether this body reads more than one (`MULTI_CAMERA_SPECS`), and a
    `rest_pose` reaches a body that parks or is refused by one that does not.

    `host` is the board `--host` names. When its daemon has a camera, the body is wrapped in a
    `HostCameraAdapter`, which adds that camera to any body and changes nothing else. The
    board is asked for its `/hello` here unless the client already has it, so a caller that
    must refuse a board that does not answer asks first; this raises `HostError` otherwise.

    `faults` is a spec of faults for a simulator to meet, and `scene` the objects it lays out
    in place of its own (`quackd preflight` reads them from a task's sidecar). Each is passed to
    `make()` only when given, so every adapter that has no simulator is called exactly as it
    always was.

    `policy` is the policy server `--policy-url` names, passed by the same rule, and refused for
    a body whose `make()` has no `policy` parameter (`_takes_policy`): only the LeRobot arm
    hands a segment to one, and a third party's adapter built without the flag is called as it
    always was."""
    if isinstance(spec, str):
        spec = parse_robot_spec(spec)
    module = _module(spec.adapter)
    extra: dict[str, Any] = {} if faults is None else {"faults": faults}
    if scene is not None:
        extra["scene"] = [dict(item) for item in scene]
    if policy is not None:
        _takes_policy(spec, module.make)
        extra["policy"] = policy
    adapter: RobotAdapter = module.make(
        spec.backend,
        robot_id=spec.robot_id,
        seed=seed,
        address=address,
        live=live,
        camera_url=camera_urls(camera_url),
        token=token,
        rest_pose=dict(rest_pose) if rest_pose else None,
        **extra,
    )
    if host is not None:
        hello = host.hello()
        if hello.has_camera:
            from quackd.adapters.host_camera import HostCameraAdapter

            return HostCameraAdapter(adapter, host, hello)
    return adapter


def list_adapters() -> list[dict[str, Any]]:
    """Rows for `quackd list-adapters` and `doctor`, without importing any SDK.

    `installed` is whether the adapter itself is here, and `sdk` whether the library its
    real backend needs is. They are separate questions: an adapter with a mock is useful
    with no SDK at all, and reporting the whole robot missing because a wheel it only needs
    for hardware is absent told a simulator user their duck was gone."""
    rows = []
    for name in adapter_names():
        row = info(name)
        sdk = None if row.sdk is None else importlib.util.find_spec(row.sdk) is not None
        rows.append(
            {
                "name": name,
                "backends": list(row.backends),
                "status": row.status,
                "extra": row.extra or "built-in",
                "official": is_official(name),
                # an adapter with no SDK to probe is usable as soon as it is here at all
                "installed": is_installed(name) and sdk is not False,
                "adapter_installed": is_installed(name),
                "sdk": sdk,
            }
        )
    return rows


def installed_vocabulary() -> VerbRegistry:
    """Every verb any body installed here provides, under one registry.

    What `quackd validate` checks a task file against when the file names no robot. A `.duck`
    is a contract rather than a robot, so a task that allows `kick` is a coherent task as long
    as something here can kick; asking whether one particular body can keep it is what
    `validate --robot NAME` is for, and that path reads the body's own manifest instead.

    The union rather than the Microduck's list, which is what it used to be. On a machine with
    only an arm installed, a duck that allows `kick` should be told nothing here kicks, rather
    than being checked against a duck that is not present.

    A body's verbs include what it offers with a policy server, `pick` and `manipulate` on the
    arm, which its description holds only when it is given one (`describe(policy=...)`), so a
    `duck: 3` task file is coherent here whichever of the arm's backends is described first.
    Each backend is asked through the factory's own `policy` parameter, which refuses a body
    that runs no policy, with an address nothing is ever sent to: a description is static, so
    this reaches no server and imports no torch."""
    registry = core_registry()
    offered = PolicyChoice("http://127.0.0.1")
    for name in adapter_names():
        if not is_installed(name):
            continue
        try:
            backends = info(name).backends
        except (AdapterError, ImportError):
            continue
        with contextlib.suppress(AdapterError, ImportError):
            _union(registry, registry_for(RobotSpec(name, backends[0])))
        for backend in backends:
            spec = RobotSpec(name, backend)
            with contextlib.suppress(AdapterError, ImportError):
                _union(registry, registry_for(spec, describe(spec, policy=offered)))
    return registry


def _union(registry: VerbRegistry, body: VerbRegistry) -> None:
    """Add to `registry` every verb of `body` it does not have yet, the first body's kept."""
    for verb_name in body.names():
        if verb_name not in registry:
            registry.register(body.get(verb_name))


def installed_manifests() -> list[tuple[str, RobotManifest]]:
    """(adapter name, its static manifest) for every body installed here, in table order.

    The first backend of each, because a body is the same body on all of them. Static, so
    this costs no SDK import and no connection.

    Installed rather than published, and that is the whole of the difference since the
    adapters became their own packages. A manifest is built by the adapter, so quackd cannot
    describe a robot whose package is absent: asking it to would raise `AdapterNotInstalled`
    from inside a verdict, which is a normal outcome of a run rather than a place to fail.
    A machine that has one robot can only speak for that one."""
    rows = []
    for name in adapter_names():
        if not is_installed(name):
            continue
        with contextlib.suppress(AdapterError, ImportError):
            rows.append((name, describe(RobotSpec(name, info(name).backends[0]))))
    return rows


def bodies_that_could(needs: Mapping[str, Any]) -> list[tuple[str, RobotManifest, list[str]]]:
    """The shipped bodies whose own datasheets meet these needs, and what each other one lacks.

    Rows are (name, manifest, missing): an empty `missing` is a body that could be asked. Only
    those are returned, so a caller naming them is naming bodies, not hopes; the lacking ones
    come back in the same shape for a caller that wants to say why not."""
    from quackd.verdict import missing_needs

    rows = [(name, m, missing_needs(needs, m)) for name, m in installed_manifests()]
    return [row for row in rows if not row[2]]
