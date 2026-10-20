"""One interface for "a robot", whatever its body.

`RobotAdapter` is a superset of `DuckTransport`: everything 0.3 took a transport for
(the executor, the loop, the heartbeat, the MCP session, a flock member) accepts an
adapter unchanged. What an adapter adds is self-description: `connect()` returns a
`RobotManifest`, `preconditions()` names the checks its verbs need, `implementations()`
supplies the verbs only this robot has. `heartbeat()` stays the watchdog contract and
`health()` is the informational call (ADR-0017).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from PIL import Image

from quackd.adapters.manifest import Health, RobotManifest
from quackd.command import readable_url, redacted_url
from quackd.duckfile.schema import POLICY_VERBS
from quackd.transport.base import Ack, DuckState, Intent, TransportError
from quackd.verbs.registry import Precondition, Verb


class AdapterError(TransportError):
    """Subclass of `TransportError` so every existing `except TransportError` still fires."""


class AdapterNotInstalled(AdapterError):
    """The adapter package, or the SDK one of its backends needs, is absent here.

    Both causes read the same on purpose, because the fix is the same command: the extra
    buys the adapter distribution and the SDK its real backend wants. The one case where
    that command can succeed and leave the SDK missing anyway is a python_version marker,
    which is why both hardware pages answer it in their troubleshooting tables."""

    def __init__(self, adapter: str, extra: str) -> None:
        super().__init__(f"adapter {adapter!r} needs an extra: uv pip install '{extra}'")
        self.adapter = adapter
        self.extra = extra


# ── the rest pose ───────────────────────────────────────────────────────────────────────

RestHow = Literal["none", "already", "arrived", "stalled", "timeout", "refused"]


@dataclass(frozen=True)
class RestResult:
    """What `go_to_rest()` did, as a value rather than an exception.

    Every caller is a teardown or the first moment of a run, and a teardown that raised
    would cost the body the disconnect it was in the middle of.

    `clipped` and `note` have defaults so that a body with nothing to say about its pose
    builds this exactly as it always did, and two results that say the same thing still
    compare equal. Both are tuples and strings rather than dicts because the value is frozen
    and hashable."""

    how: RestHow
    reason: str
    clipped: tuple[tuple[str, float, float], ...] = ()
    """`(joint, recorded, reachable)` for each joint the recorded pose puts past where the
    body's own limits let it be driven, by more than a reached pose is allowed to miss by.
    Such a joint is parked at `reachable` and let go of there. Empty for every body whose
    pose is inside its travel, which is every body but an arm recorded folded past it."""
    note: str | None = None
    """One sentence for the person at the robot about the pose itself, when there is one to
    say: what `clipped` means and how to make the pose reachable. Written by the body, which
    knows its own calibration, and said once by whoever narrates the rest move. Never a
    failure: a result carrying it reached the reachable pose."""
    answered: bool = True
    """Whether the body answered the last thing the move asked it. False for a move that ended
    because the body stopped answering a read, because a call to it never came back (one that
    ran out of time, or left the bus wedged behind it, a read or a write alike), or because it
    could not be asked at all (a bus already wedged, a closed transport). That is the one miss
    where nothing it last said about its pose or its torque can be believed: the servo supply
    cut at the switch looks exactly like this, and so does a pulled cable in front of servos
    that are still holding. A caller about to tell a person that the body is holding itself up
    reads this first, because that is a thing only a body that answered can be said to be
    doing. True for a move the body answered and still missed, a write it refused after a read
    that came back included, and for every body that never fails this way, which is why it has
    a default."""

    @property
    def reached(self) -> bool:
        """The body is at its rest pose now, whether or not it had to move."""
        return self.how in ("already", "arrived")

    @property
    def recorded(self) -> bool:
        """There was a rest pose to go to at all."""
        return self.how != "none"

    @classmethod
    def none(cls, reason: str = "this body has no rest pose") -> RestResult:
        return cls("none", reason)


HandHow = Literal["released", "held", "refused"]


@dataclass(frozen=True)
class HandResult:
    """What `let_go()` or `take_hold()` did, as a value rather than an exception.

    Both are asked for by a person standing at the robot, and both can fail for reasons that
    are not bugs: an arm away from the pose it may be released at, a register read that came
    back corrupt, a joint that moved while the hand was still on it. The caller has to be able
    to say which of those happened and then put the body down safely either way."""

    how: HandHow
    reason: str
    joints: dict[str, float] = field(default_factory=dict)
    """Where the arm was when this finished. Empty for a refusal that never read it."""
    torque_on: tuple[str, ...] | None = None
    """The joints whose torque register read on straight after a release, in the bus's order:
    empty when every one read off, and None when nothing was read back at all, which is every
    result but a release that got as far as the read. A take-hold refused with some motors read
    on (`energised` True) carries those motors here too, so the line said to the person holding
    the arm can name which joints hold, rather than say it could not tell.

    A person holding an arm is owed which of those three it was, and `how` cannot say it: a
    release whose read-back failed is still `released`, on purpose (`let_go`), and a release
    that some motors ignored is limp in part and energised in part. `quackd robot release`
    prints "torque reads off" only for the empty tuple."""
    energised: bool | None = None
    """Whether a take-hold may have left the arm with torque on, as far as anything read:
    False when it switched nothing on, because it refused before its torque write went out or
    a read of the torque register afterwards said off; True when such a read said on, on every
    motor or on some; None when the torque write went out and nothing read back what it did,
    and for every result that is not a take-hold's.

    A person holding an arm whose take-hold was refused acts on which of those it was, and
    `how` and `reason` cannot say it. A refusal that wrote nothing leaves the arm exactly as the
    release did, limp in their hands, and they can be told so. One refused after the torque
    write (a register that did not answer, a call that raised, a motor that stayed off) may be
    energised, all of it or part of it, and "quackd did not take hold, torque is off" said of it
    is a claim nothing read, made to somebody with a hand on an arm that may move.

    It speaks for every take-hold since the release, not only this one. A take-hold an
    interrupt landed on after its torque write went out records no refusal of its own, and the
    next one, refused before its own write, has not switched anything on itself while the arm it
    refused may still be energised by the last. So a refusal before its own write is False only
    where no take-hold's torque write has gone out since the release, or a read since the last
    one found every motor off."""
    outside: tuple[str, ...] = ()
    """The body joints a take-hold refused over because each read outside its calibrated
    travel, in the arm's order: empty for every other result.

    The refusal's `reason` names each of them with its reading and its travel, printed so the
    reading is never inside the travel the same sentence gives. A log line that lists the
    joints in whole degrees after it would round a joint a hair past its travel onto the edge
    of it, inside the travel the reason just put it outside, so the line leaves its own list
    out where this is set, and only there: a refusal whose reason carries no readings, such as
    an arm that moved as torque came on, keeps the list."""
    resting: bool = False
    """A take-hold refused over joints outside their travel (`outside`) whose own read found
    the whole arm at its rest pose, by the rule the rest move uses, with every motor off.

    That is a person who pressed Enter without lifting the arm out of a fold recorded past its
    travel. The arm is lying in its fold with no torque, as the placing release left it, and
    telling them it is in their hands and to keep hold of it tells them it is up and needs
    holding, which no read said. They are told it is still limp at its rest pose instead, and
    which joints to lift inside their travel for quackd to take hold."""

    @property
    def ok(self) -> bool:
        return self.how in ("released", "held")


async def let_go_if_any(transport: Any, **kw: Any) -> HandResult:
    """Release the body into a person's hands, on anything that can be handed over.

    Duck-typed like `go_to_rest_if_any`, and for the same reason: one body out of eight does
    this, and the other seven should not have to carry a method to say so.

    `kw` goes to the body's own `let_go` untouched, and the one keyword the arm takes is
    `anywhere`: True skips the rule that it is released only at its rest pose, for the two
    callers a person drives from a terminal, `quackd robot release` and the offer at the end
    of a run whose rest move missed. Nothing passes it without a person at the arm. The
    keywords are passed only when given, so a body whose `let_go` takes none is called exactly
    as it always was."""
    hand = getattr(transport, "let_go", None)
    if not callable(hand):
        return HandResult("refused", "this body is not handed to a person")
    try:
        return await hand(**kw)
    except Exception as e:
        return HandResult("refused", f"{type(e).__name__}: {e}")


async def take_hold_if_any(transport: Any) -> HandResult:
    """Hold whatever pose the body is in now, so the person can let go of it.

    Never raises, because the caller of this is either starting a run or tearing one down,
    and in both cases what it does next depends on the answer rather than on an exception."""
    hold = getattr(transport, "take_hold", None)
    if not callable(hold):
        return HandResult("refused", "this body is not handed to a person")
    try:
        return await hold()
    except Exception as e:
        return HandResult("refused", f"{type(e).__name__}: {e}")


CONNECTING_TAKES_TORQUE_OFF = "connecting takes torque off every motor for a moment"
"""What connecting does to a body that is handed to a person (`supports_hand_off`), in the
words every line that warns about it uses: the close note of an arm left holding itself up,
`quackd robot release` before it connects, and `quackd doctor` before it connects such a
body. Kept as one copy, because three copies that drifted apart would read to somebody holding
an arm as three different facts. The LeRobot arm, the one such body, configures its motors
inside `torque_disabled()` on every connect, so an arm held up by torque alone is limp for that
moment whatever else is true of it."""


async def go_to_rest_if_any(transport: Any) -> RestResult:
    """The rest move on anything: an adapter, a bare transport, a logged wrapper.

    A transport with no `go_to_rest` is a body quackd does not park, which is most of
    them, so this costs a `getattr` rather than a method on every mock in the suite."""
    move = getattr(transport, "go_to_rest", None)
    if not callable(move):
        return RestResult.none()
    try:
        return await move()
    except Exception as e:  # a teardown never raises: the caller still has to disconnect
        return RestResult("refused", f"{type(e).__name__}: {e}")


def refuse_rest_pose(adapter: str, rest_pose: Any) -> None:
    """Refuse a rest pose a body cannot hold, rather than accepting and ignoring it.

    The only way one reaches an adapter that does not park is a hand-edited
    `robots.json`, and the registry's rule is that a file which says something untrue
    names itself rather than being quietly dropped."""
    if rest_pose is not None:
        raise AdapterError(
            f"{adapter} does not return to a rest pose, so one cannot be kept for it: "
            "quackd robot edit NAME --clear rest-pose"
        )


# ── cameras ─────────────────────────────────────────────────────────────────────────────

MULTI_CAMERA_SPECS = ("lerobot:real", "lerobot:mujoco")
"""The bodies that read more than one `--camera-url`. Every other `make()` refuses a
second one rather than opening the first and silently dropping the rest. The arm's simulator
is here because it renders one view per named url, so a task that runs on the arm's cameras
rehearses on the same flags."""


def who_takes_several() -> str:
    """The end of the refusal a one-camera body gives a second url: which bodies take several,
    as a clause that reads as English whether the list holds one name or more."""
    specs = list(MULTI_CAMERA_SPECS)
    if len(specs) == 1:
        return f"only {specs[0]} takes several"
    return f"only {', '.join(specs[:-1])} and {specs[-1]} take several"


def camera_urls(value: str | Sequence[str] | None) -> tuple[str, ...]:
    """`--camera-url` however it arrived: nothing, one url, or several.

    Order is kept, because the first is the primary: the camera the detections describe,
    the one `--fov-deg` measures, and the only one a verb that steers by sight reads."""
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    return tuple(url.strip() for url in value if url and url.strip())


def one_camera_url(value: str | Sequence[str] | None, *, spec: str) -> str | None:
    """The single url a one-camera body takes, or a refusal that says who takes several."""
    urls = camera_urls(value)
    if len(urls) > 1:
        raise AdapterError(
            f"{spec} takes one --camera-url and {len(urls)} were given; {who_takes_several()}"
        )
    return urls[0] if urls else None


# ── a policy server ─────────────────────────────────────────────────────────────────────

POLICY_SPECS = ("lerobot:real", "lerobot:mujoco")
"""The bodies that hand a segment to a policy served by `quackd policy serve`. Named here for
the refusal every other body gives `--policy-url` (`quackd.adapters.factory`), as
`MULTI_CAMERA_SPECS` is for a second camera: an adapter whose `make()` takes no `policy` is
refused rather than handed one it would drop."""


class PolicyChoice:
    """The policy server a run's arm hands its segments to: `--policy-url`, and `--policy-token`
    when one was typed.

    The address comes from the flag alone, never from the environment or the registry, because
    one line in a `.env` would otherwise put a policy in charge of every run. The token may come
    from `QUACKD_POLICY_TOKEN` or the file the server writes, which the arm's client reads when
    none was typed. `url` is held redacted from the moment it is given, as the client holds it
    (`quackd.command.redacted_url`), so every header, record and refusal that names the server
    says what argv redaction would have said. An address redaction cannot read is refused
    without being quoted, since it would otherwise be held as typed. The address as typed and
    the token go only to the adapter that builds the client (`reach`).

    `accept_other_frame` is `--accept-other-frame`: the arm connects over a policy that learned
    from an arm calibrated another way, whose state percentiles lie outside this arm's travel,
    which the connect otherwise refuses. Its goals are clipped to this arm's travel either way,
    so it changes what drives the arm and never where the arm may go, and the record says so."""

    def __init__(
        self, url: str, token: str | None = None, *, accept_other_frame: bool = False
    ) -> None:
        given = url.strip()
        if not given:
            raise ValueError(
                "--policy-url is empty: give the policy server as http://127.0.0.1:PORT"
            )
        if not readable_url(given):
            # unquoted: redaction reads only a URL with a scheme and a host, and this one may
            # hold a password typed before a host it could not find
            raise ValueError(
                "--policy-url is not a URL: give the policy server as http://127.0.0.1:PORT"
            )
        self._given = given
        self._token = token.strip() if token is not None and token.strip() else None
        self.url = redacted_url(given)
        self.accept_other_frame = accept_other_frame

    def reach(self) -> tuple[str, str | None]:
        """The address as it was typed and the token, or None where none was typed, for the one
        caller that sends anything to the server: the arm's policy client."""
        return self._given, self._token

    def __repr__(self) -> str:
        return f"PolicyChoice({self.url!r})"


