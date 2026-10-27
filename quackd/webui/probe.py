"""Check a robot's connection, moving nothing, in a process of its own.

    python -P -m quackd.webui.probe '{"spec": "lerobot:real", "address": "COM5"}'
    python -P -m quackd.webui.probe '{"registered": "arm-01"}'

The page's Check button runs this rather than probing in its own process, so a robot's SDK, torch
for the arm among them, and its serial port never load into the page, and a probe that hangs is a
child the page can let go of. It is `quackd robot list --probe` for one robot, registered or not
yet (`registry.probe_entry`): connect, ask how it is, close, and never drive it anywhere. A token
comes through the environment, under the variable the request names, never on the command line.

It prints one JSON line, `{"reachable": true | false | null, "detail": ..., "elapsed_s": ...}`,
`null` for a backend a probe does not reach, and exits 0 whatever the robot said.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any


def probe(request: dict[str, Any]) -> dict[str, Any]:
    from quackd.registry import Registry, RegistryError, RobotEntry, probe_entry

    try:
        if request.get("registered"):
            entry = Registry(request.get("registry_dir")).robots()[str(request["registered"])]
        else:
            token_env = request.get("token_env")
            entry = RobotEntry(
                name=str(request.get("name") or "probe"),
                spec=str(request["spec"]),
                address=request.get("address") or None,
                token=(os.environ.get(token_env) or None) if token_env else None,
                camera_url=request.get("camera_url") or None,
            )
    except (KeyError, RegistryError, ValueError) as e:
        return {"reachable": False, "detail": f"{type(e).__name__}: {e}", "elapsed_s": 0.0}
    result = asyncio.run(probe_entry(entry))
    return {"reachable": result.reachable, "detail": result.detail, "elapsed_s": result.elapsed_s}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        request = json.loads(args[0]) if args else {}
    except ValueError as e:
        request = {"_error": str(e)}
    if not isinstance(request, dict) or "_error" in request:
        answer = {
            "reachable": False,
            "detail": "the request is not a JSON object",
            "elapsed_s": 0.0,
        }
    else:
        answer = probe(request)
    sys.stdout.write(json.dumps(answer, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
