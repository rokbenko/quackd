"""The onboard service: one robot's passport, radio, talk and brain in one long-lived process.

`roboglot onboard serve` runs it, in a terminal or under systemd. It reads the robot's directory
(robot.yaml, the robot key, the passport its owner signed), checks that passport the way any
robot would before it says a word, then:

- serves the passport and the talk inbox over HTTP, and on a robot's own computer announces both
  by DNS-SD with the beacon id the radio advertises now;
- advertises on the radio and listens to it, and lets the radio decide who is near
  (`roboglot.radio`): a robot near enough is met over the LAN when both are on one, and over a
  Bluetooth link when not, the lower beacon id dialling;
- answers a hello over the network only from a robot the radio heard, so sharing a network is
  never being near;
- hands every robot met to the run slot, which runs the meeting task while the robot is idle
  and shows the newcomer to a goal when one is running (`roboglot.onboard.brain`);
- takes goals, stops and pins from `roboglot onboard` over the control connection, and writes
  status.json every two seconds for `roboglot onboard status`.

Talk stays off, and status says why, while the robot's own passport does not check out: missing,
signed for another key, expired, or a clock earlier than the day it was signed.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from roboglot import __version__
from roboglot.boards import BoardFacts, radio_of
from roboglot.onboard import config
from roboglot.onboard.brain import CALL_THREADS, RunSlot
from roboglot.onboard.config import RobotFile
from roboglot.onboard.control import ANSWER_S
from roboglot.onboard.keys import read_robot_key
from roboglot.onboard.radio_side import RadioSide, board_side, proximity_policy, sim_side
from roboglot.onboard.store import OnboardDir
from roboglot.onboard.systemd import STOP_TIMEOUT_S
from roboglot.passport import PassportError, document
from roboglot.passport.fetch import FETCH_TIMEOUT_S
from roboglot.passport.serve import PassportServer, Served
from roboglot.passport.talk import MAX_HELLO_BYTES
from roboglot.passport_talk import Heard, Talker
from roboglot.passport_trust import judge
from roboglot.radio.meet import MeetEvent

#: How often the service looks at the clock for what is due: robots near and far, meetings, the
#: run slot. The radio's tracker wants a few looks a second.
TICK_S = 0.25
STATUS_EVERY_S = 2.0
#: The newest meetings kept in met.jsonl's view for `roboglot onboard met`, and the newest runs
#: kept in runs/: a robot that meets robots all day must not fill its disk with records.
MET_KEPT = 200
RUNS_KEPT = 200
#: How big met.jsonl grows before it is cut to its newest rows, and how many refusals a minute
#: it takes: a host that sends nothing but refusals fills neither the disk nor the journal.
MET_FILE_MAX = 1 << 20
REFUSALS_PER_MINUTE = 30
#: The close's shares, each from the unit's stop timeout, `STOP_TIMEOUT_S`, which systemd counts
#: from the stop it asks for, and so does the close (`Service.stop` notes when it came, and tells
#: the run in hand at once, whatever the look in hand is doing):
#: - the close in all, `CLOSE_S`: nine tenths of the timeout. The tenth after it is the process's
#:   own exit, a margin chosen, not measured; a run, or a call one left behind, still going at
#:   its end would hold that exit for good, so the close ends the process itself (`LEFT_EXIT`).
#: - the run: from its stop, all of `CLOSE_S` but the byes' share when there are robots met.
#: - the byes, `AFTER_RUN_S`, kept after the run only on a close with a robot to say bye to:
#:   they go out all at once, each a post on the fetch clock over the LAN (`FETCH_TIMEOUT_S`;
#:   over a link, `post_timeout`, a second or so more).
#: - the LAN record: nothing kept; it goes as the close begins, beside the run's stop.
#: - the radio, the passport server and the listener: nothing kept; they only tidy up what the
#:   process's exit drops anyway, and have what time is left.
CLOSE_S = STOP_TIMEOUT_S * 0.9
AFTER_RUN_S = FETCH_TIMEOUT_S
#: How long a stop over the control port waits for the run it stopped to end before it
#: answers: half of what `roboglot onboard stop` waits for that answer (`ANSWER_S`), so the
#: answer always comes in time to be read.
STOP_WAITS_S = ANSWER_S / 2
#: How long a reload's answer waits for talk being made on another thread (its radio side,
#: Talker and passport server, never its radio's start) to be noted or to fail, so the answer
#: says whether the passport asked about is served: the same half of `ANSWER_S`.
TALK_WAITS_S = ANSWER_S / 2
#: What a goal's terminal is told, and what the journal says, when its second Ctrl-C lets it
#: go while the run it gave is still ending.
STILL_ENDING = (
    "the run is still ending on the robot, and the service runs on: roboglot onboard status "
    "shows when it has ended"
)
LET_GO = (
    "a goal: {text}, its terminal asked to quit at once and is let go; the run is still ending, "
    "and the service stays up"
)
#: The exit code of a process the close ends because a run, or a call one left behind, is still
#: going when its budget is spent: sysexits' EX_SOFTWARE, the service unable to finish its own
#: work. Any code but 0 reads as a failure to systemd: after a stop it was asked for, the unit
#: shows as failed and is not restarted; after a stop it was not asked for (a crash, a
#: `shutdown` over the control port), `Restart=on-failure` starts it again `RestartSec` later.
LEFT_EXIT = 70
#: Why the run in hand stops when the service does, as its record says.
STOPPING = "the service is stopping"
#: The one line, in the journal and on stderr, of a close that ends the process because a run,
#: or a call it handed to a thread of its own, is still going when the budget is spent: nothing
#: in Python ends a thread, and the interpreter would wait for it at its exit, until systemd's
#: kill or, in a terminal, for good. What was sent to the body comes from the run's own record
#: as it was written (`_StopsHeard`); a run's thread that is only slow is never cut, since the
#: process is ended only once the whole budget is spent.
RUN_LEFT = (
    "the {kind} has not ended though it was told to stop, and the service ends its process now "
    "with exit code {code}: {stop}; not done: the body's close, the run's end record"
)
CALL_LEFT = (
    "a call the {kind} handed to a thread of its own has not ended, and would hold the process "
    "for good: the service ends its process now with exit code {code}"
)


class _StopsHeard:
    """The stops a run's body answered, read off the run's own record as it is written (the
    `intent` rows its transport writes once a stop has come back), for the line a close says of
    a run it leaves; every row also goes on to the run's own view, a goal's terminal."""

    def __init__(self, view: Callable[[Any], None] | None) -> None:
        self._view = view
        self.landed = False
        self.failed: str | None = None

    def __call__(self, event: Any) -> None:
        if event.kind == "intent" and event.data.get("intent") == "stop":
            if event.data.get("accepted"):
                self.landed = True
            else:
                self.failed = str(event.data.get("reason") or "refused")
        if self._view is not None:
            self._view(event)


@dataclass
class SelfCheck:
    """Whether this robot's own passport lets it talk, and if not, why."""

    ok: bool
    why: str
    reading: document.Reading | None = None
    clock_behind: bool = False
    """It failed because this robot's clock is earlier than the passport says, which signing
    again would only write down."""


