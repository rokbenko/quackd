"""Signing a robot's passport, deciding when to sign again, the service's own check of what it
serves, and the systemd unit that keeps it running.

The owner key is sealed with a passphrase, so every signing here reads one from a file, as a
script would; a terminal's prompt is the wizard's business and is left out.
"""

from __future__ import annotations

import itertools
import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest

from roboglot.onboard import config, systemd, wizard
from roboglot.onboard import preflight as preflight_module
from roboglot.onboard.keys import WrongPassphrase
from roboglot.onboard.service import check_own_passport
from roboglot.onboard.store import OnboardDir
from roboglot.passport import document
from tests import systemd_reading

SIM = {"kind": "sim", "model": "a computer standing in for a robot's own"}
PASSPHRASE = "correct horse battery staple"


class NoQuestions:
    def text(self, question: str, default: str | None = None) -> str:
        return default or ""

    def secret(self, question: str, *, twice: bool = False) -> str:
        raise AssertionError("a test reads its passphrase from a file")

    def yes(self, question: str, default: bool = False) -> bool:
        return default


def robot_text(**overrides: object) -> str:
    fields: dict[str, object] = {
        "name": "g1-edu-01",
        "vendor": "unitree",
        "model": "g1-edu",
        "owner_label": "Example lab",
        "owner_contact": None,
        "embodiment": "humanoid",
        "mobility": "legged",
        "how_to_stop": "the red button on its remote",
        "estop": "remote",
    }
    fields.update(overrides)
    return config.template(**fields)  # type: ignore[arg-type]


@pytest.fixture
def home(tmp_path: Path) -> OnboardDir:
    made = OnboardDir(tmp_path / "robot")
    made.ensure()
    made.robot_yaml.write_text(robot_text(), encoding="utf-8")
    return made


@pytest.fixture
def passphrase_file(tmp_path: Path) -> Path:
    path = tmp_path / "secrets" / "passphrase.txt"
    path.parent.mkdir()
    path.write_text(PASSPHRASE + "\n", encoding="utf-8")
    with __import__("contextlib").suppress(OSError):
        os.chmod(path, 0o600)
    return path


def sign(home: OnboardDir, passphrase_file: Path, days: int = 365) -> wizard.Signed:
    return wizard.sign(
        home,
        config.load(home.robot_yaml),
        SIM,
        ask=NoQuestions(),
        passphrase_file=passphrase_file,
        days=days,
    )


def test_the_first_signing_makes_both_keys_and_a_passport_its_owner_signed(
    home: OnboardDir, passphrase_file: Path
) -> None:
    signed = sign(home, passphrase_file)
    assert signed.robot_key_made and signed.owner_key_made
    reading = document.read_jws(signed.token)
    assert reading.signed and reading.board() == SIM
    assert reading.owner_id != reading.id
    assert home.passport_jws.read_text(encoding="utf-8").strip() == signed.token
    again = sign(home, passphrase_file)
    assert not again.robot_key_made and not again.owner_key_made
    assert document.read_jws(again.token).id == reading.id


@pytest.mark.parametrize("left", ["a file", "a link"])
def test_the_passport_goes_through_a_file_of_its_own_whatever_is_left_at_its_old_temporary_name(
    home: OnboardDir, passphrase_file: Path, tmp_path: Path, left: str
) -> None:
    """Signing wrote passport.jws through the one fixed name passport.jws.tmp, and followed it
    when it was a link: a file someone left there was taken over and renamed into place, and a
    link there had the robot's own user write the passport over whatever it pointed at.
    Whatever is at that name now stays as it was, no temporary file is left, and the passport
    takes the mode any new file takes in its folder, as before, since others are meant to read
    it."""
    old = home.root / "passport.jws.tmp"
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("not the passport\n", encoding="utf-8")
    if left == "a file":
        old.write_text("not the passport\n", encoding="utf-8")
    elif os.name == "nt":
        pytest.skip("Windows makes links only with a privilege")
    else:
        old.symlink_to(elsewhere)
    signed = sign(home, passphrase_file)
    assert home.passport_jws.read_text(encoding="utf-8") == signed.token + "\n"
    assert (old.is_symlink(), old.exists()) == (left == "a link", True)
    assert old.read_text(encoding="utf-8") == elsewhere.read_text(encoding="utf-8")
    assert elsewhere.read_text(encoding="utf-8") == "not the passport\n"
    assert [p.name for p in home.root.iterdir() if p.name.endswith(".tmp")] == [old.name]
    fresh = tmp_path / "fresh.txt"
    fresh.write_text("", encoding="utf-8")
    mode = stat.S_IMODE(home.passport_jws.stat().st_mode)
    assert mode == stat.S_IMODE(fresh.stat().st_mode), oct(mode)


