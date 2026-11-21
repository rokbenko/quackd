"""`roboglot onboard`, run bare: put a passport on this robot in one sitting.

It asks what only the owner knows (the robot's name and what it is, who owns it, how a person
stops it, which LLM is its brain), writes robot.yaml from a commented template, makes the
robot's key and the owner's, signs, and starts the service. Run again, it reads the file it wrote
and signs only when the passport section changed or the signature runs out within 30 days. Every
command it runs with sudo is printed before it runs.

The questions come through `Asker`, so a test answers them and `--yes` takes every default; a
file given with `--file` answers all of them.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from roboglot.onboard import config, facts
from roboglot.onboard.preflight import EARLIEST_YEAR, SET_THE_TIME

#: Sign again when the owner's signature runs out within this many days.
RENEW_WITHIN_DAYS = 30


class Asker(Protocol):
    def text(self, question: str, default: str | None = None) -> str: ...
    def secret(self, question: str, *, twice: bool = False) -> str: ...
    def yes(self, question: str, default: bool = False) -> bool: ...


class WizardError(RuntimeError):
    """The wizard stopped, and why, in words for the person running it."""


@dataclass
class Signed:
    """What signing made: the passport, where it went, and whether a key was made just now."""

    token: str
    path: Path
    robot_key_made: bool
    owner_key_made: bool


def owner_passphrase(ask: Asker, passphrase_file: Path | None, *, new: bool) -> str:
    """The owner's passphrase, from a file a script names or asked for (twice for a new key)."""
    from roboglot.onboard.keys import read_passphrase_file

    if passphrase_file is not None:
        return read_passphrase_file(passphrase_file)
    question = (
        "a passphrase for your owner key (12 characters at least)"
        if new
        else ("your owner key's passphrase")
    )
    return ask.secret(question, twice=new)


def sign(
    home: Any,
    file: config.RobotFile,
    board: dict[str, str],
    *,
    ask: Asker,
    passphrase_file: Path | None = None,
    days: int,
    manifest: Any = None,
    now: datetime | None = None,
) -> Signed:
    """Sign robot.yaml's passport section with the owner key and write passport.jws. A clock
    before any passport Roboglot signs refuses, before a key is made: the signature would carry
    its date for a year."""
    from roboglot.onboard.keys import OPEN_MODE, _replace, read_sealed, unseal
    from roboglot.passport import document

    moment = (now or datetime.now(UTC)).replace(microsecond=0)
    if moment.year < EARLIEST_YEAR:
        raise WizardError(
            f"this computer's clock says {moment:%Y-%m-%d}, before any passport Roboglot signs: "
            f"{SET_THE_TIME}, then sign"
        )
    home.ensure()
    robot_key, robot_made = home.robot_key_or_new()
    new_owner = not home.owner_key.exists()
    passphrase = owner_passphrase(ask, passphrase_file, new=new_owner)
    if new_owner:
        owner_key, owner_made = home.owner_key_or_new(passphrase)
    else:
        owner_key, owner_made = unseal(read_sealed(home.owner_key), passphrase), False
    doc = facts.passport_document(
        file,
        robot_did=robot_key.did,
        owner_did=owner_key.did,
        board=board,
        issued=moment,
        expires=moment + timedelta(days=days),
        manifest=manifest,
    )
    token = document.sign(doc, owner_key)
    path = home.passport_jws
    # through a file of this write's own, never a link, at the mode any new file takes here,
    # since others are meant to read it
    _replace(path, (token + "\n").encode("utf-8"), sync=False, mode=OPEN_MODE)
    return Signed(token, path, robot_made, owner_made)


