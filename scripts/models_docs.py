"""Render the tables in `docs/models/` from the code that decides what quackd takes, or check
that the pages still hold what the code says.

Every table on those pages that names a model, a vendor, a flag, a key or a rate sits between two
markers this script owns, and the rows between them are the catalogues rendered:
`quackd/agent/providers/catalogue.py` and `factory.py` for the pilots, `local.py` for the local
servers and `quackd/agent/decision/catalogue.py` for the decision LLMs. So a row is changed in
the code and this script run, never in a table, and a test runs it on every commit and fails
when a page says something the code no longer does.

    uv run --no-sync python scripts/models_docs.py          # rewrite the pages
    uv run --no-sync python scripts/models_docs.py --check  # exit 1 if any page would change

It imports the catalogues and nothing that reaches a vendor, and it reads no environment: a
`QUACKD_LLM` or an `OPENAI_BASE_URL` on the machine that runs it changes nothing it writes.
"""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from quackd.adapters.policies import FAMILIES, NOT_SERVED, PolicyFamily
from quackd.agent.decision.catalogue import (
    DECISIONS_API,
    IN_PROCESS,
    PRESET_NAMES,
    PRESETS,
    SYSTEM_ONE,
    DecisionSpec,
)
from quackd.agent.providers.catalogue import (
    CLOUD_NAMES,
    KEY_FALLBACKS,
    LOCAL_NAMES,
    OPEN_ENDED,
    OPENROUTER_PRICES_CHECKED,
    PRICES_CHECKED,
    ModelSpec,
    Price,
    models_for,
)
from quackd.agent.providers.factory import EXTRA_FOR, KEY_ENV, OPENAI_COMPATIBLE
from quackd.agent.providers.local import PRESETS as LOCAL_PRESETS
from quackd.agent.providers.openai import BASE_URL_ENV, DEFAULT_BASE_URL

REPO = Path(__file__).resolve().parents[1]
PAGES_DIR = REPO / "docs" / "models"

#: Who makes or runs each thing a page names, as a reader would say it. A name the code adds
#: and this table does not know is an error rather than a blank cell, so a new vendor cannot
#: reach a page unnamed.
MAKERS: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "gemini": "Google",
    "grok": "xAI",
    "mistral": "Mistral AI",
    "deepseek": "DeepSeek",
    "cohere": "Cohere",
    "qwen": "Alibaba Cloud (Qwen)",
    "kimi": "Moonshot AI (Kimi)",
    "glm": "Z.ai (GLM)",
    "meta": "Meta",
    "openrouter": "OpenRouter, other vendors' models behind one key",
    "local": "any server that speaks OpenAI's API",
    "ollama": "Ollama",
    "vllm": "vLLM",
    "llamacpp": "llama.cpp's `llama-server`",
    "lmstudio": "LM Studio",
}

#: The company or project behind each name `--llm` and `--decision-llm` take, as one row of
#: `vendors.md` names it, so a company that fills two roles is one row there.
COMPANIES: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "gemini": "Google",
    "grok": "xAI",
    "mistral": "Mistral AI",
    "deepseek": "DeepSeek",
    "cohere": "Cohere",
    "qwen": "Alibaba Cloud",
    "kimi": "Moonshot AI",
    "glm": "Z.ai",
    "meta": "Meta",
    "openrouter": "OpenRouter",
    "local": "any server that speaks OpenAI's API",
    "ollama": "Ollama",
    "vllm": "vLLM",
    "llamacpp": "llama.cpp",
    "lmstudio": "LM Studio",
    "decision:jev": "TypeSafe",
    "decision:openai": "OpenAI",
    "decision:kev": "Kev (jaredpalmer/kev)",
    "decision:von": "Von (von-sdk)",
    "decision:openjev": "OpenJev (razorback16/openjev)",
    "decision:opendecision": "OpenDecision",
    "decision:local": "any server that speaks System One",
    "decision:laya": "Laya",
}

#: The vendors whose own SDK picks the address, which quackd leaves alone.
SDK_ADDRESS = ("anthropic", "gemini")

_WORDS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]


def number_word(n: int) -> str:
    """`n` as the docs write a count: a word up to twenty, digits past it."""
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


def maker(name: str) -> str:
    try:
        return MAKERS[name]
    except KeyError:
        raise SystemExit(
            f"scripts/models_docs.py has no maker for {name!r}: add it to MAKERS"
        ) from None


def company(name: str) -> str:
    try:
        return COMPANIES[name]
    except KeyError:
        raise SystemExit(
            f"scripts/models_docs.py has no company for {name!r}: add it to COMPANIES"
        ) from None


