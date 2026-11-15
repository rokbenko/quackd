"""Faults the simulated bus can be told to have, seeded, so a rehearsal meets the same ones again.

The faults the lab bench found were the bus's rather than the arm's: a status packet lost in
the middle of a connect, a servo that did not answer its ping, a register read that never came
back. The simulated follower raises each of them where the real backend meets it on an arm,
from a function named as upstream's and in upstream's own words, so the backend's code sorts
it as it would the arm's (`real.may_have_written_torque`, `real.motor_in_error`). This module
decides when, and `follower.py` raises them.

A plan is a spec of rates and a seed. Each kind of fault counts its own calls, and the call at
ordinal `n` of kind `k` fails when a draw keyed on `(seed, k, n)` falls under `k`'s rate. So a
fault lands on the same call of its kind whatever else happened around it: never on a count
shared across kinds, which a heartbeat or a retry of something else would shift, and never on
the physics' step, which the clock decides. One more draw from the same key says which motor a
fault that needs one lands on.

The heartbeat is exempt from every fault a rate draws. It reads the arm on its own clock, the
wall's, so how many times it has read by any given call is chance, and a plan that counted it
would put a fault on a different call every run. Its reads take no ordinal and draw no fault.
The one fault it is not exempt from is the arm dropping off the bus (`READ_LOSS`): a pulled
cable fails every read at the lab, the heartbeat's included, and a heartbeat that still heard
the arm would keep a run going that the bench would have stopped. So which observation starts
the loss is seeded, and which caller meets it first, the heartbeat or a verb, depends on
timing. The follower cannot tell its callers apart, so whoever makes the heartbeat's calls marks
them (`Faults.heartbeat`), in the thread that makes them: a context variable would not reach
the worker thread a call runs in.

Nothing here imports anything heavy: a plan is parsed and checked where the run is built,
before the simulator is.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from roboglot.adapters.base import AdapterError
from roboglot_lerobot.sim.model import LABEL

HANDSHAKE = "handshake"
CONFIGURE = "configure"
WRITE = "write"
TORQUE = "torque"
TORQUE_READ = "torque_read"
TEMPERATURE_READ = "temperature_read"
RATES: Mapping[str, str] = MappingProxyType(
    {
        HANDSHAKE: "a connect's handshake finds one motor missing",
        CONFIGURE: "a connect's configure() gets no reply to one motor's Lock write",
        WRITE: "a send_action's goal write never goes out",
        TORQUE: "a torque write, a release's, a take-hold's or a disconnect's, gets no reply "
        "from one motor",
        TORQUE_READ: "the torque register's read gets no reply",
        TEMPERATURE_READ: "the temperature register's read gets no reply",
    }
)
"""Each kind of fault a rate can be given for, and what one does."""
READ_LOSS = "read_loss_from"
"""The one ordinal a spec can give: the observation from which the arm has dropped off the bus.
No ping, read or torque write made from then on gets a reply, and no goal sent reaches a motor,
so the motors go on holding what they were last told."""
OBSERVATION = "observation"
"""What `READ_LOSS` counts: every read of the positions made for an observation."""
EXAMPLE = "handshake=0.2,configure=0.3,read_loss_from=40"


@dataclass(frozen=True)
class Fault:
    """One fault that was drawn: its kind, the call of that kind it landed on, and a share in
    [0, 1) of its own that says which motor it lands on, where it lands on one."""

    kind: str
    ordinal: int
    share: float

    def motor(self, motors: Sequence[str]) -> str:
        """The motor this fault lands on, out of the bus's table in its own order."""
        return motors[min(int(self.share * len(motors)), len(motors) - 1)]


def _draws(seed: int, kind: str, ordinal: int) -> tuple[float, float]:
    """Two numbers in [0, 1) that depend on nothing but the key: the same on every machine and
    in every process, which Python's own `hash` of a string is not."""
    digest = hashlib.blake2b(f"{seed}/{kind}/{ordinal}".encode(), digest_size=16).digest()
    scale = float(2**64)
    return int.from_bytes(digest[:8], "big") / scale, int.from_bytes(digest[8:], "big") / scale


