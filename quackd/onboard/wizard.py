"""`quackd onboard`, run bare: put a passport on this robot in one sitting.

It asks what only the owner knows (the robot's name and what it is, who owns it, how a person
stops it, which model is its brain), writes robot.yaml from a commented template, makes the
robot's key and the owner's, signs, and starts the service. Run again, it reads the file it wrote
and signs only when the passport section changed or the signature runs out within 30 days. Every
command it runs with sudo is printed before it runs.

The questions come through `Asker`, so a test answers them and `--yes` takes every default; a
file given with `--file` answers all of them.
"""

from __future__ import annotations

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

from quackd.onboard import config, facts

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
    from quackd.onboard.keys import read_passphrase_file

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
    """Sign robot.yaml's passport section with the owner key and write passport.jws."""
    from quackd.onboard.keys import read_sealed, unseal
    from quackd.passport import document

    home.ensure()
    robot_key, robot_made = home.robot_key_or_new()
    new_owner = not home.owner_key.exists()
    passphrase = owner_passphrase(ask, passphrase_file, new=new_owner)
    if new_owner:
        owner_key, owner_made = home.owner_key_or_new(passphrase)
    else:
        owner_key, owner_made = unseal(read_sealed(home.owner_key), passphrase), False
    moment = (now or datetime.now(UTC)).replace(microsecond=0)
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
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(token + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return Signed(token, path, robot_made, owner_made)


def needs_signing(
    home: Any, file: config.RobotFile, board: dict[str, str], *, manifest: Any = None
) -> str | None:
    """Why the passport has to be signed now, or None when the signed one still says it all."""
    from quackd.passport import PassportError, document

    if not home.passport_jws.exists():
        return "it has never been signed"
    try:
        reading = document.read_jws(home.passport_jws.read_text(encoding="utf-8").strip())
    except PassportError as e:
        return f"the signed one does not check out ({e})"
    changed = facts.unsigned_changes(file, reading, board=board, manifest=manifest)
    if changed:
        return f"robot.yaml changed since it was signed: {', '.join(changed)}"
    left = reading.expires - int(datetime.now(UTC).timestamp())
    if left < RENEW_WITHIN_DAYS * 86_400:
        return f"its signature runs out in {max(0, left) // 86_400} days"
    return None


def ask_for_file(ask: Asker, *, default_name: str) -> str:
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
    llm = ask.text("its brain, as --llm spells it (e.g. local:MODEL, anthropic)", "")
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
    """quackd onboard runs as the robot's own user: as root, its files would be root's, and
    the service, which runs as that user, could not read them."""
    euid = getattr(os, "geteuid", None)  # POSIX only: Windows has no root to refuse
    if euid is not None and euid() == 0:
        raise WizardError("run quackd onboard as the robot's own user, not as root")


def write_key_file(path: Path, variable: str, value: str) -> None:
    """The brain's key, as the one line of robot.env, readable by this user alone."""
    from quackd.onboard.keys import write_secret

    if "\n" in value or "\r" in value:
        raise WizardError("a key is one line")
    write_secret(path, f"{variable}={value}\n")


def install_service(
    home: Any, *, say: Callable[[str], None], ask: Asker, run: Callable[[list[str]], int]
) -> bool:
    """Write the unit, show every sudo command, and run them when the person says yes."""
    from quackd.onboard import systemd

    refuse_root()
    quackd = shutil.which("quackd") or sys.argv[0]
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "root"
    with_group = systemd.group_exists(systemd.BLUETOOTH_GROUP)
    if not with_group:
        say(f"this system has no {systemd.BLUETOOTH_GROUP} group, so the unit names none")
    text = systemd.unit_text(
        user=user, directory=home.root, quackd=quackd, bluetooth_group=with_group
    )
    staged = Path(tempfile.mkdtemp()) / systemd.UNIT_NAME
    staged.write_text(text, encoding="utf-8", newline="\n")
    commands = systemd.install_commands(staged)
    say("to run it at every boot, quackd onboard will now run, with sudo:")
    for command in commands:
        say(f"  {systemd.shown(command)}")
    if not ask.yes("run them now?", default=True):
        say("not installed: run those yourself, or quackd onboard serve in a terminal")
        return False
    for command in commands:
        if run(command) != 0:
            raise WizardError(f"this failed: {systemd.shown(command)}")
    return True


def run_command(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode
