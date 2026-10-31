"""The onboard service: one robot's passport, radio, talk and brain in one long-lived process.

`quackd onboard serve` runs it, in a terminal or under systemd. It reads the robot's directory
(robot.yaml, the robot key, the passport its owner signed), checks that passport the way any
robot would before it says a word, then:

- serves the passport and the talk inbox over HTTP, and on a Jetson announces both by DNS-SD
  with the beacon id the radio advertises now;
- advertises on the radio and listens to it, and lets the radio decide who is near
  (`quackd.radio`): a robot near enough is met over the LAN when both are on one, and over a
  Bluetooth link when not, the lower beacon id dialling;
- answers a hello over the network only from a robot the radio heard, so sharing a network is
  never being near;
- hands every robot met to the run slot, which runs the meeting task while the robot is idle
  and shows the newcomer to a goal when one is running (`quackd.onboard.brain`);
- takes goals, stops and pins from `quackd onboard` over the control connection, and writes
  status.json every two seconds for `quackd onboard status`.

Talk stays off, and status says why, while the robot's own passport does not check out: missing,
signed for another key, expired, or a clock earlier than the day it was signed.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quackd import __version__
from quackd.boards import BoardFacts
from quackd.onboard import config
from quackd.onboard.brain import RunSlot
from quackd.onboard.config import RobotFile
from quackd.onboard.keys import read_robot_key
from quackd.onboard.radio_side import RadioSide, board_side, proximity_policy, sim_side
from quackd.onboard.store import OnboardDir
from quackd.passport import PassportError, document
from quackd.passport.serve import PassportServer, Served
from quackd.passport.talk import MAX_HELLO_BYTES
from quackd.passport_talk import Heard, Talker
from quackd.passport_trust import judge
from quackd.radio.meet import MeetEvent

#: How often the service looks at the clock for what is due: robots near and far, meetings, the
#: run slot. The radio's tracker wants a few looks a second.
TICK_S = 0.25
STATUS_EVERY_S = 2.0
#: The newest meetings kept in met.jsonl's view for `quackd onboard met`, and the newest runs
#: kept in runs/: a robot that meets robots all day must not fill its disk with records.
MET_KEPT = 200
RUNS_KEPT = 200
#: How big met.jsonl grows before it is cut to its newest rows, and how many refusals a minute
#: it takes: a host that sends nothing but refusals fills neither the disk nor the journal.
MET_FILE_MAX = 1 << 20
REFUSALS_PER_MINUTE = 30


@dataclass
class SelfCheck:
    """Whether this robot's own passport lets it talk, and if not, why."""

    ok: bool
    why: str
    reading: document.Reading | None = None


def check_own_passport(token: str | None, robot_did: str, *, now: int | None = None) -> SelfCheck:
    """The robot's own passport, read the way another robot will read it in a hello."""
    if not token:
        return SelfCheck(False, "no signed passport yet: run `quackd onboard sign`")
    try:
        reading = document.read_jws(token, now=now)
    except PassportError as e:
        if e.rule == "4.5" and "ahead of this clock" in str(e):
            return SelfCheck(
                False, f"{e}; this robot's clock is behind: set the time (timedatectl)"
            )
        return SelfCheck(False, f"its own passport does not check out: {e}")
    if reading.id != robot_did:
        return SelfCheck(False, "its passport names another robot key than the one on this robot")
    return SelfCheck(True, "its passport checks out", reading)


def check_board(check: SelfCheck, facts: BoardFacts) -> SelfCheck:
    """A passport says which computer its robot runs on, and a passport signed on a Jetson is
    not served from a laptop standing in for one, nor the other way round: sign it again here."""
    if not check.ok or check.reading is None:
        return check
    signed = check.reading.board().get("kind")
    if signed != facts.kind:
        return SelfCheck(
            False,
            f"its passport says it runs on {signed or 'nothing'}, and this computer is "
            f"{facts.kind}: sign it here with quackd onboard sign",
            check.reading,
        )
    return check


