"""The pilot's word on whether the body can do the task, and the vocabulary it says it in.

Every other gate in quackd refuses one verb. This one refuses *all motion* until the model
has said, against the datasheet in its prompt, whether the task fits the body it is driving.
The verdict is the model's own judgement and is recorded as such: what it estimated, what it
read, and what the task would need, so a transcript shows the reasoning rather than only the
refusal.

`needs` is the one vocabulary three readers share: the tool schema the model fills in, the
matcher that says which other body could, and a flock role that asks for a body that can
carry. A number is a minimum (a working height is a point inside a band), a word must match,
and a figure the maker never published is not met: a robot that cannot say what it carries is
not offered a task that carries something. `missing_needs_in` carries the four exceptions to
that last rule, and the one place a pilot judging its own body is read more kindly than a
coordinator judging a stranger's bid, and says why it has each of them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quackd.adapters.manifest import Manipulator, Mobility, Terrain

if TYPE_CHECKING:
    from quackd.adapters.manifest import RobotManifest

VerdictWord = Literal["feasible", "infeasible", "uncertain"]
Human = Literal["go", "no_go"]

BEFORE_VERDICT = frozenset(
    {
        "stop",
        "observe",
        "report_state",
        "say",
        "quack",
        "express",
        "gaze",
        "look",
        "introspect",
        "watch",
        "map",
        "mark_place",
        "forget_place",
    }
)
"""What runs before a verdict: the verbs that speak, look, or read, and the brake.

A pilot has to be able to look at the thing before judging whether it can lift it. Everything
else waits, including a verb quackd has never heard of: the gate reads this set and one
other thing, the verb's own `read_only` flag, so a body quackd has never shipped can still
say "this one only looks" about its own sensing verbs. A learned verb never carries that
flag (`learned.py` registers it as `confirm`, unproven), and the gate excludes a learned verb
from this set by name as well, so an unproven policy called `observe` waits like everything
else until somebody classifies it on purpose.

`watch` is here for the reason `gaze` is: it moves the head and nothing else, on the Microduck
as far as `gaze`'s own `left`, `right` and `up` turn it, and down as far as robotd's own idle
glances tilt it. Whether its frames may go to a decision LLM is not the pilot's verdict to give
but the run's, and only `--decision-frames` gives it (ADR-0054).

`map`, `mark_place` and `forget_place` are here because they move nothing: they read where the
robot already is, and name it or forget a name (`quackd.verbs.places`).

