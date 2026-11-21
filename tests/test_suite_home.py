"""Every road from the suite to a home folder ends in the suite's own scratch folder.

Roboglot and the libraries it drives keep files under the home of whoever runs them: memory,
the registry, a robot's onboard keys, the sim radio's air, the policy server's token, Hugging
Face's token and caches. A test that reaches one of those writes, or reads, the contributor's
own. The conftest moves some of them, one variable at a time, and the rest are reached through
the home itself, which nothing moves yet.

Each row below is one road, as a survey of the code's roads to the home found them, resolved
the way the code resolves it, at the time a test would call it. The rows the conftest already
redirects pass. The rows that go through the home itself are marked `xfail(strict=True)`: they
fail today, which is the need this file proves, and they keep failing until an autouse fixture
in `tests/conftest.py` moves `HOME` and `USERPROFILE` into the base temporary folder for every
test. That fixture is not written yet. The day it lands, each of those rows passes, strict xfail
turns the pass into a failure, and the mark comes off with it.

Two roads are not rows. git reads `~/.gitconfig`, and a moved home must keep reading the real
one (`GIT_CONFIG_GLOBAL`), or a checkout owned by another user reads as dubious, which is for
the fixture to keep. `APPDATA`, `LOCALAPPDATA`, `platformdirs` and `click.get_app_dir` are read
by nothing here.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

MOVED_IN_THE_SECOND_PART = pytest.mark.xfail(
    strict=True,
    reason=(
        "reached through the home, which no fixture moves yet: the autouse fixture in "
        "tests/conftest.py that moves HOME and USERPROFILE for every test makes this pass, "
        "and this mark goes with it"
    ),
)


def _memory() -> Path:
    from roboglot.memory import memory_dir

    return memory_dir()


def _registry() -> Path:
    from roboglot.registry import registry_dir

    return registry_dir()


def _onboard() -> Path:
    from roboglot.onboard.store import onboard_dir

    return onboard_dir()


def _hugging_face_token() -> Path:
    from roboglot_lerobot.policy.endpoint import token_path

    return token_path()


def _lerobot_calibration() -> Path:
    return Path(os.environ["HF_LEROBOT_CALIBRATION"])


def _sim_air() -> Path:
    from roboglot.radio.sim import default_air_dir

    return default_air_dir()


def _policy_token() -> Path:
    """The file `policy serve` writes a token into and the client reads one from, given neither
    a token nor a file: both expand `DEFAULT_TOKEN_FILE` when they are called."""
    from roboglot_lerobot.policy import protocol

    return Path(os.path.expanduser(protocol.DEFAULT_TOKEN_FILE))


def _ui_token_folder() -> Path:
    """Where `roboglot ui` keeps its token when the registry folder came from a `.env`: the
    registry's default, under the home, on purpose."""
    from roboglot.registry import DEFAULT_DIR

    return Path(DEFAULT_DIR).expanduser()


def _home() -> Path:
    """The home itself, as `Path.home()` gives it: what `roboglot`'s `_home_relative` measures a
    printed path against, and what Streamlit's settings (the ui job) are kept under."""
    return Path.home()


def _expanded_home() -> Path:
    """The home as `~` expands: where matplotlib's and torch's caches go (the policy job) unless
    their own variables say otherwise, which the suite does not set."""
    return Path(os.path.expanduser("~"))


def _moss_runs() -> Path:
    from tests.support import load_script

    bridge = load_script(REPO / "bridge" / "moss" / "roboglot_moss_bridge.py", "moss_bridge_home")
    return Path(bridge.runs_dir())


def _duck_config() -> Path:
    """The body's own config file, read by the Open Duck bridge and camera daemon: their
    `--duck-config` default, expanded as they expand it."""
    return Path(os.path.expanduser("~/duck_config.json"))


ROADS: list[tuple[str, Callable[[], Path], bool]] = [
    # (the road, how the code resolves it, whether the conftest redirects it today)
    ("memory", _memory, True),
    ("registry", _registry, True),
    ("onboard", _onboard, True),
    ("hugging-face-token", _hugging_face_token, True),
    ("lerobot-calibration", _lerobot_calibration, True),
    ("sim-air", _sim_air, False),
    ("policy-token", _policy_token, False),
    ("ui-token-folder", _ui_token_folder, False),
    ("moss-runs", _moss_runs, False),
    ("duck-config", _duck_config, False),
    ("home", _home, False),
    ("expanded-home", _expanded_home, False),
]


@pytest.mark.parametrize(
    "resolve",
    [
        pytest.param(resolve, id=name, marks=() if redirected else MOVED_IN_THE_SECOND_PART)
        for name, resolve, redirected in ROADS
    ],
)
def test_the_road_ends_in_the_suites_scratch_folder(
    resolve: Callable[[], Path], tmp_path_factory: pytest.TempPathFactory
) -> None:
    base = tmp_path_factory.getbasetemp().resolve()
    where = resolve().resolve()
    assert where.is_relative_to(base), f"{where} is outside the suite's scratch folder {base}"
