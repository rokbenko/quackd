"""Regenerate `contributors.svg`: one circle per human who has contributed.

Two hosted services were tried first and neither was right. `contrib.rocks` excludes bots
and sizes evenly, but its backend was still three days behind minutes after two people's
work merged, so the README would have thanked one person for a week. `contrib.nn.ci` is
current, but it has no bot filter and does not normalise a non-square avatar, so one face
came out larger than the rest.

So the image is ours: humans only (the GitHub API says who is a `Bot`), ordered by lines
added rather than commit count, every avatar clipped to the same circle with
`preserveAspectRatio="xMidYMid slice"` so a rectangular one is cropped rather than
squashed, and every byte embedded, so the file needs no network when GitHub renders it.

`.github/workflows/contributors.yml` runs this on every push to `main` and weekly, and
commits the result only when it changes. Run it by hand the same way:

    python docs/assets/contributors.py

Set `GITHUB_TOKEN` to avoid the anonymous API rate limit. Standard library only, because
this also runs on a bare CI image.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = "rokbenko/quackd"
OUT = Path(__file__).with_name("contributors.svg")

SIZE = 64  # rendered diameter of one avatar
GAP = 10  # space between circles
PER_ROW = 12
RING = "#d0d7de"  # a hairline, so a white or dark avatar still reads as a circle
PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")  # the 8 bytes every PNG starts with


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "quackd-contributors"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return bytes(response.read())


def email_to_login() -> dict[str, str]:
    """Which GitHub account authored which git email, from the commits listing.

    Only the API knows this: a git commit carries an email, and only GitHub can say the
    account behind it. One page per hundred commits, which is cheap at this size."""
    mapping: dict[str, str] = {}
    for page in range(1, 21):  # 2000 commits is far past where this stops being cheap
        batch = json.loads(
            _get(f"https://api.github.com/repos/{REPO}/commits?per_page=100&page={page}")
        )
        for commit in batch:
            author, meta = commit.get("author"), commit.get("commit", {}).get("author", {})
            if author and meta.get("email"):
                mapping[meta["email"].lower()] = author["login"]
        if len(batch) < 100:
            break
    return mapping


def lines_added() -> dict[str, int]:
    """Lines added per GitHub account, counted from the git history in this checkout.

    Ordering by commits would put somebody who pushed six small commits ahead of somebody
    who sent one considered feature, which is backwards for a row that says thank you.

    GitHub has a stats endpoint that reports exactly this, and it was the first thing tried
    here. It computes on demand and answers 202 with an empty body while it does, and for
    this repository it stayed at 202 through forty seconds of polling, so the row would
    have silently fallen back to commit order most of the time. `git log` is exact, needs
    no cache to warm and cannot rate limit, so it is the source and the API is used only to
    say which account an email belongs to. Needs full history: the workflow checks out with
    `fetch-depth: 0`."""
    log = subprocess.run(
        ["git", "log", "--no-merges", "--numstat", "--format=%x01%aE"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )
    if log.returncode != 0:
        print("git log failed, falling back to commit order", file=sys.stderr)
        return {}
    by_email: dict[str, int] = {}
    email = ""
    for line in log.stdout.splitlines():
        if line.startswith("\x01"):
            email = line[1:].strip().lower()
        elif line.strip() and email:
            added, _, _ = line.partition("\t")
            if added.isdigit():  # "-" for a binary file
                by_email[email] = by_email.get(email, 0) + int(added)
    logins = email_to_login()
    totals: dict[str, int] = {}
    for address, count in by_email.items():
        login = logins.get(address)
        if login and not login.endswith("[bot]"):
            totals[login] = totals.get(login, 0) + count
    return totals


def already_drawn() -> set[str]:
    """Who the committed image currently shows, so a bad answer cannot drop somebody.

    The login is everything before the first colon in a `<title>`, which is the one part
    of the caption that must not change shape without this being updated with it."""
    if not OUT.exists():
        return set()
    titles = re.findall(r"<title>([^<]+)</title>", OUT.read_text(encoding="utf-8"))
    return {title.split(":")[0].strip() for title in titles}


def humans() -> list[dict[str, str]]:
    """Contributors with every `Bot` account dropped, most lines added first.

    Dependabot is a real contributor to this repository and still does not belong in a row
    of faces under the words "thank you to everyone who has sent quackd code".

    Both endpoints are eventually consistent, and the first run of the workflow proved it:
    called from a runner seconds after two contributions merged, `/contributors` answered
    200 with one name and the image lost two people. So the two sources are unioned, and
    `main` refuses to write a set that has lost anybody."""
    listed = json.loads(_get(f"https://api.github.com/repos/{REPO}/contributors?per_page=100"))
    by_login = {c["login"]: c for c in listed if c.get("type") == "User"}
    added = lines_added()
    if not added:
        print("line counts unavailable, falling back to commit order", file=sys.stderr)
    # somebody the stats endpoint knows and the contributors list has not caught up on
    for login in added:
        if login not in by_login and not login.endswith("[bot]"):
            by_login[login] = {
                "login": login,
                "avatar_url": f"https://github.com/{login}.png",
                "html_url": f"https://github.com/{login}",
                "contributions": 0,
            }
    people = sorted(
        by_login.values(),
        key=lambda c: (-added.get(c["login"], 0), -c.get("contributions", 0), c["login"]),
    )
    return [
        {
            "login": c["login"],
            "avatar": c["avatar_url"],
            "url": c["html_url"],
            "added": str(added.get(c["login"], 0)),
            "commits": str(c.get("contributions", 0)),
        }
        for c in people
    ]


def avatar_data_uri(url: str) -> str:
    """The avatar at twice the drawn size, so it stays sharp, inlined with its real type.

    The type is sniffed rather than assumed: GitHub serves whatever the person uploaded,
    and the fallback URL for somebody the contributors list has not caught up on asks for
    `.png` explicitly, so hardcoding jpeg mislabelled exactly those avatars."""
    payload = _get(f"{url}{'&' if '?' in url else '?'}s={SIZE * 2}")
    kind = "png" if payload.startswith(PNG_MAGIC) else "jpeg"
    return f"data:image/{kind};base64," + base64.b64encode(payload).decode("ascii")


def _caption(person: dict[str, str]) -> str:
    """What hovering a face says. Silent about lines when they could not be counted,
    rather than reporting everybody as zero."""
    added, commits = int(person["added"]), int(person["commits"])
    parts = []
    if added:
        parts.append(f"{added:,} lines added")
    if commits:  # zero means the contributors list has not caught up, not that they did nothing
        parts.append(f"{commits} commits")
    return f"{person['login']}: {', '.join(parts)}" if parts else person["login"]


def render(people: list[dict[str, str]]) -> str:
    columns = min(len(people), PER_ROW) or 1
    rows = (len(people) + PER_ROW - 1) // PER_ROW or 1
    width = columns * SIZE + (columns - 1) * GAP
    height = rows * SIZE + (rows - 1) * GAP
    radius = SIZE / 2

    defs: list[str] = []
    body: list[str] = []
    for i, person in enumerate(people):
        x = (i % PER_ROW) * (SIZE + GAP)
        y = (i // PER_ROW) * (SIZE + GAP)
        defs.append(
            f'<clipPath id="c{i}"><circle cx="{x + radius}" cy="{y + radius}" r="{radius}"/></clipPath>'
        )
        body.append(
            f'<a href="{person["url"]}" target="_blank" rel="noopener">'
            f"<title>{_caption(person)}</title>"
            f'<image href="{avatar_data_uri(person["avatar"])}" x="{x}" y="{y}" '
            f'width="{SIZE}" height="{SIZE}" preserveAspectRatio="xMidYMid slice" '
            f'clip-path="url(#c{i})"/>'
            f'<circle cx="{x + radius}" cy="{y + radius}" r="{radius - 0.5}" fill="none" '
            f'stroke="{RING}" stroke-width="1"/>'
            f"</a>"
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="People who have contributed to quackd">\n'
        f"<defs>{''.join(defs)}</defs>\n" + "\n".join(body) + "\n</svg>\n"
    )


def main() -> int:
    people = humans()
    if not people:
        print("no contributors returned, leaving the image alone", file=sys.stderr)
        return 0
    lost = already_drawn() - {p["login"] for p in people}
    if lost:
        # the API is eventually consistent and this image is a thank-you, so a stale answer
        # must never quietly un-thank somebody. Succeed, change nothing, say why.
        print(f"the API did not list {', '.join(sorted(lost))} this time, leaving the image "
              f"alone (it is eventually consistent; the weekly run will pick them up)",
              file=sys.stderr)
        return 0
    OUT.write_text(render(people), encoding="utf-8")
    print(f"{OUT.name}: {len(people)} people ({', '.join(p['login'] for p in people)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
