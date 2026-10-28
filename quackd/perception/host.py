"""A detector whose model runs on the board `--host` names: YOLO on a Jetson's GPU, reached
from the laptop.

The frame is the one the run already has, the primary view that every other detector reads.
It goes to the board as one JPEG, the board's daemon runs its YOLO on it and sends back boxes in
that JPEG's pixels, and the geometry is done here by `detections_from_boxes`, the same function
the in-process `YoloDetector` uses. So the same boxes are the same detections whichever machine
ran the model, and the daemon never has to know quackd's labels or its lens.

Its `detect` is synchronous, like every `Detector`, and says it blocks (`blocking`), so the
verbs and the loop run it in a worker thread (`perception.base.detect_off_loop`): a round trip
to a board must not hold up the event loop that re-sends a walking body's twist. That keeps the
keepalives flowing, and no more: `go_to` holds its last twist for one deadman window
(`verbs.core.HOLD_TTL_S`) and then sends a zero one, so a board slower than that stops the body
between frames, a body with no deadman of its own included, which is the right thing for a
steering loop that has stalled.

Each wait on the board's socket is bounded by `quackd.host.DETECT_TIMEOUT_S`. Two waits are not,
as `HostClient._exchange` says: a name that has stopped resolving is looked up on the system
resolver's own clock, before there is a socket, and is paid again on every call, and a reply
trickled a byte at a time can outlast the timeout. Both happen in the worker thread, and a call
that fails gives its frame no detections.

A board that fails is not a reason to pick another detector. A failed call returns no
detections and keeps the reason in `error`, and the run goes on with this detector: the colour
detector does not label the same things on a real camera, so switching to it quietly would
change what `go_to` steers at with nothing in the record saying so, and the header would name a
detector the run had stopped using. Nor is a frame the board could not read taken for an empty
room: a verb that steers on detections reads `error` after every frame, so `go_to` and
`search_scan` stop the body on the first such frame and fail with the board's reason
(`verbs.core.detector_failed`), and `observe` reports nothing seen, with the reason beside it.

What each call cost is kept too, because whether that round trip is fast enough inside `go_to`'s
steering loop is the question a board has to answer (`bridge/jetson/README.md`): how long the
laptop waited from encoding the frame to reading the answer, and the `ms` the board says it
spent in the model, which splits that wait into the GPU's share and the rest. `take_timing`
hands them over and starts counting again, and the loop's observations and the executor's
`verb_end` write them into the record (`perception.base.detect_times`).

Nothing here has run against a Jetson. The tests drive it against the fake daemon in
`tests/fake_jetson_hostd.py`, which proves the protocol is read as written and says nothing
about a board.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from PIL import Image

from quackd.host import HostClient, HostError, confidence_floor
from quackd.perception.base import Detection
from quackd.perception.yolo import LABELS, detections_from_boxes

logger = logging.getLogger("quackd.perception")

JPEG_QUALITY = 85
"""What a frame is sent to the board at. Near the camera daemon's own 80, so a frame the host
camera took is not made worse on the way back, and small enough that a 640x480 frame is tens of
kilobytes on a robot's Wi-Fi rather than hundreds."""

GUESSED_FOV_DEG = 62.0
"""The lens assumed when nobody said which one this is: a Pi Camera Module 2's, the same guess
`YoloDetector` makes. Detections made with it carry `calibrated=False`."""


@dataclass
class _Tally:
    """The detect calls since `take_timing` was last asked, as sums and maxima, so a session
    that nobody asks keeps a few numbers and not one per frame."""

    calls: int = 0
    failed: int = 0
    answered: int = 0
    round_trip_sum_s: float = 0.0
    round_trip_max_s: float = 0.0
    timed: int = 0
    board_sum_ms: float = 0.0
    board_max_ms: float = 0.0

    def event(self) -> dict[str, Any]:
        """The `detect` block of the event that records these calls. A mean or maximum of
        nothing is None, as for a stretch where every call failed or no answer carried `ms`."""
        return {
            "calls": self.calls,
            "failed": self.failed,
            "round_trip_s_mean": (
                round(self.round_trip_sum_s / self.answered, 3) if self.answered else None
            ),
            "round_trip_s_max": round(self.round_trip_max_s, 3) if self.answered else None,
            "board_ms_mean": round(self.board_sum_ms / self.timed, 1) if self.timed else None,
            "board_ms_max": round(self.board_max_ms, 1) if self.timed else None,
        }


