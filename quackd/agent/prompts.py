"""Every word the LLM reads, in one place.

The system prompt carries the contract (in prose the model can act on) and the `.duck`
body verbatim. Each turn's observation is compact and structured — features, not frames —
with the image attached separately for providers that can see. One tool call per turn is
stated here *and* enforced by the loop; saying it is not the same as trusting it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from quackd.duckfile.schema import DuckFile
from quackd.perception.base import Detection, summarize_detections
from quackd.transport.base import DuckState
from quackd.verbs.aliases import canonical
from quackd.verbs.registry import Verb, VerbResult
from quackd.verdict import BEFORE_VERDICT, needs_properties

if TYPE_CHECKING:
    from quackd.adapters.manifest import Datasheet, RobotManifest

DUCK_BLURB = "a small biped duck robot (25 cm, 800 g)"

ASSESS_TASK = {
    "name": "assess_task",
    "description": (
        "Your verdict on whether THIS body can do THIS task, judged against the datasheet in "
        "your prompt: the task's needs against the body's limits, not whether you can see the "
        "target yet. Required before the first verb that moves the body: until you have "
        "answered, a verb that moves the body is refused and told so, and only what looks, "
        "speaks or brakes runs. The Rules in your prompt name which of your verbs those are. "
        "Answer `feasible` when every need fits inside a limit you can point to; not having "
        "found the target yet is not by itself a doubt about the body, it is what the run is "
        "for. Answer "
        "`infeasible` when one need clearly exceeds a limit (a 3 kg basket on a 0.3 kg "
        "payload): the run ends at once and nothing moves, so name the limit and what you "
        "estimated. Answer `uncertain` only when the verdict itself turns on a figure you "
        "cannot judge from here: the mass or size of a thing that decides a limit and that you "
        "have not seen (out of view, or detections rather than an image), or a limit that "
        "matters listed as not published. A human is then asked, "
        "or you are told nobody is there to ask. Do not guess a mass or a size from the words "
        "of the task alone: look first, or say uncertain. You may call this again later, once "
        "you have seen the thing. It moves nothing and does not count as a step."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["feasible", "infeasible", "uncertain"],
                "description": (
                    "feasible: go. infeasible: the run ends before any motion. uncertain: a "
                    "human decides, or you are told nobody can."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "One or two sentences: the need, the limit it meets or exceeds, and how "
                    "you compared them."
                ),
            },
            "limits_consulted": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The datasheet fields you read to decide (payload_kg, reach_m, mobility, "
                    "...). Empty when the task needs none of them."
                ),
            },
            "estimates": {
                "type": "array",
                "description": (
                    "What you guessed about the world to reach the verdict, so the record "
                    "shows it. One entry per object and quantity."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "object": {"type": "string"},
                        "quantity": {
                            "type": "string",
                            "enum": [
                                "mass_kg",
                                "size_m",
                                "distance_m",
                                "height_m",
                                "duration_min",
                                "count",
                                "other",
                            ],
                        },
                        "value": {"type": "number"},
                        "basis": {
                            "type": "string",
                            "enum": ["image", "detections", "task_text", "prior_knowledge"],
                            "description": (
                                "What the guess came from. task_text and prior_knowledge are "
                                "the weak ones."
                            ),
                        },
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["object", "quantity", "value", "basis", "confidence"],
                    "additionalProperties": False,
                },
            },
            "needs": {
                "type": "object",
                "description": (
                    "What the task requires, in the datasheet's own field names. Numbers are "
                    "minimums (payload_kg: 3 means at least 3 kg), except work_height_m, which "
                    "is a height the hands must reach. A matcher reads this to say which other "
                    "body could do the task, so fill it in even when the verdict is feasible. "
                    "It is also read back against this body's own datasheet: a feasible "
                    "verdict naming a need the sheet does not meet, or does not publish, is "
                    "refused and told which need. Name only what the task turns on; leave a "
                    "field out, or give 0 or none, when the task does not need it: a need this "
                    "task does not actually rest on is what gets a good verdict refused. A 0 "
                    "or a none asks for nothing and is always met."
                ),
                "properties": needs_properties(),
                "additionalProperties": False,
            },
        },
        "required": ["verdict", "reason"],
        "additionalProperties": False,
    },
}
ASSESS_TASK_NAME = ASSESS_TASK["name"]


DECLARE_SUCCESS = {
    "name": "declare_success",
    "description": "Call when the success criteria are met. Say which criterion and what evidence you have.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Which criterion was met, and the evidence.",
            }
        },
        "required": ["reason"],
        "additionalProperties": False,
    },
}

DECLARE_FAILURE = {
    "name": "declare_failure",
    "description": (
        "Call when the task cannot be completed after trying (target not found, repeated "
        "failures, an abort condition). If nothing has moved yet and the body itself is the "
        "reason, call assess_task with infeasible instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "additionalProperties": False,
    },
}

META_TOOLS = [ASSESS_TASK, DECLARE_SUCCESS, DECLARE_FAILURE]
META_TOOL_NAMES = {t["name"] for t in META_TOOLS}
DECLARE_NAMES = {str(DECLARE_SUCCESS["name"]), str(DECLARE_FAILURE["name"])}
"""The two that end a run. `assess_task` is a meta tool too, but it is a gate, not an ending."""

REMEMBER = {
    "name": "remember",
    "description": (
        "Save one short fact for FUTURE runs on this robot (where things usually are, what "
        "worked, what to avoid). It does not move the robot and does not count as a step. "
        "Do not repeat what the prompt already remembers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "One sentence, concrete and reusable, e.g. 'the ball is usually near the left wall'.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional short labels (place, object, strategy).",
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}
REMEMBER_NAME = REMEMBER["name"]

TELL = {
    "name": "tell",
    "description": (
        "Say one short thing to another pilot in your flock, or to all of them. It reaches "
        "them in their next observation. It moves nothing and does not count as a step, "
        "though it does use one of your calls. Say what you are about to do, what you have "
        "done, or what you need from them, so nobody waits on somebody who is not coming. "
        "You never hear your own words back."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "A member name from `Your flock`, or `all` for everyone.",
            },
            "text": {
                "type": "string",
                "description": "One or two sentences, concrete: 'I have the ball, you spot'.",
            },
        },
        "required": ["to", "text"],
        "additionalProperties": False,
    },
}
TELL_NAME = TELL["name"]


COMPOSITE_VERBS = ("walk_to", "go_to", "search_scan", "approach_and")
"""The verbs that close their own loop on the camera, in the order the prompt prefers to name
one. A body that provides none of them is told about none of them."""


def goal_strategy(allow: Sequence[str]) -> str:
    """The `## Strategy` paragraph of a `--goal` task, in the verbs this body was granted.

    It used to name `observe`, `search_scan` and `go_to` whatever the body was. On 2026-09-15
    an SO-101 arm read all three in a prompt whose allowlist two lines above listed none of
    them, and whose executor would have refused each one: the arm has no camera verb it is
    granted, no scan and no composite. The model worked it out and looked with `report_state`
    instead, which is not a thing to rely on.

    Every verb named here is in `allow`. A body with neither looking verb is told to look with
    `report_state`, and one with no composite is told to prefer the verb that does the whole
    thing rather than being pointed at a verb that does not exist. A Microduck reads exactly
    what it read before."""
    names = set(allow)
    looking: list[str] = [verb for verb in ("observe", "search_scan") if verb in names]
    if not looking and "report_state" in names:
        looking = ["report_state"]
    look = " or ".join(f"`{verb}`" for verb in looking) if looking else "however this body can"
    composite = next((verb for verb in COMPOSITE_VERBS if verb in names), None)
    prefer = (
        f"prefer composite verbs like `{composite}`"
        if composite is not None
        else "prefer the verb that does the whole thing over micro-managing"
    )
    # a body with no camera verb has no frame to verify against, only its own joints
    fresh = "frame" if "observe" in names else "reading"
    return (
        f"Use the available verbs. Look before you act ({look}), {prefer}, verify with a "
        f"fresh {fresh}, then `remember` one fact and declare success."
    )


def executor_section(allow: Sequence[str], *, frames: bool = False) -> str:
    """The `## Your executor` block, for a run whose verbs include `manipulate`, and empty for
    every other run, whose prompt is what it was.

    With it the pilot plans and a learned policy executes (llm+vla): the pilot's calls are few
    and slow, the policy's many and fast, and each has to be told which part is its own. Two
    things decide whether that works, and both are said here. A policy does one short thing it
    was trained on, so the pilot asks for one subtask a call rather than the whole task. And
    `manipulate` is ok when its segment ran, which says nothing about whether the arm did the
    subtask, so the pilot looks again after every one and judges that, rather than reading an
    ok as done. Every verb named is in `allow`, as in `goal_strategy`.

    `frames` is whether every observation brings the pilot the body's camera frames, which it
    does for a body with a camera and a pilot that can see. The frame the observation after a
    segment brings is then the fresh look, and the one that can show a block in a bowl: an arm
    that has a camera has no `observe` in its vocabulary, and its joints cannot show that."""
    names = set(allow)
    if "manipulate" not in names:
        return ""
    if frames:
        again = "judge from the fresh frame your next observation brings"
    elif "observe" in names:
        again = "look again with `observe` and judge from that fresh frame"
    elif "report_state" in names:
        again = "read the arm again with `report_state` and judge from that fresh reading"
    else:
        again = "look again however this body can and judge from what you find"
    return f"""
