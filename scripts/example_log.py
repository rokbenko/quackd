"""Write the run logs under `docs/examples/lerobot/`, or check that they still hold what it writes.

An example that has been run has a `README.md` in its folder, the log of the one run chosen for it,
and every one of them is this script's output, so their headings and sentences cannot drift apart:
only what a run recorded and what somebody wrote about it differ from one to the next. So a log is
changed in its notes and this script run, never in the README, and a test fails when a README says
anything else.

    uv run --no-sync python scripts/example_log.py add <run folder> --speed 10  # copy a run in
    uv run --no-sync python scripts/example_log.py write             # rewrite logs and the table
    uv run --no-sync python scripts/example_log.py check             # exit 1 if any would change

What `add` needs is the run folder with its video left inside it, under any name, and how many times
faster than real time that video plays, which is read off the video. What goes where, in
`docs/examples/lerobot/e114/`:

    README.md          the log, written here
    log/notes.yaml     what somebody wrote: the result, why it matters, what the run showed
    log/summary.json   the run's own record, copied as it was
    log/terminal.txt   the run's own terminal, copied as it was
    log/media.json     how fast the GIF plays, how many frames were kept, which run this is
    log/run.gif        made from the video
    log/frame-N.jpg    what the robot's camera saw at the start, the middle and the end

`write` and `check` need neither ffmpeg nor the run folders, and read nothing outside this tree.
What to write in the notes is under How a log is written in `docs/examples/lerobot/README.md`.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "docs" / "examples" / "lerobot"

README_LIMIT = 500 * 1024
"""GitHub cuts a README off past 500 KiB, and a log's trace is most of its bytes."""
GIF_LIMIT = 5 * 1024 * 1024
"""The most one GIF may weigh. Every clone and every CI job carries all of them."""
GIF_LADDER = ((800, 15), (720, 15), (640, 15), (640, 12), (480, 12), (480, 10), (400, 10), (320, 8))
"""The widths and frame rates to try, best first, until a GIF fits `GIF_LIMIT`."""
DENOISE = "hqdn3d=4:3:6:4.5"
"""A GIF stores what changed between frames, and to it a camera's sensor noise is all change, so
taking the noise out first halves the file and leaves the arm as sharp."""
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".m2ts"}
GOAL_MET = ("Yes", "Partly", "No")
TODO = "TODO"
PRIVATE = re.compile(
    r"sk-[A-Za-z0-9_-]{12,}|Bearer [A-Za-z0-9._-]{12,}|[A-Z_]*API_KEY\s*=\s*\S+"
    r"|[A-Za-z]:[\\/]+Users"
)
"""What must never reach a public log: a key, or a path into somebody's home folder."""
NOT_THE_WORD = re.compile(r"\b(?:AI\s+)?models?\b", re.IGNORECASE)
"""A log says LLM, always: Rok's rule, for what a log writes in its own words (`our_words`)."""
TABLE_START = "<!-- example-log table: written by scripts/example_log.py, do not edit by hand -->"
TABLE_END = "<!-- end of the example-log table -->"
NOTES = f"""\
hook: {TODO}
goal_met: {TODO}
why_example: {TODO}
why_result: {TODO}
goal: {TODO}
expected: {TODO}
showed:
  - {TODO}
did_not_show:
  - {TODO}
"""

LOG = """\
# {id}: {title}

**{hook}**

![{id}, filmed with an external camera](log/run.gif)

Filmed with an external camera. The GIF plays at {speed}x speed.

| Category | Goal met | The run ended with | Date | quackd |
|---|---|---|---|---|
| {category} | {goal_met} | {outcome} | {date} | {version} |

The numbers and the trace below are copied from the run's own record.
[All examples](../README.md)

## Why this matters

**Why this example exists:** {why_example}

**Why this result is important:** {why_result}

## The goal

{goal}

**What we expected:** {expected}

## What the run showed

{showed}

## What the run did not show

{did_not_show}

## How the run ended

The run ended with **{outcome}**. The reason on record:

> {reason}

## The exact command

The following command was run with quackd {version}:

{fence_command}text
{command}
{fence_command}

## The numbers

| | |
|---|---|
{numbers}

## What the robot saw

{frames}

## Try it yourself

This runs the same task file with no arm attached, on `lerobot:mock`, an arm that lives in memory.
It shows the rules a run is held to and not the physics. Run it from a checkout of this repository
with quackd's `lerobot` extra and the LLM's own SDK and key. It costs what the LLM charges.

```bash
{try_it}
```

## Full terminal trace

<details>
<summary>Show the full terminal trace ({lines} lines)</summary>

{fence_trace}text
{trace}
{fence_trace}

</details>
"""


