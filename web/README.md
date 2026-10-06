# The browser demo

A static page that puts a Microduck in front of you with no install: type a sentence, and a
model you bring the key for turns it into the robot's own skills while quackd decides what
it is allowed to do. The keyboard is live the whole time, next to the box you type in, so the
two ways of driving a robot sit a centimetre apart. It is the same idea as
`quackd run --goal "..." --robot microduck:mujoco`, with the same physics and the same
walking policy, in seven modules of plain JavaScript with no build step instead of Python.

It lives at <https://www.quackd.org/simulator>. That domain is served by quackd-web, a separate
Vercel project, whose build fetches this directory into its own `/simulator` at a pinned commit,
so the bytes a visitor gets are that project's build output rather than anything this repository
deploys. There is no build step at either end: what ships is `web/` as it stands, because
everything heavy here is a CDN URL the page fetches at run time.

### Why the paths are absolute

Every local reference in `index.html` is `/simulator/...` rather than `style.css`. That is not
a style choice. quackd-web sets `trailingSlash: false`, so a visitor to `/simulator/` is
redirected to `/simulator`, and from *that* URL a relative `style.css` resolves to
`/style.css` — the landing page's root, not this directory. The HTML would arrive and every
asset under it would 404. `tests/test_web.py` fails if a relative path comes back.

`vercel.json` at the repository root carries the same mount, rewriting `/simulator/*` to `/*`,
so this directory would answer on the mount as well as at its own root if it were ever deployed
on its own. It is not today: quackd-web is the only Vercel project, and this config is here so
that a standalone deploy would not serve an unstyled page.

### The link back

The mount is one direction of a loop. quackd-web points at `/simulator` from five places — its
hero, its loop section, its try section, its footer and the nav — and lists that address in its
`sitemap.xml`, so a visitor can arrive here having never seen the product page: from a shared
link, from search, from an agent reading the sitemap.

This page answers in the header with one deliberate way back — the `What is this?` link, which
used to open the GitHub README and now opens the landing page, because a README is for somebody
who has already decided to care and this visitor has just watched a duck walk — plus the brand
lockup (the mark and the word `quackd`, wrapped in an anchor inside the `h1`, which stays an
`h1`), which is home on every site on the web and was inert here. Both are the absolute
`https://www.quackd.org/` rather than a bare `/`, because `/` here is the mount's parent in
production and, under `serve.py`, a redirect straight back to `/simulator/` — a relative link
would loop in development. `tests/test_web.py` fails if that link goes.

## Running it locally

There is no build step and no dependencies to install. It needs a server for two reasons:
browsers refuse ES modules over `file://`, and the page expects to be mounted at `/simulator`.
`web/serve.py` is that server — stdlib only, no arguments needed:

```bash
python web/serve.py
# then open http://localhost:8000/simulator/
```

A plain `python -m http.server --directory web` will serve the HTML and then 404 the
stylesheet and the script, because nothing answers on `/simulator` at the root.

## What loads, and from where

Nothing in this directory is a robot. Everything heavy is fetched by the visitor's browser
from whoever owns it, which is also how the licences stay clean — the Microduck's 3D model
files are CC BY-NC-SA and quackd redistributes none of them.

| What | From | Size |
|---|---|---|
| MuJoCo, compiled to WebAssembly | `@mujoco/mujoco` 3.12.0 on jsDelivr (Apache-2.0) | 10 MB |
| onnxruntime-web, to run the policy | jsDelivr (MIT) | 13 MB |
| three.js, to draw it | jsDelivr (MIT) | 0.7 MB |
| `robot_walk.xml` and 38 STL meshes | `pollen-robotics/microduck_rl` at a pinned commit | 22 MB |
| `alpha_walking.onnx`, `alpha_stand.onnx` | `pollen-robotics/microduck-policies` (Apache-2.0) | 1.6 MB |

About 45 MB the first time, cached by the browser afterwards. Both upstream hosts send
`Access-Control-Allow-Origin: *`, so no proxy is involved.

Two smaller origins joined that list with the restyle: `fonts.googleapis.com` serves the
stylesheet for Nunito Sans and DM Mono and `fonts.gstatic.com` the faces themselves. They are
named in `SECURITY.md` with the rest. Neither serves script, so neither can execute in the
page the way the jsDelivr tags can, and both families have a full fallback stack behind them
in `style.css`, so a visitor who blocks them loses the typeface and nothing else.

