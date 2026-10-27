"""Runs: every run and chat session in the working folder's `runs/`, newest first, read from disk.

A run is told as `quackd log` tells it, in the terminal's own words, with its frames, its GIF on a
simulator and what its terminal showed; a chat session lists its goals, each a run of its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from quackd.webui import launch, widgets
from quackd.webui.state import setup

SHOWN = 200
"""The newest runs the list holds."""


def _runs_dir() -> Path:
    folder = launch.CONFIG.folder if launch.CONFIG is not None else Path.cwd()
    chosen = setup().get("runs_dir") or "runs"
    path = Path(chosen)
    return path if path.is_absolute() else folder / path


def _summary(run: Path) -> dict[str, Any]:
    try:
        return json.loads((run / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def screen() -> None:
    st.title("Runs")
    runs = _runs_dir()
    found = (
        sorted(
            (p for p in runs.iterdir() if (p / "transcript.jsonl").is_file()),
            key=lambda p: p.name,
            reverse=True,
        )[:SHOWN]
        if runs.is_dir()
        else []
    )
    if not found:
        st.info(f"No runs yet in {runs}.")
        return
    rows = []
    for run in found:
        summary = _summary(run)
        rows.append(
            {
                "run": run.name,
                "outcome": summary.get("outcome", "going, or ended early"),
                "reason": summary.get("reason", ""),
                "steps": summary.get("steps"),
                "seconds": summary.get("wall_s"),
                "cost": summary.get("cost_usd"),
            }
        )
    st.dataframe(rows, hide_index=True, width="stretch")
    choice = widgets.pick("_history_run", "Show", [r.name for r in found])
    run = runs / choice
    goals = sorted((run / "goals").glob("*/transcript.jsonl")) if (run / "goals").is_dir() else []
    if goals:
        names = ["the session", *[g.parent.name for g in goals]]
        which = widgets.pick("_history_goal", "Of this chat", names)
        if which != "the session":
            run = run / "goals" / which
    _show(run)


def _show(run: Path) -> None:
    from quackd.agent.transcript import Transcript
    from quackd.log import LineLog, LogEvent

    summary = _summary(run)
    if summary:
        st.caption(
            " · ".join(
                f"{k} {summary[k]}"
                for k in ("outcome", "reason", "steps", "llm_calls", "wall_s", "cost_usd")
                if summary.get(k) is not None
            )
        )
    shown = st.toggle(
        "Show the system prompt", value=False, help="As quackd log --prompt shows it."
    )
    made: list[str] = []
    renderer = LineLog(lambda text, _style: made.append(text), prompt=shown)
    for record in Transcript.read(run / "transcript.jsonl", lenient=True):
        data = {k: v for k, v in record.items() if k not in ("t", "kind")}
        renderer(LogEvent(kind=str(record.get("kind")), t=float(record.get("t") or 0), data=data))
    renderer.flush()
    with st.container(height=480):
        st.code("\n".join(made), language=None, wrap_lines=True)
    gif = run / "run.gif"
    if gif.is_file():
        st.image(str(gif), caption="run.gif")
    frames = sorted((run / "frames").glob("*.png"))[-8:] if (run / "frames").is_dir() else []
    if frames:
        st.image([str(f) for f in frames], caption=[f.stem for f in frames], width=160)
    terminal = run / "terminal.txt"
    if terminal.is_file():
        with st.expander("What its terminal showed"):
            st.code(terminal.read_text(encoding="utf-8", errors="replace"), language=None)
    st.caption(f"Folder: {run}")
