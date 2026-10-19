"""A decision LLM that can see, behind `--decision-frames`: what it is shown, and who may see.

Rok's rule, which every test here holds a piece of: a camera frame reaches a decision LLM only
when the run asks with the flag, only for a row that can see, and only at OpenAI's own hosts, and
the record keeps what was sent, written before the request that carries it is sent (ADR-0053).

Nothing here needs the `openai` package. CI installs no provider SDK, so every client that may
see is quackd's own `DecisionsAPILLM` built on `tests/fake_decisions_api.py`, which records what
it was handed and never builds a request. What the real SDK puts on the wire with a frame in it is
`tests/test_decisions_api_wire.py`, wherever the SDK is installed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import random
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from quackd.agent.decision import decisions_api, factory, frames
from quackd.agent.decision import stepper as stepper_module
from quackd.agent.decision.base import DecisionError, state_text
from quackd.agent.decision.catalogue import ENTRY_POINT_GROUP, PRESETS
from quackd.agent.decision.decisions_api import MAX_IMAGES, DecisionsAPILLM
from quackd.agent.decision.factory import (
    find_preset,
    make_decision_llm,
    resolve_decision_frames,
    resolve_decision_price,
)
from quackd.agent.decision.frames import (
    MAX_BYTES,
    MAX_SIDE_PX,
    Snapshot,
    encode,
    may_send_frames,
    too_large,
)
from quackd.agent.decision.stepper import Call, Stepper
from quackd.cli import _DECISION_FRAMES, _DECISION_MODE, _frames_notice, app
from quackd.verbs.registry import default_registry
from tests import fake_decisions_api as fake_api
from tests import fake_laya, fake_systemone
from tests.conftest import DUCKS, plain_text

runner = CliRunner()
OPENAI = PRESETS["openai"]
KEY = "sk-test-not-a-real-key"
"""Never a real one: nothing here leaves the machine, and a fake key is what proves it."""

STATE = {"goal": "find the orange ball", "now": "standing"}
QUESTIONS = {"done": {"type": "noul", "instructions": "Is it done?"}}

BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{100,}")
"""A run of base64 long enough to be a picture's, which no record and no state may hold."""


@pytest.fixture(autouse=True)
def _no_ambient_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer who pilots with OpenAI has these in a `.env`, and the CLI loads it. Empty
    rather than deleted, because an empty value survives `load_dotenv`."""
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_CUSTOM_HEADERS",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
    ):
        monkeypatch.setenv(name, "")


def _seeing(
    monkeypatch: pytest.MonkeyPatch,
    *,
    frames_on: bool = True,
    url: str | None = None,
    fake: fake_api.FakeOpenAI | None = None,
) -> tuple[Any, fake_api.FakeOpenAI]:
    """OpenAI's row built the way the CLI builds it, on the fake SDK, with a fake key."""
    installed = fake_api.install(
        monkeypatch, fake or fake_api.FakeOpenAI(answers=fake_api.turn("stop"))
    )
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    return make_decision_llm(OPENAI, url=url, frames=frames_on), installed


