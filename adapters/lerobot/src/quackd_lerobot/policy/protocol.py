"""The policy server's wire protocol: five calls, their messages, and how a number is read.

A served policy runs in a process of its own, never in the one that owns the arm's serial bus
(`upstream_api.PROCESSOR_CLASS_IMPORT`: loading a checkpoint can run code it names). The arm's
process reaches it over HTTP with this protocol, the client in `client.py` and the server in
`server.py`, and this module is the one place either of them spells it. A change here is a
change to both.

- `GET /v1/policy` says what is being served (`PolicyInfo`): the policy and its revision, the
  features it wants, the rate it runs at and where that rate came from, its chunking, whether
  it is asked every tick, whether the server has a GPU, the quantiles of the state and the
  actions it learned from, the latency it declares, its threads, how it wants its frames, and
  every repository it loaded, at the revision it loaded it.
- `POST /v1/reset` starts a session (`ResetRequest`): the instruction, the arm's motor names
  in the bus's order, each camera's name, size and rotation, and the session it replaces. It
  answers the session's id, and the one it replaced is over. A reset from any other client is
  refused while the live session is still in use, so a second client never ends a segment an
  arm is driving through the first.
- `POST /v1/end` ends a session (`EndRequest`), which is how a client that is done lets
  another start one at once.
- `POST /v1/step` asks for a chunk (`StepRequest`): the session, a sequence number, the tick
  the observation was read at, the state in the reset's motor order, a frame from every
  camera, and the command the arm last sent. It answers a chunk of actions, one a tick from
  that tick, or null for a policy with nothing more to do, with the session and sequence
  echoed and how long inference took (`StepReply`).
- `GET /v1/health` says whether the server serves, `{"ready": true}`, or is stopping, a 503 and
  `{"ready": false}`, and nothing else. It is the one call that needs no token, because what
  asks it is a platform's readiness probe, which holds none, and it reads no body and touches
  no session. A server listens only once its policy is built, so one that answers is ready.
  It is a call added to version 1, and a client that never asks it sees nothing change.

It is not LeRobot's own async inference, which unpickles what it is sent over an insecure
port, needs torch in the client, and skips an observation near the last one
(`upstream_api.ASYNC_PICKLE`, `ASYNC_CLIENT_NEEDS_TORCH`, `ASYNC_SKIPS_SIMILAR`).

**Every number is checked, on both sides.** JSON is parsed with a `parse_constant` that
refuses `NaN` and `Infinity`, which Python's `json` reads by default, and a `parse_float` that
refuses a literal too large to be finite, and every message is then validated strictly, each
number with `math.isfinite` as `quackd/host.py`'s `_is_number` does and never a bool for a
number. A message is written with `allow_nan=False`, so neither side can send one either.
Every list and every string has a bound, and so has every body.

**Frames** travel raw, as the camera's own uint8 pixels, on loopback, where bytes cost
nothing, and as JPEG at the quality the server names on any other link, which is how
`PolicyInfo.jpeg_quality` asks for them. Either way a frame is base64 in its message.
"""

from __future__ import annotations

import base64
import io
import json
import math
from typing import Annotated, Any, Literal, TypeVar

import numpy as np
from PIL import Image
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from quackd_lerobot.policy.loop import FIRST_CHUNK_S

PROTOCOL = "quackd-policy"
PROTOCOL_VERSION = 1
DEFAULT_PORT = 9875
"""The Open Duck Mini takes 9871 and 9872, the ToddlerBot daemon 9873 and the Jetson host daemon
9874, and SECURITY.md lists every one of them, so the policy server comes next."""
TOKEN_HEADER = "X-Quackd-Token"
"""The token rides in this header and nowhere else, never in a URL, which is what logs keep."""
TOKEN_ENV = "QUACKD_POLICY_TOKEN"
"""Where the client reads a token from when `--policy-token` is not given."""
DEFAULT_TOKEN_FILE = "~/.quackd/policy.token"
"""Where `quackd policy serve` writes a token when it is given no `--token-file`, and where the
client reads one when it is given no other."""
MIN_TOKEN_CHARS = 16
"""The shortest token either side takes. `quackd policy serve` writes 64 hex digits, and 16 is
still more than a client could guess over a network in a lifetime. A shorter one is refused
rather than served, and could not be scrubbed out of an error without eating the letters
around it besides."""