## Your executor
A learned policy moves this arm when you call `manipulate`, one short segment at a time: you
plan, and it executes. Give it one short subtask per call, of the kind a policy like it was
trained on and in a few plain words ("pick up the red block", "put it in the bowl"), never the
whole task at once. After every segment, {again} what the arm actually did before you choose
the next subtask. `manipulate` coming back ok means only that the segment ran, not that the
subtask was done, so never declare success on its word alone.
"""


PLAY_HEADING = "## Playing a part"

PLAY_VOICES = ("say", "quack")
"""The verbs a played part can give its lines with, in the order the section prefers one:
`say`, and the ducks' `quack`, which maps its text to a tone as their `say` does."""


def play_voice(names: Iterable[str]) -> str | None:
    """The verb a played part gives its lines with, read off the verbs a run offers, or None
    where none of them speaks. A fact about the robot and the allowlist, never the task's
    words (#25)."""
    offered = {canonical(name) for name in names}
    return next((voice for voice in PLAY_VOICES if voice in offered), None)


def play_paragraphs(
    *, still: bool, voice: str | None, memory: bool, mcp: bool = False
) -> list[str]:
    """What a pilot reads when its task may give it a part to play, one line per paragraph.

    Only a run that asks for it reads a word of this (`play: true` in a `duck: 4` task file, or
    `--play`). It exists because the prompt says what the body is, and a task that said "You
    are a cat" asked the pilot to be what the prompt had just told it it was not, with nothing
    to say that acting the part out with the body was the task. Every refusal recorded on the
    arm quoted quackd's own words back. On 2026-09-15 the same model mimed a duck with
    the gripper when the goal named the pose, and typed a quack and moved nothing when the goal
    said only "Imagine you're a duck".

    Built from three facts about the robot and the run, never from the task's words, so no
    sentence turns on what a task says (#25): `still` is a body that cannot take itself
    anywhere, `voice` is the verb among those offered that gives a part its lines
    (`play_voice`), `memory` is a run that keeps notes.
    `mcp` words the same paragraphs for a client whose own prompt frames the pilot: no rule
    about one tool call a turn, the MCP tool names, and the person in the chat where a run
    has its declare reasons.

    It carries both halves of what `uncertain` is for, "not by itself" and "mass or size", as
    every surface that speaks of the verdict must (`tests/test_verdict.py`). No paragraph
    starts with "- `", which is how four adapter tests read the offered verbs out of a
    prompt."""
    assess = "robot_assess_task" if mcp else "`assess_task`"
    you = "You stay this body's pilot" if mcp else "You stay the brain of this body"
    first = [
        "This task may give this body a part to play: an animal, a person, a character, a "
        "feeling or a thing.",
        f"{you}, and the body plays the part with its own moves, the way a mime plays a cat "
        "without being one.",
        "A part is played in poses, gestures and timing."
        if mcp
        else "A part is played in poses, gestures and timing, one tool call each turn as always.",
        "A part told only in words has not been played, and neither has one answered only by "
        "saying that this body is not the thing it plays.",
        "Where the task gives a part, acting it out in moves someone watching could read is "
        "what its success criteria ask of the part, whatever words they use, and a line that "
        "asks you to say what you cannot do, or why this body cannot, means a real limit, such "
        "as one its datasheet names, never what this body is not.",
    ]
    second = [
        f"Judge a part with {assess} against this body's datasheet, as you judge any task: the "
        "question is whether this body can act it out, not whether it is the thing it plays.",
        "Acting asks nothing of a body beyond moving inside its own limits, so a part on its "
        "own is feasible, and being other than what it plays is not by itself a reason for "
        "infeasible or uncertain.",
        "A task that also asks for a real result this body cannot make is infeasible, part or "
        "no part.",
        "In `needs`, name what acting the part out asks of this body, and every need of "
        "anything the task asks to happen for real, in full.",
        "The hands, legs, wings, paws, beak or second arm of what you play belong to the part: "
        "leave them out, or give 0 or none, which is always met.",
    ]
    if still:
        second.append(
            "This body cannot travel, so where a part, or an idiom taken at its word, would "
            "walk, run, fly or go somewhere, it shows the going from where it stands, and the "
            "needs of that say mobility none and name no terrain."
        )
    third = [
        "A part changes how this body moves and nothing else.",
        "Where the task asks for something to happen to a thing in the world, an object "
        "lifted, carried, pressed, moved or knocked over, it has to happen for real, and it is "
        "judged against the datasheet exactly as it would be without the part, the mass or "
        "size of that thing included.",
        "A mime of it does not do it and is never reported as done.",
        "A prop, a place or a sound the part needs that is not here is mimed, and "
        + ("you say so." if mcp else "your reason says so."),
        # The arm's own guard is late: a joint blocked partway through a timed move is found
        # only when the move's time is up, and until then it pushes as the step cap allows
        # (`quackd_lerobot.verbs`). A part asks for bigger poses than a plain task, close to
        # the table the arm stands on.
        "A blow, a stomp or a touch at something that is not here stops before it reaches "
        "anything, the surface this body stands on included, and one at something that is "
        "here lands only where the task asks for that touch for real.",
        # the arm has no guard against its own links meeting: only the joints' travel, and a
        # simulator's collision model where there is one
        "A touch of one part of this body on another, whatever the task asks, stops well "
        "short, because nothing stops one part of it striking another, and a grip closing on "
        "nothing is no such touch.",
        "A part never brings this body into contact with a person, or within reach of one: "
        "where a part, or an idiom taken at its word, would touch, hit, pinch or twist "
        "somebody, play it on this body itself or leave it out.",
        "A touch on a person or a thing that the task really asks for, such as a high five, is "
        "not a part, and it is judged as it would be without one, but an idiom taken at its "
        "word never asks for one.",
        "Every limit and clamp, every line saying what this body cannot do, and every limit "
        "the task sets on how far or how fast to move holds whatever the part.",
        "Inside them, make each pose big enough to read, and while anyone is within this "
        "body's reach, keep every move small and slow, and every move but a touch the task "
        "really asks for away from them.",
        "When a move is refused for its range, show the same thing inside that range, and a "
        "move a person said no to is not tried another way.",
    ]
    told = (
        "anything you tell the person you are chatting with says"
        if mcp
        else "every other reason you give, and anything you tell others, says"
    )
    fourth = [
        "The act may pretend, and the record never does.",
        "Sneaking, sulking or looking innocent belongs to the show, but your verdict reason "
        f"says plainly what this body will do, and {told} plainly what it actually did, as its "
        "joints and any camera pictures show it, then what the moves were meant to show.",
        "When the task asks what is true, about you or about what happened, the answer is the "
        "true one whatever the part.",
        "When the part is a real person, product or AI model, play your idea of them and say "
        "that it is your idea, and "
        + (
            "nothing you say claims you are anyone or anything you are not."
            if mcp
            else "no reason of yours claims you are anyone or anything you are not."
        ),
    ]
    if voice is not None and mcp:
        # `say` has a tool of its own over MCP, and `quack` is reached as any verb is
        spoken = "robot_say" if voice == "say" else f"robot_run_verb with `{voice}`"
        fourth.append(
            f"Where the part has lines, {spoken} gives them this body's own sound, as its "
            "description says, so act them as well, and tell them to the person you are "
            "chatting with after what the body did, as the part's lines."
        )
    elif voice is not None:
        fourth.append(
            f"Where the part has lines, `{voice}` gives them this body's own sound, as its "
            "description says, so act them as well, and quote them in your declare reason "
            "after what the body did, as the part's lines."
        )
    elif mcp:
        # not "this body has no voice": `voice` is read off what the loaded task allows, and
        # a Microduck whose task leaves `say` and `quack` out still has its tones
        fourth.append(
            "No verb this task allows speaks, so where the part has lines, act them, and tell "
            "them to the person you are chatting with after what the body did, as the part's "
            "lines."
        )
    else:
        fourth.append(
            "No verb here speaks, so where the part has lines, act them, and quote them in "
            "your declare reason after what the body did, as the part's lines."
        )
    if memory:
        keep = "robot_remember" if mcp else "`remember`"
        fourth.append(
            f"A note you keep with {keep} is about this robot and never about a part it played."
        )
    return [" ".join(paragraph) for paragraph in (first, second, third, fourth)]