def policy_choice(
    url: str | None, token: str | None = None, *, accept_other_frame: bool = False
) -> PolicyChoice | None:
    """`--policy-url`, `--policy-token` and `--accept-other-frame` as a command takes them, or
    None when none was given. A token or an override with no address is refused rather than
    dropped: neither has a server to go to."""
    if url is None:
        if token is not None and token.strip():
            raise ValueError(
                "--policy-token goes with --policy-url: give the server's address as well, or "
                "drop the token"
            )
        if accept_other_frame:
            raise ValueError(
                "--accept-other-frame goes with --policy-url: it lets the policy that server "
                "serves drive an arm calibrated another way, so give the server's address as "
                "well, or drop the flag"
            )
        return None
    return PolicyChoice(url, token, accept_other_frame=accept_other_frame)


def policy_hint(verbs: Sequence[str], specs: Sequence[str], command: str) -> str | None:
    """What to do about a task refused for allowing `pick` or `manipulate` on an arm with no
    policy server, or None for any other refusal. `verbs` are the verbs refused, `specs` the
    bodies as `adapter:backend`, and `command` the one that takes `--policy-url`. Said because
    the flag is the only way that arm gets either verb, and a refusal that names the verb alone
    sends the person to `quackd list-verbs`, which never lists them."""
    wanted = [verb for verb in POLICY_VERBS if verb in verbs]
    arm = next((spec for spec in specs if spec in POLICY_SPECS), None)
    if not wanted or arm is None:
        return None
    come = "comes" if len(wanted) == 1 else "come"
    return (
        f"{' and '.join(wanted)} on {arm} {come} from a policy server: start one with quackd "
        f"policy serve, and give {command} its address with --policy-url"
    )


