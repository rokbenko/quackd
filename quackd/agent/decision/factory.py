"""`--decision-llm jev`, `--decision-url`, `--decision-mode` to something that answers.

Three flags and four variables resolve here, in one place, so that a refusal can name where a
value came from: a flag somebody has just typed and a line in a `.env` they have forgotten
want different sentences. Nothing in this module imports a backend until a run has named one,
and nothing imports `typesafe_sdk`, `openai` or `laya` at all -- the classes that do are one
function call away and are reached only from `make_decision_llm`. `decisions_api` is also
asked for its address rules once a run has named its row, and those are the standard library
alone.

Kept separate from `catalogue` the way `providers/factory` is kept separate from its own, so
`quackd doctor` can ask "which decision LLMs could run here?" without paying for any of them.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import importlib.util
import os
from functools import lru_cache
from typing import Any

from quackd.agent.decision.base import (
    DecisionError,
    DecisionLLM,
    DecisionMissingKey,
    DecisionNotInstalled,
)
from quackd.agent.decision.catalogue import DECISIONS_API, IN_PROCESS, SYSTEM_ONE
from quackd.agent.decision.catalogue import ENTRY_POINT_GROUP as ENTRY_POINT_GROUP
from quackd.agent.decision.catalogue import PRESET_NAMES as PRESET_NAMES
from quackd.agent.decision.catalogue import PRESETS as PRESETS
from quackd.agent.decision.catalogue import DecisionMode as DecisionMode
from quackd.agent.decision.catalogue import DecisionSpec as DecisionSpec
from quackd.agent.providers.catalogue import Price
from quackd.agent.providers.pricing import SELF_HOSTED, parse_price

ENV_LLM = "QUACKD_DECISION_LLM"
"""Which decision LLM, in the shape `--decision-llm` takes. `off` here is off."""
ENV_URL = "QUACKD_DECISION_URL"
ENV_MODE = "QUACKD_DECISION_MODE"
PRICE_ENV = "QUACKD_DECISION_PRICE"
"""quackd's knob, which is why it is not spelled `TYPESAFE_*`. Everything with that prefix is
read by `typesafe_sdk` itself; this one quackd reads, so it carries quackd's prefix."""

MODES: tuple[str, ...] = ("off", "shadow", "on")


@lru_cache(maxsize=1)
def _plugins() -> dict[str, str]:
    """Every decision LLM a third party has installed here, name to its module.

    The entry point group is the only way one is found, which is the difference between this
    and `PRESETS`: quackd's own rows exist whether or not anything is installed, because the
    tables that print them are a catalogue rather than an inventory, and a plugin exists only
    if it is here.

    Cached, because `entry_points()` walks the whole environment and `doctor` is on this path.
    """
    found: dict[str, str] = {}
    for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        # An editable install keeps the metadata it was built with, so a module moved or
        # removed since is still announced here; believing that turns a missing plugin into an
        # ImportError from somewhere far away.
        #
        # Every exception and not only the two, because `find_spec("pkg.mod")` imports `pkg`
        # to ask it, and a stranger's package is free to raise anything at all while being
        # imported. This runs on the path of `quackd doctor` and of every press of TAB, and
        # somebody else's broken package must not be able to take either of those down.
        with contextlib.suppress(Exception):
            if importlib.util.find_spec(ep.value) is not None:
                found[ep.name] = ep.value
    return found


def preset_names() -> tuple[str, ...]:
    """Every name `--decision-llm` takes: quackd's own in table order, then whatever is
    installed here, alphabetically. This is the list a refusal prints."""
    third_party = sorted(name for name in _plugins() if name not in PRESETS)
    return PRESET_NAMES + tuple(third_party)