def play_section(*, still: bool, voice: str | None, memory: bool) -> str:
    """The `## Playing a part` block as a played run's prompt carries it, `play_paragraphs`
    under its own heading. `build_system_prompt` leaves it out of every other run, whose
    prompt is what it was."""
    paragraphs = play_paragraphs(still=still, voice=voice, memory=memory)
    return f"\n{PLAY_HEADING}\n" + "\n\n".join(paragraphs) + "\n"


def before_verdict_clause(verbs: Sequence[Verb]) -> str:
    """`only `quack` and `stop` run`: which of THIS run's verbs run before the verdict.

    Read by the same rule the gate applies in `Executor._run_verb`, so the sentence cannot
    drift from the refusal: the canonical name is in `BEFORE_VERDICT`, or the verb's own
    adapter declared it `read_only`. It used to be one fixed list naming `observe`,
    `report_state`, `say` and the head verbs whatever the body was, which was wrong three ways
    at once. hello-world's pilot was told `observe` runs when its contract allows no such verb.
    An arm was told about head verbs no arm has. And since #26 a body quackd never shipped can
    bring its own sensing verb, which the gate lets through and this sentence could not name.

    Spelled as the allowlist spells it, so a duck that allows `get_frame` reads `get_frame`.
    `stop` is last and unconditional: the executor lets the brake through whether or not a
    contract listed it."""
    named = [
        verb.name
        for verb in verbs
        if (verb.read_only or (canonical(verb.name) in BEFORE_VERDICT and verb.kind != "learned"))
        and verb.name != "stop"
    ]
    spelled = [f"`{name}`" for name in dict.fromkeys(named)] + ["`stop`"]
    if len(spelled) == 1:
        return f"only {spelled[0]} runs"
    return f"only {', '.join(spelled[:-1])} and {spelled[-1]} run"