@runtime_checkable
class RobotAdapter(Protocol):
    name: str
    """Adapter name: microduck, lerobot, ..."""
    backend: str
    """Backend name: sim2d, mock, jsonrpc, real, ..."""
    manifest: RobotManifest | None
    """None until connect()."""

    async def connect(self) -> RobotManifest: ...

    async def disconnect(self) -> None: ...

    async def close(self) -> None:
        """Same as `disconnect()`; kept so an adapter satisfies `DuckTransport`."""
        ...

    async def get_state(self) -> DuckState: ...

    async def get_frame(self) -> Image.Image | None:
        """The primary camera's newest picture, or None. A body with several cameras also
        offers `get_frames()`; `transport.frames_of()` is how callers ask for all of them."""
        ...

    async def send_intent(self, intent: Intent) -> Ack: ...

    async def health(self) -> Health:
        """Informational: doctor, robot_list, discovery. Never raises for a sick robot."""
        ...

    async def heartbeat(self) -> None:
        """The watchdog contract, unchanged: raise `HeartbeatError` to stop and abort."""
        ...

    async def stop(self) -> None: ...

    async def go_to_rest(self) -> RestResult:
        """Drive the body to the rest pose it was built with, if it has one.

        Never raises: this runs in teardowns, where the caller still has to disconnect.
        A body quackd does not park answers `RestResult.none()`."""
        ...

    # `let_go` and `take_hold` are deliberately NOT here. This protocol is runtime-checkable
    # and structural, so a method on it is a method every one of the eight bodies must carry,
    # and seven of them are never handed to a person. A body that can be declares
    # `supports_hand_off = True`; the callers reach it through `let_go_if_any` and
    # `take_hold_if_any`, which is the same way a rest move reaches a bare transport.

    def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]: ...

    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...

    def preconditions(self) -> dict[str, Precondition]:
        """Condition name -> predicate over `DuckState` (a reason string, or None)."""
        ...

    def implementations(self) -> dict[str, Verb]:
        """Extension verbs and core overrides, keyed by canonical name."""
        ...


def backend_name(transport: Any) -> str:
    """The backend name: `sim2d` for a bare `Sim2DTransport` and for an adapter over one.

    The prompt's simulator note, the CLI's detector and recorder gating and the pinned
    `transport` keys in transcripts all key on this string. The note and the recorder ask more
    than the name, because the Microduck and the LeRobot arm both have a `mujoco`."""
    backend = getattr(transport, "backend", None)
    return str(backend) if backend else str(getattr(transport, "name", "unknown"))


def adapter_name(transport: Any) -> str | None:
    """The adapter name, or None for a bare transport."""
    if getattr(transport, "backend", None):
        return str(getattr(transport, "name", None))
    return None