class LogError(Exception):
    """Something a person has to put right, said in a sentence that tells them what."""


def _write(path: Path, text: str) -> None:
    """LF on every platform, because a test compares these byte for byte on three of them."""
    path.write_text(text, encoding="utf-8", newline="\n")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: object) -> str:
    return " ".join(str(text).split())


def _cell(text: str) -> str:
    return _flat(text).replace("|", "\\|")


def _fence(text: str) -> str:
    """A code fence longer than any run of backticks inside it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"* {_flat(item)}" for item in items)


def _seconds(value: float | None) -> str:
    return "not recorded" if value is None else f"{value:.1f} s"


def our_words(text: str) -> str:
    """A log without what it quotes. A trace, a command and a recorded reason are what a run really
    said, and code spans and link targets are names and quoted output, so all of them stay as they
    are: changing the word there would make the log say something that did not happen."""
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.DOTALL)
    text = re.sub(r"^(`{3,}).*?^\1$", "", text, flags=re.DOTALL | re.MULTILINE)
    text = re.sub(r"^> .*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"`[^`\n]*`", "", text)
    return re.sub(r"\]\([^)]*\)", "]", text)


def _says_llm(where: str, text: str) -> None:
    for line in our_words(text).splitlines():
        found = NOT_THE_WORD.search(line)
        if found:
            raise LogError(f'{where} says "{found.group()}", and a log says LLM: {line.strip()}')


def experiments(root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = json.loads(_read(root / "experiments.json"))
    return rows


def notes_of(folder: Path) -> dict[str, Any] | None:
    path = folder / "log" / "notes.yaml"
    if not path.exists():
        return None
    notes: dict[str, Any] = yaml.safe_load(_read(path))
    return notes


def _complete(name: str, notes: dict[str, Any]) -> None:
    """Refuse a log with a gap in it: a README is only ever whole."""
    texts = ("hook", "why_example", "why_result", "goal", "expected")
    missing = [key for key in texts if notes.get(key) in (None, "", TODO)]
    if notes.get("goal_met") not in GOAL_MET:
        missing.append(f"goal_met (one of {', '.join(GOAL_MET)})")
    missing += [
        key for key in ("showed", "did_not_show") if not notes.get(key) or TODO in notes[key]
    ]
    if missing:
        raise LogError(f"{name}/log/notes.yaml still needs: {', '.join(missing)}")


def _summary(folder: Path) -> dict[str, Any]:
    summary: dict[str, Any] = json.loads(_read(folder / "log" / "summary.json"))
    return summary


def _duck_path(summary: dict[str, Any]) -> str | None:
    """The task file's path inside this repository, if the command named one there."""
    for word in summary["command"]:
        path = word.replace("\\", "/")
        if path.endswith(".duck") and "docs/examples/lerobot/" in path:
            return "docs/examples/lerobot/" + path.split("docs/examples/lerobot/", 1)[1]
    return None


def _pilot(summary: dict[str, Any]) -> str:
    provider, model = summary.get("provider"), summary.get("model")
    return f"{provider}:{model}" if model else str(provider)


def _try_it(summary: dict[str, Any]) -> str:
    task = _duck_path(summary) or summary["command"][2]
    return f"quackd run {task} --robot lerobot:mock --llm {_pilot(summary)} --no-memory"


def _task_file(summary: dict[str, Any], folder: Path) -> str:
    path = _duck_path(summary)
    if path is None:
        return f"`{summary['duck']}`, which ships with quackd"
    inside = path.removeprefix("docs/examples/lerobot/")
    here = f"{folder.name}/"
    link = inside.removeprefix(here) if inside.startswith(here) else f"../{inside}"
    return f"[`{inside.rsplit('/', 1)[1]}`]({link})"


