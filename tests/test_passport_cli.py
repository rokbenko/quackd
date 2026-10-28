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

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
VECTORS = REPO / "docs" / "passport" / "vectors"
KEYS = json.loads((VECTORS / "keys.json").read_text(encoding="utf-8"))["keys"]
SHORT_A = KEYS["robot-a"]["short_id"]
SHORT_OWNER = KEYS["owner"]["short_id"]


def _reg(tmp_path: Path) -> list[str]:
    return ["--registry-dir", str(tmp_path)]


# ── show, set, owner ────────────────────────────────────────────────────────────────────


# ── check, from files and stdin ─────────────────────────────────────────────────────────


def test_check_shows_a_signed_passport_and_its_stamp() -> None:
    checked = runner.invoke(app, ["passport", "check", str(VECTORS / "microduck.jws")])
    assert checked.exit_code == 0, checked.output
    flat = " ".join(checked.output.split())
    assert "claim signed by its own key" in flat
    # the stamp by the key that made it, not a count: any key can stamp any robot
    assert f"stamp owner {SHORT_OWNER}, a key nobody here pinned, until" in flat


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
    """A host serving robot A's passport at its well-known URL, signed beside it, and one
    redirect; robot A's unsigned passport alone under /unsigned/; and under /fake/ the unsigned
    one served where the signed one belongs."""
    well_known = tmp_path / ".well-known"
    well_known.mkdir()
    (well_known / "robot-passport.json").write_bytes((VECTORS / "minimal.json").read_bytes())
    (well_known / "robot-passport.jws").write_bytes((VECTORS / "microduck.jws").read_bytes())
    for folder in ("unsigned", "fake"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "robot-passport.json").write_bytes(
            (VECTORS / "minimal.json").read_bytes()
        )
    (tmp_path / "fake" / "robot-passport.jws").write_bytes((VECTORS / "minimal.json").read_bytes())

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


def test_what_the_signed_url_serves_must_be_signed(server: str) -> None:
    """A host that answers the .jws URL with the unsigned document would talk a reader down
    from a signed passport to a claim without its noticing."""
    refused = runner.invoke(app, ["passport", "check", f"{server}/fake/robot-passport.json"])
    assert refused.exit_code == 1
    assert "refused under 4.4" in " ".join(refused.output.split())


def test_a_redirect_is_refused_rather_than_followed(server: str) -> None:
    refused = runner.invoke(app, ["passport", "check", f"{server}/moved"])
    assert refused.exit_code == 1
    assert "follows no redirects" in " ".join(refused.output.split())


def test_a_url_that_answers_404_is_refused_with_the_status(server: str) -> None:
    refused = runner.invoke(app, ["passport", "check", f"{server}/nothing.json"])
    assert refused.exit_code == 1 and "404" in refused.output