BODY_HEADING = "## Your body: what it can and cannot do"

_CONFIDENCE_KEY = (
    "official: the maker or a paper says so; estimate: one vendor, a community number or a "
    "reading off a photo; measured: somebody measured it and said how"
)


def _clamp_words(limits: dict[str, float]) -> str:
    """What quackd will let through, in the prompt's words.

    Read from `manifest.limits`, never from the datasheet: a clamp is quackd's own rule about
    what it sends, not a fact about the body. `control_hz` and `camera_fov_deg` are not clamps
    and are left out."""
    parts: list[str] = []
    if (vx := limits.get("max_vx")) is not None:
        parts.append(f"{vx:g} m/s forward")
        if (vy := limits.get("max_vy")) is not None:
            parts.append("no sideways motion" if vy == 0 else f"{vy:g} m/s sideways")
        if (wz := limits.get("max_wz")) is not None:
            parts.append(f"{wz:g} rad/s turning")
    if "lift_min_mm" in limits and "lift_max_mm" in limits:
        parts.append(f"lift {limits['lift_min_mm']:g} to {limits['lift_max_mm']:g} mm")
    if (yaw := limits.get("gaze_yaw_deg")) is not None:
        parts.append(f"gaze {yaw:g} degrees of yaw")
    if (deg := limits.get("joint_deg")) is not None:
        parts.append(f"joints within {deg:g} degrees")
    if (norm := limits.get("joint_norm")) is not None:
        parts.append(f"joints within a normalised {norm:g}")
    if (grip := limits.get("gripper")) is not None:
        parts.append(f"gripper 0 to {grip:g}")
    return ", ".join(parts)


