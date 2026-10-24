"""Agile ONE SN as a mock body: quackd finds it the way it finds any third party's robot, its
passport says what Agile Robots published and nothing more, and its two verbs move a letter."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fake_tokenfactory import TEXT_MODEL, FakeTokenFactory
from passport_nebius import agile_one_sn, doorstep
from passport_nebius.cli import app
from typer.testing import CliRunner

from quackd.verbs.registry import NoParams, VerbContext

runner = CliRunner()


def test_quackd_finds_it_through_the_entry_point() -> None:
    from quackd.adapters import factory

    assert "agile_one_sn" in factory.adapter_names()
    info = factory.info("agile_one_sn")
    assert info.backends == ("mock",)
    assert "NVIDIA Isaac GR00T" in info.summary


def test_its_datasheet_is_what_agile_robots_published() -> None:
    manifest = agile_one_sn.describe("mock")
    sheet = manifest.datasheet
    assert sheet is not None
    assert (manifest.embodiment, sheet.manipulator, sheet.arms) == ("humanoid", "arms", 2)
    published = {
        "mass_kg": 43.0,
        "height_m": 1.4,
        "dof": 71,
        "payload_kg": 3.0,
    }
    for name, value in published.items():
        figure = getattr(sheet, name)
        assert (figure.value, figure.confidence, figure.source) == (
            value,
            "official",
            agile_one_sn.SOURCE,
        )
    assert sheet.reach_m is None and sheet.endurance_min is None, "not published, so not said"
    assert [v.name for v in manifest.verbs] == [
        "report_state",
        "stop",
        "hold_out_letter",
        "lay_letter_down",
    ]
    with pytest.raises(ValueError, match="choose one of"):
        agile_one_sn.describe("real")


def test_its_passport_carries_the_figures_with_their_source(tmp_path: Path) -> None:
    shown = doorstep.quackd(
        "passport",
        "show",
        "--robot",
        "agile_one_sn:mock",
        "--json",
        "--registry-dir",
        str(tmp_path),
    )
    assert shown.returncode == 0, shown.stderr
    body = json.loads(shown.stdout)["body"]
    assert body["payload_kg"] == [3.0, "official", agile_one_sn.SOURCE]
    assert (body["embodiment"], body["manipulator"]) == ("humanoid", "arms")


def _letter_after(*verbs: str, dry_run: bool = False) -> tuple[list[bool], str, bool]:
    async def play() -> tuple[list[bool], str, bool]:
        adapter = agile_one_sn.make("mock")
        await adapter.connect()
        ctx = VerbContext(transport=adapter, dry_run=dry_run)  # type: ignore[arg-type]
        results = []
        for name in verbs:
            verb = agile_one_sn.implementations()[name]
            results.append((await verb.execute(ctx, NoParams())).ok)
        state = await adapter.get_state()
        return results, state.extras["letter"], state.holding

    return asyncio.run(play())


def test_the_letter_goes_where_the_verbs_put_it() -> None:
    assert _letter_after() == ([], agile_one_sn.IN_HAND, True)
    assert _letter_after("hold_out_letter") == ([True], agile_one_sn.HELD_OUT, True)
    assert _letter_after("lay_letter_down") == ([True], agile_one_sn.ON_FLOOR, False)
    # once it lies on the floor, it is no longer in a hand to hold out
    assert _letter_after("lay_letter_down", "hold_out_letter") == (
        [True, False],
        agile_one_sn.ON_FLOOR,
        False,
    )
    assert _letter_after("lay_letter_down", dry_run=True) == ([True], agile_one_sn.IN_HAND, True)


def test_a_real_quackd_run_lays_the_letter_down(
    tokenfactory: FakeTokenFactory, tmp_path: Path
) -> None:
    """quackd's own run and executor on this body, piloted through the stand-in."""
    tokenfactory.script = [
        ("assess_task", {"verdict": "feasible", "reason": "a letter weighs next to nothing"}),
        ("lay_letter_down", {}),
        ("declare_success", {"reason": "the letter lies on the floor"}),
    ]
    runs = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "pilot",
            "--model",
            TEXT_MODEL,
            "--goal",
            "Lay the letter on the floor in front of you, then declare success.",
            "--robot",
            "agile_one_sn:mock",
            "--runs-dir",
            str(runs),
            "--no-memory",
        ],
    )
    assert result.exit_code == 0, result.output
    names = {t["function"]["name"] for t in tokenfactory.posts()[0].body["tools"]}
    assert {"hold_out_letter", "lay_letter_down"} <= names
    (transcript,) = runs.glob("*/transcript.jsonl")
    assert agile_one_sn.ON_FLOOR in transcript.read_text(encoding="utf-8")