def test_signing_again_needs_the_passphrase_the_owner_key_was_sealed_with(
    home: OnboardDir, passphrase_file: Path, tmp_path: Path
) -> None:
    sign(home, passphrase_file)
    wrong = tmp_path / "secrets" / "wrong.txt"
    wrong.write_text("not the passphrase at all\n", encoding="utf-8")
    with __import__("contextlib").suppress(OSError):
        os.chmod(wrong, 0o600)
    with pytest.raises(WrongPassphrase):
        sign(home, wrong)


def test_a_signed_passport_is_signed_again_only_when_its_file_changed_or_its_days_run_out(
    home: OnboardDir, passphrase_file: Path
) -> None:
    never = wizard.needs_signing(home, config.load(home.robot_yaml), SIM)
    assert never == "it has never been signed"
    sign(home, passphrase_file)
    assert wizard.needs_signing(home, config.load(home.robot_yaml), SIM) is None
    home.robot_yaml.write_text(robot_text(how_to_stop="pull the cord"), encoding="utf-8")
    why = wizard.needs_signing(home, config.load(home.robot_yaml), SIM)
    assert why is not None and "safety" in why
    sign(home, passphrase_file, days=wizard.RENEW_WITHIN_DAYS - 1)
    why = wizard.needs_signing(home, config.load(home.robot_yaml), SIM)
    assert why is not None and "runs out" in why


def test_what_a_computer_calls_itself_changing_is_not_robot_yaml_changing(
    home: OnboardDir, passphrase_file: Path
) -> None:
    """A firmware update can rename a computer, and nobody edited anything."""
    sign(home, passphrase_file)
    renamed = {**SIM, "model": "another name"}
    file = config.load(home.robot_yaml)
    assert wizard.needs_signing(home, file, renamed) == (
        "this computer calls itself something else since it was signed"
    )
    assert wizard.needs_signing(home, file, renamed, file_alone=True) == (
        "this computer calls itself something else since it was signed"
    ), "with no passport to build, the board is still the board"
    assert wizard.needs_signing(home, file, SIM, file_alone=True) is None
    home.robot_yaml.write_text(robot_text(how_to_stop="pull the cord"), encoding="utf-8")
    why = wizard.needs_signing(home, config.load(home.robot_yaml), renamed)
    assert why == "robot.yaml changed since it was signed: safety", "board is no edit of it"


def test_a_clock_before_any_passport_cannot_sign_and_makes_no_key_trying(
    home: OnboardDir, passphrase_file: Path
) -> None:
    """A board with no clock battery and no network wakes up long ago, and a signature from then
    would have run out before anybody read it."""
    long_ago = datetime(preflight_module.EARLIEST_YEAR - 1, 12, 31, tzinfo=UTC)
    with pytest.raises(wizard.WizardError, match="timedatectl"):
        wizard.sign(
            home,
            config.load(home.robot_yaml),
            SIM,
            ask=NoQuestions(),
            passphrase_file=passphrase_file,
            days=30,
            now=long_ago,
        )
    assert not home.robot_key.exists() and not home.owner_key.exists()
    assert not home.passport_jws.exists()