class HostDetector:
    """YOLO on the board `--host` names, as a `Detector`.

    `fov_deg` and `calibrated` are the lens the detections are measured through, as
    `YoloDetector` carries them. `calibrate()` sets both from what is known once the body has
    connected. `conf` overrides the daemon's own confidence floor, and None keeps the
    daemon's.

    `error` is None while the board answers, and the reason it did not the last time it did not.
    It is what `observe` and the loop read to say why nothing was seen."""

    name = "yolo@host"
    labels = LABELS
    """The board's boxes become a local model's labels (`detections_from_boxes`)."""
    blocking = True
    """`detect` waits on the network, so `detect_off_loop` runs it in a worker thread."""
    lens_at_connect = False
    """True on one `explicit_detector` built before the body connected, whose lens the loop and
    the MCP server set again from what the body reported. One built anywhere else keeps the
    lens it was built with."""

    def __init__(
        self,
        client: HostClient,
        *,
        fov_deg: float,
        calibrated: bool = True,
        conf: float | None = None,
    ) -> None:
        # checked here, once, because `HostClient.detect` raises ValueError for a bad floor on
        # every call and a caller mistake should surface when the detector is built, not as the
        # first frame of a run failing
        if conf is not None:
            confidence_floor(conf)
        self.client = client
        self.fov_deg = fov_deg
        self.calibrated = calibrated
        self.conf = conf
        self.error: str | None = None
        self._told_lens: str | None = None
        # `detect` runs in a worker thread and `take_timing` on the event loop's
        self._timing_lock = threading.Lock()
        self._tally = _Tally()

    def __repr__(self) -> str:
        return f"HostDetector({self.client.address!r}, fov_deg={self.fov_deg:g})"

    @property
    def address(self) -> str:
        """The board this detector asks, as `host:port`."""
        return self.client.address

    def calibrate(
        self, fov_deg: float | None, *, backend: str | None, assumed: str | None = None
    ) -> None:
        """Set the lens from what is known about the camera in front of the run.

        `fov_deg` is the field of view somebody gave (`--fov-deg`, the body's own
        `camera_fov_deg`, or the one the board's daemon was started with when its camera is the
        primary view). With none on a simulator, the simulator's own camera is known and the
        detections are calibrated. With none on a real body, the lens is a guess, and with one
        the body assumed (`assumed`) it is that one: the detections still point the right way,
        carry `calibrated=False`, and this says so once, as the colour detector does."""
        from quackd.perception import lens, tell_lens

        self.fov_deg, self.calibrated = lens(
            fov_deg, backend, guess=GUESSED_FOV_DEG, assumed=assumed
        )
        tell_lens(
            self,
            backend,
            f"no camera field of view given for a {backend} camera, so the host detector at "
            f"{self.client.address} uses {self.fov_deg:.0f} degrees. Distances will be out by "
            "tens of percent: pass --fov-deg (a Pi Camera Module 2 is about 62) once you know "
            "yours.",
            assumed=assumed,
        )

    def detect(self, image: Image.Image) -> list[Detection]:
        """The board's YOLO on this frame, as quackd's detections, or none when the board did
        not answer.

        A failure is warned about once per outage rather than once per frame, because `go_to`
        asks ten times a second and a log of the same sentence ten times a second hides the
        sentence. The next answer ends the outage, so a board that fails again is warned
        about again.

        An answer for an image of another size is a failure too. The boxes are pixels in the
        image the daemon decoded, which is the frame sent, so a reply that names another size
        describes some other image, and read against this frame its boxes would be bearings
        `go_to` steers at with nothing true about them."""
        started = time.perf_counter()
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
        try:
            found = self.client.detect(buf.getvalue(), conf=self.conf)
        except HostError as e:
            return self._failed(str(e))
        round_trip_s = time.perf_counter() - started
        sent = image.size
        if (found.w, found.h) != sent:
            return self._failed(
                f"{self.client.address} answered for a {found.w}x{found.h} image, not the "
                f"{sent[0]}x{sent[1]} frame it was sent"
            )
        with self._timing_lock:
            tally = self._tally
            tally.calls += 1
            tally.answered += 1
            tally.round_trip_sum_s += round_trip_s
            tally.round_trip_max_s = max(tally.round_trip_max_s, round_trip_s)
            if found.ms is not None:
                tally.timed += 1
                tally.board_sum_ms += found.ms
                tally.board_max_ms = max(tally.board_max_ms, found.ms)
        if self.error is not None:
            logger.warning("the host detector at %s answers again", self.client.address)
            self.error = None
        return detections_from_boxes(
            (box.as_tuple() for box in found.boxes),
            found.w,
            found.h,
            fov_deg=self.fov_deg,
            calibrated=self.calibrated,
        )

    def take_timing(self) -> dict[str, Any] | None:
        """What the frames sent to the board since the last call cost, and a fresh count from
        here, or None when none was sent.

        `calls` is every frame sent and `failed` the ones the board did not answer, or
        answered for another image (`error` says why). Over the rest, `round_trip_s_mean` and
        `round_trip_s_max` are the laptop's wait from encoding the frame to reading the answer,
        which is what a steering loop pays per frame, and `board_ms_mean` and `board_ms_max`
        the time the board says it spent in the model."""
        with self._timing_lock:
            tally, self._tally = self._tally, _Tally()
        return tally.event() if tally.calls else None

    def _failed(self, reason: str) -> list[Detection]:
        """No detections for this frame, the reason kept in `error`, and one warning for the
        outage it starts."""
        with self._timing_lock:
            self._tally.calls += 1
            self._tally.failed += 1
        if self.error is None:
            logger.warning(
                "the host detector at %s failed, so this frame has no detections and the "
                "run keeps this detector: %s",
                self.client.address,
                reason,
            )
        self.error = reason
        return []