This half is matched by name, which makes these a vocabulary rather than a list: a body
that ships a verb called `gaze` which walks has said the wrong word about itself, the way a
verb that carries `read_only` and sends an intent has."""

MOVES_THE_BODY = frozenset(
    {
        "move",
        "go_to",
        "search_scan",
        "approach_and",
        "sit",
        "stand",
        "stand_up",
        "kick",
        "grab",
        "move_joints",
        "gripper",
        "grip",
        "place",
        "pick",
        "manipulate",
        "lift",
        "home_arms",
        "perform",
        "drive_to",
        "explore",
        "collect_litter",
        "pick_and_drop",
        "stow_arm",
    }
)
"""The other half. The gate never reads this; it exists so a test can say every verb every
shipped adapter offers was classified deliberately, in both directions."""

TERRAIN_ORDER: tuple[Terrain, ...] = ("indoor_flat", "indoor", "outdoor")
"""Rated for more than the task asks is fine; rated for less is not."""

MANIPULATOR_WORDS = (*get_args(Manipulator), "any")
MOBILITY_WORDS = (*get_args(Mobility), "any")
"""`none` is a need too: "this task needs no locomotion" is what a pilot on an arm bolted to a
table has to be able to say. The words used to leave it out, so the only other thing on offer
was `any`, which means some kind and which a bolted arm fails, and nothing told the pilot that
leaving the key out was the way to say it. On the 2026-09-23 bench runs an SO-101 pilot, told
to fill `needs` in even for a feasible verdict, was refused by its own datasheet for needs it
did not have, and the y/N question that followed is why nearly every run stopped there."""

NEEDS_NUMBERS: tuple[str, ...] = (
    "payload_kg",
    "reach_m",
    "endurance_min",
    "work_height_m",
    "arms",
)
"""Minimums, in the datasheet's own field names. `work_height_m` is the odd one: it asks for a
height the hands must be able to reach, which is a point inside `workspace_height_m`."""

_NUMBER_TEXT: dict[str, str] = {
    "payload_kg": "The heaviest thing the task has the body hold or carry, in kg: a minimum.",
    "reach_m": "How far the task has the hands reach, in metres: a minimum.",
    "endurance_min": "How long the task keeps the body running, in minutes: a minimum.",
    "work_height_m": (
        "A height above the floor the hands must reach, in metres. Not a minimum: it has to "
        "fall inside the band the body works in. Leave it out, or give 0, when the task does "
        "not turn on a height."
    ),
    "arms": "How many arms the task needs at once: a minimum.",
}

_WORD_TEXT: dict[str, str] = {
    "mobility": (
        "How the body must get about. none: the task needs no locomotion, so an arm on a table "
        "is fine. legged or wheeled: that kind. any: some kind of locomotion, either one."
    ),
    "manipulator": (
        "What the body must touch objects with. none: the task touches nothing. beak, gripper "
        "or arms: that kind. any: something that touches objects, whichever kind."
    ),
    "terrain": (
        "The floor the task is on, from least to most demanding: indoor_flat, indoor, outdoor. "
        "A body rated for more meets it, and a body that does not move meets indoor_flat."
    ),
}

NEEDS_WORDS: dict[str, tuple[str, ...]] = {
    "manipulator": MANIPULATOR_WORDS,
    "mobility": MOBILITY_WORDS,
    "terrain": TERRAIN_ORDER,
}


def check_needs(value: Mapping[str, Any]) -> dict[str, float | str]:
    """What the task demands of a body, validated. Shared by `assess_task` and a flock role."""
    out: dict[str, float | str] = {}
    for key, want in value.items():
        if key in NEEDS_NUMBERS:
            if isinstance(want, bool) or not isinstance(want, int | float):
                raise ValueError(f"needs.{key} must be a number (a minimum)")
            if want < 0:
                raise ValueError(f"needs.{key} must be 0 or more")
            out[key] = float(want)
        elif key in NEEDS_WORDS:
            if want not in NEEDS_WORDS[key]:
                raise ValueError(f"needs.{key} must be one of {', '.join(NEEDS_WORDS[key])}")
            out[key] = str(want)
        else:
            vocabulary = ", ".join((*NEEDS_NUMBERS, *NEEDS_WORDS))
            raise ValueError(f"needs.{key} is not in the datasheet vocabulary ({vocabulary})")
    return out


def needs_properties() -> dict[str, dict[str, Any]]:
    """The `needs` object's JSON schema, so the model is shown the vocabulary it must use.

    Every field says what it means, because the words are only half of it. The enums went out
    bare, so a pilot saw `legged, wheeled, any` and nothing saying that `any` excludes a body
    that stays put, and `work_height_m` was described as "at least this much", which is the
    one thing it is not: `missing_needs_in` reads it as a point inside a band. The text here
    and the reader have to agree, or a pilot is refused for doing what it was told."""
    properties: dict[str, dict[str, Any]] = {
        key: {"type": "number", "description": _NUMBER_TEXT[key]} for key in NEEDS_NUMBERS
    }
    properties["arms"] = {"type": "integer", "description": _NUMBER_TEXT["arms"]}
    for key, words in NEEDS_WORDS.items():
        properties[key] = {"type": "string", "enum": list(words), "description": _WORD_TEXT[key]}
    return properties


def needs_text(needs: Mapping[str, Any]) -> str:
    """`payload_kg=3, manipulator=gripper`, in a stable order."""
    return ", ".join(f"{key}={_number(needs[key])}" for key in sorted(needs))


def _number(value: Any) -> str:
    return f"{value:g}" if isinstance(value, int | float) and not isinstance(value, bool) else value


def _as_number(value: Any) -> float | None:
    """A need's value as a number, or None for a null, a word or a bool.

    `missing_needs_in` is handed a raw dict off the wire before anything validates it, so every
    comparison goes through here: a value that is not a number is not a zero and not a height,
    and it falls through to the refusal rather than raising out of the caller."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _figure_value(facts: Mapping[str, Any], key: str) -> float | None:
    figure = facts.get(key)
    if isinstance(figure, Mapping):
        raw = figure.get("value")
        return float(raw) if isinstance(raw, int | float) else None
    return (
        float(figure) if isinstance(figure, int | float) and not isinstance(figure, bool) else None
    )