What the page does serve from this directory is three PNGs, and they are quackd's own:
`assets/duck-mark.png` is the mark in the header and on the loading panel,
`assets/favicon-96.png` is the tab icon and `assets/apple-touch-icon.png` the home-screen
one. They replaced a 🦆 emoji and an inline-SVG favicon built around the same emoji. There
are no other images: the arena is drawn with WebGL and the film grain is a data URI.

`assets/duck-mark.png` is this page's alone: the repository README opens without a logo, and
nothing else in the repository shows this one. `tests/test_web.py` checks that this page asks
for it.

Neither it nor `assets/favicon-96.png` is drawn here. Both are cut out of quackd-web's
`src/assets/duck-source.png` by [`make_mark.py`](make_mark.py), which is in this directory
because until 0.9 nothing recorded how they were made, and both had shipped with the bottom of
the duck's head sliced off at the image boundary. Run it after a checkout of quackd-web, and
fix that repository's own copies at the same time. `assets/apple-touch-icon.png` is not one of
its outputs: that head was already whole and it sits on a plate the script does not redraw.

## Your API key

It is read from an input, kept in a variable for the life of the tab, and sent straight to
the vendor from your browser. It is never stored, never logged, and there is no server here
to proxy it through. Every call is billed to you.

- **Anthropic** needs the `anthropic-dangerous-direct-browser-access: true` header, which
  the page sends. That header is exactly what its name says: your key is in a web page.
  Claude Opus 5.5, the default, and Claude Fable 5.1 answer a forced tool call with a 400,
  so the page asks them with `auto`, as the catalogue marks them, and moves any other Claude
  model there too if it answers the same way. A turn that comes back in prose rather than a
  call then ends the run, as it does for every vendor the page can only ask.
- **Gemini** and **OpenAI** work with their normal browser CORS.
- **Grok, Mistral, DeepSeek, Cohere, Qwen, Kimi and Meta** are all OpenAI-shaped, so each is
  the OpenAI client again with a different base URL, and each answered a CORS preflight from
  this page's origins when it was measured. What differs between them is how each is told to
  call a tool. Mistral spells "you must call a tool" as `any` rather than `required`, Cohere
  documents no `tool_choice` at all and so can only be asked rather than told, and Qwen, Kimi
  and Meta take `auto`. DeepSeek is asked with `required` and with its thinking turned off,
  because thinking mode refuses `required`. Those `tool_choice` values and DeepSeek's thinking
  switch are copied from the provider classes in `quackd/agent/providers/`. The page sends no
  `parallel_tool_calls`, which the CLI sends as false to OpenAI, Grok, Mistral and Meta, so a
  turn may come back with several calls, and the page takes the first.
- **OpenRouter** is OpenAI-shaped too, and a router: what the page sends reaches OpenRouter and
  the provider it picks for the model. The page asks it the way
  `quackd/agent/providers/openrouter.py` does. Every row is told to call a tool except the two
  Claude rows, which refuse a forced call and are asked with `auto`. Every request carries
  `provider: {"require_parameters": true}`, so OpenRouter routes only to an endpoint that
  honours what was asked, and `HTTP-Referer` and `X-OpenRouter-Title` headers that name quackd
  and say nothing about you. It is never moved to the Responses API, and a model's
  `reasoning_details` go back on the turn that made the call. The dropdown holds the six rows
  the catalogue carries. The CLI also takes any other id OpenRouter lists with tool calling,
  `:free` ones included, and the page does not. No OpenRouter model has answered a real quackd
  request, from this page or the CLI.
- **GLM is not offered here, and is on the CLI.** See below.
- **Some OpenAI reasoning models** refuse function tools on `/v1/chat/completions` and
  name `/v1/responses` in the 400. Every verb here is a function tool, so the page reads
  that answer, moves the run to the Responses API and stays there for the rest of it.
  `gpt-6-astra` is one such model and needs nothing set. This is the same switch
  `quackd/agent/providers/openai.py` makes for the CLI, kept in step by
  `test_the_browser_and_python_agree_on_which_400_means_responses`. Where the catalogue
  already records `api: "responses"` for a model, the page opens the run there and the failed
  call is never made. The 400 reader stays for every model it does not record, which is any id
  a vendor shipped after this build.
- **Local models** need no key. Ollama must be told to accept the page:
  `OLLAMA_ORIGINS=* ollama serve`. Any OpenAI-compatible server (llama.cpp, vLLM, LM Studio)
  works the same way — change the base URL. Browsers treat `http://localhost` as trustworthy,
  so an https page may call it.

## The model list, and where it comes from