def _numbers(summary: dict[str, Any], folder: Path, run_folder: str) -> str:
    usage = summary.get("usage") or {}
    cost = summary.get("cost_usd")
    step = ((summary.get("final_state") or {}).get("extras") or {}).get("step_deg")
    moved = summary.get("first_motion_s")
    rows = [
        ("quackd version", summary["version"]),
        ("Started", summary["started_at"][:19].replace("T", " ") + " UTC"),
        ("Robot", f"`{summary['robot']}` ({summary.get('transport')})"),
        ("LLM", f"`{_pilot(summary)}`"),
        ("Task file", _task_file(summary, folder)),
        ("Steps", summary.get("steps")),
        ("LLM calls", summary.get("llm_calls")),
        ("Total time", _seconds(summary.get("wall_s"))),
        ("Connecting", _seconds(summary.get("connect_s"))),
        ("Waiting for the LLM", _seconds(summary.get("llm_latency_s"))),
        ("First motion", "no motion" if moved is None else f"{moved:.1f} s after the start"),
        ("Tokens", f"{usage.get('input_tokens', 0):,} in, {usage.get('output_tokens', 0):,} out"),
        ("Cost at list price", "not recorded" if cost is None else f"${cost:.4f}"),
        ("Speed cap", "not recorded" if step is None else f"{step:g}° per send"),
        ("Run folder", f"`runs\\{run_folder}`"),
    ]
    return "\n".join(f"| {label} | {value} |" for label, value in rows)


def _frames(count: int) -> str:
    if count == 0:
        return "This run used no camera."
    labels = {1: ["Start"], 2: ["Start", "End"], 3: ["Start", "Middle", "End"]}[count]
    pictures = [f"![{label}](log/frame-{i}.jpg)" for i, label in enumerate(labels, start=1)]
    return "\n".join(
        [f"| {' | '.join(labels)} |", f"|{'---|' * count}", f"| {' | '.join(pictures)} |"]
    )


def render_log(root: Path, experiment: dict[str, str]) -> str:
    """One example's README, whole, from what is in its folder. Refuses rather than leave a gap."""
    folder = root / experiment["id"].lower()
    notes = notes_of(folder)
    if notes is None:
        raise LogError(f"{folder.name} has no log/notes.yaml")
    _complete(folder.name, notes)
    summary = _summary(folder)
    media: dict[str, Any] = json.loads(_read(folder / "log" / "media.json"))
    trace = _read(folder / "log" / "terminal.txt").rstrip("\n")
    first = trace.split("\n", 1)[0]
    if not first.startswith("$ "):
        raise LogError(f"{folder.name}/log/terminal.txt does not open with the command it ran")
    command = "\n".join([*(notes.get("before") or []), first.removeprefix("$ ")])
    text = LOG.format(
        id=experiment["id"],
        title=experiment["title"],
        hook=_flat(notes["hook"]),
        speed=f"{media['speed']:g}",
        category=experiment["category"],
        goal_met=notes["goal_met"],
        outcome=str(summary["outcome"]).upper(),
        date=summary["started_at"][:10],
        version=summary["version"],
        why_example=_flat(notes["why_example"]),
        why_result=_flat(notes["why_result"]),
        goal=_flat(notes["goal"]),
        expected=_flat(notes["expected"]),
        showed=_bullets(notes["showed"]),
        did_not_show=_bullets(notes["did_not_show"]),
        reason=_flat(summary.get("reason") or "No reason was recorded."),
        fence_command=_fence(command),
        command=command,
        numbers=_numbers(summary, folder, media["run_folder"]),
        frames=_frames(int(media["frames"])),
        try_it=_try_it(summary),
        lines=trace.count("\n") + 1,
        fence_trace=_fence(trace),
        trace=trace,
    )
    _says_llm(f"{folder.name}/README.md", text)
    size = len(text.encode("utf-8"))
    if size > README_LIMIT:
        raise LogError(
            f"{folder.name}/README.md would be {size // 1024} KiB and GitHub cuts a README off at "
            f"{README_LIMIT // 1024} KiB"
        )
    return text