LOOPBACK = frozenset({"127.0.0.1", "::1"})
"""The only hosts the client sends plain `http://` to, and so the only addresses the server
binds without `--behind-tls`: a server on the rest of 127/8 would be one no client could reach."""

POLICY_PATH = "/v1/policy"
RESET_PATH = "/v1/reset"
STEP_PATH = "/v1/step"
END_PATH = "/v1/end"
HEALTH_PATH = "/v1/health"
"""The route a readiness probe asks, answered before any token is asked for. It says whether the
server serves and nothing else: no policy, no session, no version."""

MAX_MOTORS = 32
"""The most motors a reset may name. An SO-101 has six, and a bimanual arm or a humanoid a few
dozen at most."""
MAX_CAMERAS = 4
"""The most cameras a reset may name. The lab arm has two, and the policies quackd means to
serve were trained on one to three."""
MAX_CHUNK = 1000
"""The most actions one chunk may hold. ACT's default chunk is 100 and SmolVLA's 50
(`upstream_api.CHUNK_SIZE`), so this is room and a bound on what a server can make the arm's
process hold."""
MAX_SIDE = 4096
"""The widest or tallest frame a camera may declare."""
MAX_FRAME_PIXELS = 1920 * 1080
"""The most pixels one frame may hold: a 1080p webcam, the largest a USB webcam commonly
gives. A policy looks at a few hundred pixels a side, so a bigger frame is a mistake."""
MAX_FRAME_B64 = 4 * ((MAX_FRAME_PIXELS * 3 + 2) // 3)
"""The longest a frame's base64 may be: the largest raw frame, as base64 writes it."""
MAX_BODY_BYTES = MAX_CAMERAS * MAX_FRAME_B64 + (1 << 20)
"""The largest request body the server reads: every camera at the largest raw frame, and a
megabyte for everything else in a step."""
MAX_REPLY_BYTES = 4 << 20
"""The largest reply the client reads. A chunk of `MAX_CHUNK` actions on `MAX_MOTORS` motors
is under a megabyte of JSON, and nothing else the server sends comes near it."""
MAX_INSTRUCTION_CHARS = 1000
MAX_TEXT_CHARS = 200
MAX_LOADED = 8
"""The most repositories a server may say it loaded: a checkpoint, the dataset its rate came
from, and the few models a policy names inside it (a tokenizer, a vision backbone)."""
MAX_INT_DIGITS = 18
"""The longest integer literal a message may hold. Every integer in the protocol is a count, a
size or a sequence number, and one longer than this is not any of them."""
MAX_LATENCY_S = FIRST_CHUNK_S
"""The latency a policy must declare less than: the loop's grace for a segment's first chunk
(`loop.FIRST_CHUNK_S`). A policy that takes this long to answer ends every segment starved
before its first chunk lands, on the arm and on the simulator alike."""
MIN_JPEG_QUALITY = 50
"""The lowest JPEG quality a server may ask for. Below it a policy sees blocks and ringing it
never saw in training, and the bytes it saves do not matter on any link a policy runs over."""
DEFAULT_JPEG_QUALITY = 90
"""The quality a server behind TLS asks for when it is not told one. High, because a policy was
trained on its camera's raw frames and every artefact is something it never saw."""
ROTATIONS = (0, 90, 180, 270)
"""The rotations a camera may declare, the ones `--camera-url`'s `rotation=` takes."""
NAME_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"
"""A motor's, a camera's or a feature's name: letters, digits and `_.-`, as LeRobot's
feature keys are (`observation.images.front`)."""
SESSION_PATTERN = r"^[0-9a-f]{32}$"
TEXT_PATTERN = r"^[\x20-\x7e]+$"
"""What a server may say about itself in words (`PolicyInfo`'s policy, versions and rate source):
printable ASCII. `quackd policy check` prints these, and a refusal of a rate quotes one, so an
escape in one could move the terminal's cursor, erase a line or write its clipboard, and a
bidi control could reverse what it shows. A server's refusals are made safe where the client
quotes them instead (`client.RemoteRunner._said`)."""

M = TypeVar("M", bound=BaseModel)


class ProtocolError(ValueError):
    """A message that is not what the protocol says, in one sentence that names what is wrong."""


def clean_token(token: str | None, source: str) -> str:
    """The token from `source` as it goes in the header, or a ValueError in a sentence that
    names `source` and never quotes the token.

    Stripped, because a token file ends in a newline. Refused with anything a header cannot
    carry, because `http.client` refuses such a header with a ValueError that quotes its value,
    and that value would be the token (`quackd.host.clean_token` is the precedent). A space or
    a line break inside is refused too, since no token either side writes has one, and a token
    shorter than `MIN_TOKEN_CHARS` is refused, so the server never guards an arm with one."""
    cleaned = (token or "").strip()
    if not cleaned:
        raise ValueError(f"the policy token from {source} is empty, and the server wants one")
    if not (cleaned.isascii() and cleaned.isprintable()) or any(c.isspace() for c in cleaned):
        raise ValueError(
            f"the policy token from {source} has a space, a line break or another character "
            "an HTTP header cannot carry: make one with openssl rand -hex 32, which has none"
        )
    if len(cleaned) < MIN_TOKEN_CHARS:
        raise ValueError(
            f"the policy token from {source} is shorter than {MIN_TOKEN_CHARS} characters, "
            "which is too easy to guess for a server whose answers move an arm: make one with "
            "openssl rand -hex 32"
        )
    return cleaned


# ── numbers ─────────────────────────────────────────────────────────────────────────────


def _finite(value: float) -> float:
    # the check `quackd/host.py`'s `_is_number` makes: NaN and Infinity are accepted by
    # `json.loads` and by a float field, and would reach the arm as a goal
    if not math.isfinite(value):
        raise ValueError("not a finite number")
    return value


Number = Annotated[float, AfterValidator(_finite)]
"""A finite real number. Validated strictly, so a bool or a string is not one."""
Name = Annotated[str, StringConstraints(pattern=NAME_PATTERN)]
SessionId = Annotated[str, StringConstraints(pattern=SESSION_PATTERN)]
Text = Annotated[str, StringConstraints(pattern=TEXT_PATTERN, max_length=MAX_TEXT_CHARS)]
"""Words a server describes itself in, safe to print (`TEXT_PATTERN`)."""
Tick = Annotated[int, Field(ge=0, le=2**53)]
"""A tick: whole, never negative, and exact in any JSON reader."""
Seq = Annotated[int, Field(ge=1, le=2**53)]
"""A step's sequence number, counted from 1 in each session."""


def _refuse_constant(name: str) -> Any:
    raise ProtocolError(f"{name} is not a number the protocol carries")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ProtocolError(f"{text[:40]} is too large to be a finite number")
    return value


def _bounded_int(text: str) -> int:
    if len(text.lstrip("-")) > MAX_INT_DIGITS:
        raise ProtocolError(f"an integer of {len(text)} digits is not a count the protocol sends")
    return int(text)


def loads(raw: bytes) -> Any:
    """`raw` as JSON, refusing every number that is not finite and every integer too long to be
    a count, or a `ProtocolError` saying what it is instead."""
    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=_refuse_constant,
            parse_float=_finite_float,
            parse_int=_bounded_int,
        )
    except ProtocolError:
        raise
    # RecursionError too: that is how `json.loads` meets ten thousand nested brackets
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise ProtocolError(f"it is not JSON ({type(e).__name__})") from None


