"""Goldens recorded from v0.3.0 before the multi-robot refactor (0.4), except the four
duck hashes regenerated in 0.6 when the solo starters gained a `remember` step.

They turn three promises into machine checks: seeded single- and multi-duck worlds are
byte-identical (poses, camera and top-down renders, RNG state), the six v0 starter ducks
are unchanged, and a `flock-kick` run on seed 3 still produces the same summary and the
same bus conversation. Regenerate ONLY on purpose:
`uv run python -m tests.golden.generate` (and say why in the commit).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from quackd.sim2d.render import render_duckcam, render_topdown
from quackd.sim2d.world import DT, World

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden"
V0_DUCKS = ("hello-world", "find-and-kick", "patrol-and-quack", "follow-me", "fetch", "flock-kick")
FLOCK_SUMMARY_KEYS = ("outcome", "kicker", "ball_displacement_m", "bus_messages", "auctions")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _poses(w: World) -> dict[str, Any]:
    return {
        "t": round(w.t, 4),
        "ducks": [[round(d.x, 6), round(d.y, 6), round(d.theta, 6), d.posture] for d in w.ducks],
        "ball": [round(w.ball.x, 6), round(w.ball.y, 6)],
        "people": [[round(p.x, 6), round(p.y, 6)] for p in w.people],
        "ball_displacement_m": round(w.ball_displacement_m, 6),
    }


def sim_case(seed: int, n_ducks: int) -> dict[str, Any]:
    """A scripted 5 s of motion, a kick, 2 s of settling: everything a seed determines."""
    w = World(seed=seed, n_ducks=n_ducks)
    rec: dict[str, Any] = {
        "seed": seed,
        "n_ducks": n_ducks,
        "t0": _poses(w),
        "cam0": [_sha(render_duckcam(w, 256, duck_index=i).tobytes()) for i in range(n_ducks)],
        "top0": _sha(render_topdown(w, 256).tobytes()),
    }
    for k in range(round(5.0 / DT)):
        if k % 4 == 0:  # re-issue inside the deadman window, like the walk verb does
            for i in range(n_ducks):
                w.set_velocity(0.15, 0.0, 0.4 * (i + 1), duck_index=i)
        w.step(DT)
    for i in range(n_ducks):
        w.stop(duck_index=i)
    w.kick("right", duck_index=0)
    for _ in range(round(2.0 / DT)):
        w.step(DT)
    rec["t7"] = _poses(w)
    rec["cam7"] = [_sha(render_duckcam(w, 256, duck_index=i).tobytes()) for i in range(n_ducks)]
    rec["top7"] = _sha(render_topdown(w, 256).tobytes())
    rec["rng"] = json.loads(json.dumps(w.rng.bit_generator.state))
    return rec


def duck_hashes() -> dict[str, str]:
    return {name: _sha((REPO / "ducks" / f"{name}.duck").read_bytes()) for name in V0_DUCKS}


def bus_kinds(events: list[dict[str, Any]]) -> list[list[str]]:
    """The conversation, as (message kind, sender) pairs in transcript order."""
    out = []
    for ev in events:
        if ev.get("kind") != "bus":
            continue
        msg = ev.get("msg")
        kind = msg.get("kind") if isinstance(msg, dict) else ev.get("msg_kind")
        out.append([str(kind), str(ev.get("src", msg.get("src") if isinstance(msg, dict) else ""))])
    return out


def flock_golden(seed: int, runs_dir: Path) -> dict[str, Any]:
    from quackd.agent.providers.fake import FakeProvider
    from quackd.duckfile.parser import load_duck
    from quackd.flock.runner import run_flock

    result = asyncio.run(
        asyncio.wait_for(
            run_flock(
                load_duck("flock-kick"),
                provider=FakeProvider.for_duck("flock-kick"),
                seed=seed,
                runs_dir=runs_dir,
            ),
            timeout=120,
        )
    )
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    with (result.run_dir / "flock.jsonl").open(encoding="utf-8") as fh:
        events = [json.loads(line) for line in fh if line.strip()]
    picked = {k: summary.get(k) for k in FLOCK_SUMMARY_KEYS if k in summary}
    picked["outcome"] = result.outcome
    picked["ball_displacement_m"] = round(result.ball_displacement_m, 4)
    return {"seed": seed, "summary": picked, "bus": bus_kinds(events)}


def _load(name: str) -> Any:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def test_sim2d_worlds_are_byte_identical() -> None:
    for want in _load("sim2d_worlds.json"):
        got = sim_case(want["seed"], want["n_ducks"])
        assert got == want, f"seed {want['seed']} n_ducks {want['n_ducks']} drifted"


def test_v0_starter_ducks_are_unchanged() -> None:
    assert duck_hashes() == _load("duck_hashes.json")


def test_flock_kick_seed_3_conversation_is_unchanged(tmp_path: Path) -> None:
    want = _load("flock_kick_seed3.json")
    got = flock_golden(want["seed"], tmp_path)
    assert got["summary"] == want["summary"]
    assert got["bus"] == want["bus"]


def test_the_plain_log_lines_are_unchanged() -> None:
    """The MCP tool result carries these bytes and a model reads them (docs/guides/mcp.md), so the
    terminal view may be redrawn but the renderer under it may not move."""
    from tests.golden.log_cases import golden

    want = _load("log_lines.json")
    got = golden()
    assert sorted(got) == sorted(want), "a case appeared or vanished: regenerate on purpose"
    for name in sorted(want):
        assert got[name] == want[name], f"{name} drifted"