def test_a_clock_behind_the_passport_is_told_to_set_the_time_and_not_to_sign_again(
    home: OnboardDir, passphrase_file: Path
) -> None:
    """Signing there would write the wrong date down. Any other failure still says to sign."""

    def passport_row() -> preflight_module.Check:
        checks = preflight_module.preflight(
            home,
            "sim",
            service=lambda: None,
            radio_rows=lambda *_: [],
            clock_state=lambda: (None, None),
        )
        return next(check for check in checks if check.name == "passport")

    def signed_at(moment: datetime, days: int) -> None:
        wizard.sign(
            home,
            config.load(home.robot_yaml),
            SIM,
            ask=NoQuestions(),
            passphrase_file=passphrase_file,
            days=days,
            now=moment,
        )

    signed_at(datetime.now(UTC) + timedelta(days=30), 30)
    row = passport_row()
    assert row.state == "fail" and "clock is behind" in row.detail
    assert row.hint == preflight_module.SET_THE_TIME
    signed_at(datetime.now(UTC) - timedelta(days=30), 1)
    row = passport_row()
    assert row.state == "fail" and "expired" in row.detail
    assert row.hint == "roboglot onboard sign"


def test_the_service_serves_only_a_passport_that_checks_out_for_its_own_key(
    home: OnboardDir, passphrase_file: Path, tmp_path: Path
) -> None:
    from roboglot.onboard.keys import read_robot_key

    assert not check_own_passport(None, "did:key:x").ok
    signed = sign(home, passphrase_file)
    robot = read_robot_key(home.robot_key)
    assert check_own_passport(signed.token, robot.did).ok
    other = OnboardDir(tmp_path / "another")
    other.ensure()
    other.robot_yaml.write_text(robot_text(name="g1-edu-02"), encoding="utf-8")
    elsewhere = sign(other, passphrase_file)
    check = check_own_passport(elsewhere.token, robot.did)
    assert not check.ok and "another robot key" in check.why
    late = int((datetime.now(UTC) + timedelta(days=400)).timestamp())
    expired = check_own_passport(signed.token, robot.did, now=late)
    assert not expired.ok and "expired" in expired.why


def test_the_unit_runs_roboglot_as_the_robots_user_and_writes_only_its_directory(
    tmp_path: Path,
) -> None:
    roboglot = "/home/robot/.local/bin/roboglot"
    text = systemd.unit_text(user="robot", directory=tmp_path, roboglot=roboglot)
    assert "User=robot" in text and "SupplementaryGroups=bluetooth" in text
    assert f"ReadWritePaths={tmp_path}" in text and "ProtectSystem=strict" in text
    assert "ExecStart=/home/robot/.local/bin/roboglot onboard serve" in text
    assert f"EnvironmentFile=-{tmp_path}/robot.env" in text
    bare = systemd.unit_text(user="robot", directory=tmp_path, roboglot="q", bluetooth_group=False)
    assert "SupplementaryGroups" not in bare
    commands = systemd.install_commands(tmp_path / "unit")
    assert all(command[0] == "sudo" for command in commands)
    assert commands[-1][-1] == systemd.UNIT_NAME


def exec_line(value: str) -> systemd_reading.Service:
    return systemd_reading.read_unit(f"[Service]\nExecStart={value}\n")


@pytest.mark.parametrize(
    ("value", "argv"),
    [
        ("/RValue r1", ["/RValue", "r1"]),
        ('"/PATH WITH SPACES/daemon" -1 -2', ["/PATH WITH SPACES/daemon", "-1", "-2"]),
        ('"/PATH\\sWITH\\sSPACES/daemon" -1 -2', ["/PATH WITH SPACES/daemon", "-1", "-2"]),
        ("/path\\s", ["/path "]),
        ("/bin/grep '\\w+\\K'", ["/bin/grep", "\\w+\\K"]),
    ],
)
def test_the_suites_reading_of_a_command_line_is_the_one_systemds_own_tests_assert(
    value: str, argv: list[str]
) -> None:
    """`tests/systemd_reading` stands in for systemd, which no machine of the suite runs, so it
    is held to what systemd's own test-load-fragment.c asserts: spaces in a quoted path, `\\s`,
    and an escape systemd does not know, kept as it is on its second, relaxed try. A stand-in
    that read these otherwise could pass a unit systemd refuses."""
    assert exec_line(value).exec_argv == argv


