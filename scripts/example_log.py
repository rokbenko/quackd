"""Write the run logs under `docs/examples/lerobot/`, or check that they still hold what it writes.

An example that has been run has a `README.md` in its folder, the log of the one run chosen for it,
and every one of them is this script's output, so their headings and sentences cannot drift apart:
only what a run recorded and what somebody wrote about it differ from one to the next. So a log is
changed in its notes and this script run, never in the README, and a test fails when a README says
anything else.

    uv run --no-sync python scripts/example_log.py add <run folder> --speed 10  # copy a run in
    uv run --no-sync python scripts/example_log.py write             # rewrite logs and the table
    uv run --no-sync python scripts/example_log.py check             # exit 1 if any would change

What `add` needs is the run folder with its video left inside it, under any name, or named with
`--video`, and how many times faster than real time that video plays, which is read off the video.
A command run after the run to put the arm right, such as `quackd robot release`, writes no run
folder, so its terminal output, saved as text, comes in with `--after` and follows the run's trace.
An example run in several parts keeps each under `log/part-N`, named with `--part N`, and has one
README. A part that is a command with no run folder comes in with `--text <file>` in place of the
folder, and needs `--example`, `--version`, `--started` and `--video`, since nothing recorded them.
What goes where, in `docs/examples/lerobot/e114/`:

    README.md          the log, written here
    log/notes.yaml     what somebody wrote: the result, why it matters, what the run showed
    log/summary.json   the run's own record, copied as it was
    log/terminal.txt   the run's own terminal, copied as it was
    log/after.txt      what the commands after the run printed, as somebody saved it, if any
    log/media.json     how fast the GIF plays, how many frames were kept, which run this is
    log/run.gif        made from the video
    log/frame-N.jpg    what the robot's camera saw at the start, the middle and the end

An example with no file of its own gets a short page here until it has a log, because git keeps no
empty folder and a number on the run sheet should always have somewhere to open.

`write` and `check` need neither ffmpeg nor the run folders, and read nothing outside this tree.
What to write in the notes is under How a log is written in `docs/examples/lerobot/README.md`.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
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
VLA = "vla"
"""The `provider` of a run `--controller vla` flew. It is `pricing.VLA_PILOT`, spelled here because
this script imports nothing of quackd's, and a test holds the two together."""
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

{films}

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

{endings}

## The exact command

{commands}

## The numbers

{numbers}

## What the robot saw

{frames}

## Try it yourself

{try_intro}

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

FILM = """\
![{id}, filmed with an external camera]({gif})

Filmed with an external camera. The GIF plays at {speed}x speed."""

ENDED = """\
The run ended with **{outcome}**. The reason on record:

> {reason}"""

COMMAND = """\
The following command was run with quackd {version}:

{fence}text
{command}
{fence}"""

NO_LOG_YET = """\
# {id}: {title}

This example has no log yet, and no file of its own: its lines run a command, a task that ships
with quackd or another example's task file.
They are on [its card on the run sheet](../run-sheet.md#{card}).

Once it has been run, its log replaces this page.
[All examples](../README.md)
"""

TRY_ON_THE_MOCK = """\
This runs the same {what} with no arm attached, on `lerobot:mock`, an arm that lives in memory.
It shows the rules a run is held to and not the physics. Run it from a checkout of this repository
with quackd's `lerobot` extra and the LLM's own SDK and key. It costs what the LLM charges."""

TRY_ON_THE_SIMULATOR = """\
This runs the same {what} with no arm attached, on `lerobot:mujoco`, the arm's simulator, against a
policy server. Leave the first command running in a terminal of its own, and answer the second's
questions yourself, as a person did on the arm. It shows a simulated arm and not yours. Run it from
a checkout of this repository with quackd's `lerobot-sim` extra, and `lerobot-vla` as well if the
policy is a checkpoint. No LLM is asked, so it costs nothing."""


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


