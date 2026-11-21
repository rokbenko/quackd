"""Hold every released section of CHANGELOG.md to the bytes it shipped with.

A released section is history: what a version said to the people who installed it. Nothing
tested its text, so RELEASING.md asked for a `diff` against the last tag by hand, and tests that
each pinned one sentence of an old section stood in for the rest. This keeps one SHA-256 per
released section in `tests/golden/changelog_released.json`, and `tests/test_changelog_lock.py`
fails when a section's bytes differ from its hash:

    python scripts/changelog_lock.py              # check: exit 1 naming each section that moved
    python scripts/changelog_lock.py add 0.35.0   # the release step: lock the newest section
    python scripts/changelog_lock.py update --accept "why the history had to change"

A section runs from its `## [` heading line, the date in it included, to the line before the
next `## [` heading. The oldest one stops before the reference links at the foot of the file,
which every release rewrites and a test of their own holds. Line ends are read as LF whatever
the checkout wrote, so a CRLF checkout locks the same bytes. `[Unreleased]` is never locked: a
section is released when its heading names a version.

`add` locks only the newest released section, and only once. `update` changes a hash only with a
reason, which it keeps in the file's `accepted` list beside the old and the new hash and the
day. Standard library only, so it runs on whatever Python a release is cut with.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHANGELOG = REPO / "CHANGELOG.md"
LOCK = REPO / "tests" / "golden" / "changelog_released.json"

_HEADING = re.compile(r"^## \[([^\]]+)\]", re.M)
_LINK = re.compile(r"^\[[^\]]+\]: ", re.M)
_VERSION = re.compile(r"\d+\.\d+\.\d+")


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sections(text: str) -> dict[str, str]:
    """Every `## [` section of a changelog, by the name in its brackets, in the file's order:
    from its heading line to the line before the next heading, the last one to the line before
    the first reference link below it."""
    text = _lf(text)
    starts = list(_HEADING.finditer(text))
    found: dict[str, str] = {}
    for number, match in enumerate(starts):
        if number + 1 < len(starts):
            end = starts[number + 1].start()
        else:
            link = _LINK.search(text, match.end())
            end = link.start() if link else len(text)
        name = match.group(1)
        if name in found:
            raise ValueError(f"CHANGELOG.md has two sections named [{name}]")
        found[name] = text[match.start() : end]
    return found


def released(text: str) -> dict[str, str]:
    """The sections whose heading names a version, newest first, as the file lists them."""
    return {name: body for name, body in sections(text).items() if _VERSION.fullmatch(name)}


def digest(section: str) -> str:
    return hashlib.sha256(_lf(section).encode("utf-8")).hexdigest()


def problems(text: str, lock: dict) -> list[str]:
    """What keeps the changelog from matching the lock, a line apiece; none when it does."""
    held: dict[str, str] = lock.get("sections", {})
    found = released(text)
    out = []
    for name, body in found.items():
        if name not in held:
            out.append(
                f"[{name}] is released and has no hash: run `python scripts/changelog_lock.py "
                f"add {name}` (RELEASING.md, step 4)"
            )
        elif digest(body) != held[name]:
            out.append(
                f"[{name}] changed after it was released: put its text back as it was locked "
                "(`git log -p CHANGELOG.md`), or accept the change with a reason: run "
                '`python scripts/changelog_lock.py update --accept "<why>"`'
            )
    for name in held:
        if name not in found:
            out.append(f"[{name}] has a hash and no section in CHANGELOG.md")
    return out


def _read_lock(path: Path) -> dict:
    loaded: dict = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _write_lock(path: Path, lock: dict) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(lock, indent=2, ensure_ascii=False) + "\n")


def _ordered(text: str, held: dict[str, str]) -> dict[str, str]:
    """The hashes in the changelog's order, newest first, those without a section last."""
    names = [name for name in released(text) if name in held]
    return {name: held[name] for name in [*names, *(n for n in held if n not in names)]}


def add(text: str, lock: dict, version: str) -> dict:
    """The lock with `version`'s hash added. Only the newest released section, and only once."""
    found = released(text)
    if version not in found:
        raise SystemExit(f"CHANGELOG.md has no released section [{version}]")
    newest = next(iter(found))
    if version != newest:
        raise SystemExit(f"[{version}] is not the newest released section; [{newest}] is")
    held = dict(lock.get("sections", {}))
    if version in held:
        raise SystemExit(f"[{version}] is locked already; a change to it is `update --accept`")
    held[version] = digest(found[version])
    return {**lock, "sections": _ordered(text, held), "accepted": lock.get("accepted", [])}


def update(text: str, lock: dict, reason: str, today: str) -> tuple[dict, list[str]]:
    """The lock with every changed hash replaced, each logged with `reason`; and the versions
    it changed. A section with no hash is `add`'s, and a hash with no section stays a problem."""
    if not reason.strip():
        raise SystemExit("--accept needs a reason: it is kept in the lock beside the two hashes")
    found = released(text)
    held = dict(lock.get("sections", {}))
    accepted = list(lock.get("accepted", []))
    changed = []
    for name, body in found.items():
        new = digest(body)
        if name in held and held[name] != new:
            accepted.append(
                {"version": name, "old": held[name], "new": new, "reason": reason, "date": today}
            )
            held[name] = new
            changed.append(name)
    return {**lock, "sections": _ordered(text, held), "accepted": accepted}, changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--changelog", type=Path, default=CHANGELOG, help=argparse.SUPPRESS)
    parser.add_argument("--lock", type=Path, default=LOCK, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("check", help="the default: exit 1 naming each section that moved")
    adding = commands.add_parser("add", help="lock the newest released section, once")
    adding.add_argument("version")
    updating = commands.add_parser("update", help="accept a changed section, with a reason")
    updating.add_argument("--accept", required=True, metavar="REASON")
    args = parser.parse_args(argv)

    text = args.changelog.read_text(encoding="utf-8")
    lock = _read_lock(args.lock) if args.lock.exists() else {"sections": {}, "accepted": []}
    if args.command == "add":
        lock = add(text, lock, args.version)
        _write_lock(args.lock, lock)
        print(f"locked [{args.version}]")
    elif args.command == "update":
        lock, changed = update(text, lock, args.accept, datetime.date.today().isoformat())
        if not changed:
            print("no locked section changed: nothing to accept")
            return 1
        _write_lock(args.lock, lock)
        print("accepted " + ", ".join(f"[{name}]" for name in changed))
    found = problems(text, lock)
    for line in found:
        print(line, file=sys.stderr)
    if found:
        return 1
    if args.command in (None, "check"):
        print(f"{len(lock['sections'])} released sections as locked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
