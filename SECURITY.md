# Security Policy

## Reporting

Please **do not** open a public issue for vulnerabilities. Email
**ksjeno@gmail.com** with "quackd security" in the subject, or use GitHub's private
vulnerability reporting on the repository if enabled. You will get an acknowledgement
within 72 hours.

## What "security" means when an LLM commands a robot

quackd sends *intents* to a robot. How much of the stopping the robot itself does depends
on the body, and each adapter declares it in its manifest's `safety_authority`
(see `docs/concepts/safety.md`). On a Microduck, `robotd` is the safety authority: it holds
joint targets inside the actuators' travel, detects falls, and zeroes the velocity when
commands stall. On an Open Duck Mini the deadman is quackd's own daemon, running on the
robot and zeroing the velocity inside the 50 Hz loop, so it is code we ship and therefore
code we are answerable for. On a Huebot it will be the shim its maker is writing, which is
asked to keep robotd's deadman and which nobody has seen. The protocol has no
authentication, so the shim is asked to listen on a unix socket or on the robot's own
loopback, never on the network, and quackd reaches it through an ssh forward. On a MOSS rover the
firmware stops the tracks 300 ms after its last command, and quackd's own bridge on the rover sends
a twist only while quackd keeps asking for one and halts what quackd started when the heartbeat
stops, so that too is code we ship. On the other
five bodies upstream has no deadman that covers the whole body. A rosbridge base declares
`native: none`, and a LeRobot arm has a torque limit on its gripper alone and holds its last goal.
An XLeRobot's host watchdog zeroes its wheels and leaves the arms holding, and an
AlohaMini's covers the base and the lift and never the arms, so both are partial by
construction. A ToddlerBot has no watchdog, timeout or e-stop anywhere upstream at all, and
cannot get up if it falls, so its only deadman is the one quackd's own daemon runs. On all
five, quackd's own heartbeat and `stop` are most or all of what stops them. That makes the client-side
layer (verb allowlists, confirm gates, budgets, the heartbeat, the kill switch)
security-relevant, not just a convenience. A bug that lets an LLM or an MCP client bypass
it is a security issue.

quackd also ships code that runs **on a robot**, or on the board beside one, which is a
different kind of surface from everything above. Everything under `bridge/` is in scope in
its own right: two daemons for an Open Duck Mini v2's Raspberry Pi, a host wrapper for an
AlohaMini, a daemon that walks a ToddlerBot, a bridge beside MOSS's cockpit that drives its tracks
and plays its arm's cycle, and a daemon for an NVIDIA Jetson that quackd
reaches with `--host` and never runs on. So is `quackd policy serve`, which is not under
`bridge/` and is the first network service whose answers move an arm: it serves a learned
policy's goals to a LeRobot arm, from a checkpoint that is code.

Also in scope:

- API keys leaking into transcripts, GIFs, logs, run directories, or a robot's memory file.
  `TYPESAFE_API_KEY`, which the optional discrete stepper reads for the `jev` preset
  ([docs/guides/decision-llms/jev.md](docs/guides/decision-llms/jev.md)), is one of these, and so
  is `OPENAI_API_KEY` when the `openai` preset reads it
  ([docs/guides/decision-llms/openai.md](docs/guides/decision-llms/openai.md)). No other decision
  LLM quackd names wants a key at all: the rest are servers you run yourself or a checkpoint in this
  process, and quackd hands those a placeholder in the key field rather than whatever hosted key
  happens to be sitting in the same `.env`. The `openai` preset sends `OPENAI_API_KEY` to OpenAI's
  own API alone, `https://api.openai.com` or one of the regional hosts OpenAI list for the
  endpoint (`us.api.openai.com`, `eu.api.openai.com`, `au.api.openai.com`, `ca.api.openai.com`,
  `jp.api.openai.com`, `in.api.openai.com`, `sg.api.openai.com`, `kr.api.openai.com`,
  `gb.api.openai.com` and `ae.api.openai.com`), decided on the address's whole authority, so a
  user, a port other than 443 or a host that only resembles one of those is never sent it. It gives
  any other `--decision-url` the same placeholder, and its client follows no redirect, which would
  carry the request itself somewhere else. What it sends besides the key is the next item's rule,
  because it is built through the same module as the pilot's clients: `OPENAI_CUSTOM_HEADERS`,
  `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` go to OpenAI's own API exactly as the SDK sends them, and
  to any other `--decision-url` not at all.