def _complete(name: str, notes: dict[str, Any], parts: int = 1) -> None:
    """Refuse a log with a gap in it: a README is only ever whole."""
    texts = ("hook", "why_example", "why_result", "goal", "expected")
    missing = [key for key in texts if notes.get(key) in (None, "", TODO)]
    if notes.get("goal_met") not in GOAL_MET:
        missing.append(f"goal_met (one of {', '.join(GOAL_MET)})")
    missing += [
        key for key in ("showed", "did_not_show") if not notes.get(key) or TODO in notes[key]
    ]
    labels = notes.get("parts") or []
    if parts > 1 and (len(labels) != parts or TODO in labels):
        missing.append(f"parts (one line for each of the {parts})")
    if missing:
        raise LogError(f"{name}/log/notes.yaml still needs: {', '.join(missing)}")


def _parts(folder: Path) -> list[Path]:
    """Where a log's records are kept: `log/` itself, or `log/part-N` for an example in parts."""
    log = folder / "log"
    found = [path for path in log.glob("part-*") if path.is_dir()]
    return sorted(found, key=lambda path: int(path.name.removeprefix("part-"))) or [log]


def _record(part: Path) -> dict[str, Any]:
    """A run's own `summary.json`, or what `add --text` read off a command that writes none."""
    path = part / "summary.json"
    record: dict[str, Any] = json.loads(_read(path if path.exists() else part / "record.json"))
    return record


def _joined(values: Iterable[str]) -> str:
    """Each different value once, in order: the parts of a log mostly agree."""
    return " / ".join(dict.fromkeys(values))


def _typed(line: str, name: str) -> str:
    """The command at the head of a trace: quackd's `$ ...`, or a saved terminal's prompt line."""
    if line.startswith("$ "):
        return line.removeprefix("$ ")
    found = re.search(r"> (quackd .*)$", line)
    if found is None:
        raise LogError(f"{name}'s terminal.txt does not open with the command it ran")
    return found.group(1)


def _duck_path(summary: dict[str, Any]) -> str | None:
    """The task file's path inside this repository, if the command named one there."""
    for word in summary["command"]:
        path = word.replace("\\", "/")
        if path.endswith(".duck") and "docs/examples/lerobot/" in path:
            return "docs/examples/lerobot/" + path.split("docs/examples/lerobot/", 1)[1]
    return None


def _pilot(summary: dict[str, Any]) -> str:
    # `model` is what a run recorded before 0.32.0 says, and a log keeps what its run wrote.
    provider, llm_id = summary.get("provider"), summary.get("llm_id") or summary.get("model")
    return f"{provider}:{llm_id}" if llm_id else str(provider)


def _goal(summary: dict[str, Any]) -> str | None:
    """The goal the command gave in place of a task file, if it gave one."""
    words = summary["command"]
    for word, following in itertools.pairwise(words):
        if word in ("--goal", "-g"):
            return str(following)
    for word in words:
        if word.startswith("--goal="):
            return str(word).removeprefix("--goal=")
    return None


def _try_intro(summary: dict[str, Any]) -> str:
    said = TRY_ON_THE_SIMULATOR if summary.get("provider") == VLA else TRY_ON_THE_MOCK
    return said.format(what="task file" if _goal(summary) is None else "goal")


def _try_it(summary: dict[str, Any], notes: dict[str, Any]) -> str:
    goal = _goal(summary)
    words = summary["command"]
    task = f'--goal "{goal}"' if goal is not None else _duck_path(summary) or words[2]
    if summary.get("provider") != VLA:
        return f"quackd run {task} --robot lerobot:mock --llm {_pilot(summary)} --no-memory"
    # `lerobot:mock` plays a scripted policy of its own and reaches no server, so a VLA pilot's run
    # is tried on the simulator, against the policy it ran. A checkpoint's pins, cameras and rate
    # are in no record, so its server's line is the one somebody typed, from the notes.
    typed = [line for line in notes.get("before") or [] if "quackd policy serve" in line]
    served = str((summary.get("policy") or {}).get("policy") or "scripted:hold")
    if not typed and not served.startswith("scripted:"):
        raise LogError(
            f"this run's policy is {served}: put the `quackd policy serve` line it ran under "
            "`before` in the notes, because what that line pinned is in no record"
        )
    serve = typed[0] if typed else f"quackd policy serve --policy {served}"
    # what the run needed of the command itself, which a try on the simulator needs as well
    kept = [flag for flag in ("--accept-other-frame", "--no-play") if flag in words]
    line = ["quackd run", task, "--robot lerobot:mujoco", "--policy-url http://127.0.0.1:9875"]
    return serve + "\n" + " ".join([*line, "--controller vla", *kept, "--no-memory"])


