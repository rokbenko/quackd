# ADR-0069: A page drives quackd's own commands, over JSON lines in a child process

**Status:** accepted · **Date:** 2026-10-26 · Amends [ADR-0056](0056-moss-through-its-cockpit-and-a-bridge.md) (a person may be asked at a page as well as at a terminal) and [ADR-0065](0065-a-person-drives-one-robot-a-command-at-a-time.md) (drive reads a page as well as the keyboard and a pipe) · Implemented in `quackd/front.py`, `quackd/__main__.py`, `quackd/cli.py` (`_fronted`, `_front_refusal`, `_someone_to_ask`, `_run_impl`, `_drive_command`), `quackd/drive/terminal.py` (`drive_from_jsonl`, `PageAsker`), `quackd/drive/run.py`, `quackd/drive/session.py` (`goal_view`, `Answered.channel`), `quackd/agent/loop.py` (`STOP_BUTTON`, `PERSON_STOPS`, `RunConfig.front`), `quackd/log.py` (`asked_on`) and `quackd/command.py` (`without_front`) · Documented in [docs/concepts/architecture.md](../concepts/architecture.md)

## Context

quackd is getting a page, `quackd ui`, where a person chooses a robot and a pilot, gives a goal or
chats goal after goal, and stops the robot from a browser, on a phone too. Something has to sit
between that page and a run, and the obvious thing, importing quackd into the page's process and
running the loop on a thread, goes wrong in every way that matters for a robot. The kill switch
reads the process's own terminal and starts a key thread it never stops. `_can_prompt` asks the
process's console whether a person is there. The consoles, the saved terminal and the kill switch
are process globals, so two runs could not share a process. The record would carry the page's
argv rather than the command that ran, `.env` is loaded only by the CLI's root callback, and a
page reloading in the middle of a verb would take the run with it.

Running `quackd run` as a plain child process has a gap of its own: stopping it. The kill switch
listens for SIGINT, and on Windows a parent cannot deliver one to a child. CTRL_C_EVENT reaches
only processes on the caller's own console, and CTRL_BREAK_EVENT arrives as SIGBREAK, which
quackd does not handle, so the child would die where it stood: no stop sent, no fold, no close,
no `run_end`. And `quackd drive` reading a pipe waits for each line's command to end before it
reads the next, so a brake typed into a pipe waits behind the walk it was meant to end.

## Decision

**The page drives the CLI, as a child, over JSON lines.** `quackd run` and `quackd drive` take a
hidden `--front jsonl`. The page starts `python -P -m quackd run ... --front jsonl`, so the child
is the quackd installed beside the page's own Python, never a `quackd/` folder in the working
directory. The child is the command a terminal runs: every flag, every refusal, the `.env` load
and the record are the terminal's, and a flag added later works in the page with no edit to it.
The terminal stays the default front end, and nothing it shows or records changes.

**The protocol is one JSON object per line.** stdout carries `hello` first and `end` last, and
between them each record of the run as it is written, frames, the status line's words, notices,
questions, and for drive its lines and their results. stdin carries the page's ops: `stop`,
`answer`, and for drive `line` and `quit`. Everything a terminal would show still goes to stderr,
and a write meant for stdout lands there too, so nothing breaks a line. `end` names the outcome,
including `refused` and `error`, and the exit code the process is about to leave with.

**A stop is a message.** The page's Stop sets the run's abort on its own loop, as the kill
switch's first press does, so the full teardown runs on every operating system: the verb moving
is ended, a stop is sent, the arm folds and the record closes. The run ends `aborted` for the
`stop button`, a person's stop like the kill switch, so it also ends a fold the run began by
itself (`PERSON_STOPS`). A second Stop, asked for as such, quits at once as a second Ctrl-C does.
In drive, a Stop brakes, and a second one ends the session in order.

**The page going away is a stop nobody pressed.** When the page's input ends, or its pipe
breaks, every question is taken back and the run stops with `the page went away`, which is no
person's stop, so a fold the run began by itself goes on to its pose, as it does when a heartbeat
fails. A drive session brakes and ends, its brake recorded as a pipe's. A page that dies outright
is the same thing: its pipes close with it.

**A page's yes is a person's yes.** Its answers are marked as a person's, `a person` in the
record with `channel: web` beside it, so a page can clear a dangerous verb (ADR-0056) and run
`--controller vla`. That holds only because the page is reachable by its own user alone, which
`quackd ui` sees to. Each question carries an id and an answer names the one it answers, so a
second tap answers nothing. A question taken back, by a stop, by the page going away, or by
nobody answering within five minutes, raises rather than answering no: the record says the
prompt raised and writes no `prompt` row, and never says a person said no when nobody did.

**What the page does not drive.** `--by-hand` waits for Enter from somebody at the arm, and a
flock runs every member behind a kill switch of its own, which no Stop in the page would reach,
so both are refused under the page, a task file with a `flock:` block included, before anything
is built. The end-of-run questions about an arm that missed its rest pose are not put to a page,
and a missed rest pose keeps the torque on, as it does for an MCP session.

**On Windows the page's input is never read until something is there.** A read left pending on
a pipe makes a question about that handle wait behind it, and a DLL asks one of the standard
handles as it loads, so a run that imported numpy behind a blocked read sat in the import until
the page next sent a line. The reader asks `PeekNamedPipe` how much is waiting and reads only
that.

**The record says which front a run came through.** `run_start` gains `front: jsonl` on a run
the page drove, and the command a record names leaves `--front` out: it is the command to type
at a terminal for the same run.