def _picture(size: tuple[int, int] = (640, 480)) -> Image.Image:
    """A camera's picture with something in it: a floor, a wall and an orange ball."""
    image = Image.new("RGB", size, (90, 110, 140))
    draw = ImageDraw.Draw(image)
    w, h = size
    draw.rectangle((0, h // 2, w, h), fill=(150, 130, 100))
    draw.ellipse((w // 3, h // 2, w // 3 + w // 6, h // 2 + w // 6), fill=(240, 120, 20))
    return image


def _snapshot(tmp_path: Path, *, camera: str = "camera") -> Snapshot:
    return Snapshot(
        image=_picture(),
        camera=camera,
        path=tmp_path / "frames" / "0003-decision.jpg",
        where="frames/0003-decision.jpg",
    )


def _stepper(llm: Any, *, frames_on: bool) -> Stepper:
    """A stepper offered the brake and nothing else, built bare: what is under test is what a
    turn shows and keeps, and a registry of verbs would only be scenery. Built bare, so `frames`
    is set by hand here, which is what the gate inside each turn exists for."""
    stepper = Stepper(
        mode="on",
        goal="a goal",
        llm=llm,
        name=llm.name,
        model=llm.model,
        price=resolve_decision_price(OPENAI),
        frames=frames_on,
    )
    stepper.calls["stop"] = Call(name="stop", arguments={}, label="stop")
    stepper.classes["stop"] = "brake"
    stepper.what["stop"] = "the brake"
    stepper.early.add("stop")
    return stepper


async def _turn(
    stepper: Stepper, snapshot: Snapshot | None, *, cameras: tuple[str, ...] = ("camera",)
) -> Any:
    obs = SimpleNamespace(features={}, cameras=list(cameras))
    return await stepper.advise(obs, cleared=True, budget="b", snapshot=snapshot)  # type: ignore[arg-type]


def _sent_image(post: dict[str, Any]) -> bytes:
    """The bytes of the one image a request carried, read back out of its data URL."""
    (_text, image) = post["body"]["input"][0]["content"]
    head, _, data = image["image_url"].partition(",")
    assert head == "data:image/jpeg;base64", head
    return base64.b64decode(data)


def _sent_text(post: dict[str, Any]) -> str:
    """The state as a request carried it: the string itself, or the text part beside a frame."""
    given = post["body"]["input"]
    return given if isinstance(given, str) else given[0]["content"][0]["text"]


# ── what a frame is ─────────────────────────────────────────────────────────────────────


def test_a_frame_is_a_jpeg_no_side_of_it_longer_than_the_limit_and_its_shape_kept() -> None:
    frame = encode(_picture((1280, 720)), "camera")
    assert (frame.width, frame.height) == (MAX_SIDE_PX, 288)
    assert frame.data[:3] == b"\xff\xd8\xff", "not a JPEG"
    with Image.open(io.BytesIO(frame.data)) as back:
        assert back.format == "JPEG" and back.size == (frame.width, frame.height)
    assert (frame.media_type, frame.detail) == ("image/jpeg", "low")
    assert frame.sha256 == hashlib.sha256(frame.data).hexdigest()
    assert frame.data_url() == "data:image/jpeg;base64," + base64.b64encode(frame.data).decode()


def test_a_small_picture_is_sent_at_its_own_size() -> None:
    """Shrunk to fit, never enlarged: enlarging adds bytes and nothing to look at."""
    frame = encode(_picture((256, 256)), "camera")
    assert (frame.width, frame.height) == (256, 256)


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L", "P"])
def test_a_picture_in_any_mode_goes_as_rgb_and_is_itself_left_alone(mode: str) -> None:
    """The step's PNG and the pilot's picture are the same image the frame is made from, so it
    is made from a copy, whatever mode the camera handed over, RGB itself included, which needs
    no conversion and is shrunk all the same."""
    source = _picture((600, 400)).convert(mode)
    before = (source.mode, source.size, source.tobytes())
    frame = encode(source, "camera")
    with Image.open(io.BytesIO(frame.data)) as back:
        assert back.mode == "RGB"
    assert (source.mode, source.size, source.tobytes()) == before


def test_the_record_of_a_frame_is_everything_about_it_but_its_bytes() -> None:
    frame = encode(_picture((640, 480)), "wrist")
    said = frame.record()
    assert said == {
        "camera": "wrist",
        "media_type": "image/jpeg",
        "width": MAX_SIDE_PX,
        "height": 384,
        "bytes": len(frame.data),
        "detail": "low",
        "sha256": frame.sha256,
    }
    assert BASE64_RUN.search(json.dumps(said)) is None


def test_no_picture_at_the_size_sent_comes_near_the_limit() -> None:
    """What `frames.MAX_BYTES` says of itself: random noise filling the largest frame sent, the
    hardest picture there is to compress, still comes in under it."""
    noise = random.Random(53).randbytes(MAX_SIDE_PX * MAX_SIDE_PX * 3)
    frame = encode(Image.frombytes("RGB", (MAX_SIDE_PX, MAX_SIDE_PX), noise), "camera")
    assert len(frame.data) < MAX_BYTES and too_large(frame) is None


def test_a_frame_over_the_limit_says_how_large_it_came_to(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frames, "MAX_BYTES", 100)
    frame = encode(_picture(), "camera")
    reason = too_large(frame)
    assert reason == (
        f"the picture came to {len(frame.data)} bytes as a JPEG, over the 100 a turn may send"
    )


# ── who may be shown one ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [None, "https://eu.api.openai.com"], ids=["api", "a region"])
def test_quackds_own_client_built_to_see_at_openai_may_be_shown_a_frame(
    url: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm, _fake = _seeing(monkeypatch, url=url)
    assert may_send_frames(llm) is True


def test_the_same_client_built_without_frames_may_not(monkeypatch: pytest.MonkeyPatch) -> None:
    llm, _fake = _seeing(monkeypatch, frames_on=False)
    assert llm.frames is False and may_send_frames(llm) is False


class _Plugin:
    """A third party's decision LLM that says everything the client says about itself: it can
    see, it was built to, and it is at OpenAI's own address. Its `decide` takes two arguments,
    as every plugin's does, so handing it a frame is a `TypeError`."""

    name, model, url, frames = "stub", "stub-1", "https://api.openai.com", True

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def decide(self, state: Any, questions: Any) -> Any:
        self.calls.append(dict(state))
        return {
            "answers": {
                "next_verb": {"choice": "stop", "confidence": 0.99},
                "done": {"noul": 0.0},
                "need_human": {"noul": 0.0},
            }
        }


def test_a_plugin_that_says_it_sees_at_openai_may_not() -> None:
    assert may_send_frames(_Plugin()) is False


def test_a_subclass_of_the_client_may_not(monkeypatch: pytest.MonkeyPatch) -> None:
    """quackd's own client and no other, so code that dresses itself as that client, or changes
    what its `decide` does, is not taken for it."""
    llm, _fake = _seeing(monkeypatch)

    class Mine(DecisionsAPILLM):
        pass

    llm.__class__ = Mine
    assert may_send_frames(llm) is False


def _at_your_own(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The client built to see at a server of your own, which only a test can make: the host
    rule is told the address is OpenAI's while the client is built, and is the rule again
    after."""
    real = decisions_api.is_openai_host
    monkeypatch.setattr(decisions_api, "is_openai_host", lambda url: True)
    llm, fake = _seeing(monkeypatch, url="http://127.0.0.1:8765")
    monkeypatch.setattr(decisions_api, "is_openai_host", real)
    assert llm.frames is True
    return llm, fake


def test_the_client_at_a_server_of_your_own_may_not_whatever_it_was_built_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate reads the address itself, with the rule that decides where the key goes, rather
    than trusting what the client was built with."""
    llm, _fake = _at_your_own(monkeypatch)
    assert may_send_frames(llm) is False


# ── what the factory refuses, before anything connects ──────────────────────────────────


@pytest.mark.parametrize("word", ["1", "true", "YES", " on "])
def test_the_variable_turns_frames_on_in_any_of_its_words(
    word: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(factory.ENV_FRAMES, word)
    assert resolve_decision_frames(None, spec=OPENAI) is True


@pytest.mark.parametrize("word", ["", "   ", "0", "false", "No", "OFF"])
def test_the_variable_turns_frames_off_and_blank_is_off(
    word: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(factory.ENV_FRAMES, word)
    assert resolve_decision_frames(None, spec=OPENAI) is False
    assert resolve_decision_frames(None, spec=None) is False, "off asks for no decision LLM"


def test_a_word_the_variable_does_not_take_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(factory.ENV_FRAMES, "maybe")
    with pytest.raises(DecisionError, match="unknown QUACKD_DECISION_FRAMES 'maybe'"):
        resolve_decision_frames(None, spec=OPENAI)


def test_the_flag_beats_the_variable_both_ways(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(factory.ENV_FRAMES, "1")
    assert resolve_decision_frames(False, spec=PRESETS["kev"]) is False
    monkeypatch.setenv(factory.ENV_FRAMES, "0")
    assert resolve_decision_frames(True, spec=OPENAI) is True


def test_decision_llm_off_on_the_line_turns_frames_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(factory.ENV_FRAMES, "1")
    assert resolve_decision_frames(True, spec=None, refused=True) is False
    assert resolve_decision_frames(None, spec=None, refused=True) is False


@pytest.mark.parametrize(
    ("flag", "env", "where"),
    [(True, "", "--decision-frames"), (None, "1", "QUACKD_DECISION_FRAMES")],
    ids=["the flag", "the variable"],
)
def test_frames_with_no_decision_llm_named_stop_the_run_naming_who_asked(
    flag: bool | None, env: str, where: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(factory.ENV_FRAMES, env)
    with pytest.raises(DecisionError) as e:
        resolve_decision_frames(flag, spec=None)
    said = str(e.value)
    assert said.startswith(f"{where} shows a decision LLM the camera, and none is named")
    assert "--decision-llm openai" in said and factory.ENV_LLM in said


@pytest.mark.parametrize("name", [n for n, spec in PRESETS.items() if not spec.sees])
def test_frames_for_a_row_that_cannot_see_stop_the_run(name: str) -> None:
    with pytest.raises(DecisionError) as e:
        resolve_decision_frames(True, spec=PRESETS[name], url="http://127.0.0.1:9")
    assert f"--decision-llm {name} is sent text alone: only openai may be shown a picture" in str(
        e.value
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765",
        "https://localhost",
        "http://api.openai.com",
        "https://api.openai.com:8443",
        "https://evil.example\\@api.openai.com",
    ],
)
def test_frames_to_any_address_but_openais_own_stop_the_run(url: str) -> None:
    """A server of your own on this machine included: a frame goes to OpenAI's hosts alone, and
    the address is read by the rule that decides the key."""
    with pytest.raises(DecisionError, match="sends the camera to OpenAI's own API alone"):
        resolve_decision_frames(True, spec=OPENAI, url=url)


@pytest.mark.parametrize("url", [None, "https://api.openai.com", "https://eu.api.openai.com"])
def test_frames_for_openai_at_its_own_hosts_are_allowed(url: str | None) -> None:
    assert resolve_decision_frames(True, spec=OPENAI, url=url) is True


def test_an_address_with_a_password_is_never_quoted_by_the_refusal() -> None:
    with pytest.raises(DecisionError) as e:
        resolve_decision_frames(True, spec=OPENAI, url="http://rok:hunter2@127.0.0.1:8765")
    assert "hunter2" not in str(e.value) and "127.0.0.1:8765" in str(e.value)


class _Entry:
    def __init__(self, name: str, value: str) -> None:
        self.name, self.value, self.group = name, value, ENTRY_POINT_GROUP


def test_a_plugin_that_says_it_can_see_is_built_blind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing a plugin says about itself makes it a row that can see: its row is built with
    `sees` false, and frames for it are refused like frames for any row that cannot see."""
    import importlib.metadata

    import tests.stub_decision_llm as stub

    monkeypatch.setattr(stub, "SEES", True, raising=False)
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [_Entry("stub", "tests.stub_decision_llm")],
    )
    factory._plugins.cache_clear()
    try:
        spec = find_preset("stub")
        assert spec.sees is False
        with pytest.raises(DecisionError, match="is sent text alone"):
            resolve_decision_frames(True, spec=spec, url="https://api.openai.com")
        with pytest.raises(DecisionError, match="is sent text alone"):
            make_decision_llm(spec, url="https://api.openai.com", frames=True)
    finally:
        factory._plugins.cache_clear()


@pytest.mark.parametrize("name", [n for n, spec in PRESETS.items() if not spec.sees])
def test_no_row_that_cannot_see_is_built_to_be_shown_a_frame(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_systemone.install(monkeypatch, fake_systemone.FakeDecisionLLM())
    fake_laya.install(monkeypatch, fake_laya.FakeRouter())
    with pytest.raises(DecisionError, match="is sent text alone"):
        make_decision_llm(PRESETS[name], url="http://127.0.0.1:9", frames=True)


def test_the_client_built_to_see_is_refused_at_a_server_of_your_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI())
    with pytest.raises(DecisionError, match="sends the camera to OpenAI's own API alone"):
        make_decision_llm(OPENAI, url="http://127.0.0.1:8765", frames=True)
    assert fake.kwargs == {}, "a client was built for it"


def test_that_refusal_is_the_same_sentence_without_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(DecisionError, match="sends the camera to OpenAI's own API alone"):
        DecisionsAPILLM(OPENAI, url="http://127.0.0.1:8765", frames=True)


# ── what goes on the wire ───────────────────────────────────────────────────────────────


async def test_a_frame_goes_beside_the_state_as_one_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reference's shape for an image: `input` as user messages, here one, with `role`, which
    it requires, and the state as `input_text` and the frame as `input_image` after it, with its
    `detail`. The text is the very string a turn without a frame sends as the whole `input`."""
    llm, fake = _seeing(monkeypatch)
    frame = encode(_picture(), "camera")
    await llm.decide(STATE, QUESTIONS, frames=(frame,))
    await llm.decide(STATE, QUESTIONS)
    with_frame, without = (post["body"]["input"] for post in fake.posts)
    assert with_frame == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": state_text(STATE)},
                {"type": "input_image", "image_url": frame.data_url(), "detail": "low"},
            ],
        }
    ]
    assert without == state_text(STATE)


async def test_a_client_built_blind_is_handed_no_frame_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm, fake = _seeing(monkeypatch, frames_on=False)
    with pytest.raises(ValueError, match="nothing was sent"):
        await llm.decide(STATE, QUESTIONS, frames=(encode(_picture(), "camera"),))
    assert fake.posts == []


async def test_a_client_away_from_openai_is_handed_no_frame_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm, fake = _at_your_own(monkeypatch)
    with pytest.raises(ValueError, match="nothing was sent"):
        await llm.decide(STATE, QUESTIONS, frames=(encode(_picture(), "camera"),))
    assert fake.posts == []


async def test_more_frames_than_one_request_carries_are_refused_before_anything_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm, fake = _seeing(monkeypatch)
    frame = encode(_picture((64, 64)), "camera")
    with pytest.raises(ValueError, match=f"at most {MAX_IMAGES}"):
        await llm.decide(STATE, QUESTIONS, frames=[frame] * (MAX_IMAGES + 1))
    assert fake.posts == []
    await llm.decide(STATE, QUESTIONS, frames=[frame] * MAX_IMAGES)
    assert len(fake.posts) == 1, "the limit itself is one request"


# ── a turn ──────────────────────────────────────────────────────────────────────────────


async def test_a_turn_keeps_its_frame_before_it_sends_it_and_says_which_it_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record before send: the copy is on disk when the request leaves, the turn's record names
    it with its size and its hash and never its bytes, and the frame is counted."""
    snapshot = _snapshot(tmp_path)
    on_disk: list[bool] = []

    class Looks(fake_api.FakeOpenAI):
        async def _send(self, path: str, cast_to: Any, body: Any, options: Any) -> Any:
            on_disk.append(snapshot.path.exists())
            return await super()._send(path, cast_to, body, options)

    llm, fake = _seeing(monkeypatch, fake=Looks(answers=fake_api.turn("stop")))
    stepper = _stepper(llm, frames_on=True)
    advice = await _turn(stepper, snapshot)
    (post,) = fake.posts
    sent = _sent_image(post)
    assert on_disk == [True], "the request left before its copy was on disk"
    assert snapshot.path.read_bytes() == sent
    assert advice.record["frame"] == {
        "camera": "camera",
        "media_type": "image/jpeg",
        "width": MAX_SIDE_PX,
        "height": 384,
        "bytes": len(sent),
        "detail": "low",
        "sha256": hashlib.sha256(sent).hexdigest(),
        "path": "frames/0003-decision.jpg",
        "kept": True,
    }
    assert "frame_skipped" not in advice.record
    assert advice.gate == "taken"
    summary = stepper.summary()
    assert (stepper.frames_sent, summary["frames"], summary["frames_sent"]) == (1, True, 1)


async def test_the_state_says_a_picture_went_and_holds_no_byte_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm, fake = _seeing(monkeypatch)
    advice = await _turn(_stepper(llm, frames_on=True), _snapshot(tmp_path))
    text = _sent_text(fake.posts[0])
    state = json.loads(text)
    assert all(isinstance(value, str) for value in state.values()), "every field is a sentence"
    assert state["picture"] == (
        "The image sent with this state is what this robot saw through its camera this turn."
    )
    for forbidden in ("png", "base64", "data:image", "jpeg"):
        assert forbidden not in text.lower(), f"{forbidden} reached the state"
    assert BASE64_RUN.search(text) is None
    assert advice.record["state_chars"] == len(text), "the state measured is the text sent"
    recorded = json.dumps(advice.event())
    assert "data:" not in recorded and BASE64_RUN.search(recorded) is None


async def test_the_camera_is_named_only_on_a_body_with_several(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm, fake = _seeing(monkeypatch)
    stepper = _stepper(llm, frames_on=True)
    await _turn(stepper, _snapshot(tmp_path, camera="wrist"), cameras=("wrist", "top"))
    await _turn(stepper, _snapshot(tmp_path), cameras=("camera",))
    several, one = (json.loads(_sent_text(post))["picture"] for post in fake.posts)
    assert "through its wrist camera" in several
    assert "through its camera" in one


async def test_a_turn_with_no_picture_is_asked_on_its_text_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm, fake = _seeing(monkeypatch)
    advice = await _turn(_stepper(llm, frames_on=True), None)
    assert advice.record["frame"] is None
    assert advice.record["frame_skipped"] == "the primary camera gave no picture this turn"
    (post,) = fake.posts
    assert isinstance(post["body"]["input"], str)
    assert "picture" not in json.loads(post["body"]["input"])


async def test_a_picture_that_will_not_encode_costs_the_turn_its_frame_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(image: Any, camera: str) -> Any:
        raise OSError("encoder not available")

    monkeypatch.setattr(stepper_module, "encode", broken)
    llm, fake = _seeing(monkeypatch)
    snapshot = _snapshot(tmp_path)
    advice = await _turn(_stepper(llm, frames_on=True), snapshot)
    assert advice.record["frame"] is None
    assert advice.record["frame_skipped"] == "the picture could not be encoded: OSError"
    assert isinstance(fake.posts[0]["body"]["input"], str) and advice.gate == "taken"
    assert not snapshot.path.exists()


async def test_a_frame_over_the_limit_is_not_sent_or_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(frames, "MAX_BYTES", 100)
    llm, fake = _seeing(monkeypatch)
    snapshot = _snapshot(tmp_path)
    advice = await _turn(_stepper(llm, frames_on=True), snapshot)
    assert advice.record["frame"] is None
    assert "over the 100 a turn may send" in advice.record["frame_skipped"]
    assert isinstance(fake.posts[0]["body"]["input"], str)
    assert not snapshot.path.exists()


async def test_a_disk_that_refuses_the_copy_costs_the_copy_and_never_the_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`kept: false`, the frame still sent and counted, and the turn answered as it would have
    been: a full disk is a reason to lose a copy and never a reason to end a run."""

    def refuse(path: Path, data: bytes) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(frames, "write_jpeg", refuse)
    llm, fake = _seeing(monkeypatch)
    stepper = _stepper(llm, frames_on=True)
    snapshot = _snapshot(tmp_path)
    advice = await _turn(stepper, snapshot)
    assert advice.record["frame"]["kept"] is False
    assert advice.record["frame"]["path"] == "frames/0003-decision.jpg"
    assert _sent_image(fake.posts[0]), "the frame did not go"
    assert stepper.frames_sent == 1 and advice.gate == "taken"
    assert not snapshot.path.exists()


async def test_a_turn_that_fails_after_its_frame_left_still_says_it_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = fake_api.status_error(fake_api.RateLimitError, 429, {"error": {"message": "slow"}})
    llm, _fake = _seeing(monkeypatch, fake=fake_api.FakeOpenAI(raises=refused))
    stepper = _stepper(llm, frames_on=True)
    snapshot = _snapshot(tmp_path)
    advice = await _turn(stepper, snapshot)
    assert advice.gate == "error" and advice.record["error"] == "RateLimitError: HTTP 429"
    assert advice.record["frame"]["kept"] is True and snapshot.path.exists()
    assert stepper.frames_sent == 1


class _Pending(fake_api.FakeOpenAI):
    """A request that is out when the run is interrupted: handed over and recorded, and then
    either a second Ctrl-C lands where it is, or it waits until it is cancelled."""

    def __init__(self, press: type[BaseException]) -> None:
        super().__init__(answers=fake_api.turn("stop"))
        self.press = press
        self.handed = asyncio.Event()

    async def _send(self, path: str, cast_to: Any, body: Any, options: Any) -> Any:
        self.posts.append({"path": path, "cast_to": cast_to, "body": body, "options": options})
        self.handed.set()
        if self.press is KeyboardInterrupt:
            raise KeyboardInterrupt
        await asyncio.sleep(30)
        raise AssertionError("never cancelled")


async def _interrupt(press: type[BaseException], pending: _Pending, asking: Any) -> None:
    """Await `asking` until `press` ends it, as `tests/test_decision.py` does."""
    if press is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            await asking
        return
    task = asyncio.ensure_future(asking)
    await asyncio.wait_for(pending.handed.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("press", [KeyboardInterrupt, asyncio.CancelledError])
async def test_a_turn_cut_off_while_its_frame_is_out_keeps_it_on_the_record(
    press: type[BaseException], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = _Pending(press)
    llm, _fake = _seeing(monkeypatch, fake=pending)
    stepper = _stepper(llm, frames_on=True)
    snapshot = _snapshot(tmp_path)
    await _interrupt(press, pending, _turn(stepper, snapshot))
    cut = stepper.cut_off
    assert cut is not None and cut.gate == "interrupted"
    assert cut.record["frame"]["kept"] is True
    assert cut.record["frame"]["sha256"] == hashlib.sha256(snapshot.path.read_bytes()).hexdigest()
    assert stepper.frames_sent == 1


async def test_the_turns_that_ask_nothing_send_and_keep_no_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm, fake = _seeing(monkeypatch)
    stepper = _stepper(llm, frames_on=True)
    snapshot = _snapshot(tmp_path)
    obs = SimpleNamespace(features={}, cameras=["camera"])
    stepper.early.clear()  # before a verdict, nothing on this stepper is on offer
    nothing = await stepper.advise(obs, cleared=False, budget="b", snapshot=snapshot)  # type: ignore[arg-type]
    stepper.goal = "x" * 40_000
    too_long = await stepper.advise(obs, cleared=True, budget="b", snapshot=snapshot)  # type: ignore[arg-type]
    assert (nothing.gate, too_long.gate) == ("not_offered", "state_too_large")
    assert nothing.record["frame"] is None and too_long.record["frame"] is None
    assert fake.posts == [] and stepper.frames_sent == 0
    assert not snapshot.path.exists()


async def test_a_stepper_built_without_frames_never_looks_at_the_picture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every record of a run that shows no frame is the record it was: no `frame` key at all."""
    llm, fake = _seeing(monkeypatch)
    stepper = _stepper(llm, frames_on=False)
    snapshot = _snapshot(tmp_path)
    advice = await _turn(stepper, snapshot)
    assert "frame" not in advice.record and "frame_skipped" not in advice.record
    assert isinstance(fake.posts[0]["body"]["input"], str)
    assert not snapshot.path.exists()
    summary = stepper.summary()
    assert (summary["frames"], summary["frames_sent"]) == (False, 0)


async def test_a_stepper_put_together_by_hand_shows_a_plugin_nothing(tmp_path: Path) -> None:
    """`frames` set by hand on a stepper whose decision LLM is somebody else's: the gate inside
    the turn says no, the plugin is asked with the two arguments it takes, and nothing is kept,
    counted or sent."""
    plugin = _Plugin()
    stepper = _stepper(plugin, frames_on=True)
    snapshot = _snapshot(tmp_path)
    advice = await _turn(stepper, snapshot)
    assert advice.gate == "taken" and plugin.calls, "the plugin was not asked as it asks"
    assert advice.record["frame"] is None
    assert advice.record["frame_skipped"] == "the decision LLM may not be shown the camera"
    assert "picture" not in plugin.calls[0]
    assert not snapshot.path.exists() and stepper.frames_sent == 0


# ── building one ────────────────────────────────────────────────────────────────────────


def _build(llm: Any, *, frames_on: bool) -> Stepper:
    return Stepper.build(
        mode="shadow",
        llm=llm,
        price=resolve_decision_price(OPENAI),
        registry=default_registry(),
        allow=["stop"],
        goal="a goal",
        frames=frames_on,
    )


def test_a_stepper_that_shows_frames_is_built_only_for_a_client_that_may_be_shown_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeing, _fake = _seeing(monkeypatch)
    assert _build(seeing, frames_on=True).frames is True
    blind, _fake = _seeing(monkeypatch, frames_on=False)
    for llm in (blind, _Plugin(), fake_systemone.FakeDecisionLLM(name="kev", model="kev-latest")):
        with pytest.raises(ValueError, match="may not"):
            _build(llm, frames_on=True)
        assert _build(llm, frames_on=False).frames is False


# ── the run ─────────────────────────────────────────────────────────────────────────────


def _lookout() -> Any:
    from quackd.duckfile.parser import load_duck

    return load_duck(str(DUCKS / "lerobot-lookout.duck"))


async def _run(tmp_path: Path, llm: Any, *, frames_on: bool) -> Any:
    """`lerobot-lookout` on the mock arm, which has a camera, with the scripted pilot and the
    stepper in shadow mode, so it is asked on every turn and nothing it says moves the arm."""
    from quackd.agent.loop import RunConfig, run_duck
    from quackd.agent.providers.fake import FakeProvider
    from quackd_lerobot import LeRobotAdapter
    from quackd_lerobot.mock import LeRobotMock

    duck = _lookout()
    return await run_duck(
        RunConfig(
            duck=duck,
            provider=FakeProvider.for_duck(duck.name),
            transport=LeRobotAdapter(LeRobotMock()),
            runs_dir=tmp_path,
            decision="shadow",
            decision_llm=llm,
            decision_price=resolve_decision_price(OPENAI),
            decision_frames=frames_on,
        )
    )


def _events(run_dir: Path, kind: str) -> list[dict[str, Any]]:
    from quackd.agent.transcript import Transcript

    return [e for e in Transcript.read(run_dir / "transcript.jsonl") if e["kind"] == kind]


async def test_a_run_shows_one_frame_a_turn_and_keeps_each_beside_its_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm, fake = _seeing(monkeypatch)
    result = await _run(tmp_path, llm, frames_on=True)
    asked = [e for e in _events(result.run_dir, "decision") if "idle_s" in e]
    assert asked and len(asked) == len(fake.posts), "one request a turn asked"
    for event, post in zip(asked, fake.posts, strict=True):
        sent = _sent_image(post)
        block = event["frame"]
        assert block["sha256"] == hashlib.sha256(sent).hexdigest() and block["kept"] is True
        assert max(block["width"], block["height"]) <= MAX_SIDE_PX
        kept = result.run_dir / block["path"]
        assert kept.read_bytes() == sent
        number = kept.name.removesuffix("-decision.jpg")
        assert (kept.parent / f"{number}.png").exists(), "not filed under its step's number"
    kept_all = sorted((result.run_dir / "frames").glob("*-decision.jpg"))
    assert kept_all == sorted(result.run_dir / e["frame"]["path"] for e in asked)
    (start,) = _events(result.run_dir, "run_start")
    assert start["decision_llm"]["frames"] is True
    block = result.summary["decision"]
    assert (block["frames"], block["frames_sent"]) == (True, len(asked))


async def test_a_run_without_the_flag_shows_no_frame_even_to_a_client_that_could_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm, fake = _seeing(monkeypatch)
    result = await _run(tmp_path, llm, frames_on=False)
    assert fake.posts and all(isinstance(post["body"]["input"], str) for post in fake.posts)
    assert all("frame" not in event for event in _events(result.run_dir, "decision"))
    assert not list((result.run_dir / "frames").glob("*-decision.jpg"))
    (start,) = _events(result.run_dir, "run_start")
    assert start["decision_llm"]["frames"] is False
    block = result.summary["decision"]
    assert (block["frames"], block["frames_sent"]) == (False, 0)


async def test_a_run_that_asks_a_blind_decision_llm_for_frames_is_refused(tmp_path: Path) -> None:
    llm = fake_systemone.FakeDecisionLLM(name="kev", model="kev-latest")
    with pytest.raises(ValueError, match="may not"):
        await _run(tmp_path, llm, frames_on=True)
    assert llm.calls == [], "the stepper was asked"


@pytest.mark.parametrize("press", [KeyboardInterrupt, asyncio.CancelledError])
async def test_a_run_cut_off_while_a_frame_turn_is_out_keeps_one_frame_and_one_record(
    press: type[BaseException], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run ends in the middle of the first turn's request, and its record still says a frame
    left: one JPEG, one `decision` event that names it, and a summary that counts it."""
    pending = _Pending(press)
    llm, _fake = _seeing(monkeypatch, fake=pending)
    await _interrupt(press, pending, _run(tmp_path, llm, frames_on=True))
    summary, (turn,), kept = _what_the_run_left(tmp_path)
    assert turn["gate"] == "interrupted" and isinstance(turn["frame"], dict)
    assert len(kept) == 1 and hashlib.sha256(kept[0]).hexdigest() == turn["frame"]["sha256"]
    assert summary["decision"]["frames_sent"] == 1


def _what_the_run_left(runs: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[bytes]]:
    """The summary, the `decision` events and the bytes of every decision JPEG of the one run
    under `runs`, for a run that raised rather than returning a result that names its
    directory."""
    (written,) = runs.rglob("summary.json")
    kept = [path.read_bytes() for path in (written.parent / "frames").glob("*-decision.jpg")]
    summary = json.loads(written.read_text(encoding="utf-8"))
    return summary, _events(written.parent, "decision"), kept


# ── the command line ────────────────────────────────────────────────────────────────────


def _cli(tmp_path: Path, *argv: str) -> Any:
    return runner.invoke(
        app,
        [
            "--no-color",
            "run",
            "lerobot-lookout",
            "--robot",
            "lerobot:mock",
            "--llm",
            "fake",
            *argv,
            "--no-memory",
            "--no-gif",
            "--runs-dir",
            str(tmp_path),
        ],
    )


@pytest.mark.parametrize(
    ("argv", "env", "said"),
    [
        (
            ("--decision-frames",),
            "",
            "--decision-frames shows a decision LLM the camera, and none is named",
        ),
        (
            ("--decision-llm", "kev", "--decision-frames"),
            "",
            "--decision-llm kev is sent text alone: only openai may be shown a picture",
        ),
        (
            ("--decision-llm", "laya", "--decision-mode", "off", "--decision-frames"),
            "",
            "--decision-llm laya is sent text alone",
        ),
        (
            (
                "--decision-llm",
                "openai",
                "--decision-url",
                "http://127.0.0.1:9",
                "--decision-frames",
            ),
            "",
            "http://127.0.0.1:9 is not one of its hosts",
        ),
        ((), "maybe", "unknown QUACKD_DECISION_FRAMES 'maybe'"),
        ((), "1", "QUACKD_DECISION_FRAMES shows a decision LLM the camera, and none is named"),
    ],
    ids=[
        "nothing named",
        "kev",
        "laya, mode off",
        "a server of your own",
        "a typo",
        "the variable",
    ],
)
def test_frames_where_none_may_go_stop_the_run_before_anything_connects(
    argv: tuple[str, ...], env: str, said: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUACKD_DECISION_FRAMES", env)
    result = _cli(tmp_path, *argv)
    assert result.exit_code == 1, result.output
    assert said in plain_text(result.output)
    assert not list(tmp_path.iterdir()), "the run started before the refusal"


def test_no_decision_frames_beats_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUACKD_DECISION_FRAMES", "1")
    asked = _cli(tmp_path / "asked", "--decision-llm", "kev", "--decision-mode", "off")
    assert asked.exit_code == 1 and "is sent text alone" in plain_text(asked.output)
    said_no = _cli(
        tmp_path / "no", "--decision-llm", "kev", "--decision-mode", "off", "--no-decision-frames"
    )
    assert said_no.exit_code == 0, said_no.output


def test_decision_llm_off_on_the_line_turns_frames_off_and_says_nothing_of_them(
    tmp_path: Path,
) -> None:
    result = _cli(tmp_path, "--decision-llm", "off", "--decision-frames")
    assert result.exit_code == 0, result.output
    assert "--decision-frames" not in plain_text(result.output)


def test_record_never_shows_a_frame_whatever_the_environment_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`record` makes the recordings and pins the stepper off; frames, which the mode does not
    decide, are pinned off with it, so a `.env` asking for both changes nothing."""
    monkeypatch.setenv("QUACKD_DECISION_LLM", "kev")
    monkeypatch.setenv("QUACKD_DECISION_FRAMES", "1")
    result = runner.invoke(
        app,
        ["record", "hello-world", "--llm", "fake", "--gif-size", "64", "--runs-dir", str(tmp_path)],
    )
    # read, the variable would refuse this run before anything connected: kev cannot see
    assert result.exit_code == 0, result.output
    assert "is sent text alone" not in plain_text(result.output)


def test_frames_on_say_once_where_they_go_and_the_run_keeps_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI(answers=fake_api.turn("stop")))
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    result = _cli(
        tmp_path, "--decision-llm", "openai", "--decision-mode", "shadow", "--decision-frames"
    )
    assert result.exit_code == 0, result.output
    said = plain_text(result.output)
    notice = " ".join(_frames_notice("shadow", OPENAI.url).split())
    assert notice in said and said.count("--decision-frames:") == 1
    assert "https://api.openai.com" in notice and f"at most {MAX_SIDE_PX} pixels" in notice
    assert fake.posts and all(_sent_image(post) for post in fake.posts)
    (run_dir,) = tmp_path.iterdir()
    assert list((run_dir / "frames").glob("*-decision.jpg"))


def test_frames_with_the_mode_off_build_the_client_and_say_no_frame_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether a frame may go is a question of its own, so the client is built for it whatever
    the mode, and a run whose mode asks the stepper nothing says no frame will go."""
    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI(answers=fake_api.turn("stop")))
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    result = _cli(
        tmp_path, "--decision-llm", "openai", "--decision-mode", "off", "--decision-frames"
    )
    assert result.exit_code == 0, result.output
    said = plain_text(result.output)
    assert " ".join(_frames_notice("off", OPENAI.url).split()) in said
    assert fake.kwargs, "no client was built"
    assert fake.posts == []


def test_frames_with_no_sdk_run_on_without_the_stepper_and_promise_no_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no decision LLM there is nothing to show a frame to, so the run is the run it would
    have been with frames off: no line promising frames, no copy, and a config that says off."""
    import quackd.agent.loop as loop

    configs: list[Any] = []

    class Keeping(loop.AgentLoop):
        def __init__(self, cfg: Any) -> None:
            configs.append(cfg)
            super().__init__(cfg)

    monkeypatch.setattr(loop, "AgentLoop", Keeping)
    monkeypatch.setitem(sys.modules, "openai", None)
    result = _cli(tmp_path, "--decision-llm", "openai", "--decision-frames")
    assert result.exit_code == 0, result.output
    said = plain_text(result.output)
    assert "running without it" in said and "--decision-frames:" not in said
    (cfg,) = configs
    assert (cfg.decision, cfg.decision_llm, cfg.decision_frames) == ("off", None, False)
    (run_dir,) = tmp_path.iterdir()
    assert not list((run_dir / "frames").glob("*-decision.jpg"))


def test_an_address_with_a_path_stops_a_run_that_asks_only_for_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frames build the decision LLM whatever the mode, so the address it would be handed is
    checked whatever the mode too: a typo stops the run while the flags are read, on a machine
    without the SDK as on one with it."""
    monkeypatch.setitem(sys.modules, "openai", None)
    result = _cli(
        tmp_path,
        "--decision-llm",
        "openai",
        "--decision-mode",
        "off",
        "--decision-frames",
        "--decision-url",
        "https://api.openai.com/v1",
    )
    assert result.exit_code == 1, result.output
    assert "--decision-url https://api.openai.com/v1 has a path" in plain_text(result.output)
    assert not list(tmp_path.iterdir()), "the run started before the typo was noticed"


def test_a_flock_says_it_sends_no_frame(tmp_path: Path) -> None:
    """A flock builds no decision LLM, so a run that asks for one, with frames and the mode off,
    is told once that none applies and that no frame is sent."""
    result = runner.invoke(
        app,
        [
            "--no-color",
            "run",
            "flock-hello",
            "--llm",
            "fake",
            "--decision-llm",
            "openai",
            "--decision-mode",
            "off",
            "--decision-frames",
            "--no-gif",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        "--decision-llm openai does not apply to a flock: every member is piloted by its model, "
        "as before, and no camera frame is sent"
    ) in plain_text(result.output)


def test_the_flag_sits_in_the_panel_of_the_other_decision_flags() -> None:
    assert _DECISION_FRAMES.rich_help_panel == _DECISION_MODE.rich_help_panel
    from tests.conftest import help_text

    said = help_text(["run", "--help"])
    assert "--decision-frames" in said and "--no-decision-frames" in said
    assert f"at most {MAX_SIDE_PX} pixels" in said