def find_preset(name: str, *, source: str = "--decision-llm") -> DecisionSpec:
    """The row this name means, quackd's own first.

    A built-in wins over a plugin that took its name, so a package on `PyPI` called `jev`
    cannot quietly become the thing `--decision-llm jev` reaches.
    """
    folded = name.strip().lower()
    if (known := PRESETS.get(folded)) is not None:
        return known
    if (module_path := _plugins().get(folded)) is not None:
        try:
            module = importlib.import_module(module_path)
        except Exception as e:
            # Named rather than propagated. `doctor` asks about every name it can see, so an
            # unguarded import here let one broken plugin end the command that exists to say
            # which things are broken.
            raise DecisionError(
                f"the decision LLM {folded!r} is installed here as {module_path}, and "
                f"importing it failed: {type(e).__name__}: {e}"
            ) from e
        return DecisionSpec(
            name=folded,
            # Prefixed, so a plugin that called itself `systemone` or `laya` cannot be
            # mistaken for one of quackd's own backends by `make_decision_llm`.
            backend=f"plugin:{folded}",
            summary=str(getattr(module, "SUMMARY", "a decision LLM quackd does not publish")),
            install=str(getattr(module, "INSTALL", f"installed here as {module_path}")),
            url=getattr(module, "URL", None),
            key_env=getattr(module, "KEY_ENV", None),
            model=getattr(module, "MODEL", None),
            extra=getattr(module, "EXTRA", None),
            sdk=None,  # it was found through its own metadata, so it is installed
        )
    raise DecisionError(
        f"unknown decision LLM {name!r} from {source}; choose one of {', '.join(preset_names())}"
    )


def parse_decision_llm(
    text: str | None, *, source: str | None = None
) -> tuple[DecisionSpec, str | None] | None:
    """`NAME[:MODEL]` from `--decision-llm`, then `QUACKD_DECISION_LLM`, then nothing.

    `None` is the answer rather than a fallback: a key sitting in a `.env` file is somebody's
    other project, and quackd never switches a paid dependency on because it found one.

    Split at the first colon only, so a model id that carries its own survives being named.
    """
    where = source or ("--decision-llm" if text is not None else ENV_LLM)
    raw = (text if text is not None else os.environ.get(ENV_LLM) or "").strip()
    if not raw or raw.lower() == "off":
        return None
    name, _, model = raw.partition(":")
    return find_preset(name, source=where), (model.strip() or None)


def resolve_decision_mode(flag: str | None, *, named: bool, refused: bool = False) -> DecisionMode:
    """`--decision-mode`, then `QUACKD_DECISION_MODE`, then whether one was named at all.

    Naming a decision LLM is asking for it, so the default once you have is `on` rather than
    a second flag you must also remember. `shadow` is the one worth typing, and the one the
    `on` warning points at.

    A mode without a decision LLM is a run that would silently do nothing, so it stops instead
    and says which of the two said so -- unless the line itself said off, which is `refused`.
    `--decision-llm off` is how one command opts out of a `QUACKD_DECISION_LLM` sitting in a
    `.env`, and refusing that command because the same `.env` also set a mode would be
    answering "I do not want this" with a demand to name one.

    A blank flag is not an answer either. `--decision-mode ""` is what a shell produces from an
    unset variable in a wrapper script, and reading it as "nothing was said on the line" is
    what lets the variable behind it be heard: taken as a spoken answer it discarded a
    `QUACKD_DECISION_MODE=shadow` and ran the stepper for real.
    """
    if refused:
        return "off"
    spoken = (flag or "").strip()
    where = "--decision-mode" if spoken else ENV_MODE
    raw = (spoken or os.environ.get(ENV_MODE) or "").strip().lower()
    if not raw:
        return "on" if named else "off"
    if raw not in MODES:
        raise DecisionError(f"unknown {where} {raw!r}; choose one of {', '.join(MODES)}")
    if raw != "off" and not named:
        raise DecisionError(
            f"{where} {raw} needs a decision LLM to run: "
            f"--decision-llm {PRESET_NAMES[0]} (or {ENV_LLM})"
        )
    return raw  # type: ignore[return-value]