- **What a pilot built on OpenAI's SDK sends besides the request.** `--llm openai`, every vendor
  that speaks OpenAI's API (Grok, Mistral, DeepSeek, Cohere, Qwen, Kimi, GLM, Meta and OpenRouter)
  and every local preset reach their address through that one SDK, and the SDK reads three variables
  of its own out of the environment and adds them to every request, wherever the client points:
  `OPENAI_CUSTOM_HEADERS`, one header per line, which is where a gateway's own credential lives for
  somebody who reaches OpenAI through one, and `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`, which it
  sends as `OpenAI-Organization` and `OpenAI-Project`. quackd decides where they go
  (`quackd/agent/providers/openai_headers.py`), and the rule is that they follow the SDK's own
  address variable. All three go, exactly as the SDK sends them by itself, to OpenAI's own API,
  `api.openai.com` or one of the regional hosts OpenAI list, over https, and to the address
  `OPENAI_BASE_URL` gives `--llm openai` when no `--base-url` is given, which is the address the SDK
  would have found for itself. A gateway named there is sent its own lines, an `Authorization` line
  among them, which the locked release sends in place of the key, and the account headers. None of
  the three goes anywhere else: not to a vendor's API, not to OpenRouter, not to a local preset's
  server, not to any other address given with `--base-url`, and not to a preset moved to a board
  with `--host`. A local preset reads `OPENAI_BASE_URL` too, as one rung of its own ladder, and its
  server is not the SDK's own address even then. At all of those no authorization goes on any
  request but the vendor's own key, under every spelling the variable gives an `Authorization` line
  of its own, a line that names `Accept`, `Content-Type`, `User-Agent` or one of the `X-Stainless-`
  lines that name the SDK's release and platform leaves the SDK's own value in place rather than
  taking it off with the line, and every other line and both account headers are left off. A gateway
  behind one of those that wants a header of its own has no way to be sent one yet. A local preset
  is sent the key `--api-key` or `LOCAL_API_KEY` gives it, or the word `not-needed`, and never
  `OPENAI_API_KEY`. The vendor's own headers still go, OpenRouter's two among them. The client
  follows no redirect, so a turn answered with one fails instead of sending the request on to
  wherever it points, and says where it pointed. And once a client is built its headers and its
  address are read back: a release of the SDK that would still send an authorization quackd did not
  write, or any other value under a name quackd answered, or whose client reads its address as
  OpenAI's where the address it was handed is not, or the other way round, is refused before the
  robot connects, naming the release and never the value. The key itself goes wherever the pilot is
  pointed, which is what `--base-url` is for, and `--llm openai` sends its key to whatever
  `OPENAI_BASE_URL` names, as the SDK always has, so a board named there is sent `OPENAI_API_KEY` in
  the clear: a model server you run is reached with a local preset, which sends it no hosted key.
  What would be a security issue: a line of `OPENAI_CUSTOM_HEADERS`, or `OPENAI_ORG_ID` or
  `OPENAI_PROJECT_ID`, reaching an address that is neither OpenAI's own API nor the one
  `OPENAI_BASE_URL` gives `--llm openai`, an authorization other than the provider's own key on any
  request to such an address, `OPENAI_API_KEY` reaching a local preset's server, a redirect
  followed, or a client built on OpenAI's SDK anywhere in quackd that does not go through that
  module.
- **The command line, which the run record now holds.** A solo run writes down what it was
  started with, in three places: `command` in the transcript's `run_start`, `command` in
  `summary.json`, and the first line of `terminal.txt`. A flock root has no `run_start`, so
  both flock runners write `command` and `version` into the root `summary.json` themselves,
  and the root `terminal.txt` opens with the same line; a pilot flock's members each keep a
  `run_start` of their own besides. The values of `--api-key`, `--token`, `--host-token` and
  `--policy-token` are replaced with `***` everywhere that line is written, so a reader sees
  that a key was passed and never what it was. The five flags that take a URL, `--base-url`,
  `--address`, `--camera-url`, `--decision-url` and `--policy-url`, keep the half a reader
  needs and lose the half that has to be rotated: the scheme, the host, the port, the path and
  the username stay, a password in the URL becomes `***`, and so does any query parameter named
  like a credential (`api_key`, `token`, `sig` and the rest of `SECRET_QUERY_KEYS`).
  `--extra-body`, and `QUACKD_EXTRA_BODY` behind it, is a JSON object a vendor asked for and
  quackd never reads, which makes it exactly where an `authorization` header ends up; it reaches
  the transcript's `run_start` as `extra_body`, and it is walked to the bottom on the way in
  with every credential-named key replaced. All of that is redaction **by name**, of flag names
  and of key names and nothing cleverer, which is worth stating plainly because it decides what
  is safe to paste into an issue. A secret typed as the value of some **other** flag is written
  down in full, and so is a credential a vendor asked for under a name these lists do not carry.
  A key handed to the provider through its own environment variable, which is the normal way and
  the right one, is in no part of the record, and `QUACKD_EXTRA_BODY` is the one environment
  variable that reaches it at all. What would be a security issue: the value of any of the four
  secret flags reaching any of the places above, a password or a named credential surviving a
  URL flag, a credential-named key surviving `extra_body`, or a new flag that takes a secret and
  is in neither `SECRET_FLAGS` nor `URL_FLAGS` (`quackd/command.py`).
