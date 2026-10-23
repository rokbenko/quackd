"""`quackd preflight`: task files rehearsed on the arm's simulator before they meet the arm.

A task file nobody has run is a guess, and the arm is where a wrong guess costs most: a bus that
loses a packet in the middle of a connect, a rest pose past the calibrated travel, a pilot that
reaches for a verb the file never allowed. `lerobot:mujoco` runs the real backend's own code
over a physics model, so a file rehearsed on it runs through the lines that will drive the arm.
This is that rehearsal, done the same way every time.

For each file, in this order:

1. The task is checked against the robot's static manifest, as `quackd validate` checks it, and
   its sidecar is read (below).
2. The simulator is connected and closed `DEFAULT_CONNECT_CYCLES` times, cycle k on seed k with
   the `--faults` plan, if one was given, drawing its bus faults. An error that escapes any of
   them fails the file, and no run is made on a robot that cannot be connected. Under a plan, a
   connect that retried and then gave up in words is what the arm's connect does when a bus
   drops too much, and it is noted rather than failed: the runs after it connect with no
   faults, so a robot that cannot be connected at all still fails every one of them.
3. The task is run once per seed through the agent loop, memory off, in a loop of this module's
   own rather than `quackd run`'s, because what a run is judged by is on the transport after the
   run has ended and the command does not hand its transport back.

A run passes when nothing escaped it, no call to the simulated bus was left hanging, its close
ended at the rest pose, found none to return to on a robot without one, or had its rest move
refused where the sidecar says to expect a refusal (`judge_close`), and every check in the
sidecar holds. A rest move that stalled or ran out of time fails the run whatever the sidecar
says, and the close is named for which it was. The pilot's own verdict is reported and is not
one of those: a model that says it succeeded is the thing being rehearsed, not the judge of it.

The sidecar is `<task>.sim.yaml` beside the task file, and never the file's frontmatter, because
`robot_load_duckfile` hands an MCP pilot the whole frontmatter, and a pilot that can read what it
will be marked on is rehearsing the marking. It says what to lay on the table and what has to be
so afterwards:

    scene:
      objects:
        - {name: block, kind: box, size: [0.0125, 0.0125, 0.0125], place: jaws}
    checks:
      - at_rest: true
      - joint_moved: {joint: shoulder_lift, min_deg: 10}
      - lifted: {object: block, min_m: 0.02, when: peak}
      - moved: {object: block, min_m: 0.05, when: latched}

Every threshold is the task's own, and is measured from the run's start: a joint from its first
observed reading, in the degrees its calibration gives, and an object from where it was laid.
Joint checks read the transcript, which is what the pilot was shown. Object checks read the
world's truth as the simulator latched it on the way into the teardown's stop, before the rest
move carried the arm back through the scene (`latched`), or the most the object reached before
then (`peak`). Never the live world, which the rest move has been through by the time anything
reads it, and never the state's extras, which reach the pilot and MCP.

Refused before anything is built: a robot that is not a simulator, which `quackd robot twin`
makes of a registered arm, and a pilot nobody named, because the scripted pilot rehearses
nothing a model would do and so runs only when `--llm fake` is typed.

Nothing here imports the simulator. The robot is built through the factory, and what a run is
judged by is read off the transport it built, by name.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:
    from quackd.adapters.factory import RobotSpec
    from quackd.adapters.manifest import RobotManifest
    from quackd.agent.providers.base import LLMProvider, NamedPng
    from quackd.duckfile.schema import DuckFile

SIDECAR_SUFFIX = ".sim.yaml"
DEFAULT_SEEDS = 3
"""Runs per file when `--seeds` is not given. One seed rehearses one table and one draw of the
pilot's choices, so a single pass says little; three is enough to see a task that passes only
sometimes, at three times one run's cost with a paid model."""
DEFAULT_CONNECT_CYCLES = 3
"""Connects and closes per file before the first run, when `--connect-cycles` is not given. A
fault plan draws a different set of bus faults on each cycle's seed, so three meet a plan's
connect faults on more than one draw, and the close after each is the arm let go of as often as
it was taken hold of."""
LATCH = "stop"
"""The label the simulator latches its truth under on the way into a stop. The teardown opens
with a stop, and the last latch under it is that one, taken before the rest move after it moved
anything: the scene as the run left it."""
FIRST_SEED = 0
"""The seed the first connect cycle and the first run are made on: cycle k and run k are each on
this plus k, so a file rehearsed twice meets the same tables and the same bus faults."""
SIZES = {"box": 3, "capsule": 2}
"""How many numbers MuJoCo's size takes for each shape: a box's three half sizes, a capsule's
radius and half length, in metres."""


