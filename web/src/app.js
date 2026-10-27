/**
 * The page: load the duck, wire the switch, run the pilot, record the result.
 *
 * Two things can drive this robot and both are always live. The keyboard writes a twist —
 * three numbers, vx, vy, wz — plus a head angle, which is the entire interface the hardware
 * has. quackd's switch adds a layer above that: a model reads an English sentence and picks
 * from the robot's own verbs, and the executor checks each one against a contract before
 * anything moves. Underneath there are two learned policies — one stands the duck up, one
 * walks it — and a scripted kick that is quackd's own, not a policy. None of it reads
 * English. That is what the switch is for, and it is the only thing the switch decides:
 * turning it off does not take the keyboard away, because the keyboard was never the layer.
 */

import { CATALOGUE, STATUS_ORDER } from "./catalogue.js";
import { MAX_VX, MAX_VY, Microduck, UPSTREAM } from "./microduck.js";
import { DEFAULT_CONTRACT, Runtime, pilot } from "./pilot.js";
import { PROVIDERS, makeProvider } from "./providers.js";
import { Recorder, shareUrl } from "./record.js";

const $ = (id) => document.getElementById(id);

// The hand's numbers. MANUAL_TURN is GAIT_FLOOR.wz (1.2), where a turn steps both ways from a
// stand, and below MAX_WZ (1.5) so it is not clipped. HEAD_RATE is integrated per frame rather
// than stepped per keydown: auto-repeat used to slam the head to its 60 degree limit in ~200 ms.
const MANUAL_TURN = 1.2, HEAD_RATE = 1.2;
const SPEED = { vx: MAX_VX, vy: MAX_VY, wz: MANUAL_TURN };

// Anything matching TYPING has its own use for SOME key. It splits in two, and the split is
// the whole point: a text control owns the entire keyboard, because W in the goal box is a
// letter; a button, a <summary> or a link owns exactly the two keys that activate it, which is
// all WCAG 2.1.1 asks for. The old guard returned for every key on every one of them, so
// clicking Reset — or Save clip, or a camera button — silently killed the drive keys until you
// clicked somewhere else, and nothing on the page said so.
const TYPING = "input, textarea, select, button, summary, a, [contenteditable]";
const TEXT_ENTRY = "input, textarea, select, [contenteditable]";
const ACTIVATION_KEYS = new Set([" ", "Enter"]);

const ui = {
  loading: $("loading"), phase: $("loading-phase"), bar: $("loading-bar"),
  canvas: $("view"), readout: $("readout"), owner: $("owner"), transcript: $("transcript"),
  goalForm: $("goal-form"), goal: $("goal"), run: $("run"), stopRun: $("stop-run"),
  reset: $("reset"), toggle: $("quackd-on"), toggleLabel: $("toggle-label"),
  toggleNote: $("toggle-note"), keyBox: $("key-box"),
  provider: $("provider"), model: $("model"), modelText: $("model-text"),
  key: $("key"), baseUrl: $("base-url"),
  providerNote: $("provider-note"), record: $("record"), save: $("save"), share: $("share"),
  clear: $("clear"),
};

let duck, view, runtime, recorder;
let running = null;
const held = new Set();
let painted = "live"; // what the pill already says; the HTML ships this state

// ── one place every failure ends up ─────────────────────────────────────────────────────

/**
 * Show a failure instead of freezing on one. Emscripten and embind can throw a bare number
 * or a string, so `error.message` alone prints "undefined" for exactly the failures that are
 * hardest to guess at.
 */
function fail(error, where) {
  const message = String(error?.message ?? error ?? "something went wrong");
  if (!ui.loading.hidden) {
    ui.phase.textContent = message;
    ui.phase.classList.add("bad");
  }
  line("end error", `<b>${escape(where)}</b> ${escape(message)}`);
  return message;
}

addEventListener("error", (event) => fail(event.error ?? event.message, "page"));
addEventListener("unhandledrejection", (event) => fail(event.reason, "promise"));

// ── boot ────────────────────────────────────────────────────────────────────────────────