def render_table(root: Path) -> str:
    """The table in the examples' README: one row per experiment, ticked once it has a log."""
    rows = []
    done = 0
    all_of_them = experiments(root)
    for experiment in all_of_them:
        folder = root / experiment["id"].lower()
        name = _cell(f"{experiment['id']}: {experiment['title']}")
        link = f"[{name}]({folder.name}/)" if folder.exists() else name
        notes = notes_of(folder)
        if notes is None:
            rows.append(f"|  | {link} | {experiment['category']} |  |  |  |")
            continue
        done += 1
        version = _summary(folder)["version"]
        hook = _cell(notes["hook"])
        rows.append(
            f"| ✅ | {link} | {experiment['category']} | {hook} | {notes['goal_met']} | {version} |"
        )
    _says_llm("the table", "\n".join(rows))
    return "\n".join(
        [
            TABLE_START,
            "",
            f"**{done} of {len(all_of_them)} examples have a log.** A ✅ means the example's "
            "folder holds a log of how it really went: why it matters, the command, a video, what "
            "it showed and the full terminal trace. Goal met is whether the run showed what the "
            "example set out to show, which is a different question from whether the run ended in "
            "success.",
            "",
            "| Done | Example | Category | What it showed | Goal met | quackd |",
            "|---|---|---|---|---|---|",
            *rows,
            "",
            TABLE_END,
        ]
    )


def _splice_table(root: Path, text: str) -> str:
    start, end = text.find(TABLE_START), text.find(TABLE_END)
    if start < 0 or end < 0:
        raise LogError(f"{root / 'README.md'} has lost the markers around its table")
    return text[:start] + render_table(root) + text[end + len(TABLE_END) :]


def _expected(root: Path) -> dict[Path, str]:
    """Every file this script owns, and what each must say."""
    wanted = {root / "README.md": _splice_table(root, _read(root / "README.md"))}
    for experiment in experiments(root):
        folder = root / experiment["id"].lower()
        if notes_of(folder) is not None:
            wanted[folder / "README.md"] = render_log(root, experiment)
    return wanted


def write_all(root: Path) -> list[Path]:
    changed = []
    for path, text in _expected(root).items():
        if not path.exists() or _read(path) != text:
            _write(path, text)
            changed.append(path)
    return changed


def check_all(root: Path) -> list[str]:
    """What is out of step with this script, as sentences. Empty when nothing is."""
    problems = [
        f"{path} is not what scripts/example_log.py writes"
        for path, text in _expected(root).items()
        if not path.exists() or _read(path) != text
    ]
    known = {experiment["id"].lower() for experiment in experiments(root)}
    for folder in sorted(p for p in root.glob("e[0-9]*") if p.is_dir()):
        if folder.name not in known:
            problems.append(f"{folder.name} is not in experiments.json")
        if (folder / "README.md").exists() and notes_of(folder) is None:
            problems.append(f"{folder.name}/README.md has no log/notes.yaml behind it")
        for path in sorted((folder / "log").rglob("*")):
            if path.suffix in {".txt", ".json", ".yaml"} and PRIVATE.search(_read(path)):
                problems.append(f"{path} holds a key or a path into a home folder")
            if path.suffix == ".gif" and path.stat().st_size > GIF_LIMIT:
                problems.append(f"{path} is over the {GIF_LIMIT // (1024 * 1024)} MiB limit")
    return problems


