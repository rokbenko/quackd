"""The run logs under `docs/examples/lerobot/`, which `scripts/example_log.py` writes.

Every log must read the same as every other but for what its run recorded and what somebody wrote
about it, so the tests that matter most compare what is committed with what the script writes.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent


def _script() -> ModuleType:
    """`scripts/example_log.py`, loaded by path as `tests/test_docs.py` loads its neighbours."""
    spec = importlib.util.spec_from_file_location(
        "example_log", REPO / "scripts" / "example_log.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _script()

GOOD = {
    "version": "0.31.1",
    "outcome": "success",
    "reason": "Swung it.",
    "steps": 4,
    "llm_calls": 6,
    "started_at": "2026-11-04T11:44:16.998Z",
    "wall_s": 49.547,
    "connect_s": 10.531,
    "llm_latency_s": 20.432,
    "first_motion_s": 24.453,
    "usage": {"input_tokens": 25166, "output_tokens": 470},
    "cost_usd": 0.027064,
    "provider": "openai",
    "model": "gpt-6-sol",
    "transport": "real",
    "robot": "arm-01",
    "final_state": {"extras": {"step_deg": 5.0}},
}
"""A run's summary.json in the shape a run that got to its end writes it."""

WENT_WRONG = {
    **GOOD,
    "outcome": "aborted",
    "reason": "the arm did not answer",
    "first_motion_s": None,
    "cost_usd": None,
    "model": None,
    "provider": "fake",
    "usage": {},
    "final_state": {},
}
"""What a run the arm walked out on leaves behind: nothing it never got as far as."""

NOTES = {
    "hook": "It asked for a lot. It got a little.",
    "goal_met": "Yes",
    "why_example": "Because an LLM can ask for anything.",
    "why_result": "Because the limit held.",
    "goal": "Show the cap.",
    "expected": "The cap holds.",
    "showed": ["It held."],
    "did_not_show": ["Nothing else."],
}


def _tree(root: Path) -> Path:
    root.mkdir(parents=True)
    rows = [
        {"id": "E001", "title": "First", "category": "Hilarity"},
        {"id": "E002", "title": '"Second | part"', "category": "Safety"},
        {"id": "E003", "title": "Third", "category": "Red team"},
    ]
    (root / "experiments.json").write_text(json.dumps(rows), encoding="utf-8")
    (root / "README.md").write_text(
        f"# Examples\n\n{script.TABLE_START}\n{script.TABLE_END}\n\nThe rest.\n", encoding="utf-8"
    )
    return root


def _logged(
    root: Path,
    name: str,
    summary: dict[str, Any],
    *,
    frames: int = 3,
    duck: str = "task",
    notes: dict[str, Any] | None = None,
) -> Path:
    """An example with a run in its folder, as `add` and somebody's notes would leave it."""
    folder = root / name
    log = folder / "log"
    log.mkdir(parents=True)
    command = [
        "quackd",
        "run",
        f"D:/x/docs/examples/lerobot/{name}/{duck}.duck",
        "--robot",
        "arm-01",
    ]
    (log / "summary.json").write_text(
        json.dumps({"duck": f"{name}-{duck}", "command": command, **summary}), encoding="utf-8"
    )
    (log / "terminal.txt").write_text(
        f"$ {' '.join(command)}\nquackd 0.31.1, started\nsome `code` here\n", encoding="utf-8"
    )
    media = {"speed": 10, "frames": frames, "run_folder": f"20261104-114416-{name}-{duck}"}
    (log / "media.json").write_text(json.dumps(media), encoding="utf-8")
    written = json.dumps({**NOTES, **(notes or {})})  # JSON is YAML
    (log / "notes.yaml").write_text(written, encoding="utf-8")
    return folder


def _experiment(root: Path, wanted: str) -> dict[str, str]:
    return next(e for e in script.experiments(root) if e["id"] == wanted)


def test_the_repository_holds_what_the_script_writes() -> None:
    """A hand-edited log or table, a key in a trace or a GIF too big all land here."""
    assert script.check_all(script.EXAMPLES) == []