async function boot() {
  // Cheapest check first, so a missing prerequisite names itself rather than arriving as a
  // failure 45 MB later. Each of these used to be a dead page: the WebGL one threw outside
  // the try, and a blocked CDN broke the module graph before any listener was attached.
  ui.phase.textContent = "checking this browser";
  if (!globalThis.ort) {
    throw new Error(
      "onnxruntime did not arrive from cdn.jsdelivr.net. This page ships none of its own " +
        "dependencies on purpose, so a blocked CDN stops it here."
    );
  }
  if (!document.createElement("canvas").getContext("webgl2")) {
    throw new Error("this browser has no WebGL2, so the arena cannot be drawn");
  }
  // Dynamic, so three.js failing to load is a message rather than a module graph that never
  // evaluates and a page that sits on "starting" with no listeners and no error.
  const { View } = await import("./view.js");
  duck = await Microduck.load({
    onProgress: ({ phase, loaded, total }) => {
      ui.phase.textContent = `${phase} — ${loaded} of ${total}`;
      ui.bar.style.width = `${Math.round((loaded / Math.max(total, 1)) * 100)}%`;
    },
  });
  view = new View(ui.canvas, duck);
  runtime = new Runtime(duck, { onError: (error) => fail(error, "physics") });
  recorder = new Recorder(ui.canvas);
  for (const button of [ui.run, ui.reset, ui.record]) button.disabled = false;
  ui.loading.hidden = true;
  // The hand holds the twist from the first frame: the keyboard works before anyone has
  // touched the switch, a key or the canvas.
  giveTwistTo("hand");
  let previous = performance.now();
  runtime.start(() => {
    const now = performance.now();
    const dt = Math.min(0.1, (now - previous) / 1000);
    previous = now;
    view.frame();
    // Q/E integrate rather than step. Guarded on `manual` so a key still held from before a
    // run cannot fight a `gaze` in flight — the head is plain last-writer-wins, unlike the
    // twist, which the hand re-asserts every control tick.
    if (runtime.manual) {
      const dir = (held.has("KeyQ") ? 1 : 0) - (held.has("KeyE") ? 1 : 0);
      if (dir) duck.setHead((duck.head[0] ?? 0) + dir * HEAD_RATE * dt);
    }
    paintReadout();
  });
}

// ── who is holding the twist ────────────────────────────────────────────────────────────

/**
 * `runtime.manual` is not a mode, it is a lease on the twist, and the invariant is one line:
 *
 *     runtime.manual === (running === null)
 *
 * The two authorities are asymmetric by construction. The hand is re-asserted every 20 ms
 * control tick inside Runtime.start, immediately before `duck.step()` reads the command; the
 * pilot writes at most at 10 Hz from a microtask between ticks. So while `manual` is true,
 * every stray pilot write — the per-verb catch, `move`'s tail, `stop`, `run()`'s finally —
 * is overwritten before the physics can see it. That is what makes barge-in safe without
 * cancelling anything first, and it is why the flag is set synchronously in the keydown
 * handler, before the abort. The abort is bookkeeping; the flag is the handover.
 */
function giveTwistTo(who) {
  if (!runtime) return;
  if (who === "hand") {
    runtime.manual = true;
    runtime.manualTwist = manualTwist(); // resume from what is actually still held
  } else {
    abortHand("quackd took the twist");  // and no kick still settling may write into its log
    held.clear();                        // no stale key may clobber the first verb
    runtime.manualTwist = [0, 0, 0];
    runtime.manual = false;
  }
  paintOwner();
  paintKeycaps();
}

const MOTION = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE"]);

function ownerState() {
  if (running) return "pilot";
  // TEXT_ENTRY, not TYPING: a focused button still drives, so saying the keys are paused
  // would be a lie the moment anyone clicked Reset.
  if (document.activeElement?.matches?.(TEXT_ENTRY)) return "typing";
  for (const code of held) if (MOTION.has(code)) return "hand";
  return "live";
}