def missing_needs_in(
    needs: Mapping[str, Any],
    facts: Mapping[str, Any],
    mobility: str | None,
    *,
    own_sheet: bool = False,
) -> list[str]:
    """One line per unmet need, sorted by key; empty means this body can be asked.

    `facts` is a datasheet dumped to a dict, which is what a flock bid carries, so a
    coordinator can judge a bid from a robot it does not run. A figure nobody published is
    unmet rather than assumed. The reader and the text the pilot reads have to agree, or a
    pilot is refused for doing as it was told, so there are four exceptions, each of which
    would otherwise refuse an honest answer:

    - A zero asks for nothing, for every number. The tool asks the pilot to fill `needs` in
      even when the verdict is feasible and to give 0 for what the task does not turn on, so
      `payload_kg: 0` is how a pilot says the task carries nothing. `work_height_m: 0` used to
      be read as "the ground", which is below the one working height band quackd ships and
      unpublished everywhere else, so a pilot that did as it was told was refused on every
      body. A task whose hands really work at the floor says so with a height above zero.
    - `none` for `mobility` or `manipulator` asks for nothing: no locomotion, nothing held.
    - An unpublished terrain meets `indoor_flat` on a body that moves and has a datasheet,
      because the prompt tells exactly that body to assume a flat indoor floor.
    - A body that does not move, and has a datasheet, meets `indoor_flat` and nothing above it:
      it stands on whatever floor its table stands on, and the prompt says "it does not move"
      where a moving body is told its terrain. It is refused as "(it does not move)", which is
      the fact the pilot was shown, rather than "(not published)", which it never was.

    A body with no datasheet at all gets none of the floor exceptions: its prompt says to treat
    every physical limit as not published, terrain included, and a bid that carried no sheet
    said nothing. `mobility` comes in apart from `facts` because it lives on the manifest.

    `own_sheet` is the one place a pilot judging its own body is read more kindly than anybody
    judging a stranger's. With it, a `work_height_m` against a sheet that publishes no working
    height band is not refused: the prompt never lists a working height as a gap
    (`Datasheet.FIGURES` leaves it out on purpose), so the pilot had no way to know it was one
    and is judging a height against the height and reach it was shown. The verdict gate
    (`own_sheet_objection`) and `solo_hint`'s last sentence about the pilot's own body read it
    that way.
    Everything that names a body to hand a task to reads it strictly, which is the default:
    `bodies_that_could`, the MCP `could` list, a flock role and the coordinator judging a bid.
    There an unknown is not a yes, and a body that never said how high it works is not the one
    to offer a task at a height. A body with no datasheet is told height is not published, so
    it gets no leniency either way."""
    out: list[str] = []
    for key in sorted(needs):
        want = needs[key]
        number = _as_number(want)
        if key in NEEDS_NUMBERS and number == 0:
            continue
        if key == "mobility":
            if want == "none":
                continue  # no locomotion needed: met by a body that has some, too
            has = mobility or "unknown"
            if not (want == "any" and has not in ("none", "unknown")) and has != want:
                out.append(f"mobility = {want} (has {has})")
            continue
        if key == "manipulator":
            if want == "none":
                continue  # nothing held: met by a body with hands, too
            has = str(facts.get("manipulator") or "unknown")
            if not (want == "any" and has not in ("none", "unknown")) and has != want:
                out.append(f"manipulator = {want} (has {has})")
            continue
        if key == "terrain":
            rated = facts.get("terrain")
            if rated is None:
                # the prompt renders an unpublished terrain as "assume a flat indoor floor and
                # decline anything else" for a body that moves (`prompts._power_and_ground`),
                # and "it does not move" for one that does not, so either way a pilot asking
                # for a flat indoor floor has done as it was told. Only where the prompt says
                # it, though: a body with no datasheet at all is told the opposite ("treat
                # every physical limit as not published"). Anything above a flat indoor floor
                # is unmet either way.
                if facts and mobility == "none":
                    if want != "indoor_flat":
                        out.append(f"terrain = {want} (it does not move)")
                elif not (facts and mobility is not None and want == "indoor_flat"):
                    out.append(f"terrain = {want} (not published)")
            elif TERRAIN_ORDER.index(str(rated)) < TERRAIN_ORDER.index(str(want)):  # type: ignore[arg-type]
                out.append(f"terrain = {want} (rated {rated})")
            continue
        if key == "endurance_min" and facts.get("tethered") is True:
            continue  # mains powered: nothing to run down
        if key == "arms":
            arms = facts.get("arms")
            if not isinstance(arms, int) or number is None or arms < number:
                out.append(f"arms >= {_number(want)} (has {arms if arms is not None else 0})")
            continue
        if key == "work_height_m":
            band = facts.get("workspace_height_m")
            if not isinstance(band, Mapping):
                # a pilot's own sheet that publishes no band was never shown the gap; a stranger
                # is judged strictly. A value that is not a number is never excused
                if not (own_sheet and facts and number is not None):
                    out.append(f"work_height_m = {_number(want)} (not published)")
            elif number is None or not float(band["low"]) <= number <= float(band["high"]):
                out.append(
                    f"work_height_m = {_number(want)} "
                    f"(reaches {float(band['low']):g} to {float(band['high']):g} m)"
                )
            continue
        have = _figure_value(facts, key)
        if have is None:
            out.append(f"{key} >= {_number(want)} (not published)")
        elif number is None or have < number:
            out.append(f"{key} >= {_number(want)} (has {have:g})")
    return out