def _run(*command: str | Path) -> str:
    done = subprocess.run(
        [str(word) for word in command],
        stdin=subprocess.DEVNULL,  # ffmpeg reads its keyboard from here, and can wait on it forever
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        tail = done.stderr.strip().splitlines()[-1:] or ["no message"]
        raise LogError(f"{command[0]} failed: {tail[0]}")
    return done.stdout


def make_gif(video: Path, gif: Path) -> None:
    """The whole video as a GIF at the best size that fits `GIF_LIMIT`. Nothing is cut or re-timed,
    so the speed the video says it plays at stays true of the GIF."""
    size = 0
    for width, fps in GIF_LADDER:
        graph = (
            f"{DENOISE},fps={fps},scale={width}:-2:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=128:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
        )
        _run("ffmpeg", "-y", "-v", "error", "-i", video, "-an", "-filter_complex", graph, gif)
        size = gif.stat().st_size
        if size <= GIF_LIMIT:
            return
    raise LogError(
        f"the GIF of {video.name} is still {size // (1024 * 1024)} MiB at the smallest size: "
        "this needs a shorter or a faster video"
    )


def _keep_frames(run_dir: Path, log: Path) -> int:
    """Up to three of the robot's own pictures, from its first camera, small enough to commit."""
    frames = sorted((run_dir / "frames").glob("*.png")) if (run_dir / "frames").is_dir() else []
    if not frames:
        return 0
    numbers = sorted({p.name[:4] for p in frames})
    picked = sorted({numbers[0], numbers[len(numbers) // 2], numbers[-1]})
    for i, number in enumerate(picked, start=1):
        first = next(p for p in frames if p.name.startswith(number))
        _run("ffmpeg", "-y", "-v", "error", "-i", first, "-vf", "scale=320:-2", "-q:v", "5",
             log / f"frame-{i}.jpg")  # fmt: skip
    return len(picked)


def _video_in(run_dir: Path) -> Path:
    found = sorted(p for p in run_dir.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
    if len(found) == 1:
        return found[0]
    if not found:
        raise LogError(f"no video in {run_dir}: copy the video into that folder")
    names = ", ".join(p.name for p in found)
    raise LogError(f"more than one video in {run_dir} ({names}): leave the one this run is")


def add_run(
    root: Path, run_dir: Path, speed: float, example: str | None = None, replace: bool = False
) -> Path:
    """Make this run its example's log and leave notes for somebody to fill in.

    An example has one log. Adding the same run again, as a better video does, keeps what was
    written about it. A different run starts the notes over, because they described the old one, so
    it takes `replace`, and the old log stays in git's history."""
    matched = re.fullmatch(r"\d{8}-\d{6}-(?:(e\d{3})(?:-.*)?|.*)", run_dir.name)
    name = example or (matched.group(1) if matched else None)
    if name is None:
        raise LogError(f"cannot tell the example from {run_dir.name}: pass --example eNNN")
    if not (run_dir / "summary.json").exists():
        raise LogError(f"{run_dir} has no summary.json: that run never reached its end")
    summary = _read(run_dir / "summary.json")
    trace = _read(run_dir / "terminal.txt")
    for what, text in (("terminal.txt", trace), ("summary.json", summary)):
        if PRIVATE.search(text):
            raise LogError(f"{what} holds a key or a path into a home folder: not copying it")
    video = _video_in(run_dir)

    log = root / name.lower() / "log"
    if (log / "media.json").exists():
        logged = json.loads(_read(log / "media.json"))["run_folder"]
        if logged != run_dir.name and not replace:
            raise LogError(
                f"{name.lower()} already logs {logged}, and an example has one log: pass --replace "
                "to log this run instead, which starts its notes over"
            )
        if logged != run_dir.name:
            shutil.rmtree(log)
    log.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        made = Path(scratch) / "run.gif"
        make_gif(video, made)
        shutil.copyfile(made, log / "run.gif")
    frames = _keep_frames(run_dir, log)
    _write(log / "summary.json", summary)
    _write(log / "terminal.txt", trace)
    media = {"speed": speed, "frames": frames, "run_folder": run_dir.name}
    _write(log / "media.json", json.dumps(media, indent=2) + "\n")
    notes = log / "notes.yaml"
    if not notes.exists():
        _write(notes, NOTES)
    return notes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    verbs = parser.add_subparsers(dest="verb", required=True)
    add = verbs.add_parser("add", help="make a run its example's log, and make the GIF")
    add.add_argument("run_folder", type=Path)
    add.add_argument(
        "--speed",
        type=float,
        required=True,
        help="how many times faster than real time the video plays, read off the video: 1 is real",
    )
    add.add_argument("--example", help="the example, where the run folder's name does not say")
    add.add_argument("--replace", action="store_true", help="log this run instead of the old one")
    verbs.add_parser("write", help="rewrite every log and the table")
    verbs.add_parser("check", help="exit 1 if any log or the table would change")
    args = parser.parse_args(argv)
    try:
        if args.verb == "add":
            notes = add_run(EXAMPLES, args.run_folder, args.speed, args.example, args.replace)
            print(f"Copied. Fill in {notes}, then run: example_log.py write")
        elif args.verb == "write":
            for path in write_all(EXAMPLES):
                print(f"wrote {path}")
        else:
            problems = check_all(EXAMPLES)
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1 if problems else 0
    except LogError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
