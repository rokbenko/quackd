/**
 * The Microduck, in the browser: upstream's MuJoCo model on upstream's walking policy.
 *
 * This is the Python `quackd/sim3d/` package with the same numbers and the same honesty,
 * compiled to WebAssembly instead of C. MuJoCo comes from `@mujoco/mujoco` (Google
 * DeepMind's official WASM bindings, Apache-2.0), the policy runs in onnxruntime-web, and
 * both the model and the policy are fetched from upstream at run time and never served
 * from this site: the meshes are CC BY-NC-SA, and quackd redistributes nothing.
 *
 * The control loop is upstream's own `scripts/infer_policy.py`, cited fact by fact in
 * `quackd/sim3d/upstream_api.py`: a 61-value observation, a 14-value action, 50 Hz, and
 * position targets of `default_pose + action`. Two numbers here were measured rather than
 * read, and are marked where they are used: the gait floor and the fraction of a command
 * the duck actually achieves.
 *
 * Three things differ from Python on purpose, and `web/README.md` says so too. Perception is
 * geometric rather than a colour detector over a rendered frame. Nothing fetched is checked
 * against a hash. And the seeded arena uses a different generator, so a seed means the same
 * distributions here, not the same layout.
 */

const RL_PIN = "2b25a48b08f1f17bc38c90bb03144c81fbd9ed07"; // microduck_rl develop, 2026-09-06
const POLICY_REV = "6677ec007142bbaf05123a468f6ae58d13808786"; // microduck-policies v7, 2026-10-05
const RAW = `https://raw.githubusercontent.com/pollen-robotics/microduck_rl/${RL_PIN}/src/mjlab_microduck/robot/microduck`;
const POLICIES = `https://huggingface.co/pollen-robotics/microduck-policies/resolve/${POLICY_REV}`;

export const UPSTREAM = {
  model: `https://github.com/pollen-robotics/microduck_rl/tree/${RL_PIN}`,
  policies: `https://huggingface.co/pollen-robotics/microduck-policies/tree/${POLICY_REV}`,
  meshLicence: "3D model files are licensed under Creative Commons BY-SA-NC",
};

// ── the contract, from upstream ────────────────────────────────────────────────────────
const JOINTS = [
  "left_hip_yaw", "left_hip_roll", "left_hip_pitch", "left_knee", "left_ankle",
  "neck_pitch", "head_pitch", "head_yaw", "head_roll",
  "right_hip_yaw", "right_hip_roll", "right_hip_pitch", "right_knee", "right_ankle",
];
const HOME = new Float32Array([
  0, -0.0872665, -0.457924, -0.004940, 0.452984,
  0.3490659, 0.3490659, 0, 0,
  0, 0.0872665, 0.457924, 0.004940, -0.452984,
]);
export const CONTROL_DT = 0.02;   // 50 Hz
const PHYSICS_DT = 0.005;         // four substeps a tick
const DECIMATION = 4;
const OBS = 61, NJ = 14, CMD = 13;
const STAND_SWITCH = 0.05;        // upstream's --switch-threshold

// ── measured here, not read from upstream (quackd/sim3d/upstream_api.py GAIT_THRESHOLD) ──
export const GAIT_FLOOR = { vx: 0.23, vy: 0.30, wz: 1.0 };
// What fraction of a commanded twist the body actually delivers. Python holds the same
// number in quackd/sim3d/gait.py; the prompt quotes it rather than rounding it to "half".
export const ACHIEVED_FRACTION = 0.38;
// What the world accepts before the gait floor sees it, from quackd/sim3d/world.py. The verb
// schemas advertise the same numbers; this is the floor under a model that ignores them.
export const MAX_VX = 0.3, MAX_VY = 0.2, MAX_WZ = 1.5;
const CMD_MAX = { vx: 0.40, vy: 0.30, wz: 1.50 };
const DEAD_FRACTION = 1 / 3;