@pytest.mark.parametrize("escape", list("abfnrtv\\'\"x"))
def test_the_suites_reading_refuses_the_program_paths_systemds_own_tests_refuse(
    escape: str,
) -> None:
    """test-load-fragment.c's "invalid character" cases: `/path\\X` for each of these is refused,
    by `string_is_safe` (a control character, a quote, a backslash) or by the escape itself."""
    with pytest.raises(systemd_reading.Refused):
        exec_line("/path\\" + escape)


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("", False),
        ("-1", False),
        ("foo\nbar", False),
        ("0123456789012345678901234567890123456789", False),
        ("aaa:bbb", False),
        (".", False),
        ("..", False),
        ("65535", False),
        ("root", True),
        ("kkk-", True),
        ("eff.", True),
        ("-kkk", True),
        ("r\xf6\xf6t", True),
        ("5some", True),
        ("piff.paff@ad.domain.example", True),
        ("0", True),
        ("1000", True),
    ],
)
def test_the_suites_reading_judges_user_names_as_systemds_own_tests_do(
    name: str, valid: bool
) -> None:
    """test-user-util.c, the relaxed rule `User=` is read by and its numeric cases."""
    assert systemd_reading.valid_user_name_relaxed(name) is valid


#: Robot directories as a Linux robot may name them, each holding what systemd's reading of a
#: unit changes: a space, which splits a list or ends a line; a %, which begins a specifier; the
#: quotes and the backslashes its quoting takes away, at the end of a line too; glob's pattern
#: characters, which EnvironmentFile= reads; what a command line or a shell would read
#: otherwise; and characters past ASCII, a line separator among them.
ODD_DIRECTORIES = (
    "/home/rok/robot one",
    "/srv/robot%h",
    "/srv/robot%",
    "/srv/%%",
    '/srv/it\'s "the" robot',
    "/srv/back\\new robot",
    "/srv/a\\",
    "/srv/a\\\\",
    "/srv/a\\\\\\",
    "/srv/a\\b\\",
    "/srv/robot[1]*?",
    "/srv/robot ",
    "/srv/ robot",
    "/srv/$HOME/${HOME}/$$",
    "/srv/a;b/ ; /;",
    "/srv/#x/;y",
    "/srv/a=b",
    "/srv/-robot",
    "/srv/+robot",
    "/srv/~",
    "/srv/@!:robot",
    "/srv/r\xf6bot\xa0x",
    "/srv/line\U00002028sep\U00002029par",
    "/srv/bom\U0000feffx",
    "/srv/\U0001f916 robot",
    "/srv/e\U00000301",
    "//srv//robot",
)
#: User names systemd's `User=` takes as they are: specifiers, a backslash inside, spaces
#: inside, letters past ASCII, a dollar, and a dash or a dot at either end.
ODD_USERS = ("rok%h", "DOMAIN\\rok", "r o k", "r\xf6k", "rok$", "-rok", ".rok", "rok.")
#: Program paths systemd runs a program from: a space and a specifier, dollars, a command line's
#: separator and comment sign, and characters past ASCII.
ODD_PROGRAMS = (
    "/opt/ro boglot %i/bin/roboglot",
    "/opt/$HOME/${HOME}/bin/roboglot",
    "/opt/a;b/#c/bin/roboglot",
    "/opt/\U0001f916/line\U00002028sep/bin/roboglot",
)