def _task_file(summary: dict[str, Any], folder: Path) -> str:
    path = _duck_path(summary)
    if _goal(summary) is not None:
        return "none, the command gave a goal"
    if path is None:
        return f"`{summary['duck']}`, which ships with quackd"
    inside = path.removeprefix("docs/examples/lerobot/")
    here = f"{folder.name}/"
    link = inside.removeprefix(here) if inside.startswith(here) else f"../{inside}"
    return f"[`{inside.rsplit('/', 1)[1]}`]({link})"


def _numbers(
    summary: dict[str, Any], folder: Path, run_folder: str | None
) -> list[tuple[str, object]]:
    # a command with no run folder asks no LLM, and has none of a run's counters
    ran = summary.get("provider") is not None
    usage = summary.get("usage") or {}
    cost = summary.get("cost_usd")
    step = ((summary.get("final_state") or {}).get("extras") or {}).get("step_deg")
    moved = summary.get("first_motion_s")
    # a VLA pilot's run asks no LLM: its pilot takes turns, and nothing waited for an answer
    vla = summary.get("provider") == VLA
    waited = _seconds(summary.get("llm_latency_s")) if ran else "none"
    waiting = [] if vla else [("Waiting for the LLM", waited)]
    if not ran:
        first_motion = "not recorded"
    elif moved is None:
        first_motion = "no verb moved the arm"
    else:
        first_motion = f"{moved:.1f} s after the start"
    tokens = f"{usage.get('input_tokens', 0):,} in, {usage.get('output_tokens', 0):,} out"
    rows: list[tuple[str, object]] = [
        ("quackd version", summary["version"]),
        ("Started", summary["started_at"][:19].replace("T", " ") + " UTC"),
        ("Robot", f"`{summary['robot']}` ({summary.get('transport')})"),
        ("Pilot" if vla else "LLM", f"`{_pilot(summary)}`" if ran else "none"),
        ("Task file", _task_file(summary, folder) if ran else "none"),
        ("Steps", summary.get("steps", "none")),
        ("Turns" if vla else "LLM calls", summary.get("llm_calls", "none")),
        ("Total time", _seconds(summary.get("wall_s"))),
        ("Connecting", _seconds(summary.get("connect_s"))),
        *waiting,
        ("First motion", first_motion),
        ("Tokens", tokens if ran else "none"),
        (
            "Cost at list price",
            "none" if not ran else "not recorded" if cost is None else f"${cost:.4f}",
        ),
        ("Speed cap", "not recorded" if step is None else f"{step:g}° per send"),
        ("Run folder", f"`runs\\{run_folder}`" if run_folder else "none, it writes no run folder"),
    ]
    return rows


def _numbers_table(parts: list[list[tuple[str, object]]]) -> str:
    """One column of values for a log, and one per part for a log in parts."""
    named = [f" Part {n} |" for n in range(1, len(parts) + 1)] if len(parts) > 1 else [" |"]
    head = ["| |" + "".join(named), "|---|" + "---|" * len(parts)]
    rows = [
        "| " + column[0][0] + " |" + "".join(f" {value} |" for _, value in column)
        for column in (list(row) for row in zip(*parts, strict=True))
    ]
    return "\n".join(head + rows)


