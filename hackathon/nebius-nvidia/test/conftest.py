"""Every test runs keyless, offline, and away from the developer's own quackd registry."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_tokenfactory import ONBOARD_KEY, ONBOARD_MODEL, FakeTokenFactory, served

#: Settings a developer's shell or .env could carry into a test and change what it proves.
_SCRUBBED = (
    "NEBIUS_API_KEY",
    "NEBIUS_BASE_URL",
    "NEBIUS_MODEL",
    "ONBOARD_API_KEY",
    "LOCAL_API_KEY",
    "QUACKD_BASE_URL",
    "QUACKD_LLM",
    "QUACKD_PRICE",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_CUSTOM_HEADERS",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _SCRUBBED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QUACKD_REGISTRY_DIR", str(tmp_path / "registry"))
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def tokenfactory(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTokenFactory]:
    """The stand-in, running, with the key and address set the way a person would set them."""
    with FakeTokenFactory() as stand_in:
        monkeypatch.setenv("NEBIUS_API_KEY", "stand-in-key")
        monkeypatch.setenv("NEBIUS_BASE_URL", stand_in.base_url)
        yield stand_in


@pytest.fixture
def onboard(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTokenFactory]:
    """A robot's own model server, running, with its key set the way a person would set it."""
    with FakeTokenFactory(key=ONBOARD_KEY, models=[served(ONBOARD_MODEL)]) as stand_in:
        monkeypatch.setenv("ONBOARD_API_KEY", ONBOARD_KEY)
        yield stand_in
