"""Token Factory as the edition reaches it: the key, the live list, and what it makes of both."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fake_tokenfactory import OTHER_MODEL, TEXT_MODEL, VISION_MODEL, FakeTokenFactory, row
from passport_nebius import nebius
from passport_nebius.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_no_key_is_refused_by_name() -> None:
    with pytest.raises(nebius.NebiusError, match="NEBIUS_API_KEY"):
        nebius.Settings.from_env()


def test_the_address_is_tidied_and_must_be_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", "k")
    assert (
        nebius.Settings.from_env("https://example.test/v1/").base_url == "https://example.test/v1"
    )
    assert nebius.Settings.from_env().base_url == nebius.DEFAULT_BASE_URL
    with pytest.raises(nebius.NebiusError, match="not an http"):
        nebius.Settings.from_env("ftp://example.test")


def test_the_key_never_shows_in_a_repr() -> None:
    assert "secret" not in repr(nebius.Settings("https://example.test", "secret"))


def test_the_list_is_read_verbose_with_the_key(tokenfactory: FakeTokenFactory) -> None:
    models = nebius.list_models(nebius.Settings.from_env())
    assert [m.id for m in models] == [TEXT_MODEL, VISION_MODEL, OTHER_MODEL]
    (request,) = tokenfactory.requests
    assert request.headers["authorization"] == "Bearer stand-in-key"
    assert request.query == {"verbose": ["true"]}


def test_what_the_list_says_and_what_it_does_not() -> None:
    text, vision = (
        nebius._model(row(TEXT_MODEL)),
        nebius._model(row(VISION_MODEL, modality="text+image->text")),
    )
    bare = nebius._model({"id": "nvidia/plain", "object": "model"})
    assert text is not None and vision is not None and bare is not None
    assert (text.takes_images, vision.takes_images, bare.takes_images) == (False, True, None)
    assert text.features == ("tools",) and bare.features == ()
    assert bare.context_length is None and bare.modality is None
    assert nebius._model({"object": "model"}) is None
    # an image on the output side is not an image read
    assert nebius._model(row("nvidia/painter", modality="text->image")).takes_images is False  # type: ignore[union-attr]


def test_a_wrong_key_is_said_to_be_the_key(tokenfactory: FakeTokenFactory) -> None:
    with pytest.raises(nebius.NebiusError, match="refused the key"):
        nebius.list_models(nebius.Settings(tokenfactory.base_url, "not-the-key"))


def test_an_unreachable_server_is_said_to_be_unreachable() -> None:
    with pytest.raises(nebius.NebiusError, match="could not reach"):
        nebius.list_models(nebius.Settings("http://127.0.0.1:9/v1", "k"), timeout=2)


def test_only_an_nvidia_id_spelled_as_the_list_spells_it_is_taken() -> None:
    models = [m for m in map(nebius._model, [row(TEXT_MODEL), row(OTHER_MODEL)]) if m]
    assert nebius.resolve(models, TEXT_MODEL).id == TEXT_MODEL
    with pytest.raises(nebius.NebiusError, match=f"spells it {TEXT_MODEL}"):
        nebius.resolve(models, TEXT_MODEL.upper())
    with pytest.raises(nebius.NebiusError, match="not an NVIDIA model"):
        nebius.resolve(models, OTHER_MODEL)
    with pytest.raises(nebius.NebiusError, match=f"NVIDIA models there: {TEXT_MODEL}"):
        nebius.resolve(models, "nvidia/not-there")


def test_thinking_is_dropped_and_json_found() -> None:
    assert nebius.strip_thinking("<think>\nhmm\n</think>\n\nAnswer.") == "Answer."
    assert nebius.json_in('Here:\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    with pytest.raises(nebius.NebiusError, match="no JSON object"):
        nebius.json_in("no braces at all")
    with pytest.raises(nebius.NebiusError, match="does not parse"):
        nebius.json_in('{"a": ')


def test_a_refused_format_falls_back_and_the_answer_is_still_read() -> None:
    sent: list[Any] = []

    def chat(n: int, body: dict[str, Any]) -> tuple[int, Any]:
        sent.append(body.get("response_format"))
        if n == 1:
            return 400, {"detail": "json_schema is not supported for this model"}
        return 200, {
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '<think>x</think>{"ok": true}'},
                }
            ],
        }

    with FakeTokenFactory(chat=chat) as stand_in:
        settings = nebius.Settings(stand_in.base_url, "stand-in-key")
        text = nebius.ask(
            settings, TEXT_MODEL, [{"role": "user", "content": "hi"}], schema={"type": "object"}
        )
    assert json.loads(text) == {"ok": True}
    assert [f["type"] if f else None for f in sent] == ["json_schema", "json_object"]


def test_models_lists_nvidia_by_default(tokenfactory: FakeTokenFactory) -> None:
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0, result.output
    assert TEXT_MODEL in result.output and VISION_MODEL in result.output
    assert OTHER_MODEL not in result.output
    assert "2 shown of 3 on the list" in result.output


def test_models_all_and_json(tokenfactory: FakeTokenFactory) -> None:
    assert OTHER_MODEL in runner.invoke(app, ["models", "--all"]).output
    rows = json.loads(runner.invoke(app, ["models", "--json"]).output)
    assert [(r["id"], r["takes_images"]) for r in rows] == [
        (TEXT_MODEL, False),
        (VISION_MODEL, True),
    ]


def test_models_without_a_key_says_which_variable() -> None:
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 1
    assert "NEBIUS_API_KEY" in result.output


def test_the_sdks_own_variables_never_reach_token_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gateway's credential, an organisation and a project the OpenAI SDK reads from its own
    variables would ride along on a client built plainly; this one sends the Nebius key alone."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer gateway-secret\nX-Gw: 1")
    monkeypatch.setenv("OPENAI_ORG_ID", "org-elsewhere")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-elsewhere")
    with FakeTokenFactory(answers=["fine"]) as stand_in:
        settings = nebius.Settings(stand_in.base_url, "stand-in-key")
        assert nebius.ask(settings, TEXT_MODEL, [{"role": "user", "content": "hi"}]) == "fine"
    (post,) = stand_in.posts()
    assert post.headers["authorization"] == "Bearer stand-in-key"
    for name in ("openai-organization", "openai-project", "x-gw"):
        assert name not in post.headers
    assert "gateway-secret" not in json.dumps(post.headers)


def test_a_redirect_is_refused_rather_than_followed_with_the_key() -> None:
    with (
        FakeTokenFactory(key=None) as elsewhere,
        FakeTokenFactory(redirect_to=f"{elsewhere.base_url}/models") as stand_in,
    ):
        settings = nebius.Settings(stand_in.base_url, "stand-in-key")
        with pytest.raises(nebius.NebiusError, match="redirect"):
            nebius.list_models(settings)
    assert elsewhere.requests == [], "the key went where the redirect pointed"
