# ADR-0071: A drive session's goals fly as a run does, and ask the person driving

**Status:** accepted · **Date:** 2026-10-27 · Amends [ADR-0065](0065-a-person-drives-one-robot-a-command-at-a-time.md) (a goal's questions reach the person driving, and drive takes run's flags for its goals) and [ADR-0069](0069-a-page-drives-quackds-own-commands-over-json-lines.md) (a chat in the page takes a VLA pilot) · Implemented in `quackd/answers.py`, `quackd/safety.py` (`ConfirmFn`), `quackd/agent/loop.py` (`RunConfig.places`), `quackd/drive/session.py` (`GoalSettings`, `GoalAsker`, `_goal_answerers`, `_vla_refusal`, `_charge`), `quackd/drive/run.py` (`DriveArgs`, `SESSION_POLICY_S`, `_given_lens`), `quackd/drive/terminal.py` (`Asker`, `PageAsker`), `quackd/drive/attached.py` (`lens_given`), `quackd/cli.py` (`_drive_refusal`, `_vla_refusal`), `quackd/mcp_server.py` (`fleet_from_flags`) and `quackd/agent/decision/laya.py` (`_predict`) · Documented in [docs/guides/drive.md](../guides/drive.md)

## Context

`quackd drive` flies a goal as a whole run of its own on the body the session holds (ADR-0065),
with less than `quackd run` gives one. Its pilot took `--llm` alone: no address for a local
server, no key, no vision switch, no extra body, no effort and no price. A goal kept no memory,
could not be given a part to play, and took the task default of steps with no way to change it. A
policy server could not be named, so a session's arm had no policy to hand `manipulate` to, and
there was no `--controller vla`. The page's chat is `quackd drive`, and inherited every gap, and a
chat was to get everything a goal gets.

And a goal asked nobody anything. A run puts four questions: the confirm gate's before a gated
verb, the pilot's doubt about the task, whether the arm did it, and whether anybody is watching a
body that cannot see a fall. All four were put synchronously, the executor and the loop calling
the answerer and taking what came back. A session cannot block the loop that reads the keyboard
on a question, so a goal's gated verb was answered by the standing rule, a no without `--yes`,
with the person who typed the goal at the keyboard, and the pilot's doubt was left to the pilot.

## Decision

**One way to wait for an answer.** `quackd.answers.answered` takes an answerer that replies at
once, as a terminal's prompt and every standing answer do, or later, from a coroutine, and races a
later one against the run's abort. A stop takes the question back, which raises
`QuestionWithdrawn`, recorded as a question nobody answered and never as a no, and a question put
once the run was stopped is never shown. Every question a run puts goes through it, the confirm
gate, `decide`, `judge` from both its callers and `acknowledge`, because a coroutine is truthy: a
site that took what an answerer returned for the answer would read every later no as a yes nobody
gave. A test reads `loop.py` and `safety.py` for a site that calls an answerer directly.

**A goal's questions reach the person who typed it.** At the keyboard or in the page, a goal's
confirm gate, the pilot's doubt and, under a VLA pilot, whether the arm did it are put to the
session's own asker, in the words a run puts them in, and recorded as a person's, with the page's
channel where it was the page. The goal waits for the answer while the session goes on reading
lines. The keyboard shows one question at a time, and a `y` answers the one on the screen. A
goal's question is named after its goal and waits behind any question showing, and a question
about a line the person has just typed is shown at once, since it is the one they are about to
answer: shown oldest first, a gated command typed over a goal's question was queued unseen, and
the `y` meant for it answered the goal's. A brake, or a newer line that moves the body, ends the
goal and takes its question back. `--yes` answers as it answers a run, yes to the gate and go to
the doubt, and a pipe's goal keeps the standing rule, since nobody reads a pipe's questions.
Whether anybody is watching stays the session's question, put before the first thing that moves
the body.