def check_own_passport(token: str | None, robot_did: str, *, now: int | None = None) -> SelfCheck:
    """The robot's own passport, read the way another robot will read it in a hello."""
    if not token:
        return SelfCheck(False, "no signed passport yet: run `roboglot onboard sign`")
    try:
        reading = document.read_jws(token, now=now)
    except PassportError as e:
        if e.rule == "4.5" and "ahead of this clock" in str(e):
            return SelfCheck(
                False,
                f"{e}; this robot's clock is behind: set the time (timedatectl)",
                clock_behind=True,
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
            f"{facts.kind}: sign it here with roboglot onboard sign",
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
                f"another roboglot onboard serve is running for {path.parent}: roboglot onboard "
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
        clock: Callable[[], float] = time.monotonic,
        end_process: Callable[[int], None] | None = None,
    ) -> None:
        self.home = home
        self.facts = facts
        #: what the close's budget is counted on, from the stop as systemd counts its timeout
        self._clock = clock
        #: how the process ends when a close leaves a run, or a call it left, still going: a
        #: flush and `os._exit` (`end_process`), or a test's stand-in
        self._end_process = end_process or _end_process
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
        #: talk on and off, and the reads of robot.yaml and passport.jws that turn it on, decided
        #: one at a time whoever asks: the main thread's tick and a reload on a control thread can
        #: both find talk off, and talk starts once. Held to decide and to note, never to wait.
        self._talk = threading.RLock()
        #: talk is being turned on or off, its radio starting above all: the looks and reloads
        #: leave it alone meanwhile, and nobody waits for it
        self._turning = False
        #: the service has begun to close, and talk is not turned on again
        self._shutting = False
        #: why talk could not start the last time a look or a reload tried, for a reload's answer
        self._talk_problem: str | None = None
        #: told whenever talk's making ends, noted or failed, for a reload's answer to wait on
        self._made = threading.Condition(self._talk)
        self._refusals: deque[float] = deque()
        self._refusals_dropped = 0
        self.file: RobotFile = config.load(home.robot_yaml)
        self.robot_key = read_robot_key(home.robot_key)
        self.token = (
            home.passport_jws.read_text(encoding="utf-8", errors="replace").strip()
            if home.passport_jws.exists()
            else None
        )
        self.check = check_board(check_own_passport(self.token, self.robot_key.did), facts)
        self._stop = threading.Event()
        #: when the stop was asked for, which systemd's stop timeout runs from, and so the close
        #: budget too; None for a close that no stop came before (Ctrl-C in a terminal)
        self._stop_asked: float | None = None
        #: why the stop could not tell the run itself, for the close to say
        self._stop_untold: str | None = None
        #: the stops the run in hand's body answered, by the run (`_make_loop`)
        self._stops_heard: dict[int, _StopsHeard] = {}
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
        # as it always has: the service's start waits for its radio, and nothing else runs yet
        self._start_talk(wait_for_radio=True)
        from roboglot.onboard.control import ControlServer

        self.control = ControlServer(
            self._control,
            hello={"robot": self.file.passport.name, "version": __version__},
        ).start(self.home.control_json)
        self._write_status()
        return self

    def run(self) -> None:
        """Tick until stopped: what the radio says is due, the run slot, the status file."""
        with contextlib.suppress(ValueError):  # not the main thread: the caller handles signals
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
        while not self._stop.wait(TICK_S):
            self.tick()

    def _start_talk(self, *, wait_for_radio: bool = False) -> None:
        """Talk on, for a passport that checks out: the Talker, the passport server and the
        radio. At start, and again whenever a check that failed passes, a passport signed or a
        clock set: once, whichever thread asks first.

        Talk's lock is held to decide and to note what was made, never while it is made: the
        radio's start can take `START_S`, BlueZ slow at boot, and the looks, a reload and the
        way from a stop to `close` must not wait on it. So it runs on a thread of its own,
        unless `wait_for_radio`, which the service's own start asks for."""
        with self._talk:
            reading, token, kept = self.check.reading, self.token, self.radio_side
            if (
                self._turning
                or self._shutting
                or self.talker is not None
                or not self.check.ok
                or reading is None
                or token is None
            ):
                return
            factory = self._make_side if kept is None else None
            if kept is None and factory is None:
                return  # a radio handed over once, and closed since, cannot be had again
            self._turning = True
        try:
            # one of the two is there: checked under the lock just now
            side = kept if kept is not None else cast("Callable[[], RadioSide]", factory)()
            if side is not kept:
                with self._talk:  # kept, started or not, as a side once made always was
                    self.radio_side = side
            side.on_event = self._meet_event
            side.busy_elsewhere = self._in_a_meeting
            talker = Talker(
                self.file.passport.name,
                self.robot_key,
                token,
                judge=self._judge_for(reading),
                send=side.send,
                runtime={"software": f"roboglot/{__version__}"},
                heard=side.heard,
                on_heard=self._heard,
                on_session=side.on_session,
                on_refused=self._refused_hello,
            )
            served = Served(self.file.passport.name, reading.raw, token)
            server = PassportServer(
                served,
                host=side.lan_host,
                port=self.file.lan.port,
                inbox=talker.inbox_http,
                inbox_limit=MAX_HELLO_BYTES,
            ).start()
            talker.endpoint.runtime["reach"] = {"http_port": server.port}
        except BaseException:
            with self._talk:
                self._turning = False
                self._made.notify_all()
            raise
        with self._talk:
            closing = self._shutting
            if not closing:
                if self.token != token and self.token is not None and self.check.reading:
                    # signed again while talk was made: what is served is what was signed last
                    newest = self.check.reading
                    server.update(Served(self.file.passport.name, newest.raw, self.token))
                    talker.endpoint.passport = self.token
                    talker._judge = self._judge_for(newest)
                self.talker, self.server = talker, server
                # talk is on from here: its radio's start is apart from the turning, so one that
                # never comes back keeps no look from turning talk off, which cuts it short
                self._turning = False
            self._made.notify_all()
        if closing:
            # the service began to close while talk was made, and has closed what it found: what
            # was made here goes now, the radio never started
            server.close()
            with contextlib.suppress(Exception):  # as close() closes a radio: nothing to say
                side.close()
            with self._talk:
                self._turning = False
                self._made.notify_all()
            return
        if wait_for_radio:
            self._start_radio(side, talker, reading, server.port)
            return
        try:
            threading.Thread(
                target=self._start_radio,
                args=(side, talker, reading, server.port),
                kwargs={"on_its_own": True},
                name="onboard-radio-start",
                daemon=True,
            ).start()
        except RuntimeError as e:  # no thread to be had: talk is on without its radio, as said
            self.log(f"radio {side.radio.kind}: it did not start: {e}")

    def _start_radio(
        self,
        side: RadioSide,
        talker: Talker,
        reading: document.Reading,
        port: int,
        *,
        on_its_own: bool = False,
    ) -> None:
        """The radio's start, the last part of talk's: one that does not answer in time, or that
        the service's close cuts short, is said by the radio side, and talk stays on without it."""
        try:
            side.start(talker, short_id=reading.short_id, digest=reading.digest, http_port=port)
        except Exception as e:
            if not on_its_own:
                raise
            self.log(f"radio {side.radio.kind}: it did not start: {e}")

    def _try_talk(self) -> None:
        """`_start_talk` from a look rather than from start: a start that fails, a port taken
        say, is said, and tried again on the next look, and never ends the service."""
        try:
            self._start_talk()
        except Exception as e:
            self._talk_problem = f"talk could not start: {e}"
            self.log(self._talk_problem)
        else:
            self._talk_problem = None

    def _stop_talk(self, why: str) -> None:
        """Talk off, until the passport checks out again: a bye to every robot met, and the
        radio and the passport server down; the lock is held to take them, not to close them."""
        with self._talk:
            if self._turning or self.talker is None:
                return
            talker, self.talker = self.talker, None
            side, self.radio_side = self.radio_side, None
            server, self.server = self.server, None
            self._turning = True
        try:
            for peer in list(talker.peers_open()):
                with contextlib.suppress(Exception):
                    talker.leave(peer)
            if side is not None:
                with contextlib.suppress(Exception):
                    side.close()
            if server is not None:
                server.close()
            self.log(f"talk is off: {why}")
        finally:
            with self._talk:
                self._turning = False

    def _recheck(self) -> None:
        """Its own passport against the clock again: one that ran out turns talk off, and a
        clock set since, or a passport signed since, turns it back on. While talk is being
        turned on or off, its radio starting say, it is looked at again next time."""
        with self._talk:
            if self._turning:
                return
            was = self.check.ok
            self.check = check_board(check_own_passport(self.token, self.robot_key.did), self.facts)
            start = self.check.ok and self.talker is None
            stop = not self.check.ok and self.talker is not None
            if start and not was:
                self.log(f"onboard {self.file.passport.name}: {self.check.why}")
        if start:
            self._try_talk()
        elif stop:
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
        """The stop SIGTERM and the `shutdown` op ask for: when it came is noted, the run in hand
        is told at once, and the ticks end at the next look."""
        if self._stop_asked is None:
            self._stop_asked = self._clock()
            if self.slot is not None:
                # on a thread of its own: a signal handler takes no lock and writes no journal,
                # and the look in hand, which `run()` finishes before `close()`, saying bye to
                # robots out of range say, must not hold the run's stop
                try:
                    threading.Thread(
                        target=self.slot.close, args=(STOPPING,), name="onboard-stop", daemon=True
                    ).start()
                except RuntimeError as e:  # no thread to be had: the close tells the run
                    self._stop_untold = str(e)
        self._stop.set()

    def close(self) -> None:
        """Down in the reverse order, `CLOSE_S` in all, so systemd never kills it partway: the
        run first, its stop, the body's close and the end of its record; then a bye to every
        robot met and the radio, beside the passport server and the listener. What has not
        finished in that time is said in the journal, and the process goes without it."""
        # from the stop, as systemd's timeout runs: the look in hand when it came is over first
        asked = self._stop_asked
        deadline = (asked if asked is not None else self._clock()) + CLOSE_S
        with self._talk:
            # talk is not turned on again: a start under way notes what it made, sees this and
            # closes it, and a radio still starting is cut short when the radio closes below
            self._shutting = True
        if self._stop_untold is not None:
            self.log(f"the run in hand was told to stop only now: {self._stop_untold}")
        if self.slot is not None:
            # closed first, if the stop has not closed it already: a goal or a meeting asked for
            # from here on, over the control port, by a robot coming near or by an op a control
            # thread read already, is refused
            self.slot.close(STOPPING)
        # then, beside the run's stop, the LAN record other robots find this one by: a robot
        # that meets nobody more is not to be found, and the record needs nothing of the radio
        side = self.radio_side
        record = self._closing("the LAN record", side.withdraw) if side is not None else None
        # the run has all that is left of the budget but the byes' share, which is kept only
        # when there is a robot to say bye to (`AFTER_RUN_S`)
        talker = self.talker
        kept = AFTER_RUN_S if talker is not None and talker.peers_open() else 0.0
        running = self.slot.running if self.slot is not None else None
        if running is not None and running.thread is not None:
            running.thread.join(timeout=max(0.0, deadline - kept - self._clock()))

        def left_s() -> float:
            return max(0.0, deadline - self._clock())

        # a bye to every robot met, all at once, each bounded by the way it goes, and beside
        # them the parts that carry none: the passport server and the listener
        talker = self.talker
        peers = list(talker.peers_open()) if talker is not None else []
        byes = [self._closing("a bye", self._bye, talker, peer) for peer in peers]
        rest = [
            (f"closing {name}", self._closing(name, part))
            for name, part in (
                ("the passport server", self.server.close if self.server is not None else None),
                ("the control listener", self.control.close if self.control is not None else None),
            )
            if part is not None
        ]
        unfinished: list[str] = []
        if record is not None:
            record.join(left_s())
            if record.is_alive():
                unfinished.append("withdrawing the LAN record")
        for bye in byes:
            bye.join(left_s())
        still = sum(bye.is_alive() for bye in byes)
        if still:
            unfinished.append(f"saying bye to {still} robot{'' if still == 1 else 's'} met")
        # the radio once the byes it carries are done, if there is time left for it
        if self.radio_side is not None:
            radio = self._closing("the radio", self.radio_side.close) if left_s() > 0 else None
            if radio is not None:
                radio.join(left_s())
            if radio is None or radio.is_alive():
                unfinished.append("closing the radio")
        for name, thread in rest:
            thread.join(left_s())
            if thread.is_alive():
                unfinished.append(name)
        if unfinished:
            self.log(f"the service stops before it finished {' and '.join(unfinished)}")
        with contextlib.suppress(OSError):
            self.home.control_json.unlink()
        # last, with what is left of the budget: a run still going, or a call one handed to a
        # thread of its own, would hold the process at its exit for good, so it is ended here
        calls = [t for t in threading.enumerate() if t.name.startswith(CALL_THREADS)]
        for call in calls:
            call.join(left_s())
        calls = [t for t in calls if t.is_alive()]
        if running is not None and running.thread is not None and running.thread.is_alive():
            said = RUN_LEFT.format(
                kind=running.kind, code=LEFT_EXIT, stop=self._stop_said(running.agent)
            )
        elif calls:
            kind = calls[0].name.removeprefix(CALL_THREADS).split("_")[0]
            said = CALL_LEFT.format(kind=kind, code=LEFT_EXIT)
        else:
            return
        self.log(said)
        with contextlib.suppress(Exception):  # a stderr that has gone: the journal has the line
            sys.stderr.write(said + "\n")
            sys.stderr.flush()
        self._end_process(LEFT_EXIT)

    def _stop_said(self, agent: Any) -> str:
        """What the run's own record says the body was sent as it stopped, in words."""
        heard = self._stops_heard.get(id(agent))
        if heard is None:
            return "its record says what stop the body was sent"
        if heard.landed:
            return "the body took the stop it was sent"
        if heard.failed is not None:
            return f"the stop it was sent did not land: {heard.failed}"
        return "no stop it was sent has come back"

    @staticmethod
    def _bye(talker: Talker, peer: str) -> None:
        with contextlib.suppress(Exception):  # a bye lost is fine: the session ends either way
            talker.leave(peer)

    def _closing(self, name: str, part: Callable[..., None], *args: Any) -> threading.Thread:
        """`part` on a thread of its own, started, so the close can stop waiting for it."""

        def go() -> None:
            try:
                part(*args)
            except Exception as e:  # said, and the rest of the close goes on
                self.log(f"stopping: {name} did not close: {e}")

        thread = threading.Thread(target=go, name="onboard-close", daemon=True)
        thread.start()
        return thread

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
            # what `roboglot onboard pin` needs: a short id to name it by, and its owner's key
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
                from roboglot.onboard.control import write_private

                kept = path.read_text(encoding="utf-8").splitlines()[-MET_KEPT * 5 :]
                write_private(path, "\n".join(kept) + "\n")

    # ── runs ────────────────────────────────────────────────────────────────────────────

    def _body(self) -> Any:
        if self.file.robot is None:
            from roboglot.onboard.body import DescribedAdapter

            return DescribedAdapter(self.file)
        from roboglot.adapters.factory import make_adapter, parse_robot_spec

        spec = parse_robot_spec(self.file.robot.adapter)
        return make_adapter(spec, address=self.file.robot.address)

    def _safe_verbs(self) -> list[str]:
        if self.file.robot is None:
            from roboglot.onboard.body import VERBS

            return list(VERBS)
        from roboglot.adapters.factory import describe, parse_robot_spec

        manifest = describe(parse_robot_spec(self.file.robot.adapter))
        return [v.name for v in manifest.verbs if v.safety_class == "safe"]

    def _holding(self, agent: Any) -> None:
        """A run holds the body: the robots met may ask it for a courtesy, which goes through
        this run's executor on this run's loop, and only while it runs."""
        import asyncio

        from roboglot.passport_courtesy import Courtesy

        if self.talker is None:
            return
        if self._courtesy is None:
            # one for the robot's whole life: its budget is every peer's together, and a new
            # run must not hand a peer a fresh one
            self._courtesy = Courtesy(agent.executor, asyncio.get_running_loop())
        self._courtesy.executor = agent.executor
        self._courtesy.loop = asyncio.get_running_loop()
        self._courtesy.stranger_pauses = self.file.trust.strangers_may_pause
        if self.check.reading is not None:
            self._courtesy.offered = tuple(self.check.reading.courtesy())
        self.talker.courtesy = self._courtesy

    def _released(self) -> None:
        if self.talker is not None:
            self.talker.courtesy = None

    def _make_loop(self, glot: Any, kind: str, front: Any) -> Any:
        from roboglot.agent.loop import AgentLoop, RunConfig

        prune(self.home.runs, keep=RUNS_KEPT - 1)
        from roboglot.agent.providers.factory import make_provider, resolve_llm
        from roboglot.memory import RobotMemory
        from roboglot.safety import deny_all

        heard = _StopsHeard(front.view if front is not None else None)
        brain = self.file.brain
        vendor, llm_id, _source = resolve_llm(None, brain.llm, robot=self.file.passport.name)
        provider = make_provider(
            vendor,
            llm_id=llm_id,
            source="robot.yaml brain.llm",
            task_name=glot.name,
            goal=glot.body if kind == "goal" else None,
            base_url=brain.base_url,
        )
        cfg = RunConfig(
            glot=glot,
            provider=provider,
            transport=self._body(),
            runs_dir=self.home.runs,
            confirm=front.confirm if front is not None else deny_all,
            decide=front.decide if front is not None else _meeting_go,
            acknowledge=front.acknowledge if front is not None else None,
            # the run's rows as written, to the goal's terminal as before, and the stops the body
            # answered noted on the way, for the line a close says of a run it leaves
            view=heard,
            # a goal's notes are the robot's; a meeting keeps none, since what it would note
            # is what a robot it met claimed, and a note comes back to a later run as the
            # robot's own word
            memory=RobotMemory(self.file.passport.name, self.home.memory)
            if kind == "goal"
            else None,
            price=brain.price,
            nearby=self.talker,
        )
        agent = AgentLoop(cfg)
        self._stops_heard = {id(agent): heard}  # one run in the slot at a time
        return agent

    # ── control ─────────────────────────────────────────────────────────────────────────

    def _control(self, op: Mapping[str, Any], conn: Any) -> dict[str, Any] | None:
        name = op.get("op")
        if name == "status":
            return {"ev": "status", **self.status()}
        if name == "stop":
            if self.slot is None:
                return {"ev": "stopped", "was": False}
            stopped, ended = self.slot.stop_and_wait(STOP_WAITS_S)
            # `ended` is new: a client from before reads `was` alone, as it always has
            return {"ev": "stopped", "was": stopped, **({"ended": ended} if stopped else {})}
        if name == "met":
            return {"ev": "met", "rows": self._met_rows(int(op.get("limit") or 20))}
        if name == "reload":
            self._seen = self._stamps()
            return {"ev": "reloaded", **self._reload(answering=True)}
        if name == "shutdown":
            self.stop()
            return {"ev": "bye"}
        if name == "goal":
            return self._goal(op, conn)
        return {"ev": "refused", "reason": f"roboglot onboard takes no {name!r}"}

    def _goal(self, op: Mapping[str, Any], conn: Any) -> dict[str, Any] | None:
        from roboglot.front import Channel, Front

        text = str(op.get("text") or "").strip()
        if not text:
            return {"ev": "refused", "reason": "a goal is a sentence"}
        if self.slot is None:
            return {"ev": "refused", "reason": "the service is still starting"}
        channel = Channel(conn.writer)
        front = Front(channel, "onboard goal", quit_now=lambda: self._let_go(text, channel, conn))
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

    def _let_go(self, text: str, channel: Any, conn: Any) -> None:
        """A goal's terminal asking its run to quit at once, its second Ctrl-C. The service
        never quits for one terminal, and the run has had its stop already, so what quits at
        once is the terminal: told the run is still ending, and let go, while the run ends."""
        self.log(LET_GO.format(text=text))
        channel.close({"ev": "notice", "text": STILL_ENDING})
        # the terminal may be gone already, and then there is nothing left to let go
        with contextlib.suppress(OSError):
            conn.sock.shutdown(socket.SHUT_RDWR)

    def _reload(self, *, answering: bool = False) -> dict[str, Any]:
        """robot.yaml again, for the settings that change without a signature: the pins above
        all, which a session judged before keeps until it ends. `answering`, for a client over
        the control port, waits a bounded while for talk being made on another thread, so the
        answer is true for the passport it was asked about; the tick's own look never waits."""
        start = False
        with self._talk:
            try:
                self.file = config.load(self.home.robot_yaml)
            except config.FileError as e:
                self.file_problem = f"{e}; the service keeps the file it read last"
                return {"ok": False, "reason": str(e)}
            self.file_problem = None
            refused: str | None = None
            # a passport signed again since start: served from now on, and in every new hello
            if self.home.passport_jws.exists():
                token = self.home.passport_jws.read_text(encoding="utf-8", errors="replace").strip()
                check = check_board(check_own_passport(token, self.robot_key.did), self.facts)
                if not check.ok:
                    refused = check.why
                if check.ok and check.reading is not None and token != self.token:
                    self.token, self.check = token, check
                    if self.server is not None:
                        served = Served(self.file.passport.name, check.reading.raw, token)
                        self.server.update(served)
                    if self.talker is not None:
                        self.talker.endpoint.passport = token
                    else:
                        start = True  # signed while talk was off: on, at once, below
            if self.slot is not None:
                self.slot.file = self.file
            if self.talker is not None and self.check.reading is not None:
                self.talker._judge = self._judge_for(self.check.reading)
                self.talker.rejudge()
            reply: dict[str, Any] = {"ok": True, "pins": len(self.file.trust.pins)}
            if refused is not None:
                reply["passport_refused"] = refused
        if start:
            self._try_talk()
        with self._talk:
            if answering:
                # talk being made on another thread notes its parts with the newest passport, or
                # fails: the answer waits for which, and says the truth either way
                self._made.wait_for(
                    lambda: not (self._turning and self.talker is None), TALK_WAITS_S
                )
            # a passport that checks out and is not served says why, so `roboglot onboard sign`
            # never tells a person it is served with no passport server running
            if self.check.ok and self.server is None:
                reply["unserved"] = (
                    "the service is stopping"
                    if self._shutting
                    else "talk is still being made"
                    if self._turning
                    else self._talk_problem or "talk is off"
                )
        return reply

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
        from roboglot.onboard.control import write_private

        with contextlib.suppress(OSError):
            write_private(self.home.status_json, json.dumps(self.status(), default=str) + "\n")


def _end_process(code: int) -> None:
    """The process ends here, with `code`, its output flushed first: what systemd's kill at the
    unit's stop timeout would do a moment later, done on every path, a terminal's too."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):  # a stream already gone has nothing left to flush
            stream.flush()
    os._exit(code)


def _browser(log: Callable[[str], None]) -> Any:
    """The DNS-SD browse that finds other robots' LAN records by beacon id, or None without
    zeroconf, when robots near are met over a link."""
    try:
        from roboglot.lan.passports import PassportBrowser

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
    """`roboglot onboard serve`: this robot's service, until SIGTERM, Ctrl-C or a shutdown over
    the control connection. A board that is not a robot's raises from `roboglot.boards.detect`,
    and a directory another service holds raises `AlreadyRunning`."""
    from roboglot.boards import detect
    from roboglot.radio.sim import default_air_dir

    chosen, facts = detect(home.board_named(board))
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
        kind = radio or chosen.radio_kind
        return board_side(
            radio_kind=kind,
            instance=file.passport.name,
            browser=_browser(log),
            policy=policy,
            rotate_s=rotate_s,
            log=log,
            advertise=file.radio.advertise,
            find_adapter=(lambda: radio_of(chosen).adapter_to_open) if kind == "bluez" else None,
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
