"""Goldens recorded from v0.3.0 before the multi-robot refactor (0.4), except the four
duck hashes regenerated in 0.6 when the solo starters gained a `remember` step.

They turn three promises into machine checks: seeded single- and multi-duck worlds are
byte-identical (poses, camera and top-down renders, RNG state), the six v0 starter ducks
are unchanged, and a `flock-kick` run on seed 3 still produces the same summary and the
same bus conversation. `sim2d_rooms.json`, recorded when explore came, holds a world laid out
in each built-in room to the same promise, and `explore_seed3.json` a `find-the-ball` run in
two-rooms on seed 3: what the scripted pilot called, what each verb said, and where the duck
and the ball ended. Regenerate ONLY on purpose:
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
from quackd.space.room import BUILT_IN, load_room

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


def room_case(room: str, seed: int) -> dict[str, Any]:
    """A world laid out in `room` for `seed`: where it puts the duck and the ball, what the
    duck's camera and the view from above show of it, and where 6 s of walking, a left turn and
    6 s more leave the duck, with the bumps it counted against the room's walls on the way, which
    every built-in room makes it meet."""
    w = World(seed=seed, room=load_room(room))
    rec: dict[str, Any] = {
        "room": room,
        "seed": seed,
        "t0": _poses(w),
        "cam0": _sha(render_duckcam(w, 256).tobytes()),
        "top0": _sha(render_topdown(w, 256).tobytes()),
    }
    for vx, wz, seconds in ((0.2, 0.0, 6.0), (0.0, 1.0, 1.6), (0.2, 0.0, 6.0)):
        for k in range(round(seconds / DT)):
            if k % 4 == 0:  # re-issue inside the deadman window, like the walk verb does
                w.set_velocity(vx, 0.0, wz)
            w.step(DT)
    w.stop()
    rec["t14"] = _poses(w)
    rec["bumps"] = w.ducks[0].bumps
    rec["cam14"] = _sha(render_duckcam(w, 256).tobytes())
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


def explore_golden(seed: int, runs_dir: Path) -> dict[str, Any]:
    """`find-the-ball` in two-rooms on `seed` with the scripted pilot: the verbs it called, in
    order, whether each worked and what it said, and where the duck and the ball ended, to the
    centimetre."""
    from quackd.adapters.factory import make_adapter
    from quackd.agent.loop import RunConfig, RunResult, run_duck
    from quackd.agent.providers.fake import FakeProvider
    from quackd.duckfile.parser import load_duck
    from quackd.perception.color_blob import ColorBlobDetector
    from quackd.safety import allow_all

    async def go() -> tuple[RunResult, list[float], list[float]]:
        adapter = make_adapter("microduck:sim2d", seed=seed, room="two-rooms")
        try:
            result = await run_duck(
                RunConfig(
                    duck=load_duck("find-the-ball"),
                    provider=FakeProvider.for_duck("find-the-ball"),
                    transport=adapter,
                    detector=ColorBlobDetector(),
                    runs_dir=runs_dir,
                    confirm=allow_all,
                )
            )
            world = adapter.transport.world
            duck = world.ducks[0]
            return (
                result,
                [round(duck.x, 2), round(duck.y, 2), round(duck.theta, 2)],
                [round(world.ball.x, 2), round(world.ball.y, 2)],
            )
        finally:
            await adapter.close()

    result, duck, ball = asyncio.run(asyncio.wait_for(go(), timeout=120))
    with (result.run_dir / "transcript.jsonl").open(encoding="utf-8") as fh:
        events = [json.loads(line) for line in fh if line.strip()]
    verbs = [
        [event["name"], event["ok"], event["summary"]]
        for event in events
        if event.get("kind") == "verb_end" and not event.get("nested")
    ]
    return {"seed": seed, "outcome": result.outcome, "verbs": verbs, "duck": duck, "ball": ball}


def _load(name: str) -> Any:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def test_sim2d_worlds_are_byte_identical() -> None:
    for want in _load("sim2d_worlds.json"):
        got = sim_case(want["seed"], want["n_ducks"])
        assert got == want, f"seed {want['seed']} n_ducks {want['n_ducks']} drifted"


def test_worlds_laid_out_in_each_room_are_byte_identical() -> None:
    recorded = _load("sim2d_rooms.json")
    assert sorted({case["room"] for case in recorded}) == sorted(BUILT_IN)
    for want in recorded:
        assert room_case(want["room"], want["seed"]) == want, (want["room"], want["seed"])


def test_v0_starter_ducks_are_unchanged() -> None:
    assert duck_hashes() == _load("duck_hashes.json")


def test_flock_kick_seed_3_conversation_is_unchanged(tmp_path: Path) -> None:
    want = _load("flock_kick_seed3.json")
    got = flock_golden(want["seed"], tmp_path)
    assert got["summary"] == want["summary"]
    assert got["bus"] == want["bus"]


def test_find_the_ball_seed_3_is_unchanged(tmp_path: Path) -> None:
    """The whole of exploring at once, walks, looks, the camera's search and the pilot on top:
    a change to any of them that moves this run moves it on purpose."""
    want = _load("explore_seed3.json")
    assert explore_golden(want["seed"], tmp_path) == want


def test_the_plain_log_lines_are_unchanged() -> None:
    """The MCP tool result carries these bytes and a model reads them (docs/guides/mcp.md), so the
    terminal view may be redrawn but the renderer under it may not move."""
    from tests.golden.log_cases import golden

    want = _load("log_lines.json")
    got = golden()
    assert sorted(got) == sorted(want), "a case appeared or vanished: regenerate on purpose"
    for name in sorted(want):
        assert got[name] == want[name], f"{name} drifted"