// "keys" means the API key everywhere else on this page — the trust pill in the header, bay
// 03 — so the one pill that is about W/A/S/D says keyboard.
const OWNER_TEXT = {
  live: "keyboard ready",
  hand: "you have the twist",
  pilot: "quackd has the twist",
  typing: "keyboard pauses while you type · Esc",
};

/** Early-return on an unchanged state, or this aria-live region announces every keystroke. */
function paintOwner() {
  const state = ownerState();
  if (state === painted) return;
  painted = state;
  ui.owner.dataset.state = state;
  ui.owner.textContent = OWNER_TEXT[state];
}

addEventListener("focusin", paintOwner);
addEventListener("focusout", () => queueMicrotask(paintOwner));

function paintReadout() {
  const fmt = (t) => t.map((v) => v.toFixed(2)).join(", ");
  const asked = duck.commanded, sent = duck.sent;
  // The gait floor is otherwise invisible. A strafe is ALWAYS lifted — GAIT_FLOOR.vy is 0.30
  // and MAX_VY is 0.20 — which is the most legible demonstration on the page of why the model
  // is told to read the pose it reached, not the numbers it sent.
  const differs = sent.some((v, i) => Math.abs(v - asked[i]) > 1e-6);
  ui.readout.textContent =
    (differs ? `asked ${fmt(asked)} → sent ${fmt(sent)}` : `twist ${fmt(sent)}`) +
    ` · ${duck.posture}` +
    (recorder.recording ? ` · ● ${recorder.seconds.toFixed(0)}s` : "");
}

// ── the switch: the language layer, and nothing else ────────────────────────────────────

const ON_NOTE =
  "A model reads your sentence and picks from the robot's own verbs. The executor checks " +
  "every one against the contract before anything moves.";
const OFF_NOTE =
  "Nothing here reads English. The duck takes a twist — three numbers — and you are " +
  "already holding it.";

/**
 * The switch answers one question: is there anything here that can read English? It no
 * longer answers "can you drive?", because that answer is permanently yes. The three lines
 * this function used to have — `ui.manual.hidden`, `runtime.manual = !on` and a
 * `duck.setTwist(0, 0, 0)` — were the whole of the old exclusivity.
 */
function applyToggle() {
  const on = ui.toggle.checked;
  ui.toggleLabel.textContent = on ? "quackd is on" : "quackd is off";
  ui.toggleNote.textContent = on ? ON_NOTE : OFF_NOTE;
  ui.keyBox.hidden = !on;                 // no model is called, so no key is needed
  ui.run.textContent = on ? "Run" : "Send";
  document.body.classList.toggle("no-quackd", !on);
}

ui.toggle.addEventListener("change", () => { abortRun("you switched quackd off"); applyToggle(); });

// ── running ─────────────────────────────────────────────────────────────────────────────

ui.goalForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const goal = ui.goal.value.trim();
  if (!goal) return;
  // Focus follows the action. Without this the caret stays in the input, the guard above
  // swallows W, and the keyboard looks broken at the moment the visitor most wants to try it.
  ui.goal.blur();
  ui.canvas.focus();
  if (!ui.toggle.checked) { refuse(goal); return; }
  if (running) return;
  await run(goal);
});

// Escape is handled once, at the window, ahead of the guard that defers to a focused control.
// It used to be bound to this input alone, which made the pill's "· Esc" true in exactly one
// place on the page.

function refuse(goal) {
  // The whole point of the off switch: say exactly why nothing happened.
  say({ kind: "refused", goal });
}

async function run(goal) {
  let provider;
  try {
    const model = chosenModel();
    provider = makeProvider({
      provider: ui.provider.value,
      key: ui.key.value.trim(),
      model,
      baseUrl: ui.baseUrl.value.trim(),
      // The catalogue knows which OpenAI models refuse function tools on Chat Completions, so
      // the run opens on the API that will answer instead of paying a 400 to find out.
    });
  } catch (error) {
    say({ kind: "end", outcome: "error", reason: error.message });
    return;
  }
  const controller = new AbortController();
  running = controller;
  ui.run.disabled = true;
  ui.stopRun.disabled = false;
  giveTwistTo("pilot");
  try {
    await pilot({
      runtime, goal, provider, contract: DEFAULT_CONTRACT,
      signal: controller.signal, onEvent: say,
    });
  } finally {
    // A barged-in run keeps unwinding after `running` has moved on — an uncancelled fetch can
    // outlive it by seconds. Without this guard the old run's finally steals the twist and the
    // buttons back from whatever started in the meantime.
    if (running === controller) {
      running = null;
      ui.run.disabled = false;
      ui.stopRun.disabled = true;
      giveTwistTo("hand");   // resumes from what is held; no dropped input across the handover
      showShare(goal);
    }
  }
}

