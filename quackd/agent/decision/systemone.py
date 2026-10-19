"""Every decision LLM that speaks `POST /v1/systemone`, over one client.

TypeSafe published the format and the client; Kev, Von, OpenJev, OpenDecision and the rest
implement it and document the same sentence -- point the SDK at our base URL. So this is one
class with a row of data in front of it rather than a module per vendor, and a server quackd
has never heard of is `--decision-llm local --decision-url` and no code at all.

`typesafe_sdk` is the transport for all of them because it is the protocol's reference client:
it owns the retry policy, the typed errors and the `int | None` usage the billing already
reads tolerantly. The extra that installs it is named `decision` rather than for the vendor
for exactly that reason.

Imported only when a run has named one of these. Nothing at module scope touches the SDK.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from quackd.agent.decision.base import NO_KEY as NO_KEY
from quackd.agent.decision.base import TIMEOUT_S, DecisionNotInstalled, client_timeout

if TYPE_CHECKING:
    from quackd.agent.decision.catalogue import DecisionSpec

# `NO_KEY` lives in `base.py` now, because `decisions_api.py` sends it too, and is re-exported
# here so that everything which knew it as this module's still finds it. A row with a `key_env`
# sends nothing instead and lets the SDK read the variable itself; every other row, and a keyed
# one reached at an address somebody typed, sends the word.

MAX_RETRIES = 1


class SystemOneLLM:
    """One System One server, reached where its row says.

    Built while the CLI is still parsing rather than on the first turn: constructing a client
    can fail -- a missing extra, an SDK that renamed its own constructor -- and that is a
    sentence before the robot connects, not a phantom cost on a turn that never left the
    machine.
    """

    def __init__(self, spec: DecisionSpec, *, model: str | None = None, url: str | None = None):
        try:
            sdk = importlib.import_module("typesafe_sdk")
        except ImportError as e:  # pragma: no cover - exercised by tests/test_extras_absent
            raise DecisionNotInstalled(spec.name, spec.extra or "decision") from e
        self.name = spec.name
        self.model = model or ""
        self.url = url
        # A row with its own key env hands the SDK nothing and lets it read that variable, with
        # its own validation; a row without one is a server that wants no key, and gets a word
        # rather than the hosted key that may be sitting in the environment beside it.
        #
        # The second half of that condition is the interesting one. A keyed row reached at an
        # address somebody typed is no longer the hosted service: `--decision-llm jev
        # --decision-url http://localhost:8009` means "Jev's model id, at my address", and
        # sending a company's API key to whatever is listening on that port is precisely what
        # `SECURITY.md` lists as a thing that must not happen. An explicit address makes it a
        # server you run, and a server you run gets the word.
        #
        # `factory.sends_key` states this same rule for the two questions asked before any
        # client exists, whether the key is wanted and what a turn costs, and
        # `tests/test_decision_llms.py` holds the two to each other.
        api_key = None if (spec.key_env and not url) else NO_KEY
        self._client: Any = sdk.AsyncTypeSafeClient(
            **({"api_key": api_key} if api_key is not None else {}),
            **({"base_url": url} if url else {}),
            **({"model": self.model} if self.model else {}),
            # Two different numbers, and the difference is the whole reason this is spelled out.
            #
            # `timeout` is a clock: the SDK hands it to httpx2 for each operation of a request,
            # the connect and each read and write, and its default is ten seconds ("Default
            # timeout in seconds for each HTTP operation", typesafe-sdk 0.7.2, read 2026-10-10).
            # Leaving it there left a turn able to wait ten, which is a promise the docs made and
            # the code did not keep. It sits above the stepper's second rather than on it
            # (`base.client_timeout`). Set to that same second, it came due with the stepper's
            # bound, so a slow server's turn could be recorded either way, as quackd's `no answer
            # within 1.0 s` or as the SDK's own `TypeSafeAPITimeoutError`, whichever the event
            # loop reached first, as the openai row's two equal clocks were seen to do against a
            # stand-in. Above it, the stepper's bound ends every turn that runs out, one way, and
            # this is a backstop for whoever calls `decide` with nothing around it.
            #
            # `RetryPolicy.timeout` is no clock at all. It is the budget a retry has to fit
            # inside, read when an attempt fails and before the next one starts ("Stops before a
            # retry whose delay would reach or exceed the budget", the same release), so it can
            # fire on nothing, and at the stepper's second it keeps the SDK from sleeping
            # towards a retry the turn would not live to send.
            timeout=client_timeout(TIMEOUT_S),
            retry=sdk.RetryPolicy(max_retries=MAX_RETRIES, backoff_max=0.2, timeout=TIMEOUT_S),
        )

    async def decide(
        self, state: Mapping[str, str], questions: Mapping[str, Mapping[str, Any]]
    ) -> Any:
        """One fan-out. The questions go as plain dicts, which the SDK takes beside its own
        `Choice` and `Noul`, so the same four reach every backend unrebuilt."""
        return await self._client.system_one(dict(state), dict(questions))
