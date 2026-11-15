"""`python -m roboglot.adapters.export` regenerates `manifest.schema.json` from the model.

Committed so other languages and editors can validate a manifest without importing
Roboglot; a test asserts it never drifts from the pydantic model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from roboglot.adapters.manifest import manifest_json_schema

SCHEMA_PATH = Path(__file__).with_name("manifest.schema.json")


def write_schema(path: Path = SCHEMA_PATH) -> Path:
    path.write_text(
        json.dumps(manifest_json_schema(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return path


if __name__ == "__main__":
    out = write_schema(Path(sys.argv[1]) if len(sys.argv) > 1 else SCHEMA_PATH)
    sys.stdout.write(f"wrote {out}\n")
