"""`mapd`, the Microduck's onboard map, as quackd reaches it.

For now, the numbers the simulators' stand-in for it keeps to (`sim_map_params`), every one read
from what quackd read of the real mapper (`mapd_upstream_api`) and robotd (`upstream_api`), so a
simulated walk stops, waits and is lost the way a real one would be.
"""

from __future__ import annotations

import math

from quackd.space.simmap import SimMapParams
from quackd_microduck import mapd_upstream_api as mapd
from quackd_microduck import upstream_api as up


def sim_map_params(footprint_m: float) -> SimMapParams:
    """The real mapper's numbers for a simulated one, and `footprint_m`, the simulator's own
    body radius, which the real mapper does not publish."""
    return SimMapParams(
        still_s=mapd.SETTLE_S,
        still_speed_mps=mapd.STILL_SPEED_MPS,
        still_turn_rps=mapd.STILL_TURN_RPS,
        period_s=mapd.STREAM_PERIOD_S,
        reloc_stops=mapd.AGREE_STOPS,
        gate_m=mapd.CORRECTION_M,
        gate_rad=math.radians(mapd.CORRECTION_DEG),
        res_m=mapd.GRID_RES_M,
        tof_fov_rad=math.radians(up.TOF_FOV_DEG),
        tof_min_m=mapd.TOF_MIN_M,
        tof_range_m=mapd.TOF_MAX_M,
        footprint_m=footprint_m,
    )
