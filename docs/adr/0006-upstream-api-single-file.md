# ADR-0006: Every upstream assumption lives in one file, tagged VERIFIED or UNVERIFIED

**Status:** accepted, amended · **Date:** 2026-08-28

**Amended 2026-10-08 by [ADR-0051](0051-a-microduck-newer-than-the-read-is-served.md):** a
VERIFIED ref is what quackd read at one pin, and the Microduck's handshake no longer refuses a
robot on any other `duck-ipc-proto` version. It refuses one older than the oldest version read
and serves the rest, warning whenever the version is not the one read, because upstream refuses
a changed call by name rather than a peer by its number. The one kind of call nothing answers,
the `robot.move` notification, is checked against the robot's own state instead.

## Context

Microduck's architecture doc is a *draft* (2026-07-22) describing v1 direction; the shipped
daemon speaks `duck-ipc-proto` API v16 (read 2026-08-28). Some things we want (a WebSocket
agent gateway, `get_frame`, a feature stream) are designed but not built. Hardware ships at
Christmas 2026; nobody outside Pollen can verify against a robot today.

## Decision

- `quackd/transport/upstream_api.py` is the **only** module allowed to spell an upstream
  method name, socket path, enum value or wire convention. Each is an `UpstreamRef` with
  `status="VERIFIED"` (read from upstream source, link given) or `"UNVERIFIED"` (designed
  but not shipped, or our assumption) and a note.
- `tests/test_upstream_api.py` proves UNVERIFIED refs are referenced only from
  `transport/jsonrpc_unix.py`, `transport/websocket_stub.py` and `doctor.py`.
- `docs/transport-status.md` is the human-readable table of the same file.
- Transport tiers: `sim2d` ✅ default · `mock` ✅ tests · `jsonrpc` 🧪 experimental (verified
  method names, unverified end-to-end) · `websocket` ⏳ stub that raises with the doc link.
- Address forms for `jsonrpc`: `unix:///run/robotd.sock` on the robot (posix only) or
  `tcp://host:port` for an SSH forward (`ssh -L 9870:/run/robotd.sock robot`), which also
  works from Windows.

## What we verified (summary; the file has the details)

`hello{api_version}` handshake · `robot.move{vx,vy,vyaw}` as a *notification* with a deadman
· `robot.stop` · `robot.look{x,y,z,neck_pitch}` · `robot.do{skill}` with skills
`ground_pick|kick_left|kick_right|sit_toggle|roulade` · `robot.sound{tag}` with seven tags
and **no TTS** · `robot.subscribe`→`robot.state` · `robot.health` with `battery.percent` ·
socket paths and `DUCK_RUNTIME_DIR`.

## Consequences

- We never silently invent an upstream API. When upstream ships the WebSocket surface, the
  stub becomes real by editing one file and one doc.
- `quackd doctor` can print exactly which assumptions a hardware run relies on.