def dumps(message: BaseModel | dict[str, Any]) -> bytes:
    """A message as the bytes of its JSON. `allow_nan=False`, so a number that is not finite is
    a ValueError here rather than a `NaN` on the wire."""
    obj = message.model_dump() if isinstance(message, BaseModel) else message
    return json.dumps(obj, allow_nan=False, separators=(",", ":")).encode("utf-8")


def parse(model: type[M], raw: bytes) -> M:
    """`raw` read into `model`, or a `ProtocolError` naming the first thing wrong with it."""
    return validate(model, loads(raw))


def validate(model: type[M], obj: Any) -> M:
    """`obj` validated as `model`, or a `ProtocolError` naming the first thing wrong with it."""
    try:
        return model.model_validate(obj)
    except ValidationError as e:
        raise ProtocolError(_first_error(e)) from None


def _first_error(e: ValidationError) -> str:
    errors = e.errors(include_url=False, include_input=False)
    if not errors:
        return "it is not a message of the protocol"
    first = errors[0]
    where = ".".join(str(part) for part in first.get("loc", ())) or "the message"
    more = f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""
    return f"{where}: {first.get('msg', 'is wrong')}{more}"


# ── the messages ────────────────────────────────────────────────────────────────────────


class _Message(BaseModel):
    """Every message: nothing it does not name, and strict types, so `true` is never a number
    and `"3"` never a count."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Quantiles(_Message):
    """The 1st and 99th percentile of each dimension of the data a policy learned from, which
    is how the arm's side tells whether the policy was trained on an arm like this one."""

    q01: list[Number] = Field(max_length=MAX_MOTORS)
    q99: list[Number] = Field(max_length=MAX_MOTORS)

    @model_validator(mode="after")
    def _same_length(self) -> Quantiles:
        if len(self.q01) != len(self.q99):
            raise ValueError("q01 and q99 have different lengths")
        return self


