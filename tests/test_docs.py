"""Docs that make promises about code are checked against the code."""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest

from quackd.agent.decision.catalogue import PRESET_NAMES, PRESETS
from quackd.agent.providers.factory import CLOUD_NAMES, KEY_ENV, default_model_for
from quackd.verbs.registry import default_registry
from quackd_microduck import upstream_api as up
from tests.adapter_layout import adapter_module

REPO = Path(__file__).resolve().parents[1]
README = (REPO / "README.md").read_text(encoding="utf-8")


def test_the_microduck_page_lists_every_microduck_upstream_ref() -> None:
    """The Microduck has two upstreams and its page carries both: `robotd`'s API, and
    `microduck_rl`'s model and policies, which the physics backend fetches and runs.

    Every other upstream in the project has a doc-completeness guard, the six other adapter
    pages through `test_adapter_doc_lists_every_upstream_ref` and `robotd` through this one.
    Without the second loop the newest table is the only one that can go stale in silence.
    """
    from quackd_microduck.sim3d import upstream_api as microduck_rl

    doc = (REPO / "docs" / "adapters" / "microduck" / "README.md").read_text(encoding="utf-8")
    missing = [ref.name for ref in up.all_refs() if ref.name not in doc]
    assert not missing, f"docs/adapters/microduck/README.md is missing: {missing}"
    unverified = [
        ref.name for ref in microduck_rl.refs_by_status("UNVERIFIED") if ref.name not in doc
    ]
    assert not unverified, f"the Microduck's page is missing microduck_rl assumptions: {unverified}"


def test_adapter_status_lists_every_backend() -> None:
    from quackd.adapters.factory import BACKENDS

    doc = (REPO / "docs" / "adapters" / "status.md").read_text(encoding="utf-8")
    for adapter, backends in BACKENDS.items():
        for backend in backends:
            assert f"`{adapter}:{backend}`" in doc, (
                f"docs/adapters/status.md lacks {adapter}:{backend}"
            )


def test_adapter_guide_and_manifest_spec_match_the_code() -> None:
    from quackd.adapters.factory import ADAPTER_NAMES
    from quackd.verbs.core import REQUIREMENTS

    guide = (REPO / "docs" / "adapters" / "writing-an-adapter.md").read_text(encoding="utf-8")
    for name in ADAPTER_NAMES:
        assert f"`{name}`" in guide, f"docs/adapters/writing-an-adapter.md does not mention {name}"
    for fn in ("describe", "implementations", "conditions", "make"):
        assert f"def {fn}(" in guide
    spec = (REPO / "docs" / "reference" / "manifest-spec.md").read_text(encoding="utf-8")
    for verb in REQUIREMENTS:
        assert f"`{verb}`" in spec, (
            f"docs/reference/manifest-spec.md does not list core verb {verb}"
        )
    assert "manifest.schema.json" in spec and "digest()" in spec


@pytest.mark.parametrize(
    "adapter",
    ["lerobot", "rosbridge", "open_duck", "xlerobot", "alohamini", "toddlerbot"],
)
def test_adapter_doc_lists_every_upstream_ref(adapter: str) -> None:
    api = adapter_module(adapter, "upstream_api")
    doc = (REPO / "docs" / "adapters" / adapter / "README.md").read_text(encoding="utf-8")
    missing = [ref.name for ref in api.all_refs() if ref.name not in doc]
    assert not missing, f"docs/adapters/{adapter}/README.md is missing: {missing}"
    assert api.PIN[:7] in doc and "never" in doc.lower()  # the honesty label


def test_adapter_doc_lists_every_simulator_upstream_ref() -> None:
    """The arm's simulator reads a second upstream, TheRobotStudio's SO-ARM100, whose refs live
    in `quackd_lerobot.sim.upstream_api`. The guard above reads `quackd_lerobot.upstream_api`
    only, so without this one the model's table could go stale in silence.

    Each ref needs a row of its own in the table for its status. A name found anywhere on the
    page is not enough: the model's file name is in the prose, and `GRIPPER_MAP` is named in a
    VERIFIED row, so either row could go and a looser check would stay green."""
    from quackd_lerobot.sim import upstream_api as so_arm100

    doc = (REPO / "docs" / "adapters" / "lerobot" / "README.md").read_text(encoding="utf-8")
    section = doc.split("\n## The simulator's upstream: SO-ARM100\n", 1)[1].split("\n## ", 1)[0]
    verified, unverified = section.split("\n### UNVERIFIED (", 1)
    tables = {"VERIFIED": verified.split("\n### VERIFIED (", 1)[1], "UNVERIFIED": unverified}
    missing = [
        f"{ref.status} {ref.name}"
        for ref in so_arm100.all_refs()
        if f"\n| `{ref.name}` |" not in tables[ref.status]
    ]
    assert not missing, (
        f"docs/adapters/lerobot/README.md has no row for these SO-ARM100 refs: {missing}"
    )
    assert so_arm100.PIN[:7] in section and so_arm100.READ_ON in section
    assert "fetched at run time and never shipped" in section  # the honesty label
    assert "QUACKD_LEROBOT_SIM_ASSETS" in section


def test_adapter_doc_lists_every_policy_upstream_ref() -> None:
    """LeRobot's policy names are read at another pin than the arm's, the release a policy
    server installs, in `quackd_lerobot.policy.upstream_api`, and the arm's page carries them
    in a section of their own. As with the simulator's, each ref needs a row of its own in the
    table for its status, because a name found anywhere on the page is not enough: the moved
    rows used to sit in the arm's own tables, and a leftover there would keep a looser check
    green."""
    from quackd_lerobot.policy import upstream_api as policies

    doc = (REPO / "docs" / "adapters" / "lerobot" / "README.md").read_text(encoding="utf-8")
    heading = f"\n## The policies' upstream: LeRobot {policies.VERSION}\n"
    section = doc.split(heading, 1)[1].split("\n## ", 1)[0]
    verified, unverified = section.split("\n### UNVERIFIED (", 1)
    tables = {"VERIFIED": verified.split("\n### VERIFIED (", 1)[1], "UNVERIFIED": unverified}
    missing = [
        f"{ref.status} {ref.name}"
        for ref in policies.all_refs()
        if f"\n| `{ref.name}` |" not in tables[ref.status]
    ]
    assert not missing, (
        f"docs/adapters/lerobot/README.md has no row for these policy refs: {missing}"
    )
    assert policies.PIN[:7] in section and policies.READ_ON in section
    # The honesty label. It went on saying no trained checkpoint had ever been loaded after the
    # laptop's server had loaded trained ones, so it says where none has been loaded instead.
    assert "No trained checkpoint has been loaded by quackd in CI or on a GPU" in section
    arm = doc.split("\n## Upstream API\n", 1)[1].split("\n## ", 1)[0]
    stale = [ref.name for ref in policies.all_refs() if f"\n| `{ref.name}` |" in arm]
    assert not stale, f"the arm's own tables still carry policy rows: {stale}"


def test_readme_promises() -> None:
    for needle in (
        "not affiliated with or endorsed by Pollen Robotics",
        "claude mcp add quackd",
        "dr-eureka",
        "github.com/pollen-robotics/microduck_rl",
        "--goal",
        "--llm fake",
        "biped",
        "pronounced",
        "One CLI for all your robots",
        "flock-hello",
        "quackd flock create",
        "Non goals for now",
        "--llm ollama",
        "quackd list-models",
        "docs/guides/local-llms.md",
        "| Local models (",
        "--flock",
        "flock-kick",
        "docs/guides/flock.md",
        "--no-log",
        "QUACKD_LOG",
    ):
        assert needle in README, needle
    # every vendor, from the code rather than a list here, so a vendor cannot be added to quackd
    # and left out of the one table that tells anyone it exists
    for name in CLOUD_NAMES:
        assert f"--llm {name}" in README, f"the README never shows --llm {name}"
        assert f"`{KEY_ENV[name]}`" in README, f"the README never names {KEY_ENV[name]}"
    assert "quadruped" not in README.lower()
    for hype in ("revolutionary", "world's first", "fully autonomous", "swarm intelligence"):
        assert hype not in README.lower(), hype


def test_the_readme_defaults_row_names_every_catalogue_default() -> None:
    """A default that is written down twice is a default that goes stale in one of them.

    This cannot stop the row being wrong the day a vendor moves, but it can stop the row being
    wrong the day quackd itself moves, which is what happened to `gpt-5`, `grok-4` and
    `gemini-2.5-pro`: the code changed under a sentence nobody re-read."""
    row = next((line for line in README.splitlines() if line.startswith("| Model |")), None)
    assert row, "the README's Configuration table has no Model row"
    for name in CLOUD_NAMES:
        default = default_model_for(name)
        assert f"`{default}`" in row, f"the Model row does not name {name}'s default, {default}"


def test_every_provider_key_is_named_where_keys_are_configured() -> None:
    """A vendor whose key variable is only in the source is a vendor nobody can authenticate."""
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    for name in CLOUD_NAMES:
        assert f"\n{KEY_ENV[name]}=" in env_example, f".env.example has no {KEY_ENV[name]} line"