def missing_needs(
    needs: Mapping[str, Any], manifest: RobotManifest, *, own_sheet: bool = False
) -> list[str]:
    """The same, for a robot whose manifest is in hand. `own_sheet` as `missing_needs_in`."""
    sheet = manifest.datasheet
    return missing_needs_in(
        needs,
        sheet.model_dump() if sheet is not None else {},
        manifest.mobility,
        own_sheet=own_sheet,
    )


def own_sheet_objection(
    needs: Mapping[str, Any], here: RobotManifest | None, *, tool: str = "assess_task"
) -> str | None:
    """Why a `feasible` verdict cannot stand on this body's own datasheet, or None when it can.

    `missing_needs` held another robot's bid to its sheet at the coordinator, and nothing held
    a pilot's verdict about its own body to its own sheet, so a `feasible` whose `needs` named
    a figure nobody published went straight through and the body moved. The agent loop and the
    MCP session both refuse with this one sentence, so a pilot hears the same words wherever
    it is driving from.

    It offers three ways out rather than one. The pilot measured on Qwen3-32B answered
    `uncertain` to a refusal that named only `infeasible`, and `uncertain` is a fine answer
    here: it asks a person, and a person who knows the figure can put it in the task file's
    `datasheet:` block, which is the only thing that makes a sheet say something new. The
    other is the honest case where the pilot asked for more than the task needs.

    Both callers withdraw the standing verdict when this fires, so the gate shuts rather than
    leaving an older `feasible` to carry the motion.

    A body with no manifest has no sheet to object with, and a verdict that named no need has
    nothing to be held to. The sheet is read as its own pilot was shown it (`own_sheet`): a
    working height the prompt never listed as a gap is not held against the verdict."""
    if here is None or not needs:
        return None
    lacking = missing_needs(needs, here, own_sheet=True)
    if not lacking:
        return None
    return (
        "this body does not meet what you said the task needs: "
        + "; ".join(lacking)
        + ". A feasible verdict cannot rest on a need its own datasheet does not meet, and "
        f"nothing moves until you answer again. Call {tool}: infeasible if that need decides "
        "the task, feasible with the need corrected if you asked for more than the task turns "
        "on, or uncertain to put it to a person, who can answer it or publish the figure in "
        "the task file's own datasheet block"
    )


def datasheet_value(manifest: RobotManifest, field: str) -> float | str | None:
    """What this body reports for one need's field, or None when nobody published it."""
    if field == "mobility":
        return manifest.mobility
    sheet = manifest.datasheet
    if sheet is None:
        return None
    if field in ("manipulator", "terrain"):
        return getattr(sheet, field)
    if field == "arms":
        return float(sheet.arms)
    if field == "work_height_m":
        band = sheet.workspace_height_m
        return band.high if band is not None else None
    figure = getattr(sheet, field, None)
    return figure.value if figure is not None else None


class Estimate(BaseModel):
    """One thing the pilot guessed about the world on its way to a verdict, and how."""

    model_config = ConfigDict(extra="forbid")

    object: str
    quantity: Literal[
        "mass_kg", "size_m", "distance_m", "height_m", "duration_min", "count", "other"
    ]
    value: float
    basis: Literal["image", "detections", "task_text", "prior_knowledge"]
    confidence: Literal["low", "medium", "high"]

    def text(self) -> str:
        return f"{self.object} {self.quantity}={self.value:g} ({self.basis}, {self.confidence})"