// ── the arena: quackd/sim3d/scene.py's dimensions, not its scene ───────────────────────
// Same half-width, same walls, same ball. Not the same look: sim3d builds upstream's own
// scene*.xml palette, a blue-grey edge-marked checker under a gradient skybox, and this
// fetches robot_walk.xml alone and draws a flat plane under a headlight.
// web/README.md carries that as a deliberate divergence.
//
// Nobody is in this arena, and nobody is in Python's either: the person marker both once
// stood up was removed from the 3D worlds together. The 2D cartoon still has one, so a task
// about a person — `follow-me` — is a 2D task and neither of these can run it.
export const ARENA_HALF = 1.0;
const BALL_R = 0.05, WALL_H = 0.08;
const DEADMAN_S = 0.3;
const KICK_RANGE = 0.30, KICK_CONE_DEG = 35, KICK_SPEED = 1.2;
const FALL_TILT = -0.5, FALL_HEIGHT = 0.06, FALL_DEBOUNCE = 10;
export const HEAD_YAW_LIMIT = (60 * Math.PI) / 180;
const HEAD_PITCH_LIMIT = (35 * Math.PI) / 180;

/** A small deterministic generator, so a seed lays out the same arena every time. */
/**
 * xorshift32, seeded. Every shift is unsigned: `>>` sign-extends once the state passes 2^31,
 * which quietly made this a different generator from the one it looks like.
 *
 * It is not numpy's PCG64 either, and it is not trying to be. The same seed lays out a
 * different arena here than in Python; what the two share is the distributions and the
 * rejection rules, not the stream.
 */
function rng(seed) {
  let s = (seed >>> 0) || 1;
  return () => {
    s ^= s << 13; s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5; s >>>= 0;
    return s / 4294967296;
  };
}

function arenaXml(ball) {
  const lim = ARENA_HALF + 0.02;
  const wall = (n, px, py, sx, sy) =>
    `<geom name="quackd_wall_${n}" type="box" pos="${px} ${py} ${WALL_H}" size="${sx} ${sy} ${WALL_H}" rgba="0.55 0.55 0.58 1"/>`;
  return `<mujoco model="quackd arena">
  <include file="robot_walk.xml"/>
  <option timestep="${PHYSICS_DT}" gravity="0 0 -9.81"/>
  <visual><headlight ambient="0.5 0.5 0.5" diffuse="0.6 0.6 0.6" specular="0 0 0"/></visual>
  <worldbody>
    <geom name="quackd_floor" type="plane" size="${ARENA_HALF + 0.5} ${ARENA_HALF + 0.5} 0.1"
          rgba="0.82 0.82 0.80 1" friction="0.8 0.005 0.0001"/>
    ${wall("east", lim, 0, 0.02, lim)}
    ${wall("west", -lim, 0, 0.02, lim)}
    ${wall("north", 0, lim, lim, 0.02)}
    ${wall("south", 0, -lim, lim, 0.02)}
    <body name="ball" pos="${ball[0]} ${ball[1]} ${BALL_R}">
      <joint name="ball_free" type="free"/>
      <geom name="ball_geom" type="sphere" size="${BALL_R}" rgba="1 0.55 0 1" condim="6"
            mass="0.05" friction="0.8 0.005 0.002"/>
    </body>
  </worldbody>
</mujoco>`;
}

async function fetchWithProgress(url, onBytes) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    total += value.length;
    onBytes?.(value.length);
  }
  const out = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) { out.set(chunk, at); at += chunk.length; }
  return out;
}

