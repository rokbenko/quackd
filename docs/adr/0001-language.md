# ADR-0001: Python, managed by uv

**Status:** accepted · **Date:** 2026-08-28

## Context

Upstream Microduck is Rust. Roboglot is the *brain*, not the reflex layer: it talks to LLM
APIs, runs perception, renders a toy simulator, and speaks MCP. Its users are people who
already have `uv` and an API key, and its contributors are the people who write `.glot`
files and verbs.

## Decision

- Python ≥ 3.11 (`X | Y` unions, `tomllib`, `asyncio.TaskGroup`).
- `uv` for everything: lockfile, `uv run`, `uvx roboglot` as the install path.
- `typer` + `rich` for the CLI, `pydantic` v2 for every contract (`.glot` schema, verb
  params, transport models), `numpy` + `Pillow` + `opencv-python-headless` for sim and
  perception, the official `mcp` SDK for the MCP server.
- Provider SDKs (`anthropic`, `openai`, `google-genai`) and `ultralytics` are optional
  extras so the default install stays small and `uvx roboglot` stays fast.
- `hatchling` build backend, version read from `roboglot/__init__.py`.
- `ruff` (lint + format), `mypy` (loose, public surface typed), `pytest` +
  `pytest-asyncio`, `pre-commit`.

## Consequences

- The 50 Hz control loop stays where it belongs (onboard, Rust). Roboglot runs at 5–20 Hz at
  most and never needs to be real-time.
- Every LLM vendor SDK is Python-first; MCP's reference SDK is Python; the RL stack
  (`microduck_rl`) is Python — a future learned-verbs track shares a language with training.
- Windows is a first-class dev platform (this project is developed on one); CI runs on
  ubuntu + macos. Anything POSIX-only (unix sockets) is isolated and skipped on win32.
