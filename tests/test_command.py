"""The line the record keeps: `quackd` in front, the secrets gone, everything else verbatim."""

from __future__ import annotations

import shlex
import sys
from typing import Any
from urllib.parse import urlsplit

import pytest

from quackd.command import (
    HIDDEN,
    SECRET_FLAGS,
    URL_FLAGS,
    command_line,
    command_text,
    redacted_argv,
    redacted_body,
    redacted_url,
)


@pytest.mark.parametrize("flag", SECRET_FLAGS)
def test_both_shell_spellings_of_a_secret_are_hidden(flag: str) -> None:
    """Parametrised over the real tuple, so a third secret flag cannot be added untested."""
    assert redacted_argv([flag, "sk-live-1234"]) == [flag, HIDDEN]
    assert redacted_argv([f"{flag}=sk-live-1234"]) == [f"{flag}={HIDDEN}"]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # A whole line, with the flag still readable next to the hidden value.
        (
            [
                "run",
                "ducks/fetch.duck",
                "--api-key",
                "sk-live-1234",
                "--llm",
                "anthropic:claude-opus-5",
            ],
            ["run", "ducks/fetch.duck", "--api-key", HIDDEN, "--llm", "anthropic:claude-opus-5"],
        ),
        # Both flags on one line, one spelling each.
        (
            ["run", "--api-key", "sk-live-1234", "--token=ghp-5678"],
            ["run", "--api-key", HIDDEN, f"--token={HIDDEN}"],
        ),
        # A value that merely looks like a flag is a value, and is hidden like one.
        (["--api-key", "--llm"], ["--api-key", HIDDEN]),
        # An unrelated argument that carries an `=` is not a secret and is not touched.
        (["--goal=go=home", "--budget=12"], ["--goal=go=home", "--budget=12"]),
        # Nothing to hide leaves the line exactly as it was.
        (["run", "ducks/fetch.duck", "--yes"], ["run", "ducks/fetch.duck", "--yes"]),
        ([], []),
    ],
)
def test_what_the_record_shows_of_each_argument(argv: list[str], expected: list[str]) -> None:
    assert redacted_argv(argv) == expected


def test_a_secret_flag_with_nothing_after_it_stays_as_it_was_typed() -> None:
    """There is no secret to hide, and the record should show the mistake as it was made."""
    argv = ["run", "ducks/fetch.duck", "--api-key"]
    assert redacted_argv(argv) == argv
    assert HIDDEN not in redacted_argv(argv)
    assert argv[-1] == "--api-key"  # and the caller's own list is not rewritten under it


def test_a_secret_is_hidden_by_the_flag_it_followed_and_not_by_how_it_looks() -> None:
    """A task can legitimately mention something key-shaped, and a guess at value shapes
    would either eat it or miss the next key format a vendor invents."""
    argv = ["run", "--goal", "post sk-live-1234 to the board", "--task-name", "ghp-5678"]
    assert redacted_argv(argv) == argv


@pytest.mark.parametrize(
    "argv0",
    [
        "/home/rok/.local/bin/quackd",
        r"C:\Users\rok\AppData\Local\quackd\Scripts\quackd.exe",
        "/home/rok/quackd/.venv/lib/python3.11/site-packages/quackd/__main__.py",
    ],
)
def test_the_line_starts_with_quackd_whatever_argv0_was(
    monkeypatch: pytest.MonkeyPatch, argv0: str
) -> None:
    monkeypatch.setattr(sys, "argv", [argv0, "run", "ducks/fetch.duck", "--api-key", "sk-live"])
    assert command_line() == ["quackd", "run", "ducks/fetch.duck", "--api-key", HIDDEN]