def needs_signing(
    home: Any,
    file: config.RobotFile,
    board: dict[str, str],
    *,
    manifest: Any = None,
    file_alone: bool = False,
) -> str | None:
    """Why the passport has to be signed now, or None when the signed one still says it all.
    With `file_alone` only what the file alone decides is compared, and no passport is built."""
    from roboglot.passport import PassportError, document

    if not home.passport_jws.exists():
        return "it has never been signed"
    try:
        reading = document.read_jws(
            home.passport_jws.read_text(encoding="utf-8", errors="replace").strip()
        )
    except PassportError as e:
        return f"the signed one does not check out ({e})"
    changed = (
        facts.file_changes(file, reading, board=board)
        if file_alone
        else facts.unsigned_changes(file, reading, board=board, manifest=manifest)
    )
    edited = [name for name in changed if name != "board"]
    if edited:
        return f"robot.yaml changed since it was signed: {', '.join(edited)}"
    if changed:
        return "this computer calls itself something else since it was signed"
    left = reading.expires - int(datetime.now(UTC).timestamp())
    if left < RENEW_WITHIN_DAYS * 86_400:
        return f"its signature runs out in {max(0, left) // 86_400} days"
    return None


def signing_state(
    home: Any, file: config.RobotFile, board: dict[str, str]
) -> tuple[str | None, str | None]:
    """Why the passport has to be signed now, and what kept all of it from being compared, each
    None when nothing did. Preflight and status ask this and neither stops for it: a body's adapter
    may be a third party's and raise anything, and what the file and its manifest say may make no
    passport, while what the file alone decides can still be compared."""
    only = "so only the name, maker, owner and board can be compared"
    try:
        manifest = facts.manifest_of(file)
    except Exception as e:  # a third party's adapter can raise anything
        trouble = f"the body its file names cannot be read here, {only}: {facts.why_unread(e)}"
    else:
        try:
            return needs_signing(home, file, board, manifest=manifest), None
        except Exception as e:  # a passport refuses what a manifest or a file may say
            what = "robot.yaml" if manifest is None else "robot.yaml and the body it names"
            trouble = f"no passport can be built from {what}, {only}: {facts.why_unread(e)}"
    return needs_signing(home, file, board, file_alone=True), trouble


def ask_for_file(ask: Asker, *, default_name: str, port: int = 8790) -> str:
    """robot.yaml's text from a person's answers: the few things only an owner knows."""
    name = ask.text("this robot's name (lower case, digits, - and _)", default_name)
    embodiment = ask.text("what it is: humanoid, quadruped, biped, wheeled or tracked", "humanoid")
    mobility = {"humanoid": "legged", "quadruped": "legged", "biped": "legged"}.get(
        embodiment, embodiment
    )
    vendor = ask.text("who made it (the maker's name, e.g. unitree)", "unknown")
    model = ask.text("its model (e.g. g1-edu)", "unknown")
    owner = ask.text("who owns it, as other robots should read it", "my robots")
    contact = ask.text("how to reach its owner (a URL or an e-mail address, or nothing)", "")
    how_to_stop = ask.text("how a person standing next to it stops it, in one sentence", "")
    llm = ask.text("its brain, as --llm spells it (e.g. local:LLM, anthropic)", "")
    base_url = ask.text("the brain's address, for an OpenAI-compatible server (or nothing)", "")
    within = _metres(ask.text("how close, in metres, another robot comes before they meet", "2"))
    return config.template(
        name=name,
        vendor=vendor,
        model=model,
        owner_label=owner,
        owner_contact=contact or None,
        embodiment=embodiment,
        mobility=mobility,
        manipulator="arms" if embodiment == "humanoid" else "none",
        estop="none",
        how_to_stop=how_to_stop or None,
        llm=llm or None,
        base_url=base_url or None,
        meet_within_m=within,
        port=port,
    )


def _metres(answer: str) -> float:
    """A distance as a person types it, 2 or 1.5 or 3 m, held to what robot.yaml takes."""
    try:
        metres = float(answer.strip().removesuffix("m").strip().replace(",", "."))
    except ValueError:
        raise WizardError(f"{answer!r} is no distance: a number of metres, such as 2") from None
    if not 0 < metres <= 20:
        raise WizardError(f"{metres:g} m is not a distance the radio judges: above 0, up to 20")
    return metres