class PreflightError(Exception):
    """A refusal before anything is built. The CLI prints it as one line."""


class SidecarError(PreflightError):
    """A sidecar that cannot be read, named by its path."""


# ── the sidecar ─────────────────────────────────────────────────────────────────────────


class SceneObject(BaseModel):
    """One object on the simulated table. `size` is MuJoCo's for the shape (`SIZES`); `place`
    is `table`, a spot the seed picks, or `jaws`, on the table between the gripper's fingers as
    the arm starts. With no `mass_kg` it weighs what MuJoCo gives a solid of its size, and with
    no `rgba` it is a colour the colour detector ignores."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    kind: Literal["box", "capsule"]
    size: list[float]
    place: Literal["table", "jaws"] = "table"
    mass_kg: float | None = Field(default=None, gt=0)
    rgba: list[float] | None = None

    @model_validator(mode="after")
    def _shape(self) -> SceneObject:
        want = SIZES[self.kind]
        if len(self.size) != want or not all(math.isfinite(v) and v > 0 for v in self.size):
            what = "three half sizes" if self.kind == "box" else "its radius and half length"
            raise ValueError(
                f"size: a {self.kind} is {want} positive numbers in metres, {what}, not {self.size}"
            )
        if self.rgba is not None and (
            len(self.rgba) != 4 or not all(0.0 <= v <= 1.0 for v in self.rgba)
        ):
            raise ValueError(f"rgba: a colour is four numbers from 0 to 1, not {self.rgba}")
        return self


class Scene(BaseModel):
    """What the simulator lays on its table, in place of its own cube and pen."""

    model_config = ConfigDict(extra="forbid")

    objects: list[SceneObject] = Field(min_length=1)

    @model_validator(mode="after")
    def _names(self) -> Scene:
        names = [obj.name for obj in self.objects]
        if len(set(names)) != len(names):
            raise ValueError(f"objects: every object needs a name of its own, not {names}")
        jaws = [obj.name for obj in self.objects if obj.place == "jaws"]
        if len(jaws) > 1:
            raise ValueError(f"objects: {' and '.join(jaws)} are both between the jaws")
        return self


class JointMoved(BaseModel):
    """The joint read at least `min_deg` away from its first reading, at some turn."""

    model_config = ConfigDict(extra="forbid")

    joint: str
    min_deg: float = Field(gt=0)


class ObjectCheck(BaseModel):
    """An object's truth, `latched` as the run ended or at its `peak` before then."""

    model_config = ConfigDict(extra="forbid")

    object: str
    min_m: float = Field(gt=0)
    when: Literal["latched", "peak"]


class Check(BaseModel):
    """One thing that has to be so, and exactly one of these keys says which."""

    model_config = ConfigDict(extra="forbid")

    at_rest: bool | None = None
    """True: the close has to end at the rest pose. False: the task leaves the arm where its
    rest move is refused, so a rest move that is made has to be refused; one that stalled or
    ran out of time still fails, and a robot with no rest pose makes none and passes. Without
    it, a close that reached the rest pose passes, and so does one with no rest pose to
    reach."""
    joint_moved: JointMoved | None = None
    lifted: ObjectCheck | None = None
    """Off the table, touching the gripper and up at least `min_m` from where it was laid."""
    moved: ObjectCheck | None = None
    """Its centre at least `min_m` from where it was laid."""

    @model_validator(mode="after")
    def _one(self) -> Check:
        given = [
            k for k in ("at_rest", "joint_moved", "lifted", "moved") if getattr(self, k) is not None
        ]
        if len(given) != 1:
            raise ValueError(
                "a check is one of at_rest, joint_moved, lifted or moved, and this one is "
                + (" and ".join(given) or "none of them")
            )
        return self