def load_env(path: Path) -> list[str]:
    """robot.env's variables into this process's environment, as systemd's `EnvironmentFile=`
    puts them into the unit's, so a service started in a terminal finds its brain's key too. A
    variable already set wins, as one set on the command line would. The names it set, never a
    value."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    names = []
    for line in lines:
        name, sep, value = line.strip().partition("=")
        name = name.strip()
        if not sep or not name.isidentifier() or name.startswith("#") or os.environ.get(name):
            continue
        os.environ[name] = value.strip()
        names.append(name)
    return names


def prune(runs: Path, *, keep: int) -> None:
    """The newest `keep` run records in `runs`, by when each was written; the rest go."""
    import shutil

    try:
        records = [p for p in runs.iterdir() if p.is_dir()]
    except OSError:
        return
    records.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old in records[keep:]:
        shutil.rmtree(old, ignore_errors=True)


class AlreadyRunning(Exception):
    """Another service holds this robot's directory."""


@contextlib.contextmanager
def held(path: Path) -> Iterator[None]:
    """The robot's directory, for one service at a time: two would answer every hello twice."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AlreadyRunning(
                f"another quackd onboard serve is running for {path.parent}: quackd onboard "
                "status shows it"
            ) from None
        yield
    finally:
        os.close(fd)


def _meeting_go(_why: str) -> bool:
    """A meeting's standing answer to the pilot's doubt: go, since a meeting moves nothing."""
    return True


_meeting_go.answers_as = "the meeting task, which moves nothing"  # type: ignore[attr-defined]


class Service:
    """One robot's onboard service. `start` brings every part up, `run` ticks until a stop,
    `close` takes it all down in the reverse order."""

    def __init__(
        self,
        home: OnboardDir,
        *,
        facts: BoardFacts,
        radio_side: RadioSide | Callable[[], RadioSide],
        log: Callable[[str], None],
    ) -> None:
        self.home = home
        self.facts = facts
        #: the radio while talk is on, and how to make one, since talk turns off when the
        #: robot's own passport stops checking out and on again when it does
        self.radio_side: RadioSide | None = None
        self._make_side: Callable[[], RadioSide] | None = None
        if isinstance(radio_side, RadioSide):
            self.radio_side = radio_side
        else:
            self._make_side = radio_side
        self.log = log
        #: why robot.yaml, edited since the service read it, does not read, for status
        self.file_problem: str | None = None
        self._lock = threading.Lock()
        self._refusals: deque[float] = deque()
        self._refusals_dropped = 0
        self.file: RobotFile = config.load(home.robot_yaml)
        self.robot_key = read_robot_key(home.robot_key)
        self.token = (
            home.passport_jws.read_text(encoding="utf-8").strip()
            if home.passport_jws.exists()
            else None
        )
        self.check = check_board(check_own_passport(self.token, self.robot_key.did), facts)
        self._stop = threading.Event()
        self._last_status = 0.0
        #: when robot.yaml and passport.jws last changed, as the service last read them: a file
        #: a person edited is read again on the next look, with no command needed
        self._seen = self._stamps()
        self.server: PassportServer | None = None
        self.talker: Talker | None = None
        self.slot: RunSlot | None = None
        self.control: Any = None
        self._courtesy: Any = None

    # ── up and down ─────────────────────────────────────────────────────────────────────

    def start(self) -> Service:
        self.home.ensure()
        self.log(f"onboard {self.file.passport.name}: {self.check.why}")
        self.slot = RunSlot(
            self.file,
            self._make_loop,
            safe_verbs=self._safe_verbs,
            log=self.log,
            holding=self._holding,
            released=self._released,
        )
        self._start_talk()
        from quackd.onboard.control import ControlServer

        self.control = ControlServer(
            self._control,
            hello={"robot": self.file.passport.name, "version": __version__},
        ).start(self.home.control_json)
        self._write_status()
        return self

    def run(self) -> None:
        """Tick until stopped: what the radio says is due, the run slot, the status file."""
        with contextlib.suppress(ValueError):  # not the main thread: the caller handles signals
            signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        while not self._stop.wait(TICK_S):
            self.tick()

    def _start_talk(self) -> None:
        """Talk on, for a passport that checks out: the Talker, the passport server and the
        radio. At start, and again whenever a check that failed passes, a passport signed or a
        clock set."""
        reading = self.check.reading
        if self.talker is not None or not self.check.ok or reading is None or self.token is None:
            return
        if self.radio_side is None:
            if self._make_side is None:
                return  # a radio handed over once, and closed since, cannot be had again
            self.radio_side = self._make_side()
        side = self.radio_side
        side.on_event = self._meet_event
        side.busy_elsewhere = self._in_a_meeting
        talker = Talker(
            self.file.passport.name,
            self.robot_key,
            self.token,
            judge=self._judge_for(reading),
            send=side.send,
            runtime={"software": f"quackd/{__version__}"},
            heard=side.heard,
            on_heard=self._heard,
            on_session=side.on_session,
            on_refused=self._refused_hello,
        )
        served = Served(self.file.passport.name, reading.raw, self.token)
        server = PassportServer(
            served,
            host=side.lan_host,
            port=self.file.lan.port,
            inbox=talker.inbox_http,
            inbox_limit=MAX_HELLO_BYTES,
        ).start()
        talker.endpoint.runtime["reach"] = {"http_port": server.port}
        self.talker, self.server = talker, server
        side.start(talker, short_id=reading.short_id, digest=reading.digest, http_port=server.port)

    def _stop_talk(self, why: str) -> None:
        """Talk off, until the passport checks out again: a bye to every robot met, and the
        radio and the passport server down."""
        talker, self.talker = self.talker, None
        if talker is None:
            return
        for peer in list(talker.peers_open()):
            with contextlib.suppress(Exception):
                talker.leave(peer)
        side, self.radio_side = self.radio_side, None
        if side is not None:
            with contextlib.suppress(Exception):
                side.close()
        server, self.server = self.server, None
        if server is not None:
            server.close()
        self.log(f"talk is off: {why}")

    def _recheck(self) -> None:
        """Its own passport against the clock again: one that ran out turns talk off, and a
        clock set since, or a passport signed since, turns it back on."""
        was = self.check.ok
        self.check = check_board(check_own_passport(self.token, self.robot_key.did), self.facts)
        if self.check.ok and self.talker is None:
            if not was:
                self.log(f"onboard {self.file.passport.name}: {self.check.why}")
            try:
                self._start_talk()
            except Exception as e:  # a port taken, say: said, and tried again on the next look
                self.log(f"talk could not start: {e}")
        elif not self.check.ok and self.talker is not None:
            self._stop_talk(self.check.why)

    def tick(self) -> None:
        if self.radio_side is not None and self.talker is not None:
            try:
                self.radio_side.tick()
            except Exception as e:  # one bad tick must not end the service
                self.log(f"radio: a tick failed: {e}")
        if self.slot is not None:
            self.slot.tick()
        now = time.monotonic()
        if now - self._last_status >= STATUS_EVERY_S:
            self._last_status = now
            stamps = self._stamps()
            if stamps != self._seen:
                self._seen = stamps
                done = self._reload()
                self.log(
                    "robot.yaml or passport.jws changed: read again"
                    if done.get("ok")
                    else f"robot.yaml changed and does not read: {done.get('reason')}"
                )
            self._recheck()
            self._write_status()

    def _stamps(self) -> tuple[float | None, float | None]:
        def stamp(path: Path) -> float | None:
            try:
                return path.stat().st_mtime
            except OSError:
                return None

        return stamp(self.home.robot_yaml), stamp(self.home.passport_jws)

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        """Down in the reverse order: the run, a bye to every robot met, the radio, the rest."""
        if self.slot is not None:
            self.slot.stop("the service is stopping")
            running = self.slot.running
            if running is not None and running.thread is not None:
                running.thread.join(timeout=10)
        if self.talker is not None:
            for peer in list(self.talker.peers_open()):
                with contextlib.suppress(Exception):
                    self.talker.leave(peer)
        if self.radio_side is not None:
            with contextlib.suppress(Exception):
                self.radio_side.close()
        if self.server is not None:
            self.server.close()
        if self.control is not None:
            self.control.close()
        with contextlib.suppress(OSError):
            self.home.control_json.unlink()

    # ── trust and what was heard ────────────────────────────────────────────────────────

    def _judge_for(self, own: document.Reading) -> Callable[[document.Reading], Any]:
        file = self.file

        def judged(reading: document.Reading) -> Any:
            return judge(
                reading,
                own_owner=own.owner_id,
                pins=file.trust_pins(),
                own_name=file.passport.name,
                own_label=file.passport.owner.label,
            )

        return judged

    def _heard(self, heard: Heard) -> None:
        """Every robot met and every word said: to the journal, to met.jsonl, to the slot."""
        words = f": {heard.text}" if heard.text else ""
        self.log(
            f"{heard.passport_name} ({heard.tier}, {heard.name} to the pilot) {heard.kind}{words}"
        )
        if heard.kind == "met":
            self._met_row(heard)
            if self.slot is not None:
                self.log(f"{heard.passport_name}: {self.slot.robot_near(heard.peer, heard.tier)}")
        elif heard.kind in ("left", "quiet") and self.slot is not None:
            self.slot.robot_left(heard.peer)

    def _met_row(self, heard: Heard) -> None:
        row: dict[str, Any] = {
            "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "id": heard.peer,
            "name": heard.passport_name,
            "shown": heard.name,
            "tier": heard.tier,
        }
        session = self.talker.endpoint.session_with(heard.peer) if self.talker else None
        if session is not None:
            # what `quackd onboard pin` needs: a short id to name it by, and its owner's key
            # and label for `--owner`, all read from the passport its owner signed
            owner = session.passport.document.get("owner", {})
            row["short_id"] = session.passport.short_id
            row["owner_id"] = session.passport.owner_id
            row["owner_label"] = owner.get("label")
            row["via"] = str(session.route or "").split(":", 1)[0] or None
            warnings = list(self.talker.tier_of(session).warnings) if self.talker else []
            if warnings:
                row["warn"] = warnings
                for warning in warnings:
                    self.log(f"{heard.passport_name}: {warning}")
        self._append(self.home.met_log, row)

    def refused(self, who: str, why: str) -> None:
        """A robot the radio heard whose hello did not check out: logged, and kept for `met`,
        at most `REFUSALS_PER_MINUTE` a minute, so a flood fills neither the disk nor the log."""
        now = time.monotonic()
        with self._lock:
            while self._refusals and now - self._refusals[0] > 60.0:
                self._refusals.popleft()
            if len(self._refusals) >= REFUSALS_PER_MINUTE:
                self._refusals_dropped += 1
                return
            self._refusals.append(now)
            dropped, self._refusals_dropped = self._refusals_dropped, 0
        if dropped:
            self.log(f"{dropped} more refusals in the last minute were not written down")
        self.log(f"refused {who}: {why}")
        row = {"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "refused": who, "why": why}
        self._append(self.home.met_log, row)

    def _refused_hello(self, origin: str, status: int, why: str) -> None:
        """A hello that came to this robot and did not check out."""
        if status == 403:
            return  # not heard by radio yet: the other robot tries again in a moment
        self.refused(origin, why.strip())

    def _meet_event(self, event: MeetEvent) -> None:
        """A hello this robot said that the other robot's passport, or its answer, failed."""
        if event.kind == "refused":
            self.refused(event.did or event.route or event.beacon_id, event.reason)

    def _in_a_meeting(self) -> bool:
        running = self.slot.running if self.slot is not None else None
        return running is not None and running.kind == "meeting"

    def _append(self, path: Path, row: Mapping[str, Any]) -> None:
        """One row more, one writer at a time, and the file cut to its newest rows once it
        passes `MET_FILE_MAX`."""
        with self._lock, contextlib.suppress(OSError):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8", newline="") as f:
                f.write(json.dumps(row) + "\n")
            if path.stat().st_size > MET_FILE_MAX:
                from quackd.onboard.control import write_private

                kept = path.read_text(encoding="utf-8").splitlines()[-MET_KEPT * 5 :]
                write_private(path, "\n".join(kept) + "\n")

    # ── runs ────────────────────────────────────────────────────────────────────────────

    def _body(self) -> Any:
        if self.file.robot is None:
            from quackd.onboard.body import DescribedAdapter

            return DescribedAdapter(self.file)
        from quackd.adapters.factory import make_adapter, parse_robot_spec

        spec = parse_robot_spec(self.file.robot.adapter)
        return make_adapter(spec, address=self.file.robot.address)

    def _safe_verbs(self) -> list[str]:
        if self.file.robot is None:
            from quackd.onboard.body import VERBS

            return list(VERBS)
        from quackd.adapters.factory import describe, parse_robot_spec

        manifest = describe(parse_robot_spec(self.file.robot.adapter))
        return [v.name for v in manifest.verbs if v.safety_class == "safe"]

    def _holding(self, agent: Any) -> None:
        """A run holds the body: the robots met may ask it for a courtesy, which goes through
        this run's executor on this run's loop, and only while it runs."""
        import asyncio

        from quackd.passport_courtesy import Courtesy

        if self.talker is None:
            return
        if self._courtesy is None:
            # one for the robot's whole life: its budget is every peer's together, and a new
            # run must not hand a peer a fresh one
            self._courtesy = Courtesy(agent.executor, asyncio.get_running_loop())
        self._courtesy.executor = agent.executor
        self._courtesy.loop = asyncio.get_running_loop()
        self._courtesy.stranger_pauses = self.file.trust.strangers_may_pause
        self.talker.courtesy = self._courtesy

    def _released(self) -> None:
        if self.talker is not None:
            self.talker.courtesy = None

    def _make_loop(self, duck: Any, kind: str, front: Any) -> Any:
        from quackd.agent.loop import AgentLoop, RunConfig

        prune(self.home.runs, keep=RUNS_KEPT - 1)
        from quackd.agent.providers.factory import make_provider, resolve_llm
        from quackd.memory import RobotMemory
        from quackd.safety import deny_all

        brain = self.file.brain
        vendor, model, _source = resolve_llm(None, brain.llm, robot=self.file.passport.name)
        provider = make_provider(
            vendor,
            model=model,
            source="robot.yaml brain.llm",
            duck_name=duck.name,
            goal=duck.body if kind == "goal" else None,
            base_url=brain.base_url,
        )
        cfg = RunConfig(
            duck=duck,
            provider=provider,
            transport=self._body(),
            runs_dir=self.home.runs,
            confirm=front.confirm if front is not None else deny_all,
            decide=front.decide if front is not None else _meeting_go,
            acknowledge=front.acknowledge if front is not None else None,
            view=front.view if front is not None else None,
            # a goal's notes are the robot's; a meeting keeps none, since what it would note
            # is what a robot it met claimed, and a note comes back to a later run as the
            # robot's own word
            memory=RobotMemory(self.file.passport.name, self.home.memory)
            if kind == "goal"
            else None,
            price=brain.price,
            nearby=self.talker,
        )
        return AgentLoop(cfg)

    # ── control ─────────────────────────────────────────────────────────────────────────

    def _control(self, op: Mapping[str, Any], conn: Any) -> dict[str, Any] | None:
        name = op.get("op")
        if name == "status":
            return {"ev": "status", **self.status()}
        if name == "stop":
            stopped = self.slot.stop() if self.slot is not None else False
            return {"ev": "stopped", "was": stopped}
        if name == "met":
            return {"ev": "met", "rows": self._met_rows(int(op.get("limit") or 20))}
        if name == "reload":
            self._seen = self._stamps()
            return {"ev": "reloaded", **self._reload()}
        if name == "shutdown":
            self.stop()
            return {"ev": "bye"}
        if name == "goal":
            return self._goal(op, conn)
        return {"ev": "refused", "reason": f"quackd onboard takes no {name!r}"}

    def _goal(self, op: Mapping[str, Any], conn: Any) -> dict[str, Any] | None:
        from quackd.front import Channel, Front

        text = str(op.get("text") or "").strip()
        if not text:
            return {"ev": "refused", "reason": "a goal is a sentence"}
        if self.slot is None:
            return {"ev": "refused", "reason": "the service is still starting"}
        channel = Channel(conn.writer)
        front = Front(channel, "onboard goal", quit_now=lambda: None)
        channel.on_broken(front.on_end)
        if op.get("yes") is True:
            # the run's record says --yes answered, never a person, for a doubt as for a verb
            front.confirm.answers_as = "--yes"  # type: ignore[attr-defined]
            front.decide.answers_as = "--yes"  # type: ignore[attr-defined]
        steps = op.get("max_steps")
        why = self.slot.goal(
            text, front, max_steps=steps if isinstance(steps, int) and steps > 0 else None
        )
        self.log(f"a goal: {text}" + (f", refused: {why}" if why else ""))
        if why is not None:
            channel.close({"ev": "refused", "reason": why})
            conn.close()
            return None
        channel.start_reading(front.on_op, front.on_end, take=lambda: conn.sock.recv(65_536))
        return None

    def _reload(self) -> dict[str, Any]:
        """robot.yaml again, for the settings that change without a signature: the pins above
        all, which a session judged before keeps until it ends."""
        try:
            self.file = config.load(self.home.robot_yaml)
        except config.FileError as e:
            self.file_problem = f"{e}; the service keeps the file it read last"
            return {"ok": False, "reason": str(e)}
        self.file_problem = None
        # a passport signed again since start: served from now on, and in every new hello
        if self.home.passport_jws.exists():
            token = self.home.passport_jws.read_text(encoding="utf-8").strip()
            check = check_board(check_own_passport(token, self.robot_key.did), self.facts)
            if check.ok and check.reading is not None and token != self.token:
                self.token, self.check = token, check
                if self.server is not None:
                    self.server.update(Served(self.file.passport.name, check.reading.raw, token))
                if self.talker is not None:
                    self.talker.endpoint.passport = token
                else:
                    self._start_talk()  # signed while talk was off: on, at once
        if self.slot is not None:
            self.slot.file = self.file
        if self.talker is not None and self.check.reading is not None:
            self.talker._judge = self._judge_for(self.check.reading)
            self.talker.rejudge()
        return {"ok": True, "pins": len(self.file.trust.pins)}

    def _met_rows(self, limit: int) -> list[dict[str, Any]]:
        try:
            lines = self.home.met_log.read_text(encoding="utf-8").splitlines()[-MET_KEPT:]
        except OSError:
            return []
        rows = []
        for line in lines[-limit:]:
            with contextlib.suppress(ValueError):
                rows.append(json.loads(line))
        return rows

    # ── status ──────────────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        reading = self.check.reading
        nearby = []
        if self.talker is not None:
            with contextlib.suppress(Exception):
                for row in self.talker.nearby():
                    nearby.append({"shown": row.get("name"), "tier": row.get("tier")})
        return {
            "pid": os.getpid(),
            "version": __version__,
            "robot": self.file.passport.name,
            "board": self.facts.passport()
            | ({"release": self.facts.release} if self.facts.release else {}),
            "passport": {
                "ok": self.check.ok,
                "why": self.check.why,
                **(
                    {
                        "short_id": reading.short_id,
                        "owner_short_id": reading.owner_short_id,
                        "expires": reading.document["expires"],
                    }
                    if reading is not None
                    else {}
                ),
            },
            "brain": {"llm": self.file.brain.llm, "base_url": self.file.brain.base_url},
            "lan": {"port": self.server.port if self.server is not None else None},
            "radio": self.radio_side.status()
            if self.radio_side is not None
            else {"state": "off", "detail": f"talk is off: {self.check.why}"},
            "file_problem": self.file_problem,
            "run": self.slot.state() if self.slot is not None else {"run": "idle"},
            "nearby": nearby,
        }

    def _write_status(self) -> None:
        from quackd.onboard.control import write_private

        with contextlib.suppress(OSError):
            write_private(self.home.status_json, json.dumps(self.status(), default=str) + "\n")


