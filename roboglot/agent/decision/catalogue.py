"""The decision LLMs Roboglot knows, as data, importing none of them.

A *decision LLM* answers typed questions about a state and generates nothing: a choice from a
set you define, a yes/no probability, a score on a rubric. TypeSafe's Jev was the first, and
what most of these share is not a vendor but a wire format -- `POST /v1/systemone`, a named
state and a mapping of typed questions in, answers with probabilities out. So a server that
speaks it is a row in the table below and not a module, and the one Roboglot cannot name is
reached with `--decision-llm local --decision-url`.

One row speaks something else. OpenAI's Decisions API asks the same kind of question in its own
shape, at `POST /v1/decisions`, so `openai` is a row with a backend of its own, `DECISIONS_API`,
and the translation lives in `decisions_api.py` rather than here. It is still a row: its address,
its key variable, its LLM and its rate are data like everybody else's.

`factory.py` builds them; this file only says which ones exist. The split is the one
`adapters/catalogue.py` makes and for the same reason: `roboglot doctor` renders the whole table
on a machine where none of them is installed, and `--help` reads the names. Everything here is
a string, a number or a `Price`, and nothing here may import `typesafe_sdk`, `openai`, `laya`,
pydantic or anything else heavy.

A decision LLM with an API of its own rather than a server announces itself through the
`roboglot.decision_llms` entry point group, the way a third party's adapter does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from roboglot.agent.providers.catalogue import Price

DecisionMode = Literal["off", "shadow", "on"]
"""`off` is Roboglot as it has always been, `shadow` asks every turn and changes nothing, `on`
lets the answer stand where it clears the floor. A mode rather than a boolean because the
middle one is the whole reason the other two are safe to choose between."""

ENTRY_POINT_GROUP = "roboglot.decision_llms"
"""Where an installed decision LLM announces itself. One entry per name, its name to its
module, and that module carries `make(spec, *, url, llm_id)` and describes itself with
`SUMMARY`, `LLM_ID`, `URL`, `KEY_ENV` and `EXTRA`."""

SYSTEM_ONE = "systemone"
"""Reached over HTTP with `typesafe_sdk`, which is the protocol's own client. Every server
below but one speaks the same wire format, so they differ only in the row."""

DECISIONS_API = "decisions"
"""Reached over HTTP at `POST /v1/decisions`, OpenAI's own wire format, through the `openai`
SDK's generic request. The questions go as a list rather than a mapping and the answers come
back as a list that names each question, so this is a second transport rather than a second
address, and `decisions_api.py` translates both ways. Named for the format rather than for the
vendor, the way `SYSTEM_ONE` is: a server of your own that answers `/v1/decisions` is the same
backend at another address."""

IN_PROCESS = "laya"
"""Loaded into this process and asked directly. No server, no network, no key."""


@dataclass(frozen=True, slots=True)
class DecisionSpec:
    """One decision LLM Roboglot can name, said without importing it."""

    name: str
    """What `--decision-llm` takes, and what the record says answered."""
    backend: str
    """`SYSTEM_ONE`, `DECISIONS_API`, `IN_PROCESS`, or a plugin's own name."""
    summary: str
    """One line for `doctor`, and for a message that has to say what this is."""
    install: str
    """How you get it running, in one line, for the person reading `doctor` and wondering."""
    url: str | None = None
    """Where it listens with nothing overridden. `None` for `jev`, whose address its SDK owns,
    and for the in-process one, which has no address at all."""
    key_env: str | None = None
    """The variable holding its key. `None` is a server that wants no key, and the client
    sends `NO_KEY` instead, so a hosted key sitting in somebody's `.env` is never sent to a
    server on their own machine."""
    llm_id: str | None = None
    """The id sent when the spec names none. `None` means name one yourself."""
    llm_env: str | None = None
    """A variable the SDK itself reads for the LLM, honoured only where the SDK owns the
    address too. One row has one, and nothing else should grow one."""
    price: Price | None = None
    """What a question costs. `None` is not free and not unknown: it is a server you run, and
    `factory.resolve_decision_price` turns it into the self-hosted rate the rest of Roboglot
    already uses for an LLM on your own machine."""
    extra: str | None = "decision"
    """The `roboglot[...]` that installs the client. `None` for a plugin, which is installed by
    definition if it was found."""
    sdk: str | None = "typesafe_sdk"
    """The import name that says whether that extra is here. `None` skips the probe."""
    sees: bool = False
    """Whether Roboglot may show this row the robot's camera, on a run that asks with
    `--decision-frames`. True on `openai` alone, whose API takes an image beside the text, and
    then only at OpenAI's own hosts (`frames.may_send_frames`); false on every other row, which
    is sent text and nothing else, and a run that asks for frames with one of them stops before
    the robot connects (`factory.resolve_decision_frames`). That a vendor would take a camera
    frame is not on its own a reason to send one, which is why the flag is off unless a run says
    so. A field rather than a sentence in the stepper so that which rows may see is one line per
    row and one test, and a plugin's row is built with it false whatever the plugin says about
    itself (`factory.find_preset`)."""