@dataclass(frozen=True)
class FaultPlan:
    """Which faults the bus has and how often, and the seed that says on which calls.

    Parsed from a spec (`parse`) and never changed: the calls counted so far are each
    follower's own (`Faults`), so two followers given one plan meet the same faults on the same
    calls."""

    seed: int
    rates: Mapping[str, float] = field(default_factory=dict)
    read_loss_from: int | None = None

    @classmethod
    def parse(cls, spec: str, *, seed: int) -> FaultPlan:
        """A plan from `name=value` pairs separated by commas: a rate between 0 and 1 for any
        kind in `RATES`, and `read_loss_from=N` for the arm to stop answering at its Nth
        observation. An empty spec is a plan with no faults. Anything else is refused with what
        was wrong and the grammar, rather than read as the nearest thing it might have meant."""
        rates: dict[str, float] = {}
        loss: int | None = None
        seen: set[str] = set()
        entries = [e.strip() for e in spec.split(",")] if spec.strip() else []
        for entry in entries:
            if not entry:
                raise _refusal(spec, "has an empty entry between two commas")
            name, sep, value = (part.strip() for part in entry.partition("="))
            if not sep or not name or not value:
                raise _refusal(spec, f"has {entry!r}, which is not name=value")
            if name in seen:
                raise _refusal(spec, f"gives {name} twice")
            seen.add(name)
            if name == READ_LOSS:
                if not (value.isascii() and value.isdigit()) or int(value) < 1:
                    raise _refusal(spec, f"gives {READ_LOSS} {value!r}, not a whole number from 1")
                loss = int(value)
            elif name in RATES:
                try:
                    rate = float(value)
                except ValueError:
                    rate = math.nan
                if not 0.0 <= rate <= 1.0:
                    raise _refusal(spec, f"gives {name} {value!r}, not a rate from 0 to 1")
                rates[name] = rate
            else:
                raise _refusal(spec, f"names {name!r}, which is not a fault the simulator has")
        return cls(seed=int(seed), rates=MappingProxyType(rates), read_loss_from=loss)

    @property
    def spec(self) -> str:
        """The plan as a spec again, each kind in `RATES` order, for a record to show. Each rate
        is written as `repr` writes it, the shortest text that reads back as the same float, so
        the plan a record shows is the plan that ran and meets the same faults."""
        said = [f"{kind}={self.rates[kind]!r}" for kind in RATES if kind in self.rates]
        if self.read_loss_from is not None:
            said.append(f"{READ_LOSS}={self.read_loss_from}")
        return ",".join(said)

    def fires(self, kind: str, ordinal: int) -> bool:
        """Whether the call at `ordinal` among `kind`'s calls fails."""
        return _draws(self.seed, kind, ordinal)[0] < self.rates.get(kind, 0.0)

    def share(self, kind: str, ordinal: int) -> float:
        """The draw that says which motor that call's fault lands on."""
        return _draws(self.seed, kind, ordinal)[1]


def _refusal(spec: str, why: str) -> AdapterError:
    kinds = ", ".join(RATES)
    return AdapterError(
        f"{LABEL} the fault spec {spec!r} {why}. A fault spec is name=value pairs separated by "
        f"commas: a rate from 0 to 1 for any of {kinds}, and {READ_LOSS}=N for the arm to stop "
        f"answering at its Nth observation, such as {EXAMPLE}."
    )


class Faults:
    """One follower's draws from a plan, or from none: how many calls of each kind it has
    made, and the faults that landed. Thread-safe, because the follower's calls run in worker
    threads."""

    def __init__(self, plan: FaultPlan | None = None) -> None:
        self.plan = plan
        self.injected: list[Fault] = []
        """Every fault that landed, in order, for a record to say what the rehearsal met."""
        self._counts: dict[str, int] = {}
        self._lost = False
        self._lock = threading.Lock()
        self._local = threading.local()

    @contextlib.contextmanager
    def heartbeat(self) -> Iterator[None]:
        """Every call made inside this, in this thread, is the heartbeat's: exempt from the
        faults a rate draws, and not from the arm dropping off the bus."""
        depth = getattr(self._local, "depth", 0)
        self._local.depth = depth + 1
        try:
            yield
        finally:
            self._local.depth = depth

    @property
    def exempt(self) -> bool:
        """Whether the call being made in this thread is the heartbeat's."""
        return bool(getattr(self._local, "depth", 0))

    def _count(self, kind: str) -> int:
        with self._lock:
            ordinal = self._counts.get(kind, 0) + 1
            self._counts[kind] = ordinal
            return ordinal

    def next(self, kind: str) -> Fault | None:
        """Count one call of `kind` and say whether it fails, or None for a heartbeat's call or
        one with no plan, which is neither counted nor failed."""
        if self.plan is None or self.exempt:
            return None
        ordinal = self._count(kind)
        if not self.plan.fires(kind, ordinal):
            return None
        fault = Fault(kind, ordinal, self.plan.share(kind, ordinal))
        with self._lock:
            self.injected.append(fault)
        return fault

    def observed(self) -> None:
        """Count one observation's read of the positions, and lose every read from the one the
        plan names on (`READ_LOSS`)."""
        if self.plan is None or self.exempt or self.plan.read_loss_from is None:
            return
        ordinal = self._count(OBSERVATION)
        with self._lock:
            if not self._lost and ordinal >= self.plan.read_loss_from:
                self._lost = True
                self.injected.append(Fault(READ_LOSS, ordinal, 0.0))

    @property
    def reads_lost(self) -> bool:
        """Whether a call made now gets no reply: the arm has dropped off the bus
        (`READ_LOSS`). The heartbeat's calls too, which never start the loss but meet it."""
        return self._lost
