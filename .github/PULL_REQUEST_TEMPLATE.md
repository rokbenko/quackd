## What

<!-- One or two sentences. Link the issue if there is one. -->

## Kind of change

- [ ] New or changed **verb** (core in `quackd/verbs/core.py`, or an extension in `adapters/<name>/src/quackd_<name>/verbs.py`) — it has a `VerbSpec` in the owning manifest (without one it does not exist), I updated `docs/concepts/architecture.md`, and `quackd list-verbs --robot <adapter>:<backend>` shows it
- [ ] New or changed **`.duck` file** (`ducks/`) — `quackd validate` passes and I ran it with `--llm fake`
- [ ] Adapter / upstream API — every upstream name is in that adapter's `upstream_api.py` (each adapter's lives in its own package, `adapters/<name>/src/quackd_<name>/upstream_api.py`) marked `VERIFIED` (with a pinned link) or `UNVERIFIED`, and the adapter's page, `docs/adapters/<name>/README.md`, is updated
- [ ] Docs only
- [ ] Other

## Checklist

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` pass locally
- [ ] No network calls in tests; no API keys needed
- [ ] No upstream assets added, from any project: no logos, meshes (`.stl`), MJCF or URDF, ONNX policies, keyframes or videos, and nothing copied out of `~/.quackd/cache`
- [ ] CHANGELOG.md updated under *Unreleased*
- [ ] `uv run quackd validate ducks/*.duck` passes (CI runs it)

<!--
What happens next: your commits are merged as they are, under your name, and never
squashed or retyped. Anything that needs fixing on top lands in separate commits so you
can see exactly what changed after you and why, and the review comment lists everything
that was found rather than just a verdict. Two things worth doing before you open this:
rebase on `main` (a checklist ticked against an old base is not evidence about the
merge), and check that any claim you make in prose is actually true of the code, because
that is where this project's honesty tends to leak. Details in CONTRIBUTING.md, under
"How your PR gets handled".
-->
