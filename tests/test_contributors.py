"""`docs/assets/contributors.py`: the image of who has contributed, and the token it sends.

The script is loaded by path and its one network call, `urllib.request.urlopen`, is replaced
by a stand-in that records each request, so nothing here reaches the network.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import urllib.request
from http.client import HTTPMessage
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "docs" / "assets" / "contributors.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("contributors", SCRIPT)
    assert spec and spec.loader, f"{SCRIPT} could not be loaded as a module"
    module = importlib.util.module_from_spec(spec)
    sys.modules["contributors"] = module
    spec.loader.exec_module(module)
    return module


class _Answer(io.BytesIO):
    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every request the script makes, answered with an empty JSON list, with a token set as
    the workflow sets one."""
    seen: list[Any] = []

    def urlopen(request: Any, timeout: float) -> _Answer:
        seen.append(request)
        return _Answer(b"[]")

    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com.example.org/x",
        "https://example.org/?u=api.github.com",
        "https://api.github.com@example.org/x",
        "https://example.org/api.github.com/x",
        "http://api.github.com/repos/someone/project",
    ],
)
def test_the_token_goes_to_no_address_that_only_mentions_the_api(
    requests: list[Any], url: str
) -> None:
    """The token went to any URL with `api.github.com` anywhere in its text, an avatar's
    address included, so a host that put those words in its name, its path or its query
    would have been handed the workflow's token (code scanning alert 3). It goes only over
    https to the API's own host now."""
    script = _script()
    script._get(url)
    (request,) = requests
    assert not request.has_header("Authorization"), url


def test_the_token_goes_to_the_api_and_not_on_to_where_it_redirects(requests: list[Any]) -> None:
    """urllib copies a header added with `add_header` onto the request a redirect makes, to
    whatever host it names, so the API's token would have followed a redirect anywhere: it goes
    on the API's request alone."""
    script = _script()
    script._get("https://api.github.com/repos/someone/project/contributors")
    (request,) = requests
    assert request.get_header("Authorization") == "Bearer t0ken"
    moved = urllib.request.HTTPRedirectHandler().redirect_request(
        request, io.BytesIO(), 302, "Found", HTTPMessage(), "https://avatars.example.org/u/1"
    )
    assert moved is not None and not moved.has_header("Authorization")


def test_the_repository_is_the_one_the_workflow_runs_in_and_the_constant_by_hand(
    requests: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slug was a constant, so the day the repository is renamed the workflow would have
    asked the API about the old name. A run in a workflow names its repository in
    `GITHUB_REPOSITORY`; a person running the script by hand has none, and gets the constant."""
    script = _script()
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/renamed")
    script.email_to_login()
    assert requests[-1].full_url.startswith("https://api.github.com/repos/someone/renamed/commits?")

    monkeypatch.delenv("GITHUB_REPOSITORY")
    script.email_to_login()
    assert requests[-1].full_url.startswith(f"https://api.github.com/repos/{script.REPO}/commits?")