class ImageFeature(_Message):
    """One image a policy looks at, under the policy's own key, and the size it was trained
    at, or None for any."""

    key: Name
    height: int | None = Field(default=None, ge=1, le=MAX_SIDE)
    width: int | None = Field(default=None, ge=1, le=MAX_SIDE)


class PolicyFeatures(_Message):
    """What a policy takes and gives: the size of its state and its action, None for a policy
    that takes whatever the arm has (a scripted one), and the images it looks at.

    `action_names` are its action's dimensions by name, in order, where the checkpoint carries
    them (`upstream_api.ACTION_FEATURE_NAMES`), which the arm checks against its bus's motors.
    `pads_images` is a policy that runs with some of its images missing and pads them
    (`upstream_api.MISSING_IMAGES_PADDED`); one without it needs a camera for every image."""

    state: int | None = Field(default=None, ge=1, le=MAX_MOTORS)
    action: int | None = Field(default=None, ge=1, le=MAX_MOTORS)
    images: list[ImageFeature] = Field(default_factory=list, max_length=MAX_CAMERAS)
    action_names: list[Name] | None = Field(default=None, max_length=MAX_MOTORS)
    pads_images: bool = False


class PolicyInfo(_Message):
    """`GET /v1/policy`: what the server is serving."""

    protocol: Text
    protocol_version: int = Field(ge=0, le=2**31)
    server_version: Text
    policy: Text
    """`repo@revision` for a checkpoint, `scripted:NAME` for a scripted policy."""
    features: PolicyFeatures
    rate_hz: Number = Field(gt=0)
    rate_source: Text
    chunk_size: int = Field(ge=1, le=MAX_CHUNK)
    n_action_steps: int = Field(ge=1, le=MAX_CHUNK)
    per_tick: bool
    gpu: bool
    state_quantiles: Quantiles | None = None
    action_quantiles: Quantiles | None = None
    latency_s: Number = Field(ge=0, lt=MAX_LATENCY_S)
    threads: int | None = Field(default=None, ge=1, le=4096)
    jpeg_quality: int | None = Field(default=None, ge=MIN_JPEG_QUALITY, le=100)
    """The quality frames are to come at as JPEG, or None for raw frames."""
    cameras: dict[Name, Name] = Field(default_factory=dict, max_length=MAX_CAMERAS)
    """The arm's camera names the server maps to the policy's image keys (`--cameras`)."""
    loaded: list[Text] = Field(default_factory=list, max_length=MAX_LOADED)
    """Every repository the server loaded, each as what it is and `repo@revision`: the
    checkpoint, the dataset its rate was read from, and every nested model it was pinned to.
    Empty for a scripted policy, which loads nothing. The arm writes them into its record."""

    @model_validator(mode="after")
    def _steps_fit_the_chunk(self) -> PolicyInfo:
        if self.n_action_steps > self.chunk_size:
            raise ValueError(
                f"n_action_steps {self.n_action_steps} is more than the chunk of "
                f"{self.chunk_size} it is played from"
            )
        return self