class Sidecar(BaseModel):
    """`<task>.sim.yaml`: the table to lay out, if not the simulator's own, and the checks a run
    is judged by besides its close."""

    model_config = ConfigDict(extra="forbid")

    scene: Scene | None = None
    checks: list[Check] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Sidecar:
        if sum(c.at_rest is not None for c in self.checks) > 1:
            raise ValueError("checks: at_rest is said once")
        if self.scene is not None:
            laid = [obj.name for obj in self.scene.objects]
            for c in self.checks:
                target = c.lifted or c.moved
                if target is not None and target.object not in laid:
                    raise ValueError(
                        f"checks: {target.object} is not in the scene, which lays out "
                        f"{', '.join(laid)}"
                    )
        return self

    @property
    def at_rest(self) -> bool | None:
        return next((c.at_rest for c in self.checks if c.at_rest is not None), None)

    def scene_items(self) -> list[dict[str, Any]] | None:
        """The scene as the simulator's `make()` takes it, or None for its own table."""
        if self.scene is None:
            return None
        return [obj.model_dump(exclude_none=True) for obj in self.scene.objects]


def sidecar_path(duck_path: str | Path) -> Path:
    """`circle.duck` -> `circle.sim.yaml`, beside it."""
    path = Path(duck_path)
    return path.with_name(path.stem + SIDECAR_SUFFIX)


def load_sidecar(duck_path: str | Path, *, joints: Sequence[str] = ()) -> Sidecar | None:
    """The task's sidecar, None where it has none, or a refusal naming the file and the field.

    `joints` is the robot's own list, from its static manifest: a check on a joint the arm does
    not have could never hold, and is refused here rather than failed on every seed."""
    path = sidecar_path(duck_path)
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise SidecarError(f"{path}: not a YAML file quackd can read ({_one_line(e)})") from e
    if not isinstance(raw, dict):
        raise SidecarError(f"{path}: a sidecar is a mapping with scene and checks")
    try:
        sidecar = Sidecar.model_validate(raw)
    except ValidationError as e:
        why = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'sidecar'}: "
            f"{str(err['msg']).removeprefix('Value error, ')}"
            for err in e.errors()
        )
        raise SidecarError(f"{path}: {why}") from e
    for c in sidecar.checks:
        if c.joint_moved is not None and joints and c.joint_moved.joint not in joints:
            raise SidecarError(
                f"{path}: joint_moved names {c.joint_moved.joint!r}, and this robot's joints "
                f"are {', '.join(joints)}"
            )
    return sidecar


def _one_line(e: Exception) -> str:
    return " ".join(str(e).split())


# ── judging a run ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    """One thing a run is judged by: what it was, whether it held, and what was measured."""

    check: str
    ok: bool
    detail: str

    def public(self) -> dict[str, Any]:
        return {"check": self.check, "ok": self.ok, "detail": self.detail}


MISSED = {
    "refused": "the rest move was refused",
    "stalled": "the rest move stalled",
    "timeout": "the rest move ran out of time",
}
"""What a rest move that did not reach the pose did, by its `RestResult.how`, in the words a
row of the table says it in."""


def judge_close(rest: Any, expect: bool | None) -> Verdict:
    """The teardown's rest move against what the sidecar expects of it (`Check.at_rest`).

    `rest` is the transport's last `RestResult`, or None where no rest move was made, which is
    an arm with no rest pose: its close lets go of it where the run left it, which on the arm's
    simulator is the model settling under its gravity, and which a run on a real arm with no
    rest pose is refused for (ADR-0058).

    Of a rest move that was made, only a refusal is what `at_rest: false` expects: the arm left
    where the rest move will not move it, such as in a person's hands. A move that set off and
    stalled, or ran out of time, is not that, and fails a run whatever the sidecar says. Each
    is named for what it was, where every miss used to read as refused. A robot with no rest
    pose makes no rest move, and the arm stays where the run left it, which fails only
    `at_rest: true`."""
    if rest is None or rest.how == "none":
        said = "no rest pose to return to"
        return Verdict("close", expect is not True, said)
    if rest.reached:
        return Verdict("close", expect is not False, "at the rest pose")
    what = MISSED.get(rest.how, f"the rest move ended {rest.how}")
    return Verdict("close", rest.how == "refused" and expect is False, f"{what}: {rest.reason}")


