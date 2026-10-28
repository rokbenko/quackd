"""`quackd passport check`: what a person types to read a passport the way a robot reads it, and
what they get back.

URL and address checks run against a real HTTP server on 127.0.0.1, so the fetch rules are proved
on a socket rather than on a fake: a bare host, or a robot's address, means its well-known URL, a
redirect is refused, and what the signed URL serves must be signed. A laptop holds no pins, so a
check shows what a passport proves and what it claims, and judges no tier.

The passports are made here, valid from yesterday for a year, since a check reads one against
today's date; the published vectors are used where no date decides the answer.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd import cli
from quackd.cli import app
from quackd.passport import document, strict
from tests.passport_helpers import OWNER, VECTORS, A, doc

runner = CliRunner()
BOARD = {"kind": "sim", "model": "a computer standing in for a robot's own"}


def _flat(output: str) -> str:
    return " ".join(output.split())


def _signed(**members: Any) -> str:
    """Robot A's passport, with its owner's label, signed by its owner."""
    unsigned = doc(A, OWNER, "g1-edu-01", board=BOARD, **members)
    unsigned["owner"]["label"] = "Example lab"
    return document.sign(unsigned, OWNER)


@pytest.fixture
def signed(tmp_path: Path) -> Path:
    path = tmp_path / "robot-passport.jws"
    path.write_text(_signed(), encoding="utf-8")
    return path


# ── check, from files and stdin ─────────────────────────────────────────────────────────


def test_check_shows_who_signed_a_passport_what_it_runs_on_and_until_when(signed: Path) -> None:
    checked = runner.invoke(app, ["passport", "check", str(signed)])
    assert checked.exit_code == 0, checked.output
    flat = _flat(checked.output)
    assert "checked g1-edu-01" in flat
    assert "claim signed by its owner's key" in flat
    # the owner by the key that signed, beside the label anybody could have written
    assert f"owner Example lab, key {OWNER.short_id}" in flat
    assert f"board {BOARD['kind']} {BOARD['model']}" in flat
    dates = document.read_jws(signed.read_text(encoding="utf-8")).document
    assert f"valid {dates['issued']} to {dates['expires']}" in flat


@pytest.mark.parametrize(
    ("vector", "why"),
    [
        ("tampered.jws", "4.4"),
        # the shape every passport had before 1.0: signed by the robot's own key
        ("robot-signed.jws", "a 1.x passport is signed by its owner"),
    ],
)
def test_check_refuses_with_the_rule_and_exits_1(vector: str, why: str) -> None:
    refused = runner.invoke(app, ["passport", "check", str(VECTORS / vector)])
    assert refused.exit_code == 1
    flat = _flat(refused.output)
    assert "refused under 4.4" in flat and why in flat


@pytest.mark.parametrize("when", ["expired", "not yet valid"])
def test_check_refuses_a_passport_outside_the_dates_its_owner_signed_for(
    when: str, tmp_path: Path
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    issued, expires = (
        (now - timedelta(days=730), now - timedelta(days=365))
        if when == "expired"
        else (now + timedelta(days=1), now + timedelta(days=366))
    )
    path = tmp_path / "robot-passport.jws"
    path.write_text(
        _signed(
            issued=issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        encoding="utf-8",
    )
    refused = runner.invoke(app, ["passport", "check", str(path)])
    assert refused.exit_code == 1
    assert "refused under 4.5" in _flat(refused.output)


def test_check_json_is_the_readers_view_and_judges_no_tier(tmp_path: Path) -> None:
    path = tmp_path / "odd.json"
    path.write_bytes(
        strict.dumps(doc(A, OWNER, "g1-edu-01", safety={"stops_on_silence": "yes", "estop": "x"}))
    )
    checked = runner.invoke(app, ["passport", "check", str(path), "--json"])
    assert checked.exit_code == 0, checked.output
    view = json.loads(checked.stdout)
    assert view["safety"] == {"stops_on_silence": False, "estop": "none", "how_to_stop": None}
    assert view["provenance"] == "unsigned: a claim"
    assert view["owner_short_id"] == OWNER.short_id
    assert [n.split(" ")[0] for n in view["notes"]] == ["safety.stops_on_silence", "safety.estop"]
    assert "tier" not in view and "warnings" not in view


def test_check_reads_stdin(signed: Path) -> None:
    piped = runner.invoke(app, ["passport", "check", "-"], input=signed.read_bytes())
    assert piped.exit_code == 0, piped.output
    assert "checked g1-edu-01" in piped.output


@pytest.mark.parametrize(
    ("version", "why"),
    [
        # what a quackd 0.23 to 0.28 laptop served at its well-known URL for several robots
        ("0.1", "refused under rule 4"),
        (document.VERSION, "refused under rule 3"),
    ],
)
def test_check_refuses_an_index_of_passports(version: str, why: str) -> None:
    index = {
        "passport": version,
        "robots": [{"name": "duck-01", "url": "/robots/duck-01/robot-passport.json"}],
    }
    refused = runner.invoke(app, ["passport", "check", "-"], input=json.dumps(index))
    assert refused.exit_code == 1
    assert why in _flat(refused.output)


def test_check_says_when_there_is_no_such_file() -> None:
    missing = runner.invoke(app, ["passport", "check", "no-such-passport.json"])
    assert missing.exit_code == 1 and "no such file" in missing.output


# ── check, over HTTP ────────────────────────────────────────────────────────────────────


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    """A robot serving its passport at its well-known URL, signed beside it, and one redirect;
    its unsigned passport alone under /unsigned/; and under /fake/ the unsigned one served
    where the signed one belongs."""
    token = _signed()
    unsigned = document.read_jws(token).raw
    served = tmp_path / "served"
    for folder in (".well-known", "unsigned", "fake"):
        (served / folder).mkdir(parents=True)
        (served / folder / "robot-passport.json").write_bytes(unsigned)
    (served / ".well-known" / "robot-passport.jws").write_text(token, encoding="utf-8")
    (served / "fake" / "robot-passport.jws").write_bytes(unsigned)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/.well-known/robot-passport.json")
                self.end_headers()
                return
            target = served / self.path.lstrip("/")
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


def test_a_bare_host_means_its_well_known_url_and_the_signed_passport_beside_it(
    server: str,
) -> None:
    checked = runner.invoke(app, ["passport", "check", server])
    assert checked.exit_code == 0, checked.output
    flat = _flat(checked.output)
    assert "checked g1-edu-01" in flat and "claim signed by its owner's key" in flat


def test_a_robots_address_means_its_well_known_url_on_the_port_robots_listen_on(
    server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    address = server.removeprefix("http://")
    by_address = runner.invoke(app, ["passport", "check", address])
    assert by_address.exit_code == 0, by_address.output
    assert "checked g1-edu-01" in by_address.output
    # where a robot listens unless told: this server's port, for the length of the test
    monkeypatch.setattr(cli, "PASSPORT_PORT", int(address.rsplit(":", 1)[1]))
    by_ip = runner.invoke(app, ["passport", "check", "127.0.0.1"])
    assert by_ip.exit_code == 0, by_ip.output
    assert "checked g1-edu-01" in by_ip.output


def test_a_host_with_no_signed_passport_is_read_as_a_claim(server: str) -> None:
    checked = runner.invoke(app, ["passport", "check", f"{server}/unsigned/robot-passport.json"])
    assert checked.exit_code == 0, checked.output
    assert "claim unsigned: a claim" in _flat(checked.output)


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
