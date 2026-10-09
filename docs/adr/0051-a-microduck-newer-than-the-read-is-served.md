# ADR-0051: A Microduck newer than quackd's read is served, and quackd says so

**Status:** accepted · **Date:** 2026-10-08 · Amends [ADR-0006](0006-upstream-api-single-file.md) (a VERIFIED ref is what quackd read at one pin, and a robot on another `duck-ipc-proto` version is served on the strength of that read and of the checks below, where the handshake used to refuse it) · Implemented in `adapters/microduck/src/quackd_microduck/transports/jsonrpc_unix.py`, with the refs in `upstream_api.py`, a row in the adapter's health and an advisory in `quackd/doctor.py` · Documented in [docs/adapters/microduck/README.md](../adapters/microduck/README.md#upstream-api) and step 2 of the [hardware checklist](../adapters/microduck/hardware-checklist.md#2-what-the-robot-says-it-is)

## Context

`microduck:jsonrpc` says hello to robotd with the API version quackd read, and refused a robotd
that answered with any other number. quackd read robotd at `bc41fb5` on 2026-09-04, on API 23.
Six days later `daemon-v0.12.0` shipped on API 27, and every release from it on was refused at
connect: thirteen releases by `daemon-v0.16.1` on 2026-10-07, on API 41, and nothing quackd
sends reads differently at the newest of them. A robot that updates is a closed socket until
quackd catches up, and robotd ships every few days.

The refusal was quackd's own. Upstream's rule, above `API_VERSION` in `duck-ipc-proto`, is that
"A bump promises nothing in either direction" and that "No daemon refuses a call because this
number differs". robotd answers `hello` without comparing, and upstream's own `robotctl` warns
once and carries on, because "a call that cannot be served will name itself". What refuses
instead is the call: a method a release does not have is `METHOD_NOT_FOUND` naming it, and a
params member it does not know is `INVALID_PARAMS` naming the member, since every params type
denies unknown fields. The doc comment above that rule still says a peer on a different version
"is refused rather than misparsed", which the rule below it and robotd's own `hello` contradict.

The number is also a poor guide in both directions. `bc41fb5` and `daemon-v0.11.0` both say 23,
and the release has `robot.rebootMotors`, which the commit does not. `daemon-v0.15.0` to
`daemon-v0.15.2` all say 37, and only the last reports `safety.picked_up`.

And upstream's mechanism stops at a request. `robot.move`, `robot.head` and `robot.pose` are
notifications, which robotd answers with nothing, so a twist robotd could not parse is dropped
without a word, and quackd recorded the walk as taken.

## Decision

- **A floor, not a match.** A robotd below API 23, the oldest `duck-ipc-proto` quackd has read
  end to end (`up.API_FLOOR`), is refused at connect, naming both numbers. So is a `hello` that
  carries no integer `api_version`, which is not a robotd quackd can check against anything:
  the old gate let one with no version through, since it compared only a version that was
  there, and took one written as a string for a number. Any other version is served.
- **Served, and said.** On any version but the one read, 41, older as well as newer, quackd
  logs a warning at connect, `doctor` prints it among its advice, and every state carries it in
  `extras.robotd`, beside the robot's version, release and revision, the version read and the
  floor. `doctor`'s health block gains a `robotd` row naming the release, the version and the
  revision whatever they are.
- **A refusal by name says both versions.** A call robotd refuses with `METHOD_NOT_FOUND` or
  `INVALID_PARAMS` fails the verb that sent it in robotd's own words, with the call and both
  versions: the refusal upstream put where the version gate was, and the version is half of
  what it means. A `hello` refused as `METHOD_NOT_FOUND` is a socket that is not robotd's, tofd
  for one, and says so, and so is a `robot.subscribe` refused that way: updaterd shares
  robotd's protocol and answers `hello` too (`up.OTHER_DAEMONS_ANSWER_HELLO`).
- **A notification is checked against robotd's echo.** Every state frame reports the twist
  robotd was last asked for, as it was asked (`robot.state.move.requested`, `up.MOVE_ECHO`). A
  twist quackd sent that the state has not shown three frames later (0.3 s at the 10 Hz quackd
  subscribes at) holds `robot.move` back until the verb that sent it stops, which is the hold a
  duck that stops standing already gets. The verb fails saying what the state showed instead,
  and `extras.move_echo` keeps the last verdict, and on a walk robotd took, how long after it
  went out the twist showed.