@pytest.mark.parametrize("where", ODD_DIRECTORIES)
def test_systemd_reads_back_every_odd_directory_user_and_program_it_can_run(where: str) -> None:
    """The robot directory, roboglot's own path and the user went into the unit as they were,
    and systemd splits `Environment=`, `ReadWritePaths=` and `ExecStart=` at a space, takes
    quotes and backslashes away, expands a `%` everywhere, reads `EnvironmentFile=` as a pattern
    and drops a space that ends a line: a directory with a space had the service run on another
    directory while the wizard said it was installed. For each odd directory, with each odd user
    and each program systemd can run, the unit loads by systemd's own reading
    (`tests/systemd_reading`), nothing in it is ignored, and each directive is what the wizard
    meant: the user, the working directory as systemd tidies a path, both variables, the key's
    file as a name and not a pattern, the program with no prefix and its three words, and the
    one writable path. A `$` in the program reaches its argv[0] only, which this leaves out."""
    simple = systemd_reading.path_simplify(where)
    for user, program in itertools.product(ODD_USERS, ODD_PROGRAMS):
        text = systemd.unit_text(user=user, directory=PurePosixPath(where), roboglot=program)
        service = systemd_reading.read_unit(text)
        assert service.user == user
        assert service.working_directory == simple
        assert service.environment == {
            "PYTHONUNBUFFERED": "1",
            "ROBOGLOT_ONBOARD_DIR": str(PurePosixPath(where)),
            "ROBOGLOT_MEMORY_DIR": f"{PurePosixPath(where)}/memory",
        }
        robot_env = systemd_reading.path_simplify(f"{where}/robot.env")
        assert service.environment_files == [(True, robot_env)]
        assert (service.exec_path, service.exec_argv) == (
            systemd_reading.path_simplify(program),
            [program, "onboard", "serve"],
        )
        assert (service.exec_flags, service.exec_more) == ("", False)
        assert service.read_write_paths == [simple]
        assert service.ignored == []


#: roboglot's own paths systemd runs no program from, however the unit quotes them: its
#: `string_is_safe` refuses a quote or a backslash in the path it reads off `ExecStart=`. The
#: last is the one an earlier test here said systemd read back.
PROGRAMS_SYSTEMD_WILL_NOT_RUN = (
    "/home/robot/Rok's robot/.venv/bin/roboglot",
    '/opt/the "robot"/bin/roboglot',
    "/opt/back\\slash/bin/roboglot",
    '/opt/ro boglot %i/"it\'s"\\bin/roboglot',
)


@pytest.mark.parametrize("program", PROGRAMS_SYSTEMD_WILL_NOT_RUN)
def test_a_roboglot_path_systemd_runs_no_program_from_is_refused_naming_it(program: str) -> None:
    """The unit quoted a path to roboglot holding a quote or a backslash, and systemd refuses
    such a program however it is quoted: the unit was installed and enabled with every sudo line
    and never started. It is refused now, naming roboglot's own path; systemd's reading of the
    path as it used to be written shows why."""
    with pytest.raises(ValueError, match=r"^roboglot's own path "):
        systemd.unit_text(user="robot", directory=PurePosixPath("/srv/robot"), roboglot=program)
    quoted = '"' + program.replace("\\", "\\\\").replace('"', '\\"') + '"'
    with pytest.raises(systemd_reading.Refused, match="special characters"):
        exec_line(f"{quoted.replace('%', '%%')} onboard serve")


@pytest.mark.parametrize(
    ("held", "why"),
    [
        ("\n", "a control character"),
        ("\r", "a control character"),
        ("\t", "a control character"),
        ("\x00", "a control character"),
        ("\x1b", "a control character"),
        ("\x7f", "a control character"),
        ("\x85", "a control character"),
        ("\udcff", "is not UTF-8"),
        (chr(0xFFFE), "a noncharacter"),
        (chr(0xFDD0), "a noncharacter"),
        (chr(0x1FFFF), "a noncharacter"),
    ],
)
@pytest.mark.parametrize("what", ["the robot directory", "the user", "roboglot's own path"])
def test_a_directory_user_or_program_no_unit_can_hold_is_refused_naming_which(
    what: str, held: str, why: str
) -> None:
    """A newline in any of the three began a directive of its own, `ExecStartPre=+` run as root
    among them, in a unit installed with sudo whose text the wizard never shows. No quoting
    carries a control character through systemd's reading, nor a name's bytes that are not
    UTF-8, nor a noncharacter, for which systemd refuses the whole unit as not UTF-8 clean, so
    the unit is refused, naming which value holds it and why."""
    given = {
        "the robot directory": "/srv/robot",
        "the user": "rok",
        "roboglot's own path": "/usr/bin/roboglot",
    }
    given[what] += held + "ExecStartPre=+/bin/true"
    with pytest.raises(ValueError, match=f"^{re.escape(what)} .*{re.escape(why)}"):
        systemd.unit_text(
            user=given["the user"],
            directory=PurePosixPath(given["the robot directory"]),
            roboglot=given["roboglot's own path"],
        )


