"""Every number published from a transcript is checked against the transcript.

`docs/assets/transcripts/` is evidence. The runs in it happened on machines this project has
never had, and the tables in `docs/guides/local-llms.md` quote their steps, their LLM calls and
their token counts. Two contributor PRs in a row shipped one of those tables (#7, #23) and both
times the numbers were checked by a person reading the files. A person reading is how a wrong number
gets in, so this is that reading, written down.

For every `.jsonl` in that folder: every line parses, the run is whole, it carries nothing from
the machine it ran on, `docs/assets/README.md` says how it was made, and the row in
`docs/guides/local-llms.md` that links it says what the run's own `run_end` says. The last test is
narrower. It holds the one thing the two Qwen3 files prove between them, that a note written by
one run is read by the next, so the claim on that page cannot outlive the evidence for it.

Nothing here is a fixture. A claim that cannot be re-derived from the published file is the
thing this is looking for.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from functools import cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from quackd.memory import DEFAULT_DIR as MEMORY_DEFAULT_DIR

REPO = Path(__file__).resolve().parents[1]
TRANSCRIPTS = sorted((REPO / "docs" / "assets" / "transcripts").glob("*.jsonl"))


@cache
def _assets_table() -> str:
    """docs/assets/README.md, the row per asset."""
    return (REPO / "docs" / "assets" / "README.md").read_text(encoding="utf-8")


@cache
def _local_llms() -> str:
    """docs/guides/local-llms.md, the page whose tables quote these files."""
    return (REPO / "docs" / "guides" / "local-llms.md").read_text(encoding="utf-8")


#: The two files of the Qwen3 pair, by name, because the memory claim on the page is about
#: these two and no others. If they are renamed or dropped, the last test fails and whoever
#: did it has to deal with the paragraph that cites them.
THINKING_ON = "qwen3-32b-awq-vllm-find-and-kick-seed1-thinking-on.jsonl"
THINKING_OFF = "qwen3-32b-awq-vllm-find-and-kick-seed1-thinking-off.jsonl"


def _rows(path: Path) -> list[dict[str, Any]]:
    """Every line of a transcript as a record, or a failure naming the line that is not one."""
    out: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(f"{path.name}:{number} is not JSON ({exc})")
        assert isinstance(row, dict), f"{path.name}:{number} is not an object"
        assert "kind" in row, f"{path.name}:{number} has no kind"
        out.append(row)
    return out


def _strings(node: Any) -> Iterator[str]:
    """Every string anywhere in a decoded record, keys excluded: keys are quackd's own."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _count(cell: str) -> int:
    """`2,049` is how a number is written for a reader. This is that number."""
    return int(cell.replace(",", ""))


def test_there_are_transcripts_to_check() -> None:
    """A parametrized test over an empty glob passes on nothing, which is the one way every
    other test in this file could go quiet without anybody noticing."""
    assert TRANSCRIPTS, "docs/assets/transcripts/ holds no .jsonl files"


@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda p: p.stem)
def test_a_published_transcript_is_a_whole_run(path: Path) -> None:
    """JSONL, one record a line, opening on `run_start` and closing on `run_end`.

    A file cut short still renders in `quackd log` (`test_log_of_a_cut_transcript_says_so`),
    which is right for a run that died and wrong for a file published as evidence: the totals
    every table quotes live in the last record."""
    rows = _rows(path)
    assert rows, f"{path.name} is empty"
    assert rows[0]["kind"] == "run_start", f"{path.name} opens on {rows[0]['kind']!r}"
    assert rows[-1]["kind"] == "run_end", f"{path.name} closes on {rows[-1]['kind']!r}"
    inner = [r["kind"] for r in rows[1:-1]]
    assert "run_start" not in inner and "run_end" not in inner, (
        f"{path.name} holds more than one run"
    )


# ── nothing from the machine it ran on ──────────────────────────────────────────────────

#: The one path a published transcript may keep that is not relative. `memory.path` is
#: `str(memory_dir(...))` and `memory_dir` calls `expanduser`, so a real run records
#: `/home/<somebody>/.quackd/memory/...`. Writing the default back in its unexpanded form
#: names nobody and stays true, which is what the Qwen3 pair does. Any other `~` path is a
#: person's machine and this is not the place for it. Taken from the code, not spelled here,
#: so moving the default moves the exemption with it.
HOME_DEFAULT = f"{MEMORY_DEFAULT_DIR}/"