def cell(text: str) -> str:
    """A table cell: a pipe escaped, so `off|shadow|on` cannot split a row."""
    return text.replace("|", "\\|")


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def rate(value: float | None) -> str:
    """A rate the way the guides write one: 2.0 as 2, every other digit kept, none for a rate
    the vendor does not publish."""
    return "none" if value is None else str(value).removesuffix(".0")


def rates(price: Price | None) -> str:
    if price is None:
        return "unpriced"
    return " / ".join(
        rate(r) for r in (price.input, price.output, price.cache_read, price.cache_write)
    )


def key_cell(name: str) -> str:
    key = f"`{KEY_ENV[name]}`"
    return f"{key}, or `{KEY_FALLBACKS[name]}`" if name in KEY_FALLBACKS else key


def address(name: str) -> str:
    """Where a vendor's requests go when nothing overrides it."""
    if name in SDK_ADDRESS:
        return "the SDK's own"
    if name == "openai":
        return f"`{DEFAULT_BASE_URL}`, or `{BASE_URL_ENV}`"
    module = importlib.import_module(f"quackd.agent.providers.{name}")
    url = getattr(module, OPENAI_COMPATIBLE[name]).base_url
    return f"`{url}`"


# ── the blocks ──────────────────────────────────────────────────────────────────────────


def pilot_vendors() -> str:
    rows = []
    for name in CLOUD_NAMES:
        ids = models_for(name)
        count = f"[{len(ids)}](#{name})"
        if name in OPEN_ENDED:
            count = f"[{len(ids)}, and ids it lists](#{name})"
        rows.append(
            [
                f"`{name}`",
                maker(name),
                f"`quackd[{EXTRA_FOR[name]}]`",
                key_cell(name),
                address(name),
                f"`{ids[0].id}`",
                count,
            ]
        )
    return table(("`--llm`", "Who", "Extra", "Key", "Address", "Default", "Ids"), rows)


def pilot_local() -> str:
    rows = []
    for name in LOCAL_NAMES:
        url = LOCAL_PRESETS[name]
        where = f"`{url}`" if url else "none: give `--base-url` or `QUACKD_BASE_URL`"
        rows.append(
            [
                f"`{name}`",
                maker(name),
                where,
                f"`quackd[{EXTRA_FOR[name]}]`",
                f"`{KEY_ENV[name]}`, or none",
            ]
        )
    return table(("`--llm`", "Server", "Default address", "Extra", "Key"), rows)


def _notes(i: int, m: ModelSpec) -> str:
    notes = ["default"] if i == 0 else []
    if m.api == "responses":
        notes.append("Responses API")
    if not m.forced_tools:
        notes.append("asked with `auto`")
    return ", ".join(notes)


def pilot_ids() -> str:
    total = sum(len(models_for(name)) for name in CLOUD_NAMES)
    out = [
        f"{total} model ids from {number_word(len(CLOUD_NAMES))} cloud vendors. Rates are USD "
        f"per million tokens, input / output / cache read / cache write, as each vendor's own "
        f"page gave them on {PRICES_CHECKED}, and OpenRouter's list on "
        f"{OPENROUTER_PRICES_CHECKED}. None is a rate the vendor does not publish, and unpriced "
        "a model with no public per-token rate at all, which a run records as an unknown cost "
        "rather than a free one."
    ]
    for name in CLOUD_NAMES:
        rows = [
            [
                f"`{name}:{m.id}`",
                m.label,
                m.status,
                "yes" if m.vision else "no",
                _notes(i, m),
                rates(m.price),
            ]
            for i, m in enumerate(models_for(name))
        ]
        out.append(f"### `{name}`")
        out.append(table(("`--llm`", "Label", "Status", "Frames", "Notes", "USD per Mtok"), rows))
    return "\n\n".join(out)


def _wire(spec: DecisionSpec) -> str:
    return {
        SYSTEM_ONE: "System One, `POST /v1/systemone`",
        DECISIONS_API: "OpenAI's Decisions API, `POST /v1/decisions`",
        IN_PROCESS: "none: asked in this process",
    }.get(spec.backend, spec.backend)


def _runs(spec: DecisionSpec) -> str:
    if spec.backend == IN_PROCESS:
        return "in this process"
    if spec.key_env:
        return "hosted, by its maker"
    return "a server you run" if spec.url else "a server you run, at `--decision-url`"


def _decision_address(spec: DecisionSpec) -> str:
    if spec.url:
        return f"`{spec.url}`"
    if spec.backend == IN_PROCESS:
        return "none"
    return "the SDK's own" if spec.key_env else "the one you give"


def _decision_rate(spec: DecisionSpec) -> str:
    if spec.price is None:
        return "nothing billed: it is yours"
    return f"{rate(spec.price.input)} / {rate(spec.price.output)}, {spec.price.source}"