@pytest.mark.parametrize(
    "user",
    ["", " rok", "rok ", "rok\\", "a:b", "a/b", ".", "..", "-1", "-", "007", "65535", "0", "1001"],
)
def test_a_user_name_that_user_would_read_as_another_or_as_root_is_refused(user: str) -> None:
    """`User=` takes one name and no quotes: systemd drops a space at either end, which makes it
    another account, reads a backslash at the end of the line as the line going on, and an
    empty `User=` runs the service as root. It reads a name of digits as a uid, and refuses the
    unit for a name its own rule refuses (a ':' or a '/', '.', '..', a minus and digits), which a
    systemd older than 234 ran as root instead. Each is refused rather than written."""
    with pytest.raises(ValueError, match=r"^the user"):
        systemd.unit_text(user=user, directory=PurePosixPath("/srv/robot"), roboglot="/usr/bin/x")


@pytest.mark.parametrize("where", ["/home/rok/x/../robot", "/srv/robot/..", "/../srv/robot"])
def test_a_robot_directory_with_parent_parts_is_refused_and_told_to_give_it_without(
    where: str,
) -> None:
    """systemd refuses a `WorkingDirectory=` that is not a normal path, a `..` part above all, so
    a `--dir` of ../robot made a unit systemd would not load, once its sudo lines had run. It is
    refused by name, saying to give the folder without them, and not resolved: a link on the way
    could make the folder resolved another than the one named. Two dots inside a name are no
    such part."""
    with pytest.raises(ValueError, match=r"^the robot directory .* without '\.\.' parts"):
        systemd.unit_text(user="rok", directory=PurePosixPath(where), roboglot="/usr/bin/x")
    kept = systemd.unit_text(
        user="rok", directory=PurePosixPath("/srv/r..2"), roboglot="/usr/bin/x"
    )
    assert "\nWorkingDirectory=/srv/r..2\n" in kept


def runs_as(
    monkeypatch: pytest.MonkeyPatch,
    name: str | None,
    *,
    uid: int = 1000,
    named: dict[str, int] | None = None,
) -> None:
    """The wizard runs with effective uid `uid`, which a stand-in for the system's account
    database names `name`, or knows no account for when `name` is None; by name, the database
    finds the uid `named` gives each name, by default `name`'s own. Windows has no `pwd` and no
    `os.geteuid`, and on Linux the suite never asks the real database who runs it."""
    by_name = named if named is not None else ({} if name is None else {name: uid})

    def getpwuid(asked: int) -> SimpleNamespace:
        if name is None or asked != uid:
            raise KeyError(f"getpwuid(): uid not found: {asked}")
        return SimpleNamespace(pw_name=name, pw_uid=asked)

    def getpwnam(asked: str) -> SimpleNamespace:
        if asked not in by_name:
            raise KeyError(f"getpwnam(): name not found: {asked!r}")
        return SimpleNamespace(pw_name=asked, pw_uid=by_name[asked])

    monkeypatch.setattr(os, "geteuid", lambda: uid, raising=False)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwuid=getpwuid, getpwnam=getpwnam))


@pytest.mark.parametrize(
    ("what", "where", "program"),
    [
        ("the robot directory", "/srv/robot\nExecStartPre=+/bin/true", "/usr/bin/roboglot"),
        ("roboglot's own path", "/srv/robot", "/usr/bin/roboglot\nExecStartPre=+/bin/true"),
    ],
)
def test_a_unit_that_cannot_hold_its_directory_or_program_is_refused_before_anything_is_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, what: str, where: str, program: str
) -> None:
    """The wizard staged the unit and ran its sudo commands with whatever the robot directory or
    roboglot's own path held, so a newline in either installed a directive of its own. It is
    refused now, naming which, before a file is staged or a command runs."""
    runs_as(monkeypatch, "rok")
    monkeypatch.setattr(wizard.shutil, "which", lambda _name: program)
    staged: list[str] = []

    def mkdtemp(*_args: object, **_kwargs: object) -> str:
        staged.append("a folder for the unit")
        return str(tmp_path)

    monkeypatch.setattr(wizard.tempfile, "mkdtemp", mkdtemp)
    ran: list[list[str]] = []
    with pytest.raises(wizard.WizardError, match=f"^{re.escape(what)} "):
        wizard.install_service(
            SimpleNamespace(root=PurePosixPath(where)),
            say=lambda _line: None,
            ask=NoQuestions(),
            run=lambda command: ran.append(command) or 0,
        )
    assert (staged, ran, list(tmp_path.iterdir())) == ([], [], [])