export class Microduck {
  /**
   * Fetch everything and compile. `onProgress({phase, loaded, total})` is called often
   * enough to drive a bar; the byte total is an estimate until the last file lands.
   */
  static async load({ onProgress = () => {}, seed = 6 } = {}) {
    // The URL is a variable so the same module can be exercised under Node, where an https
    // import is not a thing. In a browser nobody sets it and it is the CDN.
    const wasmModule = globalThis.QUACKD_MUJOCO_MODULE
      ?? "https://cdn.jsdelivr.net/npm/@mujoco/mujoco@3.12.0/mujoco.js";
    const loadMujoco = (await import(wasmModule)).default;
    onProgress({ phase: "MuJoCo", loaded: 0, total: 1 });
    const mujoco = await loadMujoco();

    const xml = await (await fetch(`${RAW}/robot_walk.xml`)).text();
    const meshes = [...xml.matchAll(/<mesh file="([^"]+)"/g)].map((m) => m[1]);
    const vfs = new mujoco.MjVFS();
    vfs.addBuffer("robot_walk.xml", new TextEncoder().encode(xml));
    let done = 0;
    // Sequential on purpose: a progress bar that moves is worth more than a second saved,
    // and twenty-two megabytes of meshes is the part of the wait people actually see.
    for (const file of meshes) {
      vfs.addBuffer(`assets/${file}`, await fetchWithProgress(`${RAW}/assets/${file}`));
      done += 1;
      onProgress({ phase: "upstream's model", loaded: done, total: meshes.length });
    }

    onProgress({ phase: "the walking policy", loaded: 0, total: 2 });
    const ort = globalThis.ort;
    ort.env.wasm.numThreads = 1; // a static site sends no COOP/COEP headers
    ort.env.wasm.wasmPaths ||= "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.29.0/dist/";
    const walk = await ort.InferenceSession.create(await fetchWithProgress(`${POLICIES}/alpha_walking.onnx`));
    onProgress({ phase: "the walking policy", loaded: 1, total: 2 });
    const stand = await ort.InferenceSession.create(await fetchWithProgress(`${POLICIES}/alpha_stand.onnx`));
    onProgress({ phase: "the walking policy", loaded: 2, total: 2 });

    const random = rng(seed);
    const dx = () => (random() - 0.5) * 0.6;
    const duck = [dx(), dx(), (random() - 0.5) * 2 * Math.PI];
    // Bounded like Python's, which stops after a thousand draws. Unbounded, a generator that
    // ever stopped producing usable numbers would hang the page during load with the overlay
    // still up and nothing to say why.
    const place = (spread, ok) => {
      let at = [0, 0];
      for (let i = 0; i < 1000; i++) {
        at = [(random() - 0.5) * spread, (random() - 0.5) * spread];
        if (ok(at)) break;
      }
      return at;
    };
    const ball = place(1.5, (b) => Math.hypot(b[0] - duck[0], b[1] - duck[1]) >= 0.5);

    const model = mujoco.MjModel.from_xml_string(arenaXml(ball), vfs);
    const data = new mujoco.MjData(model);
    return new Microduck({ mujoco, model, data, walk, stand, duck, ball, random });
  }

  constructor({ mujoco, model, data, walk, stand, duck, ball, random }) {
    Object.assign(this, { mujoco, model, data, walk, stand, random });
    this.spawn = duck;
    this.ballStart = ball;
    const id = (kind, name) => mujoco.mj_name2id(model, mujoco.mjtObj[kind].value, name);
    this.trunk = id("mjOBJ_BODY", "trunk_base");
    this.qadr = JOINTS.map((n) => model.jnt(n).qposadr);
    this.vadr = JOINTS.map((n) => model.jnt(n).dofadr);
    this.gyro = model.sensor("imu_ang_vel").adr;
    this.freeQ = model.jnt("trunk_base_freejoint").qposadr;
    this.freeV = model.jnt("trunk_base_freejoint").dofadr;
    this.ballQ = model.jnt("ball_free").qposadr;
    this.ballV = model.jnt("ball_free").dofadr;
    this.obs = new Float32Array(OBS);
    this.lastAction = new Float32Array(NJ);
    this.cmd = new Float32Array(CMD);
    this.commanded = [0, 0, 0];
    this.sent = [0, 0, 0];
    this.head = [0, 0];
    this.t = 0;
    this.cmdAge = 0;
    this.downTicks = 0;
    this.posture = "standing";
    this.kicks = 0;
    this.kicksConnected = 0;
    this.kickOrigin = null;
    this.reset();
  }