def decision_rows() -> str:
    rows = [
        [
            f"`{name}`",
            PRESETS[name].summary,
            _wire(PRESETS[name]),
            _runs(PRESETS[name]),
            f"`{PRESETS[name].key_env}`" if PRESETS[name].key_env else "none",
            f"`quackd[{PRESETS[name].extra}]`" if PRESETS[name].extra else "none",
            _decision_address(PRESETS[name]),
            f"`{PRESETS[name].model}`" if PRESETS[name].model else "the server's own",
            "with `--decision-frames`" if PRESETS[name].sees else "no",
            _decision_rate(PRESETS[name]),
            f"[{name}](../guides/decision-llms/{name}.md)",
        ]
        for name in PRESET_NAMES
    ]
    header = (
        "`--decision-llm`",
        "What it is",
        "Wire",
        "Runs",
        "Key",
        "Extra",
        "Address",
        "Default model",
        "Frames",
        "USD per Mtok in / out",
        "Guide",
    )
    return table(header, rows)


def roles() -> str:
    ids = sum(len(models_for(name)) for name in CLOUD_NAMES)
    vlas = sum(1 for family in FAMILIES if family.takes_words)
    rows = [
        [
            "**Pilot LLM**",
            "`--llm VENDOR[:MODEL]`",
            "yes, and `fake`, the scripted pilot, flies when nothing names one",
            f"{ids} ids from {number_word(len(CLOUD_NAMES))} cloud vendors, more through "
            f"OpenRouter's own list, and {number_word(len(LOCAL_NAMES))} local presets",
            "[pilot-llms.md](pilot-llms.md)",
        ],
        [
            "**Decision LLM**",
            "`--decision-llm NAME[:MODEL]`",
            "no, and off unless a run names one",
            f"{number_word(len(PRESET_NAMES))} decision LLMs, hosted or your own",
            "[decision-llms.md](decision-llms.md)",
        ],
        [
            "**Learned policy**",
            "`--policy-url`, to a `quackd policy serve` or an endpoint as `hf:NAME`",
            "no, and the SO-101 arm alone takes one",
            f"{number_word(len(FAMILIES))} LeRobot policy types, {number_word(vlas)} of them "
            "VLAs, served from the Hub",
            "[vlas.md](vlas.md)",
        ],
    ]
    return table(("Role", "Named with", "Required", "What quackd names", "Page"), rows)


def _code(text: str) -> str:
    """A need as a page writes it: a `--pin REPO@REVISION` and a config key set as code."""
    text = re.sub(r"(--pin [\w./-]+@REVISION)", r"`\1`", text)
    return re.sub(r"\((vlm_model_name|base_model_path)\)", r"(`\1`)", text)


def _licence(family: PolicyFamily) -> str:
    if family.licence_url is None:
        return family.licence
    return f"[{family.licence}]({family.licence_url}), read {family.licence_read}"


def policy_served() -> str:
    rows = [
        [
            f"`{family.type}`",
            family.label,
            family.maker,
            "yes" if family.takes_words else "no, it learns one task",
            "; ".join(_code(need) for need in family.needs) if family.needs else "nothing",
            family.gpu,
            _licence(family),
            family.ran,
        ]
        for family in FAMILIES
    ]
    header = (
        "Type",
        "Family",
        "Maker",
        "Told the subtask in words",
        "Needs besides the extra",
        "GPU",
        "Licence",
        "How far it has got",
    )
    return table(header, rows)


def policy_refused() -> str:
    rows = [
        [
            refused.name,
            refused.maker,
            f"`{refused.type}`" if refused.type else "none",
            refused.why,
            refused.would_change,
        ]
        for refused in NOT_SERVED
    ]
    return table(("Not served", "Maker", "LeRobot type", "Why not", "What would change that"), rows)