@pytest.mark.parametrize("source", ["a relative entry in PATH", "a relative argv[0]"])
def test_a_roboglot_found_by_a_relative_path_goes_into_the_unit_from_where_the_wizard_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """`shutil.which` gives a relative path for a relative entry in PATH, as `sys.argv[0]` can
    be, and the unit took it as it was, which systemd refuses, or reads from a folder of its
    own. It goes in absolute now, from the folder the wizard runs in, which is what it meant."""
    runs_as(monkeypatch, "robot")
    monkeypatch.chdir(tmp_path)
    program = tmp_path / "bin" / "roboglot"
    program.parent.mkdir()
    program.write_text("", encoding="utf-8")
    relative = os.path.join("bin", "roboglot")
    if source == "a relative entry in PATH":
        monkeypatch.setattr(wizard.shutil, "which", lambda _name: relative)
    else:
        monkeypatch.setattr(wizard.shutil, "which", lambda _name: None)
        monkeypatch.setattr(wizard.sys, "argv", [relative, "onboard"])
    staged = tmp_path / "staged"
    staged.mkdir()
    monkeypatch.setattr(wizard.tempfile, "mkdtemp", lambda *_args, **_kwargs: str(staged))
    given: list[str] = []

    def unit_text(**kwargs: Any) -> str:
        # what the wizard gives the unit, which on Windows is a path of its own kind
        given.append(kwargs["roboglot"])
        return "[Service]\n"

    monkeypatch.setattr(systemd, "unit_text", unit_text)
    assert wizard.install_service(
        SimpleNamespace(root=PurePosixPath("/home/robot/.roboglot/onboard")),
        say=lambda _line: None,
        ask=NoQuestions(),
        run=lambda _command: 0,
    )
    (word,) = given
    assert os.path.isabs(word) and os.path.samefile(word, program), word


@pytest.mark.parametrize(
    ("user", "logname"),
    [(None, None), ("someoneelse", None), (None, "someoneelse"), ("robotuser", None)],
    ids=["no USER or LOGNAME", "a stale USER", "a stale LOGNAME", "USER naming the person"],
)
def test_the_unit_runs_as_the_account_whose_uid_runs_the_wizard_whatever_user_says(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    user: str | None,
    logname: str | None,
) -> None:
    """The unit's `User=` came from `USER` or `LOGNAME` and fell back to root, just after the
    wizard had refused a root uid: with neither set it enabled the networked service as root,
    and a stale `USER` named another account. It is the account the effective uid has in the
    account database now, whatever the variables say; the staged unit is read as systemd reads
    it, and the commands that install it are the ones they always were."""
    runs_as(monkeypatch, "robotuser")
    for variable, value in (("USER", user), ("LOGNAME", logname)):
        if value is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, value)
    monkeypatch.setattr(wizard.shutil, "which", lambda _name: "/usr/bin/roboglot")
    monkeypatch.setattr(wizard.tempfile, "mkdtemp", lambda *_args, **_kwargs: str(tmp_path))
    ran: list[list[str]] = []
    assert wizard.install_service(
        SimpleNamespace(root=PurePosixPath("/home/robotuser/.roboglot/onboard")),
        say=lambda _line: None,
        ask=NoQuestions(),
        run=lambda command: ran.append(command) or 0,
    )
    unit = (tmp_path / systemd.UNIT_NAME).read_text(encoding="utf-8")
    assert systemd_reading.read_unit(unit).user == "robotuser"
    assert ran == systemd.install_commands(tmp_path / systemd.UNIT_NAME)