/** Each route names itself, so the transcript quotes a reason instead of guessing at one. */
function abortRun(reason = "you stopped the run") {
  running?.abort(new DOMException(reason, "AbortError"));
}

ui.stopRun.addEventListener("click", () => abortRun("you stopped the run"));
ui.reset.addEventListener("click", () => {
  abortRun("you reset the duck");
  abortHand("you reset the duck");
  duck.reset();
  say({ kind: "reset" });
});

/**
 * A drive key pressed during a run takes the robot, now. Everything after the handover is
 * bookkeeping: the twist is already the hand's, and the physics will read it within one
 * control tick whatever the run does on its way out.
 */
function bargeIn(code) {
  const controller = running;
  running = null;                        // the UI leaves limbo immediately
  giveTwistTo("hand");                   // 1. the handover itself
  ui.run.disabled = false;               // 2. buttons, before any await
  ui.stopRun.disabled = true;
  controller.abort(new DOMException(`you took the controls (${label(code)})`, "AbortError"));
  say({ kind: "handover", key: label(code) });  // 3. one line now, not when the run notices
}

// ── the transcript ──────────────────────────────────────────────────────────────────────

function line(className, html) {
  const item = document.createElement("li");
  item.className = className;
  item.innerHTML = html;
  ui.transcript.append(item);
  ui.transcript.scrollTop = ui.transcript.scrollHeight;  // overflow is on the list itself
}

const escape = (value) =>
  String(value).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function say(event) {
  switch (event.kind) {
    case "start":
      ui.transcript.replaceChildren();
      line("goal", `<b>goal</b> ${escape(event.goal)}
        <span class="fine">contract: ${event.contract.allow.length} verbs allowed, ${event.contract.maxSteps} steps</span>`);
      break;
    case "call":
      line("call", `<b>${escape(event.name)}</b>(${escape(JSON.stringify(event.args ?? {}))})`);
      break;
    case "result":
      line(event.ok ? "result" : "result bad", escape(event.summary));
      break;
    case "end":
      line(`end ${event.outcome}`, `<b>${escape(event.outcome)}</b> ${escape(event.reason)}`);
      break;
    case "reset":
      line("note", "the duck is back where it started");
      break;
    // The hand's discrete acts only. W/A/S/D and Q/E write nothing on purpose: one line per
    // keypress would bury the model's reasoning. A verb leaves a line; a twist leaves a
    // number, and the readout is where driving is recorded.
    case "handover":
      line("handover", `<b>you took the controls</b> <kbd>${escape(event.key)}</kbd>`);
      break;
    case "hand":
      line("hand", `<b>hand</b> ${escape(event.text)}`);
      break;
    case "refused":
      line("refused",
        `<b>no brain attached</b> This robot understands a twist — three numbers, ` +
        `<code>vx, vy, wz</code> — and a walking policy that turns them into steps. It has ` +
        `never seen the words <em>${escape(event.goal)}</em>, and nothing in it can turn them ` +
        `into one. Your keys still work; they always did. What is missing is the layer that ` +
        `reads the sentence.`);
      break;
    default:
      break;
  }
}

ui.clear.addEventListener("click", () => ui.transcript.replaceChildren());

// ── the hand ────────────────────────────────────────────────────────────────────────────

