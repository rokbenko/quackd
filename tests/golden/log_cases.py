"""Every line the plain log renderer can draw, as named cases.

The MCP tool result is made of these exact strings (`docs/guides/mcp.md`) and a model reads them on
every call, so the terminal view may be redrawn but `render_lines` may not move under it.
One case per branch, named, so a failing diff says which branch drifted rather than dumping
two hundred lines side by side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quackd.log import LogEvent, call_lines, intent_line, render_lines

REPO = Path(__file__).resolve().parents[2]

SYSTEM_PROMPT = "You are the brain of a duck.\nRules follow.\n- call exactly one tool"
MULTILINE = "planner: it can see the ball\nbearing +12 deg, 0.8 m away\nnext: go_to, then kick"
LONG_THINKING = "the ball is behind me, so I turn. " * 90


def _e(kind: str, t: float, **data: Any) -> LogEvent:
    return LogEvent(kind, t, data)


def events() -> list[tuple[str, LogEvent]]:
    """(name, event) for every branch of the renderer, the ones that draw nothing included."""
    return [
        # ── run_start: an adapter or a bare transport, and its four optional lines ──
        (
            "run_start_full",
            _e(
                "run_start",
                0.0,
                duck="find-and-kick",
                provider="anthropic",
                model="claude-opus-5",
                transport="sim2d",
                adapter="microduck",
                tools=["walk", "kick", "quack"],
                memory={"notes": 3, "episodes": 5},
                system_prompt=SYSTEM_PROMPT,
                dry_run=True,
                connect_s=0.0456,
            ),
        ),
        (
            "run_start_bare",
            _e("run_start", 0.0, duck="hello-world", provider="fake", model=None, transport="mock"),
        ),
        # ── observation ──
        ("observation", _e("observation", 0.1, step=0, text="[step 0/5]\nstate: standing")),
        ("observation_error", _e("observation", 0.1, error="camera timed out")),
        ("observation_empty", _e("observation", 0.1)),
        # ── llm_request ──
        (
            "llm_request",
            _e("llm_request", 0.2, step=2, messages=7, images=1, provider="openai", model="gpt-5"),
        ),
        (
            "llm_request_reprompt",
            _e(
                "llm_request",
                0.2,
                step=2,
                messages=8,
                provider="openai",
                model="gpt-5",
                reprompt=True,
            ),
        ),
        # ── llm ──
        (
            "llm_full",
            _e(
                "llm",
                0.3,
                step=2,
                thinking=LONG_THINKING,
                text="going for it.\nthe ball is close",
                tool_calls=[
                    {"name": "go_to", "arguments": {"target": "ball", "stop_distance": 0.25}},
                    {"name": "kick", "arguments": {"leg": "right"}},
                ],
                usage={"input_tokens": 1200, "output_tokens": 40, "reasoning_tokens": 300},
                usage_total={"input_tokens": 5000, "output_tokens": 130},
                latency_s=1.25,
                stop_reason="tool_use",
            ),
        ),
        (
            "llm_no_tool_call",
            _e("llm", 0.3, text="I think I should stop", tool_calls=[], usage={}),
        ),
        ("llm_error", _e("llm", 0.3, error="ProviderError: rate limited", latency_s=3.0)),
        ("llm_bare", _e("llm", 0.3, tool_calls=[{"name": "stop", "arguments": {}}], usage={})),
        # ── llm: the cache buckets and the money, which ride on the same tokens line ──
        (
            "llm_priced",
            _e(
                "llm",
                0.3,
                step=3,
                text="kicking now",
                tool_calls=[{"name": "kick", "arguments": {"leg": "right"}}],
                # `in=` is the WHOLE prompt and `cached=`/`cache_write=` are slices of it, so
                # these three do not add up and are not meant to. A reader who sums them is
                # reading Anthropic's disjoint buckets into the one convention quackd stores.
                usage={
                    "input_tokens": 1631,
                    "output_tokens": 16,
                    "cache_read_tokens": 1024,
                    "cache_write_tokens": 512,
                },
                usage_total={"input_tokens": 9812, "output_tokens": 96},
                cost_usd=0.002752,
                cost_usd_total=0.030876,
                latency_s=0.04,
                stop_reason="tool_use",
            ),
        ),
        (
            # A model the catalogue has no rate for: both figures are None, not 0.0, and no
            # dollar sign may appear anywhere on the line. Printing `$0.00` for a frontier
            # model is the one failure here that costs somebody real money, so it is frozen.
            "llm_unpriced",
            _e(
                "llm",
                0.3,
                step=3,
                tool_calls=[{"name": "kick", "arguments": {"leg": "right"}}],
                usage={"input_tokens": 1631, "output_tokens": 16, "cache_read_tokens": 1024},
                usage_total={"input_tokens": 9812, "output_tokens": 96},
                cost_usd=None,
                cost_usd_total=None,
                latency_s=0.04,
                stop_reason="tool_use",
            ),
        ),
        # ── decision: the discrete stepper's own turn, taken and declined ──
        (
            # `llm` is the name of the decision LLM that answered, which the record carries and
            # the line does not print. It is here so that a renderer which started printing it
            # would have to change this file to do it.
            "decision_taken",
            _e(
                "decision",
                0.3,
                mode="on",
                llm="jev",
                model="jev-1.13.0",
                latency_s=0.213,
                gate="taken",
                choice="gripper(open=false)",
                confidence=0.93,
                floor=0.85,
                probabilities={"gripper(open=false)": 0.93, "place": 0.04, "stop": 0.02},
                call={"name": "gripper", "arguments": {"open": False}},
            ),
        ),
        (
            "decision_below_floor",
            _e(
                "decision",
                0.3,
                mode="on",
                latency_s=0.44,
                gate="below_floor",
                choice="place",
                confidence=0.62,
                floor=0.85,
                probabilities={"place": 0.62, "gripper(open=true)": 0.30},
            ),
        ),
        ("decision_escalate", _e("decision", 0.3, mode="on", latency_s=0.19, gate="escalate")),
        ("decision_done", _e("decision", 0.3, mode="on", latency_s=0.2, gate="done", done=0.91)),
        (
            "decision_error",
            _e("decision", 0.3, mode="shadow", latency_s=1.0, error="TypeSafeAPITimeoutError"),
        ),
        # ── decision: what the question cost, measured and guessed ──
        (
            # The whole argument for a stepper is the ratio between this and the model call it
            # stands in for, so the figure rides in the same parenthesis as the seconds.
            "decision_billed",
            _e(
                "decision",
                0.3,
                mode="on",
                llm="jev",
                model="jev-1.13.0",
                latency_s=0.11,
                gate="taken",
                choice="gripper(open=false)",
                confidence=0.93,
                floor=0.85,
                probabilities={"gripper(open=false)": 0.93, "stop": 0.04},
                call={"name": "gripper", "arguments": {"open": False}},
                usage={"input_tokens": 527, "output_tokens": 3},
                cost_usd=0.000022,
            ),
        ),
        (
            # The decision LLM reported no input count, so the stepper divided the characters
            # it had sent by four and marked BOTH numbers `~`. The tilde is the only thing
            # telling a reader this is a guess, which is exactly the kind of mark that vanishes
            # in a refactor nobody notices, so it is frozen here.
            "decision_billed_estimated",
            _e(
                "decision",
                0.3,
                mode="on",
                latency_s=0.44,
                gate="below_floor",
                choice="place",
                confidence=0.62,
                floor=0.85,
                probabilities={"place": 0.62, "gripper(open=true)": 0.30},
                usage={"input_tokens": 612, "output_tokens": 0},
                usage_estimated=True,
                cost_usd=0.000026,
            ),
        ),
        (
            # The request went out and then the call raised, which a hosted decision LLM still
            # bills, so the estimate rides in the `after ...` of the error line rather than
            # being dropped on the floor with the answer.
            "decision_error_billed",
            _e(
                "decision",
                0.3,
                mode="shadow",
                latency_s=1.0,
                error="TypeSafeAPITimeoutError",
                usage={"input_tokens": 640, "output_tokens": 0},
                usage_estimated=True,
                cost_usd=0.000027,
            ),
        ),
        (
            "decision_shadow_agree",
            _e(
                "decision_shadow",
                0.4,
                decision_choice="report_state",
                decision_confidence=0.88,
                decision_latency_s=0.2,
                model_verb="report_state",
                agree=True,
                llm_latency_s=8.2,
            ),
        ),
        (
            "decision_shadow_differ",
            _e(
                "decision_shadow",
                0.4,
                decision_choice="stop",
                decision_confidence=0.71,
                decision_latency_s=0.3,
                model_verb="move_joints",
                agree=False,
                llm_latency_s=6.9,
            ),
        ),
        # ── enforce ──
        ("enforce", _e("enforce", 0.4, issue="no tool call", action="re-prompting once")),
        (
            "enforce_text",
            _e(
                "enforce",
                0.4,
                issue="no tool call",
                action="re-prompting once",
                text="You must call exactly one tool.",
            ),
        ),
        # ── verb_start ──
        ("verb_start", _e("verb_start", 0.5, name="go_to", params={"target": "ball"})),
        (
            "verb_start_nested",
            _e("verb_start", 0.5, name="walk_to", params={"target": "ball"}, nested=True),
        ),
        ("verb_start_mcp", _e("verb_start", 0.5, name="quack", params={}, source="mcp")),
        # The stepper chose this verb itself and the model never saw the turn, which is the one
        # thing a reader of the log has to be able to tell from a verb the pilot asked for.
        (
            "verb_start_decision",
            _e("verb_start", 0.5, name="report_state", params={}, source="decision"),
        ),
        # ── gate: every outcome word, and every optional field it can carry ──
        (
            "gate_refused",
            _e(
                "gate",
                0.6,
                name="kick",
                gate="allowlist",
                outcome="refused",
                reason="not allowed here",
            ),
        ),
        ("gate_denied", _e("gate", 0.6, gate="confirm", outcome="denied", verb="kick")),
        ("gate_allowed", _e("gate", 0.6, gate="confirm", outcome="allowed", verb="kick")),
        (
            "gate_dry_run",
            _e(
                "gate",
                0.6,
                name="go_to",
                gate="dry_run",
                outcome="skipped",
                reason="would run go_to, sent nothing",
                params={"target": None, "stop_distance": 0.25, "fast": True, "r": 0.6666666},
            ),
        ),
        (
            "gate_precondition",
            _e(
                "gate",
                0.6,
                gate="precondition",
                outcome="refused",
                reason="must be standing",
                state="fallen",
                last="kick",
            ),
        ),
        (
            "gate_budget",
            _e("gate", 0.6, gate="budget", outcome="exceeded", reason="max_steps (5)"),
        ),
        (
            "gate_fired",
            _e("gate", 0.6, gate="abort_when", outcome="fired", reason="battery < 10%"),
        ),
        # ── prompt: the question a person was put, and what they said ──
        (
            "prompt_yes",
            _e("prompt", 0.65, what="confirm", question="run kick(power=0.5)?", answer=True),
        ),
        (
            "prompt_no",
            _e(
                "prompt",
                0.65,
                what="acknowledge",
                question="this robot cannot see a fall,\nso you are the only safety left",
                answer=False,
            ),
        ),
        # ── intent, one at a time ──
        (
            "intent_one",
            _e("intent", 0.7, intent="move", params={"vx": 0.1, "vy": None}, accepted=True),
        ),
        ("intent_bare", _e("intent", 0.7, intent="stop", params={}, accepted=True)),
        (
            "intent_refused",
            _e(
                "intent",
                0.7,
                intent="sound",
                params={"tag": "greet"},
                accepted=False,
                reason="the mock refuses sound",
            ),
        ),
        ("intent_refused_silent", _e("intent", 0.7, intent="do", params={}, accepted=False)),
        # ── verb_end ──
        (
            "verb_end_ok",
            _e(
                "verb_end",
                0.8,
                name="go_to",
                ok=True,
                outcome="ok",
                summary="reached the ball",
                elapsed_s=12.5,
                intents={"move": 120, "stop": 1},
            ),
        ),
        (
            "verb_end_fail",
            _e(
                "verb_end",
                0.8,
                name="kick",
                ok=False,
                outcome="fail",
                summary="missed",
                elapsed_s=1.0,
                intents={"do": 1},
            ),
        ),
        (
            "verb_end_preempted",
            _e(
                "verb_end",
                0.8,
                name="search_scan",
                outcome="preempted",
                summary="role change to kicker",
            ),
        ),
        (
            "verb_end_nested",
            _e(
                "verb_end",
                0.8,
                name="go_to",
                ok=True,
                outcome="ok",
                summary="there",
                nested=True,
                elapsed_s=2.0,
                intents={"move": 2},
            ),
        ),
        (
            "verb_end_two_clocks",
            _e(
                "verb_end",
                0.8,
                name="walk",
                ok=True,
                outcome="ok",
                summary="walked",
                elapsed_s=1.4,
                transport_s=20.0,
                clock="sim",
                intents={"move": 200},
            ),
        ),
        (
            "verb_end_one_clock",
            _e(
                "verb_end",
                0.8,
                name="walk",
                ok=True,
                outcome="ok",
                summary="walked",
                elapsed_s=1.0,
                transport_s=1.1,
                clock="sim",
                intents={},
            ),
        ),
        (
            "verb_end_legacy_ok_flag",
            _e("verb_end", 0.8, name="quack", ok=True, summary="quacked"),
        ),
        # ── assess: the verdict, and what the pilot guessed to reach it ──
        (
            "assess_feasible",
            _e("assess", 0.4, verdict="feasible", reason="a tennis ball is well under 0.5 kg"),
        ),
        (
            "assess_infeasible",
            _e(
                "assess",
                0.4,
                verdict="infeasible",
                reason="the basket looks like 3 kg of clothes",
                ends_run=True,
                estimates=[
                    {
                        "object": "laundry basket",
                        "quantity": "mass_kg",
                        "value": 3.0,
                        "basis": "image",
                        "confidence": "medium",
                    }
                ],
                needs={"payload_kg": 3.0, "manipulator": "gripper"},
            ),
        ),
        # a record from before `answered_by`, which names nobody
        (
            "assess_uncertain_human_no",
            _e(
                "assess",
                0.4,
                verdict="uncertain",
                reason="the basket is out of frame",
                human="no_go",
                ends_run=True,
            ),
        ),
        (
            "assess_uncertain_a_person_no",
            _e(
                "assess",
                0.4,
                verdict="uncertain",
                reason="the basket is out of frame",
                human="no_go",
                answered_by="a person",
                ends_run=True,
            ),
        ),
        # the prompt raised: nobody said no, and the gate read it as no
        (
            "assess_uncertain_unanswered",
            _e(
                "assess",
                0.4,
                verdict="uncertain",
                reason="the basket is out of frame",
                human="no_go",
                raised="EOFError",
                ends_run=True,
            ),
        ),
        *(
            (
                f"assess_uncertain_{label}_go",
                _e(
                    "assess",
                    0.4,
                    verdict="uncertain",
                    reason="the basket is out of frame",
                    human="go",
                    answered_by=who,
                ),
            )
            for label, who in (
                ("a_person", "a person"),
                ("yes", "--yes"),
                ("flock", "a flock's standing answer"),
                ("pipe", "a pipe"),
            )
        ),
        ("assess_invalid", _e("assess", 0.4, verdict=None, summary="invalid: verdict: maybe")),
        # ── declare, memory, note ──
        ("declare_success", _e("declare", 0.9, outcome="success", reason="ball displaced")),
        ("declare_failure", _e("declare", 0.9, outcome="failure", reason="never found it")),
        ("memory", _e("memory", 0.9, summary="remembered: the ball lives by the sofa")),
        ("note", _e("note", 0.9, text=MULTILINE)),
        # ── the coordinator's vocabulary, which the GIF caption shares ──
        ("flock_auction", _e("auction", 1.0, first_bid="duck-1", dist=0.42)),
        ("flock_claim", _e("claim", 1.0, kicker="duck-1", dist=0.62, spotter="duck-2")),
        ("flock_claim_no_spotter", _e("claim", 1.0, kicker="duck-1", dist=0.62)),
        ("flock_miss", _e("miss", 1.0, duck="duck-0", detail="the ball rolled away")),
        ("flock_miss_bare", _e("miss", 1.0, duck="duck-0")),
        ("flock_kick_done", _e("kick_done", 1.0, kicker="duck-2")),
        ("flock_verdict", _e("verdict", 1.0, verdict="moved", moved_m=0.51, spotter="duck-1")),
        ("flock_verdict_no_distance", _e("verdict", 1.0, verdict="unchanged", spotter="duck-1")),
        ("flock_separation", _e("separation", 1.0, duck="duck-0", dist=0.3)),
        ("flock_auction_void", _e("auction_void", 1.0, auctions=2)),
        ("flock_auction_waiting", _e("auction_waiting", 1.0, missing_roles=["spotter", "kicker"])),
        ("flock_member_dead", _e("member_dead", 1.0, duck="duck-2", last_hb=3.0)),
        ("flock_member_excluded", _e("member_excluded", 1.0, duck="duck-1", why="no kick verb")),
        ("flock_wedges_rotated", _e("wedges_rotated", 1.0, round=2, by_deg=45)),
        (
            "flock_bid_rejected_why",
            _e("bid_rejected", 1.0, src="duck-0", role="kicker", why="too far"),
        ),
        (
            "flock_bid_rejected_missing",
            _e("bid_rejected", 1.0, src="duck-0", role="kicker", missing=["kick", "walk_to"]),
        ),
        (
            "flock_talk",
            _e("talk", 1.0, src="duck-a", to="arm", text="I have the ball, you spot", ok=True),
        ),
        (
            "flock_talk_refused",
            _e(
                "talk",
                1.0,
                src="duck-a",
                to="ghost",
                text="hello",
                ok=False,
                summary="no member called 'ghost'; this flock is arm",
            ),
        ),
        ("member_end", _e("member_end", 1.1, status="stopped", steps=7)),
        # ── the MCP server's own kinds ──
        (
            "tool_call",
            _e(
                "tool_call",
                1.2,
                tool="robot_run_verb",
                robot="duck",
                verb="go_to",
                params={"target": "ball"},
            ),
        ),
        ("tool_result", _e("tool_result", 1.3, ok=True, elapsed_s=1.1, budget="step 1/40")),
        (
            "tool_result_sim",
            _e("tool_result", 1.3, ok=False, elapsed_s=0.2, transport_s=20.0, clock="sim"),
        ),
        # ── the three that draw nothing on purpose, and one kind no view knows ──
        ("verb_renders_nothing", _e("verb", 1.4, name="kick", ok=True)),
        ("frame_renders_nothing", _e("frame", 1.4, path="frames/0001.png")),
        ("run_end_renders_nothing", _e("run_end", 1.4, outcome="success")),
        ("unknown_renders_nothing", _e("bus", 1.4, msg={"kind": "BID"})),
        # ── the arms a first pass at this corpus missed ──
        ("intent_no_accepted_field", _e("intent", 0.7, intent="stop", params={})),
        (
            "llm_short_thinking",
            _e("llm", 0.3, thinking="short enough to fit", tool_calls=[], usage={}),
        ),
        (
            "verb_end_legacy_not_ok",
            _e("verb_end", 0.8, name="kick", ok=False, summary="missed"),
        ),
        (
            "verb_end_clocks_a_fifth_apart",
            _e(
                "verb_end",
                0.8,
                name="walk",
                ok=True,
                outcome="ok",
                summary="walked",
                elapsed_s=10.0,
                transport_s=11.0,
                clock="sim",
                intents={},
            ),
        ),
        # ── fmt_value's own edges, carried on a line the renderer prints verbatim ──
        (
            "fmt_value_edges",
            _e(
                "verb_start",
                1.5,
                name="do",
                params={
                    "none": None,
                    "yes": True,
                    "no": False,
                    "ratio": 0.6666666666,
                    "short": "x" * 60,
                    "long": "x" * 500,
                    "big": {f"k{i}": list(range(10)) for i in range(20)},
                },
            ),
        ),
    ]


def host_detector_start() -> LogEvent:
    """A `run_start` whose detector is not the colour one, with the board beside it.

    Its own case rather than one of `events()`, whose cases are also replayed together as
    `whole_call`: a case added there would move that one's lines, and this is the case that
    proves no older line moved. The colour detector is never named on the run line, so every
    transcript written before there was a choice, and every run on a simulator, reads as it
    did."""
    return _e(
        "run_start",
        0.0,
        duck="find-and-kick",
        provider="ollama",
        model="qwen3:8b",
        transport="bridge",
        adapter="toddlerbot",
        detector="yolo@host",
        host={
            "address": "jetson.local:9874",
            "daemon_version": "0.1.0",
            "capabilities": {"camera": True, "detect": True, "tegra": True},
            "detect": {"model": "yolov8n.pt", "device": "cuda"},
        },
        tools=["observe", "go_to", "stop"],
        connect_s=0.25,
    )


def golden() -> dict[str, Any]:
    """Every branch, plus the two knobs a view is built with, plus the burst coalescing,
    plus two whole calls through the path the MCP tool result actually uses."""
    cases = events()
    out: dict[str, Any] = {name: [list(line) for line in render_lines(e)] for name, e in cases}
    by_name = dict(cases)
    out["run_start_full@no-prompt"] = [
        list(line) for line in render_lines(by_name["run_start_full"], prompt=False)
    ]
    out["run_start_host_detector"] = [list(line) for line in render_lines(host_detector_start())]
    for label, chars in (("cut", 100), ("none", 0), ("all", None)):
        out[f"llm_full@thinking-{label}"] = [
            list(line) for line in render_lines(by_name["llm_full"], thinking_chars=chars)
        ]
    out["burst_numeric"] = list(
        intent_line(
            [
                _e(
                    "intent",
                    i * 0.1,
                    intent="move",
                    params={"vx": 0.2, "wz": -0.4 + i * 0.2, "vy": None},
                    accepted=True,
                )
                for i in range(4)
            ]
        )
    )
    out["burst_robot_clock"] = list(
        intent_line(
            [
                _e("intent", 0.0, intent="move", params={"vx": 0.2}, robot_t=0.0),
                _e("intent", 1.4, intent="move", params={"vx": 0.2}, robot_t=20.0),
            ]
        )
    )
    out["burst_labels"] = list(
        intent_line(
            [
                _e("intent", i * 0.1, intent="do", params={"skill": f"s{i}"}, accepted=True)
                for i in range(6)
            ]
        )
    )
    out["burst_no_params"] = list(
        intent_line(
            [_e("intent", i * 0.1, intent="stop", params={}, accepted=True) for i in range(3)]
        )
    )
    out["burst_repeated_label"] = list(
        intent_line(
            [
                _e("intent", i * 0.1, intent="do", params={"skill": "kick"}, accepted=True)
                for i in range(4)
            ]
        )
    )
    out["burst_two_labels"] = list(
        intent_line(
            [
                _e("intent", i * 0.1, intent="do", params={"skill": f"s{i % 2}"}, accepted=True)
                for i in range(4)
            ]
        )
    )
    out["whole_call"] = call_lines([event for _name, event in cases])
    example = REPO / "docs" / "assets" / "transcript-example.jsonl"
    raw = example.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in raw if line.strip()]
    out["transcript_example"] = call_lines(
        [
            LogEvent(
                str(r.get("kind", "")),
                float(r.get("t") or 0.0),
                {k: v for k, v in r.items() if k not in ("t", "kind")},
            )
            for r in records
        ]
    )
    return out