- **What the discrete stepper is sent** (`quackd run --decision-llm`, off unless you name one,
  [docs/guides/decision-llms/README.md](docs/guides/decision-llms/README.md)). Whichever one answers
  is sent the same thing, once a turn: the task's goal, the robot's own description of itself and
  its last few results. Where that goes is the part that differs, and it is worth knowing which of
  the three you chose. `jev` is a third party's hosted API, so a run that names it sends that state
  over the network to TypeSafe
  ([docs/guides/decision-llms/jev.md](docs/guides/decision-llms/jev.md)), and `openai` is another,
  so a run that names it sends that state to OpenAI
  ([docs/guides/decision-llms/openai.md](docs/guides/decision-llms/openai.md)), whose data controls
  say it is not used for training and is kept in abuse-monitoring logs for 30 days. Every other
  server row, `local` included, is a server you run, so it goes wherever `--decision-url` points,
  which is a port on your own machine unless you moved it. What each one binds and whether anything
  authenticates it is on its own page, and the two are not the same answer:
  [kev](docs/guides/decision-llms/kev.md) binds `127.0.0.1` and authenticates nothing, while
  [von](docs/guides/decision-llms/von.md) binds every interface unless you pass `--host`, which is
  why the catalogue's own command passes it. `laya` runs inside this process, so nothing leaves it
  at all ([docs/guides/decision-llms/laya.md](docs/guides/decision-llms/laya.md)). None of them is
  ever sent a system prompt, and none is sent an API key but its own vendor's, at that vendor's
  address. The one way round that is the SDK's own: with no `--decision-url`, TypeSafe's client
  sends `jev`'s key wherever its own `TYPESAFE_BASE_URL` points, a variable quackd neither reads nor
  sets, so setting it is choosing where that key goes. A camera frame reaches a decision LLM only
  with `--decision-frames` (or `QUACKD_DECISION_FRAMES`), only for a row that can see, which is
  `openai` alone, and only at OpenAI's own hosts, the ones its key goes to: then that row is shown
  the robot's primary camera beside the state on each turn it is asked about, as one JPEG at most
  512 pixels on its longest side
  ([docs/guides/decision-llms/openai.md](docs/guides/decision-llms/openai.md)). A run that asks for
  frames with no decision LLM named, with any other row or a plugin, or with a `--decision-url` that
  is not one of those hosts, a server of your own included, stops before the robot connects, and
  `--no-vision`, which is about the pilot, changes nothing here. The record keeps what was sent:
  each frame's copy is written into the run directory as `frames/NNNN-decision.jpg`, or
  `kept: false` says the disk refused it, and a `decision_frame` line naming it with its size and
  its sha256 and never its bytes is written to the transcript and flushed before the request that
  carries it is handed over, so the record names it even when the process dies while the request is
  out. That turn's `decision` event names it again, whatever became of the request, and counts it as
  sent once handed over, though a connection refused or timed out first never carried it.
  `run_start.decision_llm.frames` says whether the run was set to show frames. On a Microduck the
  same flag gives the pilot `watch`, which hands that row one such frame a look, no more than two a
  second and sixty a call, beside the words the pilot described the thing in, and keeps each as
  `sent/watch-NNNN.jpg`, named with its size and its sha256 in a `watch_frame` line written through
  to the transcript before the request that carries it is handed to the client, so a frame `watch`
  sends with no such line naming it is a security issue
  ([microduck](docs/adapters/microduck/README.md#watch-a-head-that-keeps-something-in-view)).
  OpenAI's data controls say image inputs to that endpoint are scanned for child sexual abuse
  material when they are sent, and a flagged one kept for manual review even under zero data
  retention. How much was sent is on the record, as `state_chars` and `state_tokens_est` on each
  `decision` event, and which fields were dropped to fit is there as `trimmed`; the text itself is
  not, so a reader auditing what left the machine is reading a size and a shape rather than the
  words. That holds when a turn fails as well: its `error` is the exception's type, with its HTTP
  status where it carries one, and never the message, which can quote the request back. The only
  words kept are quackd's own: that the stepper's own second ran out, and what quackd says about a
  reply it could not read, which names the type of what came back and nothing it said. The address
  is on the record too, in `run_start.decision_llm`, with a password in it or a credential-named
  query parameter already replaced by `***`, because that url can arrive through
  `QUACKD_DECISION_URL` where argv redaction would never see it. What would be a security issue: a
  picture reaching a decision LLM on a run that did not ask for frames, or reaching any row but
  `openai` or any address but OpenAI's own hosts, a frame handed over with no line on the record
  naming it, image bytes in the state or in the record, a credential reaching any of them but its
  own vendor at its own address, a credential surviving that recorded url, a hosted key being sent
  to a server you run, or a stepper-authored call bypassing the executor.
- **The memory file** (`~/.quackd/memory/<adapter>-<backend>.jsonl`). It holds
  sentences a model wrote about a place it has been, it persists between runs, and it is
  read back into the next system prompt. It never leaves the machine and the executor never
  reads it, so a note cannot widen an allowlist, lift a budget or open a confirm gate. What
  would be a security issue: memory reaching the executor, a note from one robot appearing
  in another robot's prompt, or the file escaping the directory `--memory-dir` names.
  `--no-memory` writes nothing at all, and `quackd memory clear` deletes the file.
- **The robot registry** (`~/.quackd/robots.json`, `~/.quackd/flocks.json`). `quackd robot
  add --token ...` writes that token to disk **in plain text**, and `--host-token ...` does the
  same with a board's, which is the honest trade for not having either in shell history on
  every run. It is a file in your home directory, not a secret store: if that is not good
  enough for your robot, keep passing `--token` on the line or through `QUACKD_DUCK_TOKEN`,
  and `--host-token` or `QUACKD_HOST_TOKEN` for a board. quackd masks both in everything it
  prints, `--json` included, which reports only whether each is set (`token_set` and
  `host_token_set`). What would be a security issue: a token reaching a transcript, a log
  line, a run directory or an MCP tool result, or either file escaping the directory
  `--registry-dir` names.
- The MCP server executing verbs a loaded `.duck` contract does not allow.
- Anything that lets a `.duck` file (untrusted input — people will share them) execute
  code, read files, or reach the network.
- An adapter sending a body's "go limp" call (`robot.relax`, `disable_motors`,
  `disable_torque`, an XLeRobot `disconnect()`, a ToddlerBot torque-off) as if it were
  `stop`. Stop means stop, never collapse.
- Anything that lets an LLM, an MCP client or a `.duck` file take torque off a LeRobot arm.
  `quackd robot release` and the offer a run makes at its own terminal when its last rest move
  missed both release the arm wherever it stands, and exist only for a person holding it: the
  command asks at a terminal unless `--yes` is on its command line, and the offer waits for
  Enter and is never made on a dry run, over MCP or in a flock. Neither is a verb, an MCP tool
  or on the `RobotAdapter` protocol. A way round any of that is in scope.
- **The browser demo** (`web/`), which is now publicly reachable at
  <https://www.quackd.org/simulator> rather than only a directory you serve yourself. That
  changes the assessment: anybody can be linked to a page that asks them to paste an API key.
  The key is read from an input, sent from the browser straight to the vendor, and never
  stored, never logged and never proxied: there is no server here to proxy it through, and
  nothing in `web/src` writes to browser storage. The vendors a key can reach are exactly the
  entries of `PROVIDERS` in `web/src/providers.js`, each one contacted only once the visitor
  picks it, and the model list offered beside them is generated from the same catalogue the CLI
  ships (`web/src/catalogue.js`, written by `web/build_catalogue.py`), so the page cannot name
  an address or a model this repository does not. What that leaves is the page itself. It
  loads three payloads from `cdn.jsdelivr.net` at pinned versions, plus a stylesheet from
  `fonts.googleapis.com` and the two webfonts it names from `fonts.gstatic.com`, all with no
  subresource integrity and no content security policy, and any script running in the page can
  read that input. Google Fonts serves CSS and font files rather than script, so it cannot
  execute in the document the way the jsDelivr tags can — but it is still an origin that sees
  every visit. So the risk is not quackd holding your key, it is a third party executing in the
  same document as it: a bad CDN response, an injected script, or a copy of the page served
  from somewhere you do not control. <https://www.quackd.org/simulator> is the one copy quackd
  controls, and `/simulator/source.json` names the commit it was built from, so you can check
  it against this repository; the same files served from anywhere else are somebody else's and
  can differ from what is here. Being deployed adds nowhere for a key to be kept: the deployed
  copy is the same static files, fetched into quackd-web's build at a pinned commit, so no
  quackd server sits between the input and the vendor there either. Anthropic's
  `anthropic-dangerous-direct-browser-access` header, which the page sends, is opting out of
  the vendor's own guard against exactly this. OpenRouter is a router, so what a visitor
  sends it also reaches the provider it picks for the model, and the page names quackd to it
  in two headers (`HTTP-Referer` and `X-OpenRouter-Title`) and says nothing about the visitor.
  Use a key with a spend cap, or pick Local and
  nothing leaves the machine. What the demo cannot do: it is a simulation with no transport to
  any robot, so nothing in it moves hardware.
- **The model and the policies the physics backend fetches** (`adapters/microduck/src/quackd_microduck/sim3d/assets.py`).
  `--robot microduck:mujoco` downloads upstream's MJCF and 38 meshes from codeload.github.com
  and two ONNX policies from huggingface.co, both pinned, and then runs the policy. The defences
  are worth naming because they are the answer: only paths in a fixed allowlist are extracted
  from the tarball, so a crafted archive cannot write outside the cache, and every file is
  checked against a recorded sha256 before MuJoCo or onnxruntime sees it, so a substituted mesh
  or policy fails the run instead of loading. Both are tested rather than merely claimed:
  `tests/test_sim3d_assets.py` builds hostile archives — a traversal path, a sibling directory,
  a symlink, a tampered mesh — and asserts that none of them lands. An ONNX file is data that
  onnxruntime parses, not Python that quackd executes, so the exposure is that parser and not
  arbitrary code. The one
  path around the hashes is deliberate: `QUACKD_MICRODUCK_ASSETS` warns rather than refuses,
  because a newer export from your own checkout is the point of it. Point it at a checkout you
  built, never at one you were sent.
- **The model the arm's simulator fetches** (`adapters/lerobot/src/quackd_lerobot/sim/assets.py`).
  `--robot lerobot:mujoco`, and any robot `quackd robot twin` registered, downloads the SO-101's
  model from TheRobotStudio/SO-ARM100 the first time it connects: `so101_new_calib_camera.xml`
  and the 15 STL meshes it names, about 16 MB, one file at a time from raw.githubusercontent.com
  at a pinned commit. There is no archive to unpack. Every URL is built from a fixed list of names
  quackd holds, every file is written under a name on that list, so nothing upstream sends can
  choose a path, and each is checked against a recorded sha256 as it arrives, so the first file
  that does not match stops the fetch and nothing is installed. A reply larger than 16 MiB, or
  shorter than the length it declared, is refused as well. The set is checked again, installed
  into `~/.quackd/cache/so-arm100/<pin>` in one rename under a lock, and checked against the same
  hashes every time it is used, so a file changed on disk is fetched again rather than loaded.
  `QUACKD_CACHE_DIR` moves the cache. The model includes no other file, and MJCF and STL are data
  that MuJoCo parses rather than code quackd runs, so the exposure is MuJoCo's parser.
  `tests/test_lerobot_sim_assets.py` drives all of it with the network stubbed, a file that does
  not match its pin and an oversized reply among the cases. The one path around the hashes is
  deliberate, as it is for the duck: `QUACKD_LEROBOT_SIM_ASSETS` points at the `Simulation/SO101`
  directory of a checkout of your own, and warns rather than refuses when it differs from the
  pin. Point it at a checkout you made, never at one you were sent. The simulator itself opens no
  port and imports no LeRobot, and an address shaped like a serial port is refused before
  anything looks at it, so nothing it is given reaches a real arm.
- The LAN surfaces behind `quackd[lan]`: zeroconf TXT records advertise a robot's identity
  to anything on the network, and the MQTT flock bus carries messages that command robots
  with no authentication of its own. Both are off by default and neither has a threat model
  yet, so treat them as trusted-network only.
- **Robot Passport** ([docs/passport/security.md](docs/passport/security.md) is its whole threat
  model). `quackd passport serve` listens on every interface on port 8790 unless told otherwise,
  because a passport is a public document meant to be read, and it serves passports and nothing
  else: no other path answers and it fetches nothing. What any client can make it hold is
  bounded: 32 connections at once, five seconds a read and ten a connection, and a POST must say
  how long it is. A passport never holds a token or a key. Each robot's private key, and the owner
  key that stamps them, the one to guard most, are plain files under `~/.quackd/keys/`, on the same
  footing as the tokens in `robots.json`: readable by their owner alone on Linux and macOS, and on
  Windows by whoever can read the folder they are in. Reading a passport fetches only the URL it
  was given, follows no redirects, reads no more than 12 KB in five seconds however slowly a host
  sends, and prints whatever a passport says as text, so a passport cannot drive the terminal.
  `quackd meet ros://` opens a bridge, reads one message and closes it without publishing
  anything to that robot. With `--talk`, `quackd passport serve`, `quackd run` and
  `quackd serve-mcp` also take talk messages at `/robot-passport/inbox`, and `serve --mqtt
  --talk` on each robot's inbox topic on that broker: a three-step `hello`,
  `say` and `bye`, each signed by the robot that sends it, at most 16 KB, with at most 64
  sessions and 64 half-finished hellos at once. Nothing a robot says moves anything. An `ask` is
  decided by the courtesy handler, never the pilot: a pause of 5 s at most for a stranger and 10 s
  for a known robot, an identify by sound, one pause a minute and one identify every ten seconds
  across every peer together, run through the executor with the source `peer`, where the `peer` gate
  admits only `stop` and `say`. A pause ends any verb already moving the body, and the `paused` gate
  admits nothing that moves while it holds. A run takes courtesies only while its pilot has the
  body, never over an arm in a person's hands, and they cost the pilot no steps. A peer can never
  ask for `stop`, and a reply goes only to the host the other robot's passport or hello came from. A
  stranger's words reach the terminal and the transcript, for a person, and never a pilot or an MCP
  client. What would be a security issue: a key, the owner key above all, reaching any output, log,
  transcript or passport, a passport's text acting on the terminal it is shown on, a fetch to any
  host but the one named, a robot placed in a tier above stranger without the pin or the stamp that
  tier needs, a stranger's words reaching a pilot or an MCP client, a peer reaching any verb but
  `stop` and `say`, or a body moving while a granted pause holds.
- **The Jetson host daemon** (`bridge/jetson/quackd_jetson_hostd.py`,
  [docs/guides/jetson.md](docs/guides/jetson.md)), an HTTP server on port 9874 on a board that
  quackd reaches with `--host` and never runs on. It serves a live view from a camera on the board,
  runs YOLO on any JPEG it is sent, and hands out the board's own files, the output of
  `nvpmodel -q` and one line of `tegrastats`. It binds loopback by default and warns when it is
  bound wider with no token. A token, once one is configured, is required on every path, read
  from the `X-Quackd-Token` header and never from the query string, and compared with
  `hmac.compare_digest`. A `--token-file` that is named and missing, unreadable or empty refuses
  to start rather than run with authentication off. A request without the token is refused on
  its headers alone, and what any client can make the board hold is bounded: 16 connections at
  once, 32 KB of headers, 10 seconds for a request to arrive whole, and two `POST /detect`
  bodies at a time. It is plain HTTP, so the token and every
  frame cross the network in the clear unless a tunnel carries them. There is no control path
  in it: it answers GET, and POST on `/detect` alone, and a test fails if that changes. So the
  worst a peer the daemon lets in can do is watch the room and keep the board busy, and
  `POST /detect` is that second thing: it spends the board's GPU on whatever it is sent, for
  anyone who can reach the port when there is no token, and on a robot's own board that is the
  GPU and the memory its control loop shares. On the laptop, quackd never puts the token in a
  URL, a transcript, a run's command line or `--json`, it scrubs the token out of every reply
  before an error or `quackd doctor` can repeat it, and it ignores `HTTP_PROXY` and follows no
  redirect, since either would carry the token header somewhere other than the board. The
  model server that `--host` also reaches is your own install on the board and authenticates
  nothing: keep Ollama's `OLLAMA_HOST` on `127.0.0.1:11434` and reach it and the daemon from
  the laptop with `ssh -L 9874:127.0.0.1:9874 -L 11434:127.0.0.1:11434`, the way you reach a
  robot's bridge. What would be a security issue: a way to move anything through the daemon, a
  request served without the token when one is configured, a client without the token making
  the daemon keep what it sent or a thread past those bounds, the token reaching a URL or any
  record on the laptop, or a password in a camera pipeline reaching a reply or a log. None of
  it has been run on a Jetson by this project, so treat the arrangement as reviewed rather
  than proven.
- **The policy server** (`quackd policy serve`, `adapters/lerobot/src/quackd_lerobot/policy/`,
  [docs/guides/policies.md](docs/guides/policies.md)), an HTTP server on port 9875 whose answers
  move an arm: it serves the goals a learned policy chooses, and the arm's process sends them. It
  runs as a process of its own, never the one that owns the arm's serial bus, because a LeRobot
  checkpoint's processors can name code to import. It binds `127.0.0.1` or `::1`, and refuses
  any other address, the rest of 127/8 included, unless `--behind-tls` says a TLS proxy stands
  in front of it. A token is always required: with no `--token-file` it writes one to
  `~/.quackd/policy.token`, readable by its owner alone where the OS allows, and a named file
  that is missing, unreadable or empty refuses to start, as does a `--token-env` variable that
  holds none. The token is read from the `X-Quackd-Token` header only and compared with
  `hmac.compare_digest`, a request without it is refused on its headers alone, and what a client
  can make it hold is bounded as the Jetson host daemon bounds it, plus a cap on a request's
  body. The one route answered without it is `GET /v1/health`, which says whether the server
  serves and nothing more, for a platform's readiness probe, and reads no body and touches no
  session. A request that carries an `Authorization` header is logged once, as a warning, and the
  header's value never. Both ends refuse a token shorter than 16
  characters or with whitespace inside it, without quoting it. Every number in a message is
  checked to be finite on both sides, what a server says about itself in words is printable
  ASCII or refused, a reset from a second client is refused while another client's session is in
  use, and a step for an ended session is refused. The client, in the arm's process, sends plain
  HTTP only to `127.0.0.1` and `::1`, so a policy on another machine is reached through
  `ssh -L 9875:127.0.0.1:9875` or over `https://` with the certificate verified. It follows no
  proxy and no redirect, keeps the token out of every error, and holds every reply to a deadline
  however slowly it arrives, then caps and validates it before a goal in it reaches the arm. It
  loads a LeRobot checkpoint (`policy/pipeline.py`), whose processors can name code to import,
  so it fetches `config.json` and both processor JSONs first, at the revision named, and refuses
  a step named by a `class` key or by any registry name outside the ones ACT's, SmolVLA's and
  pi05's processors use, before any weights are fetched. It tells a step that could trust a
  repository's own code not to, and refuses a model the checkpoint names inside itself unless
  `--pin REPO@REVISION` fixes its revision at a whole commit or a tag, fetching it with no `.py`
  and no pickle, and refusing it if its directory in the Hub's cache holds anything but configs,
  tokenizer files and safetensors all the same, or maps a class to code (`auto_map`). A SmolVLA
  that names no backbone is refused, since LeRobot would load a default of its own, and every
  weight is loaded strictly, so a checkpoint is never served as a random network. Only a tiny
  random ACT has been loaded by it, in CI. The arm checks at connect, before any torque, that
  what the server says it serves fits the arm, and again as every segment starts, so a server
  started again with another policy drives nothing. What would be a security issue: a goal
  reaching the arm from anything but the server the arm was pointed at, a request served without
  the token, a number that is not finite getting through, the token reaching a URL or any
  record, a server's words reaching a terminal with an escape in them, a client without the
  token making the server keep what it sent or a thread past its bounds, a checkpoint getting
  code imported by a path it chose or a repository's own code trusted, a model a checkpoint
  names loading at a revision nobody pinned, or a policy the arm's connect did not check
  starting a segment. No quackd command loads a checkpoint in the arm's own process.
  `load_policy()` in the arm's backend, an older Python helper that nothing in quackd calls,
  still would, with none of the checks above and no check at connect, as the `LOAD_POLICY` row in
  [docs/adapters/lerobot/README.md](docs/adapters/lerobot/README.md#the-policies-upstream-lerobot-061)
  says. Whether to remove it is an open item in PLAN.md.
- **The policy server on a Hugging Face Endpoint** (`quackd policy endpoint`,
  `adapters/lerobot/src/quackd_lerobot/policy/endpoint.py`,
  [docs/guides/policies.md](docs/guides/policies.md#on-a-hugging-face-endpoint)), which rents a
  GPU that bills by the hour and runs that same server on it. quackd has never created one: it
  has run against the test suite's stand-ins alone. It asks before it creates, resumes or wakes
  one, and with no terminal and no `--yes` it refuses before anything exists. The endpoint is
  `authenticated`, which Hugging Face's own page says any Hugging Face account can reach, so the
  lock is the server's own token, made by quackd and sent only as the endpoint's secret
  `QUACKD_POLICY_TOKEN` and kept in `~/.quackd/endpoints.json`, readable by its owner alone where
  the OS allows. The account's Hugging Face token goes to huggingface.co and
  api.endpoints.huggingface.cloud, to manage the account's endpoints, and as the bearer to the one
  endpoint address that machine's `up` recorded, whose whole authority has to be an endpoint's,
  over https on port 443, and only once the API, asked with that token, has said the endpoint by
  that name on the recorded account carries the tags `up` gave it, quackd's and one made from the
  record's token that no other endpoint carries, at that address. A run reads the record only from
  a registry the shell or `--registry-dir` names: a `QUACKD_REGISTRY_DIR` that came from a
  project's `.env` is refused before anything is asked, since that `.env` could set an account
  token of its own beside a record of its own and choose which endpoint's policy drives the arm. A
  record outlives an endpoint deleted on huggingface.co, so whatever record is read, the token
  goes to no address the API has not confirmed, and `up`, `pause` and `delete` make the same read
  and leave alone an endpoint it does not confirm. That read trusts Hugging Face to answer for an
  account's endpoints only to that account and the members of its organisations, which nothing it
  publishes says (the `NAMESPACE_READS` row of
  [the endpoint's table](docs/adapters/lerobot/README.md#the-endpoints-upstream-hugging-face-inference-endpoints)),
  so an organisation the account belongs to is trusted as the account is. The token goes to
  nothing else: not into the endpoint, not to PyPI and not to the price list, which is asked with
  no token. No variable moves where it is sent. A Hub token for a gated checkpoint, named by
  `--hub-token-from`, goes only into the endpoint's secrets and to huggingface.co, and is refused
  when it is the account's own. A record whose address is not an endpoint's is refused, and every
  log line or refusal quoted has every token taken out. What would be a security issue: the
  account's token reaching any other host, an address the API has not confirmed, the endpoint, a
  record or the terminal, the server's token reaching a URL, a run's record or the terminal, an
  endpoint created, resumed or woken by `up` without a yes, or resumed, paused or deleted when
  quackd did not start it, created `public` or with more than one replica, or one that never
  scales to zero.
- **The bridge daemon** (`bridge/open_duck/quackd_duck_bridge.py`), a TCP listener on port
  9871 that walks a 42 cm biped. It binds loopback by default and compares a token with
  `hmac.compare_digest`, but a token is only required if one is configured, and binding it
  wide only warns. Anything that lets an unauthenticated peer move the robot, defeat the
  300 ms deadman, exceed the clamps, or reach past the seven floats the protocol exposes is
  a security issue. Going limp is currently unreachable by construction, and it should stay
  that way: there is no method in the protocol that touches torque.
- **The camera daemon** (`bridge/open_duck/quackd_duck_camd.py`), an HTTP server on port
  9872 that serves a live view of wherever the robot is, with **no authentication at all**.
  It binds loopback by default and warns when told otherwise. Reach it through an ssh
  tunnel. If you bind it wide, everyone on that network can watch your home.
- **The ToddlerBot daemon** (`bridge/toddlerbot/quackd_toddlerbot_bridge.py`), a TCP
  listener on port 9873 that drives a 3 kg humanoid which cannot get up if it falls. It
  binds loopback by default, compares its token with `hmac.compare_digest`, and requires one
  only if one is configured. It is a larger surface than the Open Duck's because it owns the
  50 Hz loop rather than feeding one: anything that lets an unauthenticated peer move the
  robot, defeat the 500 ms deadman, exceed the joint clamps or the per-tick rate limit, or
  reach past the methods the protocol exposes is a security issue. As with the Open Duck,
  going limp is unreachable by construction and must stay that way, and here it matters
  more: torque off on this body means the robot falls over.
- **The MOSS bridge** (`bridge/moss/quackd_moss_bridge.py`), an HTTP server on port 9876 that
  drives a tracked rover, 4.2 kg by its maker's figure for the V0.3 build, and plays the cycle
  of its arm. It binds loopback by default, compares its token with `hmac.compare_digest`,
  refuses to start on a named token file that is missing or empty, and warns when it is bound
  wide with no token. On `moss-litter` its stop also ends the mission's own run of the maker's
  arm script, with SIGTERM and then SIGKILL, and it signals no other process. Each quackd session
  is named by an id its client draws at random, and its keepalive, end and halt touch only that
  session. Anything that lets an unauthenticated peer move the tracks or the arm, defeat its
  twist deadman or a session's halt, or reach past the routes its protocol exposes is a security
  issue, and so is any path that sends MOSS's own `/cmd/stack`, whose stop ends every dimOS run
  and turns the arm's torque off. MOSS's own cockpit, on port 8904, is not quackd's code and has
  **no authentication at all**: it binds `0.0.0.0`, and anything that reaches it reads the rover
  and commands it. Reach both through an ssh tunnel.
- The recommended deployment for all of them is an ssh tunnel
  (`ssh -L 9871:127.0.0.1:9871 -L 9872:127.0.0.1:9872 -L 9873:127.0.0.1:9873 -L 9874:127.0.0.1:9874`,
  `-L 9875:127.0.0.1:9875` for a policy server on another machine, and
  `-L 8904:127.0.0.1:8904 -L 9876:127.0.0.1:9876` for a MOSS rover) rather than exposing
  any of these ports.

## Supported versions

Only the latest released minor version receives fixes.

The artifacts quackd ships for a robot or a board carry their own versions and live on
someone else's computer, so they can drift from the quackd that talks to them:
`BRIDGE_VERSION` and `CAMD_VERSION` on an Open Duck Mini's Raspberry Pi, `VERSION` in the
ToddlerBot daemon on its Jetson, `HOSTD_VERSION` in the host daemon on a Jetson that
`--host` names, `BRIDGE_VERSION` in quackd's bridge on a MOSS rover, and the AlohaMini host
wrapper's `quackd_host_version` field. The two robot daemons' handshakes carry a protocol
version and refuse a mismatch rather than guessing, and so do the MOSS bridge's `/hello` and the
host daemon's `/hello` (the AlohaMini wrapper only stamps its version into every
observation, and quackd does not read it yet), but a daemon you installed months ago is a
daemon that has not had your fixes. `quackd doctor --robot <adapter>:<backend> --address ...`
connects and shows what the robot reports about itself, though none of the robot side's
version strings is in that table yet. `quackd doctor --host` does show the host daemon's, in
its `daemon` row.