def joint_series(events: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
    """Every observation's joints, in the degrees the pilot was shown them, in order."""
    out: list[dict[str, float]] = []
    for event in events:
        if event.get("kind") != "observation":
            continue
        state = (event.get("features") or {}).get("state") or {}
        joints = (state.get("extras") or {}).get("joints") or {}
        out.append({str(k): float(v) for k, v in joints.items()})
    return out


def judge_joint(check: JointMoved, series: Sequence[Mapping[str, float]]) -> Verdict:
    name = f"joint_moved {check.joint}"
    readings = [s[check.joint] for s in series if check.joint in s]
    if not readings:
        return Verdict(name, False, f"no observation read {check.joint}")
    moved = max(abs(r - readings[0]) for r in readings)
    return Verdict(
        name,
        moved >= check.min_deg,
        f"moved {moved:.1f} deg from where it started, of {check.min_deg:g} asked",
    )


def judge_object(kind: str, check: ObjectCheck, truth: Any) -> Verdict:
    """`lifted` or `moved` against the world's latched truth (`world.Truth`), or None where the
    simulator latched nothing as the run ended."""
    name = f"{kind} {check.object} ({check.when})"
    if truth is None:
        return Verdict(name, False, "the simulator latched no truth as the run ended")
    if check.object not in truth.objects:
        laid = ", ".join(truth.objects) or "nothing"
        return Verdict(name, False, f"there is no {check.object} on the table, only {laid}")
    seen = truth.objects[check.object] if check.when == "latched" else truth.peaks[check.object]
    if kind == "lifted":
        lift = float(seen.lift_m) if seen.lifted else 0.0
        where = "as the run ended" if check.when == "latched" else "at its highest"
        detail = (
            f"lifted {lift:.3f} m {where}, of {check.min_m:g} m asked"
            if seen.lifted
            else f"not lifted {where}"
        )
        return Verdict(name, bool(seen.lifted) and lift >= check.min_m, detail)
    moved = float(seen.moved_m)
    where = "as the run ended" if check.when == "latched" else "at its furthest"
    return Verdict(
        name,
        moved >= check.min_m,
        f"{moved:.3f} m from where it was laid {where}, of {check.min_m:g} m asked",
    )


def judge(
    sidecar: Sidecar | None,
    *,
    rest: Any,
    events: Sequence[Mapping[str, Any]],
    truth: Any,
) -> list[Verdict]:
    """The close, then every check the sidecar makes, in the order it lists them."""
    verdicts = [judge_close(rest, sidecar.at_rest if sidecar is not None else None)]
    if sidecar is None:
        return verdicts
    series = joint_series(events)
    for c in sidecar.checks:
        if c.joint_moved is not None:
            verdicts.append(judge_joint(c.joint_moved, series))
        elif c.lifted is not None:
            verdicts.append(judge_object("lifted", c.lifted, truth))
        elif c.moved is not None:
            verdicts.append(judge_object("moved", c.moved, truth))
    return verdicts


# ── reports ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CycleReport:
    seed: int
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    gave_up: str | None = None
    """The connect's own refusal where it gave up on the fault plan's faults, which fails
    nothing: it is the arm's connect doing what it does on a bus that drops too much."""

    @property
    def ok(self) -> bool:
        return self.error is None

    def public(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "ok": self.ok,
            "error": self.error,
            "gave_up": self.gave_up,
            "notes": self.notes,
        }


@dataclass
class RunReport:
    seed: int
    outcome: str
    reason: str
    steps: int
    cost_usd: float | None
    run_dir: str | None
    verdicts: list[Verdict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    """What failed the run before any check was read: an error that escaped the loop, and a
    call to the simulated bus left hanging."""

    @property
    def ok(self) -> bool:
        return not self.errors and all(v.ok for v in self.verdicts)

    @property
    def failures(self) -> list[str]:
        return [*self.errors, *(f"{v.check}: {v.detail}" for v in self.verdicts if not v.ok)]

    def public(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "ok": self.ok,
            "outcome": self.outcome,
            "reason": self.reason,
            "steps": self.steps,
            "cost_usd": self.cost_usd,
            "run_dir": self.run_dir,
            "checks": [v.public() for v in self.verdicts],
            "errors": self.errors,
        }


@dataclass
class FileReport:
    file: str
    name: str | None = None
    problems: list[str] = field(default_factory=list)
    """Why the file was never run: it did not parse, does not fit the robot, or its sidecar
    cannot be read."""
    sidecar: str | None = None
    cycles: list[CycleReport] = field(default_factory=list)
    runs: list[RunReport] = field(default_factory=list)
    sim_dt_s: float | None = None

    @property
    def ok(self) -> bool:
        return (
            not self.problems
            and all(c.ok for c in self.cycles)
            and bool(self.runs)
            and all(r.ok for r in self.runs)
        )

    @property
    def cost_usd(self) -> float | None:
        """What the model cost over every run of this file, or None where a run was not
        priced: a sum with an unknown in it is not a number worth printing."""
        costs = [r.cost_usd for r in self.runs]
        if not costs or any(c is None for c in costs):
            return None
        return sum(c for c in costs if c is not None)

    def public(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "name": self.name,
            "ok": self.ok,
            "problems": self.problems,
            "sidecar": self.sidecar,
            "cycles": [c.public() for c in self.cycles],
            "runs": [r.public() for r in self.runs],
            "cost_usd": self.cost_usd,
            "sim_dt_s": self.sim_dt_s,
        }


# ── the rehearsal ───────────────────────────────────────────────────────────────────────


@dataclass
class Rehearsal:
    """One robot, one pilot and one set of flags, run over any number of task files."""

    spec: RobotSpec
    manifest: RobotManifest
    """The robot's static manifest: what a file is validated against, and whose joints a
    sidecar's joint checks may name."""
    pilot: Callable[[DuckFile], LLMProvider]
    """A fresh pilot for a task, one per run, so no run inherits another's conversation."""
    adapter_kwargs: Mapping[str, Any] = field(default_factory=dict)
    seeds: int = DEFAULT_SEEDS
    connect_cycles: int = DEFAULT_CONNECT_CYCLES
    faults: str | None = None
    max_steps: int | None = None
    runs_dir: str | Path = "runs"
    task_images: Sequence[NamedPng] = ()
    progress: Callable[[str], None] = lambda _message: None
    play: bool | None = None
    """`--play` or `--no-play` over every file's own `play`, as a run takes it. None is each
    file's own word. Last in the class because a caller may fill it by position."""

    async def file(self, path: str) -> FileReport:
        from quackd.duckfile.parser import DuckParseError, load_duck
        from quackd.duckfile.validate import validate_duck
        from quackd.verbs.registry import default_registry

        report = FileReport(file=path)
        try:
            duck = load_duck(path)
        except DuckParseError as e:
            report.problems.append(e.reason)
            return report
        report.name = duck.name
        report.problems.extend(
            str(p) for p in validate_duck(duck, [self.manifest], registry=default_registry())
        )
        try:
            sidecar = load_sidecar(
                duck.path or path, joints=tuple(self.manifest.extras.get("joints") or ())
            )
        except SidecarError as e:
            report.problems.append(str(e))
            return report
        if sidecar is not None:
            report.sidecar = str(sidecar_path(duck.path or path))
        if report.problems:
            return report
        scene = sidecar.scene_items() if sidecar is not None else None
        for k in range(self.connect_cycles):
            self.progress(f"{duck.name}: connect {k + 1} of {self.connect_cycles}")
            report.cycles.append(await self._cycle(FIRST_SEED + k, scene))
        if not all(c.ok for c in report.cycles):
            return report
        for seed in range(FIRST_SEED, FIRST_SEED + self.seeds):
            self.progress(f"{duck.name}: seed {seed}")
            run, dt = await self._run(duck, sidecar, seed)
            report.runs.append(run)
            report.sim_dt_s = dt if dt is not None else report.sim_dt_s
        return report

    def _build(self, seed: int, scene: list[dict[str, Any]] | None, faults: str | None) -> Any:
        from quackd.adapters.factory import make_adapter

        extra: dict[str, Any] = {}
        if faults is not None:
            extra["faults"] = faults
        if scene is not None:
            extra["scene"] = scene
        return make_adapter(self.spec, seed=seed, **self.adapter_kwargs, **extra)

    async def _cycle(self, seed: int, scene: list[dict[str, Any]] | None) -> CycleReport:
        from quackd.transport.base import TransportError

        report = CycleReport(seed)
        try:
            adapter = self._build(seed, scene, self.faults)
        except Exception as e:
            report.error = f"the robot could not be built: {type(e).__name__}: {e}"
            return report
        transport = getattr(adapter, "transport", adapter)
        try:
            await adapter.connect()
        except TransportError as e:
            # a refusal in words, which is how the connect gives up once its retries are spent;
            # without a plan nothing was injected, so a connect that gives up is the robot's own
            if self.faults is None:
                report.error = f"the connect raised {type(e).__name__}: {e}"
            else:
                report.gave_up = " ".join(str(e).split())
        except Exception as e:
            report.error = f"the connect raised {type(e).__name__}: {e}"
        else:
            report.notes = [str(n) for n in getattr(adapter, "connect_notes", ()) or ()]
            try:
                await adapter.close()
            except Exception as e:
                report.error = f"the close raised {type(e).__name__}: {e}"
        if report.error is None and getattr(transport, "wedged", False):
            report.error = (
                "a call to the simulated bus never came back, and the transport was left "
                "refusing every call after it"
            )
        return report

    async def _run(
        self, duck: DuckFile, sidecar: Sidecar | None, seed: int
    ) -> tuple[RunReport, float | None]:
        from quackd.agent.loop import AgentLoop, RunConfig
        from quackd.agent.transcript import Transcript
        from quackd.perception import detector_for
        from quackd.safety import allow_all

        scene = sidecar.scene_items() if sidecar is not None else None
        try:
            adapter = self._build(seed, scene, None)
        except Exception as e:
            error = f"the robot could not be built: {type(e).__name__}: {e}"
            return RunReport(seed, "error", error, 0, None, None, errors=[error]), None
        transport = getattr(adapter, "transport", adapter)
        cfg = RunConfig(
            duck=duck,
            provider=self.pilot(duck),
            transport=adapter,
            detector=detector_for(
                self.manifest.sensors,
                None,
                fov_deg=self.manifest.limits.get("camera_fov_deg"),
                backend=self.spec.backend,
            ),
            # nothing real moves, so a gated verb is let through as `--yes` lets it through,
            # and nobody is asked anything: there is nobody at a rehearsal to ask
            confirm=allow_all,
            runs_dir=self.runs_dir,
            run_name=f"preflight-seed-{seed}",
            max_steps=self.max_steps,
            task_images=self.task_images,
            memory=None,
            play=self.play,
        )
        loop = AgentLoop(cfg)
        errors: list[str] = []
        outcome, reason, steps = "error", "", 0
        try:
            result = await loop.run()
            outcome, reason, steps = result.outcome, result.reason, result.steps
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            errors.append(f"the run raised {reason}")
        finally:
            with contextlib.suppress(Exception):
                loop.transcript.close()
        if getattr(transport, "wedged", False):
            errors.append(
                "a call to the simulated bus never came back, and the transport was left "
                "refusing every call after it"
            )
        events: list[dict[str, Any]] = []
        with contextlib.suppress(OSError, ValueError):
            events = Transcript.read(loop.transcript.path, lenient=True)
        world = getattr(transport, "sim_world", None)
        truth = world.latched(LATCH) if world is not None else None
        verdicts = judge(
            sidecar, rest=getattr(transport, "last_rest", None), events=events, truth=truth
        )
        cost = (loop.summary or {}).get("cost_usd")
        report = RunReport(
            seed=seed,
            outcome=outcome,
            reason=reason,
            steps=steps,
            cost_usd=None if cost is None else float(cost),
            run_dir=str(loop.run_dir),
            verdicts=verdicts,
            errors=errors,
        )
        return report, getattr(transport, "sim_dt", None)


def refuse_real(spec: RobotSpec, label: str) -> None:
    """Refuse anything but a simulator, before anything is built. Raises `PreflightError`."""
    from quackd.adapters.factory import is_simulator

    if not is_simulator(spec):
        raise PreflightError(
            f"{label} is not a simulator, and preflight runs only on one, since it drives the "
            "robot through every task file seed after seed: quackd robot twin NAME registers a "
            "lerobot:mujoco simulator of a registered arm to rehearse on"
        )


def refuse_default_pilot(source: str) -> None:
    """Refuse a pilot nobody named: the fallback at the bottom of `resolve_llm`, which is the
    scripted one. Raises `PreflightError`."""
    if source == "default":
        raise PreflightError(
            "preflight rehearses a task with the pilot that will fly it, and none was named: "
            "give --llm VENDOR[:MODEL], register one with the robot or set QUACKD_LLM. The "
            "scripted pilot rehearses nothing a model would do, so it runs only when typed as "
            "--llm fake"
        )
