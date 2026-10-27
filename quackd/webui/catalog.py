"""What the page's pickers offer: robots, pilots, policies, decision LLMs and keys, read from the
catalogues quackd already keeps, never copied into the page.

Everything here is data a terminal command already prints in its own way (`list-adapters`,
`list-models`, `robot list`, `doctor`), read through the same functions, so the page and the CLI
cannot disagree about what a robot or a model is.

**`.env` is read, never loaded.** The page shows which keys are set, and a key a project's `.env`
holds counts. But the page's own environment is what every run it starts inherits, and a value it
loaded from a file would reach a run looking as if the shell had exported it, past the checks that
keep some settings out of a project's `.env` (`dotenv_names`). So the files are read as text with
`dotenv_values`, and each run loads its `.env` itself, as at a terminal.
"""

from __future__ import annotations

import contextlib
import importlib.metadata
import importlib.util
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── robots ──


def adapters() -> list[dict[str, Any]]:
    """`quackd list-adapters` as rows: every official adapter, installed or not, then any other
    installed, each with its backends, its status and the extra that installs it."""
    from quackd.adapters.factory import list_adapters

    return list_adapters()


def is_simulated(backend: str | None) -> bool:
    """Whether a backend is a simulator or a mock: nothing to connect, nothing to plug in."""
    from quackd.perception import is_simulated as simulated

    return simulated(backend)


def connect(adapter: str, backend: str) -> Any | None:
    """What connecting `adapter`'s real `backend` needs, as its catalogue row or its own module
    says (`AdapterInfo.connect`), or None where it says nothing."""
    from quackd.adapters.factory import info

    try:
        known = info(adapter)
    except Exception:  # an adapter that is not installed and is not one of the nine
        return None
    return dict(getattr(known, "connect", None) or {}).get(backend)


def robot_identity(
    robot: str | None, address: str | None = None, registry_dir: str | None = None
) -> str | None:
    """The body a run would drive, as one key: a registered name and its spec at the same address
    are one robot, so the page holds them as one job. The spec alone where there is no address,
    as for a simulator or a mock."""
    if not robot:
        return None
    spec, where = robot.strip(), (address or "").strip()
    with contextlib.suppress(Exception):  # a registry that cannot be read names nothing
        from quackd.registry import Registry

        entry = Registry(registry_dir).robots().get(spec)
        if entry is not None:
            spec, where = entry.spec, where or (entry.address or "")
    return f"{spec.lower()}@{where.lower()}" if where else spec.lower()


def registered(registry_dir: str | None = None) -> list[dict[str, Any]]:
    """The robots `quackd robot add` registered, tokens hidden, as `quackd robot list` shows."""
    from quackd.registry import Registry

    return [entry.public() for entry in Registry(registry_dir).robots().values()]


# ── pilots ──


def vendors() -> tuple[str, ...]:
    """Every pilot `--llm` names: the scripted one, the cloud vendors, the local presets."""
    from quackd.agent.providers.catalogue import PROVIDER_NAMES

    return tuple(PROVIDER_NAMES)


def models(vendor: str) -> list[Any]:
    """The model rows a vendor offers, its default first. Empty for a local preset, whose server
    serves whatever model it was started with, and for the scripted pilot."""
    from quackd.agent.providers.catalogue import models_for

    return list(models_for(vendor))


def default_model(vendor: str) -> str | None:
    from quackd.agent.providers.catalogue import default_model_for

    return default_model_for(vendor)


def local_presets() -> dict[str, str | None]:
    """The local model servers quackd knows, and the address each listens on by default."""
    from quackd.agent.providers.local import PRESETS

    return dict(PRESETS)


def decision_llms() -> list[Any]:
    """Every decision LLM `--decision-llm` names, built-in or installed as a plugin."""
    from quackd.agent.decision.factory import find_preset, preset_names

    return [find_preset(name) for name in preset_names()]


def policy_families() -> tuple[Any, ...]:
    """The policy types quackd's policy server serves (`quackd policy serve`)."""
    from quackd.adapters.policies import FAMILIES

    return tuple(FAMILIES)


