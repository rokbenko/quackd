"""What the page remembers: one setup, shared by every tab and phone, and the keys typed into it.

The setup is the set of choices a run is made from: the option values `options.argv_for` turns
into a command, and the pickers' own state, under names that start with `_`. It lives in the page's
process rather than in a tab, so a phone picks up what a desktop tab chose, and a session Streamlit
restarts after a locked screen finds it as it was. It is saved for the next start without a
secret in it: secret options are never set here, and the keys typed into the page live in this
process's memory alone (`keys`), reaching a run through its environment.
"""

from __future__ import annotations

import contextlib
import json
import threading
from pathlib import Path
from typing import Any

SETUP_FILE = "ui-setup.json"


class Setup:
    """The page's choices, behind a lock, versioned so every tab can tell it changed."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.values: dict[str, Any] = {}
        self.keys: dict[str, str] = {}
        """Keys typed into the page, by the variable each reaches a run under. Never saved."""
        self.version = 0
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        with contextlib.suppress(OSError, ValueError):
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.values = {str(k): v for k, v in saved.items()}

    def _save(self) -> None:
        """Saved private, as the registry keeps a token: it holds no key, but an address or an
        extra body may carry a credential in it, as `quackd.command` knows they can."""
        if self.path is None:
            return
        from quackd.registry import RegistryError, write_json

        with contextlib.suppress(OSError, RegistryError):
            write_json(self.path, dict(sorted(self.values.items())), private=True)

    def get(self, name: str, default: Any = None) -> Any:
        with self._lock:
            return self.values.get(name, default)

    def set(self, name: str, value: Any) -> None:
        with self._lock:
            if name in self.values and self.values[name] == value:
                return
            self.values[name] = value
            self.version += 1
            self._save()

    def drop(self, name: str) -> None:
        with self._lock:
            if self.values.pop(name, None) is not None:
                self.version += 1
                self._save()

    def set_key(self, env: str, value: str) -> None:
        with self._lock:
            if value.strip():
                self.keys[env] = value.strip()
            else:
                self.keys.pop(env, None)
            self.version += 1

    def snapshot(self) -> dict[str, Any]:
        """Every value, as it stands, in one copy."""
        with self._lock:
            return dict(self.values)

    def options(self) -> dict[str, Any]:
        """The option values alone, for `options.argv_for`."""
        with self._lock:
            return {k: v for k, v in self.values.items() if not k.startswith("_")}


_SETUP: Setup | None = None
_SETUP_LOCK = threading.Lock()


def setup(path: Path | None = None) -> Setup:
    """The page process's one setup, read on first use from the home `quackd ui` keeps its token
    in, which a `.env` never chooses (`launch.Config.home`)."""
    global _SETUP
    with _SETUP_LOCK:
        if _SETUP is None:
            if path is None:
                from quackd.registry import registry_dir
                from quackd.webui import launch

                home = launch.CONFIG.home if launch.CONFIG and launch.CONFIG.home else None
                path = (home or registry_dir()) / SETUP_FILE
            _SETUP = Setup(path)
        return _SETUP