def test_the_catalogue_is_documented_where_it_is_configured() -> None:
    """The same rule the log is held to, for the thing that now decides every run's model."""
    for path, needles in (
        ("README.md", ("quackd list-models", "catalogue")),
        ("docs/faq.md", ("quackd list-models", "catalogue")),
        (".env.example", ("QUACKD_LLM", "list-models")),
        ("docs/guides/local-llms.md", ("catalogue",)),
        # the one place the answer is that there is no answer, which is worth saying out loud
        ("docs/guides/mcp.md", ("selects no model", "QUACKD_LLM")),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"


#: The one dash the README is allowed: the leading one of a blockquote attribution,
#: `> — Name, Month Year`. That dash is the convention for signing a quote, not punctuation
#: inside a sentence, which is what the rule below is actually about. Only the prefix is
#: exempt. The rest of the line is held to the same standard as any other prose.
ATTRIBUTION_PREFIX = "> — "


def test_readme_punctuation_style() -> None:
    """House style: no semicolons and no dashes used as punctuation (em/en dash, ' - ').

    Fenced code blocks are exempt (YAML lists, shell comments, JSON are what they are), and
    so is the leading dash of a blockquote attribution (see ATTRIBUTION_PREFIX)."""
    prose = re.sub(r"```.*?```", "", README, flags=re.S)
    for i, line in enumerate(prose.splitlines(), 1):
        checked = line[len(ATTRIBUTION_PREFIX) :] if line.startswith(ATTRIBUTION_PREFIX) else line
        assert ";" not in checked, f"README:{i}: semicolon"
        for dash in ("—", "–", " - "):  # noqa: RUF001  (em dash, en dash, spaced hyphen)
            assert dash not in checked, f"README:{i}: dash punctuation {dash!r}"


def test_readme_ends_with_license_section() -> None:
    prose = re.sub(r"```.*?```", "", README, flags=re.S)  # ignore headings inside code blocks
    headings = re.findall(r"^## (.+)$", prose, flags=re.M)
    assert headings[-1] == "License", headings
    # a blank line (<br>) before every section, for breathing room on GitHub
    assert prose.count("<br>\n\n## ") == len(headings), "every H2 needs a <br> before it"


def test_readme_images_are_absolute_and_exist() -> None:
    srcs = re.findall(r'<img[^>]+src="([^"]+)"', README) + re.findall(
        r"!\[[^\]]*\]\(([^)\s]+)", README
    )
    assert srcs, "README has no images"
    raw = "https://raw.githubusercontent.com/rokbenko/quackd/main/"
    for src in srcs:
        assert src.startswith("https://"), f"relative image breaks on PyPI: {src}"
        if src.startswith(raw):
            path = src[len(raw) :].split("?", 1)[0]  # ?v=N busts GitHub's image cache
            assert (REPO / path).exists(), f"missing asset {src}"


def test_readme_verbs_match_registry() -> None:
    for name in default_registry().names():
        assert f"`{name}`" in README, f"README does not mention verb {name}"


def _emitted_kinds(*modules: str) -> set[str]:
    """Every literal event kind those modules emit, read out of the source. A kind nobody
    documented is a kind nobody knows to look for, and only the code knows them all."""
    import ast

    kinds: set[str] = set()
    for name in modules:
        tree = ast.parse((REPO / "quackd" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else None
            if called in ("emit", "write", "_emit", "_event") and isinstance(
                node.args[0], ast.Constant
            ):
                value = node.args[0].value
                if isinstance(value, str) and value.islower():
                    kinds.add(value)
    return kinds


def test_the_docs_describe_every_log_event_the_code_emits() -> None:
    """architecture.md is the one place that enumerates the transcript, so it is the one
    place this can go stale."""
    # the three modules that write a *run* transcript. The flock keeps its own `flock.jsonl`
    # (docs/guides/flock.md) and the MCP server's two envelope kinds are documented in
    # docs/guides/mcp.md.
    emitted = _emitted_kinds("agent/loop.py", "safety.py", "log.py")
    doc = (REPO / "docs" / "concepts" / "architecture.md").read_text(encoding="utf-8")
    missing = [kind for kind in sorted(emitted) if f"`{kind}`" not in doc]
    assert not missing, f"docs/concepts/architecture.md does not describe: {missing}"


def test_the_mcp_doc_describes_the_envelope_the_server_puts_round_a_call() -> None:
    """A model reading a tool result sees the server's own kinds first and last. They belong
    in the page the model's operator reads, not only in the one about the run loop."""
    emitted = _emitted_kinds("mcp_server.py")
    doc = (REPO / "docs" / "guides" / "mcp.md").read_text(encoding="utf-8")
    architecture = (REPO / "docs" / "concepts" / "architecture.md").read_text(encoding="utf-8")
    missing = [k for k in sorted(emitted) if f"`{k}`" not in doc and f"`{k}`" not in architecture]
    assert not missing, f"neither docs/guides/mcp.md nor architecture.md describes: {missing}"


def test_the_docs_name_every_gate_the_code_can_fire() -> None:
    """A gate is the difference between a refusal you can act on and an `ok: false` you
    cannot, so every one of them has to be findable by name in the docs."""
    import ast

    gates: set[str] = set()
    for name in ("safety.py", "mcp_server.py"):
        tree = ast.parse((REPO / "quackd" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (fn.attr if isinstance(fn, ast.Attribute) else None) not in ("emit", "_emit"):
                continue
            if not node.args or getattr(node.args[0], "value", None) != "gate":
                continue
            for kw in node.keywords:
                if kw.arg == "gate" and isinstance(kw.value, ast.Constant):
                    gates.add(str(kw.value.value))
    assert gates, "no gate names found: the reader stopped seeing what the code emits"
    docs = "".join(
        (REPO / "docs" / name).read_text(encoding="utf-8")
        for name in ("concepts/architecture.md", "guides/mcp.md")
    )
    missing = sorted(g for g in gates if f"`{g}`" not in docs)
    assert not missing, f"architecture.md and mcp.md name no gate called: {missing}"


def test_extra_body_is_documented_where_it_is_configured() -> None:
    """A knob nobody can find is a knob nobody has. This one is worse than most to discover by
    reading the source, because the field it carries belongs to the server rather than to
    quackd, so the name to search for is never in this repository at all."""
    for path, needles in (
        ("README.md", ("--extra-body", "QUACKD_EXTRA_BODY")),
        (
            "docs/guides/local-llms.md",
            (
                "--extra-body",
                "QUACKD_EXTRA_BODY",
                "chat_template_kwargs",
                # the serve-time way round, so the docs do not imply the client is the only one
                "--default-chat-template-kwargs",
            ),
        ),
        ("docs/faq.md", ("--extra-body",)),
        (".env.example", ("QUACKD_EXTRA_BODY", "chat_template_kwargs")),
        # the page has no such door, and its own list of differences is where that is recorded
        ("web/README.md", ("extra_body",)),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"


def test_the_log_is_documented_where_it_is_configured() -> None:
    for path, needles in (
        (
            "docs/concepts/architecture.md",
            (
                "## Log",
                "--no-log",
                "QUACKD_LOG",
                "QUACKD_LOG_PROMPT",
                "QUACKD_LOG_THINKING",
            ),
        ),
        ("docs/guides/mcp.md", ("log", "--no-log", "QUACKD_LOG")),
        ("docs/concepts/safety.md", ("--dry-run", "dry_run")),
        (".env.example", ("QUACKD_LOG", "QUACKD_LOG_THINKING", "QUACKD_LOG_PROMPT")),
        ("docs/guides/flock.md", ("log", "--no-log")),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"


def test_the_price_of_a_run_is_documented_where_it_is_configured() -> None:
    """The same rule the log and the catalogue are held to, for the thing that decides
    whether a run's cost counter reads a number or `cost unpriced`.

    A rate is the one knob here somebody only goes looking for after a bill, so it has to be
    findable from the page they are already on: the README's usage table for the flag, the
    architecture page for the order the three sources are tried in, `.env.example` for the
    variable, and the decision LLM hub for the stepper's own rate, which is a separate variable
    because it prices a separate vendor's tokens.

    The `.env.example` needles carry their `=` on purpose. `QUACKD_PRICE` is also spelled in
    the prose above the stepper's variable ("the same syntax as QUACKD_PRICE above"), so a
    bare substring check would still pass with the line that actually sets it deleted."""
    for path, needles in (
        ("README.md", ("--price", "--run-name")),
        ("docs/concepts/architecture.md", ("QUACKD_PRICE", "--price", "--run-name")),
        ("docs/guides/decision-llms/README.md", ("QUACKD_DECISION_PRICE",)),
        (".env.example", ("QUACKD_PRICE=", "QUACKD_DECISION_PRICE=")),
        # where somebody lands who has already been surprised by a figure, or by its absence
        ("docs/faq.md", ("--price", "--run-name", "unpriced")),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"


def test_the_decision_llm_is_documented_where_it_is_configured() -> None:
    """The same rule again, for the three flags and the three variables that decide whether a
    discrete stepper answers a turn before the model is ever called.

    There are many decision LLMs now rather than one, so the names a reader has to be able to
    find are not a vendor's: they are `--decision-llm` and `--decision-mode` on the front page,
    because that is where somebody decides whether to read any further, and on the page itself
    the address for a server quackd has never heard of, the two extras that install the two
    ways of reaching one, and the entry point group a third party publishes into.

    `TYPESAFE_API_KEY=` carries its `=` because the key is also named in the prose around it,
    and a key nobody can set is a hosted model nobody can run."""
    for path, needles in (
        ("README.md", ("--decision-llm", "--decision-mode")),
        (
            "docs/guides/decision-llms/README.md",
            ("--decision-url", "quackd[decision]", "quackd[laya]", "quackd.decision_llms"),
        ),
        (
            ".env.example",
            (
                "QUACKD_DECISION_LLM",
                "QUACKD_DECISION_MODE",
                "QUACKD_DECISION_URL",
                "TYPESAFE_API_KEY=",
            ),
        ),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"


def test_task_pictures_are_documented_where_they_are_configured() -> None:
    """A flag whose whole point is that the model can see the thing has to be findable by
    somebody who has the thing and does not know the flag exists. The README quickstart is
    where an arm owner starts, so it is named there as well as in the reference pages."""
    for path, needles in (
        ("README.md", ("--image", "--vision")),
        ("docs/adapters/lerobot/first-run.md", ("--image", "--vision")),
        ("docs/guides/local-llms.md", ("--image", "--vision")),
        ("docs/adapters/lerobot/README.md", ("--image",)),
        # the pictures land in the run directory, so the page that draws that directory says so
        ("docs/concepts/architecture.md", ("images/",)),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"


def test_the_policy_server_flags_are_documented_where_they_are_configured() -> None:
    """`--policy-url` names the server a LeRobot arm hands its segments to, on `run`,
    `preflight` and `serve-mcp`, and a flag nobody can find is a policy nobody points the arm at.
    The front page names both flags, in the usage rows of all three commands and in the
    Configuration table, the MCP page names them for `serve-mcp`, the arm's page says what a run
    does with one, and `.env.example` names the one variable there is. That is the token's: the
    address has none on purpose, so the file must never grow a line for it. `--accept-other-frame`
    goes with them on all three, and every page that names it, the safety page among them, says
    the goals of the policy it lets in are still clipped to the arm's travel."""
    from quackd.cli import app

    callbacks = {
        (c.name or c.callback.__name__).replace("_", "-"): c.callback
        for c in app.registered_commands
        if c.callback is not None
    }
    for name in ("run", "preflight", "serve-mcp"):
        params = callbacks[name].__code__.co_varnames
        assert "policy_url" in params and "policy_token" in params, name
        assert "accept_other_frame" in params, name
    rows = [line for line in README.splitlines() if line.startswith("| `quackd ")]
    for name in ("run", "preflight", "serve-mcp"):
        row = next(line for line in rows if line.startswith(f"| `quackd {name}"))
        assert "--policy-url" in row, f"the README's {name} row does not name --policy-url"
        assert "--accept-other-frame" in row, f"the README's {name} row does not name it"
    frame = "--accept-other-frame"
    for path, needles in (
        ("README.md", ("| Policy |", "--policy-token", "QUACKD_POLICY_TOKEN", frame)),
        (
            "docs/guides/mcp.md",
            ("--policy-url", "--policy-token", "QUACKD_POLICY_TOKEN", "--yes", frame),
        ),
        (
            "docs/adapters/lerobot/README.md",
            ("--policy-url", "--policy-token", "QUACKD_POLICY_TOKEN", frame),
        ),
        ("docs/concepts/safety.md", (frame, "clipped")),
        ("docs/adr/0048-policies-are-the-arms-executor.md", (frame,)),
        (".env.example", ("QUACKD_POLICY_TOKEN=",)),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "QUACKD_POLICY_URL" not in env_example, "the policy server's address has no variable"


def test_the_controller_is_documented_where_it_is_configured() -> None:
    """`--controller vla` takes the model out of a run and leaves its success to a person, so
    somebody choosing it has to find what it asks of them: the front page's `run` row and its
    Policy row, the arm's page where the policy server is, the safety page that says who the
    record says was asked (a `judge` prompt), the page that draws the record, and the MCP page,
    which says why `serve-mcp` refuses it."""
    from quackd.cli import app

    callbacks = {
        (c.name or c.callback.__name__).replace("_", "-"): c.callback
        for c in app.registered_commands
        if c.callback is not None
    }
    assert "controller" in callbacks["run"].__code__.co_varnames
    rows = [line for line in README.splitlines() if line.startswith("| `quackd ")]
    for name in ("run", "serve-mcp"):
        row = next(line for line in rows if line.startswith(f"| `quackd {name}"))
        assert "--controller" in row, f"the README's {name} row does not name --controller"
    policy_row = next(line for line in README.splitlines() if line.startswith("| Policy |"))
    assert "--controller vla" in policy_row
    for path, needles in (
        ("docs/adapters/lerobot/README.md", ("--controller vla", "Did the arm do it?", "`judge`")),
        ("docs/concepts/safety.md", ("--controller vla", "`judge`")),
        ("docs/concepts/architecture.md", ("`judge`", "providers/vla.py")),
        ("docs/guides/mcp.md", ("--controller",)),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"


def test_every_question_the_record_keeps_is_named_where_the_record_is_explained() -> None:
    """A `prompt` event's `what` is one of the questions the loop puts through
    `_ask_recorded`, or the confirm gate's, which the executor writes itself. The safety page
    lists them and counts them, and the architecture page's `prompt` row lists them. A kind the
    code gained and the pages did not tells a reader the record holds fewer questions than it
    does: `release` went missing from the safety page's list this way, and its count with it."""
    code = (REPO / "quackd" / "agent" / "loop.py").read_text(encoding="utf-8")
    gate = (REPO / "quackd" / "safety.py").read_text(encoding="utf-8")
    kinds = set(re.findall(r'_ask_recorded\(\s*"(\w+)"', code)) | set(
        re.findall(r'"prompt",\s*what="(\w+)"', gate)
    )
    assert {"confirm", "decide", "release", "judge"} <= kinds, kinds
    safety = (REPO / "docs" / "concepts" / "safety.md").read_text(encoding="utf-8")
    listed = safety.split("## Who the record says was asked\n\n", 1)[1].split("\n\n", 1)[0]
    counted = {4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}[len(kinds)]
    assert f"Those {counted} are" in " ".join(listed.split()), "safety.md counts them wrong"
    row = next(
        line
        for line in (REPO / "docs" / "concepts" / "architecture.md")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("| `prompt` |")
    )
    for kind in sorted(kinds):
        assert f"`{kind}`" in listed, (
            f"docs/concepts/safety.md's list of prompts leaves out {kind!r}"
        )
        assert f"`{kind}`" in row, f"docs/concepts/architecture.md's prompt row leaves out {kind!r}"


def test_every_key_a_runs_policy_block_holds_is_named_where_the_record_is_explained() -> None:
    """`run_start`'s `policy` block is what `RemoteRunner.record()` says about the server, and
    the summary's block starts with it, so the architecture page's `run_start` row names each
    of its keys. `accept_other_frame` went missing from that row when `--accept-other-frame`
    added it, and a reader of a run taken under the override would have met a key no page
    explained."""
    from quackd_lerobot.policy import server
    from quackd_lerobot.policy.client import RemoteRunner
    from quackd_lerobot.verbs import JOINTS

    _, info = server.served_policy(server.ServeOptions(policy="scripted:hold"))
    runner = RemoteRunner("http://127.0.0.1:1", token="0" * 64, motors=JOINTS)
    runner.info = info  # as the connect heard it, with nothing asked
    row = next(
        line
        for line in (REPO / "docs" / "concepts" / "architecture.md")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("| `run_start` |")
    )
    said = row.split("A run with `--policy-url` adds `policy`:", 1)[1]
    for key in sorted(runner.record()):
        assert f"`{key}`" in said, (
            f"docs/concepts/architecture.md's run_start row leaves out {key!r}"
        )


def test_the_hand_placed_start_is_documented_where_it_is_configured() -> None:
    """The one place quackd takes torque off a robot. Somebody about to hold an arm while it is
    released should be able to find what happens next in the page they are already reading, and
    the safety page has to carry it whether or not they ever open the arm's own."""
    for path, needles in (
        ("README.md", ("--by-hand",)),
        ("docs/adapters/lerobot/first-run.md", ("--by-hand",)),
        ("docs/adapters/lerobot/README.md", ("--by-hand",)),
        ("docs/adapters/lerobot/hardware-checklist.md", ("--by-hand",)),
        ("docs/concepts/safety.md", ("--by-hand", "torque")),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"


def test_both_ways_a_run_can_start_are_offered_together() -> None:
    """Rok asked for this specifically: the default and the hand-placed start are two options a
    reader chooses between, so the pages that teach the arm present them side by side rather
    than leaving the second one to a reference page nobody reaches.

    Checked as proximity rather than as wording, because the wording is prose and will be
    rewritten: what must survive a rewrite is that `--by-hand` is explained on the same page as
    the rest pose it departs from, and near it."""
    for path in ("README.md", "docs/adapters/lerobot/first-run.md"):
        lines = (REPO / path).read_text(encoding="utf-8").splitlines()
        rest = [i for i, line in enumerate(lines) if "rest-pose" in line or "rest pose" in line]
        hand = [i for i, line in enumerate(lines) if "--by-hand" in line]
        assert rest and hand, f"{path} must name both the rest pose and --by-hand"
        gap = min(abs(h - r) for h in hand for r in rest)
        assert gap < 60, (
            f"{path} explains --by-hand {gap} lines from the nearest mention of the rest pose; "
            "the two starts are a choice and belong next to each other"
        )


def test_mcp_doc_lists_every_tool() -> None:
    from quackd.mcp_server import TOOL_NAMES

    doc = (REPO / "docs" / "guides" / "mcp.md").read_text(encoding="utf-8")
    missing = [name for name in TOOL_NAMES if f"`{name}" not in doc]
    assert not missing, f"docs/guides/mcp.md is missing: {missing}"
    assert "--robots" in doc and "--robots" in README


def test_mcp_json_is_a_stdio_server() -> None:
    from quackd.adapters.factory import BACKENDS

    cfg = json.loads((REPO / ".mcp.json").read_text(encoding="utf-8"))
    server = cfg["mcpServers"]["quackd"]
    assert "command" in server and "type" not in server
    args = server["args"]
    assert "serve-mcp" in args
    # the robot it names has to exist, or opening the repo greets you with a stack trace
    adapter, _, backend = args[args.index("--robot") + 1].partition(":")
    assert backend in BACKENDS.get(adapter, ()), f".mcp.json names {adapter}:{backend}"
    # This is the repo's own config, so it runs the code you are editing, not the release.
    # `uv run` alone re-syncs on launch and loses to the running server's hold on
    # Scripts/quackd.exe on Windows, so the repo pins --no-sync. Users get `uvx`
    # (docs/guides/mcp.md).
    if server["command"] == "uv" and args[0] == "run":
        assert "--no-sync" in args, "uv run re-syncs and fights the server it is launching"


# ── counts, so a release cannot ship a number the code disagrees with ────────────────────

_NUMBER_WORDS = {
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    # beyond the count quackd has today, so the day it reaches one of these the guards below
    # still have a word for it, and a stale "thirteen" can be seen before then
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
}


def _prose(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def _living_docs() -> list[Path]:
    """Every document that describes quackd as it is now.

    CHANGELOG, PLAN and the ADRs and design notes are excluded: they record what was true
    when they were written, and correcting a number in them would be falsifying history."""
    return [
        path
        for path in sorted(REPO.glob("*.md")) + sorted((REPO / "docs").rglob("*.md"))
        if path.name not in ("CHANGELOG.md", "PLAN.md") and not {"design", "adr"} & set(path.parts)
    ]


@pytest.mark.parametrize(
    "name", ["README.md", "docs/adapters/writing-an-adapter.md", "docs/faq.md", "LAUNCH.md"]
)
def test_no_document_claims_the_wrong_number_of_adapters(name: str) -> None:
    """Half of the 0.5 documentation audit was stale counts that no test could see.

    "four adapters" was written in six places while five shipped. This does not police
    prose, only the specific claim that quackd has N adapters."""
    from quackd.adapters.factory import ADAPTER_NAMES

    right = _NUMBER_WORDS[len(ADAPTER_NAMES)]
    prose = _prose((REPO / name).read_text(encoding="utf-8")).lower()
    # only claim shapes that are unambiguously about how many adapters exist. "two robots
    # under one contract" is a heterogeneous flock, not a count of adapters.
    shapes = ("{w} adapters", "{w} robots supported", "{w} robots today")
    for count, word in _NUMBER_WORDS.items():
        if count == len(ADAPTER_NAMES):
            continue
        for shape in shapes:
            claim = shape.format(w=word)
            assert claim not in prose, (
                f"{name} says {claim!r}; quackd ships {right} ({', '.join(ADAPTER_NAMES)})"
            )


def test_no_living_document_claims_the_wrong_number_of_cloud_providers() -> None:
    """The same failure the adapter count already has a guard for, one layer up.

    "The four cloud providers see the camera frame as an image" was written once and was true
    for a year. Twelve is a number that will move again, and every document that spells it is a
    document nobody will re-read on the day it does."""
    right = _NUMBER_WORDS[len(CLOUD_NAMES)]
    shapes = ("{w} cloud providers", "{w} cloud vendors", "{w} vendors")
    # `_living_docs` stops at `docs/`, and the browser demo keeps its own count of the same
    # vendors in its README and in the header of the file that decides which it offers. Those
    # were both wrong the first time this list was written, which is the argument for including
    # them: a count is only checkable where somebody thought to look.
    web = [REPO / "web" / "README.md", REPO / "web" / "src" / "providers.js"]
    for path in [*_living_docs(), *web]:
        prose = _prose(path.read_text(encoding="utf-8")).lower()
        for count, word in _NUMBER_WORDS.items():
            if count == len(CLOUD_NAMES):
                continue
            for shape in shapes:
                claim = shape.format(w=word)
                assert claim not in prose, (
                    f"{path.name} says {claim!r}; quackd has {right} ({', '.join(CLOUD_NAMES)})"
                )


# ── OpenRouter's page, and the code it quotes ───────────────────────────────────────────

OPENROUTER_PAGE = REPO / "docs" / "guides" / "openrouter.md"


def _rate(value: float) -> str:
    """A catalogue rate the way the page writes it: 2.0 as 2, every other digit kept."""
    return str(value).removesuffix(".0")


def test_the_openrouter_page_agrees_with_the_code() -> None:
    """The guide quotes the code: the rows, how each is asked, its rates, the key, the extra,
    the address, both headers and every refusal. Each is read back off the page here, so a row
    or a reason that changes in one place and not the other fails the suite."""
    from quackd.agent.providers.catalogue import OPENROUTER_PRICES_CHECKED, models_for
    from quackd.agent.providers.factory import EXTRA_FOR, KEY_ENV, OPENROUTER_REFUSED
    from quackd.agent.providers.openrouter import ATTRIBUTION, BASE_URL

    page = OPENROUTER_PAGE.read_text(encoding="utf-8")
    rows = models_for("openrouter")
    for m in rows:
        assert m.price is not None
        asked = "required" if m.forced_tools else "auto"
        rates = [_rate(r) for r in (m.price.input, m.price.output, m.price.cache_read or 0.0)]
        write = "none listed" if m.price.cache_write is None else _rate(m.price.cache_write)
        line = (
            f'| `{m.id}` | `tool_choice: "{asked}"` | {"yes" if m.vision else "no"} '
            f"| {' / '.join([*rates, write])} |"
        )
        assert line in page, f"the page's row for {m.id} is not\n{line}"
    assert f"`--llm openrouter` runs `{rows[0].id}`" in page
    assert f"`{KEY_ENV['openrouter']}`" in page and f"`quackd[{EXTRA_FOR['openrouter']}]`" in page
    assert BASE_URL in page and OPENROUTER_PRICES_CHECKED in page
    for name, value in ATTRIBUTION.items():
        assert f"`{name}: {value}`" in page
    for shape, why in OPENROUTER_REFUSED.items():
        assert f"| {why} |" in page, f"the page does not give the reason for {shape}"
    assert "\n## VERIFIED" in page and "\n## UNVERIFIED" in page
    assert "**Nothing here has ever answered a real robot.**" in page
    assert "**No OpenRouter model has answered a real quackd request.**" in page


def test_every_page_that_offers_openrouter_says_none_has_answered() -> None:
    """In the same words everywhere, until somebody runs one: a vendor tested only against a
    stand-in reads exactly like one that has been flown, unless a page says otherwise."""
    sentence = "no openrouter model has answered a real quackd request"
    pages = [
        REPO / "README.md",
        REPO / "docs" / "faq.md",
        OPENROUTER_PAGE,
        REPO / "web" / "README.md",
        REPO / "web" / "src" / "providers.js",
        *sorted((REPO / "docs" / "adr").glob("0050-*.md")),
    ]
    assert len(pages) == 6, "ADR-0050 is missing"
    for page in pages:
        text = " ".join(page.read_text(encoding="utf-8").split()).lower()
        assert sentence in text, f"{page.relative_to(REPO)} does not say so"
    assert sentence in _one_line(_release_note("0.17.0")), "0.17.0's release note does not say so"


def test_the_pypi_summary_names_every_robot() -> None:
    """The one sentence on the PyPI page shipped 0.5 without the Open Duck Mini in it.

    That line and the keywords are how someone searching for their robot finds quackd, and
    nothing in the test suite had ever read pyproject.toml."""
    import tomllib

    from quackd.adapters.factory import ADAPTER_NAMES

    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    haystack = (project["description"] + " " + " ".join(project["keywords"])).lower()
    # the summary names bodies, not adapter identifiers: microduck -> "microduck",
    # open_duck -> "open duck", rosbridge -> "ros"
    for adapter in ADAPTER_NAMES:
        needle = {"open_duck": "open duck", "rosbridge": "ros"}.get(adapter, adapter)
        assert needle in haystack, (
            f"pyproject describes quackd without {needle!r}; it ships a {adapter} adapter"
        )


def test_the_readme_starter_table_lists_every_bundled_duck() -> None:
    """`open-duck-lookout` shipped in 0.5 and appeared nowhere in the README."""
    from quackd.duckfile.parser import list_bundled_ducks

    missing = [p.stem for p in list_bundled_ducks() if f"`{p.stem}`" not in README]
    assert not missing, f"README does not mention: {missing}"


def test_no_living_document_quotes_an_exact_test_count() -> None:
    """CONTRIBUTING said 445 while 451 ran, and no test could see it.

    Collecting the suite to check a number would cost every run eight seconds for a fact
    nobody reads, so the rule is simply not to quote one outside the history files, where
    a count is a record of what was true on the day and must not be rewritten."""
    for path in _living_docs():
        for claim in re.findall(r"\b\d{2,4} tests?\b", _prose(path.read_text(encoding="utf-8"))):
            pytest.fail(f"{path.name} quotes {claim!r}; say 'the whole suite' and let CI count")


#: What 0.4 and 0.11 each kept alive for one release and the release after removed. A living
#: document may not spell any of them, and neither may the source that prints to a terminal.
#: The history files keep them all: that is what a changelog and an ADR are for.
_REMOVED_SPELLINGS = ("--transport", "--trace", "--no-trace", "quackd trace")

#: The same, for names that are read rather than typed. Checked in every source file but
#: `quackd/cli.py`, whose warner has to spell one in order to say it is not read any more;
#: that it really is not read is `tests/test_cli_log.py`'s to prove.
_REMOVED_ENV_NAMES = ("QUACKD_TRACE",)

#: How each release phrased its promise. Once the removal has happened, a document still
#: making the promise is describing a version of quackd that no longer exists. "gone in 0.12"
#: is here because the README said it that way and none of the others would have caught it.
_KEPT_PROMISES = (
    "go away in 0.5",
    "gone in 0.5",
    "are removed in 0.5",
    "goes in 0.12",
    "go in 0.12",
    "gone in 0.12",
    "until 0.12",
    "removed in 0.12",
    "stop working in 0.12",
    "stop being read in 0.12",
    # docs/guides/jetson.md said the doctor section "ships in quackd 0.13.0" and told readers to
    # install from `main` until then. Nothing here could see that go stale on the day it
    # shipped, which is the day this list exists for.
    "ships in quackd 0.13",
    "until 0.13",
)


def test_no_document_still_promises_a_removal_that_happened() -> None:
    """0.4 said `--transport` and the duck_* tools go in 0.5, and 0.11 said `quackd trace`,
    the two `--trace` flag pairs and the three `QUACKD_TRACE*` variables go in 0.12. Both
    releases kept their promise, so nothing should still be making either one, and nothing
    should still be offering what went.

    A promise about a release that has NOT happened is a different thing and is allowed. What
    this guards against is the stale half: a document still describing a removal that is
    already behind us, which is how a reader ends up typing a spelling that stopped working
    two releases ago because the page they read still offered it.

    `--no-trace` is a needle of its own, because it does not contain `--trace`."""
    from quackd.mcp_server import TOOL_NAMES

    assert not [n for n in TOOL_NAMES if n.startswith("duck_")]
    # a table title and a TransportError still told users to pass it, and no doc test could
    # see a Python string, so the same rule now covers the source that prints to a terminal.
    # `"trace"` was how the retired subcommand was registered; nothing else quotes the word.
    for src in sorted((REPO / "quackd").rglob("*.py")):
        text = src.read_text(encoding="utf-8")
        where = f"quackd/{src.relative_to(REPO / 'quackd').as_posix()}"
        for spelling in (*_REMOVED_SPELLINGS, '"trace"'):
            assert spelling not in text, f"{where} still offers {spelling}"
        if src.name != "cli.py":
            for name in _REMOVED_ENV_NAMES:
                assert name not in text, f"{where} still reads {name}"
    # `.env.example` is not a `*.md` and so not a living document by the glob, and it is the
    # one file whose whole job is to list the names a reader may set.
    for path in [*_living_docs(), REPO / ".env.example"]:
        text = _prose(path.read_text(encoding="utf-8"))
        for spelling in (*_REMOVED_SPELLINGS, *_REMOVED_ENV_NAMES):
            assert spelling not in text, f"{path.name} still documents {spelling}"
        for promise in _KEPT_PROMISES:
            assert promise not in text, f"{path.name} still promises {promise!r}, which happened"


#: The one-liner ADR-0002 minted and ADR-0035 retired. ADR-0002 said "one-liner everywhere",
#: and everywhere is what happened: the README, the PyPI summary, the `quackd --help` banner,
#: the package docstring and the browser demo's title all carried a copy of it, and nothing
#: counted them. quackd is the CLI and the LLM is the brain now, so "an LLM for a brain" is
#: fine and only the old claim, that quackd itself is a brain for one small robot, is not.
_RETIRED_TAGLINES = (
    "Give your Microduck a brain",
    "Give your small robot a brain",
    "Give a Microduck a brain",
    "a brain for any small robot",
    "brain daemon Microduck was missing",
)

#: The three user-facing strings outside the README that carry the replacement, so a revert
#: in one of them cannot pass while the README still reads correctly.
_TAGLINE_SITES = ("pyproject.toml", "quackd/__init__.py", "quackd/cli.py")


def test_the_retired_tagline_is_gone_and_the_new_one_is_everywhere_it_lived() -> None:
    """The history files keep it: they record what was true on the day they were written."""
    # rglob, not glob: the retired sentence also lived in `quackd/agent/prompts.py`'s neighbours,
    # and a guard that reads only the top of the package is a guard with a floor under it
    sources = [
        REPO / "pyproject.toml",
        *(REPO / "quackd").rglob("*.py"),
        *(REPO / "bridge").rglob("*.py"),
        REPO / "web/index.html",
    ]
    for path in _living_docs() + sources:
        text = path.read_text(encoding="utf-8")
        for retired in _RETIRED_TAGLINES:
            assert retired not in text, f"{path.name} still carries the retired tagline {retired!r}"
    for name in _TAGLINE_SITES:
        text = (REPO / name).read_text(encoding="utf-8")
        assert "One CLI for all your robots" in text, f"{name} does not carry the one-liner"


#: Sentences that were true until 2026-09-15 and are not any more. An SO-101 arm ran quackd
#: that day, so a living document that still says nothing ever has is not merely stale, it is
#: telling a reader the opposite of what happened. The history files keep them on purpose:
#: CHANGELOG, PLAN and everything under docs/adr and docs/design record what was true when
#: they were written, which is what `_living_docs()` already excludes.
_RETIRED_HARDWARE_CLAIMS = (
    "no robot of any kind has run quackd",
    "has never run on any of them",
    "nothing in quackd has ever run on a real so-101",
    "never run on an arm",
    "nobody has pointed any pilot at a real webcam",
    "nothing has run on a real robot of any kind",
    "never run against an arm",
    # three more spellings, retired when the README started leading with the arm
    "nothing here has run on hardware",
    "nobody has run it on real hardware",
    "nobody has run `lerobot:real`",
)


def test_no_living_document_still_says_no_robot_has_ever_run_quackd() -> None:
    """One body has run on hardware and six have not, and both halves have to survive edits.

    The hard part of this change was not flipping the status, it was that the old claim was
    spelled seven different ways across a dozen pages, and a revert in any one of them reads
    as authoritative. The user-facing strings are checked too, because `quackd doctor` and
    `list-adapters` print a status line per adapter and those are read more often than a page.
    """
    sources = [
        REPO / "pyproject.toml",
        *(REPO / "quackd").rglob("*.py"),
        *(REPO / "adapters").rglob("*.py"),
        # the issue templates are read by the one person best placed to correct us, and the
        # arm's said "nobody has run this" for two days after somebody had
        *sorted((REPO / ".github" / "ISSUE_TEMPLATE").glob("*.yml")),
    ]
    for path in _living_docs() + sources:
        # compared in lower case: the same claim was capitalised differently per page, which
        # is how "Nothing here has run on hardware" outlived its lower case twin
        text = path.read_text(encoding="utf-8").lower()
        for retired in _RETIRED_HARDWARE_CLAIMS:
            assert retired.lower() not in text, (
                f"{path.relative_to(REPO)} still says {retired!r}; an SO-101 ran quackd on "
                "2026-09-15 (docs/adapters/status.md)"
            )
    # and the other half: the six that have not run must not be quietly promoted with it
    status = (REPO / "docs" / "adapters" / "status.md").read_text(encoding="utf-8")
    assert "2026-09-15" in status, "docs/adapters/status.md does not date the one real run"


def test_the_readme_hero_is_the_real_arm_and_its_caption_says_when_and_who() -> None:
    """The front door's most checkable claim, and the one a well meaning edit would soften.

    Until 2026-09-15 the hero was a render of a duck walked by the scripted pilot, and every
    honest caption said so. It is a phone recording of a real arm under a real model now. The
    needles rather than the wording, so the prose stays free: which file, and that the caption
    dates it, names the model and names the body. And that it does not call itself scripted,
    which is the one word that would make it the old claim again."""
    gifs = re.findall(
        r'src="https://raw\.githubusercontent\.com/rokbenko/quackd/main/(docs/assets/[^"?]+\.gif)',
        README,
    )
    assert gifs, "the README shows no GIF at all"
    assert gifs[0] == "docs/assets/lerobot.gif", (
        f"the README's first GIF is {gifs[0]}. The hero is the recording of the real arm."
    )
    # anchored to the hero's own <p>: an unanchored search walks past a missing caption and
    # binds these needles to the next figure's, so deleting the caption would pass
    block = re.search(r"<p align=\"center\">(?:(?!</p>).)*?lerobot\.gif.*?</p>", README, flags=re.S)
    assert block is not None, 'the hero is not in a <p align="center"> block'
    caption = re.search(r"<sub>(.*?)</sub>", block.group(0), flags=re.S)
    assert caption is not None, "the hero has no caption"
    for needle in ("2026-09-15", "gpt-6-astra", "SO-101"):
        assert needle in caption.group(1), f"the hero caption does not say {needle}"
    assert "scripted" not in caption.group(1).lower(), "the hero is not the scripted pilot"


def test_every_file_in_docs_assets_has_a_row_in_its_catalogue() -> None:
    """`docs/assets/README.md` is where a reader finds out how a picture was made and whether
    a model or a script drove it. An asset with no row is a picture with no provenance, which
    for a recording of a real robot is the difference between evidence and decoration."""
    catalogue = (REPO / "docs" / "assets" / "README.md").read_text(encoding="utf-8")
    for asset in sorted((REPO / "docs" / "assets").iterdir()):
        if not asset.is_file() or asset.suffix == ".py" or asset.name == "README.md":
            continue
        assert f"| `{asset.name}` |" in catalogue, (
            f"docs/assets/{asset.name} has no row in docs/assets/README.md: say what it is "
            "and how it was made, and whether the pilot in it was a model or the script."
        )


def test_the_readme_opens_with_its_name_and_only_the_browser_demo_shows_its_mark() -> None:
    """Until 2026-09-29 the README opened with a duck's head over its title, and a social card
    was built around the same head. It made quackd look like a toy rather than a tool that
    drives a real arm, so both went. The browser demo keeps the head as its own header mark and
    icons in `web/assets/`, and a living document that names one of those files is pointing a
    reader at a logo the project no longer shows anywhere else.

    The names are read from that directory rather than spelled here, so an icon added beside
    them is covered too. The history files may still name them: they record what was true."""
    assert README.startswith("<h1"), (
        "README.md has something above its title. It opens with its name and carries no logo."
    )
    icons = sorted(path.name for path in (REPO / "web" / "assets").iterdir() if path.is_file())
    assert icons, "web/assets has no files, so this test would check nothing."
    for path in _living_docs():
        text = path.read_text(encoding="utf-8")
        for icon in icons:
            assert icon not in text, (
                f"{path.relative_to(REPO).as_posix()} names web/assets/{icon}, the browser "
                "demo's own mark. The README and docs/assets carry no logo: leave it to the demo."
            )


#: Sentences that were true until 2026-09-28, when OpenAI's `gpt-6-sol` flew the arm's
#: simulator on film (`docs/assets/lerobot-sim.gif`). The README carried three of them, in its
#: status table, its limitations and its help wanted, and the change that embedded the film in
#: that same README fixed the assets catalogue's copy and none of these. The last has been
#: false since `lerobot.gif`, a model on the real arm, and was missed then too.
_RETIRED_RECORDING_CLAIMS = (
    "no real model recording has yet been made in any simulator",
    "the only recording in this repository with a model in the loop",
    "every simulator recording here is the scripted pilot",
    "the one recording here with a model in it",
    "none has yet been recorded in any simulator",
    "every *simulator* recording here is driven by the scripted pilot",
    "not an llm, like every other asset here",
)


def test_no_living_document_still_says_no_model_has_been_filmed_in_a_simulator() -> None:
    """A claim about the pictures, spelled once per page, and a page that keeps it says the
    opposite of a figure it may be showing a few screens up.

    The other half holds the catalogue to the same fact, so the two move together: a film
    re-recorded with the scripted pilot takes the model out of its row, and this test then
    says which claims have come true again."""
    catalogue = (REPO / "docs" / "assets" / "README.md").read_text(encoding="utf-8")
    row = re.search(r"^\| `lerobot-sim\.gif` \| (.*?) \|", catalogue, flags=re.MULTILINE)
    assert row is not None, "docs/assets/README.md has no row for lerobot-sim.gif"
    assert "gpt-6-sol" in row.group(1), (
        "the lerobot-sim.gif row no longer names the model that flew it. If the film is the "
        "scripted pilot now, the claims in _RETIRED_RECORDING_CLAIMS are true again: take "
        "them out of this test and put the sentences back where the pages need them."
    )
    for path in _living_docs():
        text = _one_line(path.read_text(encoding="utf-8"))
        for retired in _RETIRED_RECORDING_CLAIMS:
            assert retired not in text, (
                f"{path.relative_to(REPO).as_posix()} still says {retired!r}, and OpenAI's "
                "gpt-6-sol was filmed on the arm's simulator on 2026-09-28 "
                "(docs/assets/lerobot-sim.gif)"
            )


def test_no_living_document_or_user_facing_string_still_says_fleet() -> None:
    """One word for a group of robots, because two words for one idea is two ideas to a reader.

    ADR-0035 retired "fleet" from prose and from help text, and kept it in the code, where
    `Fleet` and `build_fleet_server` are imported by name. The split is the point: this guard
    reads what a person reads, which for `quackd/` means docstrings and help strings rather
    than identifiers, so it checks the CLI's rendered help rather than the source."""
    from typer.testing import CliRunner

    from quackd.cli import app

    for path in _living_docs():
        prose = _prose(path.read_text(encoding="utf-8")).lower()
        assert "fleet" not in prose, f"{path.name} still says fleet"
    # the help a user actually sees, top level and every sub-app, which is where three of the
    # stale ones were: a source grep would have been satisfied by `fleet_from_flags`
    runner = CliRunner()
    for argv in ([], ["robot"], ["flock"], ["memory"], ["run"], ["serve-mcp"], ["validate"]):
        result = runner.invoke(app, [*argv, "--help"])
        # exit code first: a renamed sub-app makes Click print a "No such command" usage page,
        # which contains no "fleet" either, and the assertion below would pass on nothing
        assert result.exit_code == 0, f"`quackd {' '.join(argv)} --help` did not render"
        assert "fleet" not in result.output.lower(), (
            f"`quackd {' '.join(argv)} --help` still says fleet"
        )


def test_no_living_document_claims_the_wrong_number_of_mcp_tools() -> None:
    """The guard above proves nothing still *offers* a removed tool. It could not see a
    document still *describing* one, so architecture.md went through the whole of 0.5 saying
    the server carried the old count plus the duck_* aliases that release deleted, and into
    0.6, which added two more tools. Two README sentences, a --robots help string, a module
    docstring and a test docstring carried the old count for the same reason: nothing counted
    them. This file is scanned too, which is why the stale wordings are described here rather
    than quoted."""
    from quackd.mcp_server import TOOL_NAMES

    right = _NUMBER_WORDS[len(TOOL_NAMES)]
    # 0.5 learned that a doc-only guard cannot see a Python string a user reads: the count
    # was also stale in a `--robots` help text, a module docstring and a test's docstring
    # this file is skipped because it has to spell the wordings it forbids in order to
    # forbid them; every other source file and living document is fair game
    here = Path(__file__).resolve()
    sources = [
        p
        for p in sorted((REPO / "quackd").rglob("*.py")) + sorted((REPO / "tests").glob("*.py"))
        if p.resolve() != here
    ]
    for path in _living_docs() + sources:
        text = path.read_text(encoding="utf-8")
        haystack = (_prose(text) if path.suffix == ".md" else text).lower()
        for count, word in _NUMBER_WORDS.items():
            if count == len(TOOL_NAMES):
                continue
            for shape in (f"{word} `robot_*` tools", f"{word} robot_* tools"):
                assert shape not in haystack, (
                    f"{path.name} says {shape!r}; the server registers {right} "
                    f"({', '.join(TOOL_NAMES)})"
                )
        # anything that still describes the removed aliases as present, in any wording
        for stale in ("duck_* tools kept as aliases", "`duck_*` tools kept as aliases"):
            assert stale not in haystack, f"{path.name} describes the duck_* aliases as present"


# ── the guard that was missing twice ────────────────────────────────────────────────────


def test_the_architecture_diagram_names_every_adapter() -> None:
    """`_prose()` strips fenced blocks before every other doc guard, so the mermaid diagram
    is invisible to all of them by construction.

    That is not hypothetical. `docs/design/memory.md` records 0.6 fixing exactly this defect
    ("the README's architecture diagram listed four adapters and omitted `open_duck` and its
    `bridge` backend, which the adapter-count guard could not see because it reads the phrase
    'N adapters' and not a list"). Nothing was added to catch it, so it came back three
    adapters later. This is that guard.
    """
    from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS

    node = next((line for line in README.splitlines() if 'ADAPTER["robot adapter' in line), None)
    assert node is not None, "the architecture diagram's adapter node has moved or gone"

    # The names as a SET, split on the separator, not as substrings. `"lerobot" in node` is
    # satisfied by the `xlerobot` entry, so a substring check cannot see `lerobot` go missing,
    # which is the one adapter whose name is contained in another's.
    listed = {n.strip() for n in node.split("<br/>")[1].split("·")}
    missing = [name for name in ADAPTER_NAMES if name not in listed]
    assert not missing, f"the architecture diagram does not name: {missing} (has {listed})"
    extra = [name for name in listed if name and name not in ADAPTER_NAMES]
    assert not extra, f"the architecture diagram names adapters that do not exist: {extra}"

    #: Backends are listed by their bare name in that node, so every distinct one must appear.
    kinds = {backend for backends in BACKENDS.values() for backend in backends}
    absent = sorted(k for k in kinds if k not in node)
    assert not absent, f"the architecture diagram does not name the backends: {absent}"


def test_no_fenced_block_names_a_stale_subset_of_the_adapters() -> None:
    """The general form of the same hole: any fenced block that enumerates most of the
    adapters has to enumerate all of them, or it is a list somebody forgot to update."""
    from quackd.adapters.factory import ADAPTER_NAMES

    for doc in [REPO / "README.md", *sorted((REPO / "docs").rglob("*.md"))]:
        text = doc.read_text(encoding="utf-8")
        for block in re.findall(r"```.*?```", text, flags=re.S):
            named = [n for n in ADAPTER_NAMES if n in block]
            if len(named) < len(ADAPTER_NAMES) - 2:
                continue  # not an enumeration, just a couple of examples
            missing = [n for n in ADAPTER_NAMES if n not in block]
            assert not missing, (
                f"{doc.relative_to(REPO)}: a fenced block names {len(named)} adapters "
                f"and omits {missing}"
            )


#: The README's verb table names bodies, not adapter ids, so the mapping is written down.
_VERB_TABLE_ROWS = {
    "microduck": "| Microduck |",
    "lerobot": "| LeRobot arm |",
    "rosbridge": "| rosbridge base |",
    "open_duck": "| Open Duck Mini v2 |",
    "xlerobot": "| XLeRobot |",
    "alohamini": "| AlohaMini |",
    "toddlerbot": "| ToddlerBot |",
}

_CORE_VERBS = frozenset(
    {"observe", "report_state", "stop", "say", "move", "go_to", "search_scan", "approach_and"}
)


async def _implementable(adapter: str) -> set[str]:
    """Every verb this adapter has an implementation for, across all its builds."""

    module = adapter_module(adapter)
    return set(module.implementations())


def test_the_readme_verb_table_has_a_row_per_body_listing_its_real_verbs() -> None:
    """The other list-shaped thing no guard could see.

    `test_readme_verbs_match_registry` only checks that each *core* verb appears somewhere in
    the whole README, so a body could be added with its own verbs and never get a row. Four
    were: Open Duck Mini, XLeRobot, AlohaMini and ToddlerBot all shipped verbs of their own
    with nothing in the table.
    """
    import asyncio

    from quackd.adapters.factory import ADAPTER_NAMES, make_adapter

    assert set(_VERB_TABLE_ROWS) == set(ADAPTER_NAMES), "the row map has drifted from the code"

    offline = {"microduck": "sim2d", "lerobot": "mock", "rosbridge": "mock"}
    for adapter in ADAPTER_NAMES:
        backend = offline.get(adapter, "mock")
        manifest = asyncio.run(make_adapter(f"{adapter}:{backend}", seed=0).connect())
        own = sorted(set(manifest.verb_names()) - _CORE_VERBS)
        prefix = _VERB_TABLE_ROWS[adapter]
        row = next((line for line in README.splitlines() if line.startswith(prefix)), None)
        assert row is not None, f"the verb table has no row for {adapter}"
        if not own:
            continue  # a body with nothing of its own says so in prose
        # The verbs cell only. Searching the whole row lets the description satisfy it, and
        # these descriptions name verbs: the first version of this guard passed happily with
        # two of the ToddlerBot's three verbs deleted from the cell.
        cell = row.split("|")[2]
        missing = [v for v in own if f"`{v}`" not in cell]
        assert not missing, f"the {adapter} row does not list its own verbs: {missing}"
        # And the other direction, which is the drift that happens when a verb is deleted from
        # an adapter and nobody remembers the README.
        listed = {chunk.strip() for chunk in cell.split("`") if chunk.strip()}
        # Against everything the adapter can implement, not just what this build reports: a
        # row may name a verb only some builds have (the ToddlerBot's `grip` needs the gripper
        # variant), but it may never name one the adapter cannot implement at all.
        possible = set(asyncio.run(_implementable(adapter)))
        gone = [v for v in listed if v not in possible and v not in _CORE_VERBS]
        assert not gone, f"the {adapter} row lists verbs the adapter cannot implement: {gone}"


def test_every_body_carries_its_own_numbers_on_its_own_page() -> None:
    """A datasheet is a claim about a real robot, so it belongs where a reader checks it.
    rosbridge is exempt: its numbers come off a bridge and are different every time."""
    from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe

    pages = {
        name: REPO / "docs" / "adapters" / name / "README.md"
        for name in ADAPTER_NAMES
        if name != "rosbridge"
    }
    for adapter, path in pages.items():
        page = path.read_text(encoding="utf-8")
        sheet = describe(RobotSpec(adapter, BACKENDS[adapter][0])).datasheet
        assert sheet is not None
        for label, figure, unit in sheet.known():
            low = getattr(figure, "low", None)
            amount = (
                f"{low:g} to {figure.high:g} {unit}".rstrip()  # a band
                if low is not None
                else f"{figure.value:g} {unit}".rstrip()
            )
            assert amount in page, f"{path.name} does not say {label} is {amount}"


def test_the_jetpack_table_matches_the_one_doctor_reads() -> None:
    """Two copies of a version table drift, and the copy people paste into an issue is the one
    nobody re-reads. The page's table is the documentation; `_JETPACK_FOR_L4T` is what the
    command prints. They are the same fact and this is the only thing holding them together.

    It lived beside the Jetson container's checks until the container was removed. The table
    outlived it because it describes the board rather than the image, so it sits here with the
    other pages that have to agree with the code."""
    from quackd import doctor

    page = (REPO / "docs" / "guides" / "jetson.md").read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| `r(\d[\w.]*)` \| ([\w.]+) \|", page, flags=re.M))
    assert rows, "no L4T table found in docs/guides/jetson.md, or its shape changed"
    assert rows == doctor._JETPACK_FOR_L4T, (
        "docs/guides/jetson.md and quackd/doctor.py disagree about which JetPack an L4T "
        "release is:\n"
        f"  page:   {sorted(rows.items())}\n"
        f"  doctor: {sorted(doctor._JETPACK_FOR_L4T.items())}"
    )


def test_the_page_says_no_jetson_has_run_this() -> None:
    """The claim the Jetson page turns on, and the one a well meaning edit would soften. It is
    spelled this way rather than any of the phrasings `_RETIRED_HARDWARE_CLAIMS` bans, because
    an SO-101 arm has run quackd and a Jetson has not.

    It moved here with the JetPack table when the Jetson container was removed, and the page was
    rewritten for `--host` after that. The page says it where a reader starts and again in its
    Status section, and the Status one is held here too, because that is where somebody looks to
    decide whether to trust the rest. The daemon's own README says the same of the daemon. The
    second sentence sits under the doctor block the page pastes: that block came from
    `tests/fake_jetson_hostd.py`, and without the sentence beside it an Orin Nano's L4T, memory
    and `tegrastats` line read as a board's own output, which is the opposite of what happened."""
    page = (REPO / "docs" / "guides" / "jetson.md").read_text(encoding="utf-8")
    # whole sentences, because the negation is in the first words: the substring
    # "run on a Jetson by this project" is just as true of a page claiming the opposite
    nothing = "Nothing on this page has been run on a Jetson by this project"
    for sentence in (nothing, "That block came from a fake board, not a Jetson"):
        assert sentence in page, f"docs/guides/jetson.md no longer says: {sentence}"
    status = page.split("\n## Status\n", 1)
    assert len(status) == 2, "docs/guides/jetson.md has no Status section"
    assert nothing in status[1], (
        f"the Status section of docs/guides/jetson.md no longer says: {nothing}"
    )


#: What 0.13.0 shipped to put quackd on a Jetson, and what the release after it removed: the
#: image's directory, its compose file, the workflow that built it, the tag it was built as, and
#: the two commands that ran it, the second being the one an MCP client was told to spawn.
_JETSON_IMAGE_SPELLINGS = (
    "deploy/jetson",
    "compose.yml",
    "jetson-image",
    "quackd-jetson:local",
    "docker compose run --rm quackd",
    "docker compose run --rm -T quackd",
)


def test_no_living_document_still_describes_the_jetson_image() -> None:
    """quackd no longer runs on a Jetson, so nothing a reader follows today may still build the
    image or start quackd inside it.

    The whole text is read, fenced blocks included, which is where every one of these lived: a
    reader copies a command out of a fence before reading the paragraph around it. The history
    files keep them all, the way they keep every removal: the CHANGELOG, PLAN, the ADRs and the
    design notes say what was true when they were written, which is what `_living_docs()`
    leaves out. The bridge READMEs are read as well, because they are what somebody on the
    board reads, and so is `pyproject.toml`, whose comment above the sdist list used to
    describe the image."""
    extra = [REPO / "pyproject.toml", *sorted((REPO / "bridge").rglob("README.md"))]
    for path in [*_living_docs(), *extra]:
        text = path.read_text(encoding="utf-8")
        for spelling in _JETSON_IMAGE_SPELLINGS:
            assert spelling not in text, (
                f"{path.relative_to(REPO).as_posix()} still describes the Jetson image "
                f"({spelling!r}), which went when quackd stopped running on the board (ADR-0046)"
            )


def test_the_registry_is_documented_where_it_is_configured() -> None:
    """Two files under a directory an env var moves, holding a robot's token. Every one of
    those facts has a place it has to be findable from, or somebody loses a robot or a secret.
    """
    for path, needles in (
        (
            "README.md",
            ("quackd robot", "quackd flock", "QUACKD_REGISTRY_DIR", "--registry-dir", "rest-pose"),
        ),
        (
            "docs/guides/registry.md",
            ("robots.json", "flocks.json", "--probe", "plain text", "rest-pose", "rest_pose"),
        ),
        (".env.example", ("QUACKD_REGISTRY_DIR",)),
        ("docs/guides/mcp.md", ("--flock",)),
        ("docs/guides/memory.md", ("registered",)),
        ("SECURITY.md", ("robots.json",)),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{path} does not mention {needle!r}"


def test_the_host_is_documented_where_it_is_configured() -> None:
    """The same rule again, for the flag that names a board quackd uses and never runs on.

    Each needle has a page it has to be findable from. The README for the flags and the two
    variables, since that is where somebody decides whether any of this is for them, and for
    `--detector`, whose default changes the moment `--host` names a board that can detect. The
    Jetson page, because it is the one that explains all of it. local-llms.md, because `--host`
    moves a local preset's address and that page is where the address ladder is written down.
    registry.md, because a robot keeps a host and a token. `.env.example` with its `=`, the way
    the price test spells its variables, because the name also turns up in the prose around it.
    SECURITY.md for the port and the header, since a daemon serving a camera on a network is
    exactly the thing that page exists to describe. CONTRIBUTING.md and architecture.md for the
    directory, because a daemon nobody can find in the map is one nobody reviews.

    Each needle must not run on into a longer name: `--host` is inside `--host-token`, and
    `QUACKD_HOST` inside `QUACKD_HOST_TOKEN`, so a plain substring check would pass a page that
    only ever names the token.

    The README and local-llms.md show `--host jetson.local`, and neither the daemon nor Ollama
    answers there as installed: both listen on the board's own loopback. Both pages once gave
    that spelling alone, so each has to name the way that works as installed as well, the
    tunnel and `--host 127.0.0.1`, and the README the `--bind` that makes the other one work."""
    flags = ("--host", "--host-token", "QUACKD_HOST", "QUACKD_HOST_TOKEN")
    for path, needles in (
        ("README.md", (*flags, "--detector", "--host 127.0.0.1", "--bind")),
        ("docs/guides/jetson.md", (*flags, "--detector")),
        ("docs/guides/local-llms.md", (*flags, "loopback", "tunnel")),
        ("docs/guides/registry.md", flags),
        (".env.example", ("QUACKD_HOST=", "QUACKD_HOST_TOKEN=")),
        ("SECURITY.md", ("9874", "X-Quackd-Token")),
        ("CONTRIBUTING.md", ("bridge/jetson",)),
        ("docs/concepts/architecture.md", ("bridge/jetson",)),
    ):
        text = (REPO / path).read_text(encoding="utf-8")
        for needle in needles:
            assert re.search(re.escape(needle) + r"(?![\w-])", text), (
                f"{path} does not mention {needle!r}"
            )


#: What the pages written for `--host` said and the code never did, each with what it does.
#: Every one was copied into more than one file before anybody caught it.
_HOST_CLAIMS_THE_CODE_NEVER_MADE = (
    (
        "readable by 0.13,",
        "0.12, 0.13 and 0.14 all refuse a key they do not know, so a registry with no board is "
        "readable by 0.12 to 0.14, and one robot with a host makes the whole file unreadable",
    ),
    (
        "gets four things from it",
        "the model comes from the person's own server, a preset moved to the board, and the "
        "daemon has no route to it",
    ),
    (
        "rung that answers",
        "the local provider takes the first rung that is set and probes none of them, so a dead "
        "QUACKD_BASE_URL beats a live QUACKD_HOST",
    ),
    (
        "so the body's keepalives keep flowing",
        "go_to holds its last twist for one deadman window (HOLD_TTL_S) and then sends a zero one",
    ),
)


def test_no_page_repeats_a_host_claim_the_code_never_made() -> None:
    """Four sentences about `--host` that read well and were wrong.

    The CHANGELOG, ADR-0046 and the source are read along with the living documents. None of
    these was ever true, so no record of the day has a reason to keep one, and the first and
    third were a comment and a docstring before they were a page."""
    sources = [
        REPO / "CHANGELOG.md",
        *sorted((REPO / "docs" / "adr").glob("0046-*.md")),
        *sorted((REPO / "bridge").rglob("README.md")),
        *sorted((REPO / "quackd").rglob("*.py")),
    ]
    for path in _living_docs() + sources:
        text = _one_line(path.read_text(encoding="utf-8"), seams=path.suffix == ".py")
        for wrong, true in _HOST_CLAIMS_THE_CODE_NEVER_MADE:
            # pytest.fail rather than assert: pytest explains a failed `not in` by diffing the
            # needle against the whole file, which takes minutes on a file this long
            if wrong in text:
                pytest.fail(f"{path.relative_to(REPO).as_posix()} says {wrong!r}: {true}")


def test_what_to_send_back_from_a_board_is_one_list() -> None:
    """The daemon's README and the Jetson page once asked for two different reports, and the one
    the CHANGELOG links left out `/board`, the only thing that returns the board's own files: a
    doctor `--json` carries what doctor parsed from them, so a parser wrong about a real board
    could not be seen in it. The list lives on the Jetson page, and the README points there."""
    page = (REPO / "docs" / "guides" / "jetson.md").read_text(encoding="utf-8")
    status = page.split("\n## Status\n", 1)[1]
    for needle in ("--json", "/hello", "/board", "tegrastats", "transcript.jsonl"):
        assert needle in status, (
            f"the Status section of docs/guides/jetson.md no longer asks for {needle}"
        )
    readme = (REPO / "bridge" / "jetson" / "README.md").read_text(encoding="utf-8")
    assert "(../../docs/guides/jetson.md#status)" in readme.split("\n## Status\n", 1)[1], (
        "bridge/jetson/README.md should send a board owner to docs/guides/jetson.md#status for "
        "the list"
    )


def test_every_command_is_named_in_the_readme_table_and_the_module_map() -> None:
    """`quackd memory` shipped in 0.6 and appeared in neither for a release. A command nobody
    can find is a command nobody has."""
    from quackd.cli import app

    # hidden commands are left out: `trace` is the 0.11 alias of `log` and is deliberately
    # absent from `--help`, so a README row for it would advertise the spelling being retired
    names = {
        c.name or (c.callback.__name__ if c.callback else "")
        for c in app.registered_commands
        if not c.hidden
    }
    names |= {g.name or "" for g in app.registered_groups}
    names = {n.replace("_", "-") for n in names if n}
    architecture = (REPO / "docs" / "concepts" / "architecture.md").read_text(encoding="utf-8")
    # the table row, not the prose: `quackd flock` is mentioned in three paragraphs and was
    # still missing from the one table somebody reads to find out that it exists
    rows = "".join(line for line in README.splitlines(keepends=True) if line.startswith("| `"))
    for name in sorted(names):
        assert f"| `quackd {name}" in rows, f"the README usage table has no row for {name}"
        assert name in architecture, f"docs/concepts/architecture.md never names {name}"


def _github_slug(heading: str) -> str:
    """The anchor GitHub mints for a heading, near enough to check links against.

    Backticks and inline links are stripped to their text, everything that is not a word
    character, a hyphen or a space goes, what is left is lowercased and its spaces become
    hyphens. That is GitHub's rule, and it is why `## The ones quackd names` is reached as
    `#the-ones-quackd-names`."""
    text = re.sub(r"`|<[^>]+>", "", heading)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    """Every fragment a link may point at in this file: one per heading, plus any explicit
    `id=` or `name=`. A heading that repeats gets `-1`, `-2`, the way GitHub numbers them."""
    text = path.read_text(encoding="utf-8")
    seen: dict[str, int] = {}
    out: set[str] = set()
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*$", _prose(text), flags=re.M):
        slug = _github_slug(heading)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out | set(re.findall(r'\b(?:id|name)="([^"]+)"', text))


def test_every_relative_link_and_anchor_in_the_markdown_resolves() -> None:
    """Every link between two files in this repository points at something that is there.

    Its predecessor checked one shape, `](adr/....md)`, and resolved it against `docs/`
    whatever file did the linking. So a page in a subdirectory writing `](../adr/....md)`
    was never checked at all, and one writing `](adr/....md)` passed while being broken on
    GitHub. That hole was invisible while every page sat directly under `docs/`, and stopped
    being invisible the day the decision LLM pages moved a level down.

    Anchors are checked too, because this is now a repository where one page links a heading
    on another, and a heading is renamed far more easily than a file.

    Links inside fenced blocks are not checked: those are examples of what a reader would
    type, and `_prose` takes them out for every other guard here as well.
    """
    anchors: dict[Path, set[str]] = {}
    broken: list[str] = []
    for md in sorted(REPO.glob("*.md")) + sorted((REPO / "docs").rglob("*.md")):
        for target in re.findall(r"\]\(([^)\s]+)\)", _prose(md.read_text(encoding="utf-8"))):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # http, https, mailto, any scheme
                continue
            path, _, fragment = target.partition("#")
            dest = md if not path else (md.parent / path).resolve()
            if not dest.exists():
                broken.append(f"{md.relative_to(REPO)} -> {target} (no such file)")
            elif (
                fragment
                and dest.suffix == ".md"
                and fragment.lower() not in anchors.setdefault(dest, _anchors(dest))
            ):
                broken.append(f"{md.relative_to(REPO)} -> {target} (no such heading)")
    assert not broken, "links that go nowhere:\n" + "\n".join(broken)


def test_the_docs_map_links_every_guide_robot_reference_and_concept_page() -> None:
    """docs/README.md is the map, and a page it leaves out is one a reader finds only by luck.

    Twenty six pages once sat side by side in docs/, a guide beside a spec beside one robot's
    checklist. The map is how they stay found now that each kind has a folder of its own, and
    the map says this test holds it to every page in those four folders. The ADRs and the
    design notes are reached through their folders, which the map links."""
    index = REPO / "docs" / "README.md"
    linked = {
        (index.parent / target.partition("#")[0]).resolve()
        for target in re.findall(r"\]\(([^)\s]+)\)", _prose(index.read_text(encoding="utf-8")))
        if not re.match(r"^[a-z][a-z0-9+.-]*:", target)
    }
    pages = [
        page
        for folder in ("guides", "adapters", "reference", "concepts")
        for page in sorted((REPO / "docs" / folder).rglob("*.md"))
    ]
    assert len(pages) > 20, "the folders under docs/ moved, so this test is checking nothing"
    missing = [page.relative_to(REPO).as_posix() for page in pages if page.resolve() not in linked]
    assert not missing, f"docs/README.md does not link: {missing}"


# ── a page per decision LLM, and the row it has to agree with ───────────────────────────


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_every_decision_llm_preset_has_a_page_that_agrees_with_its_row(name: str) -> None:
    """A decision LLM is a row of data, and its page is where a reader checks the row.

    The reason this is a test and not a convention: the hub's table once carried a `kev`
    install line that had lost `KEV_DTYPE=bf16` and the `uv run --extra serve` prefix the
    catalogue still had, and nothing in the suite could see it. A reader copying that cell
    ran bare `python` outside the synced environment. So every value a page quotes is read
    back off the page and compared with the row it came from.

    The three literals at the end are the honesty rule `docs/adapters/writing-an-adapter.md` puts on
    an adapter page, applied to a server: say what you read and when, say what you are assuming, and
    keep the word never until somebody has actually run it.
    """
    spec = PRESETS[name]
    path = REPO / "docs" / "guides" / "decision-llms" / f"{name}.md"
    assert path.exists(), f"every preset has a page: docs/guides/decision-llms/{name}.md is missing"
    page = path.read_text(encoding="utf-8")
    for field in ("url", "model", "key_env", "install"):
        value = getattr(spec, field)
        if value is not None:
            assert value in page, f"{path.name} does not carry the row's {field}: {value!r}"
    if spec.extra:
        assert f"quackd[{spec.extra}]" in page, f"{path.name} does not name its extra"
    if spec.price is not None:
        assert f"{spec.price.input:g}" in page, f"{path.name} does not carry its published rate"
    assert f"--decision-llm {name}" in page, f"{path.name} does not show how to name it"
    assert "--decision-mode shadow" in page, f"{path.name} does not point at shadow mode"
    # As headings, and in that spelling: `"VERIFIED" in page` is satisfied by the word
    # UNVERIFIED, so the first half of this pair could not fail on any page that had the
    # second. The status line is checked literally for the same reason -- `never` on its own
    # is a word that turns up seven times in ordinary prose on one of these pages.
    for honesty in ("\n## VERIFIED", "\n## UNVERIFIED"):
        assert honesty in page, f"{path.name} has no {honesty.strip()} section"
    assert "**Nothing here has ever answered a real robot.**" in page, (
        f"{path.name} drops the status line before anybody has run it"
    )
    if name == "local":
        assert "--decision-url" in page and "/v1/systemone" in page


def test_the_decision_llms_hub_links_every_preset_page_from_its_table() -> None:
    """The table is the way in, so it carries the links, in the order `doctor` prints.

    It also carries each install line verbatim, which is the cell that drifted before: a
    table nobody reads against the code is a table that describes an older release.
    """
    hub = (REPO / "docs" / "guides" / "decision-llms" / "README.md").read_text(encoding="utf-8")
    lines = hub.splitlines()
    rows: dict[str, int] = {}
    for name in PRESET_NAMES:
        row = next((i for i, line in enumerate(lines) if line.startswith(f"| `{name}` |")), None)
        assert row is not None, f"the hub's table has no row for {name}"
        rows[name] = row
        assert f"]({name}.md)" in lines[row], f"{name}'s row does not link its page"
        assert PRESETS[name].install in lines[row], f"{name}'s row does not quote its install line"
    assert list(rows) == sorted(rows, key=lambda n: rows[n]), "the table is not in doctor's order"
    linked = set(re.findall(r"\]\(([a-z_]+)\.md\)", hub))
    assert linked == set(PRESET_NAMES), f"the hub links {sorted(linked)}"


def test_no_living_document_claims_the_wrong_number_of_decision_llms() -> None:
    """Seven is `len(PRESET_NAMES)`, and the README says it in a status cell.

    The same shape as the cloud-provider guard above, and for the same reason: a count in
    prose is a fact about the code that nothing else would notice going stale."""
    right = len(PRESET_NAMES)
    wrong = [word for count, word in _NUMBER_WORDS.items() if count != right]
    for path in _living_docs():
        prose = _prose(path.read_text(encoding="utf-8")).lower()
        for word in wrong:
            assert f"{word} decision llms" not in prose, (
                f"{path.name} says {word} decision LLMs and the catalogue has {right}"
            )


#: TypeSafe's confidence page publishes exactly two numbers, 0.5 and 0.9, which are quackd's
#: brake and confirm-gated floors. The read floor at 0.60 and the motion floor at 0.85 are
#: quackd's own, set between those two, and nothing published sits there. The claim that all
#: four are theirs was written once and then copied onto nine pages and a README row, where it
#: outlived two rounds of editing, so it is a string now rather than a convention. A number
#: nobody published is a number nobody has calibrated either, and that is the whole reason
#: `--decision-mode shadow` exists.
_FLOORS_ARE_NOT_ALL_PUBLISHED = (
    "floors are jev's published numbers",
    "floors are jev's numbers",
    "these are jev's own published numbers",
    "floors on this page are jev's published numbers",
    "floors set on jev's published numbers",
    "floors are typesafe's published numbers",
    "every one of them a number typesafe publish",
    "typesafe's own universal floor",
    "confidence floors are typesafe's own published numbers",
)
#: An ADR body is what was believed on the day it was accepted, and both of these ADRs still
#: say it there. What has to be true today is the amendment note above the first heading,
#: which is the house's own correction mechanism, so that is the part read back -- minus
#: anything in quotation marks, because a note corrects a sentence by quoting it and a
#: checker that cannot tell a citation from a claim would forbid the fix along with the bug.


def _one_line(text: str, *, seams: bool = False) -> str:
    """A file as one lowercase line, with nothing broken by where it happened to wrap.

    Two things hide a sentence from a substring check, and both are ordinary formatting rather
    than evasion. Python splits a long message across adjacent string literals, so a warning
    reading `"... floors are Jev\'s " "published numbers."` carries a quote, a newline and an
    indent in the middle of its own sentence; that is exactly how the line `--decision-mode
    on` prints kept the old floor credit through a correction that was looking for it, with
    the file in scope. And markdown wraps prose at the column, so any sentence long enough
    falls across two lines and stops matching.

    Literal seams are joined only where `seams` says so, which is for Python sources: in
    markdown, two quotes with a space between them are two quotes and nothing is being
    concatenated. Then all whitespace collapses, which is what makes "this sentence does not
    appear" a claim about the sentence rather than about how somebody typed it.
    """
    if seams:
        text = re.sub(r'"\s*\n\s*"', "", text)
    return " ".join(text.split()).lower()


def test_no_living_document_credits_all_four_confidence_floors_to_typesafe() -> None:
    """Two of the four are quackd's own, and the docs have said otherwise twice.

    `https://docs.typesafe.ai/confidence` states 0.5 (genuinely unsure, route to a human) and
    0.9 (high stakes, proceed with confirmation), and says the right values are domain-specific.
    quackd's brake and confirm floors sit on those. Its read floor (0.60) and motion floor
    (0.85) sit between them and are nobody's published guidance, so a page that credits all
    four to a vendor is telling a reader those numbers carry an authority they do not have.

    The history files are exempt the way they always are, with one addition: ADR-0040 and
    ADR-0043 both state the old claim in their Consequences, and both now carry an amendment
    note correcting it. A body records what was believed and is left alone. The note is the
    live document, so the text above the ADR's first heading is what is read back, with
    quoted spans removed: both notes work by quoting the sentence they are overturning.
    """
    # Every source file, not just the stepper's. The one live site this correction missed was
    # the warning `--decision-mode on` prints, in `quackd/cli.py`, which said the forbidden
    # sentence word for word while the guard read markdown and one module. A string a person
    # reads on their own terminal is the most live site there is.
    sources = [
        *sorted((REPO / "quackd").rglob("*.py")),
        REPO / "docs" / "adr" / "0040-a-discrete-stepper-in-front-of-the-model.md",
        REPO / "docs" / "adr" / "0043-decision-llms-are-a-wire-format-and-a-data-row.md",
    ]
    for path in _living_docs() + sources:
        text = path.read_text(encoding="utf-8")
        if "adr" in path.parts:
            # the metadata line and the amendment notes, which stop at the first section,
            # and not the sentences they quote in order to overturn them
            text = re.sub(r'"[^"]*"', "", text.split("\n## ", 1)[0])
        text = _one_line(text, seams=path.suffix == ".py")
        for wrong in _FLOORS_ARE_NOT_ALL_PUBLISHED:
            assert wrong not in text, (
                f"{path.relative_to(REPO)} says {wrong!r}, but TypeSafe publish only 0.5 and "
                "0.9; the 0.60 read floor and the 0.85 motion floor are quackd's own"
            )


#: What the pages written for the arm's simulator said, each beside what the code does. Each
#: read well, and most were copied into a second page before anybody checked it against the
#: code: an id quackd chose credited to LeRobot, a pass rule missing the case the lookout sweep
#: passes by, LeRobot's own step cap credited to the servo's firmware, and a model's physics
#: offered as a rehearsal of the arm's.
_ARM_SIMULATOR_CLAIMS_THE_CODE_NEVER_MADE = (
    (
        "lerobot's default id",
        "`arm-01` is quackd's own DEFAULT_ID, the id it hands LeRobot for an arm nobody named, "
        "and no upstream ref gives LeRobot one",
    ),
    (
        "keeps under its default id",
        "`arm-01` is quackd's own DEFAULT_ID, the id it hands LeRobot for an arm nobody named",
    ),
    (
        "the only check that this machine can draw",
        "every connect renders one frame (LeRobotSim._render_once), a run's and each preflight "
        "cycle's included",
    ),
    (
        "its close ended at the rest pose and every check",
        "judge_close passes a robot with no rest pose, unless the sidecar asks at_rest: true",
    ),
    (
        "its close ended at the rest pose, and every check",
        "judge_close passes a robot with no rest pose, unless the sidecar asks at_rest: true",
    ),
    (
        "the close ended at the rest pose or was refused where the task expects that",
        "judge_close passes a robot with no rest pose, unless the sidecar asks at_rest: true",
    ),
    (
        "ended at the rest pose, or refused where the sidecar says to expect that",
        "judge_close passes a robot with no rest pose, unless the sidecar asks at_rest: true",
    ),
    (
        "servo behaviour is the arm's",
        "SERVO_DYNAMICS: the simulated dynamics are the model's and never the arm's",
    ),
    (
        "lerobot leaves to it",
        "the step cap is LeRobot's own ensure_safe_goal_position, called in send_action; the "
        "clamp and a limp joint's goal are the servo's",
    ),
    (
        "leaves it to the firmware",
        "the step cap is LeRobot's own ensure_safe_goal_position, called in send_action",
    ),
    (
        "does under it what the arm does",
        "the step cap is LeRobot's code, and only the clamp and a limp joint's goal are the "
        "servo's; the dynamics are the model's (SERVO_DYNAMICS)",
    ),
    (
        "rehearses here before it is let go of at the bench",
        "how a released joint settles is the model's physics (SERVO_DYNAMICS), over signs and "
        "zeros nobody has checked (JOINT_SIGN, JOINT_ZERO)",
    ),
    (
        "two of the views are quackd's",
        "the connect note is one sentence naming whichever of front and top is open "
        "(sim.transport.default_views)",
    ),
    (
        "seven bench steps 0.14.0 owes, above",
        "a later release heads the CHANGELOG, so 0.14.0's list of bench steps is below it",
    ),
    (
        "both simulators draw the ball",
        "there are three simulators, and the arm's draws nothing in a detector's colour",
    ),
    (
        "so the cartoon and the mujoco world (`quackd_microduck.sim3d`) share it",
        "the arm's simulator steps on the flock clock too (quackd_lerobot.sim.clock)",
    ),
    (
        "the physics backend's upstreams",
        "microduck:mujoco and lerobot:mujoco are both physics backends, each with its own table",
    ),
    (
        "colour ranges are the *simulator's*",
        "the detector's ranges are the duck simulators', and the arm's simulator has no ball",
    ),
    # the arm simulator's film is the first of a cloud model in a simulator, not of a model:
    # the four transcripts under docs/assets/transcripts are local models on microduck:sim2d
    (
        "the first recording in this repository of a model in a simulator",
        "the transcripts in docs/assets are local models on sim2d, so the film is the first "
        "cloud model filmed in a simulator, not the first model in one",
    ),
    (
        "the first recording here of a model in a simulator",
        "the transcripts in docs/assets are local models on sim2d, so the film is the first "
        "cloud model filmed in a simulator, not the first model in one",
    ),
    (
        "the first model recorded in a simulator here",
        "the transcripts in docs/assets are local models on sim2d, so the film is the first "
        "cloud model filmed in a simulator, not the first model in one",
    ),
    (
        "that an so-101 is rehearsed",
        "a task is rehearsed, through the arm's backend on the maker's model, and nothing has "
        "compared that model against an arm (ADR-0047), so no SO-101 is",
    ),
)


def test_no_page_repeats_a_claim_about_the_arms_simulator_the_code_never_made() -> None:
    """Sentences about `lerobot:mujoco` that read well and were wrong.

    The CHANGELOG, the ADRs that carry a note about the simulator, the arm's own README and the
    source are read along with the living documents, because each of these was in at least one
    of them, and four were docstrings. None of them was ever true, so no record has a reason to
    keep one."""
    adr = REPO / "docs" / "adr"
    sources = [
        REPO / "CHANGELOG.md",
        REPO / "adapters" / "lerobot" / "README.md",
        *(
            path
            for number in ("0016", "0030", "0036", "0045", "0047")
            for path in sorted(adr.glob(f"{number}-*.md"))
        ),
        *sorted((REPO / "quackd").rglob("*.py")),
        *sorted((REPO / "adapters" / "lerobot" / "src").rglob("*.py")),
    ]
    for path in _living_docs() + sources:
        text = _one_line(path.read_text(encoding="utf-8"), seams=path.suffix == ".py")
        for wrong, true in _ARM_SIMULATOR_CLAIMS_THE_CODE_NEVER_MADE:
            # pytest.fail rather than assert, for the reason the host claims above give
            if wrong in text:
                pytest.fail(f"{path.relative_to(REPO).as_posix()} says {wrong!r}: {true}")


#: What the release note and ADR-0036 said about the arm's torque on a disconnect when 0.15.0
#: built the follower to keep it, each beside what the code does. The first three said no
#: decided ending changed, and one refusal did: on a transport connected again after a close
#: that kept torque, it used to keep the arm energised, and it lets go now. The last listed
#: three of that release's changes to `lerobot:real` as though they were all of them.
_ARM_TORQUE_CLAIMS_THE_CODE_NEVER_MADE = (
    (
        "so every clean ending lets go where it did",
        "a refusal after a close that kept torque used to keep the arm energised, and lets go "
        "now (`_give_up` writes the flag True)",
    ),
    (
        "so they let go as they did",
        "a refusal after a close that kept torque used to keep the arm energised, and lets go "
        "now (`_give_up` writes the flag True)",
    ),
    (
        "only the endings nobody decided changed",
        "a refusal after a close that kept torque changed too, and it lets go",
    ),
    (
        "what this release changes in `lerobot:real`, the follower built",
        "the release note names more changes to lerobot:real than three, and a list of three "
        "beside the phrase read as all of them",
    ),
)


def test_no_page_repeats_a_claim_about_the_arms_torque_the_code_never_made() -> None:
    """Sentences about when the arm lets go that read well and were wrong. The CHANGELOG,
    ADR-0036, the arm's own README and its source are read along with the living documents,
    because the first two carried them and the source says the same things in docstrings."""
    sources = [
        REPO / "CHANGELOG.md",
        REPO / "adapters" / "lerobot" / "README.md",
        *sorted((REPO / "docs" / "adr").glob("0036-*.md")),
        *sorted((REPO / "adapters" / "lerobot" / "src").rglob("*.py")),
    ]
    for path in _living_docs() + sources:
        text = _one_line(path.read_text(encoding="utf-8"), seams=path.suffix == ".py")
        for wrong, true in _ARM_TORQUE_CLAIMS_THE_CODE_NEVER_MADE:
            # pytest.fail rather than assert, for the reason the host claims above give
            if wrong in text:
                pytest.fail(f"{path.relative_to(REPO).as_posix()} says {wrong!r}: {true}")


def test_the_arm_left_holding_by_an_exit_that_skips_the_close_has_a_bench_step() -> None:
    """0.15.0 built the follower to keep torque on a disconnect quackd did not ask for, so a
    second Ctrl-C during the fold back to the rest pose leaves the arm holding where 0.14.0
    could drop it. Its release note first said that change had never met an arm without saying
    how the bench would settle it, and PLAN's item for the arm pointed only at 0.14.0's seven
    steps. Both name the step now, and a later edit that drops it from either is caught here.

    Both also send the reader to step 14 of the hardware checklist for it, which is the order a
    lab visit takes the arm through, and step 14 said nothing about a second press. A visit
    that followed the checklist would have skipped the one bench step this release wrote, so
    step 14 is read too."""
    step = "a second ctrl-c during the fold back to the rest pose"
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    release = changelog.split("\n## [0.15.0]", 1)[1].split("\n## [", 1)[0]
    assert step in _one_line(release), "CHANGELOG.md's 0.15.0 section no longer names the step"
    plan = _one_line((REPO / "PLAN.md").read_text(encoding="utf-8"))
    assert step in plan, "PLAN.md's item for the SO-101 no longer names 0.15.0's bench step"
    checklist = (REPO / "docs" / "adapters" / "lerobot" / "hardware-checklist.md").read_text(
        encoding="utf-8"
    )
    step_14 = checklist.split("\n14. ", 1)[1].split("\n## ", 1)[0]
    assert step in _one_line(step_14), (
        "step 14 of docs/adapters/lerobot/hardware-checklist.md no longer asks for 0.15.0's bench "
        "step, and the release note and PLAN.md both send the reader there for it"
    )


def test_the_simulators_bench_steps_are_one_list_in_plan_and_the_release_note() -> None:
    """PLAN.md's item for the SO-101 against its simulator lists each bench step it owes, and
    0.15.0's Known limitations numbers the same steps and then 0.14.0's seven. When the rest
    move pressing the gripper into the table became a step of its own in PLAN.md, the release
    note folded it into the joint signs, so the intro named five steps and the list under it
    four. The first three words of each item are compared in order, so a step added to one
    list and not the other, or merged into its neighbour, fails here."""
    plan = (REPO / "PLAN.md").read_text(encoding="utf-8")
    item = plan.split("**The SO-101 against its simulator.**", 1)[1].split("\n\n  Until", 1)[0]
    planned = [" ".join(line.split()[1:4]).lower() for line in re.findall(r"^  - .*", item, re.M)]
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    release = changelog.split("\n## [0.15.0]", 1)[1].split("\n## [", 1)[0]
    bullet = release.split("**The arm's simulator is not the arm", 1)[1].split("\n- **", 1)[0]
    numbered = re.findall(r"^  \d+\. (.*)", bullet, re.M)
    assert numbered and "0.14.0" in numbered[-1], (
        "CHANGELOG.md's 0.15.0 list of the simulator's bench steps no longer ends with 0.14.0's"
    )
    listed = [" ".join(line.split()[:3]).lower() for line in numbered[:-1]]
    assert planned, "PLAN.md's item for the SO-101 against its simulator lists no bench step"
    assert listed == planned, (
        f"PLAN.md owes the simulator {planned} and CHANGELOG.md's 0.15.0 lists {listed}: a step "
        "in one and not the other, or merged into its neighbour"
    )


@pytest.mark.parametrize("name", ["PLAN.md", "RELEASING.md"])
def test_plan_breaks_no_line_mid_sentence_far_short_of_its_wrap(name: str) -> None:
    """PLAN.md is edited by hand at every release, and a clause spliced into an item that
    nobody reflows leaves a line that stops halfway across in the middle of a sentence. The
    0.16.0 work left two: one where the rest pose item gained a clause about `pick`, and one
    where the release added what 0.16.0 changes to the SO-101 item. Markdown renders them the
    same, but PLAN is read as source at every release, and a line that stops short there reads
    as though something was cut out of it. RELEASING.md is read as source at every release too,
    and its first draft left one in the smoke step, `nothing. A release` alone on its line.

    A line that ends a sentence, or that stops before a code span, emphasis or link too long to
    fit, is a break somebody chose. So a line counts only when the next word would have fitted
    on it inside 80 columns. The file wraps at about 96, and what a wrap leaves over at the end
    of a line never comes to 16."""
    token = re.compile(r"\(?\[[^\]]*\]\([^)]*\)\S*|`[^`]*`\S*|\*{1,2}[^*]+\*{1,2}\S*|\S+")
    lines = (REPO / name).read_text(encoding="utf-8").split("\n")
    ragged = []
    fenced = False
    for number, (line, after) in enumerate(itertools.pairwise(lines), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        rest = after.strip()
        if fenced or not line.strip() or not rest or line.rstrip().endswith((".", ":", "?", "!")):
            continue
        if line.lstrip().startswith(("#", "|")) or re.match(r"#|\||[-*] |\d+\. ", rest):
            continue  # a heading, a table row, or the next line starts an item of its own
        word = token.match(rest)
        if word and len(line) + 1 + len(word.group(0)) <= 80:
            ragged.append(f"line {number}: {line.strip()!r}")
    assert not ragged, f"{name} stops these lines mid-sentence, far short of its wrap: {ragged}"


def test_the_release_note_names_the_frames_that_are_still_encoded_on_the_loop() -> None:
    """0.15.0 moved the PNGs of a turn's own observation into a worker thread, because a
    heartbeat waiting on the loop's thread was held up by them. The `observe` verb's frames
    still go through `AgentLoop._on_frames`, which writes them on that thread, and
    `robot_observe` encodes what it returns to an MCP client there too. The note first said
    the frames a turn saves were encoded off the loop and named only the colour detector and
    the SDKs as still on it, which read as though every frame had moved."""
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    release = _one_line(changelog.split("\n## [0.15.0]", 1)[1].split("\n## [", 1)[0])
    assert "still run on the loop's thread" in release, (
        "CHANGELOG.md's 0.15.0 heartbeat entry no longer says what still runs on the loop"
    )
    still = release.split("still run on the loop's thread", 1)[0].rsplit(". ", 1)[-1]
    for named in ("the `observe` verb", "`robot_observe`", "every detector but a board's"):
        assert named in still, (
            f"CHANGELOG.md's 0.15.0 list of what still runs on the loop leaves out {named}"
        )


#: How the pages said an MCP session's minutes were counted before 0.15.0 started them at the
#: connect (`RobotSession.connect` calls `budget.start()` once the transport has connected).
#: A session still begins at the spawn, and pages that say so are right. Its clock does not.
_MCP_CLOCK_STARTS_AT_THE_SPAWN = (
    re.compile(
        r"(clock|minutes)[^.]{0,40}(start|begin)s? (when|at) "
        r"(the client spawn|the spawn|the server start)"
    ),
    re.compile(r"minutes from the spawn"),
    re.compile(r"counted from when the server started"),
)


def test_no_page_says_an_mcp_sessions_minutes_start_at_the_spawn() -> None:
    """0.15.0 starts an MCP session's clock once its robot has connected, as a `quackd run`'s
    is, so an arm's connect retries no longer come out of the minutes. docs/guides/mcp.md was
    corrected with the code, and the arm's first-run guide still said, in five paragraphs, that
    the five minutes begin when the client spawns the server."""
    for path in _living_docs():
        text = _one_line(path.read_text(encoding="utf-8"))
        for wrong in _MCP_CLOCK_STARTS_AT_THE_SPAWN:
            found = wrong.search(text)
            if found:
                pytest.fail(
                    f"{path.relative_to(REPO).as_posix()} says {found.group(0)!r}: an MCP "
                    "session's minutes count from when its robot connected"
                )


_NOBODY_ASKED = (
    "under --yes nobody is asked, and without it the confirm reads stdin, so `yes | quackd run` "
    "and `quackd run < answers.txt` open the gate too (cli.py's `_confirm_prompt`): say a person "
    "at a terminal is asked unless --yes, or a pipe or file on stdin, answers for them"
)
_LOAD_POLICY_STILL_WOULD = (
    "`load_policy()` in real.py builds a LeRobot policy in the arm's own process, and nothing in "
    "quackd calls it: say that no quackd command loads a checkpoint there"
)

#: What the pages and the source said about who clears a policy segment and where a checkpoint
#: loads, until 0.16.0's fact check. Each read well and none was ever true, so no record, the
#: CHANGELOG's included, has a reason to keep one. The first correction wrote two more of its
#: own, the two that name `--yes` as all that answers, which is why a correction is guarded too.
_POLICY_CLAIMS_THE_CODE_NEVER_MADE = (
    ("so a person says yes to each segment", _NOBODY_ASKED),
    ("a person confirms each segment", _NOBODY_ASKED),
    ("only behind a person's yes, asked before each call", _NOBODY_ASKED),
    ("unless `--yes` answers for them", _NOBODY_ASKED),
    ("unless you pass `--yes`, which starts every segment", _NOBODY_ASKED),
    ("confirm gate still asks a person on top of it", _NOBODY_ASKED),
    ("a checkpoint never runs in the process that owns the serial bus", _LOAD_POLICY_STILL_WOULD),
    ("no checkpoint and no inference ever run in the process", _LOAD_POLICY_STILL_WOULD),
    ("this is why no checkpoint is loaded in the process", _LOAD_POLICY_STILL_WOULD),
    ("why a policy never runs beside the arm's bus", _LOAD_POLICY_STILL_WOULD),
    ("no checkpoint ever loads beside", _LOAD_POLICY_STILL_WOULD),
    ("a policy never runs in the process that owns the arm's", _LOAD_POLICY_STILL_WOULD),
    ("never in the process that owns the serial bus", _LOAD_POLICY_STILL_WOULD),
    ("never in the process that holds the", _LOAD_POLICY_STILL_WOULD),
    ("never by the process that owns the arm's bus", _LOAD_POLICY_STILL_WOULD),
    ("no checkpoint is loaded beside", _LOAD_POLICY_STILL_WOULD),
    ("no checkpoint is ever loaded beside", _LOAD_POLICY_STILL_WOULD),
    ("no checkpoint runs in the process", _LOAD_POLICY_STILL_WOULD),
    ("a checkpoint runs in a process of its own, never in the one", _LOAD_POLICY_STILL_WOULD),
    ("a policy runs in a process of its own, never in the one", _LOAD_POLICY_STILL_WOULD),
    (
        "imported only in the server's own process, never in the one that drives the arm",
        _LOAD_POLICY_STILL_WOULD,
    ),
)


def test_no_page_repeats_a_claim_about_a_policy_run_the_code_never_made() -> None:
    """Sentences about a policy segment that read well and were wrong: that a person says yes to
    each one, when `--yes` and a pipe on stdin both clear it with nobody asked, and that no
    checkpoint ever loads in the process that owns the arm's bus, when `load_policy()` still
    would. The CHANGELOG, ADR-0048 and the source carried them, the source in docstrings, a
    comment and an upstream ref's note, so they are read along with the living documents, and
    so is the arm's own README, which says where its policy server runs."""
    sources = [
        REPO / "CHANGELOG.md",
        REPO / "adapters" / "lerobot" / "README.md",
        *sorted((REPO / "docs" / "adr").glob("0048-*.md")),
        *sorted((REPO / "quackd").rglob("*.py")),
        *sorted((REPO / "adapters" / "lerobot" / "src").rglob("*.py")),
    ]
    for path in _living_docs() + sources:
        text = _one_line(path.read_text(encoding="utf-8"), seams=path.suffix == ".py")
        for wrong, true in _POLICY_CLAIMS_THE_CODE_NEVER_MADE:
            # pytest.fail rather than assert, for the reason the host claims above give
            if wrong in text:
                pytest.fail(f"{path.relative_to(REPO).as_posix()} says {wrong!r}: {true}")


def test_nothing_in_quackd_calls_load_policy_as_the_pages_say() -> None:
    """The README, the release note, ADR-0048, SECURITY.md and the pages that explain the policy
    server say no quackd command loads a checkpoint in the process that owns the arm's bus,
    because nothing in quackd calls `load_policy()`. A command that called it would make every
    one of them false without a word of them changing, so the calls are counted in the syntax
    tree, where a docstring that names the function is not a call."""
    import ast

    callers = []
    for path in [
        *sorted((REPO / "quackd").rglob("*.py")),
        *sorted((REPO / "adapters").glob("*/src/**/*.py")),
        *sorted((REPO / "bridge").rglob("*.py")),
    ]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            named = (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None)) == "load_policy"
            ) or (
                isinstance(node, ast.ImportFrom)
                and any(alias.name == "load_policy" for alias in node.names)
            )
            if named:
                callers.append(f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
    assert not callers, (
        f"load_policy() is reached from {callers}: the pages that say no quackd command loads a "
        "checkpoint beside the arm's bus are wrong now, so correct them with the code"
    )


def _release_note(version: str) -> str:
    """One release's section of the CHANGELOG, from its heading to the next one."""
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    return changelog.split(f"\n## [{version}]", 1)[1].split("\n## [", 1)[0]


def test_the_policy_release_names_every_bench_step_the_arm_still_owes() -> None:
    """0.16.0's note first said the arm owed 0.14.0's seven bench steps and one of 0.15.0's, the
    second Ctrl-C, and left out the ones 0.15.0 listed for comparing its simulator against the
    arm, and with them that nobody has. The one trained policy that release ran drove a twin on
    that simulator, so those steps are what its one result rests on. Written again once 0.15.0
    had shipped, it still counted three of them, when 0.15.0 had made the rest move pressing
    the gripper into the table a step of its own. The note names all of them now, its opening
    and its last Known limitations bullet alike, the opening says the simulator was never
    compared, and PLAN still carries them."""
    release = _release_note("0.16.0")
    opening = _one_line(release.split("\n### ", 1)[0])
    assert "nothing has compared the simulator against the arm" in opening, (
        "CHANGELOG.md's 0.16.0 opening no longer says nothing has compared the simulator "
        "against the arm"
    )
    owed = _one_line(release.split("**The arm has not run quackd since", 1)[1])
    plan = _one_line((REPO / "PLAN.md").read_text(encoding="utf-8"))
    for step in (
        "a second ctrl-c during the fold back to the rest pose",
        "joint signs and zero offsets",
        "the rest move pressing the gripper into the table",
        "the gripper on a real pen",
        "the front and wrist cameras' placement and field of view",
    ):
        assert step in opening, f"CHANGELOG.md's 0.16.0 opening no longer owes {step!r}"
        assert step in owed, f"CHANGELOG.md's 0.16.0 bullet on the bench no longer owes {step!r}"
        assert step in plan, f"PLAN.md no longer carries the bench step {step!r}"


def test_the_policy_releases_opening_claims_no_more_than_was_done() -> None:
    """0.16.0's note first opened by saying the release hands the SO-101 to a learned policy,
    when no policy has driven one, and named pi05 among the checkpoints its server loads with
    nothing above its bullets saying pi05 has never run. The opening says what the release
    gives the arm's pilot now, and keeps every item nobody has done beside what it claims,
    what a policy does on the simulator among them."""
    opening = _one_line(_release_note("0.16.0").split("\n### ", 1)[0])
    assert "hands the so-101 to" not in opening, (
        "CHANGELOG.md's 0.16.0 opening says the release hands the SO-101 to a policy, and no "
        "policy has driven one: say what it gives the arm's pilot instead"
    )
    for never_done in (
        "no policy has driven the real arm",
        "and its judge have not run with a trained checkpoint",
        "pi05 has not run",
        "flux 3 action has not run anywhere",
        "runs on the real bus is unmeasured",
        "what a policy does on the simulator says nothing about the arm",
    ):
        assert never_done in opening, f"CHANGELOG.md's 0.16.0 opening no longer says {never_done!r}"


def test_the_policy_release_claims_no_fix_that_shipped_in_0_15_0() -> None:
    """0.16.0's note was first written before 0.15.0 shipped, and 0.15.0 was held back for the
    fixes the first real pilot on the simulator found, an MCP session's minutes counted from its
    connect among them. The note went on naming that one among its own fixes, in its opening
    and under Fixed. 0.15.0's note carries it, and 0.16.0's does not."""
    shipped = _one_line(_release_note("0.15.0"))
    assert "an mcp session's minutes count from its connect" in shipped, (
        "CHANGELOG.md's 0.15.0 section no longer carries the MCP session's minutes"
    )
    release = _one_line(_release_note("0.16.0"))
    for claim in ("minutes ran negative", "counts its minutes below zero", "-27495.5/5 min"):
        assert claim not in release, (
            f"CHANGELOG.md's 0.16.0 section says {claim!r}, and 0.15.0 shipped that fix"
        )


def test_every_step_of_the_arms_first_run_has_a_mirror_or_a_reason() -> None:
    """Part 2 of the arm's first-run guide mirrors Part 1 step for step, `M07` for `07`, and a
    Part 1 section with no mirror is linked from the note that opens Part 2, with the reason.
    Section 16, the rehearsal on the simulator, was added with neither, which left a reader on
    the Claude path told nothing about a twin, or that an MCP session on one is not seeded."""
    page = (REPO / "docs" / "adapters" / "lerobot" / "first-run.md").read_text(encoding="utf-8")
    part_1, part_2 = page.split("\n## Part 2", 1)
    opening = part_2.split("\n### ", 1)[0]
    mirrored = set(re.findall(r"^### M(\d\d)\. ", part_2, flags=re.MULTILINE))
    for number in re.findall(r"^### (\d\d)\. ", part_1, flags=re.MULTILINE):
        if number not in mirrored:
            assert f"](#{number}-" in opening, (
                f"docs/adapters/lerobot/first-run.md: Part 1's section {number} has no M{number} "
                "in Part 2, and the note that opens Part 2 does not say why"
            )


def test_the_arms_page_gives_a_policy_segment_s_limits_as_the_code_keeps_them() -> None:
    """Each limit a policy segment's loop keeps is a constant in `real.py`, `policy/loop.py` or
    `verbs.py`, and the arm's page says each as a number, so a change to one that leaves the page
    behind fails here. The page's example of the speed cap is worked from the same constants."""
    from quackd_lerobot import real, verbs
    from quackd_lerobot.policy import loop

    page = (REPO / "docs" / "adapters" / "lerobot" / "README.md").read_text(encoding="utf-8")
    section = _one_line(page.split("\n### What `pick` needs", 1)[1].split("\n## ", 1)[0])
    fast = 3 / verbs.TICK_S
    said = [
        f"at {real.POLICY_HZ:g} hz",
        f"past the travel for {real.CLIP_SUSTAIN_S:g} s",
        f"{real.FAILED_SENDS} sends in a row",
        f"read every {real.REGISTER_PERIOD_S:g} s",
        f"more than {real.OUT_OF_RANGE_DEG:g} degrees outside its travel",
        f"runs for {verbs.MANIPULATE_S:g} s unless it ends sooner",
        f"within {verbs.STALL_DEG:g} degrees of where it was for {loop.STALL_S:g} s of goals",
        f"between {loop.MIN_RATE_HZ:g} and {loop.MAX_RATE_HZ:g} hz",
        f"a tick of {verbs.TICK_S:g} s",
        f"at the default {real.MAX_STEP_DEG:g} degrees a policy at {fast:g} hz is capped at "
        f"{loop.speed_cap(real.MAX_STEP_DEG, fast):.1f} degrees a send",
        f"{loop.STARVE_S:g} s of that ends the segment",
        f"{loop.FIRST_CHUNK_S:g} s of grace for the first chunk",
        f"waiting up to {loop.RESET_S:g} s",
    ]
    for words in said:
        assert words in section, (
            f"docs/adapters/lerobot/README.md's pick section no longer says {words!r}"
        )


def test_the_policy_page_quotes_both_sentences_a_missing_server_gets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed port refuses at once on Linux and macOS, and Windows retries it for longer than
    the client waits, so the same missing server is one of two sentences depending on the OS.
    The page once quoted only the Windows one, which the rented GPU's Linux never prints. Both
    are taken from the client here, so a sentence reworded there fails here too."""
    from quackd_lerobot.policy import client
    from quackd_lerobot.verbs import JOINTS

    page = _one_line((REPO / "docs" / "guides" / "policies.md").read_text(encoding="utf-8"))
    for error in (TimeoutError, ConnectionRefusedError):
        runner = client.RemoteRunner("http://127.0.0.1:9875", token="t" * 32, motors=JOINTS)

        def connect(timeout_s: float, error: type[OSError] = error) -> object:
            raise error

        monkeypatch.setattr(runner, "_connection", connect)
        with pytest.raises(client.PolicyServerError) as said:
            runner.policy()
        sentence = _one_line(str(said.value))
        assert sentence in page, (
            f"docs/guides/policies.md does not quote what {error.__name__} says"
        )


def test_what_never_ran_with_a_trained_checkpoint_leaves_out_the_model_that_flew_one() -> None:
    """On 2026-09-29 OpenAI's `gpt-6-sol` flew the lab arm's twin with `quackd run --goal` and
    `--policy-url`, and handed a trained ACT two `manipulate` segments. The release note, the
    README's status row and ADR-0048 each say in one sentence what has never run with a trained
    checkpoint, and all three went on counting a model flying with a policy in it after that run,
    because nothing read them against it. Each now names the run, and no sentence of theirs that
    says never with a trained checkpoint names a model flying."""
    release = _release_note("0.16.0")
    adr = (REPO / "docs" / "adr" / "0048-policies-are-the-arms-executor.md").read_text(
        encoding="utf-8"
    )
    places = {
        "CHANGELOG.md's 0.16.0 Known limitations": release.split("\n### Known limitations\n", 1)[1],
        "README.md's status row for a learned policy": next(
            line for line in README.splitlines() if line.startswith("| A learned policy as the")
        ),
        "ADR-0048's Consequences": adr.split("\n## Consequences\n", 1)[1].split("\n## ", 1)[0],
    }
    for name, text in places.items():
        sentences = re.split(r"(?<=\.) ", _one_line(text))
        never = [s for s in sentences if "never" in s and "trained checkpoint" in s]
        assert never, f"{name} no longer says what has never run with a trained checkpoint"
        flown = [s for s in never if "flying" in s or "flew" in s]
        assert not flown, f"{name} says a model flying with a policy never had one: {flown}"
        assert "2026-09-29" in text and "`quackd run --goal`" in _one_line(text), (
            f"{name} does not name the goal run in which a model flew a trained ACT"
        )


def test_every_licence_quackd_credits_says_where_it_was_read() -> None:
    """A licence is a claim about somebody else's page, and the page is how a reader checks it.

    Every entry in NOTICE ends on the address of the thing it credits, and every row of the
    policy page's licence table that names a checkpoint links the page its licence was read on,
    because that table says it is what each page said on the day. The learned policies' entry
    once had neither, beside a licence summary that turned out to need its source's own words."""
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    entries = re.split(r"\n  \* ", notice.split("\n  * ", 1)[1])
    bare = [entry.split("\n", 1)[0] for entry in entries if "https://" not in entry]
    assert not bare, f"NOTICE credits these with no address: {bare}"
    page = (REPO / "docs" / "guides" / "policies.md").read_text(encoding="utf-8")
    table = page.split("\n## Licences\n", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith("| ") and "/" in line]
    named = [row for row in rows if re.search(r"`[\w.-]+/[\w.-]+`", row)]
    assert named, "docs/guides/policies.md's licence table names no checkpoint"
    unlinked = [row[:60] for row in named if "](https://" not in row]
    assert not unlinked, f"docs/guides/policies.md's licence rows cite no page: {unlinked}"


# ── what a version says, which RELEASING.md reads off the changelog's headings ─────────────

#: The headings RELEASING.md's table names. The first four make a release a minor, so a
#: released patch carries none of them. A heading outside the whole set files entries no rule
#: weighs, which is how a feature under `### New` could reach a patch with nothing to stop it.
_MAKES_A_MINOR = ("Added", "Changed", "Deprecated", "Removed")
_CHANGELOG_HEADINGS = frozenset(
    (*_MAKES_A_MINOR, "Fixed", "Security", "Documentation", "Known limitations")
)
_COMPARE = "https://github.com/rokbenko/quackd/compare/"


def _changelog_sections(text: str) -> list[tuple[str, str]]:
    """Each `## [...]` section of a changelog, as its bracketed name and its body, newest first.
    The last body runs on into the link block, which holds no heading."""
    parts = re.split(r"^## \[([^\]]+)\][^\n]*\n", text, flags=re.M)
    return list(zip(parts[1::2], parts[2::2], strict=True))


def _release_rule_breaches(text: str) -> list[str]:
    """What a changelog does that RELEASING.md's rule for its headings forbids, a line apiece.

    The rule reads both ways. A patch carries none of the headings that make a minor, and a
    minor carries at least one, because a release of fixes alone is a patch whatever number it
    was given. A heading at any level but the third is one no rule weighs, since `#### Added`
    renders as a heading and a check that read only `###` would let it through."""
    breaches = []
    for name, body in _changelog_sections(text):
        version = re.fullmatch(r"\d+\.\d+\.(\d+)", name)
        if not version and name != "Unreleased":
            breaches.append(f"[{name}] is neither Unreleased nor a version X.Y.Z")
        marks = re.findall(r"^(#+) *(.*?)\s*$", body, flags=re.M)
        other_levels = [f"{hashes} {title}" for hashes, title in marks if hashes != "###"]
        if other_levels:
            breaches.append(f"[{name}] has {other_levels}, at a level no rule weighs")
        headings = [title for hashes, title in marks if hashes == "###"]
        unknown = sorted(set(headings) - _CHANGELOG_HEADINGS)
        if unknown:
            breaches.append(f"[{name}] files entries under {unknown}, which no rule weighs")
        twice = sorted({h for h in headings if headings.count(h) > 1})
        if twice:
            breaches.append(f"[{name}] carries {twice} twice")
        minor = [h for h in headings if h in _MAKES_A_MINOR]
        if version and int(version.group(1)) > 0 and minor:
            breaches.append(f"[{name}] is a patch and carries {minor}, which make a minor")
        if version and int(version.group(1)) == 0 and not minor:
            breaches.append(f"[{name}] is a minor and carries none of {list(_MAKES_A_MINOR)}")
    return breaches


def test_a_released_patch_carries_nothing_that_makes_a_minor() -> None:
    """RELEASING.md: while quackd is 0.x a patch changes nothing a user has to act on and adds
    nothing to learn, and the changelog's headings say which a release is. So a released
    section whose version has a patch number above zero carries none of the four headings that
    make a minor, and documentation has a heading of its own so that it never needs one. Every
    section, `[Unreleased]` included, keeps to the headings the rule names, each at most once:
    an entry under any other is one nothing weighs, and a heading that appears twice in a
    section is how 0.12.0's audit found one release's `Fixed` pasted into two shipped ones."""
    breaches = _release_rule_breaches((REPO / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert not breaches, "CHANGELOG.md breaks RELEASING.md's rule:\n" + "\n".join(breaches)


def test_the_patch_rule_catches_each_way_round_it() -> None:
    """Until 0.16.1 ships, no released section has a patch number above zero, so the guard
    above passes on a changelog it has never had to refuse. It proves itself here instead, on
    synthetic sections: a patch of fixes and documentation passes, and each heading that makes
    a minor, the same heading one level down, a heading no rule names, a heading pasted in
    twice, a bracket that is not a version and a release of fixes alone numbered as a minor are
    each caught."""

    def changelog(*sections: str) -> str:
        return "# Changelog\n\n" + "\n".join(sections)

    patch = (
        "## [0.3.1] — 2026-01-02\n\nOne fix.\n\n"
        "### Fixed\n\n- a fix\n\n### Documentation\n\n- a page\n"
    )
    minor = "## [0.3.0] — 2026-01-01\n\n### Added\n\n- a verb\n\n### Known limitations\n\n- one\n"
    assert _release_rule_breaches(changelog(patch, minor)) == []
    for heading in _MAKES_A_MINOR:
        feature = patch.replace("### Documentation", f"### {heading}")
        assert _release_rule_breaches(changelog(feature, minor)), f"a patch under {heading}"
        deeper = patch.replace("### Documentation", f"#### {heading}")
        assert _release_rule_breaches(changelog(deeper, minor)), f"a patch under #### {heading}"
    assert _release_rule_breaches(changelog(patch.replace("### Fixed", "## Fixed"), minor))
    assert _release_rule_breaches(changelog(patch.replace("### Fixed", "### New"), minor))
    assert _release_rule_breaches(changelog(patch + "\n### Fixed\n\n- pasted\n", minor))
    assert _release_rule_breaches(changelog("## [Unreleased]\n\n### Features\n\n- x\n", minor))
    assert _release_rule_breaches(changelog("## [0.3]\n\n### Fixed\n\n- x\n", minor))
    fixes_as_a_minor = patch.replace("[0.3.1]", "[0.4.0]")
    assert _release_rule_breaches(changelog(fixes_as_a_minor, minor)), "fixes alone as a minor"


def test_every_release_links_the_compare_the_procedure_writes() -> None:
    """Step 4 of RELEASING.md: `[Unreleased]:` compares the newest tag with HEAD, and each
    release compares the tag before it with its own, newest first, down to 0.1.0, which links
    its own tag. A patch is the first release to compare within one minor, and nothing else
    reads these lines, so a link that skipped a release or pointed at the wrong one would ship."""
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    versions = [name for name, _ in _changelog_sections(text) if name != "Unreleased"]
    order = [tuple(int(n) for n in v.split(".")) for v in versions]
    assert order == sorted(order, reverse=True), (
        f"CHANGELOG.md's releases are out of order: {versions}"
    )
    lines = set(text.splitlines())
    wanted = [f"[Unreleased]: {_COMPARE}v{versions[0]}...HEAD"]
    wanted += [f"[{new}]: {_COMPARE}v{old}...v{new}" for new, old in itertools.pairwise(versions)]
    first = versions[-1]
    wanted.append(f"[{first}]: https://github.com/rokbenko/quackd/releases/tag/v{first}")
    missing = [line for line in wanted if line not in lines]
    assert not missing, "CHANGELOG.md's link block does not say:\n" + "\n".join(missing)


def test_the_release_order_lives_in_releasing_md_and_every_page_that_names_it_links_there() -> None:
    """The release checklist was PLAN.md's last section until 0.16.1 moved it into RELEASING.md,
    beside the rules for which release is which. PLAN.md keeps one line pointing there and no
    step of its own, since a step added back would be a second list that drifts. The README,
    LAUNCH.md and CONTRIBUTING.md each say something RELEASING.md decides, so each links it,
    and ADR-0037, whose Consequences put the order in PLAN.md, carries a note that does. No
    living document sends a reader to PLAN.md for a release any more, except RELEASING.md's own
    procedure, which says what a release does to PLAN.md's open items: every release commit
    from 0.13.0 to 0.16.0 edited them, and the checklist that moved never said so."""
    plan = (REPO / "PLAN.md").read_text(encoding="utf-8")
    checklist = plan.split("\n## Release checklist\n", 1)[1].split("\n## ", 1)[0]
    assert "](RELEASING.md)" in checklist, "PLAN.md's release checklist does not point on"
    assert not re.search(r"^\s*\d+\. ", checklist, flags=re.M), "PLAN.md has release steps again"
    for name in ("README.md", "LAUNCH.md", "CONTRIBUTING.md"):
        assert "](RELEASING.md)" in (REPO / name).read_text(encoding="utf-8"), (
            f"{name} does not link RELEASING.md"
        )
    adr = (REPO / "docs" / "adr" / "0037-adapters-are-their-own-packages.md").read_text(
        encoding="utf-8"
    )
    assert "](../../RELEASING.md)" in adr.split("\n## Context\n", 1)[0], (
        "ADR-0037's note above its Context does not link RELEASING.md"
    )
    steps = _one_line(_releasing_section("Cutting a release"))
    assert "plan.md's open items" in steps, (
        "RELEASING.md's procedure no longer says what a release does to PLAN.md's open items"
    )
    stale = [
        f"{path.relative_to(REPO)}: {sentence[:100]}"
        for path in _living_docs()
        if path.name != "RELEASING.md"
        for sentence in re.split(r"(?<=\.)\s", _one_line(_prose(path.read_text(encoding="utf-8"))))
        if "plan.md" in sentence and re.search(r"releas|set_version|pypi|the full order", sentence)
    ]
    assert not stale, "these still send a reader to PLAN.md for a release:\n" + "\n".join(stale)


def _releasing_section(heading: str) -> str:
    """One `## ` section of RELEASING.md, up to the next."""
    text = (REPO / "RELEASING.md").read_text(encoding="utf-8")
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


#: What RELEASING.md lets a patch carry that a reader could take for a minor's. The patch rule
#: names each, and a row of its table and CONTRIBUTING.md's list for `Fixed` have to file each
#: under a heading that keeps the release a patch.
_A_PATCH_CARRIES = ("catalogue data", "dependency", "safety fix", "refusal of what never did")


def test_releasing_md_contributing_md_and_the_guard_file_every_entry_alike() -> None:
    """The guard above, RELEASING.md's table, the command its step 1 decides the number with,
    and the list CONTRIBUTING.md gives a contributor are four copies of one rule. They
    disagreed in the first draft. CONTRIBUTING.md filed a field a fix adds to a record, and a
    refusal a fix tightens, under the headings that make a minor, where RELEASING.md files both
    under `Fixed`, so a contributor who followed it would have turned 0.16.1 into 0.17.0. And
    the patch rule named catalogue data and dependency fixes as a patch's while no row of the
    table let them stay one."""
    rules = _releasing_section("While quackd is 0.x")
    rows = re.findall(r"^\| `([^`]+)` \| (.+?) \| (.+?) \|$", rules, flags=re.M)
    table = {heading: (what, release) for heading, what, release in rows}
    assert set(table) == _CHANGELOG_HEADINGS, f"RELEASING.md's table names {sorted(table)}"
    minors = {heading for heading, (_, release) in table.items() if release == "a minor"}
    assert minors == set(_MAKES_A_MINOR), f"RELEASING.md's table makes a minor of {minors}"
    patches = {heading for heading, (_, release) in table.items() if release == "a patch"}
    assert patches == {"Fixed", "Security"}, f"RELEASING.md's table makes a patch of {patches}"

    rule = _one_line(rules.split("**A patch, 0.Y.Z+1,", 1)[1].split("\n\n", 1)[0])
    kept = _one_line(" ".join(what for heading, (what, _) in table.items() if heading in patches))
    for kind in _A_PATCH_CARRIES:
        assert kind in kept, f"no row of RELEASING.md's table keeps a patch for {kind}"
    for kind in _A_PATCH_CARRIES[:3]:
        assert kind in rule, f"RELEASING.md's rule for a patch no longer names {kind}"

    step_one = re.search(r"\^### \(([A-Za-z|]+)\)\$", _releasing_section("Cutting a release"))
    assert step_one, "RELEASING.md's step 1 has no command that prints the headings of a minor"
    assert set(step_one.group(1).split("|")) == set(_MAKES_A_MINOR), (
        f"RELEASING.md's step 1 reads {step_one.group(1)} as the headings of a minor"
    )

    contributing = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    versions = contributing.split("\n## Versions and releases\n", 1)[1].split("\n## ", 1)[0]
    bullets = [item.split("\n\n", 1)[0] for item in versions.split("\n- ")[1:]]
    filed = {}
    for bullet in bullets:
        headings = {word for word in re.findall(r"`([^`]+)`", bullet) if word in table}
        said = _one_line(bullet)
        release = "a minor" if "a minor" in said else "a patch" if "a patch" in said else None
        filed[frozenset(headings)] = (release, said)
    assert filed.get(frozenset(_MAKES_A_MINOR), ("",))[0] == "a minor", (
        "CONTRIBUTING.md has no bullet that files the four minor headings as a minor"
    )
    release, fixed = filed.get(frozenset(patches), (None, ""))
    assert release == "a patch", "CONTRIBUTING.md has no bullet that files Fixed as a patch"
    for kind in (*_A_PATCH_CARRIES, "field a fix adds"):
        assert kind in fixed, f"CONTRIBUTING.md files {kind} under a heading that makes a minor"


def test_a_lock_refresh_raises_no_bump_and_dependabot_writes_only_the_lock() -> None:
    """0.16.1 took Dependabot's grouped refresh of `uv.lock` (#31), and RELEASING.md's table had
    no row for one: `Fixed` takes a dependency fix, which a refresh is not, and nothing else in
    the table named a dependency. A refresh changes what a checkout and CI install and no
    requirement a user installs against, since a wheel's requirements are its `pyproject.toml`'s,
    so the table files it under `Documentation`, which raises no bump, the rule says why,
    CONTRIBUTING.md tells a pull request the same, and the lock is not on the surface. What
    makes that true of Dependabot's pull requests is its `lockfile-only` strategy for uv, which
    never writes a `pyproject.toml`, so this fails if the strategy goes."""
    rules = _releasing_section("While quackd is 0.x")
    rows = re.findall(r"^\| `([^`]+)` \| (.+?) \| (.+?) \|$", rules, flags=re.M)
    filed = {heading: release for heading, what, release in rows if "`uv.lock`" in what}
    assert filed == {"Documentation": "no bump of its own"}, (
        f"RELEASING.md's table files a refresh of uv.lock as {filed}"
    )
    why = "a refresh changes no requirement a user installs against"
    assert why in _one_line(rules), "RELEASING.md does not say why a lock refresh raises no bump"
    surface = _releasing_section("What a version number speaks about")
    assert "`uv.lock`" in _one_line(surface.split("Not part of it:", 1)[1]), (
        "RELEASING.md does not leave uv.lock off the surface"
    )
    contributing = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    versions = _one_line(contributing.split("\n## Versions and releases\n", 1)[1].split("\n## ")[0])
    assert "so does a refresh of `uv.lock`, which changes no requirement a user installs" in (
        versions
    ), "CONTRIBUTING.md does not file a lock refresh under Documentation"

    import yaml

    config = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    (uv,) = [job for job in config["updates"] if job["package-ecosystem"] == "uv"]
    assert uv["versioning-strategy"] == "lockfile-only", (
        "Dependabot's uv job may now write a pyproject.toml, so its pull requests are more than a "
        "lock refresh"
    )


def test_the_surface_releasing_md_lists_holds_what_quackd_prints_for_a_script() -> None:
    """`--json` is the one output quackd's own help says is for a script rather than a person,
    and eleven commands take it. The surface RELEASING.md lists first left it out, so its rules
    could not say whether a patch may rename a key a script reads."""
    cli = (REPO / "quackd" / "cli.py").read_text(encoding="utf-8")
    assert '"--json"' in cli and "for a script" in cli, "quackd/cli.py has no `--json` for scripts"
    surface = _releasing_section("What a version number speaks about")
    assert "`--json`" in surface, "RELEASING.md's surface does not name what `--json` prints"


def test_the_release_procedure_writes_outside_the_checkout_and_tags_only_what_ci_ran() -> None:
    """Step 10 builds from `git archive` because a file the checkout has and no commit does
    would ship, and the first draft then wrote the tag message, the release body and three
    hash lists into the checkout, where nothing ignores them and a root `body.md` is a living
    document the guards read. Every file a step writes goes under the scratch directory the
    setup line makes. And a patch cut from the last tag is never merged into `main` before its
    tag, so without CI on its release branch the `packaging`, `physics` and `policy` jobs would
    first run on a tag already public. The tag and the release body keep the maintainer's voice
    rules but not the length rule, which is for a reply to one person."""
    steps = _releasing_section("Cutting a release")
    fences = re.findall(r"```bash\n(.*?)```", steps, flags=re.S)
    assert re.search(r'\bout="\$\(mktemp -d\)"', fences[0]), (
        "RELEASING.md makes no scratch directory before step 1"
    )
    written = [
        target
        for fence in fences
        for target in re.findall(r'(?:(?<![0-9&])>|-F|--notes-file)\s+("?[^\s|;&)]+)', fence)
        if target != "/dev/null" and not target.startswith('"$out/')
    ]
    assert not written, f"RELEASING.md's steps write these into the checkout: {written}"

    import yaml

    ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    push = ci.get("on", ci.get(True))["push"]  # YAML 1.1 reads a bare `on:` key as true
    assert "release/**" in push["branches"], "ci.yml does not run on a push to a release branch"
    assert "git push -u origin release/$v" in steps, "RELEASING.md never pushes a release branch"

    voice = _one_line(steps)
    assert "contributing.md#how-the-reply-is-written" in voice
    assert "rule on length is for a reply to one person" in voice, (
        "RELEASING.md holds a minor's release body to the length rule for a reply"
    )


def test_releasing_md_checks_that_its_first_mypy_runs_under_3_11() -> None:
    """Step 7 type-checks under 3.11 and 3.12, because CI's 3.12 jobs see stricter numpy
    stubs, and its first mypy runs in whatever `.venv` is. That is 3.11 in the main checkout
    and nothing makes it so anywhere else: 0.16.1 was cut in a worktree whose `.venv` is 3.12,
    where the step as written would have checked 3.12 twice and said nothing. So the line
    before that mypy fails unless the interpreter it runs in is 3.11, and the other mypy names
    `.venv312`."""
    steps = _releasing_section("Cutting a release")
    fence = next(
        fence for fence in re.findall(r"```bash\n(.*?)```", steps, flags=re.S) if "mypy" in fence
    )
    lines = [line.strip() for line in fence.split("\n")]
    assert "UV_PROJECT_ENVIRONMENT=.venv312 uv run --no-sync mypy" in lines, (
        "RELEASING.md's step 7 no longer type-checks in .venv312"
    )
    first = lines.index("uv run --no-sync mypy")
    check = lines[first - 1]
    assert check.startswith("uv run --no-sync python -c") and (
        "sys.version_info[:2] == (3, 11)" in check
    ), f"RELEASING.md's step 7 runs its first mypy without checking it is 3.11, after {check!r}"


def test_releasing_md_runs_no_line_past_its_wrap() -> None:
    """RELEASING.md is read as source with a release in progress, and wraps at about 96
    columns. A prose line past 100 is one a sentence was spliced into and nobody reflowed,
    which is how its first draft left the registry's 1.0.0 condition at 136. A line that is one
    link or one code span too long to break is a break somebody chose, and so are tables and
    fenced commands."""
    name = "RELEASING.md"
    lines = (REPO / name).read_text(encoding="utf-8").split("\n")
    fenced = False
    long = []
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or len(line) <= 100 or line.lstrip().startswith("|"):
            continue
        words = re.findall(r"\(?\[[^\]]*\]\([^)]*\)\S*|`[^`]*`\S*|\S+", line)
        if any(len(word) > 60 for word in words):
            continue  # one link or code span too long to break
        long.append(f"line {number}: {len(line)} columns")
    assert not long, f"{name} runs these lines past its wrap: {long}"