class CameraInfo(_Message):
    """One of the arm's cameras, as a reset declares it: its name, the size of the frames it
    gives, and the rotation they were given as they were read."""

    name: Name
    height: int = Field(ge=1, le=MAX_SIDE)
    width: int = Field(ge=1, le=MAX_SIDE)
    rotation: Literal[0, 90, 180, 270] = 0

    @model_validator(mode="after")
    def _not_too_big(self) -> CameraInfo:
        if self.height * self.width > MAX_FRAME_PIXELS:
            raise ValueError(
                f"{self.width}x{self.height} is more than the {MAX_FRAME_PIXELS} pixels a frame "
                "may hold"
            )
        return self


class ResetRequest(_Message):
    """`POST /v1/reset`: a new session, for one instruction, on one arm."""

    instruction: str = Field(min_length=1, max_length=MAX_INSTRUCTION_CHARS)
    motors: list[Name] = Field(min_length=1, max_length=MAX_MOTORS)
    """The arm's motor names in its bus's order, which is the order of every state."""
    cameras: list[CameraInfo] = Field(default_factory=list, max_length=MAX_CAMERAS)
    replaces: SessionId | None = None
    """The session this client held before, which its reset may end however recently it was
    used. A reset whose reply was lost leaves the client holding this same id, and the server
    takes it for the session that reset made as well. None for a client's first reset."""

    @model_validator(mode="after")
    def _distinct(self) -> ResetRequest:
        if not self.instruction.strip():
            raise ValueError("the instruction is blank")
        if len(set(self.motors)) != len(self.motors):
            raise ValueError("a motor is named twice")
        names = [camera.name for camera in self.cameras]
        if len(set(names)) != len(names):
            raise ValueError("a camera is named twice")
        return self


class ResetReply(_Message):
    session: SessionId


class Frame(_Message):
    """One camera's frame: raw uint8 RGB pixels, row by row, or a JPEG, as base64."""

    name: Name
    encoding: Literal["raw", "jpeg"]
    height: int = Field(ge=1, le=MAX_SIDE)
    width: int = Field(ge=1, le=MAX_SIDE)
    data: str = Field(min_length=1, max_length=MAX_FRAME_B64)


class StepRequest(_Message):
    """`POST /v1/step`: one observation, stamped with the tick it was read at."""

    session: SessionId
    seq: Seq
    tick: Tick
    state: list[Number] = Field(min_length=1, max_length=MAX_MOTORS)
    """Each motor's reading, in the order the reset named them, in degrees (the gripper
    0..100)."""
    frames: list[Frame] = Field(default_factory=list, max_length=MAX_CAMERAS)
    sent: dict[Name, Number] = Field(default_factory=dict, max_length=MAX_MOTORS)
    """The command the arm last sent, after its clip and its step cap, empty before the first."""


Action = Annotated[dict[Name, Number], Field(min_length=1, max_length=MAX_MOTORS)]
"""One tick's goals, by motor name, in degrees (the gripper 0..100)."""


class StepReply(_Message):
    """A step's answer. `chunk[i]` is the goal for tick `tick + i`; None is a policy with nothing
    more to do, and an empty list is one with nothing for now."""

    session: SessionId
    seq: Seq
    chunk: list[Action] | None = Field(max_length=MAX_CHUNK)
    inference_s: Number = Field(ge=0)


class EndRequest(_Message):
    """`POST /v1/end`: the client is done with its session."""

    session: SessionId


class EndReply(_Message):
    ended: bool
    """Whether the session was the live one and is now over. The live one is also ended by the
    session its reset replaced, when that reset's reply never reached its client, which still
    holds the old id. A session already over, ended or replaced, is answered False, so ending
    one twice is no error."""


