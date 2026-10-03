# RELEASING.md — what a version says, and how quackd ships one

quackd is eight distributions with one version between them: `quackd`, and `quackd-microduck`,
`quackd-lerobot`, `quackd-rosbridge`, `quackd-open-duck`, `quackd-xlerobot`, `quackd-alohamini`
and `quackd-toddlerbot`. Sixteen releases, 0.1.0 to 0.16.0, were all minors whatever each one
held, so the number said only that something had shipped. 0.16.1 is the first patch. This file
is the rule that says which a release is, and then the order a maintainer ships one in.
[ADR-0049](docs/adr/0049-a-version-says-what-changed.md) records why it was written down. A
contributor needs only the part about the changelog, and
[CONTRIBUTING.md](CONTRIBUTING.md#versions-and-releases) says what that asks of a pull request.

## What a version number speaks about

A version is a promise about quackd's public surface: everything a person, a script or another
program relies on without reading quackd's source.

- **The CLI:** its commands and flags, their defaults, its exit codes, the refusals a script
  relies on to stop, and what `--json` prints for a script: each object's keys and the types
  of their values.
- **Task files:** a `.duck`, which carries its own version in its `duck:` field, and the
  `.sim.yaml` sidecar beside one.
- **What quackd keeps between runs:** the robot registry, `robots.json`, and the memory files,
  including whether an older quackd can read what a newer one wrote.
- **The MCP server:** its tools and their parameters.
- **The adapter interface a third party implements:** `make()`, `describe()`, the manifest, the
  verbs and the factory's calls, which is how a robot quackd does not publish joins it
  ([docs/adapters/writing-an-adapter.md](docs/adapters/writing-an-adapter.md)).
- **Run records:** `summary.json`, the event kinds in `transcript.jsonl`, and the fields
  `run_counters` reads back from them, which `quackd log` and people's own scripts read.
- **The wire protocols that carry a version of their own:** the policy server's
  `quackd-policy`, the Jetson host daemon's `quackd-jetson-hostd`, and the bridges that run on
  the Open Duck Mini and the ToddlerBot, `quackd-open-duck-bridge` and
  `quackd-toddlerbot-bridge`.
- **Packaging:** the extras' names, which adapters exist, the Python versions quackd supports,
  and the windows that tie the eight packages together.
- **Safety behaviour:** what the executor refuses, what it asks a person to confirm, and what it
  clips.

Not part of it: the README and the pages under `docs/`, the images, the examples under
`docs/examples/`, the wording of a terminal line written for a person to read, the internal
modules, the tests, `scripts/`, the web demo, `uv.lock` and the repository's CI and Dependabot
configuration. Any release may change those.

## While quackd is 0.x

[Semantic Versioning](https://semver.org/spec/v2.0.0.html) lets anything change at any time
while the major is 0. quackd holds itself to more than that: the minor does the job a major will
do after 1.0.0, and the patch does the rest.

**A patch, 0.Y.Z+1, changes nothing a user has to act on and adds nothing to learn.** What
worked before it works after it, but for the safety exception below. It holds fixes that bring
shipped behaviour back to what the docs or quackd's own output said it would do, safety fixes,
corrections to the docs, the examples and the images, catalogue data that changes no default
(a price, a model row), and dependency and packaging fixes. It adds no command, flag, verb,
backend, adapter, extra, MCP tool, configuration key, `.duck` version or protocol version, and
it changes no default. A record, or an object `--json` prints, may gain a field in a patch only
as part of a fix, and only if a reader that does not know the field still works. A patch may
refuse what the release before it accepted only when what it accepted never did what quackd
said it would, or under the safety exception.

**A minor, 0.Y+1.0, is everything else.** Anything new on the surface, any changed default, and
any removal or rename, which is announced a minor ahead with a `Deprecated` entry and a warning
at run time wherever that is practical. Any file a newer quackd writes that an older one cannot
read. A dropped Python version, a new required dependency, or a raised floor a user has to act
on.

**The changelog decides, by its headings.** Every entry under `## [Unreleased]` in
[CHANGELOG.md](CHANGELOG.md) sits under one of these, and nobody has to weigh the entries to know
what the release is:

| Heading | What goes under it | The release it makes |
|---|---|---|
| `Added` | something new on the surface | a minor |
| `Changed` | a default, or behaviour on the surface, that is now different for something that worked, and a refusal of something that worked | a minor |
| `Deprecated` | something still there that a later minor will remove | a minor |
| `Removed` | something gone from the surface | a minor |
| `Fixed` | shipped behaviour brought back to what the docs or quackd's own output said it would do, a refusal of what never did, catalogue data that changes no default, a dependency or packaging fix nobody has to act on, and the safety fixes below | a patch |
| `Security` | a vulnerability closed | a patch |
| `Documentation` | the README, the pages under `docs/`, the examples, the images, the web demo, a CI job or nightly that stands on its own, and a refresh of `uv.lock` or of the tooling that makes one | no bump of its own |
| `Known limitations` | what the release does not do, written as it ships | no bump of its own |

So `[Unreleased]` with an entry under any of the first four is a minor, and with only `Fixed`,
`Security` and `Documentation` it is a patch. An entry about the docs, the examples or the
images goes under `Documentation` whatever it does to them, a new page, a corrected sentence or
a deleted picture, so it never raises the bump. That is where the bench steps, the two grasp
sidecars and the corrected pages written after 0.16.0 are filed. A test, a CI job or a script is
said in the entry of the change it checks. One that stands on its own, such as a new nightly
job, goes under `Documentation` too, since what it changes is the evidence the pages cite and
not what quackd does. So does a refresh of `uv.lock`, a Dependabot pull request among them, and
a change to the tooling that makes one. The lock is what a checkout and CI install, and a
wheel's requirements are its `pyproject.toml`'s, so a refresh changes no requirement a user
installs against. A floor raised in a `pyproject.toml` does, and is filed by what it asks of a
user.

The line between `Fixed` and `Changed` is whether what changed worked. A fix may change how
something the pages describe works inside, since the pages are not the surface, and it may
refuse what the release before accepted when that never did what quackd said it would. A
latency that `quackd policy serve` took, and that `quackd policy check --bench` said covered
what it timed, but that left a segment nothing to play for part of every chunk, is that kind.
So the fix that refuses it goes under `Fixed`, and its entry says what is refused now and what
to do instead. A refusal of something that worked goes under `Changed`, and makes a minor.

`tests/test_docs.py` fails when a released patch carries one of the first four headings, when a
release whose patch number is zero carries none of them, and when any section carries a
heading this table does not have, a heading at any other level, or one twice.

**The safety exception.** A fix that tightens behaviour, because the looser behaviour could
move a body in a way the docs never promised, ships as a patch even when a script relied on the
looser one. It goes under `Fixed`, and its entry says what changes for the user: what is
refused or asked now that was not, and what to do instead. Holding a fix like that back for a
minor is the one trade these rules will not make.

## 1.0.0

1.0.0 is Rok's decision, recorded in an ADR, and it waits for four things.

1. **Every surface above is written down as public and guarded by a test.** The guards that
   exist today fail when the surface changes and its page does not:
   - in `tests/test_docs.py`, the guards that fail when a command has no row in the README's
     table, or a flag or an environment variable they list is missing from the pages that
     document it;
   - `test_schema_json_in_sync` in `tests/test_duckfile.py`, which holds
     `quackd/duckfile/schema.json` to the parser, and `test_manifest_schema_on_disk_is_current`
     in `tests/test_manifest.py`, which holds the manifest's schema to its model;
   - the MCP tool list in `tests/test_docs.py`, which fails when a tool the server registers is
     missing from [docs/guides/mcp.md](docs/guides/mcp.md) or a page counts them wrong, and beside
     it the event kinds a run's transcript holds and the keys of a run's `policy` block, which fail
     when docs/concepts/architecture.md leaves one out;
   - `tests/test_workspace.py`, which fails when the eight versions or the windows between them
     disagree.

   None of those proves that an older script still works. The exit codes, the refusals scripts
   match on, the keys `--json` prints and the safety behaviour are tested case by case, and
   none of them is written down as one list that a test holds a change to. The registry has
   tests that a robot with no board and one with a single camera are written the way an older
   quackd reads them, and nothing covers the rest of it or the memory files. Nothing guards the
   rest of `summary.json`'s keys, and each wire protocol is tested only against its other half
   from the same commit, never against an older one.
2. **At least one body has taken its bench checklist to the end on real hardware.** None has
   yet. The SO-101 is the nearest, and it still owes the bench steps 0.14.0 and 0.15.0 left.
3. **A deprecation path exists:** announced a minor ahead, warned at run time, and removed in
   the next major.
4. **The registry and the records carry a version that an older file is read by.**
   `robots.json` carries `"version": 1` and has never moved it, while quackd since 0.15.0 can
   write a registry that older releases cannot read: one holding a robot stored with a host is
   unreadable to 0.12 to 0.14, and one holding a `lerobot:mujoco` twin to 0.14 and earlier.
   `summary.json` carries the version of quackd that wrote it, and the memory files carry none.

After 1.0.0 it is Semantic Versioning proper: a major for anything that breaks the surface, a
minor for additions and deprecations that break nothing, and a patch for fixes. The safety
exception still holds.

## How the numbers move

- **Eight distributions, one version, released together,** even when only one of them changed,
  so nobody has to work out which `quackd-lerobot` goes with which `quackd`.
- **Every window starts at the release it ships in.** Each adapter allows the core from its own
  release up to the next minor (`quackd>=X.Y.Z,<X.Y+1`), and each of the core's extras allows
  its adapter's the same way. `scripts/set_version.py X.Y.Z` writes both from the whole version,
  beside the eight `__version__` lines, so a patch raises every floor in the eight
  `pyproject.toml` files to itself, and a minor or a major moves the whole window. The window
  quoted in prose, in CONTRIBUTING.md, docs/adapters/writing-an-adapter.md and ADR-0037, is edited
  by hand at every release.
- **So an adapter from a patch never installs beside a core from before it.** `quackd-lerobot`
  0.16.1 needs `quackd` 0.16.1 or a later 0.16, and `quackd[lerobot]` 0.16.1 installs
  `quackd-lerobot` 0.16.1 or a later 0.16, so an adapter's fix may need what the core gained in
  the same patch. An installer that upgrades the adapter upgrades the core with it, so the
  raised floor asks nothing of anybody. The other way round still installs: `quackd` 0.16.1
  upgraded on its own beside `quackd-lerobot` 0.16.0 is inside that adapter's window, so the
  core's fix may not break an adapter from earlier in its minor. A fix that would is a minor.
- **A patch is cut from `main`** when `main` holds nothing above patch level since the last
  tag, which its `[Unreleased]` headings say. Otherwise it is cut from the last tag, on its own
  `release/X.Y.Z` branch with the fixes brought over, and merged back into `main` once it has
  shipped.
- **A fix found while a release is still local goes into that release.** Nothing is public
  until the tag is pushed, so the release commit is written again on top of the fix. 0.15.0's
  release commit was written on 2026-09-27 and held for the fixes the first real pilot on the
  arm's simulator found, and 0.15.0 shipped with them on 2026-09-29.
- **A published version is never uploaded again.** PyPI refuses a file name it has had once,
  even after the file is deleted. A broken release is yanked on PyPI, which leaves it
  installable by an exact pin and hides it from every other resolve, and the next patch fixes
  it.
- **A patch leaves the story alone.** It gets no sentence in LAUNCH.md's first paragraph, it
  does not change the `Version X.Y` line under the README's Status, and it does not touch the
  GitHub About text or topics. Its tag message and its release body are short: what it fixes,
  in a paragraph or two.

## Cutting a release

The commands are for Git Bash on this project's Windows machine. A running MCP server holds
`.venv`'s `quackd.exe` open there, and a plain `uv run` fails trying to replace it, so every
`uv run` below carries `--no-sync`. Set the two versions once, here for 0.16.1, and a scratch
directory outside the checkout for everything the steps write:

```bash
v=0.16.1 last=0.16.0 out="$(mktemp -d)"
```

1. **Decide the number** from `[Unreleased]`'s headings, as above. This prints the ones that
   make a minor, and for a patch it prints nothing:

   ```bash
   awk '/^## \[Unreleased\]/ {f = 1; next} /^## \[/ {f = 0} f && /^### (Added|Changed|Deprecated|Removed)$/' CHANGELOG.md
   ```

   An entry under one of them that the table files elsewhere, such as a deleted picture under
   `Removed`, moves to its own heading first, since the headings are what the number is read
   from.
2. **Branch.** `git switch -c release/$v main`. A patch while `main` holds work above patch
   level starts from the last tag instead, `git switch -c release/$v v$last`, and brings each
   fix over with its changelog entry by `git cherry-pick -x`.
3. **Set the version.**

   ```bash
   uv run --no-sync python scripts/set_version.py $v
   uv lock
   ```

   It changes the eight `__version__` lines and every window in the eight `pyproject.toml`
   files: a patch raises each floor to itself, and a minor moves the whole window. Then the
   copies of the window in CONTRIBUTING.md, docs/adapters/writing-an-adapter.md and ADR-0037 are
   edited by hand, and so is the version in the `policy` line of the two `quackd policy check`
   outputs quoted in docs/adapters/lerobot/README.md, which a check run on the release has to print.
   A minor also moves the `Version X.Y` line under the README's Status, and LAUNCH.md's first
   paragraph gains a sentence if the story changed.
4. **The changelog.** `## [Unreleased]` becomes `## [X.Y.Z] — YYYY-MM-DD`, dated the day it
   ships, so a release that is held is dated again. At the foot, `[Unreleased]:` compares
   `vX.Y.Z...HEAD`, and a new `[X.Y.Z]:` line compares the last tag with `vX.Y.Z` the way every
   line below it does. A minor opens with a few paragraphs on what the release is, and a patch
   with a sentence or two.
5. **Read the note against the code.** Every release so far has found claims that went stale
   between writing and tagging, and a correction made during the read is the likeliest sentence
   in the file to be wrong, so read those again after. Sweep every `used to`, `no longer` and
   `now` against the last tag and not the commit before: a note written commit by commit
   compares each fix with a state no user ever installed. `git show v$last:<path>` is what users
   had, and `git log -S'<text>' v$last..HEAD` says when it changed. Then check that every
   shipped section is byte for byte what the last tag holds:

   ```bash
   shipped() { awk -v h="## [$last]" 'index($0, h) == 1 {f = 1} /^\[Unreleased\]:/ {f = 0} f'; }
   diff <(git show "v$last:CHANGELOG.md" | shipped) <(shipped < CHANGELOG.md) && echo "as shipped"
   ```

   No test compares the history's text. `tests/test_docs.py` reads its headings and its links,
   and the guards over the living documents skip it on purpose.

   PLAN.md's open items are read against the release the same way. An item that lists what
   each release still owes the hardware, as the SO-101's does, names this release too when it
   changed what the item is about. Reflow what you splice in: `tests/test_docs.py` fails on a
   PLAN.md line that stops short in the middle of a sentence.
6. **Commit** `release: X.Y.Z`, with a body that says what the release is and what the read
   against the code changed.
7. **Run every gate on the release commit.**

   ```bash
   uv lock --check
   uv run --no-sync ruff check .
   uv run --no-sync ruff format --check .
   uv run --no-sync python -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'
   uv run --no-sync mypy
   UV_PROJECT_ENVIRONMENT=.venv312 uv run --no-sync mypy
   QUACKD_STRICT_SEEDS=1 uv run --no-sync pytest
   QUACKD_STRICT_SEEDS=1 GITHUB_ACTIONS=true uv run --no-sync pytest
   uv run --no-sync quackd validate ducks/*.duck
   ```

   mypy runs under 3.11 and 3.12 because CI's 3.12 jobs see stricter numpy stubs. `.venv` is the
   3.11 environment in the main checkout, and nothing makes it so anywhere else, so the line
   before the first mypy fails when it is not. In a worktree whose `.venv` is 3.12, run that
   mypy with `UV_PROJECT_ENVIRONMENT` naming a 3.11 environment. The second pytest is for what
   fails only on GitHub's runners, where Typer colours `--help` because `GITHUB_ACTIONS` is set.
   The packaging check, a core wheel installed alone that must refuse and say what to install,
   is CI's `packaging` job, and it runs in the next step.
8. **Merge, push and wait.**

   ```bash
   git switch main
   git merge --no-ff release/$v -m "Merge release/$v: <the tag's clause>"
   git push origin main
   gh run watch -R rokbenko/quackd
   ```

   Every job of `main`'s run has to pass: the six test jobs, `packaging`, `physics` and
   `policy`. A red one stops the release here, with nothing tagged.

   **A patch cut from the last tag** skips those four commands, because `main` carries work the
   patch leaves out and its merge would carry that work too. It pushes the release branch
   instead, which CI runs every job on, and waits the same way:

   ```bash
   git push -u origin release/$v
   gh run watch -R rokbenko/quackd
   ```

   Step 9 then tags the release commit, and the branch merges into `main` only at step 18.
9. **Tag the merge commit**, or on a patch cut from the last tag, the release commit. Either is
   the commit checked out after step 8.

   ```bash
   git tag -a v$v -F "$out/tag.txt"
   git push origin v$v
   ```

   The message's first line is `vX.Y.Z:` and a lowercase clause, the one the merge carries.
   Then paragraphs on what changed, then the line `Eight distributions, one version between
   them:` naming all eight, and last `The full note is in CHANGELOG.md.` It goes out under the
   maintainer's name, so it keeps the first two rules of
   [How the reply is written](CONTRIBUTING.md#how-the-reply-is-written): no dash used as
   punctuation, with a hyphen only inside a name such as `quackd-lerobot`, and none of the
   assistant tells. That section's rule on length is for a reply to one person, and a minor's
   release body has a heading for each theme. Pushing the tag runs CI again on the same commit.
   That run is for the record, since this order exists so that a red run never sits behind a
   tag that is already public.
10. **Build from the tag, never from the checkout.**

    ```bash
    mkdir "$out/src"
    git archive "v$v" | tar -x -C "$out/src"
    (cd "$out/src" && uv build --all-packages --out-dir "$out/dist")
    ls "$out/dist" | wc -l
    ```

    That is sixteen, eight wheels and eight sdists. The core's wheel takes `quackd/` whole, and
    its sdist takes `quackd/`, `ducks/`, `docs/`, `tests/` and `bridge/` whole, so a file the
    checkout has and no commit does would ship. The archive holds exactly what the tag names.
11. **The GitHub Release**, with all sixteen attached:

    ```bash
    gh release create "v$v" --verify-tag -R rokbenko/quackd \
      --title "v$v <the tag's clause>" --notes-file "$out/body.md" "$out"/dist/*
    ```

    The title is the tag's first line without its colon. The body is in the tag's voice, with a
    heading for each theme in a minor and a paragraph or two in a patch.
12. **Publish to PyPI, the core first.** Every adapter depends on it, and a resolver that meets
    `quackd-lerobot` before `quackd` has nothing to resolve against.

    ```bash
    export UV_PUBLISH_TOKEN="$(grep '^UV_PUBLISH_TOKEN=' .env | cut -d= -f2-)"
    uv publish --check-url https://pypi.org/simple/ "$out"/dist/quackd-"$v"*
    uv publish --check-url https://pypi.org/simple/ "$out"/dist/*
    ```

    The token lives in the repository's ignored `.env`, and nothing echoes it. `--check-url`
    skips a file PyPI already has, so the second command uploads the seven adapters, and either
    can run again after a partial upload or a `429`.
13. **Compare every hash in three places.**

    ```bash
    names="quackd quackd-microduck quackd-lerobot quackd-rosbridge quackd-open-duck quackd-xlerobot quackd-alohamini quackd-toddlerbot"
    (cd "$out/dist" && sha256sum *) | awk '{sub(/^\*/, "", $2); print "sha256:" $1, $2}' | sort > "$out/local.txt"
    for n in $names; do curl -s "https://pypi.org/pypi/$n/$v/json" | python -c "import json, sys; [print('sha256:' + u['digests']['sha256'], u['filename']) for u in json.load(sys.stdin)['urls']]"; done | tr -d '\r' | sort > "$out/pypi.txt"
    gh release view "v$v" -R rokbenko/quackd --json assets --jq '.assets[] | "\(.digest) \(.name)"' | tr -d '\r' | sort > "$out/github.txt"
    (cd "$out" && wc -l < local.txt && diff local.txt pypi.txt && diff local.txt github.txt && echo "one hash each")
    ```

    Sixteen lines, and no difference. PyPI's JSON is the verdict and the upload's output is not:
    it has reported files uploading that never appeared.
14. **Wait for PyPI's simple index**, which is what `uv` resolves from:

    ```bash
    for n in $names; do curl -s -H 'Cache-Control: no-cache' "https://pypi.org/simple/$n/" | grep -cE -- "-$v(-py3-none-any\.whl|\.tar\.gz)#"; done
    ```

    Every line says 2. A minute after the 0.15.0 and 0.16.0 uploads the first `uvx` found no
    solution, while PyPI's JSON already listed every file.
15. **Smoke the published release from an empty cache.**

    ```bash
    export UV_CACHE_DIR="$(mktemp -d)" QUACKD_MEMORY_DIR="$(mktemp -d)"
    uvx --refresh --from "quackd[microduck]==$v" quackd run find-and-kick --robot microduck:sim2d --llm fake
    uvx --refresh --from "quackd[microduck]==$v" quackd run find-and-kick --robot microduck:sim2d --llm fake
    uvx --refresh --from "quackd==$v" quackd run find-and-kick
    ```

    The first run's header says `0 notes, 0 earlier runs` on its `memory` line, and the
    second's `0 notes, 1 earlier runs`: the second read the first one's episode. The scratch
    memory keeps the smoke out of the maintainer's own. The third installs no body, so it must
    refuse with `no robot adapter is installed` and the line that installs one, and run
    nothing. A release that touched the arm's policy path also runs
    `uvx --refresh --from "quackd[lerobot]==$v" quackd policy check --policy scripted:sweep`,
    whose `policy` line names the new version.
16. **Dispatch the nightlies on `main`** when the release touched what they cover:
    `lerobot-sim-assets.yml` for the arm's simulator and the model it fetches,
    `microduck-assets.yml` for the duck's physics model and its trained gait, and
    `toddlerbot-contract.yml` for the ToddlerBot's daemon against upstream.

    ```bash
    gh workflow run lerobot-sim-assets.yml --ref main -R rokbenko/quackd
    ```

17. **The About text and the topics** change only for a minor or a major whose positioning
    changed: `gh repo edit -R rokbenko/quackd --description "..."`, within GitHub's 350
    characters, and at most 20 topics.
18. **A patch cut from the last tag** merges `release/X.Y.Z` into `main` now, with `--no-ff`,
    and `main`'s `[Unreleased]` loses the entries that shipped in it.
