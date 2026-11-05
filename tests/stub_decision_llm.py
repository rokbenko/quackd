"""A third party's decision LLM, as one would actually ship.

This is the whole contract a plugin has to meet, and it is short on purpose: the point of the
entry point group is that quackd never has to know about you. A real one would declare itself
in its own `pyproject.toml`:

    [project.entry-points."quackd.decision_llms"]
    stub = "tests.stub_decision_llm"

and `tests/test_decision.py` stands in for that declaration by monkeypatching `entry_points`,
because installing a distribution mid-suite is not a thing a test should do.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SUMMARY = "a decision LLM that exists only in this test"
LLM_ID = "stub-1"
INSTALL = "it is already here"


class StubLLM:
    """What `make` hands back: a name, an LLM, an address and one method."""

    def __init__(self, name: str, llm_id: str | None, url: str | None) -> None:
        self.name = name
        self.llm_id = llm_id or LLM_ID
        self.url = url
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.answers: dict[str, Any] = {}

    async def decide(
        self, state: Mapping[str, str], questions: Mapping[str, Mapping[str, Any]]
    ) -> Any:
        self.calls.append((dict(state), dict(questions)))
        # A plain mapping, the way a server's JSON and an in-process LLM both answer.
        return {"answers": dict(self.answers)}


#: The last one built, so a test can assert what `make_decision_llm` passed it.
built: list[StubLLM] = []


def make(spec: Any, *, url: str | None = None, llm_id: str | None = None) -> StubLLM:
    llm = StubLLM(spec.name, llm_id, url)
    built.append(llm)
    return llm