@pytest.mark.parametrize(
    ("name", "named", "said"),
    [
        ("root", {"root": 0}, "systemd would run the service as uid 0"),
        ("nobody", {"nobody": 65534}, "systemd would run the service as uid 65534"),
        ("bob", {"bob": 1001}, "systemd would run the service as uid 1001"),
        ("bob", {}, "no account named 'bob'"),
        ("0", {"root": 0, "0": 1000}, "digits alone"),
        ("1001", {"1001": 1000, "bob": 1001}, "digits alone"),
    ],
    ids=[
        "root",
        "nobody",
        "a name that is another uid's",
        "a name the database cannot find",
        "uid 1000 named 0",
        "uid 1000 named 1001",
    ],
)
def test_the_unit_never_runs_as_another_uid_than_the_one_that_ran_the_wizard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, named: dict[str, int], said: str
) -> None:
    """`User=` names the account, and systemd finds its uid by that name: `root` and `nobody`
    without asking the account database, a name of digits as the uid it reads, and any other in
    the database. So a database naming uid 1000 `root`, `0` or `1001`, or a name it finds as
    another uid, had the networked service run as root or as another account. Each is refused
    by name now, and nothing is staged."""
    runs_as(monkeypatch, name, named=named)
    monkeypatch.setattr(wizard.shutil, "which", lambda _name: "/usr/bin/roboglot")
    staged: list[str] = []

    def mkdtemp(*_args: object, **_kwargs: object) -> str:
        staged.append("a folder for the unit")
        return str(tmp_path)

    monkeypatch.setattr(wizard.tempfile, "mkdtemp", mkdtemp)
    with pytest.raises(wizard.WizardError, match=re.escape(said)):
        wizard.install_service(
            SimpleNamespace(root=PurePosixPath("/home/robot/.roboglot/onboard")),
            say=lambda _line: None,
            ask=NoQuestions(),
            run=lambda _command: 0,
        )
    assert staged == []


@pytest.mark.parametrize(
    ("account", "said"),
    [
        (None, "no account on this system has uid 1000"),
        ("robot\nExecStartPre=+/bin/true", "the user "),
        (False, "this system has no account database"),
    ],
    ids=["no account for the uid", "a name no unit can hold", "no account database"],
)
def test_a_uid_with_no_account_a_unit_can_name_installs_nothing_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, account: str | bool | None, said: str
) -> None:
    """With no account for its uid the wizard wrote `User=root`, or whatever `USER` said, and
    went on. With none to name, or one whose name no unit can hold, it installs nothing now, not
    even a staged file, and its line says why, whatever `USER` says."""
    if account is False:
        monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
        monkeypatch.setitem(sys.modules, "pwd", None)  # importing it fails, as on Windows
    else:
        runs_as(monkeypatch, None if account is None else str(account))
    monkeypatch.setenv("USER", "robotuser")
    monkeypatch.setattr(wizard.shutil, "which", lambda _name: "/usr/bin/roboglot")
    staged: list[str] = []

    def mkdtemp(*_args: object, **_kwargs: object) -> str:
        staged.append("a folder for the unit")
        return str(tmp_path)

    monkeypatch.setattr(wizard.tempfile, "mkdtemp", mkdtemp)
    ran: list[list[str]] = []
    with pytest.raises(wizard.WizardError, match=re.escape(said)):
        wizard.install_service(
            SimpleNamespace(root=PurePosixPath("/home/robotuser/.roboglot/onboard")),
            say=lambda _line: None,
            ask=NoQuestions(),
            run=lambda command: ran.append(command) or 0,
        )
    assert (staged, ran, list(tmp_path.iterdir())) == ([], [], [])


@pytest.mark.skipif(sys.platform == "win32", reason="Windows files have no POSIX modes")
def test_the_brains_key_is_written_for_its_user_alone(tmp_path: Path) -> None:
    path = tmp_path / "robot.env"
    wizard.write_key_file(path, "LOCAL_API_KEY", "secret-value")
    assert path.read_text(encoding="utf-8") == "LOCAL_API_KEY=secret-value\n"
    assert (path.stat().st_mode & 0o077) == 0
    with pytest.raises(wizard.WizardError):
        wizard.write_key_file(path, "LOCAL_API_KEY", "two\nlines")