// Motor keys move the robot, so they barge in. Read-only keys never do: you may inspect the
// state or change camera mid-run without taking it. That is the whole rule — a key barges in
// if and only if it would move the robot.
const MOTOR = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE", "KeyG", "Space", "KeyK", "KeyR"]);
const READ = new Set(["KeyO", "Digit1", "Digit2"]);
const HOLD = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE", "ShiftLeft", "ShiftRight"]);
const ONCE = new Set(["Space", "KeyK", "KeyR", "KeyG", "KeyO", "Digit1", "Digit2"]);

const LABEL = {
  KeyW: "W", KeyA: "A", KeyS: "S", KeyD: "D", KeyQ: "Q", KeyE: "E",
  KeyG: "G", KeyK: "K", KeyR: "R", KeyO: "O", Space: "Space",
};
const label = (code) => LABEL[code] ?? code;

function manualTwist() {
  // Shift is a modifier, not a motor key: it never barges in and never takes the lease, it
  // only changes what A and D mean. It is tracked in `held` so its own keydown and keyup
  // re-run this mid-hold.
  const strafe = held.has("ShiftLeft") || held.has("ShiftRight");
  let vx = 0, vy = 0, wz = 0;
  if (held.has("KeyW")) vx += SPEED.vx;
  if (held.has("KeyS")) vx -= SPEED.vx;
  if (held.has("KeyA")) strafe ? (vy += SPEED.vy) : (wz += SPEED.wz);
  if (held.has("KeyD")) strafe ? (vy -= SPEED.vy) : (wz -= SPEED.wz);
  return [vx, vy, wz];
}

function paintKeycaps() {
  for (const cap of document.querySelectorAll("[data-code]")) {
    if (!HOLD.has(cap.dataset.code)) continue;   // one-shots flash instead, see flashKey
    cap.classList.toggle("down", held.has(cap.dataset.code));
  }
}

function flashKey(code) {
  const cap = document.querySelector(`[data-code="${code}"]`);
  if (!cap) return;
  cap.classList.add("down");
  setTimeout(() => cap.classList.remove("down"), 180);
}

function pickCamera(name) {
  for (const button of document.querySelectorAll("[data-camera]")) {
    if (button.dataset.camera === name) button.click();
  }
}

/**
 * The hand's own cancellation. K and R are the only two things on the page that keep moving
 * the robot after the key is up — a kick settles for 1.5 s, a stand-up for 1 s — and neither
 * used to be cancellable. Press K, click Run inside 1.5 s, and the kick's verdict landed in
 * the pilot's freshly cleared transcript as a `hand` line; with R the duck was re-placed under
 * a pilot that was mid-verb. The lease says the twist is the run's; this says the same about
 * everything the hand still has in flight.
 */
let handRun = null;

function handSignal() {
  abortHand("superseded by another key");
  handRun = new AbortController();
  return handRun.signal;
}

function abortHand(reason) {
  handRun?.abort(new DOMException(reason, "AbortError"));
  handRun = null;
}

/** An aborted hand action is not a failure; anything else still has to reach the transcript. */
const handFailed = (where) => (error) => {
  if (error?.name !== "AbortError") fail(error, where);
};

async function handKick() {
  const signal = handSignal();
  const connected = duck.kick("right");
  await runtime.sleep(1.5, signal);
  if (!connected) {
    const ball = duck.observe().detections?.find((d) => d.label === "ball");
    const where = ball
      ? `the ball is ${ball.est_distance_m.toFixed(2)} m away at ${ball.bearing_deg.toFixed(0)} degrees`
      : "the camera cannot see the ball";
    say({ kind: "hand", text: `kick missed: ${where} (needs under 0.3 m, roughly ahead)` });
    return;
  }
  const moved = duck.lastKickBallMoved;
  say({ kind: "hand", text: moved === null ? "kicked" : `kicked; the ball moved ${moved.toFixed(2)} m` });
}

async function handStandUp() {
  if (duck.posture !== "fallen") { say({ kind: "hand", text: "stand up — already standing" }); return; }
  const signal = handSignal();
  duck.standUp();
  await runtime.sleep(1.0, signal);
  say({ kind: "hand", text: duck.posture === "standing" ? "stand up — upright" : "stand up — still down" });
}

