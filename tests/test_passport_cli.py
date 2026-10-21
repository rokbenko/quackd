"""`quackd passport show|check|set|owner`: what a person types, and what they get back.

URL checks run against a real HTTP server on 127.0.0.1, so the fetch rules are proved on a
socket rather than on a fake: a bare host means its well-known URL, a redirect is refused, and a
sticker's `#rp=` must match the passport it leads to.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quackd.cli import app
from quackd.passport import document
from quackd.passport_store import PassportStore
from quackd.registry import Registry, RobotEntry

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
VECTORS = REPO / "docs" / "passport" / "vectors"
SHORT_A = json.loads((VECTORS / "keys.json").read_text(encoding="utf-8"))["keys"]["robot-a"][
    "short_id"
]


def _reg(tmp_path: Path) -> list[str]:
    return ["--registry-dir", str(tmp_path)]


# ── show, set, owner ────────────────────────────────────────────────────────────────────


def test_show_builds_a_registered_robots_passport_and_makes_its_key_once(tmp_path: Path) -> None:
    Registry(tmp_path).add_robot(RobotEntry(name="duck-a", spec="microduck:mock"))
    first = runner.invoke(app, ["passport", "show", "--robot", "duck-a", *_reg(tmp_path)])
    assert first.exit_code == 0, first.output
    assert "made a key for duck-a" in first.output
    assert "passport duck-a" in first.output and "unsigned: a claim" in first.output
    again = runner.invoke(app, ["passport", "show", "--robot", "duck-a", *_reg(tmp_path)])
    assert "made a key" not in again.output


def test_show_json_is_the_compact_passport_and_nothing_else(tmp_path: Path) -> None:
    runner.invoke(app, ["passport", "show", "--robot", "microduck:mock", *_reg(tmp_path)])
    shown = runner.invoke(
        app, ["passport", "show", "--robot", "microduck:mock", "--json", *_reg(tmp_path)]
    )
    assert shown.exit_code == 0, shown.output
    line = shown.stdout.strip()
    assert "\n" not in line
    reading = document.read_json(line.encode("utf-8"))
    assert reading.name == "microduck-mock"
    key = PassportStore(tmp_path).robot("microduck-mock")
    assert key is not None and reading.id == key.key.did


def test_set_and_owner_reach_the_passport(tmp_path: Path) -> None:
    owner = runner.invoke(
        app,
        [
            "passport",
            "owner",
            "--label",
            "Example lab",
            "--contact",
            "https://x.org",
            *_reg(tmp_path),
        ],
    )
    assert owner.exit_code == 0, owner.output
    said = runner.invoke(
        app,
        [
            "passport",
            "set",
            "--robot",
            "lerobot:mock",
            "--estop",
            "button",
            "--how-to-stop",
            "the red button",
            *_reg(tmp_path),
        ],
    )
    assert said.exit_code == 0, said.output
    shown = runner.invoke(
        app, ["passport", "show", "--robot", "lerobot:mock", "--json", *_reg(tmp_path)]
    )
    doc = json.loads(shown.stdout)
    assert doc["owner"] == {"label": "Example lab", "contact": "https://x.org"}
    assert doc["safety"]["estop"] == "button"
    assert doc["safety"]["how_to_stop"] == "the red button"


def test_set_refuses_an_estop_the_protocol_does_not_know(tmp_path: Path) -> None:
    refused = runner.invoke(
        app, ["passport", "set", "--robot", "lerobot:mock", "--estop", "lever", *_reg(tmp_path)]
    )
    assert refused.exit_code == 1
    assert "button, remote, power, none" in refused.output


def test_an_empty_owner_label_removes_it(tmp_path: Path) -> None:
    runner.invoke(app, ["passport", "owner", "--label", "Example lab", *_reg(tmp_path)])
    cleared = runner.invoke(app, ["passport", "owner", "--label", "", *_reg(tmp_path)])
    assert cleared.exit_code == 0, cleared.output
    assert PassportStore(tmp_path).owner().label is None


# ── check, from files and stdin ─────────────────────────────────────────────────────────


def test_check_shows_a_signed_passport_and_its_stamp() -> None:
    checked = runner.invoke(app, ["passport", "check", str(VECTORS / "microduck.jws")])
    assert checked.exit_code == 0, checked.output
    assert "signed by its own key, 1 of 1 stamps count" in checked.output


def test_check_refuses_with_the_rule_and_exits_1() -> None:
    refused = runner.invoke(app, ["passport", "check", str(VECTORS / "tampered.jws")])
    assert refused.exit_code == 1
    assert "refused under 4.4" in refused.output


def test_check_json_is_the_readers_view() -> None:
    checked = runner.invoke(
        app, ["passport", "check", str(VECTORS / "unknown-safety.json"), "--json"]
    )
    view = json.loads(checked.stdout)
    assert view["safety"] == {"stops_on_silence": False, "estop": "none", "how_to_stop": None}
    assert len(view["notes"]) == 2


def test_check_reads_stdin_and_lists_an_index() -> None:
    piped = runner.invoke(
        app, ["passport", "check", "-"], input=(VECTORS / "minimal.json").read_bytes()
    )
    assert piped.exit_code == 0, piped.output
    index = runner.invoke(app, ["passport", "check", str(VECTORS / "index.json")])
    assert "an index of 2 passports" in index.output


def test_check_says_when_there_is_no_such_file() -> None:
    missing = runner.invoke(app, ["passport", "check", "no-such-passport.json"])
    assert missing.exit_code == 1 and "no such file" in missing.output


# ── check, over HTTP ────────────────────────────────────────────────────────────────────


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    """A host serving robot A's passport at its well-known URL, and one redirect."""
    well_known = tmp_path / ".well-known"
    well_known.mkdir()
    (well_known / "robot-passport.json").write_bytes((VECTORS / "minimal.json").read_bytes())

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/.well-known/robot-passport.json")
                self.end_headers()
                return
            target = tmp_path / self.path.lstrip("/")
            if not target.is_file():
                self.send_response(404)
                self.end_headers()
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_bare_host_means_its_well_known_url(server: str) -> None:
    checked = runner.invoke(app, ["passport", "check", server])
    assert checked.exit_code == 0, checked.output
    assert "checked duck-01" in checked.output


def test_a_stickers_short_id_must_match_the_passport(server: str) -> None:
    good = runner.invoke(app, ["passport", "check", f"{server}#rp={SHORT_A}"])
    assert good.exit_code == 0, good.output
    bad = runner.invoke(app, ["passport", "check", f"{server}#rp=0000000000000000"])
    assert bad.exit_code == 1 and "refused under 5.2" in bad.output


def test_a_redirect_is_refused_rather_than_followed(server: str) -> None:
    refused = runner.invoke(app, ["passport", "check", f"{server}/moved"])
    assert refused.exit_code == 1
    assert "follows no redirects" in " ".join(refused.output.split())


def test_a_url_that_answers_404_is_refused_with_the_status(server: str) -> None:
    refused = runner.invoke(app, ["passport", "check", f"{server}/nothing.json"])
    assert refused.exit_code == 1 and "404" in refused.output
