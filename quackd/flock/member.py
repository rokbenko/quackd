"""One robot in a coordinator flock: a role-driven state machine, not an LLM loop.

(A pilot flock's member is precisely an LLM loop, and it is `quackd/agent/loop.py` unchanged;
see `pilots.py`.)

The member owns its robot's `Executor`, so the `.duck` allowlist, budgets, machine-enforced
abort rules and per-robot transcript apply exactly as in a solo run. Roles arrive over the
bus (SEARCH a wedge, KICK, YIELD, STOP; with 0.4 roles also SPOT and JUDGE); a role change
*preempts* an in-flight composite verb via `FlockPreempted`, which subclasses `SafetyStop`
so the executor re-raises it without recording a failure — being retasked is not a mistake.

A member spells the verbs its robot has: `_pick("walk_to", "go_to")` keeps flock-kick's
transcript byte-identical on a Microduck and finds `go_to` on any other body. A robot that
cannot move never pre-turns and never retreats; a robot that can only look sweeps its head.
"""

from __future__ import annotations

import contextlib
import math
from typing import TYPE_CHECKING, Any

from PIL import Image

from quackd.adapters.base import RestResult, go_to_rest_if_any
from quackd.adapters.manifest import RobotManifest
from quackd.duckfile.schema import DuckFrontmatter
from quackd.flock.bus import Bus
from quackd.flock.capability import eligible_roles
from quackd.flock.messages import (
    BidMsg,
    FlockMessage,
    FlockTask,
    HbMsg,
    Hint,
    HintMsg,
    ResultMsg,
    RoleMsg,
    VerdictMsg,
)
from quackd.log import EventLog, Sink
from quackd.perception.base import Detector
from quackd.safety import (
    Aborted,
    Budget,
    BudgetExceeded,
    Executor,
    Heartbeat,
    SafetyStop,
    allow_all,
)
from quackd.transport.base import frame_head_yaw_of, frames_taken_since
from quackd.verbs.registry import VerbResult, default_registry, registry_from_manifest
from quackd.verbs.watch import withdraw_watch

if TYPE_CHECKING:
    from quackd.flock.transcript import FlockTranscript

MEMBER_TICK_S = 0.05
TURN_RATE = 1.0  # rad/s, matches the composite verbs' scanning rate
JUDGE_OFFSETS_DEG = (0.0, 15.0, -15.0, 30.0, -30.0, 45.0, -45.0, 60.0, -60.0)
SIDESTEP_VY = 0.15
SIDESTEP_S = 1.5


class FlockPreempted(SafetyStop):
    """The flock changed this duck's role mid-verb. Not a failure, just a redirect."""

    outcome = "preempted"
    """Its own word in the log. Left as the base class `aborted` it would read as a kill
    switch, and before `SafetyStop` had a branch at all every handover printed a red ERROR."""