def resolve_decision_url(spec: DecisionSpec, url: str | None = None) -> str | None:
    """`--decision-url`, then `QUACKD_DECISION_URL`, then where the row says it listens.

    `None` is right for two rows and wrong for one: `jev`'s address belongs to its SDK, the
    in-process one has none, and `local` is the row that exists to be told, so
    `make_decision_llm` refuses it rather than guessing.

    The variable is read only for a row that wants no key, and that is a security rule rather
    than a tidiness one. `QUACKD_DECISION_URL` is the sort of line somebody leaves in a `.env`
    after an afternoon with a server on their own machine. Left to apply to every row, it
    would silently retarget the hosted ones, and those are the rows that send a key -- so a key
    meant for a company's API would be sent to whatever is listening on localhost. A flag typed
    on the line is a different thing: it says where this run goes, and it is obeyed for any
    row, with the key rule of the row's own client deciding what that address is sent.

    Whitespace is stripped everywhere, because `QUACKD_DECISION_URL="   "` is a shell saying
    unset and reading it as an address defeated the one refusal `local` exists to make.
    """
    if given := (url or "").strip():
        return given
    if spec.key_env is None and (from_env := (os.environ.get(ENV_URL) or "").strip()):
        return from_env
    return spec.url or None


def resolve_decision_address(spec: DecisionSpec, url: str | None = None) -> str | None:
    """`resolve_decision_url`, refused here when the row's client could never be handed it.

    Asked while the CLI is still reading its flags, before the soft probe that lets a run go
    ahead without its stepper, and the order is the point. An address OpenAI's row cannot use
    -- `https://api.openai.com/v1` is the likeliest, copied from anywhere that writes the
    address with its version on -- checked only by the client would be refused only on a
    machine with the SDK and the key, and everywhere else would come out as "running without
    it" and a run that exits 0. A typo should stop the run on every machine, and from here it
    does.

    Only the `DECISIONS_API` row is held to it. A System One server is reached through an SDK
    that appends its path to whatever it is given, and a server behind a gateway at a path of
    its own is a real thing somebody may be pointing at."""
    address = resolve_decision_url(spec, url)
    if spec.backend == DECISIONS_API and address is not None:
        from quackd.agent.decision.decisions_api import check_address

        check_address(address)
    return address


def resolve_decision_model(spec: DecisionSpec, model: str | None = None) -> str | None:
    """The id this decision LLM will answer as: the spec's own, then the SDK's variable where
    the row honours one, then the row's default."""
    if model:
        return model
    if spec.model_env and (pinned := os.environ.get(spec.model_env)):
        return pinned
    return spec.model


def sends_key(spec: DecisionSpec, url: str | None = None) -> bool:
    """Whether a run of this row at this address sends its vendor's key, which is the one rule
    behind two answers: whether the key is wanted (`decision_llm_is_available`) and whether a
    turn is costed at the vendor's rate (`resolve_decision_price`). Each client decides what it
    sends for itself, and this says what each decides, so that neither answer can drift from
    what actually leaves the machine.

    - A row with no key variable sends none, anywhere.
    - `openai` sends `OPENAI_API_KEY` to OpenAI's own API and nowhere else
      (`decisions_api.is_openai_host`), so at any other address it is a server of your own.
    - A keyed System One row sends its key only to the address its SDK owns, which is the row
      reached with no address at all: `systemone.SystemOneLLM` hands one it was given an address
      the word instead, because `--decision-llm jev --decision-url http://127.0.0.1:8009` is
      Jev's model id at a server you run, and a company's key has no business there.
    - A plugin's key variable is taken at its word, because what a plugin's client sends is its
      own affair, and it is wanted wherever the run is.

    `url` is the address the run was given, read the way every other rule here reads it
    (`resolve_decision_url`), so `QUACKD_DECISION_URL` moves none of these rows: they are the
    ones it is never read for."""
    if spec.key_env is None:
        return False
    address = resolve_decision_url(spec, url)
    if spec.backend == DECISIONS_API:
        from quackd.agent.decision.decisions_api import is_openai_host

        return is_openai_host(address or "")
    if spec.backend == SYSTEM_ONE:
        return not address
    return True