- **What the version cannot say is read off the robot.** `extras.pickup_detection` says
  whether the robot's frames carry `safety.picked_up` at all, and `doctor` warns when they do
  not, beside the advice it gives for a robot that detects no falls: nothing then tells quackd
  that somebody is holding the duck. `extras.robotd` records whether the state carries `frames`,
  which the floor's releases do not, and whether mediad's `GET /frame` answered, when
  `--camera-url` asked it.
- **It stands on posture failing closed.** Serving a robot quackd has not read is safe to do
  only because every policy label quackd has not read already reads as `unknown`, every verb
  that walks refuses on that, and `robot.move` is held back once the duck stops standing. A
  newer robot's new label refuses a walk rather than allowing one.
- **Re-read when quackd needs something, not on every bump.** A new pin is for a call or a
  field quackd wants, or for a robot that refuses one by name. The warning is how a person
  learns their robot has moved past the read.

## Why not

- **Exact equality, as it was.** It refused thirteen releases in a month, the newest of which
  quackd drives unchanged, and would refuse the next one within days of any re-read.
- **A ceiling at the version read.** Robots update forward, so it refuses the same robots the
  exact match did, and keeps refusing until quackd catches up.
- **Asking robotd for every method at connect.** It would cost a call per method and still
  prove nothing about a notification, and robotd already names the call that fails, at the
  moment it fails, which is when a person needs the name.
- **Trusting notifications.** A `robot.move` robotd could not parse is a walk quackd reported
  and the duck never took. The echo costs nothing on the wire: the frames already arrive.
- **Judging the echo on the first frame after a twist.** robotd reports a twist in the next
  frame it publishes, but that frame still has to come back down the ssh tunnel, and a window
  of one frame would hold back walks robotd took. Three leave two for the round trip.
- **Judging the echo on time alone.** A frame robotd built before the twist reached it can be
  read well after the twist went out, when quackd's own process stalls with the frame still in
  its socket, and on time alone it reads as a twist robotd did not take. The verdict waits for
  three frames read since the twist went out as well as for the time, so a stall delays it
  rather than inventing one.

## Consequences

- A robot quackd has not read is driven on the shapes read at the pin. A change of meaning that
  keeps its shape, units or a sign, passes every check here: robotd refuses nothing and echoes
  what it was sent. `daemon-v0.16.1`'s idle look-around is the kind of change that gets
  through, behaviour that moved without a shape moving, and it was found by reading upstream,
  not by a check. The warning is what tells a person to read. A result whose shape moved is
  refused by nothing upstream either: an `IntentResult` with no `accepted` quackd can read is
  taken for a refusal that names what came back, never for a yes.
- The echo judges a twist only once three frames have been read since it went out and three
  frame periods have passed, so a walk shorter than that is reported on what it sent, as every
  walk was. A link whose round trip takes longer than two frame periods holds back a walk
  robotd did take, which stops the duck and says why rather than walking it blind. Nobody has
  measured the round trip on a real tunnel, and `extras.move_echo` records how long each twist
  a frame showed took to show.
- `robot.head` and `robot.pose` have no echo. The one `robot.head` quackd sends is the gaze
  hold's, the head a `robot.look` answered with, sent again every second, and the hold lets go
  once `robot.state.head` moves off it, which is also what a robotd that does not take it shows
  when it glances around again. Nothing sends a pose.
- robotd keeps the newest twist from any client, and a connected gamepad sends one at least ten
  times a second, a stop while nobody touches its sticks (`up.PAD_DRIVES`). With a pad
  connected every walk quackd sends is fought by it, as it was at the previous read too, and the
  frames show the pad's stop as well as quackd's twist, so the echo holds a walk back whenever
  three frames in a row show only the pad's. That is the pad's authority, which quackd does not
  arbitrate, made visible: the walk stops and says what robotd showed instead. The hardware
  checklist asks for that same pad in hand as the e-stop, so the first run on a robot is where it
  shows how often.
- The reader takes a line of up to a mebibyte from robotd, sixteen times robotd's own limit for
  a line it reads, and skips a longer one rather than ending the link, because nothing bounds
  what robotd writes and a newer robot's state can only grow. No other line ends the link
  either, one with an `id` quackd did not send or an `error` that is not an object among them.
- **Nobody has run quackd against a Microduck.** Everything here was tested against
  `tests/test_jsonrpc.py`'s fake robotd, which answers as the source says robotd does: on other
  versions, without calls, dropping `robot.move`, and with no pick-up detector.
