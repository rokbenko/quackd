#!/usr/bin/env python3
"""Write `web/src/catalogue.js` from `quackd/agent/providers/catalogue.py`.

The browser demo needs the LLM list, and it cannot import Python. So there are two copies of
it, and the only safe number of hand-maintained copies is one: this generates the second from
the first, and `tests/test_web.py` fails if the committed file is not what `render()` produces.
That is the same bargain `test_the_browser_pins_the_same_upstream_commits_python_does` already
strikes for the upstream pins, paid by a script instead of by hand.

Every vendor in `CLOUD_NAMES` is emitted, including the ones the page cannot offer. The page
decides what to put in its dropdown in `web/src/providers.js`; this file decides nothing, so the
data stays one to one with Python and a vendor that becomes callable from a browser later is a
change in one place.

    python web/build_catalogue.py

Stdlib only, like `web/serve.py` beside it. `quackd.agent.providers.catalogue` imports nothing
but the standard library either, so this runs without the vendor SDKs installed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Run as `python web/build_catalogue.py`, sys.path[0] is `web/`, so `quackd` would resolve to
# whatever is installed in the environment rather than to the checkout this file sits in. The
# repository root goes first, so the generated file always describes THIS tree's catalogue.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quackd.agent.providers.catalogue import (
    CATALOGUE,
    CLOUD_NAMES,
    STATUSES,
    default_llm_for,
)

OUT = Path(__file__).resolve().parent / "src" / "catalogue.js"

#: How the file says it is not to be edited. The command is spelled out because the reader who
#: opens this file is the reader who just tried to edit it.
HEADER = """\
/**
 * GENERATED FILE. Do not edit.
 *
 * The LLM list, copied out of `quackd/agent/providers/catalogue.py`, which is the only place
 * an LLM is ever added. Regenerate after editing that file:
 *
 *     python web/build_catalogue.py
 *
 * `tests/test_web.py` fails if this file and that one disagree, so the two cannot drift.
 *
 * Every cloud vendor quackd knows is here, including the ones this page cannot call: a vendor
 * whose API refuses a cross-origin preflight stays out of `PROVIDERS` in `providers.js` and is
 * named in `web/README.md` with the reason. The data stays whole either way.
 */
"""


def render() -> str:
    """The exact text of `web/src/catalogue.js`, newline-terminated and LF throughout."""
    catalogue = {
        vendor: {
            "default": default_llm_for(vendor),
            "entries": [
                {
                    "id": spec.id,
                    "label": spec.label,
                    "status": spec.status,
                    "vision": spec.vision,
                    "api": spec.api,
                    "forced_tools": spec.forced_tools,
                }
                for spec in CATALOGUE[vendor]
            ],
        }
        for vendor in CLOUD_NAMES
    }
    return (
        HEADER
        + "\n"
        + f"export const STATUS_ORDER = {json.dumps(list(STATUSES))};\n"
        + "\n"
        + f"export const CATALOGUE = {json.dumps(catalogue, indent=2)};\n"
    )


def main() -> None:
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    llms = sum(len(CATALOGUE[vendor]) for vendor in CLOUD_NAMES)
    print(f"wrote {OUT} — {len(CLOUD_NAMES)} vendors, {llms} LLMs")


if __name__ == "__main__":
    main()
