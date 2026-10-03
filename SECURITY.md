# Security Policy

## Reporting

Please **do not** open a public issue for vulnerabilities. Email
**ksjeno@gmail.com** with "quackd security" in the subject, or use GitHub's private
vulnerability reporting on the repository if enabled. You will get an acknowledgement
within 72 hours.

## What "security" means when an LLM commands a robot

quackd sends *intents* to a robot. How much of the stopping the robot itself does depends
on the body, and each adapter declares it in its manifest's `safety_authority`
(see `docs/concepts/safety.md`). On a Microduck, `robotd` is the safety authority: it clamps
velocities, detects falls, and zeroes motion when commands stall. On an Open Duck Mini the
deadman is quackd's own daemon, running on the robot and zeroing the velocity inside the
50 Hz loop, so it is code we ship and therefore code we are answerable for. On the other
five bodies upstream has no deadman that covers the whole body. A rosbridge base
declares `native: none`, and a LeRobot arm has a torque limit on its gripper alone and holds
its last goal.
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
AlohaMini, a daemon that walks a ToddlerBot, and a daemon for an NVIDIA Jetson that quackd
reaches with `--host` and never runs on. So is `quackd policy serve`, which is not under
`bridge/` and is the first network service whose answers move an arm: it serves a learned
policy's goals to a LeRobot arm, from a checkpoint that is code.

Also in scope:

- API keys leaking into transcripts, GIFs, logs, run directories, or a robot's memory file.
  `TYPESAFE_API_KEY`, which the optional discrete stepper reads for the `jev` preset
  ([docs/guides/decision-llms/jev.md](docs/guides/decision-llms/jev.md)), is one of these. It is the
  only decision LLM quackd names that wants a key at all: every other one is a server you run
  yourself or a checkpoint in this process, and quackd hands those a placeholder
  in the key field rather than whatever hosted key happens to be sitting in the same `.env`.
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
  ([docs/guides/decision-llms/jev.md](docs/guides/decision-llms/jev.md)). Every other server row,
  `local` included, is a server you run, so it goes wherever `--decision-url` points, which is a
  port on your own machine unless you moved it. What each one binds and whether anything
  authenticates it is on its own page, and the two are not the same answer:
  [kev](docs/guides/decision-llms/kev.md) binds `127.0.0.1` and authenticates nothing, while
  [von](docs/guides/decision-llms/von.md) binds every interface unless you pass `--host`, which is
  why the catalogue's own command passes it. `laya` runs inside this process, so nothing leaves it
  at all ([docs/guides/decision-llms/laya.md](docs/guides/decision-llms/laya.md)). None of them is
  ever sent a camera frame, a system prompt or an API key. How much was sent is on the record, as
  `state_chars` and `state_tokens_est` on each `decision` event, and which fields were dropped to
  fit is there as `trimmed`; the text itself is not, so a reader auditing what left the machine is
  reading a size and a shape rather than the words. The address is on the record too, in
  `run_start.decision_llm`, with a password in it or a credential-named query parameter already
  replaced by `***`, because that url can arrive through `QUACKD_DECISION_URL` where argv redaction
  would never see it. What would be a security issue: a picture or a credential reaching any of
  them, a credential surviving that recorded url, a hosted key being sent to a server you run, or a
  stepper-authored call bypassing the executor.
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
  the vendor's own guard against exactly this. Use a key with a spend cap, or pick Local and
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
  that is missing, unreadable or empty refuses to start. The token is read from the
  `X-Quackd-Token` header only and compared with `hmac.compare_digest`, a request without it is
  refused on its headers alone, and what a client can make it hold is bounded as the Jetson host
  daemon bounds it, plus a cap on a request's body. Both ends refuse a token shorter than 16
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
- The recommended deployment for all of them is an ssh tunnel
  (`ssh -L 9871:127.0.0.1:9871 -L 9872:127.0.0.1:9872 -L 9873:127.0.0.1:9873 -L 9874:127.0.0.1:9874`,
  and `-L 9875:127.0.0.1:9875` for a policy server on another machine) rather than exposing
  any of these ports.

## Supported versions

Only the latest released minor version receives fixes.

The artifacts quackd ships for a robot or a board carry their own versions and live on
someone else's computer, so they can drift from the quackd that talks to them:
`BRIDGE_VERSION` and `CAMD_VERSION` on an Open Duck Mini's Raspberry Pi, `VERSION` in the
ToddlerBot daemon on its Jetson, `HOSTD_VERSION` in the host daemon on a Jetson that
`--host` names, and the AlohaMini host wrapper's `quackd_host_version` field. The two robot
daemons' handshakes carry a protocol version and refuse a mismatch rather than guessing, and
so does the host daemon's `/hello` (the AlohaMini wrapper only stamps its version into every
observation, and quackd does not read it yet), but a daemon you installed months ago is a
daemon that has not had your fixes. `quackd doctor --robot <adapter>:<backend> --address ...`
connects and shows what the robot reports about itself, though none of the robot side's
version strings is in that table yet. `quackd doctor --host` does show the host daemon's, in
its `daemon` row.