def _hands(ds: Datasheet) -> str:
    if ds.manipulator == "beak":
        return (
            "a beak, no arms. It can scoop at an object on the floor right under it, and that "
            "is all"
        )
    if ds.manipulator == "gripper":
        arms = "one arm with a gripper" if ds.arms == 1 else f"{ds.arms} arms with a gripper each"
        return arms
    if ds.manipulator == "arms":
        return f"{ds.arms} arms and no gripper: it holds by closing both on a thing"
    return "none: nothing quackd can command touches an object"


def _power_and_ground(ds: Datasheet, *, mobile: bool) -> str:
    power = {
        True: "Mains powered, so nothing runs down",
        False: "Battery powered",
        None: "Power source not published",
    }[ds.tethered]
    if not mobile:
        return f"{power}. It does not move: no base and no legs"
    ground = {
        "indoor_flat": "Rated for a flat indoor floor",
        "indoor": "Rated for indoors",
        "outdoor": "Rated for outdoors",
    }.get(ds.terrain or "") or (
        "Terrain not published: assume a flat indoor floor and decline anything else"
    )
    rated = f". Not rated for {', '.join(ds.not_rated)}" if ds.not_rated else ""
    return f"{power}. {ground}{rated}"


def body_lines(manifest: RobotManifest) -> list[str]:
    """The body's own facts, as prompt lines.

    Every line starts with a word or a dash and a word, never with a backtick: the prompt
    spells an offered verb as a backticked name after a dash, and four adapter tests read the
    offered verbs back out of the prompt by that shape."""
    ds = manifest.datasheet
    if ds is None:
        return [
            f"The {manifest.model} adapter has published no datasheet. Treat every physical "
            "limit (weight, height, reach, endurance, terrain) as not published, and decline "
            "any task that hinges on one."
        ]
    mobile = manifest.mobility != "none"
    lines = [f"Each number says how sure quackd is of it and who says so ({_CONFIDENCE_KEY})."]
    lines += [f"- {label}: {fig.text(unit)}" for label, fig, unit in ds.known()]
    lines.append(f"- Manipulator: {_hands(ds)}.")
    if unknown := ds.unknown():
        # It used to say "Decline any task that hinges on any of them", and on an arm whose
        # reach nobody had published that forbids reaching for anything, which is the whole of
        # what an arm does. An unpublished figure is a question for a person, which is what
        # `uncertain` asks, and the verdict gate already refuses a `feasible` that names one.
        which = "it" if len(unknown) == 1 else "one of them"
        lines.append(
            f"- Not published: {', '.join(unknown)}. Where a task turns on {which}, say "
            "uncertain and name it rather than guessing; do not decline on it alone."
        )
    lines.append(f"- {_power_and_ground(ds, mobile=mobile)}.")
    limits = dict(manifest.limits)
    ranges: dict[str, Any] = manifest.extras.get("joint_range_deg") or {}
    if ranges:
        # an arm that has answered knows each joint's real travel, and a goal outside it is
        # refused; the schema's symmetric bound would tell the pilot a range it cannot use
        limits.pop("joint_deg", None)
    if clamps := _clamp_words(limits):
        lines.append(f"- quackd clamps you to {clamps}.")
    if ranges:
        travel = ", ".join(f"{joint} {lo:g} to {hi:g}" for joint, (lo, hi) in ranges.items())
        # the second sentence is the servo's rule, and it is here because a reading past the
        # travel beside this line is otherwise a contradiction a careful pilot refuses to move on
        lines.append(
            f"- Each joint's travel in degrees, read from its own calibration, and the only "
            f"goals quackd will send: {travel}. A joint can read past its travel when it was "
            "folded or placed there with torque off, which is where a rest pose usually is; "
            "goals are still limited to the travel."
        )
    if manifest.sensors:
        lines.append(f"- Senses: {', '.join(manifest.sensors)}.")
    if ds.cannot:
        lines.append("Whatever the task says, this body cannot:")
        lines += [f"- {s}" for s in ds.cannot]
    if ds.notes:
        lines.append("Worth knowing:")
        lines += [f"- {s}" for s in ds.notes]
    return lines


def body_section(manifest: RobotManifest) -> str:
    """The `## Your body` block. Facts only: the rule that the pilot must judge the task
    against them before moving is one bullet in the prompt's Rules, where every other enforced
    rule is stated."""
    return f"\n{BODY_HEADING}\n" + "\n".join(body_lines(manifest)) + "\n"


