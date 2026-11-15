"""The one verb only a base over rosbridge has: ask the bridge what the body is.

Every other verb here is a core verb, because a Twist is all this adapter sends. This one
sends nothing at all: it reads the topic list and the robot's own description, and refreshes
the datasheet the pilot judges a task against. It exists because `rosbridge` is a name for a
transport, so the honest static answer is "nothing known", and this is how that stops being
true (ADR-0032).
"""

from __future__ import annotations

from roboglot.verbs.registry import NoParams, Verb, VerbContext, VerbResult
from roboglot_rosbridge.introspection import Introspection

DESCRIPTION = (
    "Ask the bridge what this body is: the topic list, and the robot's own description (its "
    "name, what the links weigh, and every joint with its limits). Sends nothing and moves "
    "nothing. Use it when the datasheet says nothing was discovered, or before planning "
    "anything that depends on a joint. The datasheet is refreshed from what comes back, but "
    "the description already in your prompt is not: read the result."
)


async def introspect(ctx: VerbContext, _params: NoParams) -> VerbResult:
    reread = getattr(ctx.transport, "introspect", None)
    if reread is None:
        return VerbResult.fail("this robot cannot re-read its own description")
    try:
        intro = await reread()
    except Exception as e:  # a bridge that has gone away is a failed verb, not a crashed run
        return VerbResult.fail(f"could not read the bridge: {type(e).__name__}: {e}")
    if not isinstance(intro, Introspection):  # pragma: no cover - a transport that lied
        return VerbResult.fail("the transport answered with something that is not an introspection")
    return VerbResult.success(intro.summary(), **intro.payload())


def rosbridge_verbs() -> dict[str, Verb]:
    return {
        "introspect": Verb(
            name="introspect",
            description=DESCRIPTION,
            execute=introspect,
            params=NoParams,
            timeout_s=30.0,
            read_only=True,
        )
    }