class Verdict(BaseModel):
    """The pilot's judgement of this task against this body."""

    model_config = ConfigDict(extra="forbid")

    verdict: VerdictWord
    reason: str = Field(..., min_length=1)
    limits_consulted: list[str] = Field(default_factory=list)
    estimates: list[Estimate] = Field(default_factory=list)
    needs: dict[str, float | str] = Field(default_factory=dict)
    human: Human | None = Field(
        default=None,
        description="What a person answered about an `uncertain` verdict. Never the model's: "
        "`assess_task` has no such field, and both surfaces refuse one that arrives with it.",
    )

    @field_validator("needs", mode="before")
    @classmethod
    def _vocabulary(cls, value: Any) -> Any:
        return check_needs(value) if isinstance(value, Mapping) else value

    @property
    def go(self) -> bool:
        """Whether verbs that move the body may run on this verdict."""
        return self.verdict == "feasible" or (self.verdict == "uncertain" and self.human == "go")

    def summary(self) -> str:
        return f"{self.verdict}: {self.reason}"

    def question(self) -> str:
        """What the person in the room is asked."""
        parts = [f"The pilot is not sure this robot can do the task: {self.reason}"]
        if self.estimates:
            parts.append("It estimated " + "; ".join(e.text() for e in self.estimates) + ".")
        if self.needs:
            parts.append(f"The task would need {needs_text(self.needs)}.")
        return " ".join(parts)

    def blocking_reason(self) -> str:
        """Why the gate is still shut, in the words the pilot gets back."""
        if self.verdict == "infeasible":
            return f"the task was judged infeasible ({self.reason})"
        if self.human == "no_go":
            return "a human was asked about an uncertain verdict and said no"
        return f"the verdict is uncertain and nobody has cleared it ({self.reason})"


def solo_hint(needs: Mapping[str, Any], here: RobotManifest | None) -> str:
    """Which bodies here could, by their own datasheets. Empty when the task named no need.

    Read from the static descriptions, so it costs no connection. It names only robots whose
    adapter is installed, because a manifest is built by the adapter and quackd cannot
    describe a body whose package is absent. So the answer is about this machine rather than
    about the nine quackd publishes, and a machine with one robot can only speak for that
    one. A body whose maker never published the figure is not named: unknown is not a yes.

    The last sentence is about the pilot's own body and reads its sheet the way the verdict
    gate does (`own_sheet`), so it cannot say "does not meet" of a verdict the gate let
    through. The list of bodies that could stays strict: it names bodies to hand a task to.

    The two readings differ in one place only, a working height on a sheet that publishes no
    working height band, and there the last sentence says that rather than "meets". Said as
    "meets those needs" it followed "No robot installed here meets needs work_height_m=..." about
    a body that is installed here, and said the sheet met a height several times the body's own
    when the sheet had only never published a band. So "meets" is kept for a sheet the strict
    reading passes too, and a sheet the lenient reading alone passes is said to publish no band,
    which leaves whether the body reaches that height to the pilot, as the gate does."""
    if not needs:
        return ""
    from quackd.adapters.factory import bodies_that_could

    able = bodies_that_could(needs)
    listed = needs_text(needs)
    if able:
        names = [name for name, _manifest, _missing in able]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
        sentence = f"By their datasheets, {joined} could (needs {listed})."
    else:
        sentence = f"No robot installed here meets needs {listed}."
        if best := _best_numeric(needs):
            sentence = sentence[:-1] + f": {best}."
    if here is not None and not missing_needs(needs, here, own_sheet=True):
        if not missing_needs(needs, here):
            sentence += (
                " This body's own datasheet meets those needs, so the verdict is the pilot's "
                "judgement rather than a limit."
            )
        else:
            sentence += (
                " This body publishes no working height band, so whether it reaches that "
                "height is the pilot's judgement."
            )
    return sentence


def _best_numeric(needs: Mapping[str, Any]) -> str:
    """`the most is toddlerbot at 1.48 kg`, for the first numeric need nobody meets.

    It used to report the first numeric key present, met or not, so a task that asked for
    nothing (`payload_kg: 0`) and was refused on some other need read "the most is lerobot at
    0.5 kg", which explains a refusal that never happened. Now it names a need only when it is
    above zero, no installed body meets it, and the want exceeds the most anybody publishes:
    the last is what "the most" answers, and it leaves out a working height that is below
    every band rather than above it."""
    from quackd.adapters.factory import installed_manifests

    installed = installed_manifests()
    for key in NEEDS_NUMBERS:
        want = _as_number(needs.get(key))
        if want is None or want <= 0:
            continue
        if any(not missing_needs({key: want}, manifest) for _name, manifest in installed):
            continue  # somebody here meets this one, so it is not why nobody could
        ranked = [
            (value, name)
            for name, manifest in installed
            if isinstance(value := datasheet_value(manifest, key), float)
        ]
        if not ranked:
            continue
        value, name = max(ranked)
        if want <= value:
            continue
        unit = key.rsplit("_", 1)[-1] if "_" in key else key
        return f"the most is {name} at {value:g} {unit}"
    return ""