#: What a transcript must not publish, and the name to say when it does. These run over the
#: decoded strings rather than the raw bytes, so a character written as a JSON escape
#: cannot slip past, and a Windows separator inside a frame path is not mistaken for a
#: UNC share.
LEAKS: dict[str, re.Pattern[str]] = {
    "a URL": re.compile(r"\b[a-z][a-z0-9+.-]*://", re.I),
    "an IP address": re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    "a host and port": re.compile(r"\b[a-z0-9][a-z0-9.-]*:\d{2,5}\b", re.I),
    "a hostname": re.compile(
        r"\b(?:localhost|[a-z0-9-]+\.(?:local|lan|internal|home|corp))\b", re.I
    ),
    "a drive letter": re.compile(r"\b[A-Za-z]:[\\/]"),
    "a UNC share": re.compile(r"\\\\[A-Za-z0-9_.$-]+\\"),
    "a home directory": re.compile(r"~[A-Za-z0-9_.-]*/"),
    "an absolute path": re.compile(
        r"(?:^|[\s\"'(=])/(?:home|users|root|mnt|media|srv|opt|var|tmp|usr|etc|private)/", re.I
    ),
}


def _is_relative(value: str) -> bool:
    """Relative on both families, and a `~` counts as rooted wherever it is written."""
    return not (
        value.startswith(("~", "/", "\\"))
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
    )


@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda p: p.stem)
def test_a_published_transcript_carries_nothing_from_the_machine_it_ran_on(path: Path) -> None:
    """These are somebody else's runs, copied out of their `runs/` directory by hand.

    `run_start` carries the two paths a run knows about, and the prompt, the thinking and the
    verb results carry whatever the model and the robot said, so the sweep reads the whole
    file rather than the two fields anybody thought to scrub."""
    rows = _rows(path)
    start = rows[0]

    duck_path = start.get("duck_path")
    assert isinstance(duck_path, str) and duck_path, f"{path.name} has no duck_path"
    assert _is_relative(duck_path), f"{path.name}: duck_path is {duck_path!r}"

    memory = start.get("memory")
    if isinstance(memory, dict):
        memory_path = memory.get("path")
        assert isinstance(memory_path, str) and memory_path, f"{path.name}: memory has no path"
        assert _is_relative(memory_path) or memory_path.startswith(HOME_DEFAULT), (
            f"{path.name}: memory.path is {memory_path!r}, which is somebody's machine. "
            f"Relative, or the unexpanded default {HOME_DEFAULT!r}, and nothing else."
        )

    for number, row in enumerate(rows, 1):
        for text in _strings(row):
            for label, pattern in LEAKS.items():
                for hit in pattern.finditer(text):
                    if text[hit.start() :].startswith(HOME_DEFAULT):
                        continue  # the documented default, which is nobody's home
                    around = text[max(0, hit.start() - 40) : hit.end() + 40]
                    pytest.fail(
                        f"{path.name}:{number} publishes {label}: {hit.group(0)!r} in {around!r}"
                    )


# ── the two tables that quote them ──────────────────────────────────────────────────────


@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda p: p.stem)
def test_every_transcript_has_a_row_in_the_assets_table(path: Path) -> None:
    """`docs/assets/README.md` is the only place that says how a file here was made, and a
    recording nobody can reproduce is a recording nobody can check. Every other asset in that
    directory has a row, and a transcript is the one kind that also carries numbers."""
    row = f"| `transcripts/{path.name}` |"
    assert row in _assets_table(), f"docs/assets/README.md has no row for transcripts/{path.name}"


#: A row of either transcript table in `docs/guides/local-llms.md`, which reads
#: `| [`…seed1-thinking-on.jsonl`](../assets/transcripts/<file>) | 1 | success | 4 | 8 |
#: 35,416 + 2,049 | 0 | what it shows |`. The label is abbreviated with a Unicode ellipsis,
#: written as an escape here so no editor and no encoding can eat it, and the counts are
#: written for a reader, with thousands separators.
ELLIPSIS = "\u2026"
_ROW = re.compile(r"\|\s*\[`(?P<label>[^`]+)`\]\(\.\./assets/transcripts/(?P<file>[^)]+)\)\s*\|")
_TOKENS = re.compile(r"^(?P<input>[\d,]+)\s*\+\s*(?P<output>[\d,]+)$")