def _frames(count: int, where: str) -> str:
    if count == 0:
        return "This run used no camera."
    labels = {1: ["Start"], 2: ["Start", "End"], 3: ["Start", "Middle", "End"]}[count]
    pictures = [f"![{label}]({where}/frame-{i}.jpg)" for i, label in enumerate(labels, start=1)]
    return "\n".join(
        [f"| {' | '.join(labels)} |", f"|{'---|' * count}", f"| {' | '.join(pictures)} |"]
    )


def render_log(root: Path, experiment: dict[str, str]) -> str:
    """One example's README, whole, from what is in its folder. Refuses rather than leave a gap."""
    folder = root / experiment["id"].lower()
    notes = notes_of(folder)
    if notes is None:
        raise LogError(f"{folder.name} has no log/notes.yaml")
    parts = _parts(folder)
    _complete(folder.name, notes, len(parts))
    labels = notes.get("parts") or []

    def tagged(n: int, body: str) -> str:
        if len(parts) == 1:
            return body
        return f"**Part {n} of {len(parts)}:** {_flat(labels[n - 1])}\n\n{body}"

    records, films, endings, commands, numbers, frames, traces = [], [], [], [], [], [], []
    for n, part in enumerate(parts, start=1):
        summary = _record(part)
        if "drive" in summary:
            raise LogError(
                f"{folder.name} holds a chat session's record, and this script writes the log of a "
                "run: a session has no task file, no version and no command to copy"
            )
        media: dict[str, Any] = json.loads(_read(part / "media.json"))
        trace = _read(part / "terminal.txt").rstrip("\n")
        command = "\n".join(
            [*(notes.get("before") or []), _typed(trace.split("\n", 1)[0], folder.name)]
        )
        if (part / "after.txt").exists():
            trace += "\n\n" + _read(part / "after.txt").rstrip("\n")
        where = part.relative_to(folder).as_posix()
        records.append(summary)
        film = FILM.format(id=experiment["id"], gif=f"{where}/run.gif", speed=f"{media['speed']:g}")
        films.append(tagged(n, film))
        reason = _flat(summary.get("reason") or "No reason was recorded.")
        ended = ENDED.format(outcome=str(summary["outcome"]).upper(), reason=reason)
        endings.append(tagged(n, ended))
        typed = COMMAND.format(version=summary["version"], fence=_fence(command), command=command)
        commands.append(tagged(n, typed))
        numbers.append(_numbers(summary, folder, media["run_folder"]))
        frames.append(tagged(n, _frames(int(media["frames"]), where)))
        traces.append(trace)
    trace = "\n\n".join(traces)
    # the first part that is a run says how to try it: a part with no run has nothing to try
    runnable = next((r for r in records if r.get("provider") is not None), records[0])
    text = LOG.format(
        id=experiment["id"],
        title=experiment["title"],
        hook=_flat(notes["hook"]),
        films="\n\n".join(films),
        category=experiment["category"],
        goal_met=notes["goal_met"],
        outcome=" / ".join(str(r["outcome"]).upper() for r in records),
        date=_joined(r["started_at"][:10] for r in records),
        version=_joined(r["version"] for r in records),
        why_example=_flat(notes["why_example"]),
        why_result=_flat(notes["why_result"]),
        goal=_flat(notes["goal"]),
        expected=_flat(notes["expected"]),
        showed=_bullets(notes["showed"]),
        did_not_show=_bullets(notes["did_not_show"]),
        endings="\n\n".join(endings),
        commands="\n\n".join(commands),
        numbers=_numbers_table(numbers),
        frames="\n\n".join(frames),
        try_intro=_try_intro(runnable),
        try_it=_try_it(runnable, notes),
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


def _holds_nothing_of_its_own(folder: Path) -> bool:
    """Whether an example's only file is this script's page, or it has no folder yet."""
    return not folder.exists() or all(path.name == "README.md" for path in folder.iterdir())


def render_no_log_yet(experiment: dict[str, str]) -> str:
    """The page of an example with no file of its own and no log, sending a reader to its card."""
    heading = f"{experiment['id']}: {experiment['title']}"
    card = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")  # GitHub's rule for an anchor
    text = NO_LOG_YET.format(id=experiment["id"], title=experiment["title"], card=card)
    _says_llm(f"{experiment['id'].lower()}/README.md", text)
    return text


def render_table(root: Path) -> str:
    """The table in the examples' README: one row per experiment, ticked once it has a log."""
    rows = []
    done = 0
    all_of_them = experiments(root)
    for experiment in all_of_them:
        folder = root / experiment["id"].lower()
        name = _cell(f"{experiment['id']}: {experiment['title']}")
        link = f"[{name}]({folder.name}/)"
        notes = notes_of(folder)
        if notes is None:
            rows.append(f"|  | {link} | {experiment['category']} |  |  |  |")
            continue
        done += 1
        version = _joined(_record(part)["version"] for part in _parts(folder))
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
        elif _holds_nothing_of_its_own(folder):
            wanted[folder / "README.md"] = render_no_log_yet(experiment)
    return wanted


def write_all(root: Path) -> list[Path]:
    changed = []
    for path, text in _expected(root).items():
        if not path.exists() or _read(path) != text:
            path.parent.mkdir(exist_ok=True)
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
        stray = notes_of(folder) is None and not _holds_nothing_of_its_own(folder)
        if stray and (folder / "README.md").exists():
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
    root: Path,
    run_dir: Path,
    speed: float,
    example: str | None = None,
    replace: bool = False,
    after: Sequence[Path] = (),
    video: Path | None = None,
    part: int | None = None,
) -> Path:
    """Make this run its example's log and leave notes for somebody to fill in.

    An example has one log. Adding the same run again, as a better video does, keeps what was
    written about it. A different run starts the notes over, because they described the old one, so
    it takes `replace`, and the old log stays in git's history. `after` is the saved terminal output
    of the commands run once the run was over, in the order they ran."""
    matched = re.fullmatch(r"\d{8}-\d{6}-(?:(e\d{3})(?:-.*)?|.*)", run_dir.name)
    name = example or (matched.group(1) if matched else None)
    if name is None:
        raise LogError(f"cannot tell the example from {run_dir.name}: pass --example eNNN")
    if not (run_dir / "summary.json").exists():
        raise LogError(f"{run_dir} has no summary.json: that run never reached its end")
    summary = _read(run_dir / "summary.json")
    trace = _read(run_dir / "terminal.txt")
    later = "\n".join(_read(path).removeprefix("﻿").rstrip("\n") for path in after)
    copied = [("terminal.txt", trace), ("summary.json", summary)]
    copied += [(path.name, _read(path)) for path in after]
    for what, text in copied:
        if PRIVATE.search(text):
            raise LogError(f"{what} holds a key or a path into a home folder: not copying it")
    if video is None:
        video = _video_in(run_dir)
    elif not video.is_file():
        raise LogError(f"no video at {video}")

    log = _log_of(root / name.lower() / "log", part)
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
    if after:
        _write(log / "after.txt", later + "\n")
    media = {"speed": speed, "frames": frames, "run_folder": run_dir.name}
    _write(log / "media.json", json.dumps(media, indent=2) + "\n")
    return _notes_for(root / name.lower() / "log", part)