def vendor_roles() -> str:
    """One row per company or project, the roles it fills merged: OpenAI's pilot and decision
    rows are one row, and so are Physical Intelligence's two policy types."""
    seen: dict[str, dict[str, list[str]]] = {}

    def row(who: str) -> dict[str, list[str]]:
        return seen.setdefault(
            who, {"pilot": [], "decision": [], "policy": [], "key": [], "extra": []}
        )

    def add(entry: dict[str, list[str]], column: str, value: str) -> None:
        if value not in entry[column]:
            entry[column].append(value)

    for name in (*CLOUD_NAMES, *LOCAL_NAMES):
        entry = row(company(name))
        add(entry, "pilot", f"`{name}`")
        add(entry, "key", key_cell(name) if name in CLOUD_NAMES else "none needed")
        add(entry, "extra", f"`quackd[{EXTRA_FOR[name]}]`")
    for name in PRESET_NAMES:
        spec = PRESETS[name]
        entry = row(company(f"decision:{name}"))
        add(entry, "decision", f"`{name}`")
        add(entry, "key", f"`{spec.key_env}`" if spec.key_env else "none needed")
        add(entry, "extra", f"`quackd[{spec.extra}]`" if spec.extra else "none")
    for family in FAMILIES:
        entry = row(family.maker)
        add(entry, "policy", f"`{family.type}`")
        gated = any("gated" in need for need in family.needs)
        add(
            entry,
            "key",
            "a Hub token granted the gated tokenizer it names" if gated else "none needed",
        )
        add(entry, "extra", f"`{family.extra}`")
    rows = [
        [
            who,
            ", ".join(entry["pilot"]) or " ",
            ", ".join(entry["decision"]) or " ",
            ", ".join(entry["policy"]) or " ",
            ", ".join(entry["key"]),
            ", ".join(entry["extra"]),
        ]
        for who, entry in seen.items()
    ]
    header = (
        "Who",
        "Pilot, `--llm`",
        "Decision LLM, `--decision-llm`",
        "Learned policy, its `type`",
        "Key",
        "Extra",
    )
    return table(header, rows)


#: Every block, by the name its markers carry: what renders it and the file it is rendered
#: from, which the start marker names so a reader of the page knows where to change a row.
BLOCKS: dict[str, tuple[str, Callable[[], str]]] = {
    "roles": ("quackd/agent/providers/catalogue.py and quackd/adapters/policies.py", roles),
    "pilot-vendors": ("quackd/agent/providers/factory.py", pilot_vendors),
    "pilot-local": ("quackd/agent/providers/local.py", pilot_local),
    "pilot-ids": ("quackd/agent/providers/catalogue.py", pilot_ids),
    "decision-rows": ("quackd/agent/decision/catalogue.py", decision_rows),
    "policy-served": ("quackd/adapters/policies.py", policy_served),
    "policy-refused": ("quackd/adapters/policies.py", policy_refused),
    "vendor-roles": ("the three catalogues", vendor_roles),
}

#: Which blocks each page carries, once each, in this order.
PAGES: dict[str, tuple[str, ...]] = {
    "README.md": ("roles",),
    "pilot-llms.md": ("pilot-vendors", "pilot-local", "pilot-ids"),
    "decision-llms.md": ("decision-rows",),
    "vlas.md": ("policy-served", "policy-refused"),
    "vendors.md": ("vendor-roles",),
}

_START = re.compile(r"^<!-- generated: ([a-z0-9-]+)\b[^\n]*-->$", re.M)
"""A block's start, with or without the sentence the script writes after its name, so a new
block can be added to a page as `<!-- generated: NAME -->` and an end marker, and filled."""
_END = re.compile(r"^<!-- end generated: ([a-z0-9-]+) -->$", re.M)


def start_marker(name: str) -> str:
    source = BLOCKS[name][0]
    return (
        f"<!-- generated: {name}, by scripts/models_docs.py from {source}. Change a row there "
        "and run the script, not this table. -->"
    )


def rewrite(text: str, page: str) -> str:
    """`text` with every block `page` carries rendered afresh between its markers.

    Refused rather than guessed: a block this script does not know, one the page should carry
    and does not, one that appears twice or out of order, and one with no end marker."""
    text = text.replace("\r\n", "\n")
    wanted = PAGES[page]
    found = _START.findall(text)
    if unknown := sorted(set(found) - set(BLOCKS)):
        raise ValueError(f"docs/models/{page} has blocks this script does not render: {unknown}")
    if tuple(found) != wanted:
        raise ValueError(
            f"docs/models/{page} carries the blocks {found}, and should carry {list(wanted)}"
        )
    for name in wanted:
        start = next(m for m in _START.finditer(text) if m.group(1) == name)
        end = next((m for m in _END.finditer(text, start.end()) if m.group(1) == name), None)
        if end is None:
            raise ValueError(f"docs/models/{page}'s block {name} has no end marker")
        body = BLOCKS[name][1]()
        text = (
            text[: start.start()]
            + start_marker(name)
            + "\n\n"
            + body
            + "\n\n"
            + text[end.start() :]
        )
    return text


def main(argv: Sequence[str]) -> int:
    check = "--check" in argv
    stale = []
    for page in PAGES:
        path = PAGES_DIR / page
        with open(path, encoding="utf-8", newline="") as f:
            old = f.read()
        new = rewrite(old, page)
        if old.replace("\r\n", "\n") == new:
            continue
        stale.append(page)
        if not check:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)
    if stale and check:
        print("stale pages in docs/models/: " + ", ".join(stale))
        print("run: uv run --no-sync python scripts/models_docs.py")
        return 1
    for page in stale:
        print(f"rewrote docs/models/{page}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
