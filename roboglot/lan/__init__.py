"""Finding Roboglot robots on a LAN: zeroconf (`_roboglot._tcp.local.`) behind `roboglot[lan]`.

The wire format (`txt.py`) is a pure module with no third-party import, so it is tested
without the extra. `announce` and `discover` import zeroconf lazily and take injectable
clients, so every test runs on fakes and the default install never imports zeroconf
(ADR-0021). A TXT record carries identity only (manifest id, digest, adapter, body); the
full manifest is fetched out of band and checked against the digest.
"""

from __future__ import annotations

from typing import Any


class LanNotInstalled(RuntimeError):
    """The `lan` extra is not installed here."""

    def __init__(self, what: str) -> None:
        super().__init__(f"{what} needs an extra: uv pip install 'roboglot[lan]'")
        self.what = what


def require_zeroconf(what: str) -> Any:
    """The `zeroconf` module, or a `LanNotInstalled` that names the extra."""
    try:
        import zeroconf
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise LanNotInstalled(what) from e
    return zeroconf


__all__ = ["LanNotInstalled", "require_zeroconf"]