function handStop() {
  // A latch, not a term in the twist: `held` is emptied, so a physically-held W is dropped
  // and has to be released and pressed again. That is what a panic button is, and the legend
  // says so.
  const moving = duck.sent.some((v) => Math.abs(v) > 1e-6);
  held.clear();
  duck.setTwist(0, 0, 0);
  if (moving) say({ kind: "hand", text: "stopped (velocity zeroed)" });
}

function dispatchOneShot(code) {
  if (ONCE.has(code)) flashKey(code);
  switch (code) {
    case "Space": handStop(); break;
    case "KeyK": handKick().catch(handFailed("kick")); break;
    case "KeyR": handStandUp().catch(handFailed("stand up")); break;
    case "KeyG": duck.setHead(0); break;
    case "KeyO": say({ kind: "hand", text: JSON.stringify(duck.observe()) }); break;
    case "Digit1": pickCamera("follow"); break;
    case "Digit2": pickCamera("duck"); break;
    default: break;
  }
}

addEventListener("keydown", (event) => {
  // Browser shortcuts keep their keys. Without this, Ctrl+W adds KeyW to `held`, never gets
  // a keyup, and the duck walks forward for ever in a tab you thought you had closed.
  if (event.ctrlKey || event.metaKey || event.altKey) return;

  // The pill promises "· Esc", so Escape has to work from anywhere rather than only inside
  // the goal box: whatever holds focus, it hands the keyboard back to the arena.
  if (event.key === "Escape") {
    document.activeElement?.blur?.();
    ui.canvas.focus();
    paintOwner();
    return;
  }

  // A control that has its own use for a key keeps that key — and only that key. A text
  // control keeps the whole keyboard, because W typed into the goal box is a letter. A
  // button, a <summary> or a link keeps Space and Enter, which is what activates it (WCAG
  // 2.1.1) and is all it ever needed: returning here for EVERY key meant that clicking Reset,
  // Save clip or a camera button silently switched the drive keys off, with nothing on the
  // page to say so and no way back that the page had told anyone about.
  const target = event.target;
  if (target?.matches?.(TYPING) && (target.matches(TEXT_ENTRY) || ACTIVATION_KEYS.has(event.key))) {
    return;
  }
  if (!runtime) return;

  const code = event.code;
  const modifier = code === "ShiftLeft" || code === "ShiftRight";
  const motor = MOTOR.has(code);
  if (!motor && !READ.has(code) && !modifier) return;

  if (motor) event.preventDefault();            // Space and WASD must not scroll the page
  if (event.repeat && ONCE.has(code)) return;   // auto-repeat is not ten kicks

  // `held` is updated before the handover so the hand resumes from the key you are actually
  // pressing, and the pill goes straight to `hand` rather than announcing `live` on the way.
  if (HOLD.has(code)) held.add(code);
  if (motor) { if (running) bargeIn(code); else giveTwistTo("hand"); }
  dispatchOneShot(code);
  if (runtime.manual) runtime.manualTwist = manualTwist();
  paintKeycaps();
  paintOwner();
});

addEventListener("keyup", (event) => {
  held.delete(event.code);
  if (runtime?.manual) runtime.manualTwist = manualTwist();
  paintKeycaps();
  paintOwner();
});

// A window that loses focus mid-keypress never receives the keyup, and the duck then walks
// for ever with no key down. Cheap to fix, and far more visible now the keyboard never sleeps.
function releaseEverything() {
  held.clear();
  if (runtime?.manual) { runtime.manualTwist = [0, 0, 0]; duck.setTwist(0, 0, 0); }
  paintKeycaps();
  paintOwner();
}
addEventListener("blur", releaseEverything);
document.addEventListener("visibilitychange", () => { if (document.hidden) releaseEverything(); });

// ── camera, recording, sharing ──────────────────────────────────────────────────────────

for (const button of document.querySelectorAll("[data-camera]")) {
  button.addEventListener("click", () => {
    for (const other of document.querySelectorAll("[data-camera]")) other.classList.remove("on");
    button.classList.add("on");
    view?.setMode(button.dataset.camera);
  });
}