def _log_of(home: Path, part: int | None) -> Path:
    """Where this add keeps its record, refusing to mix a whole log with one in parts."""
    if part is None and any(home.glob("part-*")):
        raise LogError(f"{home.parent.name}'s log is in parts: pass --part")
    if part is not None and (home / "media.json").exists():
        raise LogError(f"{home.parent.name} has a whole log already: it cannot also have parts")
    return home if part is None else home / f"part-{part}"


def _notes_for(home: Path, part: int | None) -> Path:
    notes = home / "notes.yaml"
    if not notes.exists():
        _write(notes, NOTES if part is None else NOTES + f"parts:\n  - {TODO}\n")
    return notes


def add_text(
    root: Path,
    text_file: Path,
    speed: float,
    example: str,
    version: str,
    started: str,
    video: Path,
    part: int | None = None,
) -> Path:
    """Log what a command with no run folder printed, such as `quackd robot rest-pose`.

    Nothing recorded its version or when it ran, so somebody says, and what is not in its output
    reads as not recorded. It ended as its last line marked `✓` or `✗` says."""
    trace = _read(text_file).removeprefix("\ufeff")
    if PRIVATE.search(trace):
        raise LogError(f"{text_file.name} holds a key or a path into a home folder: not copying it")
    if not video.is_file():
        raise LogError(f"no video at {video}")
    if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", started) is None:
        raise LogError(f"--started takes a UTC time like 2026-11-06T12:27:26Z, not {started}")
    typed = _typed(trace.split("\n", 1)[0], text_file.name)
    marked = [line for line in trace.splitlines() if line[:2] in ("✓ ", "✗ ")]
    if not marked:
        raise LogError(f"{text_file.name} has no line marked with ✓ or ✗ to say how it ended")
    body = re.search(r"^(\S+) \(\w+:(\w+)\) is at", trace, flags=re.MULTILINE)
    record = {
        "command": [word.strip("\"'") for word in shlex.split(typed, posix=False)],
        "version": version,
        "started_at": started,
        "outcome": "success" if marked[-1].startswith("✓") else "error",
        "reason": marked[-1][2:],
        "robot": body.group(1) if body else "not recorded",
        "transport": body.group(2) if body else "not recorded",
    }
    home = root / example.lower() / "log"
    log = _log_of(home, part)
    log.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        made = Path(scratch) / "run.gif"
        make_gif(video, made)
        shutil.copyfile(made, log / "run.gif")
    _write(log / "terminal.txt", trace)
    _write(log / "record.json", json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    media = {"speed": speed, "frames": 0, "run_folder": None}
    _write(log / "media.json", json.dumps(media, indent=2) + "\n")
    return _notes_for(home, part)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    verbs = parser.add_subparsers(dest="verb", required=True)
    add = verbs.add_parser("add", help="make a run its example's log, and make the GIF")
    add.add_argument("run_folder", type=Path, nargs="?")
    add.add_argument(
        "--speed",
        type=float,
        required=True,
        help="how many times faster than real time the video plays, read off the video: 1 is real",
    )
    add.add_argument("--example", help="the example, where the run folder's name does not say")
    add.add_argument("--replace", action="store_true", help="log this run instead of the old one")
    add.add_argument("--video", type=Path, help="the video, where it is not in the run folder")
    add.add_argument(
        "--after",
        type=Path,
        action="append",
        default=[],
        help="a text file of what a command run after the run printed; once per command, in order",
    )
    add.add_argument("--part", type=int, help="which part of an example run in parts this is")
    add.add_argument("--text", type=Path, help="what a command with no run folder printed")
    add.add_argument("--version", help="with --text: the quackd version that ran it")
    add.add_argument("--started", help="with --text: when it ran, as 2026-11-06T12:27:26Z")
    verbs.add_parser("write", help="rewrite every log and the table")
    verbs.add_parser("check", help="exit 1 if any log or the table would change")
    args = parser.parse_args(argv)
    try:
        if args.verb == "add" and args.text is not None:
            needs = (args.example, args.version, args.started, args.video)
            if None in needs:
                parser.error("--text needs --example, --version, --started and --video")
            notes = add_text(
                EXAMPLES,
                args.text,
                args.speed,
                args.example,
                args.version,
                args.started,
                args.video,
                args.part,
            )
            print(f"Copied. Fill in {notes}, then run: example_log.py write")
        elif args.verb == "add":
            if args.run_folder is None:
                parser.error("add needs a run folder, or --text")
            notes = add_run(
                EXAMPLES,
                args.run_folder,
                args.speed,
                args.example,
                args.replace,
                args.after,
                args.video,
                args.part,
            )
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