def policies_not_served() -> tuple[Any, ...]:
    from quackd.adapters.policies import NOT_SERVED

    return tuple(NOT_SERVED)


# ── keys ──


def dotenv(folder: Path | str) -> dict[str, str]:
    """What a run started in `folder` reads from `.env`, without loading it: the file beside the
    run, then the first one up from quackd's own installed directory, the first to name a
    variable winning, as the CLI's root callback reads them."""
    from dotenv import dotenv_values

    import quackd

    found: dict[str, str] = {}
    candidates = [Path(folder) / ".env"]
    here = Path(quackd.__file__).resolve().parent
    for parent in (here, *here.parents):
        if (parent / ".env").is_file():
            candidates.append(parent / ".env")
            break
    for path in candidates:
        if path.is_file():
            for name, value in dotenv_values(path).items():
                if value is not None:
                    found.setdefault(name, value)
    return found


@dataclass(frozen=True)
class Key:
    """Where a key a run needs would come from, and nothing of the key itself but its mask."""

    env: str
    """The variable quackd reads it from."""
    source: str | None
    """`environment`, `.env`, `session` (typed into the page), or None where it is not set."""
    shown: str
    """The key masked as `quackd doctor` masks it, or the empty string."""
    fallback: str | None = None
    """The second variable it was found under, where the first was empty."""


def _masked(value: str) -> str:
    from quackd.doctor import _mask

    return _mask(value)


def key_for(
    env: str,
    *,
    environment: Mapping[str, str],
    dotenv_values: Mapping[str, str],
    session: Mapping[str, str],
    fallback: str | None = None,
) -> Key:
    """Where `env` would come from for a run the page starts, in the order the run itself finds
    it: the key typed into the page for this session, which the run is started with in its
    environment, over the shell's; then the shell's environment; then `.env`, which a run loads
    without replacing either. Name by name, as a vendor reads its first name before its second."""
    for name in (env, fallback):
        if not name:
            continue
        for source, values in (
            ("session", session),
            ("environment", environment),
            (".env", dotenv_values),
        ):
            value = (values.get(name) or "").strip()
            if value:
                return Key(env, source, _masked(value), None if name == env else name)
    return Key(env, None, "")


def vendor_key(vendor: str) -> tuple[str, str | None] | None:
    """The variable a vendor's key is read from, and its second name, or None where the vendor
    needs no key: the scripted pilot. A local preset's key is optional."""
    from quackd.agent.providers.catalogue import KEY_FALLBACKS
    from quackd.agent.providers.factory import KEY_ENV

    env = KEY_ENV.get(vendor)
    return None if env is None else (env, KEY_FALLBACKS.get(vendor))


def vendor_extra(vendor: str) -> tuple[str, bool] | None:
    """The `quackd[...]` extra a vendor's SDK comes with, and whether that SDK is installed."""
    from quackd.agent.providers.factory import EXTRA_FOR, SDK_FOR

    extra, sdk = EXTRA_FOR.get(vendor), SDK_FOR.get(vendor)
    if extra is None or sdk is None:
        return None
    return extra, installed(sdk)


def installed(module: str) -> bool:
    """Whether `module` could be imported here, found without importing it, which for a module
    that pulls torch would take seconds and gigabytes of memory into the page."""
    from quackd.doctor import _METADATA_ONLY

    distribution = _METADATA_ONLY.get(module)
    if distribution is not None:
        try:
            importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return False
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def extras() -> list[tuple[str, str, bool]]:
    """`quackd doctor`'s extras table: what each is for, its install, and whether it is here."""
    from quackd.doctor import EXTRAS

    return [(label, install, installed(module)) for label, (module, install) in EXTRAS.items()]


def session_environment(
    keys: Mapping[str, str], names: Sequence[str] | None = None
) -> dict[str, str]:
    """The keys typed into the page, as the environment a run is started with: only the ones
    with a value, and only `names` where given. Never a file, never a command line."""
    chosen = keys if names is None else {k: keys[k] for k in names if k in keys}
    return {name: value.strip() for name, value in chosen.items() if value and value.strip()}
