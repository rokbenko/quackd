# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with two
headings of its own, Documentation and Known limitations, and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). What a patch, a minor and a major
mean while quackd is 0.x, and which headings make which, is in [RELEASING.md](RELEASING.md).

## [Unreleased]

### Added

- **`quackd list-models --kind` lists the decision LLMs and the learned policies too.**
  `--kind decision` prints every decision LLM `--decision-llm` names, in the order `quackd
  doctor` does, which until now was the one command that said they existed. `--kind policy`
  prints the policy types `quackd policy serve` serves, what each needs and how far it has got,
  and the ones people ask about that it does not serve, with why, read from data in the core so
  that a machine with no LeRobot can be told. `--kind all` prints the three, and `--kind pilot`,
  the default, prints what the command always has, byte for byte. Every `--json` row now says
  which kind it is, and `--llm`, which narrows the pilot rows to one vendor, is refused beside
  another kind rather than ignored.
- **`quackd policy serve` serves a pi0, and refuses a pi0-FAST in a sentence of its own.** A pi0
  is Physical Intelligence's flow-matching VLA from before pi05, and to the server it is a pi05
  with one step changed: its processors end the task with a newline for PaliGemma's tokenizer
  where pi05's write the state into the prompt, and that step, its tokenizer and the relative and
  absolute steps pi05 has too are all it allows beyond the default ones. It needs the same
  `--pin` for `google/paligemma-3b-pt-224` and the same Hub token granted it, it runs with a
  camera it is not given padded, and one that learned relative actions is served a whole chunk at
  a time, made absolute against the state it was predicted from. Its own loader hands back a
  random network when its weights do not load, as pi05's does, so the server loads them itself,
  strictly. A pi0-FAST is refused on its `config.json`, before anything else is fetched, because
  its policy loads the FAST tokenizer with `trust_remote_code=True`, hard-wired, and so would run
  the Python that tokenizer's repository brings with it. Tests hold a stand-in shaped like the
  SO-101 fine-tunes on the Hub to every check, a pi0-FAST to being refused with nothing but its
  config fetched, and a lenient pi0 loader to raising on a weight it lacks
  ([docs/guides/policies.md](docs/guides/policies.md#pi0-and-why-not-pi0-fast)).
- **`quackd policy serve` serves an X-VLA in its `auto` action mode.** X-VLA's answer means
  what `action_mode` in its config says, and `auto` is the dataset's own action, which for an
  SO-101's data is the arm's own joints. Every other mode, an end effector's pose (`ee6d`, the
  default, and `agibot_ee6d`) or another robot's joints (`joint`, `franka_joint7`,
  `so101_bimanual`), is refused before a pin or a weight, in a sentence that names the mode, and
  so is LIBERO's post-processor step, which turns an end-effector rotation into axis-angle and
  the gripper into -1 or 1. Its domain step's domain has to be one of the model's own. Its
  Florence-2 is built from its config, so BART's tokenizer is the one model it names, pinned and
  fetched without weights, it runs with a camera it is not given padded, and its own loader is
  strict. The extra brings LeRobot's `[xvla]` now, which adds nothing to the lock. Tests
  hold a stand-in shaped like an SO-101 fine-tune on the Hub to every check and each refusal to
  fetching no weight, and CI's `policy` job installs LeRobot's `[xvla]` extra and serves a tiny
  random X-VLA beside the tiny ACT, offline, through the real server and client, its tokenizer a
  few words pinned at a commit in a Hub cache of its own
  ([docs/guides/policies.md](docs/guides/policies.md#x-vla)).
- **`quackd policy serve` serves a GR00T N1.7 whose every action group is joints.** A GR00T names
  two models inside itself, the base it was fine-tuned from, `nvidia/GR00T-N1.7-3B`, and its
  backbone's tokenizer, `nvidia/Cosmos-Reason2-2B`, which is gated, and both are pinned with
  `--pin`, the base fetched with its weights and the tokenizer without. A config that names no
  base is refused, since LeRobot would fill in its own at no revision, and the pinned base's
  `config.json` is read before any weight, since LeRobot builds the backbone from a config of its
  own only while the base names Cosmos-Reason2-2B and fetches any other trusting its
  repository's code. It decodes its answer group by group, and a group decoded as an end
  effector's pose, LIBERO's gripper of -1 or 1, a decode with no statistics, whose actions would
  reach the arm still normalised to -1..1, and any N1.5 checkpoint are each refused in a sentence
  of their own, before a weight. A fine-tune that learned relative joints decodes by statistics
  its processors keep grouped, a whole chunk at a time against the state it was predicted from,
  and keeps the percentiles of the state it learned from there too, where LeRobot's normaliser
  keeps none, so the server reads them from there and the arm's frame check is made on them. One
  that learned absolute joints decodes by its dataset's statistics and is read as every other
  type is. It pads no missing camera, so the arm needs one for every image, and a checkpoint
  whose processors look for a camera its config has no image for is refused. The extra brings
  LeRobot's `[groot]` now, which adds GR00T's dependencies to the lock, decord on x86 alone and
  peft among them. Tests hold stand-ins of both kinds of SO-101 fine-tune on the Hub to every
  check, the reported percentiles to the grouped q01 and q99 and not their min and max, and each
  refusal to fetching no weight ([docs/guides/policies.md](docs/guides/policies.md#gr00t-n17)).
- **`--effort low|medium|high` says how hard the pilot's model reasons, on `run` and
  `preflight`.** It reaches Anthropic's `output_config.effort`, OpenAI's reasoning effort on
  either API and a model server of your own, which speaks OpenAI's API, and it beats
  `QUACKD_EFFORT` and `QUACKD_OPENAI_REASONING_EFFORT`, which still stand where it is not given.
  Three levels, the ones both vendors have. It is refused before anything connects where it
  cannot reach: the scripted pilot, `--controller vla`, a Claude model Anthropic's effort page
  does not list, OpenAI's GPT-4.1, GPT-4o, their minis and ChatGPT Instant, which take no
  reasoning effort, its three pro models at `low`, which they do not take, and a vendor that
  documents no such field, OpenRouter told that its own `reasoning` field goes through
  `--extra-body`. `run_start.effort` records the level every request was sent at and whose word
  it was, and the run header gains an `effort` row only when the flag asked
  ([README.md](README.md#configuration)).
- **The summary says how the model's calls spread and when the body first moved.**
  `summary.json` gains `llm_latency_p50_s`, `llm_latency_p90_s` and `llm_latency_max_s`, by
  nearest rank over the calls that answered and only where one did, and the stepper's block the
  same three. `first_motion_s` is when the first verb that may move the body went to the
  transport, on the record's own clock with the connect in it, and `first_motion_llm_calls` how
  many model calls came first. Both are null where nothing moved, and a refused verb, a dry run
  and a read never count. The counters under the verdict print them, as `model calls p50 0.0 s,
  p90 0.0 s, max 0.0 s · first motion at 0.1 s, after 3 model calls` on a scripted run, and only
  where the keys are, so a record from before replays with the counters it always had
  ([docs/concepts/architecture.md](docs/concepts/architecture.md#time-and-money)).
- **`gpt-6.1-sol` is an OpenAI row.** $2 in, $10 out, $0.10 cached and $2.50 for a cache write,
  read off OpenAI's pages on 2026-10-26 and dated so on its row, and on the Responses API as the
  other GPT-6 rows are. `--llm openai` still means `gpt-6-sol`, which OpenAI still serves with no
  shutdown date.
- **`quackd drive` drives one robot, one command at a time.** A line that names a call runs it
  with no model asked: a verb of the body's own (`kick left`, `say good morning`, or
  `move(vx=0.2, duration_s=2)`, whose values are read as literals and never evaluated), or a
  preset walk, `forward`, `back`, `turn left` or `right` and `step left` or `right`, each derived
  from the body's own clamps with no number of its own. On the mock Microduck such a line is read
  in under a millisecond. An empty Enter, `stop`, `halt`, `brake` or `q` brakes at once and waits
  on nothing, and a newer command that moves the body ends whatever is moving it and waits for
  its stop before it sends anything. With `--decision-llm`, a line no verb names is put to that
  decision LLM as one closed question, held to the stepper's floors, the `.duck`'s confirm gates
  and its second, which asks `did you mean kick(leg=left)? [y/N]` below a floor. Anything else
  is a goal, which the pilot (`--llm`, or a registered robot's own) flies as a whole run of its
  own on the body the session holds, inside the session's contract, its `--dry-run` and what is
  left of its budgets. A person's own command is the verdict for its own call, lines piped in are
  recorded as a pipe's, never a person's, and a call the fast lane took unasked as the decision
  LLM's reading. A body that cannot see a fall is driven only once somebody says they are
  watching it. Without a `--duckfile`, a session allows every verb that is not dangerous, keeps
  the battery rule a goal is held to, and runs on the schema's own ceilings for its budgets. The
  keyboard goes through prompt_toolkit, and a pipe is read a line at a time, each waited for. The
  session's record is a run directory `quackd log` replays, with each goal's own run under
  `goals/`, and its summary gives the waits from a line to its call starting and to the body's
  first motion for it, by lane, and from a brake to its stop
  ([docs/guides/drive.md](docs/guides/drive.md),
  [ADR-0065](docs/adr/0065-a-person-drives-one-robot-a-command-at-a-time.md),
  [ADR-0066](docs/adr/0066-a-persons-command-is-the-verdict-for-its-own-call.md)).
- **`quackd policy endpoint up|list|pause|delete` starts the arm's policy server on a GPU rented
  from Hugging Face.** `up NAME --policy REPO@REVISION` creates a Hugging Face Inference Endpoint
  that runs the same `quackd policy serve`, behind Hugging Face's TLS, and waits until it serves,
  saying each state as it passes. It rents AWS's `nvidia-l4` in `us-east-1` unless `--instance`,
  `--vendor` and `--region` say otherwise (`--region eu-west-1 --instance nvidia-a10g` in
  Europe), takes the serve flags a checkpoint needs and passes them on unchanged, and scales the
  endpoint to zero after `--idle-minutes`, 15 to 720 and never none. Before anything bills it
  checks that this quackd is a release on PyPI, the instance, its price on the day, the quota,
  that the checkpoint and its pins are neither gated nor private without `--hub-token-from`, and
  the checkpoint's `config.json`, then asks with the price, as it does before it resumes or wakes
  one, and refuses with no terminal and no `--yes`. An endpoint that fails, does not serve in
  time, has no GPU or meets any other error while `up` waits is paused, its log quoted with every
  token taken out. The container installs this release from PyPI with `uvx` in uv's own image,
  pinned by digest, with nothing uploaded after an hour past the release, and the endpoint is
  `authenticated`, on one replica, with a token of quackd's own as its one secret. The account's
  token goes to huggingface.co and the API, and as the bearer to the one endpoint address `up`
  recorded, and nowhere else. `list` merges this machine's records with what the API says, `pause`
  stops the bill and `delete` removes one. `up`, `pause` and `delete` read the endpoint first, and
  leave alone one by that name that does not carry the tag `up` gave it from this machine's
  record, as one made on huggingface.co or by another machine's `up` does not, and
  `delete --forget` forgets such a record and asks Hugging Face nothing
  ([docs/guides/policies.md](docs/guides/policies.md#on-a-hugging-face-endpoint),
  [ADR-0061](docs/adr/0061-a-hugging-face-endpoint-is-a-rented-gpu-quackd-starts.md)).
- **`--policy-url hf:NAME`** on `quackd run`, `quackd preflight`, `quackd serve-mcp` and
  `quackd policy check` reaches an endpoint this machine started, as the record `up` kept says:
  its address, its own token unless `--policy-token` is typed, and the account's token as the
  bearer. It reads the endpoint from Hugging Face's API first, one request with the account's
  token, and goes on only when it carries the tags `up` gave it at the recorded address, so
  neither a record left behind by an endpoint deleted on huggingface.co nor one a project's `.env`
  planted by naming another registry can send the account's token to an address the API does not
  confirm. `QUACKD_POLICY_TOKEN` and `~/.quackd/policy.token` are never read for one. The run's
  `policy` block keeps the address as `server` and adds `endpoint`.
- **`GET /v1/health` on the policy server**, answered with no token, says whether it serves,
  `{"ready": true}`, or is stopping, a 503, and nothing else, for a platform's readiness probe. It
  reads no body and touches no session. The protocol stays version 1. The server also logs once,
  as a warning, the first request that arrives carrying an `Authorization` header, and never
  the header's value.
- **`quackd policy serve --token-env NAME`** reads the token from the variable `NAME`, instead of
  a file, and the serving rows name the variable and never what it holds. It is refused beside
  `--token-file`, and with a variable that is unset, empty, short or holds a space.

### Changed

- **A processor step is allowed by the type that names it.** `quackd policy serve` and
  `quackd policy check` allowed a checkpoint any step that ACT's, SmolVLA's or pi05's processors
  used, whatever its own type, so an ACT could name pi05's tokenizer, which nobody had read for
  an ACT. Each type now allows the steps every policy is built from and the ones its own
  processors add, read at LeRobot 0.6.1, and a step another type uses is refused before any
  weight is fetched, in a sentence that lists what that type does allow. The action tokenizer,
  which no type may name, is refused in a sentence of its own, saying that it trusts its
  repository's code unless told not to and turns actions into tokens for training, which a
  server never does. No checkpoint LeRobot's own processor factories write names another type's
  step. `quackd.adapters.policies` says which policy types the server serves and which it does
  not, and why, as data the core prints with no LeRobot installed, and a test ties its rows to
  the server's types ([ADR-0060](docs/adr/0060-hugging-face-serves-many-vlas.md)).
- **A pinned model that holds a PEFT adapter is refused.** GR00T's extra brings peft, and with
  peft installed transformers loads a PEFT adapter it finds in a model's directory onto that
  model, or, when the directory has no `config.json`, loads the base model the adapter names,
  which no pin fixes. A pinned model whose directory holds an `adapter_config.json` was loaded
  without its adapter before, and is refused now, in a sentence saying to pin a model with the
  adapter merged into its weights. None of the five models the served families' checkpoints
  name, read on the Hub on 2026-10-25, holds one. A test holds a pinned directory with one to
  being refused.
- **Claude's requests are marked for Anthropic's prompt cache.** Every request sent its whole
  prompt again at the full input rate, and nothing asked Anthropic to cache any of it. The system
  prompt goes as one marked text block now, which caches the tools with it, and the loop marks
  the last block of the history the next call will still send unchanged, which it can foresee
  because it owns the trim that changes the history. Where the two differ, the previous call's
  marker goes out again beside it, since a read looks back twenty positions at most. That is
  three markers at most, under Anthropic's four, never on a thinking block, and a retry resends
  the same marked request. From the second call on, the part of a request the last call already
  sent is meant to be read back at the model's cache rate, a tenth of its input rate on most
  Claude models and a twentieth on Opus 5.5, after one write at a quarter more. A model that
  answers in prose costs a write or two that nothing reads, and never a wrong request.
  `QUACKD_ANTHROPIC_CACHE=0` sends every request unmarked. A request-level oracle runs the real
  loop and the real Claude provider over four bodies and twelve trim settings: all 48 cases fail
  with no marker in the history, and 32 of them with one at the end of each request
  ([ADR-0063](docs/adr/0063-the-pilots-prompt-is-cached-up-to-where-the-next-request-still-matches.md)).
- **The pilot's client is warmed while the robot connects.** A client of Anthropic's or OpenAI's
  SDK starts its first request by asking which platform it runs on, on Windows a WMI query or a
  subprocess, and by importing anyio's asyncio backend, and the pilot paid both in its first
  call, in the seconds a person waits for the robot to move. Every provider built on those SDKs
  asks both in a daemon thread started before the connect's clock, so `connect_s` stays the
  connect. Nothing waits for it, it sends nothing and it never raises, and `run_start.pilot_warm`
  says what became of it. The decision LLM's client has done the same since 0.19.0.
- **In shadow mode the stepper is asked beside the model's call, not before it.** Its answer
  changes nothing about a turn there, and a run being measured waited up to the stepper's second
  on every turn for the measuring. Its request goes out with the model's now and is settled once
  the model's answer is on the record: raced against the stop as before, or cut off on the gate
  `interrupted` when the model's call ends the run. The turn that finds the LLM calls spent asks
  neither, where it used to ask the stepper and then refuse the model. A shadow turn's
  `decision` line follows its `llm` line now. `on` is unchanged: the stepper still answers first,
  since its answer can take the turn ([ADR-0064](docs/adr/0064-a-run-says-where-its-seconds-went-and-waits-for-less.md)).
- **The status line names the call the model chose.** After the model answered, the line under
  the log said "choosing a verb", which was over by then. It names the call the model chose now,
  says that it answered without one or that the call failed, and a wait on the model carries the
  last answer's seconds.
- **A turn's picture is encoded once.** Every frame was encoded to PNG twice, once for `frames/`
  and once more for the request. The save hands back the bytes it wrote and the model is sent
  those, so the file a reader opens is the picture the pilot was shown.
- **prompt_toolkit is a core dependency.** `quackd drive`'s keyboard reads a line while a verb
  runs, which `input()` cannot, through prompt_toolkit 3.0.42 or later. Only that front end
  imports it, so no other command pays for it, and the lock gains prompt-toolkit and wcwidth.

### Fixed

- **The policy server's extra brings LeRobot's `[pi]`.** `quackd[lerobot-vla]` was LeRobot's
  `[smolvla]` alone, so serving a pi05 took `lerobot[pi]` installed by hand besides it, which the
  guide said. It brings `[pi]` as well now, which adds SciPy, and a pi05 or a pi0 needs nothing
  more than the extra and a Hub token granted its gated tokenizer.
- **A pinned model is fetched from the Hub's cache alone with `HF_HUB_OFFLINE` set.** The
  server says it answers from the cache alone offline, and a model a checkpoint names inside
  itself was fetched with `snapshot_download`, which at a commit asks the Hub for the commit's
  file list whenever it has not cached one, offline or not, so a SmolVLA's backbone or a pi05's
  tokenizer in a cache filled some other way than by fetching it online was refused as
  unreachable. huggingface_hub 1.29.0, the locked version, and 1.33.0 both do it. It is asked
  for the cache's own files now, and the tiny X-VLA CI serves offline fails without it.
- **A pi05 no longer fetches PaliGemma's weights to load its tokenizer.** A model a checkpoint
  names inside itself was fetched with its configs, its tokenizer files and its safetensors,
  whichever key named it, so serving a pi05, whose tokenizer step names
  `google/paligemma-3b-pt-224`, fetched 11.69 GB of weights that nothing loads, for 21.9 MB of
  tokenizer files, both sizes read off the Hub on 2026-10-25. A model a processor step names is
  now fetched as its configs and tokenizer files alone, and one `config.json` names, a backbone
  the policy is built from as SmolVLA's is, with its safetensors as before. Tests hold a step's
  pin to no `*.safetensors` and a backbone's to its weights. No pi05 has been fetched through it
  against the Hub, which takes the gated tokenizer's grant.
- **A pilot call that hangs ends the run when the task's minutes are spent.** `max_minutes` was
  read only once an answer came back, so a call that hung held the robot past the task's whole
  budget for as long as the vendor's own clock allowed, ten minutes on both SDKs, and only a
  Ctrl-C ended it sooner. The call is raced against the task's minutes as it is against the stop,
  and once they are spent it is cancelled, its `llm` record says so, and the run ends on its
  budget. On a simulator the budget's clock is the simulator's and stands still while a model
  thinks, so a simulated run is never cut off this way
  ([docs/concepts/safety.md](docs/concepts/safety.md#when-the-models-api-fails)).
- **A `stop` over MCP ends the verb beside it.** A `stop` called while a `move`, a `go_to` or a
  `search_scan` ran sent its stop, and the verb's next twist, a tenth of a second later, walked
  the body on. `move`, `go_to`, `search_scan` and `approach_and` read a stop asked for beside them
  before each twist they send now, as `watch` did, and end with the body stopped once more and a
  failed result saying why, and for a walk or a scan how far it got, which `abort_when`'s count
  of failures in a row passes over. `approach_and` no longer runs its `then` verb after one, and
  `search_scan` no longer centres the head after one. A stop asked for while a verb is still
  reading the body's state ends it too, where the walk used to run its whole length after the
  stop had come back ok
  ([ADR-0067](docs/adr/0067-a-stop-ends-the-verb-beside-it-and-a-newer-command-ends-the-one-it-finds-moving.md)).
- **A verb admitted before an abort never starts after it.** A kill switch or a failed heartbeat
  that came while a verb was past its gates and reading the body's state found it in nothing
  it could cancel, and the verb sent its first twist after the abort. The abort is read again
  after the state, as a granted pause already was.
- **A braked MOSS verb is no failure.** MOSS's `drive_to`, `explore` and arm cycles end on a
  `stop` asked for beside them, and came back failed with no mark of the brake, so over MCP
  three stops beside a `drive_to` ended the session on the rule that the same verb failing three
  times in a row aborts. Each is marked `ended_by: stop` now, as the core verbs are, and only
  for a stop: a budget that ran out is still a failure.
- **A battery below a `.duck`'s rule no longer refuses `stop`.** The rule refused the stop as it
  refused every other verb, so the brake reached for once the battery read low was a stop never
  sent. It still ends every other call after it.
- **`quackd doctor` said a vendor's key was unset when only its second name was exported.**
  Cohere's provider reads `CO_API_KEY` when `COHERE_API_KEY` is empty, Meta's reads
  `MODEL_API_KEY` after `META_API_KEY` and Gemini's `GOOGLE_API_KEY` after `GEMINI_API_KEY`, so
  a run with only the second one exported worked while doctor's row said `COHERE_API_KEY unset`.
  The three second names are now one table, `KEY_FALLBACKS`, which the providers and doctor
  both read, and doctor's row shows the masked key with the name it came from
  (`from CO_API_KEY`), which `doctor --json` carries as `key_from`.

### Documentation

- **The README says where a run's seconds go and what shortens them.** Its Performance section
  quotes the new counters from a real scripted run and names the four changes above that shorten
  a turn, and its configuration table gains `--effort` and `QUACKD_ANTHROPIC_CACHE`, as
  `.env.example` does. architecture.md describes the new record fields, safety.md the minutes
  bound, and the decision guide what shadow costs a turn now. The FAQ's two refusals of an
  unknown model, and the counters lines the FAQ and safety.md quote, were captured again.
  [ADR-0063](docs/adr/0063-the-pilots-prompt-is-cached-up-to-where-the-next-request-still-matches.md)
  records the cache and [ADR-0064](docs/adr/0064-a-run-says-where-its-seconds-went-and-waits-for-less.md)
  the rest, and they amend ADR-0010, ADR-0040, ADR-0041 and ADR-0059.
- **[docs/models/](docs/models/README.md), the models beside the robots.** Five pages: the
  three roles a model can have in a run side by side, every pilot vendor and every one of the
  123 model ids with its extra, its key, its address and its rate, the eight decision LLMs, the
  policy types the arm's server serves and the ones it does not, with why, and the companies
  behind them, each with the roles it fills. Every table on them is rendered from the
  catalogues by `scripts/models_docs.py`, and `tests/test_docs.py` fails when a page is not
  what the script writes, so a row is changed in the code and never in a table. The guides
  stay where they were and are linked, so no published link moved, and `docs/README.md` maps
  the folder ([ADR-0062](docs/adr/0062-model-tables-are-rendered-from-the-catalogues.md)).
- **The README says which models work, right after which robots work.** One table of the three
  roles, then the pilots, the decision LLMs and the learned policies each in a few lines, and
  the Usage section's vendor table moved into it whole rather than copied. Why quackd's answer
  to many VLAs is Hugging Face is said there in one paragraph, with the commands that serve a
  policy on the laptop, on a rented GPU and on a Hugging Face Endpoint.
- **The counts that are still typed by hand are held by tests.** The catalogue's 123 ids, the
  119 priced and the four unpriced, which the README, the FAQ and the architecture page each
  typed, now fail a test the day a row is added, and so does a page that miscounts the policy
  types. The local LLMs guide's table of presets is held to the addresses a run dials.
- **`GOOGLE_API_KEY` is named where keys are configured.** The Gemini provider has read it
  after `GEMINI_API_KEY` since 0.1.0, and the README's API keys row and `.env.example` said so
  for Cohere's and Meta's second names only. A test now holds both to every name in
  `KEY_FALLBACKS`.

### Known limitations

- **No pi0 has run.** quackd has not loaded, benched or served one, on a laptop or on a GPU, and
  its checks rest on stand-ins of the SO-101 fine-tunes read on the Hub on 2026-10-25. Each of
  those fine-tunes is 8.89 GB of weights, and the server fetches PaliGemma's tokenizer files,
  21.9 MB, beside it.
- **No trained X-VLA has run.** quackd has not loaded, benched or served one, and its checks
  rest on a stand-in of `mertkirgil/xvla-so101-renkli-kalem`, read on the Hub on 2026-10-25,
  whose weights are 1.76 GB, and on the tiny random one CI serves. Several SO-101 X-VLAs there
  keep the eight-number state of the base they were fine-tuned from, and the server serves one,
  which the arm's connect then refuses on its count.
- **No GR00T has run, and serving one fetches about 16 to 20 GB.** quackd has not loaded,
  benched or served a GR00T N1.7, and its checks rest on stand-ins of
  `jammyyang/groot17_so101_pick_place` and `haydendoo/so101_pickup_groot_medium_new_v1`, read on
  the Hub on 2026-10-25. Their weights were 9.34 GB and 12.58 GB, and the base's 6.91 GB more.
  Its backbone's processors are built the first time it is asked for a chunk, so a session's
  first chunk takes longer than the rest, and nobody has timed whether the 5 s a segment gives a
  first chunk covers it. LeRobot ties the backbone's unused output head to its input
  embeddings before the checkpoint's weights load strictly over the base's, and that strict load
  has not been run either.
- **MolmoAct2 is not served.** Its base model maps its classes to code of its own (`auto_map`),
  which the server refuses in every model it loads, and serving it would take the first exemption
  from that rule, which was deferred rather than written
  ([ADR-0060](docs/adr/0060-hugging-face-serves-many-vlas.md)).
- **None of the seconds or dollars this release saves has been measured on a real model.** The
  cache's reads, the warm-up's seconds, what `--effort low` takes off a turn and what shadow no
  longer costs one were checked against stand-ins alone. The check is one paid run per vendor with
  the cache on and with `QUACKD_ANTHROPIC_CACHE=0`, reading `cache_read_tokens`, `cost_usd` and
  `latency_s` per call, and timed requests to each keyed decision LLM.
- **The cache markers and `drop_block` have not met.** On Opus 5.5 and Fable 5.1 the loop leaves
  out the thinking a trim invalidates and has the API drop the one block it may not leave out.
  That markers can move around it freely comes from Anthropic's guidance, and no paid request has
  proved it.
- **`quackd drive` has driven no real robot.** It has run against the mock Microduck and the
  scripted pilot, and its keyboard against prompt_toolkit's own pipe input. Its prompt is
  untried in PowerShell under conhost, in Windows Terminal and in VS Code's terminal, and how long
  a real robot takes from a line to its first motion is unmeasured. The fast lane's floors are the
  stepper's, unmeasured on its question. `quackd log` does not yet draw drive's own lines. A
  goal's pilot takes `--llm` alone, without the vendor flags `quackd run` takes, and a session
  keeps no memory. A verb that needs a yes, run inside a call a person named, is answered by
  `--yes` alone. Over MCP, a verb an adapter brings may not read a stop asked for beside it, the
  ToddlerBot's own `search_scan` and the arm's joint moves among them. Drive's brake and a newer
  command end any of them.
- **quackd has never created a Hugging Face Endpoint.** `quackd policy endpoint` and
  `--policy-url hf:NAME` have run against the test suite's stand-ins for the API, the proxy and
  the container on 127.0.0.1, the container serving `scripted:sweep`, and nothing was paid for.
  What Hugging Face's pages leave out is designed for at its worst and recorded as UNVERIFIED:
  whether the proxy passes `Authorization` on to the container, whether an endpoint pulls the
  image from ghcr.io, whether the proxy answers 503 or 502 while a replica starts, what a paused
  endpoint answers, what a `private` endpoint admits, what the log route answers, which instance
  types the quota listing names, whether the API answers a read of an account's endpoints to that
  account and its organisations' members alone, whether an endpoint keeps its address for its
  life, and whether torch finds the GPU. No bench through an endpoint has been timed.
- **A cold start takes minutes**, since the container installs torch from PyPI and fetches the
  checkpoint each time it starts, and a run that reaches an endpoint at zero is told it is not
  serving rather than held. Start it with `up` before a run.
- **A laptop closed while `up` waits leaves the endpoint starting**, and billing until it idles
  to zero, `--idle-minutes` later. Ctrl-C says so, and `quackd policy endpoint pause` stops the
  bill at once.
- **A CUDA build of torch newer than the endpoint's driver would run on the CPU.** `up` asks the
  server whether it has a GPU once it serves, and pauses one that says it has none.
- **A project's `.env` is trusted as the shell is.** One that names another registry with
  `QUACKD_REGISTRY_DIR` and sets `HF_TOKEN`, where the shell sets none, makes
  `--policy-url hf:NAME` reach an endpoint of that token's account, with that token: the user's
  own Hugging Face token is never sent, but the arm is driven by that endpoint's policy and its
  frames go there.

## [0.25.0] — 2026-10-25

0.25.0 keeps the SO-101 playing while a policy's next chunk is on its way. A policy served with
no `--latency-s`, which is how `quackd policy serve` starts one unless told otherwise and how the
guide serves SmolVLA and pi05 on a rented GPU, had the arm hold still through every round trip,
once every half chunk. A segment now waits in a tick only for a chunk it has nothing left to
play from, a first chunk among them, and the guide's ACT, benched on this project's laptop with
no `--latency-s`, went from 22.6 Hz of 30 to 29.3.

`--async-chunks/--no-async-chunks` says which way a run plays a policy server's chunks, beside
`--policy-url` on `quackd run`, `quackd preflight` and `quackd serve-mcp`, and with `--bench` on
`quackd policy check`. `--async-chunks` is the default. `--no-async-chunks` plays each chunk out
before it asks for the next, as LeRobot's own loop does, and holds the arm still while the next
is on its way, every tick of the wait still reading and judging the arm. The same ACT benched
26.2 Hz with it.

A first chunk slower than a second is no longer taken for a stall, a bench of a policy asked
every tick that declares a latency plays its answers, where it played none, and until a verdict
is recorded an empty frame comes with the rule that a target not found yet is not by itself a
reason for `uncertain`, from a line [@Vallhalen](https://github.com/Vallhalen) wrote and
measured (#25).

No policy has driven the arm through any of it, and no round trip through a rented GPU has been
timed. What that leaves owed is under Known limitations.

### Added

- **`--async-chunks/--no-async-chunks` says how the arm plays a policy server's chunks.** It
  goes beside `--policy-url` on `quackd run`, `quackd preflight` and `quackd serve-mcp`, and
  beside `--bench` on `quackd policy check`, and `--no-async-chunks` is refused without the flag
  it goes beside. `--async-chunks`, the default, asks for each chunk while the arm still plays
  the last (below). `--no-async-chunks` plays each chunk out before it asks for the next, holds
  the arm still while that one is on its way and plays it from its first action as it lands,
  whatever latency the server declares, as LeRobot's own loop plays chunks. No chunk is waited
  for in a tick, so every tick of the wait still reads and judges the arm, the stall's clock
  stops while nothing is sent and goes on when sending does, and a wait is starved out after the
  5 s a first chunk gets. The simulator holds each chunk back its declared latency, or a tick
  for a server that declares none, and a test holds a lockstep clock, the simulator's kind, and
  the wall's kind to the same goals. A policy that answers one action at a time has its first
  answer land a tick later and then runs as it did, and one asked every tick runs as it did. On
  this project's laptop on 2026-10-25 the guide's ACT, served with no `--latency-s`, reached
  29.3 Hz of 30 over a 30 s bench by default and 26.2 Hz with `--no-async-chunks`, whose 113
  starved ticks were the waits for its 8 chunks. The bench says so in its starved row, and the
  `policy` block of `run_start` and `summary.json` carries `async_chunks`
  ([docs/adapters/lerobot/README.md](docs/adapters/lerobot/README.md#a-run-with-a-policy-server)).

### Changed

- **A policy served with no `--latency-s` no longer holds the arm still through every round
  trip.** `quackd policy serve` declares no latency unless it is given one, and a segment
  waited for each chunk of such a policy in the tick that asked for it, with half the last
  chunk still queued, so the arm held its last goal for the whole round trip, once every half
  chunk. The guide's rented GPU, where SmolVLA and pi05 run, served with no `--latency-s`, and
  so did the arm's page. A segment now waits in the tick only for a chunk it has nothing left
  to play from, a first chunk among them, and leaves every other to land while the arm plays
  what is queued, as it always did for a declared latency, unless `--no-async-chunks` says to
  play each chunk out first (above). On this project's laptop on 2026-10-25 the guide's ACT, 100
  actions at 30 Hz, reached 22.6 Hz of 30 over a 30 s bench with no `--latency-s` before the
  change and 29.3 Hz after it, its median round trip 390 ms and then 455 ms on the laptop's CPU,
  and the only ticks it skipped after the change were the 20 its first chunk took to come back.
  The wait cost a round trip's ticks out of every half chunk, so a policy of 50 actions, as
  SmolVLA and pi05 are by LeRobot 0.6.1's defaults, lost twice the share an ACT of 100 did to
  the same round trip. No round trip through a rented GPU has been timed, and no policy has
  driven the arm: a bench streams synthetic observations. A server that stops answering is
  noticed once what was queued has played out, as it always was for a declared latency. On
  the simulator a chunk of a policy that declares no latency, asked for with something still
  queued, is held back a tick, the soonest the arm takes it in, so a rehearsal of one that
  answers within a tick still plays what the arm would, seam for seam. `--latency-s` still
  sets how long the simulator holds each chunk back, how early a segment asks for the next
  once twice the latency is more than half a chunk, and the refusal past half a chunk.
  `quackd policy check --bench` paces by the same rule, so a first bench of a policy served
  with no latency waits out its first chunk alone, and its skipped row says which ticks passed
  in that wait and which a pacer woke too late for. The guide's benches are from 2026-10-25
  ([docs/guides/policies.md](docs/guides/policies.md#on-the-laptop-alone)). Tests hold a chunk
  asked for with actions queued to landing while the arm plays, a tick late and half a chunk
  late, a lockstep rehearsal of one to the trajectory the wall's clock plays, and a bench to
  waiting out its first chunk alone. The tests that run a segment through the real server on
  a clock that moves only when slept now wait there for each answer they expect, since one
  left to land would otherwise land as late as the machine running them was slow.

### Fixed

- **A first chunk slower than a second could be taken for a stall.** A segment waits for the
  first chunk of a policy that declares no latency in the tick that asked for it, then sent
  the chunk's first goal on the reading taken before the wait, with the stall's clock started
  there. So a policy that held the arm still was taken for stalled on its second goal, and
  `manipulate` called the segment ok, and a hot joint or torque that went off during the wait
  was seen only after that goal had gone out. A tick a wait for a chunk outlasts now sends
  nothing, and the tick due when it ends reads the arm, judges it and plays, so a stall is a
  second of goals, as the arm's page says. A policy that answers one action at a time still has
  its late answer sent in the tick that asked, since that is the only goal there is, on the
  reading taken before the wait, so a hot joint or torque that went off during it is seen on the
  tick after, and its stall's clock now starts when that goal goes out. A tick that waits for
  nothing in it, or whose wait brings nothing to play, runs as it did. The bench paces by the
  same rule, and tests hold a first answer slower than a stall, a chunk or one action, to a
  second of goals, a refill whose own tick ran long to sending its goal late, and slow answers
  with nothing in them to being starved out.
- **A bench of a policy asked every tick played none of its answers once it declared a
  latency.** A policy in tick mode, as an ACT trained with temporal ensembling is served, is
  asked every tick, and a segment plays each answer on the tick it takes it in, the tick after
  it was asked where the server declares a tick to answer. `quackd policy check --bench` kept
  each answer for the tick it was asked at instead, so it was a tick stale when it was taken in
  and thrown away. A scripted server in tick mode that answered in about a millisecond,
  declaring a tick at 10 Hz, benched 0 of 100 ticks over 10 s, said all 100 starved ticks came
  before its first answer, which a segment gives time for, and said the latency it was served
  with covered what it timed. It now benches 99 of 100, starving only the tick before its first
  answer came back, and a test holds a bench of it to playing its answers. A policy in tick
  mode that declares no latency was waited for in each tick and played, and still is.
- **An empty frame before the verdict comes with the rule beside it.** 0.10.0 reworded
  `assess_task` so that a target the pilot has not found yet is not by itself a reason for
  `uncertain`, and on Qwen3-32B-AWQ that did not move the verdicts: on 0.10.0,
  `quackd run --goal "Find the ball and kick it."`, the README's own goal, was `feasible` 5
  times in 30, and 23 of its 25 `uncertain` reasons, coded blind, were about where the ball
  was. Until a verdict is recorded, when a detector looks at the frame and finds nothing, the
  observation now ends with a line saying that is not by itself a reason for `uncertain`, and
  keeping `uncertain` for a limit that turns on the unseen thing's mass or size, or on a figure
  the datasheet does not publish. On 0.10.0 with that line the same goal was `feasible` 27
  times in 30, and every one of those runs found the ball and kicked it. On 02411b3, a build of
  2026-09-17, a task the body cannot do, carrying the ball, was `infeasible` 5 times in 30
  without it and 17 with it, and `feasible` none either way. The shipped line adds "by itself",
  so it says what every other surface that speaks of the verdict says, and those two words are
  the one part of it nobody has measured. Nor has anybody measured the line on a build since
  0.14.0, which changed what the prompt says about a figure nobody published, or on any model
  but that one, with thinking off. Tests hold it to both halves of the rule, keep it off a
  frame with something in it, off a run with no detector and off the turn after an `uncertain`
  nobody could answer, and bring it back after a `feasible` the body's own sheet refuses.
  Thanks to [@Vallhalen](https://github.com/Vallhalen) (#25), who measured the rewording, found
  it did not move the verdicts and moved the reasons only a little, and wrote and measured this
  line. The local LLM guide said the rewording was unmeasured, which it has not been since
  2026-09-18, and gives their numbers now
  ([docs/guides/local-llms.md](docs/guides/local-llms.md#honest-notes),
  [docs/concepts/safety.md](docs/concepts/safety.md#when-the-pilot-is-unsure)).

### Documentation

- **uv.lock takes fifteen updates from Dependabot's #39:** typer 0.27.3, pydantic 2.14.1,
  python-dotenv 1.2.4, mcp 2.3.0, cryptography 50.0.2, anthropic 1.17.0, openai 3.35.1,
  google-genai 2.32.0, typesafe-sdk 0.7.5, laya 0.4.12, ruff 0.17.1, mypy 2.4.0, pre-commit
  4.7.0, mujoco 3.16.0 and onnxruntime 1.31.0. ruff and pydantic each found one line on main,
  changed there before the merge without changing what quackd does. ruff 0.17's new RUF071 flags
  `os.path.commonprefix`, which Python 3.15 deprecates, so MOSS works out what its blueprints'
  names start with by a helper of its own, and pydantic 2.14 types `create_model`'s fields as a
  `TypeForm`, so ToddlerBot's narrowed `perform` hands it the motions' `Literal` as `Any`. Every
  CI job passed on the pull request. CI installs none of the five provider SDKs, though, so its
  green run says the lock resolves and no more, and each was run here on the lock instead. A
  real `hello-world` on anthropic, on openai through the Responses API and through Chat
  Completions, and on google-genai, each against a stand-in for its API on 127.0.0.1, sent the
  same request bodies as anthropic 1.9.0, openai 3.22.1 and google-genai 2.25.0 had, camera
  frames aside, and read its tool calls back to the run's success. anthropic's went to the beta
  endpoint with both of quackd's betas, `fallbacks` and `block_binding`. typesafe-sdk 0.7.5
  reworks its transport and adds images, multipart bodies and compression, and a shadow run
  against a System One server on 127.0.0.1 sent the same requests as 0.7.2 and recorded a
  decision on every turn. It compresses a body of 1024 bytes or more with zstd wherever it can,
  which is Python 3.14 and later or with the `typesafe-sdk[zstd]` extra, and hello-world's
  requests were over 2 KB. On 3.12 without that extra it sent plain JSON, as 0.7.2 did. laya's
  `Router` and `predict` take quackd's arguments as before, `typed-decisions` is still one of its
  checkpoints, and its answers carry the same `choice`, `confidence` and `noul`. Its own default
  checkpoint is now `multilingual`, which quackd does not use, since it names the one it loads.
  Its weights were not loaded. mujoco 3.16.0 and onnxruntime 1.31.0 passed the physics job, and
  the nightly jobs that load the Microduck's real model and the arm's run on them from the night
  after the merge. No requirement a user installs against moved.
- **Dependabot ignores pillow 13, which lerobot 0.6 rules out.** lerobot 0.6.1, the arm's SDK on
  Python 3.12 and later, asks for `Pillow>=10.0.0,<13.0.0`, and Dependabot's uv job asks uv for
  one version of a package in every split of the lock at once, so from 2026-10-20 every run of
  it ended red on pillow 13.0.0, though the rest of the group still came through.
  `.github/dependabot.yml` now ignores pillow from 13.0.0, as it does numpy and
  opencv-python-headless past lerobot's pins, so a 12.x release still comes in the group. The
  configuration ships in no package, so this needs no release.

### Known limitations

- **No policy has driven the arm through 0.25.0's chunking.** A segment asking for each chunk
  while the arm plays, from a server that declares no latency, has run against a fake arm, on
  the simulator and in benches, which stream synthetic observations, and one playing each chunk
  out with `--no-async-chunks` against a fake arm, on a lockstep clock and on the wall's kind,
  and in benches. The run that would settle both is the guide's ACT on the arm, served with no
  `--latency-s`, once as it is and once with `--no-async-chunks`, each run's `policy` block sent
  back with its `hz`, `starved_ticks`, `round_trip_ms` and `async_chunks`, which the
  learned-policy item in [PLAN.md](PLAN.md) now asks for.
- **No round trip through a rented GPU has been timed,** so what the change is worth to SmolVLA
  and pi05, whose chunks hold half the guide's ACT's actions, is the arithmetic in the entry
  under Changed and not a bench.
- **A bench of a policy asked every tick says less than a segment would.** A segment ends on
  the first tick such a policy has not answered by, and the bench counts that tick as starved
  or skipped and goes on. It also suggests a `--latency-s` past a tick for one whose steps take
  that long, which `quackd policy serve` takes and every segment then refuses, since a policy
  asked every tick has to answer within one. Tick mode has not run with a real policy: the one
  quackd serves in it, an ACT trained with temporal ensembling, is refused without a GPU.

## [0.24.0] — 2026-10-23

0.24.0 makes letting go of the SO-101 mean putting it down first. `quackd robot release` folds the
arm to its recorded rest pose and lets go of it there, where 0.14.0's command took torque off
wherever the arm stood and let it fall. A fold that misses, there or at the end of a run, asks
twice before anything drops: first to clear the arm's way and fold once more, and only if that
fold misses too, whether to take torque off where the arm stands, saying that it will drop.
Anything but Enter keeps torque on.

A real arm with no rest pose recorded is refused a run, a dry run, an MCP session and a flock,
and its close no longer lets go of it wherever it stands: `doctor` and `robot list --probe` keep
it powered and say so, at a terminal `doctor` asks before it drops it into your hands, and
`quackd robot rest-pose` lets go of the arm only once its pose is confirmed. A first Ctrl-C or
`q` now ends a model call that is still out, and stops the fold at the end of a run that ended by
itself.

A turn the model's API could not answer for now, a rate limit, an overloaded server or a dropped
connection, is asked again up to four times, after waits that grow from 5 seconds to a minute,
with the robot holding still and the heartbeat watching it. The SDKs retry nothing of their own
now, so every try is on the record and under the stop.

None of it has run on an arm, and nothing has been asked again after a real 429 or 529. The
bench steps that would settle the arm's part are under Known limitations.

### Added

- **A turn the model's API could not answer is asked again.** A rate limit (429), an overloaded
  or failing server (529, any 5xx), a request the server timed out (408), and a call that timed
  out or lost its connection once the API has answered in the run, with a turn or a status,
  used to end the run `error` after the SDK's own retries. The loop now waits 5, 15, 30 and then
  60 seconds, or as long as a `retry-after` asked where that is longer, and asks the same turn again, sending the
  same request byte for byte, with the robot holding still and the heartbeat watching it.
  Ctrl-C, `q`, a heartbeat that fails or a flock's stop ends the wait as an abort, and the arm
  folds as at any end. What decides is the SDK's own exception, read under the provider's
  (`quackd.agent.providers.base.transient_failure`), never its message, so a bad key, a refused
  request or anything quackd raised itself still ends the run at once. A connection that never
  answered at all is tried twice more, half a second and then a second later, and then ends the
  run, being as often a wrong address as a dropped line. A run out of tries, out of `max_minutes`, or asked by the API to
  wait longer than 120 s ends `error` saying which. A turn asked again is not charged to
  `max_llm_calls`.
  [ADR-0059](docs/adr/0059-a-turn-the-api-could-not-answer-is-asked-again.md).
- **Every try is on the record.** A failed `llm` record gains `retry_in_s` where quackd will ask
  again, and `llm_request` and `llm` gain `attempt` from a turn's second try; the summary gains
  `llm_retries` and `llm_retry_wait_s` only where a run asked again, so older summaries read as
  they did. The log shows a failure quackd will ask again as a yellow warning rather than a red
  error, the live status line says how long it waits, and the counters under the verdict read
  `llm calls 5 (+1 asked again)` and add `waits` to the time's split, as they do under the line a
  run that gave up ends on.
  `tests/fake_openrouter.py --fail N:STATUS` answers the first N chat requests with STATUS.
- **`quackd doctor` gives the hands time to come away before it folds the arm.** It says
  `folding NAME to its rest pose in 3 s: take your hands away from it now` and waits those
  seconds, as `quackd robot release` does, terminal or not, `--json` on stderr, and Ctrl-C or `q`
  while the arm connects, in those seconds or during the fold stops it with the arm held where it
  is. Its warning before the connect asked for the arm to be supported until doctor had finished,
  which kept a hand under the fold, and now says to take the hands away when doctor says so.

### Changed

- **Releasing the SO-101 means folding it first.** `quackd robot release NAME` folds the arm to
  its recorded rest pose and lets go of it there, through the door that refuses anywhere but that
  pose, where 0.14.0's command took torque off wherever the arm stood and let it fall into
  whoever was holding it. Its warning, printed before it connects, says to support the arm while
  it connects and then to take your hands away when it says so, and once connected it says it
  folds in 3 seconds (`quackd.release.HANDS_AWAY_S`), which is when the hands come away; the
  close ends on
  `the arm is limp at its rest pose, where it was folded and let go of because you asked for it`.
  Ctrl-C or `q` during those seconds, or during the fold, stops it where it is and keeps torque
  on.
  [ADR-0058](docs/adr/0058-letting-go-of-the-arm-means-putting-it-down-first.md).
- **A fold that misses asks twice before anything drops**, in `quackd robot release` and at the
  end of a run, where the run used to offer one Enter that released the arm where it stood. The
  first question asks you to clear whatever is in the arm's way and press Enter, and it folds once
  more; only if that fold misses too does the second say that Enter now takes torque off where it
  stands and that the arm will drop, so hold it first. Anything but Enter, at either, keeps torque
  on: a wait that runs out (60 s each), a Ctrl-C, a `q`, a terminal with nothing to read. Neither
  is put over an arm that stopped answering. `AgentLoop.RELEASE_OFFER` and `RELEASE_OFFER_S` are
  gone (`quackd.release`), and the record's `release` events gain the stages `refold` and
  `folded` and the `kept` reasons `interrupted during the fold` and
  `the arm did not answer the second fold`.
- **`quackd robot release --yes` skips only the question before the connect.** The two
  questions are still put wherever a terminal can prompt. With none, a fold that arrives lets go
  at the pose, and one that misses keeps torque on and exits 1.
- **An arm with no rest pose is asked about before `quackd robot release` connects to it**,
  because letting go of it where it stands is all the command can do, and connecting is where the
  arm first goes limp: Enter connects and drops it into your hands, anything else connects
  nothing. With no terminal the command refuses, `--yes` or not.
- **A real SO-101 with no rest pose is kept powered by its close**, where 0.23.0 and before let go
  of it wherever it stood and it fell. `quackd doctor` and `quackd robot list --probe` read such an
  arm and keep it powered, saying so (`torque left on: no rest pose recorded` on the probe's line),
  with the ways to put it down and to give it a pose. At a terminal `doctor` first asks whether to
  drop it into your hands, the way to get a first arm limp for folding by hand: Enter, with a hand
  under it, drops it, and anything else keeps it powered. The mock and the simulator let go as
  before.
- **`quackd robot rest-pose` lets go of the arm only once the pose is answered for.** Its close
  let go of the arm before the question was answered, so a no left it let go of anyway, and an arm
  read standing rather than folded fell. A yes, or `--yes`, records the pose and lets go of the
  arm where it lies; a no, or a read that fails, keeps a real arm powered in its fold, and the
  close's line says how to put it down. Without `--yes` and with no terminal to ask on, the
  command refuses before it connects rather than after it has read the arm.
- **A first Ctrl-C or `q` ends a model's call that is still out**, the pilot's and a decision
  LLM's, without waiting for the answer, which is thrown away: the run ends `aborted` and the robot
  is put down at once. The press used to be noticed only once the call returned.
- **A first Ctrl-C or `q` stops the fold at the end of a run that ended by itself**, a success, a
  failure or a spent budget, holding the arm where it is with torque on, where the fold used to
  finish first. A flock stopping its members, or a heartbeat that fails, does not stop it.
- **The SDKs retry nothing of their own.** Every anthropic and openai client is built with
  `max_retries=0` (`quackd.agent.providers.base.NO_SDK_RETRIES`), where each made two tries inside
  the call that a first Ctrl-C could not reach and that a `retry-after` could hold for minutes, so
  every try is now the loop's, on the record and under the stop. A server that answers
  `x-should-retry: true` is asked again at any status.
- **One of quackd's own adapters from another release than the core is refused**, wherever a
  body is described or built, with the command that reinstalls the two together. pip lets that
  mix happen with a warning, and a core reads every declaration an older adapter does not make as
  none, `REST_POSE_BACKENDS` among them, which would let a run of a real arm with no rest pose go
  ahead. A third party's adapter is not asked.
- **The close's line for an arm left holding itself up names one way out**: clear its way and run
  `quackd robot release NAME`, which folds it and lets go at the pose, supporting the arm while
  that connects, or cut its power. It no longer names `quackd doctor --robot NAME` to park it.
- **A real SO-101 with no rest pose is refused a run.** `quackd run`, a dry run included,
  `quackd serve-mcp` and a flock refuse a `lerobot:real` arm with no recorded rest pose before
  anything connects, because there would be nothing to fold it to at the end, and its close, which
  keeps such an arm powered rather than let it fall, as it fell at the end of every run on
  2026-09-15, would leave it holding itself up wherever it stopped. A spec on its own,
  `--robot lerobot:real --address PORT`, has no name to keep a pose under and is refused the same
  way, with `quackd robot add NAME lerobot:real --address PORT, then quackd robot rest-pose NAME`
  as the way back, and for a spec in `--robots` or a task file's `robots:`, running the flock
  from a stored one after that, since neither reads the registry. A run is refused once its
  task file has been judged against the arm, so a task that could never run there says so
  first. `doctor`, `robot list --probe`, `rest-pose`, `robot release`, the mock and the
  simulator are unaffected, and `run_pilot_flock` refuses such an arm too, for a library caller.
  An adapter declares the backends this applies to in `REST_POSE_BACKENDS`, which
  `quackd.adapters.factory.needs_rest_pose` reads before anything is built, and
  `quackd robot rest-pose NAME --clear` on a real arm now says its runs are refused until a pose
  is recorded again.

### Fixed

- **A pilot flock's member named with `--robot NAME` was built without its registered address,
  cameras and rest pose.** The flock took only the spec of the robot it named, so a registered arm
  ran as a bare `lerobot:real`, whose close would have let go of it wherever it stood. The member is
  the registered robot now, built from its own entry.
- **A fold that missed over servos that browned out was said to be holding the arm up.** A servo
  that resets under stall current comes back answering every read with its torque off, and the
  end of a run said the arm was holding itself up and offered to release it, and the close said
  torque was left on and the arm would not fall as it stood. Where the arm's own torque read finds
  joints off, nothing is offered or asked now, and the run, `robot release` and the close say what
  the read found and that the arm may already have dropped. `robot list --probe` says
  `torque reads OFF: the arm may have dropped` for it.
- **A `q`, or any line typed at the end-of-run offer, released the arm.** The kill switch's key
  thread took every line for Enter, so `n` and Enter was a yes, and a terminal in its usual mode
  hands `q` over only with the Enter that sends its line, so the key that stops a run was read as
  Enter too. A press of the kill switch is never an Enter now, at any wait, and a question whose
  Enter moves or drops an arm takes only a bare Enter: anything typed on the line is a no, and
  the record says `answered no`.
- **An Enter typed before a question answered it.** The key thread reads whatever is already in
  the terminal as it starts, so an Enter pressed while a command was starting answered the first
  question it put. A question that moves or drops an arm passes by an Enter read within half a
  second of being put (`quackd.safety.TYPED_AHEAD_S`).

### Documentation

- **The SO-101's release, on every page that told it.** The LeRobot page's
  [Releasing it](docs/adapters/lerobot/README.md#releasing-it) (the old anchor still works), the
  first run, the hardware checklist, safety, the registry guide and the README say that releasing
  folds first and what the two questions are, with output captured on `lerobot:mock`.
  [ADR-0058](docs/adr/0058-letting-go-of-the-arm-means-putting-it-down-first.md) records Rok's
  decisions, and ADR-0039 and ADR-0045 carry dated amendments.
- **A real arm is run by its registered name.** The LeRobot page, the first run and the hardware
  checklist run `arm-01` where they ran a bare `lerobot:real`, say that a run of a real arm with
  no rest pose is refused and why, and quote the refusal, captured on a scratch registry. Safety,
  the registry and MCP guides, the README and the adapter guide say the same, the adapter guide
  documents `REST_POSE_BACKENDS`, and it no longer says that `quackd robot release` lets go of
  the arm wherever it stands.
- **Robot Passport's phase 7, planned in the repository.**
  [docs/design/robot-passport-phase-7.md](docs/design/robot-passport-phase-7.md) says what each of
  its six pieces is, what is decided and what is left to decide, where its code goes, what done
  means and what it needs, then how the spec moves to its own repository and what else is open,
  and PLAN.md points to it. `scripts/passport_readme_ratio.py` measures the README's Robot
  Passport section against the pages it summarises.
- **The arm's first-run guide shows the task file it has you load, and says which policy
  latencies are refused.** M09 had the reader load `arm-look.duck` over MCP and quoted what
  loading it answers, and the file was nowhere. It is on the page now, and loaded through
  `robot_load_duckfile` on `lerobot:mock` it answers the quoted note word for word. Section 17,
  and Part 2's note on it, now say what 0.16.1 made `quackd policy serve` and `check` refuse, a
  latency of 5 s or more or one longer than half a chunk's actions take to play, and that the
  arm refuses to connect to a server an earlier quackd started with such a latency, before any
  motor is energised. The README counted fifteen bundled starters, nine of them `duck: 1`, where
  eighteen ship and twelve are. Two tests in `tests/test_docs.py` hold the guide's file to the
  budget it quotes and every page's starter counts to the starters that ship.
- **When the model's API fails**, a section of its own in
  [docs/concepts/safety.md](docs/concepts/safety.md#when-the-models-api-fails), with output
  captured against the stand-in server. The OpenRouter guide no longer says a 429 ends the run,
  and the record's fields are in docs/concepts/architecture.md.
- **A real arm with no rest pose is kept powered, on every page that said a probe let it go.** The
  LeRobot page, the first run, the hardware checklist, the registry and MCP guides, the adapter
  guide and the README say what `doctor`, `robot list --probe` and `rest-pose` do with such an arm
  and why a run of one is refused, with the refusals captured again on a scratch registry and
  doctor's question captured over the real backend with a stand-in for the arm's bus. ADR-0058 and
  ADR-0059 carry dated amendments, and the adapter guide says why quackd's own adapters must come
  from the core's release.

### Known limitations

- **None of the folding release has run on an arm.** The fold before `quackd robot release`
  lets go, both questions, a Ctrl-C during a fold and the release where the arm stands are
  exercised against `lerobot:mock`, a fake arm and the simulator. The bench steps that would
  settle them, in order, with a hand on the power switch throughout:
  1. `quackd robot release arm-01` on an arm a run left holding itself up away from its pose:
     support it through the connect, take your hands away, and say whether it folds and is let
     go of at the pose, and whether it then rests there limp.
  2. Something soft in the fold's path: the stall, the first question, Enter once it is cleared,
     and whether the second fold arrives.
  3. The path kept blocked: the second question; hold the arm and press Enter; say how far it
     drops and whether one hand catches it.
  4. Each question left for its 60 s: torque stays on and the arm holds where its fold stopped.
  5. Ctrl-C during the release's fold: the arm stops and holds; say how far it went on after
     the press.
  6. A run whose teardown fold is held back by a hand: the first question, the hand taken away,
     Enter, and the close letting go at the pose.
  7. `quackd doctor --robot arm-01` with its rest pose cleared: the arm stays powered at the end;
     at the question, a hand under the arm and Enter, and whether it drops into the hand.
  8. `quackd doctor --robot arm-01` with its pose recorded: the 3 s count, the hands away, the
     fold, and a `q` during the count holding the arm where it is.
  9. A run that ends by itself, and Ctrl-C during its fold: the arm stops and holds there.
- **Nothing has been asked again after a real 429 or 529.** The retry has run against stand-ins
  of the SDKs' exceptions and against the real openai SDK talking to a stand-in server, not
  against a vendor having a bad minute. On a simulator or a mock `max_minutes` does not bound the
  waits, since their clock stands still while the loop waits.

## [0.23.0] — 2026-10-21

0.23.0 adds Robot Passport, a small open protocol for a robot to introduce itself to one it has
never met: who it is, who vouches for it, what it can and cannot do, how a person stops it and how
to reach it. A passport is one small JSON document, signed by a robot that holds an Ed25519 key,
and found at `/.well-known/robot-passport.json`, over DNS-SD, on an MQTT broker, on a ROS 2 topic
or on a QR sticker. quackd is its first implementation. It builds a signed passport for every
robot it drives from the robot's own manifest, checks anybody's the way a reader would, pins a
robot or its owner once a person says yes, and talks for its robots.

Two robots that have met talk in six small signed messages, and a robot met may ask one of quackd's
robots for a short pause or to identify itself. Fixed code decides, never the pilot, out of one
budget every peer shares, and what it grants runs through the executor under two new gates: a peer
reaches only the `stop` a pause begins with and the `say` an identify is, and a granted pause holds
even a head still. A stranger reaches a pilot under a name made from its key and never with its
words.

Nothing of it has run between two machines or on a robot. Most of it has run between processes on
one machine, over HTTP and through a real MQTT broker, and its roadmap says so part by part. The
specification, its schemas, its test vectors and eighteen pages that explain every part of it are in
[docs/passport/](docs/passport/README.md).

And `stop` is never refused now for want of budget.

### Added

- **Robot Passport, a small open protocol for robots that have never met.** A robot says who it is,
  who vouches for it, what it can and cannot do, how a person stops it and how to reach it, in one
  JSON document that can be under 100 bytes, which a robot that holds an Ed25519 key signs as a
  compact JWS over its exact bytes, with stamps from its owner. A reader finds it at
  `/.well-known/robot-passport.json`, over DNS-SD as `_robot-passport._tcp`, retained on an MQTT
  broker, on a ROS 2 topic or on a QR sticker, and two robots that have met talk in six small signed
  messages. The specification is draft 0.1, with four JSON Schemas and test vectors signed by a
  second implementation of its rules, and quackd is its first implementation
  ([docs/passport/](docs/passport/README.md),
  [ADR-0057](docs/adr/0057-a-robot-can-introduce-itself-to-one-it-does-not-know.md)). It asks nobody
  to install anything: `cryptography`, which every install already had through `mcp`, is now a
  dependency of quackd's own.
- **`quackd passport`, a passport for every robot quackd drives.** `show --robot NAME` builds it
  from the robot's manifest and signs it with a key quackd makes for the robot the first time it
  is asked, and `--json`, `--jws` and `--out FILE` print or write it. `check` reads anybody's
  passport from a file, from stdin or from a URL the way a reader would, says what it proves and
  what it only claims, and names the rule of the specification a refusal broke. `set --robot NAME`
  records how a person stops that robot, `owner` who owns your robots and what robots they meet
  may ask them for, and `stamp` stamps a robot with your owner key, or with `--did` one whose key
  lives on the robot. `known` and `forget` list and remove pins. `serve` serves passports at the
  well-known URL, one or several with an index, advertises them over DNS-SD with `--announce` and
  keeps them retained on a broker with `--mqtt HOST`, both with `quackd[lan]`. `qr --robot NAME
  --url URL` writes the robot's QR sticker, read back before it is saved
  ([docs/passport/quackd.md](docs/passport/quackd.md)).
- **`quackd meet SOURCE`**, which finds a robot's passport from a URL, a `HOST[:PORT]`, a picture of
  its sticker, a passport file, `mqtt://BROKER/KEY`, or `ros://HOST[:PORT]` for a ROS robot on
  rosbridge, under fetch rules that follow no redirect, read nothing past 12 KB in five seconds a
  request and contact no host but the one named. It shows the robot's tier: a stranger, known, the
  same owner's, or one this quackd drives. `--pin` and `--pin-owner` pin its key or its owner's once
  a person says yes. `--as NAME` says hello as one of your robots, each proving it holds its key,
  and then `--say TEXT` says one thing, `--ask pause:N` or `--ask identify` asks for a courtesy, and
  `--share "ABOUT: FACT"` with `--place` tells it something your robot saw.
- **`quackd discover` lists the robot passports on the network** beside the quackd robots it
  always listed, a passport's `--json` object carrying `"kind": "passport"`.
- **Talk in a run.** `quackd run --talk` serves the run's robot with a talk inbox, on port 8790
  unless `--talk-port` says otherwise, and `--meet HOST` says hello to a robot as the run starts. A
  robot met appears in the pilot's observation under Robots nearby, as JSON within a budget of
  characters a turn, a stranger under a name made from its key and never with its words, and the
  pilot can `tell` it something. In a dry run the pilot's `tell` sends nothing, though `--meet`
  still says hello, and the bundled `nearby-hello` task is the smallest run that does all of it.
- **`robot_nearby`**, an MCP tool that `quackd serve-mcp --talk` adds, which shows a client the
  robots met exactly as a pilot would see them. Every `robot_list` row names its robot's
  passport, or `null` for a robot with no key yet ([docs/guides/mcp.md](docs/guides/mcp.md)).
- **Courtesies.** A robot met can ask one of quackd's robots for a pause, of 5 s at most for a
  stranger and 10 s for a known robot, or to identify itself by sound, out of one budget every peer
  shares: a pause a minute and an identify every ten seconds. Fixed code decides, never the pilot,
  and what it grants runs through the executor under a new source, `peer`, and two new gates:
  `peer`, which admits only the `stop` a pause begins with and the `say` an identify is, and
  `paused`, which admits nothing that moves any part of the body while a granted pause holds and
  ends a verb already moving it. No peer can ask a robot for `stop` itself, only for a pause, which
  ends on its own, a courtesy costs the pilot no step, a run takes courtesies only while its pilot
  has the body, and an arm in a person's hands is never paused.
  `passport owner --no-stranger-pauses` and `--courtesy` set what robots met may ask
  ([docs/passport/talk.md](docs/passport/talk.md#what-quackd-does-with-an-ask),
  [docs/concepts/safety.md](docs/concepts/safety.md)).
- **Keys and pins on disk.** A robot's key, and what its owner said about stopping it, sit in one
  small file per robot under `~/.quackd/keys/robots/`, the owner and the owner key in
  `~/.quackd/keys/owner.json`, and the robots and owners a person pinned in
  `~/.quackd/known_robots.json`, all moved by `--registry-dir` with the registry. `robots.json`
  gains nothing, so an older quackd still reads it.

### Fixed

- **`stop` is never refused for want of budget.** A run or an MCP session whose `max_steps` or
  `max_minutes` were spent had its brake refused with them, though no other gate refuses `stop`
  and quackd tells every pilot it is always allowed. It is still charged a step while one is
  left.

### Documentation

- **[docs/passport/](docs/passport/README.md)**, eighteen pages on every part of Robot Passport: why
  it exists and what it is not, one meeting step by step, a quickstart for any robot and for one
  quackd drives, every member of the document, discovery and the fetch rules, keys, stamps, pins and
  the four tiers, talk and the courtesy handler, a threat model, privacy, compatibility from a
  sticker to a talking robot, quackd's own commands with what they really print, the normative
  specification, naming, prior art, a glossary, the questions people ask, and a roadmap that says
  part by part what is built and what has run. The README gains a section on every aspect, and
  docs/README.md lists all eighteen pages.
- **SECURITY.md** describes the passport server, `meet`'s fetches and talk as surfaces, and what
  would be a security issue in each. docs/concepts/safety.md names the `peer` and `paused`
  gates, docs/concepts/architecture.md the new modules, events and gates, docs/guides/mcp.md
  `robot_nearby`, and docs/guides/lan.md the second service type.

### Known limitations

- **Nothing of Robot Passport has run between two machines or on a robot.** It ran between separate
  processes on one Windows 11 machine, with two registry directories standing for two hosts:
  passports served and announced, found over real zeroconf, read over HTTP and retained on a real
  amqtt broker, a robot and its owner pinned, all six talk messages over HTTP and through the
  broker, two `nearby-hello` runs that met, and a pause asked of a running robot and granted.
  `meet ros://` has run only against fakes. The roadmap's status table keeps that part by part,
  until somebody runs it on two machines or a robot and writes down what happened
  ([docs/passport/roadmap.md](docs/passport/roadmap.md)).
- **A run and `serve-mcp` talk over HTTP only**, not over a broker, and a run in a flock refuses
  `--talk`. A pilot speaks to a robot met with `tell` alone: asking one for a courtesy, or sharing
  what it saw, from a run is planned.
- **Bluetooth LE waits for beacon ids that rotate**, and maker stamps for makers that hold keys,
  so a passport's maker and model are its own claim, and a reader labels them so. A list of
  revoked keys at an owner's contact URL is planned.
- **A single file server for any Linux robot, an ESP32 library, a ROS 2 node, a page that shows a
  scanned passport to a person, the translators and a passport inside an A2A Agent Card** come
  after 0.23.0, and the pages mark each as planned.

## [0.22.0] — 2026-10-21

0.22.0 adds MOSS, a small tracked rover by Laurent Genoud (Show Robotics) that picks up litter,
as the ninth robot quackd can drive. quackd speaks the rover's own cockpit for its state, its
pose, its battery, a goal for its planner, its explorer and its soft stop, and ships a bridge that
runs on the rover for what the cockpit has no command for: a twist with a deadman, frames that say
when they were taken, the maker's own arm cycle, and the litter mission's own stop. `moss:cockpit`
reaches a real rover, and `moss:mock` runs offline.

Nothing has run on a MOSS rover, and the bridge has never run on one. Its hardware checklist is the
first thing to run once one is in reach, on blocks, as its maker says.

A `dangerous` verb now runs only when a person answers its confirm. `--yes`, a flock's standing
answer, the MCP server and a pipe on stdin all refuse it, and `collect_litter` is the first verb
quackd ships in that class.

### Added

- **MOSS, a ninth robot: `quackd[moss]`.** A small tracked rover that picks up litter, by Laurent
  Genoud (Show Robotics) ([github.com/metrox-eth/moss](https://github.com/metrox-eth/moss)): two
  tracks, a bin on top, and an arm derived from the SO-101 with a parallel gripper that drops what
  it picks into the bin. Its software is `moss_dimos`, a blueprint package for dimOS, read at
  `d288647` with dimOS at `a7d19f7`, and quackd imports neither. `moss:cockpit` speaks MOSS's own
  cockpit over HTTP for the state, the pose, the battery, a goal for its planner, the explorer and
  the soft stop, and `moss:mock` runs offline. The package is `quackd-moss`, imported as
  `quackd_moss`, which makes quackd ten distributions
  ([docs/adapters/moss/README.md](docs/adapters/moss/README.md),
  [ADR-0056](docs/adr/0056-moss-through-its-cockpit-and-a-bridge.md)).
- **A bridge for the rover**, `bridge/moss/quackd_moss_bridge.py`, for what MOSS's cockpit has no
  command for. A twist with a deadman of its own, sent again while it is fresh and then one zero and
  silence, since every message on the pad's channel cancels MOSS's navigation. A frame that says
  when it was taken. The base stack's own navigation, read off the bus, since the cockpit reads only
  the newest dimOS run and moss-arm starts after the base. The maker's own arm cycle, run as his own
  mission runs it, with a cancel that stops the script, cancels the trajectory and sees the arm
  hold, and a script that dies any way but its own has its trajectory cancelled and the arm read
  too. And the litter mission's own stop, the only one it listens to short of ending the whole
  stack, which comes with the end of the mission's own pick script, since the mission's stop leaves
  that running. The bridge runs on the rover with MOSS's own interpreter, imports quackd nowhere,
  binds loopback and takes a token. Each quackd session has an id of its own and keeps itself alive
  from its loop's own heartbeat, so a dead process, a dead link and a wedged loop all end it, and
  three missed keepalives later the bridge halts what that session started and nothing else, the
  whole stop on `moss-litter` only for a session that started the mission, and the next keepalive
  answer says what was halted. quackd's close halts only what its own session started, its own arm
  cycle by its id, so a `quackd doctor` stops nothing. A systemd unit and its page sit beside it
  ([bridge/moss/README.md](bridge/moss/README.md)).
- **MOSS's own verbs.** `drive_to`, a goal at most 2 m away for MOSS's own planner. `explore`, its
  explorer for up to eight minutes. `pick_and_drop` and `stow_arm`, the maker's fixed arm cycle,
  which reaches for nothing. And `collect_litter`, the rover handed to MOSS's own litter mission
  for up to eight minutes, which is `dangerous`. Every twist is shaped on the rover: an arc never
  reverses its inner track, and a turn on the spot goes out as the maker's own pulses, because the
  base sends every turn on the spot at full power whatever was asked.
- **`moss-lookout`**, the task to point at a real rover first, with nothing in its allowlist that
  moves a track or the arm, and **what MOSS's cockpit would need to answer to retire the bridge**,
  six things, written on its page for its maker.

### Changed

- **A `dangerous` verb runs only when a person answered its confirm.** Under `--yes`, a flock's
  standing answer, the MCP server or a pipe on stdin nobody is asked, so a `dangerous` verb is
  refused there before its question is put, with a `confirm` gate whose reason says to run it from
  a terminal without `--yes`. A person who answers y still runs it, and one who answers n still
  stops it. No verb quackd shipped before `collect_litter` was `dangerous`, so none of quackd's
  own refuses now where it ran before. A `dangerous` verb in anyone else's adapter is refused
  now where nobody is asked, though it ran there before
  ([docs/concepts/safety.md](docs/concepts/safety.md)).

### Known limitations

- **Nothing has run on a MOSS rover**, and quackd's bridge has never run on one. The client is
  exercised against a fake of the cockpit and against the bridge in its fake mode over loopback,
  and by hand against MOSS's own cockpit in its demo mode, on Windows and in a Linux container,
  where quackd read the cockpit's own state document and refused the demo at connect by name.
- **How far one pulse of a turn on the spot turns the rover is unmeasured**, so `search_scan`'s
  angles are the ones it asked for. Step 6 of the hardware checklist measures it.
- **A nose frame carries `frame_taken_at` only when its own stamp passes the bridge's check**
  against the bridge's own receive time, because what clock the RealSense stamps on is
  librealsense's to say and outside both pins.
- **`collect_litter` runs once per stack start.** Every stop quackd sends on `moss-litter`, the one
  at the end of every run among them, ends the litter mission, and it runs again only after the
  owner restarts the stack. A session that started nothing sends none when it closes, so a
  `quackd doctor` leaves the mission running.

## [0.21.0] — 2026-10-20

0.21.0 adds Huebot, a 25 cm biped from Leif Rydenfalk, as the eighth robot quackd can drive. Its
own API is closed, so quackd speaks to a shim its maker is writing, which is to answer the calls a
Microduck's `robotd` answers, the same way. `huebot:jsonrpc` is the Microduck's transport, copied
and renamed so that the two can diverge, and it needs `--address`, a forward to that shim, while
`huebot:mock` runs offline. Its speed caps are 0.15 m/s forward, 0.1 m/s sideways and 0.75 rad/s
of turn, and it has no `gaze`, no `watch` and no `grab` until its maker says that `robot.look`
works on it and what `ground_pick` does there. Its page says what the shim must answer, call by
call, written to be sent to its maker.

Nothing has run on a Huebot, and nobody has seen its shim answer. Its hardware checklist is the
first thing to run once it does, and until a Huebot has passed the checklist's deadman step,
`huebot:jsonrpc` claims no deadman.

A Microduck turns at 1.0 rad/s at most now, the turn upstream's walking policy was trained on, and
`stand_up` waits up to ten seconds for the duck to stand rather than reading it once at three. A
run's `search_scan` counts the turns robotd took, which 0.18.0 said it did and only the tests ever
saw, and a registered robot this quackd cannot build no longer makes every `quackd robot` command
refuse the whole registry.

### Added

- **Huebot, an eighth robot: `quackd[huebot]`.** A 25 cm biped from Leif Rydenfalk
  ([ce-net.com/huebot](https://ce-net.com/huebot)), with behaviour code of its own (needs, an LED
  face, coming to your hand), which does not run the Microduck runtime. Its own API is closed, and
  its maker is writing a shim for the robot that is to answer the same calls the Microduck's
  `robotd` answers, the same way, as written on 2026-10-15, so `huebot:jsonrpc` is the Microduck's
  transport, copied and renamed rather than imported so that the two can diverge: the same protocol
  and version gate, the same check that the robot took each twist, the same posture allowlist and
  hold on a walk once the robot stops standing, and the same reliance on the robot's own deadman.
  `huebot:mock` is the core's scripted body, and there is no simulator. The package is
  `quackd-huebot`, imported as `quackd_huebot`, which makes quackd nine distributions. Nothing has
  run it against a Huebot or against its shim, which nobody has seen
  ([docs/adapters/huebot/README.md](docs/adapters/huebot/README.md)).
- **What Huebot's shim must answer**, call by call, is the centre of its page, written for its
  maker: `robot.subscribe`, without which the connect fails as not robotd, beside the calls its
  maker named, `robot.do`, `robot.sound` and `robot.enable` for the verbs, a boolean `accepted` in
  their answers, the fields of `robot.state` quackd reads, robotd's deadman, and a socket on the
  robot reached through ssh and never an open port, since the protocol has no authentication. Since
  Huebot runs behaviour code of its own, the shim is asked to report a `policy` other than `walk` or
  `stand` while anything but quackd drives the legs, and to stop it on `robot.stop`, and the page
  carries two questions for the maker on how that code is suspended and shows. Its
  [hardware checklist](docs/adapters/huebot/hardware-checklist.md) switches Huebot's own
  behaviours off and sees that they are, tests the deadman, with quackd frozen and with the tunnel
  pulled, and `robot.stop` with the feet off the ground before anything walks, lifts the robot off
  its stand to see a walk refused, runs `stand_up` and each listed skill on the stand, and sends
  the first `robot.enable` to the robot on its back on a mat, before the feet go down. Until a
  Huebot has passed the deadman's step, the description of `huebot:jsonrpc` claims no deadman.
- **Where Huebot differs from the Microduck.** `huebot:jsonrpc` has no default address, and a run
  without `--address` is refused as the robot is built, before anything is opened or a run directory
  written, naming `ssh -L 9870:<socket> <huebot>` and `--address tcp://127.0.0.1:9870`. The shim
  does not answer `robot.look` yet, so there is no `gaze` and no `watch`, a look is refused inside
  quackd before anything is written, and `go_to` and `search_scan` wait for no glance to fade.
  `sit`, `stand` and `kick` are offered only when the shim's `robot.subscribe` answer lists every
  skill each one sends, and quackd sends no `robot.do` for any other skill. `grab` is offered on no
  Huebot until its maker says what `ground_pick` does there and how the state reports that the robot
  holds something. The camera is `--camera-url` alone, any HTTP still, and without a frame at
  connect the four verbs that need one are not offered. A `multipart/*` stream, MJPEG among them, is
  refused by its type, a redirect is refused naming where it points, and one fetch reads at most
  8 MB, within 3 s of its request. `stand_up` reads the robot every 0.25 s after `robot.enable`,
  sending nothing more, calls it upright as soon as it stands, stops waiting on a robot somebody
  picks up, and says it may need a hand only after 10 s. The speed caps are quackd's own clamp,
  0.15 m/s forward, 0.1 m/s sideways and 0.75 rad/s turning, and `search_scan` counts the turns it
  made by the ones the shim's state showed.
- **Huebot's datasheet:** 0.25 m tall with 15 actuated joints, Feetech HD-1910 bus servos, both
  official from its maker's page as read on 2026-10-20, its mass not published, no arms on that
  page, and notes that its maker wrote on 2026-10-15 that the walk's balance is still being tuned
  and that its head turns on four servos, and that `quack` and `say` send `robot.sound`, which the
  maker has not named among the shim's calls.
- **Its upstream refs.** robotd's protocol stays VERIFIED at the Microduck's pin, `d818af5`, and
  every claim about Huebot's side is UNVERIFIED and sourced to its maker: that the shim answers the
  calls quackd makes, that it has no `robot.look` yet, the joint order, the deadman, the skills,
  that its own behaviour code is off while the shim is driven, and what `robot.enable` does for a
  Huebot on the floor. A test holds the protocol refs to the Microduck's for as long as the two read
  one pin.

### Changed

- **The Microduck turns at 1.0 rad/s at most, down from 1.5.** The cap is quackd's own, the
  manifest's `limits` that every verb that walks is clamped to, since robotd clamps no twist.
  1.0 rad/s is the turn upstream's walking task was trained on and, from `daemon-v0.16.2`, what a
  gamepad's full stick asks for, "what that policy follows well on the robot", so a `move` that
  asked for more was asking for a turn the policy was not trained to follow. quackd's own scans
  and `go_to`'s steering already turned at 1.0 at most. The datasheet's clamp line, which the pilot
  and an MCP client read, now says `1 rad/s turning`. The 3D simulator keeps its own ceiling of
  1.5, since the clamp bounds every twist it is handed.
- **`stand_up` waits up to ten seconds for the duck to stand, rather than reading it once at
  three.** It asks the policy to run as before, then reads the duck every quarter second and sends
  nothing more. A duck that reads standing is called upright at once, and the wait ends there on a
  duck somebody picks up or nothing reports on. It used to read the duck once, three seconds after
  the request, so a duck the policy got up a moment later had already been told it may need a hand.
  Only a duck that has not stood when the ten seconds are over is judged as before: one robotd's
  own answer to a fall still has is told to run the verb again in a moment, and one still down
  that it may need a hand, now with how long it was given. The verb's 15 s timeout is unchanged,
  and how long the policy takes to get a real duck up is still unmeasured.
  `tests/test_jsonrpc.py` stands a duck up six seconds in and lifts one off the floor one second
  in, on a clock of the test's own.

### Fixed

- **A registered robot this quackd cannot build no longer locks every `quackd robot` command.** A
  robot on a backend its adapter has dropped, as `microduck:websocket` was in 0.19.0, or on an
  adapter not installed here made `robots.json` refuse every command that read it, until its entry
  was deleted by hand, as 0.19.0's notes said: `robot list`, `show`, `edit` and `remove`, and runs
  of every other robot in the file. Its spec is now kept as written. `robot list` marks it and says
  why, `--json` carries the reason as `spec_problem`, and `show`, `edit` and `remove` work on it.
  A run, a flock, `doctor`, `serve-mcp` and `robot list --probe` refuse that one robot by name, as
  `kitchen is registered as microduck:websocket, which this quackd cannot build`, the adapter's own
  words, and then `quackd robot edit kitchen --spec microduck:<backend>, or quackd robot remove
  kitchen`, with installing the package that provides it first for an adapter that is missing.
  `robot add` and `robot edit --spec` still refuse a spec that does not parse.
  `tests/test_robot_registry.py` and `tests/test_cli_robot.py` cover both kinds, and the second
  fails on 0.20.0 at its first `robot list`.
- **A Microduck's `search_scan` counts the turns robotd took in a run, as 0.18.0 said it did.**
  0.18.0 has the scan count the turns it made by the ones robotd's state showed it took, read off
  what the verb is handed, which in a run is the Microduck's adapter. The adapter never passed that
  on from the `jsonrpc` transport, so a run's scan counted the turns it sent: over a slow link, a
  scan stopped on a robotd that took none of them said it stopped `after 1 of 10 turns`, a turn
  robotd never took, where it now says the robot's own state did not confirm the turn sent to it.
  The tests handed the verb the transport itself, which is why they passed. The slow-link tests in
  `tests/test_jsonrpc.py` hand it the adapter now, as a run does, and the scan's test fails on the
  two slower links without the fix.

## [0.20.0] — 2026-10-20

0.20.0 lets the decision LLM see, and gives the Microduck a head that keeps something in view. With
`--decision-frames` the stepper shows its decision LLM the robot's primary camera beside the state,
one JPEG of at most 512 pixels a turn, and only `--decision-llm openai` at OpenAI's own hosts may be
shown one: any other decision LLM or address stops the run before the robot connects. Each frame's
copy is written into the run directory, and a line naming it is flushed to the transcript, before
the request that carries it is handed over.

On a Microduck the same flag gives the pilot `watch(target, duration_s)`, the reflex OpenAI's launch
video for its Decisions API shows on a Microduck. It asks where a described thing is in each frame
and turns the head toward it by quackd's own law, at most 15° of yaw a look and never past 45°, only
while the duck reads as standing or sitting, and on a real duck only through `mediad`'s `/frame`.
Every way a call ends is named, and none sends the head anywhere.

Nothing in this release has run against OpenAI or on a real duck. What a frame costs on
`gpt-6-luna`, how much slower it makes a turn and how well a model places a thing are unmeasured,
and the numbers the head is held to are starting values.

### Added

- **A decision LLM that can see: `--decision-frames`.** `--decision-frames`, or
  `QUACKD_DECISION_FRAMES=1`, shows the decision LLM the robot's primary camera beside the state on
  each turn the stepper asks it about: the picture that turn's observation read, as one JPEG no
  longer than 512 pixels on its longest side, at quality 85 and detail `low`, sent as an
  `input_image` part after the state's text and never inside it. The state gains one sentence,
  `picture`, saying an image went beside it. It is off unless a run says so, and Rok's rule decides
  the rest: only `openai` may be shown a frame, its row's `sees` now true and every other row's and
  every plugin's false, only at OpenAI's own hosts, read by the same rule that decides where its key
  goes, and the record keeps what was sent. A run that asks for frames with no decision LLM named,
  with any other row, or with a `--decision-url` that is not one of those hosts, a server of your
  own on this machine included, stops before the robot connects, and so does a word the variable
  does not take. One gate, `frames.may_send_frames`, stands in front of every frame:
  `Stepper.build` refuses frames for any other decision LLM, the stepper asks the gate again before
  each frame goes, and the client refuses a frame it was not built for, or more than the 128 one
  request may carry, before anything is sent. Before each request that carries a frame, the JPEG is
  written beside the step's own PNG as `frames/NNNN-decision.jpg`, and a `decision_frame` line
  naming it, with its camera, size, bytes, detail, sha256, path and whether the disk kept it, and
  never its bytes, is written to the transcript and flushed, so the record names the frame even when
  the process dies while the request is out. The turn's `decision` record names it again, so a turn
  that failed, ran out of its second or was interrupted still says its frame was handed over, which
  a connection refused or timed out first never carried. A disk that refuses the copy leaves none of
  it behind, and the record says `kept: false`. A turn asked without one says why, as
  `frame_skipped`. `run_start.decision_llm` gains `frames`, the summary's `decision` block gains
  `frames` and `frames_sent`, and `quackd log` says `1 frame` on each turn that sent one. Whether a
  frame may go is decided apart from `--decision-mode`, so a run that asks for frames builds its
  decision LLM whatever the mode, and with the mode off one line says no frame will go; apart from
  `--vision` too, which is the pilot's. `--decision-llm off` turns it off, and `quackd record` pins
  it off. A turn with a frame keeps the stepper's second. `tests/test_decision_frames.py` holds the
  encoding, the gate, every refusal, the copy written before the request, and a run cut off while a
  frame was out, and the wire tests send frames through openai 3.22.1 to the stand-in on 127.0.0.1,
  treated as OpenAI's, which checks each image part against the reference's shape.
  `tests/test_live_decision.py` gains a measurement of what a frame adds to a turn's time and to the
  tokens it bills, which needs a key and has not run. What a frame costs on `gpt-6-luna`, and how
  much slower it makes a turn, are unmeasured, and no frame from quackd has reached OpenAI
  ([docs/guides/decision-llms/openai.md](docs/guides/decision-llms/openai.md),
  [ADR-0053](docs/adr/0053-a-decision-llm-may-be-shown-the-camera.md)).
- **A head that keeps something in view: `watch` on the Microduck.** `watch(target, duration_s=15)`
  keeps a thing the pilot describes in view with the head alone, for 2 to 30 seconds, as OpenAI's
  launch video for its Decisions API has a Microduck do it. Each look sends one frame to the one
  decision LLM `--decision-frames` may show the camera, `openai` at OpenAI's own hosts, with a
  `choice` question, where across the picture the thing is, and on a head that tilts a second, how
  high, and quackd turns the head by a law of its own on the answer: the angle through the lens
  times 0.6, nothing within 0.08 of the middle, at most 15° of yaw and 8° of pitch a look, and never
  past 45° either way, 14° up or 11.5° down. The answer is read by its distribution and never by
  OpenAI's `confidence`, which the record keeps. One request is out at a time, looks begin at least
  0.5 s apart on the robot's clock and requests go at least 0.5 s apart on the wall's, 60 a call at
  most, and each answer is bounded at 1.5 s. The head is claimed and given robotd's glance fade and
  a settle before the first frame, sent its target again at least every second, and claimed again
  after any gap robotd could glance in, and the duck is read before every command the head is sent,
  which goes only to a duck that reads as standing or sitting, the allowlist quackd's own gaze hold
  keeps. A frame is used only when it was taken after the head settled, and never twice, which on a
  real duck takes mediad's `/frame`: its WebRTC track carries no time a frame was taken, so a duck
  on it has `watch` withdrawn. Every way a call ends is named as its `ended`, and none sends the
  head anywhere: `duration`, `lost` after 4 looks, `edge` after 3, `errors` after 3, `refused`,
  `camera`, `fallen`, `blind`, `held`, `look_refused`, `stopped`, `budget`, `error` for something
  raised, and `abort`, `timeout` and `cancel`. It is offered only on a run with
  `--decision-llm openai --decision-frames`, whatever `--decision-mode` says, on a duck whose camera
  worked at connect. Every other run, `serve-mcp` and a flock have it withdrawn when the duck
  connects, by a new `VerbRegistry.remove` that never removes `stop`, and a task that requires it is
  refused before the robot connects where the run has nothing to look through, and once it has,
  before anything moves, where the camera cannot be used. It runs before a verdict, as `gaze` does,
  and a dry run sends no frame. Before each request the JPEG is written as `sent/watch-NNNN.jpg` and
  a `watch_frame` line naming it is written through to the transcript, a disk that refuses the copy
  leaves no partial file and records `kept: false`, and each look leaves a `watch_tick` row however
  it ended. `run_start.watch` says what the head was held to and through which lens, the summary's
  `decision` block gains `watch`, whose bill is folded into that block's totals, `quackd log` prints
  each look, and the counters at a run's end give `watch`'s seconds a split of their own. A `stop`
  asked for from beside a running verb now reaches a verb that asks, through
  `VerbContext.interrupted`, which carries the run's time budget too. A new starter duck,
  `microduck-watch`, requires `watch`. In the suite the head closes on the ball on
  `microduck:sim2d`, and in MuJoCo turning and tilting, through quackd's own client on a fake of its
  SDK that finds the ball in each JPEG it is handed. Nothing of it has run on a duck or against
  OpenAI ([microduck](docs/adapters/microduck/README.md#watch-a-head-that-keeps-something-in-view),
  [ADR-0054](docs/adr/0054-a-reflex-may-look-through-a-decision-llm.md)).

## [0.19.1] — 2026-10-19

0.19.1 fixes four small things 0.19.0 left. With `--no-memory` the MCP memory tools no longer tell a
client to call them, a ToddlerBot with no walk checkpoint is no longer told how fast it may walk,
and a `.duck` quackd cannot read, a UTF-16 one from Windows PowerShell 5.1 above all, is refused in
a sentence by every command that reads it, `quackd serve-mcp --duckfile` included, rather than in a
traceback. No verb sends a robot anything it did not send before.

### Fixed

- **With `--no-memory`, `quackd serve-mcp` no longer tells its client to call `robot_recall`.** Both
  memory tools stay registered under `--no-memory` and answer that memory is off, and the server's
  instructions already said nothing about them, but `robot_recall`'s own description still said to
  call it before planning, and a client reads a tool's description as an instruction like any other.
  With memory off both descriptions say that memory is off for the server and that there is no need
  to call them, and with memory on they read as they did. `tests/test_mcp_memory.py` reads both
  descriptions from a server started each way, and the one with memory off failed before this
  change.
- **A ToddlerBot with no walk checkpoint is no longer told how fast it may walk.** Until its daemon
  reports a walk checkpoint that can move it, `toddlerbot:bridge` offers no verb that moves it and
  is told it does not move, yet its manifest keeps the walk's speed caps, and its prompt said
  `quackd clamps you to 0.2 m/s forward, 0.1 m/s sideways, 1 rad/s turning` two lines under that. A
  speed cap is a rule about a verb that moves the body, so only a body whose mobility is not `none`
  is told its caps now. The clamp itself is unchanged, and so is every other line: rendering every
  shipped body with every starter before and after the change changed the bridge's prompts alone, by
  that one line. `tests/test_datasheets.py` checks the bridge before its daemon answers and as built
  for a daemon with no checkpoint, a bridge that can walk, which is still told its caps, and that
  among the shipped bodies one is told its caps exactly when it moves. It failed before this change.
- **A `.duck` saved as UTF-16 is refused in a sentence rather than a traceback.** Windows PowerShell
  5.1 saves UTF-16 with `>` and `Out-File`, and reading such a file as UTF-8 raised an error that
  `quackd run`, `quackd validate`, `quackd preflight` and the MCP tool `robot_load_duckfile` let out
  as a traceback. The one place quackd opens a `.duck` refuses it now as a file it cannot read,
  which each of them already says in a sentence:
  `saved as UTF-16, which quackd does not read: save it as UTF-8`, and how to in PowerShell. A file
  in any other encoding that is not UTF-8 is told so, with the byte where reading it failed, and the
  `.duck` reference says both. `tests/test_duckfile.py` reads a starter saved as UTF-16 through the
  loader, `quackd validate` and what `robot_load_duckfile` calls, and a Latin-1 file through the
  loader. It failed before this change.
- **`quackd serve-mcp --duckfile` refuses a task file it cannot read in a sentence.** It loaded the
  file with nothing around it, so a file it refused, a missing one or one with no frontmatter among
  them, ended the command in a traceback with the reason at its foot. It is refused now as
  `quackd run` refuses it, before anything connects. `tests/test_cli.py` starts it on a file with no
  frontmatter and on one that does not exist, and failed before this change.

## [0.19.0] — 2026-10-19

0.19.0 lets a task give its pilot a part to play, adds OpenAI's Decisions API as a decision LLM, and
removes `microduck:websocket`, as 0.18.0 said it would. `play: true` in a `duck: 4` task file, or
`--play`, adds one section to the pilot's prompt: the pilot acts a part out with the body it has,
the verdict asks whether this body can act it out rather than whether it is the thing it plays, a
real result the task asks for still has to happen, and no part brings the body into contact with a
person or within reach of one. With play off quackd adds nothing to the prompt. 118 of the SO-101's
223 example files now play, and no played run has reached the arm yet.

`--decision-llm openai` asks OpenAI's `POST /v1/decisions` the stepper's four questions and reads
each typed answer back by its name, billed at the published $0.10 per million input tokens.
`OPENAI_API_KEY` goes to OpenAI's own API alone, and the SDK's own variables go where they go for
the pilot. A question the API declines hands the turn to the model on a gate of its own, `refused`.
Nothing has answered a real robot through it, and no request has reached OpenAI.

An OpenAI refusal now reaches the record in its own words, six fixes to the stepper and its decision
LLMs come with the new row, and a command that names `microduck:websocket` fails at `--robot`, where
`--robot microduck:jsonrpc` is what to use.

### Added

- **A task can give the pilot a part to play.** `play: true` in a `duck: 4` task file, or `--play`
  on `quackd run`, `record` and `preflight`, adds one section to the system prompt, `## Playing a
  part`, directly before the task file. It tells the pilot that a part is acted out with the body
  it has, that the verdict asks whether this body can act it out rather than whether it is the
  thing it plays, and that the legs, wings or second arm of what it plays stay out of `needs`. It
  also tells the pilot that a real result the task asks for still has to happen and is judged as it
  would be without the part, that no part brings the body into contact with a person or within
  reach of one, that every pose made with `move_joints` stops short of the surface the body stands
  on and of anything else there unless the task asks it to touch that, that every move is small and
  slow while anyone is in reach or no camera picture shows the body and where a person near it
  would stand, and that its verdict reason says plainly what the body will do and every other
  reason what it actually did. No gate reads any of it. `--no-play` turns a file's `play` off for
  one run, the flag wins over the file, and no variable sets either. The tools, the observations
  and every gate are what they were, and with play off quackd adds nothing to the prompt: rendering
  every shipped body with every starter, and the arm with every example file that does not play,
  before and after the change gave the same bytes. An example file that now plays reads its new
  lines with `--no-play` as well. Each member of a pilot flock plays. A coordinator flock refuses
  either flag and a played file, since none of its members is a pilot, and `--controller vla`
  refuses `--play`, and a played file unless `--no-play` turns its part off, since its policy reads
  no prompt. Over MCP, a played file's `robot_load_duckfile` result carries the section's
  paragraphs, worded for a chat, as `play`. A run's header, and the one `quackd log` replays, gains
  a `play` row when play is on or a file's play was turned off, a pilot flock member's `run` line
  in the log says `play` after its robot, and the record gains `run_start.play`, whether the pilot
  was told it may play and whose word that was, beside `contract.play`, the file's own, and a pilot
  flock's `flock_start` says the same. `tests/test_play.py` holds the schema's rules, a played
  prompt as the plain one plus one section on every shipped body, the tools and the gate's words
  unchanged played or not, the record of a run, a pilot flock and a rehearsal, MCP and the
  coordinator flock's refusals, with the vla ones in `tests/test_controller_vla.py`. Four opt-in
  live runs ask a real model to end a played task whose real result the body cannot make as
  infeasible, a laundry basket on the Microduck's simulator asked for plainly and as a pack mule's,
  and to move for a played part, a cat there and a T-rex on `lerobot:mock`. The first three are in
  `tests/test_llm_in_the_simulator.py` and need MuJoCo, the last is in `tests/test_play.py`, all of
  them need `QUACKD_LIVE_LLM=1` and an OpenAI key, and none of them has run yet
  ([docs/guides/play.md](docs/guides/play.md),
  [ADR-0055](docs/adr/0055-a-pilot-can-play-a-part.md)).
- **OpenAI's Decisions API as a decision LLM: `--decision-llm openai`.** OpenAI opened
  `POST /v1/decisions` to every developer on 2026-10-06, with `gpt-6-luna` its only model, and it
  asks the kind of question a decision LLM answers in a wire format of its own: the questions go as
  a list, a yes or no question is a `predicate`, and the answers come back as a list that names each
  question. So it is a second backend rather than a row on the System One client.
  `quackd/agent/decision/decisions_api.py` translates the four questions every decision LLM is asked
  out and the answers back, strictly by the name each answer carries and never by its position, and
  by its type: an answer of a type its question was not asked as, a choice with no choice in it, or
  two answers under one name answer nothing, which for a question the gates read hands the turn to
  the model on `unreadable`, and a refusal among them declines the question. The state goes as one
  JSON object with its letters as letters, because OpenAI's `input` is text its model reads as
  written, and the `state_chars` every row records now counts those letters rather than JSON's
  six-character escape for each one outside ASCII. It reaches the API through the `openai` SDK's
  generic request rather than its `decisions` resource, which arrived in 3.26.0, so the 3.22.1 the
  lock carries serves it and no floor moves, and it installs with `quackd[openai]`, the pilot's own
  extra, which `quackd[all]` already carries.
  `OPENAI_API_KEY` is sent to OpenAI's own API alone, `https://api.openai.com` or one of the
  regional hosts OpenAI's data controls page lists for it, `us.`, `eu.`, `au.`, `ca.`, `jp.`, `in.`,
  `sg.`, `kr.`, `gb.` and `ae.` in front of `api.openai.com`, over https, with nothing else in the
  address's authority but the port https uses anyway: any other `--decision-url` is a server of
  your own that answers the same format, sent the word `local` and costed at the self-hosted rate
  unless `QUACKD_DECISION_PRICE` says otherwise. `OPENAI_BASE_URL` never moves the address, and what
  else the SDK reads for itself goes where it goes for the pilot, by the rule
  `providers/openai_headers.py` keeps for every client quackd builds on the SDK:
  `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` go to OpenAI's own API exactly as
  the SDK sends them, and to a server of your own none of them, an authorization line of the
  variable in any spelling carrying the word instead. That rests on the eleven releases of the SDK
  read from the extra's floor to the locked one, and the built client is read back for the rest: one
  that would still send any of them there stops the run and is named. The client follows no
  redirect, which would carry the request somewhere else. A `--decision-url` with a path, a query or
  a fragment stops the run before anything connects, on every machine, with or without the SDK and
  the key, and a run at a server of your own needs no key. The client never retries, and its own
  clock sits above the stepper's second, so that second is what ends a turn and one that runs out of
  it is recorded one way, as `TimeoutError: no answer within 1.0 s`; `decide(timeout_s=...)` bounds
  a whole request for a caller with no stepper around it. The SDK asks which platform it is on
  before its first request, which on a cold Windows machine means a WMI query or a subprocess, so
  that is asked while the client is built and no turn waits for it. A turn is billed at the
  published $0.10 per million input tokens, output not charged, on the count OpenAI reports, and a
  turn whose request went out and then failed on quackd's own estimate, which wears a `~`. A
  question the API declines is no answer at all, so the stepper reads that before any answer and
  hands the turn to the model on a gate of its own, `refused`, whichever of the four was declined,
  `feasible` included. The turn's record names the questions declined, the run's `decision` summary
  counts `refusals`, and `quackd log` prints the names in yellow. Every stepper turn that sends a
  request now records `idle_s` as well, the seconds since the stepper's previous request ended.
  OpenAI's SDK and TypeSafe's both drop a connection idle for five seconds, and in shadow mode the
  model's own call and a verb usually take longer than that between two of the stepper's requests,
  so `idle_s` tells a turn that paid for opening a new connection from one that reused one. It is
  null on the first turn, and on the turn after a request that failed, which may have taken its
  connection with it or never had one, so no number there claims a connection that was kept warm.
  The catalogue gains a `sees` field, false on every row, `openai` included, because quackd sends a
  decision LLM text and nothing else. The row has
  [a page of its own](docs/guides/decision-llms/openai.md), SECURITY.md says where each decision
  LLM's key may go, and the decision-LLM hub, the README, `.env.example`, CONTRIBUTING, the
  architecture page, the SO-101's first run, the local-LLM guide and PLAN.md name it where they name
  the others. The hub works its rate into the cost of one decision, about a thousandth of the model
  call it replaces where Jev's is about a two-thousandth, and gives it no latency, because OpenAI
  publish only a ratio to their own Responses API. The warning `--decision-mode on` prints says the
  same: the hub's figures are estimates of speed and cost for `jev` at TypeSafe, of cost alone for
  `openai` at OpenAI, and of neither for a server you run, where it used to say Jev and nothing at
  all for anything else.
  [ADR-0052](docs/adr/0052-openais-decisions-api-is-a-second-wire-format.md) records why it is built
  in rather than a plugin, amending ADR-0043's one wire format and its one published rate, and
  ADR-0041's note that an estimated cost and its `~` came only with a rate, Jev's alone, and
  extending ADR-0040 with the new gate. `tests/test_decisions_api.py` drives the translation, the
  key, the headers and the address through a fake of the SDK, `tests/fake_decisions_api.py`, and
  `tests/test_decision.py` drives the new gate and `idle_s` through the stepper. Wherever the SDK is
  installed, `tests/test_decisions_api_wire.py` drives a real `quackd run` through the real one
  against `tests/fake_openai_decisions.py`, a stand-in for the API on 127.0.0.1, and shows on the
  wire that the key goes only where it is meant to, that at a stand-in taken for OpenAI's own API
  the SDK's own variables arrive exactly as they arrive from a client the SDK built by itself, that
  at a server of your own nothing `OPENAI_CUSTOM_HEADERS` or `OPENAI_BASE_URL` says gets in, in
  whatever spelling, that a redirect is never followed, and that a 429 is one request, recorded as
  the rate limit it is, where the SDK's own retries would have slept through the turn's second and
  recorded a timeout. The wire tests have run against 3.22.1, the release the lock carries, against
  3.1.0 and against 1.66.0, the extra's floor, and against no other release.
  `tests/test_live_decision.py` runs against `openai` as well as `jev` and waits for a key, and a
  test there fails, saying why, when the answer it checks never came back, because the request
  failed or the question was declined. Any test without the `live_decision` or `live_llm` marker
  that sends a request through the real SDK to a host under openai.com, the pilot's or the
  stepper's, is stopped before it is sent, and fails, and a process such a test starts inherits an
  empty `OPENAI_API_KEY`. The honest part: nothing has answered a real robot and no request has
  reached OpenAI, OpenAI define no `confidence` and claim no calibration, and the floors it is gated
  on were shaped around Jev, so shadow mode comes first.

### Changed

- **A stepper turn that failed records the exception's type and HTTP status, never its message.**
  The turn is still `gate: error` and still the model's, and its `error` used to carry the
  exception's message whole. An SDK puts the server's reply there, TypeSafe's the reply's own
  message and OpenAI's the body of a 4xx or a 5xx, and a reply can quote the request it turned down,
  which is the state the record otherwise keeps as a size and a shape. So `error` now holds the type
  and the status, `RateLimitError: HTTP 429` for a rate limit at OpenAI, or the type alone where
  there is no status, and so does the line `quackd log` prints. Two sentences are quackd's own and
  are kept. A turn that ran out of the stepper's own second still says
  `TimeoutError: no answer within 1.0 s`, and only such a turn: a `TimeoutError` with nothing in
  it that a backend raised on a shorter clock of its own used to be given the same sentence, beside
  a `latency_s` that said otherwise, and is recorded by its type now. TypeSafe's client keeps its
  own clock above that second too, as OpenAI's does, so on every row the second is the one clock
  that ends a turn. And a reply quackd could not read is recorded in quackd's own words, which name
  only the type of what came back, so a page a proxy answers with is
  `UnreadableReply: POST /v1/decisions answered str rather than a JSON object`. Laya's
  `ValueError` for options that overflow `head_max_len` is recorded by its type alone, so
  [its page](docs/guides/decision-llms/laya.md) now asks for the turn's `labels` beside it.
  `tests/test_decision.py` and `tests/test_decisions_api.py` fail when a reply that quotes the
  state, or carries a data URL, reaches the record, when a backend's own `TimeoutError` is given
  the stepper's sentence and when quackd's words about a reply are lost, and
  `tests/test_decision_llms.py` fails when TypeSafe's clock sits on the stepper's second.

### Removed

- **`microduck:websocket`, which 0.18.0 deprecated.** It was a stub that refused to connect while
  it waited for the WebSocket agent gateway upstream's architecture.md designed, and by
  daemon-v0.16.1 upstream had dropped that design, so there was nothing for it to become. A
  command that names it fails at `--robot` now, before any body is built, with
  `unknown backend 'websocket' for microduck; choose one of sim2d, mujoco, mock, jsonrpc`, and
  what to use instead is what 0.18.0's line named: `--robot microduck:jsonrpc`, with
  `--camera-url http://127.0.0.1:8080/frame` for mediad's camera. A robot registered on it leaves
  `robots.json` refusing every command that reads it, `quackd robot remove` among them, with that
  error naming the file and the robot, until its entry is deleted from the file by hand. What
  only the stub used went with it: the Microduck's `DEPRECATED_BACKENDS`, whose one line was the
  stub's, the `WebSocket agent gateway` ref and the link to upstream's roadmap, with their row and
  link on the Microduck's page, and the README's ⏳ stub mark, which no backend carries now. The
  `rendezvous control lane` ref stays, since it says why quackd reaches a duck elsewhere over ssh
  and not over that lane. So does the machinery itself: an adapter may still declare
  `DEPRECATED_BACKENDS`, `quackd.adapters.factory.deprecated_backend` still reads it, and `run`,
  `doctor`, `serve-mcp` and `robot list --probe` still print the line, though no adapter quackd
  publishes declares one now.
  [docs/adapters/writing-an-adapter.md](docs/adapters/writing-an-adapter.md) shows a declaration
  where it named the stub. `tests/test_doctor_and_stub.py` is `tests/test_doctor.py`, without the
  stub's own test, and the tests of the line moved to `tests/test_deprecated_backends.py`, onto an
  adapter the tests install that retires a backend of its own. Its `serve-mcp` case with two
  robots on that backend lets both connect before a third refuses, so it holds the server to
  saying the line once per start. Eleven ways of breaking the machinery, tried one at a time, each
  failed the test that guards it. [ADR-0006](docs/adr/0006-upstream-api-single-file.md) is amended
  in place.

### Fixed

- **An OpenAI refusal reaches the record in its own words.** An OpenAI model says no in a channel of
  its own: a refusal part on the Responses API, with its words under `refusal` and no text, and a
  `refusal` field of the message on Chat Completions. quackd's OpenAI parser read only text, so it
  dropped both, and a turn the model declined arrived as an empty one with no call: the record
  showed a pilot that said nothing. The turn's text now reads `[refusal]` and the refusal's words,
  as an Anthropic or OpenRouter refusal already did, which tells a refusal the model returned in
  that channel from a no it wrote in its answer, and a Responses refusal part with no words adds no
  text but still makes the turn a refusal. A turn that carries only a refusal stops with `refusal`
  where the API gave no reason or only `stop`, or on the Responses API `completed`, and keeps a
  reason it named, such as OpenRouter's `content_filter`. A Responses answer that a filter cut short
  now stops with `content_filter`, where it said `incomplete`, or `tool_calls` beside a call. On
  OpenAI's own APIs a call that arrives beside a refusal is kept, since that call is the turn's
  answer, while OpenRouter keeps its own reading: it drops any call beside a refusal, as Anthropic's
  does, and keeps only the refusal's words, where Anthropic's keeps the turn's text before them. A
  local model's turn that carries a refusal, whatever its stop reason, or that a filter cut short,
  is never read for a call written out in its text, so a call quoted in a refusal does not run,
  while a native call runs as it always has. Every other vendor quackd reads through the Chat
  Completions parser, Grok, DeepSeek, Mistral and the local servers among them, has its `refusal`
  field read the same way where it sends one. `tests/test_providers.py` holds a Responses refusal,
  one beside a call, one cut short and one with no words, a filtered Responses answer with and
  without a call, a native call in a filtered answer on either API, which still runs, a Chat
  Completions refusal alone and beside text and a call, one that keeps `content_filter`, and an
  Anthropic refusal, and `tests/test_local_provider.py` holds a call quoted in a refusal, whatever
  its stop reason, or cut off by a filter, that does not run.
- **`--decision-llm jev` at a `--decision-url` no longer wants `TYPESAFE_API_KEY`, and is costed
  as the server you run that it is.** An address typed for `jev` is a server you run, and quackd
  has always sent it the word `local` rather than the key. The run still asked for the key before
  it built the stepper, though, so a machine without one printed
  `running without it: --decision-llm jev needs TYPESAFE_API_KEY` and ran without a stepper that
  would never have sent the key, and a machine with one costed every turn at TypeSafe's $0.042 per
  million input tokens for a server on the bench. One rule now answers both questions for every
  row, the rule each client already keeps about what it sends (`factory.sends_key`): `jev` wants
  its key and is costed at TypeSafe's rate only with no `--decision-url`, and `openai` only at
  OpenAI's own hosts. `tests/test_decision_llms.py` runs `jev` at an address with no key in the
  environment, and holds the rule to what each client actually sends.
- **What the stepper's gates read is a probability or nothing, on every decision LLM.** The gates
  took any number they could make a float of. A confidence written as text, `"0.99"`, cleared its
  floor, one on a scale of its own, a percentage or a 5, cleared every floor, a `done` and a
  `need_human` of -1 said certainly not, and each of them moved the body; a `next_verb` that
  carried a confidence and no choice was read as a confident `escalate`. A confidence, `done` and
  `need_human` are now each a number from 0 to 1, a choice is a string, and anything else ends the
  turn on `unreadable`, with `null` on the record where the number or the choice was. A NaN or an
  infinity in a distribution or in `feasible` also used to reach `transcript.jsonl` as a bare `NaN`
  or `Infinity`, which Python reads back and `jq` and `JSON.parse` refuse; the distribution leaves
  such a number out and `feasible` records `null`, so every line is JSON a strict reader takes.
  `tests/test_decision_llms.py` drives each of those through the stepper, and
  `tests/test_decisions_api_wire.py` sends a NaN and an infinity through the real SDK and reads
  the events file back strictly.
- **A turn the model takes ends the stepper's run of turns, however it came to be the model's.**
  The stepper never answers twice running with the same call, nor more turns in a row than its
  limit, and a turn the model takes starts both counts again. A turn whose call failed, a timeout
  or a refused connection, went back to the model without doing that, and so did a turn whose
  state was too large to send and one with nothing on offer. So after a `stop` the stepper took
  and a turn that timed out, which the model then took, the same confident `stop` was refused as
  a repeat, and a run of stepper turns broken by a failure reached its limit early. Each of them
  now ends the stepper's run as every other handover does. None of it moved the body, since it
  only ever gave the model more turns, but it skewed the gates shadow mode counts.
  `tests/test_decision.py` has the model take a turn each of those three ways between two
  identical answers, and breaks a run of two with a failed turn.
- **A run interrupted while the stepper's request was out has that request on the record.** A
  second Ctrl-C, or a flock cancelling a member, is not an exception the stepper caught, so the
  request it cut off, already handed to the decision LLM, was missing from the stepper's count,
  its seconds and its bill, the loop wrote no `decision` record for it, and the summary written
  on the way out left it out too. The stepper now counts, times and bills that request as it
  does one that failed, on a gate of its own, `interrupted`, which `quackd log` draws in yellow
  with what cut it off, and not among `errors`, because nothing failed and the run ends there.
  The loop writes the record before the interrupt goes on, as it already did for the model's own
  call. The stepper also waits for its decision LLM in its own task now, because on Python 3.11
  `asyncio.wait_for` ran the request in a task of its own, and a Ctrl-C that landed in there left
  through the event loop at once. `tests/test_decision.py` interrupts a turn both ways, on the
  stepper and in a run on the mock arm, and finds one record, on `interrupted`, and a summary
  that counts it.
- **A shadow row whose stepper chose nothing says why, rather than `None 0.00`.** The `decide=`
  line `quackd log` prints for each shadow row gave the stepper's choice and its confidence, and
  on a turn that chose nothing, a call that failed or a body with nothing on offer, that was
  `None 0.00`, which read as an answer of its own. A `next_verb` nobody answered was recorded,
  and printed, as `escalate`, which is how such a turn is routed and not anything anybody chose,
  so every count of the stepper's choices took it for one. The record now says `null` for the
  choice of a `next_verb` declined, never answered or answered with no label, the line names the
  gate where there is no choice, `error`, `unreadable` or `refused to answer next_verb`, and
  leaves out a confidence nobody could read rather than print it as 0.00, and the shadow row of a
  turn that ended on `refused` carries the names declined, as `refused`. `tests/test_decision.py`
  and `tests/test_log.py` hold each, and the log golden gains the line.
- **A flock's chatter reaches a decision LLM in its own letters.** The stepper wrote what the
  flock's members had said into the state as JSON of its own, with every letter outside ASCII
  escaped, so every decision LLM was handed `\u017e` where a member had written `ž`. It also
  trimmed the state, and held it to its hard cap, on the same escaped text, six characters for
  each such letter, so a state in Slovenian was cut sooner than the same state in ASCII. The
  chatter goes in its own letters now, and the state is trimmed and capped on the letters
  `state_chars` counts. `tests/test_decision_llms.py` finds the flock's letters in the state a
  backend of every row is handed, and measures a goal with a letter outside ASCII the same as the
  goal spelled in ASCII.

### Documentation

- **Playing a part.** A guide, [docs/guides/play.md](docs/guides/play.md), says how to turn it on,
  quotes the section as the SO-101 reads it, and says what a part cannot do: go past the travel, be
  kept from striking itself or its table, speak on the arm, tell a person from something blue,
  reach a decision LLM, or stay out of memory. It warns that nothing keeps a played arm away from a
  person, since the section's care near people is a request no gate reads. The spec reference
  describes `duck: 4` and `play` with a whole example and the `quackd validate` it passes,
  [ADR-0055](docs/adr/0055-a-pilot-can-play-a-part.md) records why one named framing rather than an
  override file or a set of templates, and `docs/concepts/safety.md` says that a part unlocks
  nothing and that its rules are prose no gate reads.
- **The shoot's task files that ask for a part now play it.** 118 of the 223 files in
  `docs/examples/lerobot` are `duck: 4` files with `play: true`, and none of them counts saying why
  the body cannot as a success any more. They share a persona that begins
  `Game for anything: commit to the bit`, and all but `e067` and `e076` share a success line that
  asks for the act and a reason saying what the arm did, and the last step of their strategy. Their
  second step asks for the act in a few clear poses. 27 keep their older second step, which asks for
  small, deliberate moves: those where the shoot's run sheet puts a person, a held prop, a camera or
  a thing within the arm's reach, has someone hold something up in the camera's view, rates the
  experiment as contact or warns that a pose may strike the arm's own base, and `e089/less`, whose
  waves are meant to shrink. The examples README says that step is a request no gate reads, and to
  keep people beyond the arm's reach in these as in any played run. Some tasks say a little more, so
  that they end the way the run sheet means them to: `e035` acts its refusal out, `e059` says its
  sock puppet is on the gripper, the six charades of `e008` keep their word secret, from the verdict
  too, until the pilot declares, `e012` declares before its steps run out, `e060` when the person
  stops, `e067` and `e076` give their lines in the verdict reason, after what the arm will do, and,
  in a success line and a last step of their own, end without a declare, since the run sheet ends
  them on an unplug and on the budget, `e048` says its cube is foam and asks the cat to touch it for
  real, as the sheet allows, `e148/lower-flag` raises its flag and holds it briefly before it lowers
  it, since a run ends folded at rest and the flag cannot stay up between runs, the flag of `e148`
  and the placard of `e076` say they are taped to the wrist, as the sheet tapes them, and
  `e079/touch-grass` names the pot of grass the sheet sets out. The other 105 keep every line. The
  examples README also says what the aim of 90 to 95 percent of the files running as asked on the
  arm comes to: by the run sheet's own expectations 195 of the 223 should, 87 percent, short of the
  aim, and it names the 28 meant to fall short and why, where the sheet expects the arm not to do
  what they ask. A test in `tests/test_play.py` parses every file and holds every played file to the
  lines they share, the examples README's counts of the files that play and keep their lines to the
  files, and its figures for the aim, and the files it names as falling short, each in the sentence
  that says why, to the list the test keeps of them.
- **The decision-LLM hub covers both of OpenRouter's decision endpoints, and no longer says the
  in-process one counts no tokens.** Its section on what is not covered named only OpenRouter's
  alpha Decisions API, at `/api/alpha/decisions`, which the System One client's fixed path cannot
  reach. OpenRouter also documents a System One endpoint, `POST /api/v1/systemone`, for TypeSafe's
  own client, which appends `/v1/systemone` to whatever address it is handed, so the section now
  says what keeps that one out instead: an address given with `--decision-url` is sent the word
  `local`, never a key. The OpenRouter guide, which said whether OpenRouter's decision LLMs can be
  reached from `--decision-llm` "has not been checked", points there now. The hub also said the
  in-process one never reports a count and that "a model running in this process reports no count
  at all", which ADR-0043 had already corrected: `laya` reports the tokens its encoder read, and
  quackd takes that as a measurement. PLAN.md's open item on the stepper pointed at a break-even the
  hub no longer works out, and points at its speed and cost figures now.
- **A System One address keeps its path, as it always has.** The pages for `jev` and `local`, the
  hub's table of flags and `--decision-url`'s help said a System One row's address takes a scheme,
  a host and a port and no path, because its client adds `/v1/systemone` itself. TypeSafe's client
  puts that after whatever address it is handed, path and all, and quackd hands it the address as
  given, so a server behind a gateway that serves the format under a prefix of its own is reached
  by naming the prefix, while an address that already ends in `/v1` reaches `/v1/v1/systemone`.
  They say so now, and the `jev` page says what keeps each of OpenRouter's two decision endpoints
  out of reach, a route that ends another way for its alpha Decisions API and the key for its
  System One endpoint, where it named the first alone. `tests/test_docs.py` holds each of them to
  the rule the factory keeps.
- **The decision-LLM hub says what a server nobody started costs on Windows.** Windows takes
  longer than the stepper's second to refuse a connection to a port on your own machine that
  nothing listens on, so the stepper's own bound ends that turn, its record says
  `TimeoutError: no answer within 1.0 s` as a slow server's does, and in either mode it waits out
  its second before the model is asked. The hub said only that such a server costs a turn and
  never the run. `tests/test_docs.py` holds the sentence to the words the stepper writes.

### Known limitations

- **The aim of 90 to 95 percent of the example files running as asked on the arm is unmeasured.**
  By the run sheet's own expectations 195 of the 223 should, 87 percent, short of the aim, and the
  examples README names the 28 that should not. Measuring it takes every file once on a release,
  counting the runs that started, moved as asked and did not disown a part, which the bench steps
  below do not.
- **Played runs have not run on the arm.** Every check of play so far is offline: renders, the
  test suite and the scripted pilot, and no paid model has been asked. These are the bench steps
  that would settle it, in order, with a hand near the power switch, and what to report from
  each:
  1. Install this release where the arm's runs are made, and check that a run's
     `run_start.version` says it. Run the task files from a checkout of this release, by the
     command in each file's header.
  2. Before each played file: blue things out of the front camera's view, since the colour
     detector reads blue as a person, the front camera framing the arm and the space around it,
     since a played arm is asked to keep every move small and slow while no picture shows them,
     people beyond the arm's reach, and the file's props where its run sheet puts them, `e048`'s
     foam cube near the table's edge among them.
  3. `e048/be-a-cat`, `e049/t-rex`, `e043/are-you-a-duck`, `e032/mime-box`, `e079/sleep-on-it`,
     `e079/hit-the-road`, `e027/teleoperated` and `e030/as-claude`, each with
     `--llm openai:gpt-6-astra --no-memory`, the model the refusals were reported on, three ways:
     the file as it read at v0.17.1, as it reads now with `--no-play`, and as it reads now. For
     the first, check v0.17.1 out beside this checkout once, with
     `git worktree add ../quackd-v0.17.1 v0.17.1`, and run the file from there in place of the
     path in the header, `../quackd-v0.17.1/docs/examples/lerobot/e048/be-a-cat.duck` for `e048`.
     Give each of the three runs a `--run-name` of its own, such as `v0171`, `no-play` and
     `play`, in place of the `--run-name gpt-6-sol` in the header of `e030/as-claude`. Report for
     each run whether it moved, and whether its verdict or a reason said the body cannot be what
     the task asked, or acted the part and then said it had not.
     `git worktree remove ../quackd-v0.17.1` takes the old checkout away again.
  4. The controls: `e009/raise` as written and with `--play`, which must stay still both times,
     and `e079/twist-my-arm` as written, which must move nothing toward anyone.
  5. Count a refusal where a run never moved, or moved and then disowned its part in a reason.
     Count a run with a `[refusal]` line or a `stop=refusal` turn, a refusal returned in OpenAI's
     own refusal channel, as a refusal too, and report the two kinds apart.
     `quackd log DIR --prompt --thinking all` shows each run whole.

  Optional, before step 3, and only if you want a part's fold to reach all the way back into the
  arm's rest fold: calibrate again, typing `c` at the first prompt and taking the shoulder all the
  way back into its fold when the tool asks for each joint's full range, then record the rest pose
  again, which is sections 05 and 07 of
  [docs/adapters/lerobot/first-run.md](docs/adapters/lerobot/first-run.md). That moves
  `shoulder_lift`'s zero, so a task or a remembered note that names an angle means a different
  pose afterwards.

## [0.18.1] — 2026-10-19

0.18.1 makes what quackd says about a Microduck that falls true on every robotd it serves, and fixes
three small things. robotd turned `limp_fall` back on in daemon-v0.16.2: it goes limp when it
predicts a fall while the duck walks or stands, poses the duck and hands it back to its walking
policy, upstream's `v8`, which upstream says gets up from a fall. quackd's datasheet, `stand_up`'s
words and the Microduck's pages described only daemon-v0.16.1, where robotd did nothing about a fall
but report it, and they say what each release does now. The datasheet no longer calls the speed caps
a clamp in robotd, which clamps no twist at any release quackd has read: they are quackd's own. The
Microduck page names upstream's releases to daemon-v0.17.6, which from daemon-v0.17.3 report API 44,
which quackd serves with a warning: nothing it sends or reads changed shape. A `.duck` saved as
UTF-8 with a byte-order mark is read, a ToddlerBot with no walk checkpoint is told it has legs, and
a `--goal` run with `--no-memory` no longer tells its pilot to call `remember`. No verb sends a
robot anything it did not send before.

### Fixed

- **`stand_up` says so when robotd is still answering a fall as it reads the duck again, where it
  said the duck may need a hand or that its posture was unknown.** A robotd whose `limp_fall` is on,
  as it is by default again from daemon-v0.16.2, takes a falling duck off its policy, lets it land
  limp, ramps it to its standing pose and only then hands it back, and it labels those ticks
  `limp_fall` and then `limp_pose`. `stand_up` reads the duck again three seconds after its request,
  and when robotd still had it then, one that read as fallen was told that robotd does not promise
  to get a fallen duck up and that it may need a hand, and one that did not was told only that its
  posture was unknown. It is told now that robotd is still answering the fall itself, with the label
  and what it means, that robotd hands the duck back to the policy once it has posed it, and to run
  `stand_up` again in a moment. With upstream's defaults the sequence is over within about 2.1 s, at
  most 1.5 s limp and then 0.6 s of pose, so robotd still has the duck at that read only after a
  fall it caught during the wait, such as one that came after the policy had got the duck partway
  up. A duck robotd had when `stand_up` was asked is back with the policy by then and is judged as
  before: upright if it reads standing, and told it may need a hand if it is still down, even while
  the policy is still getting it up. What `stand_up` refuses before it asks the policy to drive, and
  the request itself, are as they were. Its description says the result may say this, and so do the
  Microduck page's rows for the sequence and for `stand_up`'s assumption, which says too that the
  request only sets the flag again on a robot whose policy is enabled already.
  `tests/test_jsonrpc.py` runs it against a fake robotd that catches a fall as the request arrives,
  in both halves of the sequence, fallen and not, and the four cases failed before this change. It
  also runs it against one that hands the duck back during the wait, which is told it is upright or
  may need a hand, and those cases fail when the label is read off the frame from before the
  request.

- **The Microduck's datasheet says what robotd does about a fall on daemon-v0.16.1 and on the
  releases after it.** It said that on daemon-v0.16.1 robotd does nothing about a fall but report
  it, which is so there by default. From daemon-v0.16.2 `limp_fall` is on by default again, and
  robotd goes limp in a fall it predicts while the duck walks or stands, poses the duck and hands it
  back to its walking policy, the `velstand.onnx` of upstream's policy set `v8`, which upstream says
  gets up from a fall. It catches no fall during a skill, a ground pick or a sit, so none during
  quackd's `kick` or `grab`. The sheet says all of that now, in two notes, and still that the duck
  may not get up again by itself, since nobody has seen either release get one up under quackd.
  `upstream_api.py` reads the sequence at the pin as `LIMP_FALL`, with daemon-v0.11.0 and
  daemon-v0.12.0, which had it on by default too, and daemon-v0.13.0, which turned it off. Its notes
  on the fall verdict, on velstand and on `stand_up`'s assumption say what daemon-v0.16.2 changed,
  and the physics simulator's say that `v8` changes no policy the simulator runs. The Microduck page
  has a row for the sequence, and its rows for the fall verdict, velstand and `stand_up`'s
  assumption and its physics section say the same. `tests/test_datasheets.py` holds the notes to
  both releases and to the falls robotd catches, and failed on the note that said robotd goes limp
  in every fall.

- **The Microduck's datasheet no longer says its speed caps are a clamp in robotd.** The note has
  said since 0.9.0 that the caps are a software clamp in robotd, and robotd clamps no twist at any
  release quackd has read: what limits a tick in its state is the deadman, a joint target past the
  actuator's travel or one that is not finite, and nothing caps the speed of the twist its policy is
  handed. The caps, 0.3 m/s forward, 0.2 m/s sideways and 1.5 rad/s of turn, are the Microduck's own
  `limits`, which quackd holds every twist a verb sends to, and which the pilot's prompt already
  called quackd's clamp above the note. The note says they are quackd's own clamp, and the numbers
  are as they were. `upstream_api.py` records what was read as `NO_TWIST_CLAMP`, with what
  upstream's gamepad asks for at full stick at the pin and from daemon-v0.16.2, and the Microduck
  page has a row for it. `tests/test_datasheets.py` holds the note to it.

- **A `.duck` file saved as UTF-8 with a byte-order mark is read like one saved without.** Windows
  editors can save UTF-8 with one, Notepad among them, which puts three bytes in front of the first
  line. quackd decoded them to a character standing in front of the opening `---` fence, or in
  front of the `#` comment lines a file may open with, so it refused a valid file as
  `missing frontmatter: file must start with a '---' fence` wherever it read one: `quackd run`,
  `quackd record`, `quackd validate`, `quackd preflight`, `quackd serve-mcp --duckfile` and the MCP
  tool `robot_load_duckfile`. A mark at the very start of the file is skipped now, and the `.duck`
  reference says so. `tests/test_duckfile.py` saves `hello-world`, which opens on its fence, and
  `fetch`, which opens on comment lines, each with a mark in front, and reads them through the
  parser, the loader, `quackd validate` and what `robot_load_duckfile` calls. It failed before this
  change.

- **A ToddlerBot with no walk checkpoint is no longer told it has no legs.** Until its daemon
  reports a walk checkpoint that can move it, which a fresh install has not got, `toddlerbot:bridge`
  has no `move`, `go_to` or `approach_and`. Its datasheet line then read
  `It does not move: no base and no legs`, in the same prompt as a blurb naming its two legs, and
  the same words, lowercased, were in what the MCP server tells a pilot and in the roster a flock
  of pilots shows each pilot. That line describes an arm. A body that stays put with legs reads
  `It does not move: it has legs, but no verb on this connection walks it` now, and one on wheels
  is told it has a wheeled base that no verb drives. An arm reads what it read, and so does every
  body that walks or drives. `tests/test_datasheets.py` renders the bridge as described before its
  daemon answers and as built for a daemon with no checkpoint, and checks that among the shipped
  bodies only an arm is told it has neither legs nor a base. It failed before this change.

- **A `--goal` run with `--no-memory` no longer tells its pilot to call `remember`.** With memory
  off a run is offered no `remember` tool and its prompt has no memory section, yet the task
  `--goal` writes still told the pilot to `remember` one fact before declaring success, at the end
  of its strategy and again in a Memory section under it. A pilot that did as it was told was
  refused at the price of a call, and on the local JSON fallback, which drops a call to a tool it
  was not offered, two such turns in a row ended the run as a failure. With memory off that task
  now says nothing about `remember`, and with memory on it reads as it did, byte for byte. A `.duck`
  file is its author's words and still asks for whatever it asks for, as ADR-0025 records, and what
  quackd adds to its prompt already said nothing about `remember` with memory off. The memory guide
  says a goal run asks for a note only while memory is on. `tests/test_cli_memory.py` runs a goal
  and a `.duck` with `--no-memory` and finds no `remember` in the prompt or the tools either one
  sent its pilot, and runs a goal with memory on, which still asks for one. The goal with memory
  off failed before this change.

### Documentation

- **The Microduck's pages say what upstream's releases of 2026-10-12 changed, and two ADRs and the
  README say what a fall does and whose clamp the speed caps are.** The Microduck page's upstream
  section names daemon-v0.16.2 to daemon-v0.17.1, each on API v41, and the three defaults
  daemon-v0.16.2 changed that its rows cite. Step 9 of the hardware checklist said a robot on
  daemon-v0.16.1 does nothing about a fall but report it, and now says what one on daemon-v0.16.2 or
  later does too, in a fall while the duck walks or stands, and that quackd sends it no walk
  meanwhile. Step 10 asks what robotd's answer to a fall did on a real duck, and how long the duck
  took to get up against the three seconds `stand_up` waits. ADR-0011 carries a dated amendment for
  the fall, and ADR-0032 one for the note that called the speed caps robotd's. The README's Why
  section listed standing up after a fall among the Microduck's own skills, which quackd has never
  seen, and says now that Pollen says its newest walking policy gets the duck back up.

- **SECURITY.md, the safety page and the FAQ say what robotd clamps.** SECURITY.md said robotd
  clamps velocities, the safety page that it gives joint and thermal clamps, and the FAQ that it
  has thermal clamps. At daemon-v0.16.1 and at the four releases of 2026-10-12, robotd has no
  velocity clamp and no thermal one: it holds each joint target inside its actuator's travel, and
  it reports its servos' temperatures in `robot.health` without acting on them. The three say so
  now, and `tests/test_docs.py` keeps the old words out of every living page.

- **The Microduck page says what robotd's `move.applied` is.** Its row for
  `robot.state.move.requested` called `applied` the twist after the deadman and the limits, and
  the only limits robotd has besides the deadman are on joint targets. It says now that `applied`
  is the twist robotd's control loop hands its controller: the one asked for, or zero once the
  deadman finds it stale, smoothed toward each new value and held at zero through the limp-fall
  sequence. The row that says robotd clamps no twist says too that its controller zeroes the twist
  while a client holds robotd's body-pose mode, which neither `requested` nor `applied` shows.

- **The Microduck page says what upstream's daemon-v0.17.2 to daemon-v0.17.6 changed.** `v0.17.2` is
  on API v41, and from `v0.17.3` they are on API v44, which quackd serves with a warning: the three
  bumps add only calls quackd does not make, and nothing it sends or reads changed shape. From
  `v0.17.3` robotd's idle look-around also leans the neck, opens the beak and yawns, and its 2 s and
  1.5 s timings became `[idle]` settings. At the shipped values quackd's gaze hold and its fade wait
  still hold, and the page says what a robot retuned to glance sooner or fade longer does to them.

## [0.18.0] — 2026-10-13

This release re-reads the Microduck's robotd, at upstream's daemon-v0.16.1. quackd had read robotd's
protocol on 2026-09-04, at a commit on API 23, and refused at connect every robotd whose `hello`
named another number, which from daemon-v0.12.0 on was every release. quackd's own `hello` names
API 41 now, and it serves any robotd from API 23 up, saying so when the version is not 41. Around
the re-read, a duck that does not read as standing is sent no walk, every twist is checked against
robotd's own state, a gaze holds against robotd's idle look-around, WebRTC frames come out upright
and are measured through the lens mediad reports, the Microduck page's camera is mediad's own
`GET /frame`, and the physics simulator fetches its policies at upstream's `v7`.
`microduck:websocket` is deprecated and goes in 0.19.0, since upstream dropped the gateway it was
waiting for.

No Microduck has run quackd yet. The last step of the Microduck's hardware checklist lists what only
a robot can settle, among them whether robotd's policy labels track a real duck and what pulling
`/frame` costs one. Upstream shipped daemon-v0.16.2 to daemon-v0.17.1 on 2026-10-12, after the read.
Each still names API 41, so quackd serves them without a warning, and a read of what they changed
found nothing quackd sends or reads: robotd's own code is the same, and duck-ipc-proto gained only
two constants that never go on the wire. What changed for robotd are its defaults. From
daemon-v0.16.2 robotd goes limp when it predicts a fall, poses the duck and hands it back to the
walking policy, now upstream's `v8`, which upstream says gets up from a fall and turns at the rate
it is asked for, and a pad's bumpers no longer kick. daemon-v0.17.0 adds a pad over the network, off
by default. quackd sends a duck no walk while robotd poses it, so robotd's new answer to a fall asks
nothing of quackd, but the Microduck's pages and `stand_up`'s words about a fall still describe
daemon-v0.16.1, where robotd did nothing about a fall but report it.

A pilot built on OpenAI's SDK sends three variables that SDK reads for itself,
`OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, only to OpenAI's own API and to
the address `OPENAI_BASE_URL` gives `--llm openai`, as the SDK sends them, where every pilot that
speaks OpenAI's API sent them to its own address, OpenRouter and a board over plain HTTP among them.
Everywhere else no authorization goes but the pilot's own key. Its client follows no redirect,
wherever it points. A local preset sends its server the key `--api-key` or `LOCAL_API_KEY` gives it,
or the word `not-needed`, and never `OPENAI_API_KEY`.

Some of the fixes reach every body. The executor no longer lets a stop that fails out of the
handlers that send one, and says it failed. `go_to` and `search_scan` end at the first twist a body
refuses, and say why, and `search_scan` left to its own step turns in steps whose views overlap
through a lens no wider than 45°. The scripted flock pilot waits on the wall clock before each check
for a peer, after CI's macOS runners failed two flock tests on the tags of 0.17.0 and 0.17.1. The
Microduck's pages say what the re-read found, that a gamepad does not out-rank quackd among it, and
seven table rows that lost their last cells to a `|` show them whole. Nothing in the arm's own
backend changed, and the arm has not run quackd since 2026-09-23, so the bench steps 0.14.0 and
0.15.0 owe are still owed.

### Added

- **An adapter can announce a backend it is retiring.** An adapter's module declares
  `DEPRECATED_BACKENDS`, a mapping of a backend's name to the one line that says what to use instead
  and the release it goes in, where `SIMULATOR_BACKENDS` is a tuple, and
  `quackd.adapters.factory.deprecated_backend(spec)` reads it. `quackd run`, `quackd doctor`,
  `quackd serve-mcp` and `quackd robot list --probe` print that line once on stderr just before they
  connect through the backend. A declaration in any other shape, or a line that is not a string,
  raises a TypeError naming the module rather than being read as nothing, and an adapter whose
  module fails to import has no line. `docs/adapters/writing-an-adapter.md` says how.
  `tests/test_doctor_and_stub.py` refuses a tuple declaration by name, which failed before this
  change, and finds no line from an adapter whose module raises as it is imported.

### Changed

- **A Microduck on any robotd from API 23 up is served, and quackd says so when its version is not
  the 41 it read.** `microduck:jsonrpc` refused a robotd whose `hello` named any version but the one
  quackd read, which is how every robotd release from daemon-v0.12.0 on came to be refused, and how
  the next would have been after any re-read, since robotd ships a release every few days. No robotd
  refuses a peer over the number: upstream's rule is that a bump promises nothing, and that a call
  whose shape moved refuses itself by name. quackd follows it now. A robotd older than API 23, the
  oldest quackd has read end to end, is still refused at connect, and so is one whose `hello` names
  no version, which used to be let through. Any other is served, and when its version is not 41 the
  log, `quackd doctor` and every state's `extras.robotd` say so, beside the release and the revision
  the robot answered with, which `doctor` also prints as a `robotd` row. A call robotd refuses with
  `METHOD_NOT_FOUND` or `INVALID_PARAMS` fails the verb naming the call and both versions, a socket
  with no `hello` is called not robotd's, and so is one that answers `hello` but has no
  `robot.subscribe`, as updaterd's does. An answer to `robot.do`, `robot.sound` or `robot.enable`
  that carries no `accepted` quackd can read is a refusal that names what came back, where it read
  as a yes. `robot.move` is a notification, which robotd answers with nothing, so every twist is now
  checked against robotd's own state: one that `move.requested` has not shown three state frames
  after robotd answered a call quackd made after it holds `robot.move` back until the verb that was
  walking ends, the same hold the Fixed entry on posture describes for a duck that stops standing,
  and the verb fails saying what robotd showed instead, with the verdict in `extras.move_echo`.
  Frames are counted from robotd's answer and not from the send, since robotd reads a connection in
  order, so a link that held quackd's lines back for a while delays the verdict instead of stopping
  a walk robotd then took, and each walk costs a `robot.health` more than it did. They are counted
  only up to robotd's answer to the stop that ends a walk, since robotd zeroes its twist as it reads
  a stop, and the walks of the verbs still running are counted together, so the frames of every turn
  of a `search_scan` count toward the verdict, over a slow ssh tunnel as on the robot, and the stop
  after a turn neither lifts the hold nor writes off what they said. `search_scan` counts the
  turns it says it made by the ones robotd's state showed it took, and says so when that was none,
  where a scan of a robotd that took none of its twists said it had turned all the way round and
  that the target was not there. A scan stops only at a turn it sends after the verdict, so one
  whose last turn brings it, or whose turns are too short for one, ends not finding the target and
  says how many of its turns robotd's state confirmed. A gamepad connected to the robot sends
  robotd a stop every 100 ms while nobody touches it, and robotd walks on whichever twist came last,
  the pad's or quackd's, so with one connected a walk can now stop this way, where it used to go on
  sending twists that robotd took in turn with the pad's stops.
  `extras.pickup_detection` says whether the robot reports `safety.picked_up`, which robotd first
  shipped in daemon-v0.15.2, and `doctor` warns when it does not, since nothing then tells quackd
  that somebody is holding the duck. `extras.robotd` also says whether the state carries `frames`,
  and whether mediad's `GET /frame` answered when `--camera-url` asked it, since the floor serves
  releases with neither. [ADR-0051](docs/adr/0051-a-microduck-newer-than-the-read-is-served.md)
  records the rule. `tests/test_jsonrpc.py` serves a robotd one version newer than the read and one
  at the floor, refuses two below it and five kinds of `hello` with no integer version, fails a verb
  whose call the robot lacks with both versions named, holds a walk back on a robotd that drops
  `robot.move` and on one that refuses its params by name until the verb that walked ends, through
  its own stops, but not on frames read late after a stall, nor on a link that held quackd's lines
  back for longer than the window, stops a `search_scan` on a robotd that takes no twist over links
  that hold every byte back one to four frame periods each way, and in fifteen-degree turns over a
  quick one, judges a verb's walks together on the frames before each stop and on none after,
  calls a socket with no `robot.subscribe` not robotd's, takes a kick answered with no `accepted`
  for a refusal, and probes the robot through `doctor` for the new row and both warnings, and
  `tests/test_composite.py` counts a scan's turns by the ones the body's own state confirmed. Each
  of them failed before this change. Over the same slow links `go_to` stops on a robotd that takes
  no twist, and both verbs reach their end on one that takes every twist, and a stop robotd never
  answers leaves the walk before it unjudged.

- **quackd reads robotd at daemon-v0.16.1, and `microduck:jsonrpc`'s `hello` names API 41.** quackd
  read robotd's protocol on 2026-09-04, at a commit on API 23, and its handshake refused, as the
  Microduck page said, a robotd that answered with any other number. Upstream's releases reached API
  27 with daemon-v0.12.0, and every release since was refused at connect, daemon-v0.16.1 on API 41
  among them. quackd now reads robotd at daemon-v0.16.1 (`d818af5`), every reference in
  `upstream_api.py` is anchored there, and quackd's hello names API 41. Everything quackd sends has
  the same shape at both commits, and so does everything it reads but for fields added to
  `robot.state` and `robot.health`, so the re-read changes no shape on the wire. What this release
  sends that 0.17.1 did not, such as `robot.head` every second under a gaze, a `robot.health` more
  for each walk and one `media.video` ask over WebRTC, is in the entries that add it. Which other
  versions the handshake serves now is in the entry above. The re-read also changes what the pilot
  is told. The policy labels posture is sorted by are read in robotd's source now rather than
  assumed, so `extras.assumptions` lists `stand_up`'s in posture's place, the assumption that asking
  the policy to drive gets a fallen duck up, which upstream does not promise. robotd's own stand-up
  after a fall was `limp_fall`, and on daemon-v0.16.1 that is off by default and a fall changes
  nothing in robotd, so the Microduck's datasheet no longer says the duck can get itself upright,
  `stand_up`'s description says it may not manage, and a duck still down after it is no longer told
  an onboard recovery has not finished. The WebSocket gateway `microduck:websocket` would talk to is
  gone from upstream's design, and the stub's refusal says so instead of pointing at it. The
  Microduck page's upstream tables are rewritten at the new commit with what robotd has added since:
  the camera routes `mediad` serves (`GET :8080/frame`, `media.frame`), the idle look-around that
  moves the head once nobody has steered it for two seconds and the duck has then stood still for
  two more, the pick-up detector, and the `velstand.onnx` walk. The fake robotd in
  `tests/test_jsonrpc.py` answers as daemon-v0.16.1 does, with its hello, its subscribe answer, its
  state frame and params that refuse a member robotd does not know, and a run of every verb that
  reaches the socket leaves it with nothing refused. The tests that hold the page to the commit and
  the release, the posture labels to the source they are read from, the assumptions to what
  `upstream_api.py` calls unverified, and the datasheet and `stand_up` to promising no recovery each
  failed before this change.

- **A Microduck's gaze holds against robotd's idle look-around, its state says where the camera
  points, and `go_to` and `search_scan` let a glance fade before they act on a frame.** Since
  daemon-v0.16.1 robotd glances around on its own once nobody has steered the duck's head for two
  seconds and it has then stood still for two more, up to 0.6 rad of yaw, and lets go of a glance by
  fading it out over 1.5 s. On a robotd that glances, a `gaze` that is only one `robot.look` keeps
  the head there only until robotd's first glance, which can come four seconds later, and an
  `observe` after that can report bearings off by as much as the glance. quackd 0.17.1 never reached
  such a robotd, since it refused every robotd release from daemon-v0.12.0 on. `microduck:jsonrpc`
  now sends the head a look answered with again as `robot.head` every second, as upstream's own doc
  for that answer says to, and the state says what is held as `extras.gaze_hold`. Nothing goes out
  while the duck reads as anything but standing or sitting, so a duck somebody holds, a limp or a
  fallen one and one halfway through a skill get no head command, and the hold takes up again once
  the duck stands. A head somebody turned while it waited, as a gamepad centres the head when its
  head mode ends, is let be rather than turned back if robotd said meanwhile that somebody held the
  duck, or that it was limp or in a skill, none of which robotd glances at, or if robotd's
  two-second wait had not run out since the head last went out. After longer in any other state, a
  fall among them, a glance of robotd's own cannot be told from another client's head, and the head
  goes out again as before. There is one hold at most, the newest look's. A stop ends it before the
  stop itself goes out, and so do the next look, a link that fails and the end of the connection,
  and a look still waiting on robotd's answer when one of them comes starts none. When
  `robot.state.head` moves away from the held head or stays off it, which is another client steering
  it, a gamepad posing the head among them, the hold lets go rather than pull the head back every
  second, and `extras.gaze_released` says `overridden`. On a robotd from API 41, the first release
  that glances, `go_to` and `search_scan` wait 1.5 s after centring the head, with nothing sent that
  moves the duck, and on mediad's `/frame` the first frame they act on is one taken after the wait.
  Over WebRTC it is the newest frame the stream has decoded, which the stream's own delay can put in
  the last moments of the fade. On such a robotd `search_scan` also centres the head again after
  each turn, since the stop that ends a turn ends the hold. The state also says where the camera
  points, as `extras.head_yaw_deg` and `head_pitch_deg`, positive left and up, read off
  `frames.camera`, or the yaw off the head_yaw joint on a robotd without `frames`. The yaw is in the
  convention the simulators have always published it in. `tests/test_jsonrpc.py` holds a gaze
  against a fake robotd and ends it each way it ends, sends two looks at once and stops or closes
  while a look waits on its answer, pauses it for each posture that pauses it, lets go of a head
  another client steers or holds still and of one turned while the hold waited, but not of one
  robotd is still smoothing in or fading a glance off, nor of one after a fall too long to rule a
  glance out, reads the camera's yaw and pitch off `frames.camera` and the yaw off the joint, and
  times `go_to` against the fade, and `tests/test_composite.py` holds both verbs to the wait and to
  a first frame taken after it, and `search_scan` to centring the head after each turn on a body
  that glances. Each of them failed with its part of this change taken out.

- **A Microduck's camera is mediad's own `GET /frame` now, which `microduck:jsonrpc` pulls at 2
  frames a second, and a tall first frame from it is taken for an alpha's lens.** The Microduck page
  starts with `--camera-url http://127.0.0.1:8080/frame` through
  `ssh -L 8080:127.0.0.1:8080 microduck@<duck>`, the upright PNG mediad has served since
  daemon-v0.13.0, with no extra installed and nothing stopped on the duck, where it used to offer
  WebRTC or a snapshot server of your own with mediad stopped. Every request has the duck encode a
  PNG of its whole picture, and nobody has measured what that costs a robot, so `microduck:jsonrpc`
  pulls any `--camera-url` whose path is `/frame` at 2 frames a second, whatever its host and port,
  where an HTTP camera on any other path is still pulled at 5. The PNG carries no lens, so a first
  frame taller than wide, which on a stock robot only an alpha's camera gives, is taken for the
  IMX219's 62° running down the picture: 37.3° across the 720 by 1280 frame of the default 720p.
  That goes in the manifest as `camera_fov_deg`, beside the words of the assumption, since it is the
  module's design rather than a calibration, and wrong on a duck in upstream's own simulator, whose
  camera is 45° wide. `quackd run` measures through it when no `--fov-deg` names a lens, and
  `serve-mcp`, which has no `--fov-deg`, always does, with detections that say they are uncalibrated
  and a word in the log to pass `--fov-deg`, which only `quackd run` takes. Every state lists the
  assumption in `extras.assumptions` until `--fov-deg` gives `quackd run` a lens of its own, when
  `extras.camera` says the lens was given. A wide frame and any other HTTP camera get no lens, as
  before. A WebRTC camera gets the lens mediad reports, as the Fixed entry on WebRTC frames says.
  `go_to` turns toward what it sees at a rate set by the bearing, holds that turn for a tick and
  measures again on the newest frame, a law sized for a camera with a new frame most ticks. mediad's
  route hands out the same frame for half a second or more, long enough for one measurement's turn
  to carry the duck past the target. A camera now says how long one frame takes to follow the last,
  and where two of those outlast the third of a second the turn takes to cover a whole bearing,
  which is every `/frame` camera, `go_to` measures, then moves: it turns no further than each frame
  measured, walks on straight while the turn settles, and steers again only on a frame taken after
  it, so it closes on a target more slowly there. The simulators, WebRTC and any other HTTP camera
  say no period and are steered as before, and a target lost from view is looked for as far as on
  any other camera. On mediad's route `search_scan` looks after each turn at a frame taken once the
  turn has settled, since mediad takes its picture after it is asked. The newest frame quackd holds
  there may have been asked for a frame period and a fetch before, and show the view from before the
  turn or from halfway through it. Any other HTTP camera may hand out a picture it took before it
  was asked, and a WebRTC frame arrives some unknown while after it was taken, so on those
  `search_scan` looks at the newest frame, as before. A camera of your own served at `/frame` is
  taken for mediad's in all of this, so serve it on another path to keep it pulled at 5 frames a
  second, with no lens assumed and `go_to` and `search_scan` reading it as before.
  `tests/test_jsonrpc.py` asks mediad's route at its rate and every other camera at theirs, takes
  the lens from a tall first frame of mediad's and from no other, names the assumption beside it,
  keeps it when a later frame is wide, takes it off the state once `--fov-deg` gives a lens, and
  tells mediad's route from another camera by whether a frame says when it was taken,
  `tests/test_detector_selection.py` measures through it uncalibrated on a run and over MCP and
  hands the flag's lens to the body, `tests/test_composite.py` has `search_scan` look at each
  heading through a camera that hands out frames taken half a second before, and
  `tests/test_docs.py` holds the page to the numbers in the code. Each failed before this change.
  `tests/test_composite.py` steers at a ball through a camera that hands out a frame every half
  second and takes 0.6 s to fetch one, which lost the ball, holds the change to two frame periods
  against the turn's third of a second from both sides, and the search for a lost target to turning
  as far either way, and `tests/test_jsonrpc.py` reads the period off mediad's route while its
  frames come quickly and once one comes slowly. Each failed with its part of this change taken out.

- **A local preset sends its server the key `--api-key` or `LOCAL_API_KEY` gives it, or the word
  `not-needed`, and never `OPENAI_API_KEY`.** With no `--api-key` and no `LOCAL_API_KEY`, `ollama`,
  `vllm`, `llamacpp`, `lmstudio` and `local` sent `OPENAI_API_KEY` as their bearer token whenever it
  was set, a hosted key on its way to a server you run, over plain HTTP to a board included, which
  is not what [ADR-0014](docs/adr/0014-local-llms.md) decided for them. They send `not-needed` now.
  A server that checked the key it was sent against `OPENAI_API_KEY` is given its key with one line
  in `.env`, `LOCAL_API_KEY=` and that key. `tests/test_openai_headers.py` builds every local preset
  with `OPENAI_API_KEY` set to a value of its own and finds the placeholder sent,
  `tests/test_local_provider.py` finds it as `ollama`'s fallback with `OPENAI_API_KEY` set, and
  `tests/test_openai_headers_wire.py` finds it on the wire to a board through the real SDK. Each
  failed before this change.

- **A pilot built on OpenAI's SDK follows no redirect.** In 0.17.1 every such client was built on
  the SDK's own HTTP client, which follows redirects, so a turn answered with a 307 or a 308 went
  on, body and all, to wherever it pointed, and a server that answered that way, such as one behind
  a proxy that sends plain `http` on to `https` with a 308, was reached anyway. A turn answered with
  a redirect fails now, and so does a local preset's model discovery, and the error says where the
  redirect pointed and, for an API that moved whole, the address to give quackd instead:
  `--base-url`, or `OPENAI_BASE_URL` for `--llm openai` without one. `tests/test_openai_headers.py`
  holds every preset's client to following none, and `tests/test_openai_headers_wire.py` finds one
  request sent through the real SDK for a turn answered with a 301, 302, 303, 307 or 308, and the
  `https` address of a server behind such a proxy named in the error, on a turn and on a local
  preset's model discovery. Each failed with this change taken out.

### Deprecated

- **`microduck:websocket` is deprecated, and is removed in 0.19.0.** It was a stub that refused to
  connect while it waited for the WebSocket agent gateway upstream's architecture.md designed, and
  by daemon-v0.16.1 upstream had dropped that design. A server-side agent goes over upstream's
  rendezvous control lane now, which carries no pixels and which upstream's own relay calls not a
  teleop lane: past 1200 requests a minute the service refuses the whole peer, the robot's own
  lease included. A walk under quackd re-sends its twist at a joystick's rate, so quackd will not
  drive over it, and nothing will come for the stub to become. `quackd run`, `quackd doctor`,
  `quackd serve-mcp` and `quackd robot list --probe` print one line on stderr just before they
  connect through it, naming `--robot microduck:jsonrpc`, with
  `--camera-url http://127.0.0.1:8080/frame` for mediad's camera, as what to use instead. A fleet
  with several robots on it is told once, wherever they come in it, and a command says nothing for
  a robot it does not connect: the second of a task's `--robots` when the task has no flock, a run
  refused before it connects, or a served robot after one that failed to connect. The stub's
  refusal ends with the same line, and `quackd list-adapters`, `doctor`'s adapters table and the
  status pages call the backend deprecated. The line is the one the Microduck declares for it in
  `DEPRECATED_BACKENDS`, which is under Added. `tests/test_doctor_and_stub.py` runs all four
  commands with the stub first, second or third among their robots and finds the line once in each,
  ahead of the stub's refusal in `run` and `serve-mcp`, and finds it in the log of a server started
  without the CLI, each of which failed before this change. It finds no line from any other backend
  or from a robot a command never connects.

### Fixed

- **The scripted flock pilot no longer gives up on a peer that is a moment slower.** `--llm fake` on
  `flock-hello` tells the flock it is there and then checks three times for an answer. A check on a
  mock body takes a few milliseconds, so a member whose peer connected or stepped more slowly could
  spend all three before the peer had said anything, and end with nobody answered after 3 checks
  while the peer was about to answer. CI's macOS runners failed two flock tests that way, on the
  release tags of 0.17.0 and 0.17.1. Each check now waits on the wall clock first, a quarter of a
  second, then a second, then four, so a flock whose peers have already spoken pays almost nothing
  and one whose peer never comes still ends. `tests/test_pilots.py` holds an arm that connects a
  tenth of a second after the duck's hello, which failed every time without the waits.
  `test_dry_run_reaches_every_member`, a flock test that failed on macOS on 2026-09-29 and
  2026-09-30 with no reason given, now prints its reason when it fails.

- **A Microduck that is not standing is no longer sent a walk, and a verb that has walked sends no
  more twists once robotd reports the duck held or down.** `microduck:jsonrpc` read the duck as
  sitting when its policy label contained `sit`, and as standing for every other label unless
  `safety.fallen` said it was down. robotd's label names what drove the tick, not where the duck
  is, and most of its labels are not a duck ready to walk: `held` while no network drives, which on
  a robotd with the pick-up detector is also a duck somebody is holding, `homing`, `rise` off the
  seat, `ground_pick`, a skill such as a kick, and whatever label a newer robotd adds. Each could
  be sent a walk, and `go_to`, `search_scan` and `approach_and` checked no posture at all. Posture
  is an allowlist now. `walk` and `stand` read as standing and `sit`, matched exactly, as sitting.
  Every other label reads as `unknown`, and so do a duck somebody holds (`safety.picked_up`), even
  one that also reads fallen, and a limp one (`safety.limp`) that does not, with
  `extras.posture_reason` saying which. `move`, `kick`, `grab`, `go_to`, `search_scan` and
  `approach_and` refuse a duck whose posture is `unknown`, where they used to go ahead, and the
  last three need the duck standing on every backend, so on the simulators they now refuse a seated
  or fallen duck they used to steer while its legs ignored every twist. A duck somebody holds is
  read before a fallen one, so on a robotd that reports it the refusal says so rather than pointing
  at `stand_up`, and `gaze`, `sit` and `stand` refuse it as well. `stand_up` reads the duck before
  it sends `robot.enable`: it refuses a duck somebody holds or that nothing reports on, and one on
  a robotd that cannot say whether it is held, says a standing duck is already upright, points a
  seated one at `stand`, and calls the duck upright afterwards only if it reads standing. On a
  robotd from before daemon-v0.15.2, which cannot say whether the duck is held, `stand_up` refuses
  every fallen duck, and the answer there is to stand it up by hand. Underneath all of these,
  `microduck:jsonrpc` sends no `robot.move` unless the newest state reads standing, and once the
  duck stops standing under a verb that has walked and is still running, `search_scan` looking
  between two turns among them, it sends that verb none until it ends. The preconditions are read
  once, when a verb starts, so this hold is what keeps a duck picked up halfway through a `go_to`
  from being put down onto a fresh twist. From daemon-v0.15.2 on, robotd's pick-up detector reports
  a duck held while the walk or stand network has it, unless the robot's `[pickup]` turns the
  detector off, its model will not load, or the robot is in roller mode, which it does not watch. On
  an older robotd, or one whose detector is not watching, the hold comes only once the held duck
  stops reading as standing some other way, such as by reading fallen. `go_to`, `search_scan` and
  `move` each stop at the first twist held back and say why, on every backend, where `go_to` steered
  on without one until its timeout and said it had timed out, and `search_scan` reported a full
  sweep of a duck that never turned and said the target was not there. The hold is each verb's own
  and lasts until it ends, so no stop sets walking again a verb still running, a stop from another
  call, the pilot's `stop` among them, and the stop between two of `search_scan`'s own turns alike,
  and while it stands the state reads `unknown` and `extras.move_hold` says why, which `doctor`
  prints as a `move_hold` row too, `none` on the connection it opens itself. A verb refused for
  posture says why, and the answer is to wait for the duck to stand, or to run the `stand` or
  `stand_up` it names. `tests/test_jsonrpc.py` runs every label robotd reports through a fake
  robotd, picks the duck up halfway through a `go_to` and puts it back down, once while another call
  stops the duck and once while it walks beside it, does the same while a `search_scan` looks
  between two turns, holds a verb whose duck was picked up through its own stop until it ends, and
  checks what `stand_up` sends for each posture, `tests/test_composite.py` holds `go_to` and
  `search_scan` to the first twist a body refuses, and `tests/test_adapters.py` holds the three
  camera verbs to `standing` and an `unknown` posture to a refusal. Each of them failed before this
  change.

- **A `robot.state` line longer than 64 KiB no longer cuts `microduck:jsonrpc` off from the robot
  without a word.** The reader took asyncio's default line limit, 64 KiB, and a longer line raised
  out of the task that reads robotd's socket, which died silently. Every request after it waited out
  its timeout, the heartbeat's among them, which ended the run. A state frame is a few kilobytes at
  daemon-v0.16.1, but robotd bounds nothing it writes, its state frame gained the camera's pose, an
  IMU, the whole skeleton and the joints' velocities and currents between API 24 and 36, and a
  robotd newer than quackd's read is served now. The reader takes a line of up to a mebibyte,
  sixteen times the most robotd takes in a line it reads, and skips a longer one rather than ending
  the link. `tests/test_jsonrpc.py` sends a frame between the two limits, which is read, and two
  past the new one, after which the link still answers. Both failed before this change.

- **A Microduck link that fails, or a line from robotd that quackd cannot match, ends no more than
  the call it meets, and says so.** Two other kinds of line ended the task that reads robotd's
  socket the way an over-long one did: an answer whose `id` is a word or a list rather than a number
  quackd gave a call, and an answer to a waiting call whose `error` is not an object, such as the
  null a JSON-RPC 1.0 peer writes beside every result. Each is skipped now, or fails the call it
  answers. A socket that dropped let its own `ConnectionResetError` out of `microduck:jsonrpc`: out
  of the heartbeat, which promises `HeartbeatError`, out of `get_state` and `send_intent`, and out
  of `stop`, which promises never to raise, and a verb's stop raised it from inside the executor's
  handler for the verb's failure, so the call raised instead of answering. Each fails with the
  transport's own error now, `stop` raises nothing, every call made after robotd closed the socket
  fails at once where the first stop waited out its two seconds, and the state reads the duck as
  `unknown` from then on. On every body, the executor no longer lets a stop that fails out of the
  handlers that send one, and says it failed. `tests/test_jsonrpc.py` sends answers with a string
  and a list for an `id`, a null `error` beside a result and a string for one, drops the socket
  under the transport and hangs up from the fake robotd, and `tests/test_executor.py` ends a verb
  that raises, one that times out and one an abort ends on a stop that raises. Each failed before
  this change.

- **`microduck:jsonrpc` with no `--address` looks for robotd's socket where robotd makes it,
  whatever `DUCK_RUNTIME_DIR` says.** It looked for `$DUCK_RUNTIME_DIR/robotd.sock` whenever that
  variable was set where quackd ran. Upstream's `DUCK_RUNTIME_DIR` moves only the directory its
  daemons publish their identity files in, for upstream's own tests and for a daemon run by hand,
  and robotd's socket is a literal `--socket` default that no variable moves, so with the variable
  set the default named a socket no robotd creates. The default is `unix:///run/robotd.sock` now,
  and quackd does not read the variable. A robotd anywhere else, such as each duck upstream's
  `scripts/duck-sim` starts, is reached with `--address`. A test in `tests/test_jsonrpc.py` sets
  the variable and expects robotd's own socket, and it failed before this change.

- **WebRTC frames from a Microduck come out upright, and are measured through the lens mediad
  reports.** An alpha's camera is mounted a quarter turn off upright and nothing on the robot turns
  the pixels back, so `--camera-url webrtc://` handed `observe`, `go_to` and `search_scan` frames
  lying on their side, where how far across the picture a ball sat said how high it was in the room,
  not where it was. mediad says how far to turn its picture, as `media.video`'s `rotate`, and quackd
  never read it. It asks now, once, which is the one line it writes to the control channel, reads
  the answer or the push mediad sends when the channel opens, and turns every frame clockwise by
  that much, the way upstream's own code does. No frame goes out until mediad has said, so a mediad
  that never says leaves the run with no camera and `camera_health()` saying why, rather than one
  that steers on a picture on its side. The same answer carries the lens, for the frame as it is
  sent, and turned with the frame it goes in the manifest as `camera_fov_deg`, which is where
  `quackd run` takes a lens from when `--fov-deg` names none and where `serve-mcp` always takes it:
  37.4° across an upright alpha frame by the alpha family's calibration, where both used to measure
  through the simulator's 90°. The lens is the calibration's focal length alone: its principal point
  is off the frame's centre, which on an alpha leaves every bearing taken from the centre about 6°
  to the left, and quackd does not correct it. A mediad started with `--flip-in-pipeline` turns the
  pixels before it encodes them and still reports the mount, so a frame that comes in tall where
  `media.video` describes a wide one is handed out as it came rather than turned onto its side
  again. `camera_health()` reports the turn, the lens and the intrinsics.
  `tests/test_microduck_webrtc.py` turns a frame by each of the four angles mediad can report and
  checks every pixel against upstream's own turn, holds frames back until the angle is known, reads
  it off the answer and off the push, takes the lens from the intrinsics at each angle, and hands
  out a frame the pipeline turned already as it came, `tests/test_adapters.py` carries the lens into
  the manifest, and `tests/test_detector_selection.py` has the colour detector `quackd run` builds
  before the body connects measure through it once it has. Each failed before this change.

- **A 503 at connect no longer leaves a Microduck without a camera for the whole run, and a verb
  that finds no frame says why.** `microduck:jsonrpc` decides once, at connect, whether a run can
  see, and it stopped waiting for an HTTP camera's first frame at the first failure of any kind. So
  an HTTP camera that answered its first request with a 503, or ran out of time on it, took
  `observe`, `go_to`, `search_scan` and `approach_and` away for the run. mediad's `/frame`, the
  camera the Microduck page now starts with, answers 503 while it cannot hand a frame out, and says
  to retry. quackd now waits up to 5 s through 503s and through requests that run out of time, and
  gives up at once on anything else, such as a refused connection or the 404 of a robot older than
  the route. Whatever came last is in `camera_health()`, with mediad's own reason where it gave one,
  and `quackd doctor` prints it under the camera's row. `observe`, `go_to` and `search_scan` quote
  a body's `camera_error` when no frame comes, and `microduck:jsonrpc` had none, so they said only
  that no frame came. It says now: the last failure, a frame too old to steer on, or, over WebRTC,
  a mount mediad has not said. `tests/test_jsonrpc.py` waits through two 503s to a frame, gives up
  on a camera still unavailable when the wait runs out with mediad's reason in hand, gives up at
  once on a 404 and on a refused connection, waits through both forms of a timeout, fails
  `observe` and `go_to` with mediad's reason, and finds it in `doctor`'s camera rows, and
  `tests/test_microduck_webrtc.py` checks what the WebRTC camera says. Each failed before this
  change.

- **A frame from a Microduck's HTTP camera is as old as the request that asked for it.**
  `microduck:jsonrpc` started a frame's age once the last of it had arrived, while mediad takes the
  picture a `/frame` answers with when the request reaches it, then encodes it and sends it down the
  tunnel. A PNG slow to come was handed out as if it had just been taken, and one that trickled in
  for longer than the 2 s quackd steers on was handed out all the same, since urllib's timeout
  bounds each read and not the request. A frame's age runs from its request now, on every HTTP
  camera, so one that takes longer than that to come is never handed out, and a verb that wanted it
  stops and says why. `tests/test_jsonrpc.py` serves a PNG that takes a while to come and one that
  takes longer than the window. Both failed before this change.

- **`search_scan` turns in steps whose views overlap through a lens no wider than 45°, so no wedge
  of the room goes unseen.** A scan left to its own step turned 45° at a time, and through a
  narrower lens each view ended before the next began: through an alpha's 37° view on a Microduck
  the wedges between came to a sixth of every turn, and a target standing in one was reported not
  found after a whole turn. A step the caller leaves out is now the widest that turns a whole circle
  in whole steps with each view overlapping the next, through the lens the detector measures
  bearings through, and never wider than 45°, and a count of steps left out is as many as make the
  circle, up to the 16 a scan may take: through 37°, ten steps of 36°. A lens wider than 45° scans
  as it always did, the simulators' among them, and so does a body whose detector names no lens, and
  a step or a count the caller gives is kept. A robot that sweeps its head instead of turning steps
  its gaze the same way, and the tool's own words for both parameters say so.
  `tests/test_composite.py` works out the step for lenses from 10° to 120°, those wider than 45°
  keeping the old one, keeps a step and a count the caller gave, and finds a ball standing where the
  old step left a wedge, which a scan with the old step does not.

- **The physics simulator fetches its policies at upstream's `v7`, the set a robot on daemon-v0.16.1
  is seeded with, and nothing in quackd says a robot walks on `alpha_walking.onnx` or stands on
  `alpha_stand.onnx` any more.** `microduck:mujoco` fetched `alpha_walking.onnx`, `alpha_stand.onnx`
  and their manifest from a revision of `pollen-robotics/microduck-policies` older than upstream's
  `v5`. `v5` added `velstand.onnx`, one network that walks and stands, which every robotd from
  daemon-v0.13.0 loads with no standing network, so the licences page's nine policies were one short
  of what a Microduck ships with, and `sim3d/upstream_api.py` said the daemon runs
  `alpha_stand.onnx` while the twist is small. quackd now fetches at `v7`'s revision, `6677ec0`. The
  walking and standing files are the same bytes there, so the simulator walks exactly as it did and
  only the manifest's pinned hash moved. The cache is named after the revision, so the first
  `microduck:mujoco` run after upgrading fetches the two policies again, 1.6 MB, without being
  asked. The model stays at `2b25a48`: `microduck_rl`'s `develop` had moved 36 commits on by
  2026-10-05, and every file quackd fetches from it is the same bytes there. The browser demo
  fetches from the same revision. The Microduck page names the new read, the licences page counts
  ten, the hardware checklist's rehearsal says a robot walks on another network than the
  simulator's, and ADR-0030 carries a dated note beside the nine it was written with. The README,
  the FAQ, the MCP guide, the Microduck page, the page on reading robots, the licences page, NOTICE,
  both `pyproject.toml` comments and the simulator's own docstring called its walk the robot's own,
  or the one the robot ships with, and name `alpha_walking.onnx` now, which Pollen trained before
  `velstand.onnx`. `tests/test_docs.py` holds the physics section to the revisions the simulator
  fetches, and the licences page to the set it pins, counted off a list in `sim3d/upstream_api.py`,
  and keeps the old words for the simulator's walk out of every living page, NOTICE and the sources
  it already reads. Each failed with its part of this change taken out.

### Security

- **A pilot built on OpenAI's SDK sends `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` and
  `OPENAI_PROJECT_ID` only where the SDK itself would send them, to OpenAI's own API and to the
  address `OPENAI_BASE_URL` gives `--llm openai`, and everywhere else no authorization goes but its
  own key.** The `openai` package reads those three variables for itself and adds them to every
  request wherever the client points, and quackd built its clients with no answer to any of them, so
  `--llm openai` and every vendor and local preset that speaks OpenAI's API sent them to its own
  address: OpenRouter, Grok, a model server on a Jetson over plain HTTP. In 3.22.1, the release the
  lock carries, an `Authorization` line of the variable, in any spelling, replaced the vendor's key
  on every request. In every earlier release tried that reads the variable, from 3.1.0,
  `Authorization` did the same and any other spelling went beside the key, and every release tried,
  from 1.66.0, sent `OpenAI-Organization` and `OpenAI-Project` everywhere. The three variables now
  follow the SDK's own address variable. At OpenAI's own API, `api.openai.com` or one of OpenAI's
  regional hosts over https, and at the address `OPENAI_BASE_URL` gives `--llm openai` with no
  `--base-url`, quackd hands the client no answer to any of the three, as before, so all three go
  there exactly as the SDK sends them, and a gateway reached that way is sent what 0.17.1 sent it:
  its own lines, an `Authorization` line among them, and the account headers. At every other
  address, a vendor's own API, OpenRouter, a local preset's server, an address given with
  `--base-url` and a preset moved by `--host`, the provider's own key is named as the one
  authorization, under every spelling the variable gives one, and every other line of the variable
  and both account headers are left off, except that a line naming `Accept`, `Content-Type`,
  `User-Agent` or one of the `X-Stainless-` lines that name the SDK's release and platform leaves
  the SDK's own value in place, and one naming OpenRouter's `HTTP-Referer` or `X-OpenRouter-Title`
  leaves OpenRouter's, rather than taking it off with the line. A local preset that took its address
  from `OPENAI_BASE_URL` is among those, since that rung is quackd's own ladder for a server you
  run, which is sent no hosted key, and the leak this closes was found on exactly such a server, a
  board over plain HTTP. `--llm openai` still sends its key to whatever `OPENAI_BASE_URL` names, as
  the SDK always has. The address is settled before the client is built, and a blank one reads as
  unset. `--base-url ""` given to `--llm openai` or a vendor and a blank `OPENAI_BASE_URL` under
  `--llm openai` failed every turn, and so did a URL of spaces on a local preset's ladder, which
  already read an empty one as unset. The client follows no redirect, so a turn answered with one
  fails instead of sending the request on to wherever it points, and the error says where it pointed
  and, for an API that moved whole, the address to give quackd instead. A client whose own headers
  would still carry an authorization or any other line quackd answered, or whose address reads on
  the other side of OpenAI's own API, is refused before the robot connects, naming the SDK's release
  and never the value, and a local preset's model discovery names its server with the password in
  the address taken out, where it printed the address whole. The rule is
  `quackd/agent/providers/openai_headers.py`. `tests/test_openai_headers.py` holds every preset to
  it under a model of each of the four ways the releases merge headers, with the SDK's own headers
  modelled beside the variable's, and the model agreed with all eleven releases in uv's cache, from
  1.66.0 to 3.22.1. `tests/test_openai_headers_wire.py` captures what each preset sends through the
  real SDK with the network shut, holds what goes at OpenAI's own API and through `OPENAI_BASE_URL`
  to what the SDK sends by itself, header for header, and has run against all eleven of those
  releases. Each failed with its part of this change taken out.

### Documentation

- **The Microduck's pages log in as `microduck`, the account upstream's own tools use, and say it
  has to be in the robot's `robot` group.** The hardware checklist tunnelled to the duck as `radxa`,
  which was quackd's guess. robotd's socket is `root:robot`, mode 0660, and robotd checks nothing
  else about who connects. Upstream's group file creates the group with nobody in it and its
  provisioning scripts add only whoever ran them with `sudo`, which is `microduck` on a board
  provisioned the way upstream's own example does it, so the account has to be checked rather than
  assumed. Step 1 of the checklist checks for the group with `ssh microduck@<duck> id -nG` and marks
  adding the account as a change to make only with the owner's yes, and the README's real-robot row,
  the Microduck page's sockets row, the status page and the issue template say so too. Step 6 starts
  with `mediad`'s `GET /frame` and keeps WebRTC for the stream, without the snapshot server that
  needed `mediad` stopped, step 9 runs on `/frame`, its fall note no longer leaves the recovery to
  robotd, and step 10 asks for what the re-read could not settle, the policy labels on a real duck
  and what `/frame` costs it among them.

- **The pages and strings that still described the Microduck at quackd's previous read describe it
  at daemon-v0.16.1.** The FAQ and the Open Duck page said quackd reads API v23, LAUNCH.md's draft
  to Pollen said so too and that there is no socket-level camera snapshot, PLAN.md that the read was
  API v23 and the camera WebRTC, and the `--camera-url` help and both `pyproject.toml` comments that
  WebRTC is the only camera upstream offers. Between them they now say what the read found: API v41,
  a robotd from API v23 up served, and `mediad`'s `GET /frame` beside its WebRTC track. The issue
  template asks about the handshake, posture, the camera and the duck detector as they work now, and
  `microduck-lookout` says a camera rather than a video. `docs/concepts/safety.md`, the hardware
  checklist, the Microduck page, the README and the FAQ said a gamepad out-ranks quackd, that
  upstream arbitrates who drives, and that releasing the sticks stops the robot through the deadman.
  robotd keeps one twist, the newest from any client, and ranks none above another, so a pad nobody
  touches sends a stop every 100 ms that lands between quackd's twists, letting go of sticks nobody
  was touching changes nothing, and robotd's deadman does not fire while quackd re-sends its twist
  every 100 ms. Each page says so now, and what does end a quackd walk: quackd's own `stop`, a stick
  pushed until quackd gives the walk up, the pad's sit button, and `padd` sitting the duck 1 s after
  a pad drops out. The checklist's step 8 tests the deadman with the pad switched off, since a
  connected pad's stop halts the legs first and would pass the step whether or not the deadman
  works, and has `robotctl monitor` name it. Step 9 says what a pad nobody touches may do to a walk,
  which nobody has seen, and the hardware report asks for both. `docs/concepts/learned-verbs.md`
  said a policy slot robotd could swap over its socket was not designed yet, where
  `robot.loadPolicy` has done that since API v18 and `robot.setSkill` has named a policy as a skill
  since v22, and it names the slots a robot has at daemon-v0.16.1. quackd calls neither. The Jetson
  guide no longer calls every Microduck's board a Radxa: a beta's is Seeed's RK3566 board. ADR-0011
  carries a dated note on the three rows the re-read overtook, and the 0.4 design note says its
  `GET_FRAME` ban ended with the ref. `tests/test_docs.py` fails a living page, NOTICE, a line of
  quackd's or an adapter's source, a `pyproject.toml`, an issue template, a bundled duck or an ADR
  dated since the re-read that says one of the retired things again, the pad's rank and the pad as
  the e-stop among them, and holds the pages that say what stops a walk to the deadman's 500 ms and
  `padd`'s 100 ms and 1 s as `upstream_api.py` reads them, and to a re-send shorter than the
  deadman. Each failed before this change.

- **Seven table rows that lost their last cells to a `|` show them whole.** GitHub splits a table
  row on every `|` that no backslash escapes, inside code spans too, and drops the cells past the
  header's count. The Microduck page's `robot.do` and `robot.sound` rows kept two names of each
  list, the second where what quackd uses the call for belongs, and lost the rest, and its
  `robot.mode` row did the same with its two modes and lost the cell that says quackd does not use
  it yet. The XLeRobot page's arm step clamp lost what it means, Kev's confidence row lost the
  floors quackd compares a confidence with, the README's `quackd log` row lost everything after
  `--thinking all`, and ADR-0011's `gaze` row lost its note. The `robot.mode` row names its modes
  without a pipe now, every other pipe is escaped, and the guards that look for an upstream name on
  an adapter's page read an escaped pipe as the pipe it renders as. `tests/test_docs.py` counts
  every table row's cells the way GitHub does, in every Markdown file in the repository, and failed
  on each of the six that were still split when it was written.

### Known limitations

- **A gateway or an access proxy reached with `--base-url`, through a local preset or with `--host`
  is sent nothing from `OPENAI_CUSTOM_HEADERS`, and quackd has no other way to add a header yet.**
  Those are addresses quackd was told for the run, and the rule above sends them the provider's own
  key and nothing the SDK reads for itself. A gateway in front of OpenAI is reached with
  `--llm openai` and `OPENAI_BASE_URL`, which sends it everything the SDK would. Behind any of the
  others, a gateway that takes its token as the `Authorization` works with that token as the key,
  `--api-key`, the vendor's own variable or `LOCAL_API_KEY`. One that wants a header of its own,
  Cloudflare Access's pair or `Helicone-Auth`, refuses the run, and one that passes requests on to
  OpenAI has them billed to the key's default organization and project, since neither account header
  goes there. A way to name headers for an address of your own is not in this release.

## [0.17.1] — 2026-10-07

0.17.1 corrects what quackd prints about OpenRouter ids it does not carry: `quackd list-models` and
the refusal of an OpenRouter id on its spelling said any other id with tool calling on OpenRouter's
own model list works, which quackd's own refusals contradict, and its help said `quackd list-models`
holds every id. Those are corrected, with six pages and `.env.example` that overstated what quackd
takes, and nothing quackd takes or refuses changed.

### Fixed

- **`quackd list-models` and the refusal of an OpenRouter id on its spelling no longer say every
  listed id with tool calling works.** Both said any other id with tool calling on the vendor's own
  model list works too. That was false: OpenRouter's list carries `~` aliases, its own routers and
  `:batch` entries with tool calling, and quackd refuses each on its spelling, and the list check
  refuses an entry whose expiry date has come. Nor had anything shown that the rest work, since no
  OpenRouter model has answered a real quackd request. The note `quackd list-models` prints for
  OpenRouter says an id quackd does not carry can be named, that a few shapes of id are refused on
  their spelling alone, and that any other is checked when a run starts against OpenRouter's public
  model list, which has to carry it with tool calling. The refusal still names the rows quackd
  carries and points at `quackd list-models --llm openrouter`, and says nothing else about other
  ids. The help for `quackd list-models` said the command lists the ids each cloud vendor accepts,
  the help for `--llm` on `quackd run` and `quackd record` that `quackd list-models` prints every
  id, and `quackd doctor` titled its providers table `providers (every model id: quackd
  list-models)`. All three now speak of the ids quackd carries. What quackd takes and refuses is as
  it was, and `tests/test_cli.py` and `tests/test_catalogue.py` hold the note and the end of the
  refusal to their new words.

### Documentation

- **Six pages and `.env.example` no longer say quackd takes or checks OpenRouter ids it refuses.**
  The README, the FAQ, the OpenRouter guide, the SO-101's first run, the web demo's README and
  `.env.example` said quackd takes any other id OpenRouter's list carries with tool calling, in
  those words or close to them. The architecture page said any other id OpenRouter lists is checked
  against that list, and the FAQ that a well-formed id quackd does not carry is looked up in it. The
  shapes quackd refuses on their spelling contradict all three. Each of those sentences is
  corrected, and the README's Model row no longer says `quackd list-models` prints them all.
  LAUNCH.md's 0.17 sentence makes the same claim and waits for 0.18, since a patch leaves the story
  alone. The guide quotes the note and the refusal as they read now, and the FAQ the refusal.

## [0.17.0] — 2026-10-07

This release adds OpenRouter, a router that puts models made by many vendors behind one key and one
bill, as quackd's twelfth cloud vendor. `--llm openrouter` asks `openai/gpt-6-sol`, one of six rows
quackd carries for it. Other ids OpenRouter's public model list carries with tool calling are taken
too, each checked against that list before the first paid call and priced off it. A turn OpenRouter
bills is costed at that bill, and its `llm` record says so, though a rate named with `--price` still
wins.

No OpenRouter model has answered a real quackd request. The chat requests quackd sent went only to
stand-ins, among them one on 127.0.0.1 that the real `openai` SDK reached in a real `quackd run` and
the browser demo's client reached under Node. Only the model list check reached OpenRouter itself,
with no key. Whether OpenRouter's Chat Completions gives the default row its function tools, and
whether a provider behind it takes the reasoning quackd replays, is unproven.
[docs/guides/openrouter.md](docs/guides/openrouter.md) lists what was read and what was assumed.

The rest is documentation. `docs/` is sorted into guides, reference, concepts and adapters, so a
link to a moved page from the PyPI pages of 0.16.1 and earlier returns 404. Pages that said how
cloud vendors are asked to call a tool, when the catalogue's rates were checked and what a System
One server answers are corrected, and the two issue templates that list vendors name every one. A
test of the arm's simulator no longer races its own pick, Dependabot leaves hatchling alone, and
`uv.lock` takes Dependabot's updates from #32 and #33, which move no requirement a user installs
against. No code that drives a body changed, and the arm has not run quackd since 2026-09-23, so the
bench steps 0.14.0 and 0.15.0 owe are still owed.

### Added

- **OpenRouter, a twelfth cloud vendor: `--llm openrouter`.** OpenRouter is a router, one key and
  one bill for models made by many vendors, and it speaks OpenAI's API, so it is one more
  `OpenAIProvider` subclass, reading `OPENROUTER_API_KEY` and installed as `quackd[openrouter]`,
  the same `openai` wheel as the nine other vendors that speak OpenAI's API. Its catalogue tuple
  is six rows, the first the default: `openai/gpt-6-sol`, `openai/gpt-6-luna`,
  `anthropic/claude-sonnet-5.5`, `anthropic/claude-opus-5.5`, `google/gemini-3.8-flash` and
  `x-ai/grok-4.7`, priced off OpenRouter's own model list on 2026-10-06 and dated with that day
  rather than the catalogue's, so `quackd list-models` prints 123 ids where it printed 117.
  Unlike every other vendor's, that tuple is a selection. Other ids OpenRouter's public model list
  carries with tool calling are taken too, each checked against the list, which needs no key, once
  per process before the first paid call, and priced and given the camera frame or not by its entry
  there. The check refuses an id the list lacks, one it lists without tool calling, and one it lists
  as expiring on or before the day it is read. An id is refused on its spelling before a key is
  read, even where OpenRouter lists it, when its model or price is not fixed before the call or Chat
  Completions does not serve it: a `~` alias, one of OpenRouter's own routers, a `:batch` entry or a
  routing variant (`:nitro`, `:floor`, `:exacto`). So are `:thinking` and `:online`, which
  OpenRouter has replaced, and `:extended`, which it says no model offers. `:free` is the one suffix
  taken, and a `:free` model is limited by OpenRouter to 20 requests a minute and 50 a day until 10
  credits have been bought, 1,000 a day after. Every request carries `provider:
  {"require_parameters": true}`, so OpenRouter routes only to a provider that honours what was sent,
  carries no `parallel_tool_calls`, and goes to Chat Completions only. The rows are told to call a
  tool, except the two Claude rows, which refuse a forced call and are asked with `auto`. Any id
  quackd does not carry is asked with `auto` too, or sent no `tool_choice` at all where its entry on
  the list names none. A model's `reasoning_details` go back unmodified on the turn that made the
  call. Requests also carry `HTTP-Referer` and `X-OpenRouter-Title` headers naming quackd, which
  credit it on OpenRouter's public app rankings and say nothing about the person running it. The
  browser demo offers the six rows. The honest part: no OpenRouter model has answered a real quackd
  request. What ran was `openai` 3.22.1 through a real `quackd run` against a stand-in on 127.0.0.1
  (`tests/fake_openrouter.py`), the browser demo's client under Node against the same stand-in, and
  the model list check against OpenRouter's real list, with no key. None of it proves that
  OpenRouter answers a turn, that a provider behind it takes the replayed reasoning, or that the
  default row takes function tools through Chat Completions.
  [docs/guides/openrouter.md](docs/guides/openrouter.md) has what was read and what was assumed, and
  [ADR-0050](docs/adr/0050-a-router-is-a-vendor-whose-list-is-read-on-the-day.md) why.
- **A call can be costed at what the vendor billed.** OpenRouter says in every response what the
  call was billed, `usage.cost`, which already knows which provider served it, at which tier and
  with what cache. That is now the turn's `cost_usd`, with the upstream charge added on a
  bring-your-own-key call, and the `llm` record says `billed: true`, written only when it is.
  `summary.json` counts such calls in `billed_calls`, written only when there are any. A rate
  named with `--price` or `QUACKD_PRICE` still wins over the bill, a call that brings no bill is
  costed at the rate as before, a call that ends in an error is costed the same way when OpenRouter
  reports what it billed or used, and every other vendor's records are what they were. One thing
  changes for a run with no rate at all: on a vendor that bills, its total starts at $0 and
  turns null from the first call that brings neither a bill nor a rate, where an unpriced run
  used to be null from its start.

### Documentation

- **`docs/guides/local-llms.md` no longer says every cloud vendor is sent `required` and
  `parallel_tool_calls=False`.** Mistral is asked with `any`, Qwen, Kimi, GLM and Meta with
  `auto` and Cohere with no `tool_choice` at all, and only OpenAI, Grok, Mistral and Meta are sent
  `parallel_tool_calls`. The line now says so, and how OpenRouter is asked.
- **The architecture page dates the catalogue's rates as the code does.** It said they were
  checked on 2026-09-21. `PRICES_CHECKED` has been 2026-09-23 since 0.13.0.
- **The bug and duck-submission issue templates name every vendor.** Their provider lists
  stopped at Grok, so Mistral, DeepSeek, Cohere, Qwen, Kimi, GLM, Meta and now OpenRouter could
  not be picked.
- **Dependabot ignores every hatchling version.** 0.16.1 ignored only hatchling 1.32.4 and
  said the next release would still come in the group. On 2026-09-29 Dependabot's uv job tried
  1.32.3 instead and stopped the same way, with No files have changed! before it ran uv, so the
  job was still red. hatchling is the build backend, which that updater cannot move, and
  `.github/dependabot.yml` now ignores it whole. A person moves it with
  `uv lock --upgrade-package hatchling`. The five packages 0.16.1 held back no longer fail the
  job. The configuration ships in no package, so this needs no release.
- **uv.lock takes websockets 16.1.1, from Dependabot's #32.** The ignores above hold websockets
  below 17 for google-genai, and 16.1.1 is the update that let through. google-genai,
  mujoco and the Microduck's WebRTC camera use it, and the camera's call,
  `websockets.asyncio.client.connect`, is the API 16 keeps. Every CI job passed on it, and a
  round trip through that call and an import of google-genai's live client both ran on 16.1.1.
  No requirement a user installs against moved.
- **docs/ is sorted by what a reader came to do.** Twenty six pages sat side by side in it, a
  guide beside a spec beside one robot's hardware checklist. All but the FAQ now sit in
  `guides/`, `reference/`, `concepts/` and `adapters/`, where each robot has a folder of its own:
  its page, its hardware checklist where it has one, and for the SO-101 its first run.
  `docs/README.md` maps them, and a test fails while a page in any of those four folders is
  missing from the map. The Microduck's tables leave `adapter-status.md` for a page of their
  own, the last body to get one, so the status page, now `docs/adapters/status.md`, holds status
  alone. The short copy of the arm simulator's upstream table it carried is gone: the arm's page
  has a row for every name in it, under a stricter test, and now also says the four things only
  the copy said. Every link and path in the docs, the code, the tests, the starter task files,
  the adapters' `Documentation` URLs and the issue templates names the new places, and so do the
  links in earlier release notes, PLAN.md, the ADRs and the design notes, whose words are left as
  they were. One kind of link cannot follow. The README on PyPI links `docs/` on `main`, and a
  release's description there never changes, so on the PyPI pages of 0.16.1 and every release
  before it a link to a moved page now returns 404, and so does each adapter's `Documentation`
  link. The project pages are right again from 0.17.0.
- **A test of the arm's simulator raced its own pick.**
  `test_a_pick_on_the_simulator_ends_on_its_time_or_on_a_stop_from_another_task` sends a stop
  from a second task ten ticks of the simulator's clock into a `pick`, to show that a stop ends
  a running segment. It counted those ticks from the `do`. The clock runs for the one sleeper
  while a segment's start awaits the arm and the runner on the wall's time, so on the physics
  job's runner for Dependabot's #33 the ten ticks passed before the start was over. The stop
  landed in the start, which refuses the `do`, as it should, and the assertion that the pick
  was stopped failed. The stop now counts from the policy's first goal, which only a running
  segment asks for. Holding the policy loop's start back half a second of the wall's
  reproduces the failure without the change, and with it the test passes five times of five.
  Nothing quackd does changed.
- **A System One answer names its kind, which the page for writing a server left out.**
  `docs/guides/decision-llms/local.md` sets out the response a server has to send, and gave a
  choice as `{choice, confidence}` and a noul as `{noul}`. typesafe_sdk tells the kinds of answer
  apart by a `type` field, so a server written from that row fails the SDK's validation on every
  turn, and quackd hands each of those turns to the model. Writing a server from the row, to
  check #33, found it. The row now gives `type` in each shape and says it is required.
- **uv.lock takes five updates from Dependabot's #33:** anthropic 1.9.0, openai 3.22.1,
  typesafe-sdk 0.7.2, laya 0.3.22 and zeroconf 0.151.5. CI installs none of the five, so its
  green run says the lock resolves and no more, and each was run here on the lock instead. A real
  `quackd run` drove anthropic against a stand-in for the Messages API on 127.0.0.1, which got
  the beta endpoint, both of quackd's betas, `fallbacks` and `block_binding`, and whose tool call
  came back as the run's success. A live `hello-world` on `gpt-6-sol`, held to three steps, made
  four calls on openai and read every tool call back, for $0.0157. typesafe-sdk 0.7.2's code is
  0.7.1's byte for byte, adding only an optional `http2` extra, and a shadow run against a System
  One server on 127.0.0.1 recorded a decision on every turn. laya's `Router` and `predict` take
  quackd's arguments as before and everything new in them is optional; its weights were not
  loaded. `quackd announce` and `quackd discover` found each other on zeroconf 0.151.5. The
  pull request's first CI run failed in the physics job, on the test race above, which none of
  the five reaches. No requirement a user installs against moved.

## [0.16.1] — 2026-09-29

0.16.1 is quackd's first patch: it carries fixes, not yet run on the arm, for five faults that
reruns of 0.16.0 on the arm's twin found on 2026-09-29, among them a policy latency its chunks
cannot carry, which `quackd policy serve` and the arm's connect now refuse, and it starts every
window between the eight packages at the release it ships in. The rest is documentation and a
refresh of `uv.lock`: bench steps for 0.15.0's arm changes that nobody has taken yet, two grasp
sidecars, the pages those reruns corrected, a README with no logo, and RELEASING.md, which says
what a version promises and how one ships.

### Fixed

- **A policy served with a latency past half its chunk starved the arm every chunk, and
  `quackd policy serve` took it.** A segment asks for the next chunk only once the last has
  landed, one request out at most, so a chunk that lands some ticks after it was asked for has
  that many fewer ticks of it left to play while the next is on its way. Past half a chunk the
  arm had nothing to play for part of every chunk, twice the latency less the chunk, and no rule
  for when to ask again can close that gap with one request out. The trained ACT, 100 actions at
  30 Hz, starved 86 of 300 ticks in every 10 s segment on the arm's twin served with
  `--latency-s 1.78`, and served with 2.5 its segment ended after 4.4 s, while
  `quackd policy check --bench` said the latency covered what it timed. `serve` and `check` now
  refuse a latency past half a chunk, as they refused one as long as a chunk, with the ticks of
  every chunk it would starve and the longest latency they would take: serve the policy with
  that or less, or where it answers faster. A server an earlier quackd started with such a
  latency still declares it, so `check` of one says so in its latency row, its bench says so
  rather than that the latency covers what it timed, and the arm refuses to connect to it,
  before any torque, with the same ticks and the longest latency to start it again with. Nothing
  overrides that refusal. The next chunk is still asked for once what is queued is down to half
  the last chunk, so any answer within half a chunk lands with something queued whatever latency
  was declared, and now sooner where twice the declared latency in ticks is more than that,
  which gives a step that runs past a longer latency what room the chunk has, up to twice that
  latency where a chunk holds three. The bench paces by the same rule, and where the latency
  the server declares covers what it timed but the slowest step took longer than half a chunk,
  it says a step that slow can leave the arm holding still.
- **The line for a verdict nobody was asked about said `(the human said go)`.** `--yes`, a
  flock's standing answer and a pipe on stdin answer the pilot's doubt without asking anybody,
  and the `assess` line said a human had, in a run whose own observation told the pilot nobody
  was asked. The event now names what answered as `answered_by`: `a person` only where a
  `prompt` row says one was asked, and otherwise `--yes`, `a flock's standing answer`, `a pipe`
  or `a standing answer`. The line says that, `(--yes said go)`, and a run recorded before reads
  `(answered go, and the record does not say by whom)`. A doubt answered no ends the run naming
  the same. A prompt that raised, at the end of its input or with click's `Abort`, answered
  nothing, yet the line and the run's reason said the human said no, and a person's prompt left
  a `prompt` row saying they had. The event now carries `raised` in place of `answered_by`, the
  line says `(the question went unanswered: the prompt raised EOFError)`, and no `prompt` row is
  written. `human` is still the gate's state, which is why ADR-0042 left it alone.
- **A segment could play one tick past its time.** The tick a segment's time runs out on is due
  at its end, and where the simulator's clock read that tick a hair short of the end, as the
  difference of two of its times can, it was played: a 10 s segment at 30 Hz could play 301
  ticks, three of them were recorded as 902 ticks over 30.08 s, and the run's box said 30.1 s.
  The loop now reads a segment's end within the same tolerance it reads a tick as due, so a
  segment plays exactly the ticks its seconds hold, and what it says it played is what the
  run's record adds up.
- **`place: jaws` laid a cube a close would never touch.** From the lab arm's fold with the
  gripper open, the fixed finger stood at the cube's side and the connect laid it out, but
  closing the gripper stopped the moving finger clear of the cube's top, and the refusal for
  jaws shut had advised exactly that start. The connect now closes the gripper on a copy of the
  physics, with the object alone on the table, and refuses where the moving finger never touches
  it, saying how near it came:
  `closing the gripper stops its moving finger 0.4 mm clear of cube, which it never touches on the way`.
  Each refusal of the arm's start for an object between the jaws now says the start that works,
  a rest pose whose open jaws point down at the table around the object.
- **`quackd doctor`'s `joint_range_deg` row printed each joint's travel rounded to whole
  degrees**, so an end could read up to half a degree wider than the travel the pilot is told
  and the arm takes a goal at. It prints the travel as the pilot is told it, to a tenth of a
  degree rounded inward. The advice after the table, for a rest pose past a joint's travel,
  still names the end that joint parks at to the nearest whole degree.
- **An adapter from a patch would have installed beside a core from before it.** Every window
  between the eight packages admitted its whole minor, `quackd>=0.16,<0.17`, and
  `scripts/set_version.py` kept it for a patch, so `quackd-lerobot` 0.16.1 would have installed
  beside `quackd` 0.16.0, without the core's half of any fix that needed both. Every window now
  starts at the release it ships in: `quackd-lerobot` 0.16.1 needs `quackd` 0.16.1 or a later
  0.16, and each extra of `quackd` 0.16.1 installs its adapter from 0.16.1 or a later 0.16. An
  installer that upgrades an adapter upgrades the core with it, so this asks nothing of you, and
  `quackd` 0.16.1 upgraded on its own still installs beside an adapter from 0.16.0, whose window
  admits it.

### Documentation

- **RELEASING.md says when a release is a patch, a minor or a major, and how one ships.**
  Sixteen releases were minors whatever they held. A patch now changes nothing a user has to act
  on and adds nothing to learn, a minor is everything else, and 1.0.0 waits for four things the
  file names ([ADR-0049](docs/adr/0049-a-version-says-what-changed.md)). This file's headings
  decide which: an entry under Added, Changed, Deprecated or Removed makes a minor, and Fixed,
  Security and the new Documentation heading alone make a patch. A fix may refuse what the
  release before accepted when that never did what quackd said it would, and a fix that tightens
  what could move a body in a way the docs never promised ships as a patch even when a script
  relied on the looser behaviour. `tests/test_docs.py` fails when a released patch carries a
  heading that makes a minor or a minor carries none, when any section uses a heading the rule
  does not name or one twice, and when a release's compare link breaks the file's pattern. The
  release checklist moved there from PLAN.md, with the order 0.15.0 and 0.16.0 were cut in, and
  CI now runs on a push to a `release/` branch too, so a patch cut from the last tag has every
  job green before its tag. A refresh of `uv.lock` goes under Documentation, as a nightly job
  does, since the lock is what a checkout and CI install and a refresh changes no requirement a
  user installs against. CONTRIBUTING.md says which heading a pull request files its entry under
  and that it never bumps a version.
- **The README opens with its name, and the duck head and the social preview card are gone.** A
  duck's head over the title made quackd look like a toy rather than a tool that drives a real
  arm, so the README carries no logo. `docs/assets/social-preview.png` and
  `docs/assets/social_preview.py`, which built the card around the same head, are deleted rather
  than redrawn, and so are the test in `tests/test_pypi_readme.py` that held the card and the
  README to one file and PLAN.md's item about uploading the card. GitHub serves its own
  generated preview for the repository rather than an uploaded one, so nothing needs taking down
  there. The browser demo keeps its icon: `web/assets/duck-mark.png` is still the mark in its
  header, with its favicon and touch icon beside it, and nothing outside `web/` shows it now.
  `tests/test_docs.py` holds that: it fails when the README has anything above its title, or
  when a living document names a file in `web/assets/`.
- **A bench step for each of the six changes 0.15.0 made in `lerobot:real` without one.**
  0.15.0's Known limitations named them and wrote a step for one other, the follower keeping
  torque through an exit that skips the close. The other six are now in
  [docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md), each in the step
  that meets the same moment and each saying what to do at the arm, what to watch for and what
  to report. A connect quackd refuses letting go of the arm is step 6, on a second name that
  nothing was calibrated as, taken before the fold. The deadline a call spends only while the
  bus is busy is step 9's dry run, given a camera because its detector still runs on the event
  loop's thread, and the line step 13's pulled cable ends on. A connect that fails once the arm
  is energised keeping its torque is step 13, which says it cannot be timed by hand and what to
  check if it ever happens. The close reading back the flag it wrote, and saying a stalled
  shortfall once, is step 14, with the fold back to the rest pose held back by hand. The
  heartbeat's reads kept out of the trace a grasp is judged by is step 17. PLAN.md's item for
  the SO-101 names them where it said nobody had written them.
- **Sidecars for the two grasp examples that start with a cube between the jaws and lift it.**
  `docs/examples/lerobot/e162/first-grasp.sim.yaml` and
  `docs/examples/lerobot/e165/place.sim.yaml` lay that cube between the open jaws, the size of
  the simulator's own since neither task names one, and check that it was lifted as the task
  says: two centimetres for `e162`, and for `e165`, whose task says a little, the least the
  simulator counts as a lift. Setting it back down is nothing a sidecar can check, and each
  says so. `e163` starts the same way and asks only whether the gripper holds the cube, which no
  sidecar check measures, so it has none. Neither sidecar lays out on the lab arm's twin.
  `arm-01`'s fold puts the jaws down at the table with the gripper shut, and `quackd preflight`
  of each on `arm-01-sim`, with `--llm fake` and `--seeds 2`, was refused at every connect:
  `the scene lays cube between the jaws, and as the arm starts they are open narrower than cube`.
  The same fold with the gripper open is refused too, since a close from there never brings the
  moving finger onto the cube (Fixed, above). The generic arm starts with its hand far above the
  table and is refused as well, and laying the cube on the table instead would rehearse another
  task, so each sidecar says at its top what it needs: an arm that starts with its open jaws
  pointing down at the table around the cube, which neither the twin nor the generic arm does.
- **`--decision-mode shadow` has run beside a policy, with a stub.** README.md's row for the
  learned policy and ADR-0048's Consequences said it had never run beside a trained checkpoint,
  and PLAN.md that no `decision_shadow` record existed outside the test suite. It ran by hand on
  the arm's twin on 2026-09-28, and on the released 0.16.0 on 2026-09-29, with `gpt-6-sol`
  piloting beside the trained ACT and a stub decision LLM loaded as a plugin. A stub that always
  picks `manipulate` chose it on every turn that offered it and ended on `gate: shadow_only` each
  time, with the model taking the turn. The pages say so now, and that no real decision LLM has
  run beside a policy.
- **The arm simulator's sweeps cite the nightly job's first run.** Dispatched on `main` on
  2026-09-29, at the commit tagged `v0.16.0`, `lerobot-sim-assets` fetched the maker's model and
  passed the grasp sweep, `lerobot-lookout`'s rehearsal and the grasp task's rehearsal ten of
  ten each ([run 36523568197](https://github.com/rokbenko/quackd/actions/runs/36523568197)).
  README.md, docs/adapter-status.md and the arm's page cited only the sweeps run by hand on
  2026-09-27.
- **ADR-0048 records one more place the code settled differently from the plan.** The plan had
  a policy asked every tick go through a delay line `k` ticks deep on the simulator. The code's
  line is a tick deep at most: such a policy is asked again only once it has answered, so one
  that declares more than a tick to answer could never keep up on the arm, and a segment refuses
  it on either clock.
- **The examples README says what rehearsing `e001` to `e005` showed.** Its one line passed all
  12 files on the lab arm's twin on 2026-09-29, 24 runs for $1.06 of `gpt-6-sol`, and the page
  says a pass is the code surviving the file: the simulator's table holds a red cube and a pen,
  so the drawing tasks found the pen lying on the table and no paper, and declared failure,
  while the switch task's pilots said they saw a switch the table does not have and looked
  until their steps ran out.
- **What `quackd robot twin` prints for an arm with a rest pose and no pilot or camera**, as
  the lab's `arm-01` is, is in the first-run guide and docs/registry.md beside the example that
  copies all three.
- **The examples README's rehearsal line rehearsed `e001/duck-picture` without its picture.**
  `--image` would go to every file the pattern matches, so the page says to rehearse that one on
  its own with the `--image` in its header.
- **The first-run guide quoted two of the three lines `quackd doctor --robot arm-01-sim` prints
  after its table for the lab arm's twin.** The third, the advice for a fold past what the
  calibration lets `shoulder_lift` be driven to, is quoted with them.
- **`uv.lock` takes Dependabot's grouped update, and the weekly job that made it skips what it
  cannot move.** The lock now holds anthropic 1.8.0, openai 3.19.2, google-genai 2.25.0,
  typesafe-sdk 0.7.1, laya 0.3.20, ruff 0.16.9 and mujoco 3.14.0 (#31). The lock is what a
  checkout and CI install, and a wheel's requirements are its `pyproject.toml`'s, so no
  requirement a user installs against moved. The job had failed every week since 2026-09-11 on
  five packages uv cannot move in every split of the lock at once, the way Dependabot asks it
  to, because something else in the lock holds each back: numpy and opencv-python-headless by
  lerobot 0.6's pins, ultralytics by the numpy it wants on macOS against lerobot's, websockets
  by google-genai and av by aiortc. Since 2026-09-25 it had also stopped on hatchling 1.32.4,
  which uv moves and Dependabot's updater did not write. `.github/dependabot.yml` now ignores the
  versions each hold rules out, and that one hatchling release, with a comment saying why for
  each, so a release inside a hold, and hatchling's next, still comes in the group.

## [0.16.0] — 2026-09-29

This release gives the SO-101's pilot a learned policy to hand the arm to. The model plans the
subtasks and judges each from a fresh look at the arm, and between those looks a policy drives
the arm one short segment at a time, from a server of its own.

`manipulate(instruction)` is one segment of the arm's learned policy, and `pick` now runs its
policy the same way ([ADR-0048](docs/adr/0048-policies-are-the-arms-executor.md),
[docs/policies.md](docs/guides/policies.md)). The segment's loop is quackd's: paced on the arm's clock
at the policy's own rate, capped at the verbs' own speed, and judged tick by tick before each
goal goes out. The policy is not quackd's. A checkpoint is code, so it runs in
`quackd policy serve`, a server of its own that the arm reaches over HTTP, and no quackd
command loads one in the process that owns the serial bus. That server loads an ACT, a SmolVLA
or a pi05 checkpoint it has read before building, `quackd policy check` asks a server what it
serves and times it, and the arm checks at connect, before any torque, that the policy fits it.

`--policy-url` points `quackd run`, `preflight` and `serve-mcp` at a server. A `duck: 3` task
file holds `manipulate` to its own instructions and seconds, a decision LLM is shown each
subtask and never starts one, and `--controller vla` flies the arm with no model at all and asks
a person whether it did the task.

Among this release's fixes, a stop during `pick` no longer refuses its own hold. It waits for
the one call the stop cut short, and no longer than that call's own deadline, which is judged by
the bus's stamps as every call's has been since 0.15.0. And a pick cancelled as the one before
it ended no longer drives the arm anyway. No policy has driven the real arm. Every segment ran
against the test suite's fake arm or on the simulator. There a trained ACT from the Hub drove a
twin of the lab's arm through `quackd policy serve` and `quackd serve-mcp`, and again under
`quackd run --goal`, where OpenAI's `gpt-6-sol` piloted, handed it two segments and declared
failure, since the twin's table holds no blue cap. What a policy does on the simulator says
nothing about the arm, and nothing has compared the simulator against the arm. SmolVLA took 169
to 188 s a chunk on the laptop's CPU and never answered through the client, and pi05 has not
run. `--controller vla` and its judge have not run with a trained checkpoint, FLUX 3 Action has
not run anywhere, and how fast the policy loop runs on the real bus is unmeasured. And the arm
has not run quackd since 2026-09-23, so the seven bench steps 0.14.0 owes are still owed, and so
are 0.15.0's: a second Ctrl-C during the fold back to the rest pose, and the four that would
compare its simulator against the arm, for joint signs and zero offsets, the rest move pressing
the gripper into the table, the gripper on a real pen, and the front and wrist cameras'
placement and field of view. Known limitations, below, lists what only the hardware can settle.

### Added

- **`manipulate(instruction)`, one segment of the arm's learned policy.** Present beside `pick`
  whenever the arm has a policy, confirm-gated, with `pick`'s preconditions. The segment runs
  for 10 s (`MANIPULATE_S`), or what the task file's `policy.segment_s` says, unless it ends
  sooner, on its chunks played or on the arm no longer moving under it (`STALL_S`), which holds
  the arm where it stopped in case something is in its way, and the verb holds it again after,
  failing if that hold did not reach the arm. The verb is ok on those three endings and on
  nothing else, and its summary says why it ended, how long it ran, the chunks, the clips and
  the ticks a second it achieved, and never that the task is done: the pilot judges that from a
  fresh look. A starved policy, a guard, an error or a stop end it with the arm held and the
  verb failed. The mock scripts it, moving part of the way to its object and ending on its time
  unless a stop, a release, a rest move or the next segment ends it first, and refuses, in the
  arm's own words, a verb's goal sent while it runs and an instruction of blanks.
- **The policy loop (`policy/loop.py`), with a rate, a pace and a queue.** A policy is asked
  through a runner (`policy/runner.py`), and a policy object with one `act` a call is wrapped in
  a `ScriptedRunner`, so it runs as it always did, one `act` a tick at 10 Hz. The rate is the
  runner's own, from a source it names, and one that is not a finite number between 1 and 60 Hz
  refuses the segment. Tick `k` is due at the start plus `k` periods, counted from the tick's
  number, and a tick that overruns skips to the next whole period, counts what it skipped, and
  never sends twice in one period, nor before its deadline on the simulator's stepped clock. A
  chunk's goals for ticks already played are dropped as it arrives and the rest replaces the
  queue's tail. A tick with nothing to send sends nothing, and a second of that (`STARVE_S`)
  ends the segment, with five (`FIRST_CHUNK_S`) for a first chunk that can be played. Each
  segment has an epoch, and an answer from an earlier one is thrown away. On the simulator a
  runner's inference costs no sim time, and its answer is taken in and judged its declared
  latency later, where the arm would first see it, one thrown away or raising included. A
  runner asked every tick has to answer within one, and one that declares longer is refused.
- **A policy's calls have a thread of their own.** They used to share the default pool with the
  bus's calls. A policy that stops answering now holds up no read, stop or heartbeat, and a
  segment after it is refused rather than started beside it (`RESET_S`).
- **A policy segment ends on what it reads, before it sends.** Each tick of `pick`'s loop reads
  the arm and judges that reading first. A hot joint, torque off, a camera that gave no frame,
  an action that is not a finite number or names no motor of this arm, a goal held past the
  travel for a second (`CLIP_SUSTAIN_S`), three sends in a row that did not reach the arm
  (`FAILED_SENDS`) or a read the arm did not answer each stop the policy and hold the arm where
  it is, and `pick` says which. A NaN used to be clipped into the joint's floor and counted
  nowhere. A joint reading outside its travel is left out of every action, as a stop leaves it
  out, and a pick refuses to start with one more than `OUT_OF_RANGE_DEG` outside, naming its
  reading and its travel. The loop reads the joints every tick, and torque and temperature
  every 0.5 s (`REGISTER_PERIOD_S`).
- **`quackd policy serve` and `quackd policy check`: a policy in a process of its own.** A
  LeRobot checkpoint's processors can import any code their JSON names, so no quackd command
  runs one in the process that owns the arm's bus. It runs in a server you start, on port 9875 on
  loopback, and the arm's side reaches it with `RemoteRunner`, a client that needs no torch and
  no LeRobot, over a protocol of four calls (`GET /v1/policy`, `POST /v1/reset`,
  `POST /v1/step`, `POST /v1/end`). It serves a LeRobot checkpoint and two scripted policies,
  `scripted:hold` and `scripted:sweep`. `check` asks a server what it serves, or serves
  `--policy` itself for the check, and `--bench` times one warm step, then streams synthetic
  observations through the real client at the policy's rate and says the rate it achieved, the
  ticks with nothing to send, the ticks skipped while it waited for a chunk and what they would
  have been on the arm, the round trip, and the `--latency-s` to declare, read at the 95th
  percentile of every step it timed (`LATENCY_QUANTILE`) rather than off one step, and says
  whether the latency it was served with covers that. It suggests no latency `serve` would
  refuse: a policy slower than that is said to answer too slowly to drive an arm from that
  machine, with a GPU to serve it on. docs/policies.md says to bench twice,
  the second time served with that latency, and what a good second bench looks like.
- **The policy server is hardened as the Jetson host daemon is, and more.** A token is always
  required: with no `--token-file` it writes one to `~/.quackd/policy.token`, readable by its
  owner alone where the OS allows, and the client reads it there, or `--policy-token`, or
  `QUACKD_POLICY_TOKEN`. It is compared in constant time, read from a header only, and a request
  without it is answered on its headers alone. Both ends refuse a token shorter than 16
  characters or with a space or a line break inside it, and never quote it. The daemon's bounds
  on connections, headers and a request's time are copied as named constants, with a cap on a
  body besides, and a bind to anything but `127.0.0.1` or `::1` is refused without
  `--behind-tls`. Every number in a message is checked to be finite on both sides, JSON's `NaN`
  and `Infinity` included, and what a server says about itself in words is printable ASCII or
  refused, so `check` never prints an escape a server sent. A reset from a second client is
  refused while another client's session is in use, so a check run against a server an arm is
  driving through never ends the arm's segment, a reset whose reply was lost leaves the
  session to the client that asked for it, and a step for an ended session is refused. The
  client sends plain HTTP to `127.0.0.1` and `::1` and nowhere else, refuses `localhost` with a
  sentence saying to write `127.0.0.1`, follows no proxy and no redirect, keeps the token out of
  every error, holds the URL redacted, holds every reply to a deadline however slowly it
  trickles in, caps every reply, sends a request once more on a new socket when the server has
  closed the kept-alive one, drops a reply for another session or sequence, and ends its
  session when it closes. A step sent again is answered from its first answer, never inferred
  twice. A reset is answered at once while a step is still inferring, one its client gave up
  waiting for above all: the policy's own reset runs before the new session's first step, and
  the old step's chunk is dropped as it ends, never answered as a chunk. Another client's
  reset is refused only until that step has outlived its client's patience (`ABANDONED_S`),
  since nobody waits for it after that. Stopping the server refuses every request after it,
  waits a second at most for a step still inferring (`CLOSE_WAIT_S`), and closes a checkpoint's
  runner without waiting for torch, which begins no session and answers no step after it. A
  declared `--latency-s` of 5 s or more, or as long as a chunk takes to play, is refused, since
  no chunk would ever play under it.
- **The policy server loads a LeRobot checkpoint.** `quackd policy serve --policy
  OWNER/NAME@REVISION` serves an ACT, a SmolVLA or a pi05 checkpoint through LeRobot's own
  loop (`policy/pipeline.py`): the arm's reading through `build_inference_frame` and the
  pre-processor, `predict_action_chunk` cut to `n_action_steps`, the post-processor over the
  whole chunk, and `make_robot_action` a row at a time, each row a goal per motor named by the
  bus's own motors in the bus's order. One lock holds the three together and every session's
  reset resets the policy and both processors. The rate is `--fps`, or else the fps of the
  dataset its `train_config.json` names, at the whole commit or the tag it names, or the server
  refuses to start. A branch is refused there, since the checkpoint chose it and it moves, and
  so is a tag while `HF_HUB_OFFLINE` is set, when the Hub cannot say it is one. It loads on the
  device LeRobot picks, gives torch one thread fewer than it would take unless `--threads` says,
  and names each camera's image by the camera's own name unless `--cameras` maps it. An ACT that
  ensembles its chunks over time is asked every tick, and is refused without a GPU. A pi05 that
  learned relative actions is served in chunks, the whole chunk made absolute against the state
  it was predicted from, as its training made it relative, where LeRobot's own loop makes each
  one absolute against the state of the tick it is played at. CI runs a tiny random ACT
  through it, and a trained ACT from the Hub ran through it on a laptop's CPU. SmolVLA loaded
  there and took minutes a chunk, and pi05 has not run.
- **A checkpoint is read before it is built, and refused rather than guessed at.** Its
  `config.json` and both processor JSONs are fetched first, at the revision named, and nothing
  else. A policy type other than those three, a feature an arm cannot give, a processor step
  named by a `class` key, and a step outside the registry names ACT's, SmolVLA's and pi05's
  processors use are all refused before a byte of weights is fetched, since a step named by
  class is imported from wherever it says. A step that could trust a repository's own code is
  told not to. A model the checkpoint names inside itself, SmolVLA's backbone or a tokenizer,
  is refused unless `--pin REPO@REVISION` fixes its revision at a whole commit or a tag, never a
  branch, and is fetched with no `.py` and no pickle in what comes down. Its directory in the
  Hub's cache is refused if it holds any other kind of file, left there by an earlier fetch, or
  a config that maps a class to code (`auto_map`). A SmolVLA whose config names no backbone is
  refused too, since LeRobot would fill in a default of its own at no revision. An ACT's
  ImageNet backbone is never fetched. Every weight in `model.safetensors` is loaded or the
  server refuses to start: LeRobot's loader only logs a missing one and pi05's hands back a
  random network when its weights do not load, so the one is asked to be strict and the other's
  are loaded by quackd. Every repository loaded, with its revision, is in `/v1/policy`, which
  `serve` and `check` print.
- **The arm checks at connect that the policy fits it, before any torque.** Once its cameras
  are open and before its motors are energised, the arm asks the server what it serves and
  refuses the connect, with the arm untouched, over a policy whose state or action is not as
  long as the bus has motors, whose action names are not the bus's motors in the bus's order,
  that looks at an image no camera is mapped to (a SmolVLA or a pi05 runs with it padded, and
  the record says so), that learned at another frame size than a camera gives, or whose
  learned state's 1st and 99th percentiles lie outside this arm's calibrated travel. Every
  limit is read: the motors from the bus, each size from a frame, the travel from the
  calibration file, and the slack from the backend's own. A frame of another size is refused
  with the size to give the camera with `--camera-url`'s `width=` and `height=`, and can be
  accepted from Python with `RemoteRunner`'s `accept_frame_size`. The other frame is
  `--accept-other-frame`, below. The record says when either was taken. What the policy is and
  every repository the server loaded go into the run's record. The check is made again as every
  segment starts, so a server started again with another policy since the connect starts no
  segment, and says to connect again.
- **`quackd[lerobot-vla]`, the policy server's extra.** It is `quackd-lerobot[vla]`, which is
  `lerobot[smolvla]` on Python 3.12 and no `[feetech]`, since the server never opens a serial
  port. `quackd doctor` has a row for it, read from the installer's metadata. On Python 3.12 the
  lock now resolves transformers inside LeRobot's window for every extra that pulls it.
- **CI's `policy` job loads a checkpoint.** On Python 3.12, with torch from its CPU wheel index
  and LeRobot 0.6.1, it builds a tiny random ACT with no pretrained backbone and saves it
  naming LeRobot's default one, puts it in a Hub cache of its own at a commit and a tag, and
  serves it with the Hub and torchvision offline through the real server to the real client
  and an arm behind it (`tests/test_policy_pipeline.py`), so a backbone fetched fails it. Every
  action of a chunk is checked, in order, against what LeRobot's own `select_action` plays
  from the same files, and the same files with weights that are not the model's are refused.
  It fails rather than skips when torch did not install.
- **`--policy-url` and `--policy-token` point the arm at a policy server.** On `quackd run`,
  `quackd preflight` and `quackd serve-mcp`. `lerobot:real` and `lerobot:mujoco` build the
  server's client from them (`RemoteRunner`) and take it as their policy, so `pick` and
  `manipulate` are in the arm's static manifest and a task that allows them is judged before
  anything connects. Only the flag names the address: no variable and no registered robot does,
  so a policy drives the arm only on a command that says so. The token is the flag, then
  `QUACKD_POLICY_TOKEN`, then the file the server wrote. The server is asked what it serves
  before anything connects, and the run header names it, redacted, and its checkpoint. The
  mock, which runs its own script, refuses one, and so does a flock and every other body, in
  words naming the arm: `make_adapter` and `describe` pass `policy=` only when one was given,
  and refuse an adapter whose `make()` or `describe()` has no parameter by that name rather
  than handing it a keyword it would drop or raise on. Over MCP both verbs need `--yes`. A
  task refused on the arm for allowing either verb without the flag says to start a server and
  give the command `--policy-url`, and an address redaction cannot read is refused without
  being quoted, since it may hold a password.
- **`--accept-other-frame` lets a policy learned on an arm calibrated another way connect.** On
  `quackd run`, `quackd preflight` and `quackd serve-mcp`, beside `--policy-url`, and refused
  without it. The connect refuses a policy whose learned state's percentiles lie outside this
  arm's calibrated travel, and on the lab arm's calibration it refused 55 of the 68 servable
  SO-100 and SO-101 ACT checkpoints on the Hub, every one on `shoulder_lift`, whose recorded
  travel there does not reach the arm's fold while theirs did. The refusal names the flag.
  Every goal such a policy answers is still clipped to this arm's travel, so the flag changes
  what drives the arm and never where the arm may go, which its help, docs/safety.md and
  ADR-0048 say. `run_start` and the `policy` block of `summary.json` carry `accept_other_frame`.
- **A `--goal` run with a policy server allows `manipulate`, behind a confirm.** A goal's
  contract allows safe verbs alone, and `manipulate` is not one, so a goal could never have
  used a policy. With `--policy-url` it is allowed and listed under `confirm`, so a person at a
  terminal is asked before each segment unless `--yes`, or a pipe or file on stdin, answers for
  them. Answered that way, a segment starts without anybody being asked, as any gated verb
  does, and `--controller vla` refuses both. Without a policy server a goal is what it was.
- **The pilot is told what executes.** A run whose verbs include `manipulate` has a
  `Your executor` section in its system prompt: one short subtask per call, a fresh look after
  each, and never success on the verb's ok alone, since it says only that the segment ran. The
  fresh look is the frame the next observation brings when the arm has a camera and the pilot
  can see, and a reading of the arm otherwise.
- **`duck: 3`, and a `policy` section that holds `manipulate` to the task.** A task file lists
  the instructions the arm's learned policy may be told (at most 12, the stepper's own limit,
  each one line of at most 200 characters), how long each segment runs (`segment_s`, at most
  60 s) and how long they may run in all (`total_s`, at most an hour). A `.duck` is untrusted
  input, so every bound is checked as it is parsed, `manipulate` must be allowed beside it, a
  flock duck cannot carry one, and a list refuses `pick` beside it, since `pick` tells the
  policy a target of the pilot's own. The pilot's own instruction, when a task lists none, is
  held to the same one line and the same length, and so is any target given `pick` that says
  something. One helper (`quackd.duckfile.narrow`) rebuilds `manipulate` from the arm's own
  verb every time, never from the last narrowing: its instruction becomes an inline enum of the
  list, which the executor enforces and the stepper can read (an enum of one for a list of
  one, since Gemini refuses the `const` pydantic would write), its segment reaches the backend
  through `set_segment_s`, and its timeout becomes the segment plus 10 s.
  On the simulator the timeout also covers the wall time the clock stands still while the
  policy thinks, bounded from the latency the policy declares and a frame from each camera
  timed at connect, and held to ten minutes (`FROZEN_INFERENCE_MAX_S`). A rate the loop would
  not pace, or a latency that starves every segment, adds nothing. A policy that answers more
  slowly than it declares can still run the verb out of time there, and the timeout then says
  so, with the longest a request took and the latency declared, and says to bench the policy
  with `quackd policy check --bench`. The loop calls the helper
  once the allowlist is final and before the tools are built, and an MCP session calls it at
  connect and on each task file it loads, before it adopts the file, so the model, the stepper
  and the executor read one verb, and a file its body's `manipulate` cannot be held to is
  refused before anything of the last task changes. The executor charges each segment, a
  `pick`'s as well as a `manipulate`'s, the seconds it said it ran, or the robot's clock across
  the call when it said none or ended with no result, cancelled or aborted mid-segment
  (`Budget.policy_s`). A `do` refused before its segment began says it ran 0 s. The executor
  refuses the next segment once `total_s` is spent, and a second while one runs (a `segment`
  gate), so concurrent MCP calls are never each checked against seconds not yet charged, and
  the last segment overruns `total_s` by its own length at most. An MCP session carries the
  seconds across every task file it loads, the first included, since a session with no task
  spends its own default total. A run with no section, a `--goal` run, a v2 file or an MCP
  session with no task, gets 10 s segments and 120 s of them, and the budget line in each
  observation names the seconds once one has run. The arm registers the verb with a 70 s
  timeout (`MANIPULATE_TIMEOUT_S`), past the longest segment a task may ask for and its
  headroom.
- **The discrete stepper is shown `manipulate` and never takes it.** With a task file's list it
  is a closed set, and which subtask comes next is exactly a between-segment choice, but it is
  confirm-gated, and under `--yes` nobody is asked at that gate, so a stepper that cleared the
  confirm floor would start a learned policy driving the arm with no person and no model
  involved. It is offered, an answer that clears every other gate ends on `gate: shadow_only`,
  the model takes the turn, and in `--decision-mode on` as in shadow a `decision_shadow` record
  sets the two answers side by side on every turn that offered one, which is the agreement rate
  promoting it would need.
- **`--controller vla`, a scripted pilot whose success a person judges.** `quackd run
  --controller vla` flies a LeRobot arm with `--policy-url` and no model at all. It answers
  `assess_task` with `uncertain`, so a person decides whether the arm should try, hands the
  policy each of the task file's `policy.instructions` in order, or the `--goal` as the only
  one, with one `manipulate` apiece through the same narrowed verb, confirm gate and budget as
  any pilot's, and then the run asks `Did the arm do it?`. The pilot declares from that answer,
  which the loop asks through `RunConfig.judge`, records as a `judge` prompt and hands back on
  the next observation, and only a yes from a person really asked is a success: a no, a pipe
  on stdin or nobody there is a failure, and the loop holds any pilot that says it cannot judge
  to the same rule. A prompt that ends without an answer is a failure too, and not a no: no
  `judge` row is written, and the reason says what the prompt raised. The time a person takes
  to answer is not charged to `max_minutes`. A segment that does not end ok ends the list with
  a failure in the verb's own words, and a budget that ends it early still asks about the
  segments that ran and ends on the budget. It reads the verb's result and the answer and
  nothing else, never the simulator's truth, and it costs nothing (`tokens 0+0`, `cost $0`).
  It is refused before anything connects without a policy server, with `--yes`, with no
  terminal to ask on, with `--dry-run`, beside a decision LLM, with a flag for a model or a
  picture it would ignore, for a task file that lists no instruction and for a `--goal` that
  is not one short line. `serve-mcp` refuses `--controller` in words, since over MCP the
  client is the pilot.
- **A run's record keeps what its policy did.** `run_start` names the server and the
  checkpoint, and `summary.json` has a `policy` block, beside the stepper's `decision` block
  and by the same rule, only when there was one: the server, the policy, its rate and where
  that came from, every repository the server loaded at its revision, the JPEG quality, the
  segments, their seconds on the arm's clock and on the wall's, ticks, late ticks, chunks,
  starved ticks and clipped goals, the rate the ticks were achieved at, and the mean and
  longest round trip. On the simulator the arm's clock is the simulator's own, which the block
  says (`clock: sim`). The time counter under the verdict gains the policy's wall seconds, and
  a `policy` counter says the rest. The counts come from the policy loop (`PolicyLoop.tally`),
  a segment stopped from outside included, and no action goes into a verb's result or the
  record.
- **`extras.timing`, how long the bus and a policy's ticks take.** The arm's state carries a
  count, a median, a 99th percentile and the longest for every bus call, the wait for the bus
  included, and for every tick of a policy segment, measured on `perf_counter`. A pick's
  result carries it too. Nothing acts on it: it is for a bench session to measure.
- **[docs/policies.md](docs/guides/policies.md): a policy on the laptop or on a rented GPU.** How to
  check a checkpoint and serve it in a second terminal, how to reach a server on a rented GPU
  through `ssh -L` and hand the laptop its token, which policies there are and what each needs,
  SmolVLA's pinned backbone and Pi0.5's gated tokenizer among them, their licences in one
  table, and why FLUX 3 Action is not served. Recording the 50 to 200 episodes a policy for your
  own task learns from is LeRobot's own tools, documented and not wrapped, and a checkpoint is
  code, which the page says what to do about.
  [ADR-0048](docs/adr/0048-policies-are-the-arms-executor.md) records why a policy is the arm's
  executor while quackd keeps the bus, and six earlier ADRs carry a note pointing to it. The
  arm's first-run guide gains a section on handing the arm to a policy, rehearsed on its twin,
  and step 18 of its hardware checklist is how to bring one up on the arm and the number to
  measure there. `docs/licenses.md` and `NOTICE` say that no policy's weights ship with quackd,
  and `.gitignore` keeps `*.safetensors` out of a commit.

### Changed

- **LeRobot's policy names have a table of their own, read at 0.6.1.** They moved out of the
  arm's own refs, which are pinned to a `main` commit, into `policy/upstream_api.py`, read
  against the `v0.6.1` tag, the version the laptop that drives the arm runs, with the async
  inference facts that are why quackd's policy server is its own. `quackd doctor` prints its pin
  as `LeRobot policies`, and the arm's page has the table. `POLICY_PIPELINE` is VERIFIED for
  what CI's `policy` job runs, ACT on the CPU, and `VLA_PIPELINE`, `TICK_MODE` and
  `LOAD_POLICY` say what has not run.
- **A policy's step cap is the verbs' speed at the policy's rate.** Per send it is
  `max_step_deg / TICK_S` over the rate, and never more than one verb step, so
  `QUACKD_LEROBOT_MAX_STEP_DEG` governs both, and a policy faster than the verbs' tick takes a
  smaller step rather than moving faster. It is written on the follower as the segment starts
  and the verbs' step put back as it ends, however it ends, once any send still on the wire is
  back, cancelled or run out of its time, and a stop, a rest move or a verb's goal writes it
  again where a segment's cap may still be on the follower.
- **`pick` stops its policy the moment something is held.** It used to return and leave the
  policy driving the arm through the pilot's thinking, so the next arm verb was refused once
  as `pick is running`. `holding` is now judged on the policy loop's own reads, each tick,
  and the segment ends there. The verb waits for the segment rather than polling on the
  arm's clock, so on the simulator it no longer takes part in time while the loop runs.
- **A stop from elsewhere ends `pick` as stopped, and says what stopped it.** The heartbeat's
  stop, an MCP `stop` or a verb refused while the pick ran used to show up as a pick that
  ended without a grasp, followed by a stop of its own. It is now `stopped:` with the stop
  named, and no second stop follows it. A stop that lands while a pick is still starting,
  before its policy has been asked for anything, keeps the policy from starting, and the pick
  is refused with the stop named. So does a stop still under way when the pick begins, such as
  the heartbeat's landing while the executor reads the arm before the pick. It used to hold
  the arm and return, with the policy started behind it.
- **The arm's wall clock is `perf_counter`, and its sleeps never end early.** `time.monotonic`
  ticks every 15.6 ms on Windows before Python 3.13 (gh-88494), and the event loop's timer
  wakes a sleep up to one of those ticks early. Every sleep now finishes its last millisecond
  on `perf_counter`.

### Fixed

- **`quackd serve-mcp` printed a traceback when the connect refused.** A refusal raised as the
  server starts, an arm that cannot reach its rest pose among them, arrives inside the task
  group the MCP SDK serves in, and only a bare refusal was caught, so the sentence was buried in
  a traceback on stderr and the client saw a closed connection. It is now said as `quackd run`
  says it, a policy that does not fit the arm included, and the server exits 1. Anything else in
  the group is still a traceback.
- **`quackd doctor`'s header ended in `duck-ipc-proto API v` with nothing after it** on a
  machine without the Microduck's adapter, which is where that number comes from. The header
  leaves it out there.
- **A plain `quackd validate` checked a task file against the Microduck's verbs.** With no
  robot named, a file is meant to be checked against every body installed here, and
  `installed_vocabulary()` has built that union since 0.10, but the command still passed the
  Microduck's list, so an arm's task that allows `move_joints` failed with `unknown verbs`. It
  now checks the union, which also holds what each body offers a policy server, so the
  `duck: 3` example in duck-spec.md validates as the page quotes it. `validate --robot NAME`
  checks that body as it is registered, as before.
- **On Windows, input from NUL counted as a person at a terminal.** NUL is a character device,
  so `isatty` says yes to it, and a script started with its input from NUL, or from Git Bash's
  `/dev/null`, was put the questions meant for a person, answered them with end-of-input, and
  had its record name a person. quackd now asks Windows whether its input is a console as
  well, so NUL is nobody there, as a pipe already was.
- **A stop during `pick` could refuse its own hold.** A stop cancels the policy loop first, and
  a loop cancelled in the middle of a bus call leaves that call's thread on the wire, which
  quackd files as a wedge and refuses every call behind until it comes back. The hold the stop
  was about to write was the first call refused, so the stop said it could not be delivered and
  the arm kept the policy's last goal. A rest move, a release, a take-hold or a close that
  landed there was refused the same way, and a heartbeat that landed there failed and aborted
  the run over an arm that was answering. Each now waits for that one call to come back before
  it reads or writes the arm, and no longer than the call's own deadline, which is when it
  would have been refused had nothing cancelled the loop. That deadline is judged by the bus's
  own stamps, as every call's budget has been since 0.15.0, so a call the arm answered in time
  is waited for however late a busy loop marks it done. Found in the test suite, where every
  stop that landed mid call was refused. On the arm it happens as often as a stop lands while
  one of the loop's calls is on the bus.
- **A pick abandoned as it ended the one before could run anyway.** A pick waits for the
  policy loop before it to end, and an abort or a dropped MCP call that cancelled the pick
  right then was passed on to that loop and lost there. The pick went on as if nothing had
  cancelled it, and its policy drove the arm for up to its whole `max_s`. The cancellation now
  ends it.
- **A second `pick` could replay the first one's queued actions.** A LeRobot policy keeps the
  actions its last chunk predicted, and nothing cleared them between picks. The policy is now
  reset at the start of every pick, where it has a `reset()`, and `load_policy()`'s does.
- **The docs said a policy's goal past the travel is refused.** It is clipped and counted, as
  ADR-0036 decided, and the hardware checklist, the arm's page and the `LOAD_POLICY` row now
  say so.

### Known limitations

- **No learned policy has driven the arm.** Every segment, `pick`'s and `manipulate`'s, ran
  against the test suite's fake arm or on the arm's simulator, served by the scripted policies,
  by the tiny random ACT CI builds, or by one trained ACT from the Hub,
  `natsuki0000/act-so101-bluecap` at commit `82f75fe40a311026b4f7cacdea7bf14cadc44ccd`, on a
  laptop's CPU, which drove the arm's twin through two segments over `serve-mcp`. On
  2026-09-29 OpenAI's `gpt-6-sol` flew that twin with `quackd run --goal` and `--policy-url`,
  handed the same ACT two `manipulate` segments and declared failure on seeing no blue cap,
  since the simulator's table holds a red cube and a pen. That run proves the plumbing and
  nothing about the task. `--controller vla`, its judge prompt and `--decision-mode shadow`
  beside a policy have run in the test suite and never with a trained checkpoint. pi05 has not
  run (`VLA_PIPELINE`), and an ACT asked every tick needs a GPU the CI job lacks
  (`TICK_MODE`). What this release changes in `lerobot:real`, the segment's task, the step cap
  written for it and put back, the loop's own reads, the check at connect that a policy fits,
  the waits for a call a stop cut short and the wall clock on `perf_counter` among it, has run
  only there. Step 18 of
  [docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md) is the order to find
  out in on an arm.
- **SmolVLA did not run on the laptop's CPU.** On an Intel Core i5-10210U,
  `lerobot/smolvla_base` took 169 to 188 s a chunk, 474 of its 500 parameter tensors being
  bfloat16, which that CPU has no native arithmetic for, and about 18 s cast to float32, past the
  10 s the client waits for a step, so it never answered one through the client. It wants a GPU
  ([docs/policies.md](docs/guides/policies.md#smolvla-and-act)).
- **FLUX 3 Action has not run anywhere.** The server refuses its policy type. What would change
  that is a spike on a rented Linux GPU, which has not happened, showing that `quackd policy
  check --bench` holds the checkpoint's rate through the tunnel, that its actions stay anchored
  when quackd clips a goal, and that the official SO-101 checkpoint drives the simulator end to
  end. It needs LeRobot's main branch, which is not on PyPI, a NATTEN built for the machine, and
  about 32 GB of GPU memory by its makers' report. PLAN.md carries it.
- **How fast the policy loop runs on the real bus is unmeasured.** With the server inferring on
  the laptop that drives the arm, torch's threads and the bus's worker share one CPU, and only
  the bench can say what the loop achieves then. The simulator's clock is lockstep, so nothing
  timed on it is a rate, and `quackd policy check --bench` measures the server and the wire
  without an arm. A run's `summary.json` has the number in its `policy` block. PLAN.md carries
  it.
- **What a policy does on the simulator says nothing about the arm.** The simulator's frames
  are renders of quackd's own scene from default mounts, and a policy trained on a real camera's
  frames sees pictures it never saw, so it can fail there and work on the arm, or the other way
  round. A rehearsal proves the plumbing, the clips, the stops, the guards and the budgets, and
  never a rate or a grasp. The ticks a second a segment reports there are the simulator's.
- **The stepper only shadows `manipulate`**, in `--decision-mode on` as in shadow. It is offered
  each listed instruction, its answer is recorded beside the model's, and the model takes the
  turn. Promoting it needs a measured agreement rate and a decision of its own.
- **A frame of another size can be accepted only from Python**, with `RemoteRunner`'s
  `accept_frame_size`. From the command line it refuses the connect with the size to give the
  camera.
- **`load_policy()` still builds a policy in the arm's own process.** It is a Python helper in
  the arm's backend from before this release, and nothing in quackd calls it, so no command
  reaches it. A caller of its own would load a LeRobot checkpoint beside the serial bus, with
  none of the reading the server does before it builds, as the `LOAD_POLICY` row on
  [the arm's page](docs/adapters/lerobot/README.md#the-policies-upstream-lerobot-061) says. Whether to
  remove it is not decided, and PLAN.md carries it as an open item.
- **The lab arm's twin cannot start a segment where it starts.** A segment starts only with
  every joint inside its travel, and a twin starts where its rest pose puts it, settled clear of
  its table. The lab arm's fold lies past `shoulder_lift`'s travel and past the model's stop, so
  its twin starts with that joint settled just off the stop, and its first `manipulate` is
  refused until the joint is moved in. `--controller vla` only calls `manipulate`, so it cannot
  recover. The lasting fix is to calibrate the arm again folded
  ([docs/policies.md](docs/guides/policies.md#on-the-laptop-alone)).
- **A policy takes a second terminal.** The server is a process you start, on the laptop or on a
  rented GPU, and `quackd run` never spawns one. It gives torch one thread fewer than it would
  take and does not lower its own priority, and the run's record does not keep the thread count
  `/v1/policy` reports.
- **A `--controller vla` run counts its calls as the model's are counted.** A task file listing
  N instructions takes N + 2 of `max_llm_calls`, the verdict, the segments and the declare, and a
  budget below that ends the run on its budget before the declare. While it asks whether the arm
  did it, as while any question at the terminal waits, the heartbeat does not beat. The arm
  holds where its last segment left it.
- **The arm has not run quackd since 2026-09-23, so 0.14.0's and 0.15.0's bench steps are still
  owed.** Nobody has taken the seven under 0.14.0's Known limitations or any under 0.15.0's. One
  would settle 0.15.0's follower, a second Ctrl-C during the fold back to the rest pose, and
  that release names six more of its changes nobody has written a bench step for. Four would
  compare 0.15.0's simulator against the arm: joint signs and zero offsets, the rest move
  pressing the gripper into the table, the gripper on a real pen, and the front and wrist
  cameras' placement and field of view. The fifth it lists is the policy loop's rate on the real
  bus, above. Until they are taken, the twin the trained ACT drove is a model nobody has checked
  against an arm. What those releases changed in `lerobot:real` has run only in the test suite
  and on the simulator. Step 18 of
  [docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md), which hands the arm
  to a policy, comes only after every step above it. PLAN.md carries them.

## [0.15.0] — 2026-09-29

This release is two pieces of work. quackd reaches an NVIDIA Jetson from the laptop, and the
SO-101 has a simulator that runs the arm's own backend, so the task files for a trip to the arm
can be rehearsed at home first.

quackd no longer runs on an NVIDIA Jetson. It runs on the laptop, and `--host` names the board.
quackd gets four things there. The model on its GPU comes from your own model server, which a
local preset moves to the board. The board's health in `quackd doctor`, frames from a camera on
it and YOLO detections computed on its GPU come from `bridge/jetson/quackd_jetson_hostd.py`, one
small daemon quackd ships for it. 0.13.0 went the other way and put quackd in a container on the
board beside a model server. That was the wrong direction. The board on its own is already a
robot's computer and GPU, and what quackd adds is using it, and its data, from the laptop. The
arrangement the image existed for, a model server, a robot's control daemon and quackd on one
board, was also the risky one, because a model server can starve a fifty hertz control loop. So
the container, its workflow and doctor's reading of the machine it runs on are removed, which
breaks anything that built the image or read doctor's top-level `jetson` key
([ADR-0046](docs/adr/0046-the-jetson-is-reached-not-run-on.md), which supersedes
[ADR-0044](docs/adr/0044-a-jetson-is-a-host-not-a-body.md)).

`lerobot:mujoco`, the SO-101's simulator, is the arm's real backend, its own code from the
connect and its retries to the close, running over a physics model of the arm in MuJoCo: the
maker's own model from TheRobotStudio's SO-ARM100, fetched at a pinned commit and hash checked,
and never shipped. `quackd robot twin` makes one of a registered arm, on that arm's own
calibration, and `quackd preflight` rehearses task files on it seed after seed and refuses
anything that is not a simulator before building it. A twin whose rest pose puts the model into
its table or into itself, as it starts or where the close parks it, is settled clear of them
before its clock starts and says so, or is refused at connect where a settle cannot clear it,
and preflight says whether a close that missed the rest pose was refused, stalled or ran out of
time. Most of what went wrong at the bench on 2026-09-23 was in the code between the pilot and
the bus, and this is where that code now meets a task file before the arm does
([ADR-0047](docs/adr/0047-the-arms-simulator-runs-the-real-backend.md)). It rehearses the code
and the contract, not the arm. A recording of OpenAI's `gpt-6-sol` piloting it through the
README's own sentence, a wave with the arm held out, is in the README, the arm's page and its
first-run guide.

This release also fixes three faults, and a few smaller ones. An exit that skips the close, a
second Ctrl-C during the end-of-run rest move or a crash, could let LeRobot's disconnect drop
the arm, and now leaves it holding. A second task sleeping as one simulated body can no longer
be left asleep for good by the first one's cleanup. And the arm's heartbeat no longer stops a
run over an answer that came in time, which it did when the event loop's thread was busy past
the call's deadline. That is one way a heartbeat can fail, found and measured on the simulator
and never on the arm. Nothing in this release has met the hardware it was written for. This
project has run nothing on a Jetson: the daemon, the client, doctor, the board's camera and its
detector were exercised in-process against fakes. Nothing has compared the simulator against the
arm, which takes a bench step each for its joint signs and zero offsets, for whether the arm's
rest move presses the gripper into the table as its goal would in the model, for the gripper on
a real pen, for the cameras' placement and for the policy loop's rate on the real bus. And the
arm has not run quackd since 2026-09-23, so the seven bench steps 0.14.0 owes are still owed,
and nothing this release changes in `lerobot:real` has run on it either. Known limitations,
below, lists what only the hardware can settle.

### Added

- **`--host HOST[:PORT]` names a board quackd uses and never runs on.** `--robot` still names
  the body, and the flag is the same whatever the body is. It is on `quackd run`, `serve-mcp`,
  `doctor`, `robot add` and `robot edit`, with `--host-token` beside it, and its port is the
  daemon's, 9874 unless you changed it. `QUACKD_HOST` and `QUACKD_HOST_TOKEN` are the rung
  below, and `.env.example` lists them. The board is settled once, the same way for every
  command: the flag, then the host a registered robot stores, then `QUACKD_HOST`. The token
  climbs a ladder of its own, so `--host 127.0.0.1` through an ssh tunnel still carries a
  registered robot's token, and a robot's token is sent only when that robot stores a board.
  `--host` takes a machine and not a URL, which is `--base-url`'s job, and refuses anything with
  an `@` in it, because a token has a flag of its own. `--host-token` joins `--api-key` and
  `--token` among the flags whose value never reaches a run record. `run` and `serve-mcp` ask
  the daemon for its `/hello` before anything is built, and a board that does not answer refuses
  the run while nothing is powered, with a hint that says how to run without it. A refused
  token is answered with how to change the one that was sent, which for a token the robot keeps
  is `quackd robot edit NAME --host-token`. A `--host-token` typed where nothing names a board
  refuses `run` and `serve-mcp` and fails `doctor`'s host section, and `QUACKD_HOST_TOKEN` with
  no board to go to is left unread. `validate` and `list-verbs` ask no board, and say so for a
  body described without a camera when a board is named for it ([docs/jetson.md](docs/guides/jetson.md)).
- **A registered robot can keep its board.** `robots.json` gains `host` and `host_token`, set
  with `quackd robot add` and `robot edit`, and `quackd robot edit NAME --clear host` forgets
  both. A host token needs a host. `robot show` and its `--json` print the host and only whether
  a token is set (`host_token_set`), and `robot list` gains a `host` column when some robot has
  one. Both fields are left out of the file while they are empty, so a registry that never named
  a board stays readable by 0.12 to 0.14, which refuse unknown keys. One robot stored with a host
  makes the whole file unreadable to them, every robot in it included, until this release runs
  `quackd robot edit NAME --clear host` on it ([docs/registry.md](docs/guides/registry.md)).
- **The host daemon, `bridge/jetson/quackd_jetson_hostd.py`, is what answers on the board.** It
  serves HTTP on port 9874, binds loopback by default, warns when it is bound anywhere else with
  no token, and checks an optional token in the `X-Quackd-Token` header, in constant time, on
  every path. `/hello` says what started, a camera, a detector and whether the board is a Tegra,
  and why anything did not. `/healthz` is the camera's age and the detector's last call.
  `/board` is five of the board's files, three device nodes, the output of `nvpmodel -q` and one
  `tegrastats` line, as raw text for quackd to parse. `/snapshot.jpg` is the Open Duck camera
  daemon's contract, staleness and all, and `POST /detect` takes a JPEG and returns YOLO's pixel
  boxes and the device they ran on. It is written for Python 3.10, JetPack 6's system Python,
  imports nothing from quackd and has no control path. A named token file that is missing or
  empty refuses to start rather than running open. A request without the token is refused on
  its headers alone, and what any client can make the board hold is bounded: 16 connections,
  32 KB of headers, 10 seconds for a request to arrive whole and two `POST /detect` bodies at a
  time. A camera password in a pipeline, in a URL, a query parameter or a property, is shown
  as `***`. The daemon's systemd unit runs it at `Nice=10`, under
  `MemoryMax=2G` and first in line for the OOM killer (`OOMScoreAdjust=500`), because on a
  robot's own board it is the process to lose before the control loop or the model server. It
  ships in the sdist and the repository, never in the wheel
  ([bridge/jetson/README.md](bridge/jetson/README.md)).
- **`quackd doctor --host` reads the board over the network.** With a host named, by the flag,
  by the robot `--robot` names or by `QUACKD_HOST`, doctor asks the daemon for its hello, its
  health and its raw board files, and shows a section for the board: the daemon, its camera, its
  detector and the device the detector runs on, its health, and, parsed with the parsers doctor
  already had, the board's model, the L4T release and which JetPack that is, the memory it
  shares with the GPU, whether the only swap is zram, the GPU device node, the power mode and
  the GPU's load from `GR3D_FREQ` in one `tegrastats` line. The four local presets are probed at
  the host rather than on this machine. A daemon on a machine that is not a Tegra is not called
  a Jetson. A host that does not answer fails the report the way a bad `--address` does, and
  nothing the board reports ever changes the verdict.
- **`quackd doctor` asks an Ollama that answers where its models sit.** From Ollama's own
  `GET /api/ps`, for each loaded model: all on the GPU, a share of it, or on the CPU. On a Tegra
  a model on the CPU is the generic arm64 build's pitfall, and the row says that the official
  installer picks the JetPack build. None of it touches the verdict.
- **A camera on the board joins whatever body the run drives.** When the daemon has a camera,
  `run` and `serve-mcp` add the board's frames to the body before the task file is judged, so a
  camera task on a blind body is not refused for a camera the run is about to have. The board's
  frame is the primary view only for a body with no camera of its own, which then gains `camera`
  and the core verbs a camera unlocks for it: `observe` always, and `go_to`, `search_scan` and
  `approach_and` where its mobility and intents allow them. A body with a camera keeps its
  primary, because its bearings are calibrated for that lens, and the board's frame is an extra
  view named `host` that the model is shown. A frame older than two seconds is dropped, and a
  snapshot that fails costs the picture and never the run. doctor, given a robot and a host,
  lists the board's camera with its role.
- **YOLO on the board's GPU reads a real body's frames, and `--detector` chooses.** `yolo@host`
  sends the primary frame to the board as one JPEG and turns the boxes that come back into
  detections with `detections_from_boxes`, the function the in-process `YoloDetector` now uses
  too, so the same boxes are the same detections on either machine. With no `--detector`, a run
  uses it on a real body when the daemon can detect, and never on a simulator or a mock, which
  keep the colour detector they are tuned for. `--detector color|host|yolo` is on `run` and
  `serve-mcp`. `color` opts out. `host` asks for the board's by name, on a simulator too, and is
  refused before anything connects when no board is named or its daemon cannot detect, with what
  the daemon said. `yolo` is YOLO in this process, which needs `quackd[yolo]` and had no flag
  before. A detection that fails gives that frame no detections, and the run keeps the detector
  rather than switching to the colour one, which would change what `go_to` steers at with
  nothing in the record saying so. `go_to` and `search_scan` stop the body on the first frame
  the board could not read and fail with its reason, rather than take it for the target out of
  view and turn the body looking for it. `observe` puts the reason beside "nothing detected", and
  the log gets one `note` per outage rather than one per frame. The call runs in a worker
  thread, so `go_to` keeps re-sending its last twist while the board answers, for 0.3 s, and
  then sends a zero twist, which stops a body with no deadman of its own too.
  `--detector` is refused for a fleet.
- **A run names its detector.** The run header gains a `detector` row, the board's with its
  address, model and device or the colour one "on this machine", and, with a board, a `host` row
  with the daemon's version and what it has. `run_start` gains `detector` and, with a board, a
  `host` object with the role the board's camera took at connect. The log's run line names the
  detector only when it is not the colour one, so every line written before, and every simulator
  run, reads as it did. `serve-mcp`'s startup line and `robot_list` name the detector too.
  With the board's detector, each `observation` and `verb_end` record that sent the board a
  frame gains `detect`: how many it sent, how many failed, the laptop's wait for each from
  encoding it to reading the answer, and the board's own `ms` in the model, each as a mean and a
  maximum, so a `go_to`'s record says what its steering loop waited for, however it ended.
- **`lerobot:mujoco`, the SO-101's simulator, runs the arm's real backend over a physics
  model.** `LeRobotSim` is `LeRobotReal` with a simulated follower and the scene's cameras
  where LeRobot's follower and the webcams would be, and the world's clock where the wall's
  would be, so the connect and its retries, the travel read off a calibration and every
  refusal past it, the rest move, the hold, `--by-hand` and the close are the arm's own code.
  The follower plays what LeRobot and the servo do with a goal: LeRobot caps each send at the
  step, the servo clamps the goal to the calibrated travel and leaves the reading alone, and a
  limp joint drives to its last goal when torque returns. The model is
  `so101_new_calib_camera.xml` and its 15 meshes from SO-ARM100 at `5f6d2b8`, fetched on the
  first connect one file at a time, about 16 MB, each checked against a recorded sha256, into
  `~/.quackd/cache`, with `QUACKD_LEROBOT_SIM_ASSETS` pointing at a checkout of your own
  instead. The scene is quackd's: a table, lights, a cube and a pen laid out by `--seed`, and
  front, top and wrist cameras that the arm's own `opencv://` urls name with `?name=`.
  `--address` names the calibration file, a registered name reads the one LeRobot keeps under
  it, and a bare `--robot lerobot:mujoco` runs a generic arm with the model's own ranges and
  says so, rather than reading the calibration under `arm-01`, the id quackd gives an arm nobody
  named. An address shaped like a serial port is refused before anything opens it. The arm
  starts at its rest pose, limited by the model's stops, and a pose that puts the model into its
  table or into itself by more than a millimetre, as it starts or where the close parks it at the
  edge of its travel, is settled out of them before the clock starts, with a note naming each
  contact, and its close drives back to where it settled. A pose the settle cannot clear is
  refused at connect, naming what is still in. Time is lockstep and
  moves only while something sleeps on the clock, so a pilot's thinking costs none and under
  one seed the simulator does the same again. A `--by-hand` take-hold first lets the
  released arm fall for a second of sim time, since nobody places it, and then holds or refuses
  as on the desk. The pilot is told it is on a model, the run writes no GIF, and `--live` opens
  MuJoCo's viewer.
  `quackd doctor --robot` connects a registered one with or without an address and renders once,
  and it and `quackd robot release` say it is the simulator rather than asking anybody to hold an
  arm. Its ✅ rests on a seeded grasp sweep on the maker's model, judged by the world's truth,
  and it never raises `lerobot:real`'s
  ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md#the-simulator-lerobotmujoco)).
- **`quackd robot twin SOURCE [NAME]` registers a simulator of a registered arm.** NAME is
  `SOURCE-sim` unless given, its address is the calibration file SOURCE's runs read, and it
  copies SOURCE's rest pose, pilot and each camera the simulator renders, naming any it leaves
  out. Its memory is its own. It refuses a NAME that is SOURCE, a source that is not a
  registered LeRobot arm, a missing calibration file, a serial port where the file goes, and
  `--force` over anything but a `lerobot:mujoco` robot, so no arm is ever overwritten by its own
  simulator. `--robot arm-01:mujoco` does not parse, and this is the command instead
  ([docs/registry.md](docs/guides/registry.md#a-simulator-of-an-arm)).
- **`quackd preflight FILES --robot NAME --llm VENDOR[:MODEL]` rehearses task files on a
  simulator.** A robot that is not a simulator is refused before it is built, with a pointer to
  `robot twin`, and so is a pilot nobody named: the scripted one runs only when typed as
  `--llm fake`. Each file is checked as `validate` checks it, the simulator is connected and
  closed `--connect-cycles` times, and the task is run `--seeds` times, three of each unless
  you say, with memory off. A run passes when nothing escaped it, no call to the simulated bus
  was left hanging, its close ended at the rest pose (or, on a robot with none, such as a bare
  `lerobot:mujoco`, found none to return to, unless the sidecar asks `at_rest: true`), and every
  check in the task's sidecar held. A close that missed says whether its rest move was refused,
  stalled or ran out of time, and of a rest move that was made, only a refusal is what
  `at_rest: false` expects. `--faults` gives the connects a seeded bus that drops
  packets in LeRobot's own words, from rates for `handshake`, `configure`, `write`, `torque`,
  `torque_read` and `temperature_read` and a `read_loss_from=N`. It prints a row per file and
  seed, the model's cost and the simulator's time step, `--json` prints the same, and it exits
  1 unless every run passed. `FILES` may be globs, quoted, since PowerShell does not expand
  them.
- **A task's sidecar, `<task>.sim.yaml`, says what a rehearsal of it has to leave behind.** It
  lays out the table, with an object on it or between the jaws, and checks `at_rest`,
  `joint_moved`, `lifted` and `moved`, each threshold measured from the run's own start. Joint
  checks read the transcript. Object checks read the simulator's truth, latched on the way into
  the teardown's stop or at its peak before then, which is kept off the state's extras, so the
  pilot and an MCP client never see it. It is never the frontmatter, which an MCP pilot is
  handed whole ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md#the-sidecar)).
- **`quackd[lerobot-sim]` installs the simulator.** It is `quackd-lerobot[sim]`, the arm's
  package with MuJoCo and without LeRobot, so it needs no torch and installs on Python 3.11.
  `quackd doctor` gains a `lerobot-sim (mujoco)` row, and a row in its transports table saying
  whether the SO-101's model is in the cache at its pin, which it only looks at and never
  fetches.
- **A nightly job, `lerobot-sim-assets`, fetches the SO-101's model and grasps with it.** It
  fetches the pinned files the way a first connect does, into a runner that is then destroyed,
  and runs the tests marked `so101_model` asking ten seeds of ten: the seeded grasp, the
  rehearsal sweeps, the real meshes rendered and the physics timed against the wall. It is also
  the watchdog on the pin, since a file that stops arriving at its hash fails there by name. CI's
  `physics` job runs the simulator's other tests on every push, on a primitives-only stand-in arm
  that needs nothing fetched.
- **The arm's simulator has a recording, wherever the docs introduce it.**
  `docs/assets/lerobot-sim.gif` is the README hero's sentence, *wave to the camera with an
  extended arm*, on a bare `--robot lerobot:mujoco`, piloted by OpenAI's `gpt-6-sol` on
  2026-09-28: it raised the arm, held it out level and swung it side to side at the shoulder
  three times, in seven steps, nine LLM calls, 69 seconds and $0.0537. It is the first film in
  this repository of a cloud model in a simulator: the four transcripts are local models on
  `sim2d`, with no frame. A fixed view of the table sits beside the scene's front camera, the one
  the model was sent, both drawn by a tick hook on the simulator's clock, so the model's thinking
  is not in it. `docs/assets/lerobot_sim.py` records it, and flies a scripted pilot of its own
  when it is given no `--llm`. It is in the README's paragraph on rehearsing at home, the
  simulator's section of `docs/adapters/lerobot.md` and section 16 of `docs/lerobot-first-run.md`.
  It renders TheRobotStudio's model at the commit the simulator pins, Apache-2.0, and commits no
  mesh. `quackd run` still writes no GIF on this backend.

### Changed

- **A local preset's model server moves to the board.** `--llm ollama`, `vllm`, `llamacpp` and
  `lmstudio` keep their own port and path, and their `localhost` becomes the machine the host
  names. The port in `--host` is the daemon's, and it is dropped. The address ladder is now
  `--base-url`, then a host named for this run or stored with the robot, then `QUACKD_BASE_URL`,
  then `OPENAI_BASE_URL`, then `QUACKD_HOST`, then the preset's own address, where it was
  `--base-url`, then the two variables, then the preset. A URL is used as given wherever it
  comes from, and a host only ever moves a preset, so `--llm local` still needs `--base-url`,
  and with a host alone it is refused with a hint naming that flag. `QUACKD_HOST` sits below the
  two URL variables on purpose: it is the board you usually use, and a `.env` line naming a
  model server's exact address must win over it. No vendor and no decision LLM is moved, and
  `--decision-url` already reaches a server on the board
  ([docs/local-llms.md](docs/guides/local-llms.md)).
- **Breaking. `quackd doctor --json` carries the board under `host`.** The `host` key holds the
  hello, the health, the presets asked at the host as `host.servers` and the parsed board as
  `host.jetson`. The top-level `jetson` key 0.13.0 added is gone, and so is its
  `docker_default_runtime`, which means nothing from a laptop. A malformed `QUACKD_HOST` is a
  row in the report rather than a line of prose, so `--json` still prints one document.
- **A body that reads one camera names both that read several.** Handed a second
  `--camera-url`, it now says `only lerobot:real and lerobot:mujoco take several`, and a
  registry entry with a second camera on such a body says the same, where both said
  `only lerobot:real takes several`.
- **The system prompt's simulator note is keyed on the adapter as well as the backend.** Only
  the Microduck's `mujoco` hears about the 2 m arena and the orange ball, because the arm's
  simulator now has a backend of the same name and neither. The arm's gets a note of its own:
  a model of the arm on a table, physics nobody measured on an SO-101, and cameras that are
  rendered views, or, on a run with no camera, that whatever is on the table goes unseen.
- **`quackd doctor`'s transports section is no longer headed as the Microduck's.** It is what
  an installed adapter checks on this machine, a row per `adapter:backend`, and the arm's
  simulator is the row there today.
- **The arm's heartbeat no longer feeds the gripper trace `pick` watches.** Its reads go
  through a seam of their own, which the simulator's fault plan leaves alone, and a grasp is
  noticed on the verb's own reads. On an arm, `pick` can notice a grasp one poll later than it
  did, and nothing in quackd loads a policy for `pick` yet.
- **A refusal from the arm's backend names the backend.** The real arm's still begin
  `lerobot real:`, and the simulator's begin `lerobot mujoco:`. The `--camera-url` parser takes
  the same label and says, on the simulator, that a camera there is one of the scene's mounts
  named with `?name=`.
- **An MCP session's minutes count from its connect.** `serve-mcp` started the clock
  `max_minutes` reads when it built the server, before the body connected, so the connect's own
  seconds, an arm's retries among them, came out of the minutes the client was given. It starts
  again once the body has connected, as a `quackd run`'s does. On `lerobot:mujoco`, whose clock
  is the wall's until it connects and its world's from then on, that is what lets the minutes
  reach `max_minutes` at all ([docs/mcp.md](docs/guides/mcp.md)).

### Fixed

- **An exit that skipped the close could drop a LeRobot arm.** quackd built the SO-101 follower
  with LeRobot's `disable_torque_on_disconnect` at upstream's True, and only `close()` wrote it.
  LeRobot disconnects a robot that is still connected when it is garbage collected, so a second
  Ctrl-C during the end-of-run rest move, or a crash, could end in that disconnect with torque
  off, and the arm fall wherever it stood, while `docs/safety.md` said it was left holding.
  Nobody saw it on an arm: it was found by reading upstream. The follower is now built with the
  flag False, and every connect asks for False again, so a disconnect quackd did not ask for
  keeps torque. `close()` still writes the flag just before its own disconnect, True at the rest
  pose or with none recorded and False away from it, and a connect quackd refuses because the
  arm is not calibrated, has no calibration file or has no motors bus writes True before it
  lets go. Every clean ending lets go where it did, but for one refusal that lets go where it
  used to hold: on a transport connected again after a close that kept torque, a refusal carried
  that close's flag into its disconnect and kept the arm energised without saying so. A connect
  that fails any other way once the arm is energised, such as a first read the arm does not
  answer, used to be left to that same disconnect with nothing said. It now closes the port with
  torque kept and says so: hold the arm, and cut its power. Where the flag will not take, the
  close reads it back and says what the disconnect will do, where it used to assume a release
  ([ADR-0036](docs/adr/0036-what-the-arm-does-not-say.md)).
- **Two tasks sleeping as one simulated body could leave one of them asleep for good, with no
  error.** The lockstep clock lets go of a participant's id before the task that slept there
  resumes and cleans up: it marks a due sleeper awake before it resolves that sleep, a closed
  live window does the same for every sleeper, and `unregister` drops the id. A second task
  sleeping under the same id could park in that gap, because the slot read awake, and the first
  task's cleanup then cleared the second one's wait rather than its own, so its call never
  returned. The cartoon's transport and microduck's MuJoCo one sleep every task under the
  body's one id, so two MCP calls in flight could reach it. A sleep now clears only its own
  wait on the way out ([ADR-0016](docs/adr/0016-flock-lockstep-clock.md)).
- **A LeRobot arm's heartbeat could stop a run over an answer that had come in time.** Every call
  to the arm has a deadline, and it fired on whatever the event loop's thread found when it got
  round to looking. That thread can be busy for a second or more while a call is out: a pilot's
  SDK parsing its first response, or quackd encoding a frame. The worker had the arm's answer
  in well under a millisecond, the deadline fired first when the loop resumed, and the answer
  was dropped: `heartbeat failed: the arm did not answer: TimeoutError:`, with nothing after it,
  and the run stopped. Each call is now stamped as it is handed to a worker thread and as it
  comes back, and only time the bus was busy spends a call's budget. A call that came back
  within its budget returns what it returned, and a call queued behind one that came back in
  time keeps its place in the queue until the loop hands it the bus. A call still out when its
  budget is spent is refused and wedges the bus exactly as before, and every timeout now names
  the call and its budget. The frames saved for a turn's own observation, and the pictures the
  pilot is sent with it, are encoded in a worker thread too. The frames the `observe` verb
  saves, the pictures `robot_observe` returns to an MCP client, every detector but a board's and
  the providers' SDKs still run on the loop's thread. This was found and measured on
  `lerobot:mujoco`, which runs the same code, and never on an arm. It is one way the heartbeat
  failure seen once on 2026-09-15 could have happened, and nothing says it is the one that did.
- **The close's warning said the arm's shortfall twice.** An arm whose rest move stalled was left
  holding with `the arm is not at its rest pose (elbow_flex is at 40 with a goal of 90; elbow_flex
  is at 40 with a goal of 90, and it has stopped moving)`, because the close joined its own read
  to the rest move's reason, which begins with the same words. It says it once.
- **Every observation a pilot was handed said its step twice**, as `[step 3/40 · step 3/40, llm
  calls 3/40, 0.1/5 min]`, on every body. It reads `[step 3/40 · llm calls 3/40, 0.1/5 min]`,
  and `quackd log` reads both.
- **`quackd validate` on Windows listed files in two slash styles.** A pattern written with
  forward slashes, as Git Bash writes one, came back with a backslash wherever the wildcard
  matched: `docs/examples/lerobot/e00[1-5]/*.duck` listed `docs/examples/lerobot\e001\circle.duck`.
  Its matches now come back written the way the pattern was, and so do `quackd preflight`'s.

### Removed

- **Breaking. quackd's Jetson container, the workflow that built it and its `.dockerignore`.**
  The Dockerfile, the compose file and their README went with their directory,
  [`deploy/jetson/`](https://github.com/rokbenko/quackd/tree/v0.13.0/deploy/jetson), linked here
  at the `v0.13.0` tag, where the files still are. The arm64 image workflow,
  `.github/workflows/jetson-image.yml`, went with them, and so did `.dockerignore`, whose only
  consumer was that Dockerfile. `tests/test_deploy_jetson.py` is removed too, and its two tests
  about the page rather than the image, the JetPack table held equal to the one doctor reads and
  the sentence saying no Jetson has run this, moved to `tests/test_docs.py`.
- **Breaking. `quackd doctor`'s reading of the board it runs on.** It read `/proc/device-tree`,
  `/etc/nv_tegra_release`, `/proc/meminfo` and `/proc/swaps` on its own machine, ran `nvpmodel`
  and asked Docker for its default runtime. doctor now opens none of this machine's `/proc` and
  runs no subprocess, and a test that parses `doctor.py` keeps it that way. The board is read
  with `--host` instead, above.

### Known limitations

- **Nothing here has been run on a Jetson by this project.** The daemon was driven in-process in
  the test suite, against a board made of files, a fake ultralytics and torch with CUDA and
  without, a stubbed OpenCV capture and a one-line stand-in for `tegrastats`, and its source is
  parsed with Python 3.10's grammar without having run under 3.10. The client, doctor, the host
  camera and the host detector were driven against a fake of the protocol on loopback, and one
  test runs the real daemon against the real client and doctor. That proves quackd reads the
  protocol as written, and nothing about a board.
- **What a board would say is unmeasured.** Whether `--camera csi` opens the CSI camera and
  delivers the IMX219's whole field of view. Whether ultralytics on JetPack, with NVIDIA's
  torch, detects on CUDA, and how long a detection takes there. How long a snapshot and a
  detection take over a robot's Wi-Fi inside `go_to`'s steering loop, which re-sends its last
  twist for 0.3 s while it waits and no longer, so a slower round trip lets the body stop
  between frames. Whether a model server and the detector on a robot's own board leave its fifty
  hertz control loop alone. On a ToddlerBot, the one body with a Jetson, a starved loop is a
  fall, and the daemon's lower priority, memory ceiling and OOM score are a precaution rather
  than a measurement. And whether a real board's files and `tegrastats` line look like the
  fixtures, which were written from NVIDIA's documentation. What to send back from a board that
  can is listed at the end of [docs/jetson.md](docs/guides/jetson.md#status).
- **A fleet has no board.** `--host`, or a host stored with any member, is refused for
  `--robots`, `--flock`, a task file with a `flock:` block and several robots alike, because one
  host is one camera, one detector and one board's health. `QUACKD_HOST` is not refused, and for
  a fleet it moves only the local presets' model server.
- **Two waits on a board can outlast a call's timeout.** Every wait on the board's socket has a
  timeout sized to what the call does, and these two are outside it. Looking the board's name up
  comes before there is a socket, on the system resolver's own clock, so a name that stops
  resolving can take longer than that to fail, and is paid again on every call. A reply trickled
  in a byte at a time can take longer too, which only a hostile board would send. An address
  given to `--host` is never looked up.
- **The arm's simulator is not the arm, nothing has compared the two, and only the bench can
  settle these.** PLAN.md carries each as an open item.
  1. Joint signs and zero offsets: nudge each real joint by a small positive angle and check it
     turns the same way in the simulator, and read the calibrated value at each mechanical stop
     against the model's stop. That also says whether a recorded fold can be represented at all.
     Until then a rest pose past one of the model's stops starts at the stop, and one that puts
     the model into its table or into itself, as it starts or where the close parks it at the
     edge of its travel, starts and rests where it settles against them, each with a note, or is
     refused where a second of settling cannot clear it. `arm-01`'s fold settles clear.
  2. The rest move pressing the gripper into the table: in the model, `arm-01`'s rest goal,
     clipped into the travel, puts the gripper below the table top. Before a rest move on the
     arm is trusted, watch one from a raised pose with a hand on the switch and check that the
     gripper stops above the bench rather than pressing into it.
  3. The gripper on a real pen: what it reads against the band that infers holding. The
     simulator's pen is a capsule in the model's physics and says nothing about that band.
  4. The front and wrist cameras' placement and field of view, which would replace the default
     mounts. Front and top are quackd's views of the table, and the wrist view is rendered from
     upstream's printed mount, which may not be where the lab's camera sits.
  5. The policy loop's rate on the real bus. The simulator's clock is lockstep, `--live`
     included, so nothing timed on it is a rate.
  6. The seven bench steps 0.14.0 owes, under its Known limitations below, which the simulator
     does not replace.
- **Its dynamics are the model's.** The gains are a calculation and the servo properties another
  robot's, so a settle time, a push or a grasp that holds is evidence about the model
  (`SERVO_DYNAMICS`). Its servos never warm, so the heat refusal is never rehearsed.
- **What a rehearsal costs is its connects and its cameras, not its physics.** On this
  project's laptop, with integrated graphics, the physics steps several times faster than the
  wall with nothing rendering. Each connect loads the model and renders once, and each camera a
  run names renders again whenever it is read after the world has moved. `quackd preflight` of
  `lerobot-lookout`, three connect cycles and three runs, took about 15 seconds there on a twin
  with no camera and under half a minute with two.
- **A `lerobot:mujoco` robot makes `robots.json` unreadable to quackd 0.14 and earlier**, which
  check every robot against the backends they know, every robot in the file included, until
  each `lerobot:mujoco` robot is removed. `robot twin` says so and names them all.
  `QUACKD_REGISTRY_DIR` keeps a registry for this release apart from an older install's.
- **An MCP session on the simulator is not seeded.** A run through the agent loop or
  `quackd preflight` makes one call at a time, and under one seed the simulator does the same
  again. An MCP session runs tool calls at once.
- **The nightly job has not run yet.** GitHub runs a scheduled workflow only from the default
  branch, so until it has, the simulator's ten of ten on the maker's model is the sweep run by
  hand on 2026-09-27.
- **The arm has not run quackd since 2026-09-23, and six of the seven bodies never have.** What
  this release changes in `lerobot:real` has run only in the test suite, against a fake arm or
  the simulator, and never on an SO-101: the follower built to keep torque on a disconnect
  quackd did not ask for, the refusal that asks for the release itself, the close that reads the
  flag back rather than assuming it, the port closed with torque kept after a connect that
  failed once the arm was energised, the heartbeat's reads kept out of the trace `pick` watches,
  the deadline each call to the arm spends only while the bus is busy, and the close's warning
  that says a stalled shortfall once. One bench step would settle the first, taken with
  0.14.0's: in step 14 of
  [docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md), a second Ctrl-C
  during the fold back to the rest pose, with a hand under the arm. The arm should still hold
  where it stood once quackd has exited, until you hold it and run
  `quackd robot release arm-01` or cut its power. Nobody has written a bench step for the
  other six.

## [0.14.0] — 2026-09-25

quackd has run on a real arm a second time, and this time the arm ran examples prepared for it.
On 2026-09-23 the SO-101 that waved on 2026-09-15, registered by name as `arm-01` this time and
with a rest pose recorded under it, took 26 runs in one afternoon on a build of the code that
became quackd 0.13.0, from an earlier revision of its catalogue commit and still numbered
0.12.0, on lerobot 0.6.1 and Windows, piloted by `gpt-6-sol` and `gpt-6-astra`. Twenty-three of
those runs were ten of the task files written for that day's shoot, from nine of its
experiments, and the other three were goals typed on the line. This release adds the task files
to the repository, one folder for every experiment on the run sheet that has one, under
[docs/examples/lerobot/](docs/examples/lerobot/README.md). Nineteen of the 26 runs never moved
the arm at a pilot's request, and the traces show five faults, four of them behind most of those
nineteen and the fifth waiting at the end of every run that reached its close. The rest pose lay
past the travel the arm's calibration had recorded, `shoulder_lift` at -104.7 against ±84.2, and
the servo clamps every goal to that travel: six runs aborted on the rest move before their first
model call, all 21 runs that reached their close kept torque on and ended at the power switch,
the `stop` in a run's teardown hauled the folded shoulder up out of its fold, and three pilots
shown a reading past the travel refused to move an arm they could not explain. The verdict gate
refused honest answers: the arm's own datasheet turned down a `feasible` for a wave whose needs
were `mobility` any, `terrain` indoor_flat and a `work_height_m` of 0, twelve runs stopped at a
y/N question about a pilot's doubt, and a run with a pen ended on the prompt line
`Decline any task that hinges on any of them` after a person had said go. Three runs ended at
connect on one bad status packet, LeRobot's
`Failed to write 'Lock' on id_=N with '1' after 1 tries`, each on a different motor, one that
never came back and two that came back garbled. `move_joints` had no motion time, so a pilot
asked to raise the shoulder over ten seconds read the verb's own description correctly and
declined. And nothing short of the power switch took torque off an arm a run had left holding
itself up.

This release answers each of the five in the arm's own numbers rather than that afternoon's. A
rest pose past the travel is parked at the edge of it and released there, and `stop` writes no
goal for a joint that reads past its travel. The verdict has a word for a task that goes
nowhere, and an arm's own datasheet no longer refuses the answer. A connect that meets a bad
packet is tried again. `move_joints` takes the time it is given. And a person holding an arm
can have its torque taken off wherever it stands, with `quackd robot release` or with Enter at
the end of a run whose rest move missed. None of it has run on an arm. It is exercised against
a fake arm that clamps goals the way the servo does, against `lerobot:mock` and in the test
suite, and Known limitations, below, lists the bench steps that would say whether it works.

### Added

- **A task file for every experiment on the 2026-09-23 shoot's run sheet that has one, 223 of
  them under [docs/examples/lerobot/](docs/examples/lerobot/README.md).** One folder per
  experiment, `e001` to `e165` (`e068`, `e119` and `e126` have none), holding one file per
  variant and any picture the experiment hands the model with `--image`, except four that are a
  person's own to supply: the bill and the star count of `e106` and the two video thumbnails of
  `e111`, saved beside their files as `bill.png`, `stars.png`, `thumb-a.png` and `thumb-b.png`
  before those files run. `e041` hands the model the picture in `e034`. A few more are there to
  print, and every other prop, the like button of `e077` among them, is a person's own. A file's
  `name` is its experiment number and a slug, so a run folder says which experiment it was,
  `runs/20260923-112312-e001-circle/`, and each file opens with a comment carrying its first
  command, or, for `e145` and `e152`, which an MCP session loads, how to load it. Every one
  validates against `lerobot:real`, which the page says to check before a session. Ten of them
  made 23 of that afternoon's 26 runs, on quackd 0.12.0, and the rest have not run on an arm.
  Their budgets are estimates.
- **`quackd robot release NAME` takes torque off an arm wherever it stands, while you hold
  it.** It is for an arm a run left holding itself up away from its fold, which on 2026-09-23
  only the power switch could put down. It warns before anything connects, because connecting
  takes torque off every motor for a moment and the release then lets the arm fall, asks
  (`--yes` skips the question), and only then connects with the registered rest pose and no
  camera, prints the joints, releases with no `stop` first and reads `Torque_Enable` back off
  every motor. It exits 1 unless every motor read off, names any that did not, and ends on a
  line that says what the release did, a release that did not take included. With no terminal
  and no `--yes` it refuses, and a body that is never handed to a person refuses by name. It is
  not a verb, not an MCP tool and not on the `RobotAdapter` protocol, so no pilot can reach it
  ([docs/registry.md](docs/guides/registry.md),
  [docs/adapters/lerobot.md](docs/adapters/lerobot/README.md#releasing-it-where-it-stands),
  [ADR-0039](docs/adr/0039-an-arm-placed-by-hand.md), amended).
- **A run whose last rest move missed offers to release the arm into your hands.** At a
  terminal, on a run that is not a dry one, and only over an arm that answered its last read,
  the run says the arm is holding itself up and asks you to hold it and press Enter before it
  closes. Enter releases it where it stands, through the same door as `quackd robot release`.
  Sixty seconds with no answer (`AgentLoop.RELEASE_OFFER_S`), a terminal with no key to read
  or a Ctrl-C leave torque on, exactly as a run without the offer would. A Ctrl-C that lands on
  the release itself is caught, and what the person is told turns on the arm's backend saying
  which side of the send it landed: once the release has gone out, that the arm may be limp,
  and before it did, on the read the release begins with, that nothing was sent and torque is
  as the rest move left it, with whether the arm holds itself up left to the close's own line.
  The close, `run_end` and the summary still happen either way. The exchange is a
  `prompt` row and a new `release` event whose `stage` and `reason` say how it ended. Never on
  a dry run, an MCP session or a flock member, and never over an arm whose rest move left a
  call that never came back, a goal write or the hold a stalled move ends with, since that arm
  has not answered ([docs/architecture.md](docs/concepts/architecture.md)).
- **`mobility` and `manipulator` take `none`, which asks for nothing.** `any` means some kind,
  which an arm bolted to a table fails, so a task that goes nowhere had no word to say so
  ([docs/duck-spec.md](docs/reference/duck-spec.md),
  [ADR-0032](docs/adr/0032-datasheets-and-the-verdict.md), amended).
- **The hardware report and the checklist ask what the bench has not answered yet:** whether a
  move given several seconds looked like one motion and arrived when the time was up, and
  whether a joint released at the edge of its travel settles onto its fold. The report also
  asks three questions the checklist already did: whether a `--by-hand` arm stayed where it
  was put when torque came back on, whether a pencil closed in the gripper by hand was still
  there after a drawing move, and which decision LLM ran with `--decision-mode shadow`, how long
  it took to answer and how often it agreed with the model. Its list of steps numbers them as
  the checklist does, with `--by-hand` and a Ctrl-C with the arm in your hands as steps 15 and
  16 where it gave those numbers to the gripper and the policy, and its `doctor` field asks for
  the name the arm was registered under and says to hold the arm while it runs.

### Changed

- **A rest pose past the arm's calibrated travel is parked at the edge of that travel, and
  torque is released there.** The rest move clips each body joint of the recorded pose into the
  travel `joint_ranges()` reads off this arm, at either end and for any number of joints, and a
  joint recorded past its travel counts as at rest at the edge of the travel or anywhere beyond
  it on the side it was recorded. So the arm is released at the nearest point to its fold its
  servos can be driven to, and the joint is left to settle the rest of the way, which on the
  bench arm is about 20 degrees of `shoulder_lift`. The run says so once, naming the joint,
  where it was recorded and where it parks, and says to calibrate with the arm folded. `doctor`
  adds it as advice with its verdict still green, an MCP session logs it, and
  `quackd robot rest-pose` warns before it records. A joint that stops short inside its travel
  is still a miss and still keeps torque on. The manifest carries `extras.rest_pose_clipped`
  when something was clipped, `report_state` explains a reading past the travel in the arm's own
  numbers, and the prompt's travel line says a joint can read past its travel when it was folded
  or placed there with torque off
  ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md#a-pose-past-the-travel),
  [ADR-0045](docs/adr/0045-a-rest-pose-the-calibration-cannot-reach.md)).
- **`stop` writes no goal for a joint that reads past its travel, and says which joints it left
  out.** Every `stop` and every teardown wrote each body joint's present position as its goal,
  and for a joint folded past its floor the servo clamped that to the limit and drove there,
  which is how the stop at the end of a run hauled the shoulder up out of its fold. Such a joint
  is now left out of the hold, and the stop's summary adds, for example,
  `shoulder_lift reads past its travel, so no goal was written for it`, with `not_held` in its
  data. If every body joint reads past its travel, nothing is sent and the stop is still a stop.
  What the skip cannot do is halt a joint a move has already started lifting out of a fold,
  because every goal written past the travel reaches the servo as the limit: the power switch is
  the only stop for that stretch ([docs/safety.md](docs/concepts/safety.md)).
- **`--by-hand` will not take hold of an arm with a joint placed past its travel.** Its
  take-hold wrote that joint's reading as its goal, which the servo clamps to the limit, so
  torque hauled the joint there under the person's hand. Leaving the joint out instead would
  leave its servo the last goal it was written, the rest move's, which can be the far end of the
  travel. Neither keeps the joint where it was put, so the take-hold now refuses before it
  writes anything: torque stays off, and the refusal names each such joint, where it reads and
  its travel, says that a goal written where the joint is lies past its travel and the servo
  would pull the joint to the end of it, and says the arm is taken hold of only with the joint
  inside. The run ends there and says so to the person at once. The log line for a refused
  take-hold or release reads `hold refused` or `release refused` rather than `held` or
  `released`, and a take-hold refused over a joint outside its travel ends its line on the
  reason, with no joint list after it that could round a joint a hair past its travel onto the
  edge the reason says it is past, and its `hand_off` event names each such joint in a new
  `outside` field. A refusal whose reason names no reading, an arm that moved as torque came on,
  keeps the list that says where it is holding
  ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md#placing-it-by-hand),
  [ADR-0045](docs/adr/0045-a-rest-pose-the-calibration-cannot-reach.md), amended).
- **After a refused take-hold, nothing touches the arm, and the person is told which arm they
  are holding.** The teardown's stop took hold again on its own after any refused take-hold that
  left the arm in a hand, so torque could come on under the person's hand with nothing said, and
  the rest move then folded the arm, or wrote its goals into limp servos where that second hold
  did not take. And a torque register that did not answer after the take-hold's torque write was
  refused as a hold nothing confirmed, with the person told to keep hold of the arm, the stop
  sent the torque write again, the hand-back then told them that the arm was holding where it
  ended and to take what was in the gripper, and the rest move then folded the energised arm
  under their hands. Now a take-hold says whether it may have left torque on
  (`HandResult.energised`), for every take-hold since the release. One that switched nothing on,
  refused before its torque write or read off on every motor after it, is told at once that
  quackd did not take hold of the arm and that it is still in their hands, and one refused over
  a fold nobody lifted before pressing Enter, whose own read found the whole arm at its rest
  pose with every motor off, is told that the arm is still limp at its rest pose and which joint
  to lift inside its travel. One whose torque write may have taken with nothing read back is
  told as quackd not being able to confirm whether the arm has torque, with the person asked to
  hold it as though it may move or drop and to cut its power to be sure, and one whose read
  found motors on, some or all of them, is told which joints hold and that any it does not name
  is limp, to keep hold of the arm and to cut its power, where one that found some motors on and
  the rest off used to be told that the arm still reported torque off, so nothing held it. After
  any of them, the stop takes no second hold and sends nothing, the hand-back is not asked and
  says which arm the person is holding, from the body's own refusal where an interrupt kept the
  run's from it, the rest move writes nothing and the run says once that the arm is in their
  hands and not folded, or, where its read finds the arm still at its rest pose, that it is
  already there, and no release is offered. The close says what its own read found, and a read
  speaks for the arm only when the bus carried it after the last torque write. The close used to
  tell anybody left holding the arm that it was limp and nothing held it up, over motors a read
  had found on and over a torque write nothing read back. The close's lines are: quackd cannot
  tell whether the arm has torque where nothing read the torque write back, the joints that read
  on by name, the arm limp in their hands, let go of for them to place, rather than the rest
  move's shortfall said twice, and, for an arm the placing release let go of at its rest pose
  that still reads there with every motor off, that it is limp at its rest pose rather than in
  anybody's hands. A Ctrl-C in the placement wait with no take-hold refused yet still takes hold
  where the hand has it and folds the arm, as it always did, and where that stop's take-hold is
  refused, over a joint placed past its travel or a fold nobody lifted, the person is told so
  once, naming the joint, and it is recorded
  ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md#placing-it-by-hand),
  [ADR-0039](docs/adr/0039-an-arm-placed-by-hand.md), amended).
- **`move_joints` takes the time it is asked for.** `duration_s` was how long a move could take
  before it gave up, and every move ran at the step cap whatever it said. A move is now walked
  from where the arm is to its goal across `duration_s`, one target a tick at 10 Hz, and judged
  once the walk is done. The step cap, 5 degrees a tick unless `QUACKD_LEROBOT_MAX_STEP_DEG`
  sets another, is the ceiling, so a time too short for the distance still runs at the cap. The
  default is still 5 seconds, which now means a move given no time takes at least five seconds
  where it used to take as long as the cap needed. The only move sent whole is one with nothing
  to walk, every joint already within a tenth of a degree of its goal. A goal outside the
  travel the manifest publishes, now rounded inward to a tenth, is refused by the verb before
  it reads the arm or sends anything, in the sentence both backends refuse with. That sentence
  now gives the travel to a tenth, rounded inward, and the goal to a tenth unless a tenth would
  round it onto that travel, in which case as it was given: in whole degrees it could name a
  refused goal inside the range it gave, `85 is outside -85..85`, and so would a tenth for a
  goal a few hundredths past an edge. A gripper named in `move_joints` is walked like any
  joint, and the `gripper` verb is not ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md),
  [ADR-0036](docs/adr/0036-what-the-arm-does-not-say.md), amended).
- **The line an arm left holding itself up ends on names the ways out, and puts holding it
  first:** `the arm is not at its rest pose (...), so torque was left on and it will not fall
  as it stands: hold it first, because connecting takes torque off every motor for a moment,
  then run quackd robot release NAME, or quackd doctor --robot NAME to park it, or cut its
  power`, with the name the arm was registered under. It used to end
  `hold the arm and cut its power, or run again`. An arm that stopped answering is no longer
  said to be holding itself up: the close says quackd cannot tell whether it is, and to hold it
  and cut its power, and `robot list --probe` shortens that to `torque unknown`. `quackd doctor`
  now warns that connecting takes torque off every motor for a moment before it connects an
  arm, as `quackd robot release` does, because the line sends people to both.
- **The verdict gate takes an arm's honest answer.** A zero asks for nothing on every number,
  `work_height_m` included. A body that does not move, and has a datasheet, meets
  `indoor_flat` and is refused anything above it with `(it does not move)`. A pilot's own
  sheet is not held against it for a working height its prompt never listed as missing, while
  the list of bodies that could, the MCP `could` list, a flock role and the coordinator stay
  strict. The prompt's `Not published` line tells the pilot to answer `uncertain` and name the
  figure where a task turns on it, rather than decline, and the SO-101's sheet publishes a
  reach of 0.4 m, an estimate from the link lengths in the maker's URDF, and a payload line
  with objects to judge by. Every field of the `needs` schema says what it means, and the
  `robot_assess_task` description spells out the words
  ([docs/safety.md](docs/concepts/safety.md#when-a-feasible-verdict-contradicts-itself),
  [ADR-0032](docs/adr/0032-datasheets-and-the-verdict.md), amended).
- **After a go, the pilot is told who cleared its doubt.** A pilot whose `uncertain` a person
  answered with go hears that a person read it and said go, and not to assess again on the same
  doubt, only on something new. On 2026-09-23 a pilot told only that the verbs now ran assessed
  the same doubt again as `infeasible` and ended its run. A run started to go ahead without
  asking anybody tells its pilot exactly that, and names the three ways that happens, `--yes`, a
  flock's standing answer and a pipe. The hint beside an `infeasible` verdict, where the body's
  sheet meets every need but a working height it publishes no band for, now ends by saying the
  body publishes no working height band, so whether it reaches that height is the pilot's
  judgement. In 0.13.0 it said nothing there about the body's own sheet.
- **The PyPI keywords catch up with 0.10 to 0.13.** `so-101`, `ros2`, `mujoco`, `jetson` and
  the decision LLMs `jev`, `kev`, `von`, `openjev`, `opendecision` and `laya` join the list,
  and `reinforcement-learning` and `sim2real` leave it, because the training loop they
  describe does not exist yet.
- **For adapter authors:** `RestResult` gains `clipped`, `note` and `answered`, and `HandResult`
  gains `torque_on`, `energised`, `outside` and `resting`, all with defaults, so every body that
  builds `RestResult.none()` or a plain `HandResult` is unchanged. `torque_on` names the motors
  still on after a release, empty when every one read off and `None` when nothing was read
  back, and a take-hold refused with motors read on names those motors in it too. `energised`
  says whether a take-hold may have left torque on, `outside` names the joints a take-hold was
  refused over because each read outside its travel, and `resting` marks such a refusal whose
  own read found the whole arm at its rest pose with every motor off. `let_go_if_any` passes
  its keywords on, and the one wording of the warning that connecting takes torque off is
  `quackd.adapters.base.CONNECTING_TAKES_TORQUE_OFF`. The core reads six more attributes off a
  body with `getattr`, so a body without them is unchanged: `stop_skipped` for the joints the
  `stop` verb names as left out of its hold, `connect_notes` for the connect retries a run
  records and `doctor` reports, `set_stop_check` to hear a Ctrl-C while it connects, `in_hand`
  and `refused_hold` for the end-of-run offer and the teardown of a `--by-hand` run, and
  `rest_pose_note` for the warning `quackd robot rest-pose` prints.

### Fixed

- **A bad status packet at connect ended the run.** LeRobot's `configure()` writes
  `Torque_Enable` and `Lock` to every motor with `num_retry` 0, so one status packet lost or
  garbled failed the connect and left the port open. `connect()` now makes up to three attempts
  (`CONNECT_ATTEMPTS`), half a second apart (`CONNECT_PAUSE_S`), closing the port between them
  without a write to any motor and lowering the servo SDK's busy flag, which a serial error in
  the middle of a packet leaves raised and reopening the port does not lower, so that a later
  attempt is not turned away as the port being in use and read as every motor missing. A connect
  that blew its deadline is never tried again, because its thread is still on the bus, and
  neither is a timeout LeRobot raised itself. The joint LeRobot's message names, by `id_=N` or
  in its handshake's list of missing motors or wrong models, is named through the bus's own
  motor table. Each retry is a warning, a line in the transcript and advice under `doctor` with
  the verdict still green. The refusal after the last attempt carries LeRobot's words, the
  joint, and the cable, the servo supply and the port to check, and when an attempt can have
  written torque, or ended on a timeout, it says some motors may be left with torque on and
  others off, so keep a hand under the arm. A read lost in the calibration check LeRobot's
  connect makes between its handshake and `configure()` writes nothing and does not earn that
  warning. A handshake that found none of the arm's motors, which is how a servo supply that is
  switched off looks, names no joint and says to check the arm's cables and power. A Ctrl-C
  during a connect that is failing ends it: the run hands the arm a way to hear its kill switch,
  and the connect is refused as stopped rather than making another attempt, each of which
  switches torque off every motor and on again. `let_go` and the take-hold pass `num_retry` 5 to
  their torque writes (`TORQUE_RETRIES`, upstream's own disconnect count).
- **`quackd doctor --robot NAME` probed a registered arm under the default calibration id,
  `arm-01`, whatever name it was registered under.** It builds the arm under its registered
  name now, as a run does, so it reads that arm's calibration and its lines name it.
- **A release that never went out was reported as one that did.** `let_go()`, which
  `--by-hand` calls, reported a release when the read before it had failed, and now refuses. A
  release that raised part way through its motors, or that a Ctrl-C landed on, now leaves the
  arm in a person's hands rather than in nobody's, so the close no longer says torque was left
  on over an arm that may be limp. Nor does it say "nothing is holding it up" where nothing read
  the release back, since the motors after the one a release stopped at keep their torque: it
  says the release went out, that nothing read it back, and to cut the power to be sure, on the
  real arm and on `lerobot:mock` alike.
- **A first Ctrl-C at `--by-hand`'s hand-back was recorded as nobody answering.** The wait
  watches a fresh key press, so the kill switch ends it without raising, as an empty room does.
  The record says `interrupted while waiting` for it now, and `no key could be read` for a
  terminal with nothing to read, as the new end-of-run offer's record does. A run's
  `terminal.txt` says how a wait ended too: after the run's first Ctrl-C, or `q`, it reads
  `(the kill switch ended the wait, without an Enter)`, and after a wait that ran out
  `(nobody answered: the wait ran out without an Enter)`, where every wait that returned without
  an Enter used to read `(nobody answered: the wait ended without an Enter)`. A later Ctrl-C,
  which the kill switch has handed back, cancels the wait and leaves no line for it, as it
  always did.
- **The verdict raised `TypeError` on a null asked of a body that publishes the figure**, for
  the working height band, the arm count and every published figure, and the non-number guard
  covers them now. And it named a best body for a need of zero, which is how the run with a pen
  was told `the most is lerobot at 0.5 kg` against a payload of 0. It names one now only for a
  need above zero that nobody installed meets.

### Known limitations

- **None of the code this release changes has run on an arm.** These are the bench steps that
  would settle it, in order, with a hand near the power switch, and what to report from each
  ([docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md)):
  1. `quackd doctor --robot arm-01`: whether the `rest pose` row reads `at it already` for an
     arm left folded, or `returned to it` for one that had to move, with the clip note naming
     `shoulder_lift` listed under the table as advice, and whether the shoulder, released at
     the edge of its travel, settles onto its fold without falling from height.
  2. `quackd robot release arm-01` with the arm parked: the joints it prints, the y/N, and
     `torque reads off`.
  3. The `e004-mornings` task, three times, because three of that afternoon's 26 runs failed at
     connect: whether the verdict goes through with no y/N, the wave happens and the connect
     goes through. The clip note naming `shoulder_lift` is said once, right after the run's
     first rest move, and at the end the arm folds to the edge of its travel and is released
     there with no further line, so look for the note at the start rather than at the release.
  4. The `e116-slow-raise` task: whether the model passes a `duration_s` near ten seconds and
     the shoulder takes about that long, with no refusal over the arm's state.
  5. `--by-hand` with the pen, once with a joint placed past its travel: whether a dropped
     packet is tried again, the hand-off releases at the reachable pose, and the take-hold
     leaves torque off, names the joint and its travel, and leaves the arm limp in your hand
     until you put it down, with nothing moving it or putting torque on it after that line, not
     even once the joint is moved back inside its travel.
  6. A run made to miss its rest pose with a hand in the way: whether the Enter offer
     appears, Enter releases, and left alone the arm keeps torque after 60 s.
  7. Separately, and only if you want to: `lerobot-calibrate` with the shoulder folded all the
     way back, then `quackd robot rest-pose arm-01` again, after which the clip note should be
     gone. That moves `shoulder_lift`'s zero, so a task or a remembered note that names an
     angle means a different pose afterwards.
- **What only the arm can say.** Whether a joint released at the edge of its travel settles
  onto its fold, and gently, and whether one folded past its ceiling settles at all. What a
  servo does with the goal it holds when torque comes back on (`TORQUE_ENABLE_HOLDS_PRESENT`,
  still UNVERIFIED). What an SO-101 does when a release
  reaches it away from its fold, how fast a shoulder held out drops, and whether one hand
  catches it. Whether a servo following a goal that moves every tenth of a second looks like
  one motion, how far past its walk a loaded joint needs to settle, and that a joint blocked
  early in a slow move pushes, one step off its target, until the walk ends before it is
  called stalled. That a retry after a real lost packet connects, and what torque state a
  failed `configure()` leaves the motors in. Whether a real pilot now answers the verdict
  without a y/N.
- **The SO-101's reach is quackd's arithmetic on the maker's URDF, not a measurement.**
- **A connect refusal places a failure by LeRobot's method names:** `_handshake`, the
  calibration check's `is_calibrated` and `read_calibration`, and `configure`,
  `torque_disabled`, `enable_torque` and `disable_torque`. They match the installed 0.6.1 and
  are cited in the upstream refs. If upstream renames any of them, a failure in there can no
  longer be placed, and the refusal falls back to the warning that some motors may be left with
  torque on, which is the safe reading.
- **On Windows a standard input redirected from `NUL` counts as a terminal**, so
  `quackd robot release` fed from `NUL` asks its question and aborts rather than refusing for
  want of one. Nothing connects either way ([docs/registry.md](docs/guides/registry.md)).
- **Six of the seven bodies have still never run on hardware, and the one that has last ran
  0.12.0, before any of this.**

## [0.13.0] — 2026-09-23

quackd has a path onto an NVIDIA Jetson now, and a Jetson is a host rather than a robot. The
model that decides what a robot should do next and the process that turns that decision into a
verb both fit on one small computer, so a goal in plain language never has to leave the room.
Nothing here has been run on a Jetson by this project, and [docs/jetson.md](docs/guides/jetson.md)
says so in its second paragraph, before any of its sections, and again in bold where its Status
section begins.

The other half is the model catalogue, which caught up with the day before. Anthropic shipped
Claude Opus 5.5 and OpenAI shipped GPT-6 Sol and Luna on 2026-09-22, and a bare
`--llm anthropic` and `--llm openai` now run Opus 5.5 and GPT-6 Sol. Moving the Claude default
found that Opus 5.5 refuses two things: the forced tool call quackd sent every Claude model,
and, on accounts created on or after 2026-08-31, a replayed thinking block whose earlier history
changed, which quackd's dropping of older camera frames did from the third call on. Claude Fable
5.1, a row since 0.9, does both and has failed every quackd turn since. Reading all eleven
vendors' pages again the same day found three more request forms a model does not take. Each fix
is proven against the request shape the vendor documents, and against the error text where the
vendor prints one, and none against a live model: Known limitations, below, says so again where
it counts.

### Added

- **quackd has a path onto an NVIDIA Jetson, and a Jetson is a host rather than a body.**
  There is no adapter, no extra and no `--robot jetson:...`: the board runs the quackd
  process the way a laptop does, and everything interesting is about what runs beside it.
  [`deploy/jetson/`](https://github.com/rokbenko/quackd/tree/v0.13.0/deploy/jetson) is a Dockerfile that builds quackd from the
  checkout you build it in, with its third-party Python packages pinned by `uv.lock`, onto a
  plain Debian Python image with no CUDA in it at all, and a compose file that gives the GPU
  to Ollama and nothing to quackd. The quackd service is behind a profile with
  `restart: "no"`, because `quackd run` is a command that declares a verdict and exits rather
  than a service to keep alive. The arrangement the page is really about is a ToddlerBot's own
  Jetson holding its control daemon, a model and quackd between them, three processes on one
  board talking over loopback ([docs/jetson.md](docs/guides/jetson.md),
  [ADR-0044](docs/adr/0044-a-jetson-is-a-host-not-a-body.md)).
- **`quackd doctor` reads the board it is running on, when that board is a Tegra.** The
  model, the L4T release and which JetPack it is, the memory the CPU and the GPU are
  sharing, whether the only swap is zram, the GPU device node, the power mode and Docker's
  default runtime. All of it informational: none of it can change the exit code, because
  quackd runs perfectly well on a board with every one of those wrong. It looks for a Tegra in
  `/proc/device-tree/compatible` and in `/etc/nv_tegra_release`, and it is meant to be run on
  the board itself. Inside a container it will most likely find neither:
  `/proc/device-tree` points into `/sys/firmware`, which Docker masks unless the container is
  privileged or started with `--security-opt systempaths=unconfined`, and a plain Python image
  has no `/etc/nv_tegra_release`.
- **The image is built and then run on arm64**, which is the first aarch64 Linux run of
  quackd this repository records: where the contributor transcripts say anything they put
  an aarch64 model server behind a quackd running on something else, and the largest
  measurement on that same board publishes none. It was done under emulation on the
  machine that wrote it. `jetson-image.yml` is set up to repeat it on a native arm64
  runner, running `import cv2`, `quackd doctor --json` and a whole `find-and-kick` task
  inside the result, on the scripted pilot, because a runner has no key and no model server.
  It first ran on 2026-09-23, on the commit that merged these files, and was green: the build,
  `import cv2`, the `doctor --json` assertions and the task. It publishes nothing, because an
  image with a pull command beside it is a promise that somebody ran it on the hardware it is
  named after, and nothing here has been run on a Jetson by this project.
- **The container is a new surface in [SECURITY.md](SECURITY.md).** Both services use host
  networking, so quackd is on the board's loopback rather than isolated from it. Ollama is
  pinned to `127.0.0.1:11434`, because its image's own default is every interface, and under
  host networking that would put a server with no authentication on whatever network the
  board is on. The compose file mounts `~/.quackd` into the quackd service, which hands it the
  plain-text tokens in `robots.json` and every robot's memory file, the same access a native
  install has, and runs that service as uid 1000; the image itself sets no user, so a bare
  `docker run` is root. The new `.dockerignore` at the repository root is not housekeeping:
  the build copies the whole checkout, and `**/.env` rather than `.env` is what keeps
  `deploy/jetson/.env`, the file the compose file reads provider keys from, out of it.
- **A turn a refusal fallback answered says which model answered it.** Server-side fallbacks
  are on by default for Claude: a turn the requested model declines is re-run on another one
  inside the same call. Nothing recorded that. The `llm` line the agent loop writes to
  `transcript.jsonl`, in a solo run and in each member of a pilot flock, now carries
  `served_by`, the model that took the turn, on exactly the turns a fallback took and on no
  others, and the log prints it at the end of that turn's tokens line. A coordinator flock's
  one planner call does not record it yet. The cost beside it is still priced at the rate of
  the model that was asked for.

### Changed

- **`quackd doctor` names the architecture on every machine, and its JSON has a `jetson` key
  everywhere.** The header line and the `platform` field of `--json` now end with
  `platform.machine()`, where they used to stop at the operating system's release, so this
  Windows laptop's `Windows 10` is now `Windows 10 AMD64`, and the arm64 image's ends in
  `aarch64`. `--json` also carries a top-level `jetson` key, `null` unless the device tree
  names a Tegra or `/etc/nv_tegra_release` exists. A script that compared `platform` with what
  0.12.0 printed will stop matching.
- **The ToddlerBot hardware checklist covers quackd on the robot's own board.** It now says to
  drive the robot from a laptop the first time, with nothing else running on the Jetson,
  because a model server saturating that board is the load that can starve the fifty hertz
  loop, and to try the crowded board only once the steps have passed. Step 11 tests the
  deadman by pulling the network cable mid-move, and over loopback there is no cable, so it
  now says to `kill -STOP` quackd's process instead, which stops the keepalives without
  closing the socket, then kill it outright and start a fresh run once the robot has settled.
  Never resume it with `kill -CONT`: `bot.keepalive` feeds the deadman on its own and the
  daemon clears the trip on the first keepalive it sees, so resuming hands the body straight
  back to the verb that was in flight, while your hands are on it.
- **A bare `--llm anthropic` runs Claude Opus 5.5, and a bare `--llm openai` runs GPT-6 Sol.**
  Both shipped on 2026-09-22. Anthropic's models overview says to start with Opus 5.5 and
  files Opus 5 under legacy, and OpenAI's models page lists GPT-6 Sol with Astra and Luna as
  its flagships. Each costs less than the default it replaces, Opus 5.5 at $4/$20 a million
  tokens against $5/$25, and GPT-6 Sol at $2/$10 against $4/$20. Naming the old default
  explicitly still works: `claude-opus-5` is a `legacy` row and `gpt-5.6-sol` a `current` one.
  A bare `--llm grok` runs Grok 4.7 for the same reason, at the price Grok 4.6 had
  ([ADR-0031](docs/adr/0031-model-catalogue.md), amended).
- **The catalogue was read again against all eleven vendors' own pages, on 2026-09-23.** Nine
  ids are new: `claude-opus-5-5`, `gpt-6-sol`, `gpt-6-luna`,
  `gemini-3.1-pro-preview-customtools`, `grok-4.7`, `zai-glm-5-3` on Mistral,
  `north-mini-code-1-0` on Cohere, `qwen-max` and `glm-5.3-flashx`. Claude Opus 5, Gemini 3.7
  and 3.6 Flash and DeepSeek V4 Pro move from `current` to `legacy`, each on its vendor's own
  grouping, and Leanstral takes images. `PRICES_CHECKED`, which every `run_start` records, is
  2026-09-23.
- **Breaking, for anyone who names one of them: seven ids leave the catalogue.** The three
  Gemini 2.5 models, because since 2026-09-18 Google is "limiting access to the 2.5 models to
  users who have actively used them in the past", and a model a new user cannot call does
  not belong in the list; `grok-4.20-multi-agent-0309`, because xAI's own guide says it takes no
  client-side function tools and does not answer on Chat Completions; and `qwen3-max`,
  `qwen3-coder-plus` and `qwen3-coder-next`, which Alibaba retire on 2026-10-10. A run that
  names one is refused before it starts, with the list of what the vendor does take, and
  `--llm gemini:gemini-2.5-flash` on a key that still has access is refused too.
- **A cache write is charged at the input rate wherever the vendor sells no cache write.**
  OpenAI's models before GPT-5.6, every Mistral model and both DeepSeek models carried a cache
  write rate of `0`, which would have made any token written to a cache free. None of those
  vendors has a write rate: they bill the tokens that fill a cache at the ordinary input rate,
  which is what `None` charges. No run so far is affected, because none of those vendors
  reports a written token, and the rule is now written beside the others in `catalogue.py`.
- **Meta is asked for one call per turn.** Meta's tool-calling page documents
  `parallel_tool_calls` and defaults it to allowing several calls at once. quackd now sends it
  as `false`, as it already did for OpenAI, Grok and Mistral, where it had sent nothing. The
  loop took the first call when several came, so what changes is that the others are no
  longer generated and paid for.

### Fixed

- **Claude Fable 5.1 answered every quackd turn with a 400 since 0.9, and Claude Opus 5.5
  would have too.** Two things, both in Anthropic's documentation. Both models refuse a
  forced tool call, `tool_choice: type "tool" and "any" are not supported for this model.`,
  and quackd asked every Claude model for its one call per turn that way. And both bind each
  replayed thinking block to everything before it, while quackd drops the camera frames of
  older turns from every request, which edits an earlier message from the third call on and
  makes that replayed block a 400 for accounts created on or after 2026-08-31. The catalogue
  now marks both models. They are asked with `auto`, still one call per turn, and they ask the
  API to drop a replayed block whose history changed rather than refuse the request, behind
  Anthropic's `thinking-binding-controls-2026-08-01` beta, while the loop trims their old
  frames every eight exchanges rather than on every call and leaves out the blocks each trim
  invalidates from that call on, so they keep the reasoning they produced since the last one.
  Any other Claude model that answers the forced-call 400 is moved to `auto` after it and
  stays there. The browser demo sent the same forced call from its own Anthropic client and
  does the same now; it replays no thinking blocks, so the second fix is the CLI's alone. What
  `auto` changes is that a turn can come back as prose with no call in it: the CLI re-prompts
  once and then ends the run, as it always has for a turn like that, and the browser demo ends
  the run at once, as it does for every vendor it can only ask. A coordinator flock's one
  planner call is asked the same way, and a prose reply there falls back to the task's own
  defaults, with a `planner fallback:` note and `fallback: true` on the `plan` line, as a reply
  with no plan in it always has.
- **Claude Haiku 4.5, Sonnet 4.5 and Opus 4.5 failed their first call on every run.** They
  take only the older extended thinking, and answer quackd's adaptive request with
  `adaptive thinking is not supported on this model`. The retry that exists for exactly those
  models matched a sentence opening with the word `thinking`, and this one does not, so it
  never fired. It matches the sentence Anthropic's errors page gives now, and those three go
  on without thinking text after one refused call.
- **Claude Haiku 4.5 and Sonnet 4.5 were sent an effort they do not take.** Anthropic's effort
  page lists every model that takes `output_config.effort`, and those two are not on it.
  The catalogue marks them (`effort=False`) and they are sent none. There is no 400 reader
  behind this one, because the page does not say what the API answers.
- **DeepSeek was asked in a form it refuses.** DeepSeek thinks by default. Its request
  reference says "`required` and named tool choices are not supported in thinking mode; the
  API returns a `400` error", and its thinking-mode guide adds that a request carrying tools
  must send every earlier turn's reasoning back or be a 400 too, which quackd never did.
  quackd sent `required`. It now turns thinking off, in the CLI and in the browser demo, and
  asks with `required` as it did before, which non-thinking mode accepts. `--extra-body` can
  still turn thinking on, and brings both refusals back with it.
- **Catalogue comments the vendors' own pages contradicted, and one missed price.** Gemini
  3.5 Flash-Lite was recorded with no cached-input rate, when Google publishes $0.03 in the
  paid column beside a free tier's "Not available", so a cached prompt on it was charged at the
  full input rate: an overstatement, never an understatement. The GLM block said only the `v`
  models take images, the DeepSeek block named the wrong retired aliases, the Kimi block gave
  its discontinuations one date when there were three, and the Mistral block described
  Leanstral as a model Mistral did not train. The docstrings of `kimi.py` and `meta.py` said
  those vendors document no `tool_choice` values, and both now do. And four places said
  Mistral answers OpenAI's `required` with a 400, which Mistral's own spec, listing
  `required` beside `any`, does not support; quackd still sends `any`, the value its guide
  documents.

### Known limitations

- **Nothing here has been run on a Jetson by this project.** The native arm64 runner has no
  GPU and is not a Tegra, so what it proves is that the image builds and that quackd runs on
  aarch64 Linux. Ollama on an Orin's GPU, the NVIDIA container runtime, `nvpmodel`, how much
  memory a model takes beside quackd, and what the doctor section reads off a real board's
  files are all unproven. [docs/jetson.md](docs/guides/jetson.md) says what to send back from a board
  that can: the `jetson` block of `quackd doctor --json`, a run's `terminal.txt` and
  `transcript.jsonl`, and one `tegrastats` line taken while the model was answering.

- **The Jetson section of `quackd doctor` has never read a real board.** What it reads is tested
  against a board made of files in `tests/test_doctor_and_stub.py`, with `nvpmodel` and
  `docker` answered by stubs, and the arm64 job checks only the other half, that `jetson` is
  `null` on a runner that is not a Tegra. That job gates nothing and does not rerun on a change
  to `quackd/` alone.

- **Nobody has measured what a model server does to a ToddlerBot's fifty hertz control loop on
  the same Jetson, and on that body a starved loop is a fall.** A model server saturating the
  CPU and the memory bus is exactly the load that can starve that loop, and the daemon's
  deadman is what protects the robot there, doing that job for real rather than as a
  formality. Bring the robot up from a laptop first, as the checklist now says. On the crowded
  board, keep it on its stand, watch `tegrastats` while a model answers, and consider pinning
  the model server off the cores the loop runs on
  ([ADR-0044](docs/adr/0044-a-jetson-is-a-host-not-a-body.md)).

- **A `SIGTERM` ends a run without sending the robot a `stop`, and it always has.** quackd
  installs a handler for SIGINT and for no other signal. A run that ends by itself, or by
  Ctrl-C, goes through the loop's `finally`, which sends the `stop`, and a signal other than
  SIGINT skips it: a bare `kill`, a systemd unit's default stop and a `docker compose stop`
  against a service that names no `stop_signal` all end a run that way. Outside a container
  the process dies where it is. Inside one, where quackd is PID 1 with no handler, the signal
  is ignored until Docker's SIGKILL arrives. That is why `deploy/jetson/compose.yml` sets
  `stop_signal: SIGINT`, and anything else you wrap `quackd run` in should send SIGINT too.
  Until this release [docs/safety.md](docs/concepts/safety.md) said the `finally` always sends the
  `stop`, and it now says which ways of stopping quackd skip it.

- **No provider fix in this release has reached a live model.** There is no Anthropic or
  DeepSeek key on the machine that made them, and the one live check tried, a bare `--llm
  openai` on `hello-world`, was refused for want of credit before it took a turn. So GPT-6 Sol
  opening on the Responses API, `auto`, the stepped trim and the blocks left out on the two
  Claude models that need them, the thinking retry on the 4.5 models, no effort for Haiku 4.5
  and Sonnet 4.5, DeepSeek with its thinking off and Meta's one call per turn are each proven
  against the request shape the vendor documents, and against the error text where the vendor
  prints one, which is the vendor's claim and not a measurement. On a Claude model asked with
  `auto`, a turn that answers in prose is newly possible, and how often that happens is
  unmeasured too. So is what DeepSeek loses with its thinking off.

- **Claude Opus 5.5 and Fable 5.1 carry up to nine exchanges' camera frames, and lose their
  own earlier reasoning every eighth exchange.** Every other pilot is sent the frames of its
  last two exchanges. Those two bind each replayed thinking block to everything before it, and
  dropping a frame edits what came before, so their frames are trimmed every eight exchanges
  instead. A trim invalidates every thinking block produced while a frame it took was still
  sent: from the trim call on those are left out, and the API is asked to drop the latest
  turn's, which may not be left out. A run with no frames to trim, under `--no-vision` or on a
  body with no camera, loses nothing. The request is larger between trims than any other
  model's, and the reasoning a trim takes with it does not come back. Keeping every frame
  instead would have passed the API's 32 MB request limit on a long run with two cameras.

- **The catalogue is a snapshot of 2026-09-23.** Rates are the short context band, so a prompt
  over 272k tokens on the ten OpenAI models that have a long-context rate, or over 200k on xAI
  and Gemini 3.1 Pro, is under-costed, and Alibaba's rows are its first input-length tier, so
  `qwen3.7-flash` is under-costed more than threefold on a prompt past 32k and
  `qwen3-coder-flash` past 256k. Three Gemini Flash models and Robotics ER 2 double their rates
  on 2027-01-01, and `gpt-5.6-sol`'s promotional rate is promised only through 2026-11-21. A
  turn a refusal fallback took is priced at the rate of the model that was asked for, not the
  one that answered.

- **Nothing has answered a real robot through a decision LLM, or made a real call to one.**
  Unchanged since 0.12.

- **Two of the four confidence floors are numbers nobody published.** Unchanged since 0.12.

- **A stepper is not on the critical path, and the arithmetic that says it pays is arithmetic.**
  Unchanged since 0.12.

- **Six of the seven bodies have still never run on hardware, and the one that has was running
  0.9 at the time.** Unchanged since 0.10.

- **Not one `cost_usd` has been checked against a bill.** Unchanged since 0.11, although every
  rate was read again on 2026-09-23. `--price` is the answer to a disagreement.

- **`terminal.txt` is most of the screen and not all of it, and it is redacted by name only.**
  Unchanged since 0.11.

- **No pilot flock has been driven by a real model, or by a real robot.** Unchanged since 0.9.

- **A run directory from 0.10 or 0.11 that used the stepper replays with no stepper lines.**
  Unchanged since 0.12.

## [0.12.0] — 2026-09-23

Jev turned out to be the first of many. In the week after it launched, thirty-odd projects
shipped models that answer the same typed questions over the same wire format, most of them
open and several small enough to run on a laptop, and quackd had spelled one vendor's name into
a flag, an extra, three variables, two transcript kinds, a module and a 600-line page. This
release names the thing rather than the vendor.

The pilot is `--llm`, one spec where `--provider` and `--model` used to be two flags:
`--llm anthropic:claude-opus-5`, or `--llm anthropic` for that vendor's default, or
`--llm claude-opus-5` on its own because the catalogue's ids are unique across vendors. The
split is at the first colon, so `--llm ollama:qwen3:8b` keeps the tag. The stepper is
`--decision-llm`, naming which decision LLM answers, with `--decision-url` beside it the way
`--base-url` sits beside `--llm`, and `--decision-mode off|shadow|on` for what its answer is
allowed to do. Naming one defaults the mode to `on`, so the common case is still one flag.

Jev is a preset, and so are Kev, Von, OpenJev, OpenDecision and Laya. Any other server that
speaks `POST /v1/systemone` is `--decision-llm local --decision-url`, and a decision LLM with a
Python API of its own is a plugin under the `quackd.decision_llms` entry point group. Adding a
wire-compatible one to quackd itself is a row of data. What has not changed at all is the part
that matters near a robot: which turns are a choice, what an answer has to clear before it
moves a servo, and the rule that a stepper may never record a verdict, declare an outcome or
end a run. That is quackd's half and it does not move with the vendor
([ADR-0043](docs/adr/0043-decision-llms-are-a-wire-format-and-a-data-row.md)).

Two honest notes, because the rest of this is a robot. Two of the four confidence floors are
numbers TypeSafe publish and two are quackd's own, all four shaped around Jev, and every
other preset inherits every one of them unmeasured while computing confidence by its own
formula. And nothing here has been run against a real robot: the client is tested
against a stub. `--decision-mode shadow` asks a decision LLM every turn, records what it would
have chosen beside what the model did, and changes nothing about the run. It is how that
changes, and `--decision-mode on` says so, loudly, every time you use it.

The old spellings are gone rather than deprecated, and that is a deliberate departure from how
0.11 retired `quackd trace`. Those spellings were three releases old and in people's scripts.
These are three days old, `quackd record` pinned the stepper off so no recording in this
repository carries them, and [ADR-0040](docs/adr/0040-a-discrete-stepper-in-front-of-the-model.md)
already said a transcript kind the renderer does not know is drawn as nothing. So `quackd log`
on a run directory from 0.10 or 0.11 that used the stepper prints no stepper lines at all, still
prints `from jev` on the verbs it chose, and shows no stepper seconds or cost in its counters,
because it is now looking for a `decision` block. The run directory itself is untouched; only
the reading of it is.

### Added

- **Six decision LLMs beside Jev, five of them a row of data and nothing else.** `kev` (Qwen3.5 with a
  decision head, your own GPU), `von` (a 395M encoder, about 18 ms on a GPU, CPU viable),
  `openjev`
  (DiffusionGemma behind vLLM or MLX), `opendecision` (a zero-shot encoder, no GPU),
  `local` (anything else that speaks the format, with `--decision-url`), and `laya`, which runs
  inside the quackd process with no server and no key at all -- the one of the six that is a
  row plus a module, because a checkpoint in this process is not an HTTP call and `laya.py` is
  the proof that the seam is the protocol rather than the transport. A row carries whatever that
  project actually fixes, which for four of them is an address and a model id and for the
  other three is less: `jev` names no address, because the SDK has its own; `laya` has none to
  name; and `local` names neither, which is what `--decision-url` is for. Where a row does
  carry one it is not bookkeeping: OpenJev refuses a pinned Jev version with a 400, and Laya's
  plain name is an alias for its English checkpoint rather than its decision-tuned one, so a
  shared default would have been wrong for both on every request.
- **`quackd[decision]` and `quackd[laya]`.** The first installs the System One protocol client,
  which is one wheel for every server above; the second installs the one that runs in-process
  and pulls torch with it. Neither is in `quackd[all]`, for the same reason `quackd[jev]` never
  was: a decision LLM generates nothing and cannot pilot a robot.
- **A plugin group, `quackd.decision_llms`.** A module exposing `make(spec, *, url, model)` and
  describing itself with `SUMMARY`, `MODEL`, `URL`, `KEY_ENV` and `EXTRA` is found the way an
  adapter is found, named in `--decision-llm`'s completions and listed by `quackd doctor`,
  without quackd having been rebuilt. A name quackd ships always wins over a plugin that took
  it.
- **The record says which decision LLM answered, not only which model id.** `run_start` carries
  `decision_llm` with its name, model and address beside `decision_price`, and the summary's
  block carries `llm` and `url`. `kev-latest` on two machines is two different servers, and a
  transcript that recorded only the id could not tell a reader which one it had.
- **`quackd doctor` lists every preset** with its extra, its key variable, its model and where
  it listens, so "could this run here?" is answerable without starting a task. It does not
  probe any of them, the way it does not probe a cloud vendor.

### Changed

- **Breaking. `--provider` and `--model` are one flag, `--llm VENDOR[:MODEL]`** (short `-l`).
  `QUACKD_MODEL` is `QUACKD_LLM` and now holds a whole spec. `quackd list-models` takes
  `--llm`, and `quackd robot add` and `robot edit` take `--llm` in place of the pair, with
  `--clear llm` where `--clear provider` and `--clear model` used to be. A refusal now names
  where the spec came from, so a bad value in a `.env` three directories up reads differently
  from one still on your screen.
- **Breaking. `~/.quackd/robots.json` stores one `llm` key** instead of `provider` and `model`.
  A file written by 0.11 or earlier is folded on read -- `provider` alone is the vendor, the
  pair together is the spec, and a `model` with no `provider` beside it names no pilot, because
  under the old flags such an entry did nothing unless a `--provider` was typed beside it --
  and the next write stores the new shape, so
  nothing needs migrating by hand. `quackd robot show --json` emits `llm` where it emitted the
  pair. Reading is deliberately lenient: a spec the catalogue can no longer resolve is kept as
  written, and the run that names that robot is what refuses, because refusing on read would
  take `quackd robot edit` down with everything else and leave no way to mend the file.
  `quackd robot add` and `robot edit` check in full, which is where a typo is actually made.
- **Breaking. There is no longer any way to mix a vendor from one place with a model from
  another.** A robot registered against OpenAI and run with `--llm gemini` gets Gemini's
  default, full stop. The guard that used to carry a model across vendors, and the refusals it
  produced, are both gone: a spec carries both halves.
- **Breaking. `--jev off|shadow|on` is `--decision-llm`, `--decision-url` and
  `--decision-mode`.** A script passing `--jev` gets Typer's "no such option". `QUACKD_JEV` is
  `QUACKD_DECISION_LLM` plus `QUACKD_DECISION_MODE`, `QUACKD_JEV_PRICE` is
  `QUACKD_DECISION_PRICE`, and `QUACKD_LIVE_JEV` is `QUACKD_LIVE_DECISION`. None of the old
  names is read. `QUACKD_JEV` is the one that says so, because it is the one that used to
  switch a stepper on and silence there would leave a run quietly not doing what a `.env` line
  asked for; the other three go without a word. Grep your `.env` files and your CI for
  `QUACKD_JEV`, and for `QUACKD_LIVE_JEV` separately, which that string does not match.
  `TYPESAFE_BASE_URL` is not read by quackd, and was not read by 0.11 either: an address is
  `--decision-url` and belongs to every row rather than to one vendor, and that variable has
  always belonged to the SDK, which still reads it for the hosted row. `TYPESAFE_DEFAULT_MODEL` is still
  honoured, for `jev` alone, as the fallback when the spec names no model, but the spec is the
  way to say it: `--decision-llm jev:jev-latest` is a choice the record can show somebody
  made.
- **Breaking. `quackd[jev]` is `quackd[decision]`.**
- **Breaking. The record.** The transcript kinds `jev` and `jev_shadow` are `decision` and
  `decision_shadow`; `jev_choice`, `jev_confidence`, `jev_gate`, `jev_latency_s` and
  `jev_cost_usd` are `decision_*`; `run_start.jev_price` is `decision_price`; the `jev` block in
  `summary.json` is `decision`; `verb_start.source` says `decision`; and a stepper-authored tool
  call's id starts `decision-`. Anything parsing these has to change today. On screen the gutter
  says `decide`, `decide?` and `decide=`, and a verb it chose says `from decision`.
- **Breaking. `quackd/agent/jev.py` is the package `quackd/agent/decision/`**, `RunConfig.jev`
  is `RunConfig.decision` with `RunConfig.decision_llm` and `RunConfig.decision_price` beside
  it, `RunResult.jev_calls` is `decision_calls`, and `JevMode` is `DecisionMode`. The component
  is still called a stepper, because that is what quackd's half of this is.
- **A server you run is costed at nothing, explicitly.** Not `None`, which quackd reserves for
  "there is a rate and nobody here knows it", but the self-hosted `$0` the rest of quackd
  already prices a local model at. Jev is the only preset with a published rate.
  `QUACKD_DECISION_PRICE` is how you say otherwise for a paid endpoint behind `local`.
- **A decision LLM's address is redacted where the run records it.** It can carry a password or
  a credential-shaped query parameter and it can arrive from `QUACKD_DECISION_URL`, which no
  amount of argv redaction reaches, so it is written through the same redaction `--base-url`
  already gets, at the one place that holds it.
- **`docs/jev.md` is `docs/decision-llms.md`.** The 0.11.0 README on PyPI links to the old path
  and will 404 there until the next release replaces that README.
- **One page per decision LLM, under `docs/decision-llms/`.** The hub keeps everything that does
  not change with the vendor, the argument, the SO-101 worked example, the arithmetic, how a
  turn is decided and the presets table, and that table now links a page per row. The seven
  per-preset sections moved out to `docs/decision-llms/<name>.md`, one each in the shape of the
  robot pages under `docs/adapters/`: the catalogue row it has to agree with, what was read
  from that project's README and source on 2026-09-22 and where, what quackd assumes about it
  and what it does about each assumption, and the line **Nothing here has ever answered a real
  robot.** until one of them has. A test reads every row back off its page, so a table cell and a catalogue field
  cannot drift apart again. The hub's own path did not move, so nothing that links it, the PyPI
  README included, had to change.
- **`docs/lerobot-first-run.md` says how to add one to a real arm.** A new final section walks
  the optional path in order: the two extras, `--decision-mode shadow` on `lerobot-lookout`
  first with the line it prints, then `on` and what `from decision` means in the log, which of
  that arm's verbs are answerable and why `move_joints` is not, and which of the seven to point
  it at. It is last and marked optional because a first run wants fewer moving parts, not more.
### Fixed

- **The loud correction about the confidence floors was missing from the one site that is not
  a document.** The line `--decision-mode on` prints on every run said "The confidence floors
  are Jev's published numbers", which is the first entry in the test's own list of forbidden
  phrasings, word for word. It survived the correction because that guard read the markdown,
  `stepper.py` and two ADRs, and never opened `quackd/cli.py`; the intro to this very note
  points a reader at that warning as the place the correction is made loudly. Two things
  changed rather than one. The sentence now says which two floors are published and which two
  are quackd's own. And the guard reads every file under `quackd/` and normalises before it
  searches: a warning this long is split across adjacent string literals in the source, so the
  sentence carries a quote, a newline and an indent in the middle of itself and matched
  nothing even once the file was in scope. Markdown wraps prose for the same reason, so the
  same normalisation closes the same hole in the documents. Proved by putting the shipped
  sentence back and watching the guard fail.
- **Two shipped release notes had this release's fixes pasted into them.** The last nine
  `Fixed` bullets of this section, from **Three install lines a reader could not paste** down,
  were also written into the 0.9.0 and 0.5.0 sections, each of which then
  carried a second `Fixed` heading describing `kev`, `openjev`, `laya` and `von serve` -- none
  of which existed on 2026-09-15, and none of which existed at all on 2026-09-03, since Jev
  itself launched after 0.5.0 shipped. 114 lines, and nothing failed, because the changelog is
  excluded from the living-document checks on purpose: it records what was true at a release
  rather than what is true now. They are out, and the whole of the file from `## [0.11.0]`
  down is byte for byte what v0.11.0 shipped, but for the two `docs/jev.md` links that this
  release's rename turned into plain code spans rather than leave pointing at a deleted file.
- **The one-second bound was never in force, and a stepper could hold a turn for ten.** The
  page has said a second since 0.10 and what was actually set was `RetryPolicy.timeout`, which
  is the budget for the retry sequence rather than for a request; the per-request timeout is a
  different keyword whose default is ten seconds. Measured against a socket that never answers
  it was 10.14. That is the whole argument for a stepper inverted: the turn it was meant to
  save costs more than the model's. The bound is now a `wait_for` around `decide` in the
  stepper, which is the one place it can hold for every backend including the one running in
  this process with no timeout of its own, with the client's own kept beside it so a socket is
  not held past the turn. A turn that runs out says how long it waited, because a bare
  `TimeoutError` stringifies to nothing.
- **A verb the task file gated on a person answered to the wrong floor.** `Executor.
  needs_confirm` has always read the `.duck`'s own `verbs.confirm` as well as the manifest's
  safety class, and `verb_class` read only the manifest, so a verb its author gated cleared
  the 0.85 motion floor where the executor would have stopped and asked. Under `--yes` nothing
  else would have noticed. The contract's list reaches the floors now, with `stop` exempt
  exactly as the executor exempts it.
- **The streak backstop could not fire on a short duck.** `MAX_IN_A_ROW` is eight and
  `hello-world` allows five steps, so on a duck whose whole budget is smaller than the constant
  the streak was unreachable and the model could be consulted once, or not at all, before the
  run ended on `max_steps` -- which is the failure that constant's own docstring says it
  prevents. The limit that holds is now that number or half the run's budget, whichever is
  less. What that buys is not parity, and the bullet said parity: a streak ends by handing one
  turn to the model and starting the count again, so the shape is a run of stepper turns and
  then a model turn, not one each. What it guarantees is that on a budget of two steps or more
  the stepper cannot answer the whole run. One step is the exception and the arithmetic says
  so: the limit is at least 1, so a single-turn run can be the stepper's alone.
- **A question nobody answered read as an answer of no, and a confidence spelled `true` cleared
  every floor.** Both are reads of a backend's reply that shipped in 0.10. The two Nouls are
  the gates that stop a run and ask for a person, and both were read with a default of `0.0`:
  read that way, a reply carrying only `next_verb` said "certainly not finished, certainly
  nobody needed", which is precisely the pair of answers that lets a verb through to a servo.
  They hand the turn to the model now and the record says `null`, so a reader can tell a
  backend that said "not done" from one that said nothing. Separately `float(True)` is `1.0`,
  above the highest number in the table, so a backend writing `true` was believed absolutely
  rather than doubted; `_finite` rejects `bool` alongside NaN and the infinities now, for the
  reason it already rejected those. Neither was reachable through the one backend 0.11 shipped,
  and both are reachable through a plugin, a server that drops a field, or a model with no
  Nouls at all, which is what this release adds.
- **Three install lines a reader could not paste.** `kev` lost the `cd kev` between its clone
  and its `uv sync`, so a copied command synced whatever directory you were standing in.
  `openjev` was missing the `--ipc=host` vLLM needs and the Hugging Face cache mount, without
  which every container restart downloads about 18 GB again, and it carried its MLX
  alternative inside the same string, so the whole cell was not a command. `laya` carried its
  own parenthetical the same way and left `quackd[laya]` unquoted, which zsh eats as a glob
  before pip sees it. Each row now holds one command and nothing else, and the note that used
  to be wedged into it is on the page.
- **`von serve` was handed to readers with its own default bind.** Its `--host` defaults to
  `0.0.0.0`, which is every interface on the machine, and it authenticates nothing unless
  `VON_API_KEY` is set. The row now says `--host 127.0.0.1`, which is where its page already
  told you to put it.
- **The confidence floors were credited to TypeSafe, and two of the four are quackd's own.**
  Their confidence page publishes 0.5 and 0.9, and both are here as the brake and the
  confirm-gated floors. The read floor at 0.60 and the motion floor at 0.85 are quackd's,
  set between those two, and nothing published sits there. Every live site now says which is
  which: the `FLOORS` comment in `stepper.py`, the floors table and both warnings in
  `docs/decision-llms.md`, all seven decision LLM pages, two README rows, the changelog's own
  honest-notes paragraph, the first-run guide and the amendment notes on ADR-0040 and
  ADR-0043. A test forbids the old phrasings, because this one was written once and then
  copied, and it survived a first correction that only caught half the copies. A number
  nobody published is a number nobody has calibrated either.
- **A docs guard that could not fail.** The test asserting every decision LLM page has a
  `VERIFIED` section was satisfied by the word `UNVERIFIED`, so only one of the pair was ever
  really checked. Both are matched as headings now, and the honesty line is matched literally
  rather than by the word *never*, which turns up in ordinary prose.
- **Laya reports a token count, and quackd said it did not.** `laya/agent.py` returns
  `usage.input_tokens` as `int(attention_mask.sum())`, a real count of what the model read,
  so its turns are billed measured and print without the `~`. The module docstring, the page
  and `tests/fake_laya.py` all said the opposite, which left the fake standing in for a shape
  Laya never produces and the suite exercising the estimate path for the one backend that
  does not take it. The fake now returns the real shape, probabilities on a choice included,
  and a test reads the count back.
- **Von accepts a model id, ignores it, and deliberately does not hand it back.** Its engine
  overwrites whatever was asked for with `von-1.1.0` before the backend sees it, on purpose,
  so a run's record and the server's answer name two different strings. The page had said
  first that the id was neither routed nor echoed, then that it was echoed; it is the former.
  The same re-read retired a second backend, a 512-token truncation and a `--backend` flag
  that upstream no longer has, and corrected its CORS credentials, its `transformers` floor
  and its weights repository.
- **Every number a page attributes now survives a re-read.** Kev's latency figures had been
  replaced upstream (149 ms and 721 ms through MLX on an M5, not 329 ms and 779 ms), which
  also reversed the page's conclusion that the 4B does not fit inside the one-second budget.
  Its state constants were renamed, its probability rounding is four decimal places rather
  than two, and every line-number citation on every page is gone in favour of the file and
  the symbol, because the numbers had already drifted on a page dated the same day.
- **Three smaller claims that were not true.** `DecisionMissingKey` refuses the stepper, not
  the run, and the run carries on without it. The SO-101 section of the first-run guide
  listed gaze verbs the arm does not have and omitted two it does. `local.md` claimed a live
  round-trip that only runs behind an opt-in environment variable.
- **Two pasted log blocks that could not have come from a run.** The first-run guide's
  decision blocks used the ASCII gutter on a page that uses the Unicode one, and printed
  token counts bare where quackd prints a `~` for a figure it estimated itself. Both are
  output from real runs now, re-rendered through the same renderer the CLI uses.

### Removed

- **`quackd trace`, `--trace/--no-trace`, `--trace-prompt/--no-trace-prompt`, `QUACKD_TRACE`,
  `QUACKD_TRACE_THINKING` and `QUACKD_TRACE_PROMPT`**, the spellings 0.11 kept alive for one
  release beside `quackd log`, `--log/--no-log`, `--log-prompt/--no-log-prompt`, `QUACKD_LOG`,
  `QUACKD_LOG_THINKING` and `QUACKD_LOG_PROMPT`, along with the `sys.argv` scan in
  `quackd/cli.py` and the environment fallback in `quackd/log.py` that existed only to carry
  them. 0.11 said in twelve files, from the flags' own help text to the warning line itself,
  that they would go here, which is the promise 0.4 made about `--transport` and 0.5 kept. The
  subcommand and the flags are refused the way any unknown one is, naming what they are, with
  exit code 2 and no run directory written. The three variables are not simply dropped, and
  that asymmetry is the point: a flag that is gone fails loudly because Click refuses it, while
  a variable that is gone says nothing at all, and `QUACKD_TRACE=0` going unread would switch
  the log back on for the one reader who had deliberately turned it off. So a run that finds
  one set prints `QUACKD_TRACE is not read any more and this run ignores it; set QUACKD_LOG
  instead`, the line this release gives `QUACKD_MODEL`, and carries on. It is read where the
  command line is rather than where the value used to be consulted, so a `.env` hears about
  every old line in it on any one run rather than only the name that run would have read,
  which is the limit 0.11 described and asked you to treat as a floor. The line is replayed
  into the top of `terminal.txt` where the flag warnings used to be. Not removed:
  `trace_dropped` is still read out of a `summary.json`, because a run directory outlives the
  release that wrote it and 0.11 scoped this to what you type on a command line or set in a
  shell.
- **`--jev`, `QUACKD_JEV`, `QUACKD_JEV_PRICE`, `QUACKD_LIVE_JEV`, the `live_jev` pytest marker
  and the `quackd[jev]` extra**, with no alias, for the reasons above. Only `QUACKD_JEV` says
  anything when it is found set, as the Changed bullet above describes; the other five go
  without a word, and a flag among them fails loudly by itself.
- **`--provider`, `--model`, `QUACKD_MODEL`, and `list-models --provider`/`-p`.**

### Known limitations

- **Nothing in this release has answered a real robot, or made a real call to any of these
  services.** Every decision LLM here is exercised against a stub or a fake: not TypeSafe's
  hosted API, not a `kev`, `von`, `openjev` or `opendecision` server, not `laya` in this
  process, and none of them on hardware. So there is no agreement rate, no calibration curve
  and no measured latency for any of the seven. Every latency on those pages is the figure
  that project publishes for itself, on its own hardware and its own task, copied out on the
  day it was read: TypeSafe's for Jev, and this note repeats two more of them, von at about 18
  ms on a GPU and Kev at 149 and 721 ms through MLX on an M5. Not one of them was measured
  here, through quackd, answering a robot. `--decision-llm` ships off unless you
  name one, and `--decision-mode shadow` exists to be the thing that changes this: it asks
  every turn, records what it would have chosen beside what the model did, and changes nothing
  about the run.

- **Two of the four confidence floors are numbers nobody published, and all seven presets
  inherit all four unmeasured.** TypeSafe's confidence page publishes 0.5 and 0.9, and both
  are here as the brake and the confirm-gated floors. The read floor at 0.60 and the motion
  floor at 0.85 sit between those two and are quackd's own. Every one of the four was shaped
  around Jev, and the other six rows are gated on them while computing confidence by their own
  formula, on their own architecture, with their own idea of what a probability means. A
  number nobody published is a number nobody has calibrated either.

- **A stepper is not on the critical path, and the arithmetic that says it pays is arithmetic.**
  The break-even in [docs/decision-llms.md](docs/guides/decision-llms/README.md) is a stepper answering in
  1.40 seconds; under that it pays for itself and over it is a net loss. That figure comes from a worked example and
  not from a run, and the one-second bound this release put in force is the ceiling rather
  than the observation.

- **Six of the seven bodies have still never run on hardware, and the one that has was running
  0.9 at the time.** Unchanged since 0.10.

- **Not one `cost_usd` has been checked against a bill.** Unchanged since 0.11. Every rate was
  read off a vendor's pricing page by hand on 2026-09-21, and a rate read by hand is wrong from
  the day the vendor edits the page until somebody reads it again, silently. `--price` is the
  answer to a disagreement.

- **`terminal.txt` is most of the screen and not all of it, and it is redacted by name only.**
  Unchanged since 0.11. A credential under a name that is not on the list is kept, and so is a
  key typed inside the JSON of `--extra-body` on a command line. Read the file before you paste
  a run directory into an issue.

- **No pilot flock has been driven by a real model, or by a real robot.** Unchanged since 0.9.

- **A run directory from 0.10 or 0.11 that used the stepper replays with no stepper lines.**
  The record's Jev-named kinds were renamed rather than aliased, and ADR-0040 said from the
  start that a transcript kind the renderer does not know is drawn as nothing. So `quackd log`
  on such a directory prints no stepper lines, still prints `from jev` on the verbs it chose,
  and shows no stepper seconds or cost in its counters. The directory itself is untouched; only
  the reading of it is.

## [0.11.0] — 2026-09-22

A run records when it started, when it ended, where its seconds went and what it cost, and you
can give it a name. Until now the only absolute time a run had was the name of its directory,
which is the local clock at second precision and gone the moment anybody renames the folder, and
the nearest thing to a cost was a token count, which is not a cost: it is one of the two numbers
a cost is made of. `summary.json` carries both halves now, per call and per run, and `quackd
log` reprints them from a run recorded weeks ago.

What is measured and what is arithmetic is worth separating once, plainly, because the rest of
this is money. Measured: `wall_s`, `connect_s` and `llm_latency_s` come off a clock in this
process, and the token counts are the vendor's own, returned beside the answer. One count is
not, and it is the exception that proves the rule: where TypeSafe's stepper answers without a
usage, quackd estimates the count from the request it sent, and says `usage_estimated` wherever
that number goes. Arithmetic: every `cost_usd` in this release is a count multiplied by a rate,
and the rates a person read off a vendor's own pricing page on 2026-09-21 are in
`quackd/agent/providers/catalogue.py`, with the stepper's own in `quackd/agent/jev.py` because
there is one model behind that flag rather than a table. The rest are not read off anything:
`--price` and `QUACKD_PRICE` are yours, and a local model and the fake provider cost nothing per
token by construction.

quackd has never seen an invoice, and not one figure here has been reconciled against a bill. It
knows nothing about your discount, your committed tier, your free credits or a minimum charge,
and it cannot know that a rate moved this morning: a price read by hand is wrong from the day the
vendor edits the page until the day somebody reads it again, and wrong silently. So every price
is recorded with where it came from, and with the date quackd read it where quackd is the one
that read it: a rate you handed in carries no date, because stamping it with quackd's check date
would be quackd vouching for a number it has never seen. Every run keeps the rate it was
actually costed at, and `--price` is there for the moment quackd's number and your bill disagree.

Reading those pricing pages caught two counting bugs quackd has been shipping. Google reports
thinking beside the answer rather than inside it, and quackd read `candidates_token_count` alone,
so every thinking Gemini run has been under-reporting the output it was billed for by however
much it thought. Anthropic reports its three input buckets disjoint, and quackd read only the
bucket that was neither read from a cache nor written to one. Both are corrected here, and the
second is a no-op today, because nothing in quackd sets `cache_control` yet: it is the arithmetic
that stops being a no-op the day something does. The loop had a smaller one of the same shape,
reading `perf_counter` twice on a model turn and disagreeing with itself about how long the call
took. It times a call once now, and the record and the running total read that one number.

The trace is the log, and it is now the whole screen as well. The record outgrew the word: it
holds every intent the robot was sent, every gate the executor closed, the clocks and the money
above, and from this release what was on your terminal while all of it happened. `quackd trace`
is `quackd log`, `--trace/--no-trace` is `--log/--no-log`, `--trace-prompt/--no-trace-prompt` is
`--log-prompt/--no-log-prompt`, `QUACKD_TRACE`, `QUACKD_TRACE_THINKING` and `QUACKD_TRACE_PROMPT`
are `QUACKD_LOG`, `QUACKD_LOG_THINKING` and `QUACKD_LOG_PROMPT`. Every one of those spellings,
which is everything you type on a command line or set in a shell, still works for this release,
printing one line on stderr saying what it is called now, and every one of them goes in 0.12,
which is the same single release of grace `--transport` got in 0.4 before 0.5 removed it. Three
things do not get that grace and are the three to change today. An MCP client that reads `trace`
off a tool result must read `log`. Anything that reads `trace_dropped` out of a summary should
read `log_dropped`. And `quackd/trace.py` is `quackd/log.py` with its classes renamed after it,
with no module left behind at the old path, so `import quackd.trace` raises rather than warning:
a name you import is not a spelling anybody can be told about at runtime. quackd's own replay
takes either spelling of the dropped counter, so a run directory recorded before this release
still replays; nothing here writes the old one any more.

What you watched is kept as well. Every run directory gets a `terminal.txt`: the screen as plain
text with no colour codes in it, opening with two lines: the command that started the run, and
then the version that ran it with the UTC time it opened and the directory it ran in. It carries
the questions you were asked and what you answered, and neither of those reached the file the
way the rest of the screen did. The question is written straight to the terminal rather than
through either of quackd's consoles, and your keystrokes are echoed by the terminal itself, so
the tee sees neither half. quackd writes the exchange into the file itself instead, which is
why it is there at all. Control characters are taken out on the way
in, every C0 code except tab and newline, and DEL, so a goal, a downloaded `.duck` body, a model
or a robot that puts an escape sequence in its text cannot drive the terminal of whoever reads
the file with `cat`. It is the screen and not the record, so `--no-log` empties most of it
exactly as it empties the terminal, and `transcript.jsonl` is written either way as always, with
every one of those characters still in it and JSON-escaped, so nothing is lost by dropping them
from the file a person reads.

Before you paste a run directory into an issue, know what is now in it. The command line at the
top has the values of `--api-key` and `--token` replaced by `***`. `--base-url`, `--address` and
`--camera-url` keep their host and path and lose their password and any credential-named query
parameter, so `https://rok:hunter2@gw/v1?api_key=sk-live` is recorded as
`https://rok:***@gw/v1?api_key=***`. `--extra-body` has its credential-named keys replaced where
the run records the object, which is the only place a body set through `QUACKD_EXTRA_BODY` could
ever be caught, and not where it records your command line: a key typed inside that JSON on the
command line is in the record in the clear. Nothing else on that screen is redacted, and that
includes the working directory on the header's second line: it is the absolute path as it was,
so a run started from a home directory puts your account name in the file.

### Added

- **Timing in the record: `started_at`, `ended_at`, `wall_s`, `connect_s` and `llm_latency_s`.**
  `run_start` carries the wall clock read in the same breath as the monotonic zero that every
  record's `t` counts from, so one absolute time at the top of a transcript places all of them and
  no intent has to carry an ISO string of its own. `run_end` and `summary.json` carry the rest,
  and each answers a different question: `wall_s` is how long the whole thing took, `connect_s`
  how much of that went on reaching the robot before anything was asked of it, and
  `llm_latency_s` how much went on waiting for a model, summed over every call including one that
  raised, because a run that died on a timeout spent that time too. `ended_at` is derived as
  `started_at + wall_s` rather than read off the clock a second time, so `ended_at` minus
  `started_at` is `wall_s` in every record quackd writes, including on a laptop that synced its
  clock or slept through part of a run. `elapsed_s` keeps exactly the meaning it had, which is
  the budget clock: it starts later than the record does, it restarts after a `--by-hand`
  handover, and it runs on the transport's own clock, so on a simulator it is the simulator's.
  The two are far apart and that is not a bug. The example run below recorded `elapsed_s` 11.9
  against a `wall_s` of 0.359, because 11.9 seconds of simulated duck happened in about a third
  of a second of laptop.
  Reading one as the other is the mistake this record invites, so they are named apart and said
  apart here.

- **Cost in the record: `cost_usd` on every model call and on the run.** The `llm` line prints
  what that call cost and what the run has spent so far, `summary.json` keeps the total and the
  rate it was computed at, and the verdict counts it:
  `tokens  in=1734 out=16 (run total in=10421 out=96 $0.0327) latency=0.0 s cost=$0.0054 stop=tool_use`.
  The rates are USD per million tokens off each vendor's own pricing page, read on 2026-09-21, and
  112 of the 115 models in the catalogue carry one. The three that do not are Cohere's
  `command-a-plus-05-2026`, `command-a-03-2025` and `command-a-reasoning-08-2025`, which is the
  whole Command A family and includes that vendor's own default: Cohere publish per-token rates
  for Command R and R7B only and sell Command A as dedicated instances by the hour, which is not
  a number of dollars per token and cannot be made into one. A run on one of those records
  `cost_usd: null` and prints `cost unpriced`. It must not print `$0`, because `$0` is a thing
  quackd also says truthfully: several GLM Flash models and Mistral's Leanstral are priced at
  zero by their own vendors, and a model with no published rate is a different thing from a model
  that is free. Where a prompt was partly cached the cached slices are billed at their own rates
  and the remainder at the full input rate, and where a vendor publishes no cache rate the cached
  tokens are charged at the full input rate rather than skipped, which overstates on purpose. Of
  the two ways to be wrong about somebody's bill, the low one is the one that gets them in
  trouble.

- **The stepper's own bill, measured where TypeSafe report a usage and estimated where they do
  not.** Jev's published rate is $0.042 per million input tokens with output not charged
  ([their models page](https://docs.typesafe.ai/models), read 2026-09-21), and it is the one rate
  in quackd that does not live in the catalogue, because there is a single model behind the flag
  rather than a table. Where the API reports an input count the bill is measured from it. Where
  it does not, which their own SDK types as a possibility, quackd estimates from the size of the
  request it actually sent, the named state and the four questions together at four characters to
  the token, and flags the figure as estimated everywhere it surfaces: `usage_estimated` on the
  turn, `cost_estimated` in the summary, and a `~` in front of both numbers on the console,
  `jev     place 0.62 < 0.85, to the model (0.44 s, ~612 tok ~$0.000026)`. A call that raised
  after the request went out is billed, because the tokens went out with it. The gates that
  refuse a turn before the network is touched, `not_offered` and `state_too_large`, add nothing
  at all, and a machine with no `typesafe_sdk` installed owes nothing.

- **`--price`, `QUACKD_PRICE` and `QUACKD_JEV_PRICE`, for the rate quackd does not have.**
  `--price "in=3,out=15,cache_read=0.3,cache_write=3.75"`, USD per million tokens, `in` and `out`
  required and the two cache rates optional. It is validated at the top of the run, before
  anything connects and before a directory is made, so a typo costs one line and no half a run on
  disk: *--price 'junk' is not a price: 'junk' has no `=`. Write it as
  in=3,out=15[,cache_read=0.3,cache_write=3.75], in USD per million tokens.* `QUACKD_PRICE` is
  the same thing for a shell that runs many and `QUACKD_JEV_PRICE` the same for the stepper, and
  an override beats everything, including the zero the fake provider would otherwise report,
  because a rehearsal you want costed is a reason to pass a rate. Every price written into a
  record says where it came from, which is `--price`, `QUACKD_PRICE`, `QUACKD_JEV_PRICE`,
  `catalogue`, `published`, `fake` or `self-hosted`, so any figure can be traced back to the
  rate that produced it. The two quackd read off a vendor's page, `catalogue` and `published`,
  carry the date they were read on; the other five carry no date, because a rate somebody
  handed in is theirs to vouch for and stamping it with quackd's own check date would be quackd
  vouching for it instead. A local model is `self-hosted` and costs nothing per token by
  construction, which is a different claim from a vendor charging nothing and is labelled as one.

- **A run can be named: `--run-name` on `quackd run` and `quackd record`, and a pass in `quackd
  log` that resolves one.** This is for the person running a hundred examples on one arm in an
  afternoon, who ends the day with a hundred directories named after the second they started and
  no way to tell which was which without opening them. `--run-name "Example 1"` slugs to
  `example-1` and goes into the directory name after the duck and before the collision counter,
  `runs/20260921-160009-find-and-kick-example-1/`, and the text as you typed it is kept in
  `run_start`, `run_end` and the replay header, which grew a `run name` row beside new `started`
  and `price` rows. `quackd log example-1` then finds it: an exact label match is tried ahead
  of both the timestamp prefix and the loose substring, so `example-1` resolves to the run you
  called `Example 1` even when `example-19` was recorded later, and a run you called `20260921`
  is reachable by that name rather than being answered by whichever run's timestamp starts the
  same way. The label is matched against what follows the stamp and only where a duck name
  precedes it, so `quackd log find-and-kick` still means the newest run of that duck rather
  than the one that happens to have gone unnamed. A name with nothing ASCII in it is refused
  before anything connects, *--run-name '!!!' has no ASCII letters or digits in it, so there is
  nothing to name the directory after*, rather than quietly becoming something else, because a
  hundred directories that all say `run` is the problem the flag was reached for. Accents fold
  rather than drop, so `Café 1` and `Cafe 1` land in the same place. A name adds up to 65
  characters to every path inside the run, frames included, so on a Windows box without long
  paths enabled a long enough one is how a `--run-name` used to become a bare traceback out of
  the standard library. It now names the path it could not make, counts the characters in that
  path once resolved, which is the length the filesystem actually refused and not the shorter
  relative one you typed, and says that a shorter `--run-name` or a shorter `--runs-dir` will
  fit, because the length is the whole diagnosis and nothing else in the message hints at it.
  Both are `quackd run` options and both reach a flock: a `--flock N` simulator flock and a
  stored flock of pilots each take the name into their own summary and cost every member at
  the one rate. `quackd record` takes `--run-name` and not `--price`: a recording is still
  costed at whatever rate the catalogue or `QUACKD_PRICE` gives it, and the one thing it cannot
  be handed is a rate on the command line.

- **`terminal.txt`, the session as plain text, in every run directory.** The transcript says what
  quackd did and this says what you saw while it did it: the header, the narration, the warnings,
  the questions and your answers, the verdict, in the order they appeared and with no escape codes
  in them, so it greps and diffs like any other file. It opens with the command that started the
  run, then the version, the UTC time the capture opened and the directory it ran in. That time is
  its own reading of the clock, taken before the run directory exists and truncated to the second
  rather than rounded, so it never names a later second than the `started_at` in `summary.json`,
  which stays the one to quote. The answers in it are quackd's account of what you said rather than
  a copy of your keystrokes: where the kill switch is reading the terminal it writes down what you
  typed, and where it is not, `typer.confirm` has already reduced the answer to a yes or a no and
  the file says `y` or `n`. Nothing is written until there is a run directory, so what quackd
  refuses before it makes one still leaves nothing behind: a bad `--price`, a `--run-name` with
  nothing to slug, a `.duck` that does not parse. A run that gets a directory and then fails to
  reach its robot has always left one, and now it has the screen in it too. `--no-log` takes the
  narration out of the file as it takes it off the screen, because this is the screen;
  `transcript.jsonl` is unaffected and still not optional. A flock keeps one at the run root rather
  than one per member, because there was one terminal however many robots were narrating into it. If
  the file cannot be opened at all the run does not notice: the capture disables itself and the
  terminal never pays for it. If it breaks after it was opened, which is the case that matters, the
  file does not just stop: it ends with the reason it stopped, and the screen says `terminal.txt is
  not the whole session` and names it. A truncated record that declares itself is recoverable, and
  one that does not is a wrong answer to the only question the file exists to answer.

- **The command and the version are in the record: `command` and `version` in `run_start`, `run_end`
  and `summary.json`.** `command` is the argv as a list with `argv[0]` replaced by the literal
  `quackd`, so it says what was run rather than which interpreter path ran it, and the values of
  `--api-key` and `--token` are `***` in both spellings a shell allows, `--api-key sk-…` and
  `--api-key=sk-…`. The flag itself stays, because *that* a key was passed is usually what a reader
  is checking, and a trailing `--api-key` with nothing after it is left as it was typed. Redaction
  is by flag name and nothing else: a secret passed some other way is not found by this, and the
  rest of `terminal.txt` is not redacted at all.

- **A `prompt` event, for the questions a person was actually asked.** `{what, question, answer}`
  with `what` one of `confirm`, `decide`, `acknowledge` or `hand_off`: the confirm gate, the
  feasibility verdict, an acknowledgement, and each half of a `--by-hand` handover. The confirm
  gate's question changed shape to be worth quoting: it was built from a raw dict and read
  `run kick({'force': 0.5})?`, and it is `run kick(force=0.5)?` now, through the same formatter
  the log line uses, because the record has to quote the question in the words it was put in
  and those words should be the ones a person would write. What an
  answer *caused* was already recorded by whoever acted on it; this is the exchange itself, in
  the words it was put in, which nothing held before. It is written only where somebody was
  really there, so a `--yes` run and a flock member's standing answers produce no `prompt` rows
  at all. A record claiming a human authorised a verb when no human was asked would be worse than
  no record. A run with no terminal under it counts as nobody being there, and that is the part
  to read if you script quackd: `yes | quackd run` and `quackd run < answers.txt` reach the same
  prompt, `input()` reads the pipe, and the confirm gate opens on that answer exactly as it
  always has. Only the testimony changed. There is no `prompt` row, and the gate's own `reason`
  stopped saying `a human said yes` on a run nobody was watching: it reads
  `the confirm gate was allowed` or `the confirm gate was denied`. If you parse gate reasons,
  those two strings are new. A job with no stdin at all is unchanged in every way: the prompt
  raises, and the gate denies and says which exception it raised on.

- **`on_run_dir` on both flock runners.** `run_pilot_flock` and `run_flock` take
  `on_run_dir: Callable[[Path], None] | None`, called the moment their run directory exists and
  before any member has written into it. The CLI passes the terminal capture; anyone embedding a
  flock now has one hook for *the directory this run will fill*, which previously could only be
  learned from the result after the run was over.

- **The arm from a chat is a second half of the first-run page rather than an appendix to it.**
  [docs/lerobot-first-run.md](docs/adapters/lerobot/first-run.md) walked the terminal path in fifteen
  steps and then closed with a twenty-eight-line appendix for MCP, which is a pointer rather
  than a path somebody can follow. It is two parts now that share the steps which set the arm
  up, and Part 2 is fifteen of its own, M00 to M14, one for each of Part 1's: both clients
  configured, what each of the nine tools answers, what every refusal means,
  and which moments move the arm without anybody asking for it. The README's quickstart offers
  the two paths rather than the one. Every result quoted in Part 2 was captured against
  `lerobot:mock` and is labelled as the mock where it is quoted, because no MCP session has
  driven a real arm yet, and that is said on the page rather than left to be worked out. An
  adversarial pass over the draft corrected six things about what the arm does, three of them
  blocking, and the worst of those told a reader that `stop` brakes a moving arm. It does not:
  the verb never sets the executor's abort, so a `move_joints` already running is not cancelled
  and keeps re-sending its own goal until it finishes. [docs/safety.md](docs/concepts/safety.md)
  grew *Who the record says was asked*, for the `prompt` event below.

- **[ADR-0041](docs/adr/0041-the-record-says-when-it-ran-and-what-it-cost.md)**, on why a run
  records money at all, what the record is and is not evidence of, and where the arithmetic
  stops.

- **[ADR-0042](docs/adr/0042-the-log-is-the-whole-screen.md)**, on why the trace became the log,
  the name collision that rename had to route around, why the terminal is teed rather than
  recorded, and which spellings were kept for a release and which one was not.

### Changed

- **Breaking. The MCP tool result key `trace` is now `log`, and there is no second spelling.**
  `robot_run_verb`, `robot_observe`, `robot_say` and `robot_assess_task` come back with `log`, a
  list of at most thirty lines, and `robot_observe` appends the same thing as a text block headed
  `log:`. Change `result["trace"]` to `result["log"]`; a client that used `.get("trace")` will
  otherwise show an empty log and say nothing about it. This is the one place the old name is
  gone outright rather than kept for a release, because the reader there is a model, it learns
  the key from the tool description on every session and there is no yellow line it would ever
  see. Carrying both would spend a second copy of up to thirty lines of context on every call
  that touched a robot.

- **Breaking. `trace_dropped` is `log_dropped`, in `summary.json` and in `run_end`.** If you parse
  a summary, read `log_dropped` and fall back to `trace_dropped` for the runs you already have.
  That is what quackd's own replay does, which is why a run directory recorded before this
  release still replays with its counters intact, and nothing in quackd writes the old name now.

- **Breaking. `quackd/trace.py` is `quackd/log.py`, and its classes were renamed with it.**
  `TraceEvent` is `LogEvent`, `TraceLine` is `LogLine`, `TracedTransport` is `LoggedTransport`,
  `ConsoleTrace` and `LineTrace` are `ConsoleLog` and `LineLog`, `Tracer` is `EventLog` and the
  attribute holding one is `.event_log`, `trace_enabled_default` is `log_enabled_default`,
  `intent_trace_line` is `intent_log_line`, `MCP_TRACE_MAX_LINES` is `MCP_LOG_MAX_LINES`, and
  `RunConfig.trace`, the live view, is `RunConfig.view`. `import quackd.trace` raises.
  If you embed quackd rather than calling it, the signatures moved with the module and they are
  the half a rename does not announce: `build_server` and `build_fleet_server` take `log=` where
  they took `trace=`; `run_flock`, `run_pilot_flock` and `FlockMember` take `view=`;
  `TraceFactory` is `ViewFactory`; `FlockCoordinator.trace` and `AgentLoop.tracer` are both
  `.event_log`; `Executor.traced_transport()` is `logged_transport()`; `RunResult.trace_dropped`
  and `FlockResult.trace_dropped` are `log_dropped`; and `plan_flock_task` returns six values
  rather than five, the new one being what the planner's own call spent. Every one of those
  parameters is keyword only, so the call raises `TypeError` and names the argument rather than
  quietly doing nothing. One of them can fail quietly and is the reason this list is here:
  `run_flock` and `run_pilot_flock` have taken a `log=` of their own since long before this
  rename, which is the `--verbose` callback. Renaming `trace=` to `log=` on those two by hand,
  the way the rest of this release reads, hands the view to that callback and leaves the run
  with no live view at all. `run_pilot_flock` types its `log` as a `Callable[[str], None]`, so
  a type checker says something; `run_flock` types it `Any`, so nothing does, and that call
  fails silently at runtime by quietly doing nothing. On both the new name is `view=`. Two names
  deliberately did not move: `RunConfig.log` and `Executor.log` are still the `--verbose`
  callback they have always been, and `member_log` is still the record kind a flock member's free
  text lands in, because both are contracts that predate this rename and neither is the event
  stream.

- **The `Usage` convention is written down, and two vendors' numbers move to match it.**
  `input_tokens` is the whole prompt, cached parts included; `cache_read_tokens` and
  `cache_write_tokens`, both new and both on every usage object quackd builds, are the slices of
  that prompt billed at cache rates; `output_tokens` is everything generated, thinking included;
  and `reasoning_tokens` is the part of the output the vendor counts apart, a subset of it, never
  priced again. Gemini's `output_tokens` now adds `thoughts_token_count`, because Google's own
  reference defines the total as prompt plus thoughts plus response candidates, three addends,
  so a thinking run was under-reporting output it was billed for by exactly the thinking.
  Anthropic's `input_tokens` now adds its two cache buckets, because that vendor is the one that
  reports the three disjoint, and its `reasoning_tokens` comes from the thinking count in
  `output_tokens_details` rather than from a field that was never there. OpenAI's two parsers and
  the local providers report the same totals they always did, deliberately so, because those
  prompt counts already contain the cached part. What is new for them is that the cached slice is
  named beside the total instead of being invisible inside it, and on OpenAI that is not
  cosmetic: `cost_usd` takes the cached tokens out of the prompt and bills them at the cache
  rate, which on most of this vendor's models is about a tenth of the full one, and that vendor
  caches without being asked. From the second turn of a run most of the prompt is that slice, so
  the figure is well under the prompt times the input rate, and it is right rather than
  generous. This is where it differs from the Anthropic correction above, which is a no-op
  until something sets `cache_control`.

- **A solo run's verdict panel and `quackd log` print the same counters, through one function.**
  They were two lists that had already drifted, and both gained a time counter and a cost
  counter:
  `steps 4 · llm calls 6 · tokens 10421+96 · time 0.4 s (model 0.0 s) · cost $0.0327`,
  with the separator falling back to a hyphen on a stream that cannot carry the dot. A flock
  counts different things and is only partly in: each of the two runners builds its own list
  live, a coordinator naming its kicker and its auctions and a pilot flock naming how many
  members succeeded, but on replay only a coordinator is recognised, by the `kicker` in its
  summary, and a pilot flock is read back through this one function like a solo run. The time
  counter carries the split and not only the total, because on the one hardware run this project
  has, 62.1 of 78.8 seconds were spent waiting on the model, and that ratio is the most useful
  thing a run measures about itself; the stepper's seconds join it when one ran. The cost counter
  reports an unpriced model and a priced stepper separately rather than summing them away, and
  wears a `~` when the stepper's half was estimated. Every field is optional on purpose: a
  transcript recorded before this change has no `wall_s`, no `cost_usd` and no `jev` block, and
  replays with exactly the three counters it has always had rather than with `time None`. The one
  thing that is new on screen and not in the record is a single yellow line on stderr under the
  verdict when a run could not be costed, naming the model and the flag that would fix it,
  because a missing number should say it is missing rather than leave a reader deciding whether
  it means zero. `--provider fake` never draws it: that provider reports a rate of its own, of
  zero, and a rehearsal is not a model quackd has no price for.

- **The stepper's summary block grew `usage`, `cost_usd`, `cost_estimated` and `price`**, so the
  `jev` block answers what it cost as well as what it decided, and its own log line carries the
  tokens and the money inside the parenthesis it already had:
  `jev     gripper(open=false) 0.93 >= 0.85 (0.11 s, 527 tok $0.000022)`. The shadow event carries
  `llm_cost_usd` and `jev_cost_usd` side by side, which is the comparison shadow mode exists to
  make and the one that was previously left for a reader to do by hand out of two other records.
  `run_start` carries the stepper's rate too, so a run costed against `QUACKD_JEV_PRICE` says so
  in the file rather than in somebody's shell history.

### Deprecated

- **`quackd trace` is `quackd log`.** The old subcommand still runs, hidden from `--help`, and
  prints one yellow line on stderr naming the new spelling. It is removed in 0.12.

- **`--trace/--no-trace` and `--trace-prompt/--no-trace-prompt` are `--log/--no-log` and
  `--log-prompt/--no-log-prompt`.** Both spellings are on the same option, so a script that passes
  the old one keeps working this release and gets one yellow line per spelling per process. That
  line goes to stderr while the flags are being read, which is before the run's capture opens, so
  a script that greps its own stderr sees it, and the run replays it into the top of
  `terminal.txt` where it was on the screen. Removed in 0.12.

- **`QUACKD_TRACE`, `QUACKD_TRACE_THINKING` and `QUACKD_TRACE_PROMPT` are `QUACKD_LOG`,
  `QUACKD_LOG_THINKING` and `QUACKD_LOG_PROMPT`.** The old variable is read only when the new one
  is unset, so a shell or a `.env` that sets both is answered by the new name and the old one is
  ignored. Falling back to an old name writes one line to stderr, once per process, which under
  `serve-mcp` lands in the host's log for that server. Read the warnings as a floor and not a
  list: a name only warns on a run that reads it, so `QUACKD_TRACE=0` beside
  `QUACKD_TRACE_PROMPT=0` tells you about the first and says nothing about the second, because
  switching the log off means the prompt setting is never consulted. A flag has the same effect
  one level up: `--log` and `--no-log` answer the question `QUACKD_LOG` would have answered, so
  a run that passes either never consults it and is never told that name has moved. The run that
  names all three is the one that passes no flag and has the log on by default. Grep your `.env`
  files and CI for `QUACKD_TRACE` rather than waiting to be told; they stop being read in 0.12.

### Fixed

- **A grasp that landed between two polls was called a failed pick.** The LeRobot `pick` verb
  runs a policy and watches `holding` while it runs. It read that flag on the one poll that
  could not yet know the answer: the policy had gone idle, the gripper had not finished
  closing, and the verb declared failure on a grasp that was about to succeed. Worse than the
  wrong answer is what follows it, because `place` refuses on a precondition that says nothing
  is held, so the arm is holding the object and quackd will not put it down. It waits one
  settle now, `PICK_SETTLE_S`, which is the same five ticks the stall detector already used,
  and reads the flag once more before calling it a failure. It surfaced as a macOS job going
  red on one run and it is fixed as the hardware bug it is rather than as a flaky test, and
  like everything else this repository says about that arm, it has been exercised against the
  mock and against a fake arm and never against a real one.

- **`quackd log` on a flock printed one member's counters as though they were the flock's.**
  A flock replays every member in turn, reading `ducks/<member>/transcript.jsonl` in sorted
  order, so the `run_end` still in hand at the end belonged to the last of them to have
  recorded one.
  Its wall clock, its tokens and its bill are that one robot's rather than the run's, and
  because the order is the member's name rather than the clock's it was the same wrong member
  every time rather than a different one per replay. The flock's own `summary.json` sits in the
  run directory above those, and is what the counters are about, so it is read whenever there
  is more than one member transcript. A single-robot run is unchanged.

- **The loop read the clock twice for one model call and disagreed with itself about it.** The
  `llm` event and the record the shadow comparison reads each called `perf_counter()` for
  themselves, a few statements apart, so the same call was written down as two slightly
  different durations and the later one was always the longer. The call is timed once now and
  both read that one number, as does the new running total. The difference was small and the
  arithmetic this release builds on top of it is not, which is why it is fixed rather than
  noted.

- **The deprecation notice for `quackd trace` was silent for the scripts it was written for.**
  It was printed when `trace` was the very first word on the command line and never otherwise,
  so `quackd --no-color trace <run>` ran the retired spelling and said nothing at all. That is
  precisely the shape of an automated caller, which is the one audience a deprecation cannot
  afford to miss: nobody is reading its output, and it breaks in 0.12. The subcommand is read
  as the first argument that is not an option now. A run that happens to be called `trace` is
  still not somebody typing the old spelling, because `quackd log trace` has `log` in that
  position, and neither is `--run-name trace`. Found by this release's own audit of the
  paragraph above, which promised a line that was not always printed.

- **A mangled comment shipped in `quackd log`'s replay path.** The sentence explaining why the
  dropped-events counter is read through `_number` lost its opening clause in the rename, so
  what landed on `main` began mid-sentence. Comment only; no behaviour depended on it.

### Known limitations

- **Not one figure in this release has been checked against a bill.** Every rate was read off a
  vendor's pricing page by a person on 2026-09-21 and typed into a table, and a rate read by
  hand is wrong from the moment the vendor edits the page until somebody reads it again, and
  wrong silently. quackd knows nothing about your discount, your committed tier, your free
  credits or a minimum charge. Three of the 115 catalogued models carry no rate at all, the
  whole of Cohere's Command A family including that vendor's own default, because Cohere sell
  those by the hour rather than by the token; a run on one prints `cost unpriced` and records
  `cost_usd: null`. The Anthropic cache arithmetic corrected here changes no number today,
  because nothing in quackd sets `cache_control` yet: it is arithmetic waiting for the day
  something does. Treat a `cost_usd` as what quackd's table says the call should have cost, and
  `--price` as what to reach for when it disagrees with your invoice.

- **Six of the seven bodies have still never run on hardware, and the one that has was running
  0.9 at the time.**
  Unchanged since 0.10, and the pick fix in this release is the newest thing on the list of
  what no arm has exercised: it is tested against the mock and a fake arm, which is the
  standing every `lerobot:real` behaviour has. The day one of these stops a real grasp being
  called a failure is the day somebody reports that it did.

- **`terminal.txt` is most of the screen and not all of it, and it is redacted by name only.** It
  holds what went through quackd's two consoles, which is the narration, the warnings and the
  verdict, plus the handful of lines written straight into the capture because they never went
  through either console: the questions you were asked, which go out to the terminal raw, and the
  answers you typed, which the terminal echoed for itself. It does not hold the live status region,
  which is drawn and erased rather than printed; it does not hold a traceback, because a crash
  closes the file on a line naming the exception and `sys.excepthook` draws the trace after that;
  and it holds nothing written to the underlying file descriptor rather than through one of those
  two consoles, which is where anything a native library prints for itself would go. A session under
  `serve-mcp` writes none at all, because it has no run directory. On redaction, the rule is a list
  of names: two flags whose values are replaced outright, three URL-taking flags that keep their
  host and path and lose their password and any credential-named query parameter, and the same name
  list applied to the recorded `--extra-body` object. A credential under a name that is not on that
  list is kept, and so is a key typed inside the JSON of `--extra-body` on the command line, which
  is recorded as it was typed. Read the file before you paste a run directory into an issue.

- **The stepper has still never made a real call in this repository.** Unchanged since 0.10.
  Everything `--jev` reports about money is arithmetic over measured inputs: TypeSafe's own
  published rate, quackd's measured request size, and an estimate from that size wherever their
  API reports no usage at all, which is flagged as estimated everywhere it surfaces. The flag is
  off by default and says as much itself when it is switched on.

- **No pilot flock has been driven by a real model, or by a real robot.** Unchanged since 0.9.

- **Every old `trace` spelling in this release is on its last one.** The subcommand, the two
  flag pairs and the three environment variables all still work and all print one line saying
  what they are called now. They stop working in 0.12. The MCP result key is already gone, and
  that is the one to change today.

## [0.10.0] — 2026-09-19

A robot ran quackd, `uv pip install quackd` stopped installing one, and quackd learned to put the
arm down before it lets go. Those are the three things in this release. The first happened on 0.9
and is what the third was written in answer to; the second is the breaking change, a debt ADR-0017
had been carrying since a package meant a directory, and it is one line on your install command;
the third is a posture you record once and that quackd drives the arm to at both ends of a run,
including the ends nobody plans for, and it has stopped no arm falling yet because no arm has run
it. Behind them is an optional model that answers the turns that are a choice rather than the
turns that need writing, off by default and never measured on a robot, which is said here as
plainly as it is said by the flag itself.

A robot ran quackd. On 2026-09-15 a LeRobot SO-101 follower arm, calibrated as `arm-01` and
reached as `--robot lerobot:real --address COM3` with no registered name, did the thing every
release before this one had to say nothing had ever done: it
connected, and a real model drove it. Windows 11, Python 3.12.12, lerobot 0.6.1, quackd 0.9.0,
piloted by OpenAI's `gpt-6-astra`. `ducks/lerobot-lookout.duck` ran and succeeded, and ran once
with `--provider fake` as well. Free-form `--goal` runs came after it: the arm waved by rolling
its wrist about 27 degrees either way, waved again with the arm extended, `shoulder_lift` at -39
and `elbow_flex` between 24 and 30, opened and closed the gripper, which reported 98 where 100
was commanded and 3 when shut with the jaws nearly touching, and in one run mimed a duck quacking
with it. A USB webcam answered on `opencv://1` and later on `opencv://2` at 640x480, and neither
needed a `?backend=` key.

The honest half of that day is the longer half. **The arm fell at the end of every run.** LeRobot's
`disconnect()` disables torque by its own default, quackd kept that default deliberately, and so a
run that ended well ended with a raised arm dropping onto the bench. One dry run aborted with *the
arm did not answer: TimeoutError* when a single heartbeat round trip failed, and it never happened
again, so nobody here knows what that was. One dry run aborted because the pilot answered
`uncertain` and the person at the terminal said no, which is 0.9's verdict gate doing on hardware
exactly what it was built to do. And the webcam framed the gripper and cropped the raised arm out
of the picture, so the model confirmed its own waves from the joint readings rather than from what
it could see: the run proved the camera path works and proved nothing about whether that view is
the one to mount. Four things nobody has measured: whether the holding band is right, what a joint
reads after ten minutes of work, whether a stall is caught on purpose rather than by luck, and
whether 5 degrees an action is the right cap in the room. Six of the seven bodies have still never
run on hardware. Exactly one has, and every forward-facing sentence in this repository that said no
robot ever had is corrected in this release rather than softened.

What this release does about the falling is a rest pose: one posture recorded off the arm, driven
to before the pilot is handed control and returned to before quackd lets go, on every path a run
can end by. Torque is released only where the arm is known to be at it, and where it is not, the
arm is left holding itself up and the person is told so in one line. It was written after that
day and no arm has run it: it is exercised against a fake arm and the mock, which is the standing
every other `lerobot:real` behaviour has, and the day it stops an arm falling is the day somebody
reports that it did.

### Added

- **An optional discrete stepper in front of the model: `quackd run --jev`, off by default.**
  quackd asks one question a turn, which single tool call now, and pays a frontier model's full
  latency for it whether the answer is `report_state` or a six-joint pose. On the SO-101 run at
  the top of the README that is 62.1 seconds of a 78.8 second run. Some of those turns are not
  writing, they are choosing, and [TypeSafe's Jev](https://docs.typesafe.ai/introduction) is a
  model that answers a choice and generates no text at all. `--jev shadow` asks it every turn and
  records what it would have chosen beside what the model did, changing nothing about the run.
  `--jev on` lets it take the turns it is confident about. `--jev off` is the default, needs
  nothing installed, and is the path quackd has always taken.

  Which turns it may answer is computed from each tool's own JSON schema and nothing else, so a
  body quackd has never shipped is classified by the same rule as the seven that ship. A verb is
  a choice when every parameter is a closed set, an enum, a constant or a boolean, or is optional
  and defaults to null, and the closed sets multiply out to no more than a dozen concrete calls.
  Everything else is a number and the model's. `move_joints` is therefore
  never the stepper's on any of the three arms that have one, for two reasons that hold
  independently: its `positions` is a required object, and the joint names are not in the schema
  at all, because they live in a validator, so there is nothing for a classifier to enumerate
  even in principle. Every meta tool takes a required sentence, so the stepper cannot record a
  feasibility verdict, cannot declare an outcome, cannot write to memory and cannot speak to a
  flock. Every ending goes through the model or through a budget.

  A turn the stepper answers appends nothing to the model's history, because none of it is
  anything the model said, and writing it down would hand a model back an unsigned tool call it
  never made. The model is told instead, in its own block on the observation it is next shown, one line per
  turn, naming the verbs and who chose them. The stepper is only ever offered what the executor would run that
  turn, so before a feasibility verdict it may reach for a read, the brake, or a verb that only
  looks or sounds, which on a duck is `gaze` and `quack`, and nothing else, and `VerdictRequired`
  is unreachable rather than caught. The confidence floors are TypeSafe's
  own published numbers by what the verb does, with `stop` at the lowest floor in the system on
  purpose, because a wrong `stop` costs one step and a wrong anything-else costs a move nobody
  chose. Two trace kinds, `jev` and `jev_shadow`, and `summary.json` grows a `jev` block when
  there was a stepper and nothing when there was not.

  **How much it saves depends on how many of a task's turns are a choice**, which is a property
  of the task and the body rather than of Jev, so there is no single multiplier to quote. Taking
  TypeSafe's published 0.114 s and $0.042 per million input tokens together with quackd's own
  measured 6.21 s mean model call on that arm, one decision is about 54 times faster and about a
  two-thousandth of the cost, and a whole run is roughly 1.2 times faster on the wave, where one
  turn in five is a choice, and roughly 2.9 times on `arm-grip-check`, where a run on the mock
  arm, with a stub standing in for Jev, put four of six turns on the stepper. That run measures
  which turns are a choice and nothing about the model that would answer them.
  **Those are estimates and are labelled as such: none of this has run against a real robot.**
  The workings, the inputs and what would make them wrong are in `docs/jev.md`
  ([ADR-0040](docs/adr/0040-a-discrete-stepper-in-front-of-the-model.md)). Behind
  `quackd[jev]`, which is not part of `quackd[all]`, and `TYPESAFE_API_KEY`, or `QUACKD_JEV` for
  the mode where you would rather not pass the flag. It is not a provider: `--provider` does not
  take it, and `quackd doctor` gives it a section of its own. The flag is marked EXPERIMENTAL in
  its own help.

  A machine with neither the extra nor a key does not fail: `--jev on` says once, before
  anything is connected, that it is running without the stepper and why, and the run carries on
  with the model as pilot, because a stepper is an optimisation and a script that always passes
  the flag should still drive the robot. A mode nobody defined is a different thing, and
  `--jev maybe` still stops the run and lists the three that exist. Switching it on also says,
  once, that it has never been measured against a real robot: no latency, no agreement rate, and
  the figures above are estimates. `--jev shadow` says nothing, because shadow is how the
  measurement gets made. It reaches neither a flock, where every member is piloted by its own
  model and is told so, nor `quackd record`, which pins it off so that a `QUACKD_JEV` left in the
  environment cannot change what a recording records.

- **A new starter task for the arm: `arm-grip-check`.** Read the state, shut the gripper, read it
  again, release, stop. Nothing in it authors an angle, because `move_joints` is deliberately
  left out of its allowlist, which makes it the task with the largest share of turns that are a
  choice, four of its six, and the worked
  example in `docs/jev.md`. It also asks a question the README says nobody has answered: whether
  the band that infers `holding` from a gripper stopping short of shut is right. Run it with
  `--by-hand` and something in the gripper.

- **An arm starts from and returns to a rest pose you recorded: `quackd robot rest-pose NAME`.**
  Two things were wrong with a run on a real arm and they are the same thing twice. It ended by
  letting go, and a LeRobot arm goes limp when it is disconnected, so the arm fell. And it began
  wherever the last run had left the arm, so what a model improvised from was a different body
  every time. You fold the arm by hand, which you can do because nothing is connected to it and it
  is limp, then run `quackd robot rest-pose arm-01`: quackd connects, reads every joint, prints
  them, asks whether that is the pose, and keeps it in `~/.quackd/robots.json` beside the address
  and the camera. `--yes` answers for a script, `--clear` forgets it, and `--address`,
  `--registry-dir` and `--json` behave as they do everywhere else. `quackd robot show` grows a
  `rest pose` row, and `quackd robot list --probe` does not move the arm at all, saying `torque
  left on: not at its rest pose` where it had to keep it. From then on every run drives the arm
  there before the first LLM call, and a run that cannot get there aborts before the pilot is asked
  anything; every run drives it back between the stop and the disconnect, on every exit path there
  is, which is success, failure, an `infeasible` verdict, a spent budget, an abort, an error and
  Ctrl-C, and also the window between the connect and the first step, where a task that needs a
  verb this build does not have is refused with the arm already connected and holding. A pose that
  survived the registry and names no joint the arm drives, which a hand-edited `robots.json` can
  write and `quackd robot rest-pose` cannot, is refused by name rather than accepted and driven
  nowhere. Torque is released only where the arm is known to have arrived. Where it did not, quackd
  turns LeRobot's own disconnect flag off, leaves the arm holding itself up and says once: *the arm
  is not at its rest pose (...), so torque was left on and it will not fall: hold the arm and cut
  its power, or run again*. A `--dry-run` moves the arm at neither end. Only the five body joints
  are ever driven, and the gripper is recorded and never commanded, for the same reason `stop`
  leaves it alone: re-sending it would open a hand that is holding something. The pose is sent
  unclipped, which is the one place a stored pose escapes quackd's own out-of-range refusal, because a
  folded arm usually sits outside the travel its calibration recorded, the bench arm folding to
  `shoulder_lift` -113.5 against a calibrated 84.2 either way, and a range check that will not let
  you put the arm down is worse than no range check there. `quackd doctor` parks a probed arm too
  and says what it did in a `rest pose` row, because a probe connects and disconnects like anything
  else and is one of the ways the arm hit the bench. An MCP session parks at both ends and refuses
  to start if it cannot get there. One body is parked today and the other six refuse a pose rather
  than accepting one and ignoring it, and say which kind of refusal it is: a Microduck has no
  joints to record at all, and an XLeRobot has joints quackd does not drive to a pose yet
  ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md), [docs/registry.md](docs/guides/registry.md),
  [docs/safety.md](docs/concepts/safety.md)).

- **Several cameras on the LeRobot arm: repeat `--camera-url`.** One arm, one webcam was the shape
  of the camera 0.9 added, and the first real run is what showed the limit: the one camera framed
  the gripper and cropped the raised arm, and pointing it at the arm would have lost the gripper.
  `quackd robot add arm-01 lerobot:real --address COM5 --camera-url "opencv://1?name=top"
  --camera-url "opencv://2?name=side"` registers both, and the flag repeats the same way on the
  commands that take it directly. With several, every url has to carry `?name=` and the names have
  to differ, because the name is what the model reading two pictures, a pick policy's observation
  and `frames/NNNN-<name>.png` tell the views apart by, and an index may not appear twice, because
  two handles on one webcam is not two views. The first url is the primary and keeps everything a
  single camera had: it is the camera `--fov-deg` describes, the one the `camera:` detections line
  reports, and the only one the verbs that steer by sight read, because those run at 10 Hz and
  fetching every camera inside that loop would spend the deadman window on pictures. Every camera's
  frame reaches the model on every step, each labelled with its own name, on Claude, on both
  OpenAI APIs, on Gemini and on any OpenAI-compatible local server with `--vision` on, wherever
  the model takes a picture at all: one that does not is sent none. A camera
  that stalls later costs its own picture and nothing else, and both surfaces name which one:
  `report_state` in a `CAMERA DOWN: <name>:` clause, `quackd doctor` in a `camera <name>` row. If the primary is the one that died, the other frames
  still reach the model and the detections line reports nothing seen, because a bearing measured
  off a different lens would point somewhere else. Whether a picture is named is decided by how
  many cameras the arm has and never by how many answered this step, which is the difference
  between a useful second camera and a dangerous one: an arm with two lenses that is down to one
  sends a single picture, and that picture is the one that most needs saying which lens it is,
  because it arrives directly under a detections line measured off the lens that died. It is
  named, it is written to `frames/NNNN-<name>.png`, and it is named over MCP too, and it is
  not renamed: the picture is only called the primary where the primary is the lens that
  answered. Where the primary is the one that died, `robot_observe` says so and says there are
  no detections, because the detector reads that lens alone and never ran. Calling the
  survivor the primary and handing it the dead lens's empty detections would be a worse
  answer than the unnamed picture this replaced. A second camera that will not open refuses at
  connect, before the arm is energised, and lets go of the first. What it costs, which is the
  reason to decide rather than to switch it on: the last two exchanges keep their images, so two
  cameras is four pictures in every request where one camera is two, on every vendor that bills per
  image and against every context window. A local server, or a model behind one, may accept only
  one image per message; pass a single `--camera-url` if yours refuses. `robots.json` stores a
  string for one camera and a list for several, so a file written by 0.9 loads unchanged, and a
  body that reads one camera refuses a second by name rather than taking it and using the first:
  `microduck:mock takes one camera url; only lerobot:real takes several`
  ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md)).

- **An arm served over MCP now gets the rest pose it was registered with.** `quackd serve-mcp
  --robot arm-01` built the arm without it, so every guard that stops the arm falling read
  `None` and did nothing: the session connected without parking, refused nothing, and released
  torque wherever the client left the arm. The gate, the refusal and the torque hold all
  worked; nothing reached them.

- **`.env` is read from the folder you run in, too.** quackd read a `.env` by walking up from its
  own installed directory, which is what finds the file a `uv venv` user put in their venv root,
  and that is still read. The directory you typed the command in is read first now. Neither file
  overrides a variable already in your environment, and the first file to define a name wins, so
  the one next to the command you typed is the one that counts. The arm's first-run page draws the
  layout that works and carries the warning the lab earned, which is that the variable name is case
  sensitive on macOS and Linux: the file at the bench said `OPENAI_API_Key`, which happens to work
  on Windows and would have worked nowhere else
  ([docs/lerobot-first-run.md](docs/adapters/lerobot/first-run.md)).

- **A picture can come with the task: `quackd run --goal "draw what is in the picture" --image
  sketch.png`.** The only images a model could see were camera frames, fetched fresh each step and
  dropped from all but the last two exchanges, which is right for perception and wrong for a task
  that is *about* a picture. "Draw what is in this one" is not a sentence a robot's own camera can
  answer. The flag repeats, each file is re-encoded to PNG and sized down to fit, and every
  picture arrives on the pilot's first turn, labelled `task picture sketch.png:` in front of it,
  and stays there for the whole run rather than being trimmed with the frames. That first turn is
  where the verdict gate judges whether this body can do the task at all, which is the turn most
  in need of seeing what it is being asked about. Where the body also has a camera, both go out in
  one message and both are named, so a picture of the thing and a picture of the room are never
  two unlabelled images in a row. The prompt names each file and says plainly that these are not
  what the camera sees. The copies land in `runs/<id>/images/`, byte for byte what was sent, and
  the request line in the trace counts them: `1 messages (1 with image, 1 task picture)`. A pilot
  that cannot take an image refuses the flag before the run starts rather than dropping the
  pictures and improvising; `quackd list-models` marks those, `--vision` overrides it where the
  vendor does take them, and a local model needs `--vision` either way. Refused, too, on a flock:
  one task, one body, one set of pictures
  ([ADR-0008](docs/adr/0008-perception-features-not-frames.md)).

- **You can place the arm yourself before the model gets it: `quackd run --by-hand`.** The rest
  pose fixed an arm that fell and an arm that started somewhere different every time, and it left
  one thing you could not do: start a run from a pose you chose. Handing a pencil to a robot that
  begins every run folded on the bench means teaching it to pick a pencil up first, which is a
  harder task than the drawing. So with this flag the run goes to the rest pose as usual, quackd
  takes torque off *there* and tells you the arm is yours, and you lift it, load the gripper, hold
  it where the work should begin and press Enter. quackd writes those joint angles back as the
  goal, switches torque on, writes them again, reads the arm to check it stayed, and tells you it
  is holding the pose you set and you can let go. The pilot starts from there, and is told in its
  prompt that a person placed this body and that the gripper is where their fingers closed it,
  which is a position and not a grip. At the end the arm holds where it finished and you are asked
  once more before the gripper opens, because an arm folding to its rest pose with a pencil in the
  jaws drives that pencil into the bench, and the person who put it there is the one to take it
  out. Leave it and the arm folds up anyway, after two minutes.

  This is the only place quackd has ever taken torque off a robot, and it is fenced accordingly.
  It refuses anywhere but the recorded rest pose, which is the same condition `close()` already
  uses to decide that letting go will not drop the arm; it refuses a body that is not the arm, an
  arm with no pose recorded, a flock, a dry run and a terminal that is not there; no verb reaches
  it and no model can ask for it. A Ctrl-C while you are holding the arm re-energises it where
  your hand is and then folds it, which is the same motion the checklist already tells you to
  expect. One thing upstream does not document is what a servo does with its goal when torque
  comes back on, so quackd never relies on it: the present position is written before the switch
  and again after, and the arm is read back to see whether it actually stayed
  ([ADR-0039](docs/adr/0039-an-arm-placed-by-hand.md),
  [docs/adapters/lerobot.md](docs/adapters/lerobot/README.md)).

### Changed

- **An extra now pins the adapter it installs.** `quackd[microduck]` named `quackd-microduck` with
  no window, so `uv pip install "quackd[microduck]==0.10.0"` was free to pair this core with an
  adapter from another release, and since these are the first adapter wheels there has ever been,
  that means a later one. The only thing standing in the way was the adapter's own back-pin, which
  arrives as a resolver error rather than as the right version. The window runs in both directions
  now: every extra that names an adapter carries `>=0.10,<0.11`, written by the same
  `scripts/set_version.py` pass that writes the other half, and a test fails the build if the two
  ever disagree. The `dev` extra is deliberately exempt, because `[tool.uv.sources]` resolves it
  from the checkout and a window there would pin nothing.

- **The README leads with a real arm, and the hero is a recording of one.** Since 0.8 the picture
  at the top of the page was two rendered Microducks in MuJoCo, one with quackd and one without,
  walked by the scripted pilot with no key in the machine. It is `docs/assets/lerobot.gif` now: a
  phone pointed at a bench on 2026-09-15, run `20260915-145349-goal`, an SO-101 follower on
  `lerobot:real` told *wave to the camera with an extended arm*, and OpenAI's `gpt-6-astra`
  choosing one of the arm's own verbs at a time, `report_state` first, then `move_joints` once
  to extend, four times to
  roll the wrist and once more to return it to centre, then `stop`. The whole run at ten times speed. It is the first recording in this
  repository with a model in the loop, and the only one made on hardware: the four transcripts
  under `docs/assets/transcripts/` are the others, and none of them is a robot. It is also the one file
  under `docs/assets` allowed over the 2048 KB cap, with an exclude in the hook, a cap of its own
  in `docs/assets/lerobot_hero.py` and a test holding the two together: a render spends bytes on
  what moved, a photograph of a lab spends them on every pixel of every frame, and the same nine
  seconds fits under the general cap only at 320 pixels, which is no hero
  ([ADR-0038](docs/adr/0038-the-readme-hero-is-a-real-run.md)).

  Beside it, `docs/assets/lerobot-what-it-saw.png` is three of the ten frames the model itself was
  sent: the arm folded before the first call, extended after the second with the webcam cropping
  its raised end, and a hand waving back after the stop. A reader sees what the pilot had to go on
  and not only what the phone saw. Both are cut by `docs/assets/lerobot_hero.py` from a video and a
  run directory that are not in this repository and never will be, so that row of
  `docs/assets/README.md` says where they are and that it is reproducible on one machine.

  The page gained two sections. *Quickstart: a LeRobot SO-101 arm* is ten steps from an empty
  laptop to the arm waving, condensed from `docs/lerobot-first-run.md`, and it says plainly that
  two of its steps, the registered name and the rest pose, were written after that afternoon and
  have not run on an arm. *What happened in that run* reads the transcript beside the pictures: the
  command as typed, a third of the system prompt with the two wrong lines left in, all ten calls
  with what each asked for and what the arm reached, the cost, both of the model's own sentences
  verbatim, and an honest half about frames that were seconds stale, a detector that called a
  shelf figurine a person on eight of ten steps, and a model that used the picture for exactly one
  thing. The rendered pair moves down under *No robot yet? Try it in 60 seconds*, where it now
  argues for the simulator rather than for the project, and carries its CC BY-NC-SA label there.

  Every limitation stays where it was. Six of the seven bodies have not run on hardware, `pick` was
  never loaded, the rest pose has been driven on no arm, the arm that ran was reached by
  `--address` and not by a registered name, no flock has touched hardware, and the browser demo is
  still unrecorded. `LAUNCH.md`, the LeRobot hardware issue template, `docs/faq.md`,
  `docs/flock.md`, `docs/adapters.md`, `docs/memory.md`, `docs/licenses.md`,
  `docs/adapters/lerobot.md`, `docs/registry.md` and `PLAN.md` are corrected to match, the guard
  that scans living documents for the retired claim learns three more spellings and now reads the
  issue templates too, and the port in every dated account of that afternoon is `COM3`, which is
  what was actually typed.

- **Breaking. `uv pip install quackd` now installs no robot at all.** Every adapter is its own
  distribution, built out of this repository as a uv workspace: `quackd-microduck`,
  `quackd-lerobot`, `quackd-rosbridge`, `quackd-open-duck`, `quackd-xlerobot`, `quackd-alohamini`
  and `quackd-toddlerbot`. The extras you already know are still how you ask for a body, and each
  of them now buys the adapter itself rather than only the SDK behind it: `quackd[microduck]`,
  `quackd[lerobot]`, `quackd[rosbridge]`, `quackd[open_duck]`, `quackd[xlerobot]`,
  `quackd[alohamini]`, `quackd[toddlerbot]`, and `quackd[robots]` for all seven at once, each
  with the SDK its real backend needs. `quackd[mujoco]` is the duck plus its physics simulator
  and `quackd[microduck-camera]` the duck plus its WebRTC camera, both of them
  `quackd-microduck`'s own extras under the names they already had. **The migration is one line:
  anybody who installed `quackd` and ran the simulator now needs `quackd[microduck]`.** `uvx
  quackd run find-and-kick` with no extra refuses and names what to install rather than running
  anything, and that extra on the install line is what everything else in this entry costs.

  An installed adapter announces itself through the `quackd.adapters` entry point group, so a
  robot is available because something in the environment declared it rather than because quackd
  shipped it. That is the whole of the contract, which means somebody can publish an adapter for
  a body nobody here owns and quackd will find it, with no pull request to this repository. The
  core keeps a catalogue of the seven it does publish (`quackd/adapters/catalogue.py`), as
  strings that import none of them, so `quackd list-adapters` and `quackd doctor` still print the
  whole table on a machine with none of them installed, every row marked `not installed`, and a
  robot can be registered, listed and shown on a machine that cannot build it.

  There is no default robot any more, with one exception kept on purpose. With nothing installed,
  every command that needs a body refuses with `no robot adapter is installed: uv pip install
  "quackd[microduck]" (or lerobot, rosbridge, open_duck, xlerobot, alohamini, toddlerbot;
  quackd[robots] installs all seven), then --robot <adapter>:<backend>`. With exactly one adapter
  installed that one is the default, because a machine with one robot has no ambiguity to resolve
  and making its owner type the name would be ceremony, so a task file that names no robot runs
  on whichever body is there. With several installed and the Microduck among them,
  `microduck:sim2d` is still the default, because the six `duck: 0` starters carry no
  `robots:` line and have always meant the cartoon. With several and no Microduck, quackd lists
  what is installed and asks you to name one. A pilot flock whose members name no body asks the
  same question rather than reaching for the duck, because a pilot flock is N bodies of any kind;
  a coordinator flock still means N simulated Microducks, which is what it is made of. An adapter named but not installed says what to
  buy: `adapter 'lerobot' needs an extra: uv pip install 'quackd[lerobot]'`. `quackd record`
  still pins `microduck:sim2d` and a `--flock N` auction is still sim2d Microducks only, because
  that is what each of them is, and both now stop with the duck's extra named rather than
  assuming it is there.

  What moved is what was only ever the duck's: the `robotd` JSON-RPC transport, the WebSocket
  stub, the MuJoCo backend, the transport factory, the whole `sim3d` physics simulator and the
  `upstream_api.py` that names what robotd promises, all of them now under
  `adapters/microduck/src/quackd_microduck/`. What stayed in the core is the 2D cartoon arena
  (`quackd/sim2d/`) and the mock transport (`quackd/transport/mock.py`), because those were never
  the duck's either: four of the seven bodies run in that arena, the duck's mock backend is
  `MockTransport` itself, two of the other six subclass it, and all six draw their frames with
  the 2D renderer. `UpstreamRef` is `quackd/upstream.py` now, because every adapter cites upstreams and
  none of them should import a duck to do it. An adapter that was `quackd/adapters/<name>/` is
  `adapters/<name>/src/quackd_<name>/`, imported as `quackd_<name>`, which is the part of this
  that breaks a fork or an out-of-tree patch rather than an install. No manifest, verb, datasheet or safety rule changed, and no body gained or
  lost a backend.

  The rest of the cost is ours and it recurs. A release is eight wheels and eight sdists instead
  of one and one, they carry one version between them, which `scripts/set_version.py X.Y.Z`
  writes in all eight places at once, and PyPI takes the core first because every adapter depends
  on it and a resolver meeting `quackd-lerobot` before `quackd` has nothing to resolve against.
  CI gained `uv lock --check`, so a lock that has drifted fails a job rather than reaching a
  release, and a `packaging` job that builds every package, installs the core wheel on its own
  and proves it carries no adapter and says what to install. Contributors run `uv sync --extra
  dev`, which installs the core and all seven adapters as editable workspace members and exactly
  one robot SDK, `pyzmq`, because running quackd's client against a fake XLeRobot host over
  loopback is the only way that wire protocol is exercised for real
  ([ADR-0037](docs/adr/0037-adapters-are-their-own-packages.md)).

- **A `doctor` probe or a dry run on an arm away from its rest pose now leaves torque on where it
  used to drop it.** `quackd doctor --robot <arm>`, `quackd robot list --probe` and a `--dry-run`
  all connect and disconnect, and disconnecting is what let the arm go. An arm at its recorded rest
  pose is released as it always was; an arm that is not is left energised, holding its own weight,
  with the one line saying so and what to do about it. That line is printed on every path a probe
  can end by, including the timeout and the failure, which is where an arm is most likely to have
  been left holding itself up, and `quackd doctor` prints it whenever the disconnect left it,
  rather than only when the rest move had already reported a miss. A run that leaves the arm
  powered also fails `doctor`'s verdict rather than printing the warning above a green tick:
  the rest move and the disconnect take separate readings, and when they disagree the second
  one is the arm's actual state. The probe line keeps the note's direction too, since there
  are two of them and one says the arm was released rather than held. That is a real cost and it is the deliberate
  one: the servos stay powered and warming after a command you thought was read-only, until you
  return the arm to its pose or cut the power. An arm with no recorded rest pose is untouched by
  all of this and behaves exactly as it did, which is that torque drops where it stands, and
  `quackd robot rest-pose <name> --clear` says so when it takes a pose away.

- **`llm_request` counts the images in a request as well as the exchanges that carry one.** The
  event's `images` field meant exchanges, which was the same number until a body could have two
  cameras. It now carries `images`, every camera frame in the request, beside `with_image`, the
  exchanges that carry any, and `task_pictures` for the pictures the task itself brought, which
  ride in the same request and are not frames. A run with one camera reads exactly as it did. A transcript written
  before this reads its `images` as `with_image` and replays line for line.

- **An observation carries a list of named images rather than one image.** That is internal, and it
  is in this section because it is the shape every provider renders from: Claude, both OpenAI APIs,
  Gemini and the OpenAI-compatible local servers each label the frames they send with their camera
  names, and the transcript writes `NNNN.png` for a body with one camera, exactly as before, and
  `NNNN-top.png` beside `NNNN-side.png` for a body with several, with the camera named in each
  `frame` record. Anything reading a single image off an observation reads the list now.

- **A target the pilot has not found yet is the task, not a reason to be unsure about the
  body.** `assess_task` told the model its verdict was judged against the datasheet and then
  listed "the object is out of view" as a reason to answer `uncertain`, which are two different
  questions: where the ball is decides nothing about whether a duck can kick a ball on the
  floor it is rated for. It cost the duck's own starter task. `quackd run --goal "Find the
  ball and kick it."`, which is the goal the README's opening paragraph names and the first one
  it shows for a duck, stopped at the gate 5 times in 6 on Qwen3-32B-AWQ, `uncertain` and the
  run aborted, where `ducks/find-and-kick.duck` passed 6 of 6 on the same body, seed and model,
  and the pilot's own reason was "Since the camera currently detects nothing, I cannot
  determine feasibility yet". The description now says the verdict is the task's needs against
  the body's limits and not whether you can see the target, and keeps `uncertain` for what it
  is for: a figure that decides a limit, on a thing you have not seen, or a limit nobody
  published. The rule line, the MCP description and `docs/safety.md` carry both halves of that
  in the same words, and a test holds them to each other, because the first attempt stated the
  rule absolutely in the Rules line and kept the exception in the tool description beside it,
  which for "pick up the box" with the box out of frame gave opposite answers with the more
  authoritative one wrong.
  `Estimate.quantity` gains `duration_min`, because `needs` could already ask for
  `endurance_min` and the same pilot could not estimate one, so it spent LLM calls on a
  validation refusal. Thanks to [@Vallhalen](https://github.com/Vallhalen) (#25), who measured
  which of the four differences between a goal and a file does it: the allowlist, not the
  words. **The allowlist is not narrowed** (quackd cannot know what a sentence needs, and the
  README's own goal needs `kick`), so the fix is the words, which is their untested candidate
  and still untested: nobody here runs that model, and the six cells want running again on a
  build that carries this.

### Fixed

- **A `tool_result` answered the model's own call with a different verb's outcome.** When the
  stepper answers a turn, the observation built for that turn is discarded, so the id linkage was
  recomputed from the last surviving decision and the model's own result never reached it. On
  `lerobot-lookout` that means the model asked for a reading, was handed `stop`'s summary under
  its own `tool_use` id, and declared success against a criterion about joint angles it had never
  been shown. The observation has two readers with different needs, and they are separate now:
  the features carry the most recent verb whoever chose it, because the stepper is deciding what
  to do next, and the text carries the last verb the *model* chose, because the text is the
  `tool_result` answering the model's own call. Without a stepper the two are always the same and
  every observation is byte for byte what it was.

  Five more from the same pass. **Shadow mode's agreement metric compared verb names**, so
  `gripper(open=true)` and `gripper(open=false)`, which are opposite instructions that share a
  word, scored as agreement, and it was worst on `arm-grip-check`, the benchmark `docs/jev.md`
  names, where the gripper is the only verb with arguments. Agreement is about the whole call
  now, with `same_verb` recording the coarser reading beside it. **A malformed answer ended the
  run**: the parse sat outside the guard, so a confidence of `"high"` or a list-shaped
  `probabilities` raised through the loop and ended the run with a traceback, mid task, with the
  arm energised, which is the one thing a tolerant reader exists to prevent. Reading the answer
  is part of the call now and fails the way the call does. **A NaN cleared every gate at once**,
  because `nan < 0.85` is False, so it passed the motion floor, the done gate and the need_human
  gate together and moved the body, while the record printed the floor it had not enforced; a
  number that is not a number is an answer that cannot be read, and the turn goes back. And
  `--jev` on a flock was accepted and silently did nothing, while `QUACKD_JEV` reached
  `quackd record`, which the docs said it must not.

- **An `infeasible` verdict raised instead of reporting itself, on any machine without all seven
  adapters.** `shipped_manifests()` built a manifest for every robot quackd publishes, and a
  manifest is built by its adapter, so once the adapters became separate distributions that call
  asked for seven adapters on a machine that has one. Everything that calls it sits under the hint that
  names which other body could have done the task, and the loop reached it unguarded: on an install of
  `quackd[lerobot]` alone, a run that ended in `infeasible` ended in a traceback instead. That is
  the outcome the whole gate exists to produce, and it has its own exit code. The hint reads what
  is installed here and says so, *No robot installed here meets needs ...*, rather than speaking
  for what quackd publishes. Found by auditing the merged work against the plan rather than by a
  test, because no test ran the verdict path with an adapter missing. One does now, checked by
  reverting the fix and watching it fail.

- **A drawing with no background reached the model as a solid black rectangle.** A sketch
  exported the ordinary way is strokes on transparency, and a transparent pixel still stores a
  colour underneath: for most tools that colour is black. `convert("RGB")` keeps the colour and
  throws the alpha away, so paper and strokes both came out black, and a 200x200 circle on
  transparency arrived with exactly one distinct colour in it. That is the likeliest file
  `--image` will ever be given. It is composited onto white first now, because a drawing with no
  background is a drawing on paper. A palette picture with a transparent index, which is what a
  GIF is, goes the same way. The same function also dropped a photograph's rotation, which a
  phone stores in an EXIF tag rather than in the pixels, so a re-encode to PNG handed the model
  the sideways image with nothing left to say so. Nothing raised and nothing warned for either:
  both were found by a test being written for something else.

- **A by-hand run told every pilot to close the gripper, including the ones whose task does not
  allow it.** The advice was recited rather than read off the verbs the body was actually
  granted, and `lerobot-lookout` grants `report_state` and `stop`, so the sentence was an
  instruction to walk into a refusal. It is read off the allowlist now, the way the verdict
  clause and the composite-verb sentence already were, and where the gripper is not allowed the
  pilot is told the truth instead: what is between the jaws is held at the squeeze the person
  left, and it cannot tighten it.

- **The stepper answered with verbs it had not been offered that turn.** The router checked the
  model's answer against every discrete call the body has rather than against the ones it had
  just put on the table, so before a verdict it could reach for `gripper` and `place`, the
  executor refused both, and two turns went on `REFUSED` before the model was ever asked. The
  verdict gate was doing its job; the stepper was walking into it. It checks what it offered now,
  which makes that refusal unreachable rather than unlikely, and a test says so end to end.
  Running `arm-grip-check` is what found it.

- **A wait for a keystroke that nothing could deliver, with the arm limp.** The `--by-hand`
  placement wait has no clock on it on purpose, so somebody can go and find a pencil, which makes
  the key thread the only thing that can end it. Two ways that thread is not there: stdin reaches
  its end underneath it, which is a closed terminal or a Ctrl-D, and it was never started at all,
  which happens when the terminal check the kill switch makes disagrees with the one the CLI made
  a moment earlier. Either way the run waited for ever, asking nobody, with the arm de-energised.
  The switch says outright when nothing is reading, and a wait ends on that.

- **A confirmation that could never be answered, with a robot mid-verb.** This one predates the
  work around it. The kill switch's key thread reads every character of stdin and `typer.confirm`
  calls `input()` on the same terminal, so whichever took a character first kept it and the
  prompt waited for a newline that had already been swallowed, while somebody typed `y` at a
  question that would never return. Every prompt goes through the switch now, which collects the
  line in the thread that is already reading and hands it over; where no key thread runs, which
  is every caller without a terminal and every test, it is `typer.confirm` exactly as before.

- **One bad register spoke for the other, and a person was told to let go of an unpowered arm.**
  `take_hold()` refused unless the arm read back with torque on, then switched that check off
  whenever a register read had failed, on the reasoning that an unknown is not a refusal. But the
  two status registers were read inside one `try`, so a corrupt *temperature* packet reported a
  failure over a *torque* reading that had arrived perfectly well. An SO-101 that ignores
  `enable_torque()`, which is what one in overload lockout does, was then reported as holding the
  pose a person had just set, and what they read next was *holding the pose you set, you can let
  go*, with their hands on a limp arm and something in the gripper. Each register has its own
  `try` and its own error now, and a torque register that says nothing at all is as loud a
  refusal as one that says off. `let_go()` keeps the opposite reading of the same silence on
  purpose: a release that did not happen costs a refusal, a hold that did not happen costs the
  arm. Alongside it, `close()` on an arm still in somebody's hand dropped torque anyway, a
  Ctrl-C between the connect and the first turn left the abort flag set but unread, a gripper
  that would not open was silent because the backend answers a refusal rather than raising one,
  a 16-bit picture was clipped into a byte rather than scaled so a depth map arrived as a white
  rectangle, and a picture large enough to trip PIL's bomb guard came out as a traceback.

- **A refused re-assessment was written into the transcript as the standing verdict.** The early
  refusals in `_assess` return before the verdict is recorded, and the row was built from whatever verdict
  happened to be in the executor, so a refused re-assessment carried the *earlier* verdict's
  word, reason and needs and read as though that one had been refused. The row describes the
  call now.

- **`quackd validate` with no robot named checked the task against the duck.** The Microduck's
  verb list was the only vocabulary the core could produce without connecting to something, so on
  a machine with only an arm installed it passed a task that allows `kick`, and on a machine with
  nothing installed it had an opinion at all. It unions what every installed body provides now. A
  `.duck` is a contract rather than a robot, so a task that allows `kick` is coherent as long as
  something here can kick, and asking whether one particular body can keep it is what
  `quackd validate --robot NAME` already does.

- **`quackd[openai]` allowed a version of the SDK that cannot make the call quackd makes.** The
  extra asked for `openai>=1.50`, and the provider opens on the Responses API for every model the
  catalogue marks, switching to it elsewhere when a 400 says to. `client.responses` arrived in
  openai 1.66.0: on 1.65 the client carries no such attribute at all. So a resolver that landed
  anywhere between 1.50 and 1.65 produced an install that imported cleanly, passed
  `quackd doctor`, and could only fail at the first call on those models. The floor is 1.66 now,
  with an upper bound at the next major, which is the treatment `anthropic>=1.0,<2` beside it
  already had, and `google-genai` gains the same bound. Measured rather than remembered: 1.65.0
  and 1.66.0 were each installed into a throwaway environment and the client was asked whether it
  had `responses`.

- **A ToddlerBot's `perform` advertised motions that build had never loaded.**
  `toddlerbot_verbs(motions=...)` takes the list of keyframes the daemon actually managed to
  load and put it in the verb's description, while the schema kept a fixed five-member enum. So
  a robot with two motions loaded described two and offered five, pydantic accepted any of the
  five because the schema said they were legal, and the refusal arrived from the daemon a step
  later. A pilot that reads schemas rather than prose, which is most of them, was being told
  something untrue about the body. The enum is now built from the same tuple the sentence is.
  Found while writing the classifier for `--jev`, which reads schemas and nothing else.

- **The duck could not walk to anything straight ahead, and the nightly job had been saying so
  since 2026-09-09.** The physics backend raises a small twist to the gait floor, because below
  it upstream's walking policy stands and shifts its weight rather than stepping, and `go_to`
  asks for 0.2 m/s and was raised to exactly that floor. The floor was measured at 0.22 on
  MuJoCo 3.12. The lock resolves MuJoCo 3.13 now, where 0.22 is no longer enough: the duck
  covers thirteen millimetres in ten seconds and 0.23 walks 0.81 to 0.89 m. So a ball dead
  ahead was unreachable, `go_to` timed out three times, and the duck's own abort rule ended the
  run. It only ever looked fine because turning toward a ball off to one side brought it inside
  the stop distance without a step being taken. The floor is 0.23 now, re-measured across all
  ten sweep seeds, the achieved fraction with it at 0.38 where it was 0.42, and both numbers
  are corrected in the seven places that quote them, the browser demo's own copy included.
  `upstream_api.GAIT_THRESHOLD` carries both measurements and the MuJoCo version each was taken
  on, because a floor that moves with the physics build is the sort of thing the next person
  should be told rather than left to find.

- **A duck lying on its face read as facing backwards on one operating system and forwards on
  another.** Prone is exactly the gimbal singularity, so both arguments to the yaw's `atan2`
  are mathematically zero and the answer is decided by the last bit of a subtraction that
  should be exact. It rounds negative on Windows and positive on Linux, so the same duck in the
  same pose read pi here and 0 in CI, and the test that documented why `stand_up` reads the
  trunk's own axis instead was asserting a coin flip. Nothing behaved differently: `stand_up`
  has read `heading()` since 0.8, and only the test changed. It now asserts the degeneracy itself, which
  is true wherever it runs.

- **`stand` barely moved the ToddlerBot, and said it was still moving for ever.** Two faults,
  one on top of the other, and the contract job had never once passed because of them.

  The first hid the second. quackd refuses a reading of all-zero positions with all-zero
  velocities, because that is byte for byte what a Dynamixel bulk read hands back when it
  fails, and driving a position controller to it commands a full-scale move to zero. Upstream's
  MuJoCo body rests with every motor and every velocity at exactly zero, so every reading was
  refused and the planner was never reached at all. That refusal is about a serial bus and a
  simulator has none, so it applies to hardware now and the daemon is told which it is driving.

  Underneath it, the slew was starving itself of torque. These motors are position controlled,
  so the torque one makes is proportional to the distance between where it is told to be and
  where it is. The trajectory advanced from the *measured* pose each tick, which pinned that
  distance at a single step, 0.006 rad at fifty hertz, and a servo asked to move six
  thousandths of a radian pushes almost not at all. The arms were commanded through ninety
  degrees and asymptoted at twenty-six, the command creeping along behind them, and `stand`
  never finished because finishing meant the body arriving. It advances from the last commanded
  target now, which is what a trajectory is, and the same stand completes in the 5.2 seconds
  its own arithmetic predicts with a tracking error of one degree rather than sixty-two. The
  first reading still seeds the target from where the robot actually is, as upstream's own
  policies do, and every tick is still bounded by `MAX_STEP_RAD` and the joint limits.

  This is a real change to what a real ToddlerBot would do, on a body no ToddlerBot has ever
  run, and it is the difference between `stand` standing the robot up and `stand` leaning on
  it. It is exercised against upstream's own physics and against the fake body, and on no
  hardware, which is what [docs/adapter-status.md](docs/adapters/status.md) has always said
  about this row.

- **The contract job's deadman test asked a socket it had just closed on purpose.** It kills the
  client to prove the daemon survives a client vanishing, then asked that same dead link for the
  daemon's health, which raises from the writer's own drain. It asks over a live connection now,
  which is what the assertion was ever about, and the killed link is left alone rather than
  raced. The stand test's wait was a flat 25 seconds against a slew the daemon gives itself up
  to 120 for, so it now reads that ceiling out of the daemon rather than guessing at it.

- **`--max-steps` changed the budget and not the sentence about it.** The `--goal` run of
  2026-09-15 with `--max-steps 10` was handed a system prompt saying `Budgets: 40 steps` while
  every observation header it then read said `step 0/10` and the transcript's own contract said
  ten. The loop copied the override into the contract it enforces and built the prompt from the
  task file's original, so the model planned against four times the budget the run would stop at,
  and every `--max-steps` run before this one did the same. The prompt is built from the contract
  the executor and the header read now, so the number the model is told is the number it gets.

- **A `--goal` run told an arm to look with `observe` and to prefer `go_to`, and the arm had
  neither.** The strategy paragraph `--goal` writes into the task body named the duck's verbs
  whatever the body was, so the SO-101 on 2026-09-15 read `observe`, `search_scan` and `go_to` in
  the same prompt whose allowlist at the top listed none of them and whose executor would
  have refused all three. It worked around it by looking with `report_state`, which is not a thing
  to rely on. The paragraph is written from the allowlist the body was actually granted now: the
  looking verb it has, a composite only where it provides one, and a fresh reading rather than a
  fresh frame where there is no camera verb. A Microduck's reads exactly as it did.

- **The arm fell at the end of every run.** LeRobot's `disconnect()` disables torque by its own
  default, quackd took that default on purpose and wrote it down in `upstream_api.py` and on the
  arm's page, and the reasoning was that a limp arm is a safe arm to be standing next to. On a
  bench, at the end of a run nobody is holding, it is not: the arm falls from wherever the last
  verb left it, which on 2026-09-15 was every single run, including the ones that succeeded. The
  rest pose above is the fix at both ends. A run returns the arm to a posture it can be let go
  from before disconnecting, and where it did not get there, quackd turns the flag off, leaves the
  arm holding itself up and prints the one line rather than dropping it and reporting success.

- The feasibility verdict gate now lets a verb declared `read_only` by its adapter run before
  the verdict, the way `observe` and `report_state` do. Before, a third-party body's own
  sensing verb (a `locate` that reads where things are) was refused as "moves the body", and
  the pilot had to judge feasibility without the one tool that answers the question; a local
  14B model given a humanoid with two objects in reach declared the task infeasible twice
  without a single look. Learned verbs are unchanged: they never carry the flag. Thanks to
  [@Bayway](https://github.com/Bayway) (#26), who wired a humanoid quackd has never shipped to
  0.9.0 and measured what the gate cost it.

  Four things landed on top of it. A test pins every shipped read-only verb inside
  `BEFORE_VERDICT` and out of `MOVES_THE_BODY`, because the flag is now a second way into the
  gate and the test that closed that set could not see it; a second one runs every read-only
  verb on the mock and reads the wire, since two gates believe the flag and neither had ever
  checked. The prompt's rule line is read off the body's own allowlist rather than reciting a
  fixed list, so a pilot with its own `locate` is told it may look, hello-world's pilot is no
  longer told that `observe` runs when its contract allows no such verb, and an arm is no
  longer told about head verbs no arm has; over MCP, where there is no system prompt,
  `robot_list_verbs` marks each verb `before_verdict` and both tool descriptions point at what
  is true of every body instead of listing verbs, and a learned verb can no longer take one of
  those nine names and run on it, which an audit of this branch found the docstring promising
  and only the confirm gate keeping. And the three documents that said the gate reads
  `BEFORE_VERDICT` and nothing else now say what it reads, ADR-0032 by dated amendment rather
  than a quiet edit, including the adapter guide, which had been telling the author of a body
  quackd does not ship to classify their verb in a file they do not own, in a set whose own
  test rejects it. Two more documents were corrected while reading them: the safety page and
  the duck spec each hand list the read-only verbs that survive `--dry-run`, and both had
  omitted `introspect` since the rosbridge adapter shipped.

- **A pilot is held to its own datasheet, the way a bid already is.** `missing_needs`
  compared a `needs` against a sheet at the coordinator, where it judges another robot's
  bid, and nothing called it when a pilot judged its own body, so a `feasible` whose
  `needs` named a figure nobody published passed through and the body moved. `_assess` now
  runs the same function against the pilot's own manifest and refuses such a verdict the
  way it refuses one carrying `human`, naming the unmet need so the pilot can assess again.
  `uncertain` is left alone, because it asks a person and a reachable person knows things a
  sheet does not, and `infeasible` ends the run anyway. Measured on Qwen3-32B-AWQ over 54
  runs: a 45 minute patrol on a body whose endurance is not published came back `feasible`
  six times out of six and walked until the step budget, twice with
  `needs: {"endurance_min": 45}` recorded in the same row. On the patched build the check
  fired three times, all on that task, none on the other eight, and the body moved once
  instead of six times. What it does not do: the pilot came back `uncertain` rather than
  `infeasible` each time, and one run declared no endurance at all and so had nothing to be
  checked against. The check reads what a pilot declares, so it rewards honesty and cannot
  catch silence. Thanks to [@Vallhalen](https://github.com/Vallhalen) (#24), who measured it.

  They measured it across 108 runs and reported the noise floor with the result.

  What landed on top of it. **The MCP session is held to the same sheet**, because #24's own
  "your rule, applied on both sides" was true of one: `robot_assess_task` already matched
  `needs` against every *other* robot in the fleet to fill in `could`, and the one sheet it
  never compared against was that of the robot it was about to drive. Both surfaces refuse in
  one sentence now, from `own_sheet_objection`, which names three ways out rather than one:
  the measured pilot answered `uncertain` to a message offering only `infeasible`, and for a
  figure nobody published that is the right answer, because it asks a person and a person who
  knows the figure can publish it in the task file's own `datasheet:` block.

  **A refusal shuts the gate.** The check runs before the verdict is recorded, the way the
  `human` and validation refusals do, so a pilot already cleared for one reading of the task
  could name a need this body cannot meet, be refused, and go on moving on the older verdict
  while the newer and better informed one was thrown away. Both surfaces withdraw what was
  standing now, and an adversarial audit of this branch is what found it: the check refused
  the words and not the motion, which is the failure it exists to stop, one re-assessment
  later.

  **Two readings that would have refused an honest pilot** are fixed in the matcher itself,
  where the flock coordinator shares them: a minimum of zero asks for nothing, except `work_height_m`, where zero means the ground and a
  body either reaches it or does not, and a body that
  published no terrain meets `indoor_flat`, which is what the prompt already tells such a body
  to assume about itself, where the prompt says it. The same audit narrowed that second one:
  the sentence is only rendered for a body that moves and has a datasheet at all, so a bid
  that carried no datasheet no longer wins a role on it. A need that is not a number no longer
  raises out of the MCP tool either, which the zero check had started doing.

  And the places a pilot reads about `needs` say the check exists, because a refusal nobody
  was warned about reads as a bug. So does `docs/safety.md`, which says out loud the thing
  that is easy to resent: the check asks more of a pilot that answers fully, because a duck
  asked to nudge a 60 g ball has no published payload to compare against, and a `duck: 2`
  datasheet block is the answer that outlives one run. ADR-0032 carries a dated amendment for
  this as well as for #26, since it is the record for the gate and this changes what its
  Decision section describes.

### Known limitations

- **Six of the seven bodies have still never run on hardware, and everything this release did
  about the arm falling has run on no arm.** Exactly one body has been driven for real, the
  SO-101 on 2026-09-15, and the rest pose, `--by-hand`, the second camera and the MCP parking
  were all written after that afternoon. They are exercised against a fake arm and the mock,
  which is the standing every other `lerobot:real` behaviour has, and the day one of them stops
  an arm hitting a bench is the day somebody reports that it did. The other six bodies speak
  names read from upstream source at a pinned commit and have only ever talked to fakes, to the
  2D arena, and, for the Microduck and the ToddlerBot, to upstream's own MuJoCo body in a
  nightly job.

- **The stepper has never made a real call in this repository.** Every speed and cost figure in
  `docs/jev.md` is arithmetic over measured inputs rather than a measurement of Jev:
  TypeSafe's published latency and price, quackd's own measured model latency from the arm run,
  and a request size measured against the mock. The two runs that put a share of turns
  on it were driven on `lerobot:mock` with a stub in Jev's place, so they measure which turns are
  a choice and nothing about the model that would answer them. `tests/test_live_jev.py` is the
  one thing that can produce the real number, and it is opt-in twice over, `QUACKD_LIVE_JEV=1`
  and a key, so it has never run here or in CI. `--jev on` says as much itself.

- **No pilot flock has been driven by a real model, or by a real robot.** Unchanged since 0.9.
  `flock-hello` runs a duck and an arm on the scripted rule, which cannot reason about a
  datasheet, so what a real model does with the `Your flock` section and with `tell` is still
  unknown. N simulated pilots are also N separate worlds, and no flock has crossed from one
  machine to a second.

- **What 0.9 listed here and this release takes off the list:** the trained gait walks 10 of 10
  seeds under `QUACKD_STRICT_SEEDS=1` again. Seed 4 was not marginal for a reason nobody
  understood; it was the gait floor, measured at 0.22 on MuJoCo 3.12 and no longer enough on the
  3.13 the lock resolves. Both nightly jobs, neither of which had ever passed a scheduled run,
  are green.

## [0.9.0] — 2026-09-15

A robot has a name now. `quackd robot add scout open_duck:bridge --address ... --token ...`
writes it once to `~/.quackd/robots.json`, and `--robot scout` then means the same thing in
every command that runs or inspects a robot, so reaching a real body stops being four flags, one
of them a secret, retyped into shell history on every run. A flock is a list of those names you keep, and it can
be a second kind of thing now: run a task file that asks for pilots, or one with no `flock:`
block at all, as `quackd run <duck> --flock <name>` and every member gets a whole
pilot of its own, with its own provider, executor, allowlist, budgets, heartbeat, memory and
feasibility verdict, all at once on wall-clock time and on any mix of bodies, dividing the work
by talking to each other rather than by an auction a referee runs. A file that asks for an
auction still gets the 0.3 coordinator, whatever it is started with. And every robot carries a
datasheet: what it weighs, what it can carry and reach, the band of heights its hands work at,
how long it runs, and the things it cannot do whatever the task says, with a confidence label
and a source on every number and "not published" where the maker never said. Nothing that
moves the body runs until the pilot has read that sheet and recorded a verdict, and `infeasible`
is its own outcome rather than a failure: nothing moved, the exit code is 3, and the reason
names which shipped bodies could have done it. The CLI got a house style in the same release,
which is where `--json`, `--no-color`, grouped `--help`, `-h`, the panels and `doctor --json`
come from.

Two things are breaking. `--model` and `QUACKD_MODEL` now take an id from a curated catalogue,
115 of them across eleven vendors, so an unlisted id is refused before a key is read rather
than by the vendor after the robot has connected — and the three defaults that had quietly
gone wrong, `gpt-5`, `gemini-2.5-pro` and a `grok-4` that was being silently answered by
`grok-4.3`, are replaced. And the Reachy Mini adapter is gone, which is why every count here
reads seven bodies where 0.8 read eight.

Still nothing has run on a robot, on any of the seven adapters. The SO-101 arm got more work
than any other body in this release, a camera, a checklist, a lookout task and an audit that
stopped it taking the arm's word for four things, and no arm was driven for any of it. Of the
eleven cloud vendors, two have answered a real request: OpenAI here, and Gemini on a
contributor's machine, which is where both of this release's Gemini bugs came from. No real
model has yet refused a task on feasibility grounds, no pilot flock has been driven by a real
model or by a robot, and the two nightly jobs that watch upstream have been red since before
0.8.0 shipped. Known limitations, below, says what each of those leaves open.

### Added

- **A robot has a name now, and quackd keeps it: `quackd robot add|list|show|edit|remove`.**
  Reaching a real body took four flags, one of them a secret: `--robot open_duck:bridge
  --address tcp://10.0.0.5:9871 --token ... --camera-url ...`, retyped on every run and
  therefore in shell history from then on. `--robots name=<adapter>:<backend>,...` gave a name
  that died with the process. `quackd robot add scout open_duck:bridge --address ... --token
  ...` writes it once to `~/.quackd/robots.json`, and `--robot scout` then means the same thing
  in `run`, `validate`, `list-verbs`, `doctor`, `serve-mcp` and `quackd memory`. Two commands are
  outside it: `record`, which pins the simulator, and `announce`, which advertises a static
  manifest and so takes an `<adapter>:<backend>` spec and nothing else. An
  entry may also name the provider and model that pilot that robot, so a real duck can default
  to Claude and the simulator to the scripted rule without a flag; a flag on the line still
  wins, field by field, because reaching the same robot through a tunnel today is not renaming
  it. `quackd robot list` is static, because it is what you run to remember a name, and
  `--probe` connects to each robot and says whether it answered, exiting 1 if any did not.
  `--registry-dir` beats `QUACKD_REGISTRY_DIR` beats `~/.quackd`, which is the precedence
  `--memory-dir` already has. A name may not be a number, an adapter name, or an
  `adapter-backend` memory slug, because each of those already means something else on a
  command line. The honest part: the token is stored in plain text in a file in your home
  directory, quackd masks it in everything it prints, and `SECURITY.md` says so
  ([docs/registry.md](docs/guides/registry.md), [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **A flock is a list of names you keep: `quackd flock create|list|show|edit|delete`.**
  Members are robots registered with `quackd robot add`, so a flock is a composition rather
  than a command line, and `~/.quackd/flocks.json` remembers it between runs. `create` with no
  `--robot` prints what you have registered, numbered, and asks which to include; numbers and
  names can be mixed, a bad answer says what was wrong and asks again, and where there is no
  terminal to ask on it says so and tells you to pass `--robot` instead, because a script must
  never hang on a prompt. Order is kept, because it is the order the members are listed and
  coloured in when the flock runs. A flock stores up to 8 robots and runs with 2 to 8, so one
  you are still building, or one edited down to nothing, is stored and marked rather than refused. The one broken state either
  file can be in is a flock naming a robot nobody registered: `quackd robot remove` refuses
  while a flock lists it and names the flocks, `--force` drops it from them, and a flock that a
  hand edit left dangling is marked in every listing and refuses to run rather than quietly
  running smaller ([docs/registry.md](docs/guides/registry.md),
  [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **A flock can be N pilots talking, not only a coordinator refereeing: `quackd run <duck> --flock <name>`.**
  The 0.3 flock is one deterministic referee and N state machines in one simulated arena on a
  lockstep clock, which is the right machine for finding and kicking a ball and the wrong one
  for two robots whose bodies differ, because an auction has no way to express half a task.
  A pilot flock is the other kind: one whole `AgentLoop` per body, all at once, on wall-clock
  time, on any adapter and backend including mixed ones, 2 to 8 of them. Each member keeps its
  own provider, executor, allowlist, budgets, heartbeat, memory and feasibility verdict, so what
  a pilot flock can do is close to what one pilot can do times the number of bodies. What a
  member gives up is what N concurrent pilots cannot have: nobody prompts a person, so a task
  with a `verbs.confirm` needs `--yes` and an `uncertain` verdict cannot be put to anyone, and
  there is one live view and one rollup for the flock rather than one per member. Each member is handed the part of the contract its own body can
  answer for, so an arm in a walking flock is not turned away at the door for having no legs,
  while what the task *requires* is still checked against the union of every body before
  anything connects. Every pilot declares for itself and the flock succeeds only when all of
  them did; otherwise the worst outcome wins, with `error` above `aborted` because one member
  raising and the rest being stopped because it did makes the error the cause and the aborts
  the consequence. Ctrl-C or `q` fans one kill switch out to every executor. `ducks/flock-hello.duck`
  is the bundled demo, a duck and an arm saying hello, and it runs with `--provider fake` on a
  fresh checkout with no key and no registry. The honest part, which is also in the docs: N
  simulated members are N separate worlds with no shared arena and nothing to check a claimed
  success against, a seed does not make it reproducible, it costs one budget and one model call
  per member per turn, and nothing here has run on hardware
  ([docs/flock.md](docs/guides/flock.md#the-pilot-flock), [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **Pilots talk to each other: a `tell` tool and a `TALK` message on the flock bus.**
  `tell(to, text)` sits beside `assess_task`, `declare_success` and `remember`: it moves
  nothing, costs no step and one model call, and whatever was said arrives in the addressee's
  next observation under "Messages from your flock". `to` is a member name or `all`, a pilot
  never hears its own words back, and every message is a `TALK` in `flock.jsonl` like any other
  bus kind. It is not a verb, it is in no manifest, and no robot ever executes one. Each
  pilot's system prompt also gains a `## Your flock` section naming every peer and giving the
  short one-paragraph form of its datasheet, which says less than the bulleted sheet a pilot gets
  for its own body and says so out loud, so a pilot
  deciding who fetches and who holds is reading data rather than guessing, and saying that
  `assess_task` judges its own part rather than the whole task. The runner speaks too, under
  the name `flock`, when a member's loop ends, because otherwise a pilot waiting on somebody
  who has already stopped would wait until its budget ran out
  ([ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **`flock.allocation.method` takes `pilots`, the second value it has ever had.**
  The task file says which kind of flock it wants. `auction` is the default and everything it
  always was, and `--flock N` is always that one. `pilots` needs `duck: 1` and reads only
  `flock.members`. A coordinator flock is still 2 to 4 members, because its arena holds four; a
  pilot flock is 2 to 8, because nothing is shared and the bound is what one terminal can show.
  `flock.roles` stays a coordinator feature and is refused on a pilots file, which splits the
  work by talking instead. An older quackd refuses the value rather than ignoring it, which is
  the correct failure for a file asking for behaviour it does not have
  ([docs/duck-spec.md](docs/reference/duck-spec.md), [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **`quackd serve-mcp --flock <name>` fronts a stored flock as the MCP fleet, and adds no tools.**
  `--robots name=<adapter>:<backend>,...` already served several robots from one process, with
  one executor, budget and heartbeat each. What it could not do is give each of them its own
  address, token and camera: those three flags were one value applied to every robot, which is
  fine for three simulators and wrong for three machines. A stored flock takes all three from
  the registry per member, keys each member's memory by its registered name, and makes the
  flock's own first member the default robot rather than whichever Microduck came first.
  `--flock` refuses `--robot`, `--robots` and the three endpoint flags, because the registry
  already answers all five. The nine `robot_*` tools are untouched, and a flock **task file**
  is still refused over MCP, whichever kind it is ([docs/mcp.md](docs/guides/mcp.md),
  [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **Every robot carries a datasheet, and the pilot is told to check the task against it before anything moves.**
  A pilot used to be told one line about the body it was driving and a list of verbs, and nothing
  numeric: no payload, no reach, no working height, no endurance, and no way to say "this body
  cannot do that at all". Asked to carry a laundry basket on a 25 cm duck it had no ground to
  refuse on. Every manifest now carries a `datasheet`: what it weighs, how tall it is, how many
  joints it actuates, what it can carry and reach, the band of heights its hands work at, how long
  it runs, what it holds with, what it is rated for, and the things it cannot do whatever the task
  says. Every number states how sure quackd is of it (`official`, `estimate` or `measured`) and
  who says so, because a figure without a source is a rumour. A figure the maker never published
  is rendered as "not published: decline any task that hinges on it" and never as a zero. Speeds
  stay out of it: `limits` is what quackd clamps to, which is a rule about what quackd sends
  rather than a fact about the body, and the prompt renders those separately as clamps. The same
  sheet describes a body on every backend, which is what keeps a robot's digest equal across
  sim2d, mock and the real thing. `rosbridge` is the deliberate exception, and this release is
  what made it one: its sheet is whatever the bridge answered, so `mock` and `ws` describe
  different bodies on purpose, and a test asserts that they differ. The numbers were read from the makers' pages, repositories and
  one paper on 2026-09-13, and none of them were measured here, which is what the confidence
  labels are for ([ADR-0032](docs/adr/0032-datasheets-and-the-verdict.md)).

- **Nothing moves until the pilot has said the body can do the task.**
  The only way out of a task was `declare_failure`, which means "I tried and could not". A new
  meta tool, `assess_task` (`robot_assess_task` over MCP, the ninth `robot_*` tool and the first
  new one since 0.6), records a verdict first: `feasible`,
  `infeasible` or `uncertain`, with the reason, the datasheet fields it read, the estimates it
  made about the world and what the task would need. The executor gains a `verdict` gate that
  refuses every verb that moves the body until a feasible verdict exists; looking, speaking and
  the brake run before it, because that is how a pilot works out what it has been asked to do.
  `infeasible` is its own outcome, not a failure: the run ends where it stands, `quackd run` exits
  3, and the run's reason names which shipped bodies could do it by their own datasheets. On the
  first call, which is where the gate forces it, that means nothing moved. A pilot may record a
  verdict again later, so a run that drove and then declared `infeasible` ends the same way with
  the distance already travelled. `uncertain` asks
  the person at the terminal, and a no ends the run the way the kill switch does; over MCP, where
  there is no terminal, it stays pending and the model is told to ask the person it is chatting
  with, because a reachable human is a better answer than a flag. A model cannot clear its own
  doubt: the tool has no field for it. The two kinds of flock member differ here: a coordinator
  member is a state machine with no pilot at all, so there is no gate to ask with, while a pilot
  flock member has the gate and no terminal, so an `uncertain` there is told that nobody is
  present to answer for the human. The scripted pilot answers the gate as a rule and says so in its
  reason, which keeps every keyless example and all ten acceptance seeds running as before
  ([ADR-0032](docs/adr/0032-datasheets-and-the-verdict.md)).

- **A base over rosbridge reads its own description, and a flock role can ask for a body that can carry.**
  `rosbridge` names a transport, not a robot, so the honest static answer was that nothing is
  known. At connect it now asks the bridge for the topic list and the robot's own URDF, from the
  `robot_description` parameter first and the latched topic second, and two things a description
  can honestly settle go into the datasheet tagged official and sourced to the file: what the
  links weigh and how many joints are not fixed. Everything else stays unknown, payload above
  all. One deadline covers the whole look and it never fails a connect, so a bridge without
  `rosapi` costs that and leaves the sheet saying nothing was discovered, with the reason. A new
  verb, `introspect`, asks again for a robot that was still booting. Separately, a `duck: 2` flock
  role may state physical `needs` in the same vocabulary a verdict uses, checked by `validate`,
  by the member before it bids, and by the coordinator from the datasheet the bid itself carries,
  so a robot quackd does not run is held to the same standard. An unpublished figure counts as
  not met ([ADR-0032](docs/adr/0032-datasheets-and-the-verdict.md)).

- **A `duck: 2` file can correct a robot's datasheet for the build in front of you.**
  A vendor says an SO-101 lifts 500 g; your printed gripper holds 300. A `datasheet:` block in
  the frontmatter replaces any figure with your own, and the prompt renders it as coming from the
  task file so a reader can tell the maker's numbers from yours. The sentence lists extend and
  never delete: a task file can add something a body cannot do, and can never remove one. A
  correction the body contradicts, a payload on a robot with nothing to hold with, is refused by
  `quackd validate` before anything connects. `duck: 2` also unlocks `flock.roles.<role>.needs`
  ([docs/duck-spec.md](docs/reference/duck-spec.md)).

- **quackd carries a list of the models it will let you pick, and `--model` picks from it.**
  `--model` used to take any string and hand it to the vendor, so a typo, an id retired last spring
  and an id belonging to a different vendor all failed the same way: at the first call, in the
  vendor's own words, after the run directory had been made and the robot had connected. There is
  now one curated list per cloud vendor in `quackd/agent/providers/catalogue.py` — eleven vendors,
  115 models, each with the id as the vendor spells it, a label a human can read, one of five
  statuses (`current`, `legacy`, `preview`, `specialised`, `open`) and two hints about what the
  vendor will accept. The first entry of a vendor is its default, so a default is no longer a second
  place the id gets written down and falls out of step. `quackd list-models` prints the whole table
  and `--provider NAME` narrows it to one vendor; the notes column marks the default, the OpenAI
  models that open on the Responses API, and the individual models whose vendor does not document
  image input, where
  quackd sends the detections as text instead of the camera frame and `--vision` overrides. That
  last one is a per-model fact rather than a per-vendor one: several vendors carry both kinds.
  `QUACKD_MODEL` is checked exactly as `--model` is, and the refusal says which of the two the id
  came from, because a flag you just typed and a line you forgot in a `.env` want different answers.
  `--model` also completes in the shell, following whichever `--provider` is already on the command
  line. The local presets are deliberately outside all of this: `local`, `ollama`, `vllm`,
  `llamacpp` and `lmstudio` serve whatever you pulled, so `--model` is still free text there and
  passing none still means "ask the server what it has". `quackd serve-mcp` picks no model at all —
  the client's own model is the pilot and `QUACKD_MODEL` is irrelevant there — which was already
  true and is now written down ([ADR-0031](docs/adr/0031-model-catalogue.md)).

- **`quackd list-models`**, the command that prints what `--model` will accept. Five columns per
  id: the vendor, the id as that vendor spells it, a label a human can read, its status, and a
  notes column that marks each vendor's default, the OpenAI ids that open on the Responses API,
  and the individual ids whose vendor does not document image input. `--provider NAME` narrows it to one vendor
  and `--json` prints one object per line. It is the answer to a question the catalogue's refusal
  raises, so the refusal names it.

- **Seven more cloud vendors: Mistral, DeepSeek, Cohere, Qwen, Kimi, GLM and Meta.** All seven serve
  an OpenAI-shaped endpoint, so each is one small `OpenAIProvider` subclass with a base URL and a
  key variable, exactly as Grok has been since the first release. `--provider mistral` reads `MISTRAL_API_KEY`,
  `deepseek` reads `DEEPSEEK_API_KEY`, `cohere` reads `COHERE_API_KEY` (or `CO_API_KEY`, which is
  what Cohere's own examples export), `qwen` reads `DASHSCOPE_API_KEY`, `kimi` reads
  `MOONSHOT_API_KEY`, `glm` reads `ZAI_API_KEY`, and `meta` reads `META_API_KEY` (or
  `MODEL_API_KEY`). Each has its own extra — `quackd[mistral]`, `quackd[deepseek]` and so on — and
  every one of them installs the same `openai>=1.50` wheel that `quackd[openai]` and `quackd[grok]`
  do. Nine vendors, one package: the extra exists so that a missing-SDK error can name the install
  the reader actually wants rather than a package name they will not connect to the vendor they
  asked for. Meta here is not Llama — Meta retired the hosted Llama API in July 2026, and its
  replacement, the Meta Model API, serves Muse Spark; two of those are a contributor tier, cheaper
  because Meta trains on your prompts, and the label in `list-models` says so. The honest part: of
  the eleven cloud vendors, two have answered a real request. OpenAI on the machine that wrote
  this, and Gemini from a contributor's, which is how the two Gemini bugs under Fixed were found.
  The other nine are wired from their own published documentation and held by tests that stub
  the SDK client. For the eight that speak OpenAI's API that proves the base URL, the key
  variable, the extra and the default model; Anthropic sets no base URL of its own, so for that
  one it proves the extra and the request shape. None of it proves that any vendor answers.

- **The browser demo picks its model from a dropdown.** The page declared a `models:` array per
  vendor that nothing ever read, and offered a free-text box instead, so a visitor who came to click
  one thing had to already know a model id to type. The dropdown is now fed by the same catalogue
  through `web/src/catalogue.js`, generated from the Python and grouped by status, with a test that
  fails when the generated file has drifted from `catalogue.py`.

- **A bring-up checklist and a lookout task for the LeRobot arm, which had neither.**
  Every other body quackd ships a lookout for had both, and this file has said the arm did not
  since 0.7. [docs/lerobot-hardware-checklist.md](docs/adapters/lerobot/hardware-checklist.md)
  is the order to try an SO-101 in, with nothing moving until step 10 and a hand on the power
  switch from there, because this arm has no e-stop. `ducks/lerobot-lookout.duck` is the task
  to point at a real arm first: it moves no joint, and it asks for `report_state` rather than
  `observe`, because a `.duck` is checked against the static manifest, which cannot know
  whether a webcam is plugged in
  ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md)).
  One body still has neither, and it is the one that names a transport rather than a robot: a
  `rosbridge:ws` base gets no lookout task and no checklist in this release either, so it goes
  on blocks and a person reads [docs/adapters/rosbridge.md](docs/adapters/rosbridge/README.md) instead.

- **A camera on the LeRobot arm: `--camera-url opencv://N`.** No SO-101 has a camera in it,
  whatever a kit's listing says: the arm is six servos and a serial board, and every camera on
  one is a USB webcam plugged into the computer. `lerobot:real` now opens one, so `observe`
  exists on a real arm for the first time. The url is the OpenCV index
  (`lerobot-find-cameras opencv` prints them and saves a frame from each, which is the only
  honest way to tell which is which) or a device path, `opencv:///dev/video2`, with `?width`,
  `?height`, `?fps`, `?fourcc`, `?rotation`,
  `?name`, `?fov` and `?backend=msmf` for the Windows camera that lists and then will not open.
  Nothing is asked of the camera by default, because a mode it cannot do is a refusal at
  connect and the webcam in a lab drawer is unknown. An unknown key or a bad value is refused
  with the shape, before LeRobot is imported.
  quackd builds the camera itself rather than handing it to the follower, and that is the
  whole design: a follower's `is_connected` is the bus **and** every camera, and `send_action`
  and `disconnect()` are gated on it, so one webcam coming unplugged would have made every
  move and every hold raise while the arm was perfectly fine. Beside the follower, a camera
  asked for and not opened refuses at connect naming the url, before the arm is touched at
  all, and a camera that dies later costs `observe` and a `pick` in flight and nothing else:
  the heartbeat still reads the arm, the joints still move, `stop` still holds. `observe` now says what the camera said rather than "this transport has
  no camera", `quackd doctor` gates its verdict on a real frame as it does on every body whose
  transport reports camera health, which is the Microduck over robotd and the Open Duck's bridge
  and not the four that report none, and `?fov=` travels with the camera into `limits.camera_fov_deg` so bearings are
  calibrated over MCP too, where there is no `--fov-deg`
  ([docs/adapters/lerobot.md](docs/adapters/lerobot/README.md#camera), step 8 of
  [the checklist](docs/adapters/lerobot/hardware-checklist.md)).

- **The LeRobot pages rewritten for someone who owns the arm rather than someone who wrote
  the adapter, and five things they said that were not true.** An SO-101 owner is the likeliest
  first external user of quackd, and the two pages assumed a reader who already knew what
  quackd was for. [docs/adapters/lerobot.md](docs/adapters/lerobot/README.md) now opens with what
  LeRobot already does for you and what quackd deliberately does not touch (teleoperation,
  recording, training), the three properties of this body that shape every guard, and a
  starting path that begins with the mock and no arm at all. It gained the install trap in
  full (the `[feetech]` extra, and the `python_version >= '3.12'` marker that makes an install
  on 3.11 resolve to nothing while `doctor` keeps saying `not installed`), a section on the
  calibration id, which is the name you give the robot and the one thing that silently breaks
  a connection after a calibration you watched succeed, the three ways to drive the arm with
  the MCP config written out, `--dry-run` as a rehearsal that connects for real and sends
  nothing, and a troubleshooting section whose every row quotes a refusal from the code that
  raises it, beside what to do about it, followed by a short list of failures SO-101 owners report that nobody
  here has verified, labelled as such.
  The five corrections: a task that allows `observe` is refused on **every** real arm rather
  than only on one without a camera, because `quackd run` checks the allowlist as well as
  `requires` against the static manifest, and the place it does work is MCP, where
  `robot_load_duckfile` validates against the robot already connected, so the same task loads
  on a session started with `--camera-url`; the pilot gets detections every step whether or
  not `observe` is allowed, which makes `--vision` the picture rather than the sight;
  `load_policy()` imports
  `lerobot.configs.policies.PreTrainedConfig`, which had no ref, so "every name quackd spells
  lives in `upstream_api.py`" was false until this commit added it; and `pick` is not reachable
  from the CLI or from MCP at all, because `make()` has no policy parameter and `load_policy()`
  has no caller outside a test, which the checklist had presented as something to try at the bench.
  The checklist could not be followed as written: step 4 ran `lerobot-calibrate`, which step 5
  installed. Installing now comes first, finding the port with upstream's own `lerobot-find-port`
  comes with the calibration, and the claim that Windows needs a CH340 or CP210x driver is gone,
  because the arm enumerates as a USB CDC device and no primary source names that chip. It also
  gained the `--dry-run` rehearsal as step 9, so nothing moves until step 10, and a
  [hardware report template](.github/ISSUE_TEMPLATE/lerobot-hardware-report.yml) that asks for
  exactly what *What to report* asks for. Two things in the code changed because writing the
  pages found them, and they are under Fixed: a camera that dies mid-run used to be invisible
  on a `quackd run`, and `doctor` advised about verbs an arm does not have.

  One thing neither page knew: **upstream's calibration does not sweep `wrist_roll`.** It
  prints *move all joints except 'wrist_roll'* and records a full encoder turn for it, so that
  joint's travel comes out as -180..180 and quackd's out-of-range refusal, which is real on the
  other four body joints, cannot catch anything on that one. Both pages now say so, and the
  checklist's out-of-range step says which joint not to test it on.

- **A body field the server wants and quackd never sends: `--extra-body` and
  `QUACKD_EXTRA_BODY`.** One JSON object, merged into the top of every request body on every
  provider that speaks OpenAI's API, which is nine of the eleven cloud vendors and all five
  local presets, and sent on Chat Completions and Responses both, so it keeps working when a
  run moves from one to the other mid-flight. The case that asked for it: Qwen3 on vLLM thinks
  before it answers unless the request body says `{"chat_template_kwargs": {"enable_thinking":
  false}}`, and that switch is a chat template argument rather than a sampling parameter, so on
  a server somebody else runs there was nowhere to say it. One reported step spent 150 s and
  1717 output tokens deliberating before a decision that was correct anyway. The flag beats the
  variable, an empty object sends nothing, and a value that is not one JSON object is refused
  before a robot is connected, naming the flag or the variable it came from. Six keys are
  refused because they are quackd's to send — `model`, `messages`, `input`, `instructions`,
  `tools` and `stream`, the fourth being the system prompt on Responses the way the second is
  on Chat Completions — and everything else replaces what quackd would have sent, `tool_choice`
  included, because overriding it is the point. `run_start` records the object, since a run
  whose model was told not to think reads nothing like one that was. Anthropic and Gemini
  ignore it, as they already ignore `--base-url`. It is in `.env.example` with the others,
  where single quotes matter: double ones make python-dotenv drop the line without a word. If
  you run the server yourself, vLLM's own `--default-chat-template-kwargs` does the same thing
  once at serve time, and the docs now name both. Thanks to
  [@Vallhalen](https://github.com/Vallhalen) (#12), who measured it and proposed the
  passthrough ([docs/local-llms.md](docs/guides/local-llms.md#knobs)).

- **Two transcripts of `find-and-kick` piloted by Qwen 2.5 Coder 14B on LM Studio**, seeds
  5 and 6, land in `docs/assets/transcripts/` with a table reading them in
  `docs/local-llms.md`, from the contributor whose memory feature they were recorded for.
  The README, `local-llms.md` and PLAN.md no longer say "no transcript in this
  repository". The pair is not a chain: they ran against different memory directories,
  so one shows the write and the other the read, and neither shows a note surviving
  from one run into the next. Thanks to [@Bayway](https://github.com/Bayway) (#7),
  whose runs and whose transcripts these are.

- **Two transcripts from Qwen3-32B-AWQ on vLLM, and the first numbers anybody has for what
  `--extra-body` actually stops.** Same build (`739ff84`), same server, and the same seed on the
  contributor's word, since no transcript records one. The flag is the difference the pair was
  built around.
  Every test in this repository proves the object reaches the SDK call and not one of them proves
  the thinking stops, so until now that flag shipped on a mechanism nobody had watched work. With
  it off, all eight LLM calls deliberate in the open, 599 to 1,446 characters of it in each row's
  `thinking` field; with it on, not one of the five carries that field at all. Neither run needed
  the JSON text fallback, on a server nothing here had ever talked to.
  **The drop is 4.9x, not the 7.8x the totals show.** The two runs took different paths, so
  2,049 against 263 is not a comparison. Five decisions are common to both, `assess_task`,
  `search_scan`, `walk_to`, `kick` and `declare_success`, and on those it is 1,290 against 263.
  The rest is path, and the path is not the flag's doing either: the quiet run skipped `remember`
  because its prompt already held the fact it would have saved and the duck tells it to skip one
  that is already there, and it skipped the `quack` its persona asks for, which is an instruction
  missed rather than a step saved. The thinking run's own first call asked for `search_scan`
  before recording a verdict and the gate refused it, so one of its eight bought nothing.
  Its expensive first call is the verdict, not a warm-up: `assess_task`'s `reason` argument is a
  five sentence paragraph, and at the 8.1 to 10.2 tokens a second every call in that run decodes
  at, 130 tokens is about 13 of its 16 seconds. `reasoning_tokens` stays 0 in both, which is a
  property of a server running without `--reasoning-parser` rather than evidence of a model that
  did not reason: the server leaves the thinking in `content`, billed as output, and quackd then
  splits it into the transcript's own `thinking` field before the text fallback can read a verb
  out of a discarded thought.
  **And the pair is the first published chain of quackd's memory**, which the PR delivered
  without claiming. The thinking run's `remember` saved *"The ball was found at 43° left, ~0.85 m
  during initial scan."* and that sentence is in the other run's `system_prompt` verbatim, beside
  the episode quackd wrote from the same run, with the counters moving from 1 note and 2 episodes
  to 2 and 3, so nothing ran between them. A note written by one run and read by the next, both
  ends in this repository, which no transcript here had shown and which
  [PLAN.md](PLAN.md) had carried as open since #7. The machine is an `aarch64` NVIDIA GB10,
  which nothing here had run on before. Thanks to
  [@Vallhalen](https://github.com/Vallhalen) (#12, #23), who asked for the flag, was told the
  tests could not prove it worked, and went and measured it on the only machine that could.

### Changed

- **The README and the social card wear the duck head quackd.org uses, and `logo.svg` is
  gone.** The README opened with a flat biped and a wordmark that set `quack` in yellow and
  the `d` in purple. It opens with the 3D duck head instead, the same drawing quackd.org
  shows in a browser tab, over a plain `<h1>` in whatever colour the reader's GitHub theme
  uses for a heading. The two marks had been diverging since the landing page shipped, and a
  project whose front door and whose website do not look like the same project is paying for
  two identities and getting neither. The head was already in this repository at
  `web/assets/duck-mark.png`, vendored for the browser demo in 0.8, so the README, the card
  and the demo now read one file. `docs/assets/social_preview.py` stops transcribing
  `logo.svg`'s geometry into Pillow and pastes that PNG instead, after the resample rather
  than before, so the art is never resized, and it measures the mark's own ink and the type's
  own height to place them rather than trusting a coordinate, because `_FACES` resolves to a
  different typeface on every machine. The wordmark loses the two-colour split for the same
  white the headline uses. `docs/assets/logo.svg` is deleted, because a second mark nothing
  renders is a second mark that goes stale. What kept the two in step before was nothing, so
  `tests/test_pypi_readme.py` now asserts that the file the README opens with and the file the
  card pastes are the same one, and `web/README.md` records that `web/assets/` stopped being
  the browser demo's private chrome the moment two other things started reading it. The
  uploaded social preview is a manual follow-up as always: GitHub has no API for it, and
  PLAN.md carries it.

- **The LeRobot arm adapter stops taking the arm's word for four things it never said.**
  Re-reading upstream at the pinned commit found that `is_connected` is only the serial
  port's open flag, that `get_observation()` reads positions and nothing else, that a degrees
  goal past the calibrated travel is written unclamped, and that one action may slew a joint
  its whole travel. So the heartbeat now reads the arm; torque and each servo's temperature
  are read off the bus by register; every joint's range comes from the arm's own calibration
  file and a goal outside it is refused; and one action moves a joint at most a capped step,
  set by `QUACKD_LEROBOT_MAX_STEP_DEG`. What a pilot notices: `move_joints` and `gripper`
  re-send the goal and watch the measurement, so they can now fail with where the arm stopped
  and `duration_s` is a budget rather than a wait; a new `not_hot` precondition refuses
  `move_joints` and `pick` when one of the five body joints reads 60 °C or more, the gripper
  being left out on purpose because opening it is how you put down what it is holding;
  `report_state` carries `torque` as measured, `temperature_c`, `hot`, `out_of_range` and
  `step_deg`, and the manifest of a connected arm gains `joint_range_deg`, `calibration_file`
  and `torque_limit_scope`; `holding` is inferred from the gripper settling short of shut; `stop`
  holds the five body joints and leaves the gripper's goal alone, so a failed verb never drops
  what is held; and the datasheet no longer claims a mass, because vendor listings disagree by
  a factor of three. Why each of these is the way it is:
  [ADR-0036](docs/adr/0036-what-the-arm-does-not-say.md).

- **The one-liner changed, and every place that carried the old one followed.**
  It was *Give your Microduck a brain. Any LLM, one `.duck` file.* It is now *One CLI for all your
  robots. Connect them, command them, and let them work together, each with an LLM for a brain.*
  That sentence was accurate for 0.3, when quackd was a brain for exactly one robot. 0.4 made the
  robot an adapter that declares a manifest, and the registry and the pilot flock in this same
  section made a robot a name you keep and a flock a list of those names, so what quackd is is the
  place you connect the robots you own, the place you command all of them, and where they divide a
  task between themselves, each with an LLM for a brain. quackd is the CLI, not the brain. The new
  sentence is in the README, in `pyproject.toml`'s description, which is what PyPI shows from this
  release on, and in its keywords, where `reachy-mini` gave way to nine vendor names and to
  `multi-robot` and `robot-fleet`, the one place the retired word stays on
  purpose, in the `quackd --help` banner, in `quackd/__init__.py`'s docstring and in `LAUNCH.md`.
  The demo's title and meta tags, [docs/architecture.md](docs/concepts/architecture.md) and
  [docs/mcp.md](docs/guides/mcp.md) did not take the sentence itself: they lost the retired tagline and
  had the new positioning written into their own words. "Fleet" is retired
  from prose and from help text in favour of "flock", while the code identifiers keep it, because
  renaming `build_fleet_server` would be churn no reader sees. [docs/flock.md](docs/guides/flock.md) now
  reads pilots-first. Nothing new ships here: the features this describes landed in this same
  release, every body is still simulated or mocked, and flock mode is still EXPERIMENTAL
  ([ADR-0035](docs/adr/0035-one-cli-for-all-your-robots.md)).

- **A registered robot keys its memory by its name, not by its body.**
  Memory was keyed `adapter:backend` so that a simulated duck never inherited a real one's
  notes, which was right and also meant two real ducks on one desk shared one file. A robot
  registered with `quackd robot add` now keys by the name you gave it, so `duck-a` and `duck-b`
  keep separate notes, while an unregistered `--robot microduck:sim2d` run keys exactly as it
  did. A registered name may not collide with an `adapter-backend` slug, so a run can never be
  ambiguous about which file it is writing. Amends
  [ADR-0025](docs/adr/0025-memory-between-runs.md)
  ([docs/memory.md](docs/guides/memory.md), [ADR-0034](docs/adr/0034-registered-robots-and-pilot-flocks.md)).

- **Breaking. `--model` and `QUACKD_MODEL` now take a catalogue id, and three of the four defaults moved.**
  [ADR-0010](docs/adr/0010-providers.md) shipped four defaults with the first release and marked
  three of them "(verify)". Nobody ever did, and by 2026-09-12 all three were wrong: `gpt-5` has
  an announced shutdown date, `gemini-2.5-pro` is legacy, and `grok-4` was retired in May 2026 and
  is silently answered by `grok-4.3` — which is the worst of the three, because nothing fails, the
  key is billed and the transcript records a model that did not run. The defaults are now
  `gpt-5.6-sol` for `openai`, `gemini-3.8-flash` for `gemini` and `grok-4.6` for `grok`;
  `claude-opus-5` for `anthropic` is unchanged. This is breaking for anyone passing an id quackd
  does not list, and that is the point: the refusal arrives
  before a key is read or a packet is sent, and it says what to pass instead. Two of the three
  old defaults are in that position, `gpt-5` and `grok-4`, both of which the catalogue refuses by
  name. The third is not: `gemini-2.5-pro` is still listed as `legacy`, because the rule for
  being listed is that the vendor still serves it with no end announced, so a run that names it
  explicitly keeps working and only the default moved out from under it.

      error: openai: unknown model 'gpt-5' from --model. Valid ids: gpt-5.6-sol (default),
      gpt-6-astra, gpt-5.6-terra, ... See `quackd list-models --provider openai`.

  When the id belongs to another vendor the refusal names it — `('grok-4.6' is a grok model: pass
  --provider grok)` — because that is the commonest mistake and the hardest to see, the id looking
  perfectly valid. A `QUACKD_MODEL` line that has sat in a `.env` since then now stops the run rather
  than starting a wrong one, and `quackd doctor` shows the id each provider would actually be given
  instead of guessing at it. One more thing rides on the same list: five OpenAI entries are marked
  as Responses-API models, `gpt-6-astra`, the three pro ids and `gpt-5.3-codex`, so a run with one
  of them opens on `/v1/responses`
  instead of paying a failed Chat Completions call to find that out. The 400 reader 0.8 added stays,
  because it is what covers a model the catalogue has not been told about yet.

- **The CLI has a house style, and a way out of it.** quackd's colours had grown inline: a
  `[green]` in one command, a `Table` in another, an emoji in a third, with nothing saying
  which green meant *this worked* and which meant *this is installed*. `quackd/ui.py` is now
  that vocabulary in one place, and three rules run through it. Text a model, a robot or a
  manifest wrote is never read as markup, so a note saying the ball is `[behind]` the sofa
  and an error naming `quackd[anthropic]` both arrive intact. Glyphs come from a table with
  an ASCII half chosen from the stream's own encoding, and the panels pick their half when
  they are drawn rather than when they are built, because a replay goes to stdout, a run
  narrates to stderr, and a test hands round a buffer of its own. And chrome goes to stderr
  while answers go to stdout, so `quackd list-adapters > adapters.txt` gets the table and
  nothing else. On a Windows pipe, where every decoration used to arrive as a question mark,
  the adapter roster now reads `[ok] built-in: sim2d` and `[exp] jsonrpc`.

- **`--no-color`, and `--json` on `validate`, `list-verbs`, `list-adapters`, `list-models` and the
  four registry listings.** The tables are for a person. `--json` prints one object per line on stdout and nothing else, keeping
  the exit code it would have had, so `quackd validate ducks/*.duck --json` still exits 1 on
  a failure and a script can read which file and why. `--no-color` sets `NO_COLOR` as well as
  quackd's own consoles, because Typer builds a console of its own for every `--help` it
  renders. `FORCE_COLOR=1` is the other direction, for a pipe you are colouring on purpose.
  `quackd list-models` arrived in the same release from the other direction and wears the
  house style too: its model ids still fold rather than elide, and none of the five columns
  goes through Rich's markup on the way, so a label with a bracket in it survives.

- **`-h` works**, everywhere `--help` does, and `--help` groups what it shows. `quackd run`
  offered twenty seven flags in one flat list. They are sorted into Task, Model, Robot,
  Output and Memory now, the commands are grouped the same way, and the root help ends with
  three worked examples.

- **A line saying what the run is waiting for.** A run spends nearly all its wall clock
  inside two calls, a model deciding and a verb steering a robot, and said nothing until
  each of them finished. With `--no-trace` it said nothing at all between the header and the
  verdict, however long that took. A transient line at the bottom of stderr now names what
  is being waited on, which step it is, and how many seconds it has been there. It reads the
  same event stream the trace does, it exists only when stderr is a terminal somebody is
  watching, and it steps out of the way for a confirmation prompt, because a live region
  redirects stdout and would otherwise swallow the question until after it was answered.
  `quackd doctor`, `quackd discover`, `quackd announce` and GIF encoding get a spinner for
  the same reason.

- **A run opens and closes with a panel.** The header used to be one line of middle dots and
  the verdict three lines under it, and on a long run the two ends of the story were the two
  things hardest to find in a screenful of trace. They are now bordered, the verdict is
  coloured by its outcome, and the run directory and the GIF are links where the terminal
  allows it. `quackd trace` prints the same verdict from the transcript, so a replay still
  ends the way the run did.

- **`quackd memory show` is two tables**, notes and recent runs, rather than the block of
  dim text the model is given. The outcome has its own column and its own colour, and an
  episode no longer repeats the duck and the outcome inside the sentence that follows them.

- **`quackd doctor --json`**, and a verdict at the end of the human version. doctor knew
  whether this machine could run anything and said so only through its exit code, which
  nobody reads off a screen. It now closes with one line: what works here, how many extras
  are installed, how many assumptions are unverified, and which cloud providers are one key
  away from working. The `--json` half exists because `collect` and `render` are now two
  functions rather than one: the collector answers in dataclasses with no styling in them,
  and it could not have been serialised before, because every cell it produced *was* a
  markup string.

- **The trace is drawn for the person reading it.** The arrow is a glyph in a gutter now and
  the column says the word it stood for, so `-> sound(...)` reads `→  send    sound(...)`
  and a result is `✓` or `✗` or, for a handover that ended a verb early on purpose, `•`.
  Each step is ruled off with the budget line lifted out of the observation it was buried
  in, and said once rather than twice. The system prompt is an indented block between two
  rules instead of forty lines of the same dim colour. A flock gives each member a colour as
  well as a name. [ADR-0033](docs/adr/0033-terminal-theme.md) records the decision and
  amends [ADR-0029](docs/adr/0029-tracing.md), whose "lines are ASCII first" rule now
  applies where it was earned: the MCP tool result carries exactly the bytes it always did,
  frozen case by case by a new golden, and the terminal asks the stream it is writing to
  which half of the glyph table it can carry. A redirected stderr on Windows still gets
  `->`, `+` and `x`, and the degree signs, section signs and dashes that used to arrive as
  question marks now arrive as ASCII quackd chose rather than as the terminal's guess.

- **A closed pipe is not an error.** `quackd list-verbs | head` answered with a wall of
  traceback about a broken pipe printed on top of the output that was asked for. The console
  entry point is now `quackd.cli:main`, which leaves quietly, and Typer's pretty exceptions
  are off because they print local variables and this process's locals hold an API key, a
  robot's address and its bridge token. A Rich traceback without locals is installed instead,
  so a real crash still reads well.

- **`quackd validate` counts in English** (`12 files valid`, `1 of 3 files failed`), names
  where to look next when it fails, and no longer lets a long file path squeeze the column
  that carries the answer down to nothing.

- **`quackd doctor` reads as a report rather than a wall.** Fourteen tables arrived stacked
  with nothing between them, and eight of them were per-upstream lists of unverified
  assumptions, each followed by its own dim footer. Those sixteen blocks are now two: one
  sectioned table of assumptions, and one table of pins that puts the eight upstreams side
  by side where they can be compared, with the doc paths on a line under it. The sections
  are ruled off and named, and the five local LLM servers are probed behind a spinner
  rather than ten seconds of silence.

- **Extras in `--help` keep their brackets.** `--live` advertised an install called `quackd`
  rather than `quackd[live]`, because Rich had read the extra as markup and eaten it. Same
  for `quackd[microduck-camera]` and `quackd[lan]`.

### Removed

- **Breaking. The Reachy Mini adapter.** `--robot reachy_mini:{sim2d,mock,sdk}`, `quackd[reachy]`,
  the `reachy-spotter` and `reachy-spots-duck-kicks` starters, the `reachy-mini` GitHub topic and
  the PyPI keyword are gone, along with its doc page, its `hetero.gif` and every mention that
  described it as a robot quackd still drives. A command line or a `.duck` naming that adapter
  fails at `--robot` now, which is the correct failure for a body that is not there. The ADRs
  that document it keep their text, marked superseded or amended. The design records under
  `docs/design/` keep their text unmarked, `multi-robot.md` still describing the adapter in the
  present tense, because those are the record of what 0.4 designed rather than of what ships. quackd ships seven adapters now: Microduck, Open Duck Mini v2, LeRobot arm, any rosbridge
  base, XLeRobot, AlohaMini and ToddlerBot. Reachy Mini was the only "stationary head"
  embodiment quackd ever carried, so the machinery that existed solely to pair one with a
  Microduck goes with it: `StationaryHead` and the fixed head poses in `sim2d/world.py` and
  `sim2d/render.py`, and the reachy-only branch of `flock/runner.py`'s `make_sim_flock`. The
  capability-based role auction itself (`flock/auction.py`'s `RoleAuction`,
  `flock.roles` in a `.duck` file) is separate, still-generic machinery and is untouched —
  [ADR-0020](docs/adr/0020-heterogeneous-flocks.md) always said a two-Microduck spotter/kicker
  flock is valid on its own; the auction simply has no second embodiment to pair a Microduck
  against today. [ADR-0023](docs/adr/0023-reachy-mini.md) is marked Superseded rather than
  deleted, and [ADR-0020](docs/adr/0020-heterogeneous-flocks.md) is amended in place, both left
  readable as the record of what 0.4 through 0.8 actually shipped. Nothing about the other
  seven adapters changed.

- **`docs/assets/logo.svg`**, the flat biped and the two-colour wordmark the README opened with
  until this release. The mark entry under Changed says what replaced it and why a second mark
  nothing renders is a second mark that goes stale.

### Fixed

- **The trained gait's 10 of 10 is not reproducible, and four documents stated it as a
  property.** 0.8 shipped that number in this file, in the README three times, in
  [docs/adapter-status.md](docs/adapters/status.md) and in
  [ADR-0030](docs/adr/0030-mujoco-physics-backend.md). What the evidence supports is narrower.
  The nightly job that runs the sweep had 10 of 10 on each of its first five runs, 2026-09-09
  to 09-13, and 9 of 10 on 09-14, seed 4 aborting. Run by hand on the machine that cut this
  release it is 9 of 10, seed 4 again, and it is 9 of 10 there on the `mujoco` and
  `onnxruntime` versions 0.8 shipped as well as the ones this release bumps to, so the bump is
  not what moved it. The failing seed is not a fall: the duck is standing, the ball has not
  moved, and the run ends on the duck's own *same verb fails 3 times in a row* rule after three
  steps. So seed 4 is marginal and platform dependent rather than the gait being broken, and
  the honest claim is that the sweep has returned 10 of 10 and does not always. The shipped
  threshold outside `QUACKD_STRICT_SEEDS` is 8, which it clears every time. Every one of those
  four documents now says what was measured instead of a round number, the ADR by dated
  amendment rather than by rewriting what it decided. The kinematic stand-in is untouched and
  still gates every push at 10 of 10.

- **A camera that died mid-run was invisible on a `quackd run`.** `camera_error` was read only
  by `observe` and by `doctor`, and `observe` cannot be in a `.duck`'s allowlist on the arm, so
  on the one body where a camera is a thing you plugged in yourself the frames simply stopped
  and nothing said why. The camera's health now rides in the arm's own state beside the policy
  and the register errors, which puts it in the transcript and in front of an MCP client, and
  `report_state` says `CAMERA DOWN:` with the reason when a read has actually failed, staying
  quiet for a camera that is merely unread.

- **`quackd doctor` advised about verbs the robot in front of it does not have.** Its no-frame
  advisory named `go_to`, `search_scan` and `approach_and`, none of which exist on an arm bolted
  to a table. It names that body's own camera verbs now. A `camera_health()` proxy on the adapter
  went with it: `doctor` reaches the transport's own method, as it does on every other body, so
  the proxy was called by nothing but the tests that were meant to be covering `doctor`.

- **`lerobot-lookout` asked the pilot for three things it could never see.** A pilot reads a
  verb's summary text and never its data: the dump goes to the transcript and to an MCP client,
  and the observation the model is handed carries the summary. The core `report_state`
  summarises a posture and a policy name, which on a bolted-down arm is two facts it has not got
  and none of the four it has, so the lookout task asked a real model to report where the joints
  are, whether torque is on and whether anything is too hot, none of which had ever reached it.
  The scripted pilot passed the task because it reads the feature dict instead, which no LLM
  gets. The arm supplies its own `report_state` now, and a test pins it through the observation
  text the model actually receives.

- **quackd's log records no longer stop at quackd's own handler.** `install_logging` set
  `propagate = False`, which is the obvious way to stop a record being printed twice and also
  cuts every quackd logger off from the root: an application embedding quackd saw nothing
  through its own `logging.basicConfig`, and `caplog` caught nothing in a test. Propagation is
  left alone now, and the double print is prevented where it starts instead.

- **The duck's chin was missing from the mark, and nothing recorded how to put it back.**
  `web/assets/duck-mark.png` and `web/assets/favicon-96.png` had both been exported with the
  crop window about twenty rows too high, so the head ran off the bottom of its own canvas and
  took the chin, the lower jaw and the orange mouth with it. Opaque pixels sat on the last row
  of each file, which is the tell. The README has opened with that mark since earlier in this
  release and the social card is built around it, so the same slice showed up in three places
  at once. Both are re-cut from quackd-web's `src/assets/duck-source.png`, the only original
  there is, by a new `web/make_mark.py` that exists because the answer to "how was this made"
  was nothing at all. It keeps the old scale deliberately, recovering it by measuring the dark
  visor panel in both images rather than trusting a number, so the mark's weight on the README
  and on the card is unchanged and only the missing part is new. It drops the body at the neck,
  lifts out the heart speech bubble, and refuses to write a file whose ink reaches an edge,
  which is the check that would have caught this. `web/assets/apple-touch-icon.png` was already
  whole and is left alone. quackd-web carries its own copies of the two bad exports and needs
  the same fix, which is a separate repository and a manual follow-up.

- **`quackd[lerobot]` installed a LeRobot that could not open a serial port.**
  At the pinned commit the Feetech SDK and pyserial live in lerobot's own `[feetech]` extra
  rather than in its base dependencies, so `uv pip install 'quackd[lerobot]'` gave you a
  lerobot that imports perfectly and then cannot reach an arm. The extra now asks for
  `lerobot[feetech]`, and `quackd doctor` has a row for the SDK by name, because a failure
  that arrives at `connect()` rather than at import is one a diagnostics command should be
  the first to find.

- **The system prompt no longer promises every body a verb only a duck has.**
  It opened by telling the pilot that composite verbs like `walk_to` close their own loops on
  the camera, falling back to naming `search_scan` when it found neither `walk_to` nor `go_to`.
  A robot that provides none of the three was therefore told about one it does not have, in the
  same prompt whose allowlist does not list it, and a bolted-down arm was told its controllers
  handle balance and gait. It now names a composite verb only when the body actually provides
  one, and says "the motion" for a body that does not move itself. A Microduck's prompt is
  unchanged, byte for byte. Found by reading the arm's real prompt in the new `flock-hello`
  demo, which is what a bundled task on a second body is for.

- **A registered robot's model no longer follows `--provider` to another vendor.**
  `quackd robot add duck-a ... --provider anthropic --model claude-opus-5` then `quackd run
  <duck> --robot duck-a --provider openai` carried the Claude id into OpenAI's catalogue, where
  it was refused with a message blaming a `--model` nobody typed. A stored model now applies
  only to the provider it was stored against, and an explicit `--model` is always taken as
  typed.

- **The browser demo can drive a model that will not take function tools on Chat
  Completions.** 0.8 taught the Python provider to read that 400, move the whole run to the
  Responses API and stay there. The demo at <https://www.quackd.org/simulator> has an OpenAI
  client of its own — the page calls the vendor straight from the browser and there is no
  server here to proxy it through — and that client never learned any of it. So picking
  `gpt-6-astra` in the page ended the run at the first call with the raw vendor message in
  the transcript, on a demo whose whole job is to take one step in front of whoever clicked
  it. The browser now does what Python does: Chat Completions first, and on the 400 that
  names both function tools and Responses, the rest of the run goes to `/v1/responses` with
  that API's shapes — flat tools, the system prompt as `instructions`, and each replayed
  turn as `function_call` and `function_call_output` items keyed by `call_id`. It matches on
  what the API said rather than on a model name, so an unrelated 400 still reaches the
  transcript intact and never moves the run. Both halves are now tested against the same
  quoted 400, the browser's under `node` with a stubbed `fetch` rather than by reading its
  source. A guard runs the two predicates over the same seven vendor messages and fails if
  they ever disagree, which reading them for the right words could not do: that passes
  happily on an `and` quietly turned into an `or`, and an `or` would move a run on any 400
  that said `responses`. The Chat Completions path the rewrite lifted out and re-keyed is
  still every run on a model the catalogue does not mark Responses, and every local server, and it
  now has the guard it never had.

- **Gemini 3 as the pilot.** Two things stood between the provider and a current Gemini
  model, found by pointing a duck at `gemini-3.5-flash`. The schema cleaner did not strip
  `exclusiveMinimum` / `exclusiveMaximum` — pydantic writes `gt=0` that way, and `move` and
  `go_to` are core verbs that both do, so this was every robot that walks or drives, six of the
  seven, rather than some of them; the arm, which provides neither verb, never sent a schema
  the validator could refuse — and
  google-genai 2.x validates the declaration and refuses the keyword; the executor still
  enforces the bound on the way in. And Gemini 3 signs each function call with a
  `thought_signature` the
  next turn must hand back on that same call, or it is refused with a 400; `ToolCall` carries
  it as base64 text (`signature`, empty for every other provider) and `render_contents` puts
  the bytes back on the part. Both are on the default path: the catalogue's first Gemini entry
  is a Gemini 3 model. Verified with a two-step run to success on `gemini-3.5-flash`
  with thought summaries on. The first of the two is older than Gemini 3 and wider than it:
  google-genai has validated function declarations since 2.x, so the schema bug refused every
  verb call on every one of those six robots for every Gemini model, and it sat there because 0.8.0's own
  "non-Anthropic default model IDs are unverified" bullet was literally true — Gemini's default
  had never been sent a request from here. A `--provider` nobody runs is a `--provider` nobody
  finds the bugs in. Thanks to [@Bayway](https://github.com/Bayway) (#13), who ran it.

### Known limitations

- **Nothing has run on hardware, on any of the seven adapters.** 0.8 said eight, and the
  difference is a removal rather than a robot. The arm had the most attention of any body in
  this release, a camera, a checklist, a lookout task and an audit that stopped it taking the
  arm's word for four things, and no SO-101 was driven for any of it. The rosbridge base is the
  one body with neither a lookout task nor a checklist, so it goes on blocks first. The
  Microduck's own hardware ships around Christmas 2026.

- **The trained gait does not reliably do 10 of 10 seeds.** Seed 4 is the one that goes, and
  the entry under Fixed says on which machines, with which physics versions, and what the
  shipped threshold actually asks for. Nobody has worked out why that seed is marginal. The
  other nine walk, and the cartoon's own sweeps are unaffected and still gate every push.

- **No real model has ever refused a task on feasibility grounds here.** The gate, the
  `infeasible` outcome, the hint and the flock handoff are exercised with scripted verdicts and
  an in-process MCP client. Whether a frontier model reaches for `uncertain` when it should, or
  for `infeasible` too readily, is unknown. There is one `live_llm` test waiting for a key. Over
  MCP an `uncertain` verdict stays pending and the model is told to ask the person it is
  chatting with, which has never been watched happening either.

- **No pilot flock has been driven by a real model, or by a real robot.** `flock-hello` runs a
  duck and an arm on the scripted rule, which cannot reason about a datasheet, so what a real
  model does with the `Your flock` section and with `tell` is unknown. N simulated pilots are
  also N separate worlds: nothing checks one member's claimed success against ground truth the
  way the coordinator's shared arena does, a seed does not make a pilot flock reproducible, and
  it costs one budget and one model call per member per turn. No asset shows a pilot flock,
  because N members are N worlds and there is no single GIF to record.

- **The coordinator flock still knows one adapter.** `flock/runner.py` builds Microducks, so
  `flock.roles.<role>.needs`, which validates and is matched against the datasheet a bid
  carries, has nothing heterogeneous to be exercised against end to end. The latent ordering bug
  [ADR-0020](docs/adr/0020-heterogeneous-flocks.md) records is inherited with it: the
  coordinator judges eligibility before members report their vocabulary.

- **Two of the eleven cloud vendors have answered a real request.** OpenAI from the machine that
  wrote this and Gemini from a contributor's. The other nine are built from their own published
  documentation and held by tests that stub the SDK client, which proves the base URL, the key
  variable, the extra and the default id, and proves nothing about whether the vendor answers.
  The catalogue is a hand-read snapshot of 2026-09-12 rather than a live list, so a vendor that
  renames or retires an id between releases leaves this build refusing something real.

- **The browser demo is not at parity with the backend, and nobody has watched it finish.**
  Seven of the manifest's fifteen verbs and none of the three composites, a contract and a
  prompt of its own, an arena that is not upstream's scene, geometric perception, no hash check
  on what it fetches, and a seed that means the same distributions rather than the same layout.
  It has no datasheet and no feasibility gate, so a page asked to carry something will try. The
  page has been booted, a held `W` walks the duck and a real model has answered once, all on the
  machine that wrote it. A run reaching its own end, the recording, the switch thrown mid-run and
  any other browser or machine are still unwatched. The model dropdown and the Responses
  fallback this release adds are tested under `node` with a stubbed `fetch`, which is not a
  browser.

- **Both nightly jobs are red, and always have been.** `microduck assets` fails the trained-gait
  sweep described above and a fall-recovery heading check that passes on the developer's laptop
  and not on the runner. `toddlerbot contract` fails a `stand` that never reports settling and
  loses the deadman test's connection. Neither gates anything by construction, which their own
  headers say in as many words, and `ci` is the job that gates and is green on this tag. Nothing
  in this release touched those code paths. They are three separate things to go and look at.

- **The physics backend is still measured on one machine**, `follow-me` is cartoon only because
  nobody stands in the physics arena, a coordinator flock member must be `sim2d` so the physics
  backend does not fly in one, `GAIT_FLOOR_VY` was never measured but assumed equal to the
  training maximum, so a lateral request that clears the dead zone is sent at full scale while
  one below it is dropped entirely, and `--live` on macOS needs `mjpython` because MuJoCo's
  viewer must own the main thread.

- **Outside this repository.** The social preview GitHub serves is a version behind, and there
  is no API that would let a commit here replace it. quackd-web carries its own copies of the
  two mark files this release re-cut, so the chinless duck is still live there, and its landing
  copy was written around 0.5.

## [0.8.0] — 2026-09-09

Two things, mainly. A run narrates itself now: the system prompt once, then per turn the
observation the model was given, what it reasoned, the verb it chose, every executor gate that
fired, every intent that actually reached the robot and what came back — on the terminal as it
happens, in a `trace` list on every MCP result, and replayable afterwards with `quackd trace`.
And there is a physics simulator, `--robot microduck:mujoco`, which is MuJoCo with upstream's
own Microduck in it: its meshes, its trained walking policy, its 50 Hz loop and now its scene,
with quackd supplying a twist and a head pose and writing no gait at all. A third strand landed
in the last day: the browser demo is live at <https://www.quackd.org/simulator>, rebuilt in the
quackd-web design language, with the keyboard and a model driving one duck at the same time and
neither taking turns with the other.

Still nothing has run on a robot, on any of the eight adapters, and the Microduck's own hardware
ships Christmas 2026. The physics is a simulator walking on upstream's policy, not a duck, and
nobody is in either 3D arena any more, which makes `follow-me` a cartoon-only starter. The
browser demo has been booted in a browser, a held `W` walks the duck, a real model has been
handed it, and a key has taken the duck back out of a live run — all of it on the machine that
wrote it. What nobody has watched there is a run through to its own end, or the recording.
Known limitations, below, says what each of those leaves open.

### Added

- **quackd narrates itself now, on both surfaces, on by default.** Ask it to walk in a circle and
  the terminal used to print a header, an outcome and a run directory. It now shows the whole
  conversation as it happens: the system prompt once, then per turn the observation the model was
  given, what it reasoned, the tool it chose with its parameters, the tokens and the latency,
  every executor gate that fired and why, every intent that actually went to the robot, and what
  came back. A steering loop's burst becomes one line with real ranges (`-> move x26 over 0.5 s
  (vx 0.1..0.2, wz -0.01..0.88)`), because `go_to` recomputes its twist every 100 ms and a line
  per intent would be two hundred lines. Over MCP, where the pilot is the client and its reasoning
  is not quackd's to see, every call that reaches an executor comes back with a `trace` list of
  the same lines, capped at thirty, with the uncapped version on the server's stderr. `--no-trace`
  or `QUACKD_TRACE=0` turns the views off, and `QUACKD_TRACE_THINKING` caps how much reasoning
  each turn prints — 2000 characters by default, `all` for everything, `0` for none — because on a
  thinking model the reasoning is otherwise longer than everything else on the screen put
  together. Every knob this release adds is in `.env.example` — the tracing
  ones, the physics ones, and `QUACKD_OPENAI_API` and `QUACKD_OPENAI_REASONING_EFFORT`, which
  the Responses entry under Fixed explains — bar three that only the test suite reads:
  `QUACKD_REQUIRE_GL`, `QUACKD_LIVE_LLM` and `QUACKD_LIVE_LLM_MODEL`. The transcript is unaffected either
  way: it is the record, and it now carries `llm_request`, `verb_start`, `gate`, `intent`,
  `verb_end` and `note` alongside the kinds it always had ([ADR-0029](docs/adr/0029-tracing.md),
  [docs/architecture.md](docs/concepts/architecture.md#trace)).
- **A flock narrates itself too, one robot per column.** `docs/flock.md` promised a per robot
  transcript and the file held nothing but frames: a member built its executor with no tracer,
  so `--trace` on a flock did nothing at all. Each member now records into its own
  `ducks/<name>/transcript.jsonl` exactly as a solo run records into its own, and the terminal
  gives each one a view with its name on every line, so three robots moving at once are three
  readable columns rather than one interleaving. The coordinator's decisions and the planner's
  one model call print under `flock`, in the same words the GIF captions use. `flock.jsonl` is
  unchanged, and so is `--verbose` ([ADR-0029](docs/adr/0029-tracing.md) amended,
  [docs/flock.md](docs/guides/flock.md)).
- **`quackd trace` replays a finished run.** The transcript held every line the console
  printed and there was no way to read it back except with `jq`. With no argument it replays
  the newest run under `runs/`, and it takes a run name, a timestamp prefix, a duck name or a
  transcript file. `--from-step N` starts part way in, `--no-prompt` drops the system prompt,
  `--thinking all|N` sets how much reasoning to show, and `--frames` adds a line per camera
  frame. It prints on stdout, because a replay is what you pipe. A flock run replays every
  member under its own name.
- **A switch for the system prompt.** It is forty to seventy lines — measured across the
  fourteen shipped ducks on `microduck:sim2d`, and longer again with memory lines or on
  `microduck:mujoco`, where ten assumptions and a longer note about the arena join it — worth
  reading once and tiresome on the fiftieth run of an afternoon. `--no-trace-prompt` or
  `QUACKD_TRACE_PROMPT=0` drops it and keeps everything else; the transcript has it either way.
- **A long burst is shown as it happens.** A twenty second `go_to` used to print nothing until
  it ended, because a burst of intents is coalesced into one line and the line was only written
  when something else happened. A burst still going after two seconds is now flushed as it
  stands and the next line continues it.
- **The robot's own clock, beside the wall clock.** On a simulator a verb that took 4.3
  seconds of robot time and 0.1 seconds of yours now says both, and intents carry the robot's
  clock too. On hardware there is one clock and one number, as before.
- **The scripted pilot says which rule it followed.** `--provider fake` returns no reasoning,
  because there is no model, but it now reports what it saw, how the last verb ended and the
  verb that fell out of that, marked `[scripted]` so it can never be mistaken for a model's own
  words. A run with no API key shows the shape of a real one.
- **A count of what a broken console dropped.** An observer that raises never ends a run, which
  meant a console that raised on every event produced a silent trace and no sign of it.
  `trace_dropped` is in `summary.json`, in the `run_end` record and on the last line of the
  run when it is not zero.
- **The providers return what the model thought.** `ProviderTurn.thinking`, filled from
  Anthropic's thinking blocks, from `reasoning_content` or `reasoning` on an OpenAI-compatible
  server, from Gemini's thought parts, and from an inline `<think>` block a local server did
  not separate. Every one degrades on its own: a model that rejects the request parameter gets
  one retry without it and the run carries on with no thinking text. Reasoning token counts
  ride along in `usage` where the vendor reports them.
- **A physics simulator, with the Microduck's own legs in it** (`--robot microduck:mujoco`,
  needs `quackd[mujoco]`). The cartoon in `sim2d` has always said what it is: it tests the
  agent loop, not the robot, and it will never tell you whether a gait works. `sim3d` is
  MuJoCo, and the duck in it is upstream's: `robot_walk.xml` and its 38 meshes from
  `microduck_rl` at a pinned commit, the `alpha_walking` and `alpha_stand` ONNX policies from
  the Hugging Face Hub at a pinned revision, and upstream's own 50 Hz loop around them. quackd
  supplies a twist and a head pose, which is what a gamepad supplies on the real robot, and
  writes no gait at all. `find-and-kick` succeeds on 10 of 10 seeds with the scripted pilot
  while the duck walks on its trained policy, ground truth checked, and on the same ten seeds
  with the kinematic stand-in. Both are named tests, and both run in CI on the two schedules
  under Changed. Design: `docs/adr/0030-mujoco-physics-backend.md`.
- **Nothing of upstream's is shipped.** The 3D model files are CC BY-NC-SA, so the first run
  downloads them into `~/.quackd/cache`, checks every file against the sha256 it was read at,
  and writes the licence notice beside them. `QUACKD_MICRODUCK_ASSETS` points at your own
  checkout instead, and `QUACKD_MUJOCO_BODY=puppet` runs a kinematic stand-in that needs no
  download and is the body the tests build. `QUACKD_CACHE_DIR` puts the cache somewhere other
  than `~/.quackd`.
- **The gait floor is in the open.** Under the model's own actuators the walking policy does
  not step below about 0.22 m/s or 1.0 rad/s and achieves about 0.42 of what it is asked,
  while `move` defaults to 0.15 m/s. A twist that would produce nothing is scaled up bodily,
  keeping the ratio between its axes so an arc stays an arc; one below a third of the floor is
  dropped rather than amplified into a lurch; and the floor, what was asked and what was sent
  are all in the state, in the system prompt and in `extras.assumptions`. Four skills are
  named stand-ins there too: `kick` and `grab` use the cartoon's contact rules, `sit` is
  refused, and a fall is recovered by standing the model up, because upstream's episodic
  policies did nothing from a standing pose and it ships no get-up policy.
- **You can watch the physics, and record it.** `--gif` on `microduck:mujoco` writes the same
  two-pane recording the cartoon writes, except both panes are MuJoCo renders and the recorder
  samples half as often, because drawing a physics world is what costs. `--gif-size` stopped
  being a sim2d-only flag — its help says "Simulators" now, and it is bounded 64 to 1024 against
  the offscreen buffer the scene actually allocates. `--live` opens MuJoCo's own passive viewer
  rather than the pygame window: orbit, pan and the contact overlay, kept in step with the
  robot's clock, its close button raising the same interrupt. On macOS that viewer must own the
  main thread, so it fails with a sentence telling you to run the same command under `mjpython`
  instead of `python`.
- **A real model driving the physics simulator, opt-in twice.** This is the first time an actual
  LLM has been put in front of this arena at all, which is why it sits here rather than among the
  verification work under Changed. Everything else in the suite fakes the brain: `FakeProvider`
  scripts the verbs and proves the loop, the executor and the world, and nothing showed that a
  model handed this arena gets anywhere in it. `tests/test_llm_in_the_simulator.py` runs one
  against MuJoCo: `hello-world` end to end, and a goal asking for the person who is no longer
  there — which is the one worth the tokens, because without the arena note in the prompt the
  honest reading of `search_scan(target="person")` is to keep scanning, and the run spends its
  whole budget on a lap of an empty room. The live tests cost money and need the network, so
  they are opt-in twice, by a `live_llm` marker and `QUACKD_LIVE_LLM=1`; CI sets neither and a
  bare `pytest` runs neither. They read `.env` the way the CLI does, and `QUACKD_LIVE_LLM_MODEL`
  is what points them at a second model, which is how both of the OpenAI API paths under Fixed
  get exercised. The third test, that the prompt tells the model the arena is empty, needs no key
  and no network — and it is gated behind the `mujoco` extra and named in no CI job, so today it
  runs on a developer's machine and nowhere else. None of this makes a real model part of a
  build: no job in this repository sends a request to a provider, and the release is still tested
  with a scripted pilot.
- **`walk in a square` and `walk in a circle` need no API key, on the command line.** The
  scripted pilot learned two shapes, and both close the loop on the pose the robot reports
  rather than on a stopwatch, so a body that delivers half of what it was asked still walks the
  shape. That is also the correction a model makes, which is the point of them being here.
- **A browser demo, so trying quackd costs nobody an install** (`web/`, a static page with no
  build step, live at `www.quackd.org/simulator`). quackd-web, the separate project that owns
  that domain, builds it by fetching this directory at a pinned commit, and `vercel.json` here
  rewrites `/simulator/*` so this project also answers on the mount if it is ever deployed on
  its own. Locally it is `python web/serve.py`, then <http://localhost:8000/simulator/> — a
  stdlib server that mounts the directory the way the deploy does. `python -m http.server
  --directory web` no longer serves it: every local reference in `index.html` is absolute under
  the mount, so a root server hands over the HTML and 404s the stylesheet and the script.
- **The same physics and the same two policies, in a tab.** MuJoCo's official WebAssembly build
  and onnxruntime-web run `alpha_walking` and `alpha_stand`, with seven of the verbs, a contract
  of its own and the one-tool-per-turn loop in six modules of plain JavaScript. Bring your own
  key for Anthropic, OpenAI or Gemini, or point it at Ollama and keep everything on your
  machine. A run can be recorded from the canvas and shared.
- **Both ways of driving are live at once, and neither takes turns with the other.** The
  keyboard writes the twist the hardware actually takes — `W`/`S` walk, `A`/`D` turn, `Shift`
  strafes, `Q`/`E` look, `G` centres the head, `Space` stops, `K` kicks, `R` stands it up —
  while the box above it hands the same robot to a model. A drive key pressed during a run
  takes the duck back at once, aborts the run and the request in flight with it, and leaves the
  key that did it in the transcript; `O` and the camera keys read without interrupting, and
  `Esc` hands the keyboard back to the arena from wherever focus is. The switch keeps the one
  job that is the demo's whole argument — whether anything here reads English — and no longer
  decides whether you may drive at all.
- **The page wears quackd's own mark, and points back at what it is a demo of.** It is in the
  quackd-web design language rather than an emoji and a default stylesheet. The brand lockup is
  the way home, and "What is this?" answers with the product page rather than a build guide.
  Both are absolute `https://www.quackd.org/` URLs rather than a bare `/`, because this file is
  fetched into another project's build and `web/serve.py` redirects the local root straight back
  to `/simulator/`.
- **The demo is one screen, and its bays have names.** A first-time visitor landed, saw the
  arena, typed a sentence, pressed Run and got an error about a credential the page had never
  shown them: the key box and the key map were both below the fold, and no wording fixes that.
  The switch and the key box are a full-width band across the top now, the arena takes what is
  left of the viewport rather than a fixed fraction of it, and the keycaps run down its right
  side, so both ways of driving are on screen from the first frame. Checked by resizing one
  browser on the machine that wrote it, at 1920x1080, 1440x900, 1366x768, 1366x641 and
  1280x720, with no horizontal scroll down to 360. The numbered bays 01-04 are headings that
  say what they are: `Your API key`, `Command the duck`, `Drive the duck` and `Transcript`.

### Changed

- **Claude runs now ask for a thinking display.** Adaptive thinking is the model's default on
  Opus 5, but the blocks come back with empty text unless the request says
  `display: "summarized"`, so "what it thought" would have been a blank line on every turn.
  Display changes what is shown, never what is thought or billed, and the raw chain of thought
  is never returned by any model. `QUACKD_THINKING_DISPLAY=omitted` opts out.
- **Gemini's thought parts no longer land in the answer.** They were appended to `text`
  regardless, so with thoughts on they would have been replayed to the model next turn as
  things it had said. `QUACKD_GEMINI_THOUGHTS=0` stops quackd asking for them at all.
- **The transcript flushes on events, not on every intent.** The flush was a syscall on the
  event loop between two deadman resends of a steering verb. Loss on a hard kill is now bounded
  by the text buffer and ends at every `verb_end`.
- **A verb ended by another layer keeps its own word.** A flock role change preempts an
  in-flight composite verb, and the executor had no branch for that, so every handover in a
  three robot run printed a red `ERROR`, which the docs define as a bug in quackd. It reads
  `PREEMPTED` now, in yellow.
- **`--verbose` is the compact view, not a second one.** With the trace on, the executor's own
  one-line-per-verb log would say every verb twice, so it stands down: `--verbose` is what you
  get with `--no-trace`, and on the MCP server the executor's log drops to DEBUG. Nothing lost
  its `log` callback, which the flock's member records and several tests read.
- **A verb that starts always ends.** `Executor.run_verb` now emits exactly one start and one
  end with an outcome, through a `finally`. Seven exits used to leave nothing behind, among
  them the two that matter most: a verb cancelled mid-flight by a kill switch or a heartbeat
  failure, and the repeat-failure abort.
- **The arena is upstream's own scene, and the head camera is the one place it cannot go.**
  `sim3d` no longer draws a world quackd invented. It builds upstream's: the blue-grey checker
  with edge marks, the gradient skybox, the haze, the headlight at ambient 0.3 and the
  directional light overhead, and the viewer's own azimuth 160 and elevation -20, all taken from
  the `scene*.xml` wrappers in `microduck_rl`, so a duck here stands where a duck there stands.
  Shadows are upstream's default and the largest render cost there is, so they are a switch,
  `QUACKD_MUJOCO_SHADOWS`, rather than a constant. The head camera is the exception, for a
  measured reason under Fixed: it keeps the same checker with the colour taken out, in a geom
  group hidden from every human view including the live viewer, and no skybox. That split is a
  stand-in like the others, so it is a ninth line in `extras.assumptions` and in
  [ADR-0030](docs/adr/0030-mujoco-physics-backend.md), and it is worth saying plainly that the
  model's camera does not see what any recording of the same run shows. The README hero was
  re-recorded in the new scene at the same seed, and again once the arena emptied so that the
  picture shows the world this release ships: 2.72 m walked, the square closed 0.14 m from where
  it started, both times. `MicroduckBody` draws nothing from the world's shared generator, so
  removing the person moved neither number.
- **Nobody is in the 3D arena, and the cartoon is the only simulator with a person in it.** The
  marker is gone from both 3D worlds — the MuJoCo one in `quackd/sim3d/` and the browser demo —
  while `sim2d` keeps its own, so this is the single place the two arenas differ. What it costs
  is a starter: `follow-me` is a task about following somebody and cannot succeed on
  `microduck:mujoco`, and `patrol-and-quack` still runs there with its person clause gone
  vacuous, leaving three walk legs. Both `.duck` files are untouched, because their hashes are
  pinned in the goldens and they are right for the cartoon they were written for, so nothing
  stops you pointing `follow-me` at the physics backend and nothing pretends it will get
  anywhere; the README and `docs/faq.md` say which starter is now cartoon-only. What it does not
  cost is the seed: the cartoon draws its person from the RNG after the duck and the ball, so
  dropping that draw leaves every seeded duck and ball position bit-identical between the two
  simulators, checked at all ten seeds, and the parity test was not loosened for it: it still
  holds both arenas to the same duck pose and the same ball, the ball to the millimetre it always
  allowed. What did move is everything downstream of that draw on the shared generator — the
  puppet's gait noise, the kick skew, the scoop coin-flip — so the ten-seed `find-and-kick`
  sweep was re-run under `QUACKD_STRICT_SEEDS=1` on both bodies and still passes 10 of 10. The
  model is told outright rather than left to infer it: the mujoco branch of the system prompt
  says nobody is in the arena and that a `person` detection is scenery misread, and the demo's
  own contract says the same. `search_scan` still offers `person` as a target and the head
  camera's detector still carries the person hue band, because one verb registry and one
  detector serve the cartoon, YOLO on a real camera and this, which is why the colourless floor
  under Fixed stays and why its guard got stricter. `people` has left the 3D snapshot and
  `extras`, the `person` flag has left the world's constructor, and a tenth line joins
  `extras.assumptions` ([ADR-0030](docs/adr/0030-mujoco-physics-backend.md) and
  [docs/faq.md](docs/faq.md) amended).
- **`quackd doctor` reports the physics backend's upstream too.** Every run of the command now
  ends with a `microduck_rl` table: the UNVERIFIED count with each one's note, the `microduck_rl`
  pin and the policy revision with the date they were read at, the VERIFIED count, and the line
  that the model and the policies are fetched at run time and never shipped. It is the one place
  that limit is stated in the tool rather than in a document — and it is a different limit from
  the hardware adapters', because this upstream is measured here, on one machine, rather than
  never run at all. `mujoco` joins doctor's list of optional extras, and the `microduck` row in
  `list-adapters` gained the `mujoco` backend with the extra named in its status line.
- **A documentation pass over everything 0.7.0 shipped, for fewer words rather than more.** The
  README lost about a fifth of its length: the table that listed all eight bodies a third time is
  gone, the bullet list that repeated the status table is gone, the "since 0.4" release narration
  is gone, and the tagline no longer names four bodies out of eight. Every count and claim in the
  living docs was then read against the code, twice, and the second read is where the value was.
  What it found that would have stopped a reader dead — four commands that could not run and two
  safety claims printed the wrong way round — is under Fixed, with the other things that were
  broken rather than merely wordy.
- **The rest of what reading found.** The README said the AlohaMini's arm verbs appear only with
  quackd's host wrapper, when they exist and refuse without it, which is the distinction the
  README's own Open Duck row exists to make. The Open Duck's camera capability comes from
  `--camera-url` and not from `duck_config.json`, so the checklist's abort note stopped an owner
  at a correctly built duck; `FALL_SIGNAL` described an IMU read the daemon does not do; the
  sound row said the bridge resolves a mood to a file when it presses the pad's random-sound
  button. `bridge/open_duck/README.md` was missing three flags from a table that claims to list
  them all, and still said a velocity ceiling above upstream's is "applied on top" when 0.7
  refuses to start. The ToddlerBot page called itself the second body whose robot side quackd
  ships (it is the third) and credited the daemon with a walk clamp that quackd applies. The
  ToddlerBot checklist never mentioned that the daemon binds loopback, so every `<host>` in it
  would have been refused. `docs/memory.md` and `CONTRIBUTING.md` said every solo starter asks
  for a `remember` when the three 0.7 lookouts and `hello-world` do not, `docs/architecture.md`
  gave the Open Duck a stand-up policy it does not have, `docs/lan.md` documented a `--json`
  flag that `list-verbs` never had, the FAQ named only the duck's token variable for two
  daemons, `docs/duck-spec.md` promised a `validate` warning nothing emits and three flock
  ranges that included a zero the schema rejects, `docs/safety.md` put the consecutive-failure
  abort at the wrong stage of the executor, `docs/reading-robots.md` counted three upstreams
  that de-torque on disconnect when there are four, and `SECURITY.md` claimed a version
  handshake and a `doctor` read-out that the AlohaMini wrapper and `doctor` do not do. The rest
  is release-history narration deleted from pages that describe quackd as it is now.
- **A second reading, and the three claims a user would have acted on.** `docs/mcp.md` told an
  operator that after a transport failure every later call to that robot is refused. `stop` is
  exempt and still runs, which the refusal text itself says, so the page took the brake away at
  exactly the moment a pilot reaches for it. `docs/flock.md` offered a duck falling in
  `microduck:mujoco` as the way to exercise fall handling, when both the CLI and the runner
  refuse a flock member that is not `sim2d`, so the route landed the reader on an error naming
  that very page — a flock is still simulator-only and still cartoon-only, and the physics
  backend does not fly in one. And the README's status table said the trained-gait sweep runs on
  a developer's machine when it runs nightly in CI, which undersold what is actually verified.
  Fourteen smaller ones behind them, across eleven files: the loop's fifth outcome (`error`)
  missing from two documents; `trace` still absent from `docs/architecture.md`'s command list, an
  edit written during the tracing work that silently did nothing because the script asserted on
  the aggregate rather than per replacement; the README intro promising the cartoon to "the other
  seven bodies" when the LeRobot arm, a rosbridge base and the XLeRobot ship a mock and nothing
  else; the replay row never saying stdout while the configuration row two sections down says the
  trace is on stderr, which points a reader at `2>` and an empty file; and a prose list of the
  transcript's kinds above `docs/architecture.md`'s own table of them, drifted by six kinds.
- **CI ends a hung test run in minutes, with every thread's stack.** A 20 minute job timeout,
  pytest's own `faulthandler_timeout` at 300 s, and an exit watchdog in `tests/conftest.py`
  that dumps every thread and forces the exit if the interpreter has not gone two minutes after
  pytest is done, flushing first so the failure report survives. The first hang it caught had
  run for six hours three times and named nothing; with it, the same hang failed in five
  minutes with the frame in the log.
- **CI runs the physics backend, and the browser demo has a floor under it.** A `physics` job
  installs `quackd[mujoco]` and runs the two sim3d modules against a software rasteriser, so
  36 tests that skipped on all six runners now execute somewhere. It sets
  `QUACKD_REQUIRE_GL=1`, because a job whose purpose is to run tests that skip has to fail
  when they skip. A nightly `microduck-assets` job fetches upstream's real model the way a
  user's first run does, into a runner that is then destroyed, and runs the trained-gait
  sweep; it caches nothing, because `docs/licenses.md` says no CI fixture carries a byte of
  those meshes. `tests/test_web.py` checks the browser demo without a browser: the ids the
  script looks up, the rule that was hiding nothing, static CDN imports, key storage, the pins
  and gait numbers shared with Python, and `node --check` on each module.
- **The demo's deployment moved off GitHub Pages, and a merge to `web/` does not reach a visitor
  by itself.** `.github/workflows/pages.yml` is deleted. It was the one thing on this repository
  that stayed permanently red, failing at configure-pages on a repository where Pages was never
  enabled, and it was never the deployment this project uses: the address it promised was a
  github.io one that would have 404ed. `vercel.json` replaces it, with the install and the build
  stubbed out because there is nothing to build — every dependency in that directory is a CDN URL
  the page fetches at run time. And because quackd-web fetches this directory at build time, a
  push to `main` touching `web/` pings a deploy hook
  (`.github/workflows/refresh-the-simulator.yml`) asking that site to rebuild. It is inert until
  `VERCEL_DEPLOY_HOOK` exists — a missing secret says so and exits zero, rather than showing a
  fork a red mark for a deployment nobody configured — it runs only on this repository, and
  `curl --fail` is deliberate, so a hook that has quietly died cannot look like a working one.
  That last part is the one a contributor needs: for this directory, merging is not shipping.
- **The 10 of 10 claim has a test.** `test_find_and_kick_on_the_real_duck` runs the same ten
  seeds on upstream's trained gait rather than the stand-in, and asserts the body really is
  the trained one first, because a silent fall back to the puppet passing it is the point.
  Measured: 10 of 10, so the claim was true and simply unbacked. `assets.py` has eighteen
  tests where it had none, including the tarball allowlist `SECURITY.md` makes a claim about,
  and the gait floor moved into a module that imports no `mujoco`, so the arithmetic deciding
  whether a duck moves or only reports moving is checked on every runner.

### Fixed

- **The physics backend crashed, hung and misreported, on paths its tests never reached.**
  Reading the state of a duck lying on its back raised a `ValueError` out of every
  `get_state()`, because the tilt's `acos` was clamped on one side only: the reading a fall is
  exactly when a pilot needs it. A world that could not step killed the clock advancer, which
  is a task nobody awaits, so every parked verb waited on a future that would never resolve
  and the reason was collected by the garbage collector; the failure is now raised into every
  sleeper and out of the heartbeat. A subscription running beside a verb deadlocked both,
  because the clock keeps one parked waiter per participant and the second silently overwrote
  the first; that collision is an error naming the id now. A non-finite twist walked through
  `np.clip` and the gait floor's dead zone to arrive at the servos, and MuJoCo answers a
  non-finite state by resetting the world rather than raising, so a run could carry on
  reporting poses from a world that had quietly restarted. And a headless machine got an
  OpenGL traceback where `docs/faq.md` promises a sentence naming `MUJOCO_GL`.
- **The state said `walk` while the duck stood still.** `policy` was read from the twist quackd
  commanded rather than from what the body did with it, and a body is free to decline one:
  below the gait floor it sends nothing and stands. That is the exact failure the gait floor
  exists to prevent, and it reached the model while the body's own truthful `extras["policy"]`
  did not. `head_yaw` reported the angle a `look` asked for, on a body whose neck is a servo
  that lags, while bearings already came from the achieved pose.
- **`stand_up` stood the duck up facing backwards.** The heading came from the trunk
  quaternion, which is arbitrary at the gimbal degeneracy where a face-down duck lies:
  measured on the real model, a duck facing +x that goes onto its nose reads as yaw 3.14. It
  now comes from the trunk's own forward axis, the duck is pushed clear of anything it landed
  on, and the twist that put it down is cleared rather than resumed.
- **The duck reported a person standing in front of it, in every single frame.** Upstream's floor
  and the person marker that was then in the arena could not share a camera. Measured off a
  rendered frame, the checker sits at hue 105 with saturation to 185 and value to 229 and the
  marker at hue 114, saturation 185, value 197: they overlapped on all three channels, so no
  threshold separated them, and the skybox does the same thing above an 8 cm wall. With the scene
  as upstream ships it the colour detector reported somebody standing 0.12 m ahead in 48 frames
  out of 48, whichever way the duck faced. The head camera now looks at the same checker with the
  colour taken out, in a geom group hidden from every human view, and at no sky: phantoms went 48
  of 48 to 0 of 32 on the sweep that now guards it, and both seeded sweeps still pass 10 of 10.
  Nothing caught it before because the parity test only ever asserted about the ball; three tests
  do now, one sweeping four seeds by eight headings and counting every person reported at any
  range, where it used to excuse anything beyond a duck's own radius, one pinning which floor the
  camera collides with, and one holding this arena to having nobody in it. That the marker left
  the physics world in the same pass is why the two floors stay and why the guard could tighten:
  upstream's checker is still the blue the detector calls a person, that band still has to be
  there for the cartoon, and with nobody left to weigh a detection against, every person reported
  from here is scenery.
- **The stand-ins were told to the transcript and not to the model.** `extras.assumptions` is
  what a backend says quackd stands in for, and only `FakeProvider` ever read it: every real
  provider sends `obs.text`, built from `DuckState.summary()`, and neither had a branch for it.
  ADR-0030's claim that a transcript never implies more than happened was true of the
  transcript and false of the model. The list now goes into the system prompt in the robot's
  own words, and a pointer into `summary()`, because the MCP server has no system prompt at all
  and a Claude Desktop pilot reads tool results only.
- **An interrupted download left a cache the next run blamed upstream for.** `assets.py` wrote
  extracted files straight to their final path with no lock, so a Ctrl-C or a second terminal
  left a half-written model that the next run reported as a pin mismatch, asking the user to
  report that upstream had moved the archive. Files now land in a scratch directory, are
  verified there and renamed into place under a lock. `fetch` caught `OSError` only, so a
  truncated body and a captive portal's HTML both escaped as bare tracebacks; the cache
  variable never expanded a tilde, though `.env.example` suggests one; and the licence notice,
  the file that makes the licence claim true on disk, was written only on a fresh fetch.
- **A square was four legs and a hope.** `square_strategy` declared success on the leg count
  alone, and that outcome is what `docs/assets/hero3d.py` publishes the README hero on. It
  measures the distance back to where it started now and says it either way. Both shape
  strategies also sampled the heading once per move and accumulated it with `abs(wrap(...))`,
  so a long turn discarded whole revolutions.
- **The browser demo could not load, and a stale key could leave the machine.** A CSS rule kept
  the loading panel over the canvas for good, a blocked CDN or a machine without WebGL left a
  dead page with no message, any physics error stopped the loop silently and hung every verb,
  and Space stopped activating every button on the page. Every verb reported success, including
  a kick that missed; nothing validated a model's arguments, so `duration_s: 1e6` hung the tab
  and `duration_s: "soon"` reported success having done nothing; and `stand_up` restarted the
  whole episode, which made the time budget permanently negative. Switching the provider to a
  local server disabled the key field without clearing it, and a disabled input's value is
  still readable, so a key pasted for one vendor went out as a bearer token to whatever host
  was typed in the free-text box.
- **Two calls at once no longer report each other's work.** The per verb intent tally was one
  stack on the executor, so an MCP `quack` that overlapped a `move` reported the move's intents
  as its own and the move reported neither its resends nor its stop. Each verb now counts in a
  context variable, which asyncio copies into a task at creation, so a nested verb still rolls
  up into its parent and two concurrent calls never cross.
- **A cancelled call cancels its verb and sends a stop.** An MCP client that cancelled, or a
  second Ctrl-C, returned at once and left the legs moving with nothing to stop them. The verb
  is cancelled, a stop goes out, and the trace says `gate cancelled` so the record shows why.
- **A Ctrl-C at a y/N prompt is a denial, not a traceback.** The confirm gate recorded `asked`
  and never the answer, and a prompt that raised ended the run with an empty reason and no
  gate. It now records `allowed` or `denied`, and names the exception when the prompt itself
  failed.
- **A state read that fails still sends a stop**, and a record sink that raises can no longer
  stop the heartbeat from aborting the run.
- **An interrupted run ends as `aborted`.** A `KeyboardInterrupt` or a cancellation was
  recorded as "loop exited unexpectedly". The summary is written and the transcript closed
  whatever happens on the way out, and writing to a closed transcript is a no-op rather than an
  exception inside a task nobody awaits.
- **A local model no longer runs a verb it only contemplated.** An inline `<think>` block is
  split out before the JSON fallback reads the answer, so a tool call the model was reasoning
  about inside its thinking is not executed. An unterminated `<think>` is thinking to the end
  rather than the answer.
- **A model's own words cannot break the terminal.** Rich read `[/think]` in a reason or a verb
  description as markup and raised. Everything the model wrote prints as text.
- **A malformed provider response is a `ProviderError`.** An empty `choices` or a usage field
  that is a string reached the user as a traceback from inside the SDK.
- **The three thinking fallbacks match the errors they were written for.** A 400 about a
  replayed thinking block disabled thinking and retried the same request, and Gemini retried
  any error whose text happened to contain the word.
- **A model that will not take function tools on Chat Completions can still drive the robot.**
  Some reasoning models refuse function tools on `/v1/chat/completions` and say so in a 400 that
  offers two remedies, of which only one exists: `reasoning_effort="none"` comes back from
  `gpt-6-astra` as an unsupported value for that model, and the tools are refused at `low`,
  `medium`, `high` and `xhigh` alike and with no effort field at all. Probed directly,
  chat/completions with tools fails four ways there while `/v1/responses` with tools returns a
  clean `function_call`. Every verb quackd has is a function tool, so for such a model Chat
  Completions is not a degraded path, it is no path: before this, pointing quackd at one ended
  the run at the first call with a `ProviderError` and nothing but a different model to try. The
  provider now reads that 400, moves the whole run to the Responses API and stays there rather
  than paying a failed call every turn, and it matches on what the API said rather than on a
  model name, so an unrelated 400 still surfaces. The two APIs agree on almost no field name, so
  Responses gets its own renderer and parser beside the existing pair: tools go flat, the system
  prompt becomes `instructions`, history becomes `function_call` and `function_call_output` items
  keyed by `call_id` — the item `id` is a 400 — images are `input_image`, and the usage counts
  live under different names. `QUACKD_OPENAI_API=responses` starts there without waiting to be
  told; `QUACKD_OPENAI_REASONING_EFFORT` sets the effort on either API and is sent only when set,
  so `gpt-5` and every OpenAI-compatible server are untouched. Verified on the physics simulator
  end to end rather than at the first call: `gpt-6-astra` runs `hello-world` to
  `declare_success` and gives up correctly on the person who is no longer in the arena, with
  images and tool results flowing over Responses throughout.
- **The MCP server logs each call as one block when it ends**, so two calls at once are two
  readable blocks rather than an interleaving, and the heartbeat's own note and stop reach
  stderr the moment they happen instead of being attributed to whichever call was open. A
  session refusing calls because the link died now says so.
- **A renderer bug never turns a tool result into an internal error**, a failed write keeps the
  burst for the next flush, and a gate shows a parameter the model left unset, which on a dry
  run is exactly what you are checking.
- **A run that failed outside the safety layer said `loop exited unexpectedly`.** A bad key, a
  429, a dropped connection or a dead camera is not a `SafetyStop`, so nothing caught it, the
  summary recorded a default string, and the CLI printed a traceback. The call that failed is
  now in the transcript with its error and its latency, `run_end` says what happened, and the
  CLI answers in one line like every other failure.
- **Three documented commands could not run, all the same way.** `quackd list-verbs` takes only
  `--robot`, so the one line on `docs/adapters/lerobot.md` and on `docs/adapters/rosbridge.md`
  that reaches a real robot exits 2 on an unknown option, and step 7 of the ToddlerBot checklist
  did too. Worse than failing, that step could not have done its job even without the flag:
  `list-verbs` builds its table from the static description, so `move` is absent whether or not
  a walk checkpoint is staged. All three are `quackd doctor --robot X --address Y` now, which is
  the command that connects and reports what the robot itself said.
- **A fourth could not run, for a different reason.** The sixty-second block's
  `uvx --from "quackd[anthropic]" ... --robot microduck:mujoco` is the one line in the repository
  joining bring-your-own-key to the trained gait, which is the thing in this release most worth
  trying, and it installs the SDK and no physics: the extras are independent, so the backend
  refuses before anything happens. It asks for both now. The provider table below it had the same
  gap — every row ran the cartoon, because no row named a robot — and it now says how to put the
  same model on the physics simulator.
- **Two safety claims were the wrong way round.** `docs/adapters/xlerobot.md` told an owner that
  when a camera takes down the observation stream "the arms keep holding their last goal under
  torque, which is what you want". Upstream's host at the pin calls `get_observation()` outside
  its inner `try`, so that exception leaves the loop and reaches `finally: robot.disconnect()`,
  which is the torque-off path: the arms go limp and drop what they hold, and the watchdog never
  fires because the loop that checks it is gone. The hour-mark exit takes the same path, which
  the XLeRobot checklist now says at the step about the clock. And the README claimed a verb
  timeout aborts the run; the executor stops the robot and returns a failed result, which the
  model then sees.
- **`--dry-run` never showed the intents it promised.** `docs/safety.md` has always said it
  prints every intent a model would send; the dry-run branch logged a verb name, and only
  under `--verbose`. The trace now names the verb and the parameters it would have sent.
- **A failed ZeroMQ test could hold the interpreter's exit forever.** pyzmq's `Context.__del__`
  closes each surviving socket with its own linger, which defaults to forever, and a test that
  fails never reaches its `close()`, so one flaky assertion held three macOS CI jobs for six
  hours with the last line of dots still unflushed. Both ZeroMQ clients and both fakes set
  `LINGER` to 0 when a socket is created now, not only on the close path, so a context torn
  down by anything but the happy path never waits on a peer that is gone. The flaky test
  itself, `test_a_stale_reading_is_a_heartbeat_failure_not_a_reading`, used a 50 ms stale limit
  a loaded runner cannot keep and could read the fake's last observation as fresh; it uses the
  host's own window now and drains the socket once after the host stops, so the silence it
  asserts is silence.
- **Two SVGs shipped in every published package.** The sdist excluded `docs/assets/*.gif` and
  `*.png` under a comment saying no published artefact needs those images, and `contributors.svg`
  and `logo.svg` matched neither pattern, so both went out in every release so far. Only the GIF
  is licence-critical — it renders upstream's CC BY-NC-SA model, and the published package has to
  stay wholly Apache-2.0 — but that exclude is the rule keeping it out and nothing tested the
  rule. Every image under `docs/assets` now has to be matched by a pattern, with the reason in
  the failure message. The two scripts living beside those images, one of which builds the README
  hero, were outside ruff and mypy for the same reason and are under both now.
- **mypy on Python 3.12 rejected the ToddlerBot daemon's fake body.** The lock resolves numpy
  2.2 there, whose stubs infer a fixed one-dimensional shape from `np.full` and refuse the
  `asarray(...).copy()` stored into the same attribute, so it is annotated shape-free now. The
  release gate ran under 3.11 and never saw it, which is why the `v0.7.0` tag's own CI run is
  red on three jobs while the published files are unaffected.
- **The ToddlerBot's shutdown could torque off a robot that was still moving.** `settle()` gave
  the slew to the safe pose a fixed 8 seconds, but the slew is handed out one control tick at a
  time, so a joint 1.5 rad away needs 5 seconds of slew and closer to 9 of wall time. It ran
  out, logged `did not settle`, and `shutdown()` went on to disable torque on a standing
  humanoid, which is the fall the method exists to get in front of. The deadline is now sized
  to the distance the body actually has to cover, with a floor for one already there and a cap
  so a jammed joint is not waited on forever. Found because the test that covers this settles a
  full-scale slew inside a wall-clock budget, and a loaded macOS runner is slow enough to miss
  it; that test now uses the real default and a second one checks the rule arithmetically.

### Known limitations

- **Nothing has run on hardware, on any of the eight adapters.** Unchanged since 0.7.0, which
  lists each one and the lookout task to point at a body first. The Microduck's own hardware
  ships Christmas 2026. What the physics backend adds is a simulator, not a robot: `sim3d` walks
  on upstream's trained policy and matches upstream's own numbers, but a duck in MuJoCo is still
  a duck in MuJoCo.
- **The browser demo starts, the hand works, and the loop has now been watched once.** The page
  has been booted in a browser on the machine that wrote it, and a held `W` walks the duck. A
  real model has been handed the page and answered: `gpt-5` read the arena and returned
  `move(vx=0.26, duration_s=0.6)` about seven seconds in, one request, no page errors. `W` then
  took the duck back out of that live run, and the transcript named the key that did it and
  ended the run as aborted. Both were watched once, headless, on that same machine. Nobody has
  watched a run reach its own end, the recording, the switch thrown mid-run, or the page on any
  other browser, screen or machine. `tests/test_web.py` is the floor under that, and
  a floor is all it is: it runs without a browser, so it cannot watch anything render. The demo
  also loads exactly two learned policies, `alpha_walking` and `alpha_stand`; its kick is a
  scripted impulse in the cartoon's cone, not a policy; and it has no scripted pilot, so the
  pre-filled goal still needs a key or a local server before anything happens.
- **The physics backend's upstream is measured here, on one machine.** `quackd doctor` prints
  that table rather than leaving it in a document, because it is a different claim from the
  hardware adapters': not "never run" but "run once, by one person, on one laptop". Four edges
  come with it. The head camera deliberately sees a floor no human view shows, so what the model
  perceives and what the GIF records are not the same picture — and the GIF is one change behind
  besides: `docs/assets/hero3d.py` stopped drawing a person in this release and
  `quackd-on-off.gif` was not re-rendered after it, so the animation the README leads with still
  shows a blue marker in both of its top-down insets, in an arena this release says has nobody in
  it. Nobody is in the physics arena, so `follow-me` is a cartoon-only starter and
  `patrol-and-quack` on `microduck:mujoco` is a patrol with nobody to announce. A flock member
  must be `sim2d`, so the physics backend does not fly in a flock. And `--live` on macOS needs
  the command run under `mjpython`, because MuJoCo's viewer must own the main thread.
- **Non-Anthropic default model IDs are unverified.** Override with `QUACKD_MODEL`. One
  qualification this release earns and no more: `gpt-5`, the OpenAI default, drove whole runs on
  the physics simulator while the Responses path above was being written, on one machine on one
  afternoon. That is enough to know the ID answers and takes tools, and not enough to move it out
  of this bullet; the README's configuration table still lists all three as unverified. Gemini's
  default and Grok's have never been sent a request.

## [0.7.0] — 2026-09-07

A sixth, a seventh and an eighth robot, two hardware paths audited against upstream rather
than against themselves, and an abort that finally reaches the verb it is aborting. Two of the
new robots quackd drives without importing anything from them, because neither is an
installable package, and the third has no network API at all, so quackd ships the loop it runs
on. The Open Duck Mini's daemon and installer were read against a duck set up exactly the way
the docs instruct, and it could not start the bridge; if it could, it would have had no camera
verbs; and the operator's Ctrl-C would not have stopped it. The Microduck's transport was read
against an API that had moved on while nobody was looking. Still nothing has run on a robot;
what changed is that several things which could not have worked now can, and several claims
that were not true no longer are.

### Added

- **The XLeRobot: a dual-arm mobile manipulator on an IKEA cart, about $660 to build**
  (`--robot xlerobot:mock` or `xlerobot:zmq`). Two five-joint arms with grippers on a
  three-omniwheel base that really can drive sideways, so `move` here carries a `vy` that
  means something for the first time. It is also the first body quackd talks to **without
  importing anything from it**: XLeRobot is not a package — no PyPI entry, no `pyproject.toml`,
  and its documented install is copying files into an existing lerobot tree — but it already
  ships a ZeroMQ host, so quackd speaks that wire and its extra is `pyzmq` and nothing else.
  That keeps the 3.11 floor and works on Windows, unlike `quackd[lerobot]`. Design:
  `docs/adr/0026-xlerobot.md`, with the page at
  [docs/adapters/xlerobot.md](docs/adapters/xlerobot/README.md).
- **The whole wire format is exercised against a fake host over loopback**, on every CI
  platform. Upstream ships no test, no CI and no simulator that runs — its ManiSkill host
  imports `xlerobot_single`, which is defined nowhere in the repository — so the other end of
  the protocol is written from upstream's source at a pinned commit and quackd's real client is
  driven against it. That caught a bug no reading would have: `stop` rebuilt its hold from the
  latest observation, which is several cycles behind and carries no timestamp, so stopping
  would have commanded an arm back towards zero. A stop that moves an arm is the failure this
  project exists to prevent. `stop` now zeroes the wheels and leaves every arm goal exactly
  where it already was.
- **`xlerobot-lookout`**, the task to point at a real cart first: nothing in its allowlist
  moves a wheel or an arm. It is the thinnest starter quackd ships, and honestly so — without
  a head to turn and without a voice, a task that moves nothing can only look and report.
- **The AlohaMini: two arms on a motorised lift, on a wheeled base** (`--robot alohamini:mock`,
  `sim2d` or `zmq`). quackd's second bimanual body and its first with a vertical axis. Like the
  XLeRobot it is reached by speaking its ZeroMQ host protocol rather than importing it, and for
  a stronger reason: upstream is a fork of LeRobot that *calls itself* `lerobot`, is not on
  PyPI, and installs only from a large git clone on Python 3.12 with torch. Speaking the wire is
  also more correct than importing would have been, because upstream's own client throws away
  the camera list the host sends and its robot-model default disagrees with the host's, with
  nothing cross-checking either. quackd reads both off the wire, so neither can be silently
  wrong. Design: `docs/adr/0027-alohamini.md`, with the page at
  [docs/adapters/alohamini.md](docs/adapters/alohamini/README.md).
- **A host wrapper for the robot, because upstream's own host leaves the arms limp.**
  `configure()` disables arm torque and both of its `enable_torque()` calls are commented out;
  nothing else in the driver turns it on. So `bridge/alohamini/` holds a wrapper that runs
  upstream's loop with torque enabled and the lift stopped, and adds three fields so quackd can
  tell it from a stock host. Without it, `move_joints`, `gripper` and `home_arms` refuse and say
  why, rather than commanding joints that would not move.
- **A stop that actually stops this robot.** Three upstream behaviours conspire against one.
  All three velocity keys are mandatory in every payload, because `send_action` indexes them
  with no default and would otherwise discard the whole action, arms included, without
  refreshing its own watchdog. The lift latches, so a payload that says nothing about it leaves
  it travelling. And the lift's two keys are not symmetric, so a payload carrying both freezes
  it instead. One function builds every payload and holds all three invariants, and the fake
  host reproduces the bugs so the tests mean something: `test_stop_zeroes_the_lift` fails
  against the naive three-key stop that the shape of the driver invites.
- **`alohamini-lookout`**, the task to point at a real AlohaMini first: nothing in its
  allowlist moves a wheel, an arm or the lift. It is the same shape as `xlerobot-lookout` and
  for the same reason, since this body has no head and no voice either: `observe`,
  `report_state` and `stop` are the whole allowlist, a human aims the camera, and the answer
  goes in the reason the pilot declares success with. The budget is twelve steps and three
  minutes. The scripted pilot takes one frame and answers, and on `alohamini:sim2d` it succeeds
  on ten of ten seeds with the world's ground truth checked that the base never moved. The
  AlohaMini checklist runs it at step 5, on upstream's stock host with the arms still limp.
- **The ToddlerBot: a small open source humanoid, and the first body here that can hurt
  itself** (`--robot toddlerbot:mock`, `sim2d` or `bridge`). Two arms, two legs, a two joint
  neck and thirty servos, on a machine with no network API of any kind, so quackd ships the
  daemon that runs on it. That is the Open Duck Mini's shape, but for a second reason that
  matters more: a verb is episodic and this body is not. Its `step()` is a no-op, so nothing
  times out and nothing re-arms, and a humanoid frozen mid-stride while a model thinks is a
  humanoid on the floor. The daemon runs the fifty hertz loop and quackd's intents steer what
  it is already doing. Design: `docs/adr/0028-toddlerbot.md`, with the page at
  [docs/adapters/toddlerbot.md](docs/adapters/toddlerbot/README.md).
- **Seven things upstream does not do, because reading it at the pin said so.** It clamps
  nothing and never reads the joint limits that exist, on motors in multi-turn mode where the
  firmware limits are off too. A dropped packet returns an all-zeros reading indistinguishable
  from every joint at zero, which fed to a position controller commands a full-scale move to
  zero. A controller fault arrives as a bare `KeyError`. There is no reset anywhere, no
  watchdog, no timeout and no e-stop. So the daemon carries a clamp, a rate limit, an
  all-zeros detector, a fault guard, a safe-pose slew, signal handlers and a construction
  watchdog, and every one of them is exercised in CI against a fake body over a real socket.
- **A shutdown that does not drop the robot.** Upstream's does: a C level `atexit` handler
  disconnects every client and disconnecting disables torque, so any unhandled exception or
  plain Ctrl-C de-torques a standing humanoid with no lowering and no ramp. `SIGTERM` does not
  even reach that handler, and upstream installs no Python one, so systemd stopping it leaves
  the robot fully torqued holding its last target instead. quackd's daemon settles to a safe
  pose first, then closes under a hard deadline, because `close()` holds the GIL and retries
  forever on a dead bus.
- **A deadman that is a trajectory rather than a message.** On a duck, silence is safe and
  zero velocity is a stop. Here the command is an absolute pose, so holding the last target,
  jumping to a new one and going limp are the only three options and none of them is safe. The
  daemon slews to upstream's own default pose at upstream's own rate, waist first as its own
  reset does, and holds.
- **`toddlerbot-lookout`**, the task to point at a real humanoid first, on its safety stand:
  nothing in its allowlist moves a leg, an arm or the waist.
- **[docs/toddlerbot-hardware-checklist.md](docs/adapters/toddlerbot/hardware-checklist.md), the order
  to try a real ToddlerBot in.** Fourteen steps on the safety stand upstream ships, feet off
  the ground until step 13. Step 2 is `calibrate_zero`, because the daemon refuses to actuate
  without a `motors.yml`. It runs the daemon with `--fake --once` and `quackd doctor` against
  port 9873 before a motor is powered, points `toddlerbot-lookout` at the robot at step 8, and
  holds steps 11 and 12 (pull the network cable mid-move, then send `SIGTERM`) as the two that
  matter, because on this body silence means hold forever and the daemon is the only thing that
  makes it mean anything else. It ends with the four things only a real robot can answer, the
  safe-pose slew from a crawl or a prone start first. There is no issue template for it, so
  open a plain issue with the transcript.
- **`QUACKD_TODDLERBOT_TOKEN` and `TODDLERBOT_ROOT`**, the two environment variables the
  ToddlerBot's adapter and daemon read. The token is the ToddlerBot's own and not
  `QUACKD_DUCK_TOKEN`, and `--token`'s help says so now: `quackd/adapters/toddlerbot/bridge.py`
  reads it when `--token` is absent and sends it in `bot.hello`, and
  `bridge/toddlerbot/quackd_toddlerbot_bridge.py` reads the same name as the default for its
  own `--token`, compares it with `hmac.compare_digest`, and refuses every other method until a
  hello carries it. A daemon started with no token accepts everyone, and nothing writes one for
  you, because this daemon has no installer. `TODDLERBOT_ROOT` is the daemon's alone: it is the
  default for `--toddlerbot` (otherwise `.`), the upstream checkout the daemon changes
  directory into and imports from, because every path upstream's `Robot` builds is relative,
  and `--fake` never opens it. The nightly contract job sets it to its own sparse checkout of
  upstream.
- **Video off a real Microduck** (`--camera-url webrtc://host:8443`, `quackd[microduck-camera]`).
  There is no camera method in `duck-ipc-proto`, no snapshot route in `mediad` and no camera
  subcommand in `robotctl`, so a picture means being a WebRTC peer. It runs on your machine and
  writes nothing to the robot — the alternative needs `mediad` stopped, because its `v4l2src`
  holds `/dev/video0`. Signalling is read off `mediad`'s own web client at the pin and tested
  through a fake socket; the H.264 and the ICE are not tested and have never met a duck.
- [docs/microduck-hardware-checklist.md](docs/adapters/microduck/hardware-checklist.md), an issue
  template, and `microduck-lookout` — a bring-up task whose allowlist moves no legs, which
  copes with having no camera and stops to say so when posture reads `unknown`. The checklist
  assumes the duck is borrowed: nothing in it installs anything or needs `sudo`.
- CI runs on Windows. `robotd` speaks over a unix socket, Windows cannot open one, and the test
  covering quackd's `ssh -L` answer only runs there — so it had never run anywhere.
- **Bring-up checklists for the XLeRobot and the AlohaMini**
  ([docs/xlerobot-hardware-checklist.md](docs/adapters/xlerobot/hardware-checklist.md),
  [docs/alohamini-hardware-checklist.md](docs/adapters/alohamini/hardware-checklist.md)). The two
  bodies here you can buy today were the two without one. They are not copies of each other:
  the cart's hazard is that its watchdog stops the wheels and leaves fourteen arm servos
  holding, so it stays on blocks until step 9; the AlohaMini's is the opposite, that its arms
  are limp until quackd's own host turns torque on, which makes upstream's stock host the
  safest place to learn the base and the lift first.
- **[docs/reading-robots.md](docs/adapters/reading-robots.md), the traps by pattern rather than by
  robot.** quackd drives eight bodies and has run on none, so everything it does came from
  reading upstream closely enough to be safe without executing it, and the same shapes kept
  recurring: a number that looks like a different number, a default pose that is not neutral,
  a stop that is not a stop, a partial message that means something else, a capability that is
  only a claim, a name that exists on the wrong class, a line somebody commented out. The next
  robot will not have the AlohaMini's bug; it will have one of the AlohaMini's shape.

### Changed

- **The ToddlerBot's manifest is mostly absences, and every one of them is upstream's.** There
  is no text to speech at this pin, so `say` does not exist. There is no get-up policy, so
  `stand_up` is not declared and a fall ends the run with a message that names no verb and
  asks for a human. Nothing reports a battery to Python, so a battery abort cannot fire. And
  the walk policy is an ONNX checkpoint from a wandb artifact that upstream neither publishes
  nor checks in, so on a bare install **there is no locomotion at all**: `move`, `go_to` and
  `approach_and` are not gated off, they do not exist, and `mobility` reads `none` until the
  daemon reports a checkpoint staged.
- **No raw joint verb on the humanoid.** Thirty unclamped radians from a language model, on a
  machine in multi-turn mode with no current limit and no position limit, is the exact failure
  this project exists to prevent. quackd offers named moves and the daemon owns every
  trajectory.
- **The XLeRobot's manifest says no to more than it says yes to, and each no is upstream's.**
  There is no speaker and no microphone in the bill of materials, so the `sound` intent is not
  declared and `say` does not exist here. Which head motor is yaw is stated nowhere upstream,
  so quackd never commands the head and `search_scan` turns the whole cart. The power station
  has no data link, so `battery_percent` is permanently `None`. There is no odometry, so
  `go_to` closes the loop on the camera alone. And a stock cart ships with every camera
  commented out of its config, so `observe`, `go_to`, `search_scan` and `approach_and` exist
  only once a camera has actually been seen on the wire.
- Arm positions on **both** new robots are **normalised −100..100, not degrees**, because their
  `use_degrees` defaults to False. That is a different contract from the SO-101 arm next door,
  which sets degrees: the same number means a different angle, so `move_joints` validates
  against `joint_norm` and never against `lerobot`'s `joint_deg`. Turn rate is converted on both
  too, because each wire is deg/s while quackd is rad/s, and a pass-through would be a 57× error
  on a robot heavy enough to hurt someone.
- `pyzmq` joins the `dev` extra, and is the extra for both new robots. CI installs only
  `dev`, and the fake-host tests are what the two `zmq` backends' 🧪 rest on, so a status claim
  CI could not check would not have been honest.
- **`quackd run` asks "Are you watching the robot right now?" once before a fall-blind robot
  walks.** The question comes at the start of the run, before any verb, when the task allows
  `move`, `go_to`, `search_scan` or `approach_and` on a robot that reports `fall_detection`
  false and has no `stand_up`, which today is `open_duck:bridge` alone, because the Microduck
  can get up and no other body reports the flag. The default answer is no, and no aborts the
  run with exit 1 before a leg moves, so a script that started the bridge walking without
  `--yes` in 0.6.0 now stops at the question until you add `--yes`. Over MCP nothing asks: the
  pilot sees `fall-blind=nothing-detects-falls` in `report_state` and every observation,
  `quackd doctor` prints the warning, and that is all it gets. This is the guard that stands in
  for the ones "It claimed guards it did not have" above says were never there
  ([docs/open-duck-hardware-checklist.md](docs/adapters/open_duck/hardware-checklist.md)).
- **`quackd serve-mcp` with no `.duck` loaded now runs on a default budget of 40 verb steps and
  five minutes, counted from when the server started.** In 0.6.0 that session had no `Budget`
  at all, so `quackd serve-mcp --robot open_duck:bridge` would have handed an MCP client
  unlimited and uncounted control of a physical biped. The numbers are the frontmatter
  defaults, `max_steps: 40` and `max_minutes: 5`, and every `robot_run_verb` and
  `robot_observe` counts, `stop` included, so from the 41st call or once five minutes have
  passed every verb answers `budget exhausted` until `robot_load_duckfile` starts the
  contract's own budget. The clock is wall time on `open_duck:bridge` and `microduck:jsonrpc`
  and the simulator's own clock on `sim2d`, and there is no flag to turn the budget off
  ([docs/mcp.md](docs/guides/mcp.md)).
- **One Ctrl-C stops the robot, and a second one quits.** The first Ctrl-C, or `q`, cancels the
  verb that is running and sends `stop`, the fix described under "The operator's stop did not
  stop the duck" above. New here is the second: the kill switch hands `SIGINT` back before it
  fires, so another Ctrl-C is a plain `KeyboardInterrupt`, where 0.6.0 swallowed every Ctrl-C
  after the first for the rest of the run and left you holding a key on a robot you could not
  be sure had heard. The `kill switch: Ctrl-C` line now prints to stderr on every `quackd run`
  rather than only with `--verbose`, and the banner says `Ctrl-C or q stops the duck. Press it
  twice to quit at once.` A flock run keeps its old banner, and its kill-switch line is still
  `--verbose` only.
- **The bridge refuses to start when `--max-vx`, `--max-vy` or `--max-vyaw` is above upstream's
  own clamp, or `--head-safety` is outside (0, 1].** The three velocity flags, documented in
  [bridge/open_duck/README.md](bridge/open_duck/README.md), were never checked, so the number
  in the unit went straight into the clamp the bridge applies and into the limits it advertises
  in the hello, and a unit asking for more than the 0.15, 0.2 and 1.0 that upstream's own
  gamepad allows would have been honoured. Narrowing them is the obvious first power-on
  precaution, and widening them past the pad is not something quackd should do quietly on your
  behalf, so the bridge exits with the bound in the message rather than silently clamping.
  `--head-safety` is the fraction of upstream's head range quackd will use, not a multiplier on
  it, so 0 and anything above 1 are refused the same way.
- **The bridge's systemd unit has no start rate limit any more (`StartLimitIntervalSec=0`).**
  It was 3 starts in 120 s, and with `Restart=no` the only thing a burst limit could throttle
  is you editing a path and running `systemctl start` again, which is exactly what bring-up is.
  The fourth attempt failed with an error about none of the problems being debugged and needed
  `systemctl reset-failed`, which appeared nowhere in the docs.
- **camd captures at 5 fps and 256 px by default, from 1 fps and 512.** `go_to` and
  `search_scan` close a visual loop at 10 Hz, and holding one steering correction for a whole
  second gives a per-frame loop gain above 1, a weave that swings the target back out of frame.
  The smaller frame pays for the faster rate on a Pi Zero 2 W, and `quackd-duck-camd.service`
  now passes `--fps 5 --size 256` explicitly. A 0.6.0 unit left in place still passes `--fps 1`
  and nothing for `--size`, so it keeps the slow loop at the new size until you re-run
  `install.sh`.
- **mypy type-checks `bridge/` now, the code that actually runs on the robot.** `[tool.mypy]`
  had `packages = ["quackd"]`, so the daemons on the robot's side were the only Python in the
  repository nothing type-checked. It is `files = ["quackd", "bridge"]` now, because mypy takes
  packages or files and not both, and CI's bare `uv run mypy` enforces it. The two Open Duck
  files were clean when the scope widened, and the gate earned its keep at the merge that
  brought the three new robots in: the ToddlerBot daemon's `int(getattr(robot, "nu", ...))` was
  typed `Any | None` because `robot` is optional there, and it now falls back to the frame's
  own width instead of crashing on a body nobody passed.
- **Nine of ten upstream unknowns were closed by reading, not guessing** — the import form the
  shim depends on, that `get_last_command` runs at 50 Hz and before the pause check, that `B`
  is the sound button, that the antennas are trigger-driven and unclamped, that the head slots
  are written unconditionally with no mode button, that the walk loop opens no camera, and that
  the IMU in use is `raw_imu.Imu` (a dict of gyro and accelero) rather than `imu.Imu` (a
  quaternion). The four head floats turn out to be **offsets** added to the walk policy's own
  head targets, not absolute joint angles. Fall detection was deliberately left unimplemented:
  which axis reads gravity depends on an axis remap, a config flag and a tare offset, and a
  fall detector that is wrong fails as a confident "not fallen".

### Fixed

- **The ToddlerBot's `search_scan` sweeps its head rather than turning its body.** `scan_mode`
  turns any robot with mobility and the twist intent, which is right for a duck and wrong for
  a humanoid with no get-up policy: with a walk checkpoint staged the shared verb would have
  pirouetted 3 kg of fall-prone robot to look for a ball. quackd supplies its own for this
  body, and it waits for the neck to arrive before taking the frame, because the daemon rate
  limits every joint and the shared sweep looks after a tenth of a second.
- **The transport keeps the link alive, so a long verb is not cancelled by its own deadman.**
  The daemon's deadman fires after half a second of silence and `stand` takes three, and the
  executor sends one command and then only polls while it waits. quackd's own `Heartbeat`
  cannot be that signal, because its period is a run setting rather than the manifest's and
  defaults to the same half second. The ToddlerBot transport now sends `bot.keepalive` on its
  own timer, and reading state or a frame deliberately does not count as being alive.
- **Every ToddlerBot disconnect used to stall for the full request timeout.** `close()`
  cancelled the read loop and then asked for a final stop, which waits on a future only that
  read loop could resolve.
- **A walk checkpoint that cannot move the robot is no longer offered as locomotion.** An
  envelope of zero on every axis clamps every velocity to nothing, so `move`, `go_to` and
  `approach_and` would have accepted every command and moved nothing.
- **The mocks and the simulators stopped reporting a pose their robots do not have.** Neither
  ZeroMQ wire carries a position, so the real backends report None and the offline doubles
  were dead-reckoning one. A double that is easier than the robot is a task that passes here
  and fails there.
- **The ToddlerBot daemon stopped claiming four things it could not do.** An audit of the
  three new adapters against their own plan found the same shape of bug three times, and it
  is the shape this project exists to prevent: a capability flag the operator sets, a
  manifest that promises verbs because of it, and a daemon with no implementation behind it.
  `--camera` declared `observe`, `search_scan`, `go_to` and `approach_and` while the frame
  handler read an attribute that was never defined, so every frame came back empty and
  `toddlerbot-lookout`, the one task shipped for the first hardware day, could not have run.
  `--walk` declared locomotion and returned `accepted: True` for every command while the
  policy attribute it consulted was never assigned, so the robot would have stood still and
  reported success. And `perform` refused every motion because the keyframe library was
  initialised empty and never filled. A later pass found a fourth of the same shape: `grip`
  answered accepted and commanded nothing at all, on a capability read from the command line
  rather than from the body. All four are now real, and **the handshake reports what actually
  loaded rather than what was asked for**: a camera that will not open means the camera verbs
  never appear, a walk checkpoint is a file you supply rather than a claim you make, and the
  gripper capability follows the motors.
- **`policy.step_target()` never existed upstream.** It was quackd's invention, which is the
  exact failure ADR-0022 is written to prevent. The real interface takes the whole
  observation and the sim and answers with a pair, and it is now cited at a pinned line
  along with sixteen other names the daemon needed and did not have: that motions carry a
  per-variant suffix, that upstream ships no loader at all, that `cartwheel` cannot be
  replayed because its file holds no action array, that `walk_zmp` is a lookup table rather
  than a motion, and that `Camera.get_jpeg` hands RGB to an encoder that wants BGR and so
  returns a picture with red and blue swapped.
- **The walk envelope comes from the checkpoint now.** `command_range` is read at connect off
  the policy that is actually loaded, rather than hardcoded from a gin file, so a robot whose
  gait was trained tighter than quackd's caps gets the tighter number. It can only ever
  narrow: a checkpoint trained wider does not get to widen `limits`.
- **A daemon fault no longer looks like a healthy robot.** A raising tick used to kill the
  control thread while the socket went on answering `ok`. It is now caught: the deadman is
  forced, `bot.health` reports the fault so quackd's heartbeat trips, and a bus that never
  comes back settles and stops rather than failing fifty times a second forever. The
  `sys.excepthook` and `threading.excepthook` that Part C of the plan asked for are installed
  too, because upstream's C level `atexit` disables torque on any interpreter exit and would
  otherwise drop a standing robot before Python got a say.
- **The ToddlerBot daemon moved off port 9872 to 9873.** The Open Duck Mini already had it:
  9871 for its bridge and 9872 for its camera daemon, and `SECURITY.md` tells people to
  tunnel that pair. The comment justifying the old choice named only the bridge. Its token is
  also compared with `hmac.compare_digest` now, as the Open Duck's always was, rather than a
  plain `!=` that returns as soon as two bytes differ.
- **Two simulator rows earned the tick they were claiming.** `alohamini:sim2d` and
  `toddlerbot:sim2d` were marked ✅ while their own text admitted no seeded sweep, on a page
  where ✅ means exactly that. Both now run their lookout task on ten of ten seeds in CI, with
  the ground truth checked: the AlohaMini never moves a wheel, an arm or the lift, and the
  ToddlerBot never takes a step or turns its body. The `alohamini:zmq` row was also sitting
  under the ToddlerBot's heading rather than its own robot's.
- **`quackd[alohamini]` was never locked.** The extra shipped in `pyproject.toml` and never
  reached `uv.lock`, so `uv sync --locked` refused it. `NOTICE` also credited every other
  upstream and none of the three new ones, including the fact that ToddlerBot's design files
  are non-commercially licensed and quackd therefore distributes none of them.
- **The five-to-eight sweep reached the places the tests do not police.** `test_docs.py`
  checks three exact phrases, so everything phrased differently had gone stale: `SECURITY.md`
  counted five bodies and scoped on-robot code to the Open Duck's two daemons, `docs/safety.md`
  had five rows in the table that answers "what stops this body when quackd goes quiet",
  `docs/faq.md` listed five adapters, and the README said five in four more places while
  saying eight in a fifth. The architecture pages also still said quackd hosts a control loop
  on one body when it now does on two, and in very different ways.
- **The duck could not be started at all.** The shipped `ExecStart` exits 2 before binding a
  socket, because `--script-arg --onnx_model_path` makes argparse refuse a value beginning with
  a dash — and it was the only `serve --script` invocation shipped anywhere, so it was the one
  an owner would copy. `WorkingDirectory` was one level above the data: upstream opens
  `./polynomial_coefficients.pkl` and `../mini_bdx_runtime/assets/` relative to the working
  directory, and the first is read inside `RLWalk.__init__` *after* the servo bus is powered,
  so the wrong directory is a traceback over fourteen energised joints. A pre-flight now
  refuses before the socket and before the servos. And no configuration produced both frames
  and the verbs that use them: the camera capability came from `expression_features.camera`,
  but that flag says who owns the *device*, and camd refused to start while it was true — so a
  correctly configured duck reported no camera and dropped `observe`, `go_to`, `search_scan`
  and `approach_and`, making both starter tasks and three checklist steps unreachable.
- **The operator's stop did not stop the duck.** An abort never reached the verb that was
  moving: `asyncio.wait_for` watches only the clock, so a kill switch, a Ctrl-C or a failed
  heartbeat set a flag nothing read until the verb returned on its own, while the verb's own 10
  Hz resend kept feeding the daemon's deadman. `stop` was then refused by the very gate that
  made it necessary, in both the executor and the MCP session. `duck.stop` lasted one tick and
  was undone by the next command. Nothing settled the duck on shutdown — the `finally` that
  looked like it did runs after the loop has exited, so its zeros had no reader — and
  `systemctl stop` killed the interpreter between two 20 ms ticks with the servos holding their
  last goal. Any client's disconnect zeroed a walking duck, including the `doctor` the
  checklist tells you to run from a second terminal. And `stop` reported a success it could not
  know, because the guard reads `stop_error` off the adapter and no adapter forwarded it.
- **It claimed guards it did not have.** The task file told the pilot every moving verb would
  refuse if it fell; nothing detects a fall on this backend, so none ever did, and an accepted
  `move` read to the model as evidence it was upright. A duck with `start_paused` could never
  walk and was misdiagnosed as a starved Pi. A wedged control loop reported itself healthy at
  50 Hz forever. A failed state read was dressed up as a healthy duck, with a fabricated pose
  for a robot that has no odometry. And the token was unreadable by the service user, so the
  bridge ran with authentication silently off while four places in the docs promised otherwise.
- **The camera was not safe to steer on.** camd served a frozen frame forever once capture
  stopped; its 1 fps default gives a divergent per-frame loop gain against a 10 Hz visual loop;
  `doctor` could not probe it at all; `go_to`'s command cadence was frame-fetch-bound against a
  300 ms deadman; and every distance was wrong, because the detector assumes the simulator's
  90° field of view where a Pi Camera Module 2 is about 62 — 0.23 m against 0.38 m for the same
  ball, enough for `go_to` to announce arrival outside the task's own success criterion.
  `--no-swap-rb`, offered in the README as the cure for wrong colours, inverts a correct image
  and relabels an orange ball as a person.
- **`express` on an Open Duck was a guaranteed no-op, and `droop` moved nothing even when it
  played.** `duck.antennas` does not refresh the command timestamp and every verb is its own
  tool call with an LLM turn between, so the command was always stale by the time a gesture
  arrived and the deadman rested the antennas before it played, while `express` reported
  success. Two 9 g servos on a GPIO pin are not motion, so a gesture now plays through a stale
  command for `GESTURE_S` (1.0 s) and then rests on its own, and `duck.stop` still rests it at
  once. `droop` sent 0.0, which upstream maps to the antennas' exact resting position, so it
  now sends `DROOP_POSITION`, -0.6 on upstream's -1..1 scale, a value no gamepad trigger can
  produce and a place nobody has watched these two servos go on a real duck, which is why it
  stops short of -1.
- **A second connection could drive the duck with no handshake at all.** The bridge kept its
  `greeted` flag on the core rather than on the connection, so on a bridge with no token, which
  the token bug above made every duck 0.6.0's `install.sh` set up, once any client had said
  `duck.hello` another socket could send `duck.command` straight away and skip the protocol and
  version checks on a socket that walks a robot. `authed` is per connection now, and anything
  but `duck.hello` on a fresh connection is refused with `say duck.hello first` whether or not
  a token is set.
- **camd served its last frame forever once capture stopped.** The handler read the capture
  timestamp and threw it away, so after one good frame the only 503 was unreachable: a ribbon
  working loose on a walking duck left `/snapshot.jpg` answering 200 with the same JPEG,
  `observe` reporting a confident detection and `go_to` steering on a photograph. Every
  snapshot now carries an `X-Frame-Age` header, and one older than four capture periods, never
  less than 1.5 s, answers 503 with the reason in the body (`/healthz` reports the threshold as
  `stale_after_s`). On the laptop `get_frame` reads the header, returns no frame instead of
  raising and keeps the reason where `doctor` can see it, treats a stamped frame over 2 s old
  as stale whatever the server's own threshold was (a server that does not stamp its frames is
  not checked), and gives up on a fetch after 1 s rather than 3, because `go_to` sends its next
  command only when the fetch returns and the deadman is 300 ms.
- **camd refused to start when `duck_config.json` had `expression_features.camera` true.** That
  flag says who owns the device, and camd exited 2 to avoid fighting the runtime for it, while
  the 0.6.0 bridge took its camera capability from the same flag, so the one setting under
  which the bridge advertised a camera was the one under which nothing served frames. Reading
  upstream at the pin, the walk loop the bridge runs opens no camera at all, so the collision
  could not happen in that process. camd now logs a warning and starts, and if you do run one
  of upstream's own camera scripts alongside it the contention shows up as failing captures on
  `/healthz` and expiring snapshots rather than a frozen frame, though setting the flag false
  is still the tidier setup.
- **Every distance on a real camera was wrong, and nothing let you correct it.** The detector
  assumed the simulator's 90 degree field of view where a Pi Camera Module 2 is about 62, so on
  hardware distances came out about 40 percent short, and [docs/faq.md](docs/faq.md) told you
  to set `fov_deg=62` in code. `quackd run` now takes `--fov-deg`, a manifest can carry
  `camera_fov_deg` under `limits` ([docs/manifest-spec.md](docs/reference/manifest-spec.md)), and the
  flag wins over the key. Until one of them is set, every detection on a real backend ends in
  `(uncalibrated: distance is a rough guess)` and a warning names the flag, while `sim2d` and
  `mock` stay calibrated because their camera is the one the geometry assumes.
- **The API version quackd was written against had moved on.** `upstream_api.py` was the one
  adapter ADR-0022 let cite `main` instead of a commit hash, and in the week after it was
  read upstream went from `API_VERSION` 16 to 23. The handshake refuses on mismatch rather
  than guessing — correct behaviour, and it meant the first real Microduck anyone connected
  to would have closed the socket before a single intent was sent. The file now carries `PIN`
  and `READ_ON` like the other four, every ref links to a line at that hash, and a test keeps
  `/blob/main/` out. Re-reading all of them found the rest of the drift is small and additive:
  `Skill` is a free string now (a robot's real list arrives in `robot.subscribe`'s answer),
  and `RobotState.theremin`, `RobotState.chorale` and `HealthResult.cpu_temp_c` are new.
- **`robot.subscribe` was never sent, so nothing was ever known about the robot.** It lived
  in the `subscribe()` generator, which nothing in the CLI, the agent loop, the MCP server or
  the executor iterates, and upstream does not push state until asked. Every fact derived from
  the state frame was therefore empty for the life of a session — and empty read as safe:
  `fallen` was `False` because nobody was looking, so the precondition layer `docs/safety.md`
  advertises was inert on the one backend where a fall is a real robot on a real floor.
  `connect()` now subscribes, frames carry an arrival time and stop being believed when they
  stop arriving, and a duck nobody is watching reads as `unknown` rather than as standing.
- **`sit` and `stand` could do the opposite of what they said.** Both send upstream's single
  `sit_toggle`, and posture was the only thing telling them apart, so with posture permanently
  unknown `stand` would sit a standing duck down and report success. They refuse rather than
  fire a toggle nobody can aim. `stand_up` no longer reports "upright" when nothing reports
  falls.
- **One dropped camera frame ended the run.** `get_frame` raised beneath a comment promising
  it would not, and `AgentLoop._observe` calls it every step and catches nothing. Frames are
  now pulled on a timer and served from memory, `get_frame` never raises, and `camera_health()`
  carries the failure.
- **`doctor` accepted `--camera-url` and never fetched a frame**, so a wrong snapshot URL
  passed preflight and failed at the first `observe`. It now fetches one and prints its size.
- **The manifest claimed a camera on every backend.** Upstream serves no frames over
  `robotd`'s socket, so on a real duck `--camera-url` is the whole camera; `observe`, `go_to`,
  `search_scan` and `approach_and` were advertised while only able to answer "this transport
  has no camera". They are absent unless something is serving frames.
- **The heartbeat logged a stop it had not delivered.** `stop()` swallowed every error. It
  still never raises — it is called from the paths that run because the socket died — but it
  records why it failed and says so, and the heartbeat no longer asserts the outcome.
- An unknown sound tag became a chirp silently; JSON-RPC errors lost their code, so `BUSY` and
  `PERMISSION_DENIED` were the same thing to a caller.
- **Loading a second `.duck` over MCP refunded the budget.** `robot_load_duckfile` is a tool
  the model itself holds, and adopting a contract built a fresh `Budget` and cleared the
  consecutive-failure tallies, so a pilot that had spent its steps or been refused a verb
  could load a wider duck and start counting from zero. The limits still become the new
  contract's; the steps, the llm calls, the clock and the failure counts now stay the
  session's, and the tool's reply says what carried over.
- **`quackd doctor` and `quackd validate` crashed on a Windows pipe.** Python uses the ANSI
  codepage when stdout is not a console there, and quackd prints ✓, 🦆 and the status emoji in
  `doctor`, so on cp1252 the first two commands
  [docs/microduck-hardware-checklist.md](docs/adapters/microduck/hardware-checklist.md) puts in front of
  a Windows user died with `UnicodeEncodeError`, and so did the last step of CI's own Windows
  job. Both streams now replace what the codepage cannot carry, because a lost glyph costs a
  character and the raise cost the command.

### Known limitations

- **Nothing has run on hardware, on any of the eight adapters.** `microduck:jsonrpc`,
  `open_duck:bridge`, `reachy_mini:sdk`, `lerobot:real`, `rosbridge:ws`, `xlerobot:zmq`,
  `alohamini:zmq` and `toddlerbot:bridge` spell every upstream name from upstream source at a
  pinned commit and have only ever talked to fakes, the daemons quackd ships have only ever run
  with `--fake` over loopback or, for the ToddlerBot's, against upstream's MuJoCo body in a
  nightly job, and `microduck:websocket` is still a stub. If you have a body, the first thing
  to point at it is the task that moves nothing: `open-duck-lookout` with the feet off the
  ground until step 10 of
  [docs/open-duck-hardware-checklist.md](docs/adapters/open_duck/hardware-checklist.md),
  `microduck-lookout` on a duck you are probably borrowing with someone holding the gamepad
  ([docs/microduck-hardware-checklist.md](docs/adapters/microduck/hardware-checklist.md), feet off until
  step 9), `xlerobot-lookout` with the wheels on blocks until step 9, `alohamini-lookout` on
  upstream's stock host with the arms limp, before quackd's wrapper ever turns torque on, and
  `toddlerbot-lookout` on the safety stand with the feet off until step 13. The Reachy Mini,
  the LeRobot arm and the rosbridge base have no lookout task and no checklist, and the base
  has no verified deadman, so it goes on blocks first.
- **An Open Duck set up by 0.6.0 needs `bridge/open_duck/install.sh` run again on the robot
  before the new bridge will start.** 0.6.0's installer wrote the token `0600 root:root` inside
  a `0700` directory while the unit ran as `pi`, and the new daemon refuses to start on a token
  file it cannot read rather than run with authentication silently off, which is what the old
  one did. Nothing is lost by re-running it: the 0.6.0 unit could never start the bridge
  anyway, because argparse exits 2 on `--script-arg --onnx_model_path` before a socket is
  bound. The new `install.sh` keeps an existing token, makes it group readable by the service
  user and checks that it is, so the `--token` or `QUACKD_DUCK_TOKEN` your laptop already has
  stays valid.
- **Update the laptop and the robot together, because the handshake cannot tell 0.6.0 from
  0.7.0.** `PROTOCOL_VERSION` is still 1 on both ends, so a 0.6.0 laptop pairs with an updated
  bridge without a word and then sends every `duck.command` without the `epoch` the bridge now
  uses to tell a deliberate command from one that was already in flight when a stop landed. A
  client that sends none is held to the blunt window instead: for `--deadman-ms` (300 by
  default) after every stop its commands are silently dropped, and since `_turn` ends every
  `search_scan` step with a stop, the opening commands of the next step (up to three at 10 Hz)
  are swallowed and the duck under-rotates the scan. The other way round is quieter and no
  better: a 0.7.0 laptop drives a 0.6.0 bridge, which ignores `epoch`, but that bridge has none
  of the fixes above.
- **`--fov-deg` is a `run` option only, and no shipped manifest sets `camera_fov_deg`.**
  `record` and `doctor` do not take the flag and `serve-mcp` reads only the manifest key, so an
  MCP session on hardware carries the uncalibrated label with no way to clear it short of
  editing the adapter's manifest in source. The label is text the pilot reads, not a gate:
  nothing in the verbs checks `calibrated`, so an uncalibrated `go_to` still drives on the
  number.
- **`quackd[microduck-camera]` is a heavy extra, and the default install is unchanged.** It is
  `aiortc`, `av` and `websockets`, and `aiortc` brings `aioice`, `pylibsrtp`, `pyee`,
  `google-crc32c`, `cryptography` and `pyopenssl` with it, while `av` carries FFmpeg in a wheel
  of 18 to 39 MB depending on the platform. Nothing imports any of it unless `--camera-url`
  starts with `webrtc://`, and a missing extra is one error naming the `pip install` and the
  HTTP snapshot alternative that needs none. Nobody who is not pointing quackd at a real
  Microduck needs it, and the H.264 and the ICE inside it have never met a duck.

## [0.6.0] — 2026-09-04

A run stops starting from nothing. Every release so far built a pilot with no past: the
transcript recorded each prompt, tool call and frame, and the next run never read a line of
it, so a duck that had found the ball behind the sofa three times searched the whole room a
fourth time. Now each robot keeps a small file of notes the pilot chose to save and one
line per earlier run, and the newest of both are in the prompt before the first observation.
This is also the first release built on other people's pull requests: memory (#5) and the
`max_minutes` fix (#3) are both theirs. Design: `docs/adr/0025-memory-between-runs.md`,
with the release's own notes in [docs/design/memory.md](docs/design/memory.md).

Still nothing has run on a robot of any kind. What memory adds is tested end to end offline,
and no cloud model has ever written a note.

### Added

- **Memory between runs** (`quackd/memory.py`, [docs/memory.md](docs/guides/memory.md)). Every
  run used to start from nothing. Now each robot, keyed `adapter:backend`, has a JSONL
  file under `~/.quackd/memory/` that holds the notes the pilot saved with the new
  `remember` tool and an episode line quackd writes at the end of every non-dry run
  (outcome, reason, the last few verb results). The newest of both are rendered into the
  system prompt at the next run. `remember` costs an LLM call but no step, and a repeated
  sentence refreshes the old note. `quackd run --no-memory` / `--memory-dir`,
  `quackd memory show|add|clear`, and over MCP `robot_recall` / `robot_remember` (eight
  tools now). A simulated body never inherits a real one's notes. Thanks to
  [@Bayway](https://github.com/Bayway) (#5), whose design and implementation this is.
- **The solo starter ducks ask for a note.** `find-and-kick`, `fetch`, `follow-me`,
  `patrol-and-quack`, `open-duck-scout`, `open-duck-lookout` and `reachy-spotter` carry
  `remember` in their last strategy step and a *Memory* section saying what is worth
  keeping, and `--goal` runs get the same line. A prompt-level hint alone was ignored by a
  14B local model; the strategy step is followed. The v0 duck goldens were regenerated
  for this. `hello-world` and the flock ducks are unchanged.
- **A test that the PyPI project page's links resolve** (`tests/test_pypi_readme.py`),
  because the rewrite below is invisible when it breaks: nothing in a normal run reads the
  built metadata, and the only symptom is dead links on a page the maintainer rarely opens.

### Changed

- CI runs on `actions/checkout@v7` and `astral-sh/setup-uv@v7`. `checkout@v4` targets
  Node 20, which GitHub deprecated and now force-runs on Node 24. Thanks to Dependabot
  (#1, #2).

### Fixed

- **`max_minutes` could be beaten by a slow provider.** The time budget was checked in
  `note_llm_call()`, *before* `provider.step()`, and nothing looked at the clock again
  before the answer was processed, so a provider that replied after the deadline still had
  its `declare_success` honoured and the run recorded a success it had no right to. The
  loop now re-checks the clock the moment a turn comes back, and a late declaration ends
  the run as `budget`. Thanks to [@r0jin](https://github.com/r0jin) (#3).
- **Every relative link in the README 404'd on the PyPI project page.** `README.md` is the
  long description, and its 60 relative links are correct on GitHub, where a relative link
  follows the branch you are reading, and meaningless anywhere else. A hatchling metadata
  hook (`hatch_build.py`) absolutises them at build time, in both syntaxes this README
  uses: 56 Markdown links and four raw `<a href>` in the centred HTML blocks. The first cut
  of the hook handled only Markdown, so the test now checks both and would have caught it.
  The repository's own README is unchanged, images were already absolute, and in-page
  anchors are left alone.
- **A hand-edited memory file could take the whole command down.** The file is documented
  as one you may edit, and a line that is not JSON is skipped. A line that *was* JSON but
  not an object (`"a note"`, `17`, `[]`) raised `AttributeError` out of every reader,
  because only `JSONDecodeError` was caught. Now every such line is skipped, as promised.
- **`--dry-run` wrote permanent notes.** The episode at the end of a run was guarded and
  the `remember` tool was not, so a run that sent nothing to the robot could still leave a
  permanent conclusion drawn from verb results the dry run itself invented. It now refuses
  and says so, like every other intent a dry run declines to send.
- **A refreshed note sorted as the oldest.** Repeating a sentence refreshes its timestamp
  and used to leave it where it was in the file, and both the prompt's "newest notes" window
  and the 400-entry cap read file order. So the note a model had just repeated was the first
  one dropped. A refresh now moves the note to the newest position.
- **`quackd memory show --raw` did not print the file as is.** It went through Rich, which
  ate anything in square brackets: a note reading `the ball is [bold]behind[/bold] the sofa`
  printed as `the ball is behind the sofa`. A note is text a model wrote, so it can contain
  anything.
- Tests set `QUACKD_MEMORY_DIR` to a temporary directory, so a test run no longer writes
  the developer's own `~/.quackd/memory`.
- `quackd memory show|add|clear --robot <bad spec>` printed a Python traceback. Every other
  command taking `--robot` answers in one line, and now these do too.
- With `--no-memory` the MCP server still told the pilot to call `robot_recall` early, a
  tool that would answer "memory is off". Those two sentences are now omitted.
- The prompt told the model `remember` "is free". It costs an LLM call, which is a budget it
  shares with every other turn, and the ADR and docs both said so.
- **Claims that had gone stale before this release, found while auditing it.**
  `docs/architecture.md` still described the `duck_*` MCP aliases 0.5 deleted, and had not
  been updated for memory at all (no `memory` command, no `remember` tool, no `memory`
  transcript event). The README's architecture diagram listed four adapters and omitted
  `open_duck` and its `bridge` backend, which the adapter-count guard could not see because
  it reads the phrase "N adapters" and not a list. The README, `docs/architecture.md` and
  the FAQ each said one quackd daemon runs on a robot, while `bridge/open_duck/` has shipped
  two since 0.5, and `docs/architecture.md` contradicted itself about it. `docs/duck-spec.md`
  called `max_minutes` a cap without saying a verb already running is not interrupted.
  `_deprecated()` in `quackd/cli.py` was dead from the 0.5 `--transport` removal. `quackd
  --help` still introduced the tool as a way to "pilot a Microduck", five adapters later.
  `_pick_default` justified its rule with the `duck_*` aliases. `tests/test_goldens.py`
  dated its goldens to 0.3.0 after four of the six duck hashes were regenerated here, and
  ADR-0025 filed learned verbs under ADR-0019, which is the `.duck` spec v1.
- **The README said no live local server had been run.** One now has, by this release's
  contributor, which is the first local-model evidence this project has had. The sentence
  says whose machine it was and that no transcript from it is in the repository.
- `test_cap_drops_old_episodes_before_notes` wrote 405 entries one at a time, each one
  re-reading and rewriting the whole file. It took 14.6 s here, on a matrix that runs it
  four times, to prove a property a cap of 12 proves in 0.16 s. It now also asserts *which*
  entries survive, which is what the test was named for.
- Guards for the class of mistake this release's own review kept finding. A living document
  *or a Python string a user reads* may not claim a number of `robot_*` tools the server
  disagrees with, nor still describe the `duck_*` aliases 0.5 deleted: 0.5's guard proved
  nothing still *offers* a removed thing and could not see one still being *described*, and
  the stale count turned out to be in a `--robots` help text and two docstrings as well as
  two README sentences. The key the CLI computes for a robot's memory file must equal the
  key the MCP server computes, for all five adapters and all fourteen backends, because
  "a note saved from Claude Desktop is read by the next `quackd run`" is otherwise an
  intention. And the built wheel's long description must contain no relative links.
- The `quackd memory` command group, and the memory flags on `run`, now have tests. They
  shipped with none, which is how two of the three subcommands were wrong.
- One assertion in the new memory tests was `A or B` where `A` was never true (the sound in
  the highlight is the robot's own tone, not the text), so it checked almost nothing. It is
  one assertion now.

### Known limitations

- **The scripted pilot never calls `remember`**, so `--provider fake` accumulates run
  outcomes and never writes a note. Notes have been exercised by one local model
  (Qwen 2.5 Coder 14B through LM Studio, `find-and-kick`, seeds 5 and 6) on one machine,
  and by no cloud model at all.
- **`--no-memory` does not silence the ducks.** The seven starter tasks above ask for a
  `remember` inside their strategy, and that text is in the prompt whether or not memory is
  on. A pilot that follows it gets a clear refusal that costs an LLM call and no step.
- Memory is a file, not a memory system: newest wins, there is no embedding and no search,
  nothing is shared between bodies, and the executor never reads it. A note is text a model
  wrote.

## [0.5.0] — 2026-09-03

The first robot you can actually build. Every hardware backend before this one targeted a
robot you could not buy or had not assembled: the Microduck ships around Christmas 2026, and
the Reachy, LeRobot and rosbridge backends have only ever talked to fakes. The Open Duck
Mini v2 is open hardware people are printing at home today, so for the first time a stranger
can follow these instructions all the way to a walking robot. That also makes this the first
release where quackd ships code that runs **on** a robot. Design: `docs/adr/0024-open-duck-mini.md`.

Still nothing has run on a duck. What is new is that everything except the duck is tested.

### Added

- **The Open Duck Mini v2 adapter** (`--robot open_duck:sim2d`, `open_duck:mock`,
  `open_duck:bridge`): the
  first robot quackd supports that anyone can build today. It is an open hardware 3D
  printed biped that walks on its own 50 Hz ONNX policy on a Raspberry Pi Zero 2 W. Its
  manifest is a strict subset and says so: this robot has no beak, no gripper, no kick
  policy, no sit policy and no get-up-after-fall policy, so `kick`, `grab`, `sit`, `stand`
  and `stand_up` are never declared and therefore do not exist for it anywhere. A duck
  built without a camera or a speaker loses exactly the verbs that need them. Velocities
  are clamped to the ranges read from the robot's own runtime (0.15 m/s forward, 0.2 m/s
  sideways, 1.0 rad/s turning), and a fallen duck refuses to move with a message saying a
  human must stand it up, because nothing quackd can call will.
- **A bridge daemon that runs on the robot** (`bridge/open_duck/`), which is a first for
  quackd: every other adapter talks to someone else's daemon, and this robot has none. It
  does not reimplement the 50 Hz control loop, it *is* that loop, with the class upstream
  imports to read a gamepad rebound to read a socket instead. One process, so the servo bus
  keeps one owner, and nothing of upstream's is copied. Going limp is unreachable rather
  than forbidden: the only channel from the network to the body is seven floats and a few
  buttons. The deadman is quackd's own, it runs on the robot, and it is evaluated by the
  control loop rather than a timer, so a server thread that is starved, wedged or dead still
  stops the duck. Standard library plus numpy, so it installs on a 512 MB Pi, and
  `--fake` runs the whole protocol on a laptop with no robot at all.
- **A camera server for the duck's Pi** (`bridge/open_duck/quackd_duck_camd.py`), in its
  own process because encoding a JPEG inside a 20 ms control tick is not affordable on a Pi
  Zero 2 W. It captures on a timer so a slow client cannot stall it, serves the newest frame
  over HTTP, and has no control path at all: it reads a camera and answers GET. Without it
  the bridge advertises no camera and the verbs that need one do not exist rather than exist
  and fail. `--fake` paints a duck's eye view, so the whole chain runs with no hardware.
- **Two starter tasks**, `open-duck-scout` (find the ball, walk up, report, 10 of 10 seeds)
  and `open-duck-lookout`, whose allowlist moves no legs at all and which exists to be the
  first thing anyone points at a physical duck.
- ADR-0024, `docs/adapters/open_duck.md`, and a hardware checklist and issue template for
  the first person to run this on a duck they built.
- **`--camera-url`** on `run`, `serve-mcp` and `doctor`, for a robot whose camera is an HTTP
  snapshot rather than something the control socket can serve.
- **`--token` and `QUACKD_DUCK_TOKEN`** for a robot that wants authentication. The Open Duck
  bridge's own installer writes one, so until now a duck set up by the book refused every
  client and no document explained why.
- **`quackd doctor --robot X --address Y` connects.** Everything else in `doctor` is offline
  and reads the static manifest, which describes a fully built robot. This prints what your
  robot actually reported: its capabilities, which verbs it does and does not have, and
  whether its control loop is healthy. It is the only way to see that difference before a
  run does.

### Removed

- **`--transport X`**, the 0.4 alias of `--robot microduck:X`, along with `resolve_robot`'s
  transport branch and the warn-once machinery that existed only to support it. 0.4 said in
  ten places that it would go in 0.5.
- **The eight `duck_*` MCP tools**, 0.3 aliases pinned to the default robot. Omitting the
  `robot` argument to a `robot_*` tool does the same thing. `duck_get_frame` has no exact
  replacement by design: `robot_observe` does the same job but goes through the executor, so
  frames are now budgeted and logged like every other verb.
- Removing them broke the MCP system prompt, which named three of the removed tools and
  hardcoded "a small biped duck robot (25 cm, 800 g)" for every body. It now names the robot
  and takes its description from the manifest's own `blurb`, so a lone Open Duck introduces
  itself as the duck that cannot pick anything up and cannot get back up if it falls.

### Fixed

- **Perception was attached only for the simulator.** Every hardware backend with a camera
  ran blind: it fetched frames, detected nothing because nothing was detecting, and reported
  that it could not see. The detector now follows the camera rather than the backend, which
  also fixes `microduck:jsonrpc` and `rosbridge:ws`. Both entry points ask one function,
  `perception.detector_for`, and both ask it at *connect*: the first cut of this fix keyed
  `quackd run` on the camera and left `serve-mcp` keyed on the backend, so every hardware
  body over MCP still ran blind, and deciding from the static description would still have
  missed a `rosbridge:ws` base, which only reports its camera once it has connected.
- **A robot that reports fewer capabilities at connect than its description claims** used to
  crash the run. `validate` checks the static manifest, which describes a fully built robot,
  so a duck with no camera got past it and then raised a bare `VerbNotFound` when the agent
  loop built its tools. A verb the task *requires* now refuses in the validator's words, and
  one it merely *allows* is dropped with a line in the log, which is what a v1 task allowing
  more than it needs is for.
- `quackd run` now checks a `.duck` against its robot before connecting, the way
  `serve-mcp` always has. Pointing a task at a robot that lacks one of its verbs used to
  reach the agent loop and raise a bare `VerbNotFound` with the robot already connected and
  an empty run directory already written. It now refuses up front with the validator's own
  sentence and writes nothing.
- **Two commands still told users to pass `--transport`**, the flag this release removes: a
  `doctor` table title and the `TransportError` the WebSocket stub raises. The guard that
  swept the documentation for it could not see a Python string, and now reads
  `quackd/**/*.py` too. Six other user-visible strings were still dated "in 0.4", and
  `doctor` printed the extra as `quackd` because rich ate the `[lan]` as markup.
- **The PyPI summary and keywords never mentioned the Open Duck Mini**, the robot this
  release is named for, and no test had ever read `pyproject.toml`. One now does. The
  `Framework :: Robot Framework` classifier is also gone: that is the test-automation tool
  of the same name, not robotics, and it filed quackd under the wrong ecosystem.
- `quackd doctor` reported `?` for opencv and Pillow, which are not optional and were plainly
  installed. An import name is not a distribution name, so it now asks the installer which
  distribution provides the module before giving up.
- The Open Duck bring-up checklist keeps the feet off the ground through step 9 and puts
  them down at step 10. Three documents said step 8.
- `mypy` failed on Python 3.12 (the opencv stubs that resolve there type only the array
  overload of `cv2.inRange`, so the tuple bounds in `perception/color_blob.py` matched no
  variant). Bounds are now `uint8` arrays. OpenCV accepts both, so nothing about detection
  changes: the seeded goldens are byte-identical and all five sweeps still pass 10 of 10.
  This landed just after the v0.4.0 tag, so the tagged commit and the 0.4.0 files on PyPI
  still carry it. It is a type-check-only issue and does not affect the released package at
  runtime.

## [0.4.0] — 2026-09-02

From "a brain for the Microduck" to "a brain for any small robot". The thesis does not
change: the LLM picks verbs, the robot's own controllers move, quackd enforces the
contract. Four adapters (Microduck, Reachy Mini, a LeRobot arm, any base over rosbridge),
one `.duck` contract across bodies, a head and a duck completing one task together in the
simulator, and nothing claimed on hardware. Design: `docs/design/multi-robot.md`.

### Added

- **Robot adapters and manifests** (`quackd/adapters/`): every robot is an adapter that
  returns a `RobotManifest` from `connect()`, and the verb registry is built from that
  manifest rather than hardcoded. A verb that is not in the manifest does not exist. The
  Microduck is the first adapter and wraps the four existing transports with zero
  behaviour change; `manifest.schema.json` is generated and drift-tested. (ADR-0017)
- **Core verbs and aliases**: `observe`, `report_state`, `stop`, `say`, `move`, `go_to`,
  `search_scan` and `approach_and` exist on any robot that meets their requirements;
  `get_frame`, `walk_to` and `walk` are permanent aliases, listed once in
  `quackd/verbs/aliases.py`. `search_scan` sweeps the head on a robot that can only look.
  Preconditions are named in the manifest and supplied by the adapter; the executor spells
  none. (ADR-0018)
- **`.duck` v1**: `requires`, `robots`, `flock.roles` and `flock.frame_hints`; v0 files
  parse unchanged. `quackd validate --robot <adapter>:<backend>` checks a task against a
  robot's manifest with field-level errors such as `requires kick, but reachy-01
  (reachy-mini) does not provide it`. (ADR-0019)
- **`--robot <adapter>:<backend>`** on `run`, `validate`, `serve-mcp`, `doctor` and
  `list-verbs`; `--robots name=spec,...` on `run` and `validate`; `quackd list-adapters`;
  `doctor --robot` shows one manifest; a `.duck` may declare its default robot.
- Transcripts: `verb` events carry `canonical`; `run_start` and `summary.json` carry the
  robot's manifest and id. `duck_list_verbs` entries gain `canonical`, `aliases` and
  `core`; `duck_get_state` gains `robot`.
- Goldens recorded from 0.3.0 (`tests/golden/`) prove seeded worlds, the starter ducks and
  a `flock-kick` conversation are unchanged; CI runs both seeded sweeps at 10 of 10
  (`QUACKD_STRICT_SEEDS=1`).
- **Reachy Mini adapter** (`--robot reachy_mini:sim2d | mock | sdk`, extra `quackd[reachy]`
  for the SDK): a stationary head with a camera, a 180° neck, expressions and a speaker.
  Its manifest carries `observe`, `report_state`, `stop`, `say`, `search_scan` (a gaze
  sweep), `gaze`, `express`, `play_sound` and a confirm-gated `wake_up`; no locomotion
  verbs exist on it. `say(text)` is voiced as the closest expressive sound because the SDK
  has no text-to-speech; `stop` is `cancel_move` and `disable_motors` is never sent. Every
  SDK name is VERIFIED in `quackd/adapters/reachy_mini/upstream_api.py` against a pinned
  commit and the 1.10.0 wheel; the `sdk` backend has never been run on a robot.
  (ADR-0022, ADR-0023)
- **`StationaryHead`** in `sim2d`: a fixed camera on a wall with zero RNG draws, so every
  world without a head is byte-identical to 0.3; the recorder and the live window can
  focus a head camera.
- **`reachy-spotter` starter duck** (`duck: 1`, `robots: reachy_mini:sim2d`): find the
  ball with your gaze and say where it is; 10 of 10 seeds with the scripted pilot, judged
  by ground truth.
- **Heterogeneous flocks** (ADR-0020): members are adapters sharing one arena and one
  lockstep clock; bids carry a capability term so a robot bids only for a role its
  manifest can fill; one auction fills every role (most constrained first, lowest own
  distance, member-name tie-break, per-role hysteresis; the spotter is held for the run).
  With roles the kicker reports `kick_done` and the spotter judges from its own fresh
  frames (`VERDICT`); only `moved` is a success and the ground-truth veto stays on top.
  Frame hints (`HINT`, arena frame, sim only) choose the kicker's pre-turn; the
  frame-of-reference limitation is documented in `docs/flock.md`. `run --robots
  name=<adapter>:<backend>,...`.
- **`reachy-spots-duck-kicks` starter duck**: a Reachy Mini head spots the ball, a
  Microduck kicks it, the head judges the kick. 10 of 10 seeds with scripted pilots,
  every message in `flock.jsonl`, zero planner calls with the fake provider.
- **Multi-robot MCP**: `quackd serve-mcp --robots duck=microduck:sim2d,reachy=reachy_mini:mock`
  fronts a fleet with `robot_list`, `robot_list_verbs`, `robot_run_verb`, `robot_observe`,
  `robot_say` and `robot_load_duckfile`; every robot has its own executor, budget,
  heartbeat and contract, and `robot_load_duckfile` checks the contract's `requires`
  against that robot's manifest before adopting it. The eight `duck_*` tools stay as
  aliases of the default robot (deprecated, removed in 0.5). Simulated robots over MCP
  each get their own world; a shared arena over MCP is future work.
- **LAN discovery** (`quackd discover`, `quackd announce`, ADR-0021): zeroconf service
  `_quackd._tcp.local.` with an identity-only TXT record (manifest id, digest, adapter,
  body, verb count), every pair validated under 200 bytes before zeroconf sees it. Behind
  `quackd[lan]`, imported lazily, tested on fakes; exercised once for real between two
  processes on one machine, never between two machines.
- **MQTT flock bus** (`quackd.flock.mqtt_bus.MqttBus`, `run_flock(bus_factory=)`): the
  same two-method `Bus` protocol over a broker, `quackd/<flock_id>/ctl` at QoS 1 and
  `/hb` at QoS 0, never retained, the `FlockMessage` JSON as payload. Broker echo is
  dropped, the tap fires exactly once per message per node, remote messages are
  marshalled onto the event loop, and duplicates are tolerated by the coordinator's
  idempotent handlers. Library only: a flock across machines also needs a clock across
  machines, so there is no `--bus` flag. Tested on a fake broker; exercised once for real
  against a local `amqtt` broker with paho 2.1 (all eight kinds, one machine).
  `Subscription.drain()` is now an atomic `popleft` loop. `doctor` lists both LAN
  libraries.
- **LeRobot adapter** (`--robot lerobot:mock|real`, ADR-0022): an SO-101 class desktop arm
  with `move_joints`, `gripper`, `place` and, when a policy is available, `pick` as one
  skill intent that the arm's own learned policy executes. No locomotion, no voice, no
  gaze in its manifest. `real` sits behind `quackd[lerobot]` (Python 3.12 or newer, torch
  never imported on the default path), passes `calibrate=False`, refuses an uncalibrated
  arm, holds position on stop and never disables torque; every LeRobot name is pinned
  and line-linked in `quackd/adapters/lerobot/upstream_api.py`; never run on an arm.
- **rosbridge adapter** (`--robot rosbridge:mock|ws`): any wheeled base that takes a
  `geometry_msgs/msg/Twist` over `rosbridge_server`. The address carries the topics
  (`ws://host:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/compressed`); with an image
  topic the base also gets `observe`, `go_to`, `search_scan` and `approach_and`. There is
  no deadman: quackd re-sends the Twist at 10 Hz and zeroes it on stop, and the manifest
  says so. `ws` sits behind `quackd[rosbridge]` (roslibpy 2.x); every roslibpy, rosbridge
  protocol and message name is pinned and line-linked; never run against a bridge.
- **Speed limits come from the manifest**: `move`, `go_to` and the turn used by
  `search_scan` clamp to `limits.max_vx/max_vy/max_wz` when a manifest names them; the
  Microduck's limits equal the old schema bounds, so its runs are unchanged.
- **Docs**: `docs/adapters.md` (write an adapter in a day), `docs/manifest-spec.md`,
  `docs/adapter-status.md` (every adapter's honesty table, the Microduck's rows moved
  there unchanged), `docs/lan.md`, one page per adapter under `docs/adapters/`, and
  ADRs 0017 to 0023. `docs/safety.md` says what stops each body; `docs/faq.md` answers
  "can it drive something that is not a duck".

### Changed

- `default_registry()` is the Microduck manifest's registry; its names are canonical
  (`move`, `go_to`, `observe`) and every entry point accepts the old spellings. The
  bundled starter ducks keep their 0.3 spellings and stay at `duck: 0`.
- The agent loop connects before writing `run_start`, because the vocabulary comes from
  the connected robot.
- `docs/transport-status.md` is a redirect to `docs/adapter-status.md`; the docs test
  that keeps the Microduck's upstream table in sync now reads the new page.
- **The README was rewritten for four bodies**: the tagline and intro name the other
  robots, a new "Any small robot" section puts all four side by side with what each gets
  and what has actually run, the verb table gains a row per body, both architecture
  diagrams show the adapter layer, and the status table states per feature what was
  exercised against its real target and what was not.
- Test suite: 360 tests collected, no network and no keys, with four seeded sweeps CI holds
  at 10 of 10 (`find-and-kick`, `flock-kick`, `reachy-spotter`, `reachy-spots-duck-kicks`).
- Eight starter `.duck` files ship, up from six.

### Deprecated

- `--transport X` is an alias of `--robot microduck:X` that prints one warning per
  process; it is removed in 0.5. The `quackd.transport` package is not deprecated: it is
  the Microduck backend layer.
- The eight `duck_*` MCP tools (`duck_list_verbs`, `duck_run_verb`, `duck_get_frame`,
  `duck_get_state`, `duck_set_velocity`, `duck_stop`, `duck_quack`, `duck_load_duckfile`)
  are aliases of the six `robot_*` tools on the default robot. Each carries a deprecation
  note in its description and all eight are removed in 0.5.

### Fixed

- A role auction is complete only when every role can be filled by a *different* member,
  so a single robot that satisfies both roles can no longer deadlock a heterogeneous
  flock.
- `Subscription.drain()` is an atomic `popleft` loop rather than copy-then-clear, so a
  producer on another thread (the MQTT bus, before a message reaches the event loop)
  cannot have its message cleared unseen.

### Known limitations

- **Nothing has run on hardware, on any of the four adapters.** `microduck:jsonrpc`,
  `reachy_mini:sdk`, `lerobot:real` and `rosbridge:ws` spell every upstream name from
  upstream source (the three new ones at pinned commits) and have only ever talked to
  fakes. `microduck:websocket` is a stub waiting on upstream.
- LAN discovery and the MQTT bus were each exercised once, on one machine: zeroconf
  between two processes, MQTT against a local `amqtt` broker. Neither has crossed to a
  second machine, and a flock across machines also needs a clock across machines, which
  does not exist.
- Flock mode stays simulator only, with two choreographies and exactly two roles.
- A manifest can be smaller than the robot: `lerobot:real` claims no camera and no `pick`
  until it connects, and `rosbridge:ws` has no camera verbs unless the address names an
  image topic.
- The MCP server speaks `stdio` only, so it is a local subprocess of Claude Code or
  Claude Desktop and cannot be reached from a phone. A network transport is roadmap, not
  shipped.

## [0.3.0] — 2026-08-31

Multiple simulated Microducks cooperate: split the search, hold an auction, the closest
one kicks. Everything is on the record.

### Added

- **Flock mode** (`flock:` block in the `.duck`, or `--flock N` on `run`/`record`): 2 to 4
  ducks share one arena and an in-process message bus. A deterministic Contract Net
  auction picks the kicker (bid = each duck's own camera distance estimate, 20 %
  hysteresis, 6 s claim lease, duck-id tie-break, one-claimant lock), heading sectors
  split the search, misses trigger a full-circle re-search and re-auction, and a sim-time
  watchdog drops silent ducks. Every message, bid, claim and role change lands in
  `flock.jsonl`; the outcome is judged from sim ground truth, not a model claim.
  Guide: `docs/flock.md`. (ADR-0015)
- **Multi-duck simulator**: `World(n_ducks=…)` with per-duck deadman, noise streams, kick
  counters, duck-duck collisions and the four Microduck colorways (Cream, Sky, Lavender,
  Graphite); per-duck cameras render teammates, and the detector gained four `duck`
  targets. Sim time is governed by a lockstep clock, so the world freezes while any pilot
  thinks and single-duck runs stay bit-identical per seed. (ADR-0016)
- The planner makes **at most one** LLM call per flock run (parameters validated and
  clamped, deterministic fallback); `--provider fake` computes the plan as a pure
  function. Per-duck LLM pilots are deliberately out of scope.
- Duck to duck separation is watched from world ground truth while a claim is live: the
  coordinator orders an intruding non-kicker to retreat, and the retreat still runs
  through that duck's own executor.
- Starter `ducks/flock-kick.duck`; `runs/<ts>-flock-…/` layout with per-duck transcripts;
  flock demo GIF in the README. Scripted 3-duck acceptance: 10 of 10 seeds.
- The flock shipped through an adversarial review (69 agents, 24 confirmed findings, all
  fixed before release): deadlock guards around the shared clock's tick hooks and around
  member connect failures, per-duck `max_minutes` enforcement, a heartbeat watchdog floor
  above the longest verb sleep, cooldown gating at bid time, per-field planner clamping,
  `--max-steps` honoured on flock runs, `flock.search.restart_s` honoured, and
  `one_claimant: false` rejected instead of silently ignored.

### Changed

- `.duck` spec v0 gains the optional `flock:` block (`docs/duck-spec.md`, `schema.json`
  regenerated). Files using it need quackd 0.3.0 or newer; older versions refuse them
  loudly. `quackd validate` reports flock size and rejects flock + `verbs.confirm`.
- `quackd doctor` notes flock status; `serve-mcp` refuses flock ducks with a clear
  message.

## [0.2.0] — 2026-08-29

Local and open-source LLMs can pilot the duck. No API key needed.

### Added

- **Local providers** `ollama`, `vllm`, `llamacpp`, `lmstudio` and `local --base-url …`
  for any OpenAI-compatible server: no key, model discovery from `/v1/models`,
  `tool_choice=auto` and no `parallel_tool_calls` field for picky servers
  (`QUACKD_TOOL_CHOICE` overrides), vision opt-in with `--vision`, and a JSON text
  fallback for models that cannot call tools natively (marked `text_fallback` in the
  transcript). `quackd doctor` probes the four default local addresses.
  Guide: `docs/local-llms.md`. (ADR-0014)
- `quackd run --goal "…"`: a plain-language goal instead of a `.duck` file (ad-hoc contract:
  every `safe` verb, default budgets, standard abort rules). The scripted `fake` pilot picks
  a strategy from the goal's keywords.
- `--base-url`, `--api-key`, `--vision/--no-vision`, and `--gif-size` on `run`/`record`.
- Logo (`docs/assets/logo.svg`, a Microduck-like biped in the Lavender colourway) and a
  social-preview card.

### Changed

- README rewritten for people who know nothing about robots or LLMs first, developers
  second: what it does today vs. where it is going, Mermaid architecture diagrams, usage,
  configuration, performance and limitations sections. Images use absolute URLs so the
  PyPI page renders them. Providers are named by company ("OpenAI"), not model family.
- The hero GIF is recorded at 320 px panes.

### Fixed

- Rich markup ate `quackd[extra]` in CLI error hints.
- mypy on Python 3.12 (numpy's PEP 695 stubs) in CI.

## [0.1.0] — 2026-08-28

First release: sim-first, honest about hardware.

### Added

- **`.duck` spec v0**: strict pydantic frontmatter, generated `schema.json`,
  `quackd validate` with fail-fast field-level errors; five starter ducks
  (`hello-world`, `find-and-kick`, `patrol-and-quack`, `follow-me`, `fetch`) bundled in the
  wheel and resolvable by name.
- **Verb registry**: built-ins mapping 1:1 to shipped robot behaviours (`walk`, `sit`,
  `stand`, `kick`, `grab`, `stand_up`, `stop`, `quack`, `gaze`, `get_frame`), composites
  (`search_scan`, `walk_to`, `approach_and`), and the reserved learned-verb interface.
- **Safety executor**: allowlist, confirm gates, budgets, dry-run, machine-enforced
  `abort_when` (battery, consecutive failures), heartbeat, kill switch (Windows-safe).
- **Agent loop** with one tool call per turn, `runs/<ts>/transcript.jsonl`, frames,
  `summary.json`, and `run.gif` on the simulator.
- **`sim2d`**: built-in 2D simulator (deterministic under `--seed`, deadman, kick cone,
  unreliable open-loop scoop), top-down + first-person duck-cam renders, GIF recorder,
  optional `--live` window.
- **Perception**: `ColorBlobDetector` (HSV, bearing + distance from apparent size) and a
  lazy `YoloDetector` extra.
- **Providers**: `anthropic` (adaptive thinking, refusal handling, thinking-block replay),
  `openai`, `grok` (xAI endpoint), `gemini`, and the scripted `fake`; all vendor SDKs are
  optional extras.
- **`quackd serve-mcp`**: the duck as MCP tools for Claude Code / Claude Desktop through
  the same executor; `docs/mcp.md` with verified client config; project `.mcp.json`.
- **Transports**: `sim2d` (default), `mock`, experimental `jsonrpc` for the real robot
  (verified `duck-ipc-proto` v16 vocabulary, fake-robotd tests), `websocket` stub.
- **`quackd doctor`**, `quackd list-verbs`, `quackd record`.
- Docs: architecture, duck spec, transport status, safety, learned verbs (v2), licenses,
  FAQ, MCP; 13 ADRs; LAUNCH.md; CONTRIBUTING.md; hero GIF (scripted pilot, labelled).

### Known limitations

- The hardware transport has never run on a Microduck (hardware ships Christmas 2026).
- The README hero is a scripted-pilot recording; a real-model recording needs an API key.
- Non-Anthropic default model IDs are unverified; override with `QUACKD_MODEL`.

[Unreleased]: https://github.com/rokbenko/quackd/compare/v0.25.0...HEAD
[0.25.0]: https://github.com/rokbenko/quackd/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/rokbenko/quackd/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/rokbenko/quackd/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/rokbenko/quackd/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/rokbenko/quackd/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/rokbenko/quackd/compare/v0.19.1...v0.20.0
[0.19.1]: https://github.com/rokbenko/quackd/compare/v0.19.0...v0.19.1
[0.19.0]: https://github.com/rokbenko/quackd/compare/v0.18.1...v0.19.0
[0.18.1]: https://github.com/rokbenko/quackd/compare/v0.18.0...v0.18.1
[0.18.0]: https://github.com/rokbenko/quackd/compare/v0.17.1...v0.18.0
[0.17.1]: https://github.com/rokbenko/quackd/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/rokbenko/quackd/compare/v0.16.1...v0.17.0
[0.16.1]: https://github.com/rokbenko/quackd/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/rokbenko/quackd/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/rokbenko/quackd/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/rokbenko/quackd/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/rokbenko/quackd/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/rokbenko/quackd/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/rokbenko/quackd/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/rokbenko/quackd/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/rokbenko/quackd/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/rokbenko/quackd/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/rokbenko/quackd/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/rokbenko/quackd/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/rokbenko/quackd/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/rokbenko/quackd/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/rokbenko/quackd/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/rokbenko/quackd/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rokbenko/quackd/releases/tag/v0.1.0