def body_summary(manifest: RobotManifest) -> str:
    """The same facts as one paragraph, for a tool result (MCP has no system prompt)."""
    ds = manifest.datasheet
    if ds is None:
        return f"{manifest.model}: no datasheet published; treat every physical limit as unknown."
    facts = [f"{label.lower()} {fig.text(unit)}" for label, fig, unit in ds.known()]
    facts.append(_hands(ds))
    facts.append(_power_and_ground(ds, mobile=manifest.mobility != "none").lower())
    text = f"{manifest.model}: " + ", ".join(facts) + "."
    if unknown := ds.unknown():
        text += f" Not published: {', '.join(unknown)}."
    if ds.cannot:
        text += " Cannot: " + "; ".join(ds.cannot) + "."
    if clamps := _clamp_words(manifest.limits):
        text += f" Clamps: {clamps}."
    return text


def build_system_prompt(
    duck: DuckFile,
    verbs: list[Verb],
    transport_name: str,
    manifest: RobotManifest | None = None,
    memory_text: str | None = None,
    *,
    assumptions: list[str] | None = None,
    flock_text: str | None = None,
    task_images: Sequence[str] | None = None,
    by_hand: bool = False,
    adapter: str | None = None,
    sees: bool = False,
    play: bool = False,
) -> str:
    """`memory_text` is what the robot remembers from earlier runs (`RobotMemory.recall`);
    None means memory is off for this run, "" means on but empty.

    `play` is whether the task may give the pilot a part to play, as the run decided it: the
    task file's `play`, or `--play` and `--no-play` over it. Passed in rather than read off
    the frontmatter, because a flag can turn it on for a `duck: 1` file whose contract cannot
    say so. False is every run before there was such a thing, and it adds nothing.

    `sees` is whether the pilot is shown the frames an observation carries (`supports_vision`),
    which with a manifest that has a camera means every observation brings a fresh one.

    `assumptions` is what the robot says quackd is standing in for on this backend, read from
    `state.extras` when the run connects. It belongs in the prompt rather than in every
    observation because the list is sentences and an observation line is a line.

    `adapter` is whose backend `transport_name` is (`base.adapter_name`), because two bodies
    now have a backend called `mujoco` and their simulators are nothing alike. None is a bare
    transport, which in quackd is only ever the Microduck's, as the blurb's fallback assumes."""
    fm = duck.frontmatter
    blurb = manifest.blurb if manifest is not None and manifest.blurb else DUCK_BLURB
    names = {v.name for v in verbs}
    # what this body actually has, not what a duck has. The fallback used to name
    # `search_scan` unconditionally, so an arm was told about a composite verb it does not
    # provide, in the same prompt whose allowlist does not list it.
    composite = next((v for v in COMPOSITE_VERBS if v in names), None)
    moves_itself = manifest is None or manifest.mobility != "none"
    controllers = "balance and gait" if moves_itself else "the motion"
    pilot_line = f"the robot's own controllers handle {controllers}"
    if composite is not None:
        pilot_line += (
            f", and composite\nverbs like `{composite}` close their own loops on the camera"
        )
    verb_lines = "\n".join(f"- `{v.name}`: {v.description}" for v in verbs)
    before_verdict = before_verdict_clause(verbs)
    success = "\n".join(f"- {s}" for s in fm.success)
    advisory = fm.advisory_abort_conditions
    abort_lines = (
        "\n".join(f"- {a}" for a in advisory) if advisory else "- (none beyond the enforced ones)"
    )
    body = body_section(manifest) if manifest is not None else ""
    executor = executor_section(
        sorted(names), frames=sees and manifest is not None and "camera" in manifest.sensors
    )
    cameras = list(manifest.extras.get("cameras") or []) if manifest is not None else []
    if len(cameras) > 1:
        # more than one, not merely present. `extras["cameras"]` is the adapter's own list and
        # an adapter may publish it for a single camera, which the LeRobot arm does not and
        # another body may; a pilot with one view told "this body has 1 cameras: forward,
        # every frame is labelled" is promised something the wire never does, because
        # `name_cameras` reads the same count and sends that one picture bare.
        listed = ", ".join(cameras)
        body += (
            f"\nThis body has {len(cameras)} cameras: {listed}. Every frame reaches you each "
            f"step, labelled with the name of the camera that took it. {cameras[0]} is the "
            "primary: the `camera:` line in your observation describes that view and no "
            "other, and the verbs that steer by sight read it alone. A camera that gave "
            "nothing this step is absent rather than blank, so read the labels to see which "
            "views you actually have, and do not assume a missing one is showing you an "
            "empty room.\n"
        )
    placed = ""
    if by_hand:
        reader = "`report_state`" if "report_state" in names else "your state line"
        # what this task was actually granted, not what the body has: a pilot told to close the
        # gripper by a prompt whose allowlist has no `gripper` is being sent at a refusal
        grip = (
            "closing on an object is what makes this body say it is holding something, so if "
            "the task needs a firm hold on what is already between the jaws, call `gripper` to "
            "close on it before you lean on it"
            if "gripper" in names
            else "this task cannot work the gripper, so whatever is between the jaws is held at "
            "the squeeze the person left and you cannot tighten it"
        )
        placed = f"""
## Where this run starts
A person placed this body by hand before your first turn, and quackd is holding it exactly
where they left it. This run does **not** start from the recorded rest pose, so do not assume
a folded arm or a known shape: read {reader} and work from the joint angles it gives you. They
are where somebody decided the work should begin.

The gripper is where their fingers closed it, which is a position and not a grip. Nothing is
reported as held, and nothing should be: {grip}.

When the run ends, the arm is handed back the same way: it holds where you left it, the person
is asked to take whatever is in the gripper, and only then does quackd fold the arm up.
"""
    pictures = ""
    if task_images:
        listed = ", ".join(f"`{name}`" for name in task_images)
        several = len(task_images) > 1
        pictures = f"""
## The picture{"s" if several else ""} that came with this task
{len(task_images)} picture{"s" if several else ""} {"were" if several else "was"} handed to this task on the command line: {listed}. {"Each is" if several else "It is"} in your first turn, labelled `task picture NAME:` in front of the picture itself, and {"they stay" if several else "it stays"} in front of you for the whole run.

{"These are" if several else "This is"} what the task is about. {"They are" if several else "It is"} not what the robot can see: a camera frame, where this body has one, is a separate picture of the room the robot is in right now, labelled with the name of its camera. When the task says "this picture", or "what you see in the image", it means {"these" if several else "this one"} and not the camera.
"""
    stand_ins = ""
    if assumptions:
        listed = "\n".join(f"- {a}" for a in assumptions)
        stand_ins = f"""
## What is a stand-in on this robot
The robot reported these itself, and they are in `extras.assumptions` in every observation.
Each is something quackd stands in for or assumes, not something this robot does. Do not
report one as the robot's own work, and do not read a success from one as evidence that the
real skill works.
{listed}
"""
    persona = f"\n## Persona\n{fm.persona}\n" if fm.persona else ""
    memory = ""
    if memory_text is not None:
        remembered = memory_text.strip() or "(nothing yet — this is the first run on this robot)"
        memory = f"""
## What you remember from earlier runs on this robot
{remembered}

Call `remember` (one short sentence) when you learn something worth keeping for next time:
where an object usually is, which strategy worked, what to avoid. It moves nothing and
costs no step, though it does use one of your calls. Do not save what is already listed
above.
"""
    # built by `quackd.flock.talk`, which knows the roster; this only decides where it goes
    flock = f"\n{flock_text.strip()}\n" if flock_text else ""
    sim_note = ""
    if transport_name == "sim2d":
        sim_note = (
            "\nYou are in the built-in 2D simulator: a cartoon top-down world. Distances are "
            "metres, the arena is about 2 m across, and the ball is orange.\n"
        )
    elif transport_name == "mujoco" and adapter in (None, "microduck"):
        # Deliberately only the arena. What the body is, and what its legs can do, differs
        # between the real duck and the kinematic stand-in behind this one backend name, and
        # both describe themselves in the stand-ins block below. Saying it here as well
        # contradicted one of them: this told the model it was driving "a real biped on its
        # own learned gait" even when it was driving the puppet.
        # Nobody is in this arena, and the model has to be told so outright. `search_scan`
        # still offers `person` as a target, because one shared verb registry serves the
        # cartoon (which has a person), a real camera through YOLO, and this; and the head
        # camera's colour detector still carries the person hue band for the same reason. So
        # a model left to infer it would reasonably scan for somebody and never stop.
        sim_note = (
            "\nYou are in the physics simulator (MuJoCo): a 2 m arena with low walls and an "
            "orange ball that rolls when kicked. Nobody is in the arena with you: there is no "
            "person here to find, follow or walk up to, so do not scan for one, and treat any "
            "`person` detection as scenery misread rather than somebody standing there. "
            "Distances are metres.\n"
        )
    elif transport_name == "mujoco" and adapter == "lerobot":
        # The arm's simulator has no arena and no ball, and until this was keyed on the adapter
        # it was told about both, because the duck's note keyed on the backend name alone.
        # What it has to be told is that nothing it sees or does here was measured on an arm:
        # the servo dynamics are the model's, which the simulator lists among its assumptions,
        # so a grasp that holds here is a rehearsal and not a result. What lies on the table
        # is left to the cameras, because the scene is the simulator's and not the task file's,
        # and a run given no camera is told it has none: the scene has things on the table
        # either way, and a sentence about what the cameras show would have it think not.
        if manifest is not None and "camera" in manifest.sensors:
            seen = (
                "What is on the table is what the cameras show, and every camera is a rendered "
                "view of the model, not a webcam's picture. "
            )
        else:
            seen = (
                "This run has no camera, so whatever is on the table goes unseen: the arm "
                "reports only its own joints. "
            )
        sim_note = (
            "\nYou are in the arm's physics simulator (MuJoCo): a model of this arm on a "
            f"table, with the table in front of it. {seen}"
            "How the joints move and how an object grips, slides or falls is the model's "
            "physics, not anything measured on a real SO-101, so a grasp that works here "
            "rehearses the task rather than showing that the arm can do it.\n"
        )
    # Last before the task, where it can answer the lines above it that say what this body is
    # not ("Whatever the task says, this body cannot:"), and next to the part the task gives.
    played = (
        play_section(
            still=not moves_itself, voice=play_voice(names), memory=memory_text is not None
        )
        if play
        else ""
    )
    return f"""You are the brain of {blurb}. You are a high-level pilot:
you choose ONE verb per turn; {pilot_line}. Do not micro-manage.

## Rules (enforced by the executor — not optional)
- Call exactly one tool per turn. Never zero, never two.
- Only these verbs are allowed: {", ".join(fm.verbs.allow)}. Anything else is refused.
- Budgets: {fm.budgets.max_steps} steps, {fm.budgets.max_minutes:g} minutes, {fm.budgets.max_llm_calls} LLM calls. The run stops when any is hit.
- Verbs marked confirm ({", ".join(fm.verbs.confirm) or "none"}) ask a human before running.
- Before the first verb that moves the body, call `assess_task` with your verdict on whether this body can do this task at all, judged against its datasheet below: `feasible`, `infeasible` (the run ends, nothing moves) or `uncertain` (a human is asked). The verdict is about the body, not the view: not having found the target yet is not by itself a reason for `uncertain`. It is one when a limit turns on that unseen thing's mass or size, or on a figure the datasheet does not publish. Until then {before_verdict}. Assess again later if what you see changes your mind.
- When a success criterion is met, call `declare_success`. If the task turns out impossible while doing it, call `declare_failure`.

## Success criteria
{success}

## Abort conditions you must respect yourself
{abort_lines}

## Verbs
{verb_lines}
{body}{executor}{placed}{pictures}{stand_ins}{persona}{memory}{flock}{sim_note}{played}
## Task file: {fm.name} — {fm.description}

{duck.body}
"""