The model field is a dropdown, not a text box. It is grouped by the catalogue's own statuses —
current, legacy, preview, specialised, open — and it opens on the vendor's default, so the
page offers exactly what `quackd list-models` does and a typo cannot reach a vendor at all.

`quackd/agent/providers/catalogue.py` is the only place a model is ever added. `web/src/catalogue.js`
is generated from it:

```bash
python web/build_catalogue.py
```

Committing the output is part of the change. `tests/test_web.py` re-runs the generator and
compares it against the committed file, so the two copies cannot drift the way the upstream
pins could before `test_the_browser_pins_the_same_upstream_commits_python_does` was written.

Every cloud vendor is in `catalogue.js`, including GLM, which the page cannot call. The data
stays one to one with Python; `PROVIDERS` in `src/providers.js` is what decides the dropdown.

Local is the exception and keeps its free-text box. A local server serves whatever you pulled,
so quackd has no list to offer and does not pretend to (ADR-0014).

## Which vendors this page can offer, and why GLM is not one

The CLI calls a vendor from your machine. This page calls it from your browser, which means a
vendor that refuses a cross-origin preflight cannot be offered here at all, however well it
works under `quackd run`. So it was measured rather than assumed, on **2026-09-12**, with an
`OPTIONS` preflight to each vendor's `/chat/completions` from both origins this page is served
from (`http://localhost:8000` and `https://www.quackd.org`), asking for `POST` with
`authorization` and `content-type`. OpenRouter was measured the same way on **2026-10-06**,
also asking for `http-referer` and `x-openrouter-title`, the two headers the page sends it:

| Vendor | Preflight | Verdict |
|---|---|---|
| Grok (`api.x.ai`) | `200`, `allow-origin: *`, `allow-headers: *` | offered |
| Mistral (`api.mistral.ai`) | `200`, `allow-origin: *`, `allow-headers: Authorization,Content-Type,...` | offered |
| DeepSeek (`api.deepseek.com`) | `200`, `allow-origin` echoes the origin, `allow-headers: authorization,content-type` | offered |
| Cohere (`api.cohere.ai`) | `200`, `allow-origin: *`, `allow-headers: Authorization, Content-Type` | offered |
| Qwen (`dashscope-intl.aliyuncs.com`) | `200`, `allow-origin` echoes the origin, `allow-headers: authorization,content-type` | offered |
| Kimi (`api.moonshot.ai`) | `204`, `allow-origin` echoes the origin, `allow-headers: authorization,content-type` | offered |
| Meta (`api.meta.ai`) | `200`, `allow-origin: *`, `allow-headers: *` | offered |
| OpenRouter (`openrouter.ai`) | `204`, `allow-origin: *`, `allow-headers` naming `Authorization`, `Content-Type`, `HTTP-Referer`, `X-Openrouter-Title` and `X-Title` among others | offered |
| **GLM (`api.z.ai`)** | `200`, and **no `Access-Control-Allow-*` header of any kind** | **not offered** |

**GLM is the one that failed.** Z.ai answers the preflight `200 OK` with `Vary: Origin` and
nothing else: no `Access-Control-Allow-Origin`, no `Access-Control-Allow-Headers`. A browser
reads that as a refusal and never sends the POST. The `POST` itself does carry
`Access-Control-Allow-Origin`, which is the confusing part, but a request bearing an
`Authorization` header and a JSON content type is always preflighted, so the browser never
reaches it. Nothing the page can do fixes that from this side. It needs a header from Z.ai.

GLM therefore stays on the CLI, where it works — `quackd run --llm glm` is unaffected —
and stays in `catalogue.js` so the two model lists remain identical. `tests/test_web.py` fails
if a vendor drops out of `PROVIDERS` without being named in this file, so this cannot happen
quietly to the next vendor.

These are measurements from one machine on two days. A vendor can add the header, or remove
it. Re-run the preflight before trusting the table.

## Two hands on the same duck

The sentence box and the keyboard are both live, always, and neither takes turns with the
other. There is no mode to flip before you can drive.

The keys write a twist — `vx, vy, wz` — and a head angle, which is the entire interface the
hardware has. The legend on the page is this same list, and the keycaps light up as you hold
them.

| Keys | What they do |
|---|---|
| `W` `S` | walk forward and back |
| `A` `D` | turn, or strafe with `Shift` held |
| `Q` `E` | look left and right, `G` centres the head |
| `Space` | stop. A latch, not a term in the twist: a key you are holding is dropped and has to be pressed again |
| `K` `R` | kick, and stand the duck up after a fall |
| `O` | read the raw state into the log, as JSON, without interrupting a run |
| `1` `2` | over the shoulder camera or duck cam, also without interrupting |
| `Esc` | hand the keyboard back to the arena from wherever focus is |

