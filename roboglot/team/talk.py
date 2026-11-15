"""One pilot's end of the team bus: what it hears, what it says, what it is told about
the others.

A pilot team does not have a referee. Nobody assigns the work, nobody judges the result, and
the only thing keeping two robots from both doing the same half of a task is that they said
what they were going to do. That makes this module small and load-bearing: a `tell` tool, a
`TALK` message, and a paragraph in every pilot's prompt naming its peers and what their bodies
are (ADR-0034).

The peer paragraph is the part that is not obvious. A pilot deciding who fetches and who holds
needs the *other* robot's datasheet, not only its own, and it gets exactly the one-paragraph
form its own prompt uses for itself, so the two are read in the same words. It is the static
manifest, so building this costs no connection.

Nothing here awaits the bus, for the reason `roboglot/team/bus.py` gives: publish is a
synchronous fan-out and a reader drains between turns.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from roboglot.agent.prompts import body_summary
from roboglot.team.bus import Bus, Subscription
from roboglot.team.messages import TALK_MAX_CHARS, TalkMsg, TeamMessage

if TYPE_CHECKING:
    from roboglot.adapters.manifest import RobotManifest

TEAM_SRC = "team"
"""The runner's own name on the bus. Not a member, so no pilot can be told to address it."""

EVERYONE = "all"

_SEEN = 32
"""How many recent messages a link remembers to drop a duplicate. The MQTT bus is
at-least-once, and the same sentence arriving twice reads as a peer repeating itself."""


@dataclass(frozen=True)
class Peer:
    """Another robot in the team, as this pilot is told about it."""

    name: str
    key: str
    """`adapter:backend`, so a pilot can tell a mock peer from a real one."""
    manifest: RobotManifest | None = None


class TeamLink:
    """One member's end of the bus. Handed to `RunConfig.link`; the loop knows nothing else
    about teams."""

    def __init__(
        self,
        name: str,
        peers: Sequence[Peer],
        *,
        bus: Bus,
        task_id: str,
        now: Callable[[], float],
    ) -> None:
        self.name = name
        self.peers = list(peers)
        self.task_id = task_id
        self._bus = bus
        self._now = now
        self._sub: Subscription = bus.subscribe(name)
        self._seen: list[tuple[str, float, str]] = []
        self.sent = 0
        self.heard = 0
        self.abort_reason: str | None = None
        """Why this pilot is being stopped, when it is not the person at the terminal. Set by
        the runner before it fans the abort out, so the record says which member broke."""

    @property
    def members(self) -> list[str]:
        """Everyone: the other members in roster order, then this pilot last."""
        return [*(p.name for p in self.peers), self.name]

    # ── saying ──────────────────────────────────────────────────────────────────────

    def send(self, to: str | None, text: str) -> TalkMsg:
        """Publish one message. `all`, an empty string or None reaches everyone but you."""
        clean = " ".join(str(text).split())[:TALK_MAX_CHARS]
        if not clean:
            raise ValueError("nothing to say")
        whom = (to or EVERYONE).strip()
        addressee: str | None = None
        if whom.lower() not in (EVERYONE, ""):
            if whom == self.name:
                raise ValueError(f"you are {self.name}: tell another member, or `{EVERYONE}`")
            if whom not in {p.name for p in self.peers}:
                others = ", ".join(p.name for p in self.peers)
                raise ValueError(f"no member called {whom!r}; this team is {others}")
            addressee = whom
        message = TalkMsg(
            t=self._now(), src=self.name, task_id=self.task_id, to=addressee, text=clean
        )
        self._bus.publish(message)
        self.sent += 1
        return message

    # ── hearing ─────────────────────────────────────────────────────────────────────

    def drain(self) -> list[dict[str, Any]]:
        """Every TALK addressed to me since the last call, oldest first.

        The bus never echoes a sender to itself, so this only has to drop what was addressed
        to somebody else, and the duplicate a broker may deliver twice."""
        out: list[dict[str, Any]] = []
        for msg in self._sub.drain():
            if not isinstance(msg, TalkMsg):
                continue  # a pilot team sends nothing else, but the protocol is shared
            if msg.to is not None and msg.to != self.name:
                continue
            key = (msg.src, msg.t, msg.text)
            if key in self._seen:
                continue
            self._seen.append(key)
            del self._seen[:-_SEEN]
            out.append({"from": msg.src, "to": msg.to, "text": msg.text})
        self.heard += len(out)
        return out

    # ── being told about the others ─────────────────────────────────────────────────

    def close(self) -> None:
        """Stop hearing. A member that has ended must not keep a queue nobody drains."""
        self._sub.close()

    def describe(self) -> dict[str, Any]:
        """What the transcript and the scripted pilot read: who I am and who else there is."""
        return {"me": self.name, "members": self.members}

    def prompt_section(self) -> str:
        """The `## Your team` block of this pilot's system prompt."""
        if not self.peers:
            return ""
        others = "\n".join(
            f"- `{p.name}` ({p.key}): "
            + (body_summary(p.manifest) if p.manifest is not None else "no datasheet.")
            for p in self.peers
        )
        n = len(self.peers) + 1
        return f"""## Your team
You are `{self.name}`, one of {n} pilots on this task file. Each of you is in a different body
and all of you are working at the same time. Nobody is in charge and nothing assigns the work:
you divide it between you by saying what you will do.

The others:
{others}

Each of those is the short form of the same sheet your own body is described by above: what it
weighs, what it can carry and reach, what it holds with, and what it cannot do at all. It is
shorter than yours and says less, so treat a peer's silence about something as not knowing
rather than as a no. Use it to work out which part of the task is yours and which is somebody
else's.

Talking: call `tell` with `to` (a name above, or `{EVERYONE}`) and a short `text`. It reaches
them in their next observation and moves nothing: it costs no step, though it does use one of
your calls. Say what you are about to do before you do it, and say when you are done, so nobody
waits on you. What they say to you
arrives under "Messages from your team"; you never hear your own words back, and a message
from `{TEAM_SRC}` is the run itself telling you that somebody has finished or stopped.

Your part: `assess_task` judges whether YOUR body can do YOUR part of this task, not whether
the team can do all of it. If no part of it is for this body, say so with `tell` and then call
`declare_success` once the others have told you they are done: answering `infeasible` for a
part that was never yours ends your run for nothing. Tell the others before you declare, either
way.
"""


def make_links(
    members: Sequence[str],
    peers: dict[str, Peer],
    *,
    bus: Bus,
    task_id: str,
    now: Callable[[], float],
) -> dict[str, TeamLink]:
    """One link per member, each told about everyone but itself, in roster order."""
    return {
        name: TeamLink(
            name,
            [peers[other] for other in members if other != name],
            bus=bus,
            task_id=task_id,
            now=now,
        )
        for name in members
    }


def notice(bus: Bus, *, task_id: str, t: float, text: str) -> TalkMsg:
    """The runner's own word to the team: `duck-a declared success: ...`.

    Sent under `team` rather than under the member's name, because the member is no longer
    running and a message from it would read as one it chose to send."""
    message = TalkMsg(
        t=t, src=TEAM_SRC, task_id=task_id, to=None, text=" ".join(text.split())[:TALK_MAX_CHARS]
    )
    bus.publish(message)
    return message


__all__ = [
    "EVERYONE",
    "TEAM_SRC",
    "Peer",
    "TeamLink",
    "TeamMessage",
    "make_links",
    "notice",
]
