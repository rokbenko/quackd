"""The body a drive session holds, lent to a goal the pilot runs on it."""

from __future__ import annotations

from typing import Any

from quackd.adapters.manifest import RobotManifest


class AttachedBody:
    """A transport for a goal: the session's connected body, with none of a run's own care of a
    body reaching it.

    A goal is a whole `AgentLoop`, and a run connects its body at the start, watches it with a
    heartbeat, folds it to its rest pose and lets go of it at the end. The session has done the
    first already and does the others once, when it closes, so a goal that did them would have
    the arm fold and go limp between two commands. Here `connect` hands back the manifest the
    session connected with and touches nothing, `close` and `heartbeat` do nothing, since the
    session keeps the link and its one heartbeat watches it, and there is no `rest_pose` and no
    rest move, let-go or take-hold to reach, so a goal never moves the body but by its verbs. Nor
    are the session's connect notes or close note told again in each goal's record. Everything
    else is the body's own, delegated as `LoggedTransport` delegates, `stop` and every intent
    above all."""

    rest_pose: Any = None
    """None, so a goal never folds the body between commands (`AgentLoop._rest`)."""
    go_to_rest: Any = None
    let_go: Any = None
    take_hold: Any = None
    """Not callable, so `go_to_rest_if_any`, `let_go_if_any` and `take_hold_if_any` find nothing
    to call: whatever path of a run's teardown reaches for them, a goal never folds the body,
    lets go of it or holds it for a person. The session does each once, as it closes."""
    supports_hand_off: bool = False
    """A goal is never a by-hand run, whatever the body supports."""
    connect_notes: tuple[str, ...] = ()
    """The session's connect said its own, once; a goal connected nothing."""
    close_note: str | None = None
    """Nor did a goal close anything, so it has nothing to say about a close."""
    set_stop_check: Any = None
    """Not callable, so a goal's connect, which connects nothing, lends the body no stop check."""
    set_segment_s: Any = None
    """Not callable, so a goal leaves the length of the arm's policy segments as the session's
    contract set it: a goal's own, its contract's default, was set on the session's arm and
    outlived the goal, under the session's `manipulate` timeout for its own length."""

    def __init__(self, inner: Any, manifest: RobotManifest) -> None:
        self._inner = inner
        self._manifest = manifest

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):  # never delegate our own privates (copy, pickle, half-init)
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def connect(self) -> RobotManifest:
        return self._manifest

    async def heartbeat(self) -> None:
        return None

    async def close(self) -> None:
        return None