There is deliberately **no key for `say`**. Every other verb the model can pick has a key
beside it, and that one cannot: a key carries a command, and a sentence needs something to
read it. The absence is the argument the page is making, and it is the reason the keyboard
sits a centimetre from the box rather than behind a mode.

Underneath there are exactly two learned policies: `alpha_stand` stands the duck up and
`alpha_walking` walks it. The kick is quackd's own scripted impulse and not a policy, exactly
as in `sim3d`. None of it reads English.

## The switch, and what it decides now

`quackd is on` runs the loop: a system prompt carrying the contract, one verb per turn, an
executor that checks the verb against the allowlist before the robot moves, and a budget that
ends the run whatever the model thinks.

`quackd is off` removes that layer, and only that layer. The physics, the robot and its two
policies are identical; what is gone is anything that reads English, so typing a sentence
gets the honest answer the page prints — this robot understands a twist, three numbers, and a
walking policy that turns them into steps, and it has never seen your words. It is not a
rigged comparison against a worse model: there is no model,
because before quackd there was nowhere to put one.

What the switch no longer decides is whether you may drive. It used to: the cockpit was
hidden while quackd was on and the keydown handler returned early on the toggle's state, so
the demo's argument arrived as an either/or. The keyboard was never the layer, and it is not
gated on the layer any more.

## Barge-in

A key that would move the robot takes it, mid-run, at once. The run is aborted, the request
to the model is aborted with it — the signal reaches `fetch`, so nothing keeps running against
your key and no answer arrives after you took the duck back — and the transcript records the
handover with the key that did it. What that abort cannot promise is your bill: these are plain
non-streaming POSTs, so closing the connection stops the transfer, not necessarily a completion
the vendor has already generated. A key that only reads (`O`, and the camera keys) never barges
in: you can inspect the state or change the view without stopping the run. That is the whole rule — a key takes control if and only
if it would move the robot.

The handover itself is one flag, not a negotiation. `Runtime.start` re-asserts the hand's
twist every 20 ms control tick, immediately before the physics reads it, while the pilot
writes at most at 10 Hz between ticks, so setting `runtime.manual` synchronously in the
keydown handler is the handover; the abort that follows is bookkeeping. The invariant is
`runtime.manual === (running === null)`.

## What has been checked, and what has not

`web/src/microduck.js` and `web/src/pilot.js` were exercised under Node against the real
model and the real policy: the duck walks, the gait floor maps a twist the way the Python
backend does, a scripted pilot walks a square and closes it to 10 cm, and an unallowed verb
is refused while the run continues. That harness is a scratch script and is not in this
repository, so those four results are one measurement on one machine rather than something
you or CI can re-run — and both files have changed since, in the abort path and in the
arena's geometry, with nothing in this repository able to re-run it.

The page has been opened in a real browser twice, both times on the machine that wrote it and
neither time recorded. The first (commit `8d72a2a`): it boots with no page errors and a held
`W` walks the duck — `twist_sent [0.3, 0, 0]`, the pose moving with it. The second (commit
`a9fea18`), at `http://localhost:8020/simulator/`: it boots with no page errors and no failed
request, Nunito Sans applied and the duck mark loaded. So the rendering has run, the mount and
the restyle were watched rather than reasoned about, and somebody has held `W`.

What those two sessions did not cover is most of it. Nobody has watched a full model-driven
run, a barge-in *out of* a live run, the recording (Record, Save clip, Share), the switch
thrown mid-run, or the page on any browser, screen or machine but the one. The model dropdown
is newer than both sessions and has never been opened in a browser: that it groups by status,
fits the band beside the provider select at both layout tiers, and hands its place to the
free-text box on Local are all read rather than seen. Every vendor the page offers was exercised
under Node against a stubbed `fetch` — right base URL, right default model, right `tool_choice`
— which is not the same as a key and a real answer. OpenRouter's entry also sent its request
under Node over a real socket to a stand-in on 127.0.0.1 (`tests/fake_openrouter.py`), which
checks what the page sends and proves nothing about OpenRouter, and it has never been opened
in a browser. Nobody has run this page against any vendor but the three that were here before. Two clean boots are not a browser test, and none of it was
recorded, so the honest reading is that the page starts and the hand works, and everything
downstream of a model answering is still only read.

