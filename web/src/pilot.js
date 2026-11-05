/**
 * quackd, in about two hundred lines: the loop, the verbs and the contract.
 *
 * This is the browser's version of `quackd/agent/loop.py`, `quackd/verbs/core.py` and
 * `quackd/safety.py`, kept deliberately small so the whole idea fits on one screen. The
 * LLM never sees a joint, a motor or a pixel. It sees a list of verbs the robot's own
 * manifest declares, one observation per turn, and a contract: which verbs are allowed,
 * how many steps it gets, and how long. It answers with exactly one verb per turn. The
 * executor checks that verb against the contract before the robot moves, and the run stops
 * when the budget is spent whatever the LLM thinks.
 *
 * The `Runtime` underneath owns the 50 Hz clock. Verbs ask it for simulated seconds; it
 * paces those against the wall clock so the page shows real time and a recording plays
 * back at the speed it happened.
 */

import { ACHIEVED_FRACTION, CONTROL_DT, GAIT_FLOOR, HEAD_YAW_LIMIT } from "./microduck.js";

const MOVE_RESEND_S = 0.1; // the twist is re-sent at 10 Hz or the deadman zeroes it

export class Runtime {
  constructor(duck, { onError = null } = {}) {
    this.duck = duck;
    this.running = false;
    this.manualTwist = [0, 0, 0];
    this.manual = false;
    this.error = null;
    this._waiters = [];
    this._onFrame = null;
    this._onError = onError;
  }