def inbox_lines(inbox: Sequence[Mapping[str, Any]], me: str | None) -> list[str]:
    """What the flock said to you since your last turn, oldest first.

    `to` is the addressee, so a message sent to everyone reads `all` and one sent to you reads
    `you`: a pilot deciding whether to answer needs to know which it was."""
    out = []
    for message in inbox:
        who = str(message.get("from", "?"))
        to = message.get("to")
        whom = "you" if (to is not None and to == me) else "all"
        out.append(f"- {who} -> {whom}: {str(message.get('text', '')).strip()}")
    return out


def build_observation_text(
    *,
    step: int,
    max_steps: int,
    state: DuckState,
    detections: list[Detection],
    last_verb: str | None,
    last_result: VerbResult | None,
    budget_status: str,
    inbox: Sequence[Mapping[str, Any]] | None = None,
    inbox_for: str | None = None,
    cameras: Sequence[str] | None = None,
    stepped: Sequence[str] | None = None,
) -> str:
    head = f"step {step}/{max_steps}"
    # the budget's own line starts with the same step (`Budget.status`), and every observation
    # said it twice: `[step 3/40 · step 3/40, llm calls 3/40, ...]`
    lines = [
        f"[{head} · {budget_status.removeprefix(f'{head}, ')}]",
        f"state: {state.summary()}",
        f"camera: {summarize_detections(detections)}",
    ]
    if cameras and len(cameras) > 1:
        # only with more than one, and only as a pointer: the pictures are labelled where
        # they are, and the `camera:` line above is the primary's and says nothing of the rest
        rest = ", ".join(cameras[1:])
        lines.append(f"cameras: {cameras[0]} (detections above), {rest}")
    if last_verb is not None and last_result is not None:
        lines.append(
            f"last verb `{last_verb}`: {'ok' if last_result.ok else 'FAILED'} — {last_result.summary}"
        )
    if inbox:
        lines.append("Messages from your flock (newest last):")
        lines.extend(inbox_lines(inbox, inbox_for))
    if stepped:
        # Turns the discrete stepper answered while the model was not asked (`--decision-mode on`).
        # None of it is in the model's history, because none of it is anything the model
        # said, so this line is the whole of what it knows about that time. It has to say
        # who chose them: a pilot that thinks it closed the gripper itself will not think
        # to check.
        lines.append("While you were not asked, the stepper chose these (newest last):")
        lines.extend(f"- {line}" for line in stepped)
    lines.append("Choose exactly one tool.")
    return "\n".join(lines)


def observation_features(
    *,
    state: DuckState,
    detections: list[Detection],
    last_verb: str | None,
    last_result: VerbResult | None,
    allowed: list[str],
    inbox: Sequence[Mapping[str, Any]] | None = None,
    flock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # `inbox` and `flock` are absent rather than empty on a solo run, so every existing reader
    # of these features (the scripted strategies, the goldens) sees exactly what it always did
    extra: dict[str, Any] = {}
    if inbox is not None:
        extra["inbox"] = [dict(m) for m in inbox]
    if flock is not None:
        extra["flock"] = dict(flock)
    return {
        **extra,
        "state": state.model_dump(),
        "detections": [d.model_dump() for d in detections],
        "last_result": (
            {
                "verb": last_verb,
                "ok": last_result.ok,
                "summary": last_result.summary,
                "data": last_result.data,
            }
            if last_result is not None
            else None
        ),
        "allowed": allowed,
    }