def test_an_explicit_argv_is_never_the_process_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flock members and the tests hand their own list in, and an empty one is a list
    and not a request to go and read `sys.argv`."""
    monkeypatch.setattr(sys, "argv", ["quackd", "run", "--api-key", "sk-live-1234"])
    assert command_line(["record", "--robot", "microduck"]) == [
        "quackd",
        "record",
        "--robot",
        "microduck",
    ]
    assert command_line([]) == ["quackd"]


def test_the_text_quotes_an_argument_that_would_otherwise_come_apart() -> None:
    assert command_text(["run", "--goal", "pick up the ball"]) == (
        "quackd run --goal 'pick up the ball'"
    )
    assert command_text(["--api-key", "sk-live-1234"]) == "quackd --api-key '***'"


def test_the_quoting_is_posix_on_every_platform() -> None:
    """`shlex.join` and not a Windows spelling, so the same run reads the same whether the
    directory is copied off the bench laptop or off a Linux box."""
    argv = ["run", "ducks/fetch.duck", "--goal", "pick up the ball"]
    assert command_text(argv) == shlex.join(["quackd", *argv])
    assert "'pick up the ball'" in command_text(argv)


def test_a_secret_flag_standing_where_a_value_belongs_is_read_as_a_flag() -> None:
    """`--api-key --token sk-live-...` is somebody who lost their place while typing.

    The shell's own reading of it hands `--api-key` the literal text `--token` and leaves the
    real key as a stray argument, and that reading writes a live key into the record in the
    clear. A flag name is never worth hiding and a key always is, so the tie goes to hiding.
    """
    assert redacted_argv(["--api-key", "--token", "sk-live-1234"]) == [
        "--api-key",
        "--token",
        HIDDEN,
    ]
    assert "sk-live-1234" not in redacted_argv(["--token", "--api-key", "sk-live-1234"])


def test_a_secret_flag_given_nothing_after_the_sign_is_left_as_it_was() -> None:
    """The same rule the trailing bare `--api-key` follows, and for the same reason: writing
    `***` where nothing was typed puts a secret in the record that never existed."""
    assert redacted_argv(["--api-key="]) == ["--api-key="]
    assert redacted_argv(["run", "--token=", "--goal", "wave"]) == [
        "run",
        "--token=",
        "--goal",
        "wave",
    ]


def test_the_policy_servers_url_and_token_are_redacted_like_the_rest() -> None:
    """The policy server's two flags, which reach a server whose answers move the arm: its
    token is hidden whole, and a password typed into its URL is taken out."""
    assert "--policy-token" in SECRET_FLAGS and "--policy-url" in URL_FLAGS
    line = redacted_argv(
        [
            "policy",
            "check",
            "--policy-url",
            "https://rok:hunter2@gpu.example.com:9875",
            "--policy-token=0123abcd",
        ]
    )
    assert line == [
        "policy",
        "check",
        "--policy-url",
        f"https://rok:{HIDDEN}@gpu.example.com:9875",
        f"--policy-token={HIDDEN}",
    ]


@pytest.mark.parametrize("flag", URL_FLAGS)
def test_both_shell_spellings_of_a_url_flag_lose_the_password(flag: str) -> None:
    """Parametrised over the real tuple, so a fourth URL flag cannot be added untested."""
    typed = "https://rok:hunter2@gateway.example.com:8443/v1/chat"
    clean = f"https://rok:{HIDDEN}@gateway.example.com:8443/v1/chat"
    assert redacted_argv([flag, typed]) == [flag, clean]
    assert redacted_argv([f"{flag}={typed}"]) == [f"{flag}={clean}"]


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        # An LLM proxy, which is what `--base-url` is pointed at.
        (
            "https://rok:hunter2@gateway.example.com:8443/v1",
            f"https://rok:{HIDDEN}@gateway.example.com:8443/v1",
        ),
        # A rosbridge, which is what `--address` is pointed at.
        ("ws://rok:hunter2@rosbridge.local:9090", f"ws://rok:{HIDDEN}@rosbridge.local:9090"),
        # An IP camera's snapshot, which is what `--camera-url` is pointed at.
        (
            "http://admin:letmein@camera.local/snapshot.jpg",
            f"http://admin:{HIDDEN}@camera.local/snapshot.jpg",
        ),
    ],
)
def test_a_url_keeps_the_half_a_reader_needs_and_loses_the_half_that_rotates(
    typed: str, expected: str
) -> None:
    """Somebody reading a failed run has to see which gateway it was pointed at, and the
    username is how they recognise which account was used."""
    cleaned = redacted_url(typed)
    assert cleaned == expected
    assert "hunter2" not in cleaned
    assert "letmein" not in cleaned
    assert urlsplit(cleaned).scheme == urlsplit(typed).scheme
    assert urlsplit(cleaned).port == urlsplit(typed).port
    assert urlsplit(cleaned).path == urlsplit(typed).path
    assert urlsplit(cleaned).hostname == urlsplit(typed).hostname
    assert urlsplit(cleaned).username == urlsplit(typed).username


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        (
            "https://gw.example.com/v1?api_key=sk-live-1234&model=qwen3",
            f"https://gw.example.com/v1?api_key={HIDDEN}&model=qwen3",
        ),
        # The same name as a vendor spells it, because a query string is nobody's to style.
        (
            "https://gw.example.com/v1?API_KEY=sk-live-1234&model=qwen3",
            f"https://gw.example.com/v1?API_KEY={HIDDEN}&model=qwen3",
        ),
        (
            "https://gw.example.com/v1?Api-Key=sk-live-1234&model=qwen3",
            f"https://gw.example.com/v1?Api-Key={HIDDEN}&model=qwen3",
        ),
        # The secret in the middle, so the parameters on both sides of it are watched.
        (
            "https://gw.example.com/v1?model=qwen3&Token=sk-live-1234&stream=true",
            f"https://gw.example.com/v1?model=qwen3&Token={HIDDEN}&stream=true",
        ),
    ],
)
def test_only_the_credential_named_query_parameter_is_replaced(typed: str, expected: str) -> None:
    """`?api_key=` is the other place vendors put a key, and they disagree about its spelling,
    so the name is matched without its case and the parameter next to it is left alone."""
    cleaned = redacted_url(typed)
    assert cleaned == expected
    assert "sk-live-1234" not in cleaned
    assert "model=qwen3" in cleaned


def test_a_url_with_no_credential_in_it_comes_back_byte_identical() -> None:
    """The record is read to find out which gateway a run failed against, and a URL that
    comes back re-spelled is one a reader cannot paste back into curl."""
    typed = "https://gateway.example.com:8443/v1/chat/completions?model=qwen3&stream=true"
    assert redacted_url(typed) == typed
    assert (
        redacted_url("https://rok@gateway.example.com/v1") == "https://rok@gateway.example.com/v1"
    )


@pytest.mark.parametrize(
    "typed",
    [
        "not a url at all",
        "localhost:8000",
        "/home/rok/ducks/fetch.duck",
        "gateway.example.com/v1",
        "",
    ],
)
def test_a_value_that_is_not_a_url_is_returned_as_it_was_typed(typed: str) -> None:
    """A mangled value in the record sends a reader after the wrong bug, so a value that
    does not look like a URL is written down rather than guessed at."""
    assert redacted_url(typed) == typed


def test_a_url_that_does_not_parse_is_returned_as_it_was_typed() -> None:
    """An unclosed IPv6 bracket is the parser's own error and not quackd's to raise. The
    run is already over by the time the record is written and a typo in a flag is not worth
    a traceback on top of it."""
    typed = "http://[::1"
    with pytest.raises(ValueError, match="IPv6"):
        urlsplit(typed)
    assert redacted_url(typed) == typed


@pytest.mark.parametrize("flag", ["--goal", "--task-name", "--llm"])
def test_a_url_on_a_flag_that_is_not_a_url_flag_is_not_touched(flag: str) -> None:
    """Redaction goes by the flag a value followed and never by how the value looks. A task
    can legitimately name a URL, and a guess at value shapes would eat it."""
    typed = "https://rok:hunter2@gateway.example.com:8443/v1"
    assert redacted_argv([flag, typed]) == [flag, typed]
    assert redacted_argv([f"{flag}={typed}"]) == [f"{flag}={typed}"]


def test_a_password_in_a_url_never_reaches_the_line_the_record_keeps() -> None:
    """The whole point of the module, through the function the record actually calls."""
    line = command_text(["run", "--base-url", "https://rok:hunter2@gw.example.com/v1", "--yes"])
    assert "hunter2" not in line
    assert f"https://rok:{HIDDEN}@gw.example.com/v1" in line
    assert command_line(["--camera-url=http://admin:letmein@camera.local/snapshot.jpg"]) == [
        "quackd",
        f"--camera-url=http://admin:{HIDDEN}@camera.local/snapshot.jpg",
    ]


def test_a_credential_named_key_is_replaced_wherever_it_sits_in_the_body() -> None:
    """A vendor's nesting is nobody's to predict, so the body is walked to the bottom. The
    `authorization` header is the canonical field somebody puts in `--extra-body`, and no
    argv redaction reaches it when it arrives through `QUACKD_EXTRA_BODY`."""
    body = {
        "authorization": "Bearer sk-live-1234",
        "headers": {"Authorization": "Bearer sk-live-5678", "x-run": "fetch"},
        "messages": [{"api_key": "sk-live-9012", "role": "system"}, {"role": "user"}],
        "temperature": 0.2,
    }
    assert redacted_body(body) == {
        "authorization": HIDDEN,
        "headers": {"Authorization": HIDDEN, "x-run": "fetch"},
        "messages": [{"api_key": HIDDEN, "role": "system"}, {"role": "user"}],
        "temperature": 0.2,
    }


def test_the_extra_body_the_published_qwen3_runs_use_survives_untouched() -> None:
    """The documented reason the flag exists. A redaction that ate it would make the flag
    useless and the record a lie about what was sent."""
    body: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}
    cleaned = redacted_body(body)
    assert cleaned == {"chat_template_kwargs": {"enable_thinking": False}}
    assert HIDDEN not in str(cleaned)
    assert body == {"chat_template_kwargs": {"enable_thinking": False}}  # and the caller's
    assert cleaned is not body  # own object is not rewritten under it


@pytest.mark.parametrize("body", [None, "a string", ["one", "two"], [1, 2, 3], 12, True])
def test_a_body_that_is_not_an_object_comes_back_as_it_was(body: Any) -> None:
    """`--extra-body` is whatever JSON a vendor asked for, and the record is written after a
    run has already failed. Nothing here is worth raising over."""
    assert redacted_body(body) == body


def test_no_flag_that_could_carry_a_credential_is_missing_from_the_two_lists() -> None:
    """The lists are by hand, and a flag added to the CLI without being added to one of them
    puts a password in the record with nothing going red.

    Matched on the flag's own name, which is the same judgement somebody adding one would
    make: anything spelled like a key or a token is a secret, and anything spelled like a URL
    or an address can carry one in its userinfo. A new flag that trips this and genuinely
    holds no credential goes in the exemption below with a reason, which is the point:
    somebody has to look at it once.

    Through `typer.main.get_command` rather than `app.registered_commands`, because Typer
    builds the Click parameters when it builds the command and the registered list has none:
    a guard written against it passes because it inspects nothing.
    """
    import typer.main

    from quackd.cli import app

    exempt = {
        "--registry-dir",  # a directory, and any secret in it is a secret on disk already
        "--memory-dir",  # the same
        # a machine, never a URL: `parse_host` refuses a value with an `@` in it, without
        # quoting it, before any record exists, so no credential it could carry gets that far
        "--host",
        # `quackd policy serve`'s: it names the file the token is in and never holds the value,
        # which is the reason the flag exists rather than a `--token` on that command
        "--token-file",
        # `quackd policy serve`'s too: it names the variable the token is in and never holds the
        # value, and a value that is not a variable's name is refused without being quoted
        "--token-env",
    }
    secret_words = ("key", "token", "secret", "password", "passwd", "credential")
    url_words = ("url", "address", "endpoint", "host", "broker")

    seen: set[str] = set()

    def walk(command: Any) -> None:
        for param in getattr(command, "params", None) or []:
            for name in getattr(param, "opts", None) or []:
                if name.startswith("--"):
                    seen.add(name)
        for sub in (getattr(command, "commands", None) or {}).values():
            walk(sub)

    walk(typer.main.get_command(app))
    assert len(seen) > 40, f"the walk found only {len(seen)} flags, so it is not walking the CLI"

    missed: list[tuple[str, str]] = []
    for name in sorted(seen - exempt):
        bare = name.lstrip("-").replace("-", "")
        if name in SECRET_FLAGS:
            # hidden whole, which covers anything a URL shape could carry too: `--host-token`
            # is spelled like both and needs only the stronger of the two
            continue
        if any(w in bare for w in secret_words):
            missed.append((name, "SECRET_FLAGS"))
        elif any(w in bare for w in url_words) and name not in URL_FLAGS:
            missed.append((name, "URL_FLAGS"))
    assert not missed, f"flags shaped like a credential and redacted nowhere: {missed}"
