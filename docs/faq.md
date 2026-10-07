# FAQ

## Simulators and the browser demo

**Which simulator should I use?** For the duck, both come with the Microduck adapter,
`quackd[microduck]` for the cartoon and `quackd[mujoco]` for the physics, and the cartoon is
still the default. `sim2d` starts in a second, needs no network, runs anywhere, and is what the
three other bodies that have a simulator and every CI sweep use. It tests the *agent loop* —
search, approach, act, verify — and it will never tell you whether a gait works, because it has no joints
([ADR-0007](adr/0007-sim2d-cartoon.md)). `--robot microduck:mujoco` is upstream's own Microduck
model in MuJoCo, walking on `alpha_walking.onnx`, the policy Pollen trained, at 50 Hz on the
CPU. The ball rolls, the duck undershoots what you asked for, and a pilot that works there has
met a robot that does not do what it is told. Same arena, same seeded layout for the duck and
the ball, same verbs, so a `.duck` written for one runs on the other
([ADR-0030](adr/0030-mujoco-physics-backend.md)). One exception, and it is the only one: the
cartoon stands a person in its arena and the physics world does not, so `follow-me`, whose
whole task is to follow somebody, is cartoon only.
Neither one installed? [`web/`](../web/README.md) is the same physics and the same two policies
in a page, and it does one thing neither Python simulator does: the sentence box and the
keyboard drive the same duck at the same time. For the LeRobot arm there is a third, its own,
and the next answer is about it.

