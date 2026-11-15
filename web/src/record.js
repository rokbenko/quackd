/**
 * Record what just happened, and make it easy to show someone.
 *
 * `MediaRecorder` over the canvas's own stream, so the clip is exactly the pixels that were
 * on screen, recorded in the browser and never uploaded anywhere. X's web intent can carry
 * text and a link but not a file, so the flow is: save the clip, open the composer with the
 * post already written, attach the clip. The page says so rather than pretending otherwise.
 */

const REPO = "https://github.com/rokbenko/quackd";

export class Recorder {
  constructor(canvas) {
    this.canvas = canvas;
    this.recorder = null;
    this.chunks = [];
    this.blob = null;
    this.startedAt = 0;
  }

  static get supported() {
    return typeof MediaRecorder !== "undefined" && typeof HTMLCanvasElement.prototype.captureStream === "function";
  }

  get recording() { return this.recorder?.state === "recording"; }

  start() {
    if (this.recording) return;
    const stream = this.canvas.captureStream(30);
    const preferred = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
    const mimeType = preferred.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
    this.chunks = [];
    this.blob = null;
    this.recorder = new MediaRecorder(stream, mimeType ? { mimeType, videoBitsPerSecond: 4_000_000 } : undefined);
    this.recorder.ondataavailable = (event) => { if (event.data.size) this.chunks.push(event.data); };
    this.recorder.start(200);
    this.startedAt = performance.now();
  }

  /** Stop and hand back the clip, or null if nothing was captured. */
  stop() {
    return new Promise((resolve) => {
      if (!this.recorder || this.recorder.state === "inactive") { resolve(null); return; }
      this.recorder.onstop = () => {
        this.blob = this.chunks.length ? new Blob(this.chunks, { type: this.chunks[0].type }) : null;
        resolve(this.blob);
      };
      this.recorder.stop();
    });
  }

  get seconds() { return this.recording ? (performance.now() - this.startedAt) / 1000 : 0; }

  save(name = "roboglot-microduck.webm") {
    if (!this.blob) return;
    const url = URL.createObjectURL(this.blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}

/**
 * The post, with what the viewer actually typed in it. Short on purpose: a claim, the thing
 * that makes it interesting, and somewhere to go. X counts any link as 23 characters.
 */
export function shareText(goal, { roboglot = true } = {}) {
  const asked = (goal || "walk in a square").trim().replace(/\s+/g, " ").slice(0, 60);
  if (!roboglot) {
    return `A Microduck without a brain: I typed "${asked}" and nothing happened, because a robot understands three numbers, not English. Roboglot is the missing layer — try it in your browser:`;
  }
  return `I typed "${asked}" and a Microduck did it 🦆🧠 Roboglot turns a sentence into the robot's own skills, and decides what it is allowed to do. Runs in your browser with your own API key:`;
}

export function shareUrl(goal, options) {
  const params = new URLSearchParams({ text: shareText(goal, options), url: REPO });
  return `https://x.com/intent/post?${params.toString()}`;
}