  /** Put the robot back on its feet where it is. Nothing else in the world is touched. */
  placeBody(x, y, theta) {
    const { data, mujoco, model } = this;
    const q = data.qpos;
    q[this.freeQ] = x; q[this.freeQ + 1] = y; q[this.freeQ + 2] = 0.125;
    q[this.freeQ + 3] = Math.cos(theta / 2);
    q[this.freeQ + 4] = 0; q[this.freeQ + 5] = 0;
    q[this.freeQ + 6] = Math.sin(theta / 2);
    for (let i = 0; i < 6; i++) data.qvel[this.freeV + i] = 0;
    for (let j = 0; j < NJ; j++) {
      q[this.qadr[j]] = HOME[j];
      data.qvel[this.vadr[j]] = 0;
      data.ctrl[j] = HOME[j];
    }
    this.lastAction.fill(0);
    this.posture = "standing";
    this.downTicks = 0;
    this.setTwist(0, 0, 0);
    mujoco.mj_forward(model, data);
  }

  /** Start the whole episode again: the robot, the ball, the clock and the tally. */
  reset() {
    const { data, mujoco, model } = this;
    mujoco.mj_resetData(model, data);
    const q = data.qpos;
    q[this.ballQ] = this.ballStart[0]; q[this.ballQ + 1] = this.ballStart[1]; q[this.ballQ + 2] = BALL_R;
    q[this.ballQ + 3] = 1; q[this.ballQ + 4] = 0; q[this.ballQ + 5] = 0; q[this.ballQ + 6] = 0;
    this.t = 0;
    this.kicks = 0; this.kicksConnected = 0; this.kickOrigin = null;
    // The head is a standing command, not part of the state mj_resetData clears, and it does
    // NOT belong in placeBody — standUp() reuses that mid-run and must not re-centre a gaze.
    // Without this line the transcript said "back where it started" while buildObs fed the
    // pre-reset yaw straight back in on the next tick and observe() went on reporting it.
    this.head = [0, 0];
    this.placeBody(...this.spawn);
  }

  // ── what a pilot may ask for ─────────────────────────────────────────────────────

  /** A body-frame twist. Re-send it at least every 0.3 s or the deadman zeroes it. */
  setTwist(vx, vy, wz) {
    // Clipped to the envelope the world accepts before the gait floor sees it, exactly as
    // `MujocoWorld.set_velocity` does. Nothing clamped here at all, so a model that ignored
    // the schema could ask for any speed it liked.
    const clip = (v, limit) => (Number.isFinite(v) ? Math.max(-limit, Math.min(limit, v)) : 0);
    this.commanded = [clip(vx, MAX_VX), clip(vy, MAX_VY), clip(wz, MAX_WZ)];
    this.cmdAge = 0;
  }

  /** Aim the camera. Yaw and pitch in radians, clamped; returns true if it clamped. */
  setHead(yaw, pitch = 0) {
    const clamped = Math.abs(yaw) > HEAD_YAW_LIMIT || Math.abs(pitch) > HEAD_PITCH_LIMIT;
    const clip = (v, lim) => Math.max(-lim, Math.min(lim, v));
    this.head = [clip(yaw, HEAD_YAW_LIMIT), clip(pitch, HEAD_PITCH_LIMIT)];
    return clamped;
  }