**drive takes run's flags for its goals, given once for the session.** `--base-url`,
`--api-key`, `--vision`, `--extra-body`, `--effort` and `--price` reach the pilot's model.
`--max-steps` is each goal's, within what the session has left. `--play`, `--memory` and
`--memory-dir` and `--fov-deg` reach every goal, and `--policy-url`, `--policy-token`,
`--accept-other-frame` and `--async-chunks` the arm. `--controller`, `--decision-mode` and
`--decision-frames` say who flies a goal and what a goal's stepper may do. Each is refused in
run's words where run refuses it, before the robot connects.

**Memory stays off in drive.** `quackd run` keeps it on, and a session was never handed one, so a
session started as before keeps none, and a goal recalls the robot's memory and adds to it only
under `--memory`. The places a person marks were kept already, and each goal now reads them, so a
place marked between two goals is there for the second. What a goal marks or forgets is kept only
under `--memory`, as a run without a memory keeps its own places as long as it lasts.

**`--decision-llm` stays the fast lane.** A goal gets a stepper only with `--decision-mode` on
the command line, and is shown the camera only with `--decision-frames`. `QUACKD_DECISION_MODE`
and `QUACKD_DECISION_FRAMES` are not read, so a `.env` written for `quackd run` changes no
session. A goal's stepper asks the fast lane's own client. The one that runs in this process,
laya, runs one forward pass at a time, since the fast lane and a goal's stepper may ask it from
two threads at once.

**`--fov-deg` is the session's.** It is told to the body once, as the session connects, and each
goal measures through the same lens without telling the body again: a goal's `lens_given` would
reach the body every goal shares.

**A goal holds `manipulate` to the session's own `.duck`.** Its policy section is the goal's,
listed instructions and all, cut to the policy seconds the session has left, so a goal's pilot
tells the arm's policy nothing the `.duck` does not list, as `quackd run` holds a task to it. A
goal is built once whatever it takes over has ended and been charged, so what it may spend is
what is really left.

**A VLA pilot flies a goal a line at a time.** Each line that reaches the pilot is one instruction
to the arm's learned policy, held to the rule for one as it gets there, and to the `.duck`'s list
where it has one. A `.duck` that does not allow `manipulate` is refused before the session
starts. The person driving is asked before the segment whether the body should try, about the
segment itself, which a goal allows behind the confirm gate as `quackd run --goal` does, and after
it whether the arm did it. Only their yes makes the goal a success. A VLA pilot is refused with
`--yes`, `--dry-run`, a pipe, a model's flag, `--play`, `--effort` and a goal's stepper, as a run
refuses it, and `--decision-llm` alone is let through as the fast lane. The seconds a policy
drives the body are the session's: each goal's are charged to it, and each goal is held to what is
left. A session without a `.duck` has the schema's ceiling of them, an hour, where a task's
default is two minutes, for the reason it has a task's highest budgets.

## Why not

- **A question put on a thread that blocks, as the page's run puts one.** A run has nothing else
  to read while it waits, and a session has its brake.
- **The standing rule for the page, and a person asked only at the keyboard.** A page's yes is a
  person's (ADR-0069), and a chat that could neither clear a gated verb nor judge an arm would be a
  chat without what a goal gets.
- **Memory on in drive, as in run.** A session started as one always was would begin writing the
  robot's memory, which nobody asked for.
- **A goal's stepper from `--decision-llm` alone.** The fast lane is a decision LLM's job in a
  session. A stepper in front of every goal is a second job, asked for by name.
- **A second decision client for the goals.** The one that runs in this process would load its
  weights twice.

## Consequences

- Nothing here has driven a real robot. A VLA pilot's segments have run on the LeRobot mock's
  scripted policy alone.
- A goal waiting for an answer counts the wait against its minutes, as a run waiting at its
  terminal does, but for the question about the arm, whose seconds are the person's. The robot
  stands under the session's heartbeat meanwhile. A question in the page waits as long as the
  keyboard's does, and the page ends a chat nobody has looked at for fifteen minutes.
- A session with a policy server says so in its `run_start`, as a run does.
- A goal's question from inside a composite, a gated `kick` as `approach_and`'s `then`, waits
  inside the composite's own time and is taken back when that runs out, recorded as unanswered.
- A local pilot in a session with `--host` asks the board's model server, as a run's does, where
  it asked this computer's.
- `quackd log` replays the verbs and does not yet draw drive's own lines.