`tests/test_web.py` is the floor under that. It runs in the ordinary suite, with no browser:
every id the JavaScript looks up exists in the page, nothing it hides is pinned visible by a
rule, no module reached from the page imports a CDN statically, the key is stored nowhere,
each module parses under `node --check`, every asset the page asks for is on disk here, the
header carries the vendored mark rather than an emoji, the keydown handler reads nothing
about the switch, and nobody has crept back into the arena — no `person` body, no `person`
label out of `observe()`, no copy that promises one. It is not the same as driving the page.

## Where this differs from the Python backend

Deliberately, and none of it is a bug. This list is the canonical one: `README.md` and
`PLAN.md` point here rather than keeping counts of their own.

- **Seven verbs, and a contract of its own.** `pilot.js` implements `move`, `stop`,
  `report_state`, `gaze`, `kick`, `say` and `stand_up`, plus the two declarations — seven of
  the manifest's fifteen and none of the three composites, which is what Python's own prompt
  tells a model to prefer. `DEFAULT_CONTRACT` allows exactly those seven with 18 steps and 3
  minutes, so it is the same verb definitions and the same allowlist-and-budget machinery as
  Python, not the same allowlist and not the same budget as any `.duck` in `ducks/`.
- **No datasheet, and no feasibility gate.** Python hands its pilot the body as numbers and
  refuses every verb that moves until the model has answered `assess_task` — the release's
  headline guarantee, and the one on that list the page does not have. `pilot.js` is a
  second loop with its own hardcoded Microduck prompt and shares no code with the package,
  so the body section and the gate stopped at the language boundary. A page asked to carry
  something will try.
- **The arena is not upstream's scene.** `sim3d` builds upstream's own `scene*.xml` palette —
  a blue-grey edge-marked checker, a gradient skybox, upstream's lights. The browser fetches
  `robot_walk.xml` and none of the `scene*.xml` wrappers, so it draws a flat pale plane under
  a headlight with no sky. Same dimensions, same walls, same ball, and nobody in either of
  them; a screenshot of one does not look like `docs/assets/hero.gif`.
- **Perception is geometric.** The bearing and distance in each observation are read from the
  simulator's ground truth inside a 90 degree cone out to 1.6 m, with no occlusion modelled at
  all. Python renders the head camera and runs a colour detector over the pixels. The page says
  so in the transcript as well as here.
- **Nothing fetched is hash-checked.** Python verifies all 41 files against a recorded sha256
  before MuJoCo or onnxruntime sees a byte. The browser trusts the two hosts and the transport.
- **A seed means the same distributions, not the same layout.** The arena here is laid out by a
  xorshift and in Python by numpy's PCG64. The spawn ranges and the rejection rules match; the
  stream does not, so seed 3 is a different arena in each.
- **No `extra_body`.** The CLI can merge a JSON object into every request body, with
  `--extra-body` or `QUACKD_EXTRA_BODY`, for a field a server wants and quackd never sends.
  This page has no such door, so a model that has to be told something in the body, Qwen3 and
  its thinking being the one that came up, cannot be told it here.
- **There is no scripted pilot.** Python's `--llm fake` walks the whole task with no
  model. Here the pre-filled goal still needs a key, or a local server, before anything
  happens.
- **OpenRouter's six rows and no others.** The CLI takes any other id OpenRouter's public model
  list carries with tool calling, checked against that list when a run starts. The page offers
  the rows the catalogue carries in a dropdown, with no free text for any cloud vendor.

## Layout

| File | What it is |
|---|---|
| `index.html` | the page: the fonts, the onnxruntime tag, the copy, the keycap legend |
| `serve.py` | the mount, locally: stdlib only, serves this directory under `/simulator` the way the deploy does |
| `make_mark.py` | cuts `assets/duck-mark.png` and `assets/favicon-96.png` out of quackd-web's duck. Run it after that repository's art changes |
| `build_catalogue.py` | writes `src/catalogue.js` from Python's catalogue. Stdlib only. Run it after adding a model |
| `style.css` | hand-authored, in the quackd-web design language |
| `assets/` | quackd's own mark: the header logo, the favicon, the touch icon |
| `src/microduck.js` | the robot: MJCF, the 50 Hz loop, the policy, the gait floor |
| `src/pilot.js` | quackd itself: the clock, the verbs, the contract, the loop |
| `src/catalogue.js` | GENERATED. the model list, from `quackd/agent/providers/catalogue.py` |
| `src/providers.js` | one tool call from any cloud vendor the page offers or a local server |
| `src/view.js` | three.js built from the compiled model's own geoms |
| `src/record.js` | canvas capture, and the post the Share button writes |
| `src/app.js` | the page: the switch, the keyboard, the barge-in, the transcript |