  start(onFrame) {
    this._onFrame = onFrame;
    if (this.running) return;
    this.running = true;
    let carry = 0;
    let previous = performance.now();
    const tick = async (now) => {
      if (!this.running) return;
      carry += Math.min(0.1, (now - previous) / 1000); // never simulate a lost tab
      previous = now;
      try {
        while (carry >= CONTROL_DT) {
          carry -= CONTROL_DT;
          // LOAD-BEARING, and invisible: the hand's twist is RE-ASSERTED here every tick,
          // immediately before the physics reads it. That asymmetry — 50 Hz against the
          // pilot's 10 Hz — is what lets a key take the robot mid-run without cancelling
          // anything first, because every stray pilot write is overwritten before `step()`
          // sees it. Turn this into a one-shot write on key change and four separate
          // `setTwist(0, 0, 0)` calls (the per-verb catch, `move`'s tail, `stop`, and the
          // page's `run()` finally) all become real bugs at once.
          if (this.manual) this.duck.setTwist(...this.manualTwist);
          await this.duck.step();
          this._wake(CONTROL_DT);
        }
        this._onFrame?.();
      } catch (error) {
        // Without this the loop simply stopped: no more frames, and every verb waiting on
        // simulated time hung for good, with nothing on the page to say why.
        this._die(error);
        return;
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  stop() { this.running = false; }

  /** Stop the world and refuse every waiter, so nothing is left hanging on a dead clock. */
  _die(error) {
    this.running = false;
    this.error = error;
    const waiting = this._waiters;
    this._waiters = [];
    for (const waiter of waiting) waiter.reject(error);
    this._onError?.(error);
  }

  /** Let `seconds` of simulated time pass. Verbs never touch the clock directly. */
  sleep(seconds, signal = null) {
    if (this.error) return Promise.reject(this.error);
    if (signal?.aborted) return Promise.reject(signal.reason);
    if (seconds <= 0) return Promise.resolve();
    return new Promise((resolve, reject) => {
      // The listener has to come OFF again when the sleep ends normally. `once: true` only
      // covers the abort path, and `move` issues up to a hundred slices per verb against a
      // budget of eighteen steps, so one run left ~1800 live closures on a single signal —
      // and an abort then walked every one of them.
      const done = () => signal?.removeEventListener("abort", onAbort);
      const waiter = {
        left: seconds,
        resolve: () => { done(); resolve(); },
        reject: (error) => { done(); reject(error); },
      };
      // Stop has to reach a move that is already running. Without this a ten second move ran
      // to completion after the button, and the duck kept walking the whole time.
      const onAbort = () => {
        this._waiters = this._waiters.filter((w) => w !== waiter);
        reject(signal.reason);
      };
      this._waiters.push(waiter);
      signal?.addEventListener("abort", onAbort, { once: true });
    });
  }

  _wake(dt) {
    if (!this._waiters.length) return;
    const still = [];
    for (const waiter of this._waiters) {
      waiter.left -= dt;
      if (waiter.left <= 0) waiter.resolve();
      else still.push(waiter);
    }
    this._waiters = still;
  }
}

// ── the same checks pydantic makes on the Python side ──────────────────────────────────

/**
 * Check an LLM's arguments against the schema they were offered, and say what is wrong.
 *
 * The schemas were sent to the vendor and never enforced here, so an LLM that ignored one
 * got whatever it asked for: `duration_s: 1e6` became ten million awaited slices and hung the
 * tab, and `duration_s: "soon"` produced NaN, ran no loop at all, and reported success.
 * Python refuses both at the verb boundary, with `extra="forbid"` and real bounds.
 *
 * Returns null when the arguments are usable, or a sentence for the transcript when not.
 */
export function checkParams(schema, args) {
  if (args === null || typeof args !== "object" || Array.isArray(args)) {
    return `expected an object of arguments, got ${JSON.stringify(args)}`;
  }
  const properties = schema.properties ?? {};
  if (schema.additionalProperties === false) {
    const unknown = Object.keys(args).filter((k) => !(k in properties));
    if (unknown.length) return `unknown parameter(s): ${unknown.join(", ")}`;
  }
  for (const name of schema.required ?? []) {
    if (!(name in args)) return `${name} is required`;
  }
  for (const [name, value] of Object.entries(args)) {
    const rule = properties[name];
    if (!rule) continue;
    if (rule.type === "number") {
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return `${name} must be a finite number, got ${JSON.stringify(value)}`;
      }
      if (rule.minimum !== undefined && value < rule.minimum) {
        return `${name} must be at least ${rule.minimum}, got ${value}`;
      }
      if (rule.maximum !== undefined && value > rule.maximum) {
        return `${name} must be at most ${rule.maximum}, got ${value}`;
      }
    }
    if (rule.type === "string") {
      if (typeof value !== "string") return `${name} must be a string`;
      if (rule.maxLength !== undefined && value.length > rule.maxLength) {
        return `${name} must be at most ${rule.maxLength} characters`;
      }
      if (rule.enum && !rule.enum.includes(value)) {
        return `${name} must be one of ${rule.enum.join(", ")}`;
      }
    }
  }
  return null;
}

// ── the verbs, as the Microduck's manifest declares them ───────────────────────────────

export const VERBS = {
  move: {
    description:
      "Walk with a velocity for a duration. Use small values; the robot is 25 cm tall. " +
      `This body does not step below ${GAIT_FLOOR.vx} m/s or ${GAIT_FLOOR.wz} rad/s, and ` +
      `achieves about ${ACHIEVED_FRACTION} of what it is asked, so read the pose afterwards.`,
    params: {
      type: "object",
      properties: {
        vx: { type: "number", description: "Forward m/s (negative = back).", minimum: -0.3, maximum: 0.3 },
        vy: { type: "number", description: "Left m/s (negative = right).", minimum: -0.2, maximum: 0.2 },
        wz: { type: "number", description: "Turn rate rad/s (+ = left).", minimum: -1.5, maximum: 1.5 },
        duration_s: { type: "number", description: "How long to hold this velocity.", minimum: 0.1, maximum: 10 },
      },
      // Python's MoveParams defaults vx to 0.15 and duration_s to 1.0 and requires neither.
      required: [],
      additionalProperties: false,
    },
    async run(runtime, p, signal = null) {
      const { vx = 0.15, vy = 0, wz = 0, duration_s = 1 } = p;  // Python's defaults
      const slices = Math.max(1, Math.round(duration_s / MOVE_RESEND_S));
      for (let i = 0; i < slices; i++) {
        runtime.duck.setTwist(vx, vy, wz); // re-sent, exactly as the real verb does
        await runtime.sleep(duration_s / slices, signal);
      }
      runtime.duck.setTwist(0, 0, 0);
      const sent = runtime.duck.sent;
      const note = sent.some((v, i) => Math.abs(v - [vx, vy, wz][i]) > 1e-6)
        ? ` (the gait floor turned that into ${sent.map((v) => v.toFixed(2)).join(", ")})`
        : "";
      const { x, y, theta } = runtime.duck.pose;
      return `walked vx=${vx} vy=${vy} wz=${wz} for ${duration_s}s${note}; now at ` +
        `(${x.toFixed(2)}, ${y.toFixed(2)}) facing ${((theta * 180) / Math.PI).toFixed(0)} degrees`;
    },
  },
  stop: {
    description: "Stop moving immediately (zero velocity). Always allowed.",
    params: { type: "object", properties: {}, additionalProperties: false },
    async run(runtime) {
      runtime.duck.setTwist(0, 0, 0);
      return "stopped (velocity zeroed)";
    },
  },
  report_state: {
    description: "Report the robot's state: posture, pose, what the camera can see.",
    params: { type: "object", properties: {}, additionalProperties: false },
    async run(runtime) {
      return JSON.stringify(runtime.duck.observe());
    },
  },
  gaze: {
    description: "Point the head at a bearing in degrees (+ = left), up to 60 either way.",
    params: {
      type: "object",
      properties: { bearing_deg: { type: "number", minimum: -90, maximum: 90 } },
      required: ["bearing_deg"],
      additionalProperties: false,
    },
    async run(runtime, p, signal = null) {
      const clamped = runtime.duck.setHead((p.bearing_deg * Math.PI) / 180);
      await runtime.sleep(0.6, signal); // the neck is a servo the policy drives, not a teleport
      const limit = ((HEAD_YAW_LIMIT * 180) / Math.PI).toFixed(0);
      return `looking ${p.bearing_deg} degrees${clamped ? ` (clamped to ${limit})` : ""}`;
    },
  },
  kick: {
    description:
      "Kick forward with one leg. Only connects if the ball is under 0.3 m away and roughly ahead.",
    params: {
      type: "object",
      properties: { leg: { type: "string", enum: ["left", "right"] } },
      additionalProperties: false,
    },
    async run(runtime, p, signal = null) {
      const connected = runtime.duck.kick(p.leg ?? "right");
      await runtime.sleep(1.5, signal);
      const moved = runtime.duck.lastKickBallMoved;
      if (!connected) {
        const ball = runtime.duck.observe().detections?.find((d) => d.label === "ball");
        // `est_est_distance_m` was a typo for the field observe() actually publishes, so a
        // miss with the ball in view threw a TypeError instead of reporting how far off it was.
        const where = ball
          ? `the ball is ${ball.est_distance_m.toFixed(2)} m away at ${ball.bearing_deg.toFixed(0)} degrees`
          : "the camera cannot see the ball";
        return { ok: false, summary: `kick missed: ${where} (needs under 0.3 m, roughly ahead)` };
      }
      return {
        ok: true,
        summary: moved === null ? "kicked" : `kicked; the ball moved ${moved.toFixed(2)} m`,
        data: { ball_moved_m: moved },
      };
    },
  },
  say: {
    description: "Say something. The robot has seven duck tones and no speech.",
    params: {
      type: "object",
      properties: { text: { type: "string", maxLength: 200 } },
      required: ["text"],
      additionalProperties: false,
    },
    async run(runtime, p) {
      runtime.said = p.text;
      return `said ${JSON.stringify(p.text)} as a quack`;
    },
  },
  stand_up: {
    description: "Recover to standing after a fall.",
    params: { type: "object", properties: {}, additionalProperties: false },
    async run(runtime, p, signal = null) {
      runtime.duck.standUp();
      await runtime.sleep(1.0, signal);
      const up = runtime.duck.posture === "standing";
      return { ok: up, summary: up ? "upright" : "still down" };
    },
  },
};

const META = {
  declare_success: {
    description: "Call when the goal is met. Say which part of the goal, and your evidence.",
    params: {
      type: "object",
      properties: { reason: { type: "string" } },
      required: ["reason"],
      additionalProperties: false,
    },
  },
  declare_failure: {
    description: "Call when the goal cannot be reached (repeated failures, nothing found).",
    params: {
      type: "object",
      properties: { reason: { type: "string" } },
      required: ["reason"],
      additionalProperties: false,
    },
  },
};

export const DEFAULT_CONTRACT = {
  allow: ["move", "stop", "report_state", "gaze", "kick", "say", "stand_up"],
  maxSteps: 18,
  maxMinutes: 3,
};

export function toolSchemas(contract) {
  const tools = [];
  for (const [name, verb] of Object.entries(VERBS)) {
    if (contract.allow.includes(name)) {
      tools.push({ name, description: verb.description, input_schema: verb.params });
    }
  }
  for (const [name, meta] of Object.entries(META)) {
    tools.push({ name, description: meta.description, input_schema: meta.params });
  }
  return tools;
}

export function systemPrompt(goal, contract) {
  return `You are the brain of a Microduck: a small biped duck robot, 25 cm tall, 800 g.
You are a high-level pilot. You choose ONE verb per turn; the robot's own controllers handle
balance and gait. You are in a physics simulator: a 2 m arena with low walls and an orange
ball that rolls when it is kicked. Nobody is in the arena with you: there is no person here
to find, follow or walk up to. Distances are metres.

## Rules, enforced by the executor and not optional
- Call exactly one tool per turn. Never zero, never two.
- Only these verbs exist: ${contract.allow.join(", ")}. Anything else is refused.
- Budget: ${contract.maxSteps} steps and ${contract.maxMinutes} minutes. The run stops when either is spent.
- Call declare_success when the goal is met, or declare_failure when it cannot be.

## This body
It walks on a learned policy, not on arithmetic. It does not step at all below about
${GAIT_FLOOR.vx} m/s or ${GAIT_FLOOR.wz} rad/s, and it achieves about ${ACHIEVED_FRACTION} of what it is asked. Every
observation carries the pose it actually reached: steer from that, not from the numbers you
sent. To walk a shape, walk a leg, read the pose, correct, and repeat.

## The goal
${goal}`;
}

export function observationText(duck, step, contract, last) {
  const state = duck.observe();
  const lines = [
    `[step ${step}/${contract.maxSteps} - ${state.sim_time.toFixed(1)}s of ${contract.maxMinutes * 60}s]`,
    `state: posture=${state.posture} pose=(${state.pose.x}, ${state.pose.y}, ${state.pose.theta} rad) head=${state.head_yaw_deg} deg`,
    `camera: ${state.detections.length
      ? state.detections.map((d) => `${d.label} at ${d.bearing_deg} degrees, about ${d.est_distance_m} m`).join("; ")
      : "nothing detected"}`,
  ];
  if (last) lines.push(`last verb \`${last.name}\`: ${last.ok ? "ok" : "FAILED"} - ${last.summary}`);
  lines.push("Choose exactly one tool.");
  return lines.join("\n");
}

/**
 * Why the run stopped, in the words of whoever stopped it. Every route names itself — the
 * Stop button, Reset, the switch, a drive key taking the controls — so the transcript quotes
 * a reason instead of assuming there was only ever one.
 */
const abortedBecause = (signal) => signal?.reason?.message ?? "you stopped the run";

/** One run of the loop. `onEvent` is how the page draws the transcript. */
export async function pilot({ runtime, goal, provider, contract = DEFAULT_CONTRACT, onEvent, signal }) {
  const duck = runtime.duck;
  const history = [];
  const startedAt = duck.t;
  let last = null;
  onEvent({ kind: "start", goal, contract });

  for (let step = 0; step < contract.maxSteps; step++) {
    if (signal?.aborted) { onEvent({ kind: "end", outcome: "aborted", reason: abortedBecause(signal) }); return; }
    if (duck.t - startedAt > contract.maxMinutes * 60) {
      onEvent({ kind: "end", outcome: "budget", reason: "the time budget is spent" });
      return;
    }
    const observation = observationText(duck, step, contract, last);
    onEvent({ kind: "observation", text: observation, step });
    let call;
    try {
      call = await provider.step({
        system: systemPrompt(goal, contract),
        history,
        observation,
        tools: toolSchemas(contract),
        signal,
      });
    } catch (error) {
      // An abort during the LLM's turn is not a provider failure. Without the signal on the
      // fetch this arrived seconds late, after a response nobody wanted and the visitor's own
      // key had already been billed for.
      if (signal?.aborted) { onEvent({ kind: "end", outcome: "aborted", reason: abortedBecause(signal) }); return; }
      onEvent({ kind: "end", outcome: "error", reason: String(error.message || error) });
      return;
    }
    history.push({ observation, call });
    onEvent({ kind: "call", name: call.name, args: call.arguments });

    if (call.name === "declare_success" || call.name === "declare_failure") {
      onEvent({
        kind: "end",
        outcome: call.name === "declare_success" ? "success" : "failure",
        reason: call.arguments?.reason ?? "",
      });
      return;
    }
    const verb = VERBS[call.name];
    if (!verb || !contract.allow.includes(call.name)) {
      // Refused, and the run continues: the LLM is told and may choose differently. This
      // is the executor's job, and the reason a contract is worth having.
      last = { name: call.name, ok: false, summary: `refused: ${call.name} is not in this task's allowlist` };
      onEvent({ kind: "result", ...last });
      continue;
    }
    const complaint = checkParams(verb.params, call.arguments ?? {});
    if (complaint !== null) {
      // Refused before anything moves, exactly as a pydantic ValidationError does in Python.
      last = { name: call.name, ok: false, summary: `refused: ${complaint}` };
      onEvent({ kind: "result", ...last });
      continue;
    }
    try {
      // The signal is passed down. It was not, so `Runtime.sleep`'s abort listener was dead
      // code and a ten second `move` ran to completion after Stop, exactly as the comment
      // above that listener claimed it would not.
      const outcome = await verb.run(runtime, call.arguments ?? {}, signal);
      // A verb may report that it did not work. `ok: true` used to be unconditional, so a
      // kick that missed read as a success and the failure style was unreachable.
      last =
        typeof outcome === "string"
          ? { name: call.name, ok: true, summary: outcome }
          : { name: call.name, ok: outcome.ok, summary: outcome.summary, data: outcome.data };
    } catch (error) {
      runtime.duck.setTwist(0, 0, 0); // a verb that threw leaves the robot stopped
      // An abort is not a failed verb: this used to print `result move FAILED AbortError`.
      if (signal?.aborted) { onEvent({ kind: "end", outcome: "aborted", reason: abortedBecause(signal) }); return; }
      last = { name: call.name, ok: false, summary: String(error.message || error) };
    }
    onEvent({ kind: "result", ...last });
  }
  runtime.duck.setTwist(0, 0, 0);
  onEvent({ kind: "end", outcome: "budget", reason: `the ${contract.maxSteps} step budget is spent` });
}