def _published_row(name: str) -> list[str]:
    """The cells of the row that links this transcript, the link cell first."""
    for line in _local_llms().splitlines():
        found = _ROW.search(line)
        if found is None or found.group("file") != name:
            continue
        label = found.group("label").lstrip(ELLIPSIS)
        # the shortened label has to be the end of the file it links, or the row is describing
        # one run and pointing at another
        assert name.endswith(label), (
            f"docs/guides/local-llms.md labels {name} as {found.group('label')!r}"
        )
        return [cell.strip() for cell in line.strip().strip("|").split("|")]
    pytest.fail(f"docs/guides/local-llms.md has no table row linking {name}")


@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda p: p.stem)
def test_the_published_row_agrees_with_the_run_it_links(path: Path) -> None:
    """The guard the last two PRs did without.

    Every figure in the row comes out of `run_end`, except the text fallbacks, which are a
    count of the `llm` rows the loop had to rescue a call from. That column is the one a
    reader uses to decide whether a small model can be trusted with the loop at all, so it is
    counted here rather than believed."""
    rows = _rows(path)
    end = rows[-1]
    llm = [r for r in rows if r["kind"] == "llm"]
    _link, seed, outcome, steps, calls, tokens, fallbacks = _published_row(path.name)[:7]

    assert f"seed{seed}" in path.name, f"{path.name}: the row says seed {seed}"
    assert outcome == end["outcome"], f"{path.name}: the row says {outcome!r}"
    assert _count(steps) == end["steps"], f"{path.name}: the row says {steps} steps"
    assert _count(calls) == end["llm_calls"], f"{path.name}: the row says {calls} LLM calls"
    # and the same column the other way round, off the rows themselves rather than the total
    assert len(llm) == end["llm_calls"], (
        f"{path.name}: {len(llm)} llm rows, run_end says {end['llm_calls']}"
    )

    split = _TOKENS.match(tokens)
    assert split is not None, f"{path.name}: the tokens cell reads {tokens!r}, not 'N + M'"
    usage = end.get("usage") or {}
    assert _count(split.group("input")) == usage["input_tokens"], f"{path.name}: input tokens"
    assert _count(split.group("output")) == usage["output_tokens"], f"{path.name}: output tokens"

    rescued = sum(1 for r in llm if r.get("stop_reason") == "text_fallback")
    assert _count(fallbacks) == rescued, (
        f"{path.name}: the row says {fallbacks} text fallbacks and the file has {rescued}"
    )


# ── the one claim the files prove between them ──────────────────────────────────────────


def test_the_note_one_qwen3_run_saved_is_read_by_the_other() -> None:
    """`docs/guides/local-llms.md` calls this pair the first published chain: a note written by one
    run and read by the next, both ends in this repository. Nothing else here can show that,
    and the page says so, so the sentence has to stay derivable from the two files.

    The counters are checked as well as the text. One note and one episode apart is what says
    no third run sat between them, which is the difference between a chain and two runs that
    happen to share a memory directory."""
    on = _rows(REPO / "docs" / "assets" / "transcripts" / THINKING_ON)
    off = _rows(REPO / "docs" / "assets" / "transcripts" / THINKING_OFF)

    saved = [r["text"] for r in on if r["kind"] == "memory" and r.get("ok") and r.get("text")]
    assert len(saved) == 1, f"{THINKING_ON} saved {len(saved)} notes, not one"
    note = saved[0]

    prompt = off[0]["system_prompt"]
    remembered = prompt.partition("What you remember")[2]
    assert remembered, f"{THINKING_OFF} has no memory section in its system prompt"
    assert note in remembered, (
        f"{THINKING_OFF} does not carry the note {THINKING_ON} saved: {note!r}"
    )
    # and the page quotes it, so a rewrite that drops the evidence drops the claim with it
    assert note in _local_llms(), f"docs/guides/local-llms.md no longer quotes the note: {note!r}"

    before, after = on[0]["memory"], off[0]["memory"]
    assert after["notes"] == before["notes"] + 1, (
        f"notes moved {before['notes']} to {after['notes']}, so a run sat between them"
    )
    assert after["episodes"] == before["episodes"] + 1, (
        f"episodes moved {before['episodes']} to {after['episodes']}, so a run sat between them"
    )