**Is there a simulator for the arm?** Yes, `lerobot:mujoco`, behind `quackd[lerobot-sim]`, and
it is a different kind of thing from the duck's two. It is the arm's real backend, the code that
drives an SO-101 on a desk, running over a physics model of the arm in MuJoCo, so a task file
rehearsed on it goes through the lines that will drive the arm in the lab: the connect and its
retries, the travel read off your calibration, the rest pose, the refusals and the close.
`quackd robot twin SOURCE` registers one of an arm you registered, on the calibration file its
runs read, and `quackd preflight` rehearses task files on it seed after seed, with a
`<task>.sim.yaml` beside a file to lay out the table and say what has to be so when a run ends.
The first connect fetches the maker's own model, `so101_new_calib_camera.xml` and 15 meshes
from TheRobotStudio's SO-ARM100 at a pinned commit, about 16 MB, one file at a time, each
checked against a recorded sha256, into `~/.quackd/cache`. `QUACKD_LEROBOT_SIM_ASSETS` points
at a checkout of your own instead. It needs no LeRobot and no torch, so it installs on Python
3.11. What it cannot tell you is how the arm moves: the dynamics are the model's, which way each
joint turns and where its zero sits are assumed until a bench checks them, and nothing measured
on it is a rate.
[adapters/lerobot/README.md](adapters/lerobot/README.md#the-simulator-lerobotmujoco) has the rest,
and [ADR-0047](adr/0047-the-arms-simulator-runs-the-real-backend.md) the reasoning.

**How do I run the physics simulator, and what does it download?**
`uvx --from "quackd[mujoco]" quackd run find-and-kick --robot microduck:mujoco --llm fake`.
The first run fetches upstream's model, `robot_walk.xml` and 38 STL meshes, from `microduck_rl`
at a pinned commit, and `alpha_walking.onnx`, `alpha_stand.onnx` and their manifest from the
Hugging Face Hub at a pinned revision, into `~/.quackd/cache`. About 10 MB over the wire and 23
on disk. Every file is checked against a sha256 recorded when it was read, a run that gets a
different file fails rather than continues, and the licence notice is written beside them,
because the model files are CC BY-NC-SA and quackd ships none of them
([reference/licenses.md](reference/licenses.md)). `QUACKD_MICRODUCK_ASSETS` points at a
`microduck_rl` checkout of your own instead, `QUACKD_CACHE_DIR` moves the cache, and
`QUACKD_MUJOCO_BODY=puppet` runs a kinematic stand-in that downloads nothing and is the body the
tests build directly.

**How do I run the browser demo, and is it live anywhere?** `python web/serve.py`, then open
<http://localhost:8000/simulator/>. Nothing to build, and no quackd to install: that server is
one stdlib file, it takes an optional port, and [web/README.md](../web/README.md) says why a
plain `http.server` will not do. The page is also live at <https://www.quackd.org/simulator>,
where the separate quackd-web project fetches this directory into its own build at a pinned
commit; `/simulator/source.json` says which one. The browser fetches
about 45 MB the first time and caches it: MuJoCo's WebAssembly build, onnxruntime-web and
three.js from jsDelivr, upstream's model at the same pinned commit Python uses, and
`alpha_walking.onnx` and `alpha_stand.onnx` — the same two policies Python fetches, with the
kick a scripted impulse in both. It is more megabytes than the answer above because the browser
pulls `robot_walk.xml` and 38 separate meshes uncompressed, 22 MB of it, where Python pulls one
compressed archive. Nothing fetched is hash checked, which Python does and the page admits
([web/README.md](../web/README.md)).

**Can I drive the browser demo myself?** Yes, at the same time as the model, which is the
argument the page exists to make. The keyboard is never handed over because it is never taken
away. A key that would *move* the robot takes it mid-run: the run is aborted and the request to
the model is aborted with it, so nothing keeps running against your key and no answer arrives
after you took the duck back, and the transcript records the handover with the key that did it.
A key that only reads never interrupts. There is no key for `say`, deliberately: a key carries a
command, and a sentence needs something to read it. The `quackd is on` switch decides only that
last part, whether anything reads English, and no longer decides whether you may drive. The key
map is in [web/README.md](../web/README.md).

**Why does the duck in the physics simulator not go the speed I asked for?** Because the walking
policy has a floor and quackd will not hide it. Under the model's own actuators the gait does
not start below about 0.23 m/s or 1.0 rad/s, and above that it achieves roughly 0.38 of what it
is asked. Both were measured here on one machine and are tagged UNVERIFIED in
`adapters/microduck/src/quackd_microduck/sim3d/upstream_api.py`. `move` defaults to 0.15 m/s,
so a non-zero twist below the floor is scaled up bodily, keeping the ratio between its axes
so an arc stays an arc, and a twist
below a third of the floor is dropped to zero rather than turned into a lurch nobody asked for.
What was asked and what was sent are both in the state (`twist_commanded`, `twist_sent`,
`gait_floor`) and in the prompt. Upstream trains and deploys with a different actuator model, so
a real Microduck may track commands directly.

**Do I need a GPU for the physics simulator?** No, for either body. MuJoCo steps on the CPU and
the duck's policy runs under onnxruntime's CPU provider. Upstream needs CUDA to *train* that
policy, never to run it. What the head camera needs is an OpenGL context to render into: a laptop has one, a bare server
may not, and the frames are what fails first there. On a headless Linux box, install `libosmesa6`
and set `MUJOCO_GL=osmesa`, which renders into process memory and needs no display, no GPU and no
`/dev/dri` — it is what CI's own physics job does. quackd names both in the error rather than
letting an OpenGL traceback out. The arm's simulator needs the same context for its cameras,
and renders once as it connects, so a machine that cannot draw is told at connect rather than
at the first frame. Rendering is the real cost of both, not physics.

## Models and providers

**Is the pilot always an LLM?** By default, yes, and nothing changes unless you ask it to. A
run's pilot is the provider you named, and every verb, every joint angle and every sentence
comes from it. The one exception is opt-in: `--decision-llm` puts an optional non-generative
stepper in front of the model for the turns whose answer is a choice among calls the body
already has, a read, the brake, a gripper, a gaze. It is off unless you name one, it needs its
own extra and, for the hosted one, its own key, it authors no number and no sentence, and it
cannot end a run or record a feasibility verdict. It is also not a provider and `--llm` does not take one.

**And it is not a second LLM.** That distinction is the whole reason it is allowed near a robot. A language model generates tokens, so asking it for a verb means asking it to write one, and it could just as easily write a joint angle. A decision LLM generates nothing: it scores the options you hand it against a state and returns which one, with a probability for each. There is no text in the answer, so there is nowhere for an invented verb or an improvised angle to come from. TypeSafe, whose Jev was the first of them and gave the wire format its name, put it as *"LLMs produce words for people. Jev produces typed decisions"*, and the same holds for every other one quackd names, the open servers you run yourself, the checkpoint that loads into this process, and anything else you point `--decision-url` at. [guides/decision-llms/README.md](guides/decision-llms/README.md) says what they do, what they deliberately cannot, and roughly what they save.

**Does `uvx quackd run … --llm anthropic` work with no extras?** No, and it now takes
two of them rather than one: the default install is light on purpose, so a bare `uvx quackd`
has no vendor SDK and no robot either. Name the brain and the body together:
`uvx --from "quackd[anthropic,microduck]" quackd run find-and-kick --llm anthropic`, or
`uv pip install "quackd[anthropic,microduck]"`. Whichever half is missing, quackd prints the
command that fixes it: the provider one names `quackd[anthropic]`, the robot one names
`quackd[microduck]` and the six other adapters. `--llm fake` still needs no key and no
extra for itself, but it does need a body to drive.

**Which models can I pick?** Whatever the catalogue lists for the vendor you named. It is one
hand-written table of 123 ids across twelve cloud vendors, and `quackd list-models` prints it,
`--llm mistral` (or any other name) narrowing it to one vendor. Every row carries a status
— `current`, `legacy`, `preview`, `specialised` or `open` — and a notes column that marks three
things worth knowing before you pass an id: `default`, `Responses API` for the OpenAI models
that refuse function tools on Chat Completions, and `no frames` for a model whose vendor does
not document image input, which gets the detections as text instead of the camera frame unless
`--vision` says otherwise. The default is simply the first row for that vendor, so
`--llm openai` is the same as naming that vendor's first id after the colon. Local presets are
deliberately outside all of this:
`ollama`, `vllm`, `llamacpp`, `lmstudio` and `local` take any id the server serves, or the first
model it lists when you name none. Anthropic extras: `QUACKD_EFFORT` (default `medium`) and
`QUACKD_ANTHROPIC_FALLBACKS=0` to disable server-side refusal fallbacks.

OpenRouter sits between the two. Its rows are a selection rather than all it takes:
`--llm openrouter:AUTHOR/MODEL` can also name an id quackd does not carry. Unless quackd refuses
its spelling first, that id is checked against OpenRouter's public model list when the run starts,
and the list has to carry it with tool calling. An id with a suffix (`google/gemma-4-31b-it:free`)
needs `openrouter:` in front of it, because `--llm` splits at the first colon. No OpenRouter model
has answered a real quackd request: everything after its model list has only met a stand-in. See
[guides/openrouter.md](guides/openrouter.md).

**Why is my model rejected?** Because that vendor's catalogue does not list the id, and quackd
checks before it reads a key or opens a connection, so nothing was sent anywhere:

```
$ quackd run hello-world --llm openai:gpt-5 --robot microduck:mock
✗ error: openai: unknown model 'gpt-5' from --llm. Valid ids: gpt-6-sol (default), gpt-6-astra,
gpt-6-luna, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna, gpt-5.5, gpt-5.4, gpt-5.4-mini, gpt-5.4-nano,
gpt-5.2, gpt-5.1, gpt-4.1, gpt-4.1-mini, gpt-4o, gpt-4o-mini, gpt-5.5-pro, gpt-5.4-pro,
gpt-5.2-pro, gpt-5.3-codex, chat-latest. See `quackd list-models --llm openai`.

$ quackd run hello-world --llm openai:grok-4.6 --robot microduck:mock
✗ error: openai: unknown model 'grok-4.6' from --llm ('grok-4.6' is a grok model: --llm
grok:grok-4.6). Valid ids: gpt-6-sol (default), gpt-6-astra, gpt-6-luna, gpt-5.6-sol,
gpt-5.6-terra, gpt-5.6-luna, gpt-5.5, gpt-5.4, gpt-5.4-mini, gpt-5.4-nano, gpt-5.2, gpt-5.1,
gpt-4.1, gpt-4.1-mini, gpt-4o, gpt-4o-mini, gpt-5.5-pro, gpt-5.4-pro, gpt-5.2-pro, gpt-5.3-codex,
chat-latest. See `quackd list-models --llm openai`.
```

Ids are unique across the catalogue, so an id that belongs to somebody else is named as such
rather than just refused, which is the mistake worth catching early, and that same uniqueness is
why `--llm gpt-6-astra` can work out its own vendor. `QUACKD_LLM` goes through the same check
and gets the same refusal, with `QUACKD_LLM` in place of `--llm` in the message, so a stale line
in your `.env` cannot quietly start a run either. The one thing this never applies to is a local
preset, whose model half is free text.

OpenRouter is checked in two steps. An id quackd will not take even where OpenRouter lists it is
refused on its spelling, offline and before a key is read:

```
$ quackd run hello-world --llm openrouter:~anthropic/claude-opus-latest --robot microduck:mock
✗ error: openrouter: '~anthropic/claude-opus-latest' from --llm is refused: a `~` alias always
resolves to the newest model of its family, so the model that answers could change under a run that
names it. quackd lists openai/gpt-6-sol, openai/gpt-6-luna, anthropic/claude-sonnet-5.5,
anthropic/claude-opus-5.5, google/gemini-3.8-flash, x-ai/grok-4.7. See `quackd list-models --llm
openrouter`.
```

An id quackd does not carry that passes that spelling check is then looked up in OpenRouter's
public model list when the run starts. That sends one request to `openrouter.ai`, with no key, and
the run is refused if the list does not carry the id, carries it without tool calling, or says it
has expired.

**The id is in the catalogue, but the vendor refuses my key.** Check which endpoint your key
belongs to. quackd calls each cloud vendor at one fixed base URL, and for two of them there
is more than one to choose from: Qwen goes to DashScope's *international* endpoint
(`dashscope-intl.aliyuncs.com`), so a key issued on Alibaba's China console will not
authenticate, and Cohere goes to its OpenAI compatibility path rather than its native one.
`--base-url` moves any vendor that speaks OpenAI's API, which is every one of them except
Anthropic and Gemini, where the flag is accepted and ignored. `--extra-body` adds a field
that vendor wants and quackd never sends, on the same terms.

**Why did my OpenAI run move to a different API mid-flight?** Because some OpenAI models refuse
function tools on `/v1/chat/completions` at every reasoning effort and name `/v1/responses` in
the 400. Every verb is a function tool, so quackd reads that answer, moves the run to the
Responses API and stays there. The catalogue already knows which models those are, so `quackd
list-models` marks them `Responses API` and a run on one opens there without spending a call to
find out; reading the 400 is what still covers a model the catalogue has not been told about.
`QUACKD_OPENAI_API=responses` starts there in every case, and `QUACKD_OPENAI_REASONING_EFFORT`
sets the effort on either API. The browser demo does the same on both counts, with nothing to
set.

**Are local LLMs supported (llama.cpp, vLLM, Ollama, LM Studio)?** Yes. They all speak
OpenAI's Chat Completions API, so `--llm ollama`, `vllm`, `llamacpp`, `lmstudio`, or
`local --base-url http://host:port/v1` works with no API key. Tool calling must be enabled
on the server (`llama-server --jinja`, `vllm serve --enable-auto-tool-choice
--tool-call-parser …`), vision is off unless you pass `--vision`, and a small model that
writes its tool call as plain JSON is still understood. A field the server wants in the
body goes in with `--extra-body`, which is how Qwen3 is told not to think on vLLM. Details: [guides/local-llms.md](guides/local-llms.md).

## Seeing what happened

**How does the LLM "see"?** Providers with vision get the duck-cam PNG for the last two turns,
or up to the last nine on Claude Opus 5.5 and Fable 5.1; every provider gets a text line like
`ball at bearing 12° left, ~0.80 m` from the detector. Composite verbs steer on detections at
10 Hz and never wait for the model.

**How do I see what the model was told, what it thought, and what it sent the robot?**
You already do: the log is on by default. `quackd run` narrates the whole run to stderr as
it happens, and every MCP tool call that reaches a robot comes back with a `log` list of
the same lines. You get the system prompt once, then per turn the observation, the model's
reasoning where the provider returns any, the tool it chose, the tokens, the latency and
what that call cost, every executor gate that fired, every intent that went to the robot (a
steering loop's burst collapsed into one line with its parameter ranges), and the result.
`--no-log` or `QUACKD_LOG=0` decides what you *watch* and nothing else: the narration stops,
the run still writes its log, and `runs/<ts>/transcript.jsonl` has all of it either way,
uncapped. It was the trace until 0.11. The old subcommand, the old flags and the old variable
names had one release of grace and 0.12 removed them: a script that still types one is refused
and told so, and a `.env` that still sets one is ignored and told so, which matters because a
name that quietly stopped being read would switch the log back on for whoever had turned it
off. The MCP result key is the one that changed outright, because a model learns the name from
the tool description on every call and carrying both would cost every call a second copy of the
same thirty lines. Details and the event list:
[concepts/architecture.md](concepts/architecture.md#log), [ADR-0029](adr/0029-tracing.md).

**Can I read a run after it finished?** Yes. `quackd log` replays the newest run under
`runs/` as the same lines it printed while it ran, and it takes a directory name, a timestamp
prefix, the name you gave the run, a duck name or a transcript file if you want an older one.
`--from-step N` starts part way in, `--no-prompt` drops the system prompt, `--thinking all`
shows every character the model thought, and `--frames` adds a line per camera frame. It
prints to stdout, so piping it to a pager or a file is the point. A run recorded before 0.11
replays unchanged, because the only thing the rename touched on disk is one counter,
`trace_dropped`, and the reader still takes that spelling beside `log_dropped`. A flock run
replays every member, each line prefixed with the robot that wrote it, and its counters come
from the flock's own summary rather than from whichever member happened to finish last. A
SOLO replay opens with a header saying when the run started, what it was named and what the
model was priced at, so a transcript you come back to a month later still says what it cost
and why. A flock has no such header, because there is no one model and no one clock to put in
it: its name and its rate are in `summary.json`.

**Where is what was actually on my screen?** In the run directory, as `terminal.txt`:
everything the run printed, as plain text with no colour codes in it, opening with the command
that started it and the version that ran it. `quackd log` replays the record and this is the
screen, which is not the same thing: the file has the header panel, the warnings, the
questions you were asked and the answers you typed, in the order you saw them. It holds what
you watched, so a run started with `--no-log` leaves a short one, and a flock keeps a single
file at the run root because a flock is still one terminal. A key you passed on the line is
not in it: the command it opens with has the value of `--api-key` and `--token` replaced by
`***`, and `--base-url`, `--address` and `--camera-url` keep their host and lose a password
in the URL. That is redaction by name, so a secret typed as the value of some other flag is
in the file in full, and that is the thing to check before you paste one into an issue
([SECURITY.md](../SECURITY.md)).

**How do I find one run again a week later?** Name it when you start it. `--run-name
"Example 1"` puts the name in the directory after the duck,
`runs/20260921-155628-find-and-kick-example-1/`, slugging it on the way: lowercased, every
run of anything that is not an ASCII letter or digit becomes one hyphen, and the result is
capped at 64 characters. Accents are folded rather than dropped, so `Café 1` and `Cafe 1` both
land on `cafe-1`. The text as you typed it is kept as `run_name` in `summary.json` and shown
in the replay header, so the slug names a directory and never loses what you wrote. A name
with nothing ASCII in it at all, which includes a name written entirely in another script, is
refused before anything connects and before any directory exists:

```
$ quackd run find-and-kick --run-name "!!!"
✗ error: --run-name '!!!' has no ASCII letters or digits in it, so there is nothing to name the directory after
```

Afterwards `quackd log example-1` finds it by that label. That pass runs ahead of both the
timestamp prefix and the loose substring, so a bench holding both `-example-1` and
`-example-19` hands you the run you actually named rather than whichever is newer, and a run
you called `2` is reachable by `2` rather than being answered by the first run of 2026 that
comes to hand. `quackd record` takes `--run-name` too.

**Why is the thinking line empty for my model?** Because that model did not return any. Only
some do, and each in its own way: Claude returns a summary (quackd asks for one, since the
default is to send the blocks back empty), an OpenAI-compatible server may fill
`reasoning_content` or `reasoning`, Gemini returns thought parts when asked, and a local
server that separates nothing gets its `<think>` block split out of the answer. OpenAI's own
Chat Completions returns a reasoning token count and no text, so that is what the log
shows. The scripted pilot has no reasoning either, but it does report which rule it followed
— what it saw, how the last verb ended, and the verb that fell out — on the same line, marked
`[scripted]` so it can never be mistaken for a model's own words. So a run with no API key
still shows you the shape of the log.

## Real robots

**Does the robot need a powerful onboard computer?** No. quackd's own process, the part
that calls the LLM and runs the detector, never has to run on the robot itself: you run
`quackd run` on a laptop or a server, a network hop away, and it talks to the robot (or the
simulator) from there. The Open Duck Mini's official target, a Raspberry Pi Zero 2 W, only
ever runs its existing 50 Hz walk policy plus two small daemons that do run on the Pi, a
bridge and a camera server, and neither does any perception or inference of its own: just
enough to swap the gamepad for a socket and to serve a JPEG ([`bridge/open_duck/`](../bridge/open_duck/README.md)). The Microduck's onboard
computer works the same way, through `robotd`. Nothing here needs an NPU or a bigger board
to keep up, because nothing model-shaped *has to* run on the robot's own board.
When the robot's computer is an NVIDIA Jetson, as a ToddlerBot's is, quackd still stays on the
laptop and uses the board from there: `--host` reaches the model server on its GPU, a camera on
it, YOLO detections computed on that GPU, and the board's health in `quackd doctor`, all
through one small daemon quackd ships for the board ([guides/jetson.md](guides/jetson.md)).

**Does quackd use TOF or another depth sensor for obstacle avoidance?** Not yet. The only
sensing input today is a single colour camera: an HSV threshold (or optionally YOLO) gives
a bearing and an apparent-size distance to one named target, and `go_to` steers toward it —
there's no depth data, no occupancy grid and no general obstacle avoidance. The manifest
schema has a generic `tof` sensor slot for future adapters
([reference/manifest-spec.md](reference/manifest-spec.md)), and the Microduck's own `tofd` depth
stream isn't read yet either ([adapters/microduck/README.md](adapters/microduck/README.md)); the
Open Duck Mini's official build has no depth sensor at all.

**How do I tune the detector for a real orange ball?** `ColorBlobDetector` takes
`targets=(Target("ball", HSVRange(h_lo, h_hi, s_lo, v_lo), size_m=radius, round=True), …)`
in OpenCV HSV (H 0–180). Photograph the ball under your light, sample its hue, give ±8, and
pass `--fov-deg 62` to `quackd run` for the IMX219. Distance comes from apparent size:
measure the pixel radius at 1 m once and adjust `size_m` until it reads 1.00. Or install
`quackd[yolo]` and pass `--detector yolo`, which runs YOLO in quackd's own process. A Jetson
reached with `--host` runs the same YOLO on the board's GPU instead, and that one is
`--detector host`.

**Does it remember anything between runs?** Since 0.6, a little, per robot. Each
`adapter:backend` has a JSONL file under `~/.quackd/memory/` holding two kinds of line: the
notes the pilot chose to keep with the `remember` tool, and one line per earlier run that
quackd writes itself (outcome, reason, the last few verb results). The newest twenty notes
and five episodes go into the next run's system prompt. It is deliberately not a memory
*system*: no embeddings, no search, newest wins, nothing shared between bodies, and the
executor never reads it, so a note can never widen an allowlist or lift a budget. The
scripted pilot has no `remember` in its script, so `--llm fake` accumulates run
outcomes and never a note. [guides/memory.md](guides/memory.md),
[ADR-0025](adr/0025-memory-between-runs.md)

**Who decides the run succeeded?** The LLM, via `declare_success(reason)` — that is the
honest state of the art. In the duck's two simulators the run summary also records ground truth
(`ball_displacement_m`) and the tests check the claim against it. The arm's simulator keeps its
truth where the pilot cannot read it, and `quackd preflight` judges a rehearsal by it.

**What if the robot cannot do what I asked?** It says so before it moves. Every robot carries
a datasheet of what it weighs, can carry and can reach, and the pilot has to judge the task
against it (`assess_task`) before any verb that moves the body will run. A task that clearly
exceeds a limit ends the run as `infeasible` rather than `failure`: nothing moved, `quackd run`
exits 3, and the reason names which other shipped body could do it. If the verdict turns on
something the pilot cannot judge from where it is, it answers `uncertain` and you are asked.
An infeasible run is remembered like any other, so the next run on that robot is told what was
already found not to fit it. If the number the verdict turned on is simply wrong for the build
in front of you, a printed gripper that holds more than the vendor's, say, a `duck: 2` task file
can correct it with a `datasheet:` block, and the prompt labels those figures as coming from the
task file rather than from the maker ([reference/duck-spec.md](reference/duck-spec.md),
[guides/memory.md](guides/memory.md), [concepts/safety.md](concepts/safety.md)).

**Why can't the duck say words?** Upstream has seven duck sounds and no TTS. `quack(text)`
maps your text to the closest tone (`greet`, `inquire`, `alarm`, `wheee`, …) and logs the
text.

**What does it cost?** A `find-and-kick` run is 3–8 model turns, each a few thousand input
tokens (mostly the system prompt and one image) and a short tool call. quackd works the
dollars out itself rather than leaving you to multiply a token count by a rate you looked up:
the catalogue carries a price per model in USD per million tokens, read off that vendor's own
pricing page and dated with the day it was read, and 119 of the 123 ids have one. So every
`llm` record in `transcript.jsonl` carries `cost_usd` for that call and `cost_usd_total` for
the run so far, `summary.json` carries the run's `cost_usd` beside its `usage` and the exact
rate it was charged at, and the counters under the verdict print the total:

```
steps 4 · llm calls 6 · tokens 7688+96 · time 0.2 s (model 0.0 s) · cost $0.0245
```

Cached input is billed at the cache rate wherever a vendor reports the cached slice, and a
cache rate the vendor does not publish is charged at the full input rate, because a figure
you act on should overstate rather than understate. A model quackd has no published rate for
records `cost_usd: null` and prints `cost unpriced` instead of a zero, since a number that
could not be computed must never read as a number that came out to nothing, and the run says
so once on stderr with the flag that fixes it. That is four ids today, all of them Cohere's,
the Command A family and North Mini Code, for which Cohere publish no per-token rate at all.
On OpenRouter a turn is costed at what OpenRouter says it billed, its `usage.cost`, which
already knows which endpoint served the call and at what tier, and that call's `llm` record
says `billed: true`. The fee OpenRouter charges when you buy credits is outside every run.
Your own rate goes in with
`--price in=3,out=15[,cache_read=0.3,cache_write=3.75]` for one run, or `QUACKD_PRICE` for a
shell full of them: a negotiated rate, a paid endpoint behind a local preset, or an id the
catalogue has never heard of. The `fake` pilot and every local preset are priced at zero
rather than left unpriced, so a sim run honestly reads `cost $0`.

**Windows?** Fully supported for sim, MCP and development. The real-robot `unix://` socket
is POSIX-only; forward it with `ssh -L 9870:/run/robotd.sock <robot>` and use
`--address tcp://127.0.0.1:9870`.

**Can I run it on my Microduck today?** `--robot microduck:jsonrpc` speaks the verified
`duck-ipc-proto` v23 vocabulary but has never touched hardware. Start with `--dry-run`,
read [adapters/microduck/README.md](adapters/microduck/README.md), and tell us what happened.

## Driving it from somewhere else

**Can I drive it from the Claude mobile app?** Not yet. `quackd serve-mcp` speaks `stdio`
only, so Claude Code and Claude Desktop spawn it as a local subprocess on the same machine
and talk to it over pipes. The mobile app reaches tools as remote connectors instead:
servers that run persistently at a network address with their own auth. quackd would need
an HTTP or SSE transport, a long-lived process, a reachable address and authentication
before a phone could talk to it. Roadmap, not shipped. The details are in
[guides/mcp.md](guides/mcp.md#why-not-from-my-phone-yet).

**Is control text-only?** Yes, from you — a `--goal` string, a `.duck` file, or a chat
message over MCP; there's no voice or GUI input. The loop isn't text-only end to end,
though: cloud providers also read the camera frame as an image each turn, and whatever
the model decides is always one of a fixed set of verbs (`walk_to`, `kick`, `quack`, …),
never a freeform command sent to the motors.

**Do I need to be near the robot to control it?** No, proximity is not the constraint.
`robotd`'s socket only accepts connections from the robot's own computer, so control always
goes through a network hop (see `Windows?` above), and that hop works the same across the room
or across the world. What matters is latency: the deadman expects `robot.move` roughly every
100 ms, so a slow or flaky link stops the robot outright, however close you are standing.

## Security and privacy

**Is quackd production-ready?** No — it's a research prototype built around one trusted local
operator, not a hardened multi-user product. There's almost no authentication anywhere; `.duck`
files with a `flock:` block are refused over MCP for exactly that reason: the session is one
model, a coordinator flock needs a referee this process does not run, and a pilot flock needs
one model per robot rather than one for all of them. Nothing arbitrates two sessions driving the
same robot at once. Every real-hardware transport but one is experimental and unverified end to
end, and the one that has run, `lerobot:real`, ran for two afternoons on one arm, 2026-09-15 and
2026-09-23 ([adapters/status.md](adapters/status.md)); the CLI and MCP server are both thin
callers of the same executor and verb registry, so a real client like a phone app would mean
adding a network-reachable server and auth on top, not rewriting the core.

**Can I control who's allowed to pilot my robot?** Barely, and only where quackd ships the
robot side. quackd
adds no login or accounts, so access is mostly whatever your OS and network enforce.
`robotd`'s socket can't be reached off the robot's own computer unless something bridges
it, so the real gate there is SSH's authentication (and your Wi-Fi's), not quackd's;
`quackd announce`/`discover` do broadcast a robot's identity, unauthenticated, to anyone on
the LAN ([guides/lan.md](guides/lan.md)), though that's identity only, not a way to drive it. The
two exceptions are the daemons quackd itself ships, for the Open Duck Mini and the ToddlerBot:
each binds loopback, and if a token is configured it checks one with `hmac.compare_digest`
before accepting a
handshake (`--token`, or `QUACKD_DUCK_TOKEN` for the duck and `QUACKD_TODDLERBOT_TOKEN` for the
ToddlerBot). The duck's camera server has no authentication at
all, so tunnel it. The daemon quackd ships for a Jetson cannot move a robot at all. It binds
loopback too, and with a token set (`--token`, `--token-file` or `QUACKD_HOST_TOKEN`) it checks
it on every request the same way, because what it serves is a camera and a GPU. On a
Microduck the physical gamepad preempts remote commands; on an Open Duck it does not, because
quackd's daemon *replaces* the gamepad the walk loop reads, which makes the power switch the
only thing that always wins ([concepts/safety.md](concepts/safety.md)).

**What stops the model itself from doing something dangerous?** The executor, not the
model's judgment: every verb call is checked against the loaded `.duck`'s allowlist,
budgets and confirm gates before anything is sent, and machine-enforced `abort_when` rules
and preconditions (not fallen, not sitting) run right after — a refusal is enforced code,
not a request the model can talk its way around. That's still only the software layer, and
what the body adds under it varies: the Microduck's `robotd` has fall detection, thermal
clamps and a deadman, while an Open Duck Mini v2 declares `none` and the watching human is
its fall detector — see [concepts/safety.md](concepts/safety.md).

**Does my data ever leave my machine?** Only if you choose a cloud provider. All twelve of
them (Claude, OpenAI, Gemini, Grok, Mistral, DeepSeek, Cohere, Qwen, Kimi, GLM, Meta and
OpenRouter) send
the prompt to that vendor's API over the network, under its own terms, and the camera frame
with it wherever the model takes an image — `quackd list-models` marks the ones that do not
with `no frames`, and those get the text detections instead. One vendor is worth reading the
labels for: two of Meta's Muse Spark models are a *contributor tier*, discounted in exchange
for Meta training on your prompts, and `quackd list-models` says so on those rows. OpenRouter
is the one that does not run the model itself: it is a router, so a run on it sends the prompt
and the frame to OpenRouter and on to whichever provider it picks for that model, under both
their terms. OpenRouter's FAQ said on 2026-10-06 that it logs no prompt or completion unless
you opt in, and that it does not route to a provider that logs, or whose policy it could not
confirm, unless you switch on the model training toggle in your OpenRouter privacy settings.
`--extra-body` narrows the providers further per run, with `{"provider": {"data_collection":
"deny"}}` or `{"provider": {"zdr": true}}` for zero data retention. Every request to it
also carries `HTTP-Referer: https://github.com/rokbenko/quackd` and
`X-OpenRouter-Title: quackd`, which credit quackd's traffic on OpenRouter's public app
rankings and say nothing about you. The `fake`
pilot and any local model (`ollama`, `vllm`,
`llamacpp`, `lmstudio`) never do and need no API key, though a local model is still served
over its own local HTTP endpoint, not literally air-gapped. Since 0.6 one thing also stays
behind on your machine: `~/.quackd/memory/<robot>.jsonl`, a plain text file of sentences
about where things are, which is then part of the prompt on the next run and so part of what
a cloud provider sees. `quackd memory show` prints it, `quackd memory clear` deletes it, and
`--no-memory` never writes it. See [guides/memory.md](guides/memory.md) and
[guides/local-llms.md](guides/local-llms.md).

## What it can and cannot drive

**What does `uv pip install quackd` give me?** The loop, and no robot at all. Every adapter is
its own distribution built from the same repository, so the core carries the CLI, the executor,
the verb registry, the 2D arena every simulated body draws itself in and the MCP server, and
you choose the bodies:

```bash
uv pip install "quackd[microduck]"    # the duck: the cartoon, the mock and the real one
uv pip install "quackd[lerobot]"      # an SO-101 arm, with LeRobot on Python 3.12 or newer
uv pip install "quackd[lerobot-sim]"  # the same arm's simulator, MuJoCo and no LeRobot
uv pip install "quackd[robots]"       # all seven, each with the SDK its real backend needs
```

The seven names are `microduck`, `lerobot`, `rosbridge`, `open_duck`, `xlerobot`, `alohamini`
and `toddlerbot`. `quackd list-adapters` prints the whole table whether or not you have any of
them, marking each row `installed` or `not installed`, so it is a shopping list before it is an
inventory. A command that needs a body and finds none refuses by naming what to install rather
than failing somewhere confusing. Install exactly one adapter and that one is the default, so
`--robot` is something you start typing when you own a second robot. Install several including
the Microduck and `microduck:sim2d` stays the default, because that is what the starter `.duck`
files have always meant.

**Can two different robots share a task?** Yes, with a pilot flock. Register them, group
them, run it:

```bash
quackd robot add duck microduck:mock
quackd robot add arm lerobot:mock
quackd flock create pair --robot duck --robot arm
quackd run flock-hello --flock pair --llm fake
```

Each body gets its own LLM pilot, each pilot's prompt carries the other's datasheet, and they
divide the task by talking. Each member keeps its own memory, and what one needs another to
know during the run it says with `tell`. `quackd run flock-hello --llm fake` does the same
thing with no registry at all. The honest part: this has run on `mock` and `sim2d` bodies and on
no hardware, N simulated members are N separate worlds with nothing checking a claimed success,
and `tell` has been exercised by the scripted pilot and by no real model
([guides/flock.md](guides/flock.md#the-pilot-flock)).

The other kind of flock, the deterministic coordinator, still knows only the Microduck on
`sim2d`. `flock.roles` there declares capability-differentiated roles (a spotter that observes
and judges, a kicker that goes to and kicks), which is unit tested and has no bundled starter
that reaches it end to end.

**Can quackd drive something that is not a duck?** Since 0.4, yes: a robot is an adapter
that returns a manifest, and the verbs come from the manifest. `quackd list-adapters`
shows the seven that ship (Microduck, a LeRobot arm, any base over rosbridge, an Open
Duck Mini v2, an XLeRobot dual-arm cart, an AlohaMini with two arms on a lift, and a
ToddlerBot humanoid), `quackd list-verbs --robot microduck:sim2d` shows what one of them can do,
and `quackd validate your.duck --robot lerobot:mock` tells you, field by field, whether
your task fits that body. The rule never bends: a verb that is not in the manifest does
not exist on that robot. Those seven are each their own package, and an adapter somebody else
publishes is found exactly the same way, through the `quackd.adapters` entry point group, with
no pull request to this repository. Writing one:
[adapters/writing-an-adapter.md](adapters/writing-an-adapter.md).

**Is a Jetson one of the seven?** No, and it is not an eighth. A Jetson is a computer, not a
body: it has no manifest and no verbs, `quackd list-adapters` will never show it, there is no
`--robot jetson:...`, and there is no adapter to install for it
([ADR-0044](adr/0044-a-jetson-is-a-host-not-a-body.md)). quackd does not run on it either. It
names the board with `--host` from the laptop, and uses the model server on its GPU, its camera,
its detector and its health from there, which is the arrangement *Does the robot need a
powerful onboard computer?* above describes ([guides/jetson.md](guides/jetson.md),
[ADR-0046](adr/0046-the-jetson-is-reached-not-run-on.md)).

**Why does `validate` say "requires kick, but arm-01 (lerobot-so101) does not provide
it"?** Because it is true. A `.duck` lists what it needs (`requires`, or for a `duck: 0`
file its whole allowlist) and an arm has no legs. Either pick a body that has the verb,
or write a task for the body you have.

## The name

**Why "quackd"?** Upstream names its daemons `robotd`, `mediad`, `padd`, `tofd`… the brain
daemon was the one missing, so the first quackd was exactly that, for one Microduck. The name
stayed when quackd became the command line for every robot you own, and so did the ducks: a task
is a `.duck` file and a group of robots is a flock.
([ADR-0002](adr/0002-name.md), [ADR-0035](adr/0035-one-cli-for-all-your-robots.md))