#: Every decision LLM Roboglot names, in the order `doctor` prints them: the two hosted ones, Jev
#: first because it set the format, then the servers that speak that format, the escape hatch for
#: the one Roboglot has never heard of, and the one that needs no server at all. Adding a server
#: that speaks System One is one row.
#:
#: `127.0.0.1` rather than `localhost` throughout, because these servers bind IPv4 and a
#: `localhost` that resolves to `::1` first is refused with nothing useful said.
PRESETS: dict[str, DecisionSpec] = {
    "jev": DecisionSpec(
        name="jev",
        backend=SYSTEM_ONE,
        summary="TypeSafe's Jev, hosted: the System One LLM the format is named after",
        install="roboglot[decision] and TYPESAFE_API_KEY (typesafe.ai)",
        url=None,  # the SDK's own default; TYPESAFE_BASE_URL is how you move it
        key_env="TYPESAFE_API_KEY",
        # Pinned, not `jev-latest`. The aliases move, and a run whose decision LLM changed
        # under it is a run whose transcript describes an LLM that is no longer the one that
        # answered. `--decision-llm jev:jev-latest` is how you ask for a moving one. The pin is
        # 1.14.0, "the newest Jev release, available from October 14, 2026", since 2026-11-14,
        # and `jev:jev-1.13.0`, still current, is how you ask for the one it moved from.
        llm_id="jev-1.14.0",
        llm_env="TYPESAFE_DEFAULT_MODEL",
        # TypeSafe charge $0.042 per million input tokens and do not charge for output, the
        # same for 1.14 and 1.13 ([their models page](https://docs.typesafe.ai/models), read
        # 2026-09-21 and again 2026-11-14). One of the two rows here with a published rate,
        # `openai` the other: every other one is a server you run yourself.
        price=Price(input=0.042, output=0.0, source="published"),
    ),
    "openai": DecisionSpec(
        name="openai",
        backend=DECISIONS_API,
        summary="OpenAI's Decisions API, hosted: gpt-6-luna over POST /v1/decisions",
        # The pilot's own extra, because the client is the same `openai` SDK the pilot uses: an
        # extra of its own would install that wheel under a second name. Nor does this row move
        # the floor the extra pins, because the request it makes is one the SDK has carried
        # since before that floor (`decisions_api.py` says which).
        install="roboglot[openai] and OPENAI_API_KEY (platform.openai.com)",
        # An address, unlike `jev`'s `None`, and the backend hands the SDK `<url>/v1` on every
        # build whatever was typed. Left to itself the SDK reads `OPENAI_BASE_URL` when it is
        # given no address, and Roboglot's own local presets read that variable as the address of
        # an LLM server on your own machine (`providers/local.py`), so it may well be set where
        # this row runs, and the hosted key would follow it there. Holding the address here is
        # also what lets `doctor` print where the key goes.
        url="https://api.openai.com",
        key_env="OPENAI_API_KEY",
        # The only LLM the endpoint serves, by OpenAI's own Decisions guide, read 2026-10-08 and
        # again 2026-11-14, the API generally available since 2026-10-22. Whether the name moves
        # under a run is on the row's page as an assumption.
        llm_id="gpt-6-luna",
        # OpenAI charge $0.10 per million input tokens and nothing for output, by the same guide:
        # https://developers.openai.com/api/docs/guides/decisions
        # It now prices the cache too: "Cache-read and cache-write rates match the standard
        # `gpt-6-luna` prompt caching rates", which are the `gpt-6-luna` pilot row's, $0.01 to read
        # and $0.125 to write. This guide names no cache lifetime, so the $0.125 is an inference:
        # the write rate OpenAI's caching guide gives the default 30 minute one. The guide adds that
        # regional processing premiums and long-context multipliers apply and publishes neither for
        # this endpoint, so a run at any of OpenAI's regional hosts (`openai_headers.OPENAI_HOSTS`)
        # is costed at this rate, and `ROBOGLOT_DECISION_PRICE` is how you say otherwise.
        price=Price(input=0.10, output=0.0, cache_read=0.01, cache_write=0.125, source="published"),
        extra="openai",
        sdk="openai",
        # The one row that may be shown the camera, and only on a run that says so, at OpenAI's
        # own address: the field's own docstring says why, and `frames.py` holds the rule.
        sees=True,
    ),
    "kev": DecisionSpec(
        name="kev",
        backend=SYSTEM_ONE,
        summary="Kev, self-hosted: Qwen3.5 with a decision head, on your own GPU",
        install=(
            # Kev 1.0 pinned twice: the server at the `kev-1.0` tag and the weights at the `v1.0`
            # tag Kev 1.0 put on each of its four checkpoints, so what runs is what the page
            # read. Its own release notes pin the weights alone and clone `main`, whose later
            # commits are fixes this pin leaves out. No `KEV_DTYPE=bf16`: bf16 is the server's
            # own default on a GPU. `cd kev` and not only the clone: without it `uv sync` runs
            # in whatever directory the reader was standing in, which is the one mistake a
            # copied command should not be able to make.
            "git clone --branch kev-1.0 https://github.com/jaredpalmer/kev && cd kev && "
            "uv sync --extra serve && "
            "uv run --extra serve python -m kev.serve "
            "--run jaredpalmer/kev-4b@v1.0 --port 8009"
        ),
        # 8009 because its own README says so on every line that starts it. Its code default
        # is 8008, so a reader who runs it bare lands elsewhere and `--decision-url` is how
        # they say where.
        url="http://127.0.0.1:8009",
        llm_id="kev-latest",
    ),
    "openjev": DecisionSpec(
        name="openjev",
        backend=SYSTEM_ONE,
        summary="OpenJev, self-hosted: DiffusionGemma behind vLLM, or MLX on Apple silicon",
        install=(
            # Its own README's command, verbatim, and nothing but the command: a reader
            # copies this cell. `--ipc=host` because vLLM needs the shared memory, and the
            # mount because the weights are about 18 GB: without it every new container
            # downloads them again. On Apple silicon it is
            # `OPENJEV_BACKEND=mlx python -m openjev` instead, which the page says and this
            # line deliberately does not, because the two are alternatives rather than one
            # command. 0.7.0 is the newest tag of this image and the one the README names;
            # never `:latest`, which Docker Hub still points at 0.2.0.
            "docker run -d --gpus all --ipc=host -p 127.0.0.1:8080:8080 "
            "-v ~/.cache/huggingface:/root/.cache/huggingface razorback16/openjev:0.7.0"
        ),
        url="http://127.0.0.1:8080",
        # Its accepted set is closed -- `openjev-latest`, `openjev-0.1`, `jev-latest`,
        # `jev-preview` -- and a pinned Jev version is refused with a 400. That is exactly
        # why the id lives in the row: a shared default would have been wrong here on every
        # single request.
        llm_id="openjev-latest",
    ),
    "opendecision": DecisionSpec(
        name="opendecision",
        backend=SYSTEM_ONE,
        summary="OpenDecision, self-hosted: a zero-shot encoder that runs without a GPU",
        install="pip install OpenDecision && opendecision serve",
        url="http://127.0.0.1:8000",
        # Ignored: it serves one LLM and names it in the answer. Sent anyway, because the
        # record should say what Roboglot asked for and not what the SDK would have filled in.
        llm_id="opendecision",
    ),
    "local": DecisionSpec(
        name="local",
        backend=SYSTEM_ONE,
        summary="any other server that speaks /v1/systemone, at --decision-url",
        install="--decision-url http://host:port (or ROBOGLOT_DECISION_URL)",
        url=None,  # refused without one, the way `--llm local` is
        llm_id=None,  # the server names its own, and `local:<id>` is how you say which
    ),
    "laya": DecisionSpec(
        name="laya",
        backend=IN_PROCESS,
        summary="Laya, in this process: an encoder off Hugging Face, no server and no key",
        # Quoted, because `[laya]` unquoted is a glob in zsh and the shell eats it before
        # pip sees it. What it pulls (torch) and when it downloads its weights (first use)
        # is on the page: this line is the command and nothing else.
        install='uv pip install "roboglot[laya]"',
        # Its own name for the decision-tuned checkpoint. Not `laya`, which is an alias for
        # the plain English one: the names that reach a servo are worth being exact about.
        # `multilingual` is the other, and `--decision-llm laya:multilingual` asks for it.
        llm_id="typed-decisions",
        extra="laya",
        sdk="laya",
    ),
}

PRESET_NAMES: tuple[str, ...] = tuple(PRESETS)
"""The names Roboglot ships, in table order. `factory.preset_names()` adds whatever plugins the
environment announces, and that longer list is what a refusal prints."""