def resolve_decision_price(spec: DecisionSpec, url: str | None = None) -> Price:
    """What one question is costed at: `QUACKD_DECISION_PRICE`, then the row's published rate,
    then the self-hosted rate.

    A server on your own machine bills you in electricity, and `SELF_HOSTED` is the same `$0`
    the rest of quackd already prices a local model at -- not `None`, which quackd reserves
    for "there is a rate and nobody here knows it". A paid endpoint behind `local` is the
    exception, and `QUACKD_DECISION_PRICE` is how you say so.

    `url` is the address the run was given, and it matters for the two rows with a published
    rate. Each is its vendor's only where its vendor's key goes (`sends_key`). Anywhere else it
    is a server of your own answering the same format, sent the word rather than the key: `jev`
    at a `--decision-url`, and `openai` at an address that is not OpenAI's. A server of your own
    is costed as one rather than at a rate it never bills. At OpenAI's regional hosts it is
    OpenAI's rate, because that is the only one they publish for this endpoint.
    """
    text = os.environ.get(PRICE_ENV)
    if text is not None and text.strip():
        return parse_price(text, source=PRICE_ENV)
    if spec.price is None or not sends_key(spec, url):
        return SELF_HOSTED
    return spec.price


def decision_llm_is_available(spec: DecisionSpec, url: str | None = None) -> tuple[bool, str]:
    """Whether this decision LLM could run here, and in plain words why not.

    Asked before the robot is connected, and answered softly: a missing extra or key means the
    run goes ahead on the model alone, because the stepper is an optimisation and the model is
    the pilot either way. What is not asked here is whether a server is actually listening --
    that is a turn's problem, and a turn that cannot reach one escalates.

    `url` is the address the run was given, and it matters for the keyed rows: a key is wanted
    only where it would be sent (`sends_key`). At a server of your own a keyed row is sent the
    word instead, and a run there must not be switched off for want of a key it was never going
    to send. `openai` is also the row whose SDK can be installed and still lack the client, so
    that is asked too (`decisions_api.client_problem`), and comes out as the same sentence as a
    missing one rather than as a traceback.
    """
    if spec.sdk is not None:
        try:
            importlib.import_module(spec.sdk)
        except Exception:
            return False, str(DecisionNotInstalled(spec.name, spec.extra or spec.name))
    if spec.backend == DECISIONS_API:
        from quackd.agent.decision.decisions_api import client_problem

        if (problem := client_problem(spec)) is not None:
            return False, problem
    if spec.key_env and sends_key(spec, url) and not os.environ.get(spec.key_env):
        return False, str(DecisionMissingKey(spec.name, spec.key_env))
    return True, ""


def make_decision_llm(
    spec: DecisionSpec, *, model: str | None = None, url: str | None = None
) -> DecisionLLM:
    """The thing that answers, built before the robot connects.

    Built here rather than on the first turn on purpose: constructing a client can fail, and
    that failure is a sentence while the CLI is still parsing rather than a phantom cost on a
    turn that never left the machine.
    """
    model = resolve_decision_model(spec, model)
    url = resolve_decision_url(spec, url)
    if spec.backend == SYSTEM_ONE:
        if spec.url is None and spec.key_env is None and not url:
            # `local` is the row with no address of its own, and the only one that can arrive
            # here empty. Refused rather than guessed, the way `--llm local` refuses.
            raise DecisionError(
                f"--decision-llm {spec.name} needs the server address: "
                "--decision-url http://host:port (or QUACKD_DECISION_URL). Or name one "
                f"quackd knows: {', '.join(n for n in PRESET_NAMES if n != spec.name)}."
            )
        from quackd.agent.decision.systemone import SystemOneLLM

        return SystemOneLLM(spec, model=model, url=url)
    if spec.backend == DECISIONS_API:
        # Before the plugin lookup below, which would otherwise take this row for a plugin and
        # fail with a `KeyError` the CLI does not catch.
        from quackd.agent.decision.decisions_api import DecisionsAPILLM

        return DecisionsAPILLM(spec, model=model, url=url)
    if spec.backend == IN_PROCESS:
        from quackd.agent.decision.laya import LayaLLM

        return LayaLLM(spec, model=model)
    module = importlib.import_module(_plugins()[spec.backend.removeprefix("plugin:")])
    built: Any = module.make(spec, url=url, model=model)
    return built  # type: ignore[no-any-return]