def _browser(log: Callable[[str], None]) -> Any:
    """The DNS-SD browse that finds other robots' LAN records by beacon id, or None without
    zeroconf, when robots near are met over a link."""
    try:
        from quackd.lan.passports import PassportBrowser

        return PassportBrowser().start()
    except Exception as e:  # no zeroconf, or no network yet
        log(f"lan: no DNS-SD browse ({e}), so robots near are met over a link")
        return None


@dataclass(frozen=True)
class SimPlace:
    """Where a robot on the sim board stands and walks, on which sim air and which sim LAN."""

    air: Path
    path: str = "0,0@0"
    network: str = "home"
    t0: float | None = None
    speed: float | None = None
    seed: int = 0
    token: str | None = None


def serve(
    home: OnboardDir,
    *,
    board: str | None,
    log: Callable[[str], None],
    sim: SimPlace | None = None,
    radio: str | None = None,
    meet_within_m: float | None = None,
    started: Callable[[Service], None] | None = None,
) -> None:
    """`quackd onboard serve`: this robot's service, until SIGTERM, Ctrl-C or a shutdown over
    the control connection. A board that is not a robot's raises from `quackd.boards.detect`,
    and a directory another service holds raises `AlreadyRunning`."""
    from quackd.boards import detect
    from quackd.radio.sim import default_air_dir

    chosen, facts = detect(board)
    file = config.load(home.robot_yaml)
    for name in load_env(home.robot_env):
        log(f"the brain's {name} is read from robot.env")
    speed = sim.speed if sim is not None and sim.speed else 1.0
    policy = proximity_policy(
        meet_within_m=meet_within_m or file.radio.meet_within_m,
        rssi_at_1m=file.radio.rssi_at_1m,
        speed=speed,
    )
    rotate_s = file.radio.rotate_min * 60.0 / speed

    def side() -> RadioSide:
        if facts.kind == "sim" and radio != "off":
            place = sim or SimPlace(air=default_air_dir())
            return sim_side(
                air=place.air,
                path=place.path,
                network=place.network,
                t0=place.t0,
                speed=place.speed,
                seed=place.seed,
                token=place.token,
                policy=policy,
                rotate_s=rotate_s,
                log=log,
            )
        return board_side(
            radio_kind=radio or chosen.radio_kind,
            instance=file.passport.name,
            browser=_browser(log),
            policy=policy,
            rotate_s=rotate_s,
            log=log,
            advertise=file.radio.advertise,
        )

    home.ensure()
    with held(home.lock):
        service = Service(home, facts=facts, radio_side=side, log=log)
        try:
            service.start()
            if started is not None:
                started(service)
            service.run()
        except KeyboardInterrupt:
            log("stopping: Ctrl-C")
        finally:
            service.close()
