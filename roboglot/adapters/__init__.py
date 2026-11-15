"""Robots are adapters: each one declares a `RobotManifest` and speaks the `RobotAdapter`
protocol, and the verb registry is built from that manifest at connect time (ADR-0017).

This package init imports nothing eagerly. `adapters.manifest` needs `verbs.registry` and
`verbs.core` needs `adapters.manifest`, so the re-exports below resolve lazily to keep the
import graph acyclic whichever module is imported first.
"""

from __future__ import annotations

from typing import Any

__all__ = ["AdapterError", "AdapterNotInstalled", "RobotAdapter", "RobotManifest"]


def __getattr__(name: str) -> Any:
    if name in ("RobotAdapter", "AdapterError", "AdapterNotInstalled", "backend_name"):
        from roboglot.adapters import base

        return getattr(base, name)
    if name in ("RobotManifest", "VerbSpec", "Health"):
        from roboglot.adapters import manifest

        return getattr(manifest, name)
    raise AttributeError(name)