def test_the_table_has_one_row_per_experiment_in_order() -> None:
    experiments = script.experiments(script.EXAMPLES)
    assert [e["id"] for e in experiments] == [f"E{n:03d}" for n in range(1, len(experiments) + 1)]
    assert all(e["title"] and e["category"] for e in experiments)
    table = script.render_table(script.EXAMPLES)
    rows = re.findall(r"^\| (?:✅)? *\| .*$", table, flags=re.MULTILINE)
    assert len(rows) == len(experiments)


def test_every_log_reads_the_same_but_for_its_content(tmp_path: Path) -> None:
    """The one thing asked of these: two very different runs, one structure, one set of lines."""
    root = _tree(tmp_path / "examples")
    _logged(root, "e001", GOOD)
    _logged(root, "e002", WENT_WRONG, frames=0, duck="other", notes={"goal_met": "No"})
    one = script.render_log(root, _experiment(root, "E001"))
    two = script.render_log(root, _experiment(root, "E002"))

    def headings(text: str) -> list[str]:
        return [
            re.sub(r"^(# E)\d.*", r"\1", line) for line in text.splitlines() if line.startswith("#")
        ]

    def labels(text: str) -> list[str]:
        block = text.split("## The numbers\n", 1)[1].split("\n## ", 1)[0]
        return [row.split("|")[1] for row in block.splitlines() if row.startswith("|")]

    assert headings(one) == headings(two)
    assert labels(one) == labels(two)
    for line in script.LOG.splitlines():
        if line and "{" not in line:
            assert line in one, f"the log dropped a fixed line: {line}"
            assert line in two, f"the log dropped a fixed line: {line}"