def refuse_root() -> None:
    """roboglot onboard runs as the robot's own user: as root, its files would be root's, and
    the service, which runs as that user, could not read them."""
    euid = getattr(os, "geteuid", None)  # POSIX only: Windows has no root to refuse
    if euid is not None and euid() == 0:
        raise WizardError("run roboglot onboard as the robot's own user, not as root")


def unit_user() -> str:
    """The account the unit runs as: the one whose effective uid runs the wizard, as the system's
    account database names it. Never `USER` or `LOGNAME`, which a shell, `sudo` or `env` can
    leave naming another account or none at all, and never root for want of a name: with no
    account to name, the service has nobody to run as and is not installed."""
    uid, accounts = _account_database()
    try:
        return str(accounts.getpwuid(uid).pw_name)
    except KeyError:
        raise WizardError(
            f"the service has no user to run as: no account on this system has uid {uid}, which "
            "runs roboglot onboard; run roboglot onboard serve in a terminal instead"
        ) from None


#: The names systemd takes a uid for without asking the account database (`get_user_creds`,
#: in its src/basic/user-util.c): root's, and nobody's, the uid it keeps for that name.
FOUND_WITHOUT_THE_DATABASE = {"root": 0, "nobody": 65534}


def refuse_another_uid(user: str) -> None:
    """Refuse a `User=` that systemd would find as another uid than the one running the wizard:
    it takes `root` and `nobody` without the account database and finds every other name in it,
    so a name the database gives this uid that leads systemd to another uid would have the
    service run as that account, root among them."""
    uid, accounts = _account_database()
    found = FOUND_WITHOUT_THE_DATABASE.get(user)
    if found is None:
        try:
            found = int(accounts.getpwnam(user).pw_uid)
        except KeyError:
            raise WizardError(
                f"the service has no user to run as: the account database names uid {uid} "
                f"{user!r}, and has no account named {user!r} for systemd to find"
            ) from None
    if found != uid:
        raise WizardError(
            f"the service has no user to run as: the account database names uid {uid} "
            f"{user!r}, and systemd would run the service as uid {found} by that name"
        )


def _account_database() -> tuple[int, Any]:
    """The effective uid running the wizard and the system's account database, or a
    `WizardError` saying there is none to name the service's user from."""
    euid = getattr(os, "geteuid", None)  # POSIX only, as `pwd` is
    try:
        accounts = importlib.import_module("pwd")
    except ImportError:
        accounts = None
    if euid is None or accounts is None:
        raise WizardError(
            "the service has no user to run as: this system has no account database to name "
            "the one running roboglot onboard; run roboglot onboard serve in a terminal instead"
        )
    return int(euid()), accounts


def write_key_file(path: Path, variable: str, value: str) -> None:
    """The brain's key, as the one line of robot.env, readable by this user alone."""
    from roboglot.onboard.keys import write_secret

    if "\n" in value or "\r" in value:
        raise WizardError("a key is one line")
    write_secret(path, f"{variable}={value}\n")


def check_service(home: Any) -> None:
    """Refuse all that the service's unit would refuse, before any sudo line runs, the address
    privacy's included: root, a uid with no account, a name systemd would find as another uid,
    and a folder, user or path to roboglot no unit can hold. `install_service` checks the same
    again as it writes the unit."""
    _unit_text(home, bluetooth_group=True)


def _unit_text(home: Any, *, bluetooth_group: bool) -> str:
    """The unit for the robot directory `home.root`, run as the account running the wizard, or
    a `WizardError` saying why there is none."""
    from roboglot.onboard import systemd

    refuse_root()
    found = shutil.which("roboglot") or sys.argv[0]
    # a relative entry in PATH, or a relative argv[0], means a path from the folder the wizard
    # runs in, which systemd would refuse, or read from a folder of its own
    roboglot = found if os.path.isabs(found) else os.path.abspath(found)
    user = unit_user()
    try:
        text = systemd.unit_text(
            user=user, directory=home.root, roboglot=roboglot, bluetooth_group=bluetooth_group
        )
    except systemd.UnitError as e:  # before a file is staged or a command runs
        raise WizardError(str(e)) from None
    refuse_another_uid(user)
    return text