ui.record.addEventListener("click", async () => {
  if (!Recorder.supported) { say({ kind: "end", outcome: "error", reason: "this browser cannot record a canvas" }); return; }
  if (recorder.recording) {
    await recorder.stop();
    ui.record.textContent = "● Record";
    ui.record.classList.remove("live");
    ui.save.disabled = !recorder.blob;
    showShare(ui.goal.value);
  } else {
    recorder.start();
    ui.record.textContent = "■ Stop recording";
    ui.record.classList.add("live");
    ui.save.disabled = true;
  }
});

ui.save.addEventListener("click", () => recorder.save());

function showShare(goal) {
  ui.share.hidden = false;
  ui.share.href = shareUrl(goal, { quackd: ui.toggle.checked });
  ui.share.title = recorder?.blob
    ? "Opens X with the post written. Save the clip first and attach it there."
    : "Opens X with the post written.";
}

// ── providers ───────────────────────────────────────────────────────────────────────────

for (const [id, spec] of Object.entries(PROVIDERS)) {
  const option = document.createElement("option");
  option.value = id;
  option.textContent = spec.label;
  ui.provider.append(option);
}

/**
 * Rebuild #model for one vendor, grouped by status and defaulted to the vendor's own default.
 *
 * The field used to be free text with one id pre-filled, so every other model this build knows
 * was something you had to have read the source to name, and a typo reached the vendor before
 * anything said so. The list is `catalogue.js`, generated from the Python that is the single
 * source of truth, so the page offers exactly what `quackd list-models` does.
 *
 * createElement throughout. A label is vendor copy — "Z.ai GLM 5.2 (hosted by Mistral)" — and
 * innerHTML would make a `<` in one of them markup.
 */
function fillModels(provider) {
  const catalogue = CATALOGUE[provider];
  ui.model.replaceChildren();
  if (!catalogue) return;
  for (const status of STATUS_ORDER) {
    const entries = catalogue.entries.filter((entry) => entry.status === status);
    if (!entries.length) continue;
    const group = document.createElement("optgroup");
    group.label = status;
    for (const entry of entries) {
      const option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.label;
      group.append(option);
    }
    ui.model.append(group);
  }
  ui.model.value = catalogue.default;
}

/** Whichever of the two model controls is the live one. */
function chosenModel() {
  return ui.model.hidden ? ui.modelText.value.trim() : ui.model.value;
}

function applyProvider() {
  const provider = ui.provider.value;
  const spec = PROVIDERS[provider];
  // A vendor quackd keeps a list for gets the list; a local server gets the free-text box,
  // because it serves whatever you pulled and there is no list to keep.
  const fromCatalogue = Boolean(CATALOGUE[provider]);
  fillModels(provider);
  ui.model.hidden = !fromCatalogue;
  ui.modelText.hidden = fromCatalogue;
  if (!fromCatalogue) ui.modelText.value = spec.defaultModel;
  // Cleared on every change, in both directions. Disabling the field left the value readable,
  // so a key pasted for Anthropic was still there when the visitor switched to a local server
  // and went out as a bearer token to whatever host they had typed in the box below.
  ui.key.value = "";
  ui.key.placeholder = spec.keyPlaceholder;
  ui.key.disabled = false;
  ui.baseUrl.hidden = spec.needsKey;
  if (!spec.needsKey) ui.baseUrl.value ||= spec.baseUrl;
  ui.providerNote.textContent = "";
  if (spec.note) {
    ui.providerNote.textContent = spec.note;
  } else {
    // built rather than interpolated: an href is a URL context, and escape() is not enough
    ui.providerNote.append("Need a key? ");
    const link = document.createElement("a");
    link.href = spec.keyUrl;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = spec.label;
    ui.providerNote.append(link, ".");
  }
}

ui.provider.addEventListener("change", applyProvider);
for (const chip of document.querySelectorAll(".chip")) {
  chip.addEventListener("click", () => { ui.goal.value = chip.textContent; ui.goal.focus(); });
}

$("model-link").href = UPSTREAM.model;
applyProvider();
applyToggle();
boot().catch((error) => fail(error, "boot"));
