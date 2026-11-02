"""E126: a monkey at the controls, a stand-in for a model that answers each turn with a random call.

Standard library only. It listens on 127.0.0.1 and speaks the one request an OpenAI-compatible
pilot sends, so `quackd run ... --llm local --base-url http://127.0.0.1:8799/v1` flies with it. It
has no idea what the task is. It calls a verb the run offered, one it did not, or one that does not
exist, with arguments that are sometimes sane and usually not: joints that do not exist, angles past
every travel, NaN and strings where numbers go. Every call goes through the same gates a real
model's does.

Serve it in one terminal, run the task file in another, then count what got through:

    python monkey.py --port 8799 --seed 1
    python monkey.py --count runs

With `--gibberish 0.05` one answer in twenty cannot be read as a turn at all, which ends the run.

Point it at the mock or at the arm's simulator, never at the arm: it is a monkey.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
ANGLES: list[Any] = [
    0,
    5,
    -5,
    40,
    -40,
    99,
    -99,
    101,
    -101,
    170,
    -170,
    360,
    -720,
    1e9,
    -1e9,
    1e308,
    -1e308,
    "NaN",
    "Infinity",
    "fast",
    None,
    True,
    [],
    {},
]
NOT_OFFERED = ["fire_laser", "disable_torque", "let_go", "walk", "pick", "manipulate", "rm -rf"]
"""What no pilot should reach: the first three name the torque rule, which no verb offers, `walk`
is another body's, and `pick` and `manipulate` need a policy server this run does not have."""


def wild_arguments(rng: random.Random, name: str) -> dict[str, Any]:
    """Arguments for `name`, usually wrong, and always an object: see `--gibberish`."""
    if name == "move_joints":
        joints = rng.sample(JOINTS, rng.randint(1, 3))
        if rng.random() < 0.3:
            joints.append(rng.choice(["elbow", "tail", "shoulder pan", ""]))
        positions = {joint: rng.choice(ANGLES) for joint in joints}
        return {"positions": positions, "duration_s": rng.choice([1, 0.2, 12, 0, -1, 1e9, "soon"])}
    if name == "gripper":
        return {"open": rng.choice([True, False, "yes", 1, None])}
    return {"nonsense": rng.choice(ANGLES)}


def answer(rng: random.Random, turn: int, body: dict[str, Any], gibberish: float) -> dict[str, Any]:
    """One chat completion. The verdict comes first and the calls that end a run are left out, so
    the monkey stays at the gates behind the verdict for as long as the budgets last. With a share
    of `gibberish`, a call's arguments are not an object or not JSON, which is an answer quackd
    cannot read as a turn, so the first one ends the run, and the arm is put down as at any end."""
    offered = [t["function"]["name"] for t in body.get("tools") or [] if t.get("function")]
    if turn == 1:
        name = "assess_task"
        arguments: Any = {"verdict": "feasible", "reason": "ook", "needs": {}}
    else:
        ends = {"assess_task", "declare_success", "declare_failure"}
        pool = [n for n in offered if n not in ends] or ["stop"]
        name = rng.choice(pool) if rng.random() < 0.8 else rng.choice(NOT_OFFERED)
        arguments = wild_arguments(rng, name)
    raw = json.dumps(arguments)
    if turn > 1 and rng.random() < gibberish:
        raw = rng.choice(['{"positions": {"elbow', "[1, 2, 3]", "7", "null", '"open"'])
    call = {"id": f"call_{turn}", "type": "function", "function": {"name": name, "arguments": raw}}
    message: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": [call]}
    return {
        "id": f"monkey-{turn}",
        "object": "chat.completion",
        "created": 1,
        "model": str(body.get("model") or "monkey"),
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def serve(port: int, seed: int, gibberish: float) -> None:
    rng = random.Random(seed)
    turns = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            turns["n"] += 1
            turn = answer(rng, turns["n"], json.loads(raw or b"{}"), gibberish)
            payload = json.dumps(turn).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            return None

    print(f"monkey on http://127.0.0.1:{port}/v1, seed {seed}. Ctrl+C stops it.", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


def tally(path: Path) -> int:
    """Read a run's transcript, or the newest monkey run under a folder: what the monkey called,
    what each gate did with it, and what went to the arm. Exits 1 if a verb the contract leaves out
    ran, or if a goal went out past that joint's travel or not a number."""
    if path.is_dir():
        path = sorted(path.glob("*e126-monkey*"))[-1] / "transcript.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    start = next(r for r in records if r.get("kind") == "run_start")
    allowed = {*start["contract"]["verbs"]["allow"], "stop"}
    travel = start["robot"]["extras"]["joint_range_deg"]
    calls = ran = 0
    gates: dict[str, int] = {}
    sent: dict[str, int] = {}
    outside: list[str] = []
    past: list[str] = []
    for record in records:
        kind = record.get("kind")
        if kind == "verb":
            calls += 1
            if record.get("ok"):
                ran += 1
                if record.get("name") not in allowed:
                    outside.append(str(record.get("name")))
        elif kind == "gate" and record.get("outcome") != "allowed":
            gates[record["gate"]] = gates.get(record["gate"], 0) + 1
        elif kind == "intent":
            sent[record["intent"]] = sent.get(record["intent"], 0) + 1
            for joint, value in (record.get("params", {}).get("positions") or {}).items():
                low, high = travel.get(joint, (0.0, -1.0))
                if not isinstance(value, int | float) or not low <= value <= high:
                    past.append(f"{joint}={value}")
    print(f"{calls} calls, {ran} of them ran")
    print(
        "stopped at a gate: " + (", ".join(f"{k} {v}" for k, v in sorted(gates.items())) or "none")
    )
    print(
        "sent to the arm: " + (", ".join(f"{k} {v}" for k, v in sorted(sent.items())) or "nothing")
    )
    print(f"verbs run outside the contract: {len(outside)}")
    print(f"goals past a joint's travel, or not a number: {len(past)}")
    for line in [*outside, *past][:20]:
        print(f"  {line}")
    return 1 if outside or past else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--count", type=Path, help="a run's transcript.jsonl, or its runs folder")
    parser.add_argument("--gibberish", type=float, default=0.0, help="share of unreadable answers")
    args = parser.parse_args()
    if args.count:
        return tally(args.count)
    serve(args.port, args.seed, args.gibberish)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