def install_service(
    home: Any, *, say: Callable[[str], None], ask: Asker, run: Callable[[list[str]], int]
) -> bool:
    """Write the unit, show every sudo command, and run them when the person says yes."""
    from roboglot.onboard import systemd

    with_group = systemd.group_exists(systemd.BLUETOOTH_GROUP)
    text = _unit_text(home, bluetooth_group=with_group)
    if not with_group:
        say(f"this system has no {systemd.BLUETOOTH_GROUP} group, so the unit names none")
    staged = Path(tempfile.mkdtemp()) / systemd.UNIT_NAME
    staged.write_text(text, encoding="utf-8", newline="\n")
    commands = systemd.install_commands(staged)
    say("to run it at every boot, roboglot onboard will now run, with sudo:")
    for command in commands:
        say(f"  {systemd.shown(command)}")
    if not ask.yes("run them now?", default=True):
        say("not installed: run those yourself, or roboglot onboard serve in a terminal")
        return False
    for command in commands:
        if run(command) != 0:
            raise WizardError(f"this failed: {systemd.shown(command)}")
    return True


def offer_privacy(
    *, say: Callable[[str], None], ask: Asker, run: Callable[[list[str]], int]
) -> bool:
    """Offer BlueZ's address privacy: main.conf with `Privacy = device`, a backup of the old
    one, and bluetoothd restarted, every sudo command shown before it runs. True when it was
    set here; False when it was on already, or the person said no."""
    from roboglot.onboard import systemd
    from roboglot.radio.bluez.privacy import (
        BACKUP,
        MAIN_CONF,
        PRIVATE,
        install_commands,
        privacy_setting,
        with_privacy,
    )

    try:
        current = Path(MAIN_CONF).read_text(encoding="utf-8")
    except OSError as e:
        say(f"address privacy is left as it is: {MAIN_CONF} could not be read ({e.strerror})")
        return False
    if privacy_setting(current) in PRIVATE:
        say(f"BlueZ's address privacy is asked for already in {MAIN_CONF}")
        return False
    staged = Path(tempfile.mkdtemp()) / "main.conf"
    staged.write_text(with_privacy(current), encoding="utf-8", newline="")
    commands = install_commands(str(staged), backup_exists=Path(BACKUP).exists())
    say(
        "the radio's address goes out with every advert, and while it is public anybody can "
        "follow this robot by it; roboglot onboard can ask BlueZ to rotate it, with sudo:"
    )
    for command in commands:
        say(f"  {systemd.shown(command)}")
    say("restarting bluetoothd drops a Bluetooth remote or e-stop for a moment")
    if not ask.yes("run them now?", default=True):
        say("left as it is: roboglot onboard status says so while the address is public")
        return False
    for command in commands:
        if run(command) != 0:
            raise WizardError(f"this failed: {systemd.shown(command)}")
    return True


def restore_privacy(
    *, say: Callable[[str], None], ask: Asker, run: Callable[[list[str]], int]
) -> bool:
    """Put back the main.conf roboglot onboard replaced, when it is still what Roboglot made of
    it; a file somebody edited since is theirs, and is left."""
    from roboglot.onboard import systemd
    from roboglot.radio.bluez.privacy import BACKUP, MAIN_CONF, restore_commands, should_restore

    try:
        backup = Path(BACKUP).read_text(encoding="utf-8")
        current = Path(MAIN_CONF).read_text(encoding="utf-8")
    except OSError:
        return False  # no backup: Roboglot never touched main.conf here
    if not should_restore(current, backup):
        say(f"{MAIN_CONF} was edited since roboglot onboard set its privacy, so it is left")
        return False
    commands = restore_commands()
    say(f"to put back the {MAIN_CONF} roboglot onboard replaced, with sudo:")
    for command in commands:
        say(f"  {systemd.shown(command)}")
    if not ask.yes("run them now?", default=True):
        return False
    for command in commands:
        if run(command) != 0:
            raise WizardError(f"this failed: {systemd.shown(command)}")
    return True


def run_command(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode
