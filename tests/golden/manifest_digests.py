"""The digest and the verb list of every manifest two bodies of one family can describe.

`tests/test_microduck_body.py` holds the code to `manifest_digests.json`, which is how a refactor
of the family says it changed no description: a digest changes with anything a pilot or a peer
reads off a manifest, and the verb list says in what order. Run
`uv run python -m tests.golden.manifest_digests` only when a manifest is meant to change, and say
why in the commit: a digest is the fingerprint a LAN announce carries (`quackd.lan.txt`).
This is on its own and not in `generate.py` because that one rewrites the simulators' goldens too.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

GOLDEN = Path(__file__).resolve().parent / "manifest_digests.json"

TRI = (None, True, False)
"""What a connection may say of a thing, and what a description says before one: yes, no, unset."""


def digests() -> dict[str, Any]:
    """Every manifest over what a connection can settle: `{body: {case: digest}}`, and `verbs`,
    what each digest offers in order."""
    from quackd_huebot import BACKENDS as HUEBOT_BACKENDS
    from quackd_huebot import huebot_manifest
    from quackd_huebot.verbs import KICK_SKILLS, SIT_TOGGLE
    from quackd_microduck import BACKENDS, microduck_manifest

    microduck: dict[str, str] = {}
    huebot: dict[str, str] = {}
    verbs: dict[str, list[str]] = {}
    for backend, camera, places, mapped, sized, fov in itertools.product(
        BACKENDS, (True, False), TRI, TRI, TRI, (None, 62.2)
    ):
        manifest = microduck_manifest(
            backend,
            camera=camera,
            camera_fov_deg=fov,
            camera_fov_assumed="the words of an assumption" if fov else None,
            places=places,
            mapped=mapped,
            sized=sized,
        )
        digest = manifest.digest()
        microduck[f"{backend} {camera} {places} {mapped} {sized} {fov}"] = digest
        verbs[digest] = manifest.verb_names()
    kicks = tuple(KICK_SKILLS.values())
    for backend, camera, skills in itertools.product(
        HUEBOT_BACKENDS,
        (True, False),
        (None, (), (SIT_TOGGLE, *kicks), (SIT_TOGGLE,), ("kick_left",), kicks),
    ):
        manifest = huebot_manifest(backend, camera=camera, skills=skills)
        digest = manifest.digest()
        said = "unset" if skills is None else ",".join(skills) or "none"
        huebot[f"{backend} {camera} {said}"] = digest
        verbs[digest] = manifest.verb_names()
    return {"microduck": microduck, "huebot": huebot, "verbs": verbs}


def main() -> int:
    GOLDEN.write_text(
        json.dumps(digests(), indent=0, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {GOLDEN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