def test_a_log_says_why_it_matters_before_anything_else(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    _logged(root, "e001", GOOD)
    text = script.render_log(root, _experiment(root, "E001"))
    assert "**Why this example exists:** Because an LLM can ask for anything." in text
    assert "**Why this result is important:** Because the limit held." in text
    assert re.findall(r"^## .*$", text, flags=re.MULTILINE)[0] == "## Why this matters"


def test_every_log_and_the_table_say_llm_and_never_the_other_word() -> None:
    """Rok's rule, on what is committed: a log says LLM in everything it writes in its own words."""
    readmes = sorted(script.EXAMPLES.glob("e[0-9]*/README.md"))
    assert readmes, "no log to read"
    for readme in readmes:
        found = script.NOT_THE_WORD.search(script.our_words(readme.read_text(encoding="utf-8")))
        assert found is None, f"{readme} says {found.group() if found else ''}"
    for written in (script.LOG, script.NOTES, script.render_table(script.EXAMPLES)):
        assert script.NOT_THE_WORD.search(script.our_words(written)) is None


@pytest.mark.parametrize(
    "words",
    [
        "The model asked.",
        "An AI model asked.",
        "An AI  model asked.",
        "Two models asked.",
        "What the model's asked.",
        "The MODEL asked.",
        "Model calls.",
    ],
)
def test_a_note_that_says_the_other_word_is_refused(tmp_path: Path, words: str) -> None:
    root = _tree(tmp_path / "examples")
    _logged(root, "e001", GOOD, notes={"hook": words})
    with pytest.raises(script.LogError, match="a log says LLM"):
        script.render_log(root, _experiment(root, "E001"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("why_example", "A model."),
        ("why_result", "A model."),
        ("goal", "A model."),
        ("expected", "A model."),
        ("showed", ["A model."]),
        ("did_not_show", ["A model."]),
    ],
)
def test_no_field_of_the_notes_escapes_the_word(tmp_path: Path, field: str, value: Any) -> None:
    root = _tree(tmp_path / "examples")
    _logged(root, "e001", GOOD, notes={field: value})
    with pytest.raises(script.LogError, match="a log says LLM"):
        script.render_log(root, _experiment(root, "E001"))


def test_a_title_that_says_the_other_word_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    rows = [{"id": "E001", "title": "The dumbest model", "category": "Hilarity"}]
    (root / "experiments.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(script.LogError, match="the table says"):
        script.render_table(root)


def test_the_words_near_it_are_left_alone(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    _logged(
        root, "e001", GOOD, notes={"hook": "A remodelled arm, modelled in CAD, as a ModelSpec."}
    )
    script.render_log(root, _experiment(root, "E001"))


def test_what_a_run_really_said_keeps_its_own_words(tmp_path: Path) -> None:
    """A trace, a command and a recorded reason are evidence, and a code span quotes: changing the
    word in any of them would make a log say something that did not happen."""
    root = _tree(tmp_path / "examples")
    folder = _logged(
        root,
        "e001",
        {**GOOD, "reason": "The model refused."},
        notes={"showed": ["It printed `model calls p50`."]},
    )
    (folder / "log" / "terminal.txt").write_text(
        "$ quackd run x.duck --model big\ntime 49.5 s (model 20.4 s)\n", encoding="utf-8"
    )
    text = script.render_log(root, _experiment(root, "E001"))
    assert "(model 20.4 s)" in text
    assert "> The model refused." in text
    assert "`model calls p50`" in text
    assert "```text\nquackd run x.duck --model big\n```" in text


def test_the_numbers_say_llm(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    _logged(root, "e001", GOOD)
    text = script.render_log(root, _experiment(root, "E001"))
    assert "| LLM | `openai:gpt-6-sol` |" in text
    assert "| LLM calls | 6 |" in text
    assert "| Waiting for the LLM | 20.4 s |" in text


def test_the_gif_says_how_fast_it_plays(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    _logged(root, "e001", GOOD)
    text = script.render_log(root, _experiment(root, "E001"))
    assert "Filmed with an external camera. The GIF plays at 10x speed." in text
    assert "(log/run.gif)" in text


def test_a_run_that_went_wrong_still_gets_a_whole_log(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    _logged(root, "e001", WENT_WRONG, frames=0)
    text = script.render_log(root, _experiment(root, "E001"))
    assert "The run ended with **ABORTED**" in text
    assert "> the arm did not answer" in text
    assert "This run used no camera." in text
    assert "| First motion | no motion |" in text
    assert "| Cost at list price | not recorded |" in text
    assert "| Speed cap | not recorded |" in text


def test_the_command_comes_from_the_trace_with_its_quotes(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    folder = _logged(root, "e001", GOOD)
    typed = "quackd run x.duck --camera-url 'opencv://1?name=arm'"
    (folder / "log" / "terminal.txt").write_text(f"$ {typed}\nquackd 0.31.1\n", encoding="utf-8")
    assert f"```text\n{typed}\n```" in script.render_log(root, _experiment(root, "E001"))


def test_what_was_set_before_the_command_goes_with_it(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    before = '$env:QUACKD_LEROBOT_MAX_STEP_DEG = "2"'
    _logged(root, "e001", GOOD, notes={"before": [before]})
    text = script.render_log(root, _experiment(root, "E001"))
    assert f"```text\n{before}\nquackd run D:/x/" in text


def test_a_trace_with_backticks_cannot_close_its_fence(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    folder = _logged(root, "e001", GOOD)
    trace = "$ quackd run x.duck\n````\ninside\n"
    (folder / "log" / "terminal.txt").write_text(trace, encoding="utf-8")
    assert f"`````text\n{trace}`````" in script.render_log(root, _experiment(root, "E001"))


def test_a_hand_edit_is_found_and_write_puts_it_right(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    folder = _logged(root, "e001", GOOD)
    assert sorted(p.name for p in script.write_all(root)) == ["README.md", "README.md"]
    assert script.check_all(root) == []
    readme = folder / "README.md"
    good = readme.read_text(encoding="utf-8")
    readme.write_text(good.replace("The following command", "This command"), encoding="utf-8")
    assert any("e001" in problem for problem in script.check_all(root))
    script.write_all(root)
    assert readme.read_text(encoding="utf-8") == good


def test_the_table_ticks_what_has_a_log_and_links_what_has_a_folder(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    _logged(root, "e002", GOOD)
    (root / "e003").mkdir()
    table = script.render_table(root)
    assert "**1 of 3 examples have a log.**" in table
    assert "|  | E001: First | Hilarity |  |  |  |" in table
    assert (
        '| ✅ | [E002: "Second \\| part"](e002/) | Safety | It asked for a lot. It got a little. '
        "| Yes | 0.31.1 |"
    ) in table
    assert "|  | [E003: Third](e003/) | Red team |  |  |  |" in table
    script.write_all(root)
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# Examples\n\n")
    assert readme.endswith(f"{script.TABLE_END}\n\nThe rest.\n")


def test_a_hand_edited_table_is_found(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    script.write_all(root)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("|  | E001", "| ✅ | E001"), encoding="utf-8"
    )
    assert script.check_all(root)


@pytest.mark.parametrize(
    ("field", "value", "named"),
    [
        ("hook", "TODO", "hook"),
        ("why_example", "TODO", "why_example"),
        ("why_result", "", "why_result"),
        ("goal", "", "goal"),
        ("goal_met", "Sort of", "goal_met"),
        ("showed", ["TODO"], "showed"),
        ("did_not_show", [], "did_not_show"),
    ],
)
def test_a_log_with_a_gap_is_refused(tmp_path: Path, field: str, value: Any, named: str) -> None:
    root = _tree(tmp_path / "examples")
    _logged(root, "e001", GOOD, notes={field: value})
    with pytest.raises(script.LogError, match=named):
        script.render_log(root, _experiment(root, "E001"))


def test_a_log_github_would_cut_off_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path / "examples")
    _logged(root, "e001", GOOD)
    monkeypatch.setattr(script, "README_LIMIT", 100)
    with pytest.raises(script.LogError, match="cuts a README off"):
        script.render_log(root, _experiment(root, "E001"))


def _run_folder(
    tmp_path: Path, name: str = "20261104-114416-e001-task", trace: str = "$ quackd run x\n"
) -> Path:
    run = tmp_path / "runs" / name
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"duck": "e001-task", **GOOD}), encoding="utf-8")
    (run / "terminal.txt").write_text(trace, encoding="utf-8")
    return run


def _no_media(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_gif(video: Path, gif: Path) -> None:
        gif.write_bytes(b"GIF89a")

    monkeypatch.setattr(script, "make_gif", fake_gif)
    monkeypatch.setattr(script, "_keep_frames", lambda run_dir, log: 3)


def test_add_copies_the_run_and_leaves_notes_to_fill_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_media(monkeypatch)
    root = _tree(tmp_path / "examples")
    run = _run_folder(tmp_path)
    (run / "E001.mp4").write_bytes(b"video")
    notes = script.add_run(root, run, 10.0)
    log = root / "e001" / "log"
    assert sorted(p.name for p in log.iterdir()) == [
        "media.json",
        "notes.yaml",
        "run.gif",
        "summary.json",
        "terminal.txt",
    ]
    assert json.loads((log / "media.json").read_text(encoding="utf-8")) == {
        "speed": 10.0,
        "frames": 3,
        "run_folder": run.name,
    }
    with pytest.raises(script.LogError, match="hook, why_example, why_result, goal, expected"):
        script.write_all(root)

    # The same run again, as a better video does, keeps what was written about it.
    written = notes.read_text(encoding="utf-8").replace("hook: TODO", "hook: Written.")
    notes.write_text(written, encoding="utf-8")
    script.add_run(root, run, 5.0)
    assert notes.read_text(encoding="utf-8") == written
    assert json.loads((log / "media.json").read_text(encoding="utf-8"))["speed"] == 5.0


def test_an_example_keeps_one_log_unless_told_to_replace_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_media(monkeypatch)
    root = _tree(tmp_path / "examples")
    first = _run_folder(tmp_path)
    (first / "E001.mp4").write_bytes(b"video")
    notes = script.add_run(root, first, 10.0)
    notes.write_text("hook: About the first run.\n", encoding="utf-8")

    second = _run_folder(tmp_path, "20261104-120000-e001-task")
    (second / "E001.mp4").write_bytes(b"video")
    with pytest.raises(script.LogError, match="one log"):
        script.add_run(root, second, 10.0)
    assert notes.read_text(encoding="utf-8") == "hook: About the first run.\n"

    script.add_run(root, second, 10.0, replace=True)
    media = json.loads((root / "e001" / "log" / "media.json").read_text(encoding="utf-8"))
    assert media["run_folder"] == second.name
    assert notes.read_text(encoding="utf-8") == script.NOTES, "old notes described the old run"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnopqrstuv",
        "OPENAI_API_KEY=abc123",
        "Authorization: Bearer abcdefghijklmnop",
        "calibration in C:\\Users\\somebody\\.cache",
    ],
)
def test_add_will_not_copy_a_key_or_a_home_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    _no_media(monkeypatch)
    root = _tree(tmp_path / "examples")
    run = _run_folder(tmp_path, trace=f"$ quackd run x\n{secret}\n")
    (run / "video.mp4").write_bytes(b"video")
    with pytest.raises(script.LogError, match=r"terminal\.txt holds a key"):
        script.add_run(root, run, 10.0)
    assert not (root / "e001").exists()


def test_a_key_that_got_committed_anyway_is_found(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    folder = _logged(root, "e001", GOOD)
    script.write_all(root)
    trace = folder / "log" / "terminal.txt"
    trace.write_text(
        trace.read_text(encoding="utf-8") + "sk-abcdefghijklmnopqrstuv\n", encoding="utf-8"
    )
    assert any("holds a key" in problem for problem in script.check_all(root))


def test_add_wants_exactly_one_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_media(monkeypatch)
    root = _tree(tmp_path / "examples")
    run = _run_folder(tmp_path)
    with pytest.raises(script.LogError, match="copy the video into that folder"):
        script.add_run(root, run, 10.0)
    (run / "one.mp4").write_bytes(b"video")
    (run / "two.mov").write_bytes(b"video")
    with pytest.raises(script.LogError, match="more than one video"):
        script.add_run(root, run, 10.0)


def test_add_needs_a_run_that_reached_its_end(tmp_path: Path) -> None:
    root = _tree(tmp_path / "examples")
    run = tmp_path / "20261104-114416-e001-task"
    run.mkdir()
    with pytest.raises(script.LogError, match="never reached its end"):
        script.add_run(root, run, 10.0)


def test_add_needs_to_be_told_the_example_of_a_goal_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_media(monkeypatch)
    root = _tree(tmp_path / "examples")
    run = _run_folder(tmp_path, "20261104-114416-goal")
    (run / "video.mp4").write_bytes(b"video")
    with pytest.raises(script.LogError, match="--example"):
        script.add_run(root, run, 10.0)
    script.add_run(root, run, 10.0, example="e003")
    assert (root / "e003" / "log" / "run.gif").exists()


def _clip(path: Path) -> None:
    """Three seconds of a moving box, with sensor noise on every frame."""
    graph = (
        "color=gray:s=640x360:r=25:d=3[bg];color=white:s=60x60:r=25:d=3[box];"
        "[bg][box]overlay=x='100+t*100':y=100,noise=alls=6:allf=t"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-filter_complex", graph, "-t", "3", str(path)],
        stdin=subprocess.DEVNULL,
        check=True,
    )


def _seconds(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed here")
def test_a_gif_keeps_the_whole_video_so_its_speed_stays_true(tmp_path: Path) -> None:
    video, gif = tmp_path / "video.mp4", tmp_path / "run.gif"
    _clip(video)
    script.make_gif(video, gif)
    assert gif.stat().st_size <= script.GIF_LIMIT
    assert abs(_seconds(gif) - _seconds(video)) < 0.2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed here")
def test_a_video_too_big_for_any_gif_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video, gif = tmp_path / "video.mp4", tmp_path / "run.gif"
    _clip(video)
    monkeypatch.setattr(script, "GIF_LIMIT", 100)
    monkeypatch.setattr(script, "GIF_LADDER", ((320, 8),))
    with pytest.raises(script.LogError, match="shorter or a faster video"):
        script.make_gif(video, gif)


def test_the_how_to_names_everything_a_log_asks_for() -> None:
    """The section a person writes a log from, held to the fields and the verbs the script has."""
    readme = (script.EXAMPLES / "README.md").read_text(encoding="utf-8")
    section = readme.split("## How a log is written\n", 1)[1].split("\n## ", 1)[0]
    for field in [*yaml.safe_load(script.NOTES), "before"]:
        assert f"| `{field}` |" in section, f"the how-to does not say what goes in {field}"
    for word in script.GOAL_MET:
        assert f"`{word}`" in section
    for verb in ("add", "write", "check"):
        assert f"example_log.py {verb}" in section
    for flag in ("--speed", "--example", "--replace"):
        assert flag in section


def test_no_published_package_carries_a_logs_pictures() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert {"docs/examples/**/*.gif", "docs/examples/**/*.jpg"} <= set(sdist["exclude"])