class Refusal(_Message):
    """What every refusal carries, whatever its status."""

    ok: Literal[False] = False
    reason: str


# ── frames ──────────────────────────────────────────────────────────────────────────────


def encode_frame(name: str, image: Any, jpeg_quality: int | None) -> Frame:
    """One camera's frame for a step: an H x W x 3 uint8 RGB array, raw or as a JPEG at
    `jpeg_quality`. Anything else is a ValueError, raised before anything is sent."""
    pixels = np.asarray(image)
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError(
            f"the {name} camera's frame is {pixels.dtype} {tuple(pixels.shape)}, and a frame is "
            "sent as height x width x 3 uint8"
        )
    height, width = int(pixels.shape[0]), int(pixels.shape[1])
    if jpeg_quality is None:
        data = np.ascontiguousarray(pixels).tobytes()
        encoding: Literal["raw", "jpeg"] = "raw"
    else:
        buf = io.BytesIO()
        Image.fromarray(pixels).save(buf, format="JPEG", quality=jpeg_quality)
        data = buf.getvalue()
        encoding = "jpeg"
    return Frame(
        name=name,
        encoding=encoding,
        height=height,
        width=width,
        data=base64.b64encode(data).decode("ascii"),
    )


def decode_frame(frame: Frame, camera: CameraInfo) -> np.ndarray:
    """A frame back to an H x W x 3 uint8 RGB array, checked against the size its camera was
    declared at, or a `ProtocolError`. A JPEG's size is read from its header and checked
    before a pixel of it is decoded."""
    if (frame.height, frame.width) != (camera.height, camera.width):
        raise ProtocolError(
            f"the {frame.name} camera's frame is {frame.width}x{frame.height}, and the reset "
            f"declared it {camera.width}x{camera.height}"
        )
    try:
        data = base64.b64decode(frame.data, validate=True)
    except ValueError:
        raise ProtocolError(f"the {frame.name} camera's frame is not base64") from None
    if frame.encoding == "raw":
        expected = frame.height * frame.width * 3
        if len(data) != expected:
            raise ProtocolError(
                f"the {frame.name} camera's raw frame is {len(data)} bytes, and "
                f"{frame.width}x{frame.height} RGB is {expected}"
            )
        return np.frombuffer(data, dtype=np.uint8).reshape(frame.height, frame.width, 3)
    try:
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format != "JPEG" or picture.size != (frame.width, frame.height):
                raise ProtocolError(
                    f"the {frame.name} camera's frame is not a {frame.width}x{frame.height} JPEG"
                )
            return np.asarray(picture.convert("RGB"), dtype=np.uint8)
    except ProtocolError:
        raise
    except Exception as e:
        raise ProtocolError(
            f"the {frame.name} camera's frame is not a JPEG PIL can read ({type(e).__name__})"
        ) from None


__all__ = [
    "DEFAULT_JPEG_QUALITY",
    "DEFAULT_PORT",
    "DEFAULT_TOKEN_FILE",
    "END_PATH",
    "HEALTH_PATH",
    "LOOPBACK",
    "MAX_BODY_BYTES",
    "MAX_CAMERAS",
    "MAX_CHUNK",
    "MAX_LATENCY_S",
    "MAX_LOADED",
    "MAX_MOTORS",
    "MAX_REPLY_BYTES",
    "MIN_TOKEN_CHARS",
    "POLICY_PATH",
    "PROTOCOL",
    "PROTOCOL_VERSION",
    "RESET_PATH",
    "STEP_PATH",
    "TOKEN_ENV",
    "TOKEN_HEADER",
    "CameraInfo",
    "EndReply",
    "EndRequest",
    "Frame",
    "ImageFeature",
    "PolicyFeatures",
    "PolicyInfo",
    "ProtocolError",
    "Quantiles",
    "Refusal",
    "ResetReply",
    "ResetRequest",
    "StepReply",
    "StepRequest",
    "clean_token",
    "decode_frame",
    "dumps",
    "encode_frame",
    "loads",
    "parse",
    "validate",
]