  /**
   * The kick is quackd's, not upstream's: `ball_kick_left.onnx` did nothing from a
   * standing pose when it was tried, so this is the cartoon's rule — an impulse on the
   * ball when it is inside 0.30 m of the head and within a 35 degree cone.
   */
  /** A normal deviate from the seeded stream, by Box-Muller. */
  gaussian(mean, sigma) {
    const u = Math.max(this.random(), Number.EPSILON);
    return mean + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * this.random());
  }

  kick(leg = "right") {
    this.kicks += 1;
    if (this.posture !== "standing") return false;
    const { distance, bearing } = this.relative(this.ballX, this.ballY);
    this.kickOrigin = [this.ballX, this.ballY];
    if (distance > KICK_RANGE || Math.abs((bearing * 180) / Math.PI) > KICK_CONE_DEG) return false;
    this.kicksConnected += 1;
    // Gaussian with sigma six degrees, as sim2d and sim3d both use. A uniform +/- 5.7 was a
    // different distribution wearing the same numbers, so a kick here missed differently.
    const skew = this.gaussian(0, (6 * Math.PI) / 180) + (leg === "left" ? 0.05 : -0.05);
    const angle = this.pose.theta + skew;
    this.data.qvel[this.ballV] = KICK_SPEED * Math.cos(angle);
    this.data.qvel[this.ballV + 1] = KICK_SPEED * Math.sin(angle);
    this.data.qvel[this.ballV + 2] = 0;  // Python zeroes it: a kicked ball rolls, it does not hop
    return true;
  }

  /** Upstream ships no get-up policy, so recovery stands the model up where it lies. */
  standUp() {
    if (this.posture !== "fallen") return;
    // The body only. This used to call reset(), which re-placed the ball, set the clock back
    // to zero and cleared the kick tally: the run's time budget went permanently negative
    // because it is measured from a start time, and every kick the pilot had landed was
    // erased along with the evidence it would cite. Python's `enable()` moves the robot and
    // nothing else.
    const { x, y, theta } = this.pose;
    this.placeBody(x, y, theta);
  }

  // ── one 50 Hz control tick ───────────────────────────────────────────────────────

  step() {
    const { data, mujoco, model } = this;
    this.cmdAge += CONTROL_DT;
    if (this.cmdAge > DEADMAN_S) this.commanded = [0, 0, 0]; // robotd's deadman
    const twist = this.usableTwist(this.commanded);
    this.sent = twist;
    this.buildObs(twist);
    const session = Math.hypot(...twist) <= STAND_SWITCH ? this.stand : this.walk;
    const out = session.run({ obs: new globalThis.ort.Tensor("float32", this.obs, [1, OBS]) });
    return out.then((result) => {
      const action = result.actions.data;
      this.lastAction.set(action);
      for (let j = 0; j < NJ; j++) data.ctrl[j] = HOME[j] + action[j];
      for (let s = 0; s < DECIMATION; s++) mujoco.mj_step(model, data);
      this.t += CONTROL_DT;
      this.updatePosture();
    });
  }

  /**
   * The commanded twist, mapped onto what the gait can do. The floor belongs to the twist
   * as a whole: a duck already walking turns happily at a rate that would not start a turn
   * on its own, so the whole vector is scaled rather than each axis raised, which is what
   * keeps an arc an arc instead of turning "walk in a circle" into a spin.
   */
  usableTwist([vx, vy, wz]) {
    if (this.posture !== "standing") return [0, 0, 0];
    const activity = Math.max(
      Math.abs(vx) / GAIT_FLOOR.vx, Math.abs(vy) / GAIT_FLOOR.vy, Math.abs(wz) / GAIT_FLOOR.wz);
    if (activity < DEAD_FRACTION) return [0, 0, 0]; // too small to step; standing is honest
    const gain = activity < 1 ? 1 / activity : 1;
    const clip = (v, max) => Math.sign(v) * Math.min(Math.abs(v) * gain, max);
    return [clip(vx, CMD_MAX.vx), clip(vy, CMD_MAX.vy), clip(wz, CMD_MAX.wz)];
  }

  buildObs(twist) {
    const { data, obs } = this;
    const R = data.body(this.trunk).xmat;
    let i = 0;
    for (let a = 0; a < 3; a++) obs[i++] = data.sensordata[this.gyro + a];
    obs[i++] = -R[6]; obs[i++] = -R[7]; obs[i++] = -R[8]; // projected gravity
    for (let j = 0; j < NJ; j++) obs[i++] = data.qpos[this.qadr[j]] - HOME[j];
    for (let j = 0; j < NJ; j++) obs[i++] = data.qvel[this.vadr[j]];
    for (let j = 0; j < NJ; j++) obs[i++] = this.lastAction[j];
    this.cmd.fill(0);
    this.cmd[0] = twist[0]; this.cmd[1] = twist[1]; this.cmd[2] = twist[2];
    this.cmd[4] = -this.head[1]; // a positive head-pitch command tilts the camera down
    this.cmd[5] = this.head[0];
    for (let c = 0; c < CMD; c++) obs[i++] = this.cmd[c];
  }

  updatePosture() {
    if (this.posture === "sitting") return;
    const down = this.gravityZ > FALL_TILT || this.data.qpos[this.freeQ + 2] < FALL_HEIGHT;
    this.downTicks = down ? this.downTicks + 1 : 0;
    if (this.downTicks >= FALL_DEBOUNCE) this.posture = "fallen";
    else if (!down && this.posture === "fallen") this.posture = "standing";
  }

  // ── what it knows about itself ───────────────────────────────────────────────────

  get gravityZ() { return -this.data.body(this.trunk).xmat[8]; }
  get ballX() { return this.data.qpos[this.ballQ]; }
  get ballY() { return this.data.qpos[this.ballQ + 1]; }

  get pose() {
    const q = this.data.qpos, a = this.freeQ;
    const [w, x, y, z] = [q[a + 3], q[a + 4], q[a + 5], q[a + 6]];
    return { x: q[a], y: q[a + 1], theta: Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)) };
  }

  get ballDisplacement() {
    return Math.hypot(this.ballX - this.ballStart[0], this.ballY - this.ballStart[1]);
  }

  get lastKickBallMoved() {
    if (!this.kickOrigin) return null;
    return Math.hypot(this.ballX - this.kickOrigin[0], this.ballY - this.kickOrigin[1]);
  }

  /** Where the head camera is and which way it looks. Upstream's camera quaternion is not
   *  MuJoCo's viewing convention, so forward is the camera frame's +z, not its -z. */
  headPose() {
    const cam = this.model.cam ? 0 : 0; // one camera in the model
    const p = this.data.cam_xpos, m = this.data.cam_xmat;
    const forward = [m[2], m[5], m[8]];
    return {
      x: p[0], y: p[1], z: p[2],
      yaw: Math.atan2(forward[1], forward[0]),
      pitch: Math.asin(Math.max(-1, Math.min(1, forward[2]))),
    };
  }

  /** Distance and bearing of a world point from the front of the duck, where the camera is
   *  and where a kick connects — the same choice the Python world makes. */
  relative(x, y, { camera = false } = {}) {
    const head = this.headPose();
    const dx = x - head.x, dy = y - head.y;
    const heading = camera ? head.yaw : this.pose.theta;
    let bearing = Math.atan2(dy, dx) - heading;
    bearing = Math.atan2(Math.sin(bearing), Math.cos(bearing));
    return { distance: Math.hypot(dx, dy), bearing };
  }

  /** What a pilot sees each turn: features, never pixels. */
  observe() {
    const { x, y, theta } = this.pose;
    const ball = this.relative(this.ballX, this.ballY, { camera: true });
    const inView = (r) => Math.abs(r.bearing) < Math.PI / 4 && r.distance < 1.6;
    return {
      pose: { x: +x.toFixed(3), y: +y.toFixed(3), theta: +theta.toFixed(3) },
      posture: this.posture,
      sim_time: +this.t.toFixed(2),
      twist_sent: this.sent.map((v) => +v.toFixed(2)),
      head_yaw_deg: Math.round((this.head[0] * 180) / Math.PI),
      detections: [
        inView(ball) && { label: "ball", bearing_deg: Math.round((ball.bearing * 180) / Math.PI), est_distance_m: +ball.distance.toFixed(2) },
      ].filter(Boolean),
      ball_moved_m: +this.ballDisplacement.toFixed(2),
    };
  }
}