class FlockMember:
    def __init__(
        self,
        name: str,
        contract: DuckFrontmatter,
        transport: Any,
        detector: Detector,
        bus: Bus,
        transcript: FlockTranscript,
        task: FlockTask,
        *,
        hb_period_s: float = 1.0,
        frame_stride: int = 4,
        dry_run: bool = False,
        view: Sink | None = None,
    ) -> None:
        self.name = name
        self.transport = transport
        self.bus = bus
        self.task = task
        self.hb_period_s = hb_period_s
        self._member_transcript = transcript.member(name)
        self.event_log = EventLog(
            record=self._member_transcript.sink,
            observers=[view] if view is not None else [],
        )
        """This robot's narration. The record is unconditional — `ducks/<name>/transcript.jsonl`
        is this member's record the way `runs/<ts>/transcript.jsonl` is a solo run's, and docs
        promise it — and only the observer follows the CLI's log switch."""
        self._frame_stride = frame_stride
        self._frame_counter = 0
        self._frame_seen: Any = None
        """The frame a verb of this member's looked at last (`_on_frame`)."""
        self.budget = Budget(contract.budgets, now=transport.now)
        self.executor = Executor(
            registry=default_registry(),
            transport=transport,
            contract=contract,
            budget=self.budget,
            detector=detector,
            dry_run=dry_run,
            confirm=allow_all,  # a flock has no per-duck terminal; validate enforces confirm=[]
            # flock.jsonl, never the terminal: the log shows the same line as a `note`, so
            # keeping both doubles nothing on any one surface
            log=lambda m: transcript.write("member_log", duck=name, message=m),
            on_frame=self._on_frame,
            event_log=self.event_log,
        )
        self.heartbeat = Heartbeat(transport, self.executor.abort, event_log=self.event_log)
        self.sub = bus.subscribe(name)
        transport.post_sleep = self._control_check
        self.flock_transcript = transcript
        self.mobile = getattr(transport, "mobility", "legged") != "none"
        self.provides: list[str] = []
        self.role: RoleMsg | None = None
        self._pending: list[FlockMessage] = []
        self._preempt = False
        self._done = False
        self._acted_for_role = False
        self._searched_at: float | None = None
        self._bid: BidMsg | None = None
        self._ref: tuple[float, float] | None = None
        """The target's body-frame point at this robot's FIRST sighting (the judge's reference)."""
        self._sighting_deg: float | None = None
        self._hint: Hint | None = None
        self._last_hb = -1e9
        self.steps = 0
        self.verbs_failed = 0
        self.final_status = "running"

    # ── frames (strided so a flock does not write 3x the frames of a solo run) ──────

    def _on_frame(self, img: Any, caption: str) -> None:
        # the frame the verb in flight looked at last: the one `observe` detected on, and the one
        # `search_scan` found the target in, which `_body_bearing` reads the head off
        self._frame_seen = img
        self._frame_counter += 1
        if self._frame_counter % self._frame_stride == 1:
            self._member_transcript.save_frame(img, caption)

    # ── bus plumbing ────────────────────────────────────────────────────────────────

    def _ingest(self) -> None:
        for msg in [*self._pending, *self.sub.drain()]:
            if msg.kind == "HINT" and msg.src != self.name:
                self._hint = msg.hint
            if msg.kind == "ROLE" and msg.duck == self.name:
                is_new = (
                    self.role is None
                    or msg.role != self.role.role
                    or msg.wedge != self.role.wedge
                    or msg.retreat != self.role.retreat
                    or msg.seq != self.role.seq
                )
                # a repeated retreat order is always actionable: we are still too close
                if is_new or msg.retreat:
                    self.role = msg
                    self._acted_for_role = False
                    self._searched_at = None
                if msg.role == "STOP":
                    self._done = True
        self._pending.clear()

    def _control_check(self) -> None:
        """Runs after EVERY sim sleep, including inside composite verbs."""
        self._maybe_hb()  # a duck mid-verb must still sound alive to the watchdog
        fresh = self.sub.drain()
        if fresh:
            self._pending.extend(fresh)
        if self.executor.abort.is_set():
            raise Aborted("flock abort")
        for msg in self._pending:
            if (
                msg.kind == "ROLE"
                and msg.duck == self.name
                and (self.role is None or msg.role != self.role.role)
            ):
                raise FlockPreempted(f"{self.name}: role change to {msg.role}")

    def _publish(self, msg: FlockMessage) -> None:
        self.bus.publish(msg)

    def _maybe_hb(self) -> None:
        now = self.transport.now()
        if now - self._last_hb < self.hb_period_s:
            return
        self._last_hb = now
        self._publish(
            HbMsg(
                t=now,
                src=self.name,
                task_id=self.task.task_id,
                role=self.role.role if self.role else "IDLE",
                steps=self.steps,
            )
        )

    # ── the rest pose ───────────────────────────────────────────────────────────────

    def _note(self, text: str) -> None:
        """One free-text line on both of this member's surfaces, where its executor's own
        notes land: `flock.jsonl` for the flock, this duck's stream for this duck."""
        self.flock_transcript.write("member_log", duck=self.name, message=text)
        self.event_log.emit("note", text=text)

    async def _rest(self) -> RestResult | None:
        """The rest move, narrated. None for a dry run, and for a body with no rest pose.

        Defensive rather than exercised. A coordinator flock is N views of one simulated
        Microduck world by construction (`flock/runner.py` builds the members), and a
        Microduck carries no rest pose at all, so this returns None on every flock that can
        be built today. It is here because a member is handed a transport and should behave
        like the solo loop if it is ever handed an arm, not because anything drives it.

        Called directly on the transport rather than through the executor: this runs at the
        end of every run including the one the flock stopped, and by then the executor's
        abort is set and would cancel the move that puts the arm down."""
        if self.executor.dry_run or getattr(self.transport, "rest_pose", None) is None:
            return None
        self._note("moving to the rest pose")
        parked = await go_to_rest_if_any(self.transport)
        if parked.reached:
            self._note(
                "already at the rest pose" if parked.how == "already" else "at the rest pose"
            )
        else:
            self._note(f"the arm did not reach its rest pose: {parked.reason}")
        return parked

    # ── the loop ────────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            # inside the try: a failed connect must still hit the finally below, or a
            # half-registered duck would freeze the lockstep clock for the whole flock
            await self.transport.connect()
            manifest = getattr(self.transport, "manifest", None)
            if isinstance(manifest, RobotManifest):
                # an adapter: this member's vocabulary is its own robot's (ADR-0017)
                self.executor.registry = registry_from_manifest(manifest, self.transport)
                self.executor.manifest = manifest
            # A member has nothing to look through, since a flock builds no decision LLM, so its
            # body's `watch` is not offered and not announced as something it provides.
            withdraw_watch(self.executor.registry, eyes=None, transport=self.transport)
            self.provides = sorted(self.executor.registry.names())
            # a member starts from the pose it will end at, so what it acts from is the same
            # arm every time rather than wherever the last run put it down. Before the
            # heartbeat, so an arm that will not park aborts instead of joining the flock.
            parked = await self._rest()
            if parked is not None and parked.recorded and not parked.reached:
                raise Aborted(f"{self.name}: the arm did not reach its rest pose: {parked.reason}")
            self.heartbeat.start()
            self.budget.start()
            while not self._done:
                self._ingest()
                if self._done:
                    break
                self._maybe_hb()
                try:
                    await self._act()
                except FlockPreempted:
                    with contextlib.suppress(Exception):
                        await self.transport.stop()
                    continue  # _ingest will pick up the new role
                try:
                    await self.transport.sleep(MEMBER_TICK_S)
                except FlockPreempted:
                    continue
            self.final_status = "stopped"
        except BudgetExceeded as e:
            self.final_status = "budget"
            self._result("budget", str(e))
        except Aborted as e:
            self.final_status = "aborted"
            self._result("aborted", str(e))
        except Exception as e:
            # a crashed member must still tell the flock it is gone, not just vanish
            self.final_status = "error"
            with contextlib.suppress(Exception):
                self._result("aborted", f"member error: {e}")
        finally:
            with contextlib.suppress(Exception):
                # through the wrapper, so this member's record ends with its stop the way a
                # solo run's does rather than with whatever it was doing when it was told to
                await self.executor.logged_transport().stop()
            await self.heartbeat.stop()
            self.sub.close()
            with contextlib.suppress(Exception):
                # after the stop and before the close: the stop holds the arm where it is,
                # and the close is what releases torque, so this is the only window in which
                # putting it down changes whether it falls
                await self._rest()
            with contextlib.suppress(Exception):
                await self.transport.close()  # unregisters from the clock: never wedge time
            if note := getattr(self.transport, "close_note", None):
                self._note(str(note))
            self.flock_transcript.write(
                "member_end", duck=self.name, status=self.final_status, steps=self.steps
            )
            # and again on this duck's own stream: a per-duck record needs an ending too
            self.event_log.emit("member_end", status=self.final_status, steps=self.steps)

    def _result(self, status: str, detail: str = "", ball_moved_m: float | None = None) -> None:
        self._publish(
            ResultMsg(
                t=self.transport.now(),
                src=self.name,
                task_id=self.task.task_id,
                status=status,  # type: ignore[arg-type]
                detail=detail,
                ball_moved_m=ball_moved_m,
            )
        )

    def _pick(self, *names: str) -> str | None:
        """The first of `names` this robot has and may use: flock-kick keeps spelling
        `walk_to`, a robot that only knows `go_to` gets that, a robot with neither, None."""
        for name in names:
            if name in self.executor.registry and self.executor.is_allowed(name):
                return name
        return None

    async def _verb(self, name: str, params: dict[str, Any]) -> VerbResult:
        from quackd.safety import ConfirmDenied, VerbNotAllowed

        self.steps += 1
        try:
            result = await self.executor.run_verb(name, params, source="agent")
        except (VerbNotAllowed, ConfirmDenied) as e:
            # a contract refusal is feedback, not a member crash (mirrors the solo loop)
            result = VerbResult.fail(str(e))
        if not result.ok:
            self.verbs_failed += 1
        self.flock_transcript.write(
            "verb",
            duck=self.name,
            name=name,
            canonical=self.executor.registry.canonical(name),
            params=params,
            ok=result.ok,
            summary=result.summary,
        )
        return result

    # ── geometry: every judgement is in THIS robot's own body frame ─────────────────

    async def _body_bearing(self, camera_bearing_deg: float, frame: Any = None) -> float:
        """A bearing the camera measured in `frame`, from the body: the head's yaw added to it.

        The yaw has to be the head the frame was taken through. It was always read off the state,
        which says where the head points now, and on a body that hands out a frame it took a
        while before, as a Microduck on mediad's `/frame` does, the frame could be older than the
        member's last `gaze` by a frame period and a fetch, so a bearing measured through the old
        head would be added to the new one. A body that knows records the head on the frame
        (`FRAME_HEAD_YAW_DEG`), and that is the yaw used. Every other body draws its frame when
        it is asked for, with the head where the state says, so the state's yaw is the same
        moment, as it always was on the simulator a coordinator flock runs on."""
        head_yaw = frame_head_yaw_of(frame if isinstance(frame, Image.Image) else None)
        if head_yaw is None:
            state = await self.transport.get_state()
            head_yaw = float(state.extras.get("head_yaw_deg") or 0.0)
        return head_yaw + camera_bearing_deg

    @staticmethod
    def _point(body_deg: float, dist: float) -> tuple[float, float]:
        rad = math.radians(body_deg)
        return dist * math.cos(rad), dist * math.sin(rad)

    async def _arena_hint(self, body_deg: float, dist: float, bearing: float) -> HintMsg | None:
        """The target in the ARENA frame from this robot's pose, sim only (no pose, no hint)."""
        state = await self.transport.get_state()
        if state.x is None or state.y is None or state.theta is None:
            return None
        heading = state.theta + math.radians(body_deg)
        return HintMsg(
            t=self.transport.now(),
            src=self.name,
            task_id=self.task.task_id,
            hint=Hint(
                target=self.task.target,
                x_m=round(state.x + dist * math.cos(heading), 3),
                y_m=round(state.y + dist * math.sin(heading), 3),
                by=self.name,
                est_dist_m=round(dist, 3),
                bearing_deg=round(bearing, 1),
            ),
        )

    # ── roles ───────────────────────────────────────────────────────────────────────

    async def _act(self) -> None:
        role = self.role
        if role is None or (self._acted_for_role and role.role != "SEARCH"):
            return
        if role.role == "SEARCH":
            await self._act_search(role)
        elif role.role == "KICK":
            self._acted_for_role = True
            await self._act_kick(role)
        elif role.role == "YIELD":
            self._acted_for_role = True
            await self._act_yield(role)
        elif role.role == "SPOT":
            self._acted_for_role = True
            await self._act_spot()
        elif role.role == "JUDGE":
            self._acted_for_role = True
            await self._act_judge(role)

    async def _pre_turn(self, target_deg: float) -> None:
        turn_verb = self._pick("walk", "move")
        if not self.mobile or turn_verb is None:
            return
        state = await self.transport.get_state()
        theta = state.theta or 0.0
        target = math.radians(target_deg)
        dtheta = math.atan2(math.sin(target - theta), math.cos(target - theta))
        if abs(dtheta) > 0.1:
            await self._verb(
                turn_verb,
                {
                    "vx": 0.0,
                    "wz": TURN_RATE if dtheta >= 0 else -TURN_RATE,
                    "duration_s": min(abs(dtheta) / TURN_RATE, 6.5),
                },
            )

    async def _act_search(self, role: RoleMsg) -> None:
        now = self.transport.now()
        restart_due = (
            self._searched_at is not None and now - self._searched_at >= self.task.restart_s
        )
        if self._acted_for_role and not restart_due:
            return
        self._acted_for_role = True
        self._searched_at = now
        wedge = role.wedge
        hint = self._hint if self.task.frame_hints else None
        if wedge is not None and self.mobile:
            if hint is not None:
                state = await self.transport.get_state()
                if state.x is not None and state.y is not None:
                    await self._pre_turn(
                        math.degrees(math.atan2(hint.y_m - state.y, hint.x_m - state.x))
                    )
                else:
                    await self._pre_turn(wedge.start_deg)
            else:
                await self._pre_turn(wedge.start_deg)
            max_steps = max(1, min(16, math.ceil(wedge.width_deg / self.task.step_deg)))
        else:
            max_steps = 8
        self._frame_seen = None
        result = await self._verb(
            "search_scan",
            {"target": self.task.target, "step_deg": self.task.step_deg, "max_steps": max_steps},
        )
        if not result.ok:
            self._result("search_empty", result.summary)
            return
        detections = result.data.get("detections") or []
        dist = detections[0].get("est_distance_m") if detections else None
        bearing = detections[0].get("bearing_deg") if detections else None
        dist_f = float(dist) if dist is not None else 9.9
        bearing_f = float(bearing) if bearing is not None else 0.0
        # search_scan stops at the frame it found the target in, so that is the last one it saw
        body_deg = await self._body_bearing(bearing_f, self._frame_seen)
        self._sighting_deg = body_deg
        if self._ref is None and dist is not None:
            self._ref = self._point(body_deg, dist_f)
        # only a role preemption may be swallowed here: Aborted/BudgetExceeded must
        # propagate so a dying duck ends with a RESULT instead of placing a bid
        voice = self._pick("quack", "say")
        if voice is not None:
            with contextlib.suppress(FlockPreempted):
                await self._verb(voice, {"text": "quack! ball!"})  # the theatrical sighting
        if not self.task.roles:
            self._bid = BidMsg(
                t=self.transport.now(),
                src=self.name,
                task_id=self.task.task_id,
                ball_dist_m=dist_f,
                bearing_deg=bearing_f,
            )
            self._publish(self._bid)
            return
        manifest = self.executor.manifest
        sheet = manifest.datasheet if manifest is not None else None
        for role_name in eligible_roles(self.task.roles, self.provides, manifest):
            self._bid = BidMsg(
                t=self.transport.now(),
                src=self.name,
                task_id=self.task.task_id,
                ball_dist_m=dist_f,
                bearing_deg=bearing_f,
                role=role_name,
                provides=self.provides,
                # what it says about its own body travels with the bid, so a coordinator
                # judges a role's physical needs itself rather than taking the bidder's word
                datasheet=sheet.model_dump(mode="json") if sheet is not None else None,
                mobility=manifest.mobility if manifest is not None else None,
            )
            self._publish(self._bid)
        if self.task.frame_hints and dist is not None:
            hint_msg = await self._arena_hint(body_deg, dist_f, bearing_f)
            if hint_msg is not None:
                self._publish(hint_msg)

    async def _act_kick(self, role: RoleMsg) -> None:
        approach = await self._verb(
            self._pick("walk_to", "go_to") or "walk_to",
            {"target": self.task.target, "stop_distance": self.task.stop_distance},
        )
        if not approach.ok:
            self._result("miss", f"approach failed: {approach.summary}")
            return
        kick = await self._verb("kick", {"leg": self.task.kick_leg})
        moved = kick.data.get("ball_moved_m")
        if self.task.roles:
            # the actor never evaluates success: step out of the spotter's line and report
            side = self._pick("walk", "move")
            if side is not None:
                with contextlib.suppress(FlockPreempted):
                    await self._verb(side, {"vx": 0.0, "vy": SIDESTEP_VY, "duration_s": SIDESTEP_S})
            self._result("kick_done", kick.summary, ball_moved_m=moved)
            return
        state = await self.transport.get_state()
        total = float(state.extras.get("ball_displacement_m") or 0.0)
        # the contract's criterion is total displacement, so a rally of short kicks counts
        if kick.ok and (
            (moved is not None and moved >= self.task.success_moved_m)
            or total >= self.task.success_moved_m
        ):
            self._result("kicked", kick.summary, ball_moved_m=float(moved or total))
        else:
            self._result("miss", kick.summary, ball_moved_m=moved)

    async def _act_yield(self, role: RoleMsg) -> None:
        await self._verb("stop", {})
        # back off on a coordinator retreat order (measured from world ground truth), or
        # as blind courtesy when our own last ball estimate was inside the ring
        if role.retreat or (self._bid is not None and self._bid.ball_dist_m < role.min_sep_m):
            back = self._pick("walk", "move")
            if back is not None and self.mobile:
                await self._verb(back, {"vx": -0.1, "duration_s": 1.5})
            self._bid = None

    async def _gaze_to(self, body_deg: float) -> None:
        if self._pick("gaze") is None:
            return
        yaw = max(-90.0, min(90.0, body_deg))  # within every robot's gaze range
        with contextlib.suppress(FlockPreempted):
            await self._verb("gaze", {"bearing_deg": yaw})

    async def _look(self) -> tuple[float, float] | None:
        """One fresh frame: the target's body-frame point, or None if not seen.

        Fresh on every body: a frame taken after this member's last head move had been answered,
        on a body that hands out frames it took a while before and says when (`frames_taken_since`),
        which is the rule `go_to` and `search_scan` keep after a move of theirs. A frame from
        before a `gaze` the judge or the spotter had just sent showed the view the head had left.
        Every other body draws its frame when it is asked for, the simulator a coordinator flock
        runs on among them, and is looked at at once, as it always was."""
        observe = self._pick("observe", "get_frame")
        if observe is None:
            return None
        await frames_taken_since(self.transport, self.transport.now(), MEMBER_TICK_S)
        self._frame_seen = None
        result = await self._verb(observe, {})
        dets = [
            d for d in (result.data.get("detections") or []) if d.get("label") == self.task.target
        ]
        if not dets or dets[0].get("est_distance_m") is None:
            return None
        body_deg = await self._body_bearing(
            float(dets[0].get("bearing_deg") or 0.0), self._frame_seen
        )
        self._sighting_deg = body_deg
        return self._point(body_deg, float(dets[0]["est_distance_m"]))

    async def _act_spot(self) -> None:
        """Keep the target in view; the reference frame was fixed at the first sighting."""
        if self._sighting_deg is not None:
            await self._gaze_to(self._sighting_deg)
        point = await self._look()
        if point is not None and self._ref is None:
            self._ref = point

    async def _act_judge(self, role: RoleMsg) -> None:
        """Judge the kick from this robot's own fresh frames: zero LLM, detector arithmetic."""
        threshold = self.task.success_moved_m + self.task.judge_margin_m
        base = self._sighting_deg if self._sighting_deg is not None else 0.0
        best: float | None = None
        seen: tuple[float, float] | None = None
        frames = 0
        for offset in JUDGE_OFFSETS_DEG:
            await self._gaze_to(base + offset)
            point = await self._look()
            frames += 1
            if point is not None and self._ref is not None:
                moved = math.hypot(point[0] - self._ref[0], point[1] - self._ref[1])
                if best is None or moved > best:
                    best, seen = moved, point
                if moved >= threshold:
                    break
            await self.transport.sleep(0.1)  # fresh frames, and a heartbeat opportunity
        if self._ref is None or best is None:
            verdict = "lost"
        elif best >= threshold:
            verdict = "moved"
        else:
            verdict = "not_moved"
        self._publish(
            VerdictMsg(
                t=self.transport.now(),
                src=self.name,
                task_id=self.task.task_id,
                target=self.task.target,
                kicker=role.kicker or "",
                verdict=verdict,  # type: ignore[arg-type]
                moved_m=round(best, 3) if best is not None else None,
                ref={"x_m": round(self._ref[0], 3), "y_m": round(self._ref[1], 3)}
                if self._ref is not None
                else {},
                seen={"x_m": round(seen[0], 3), "y_m": round(seen[1], 3)} if seen else None,
                frames=frames,
            )
        )
        if seen is not None:
            self._sighting_deg = math.degrees(math.atan2(seen[1], seen[0]))
