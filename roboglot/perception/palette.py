"""What a mock's camera draws: the colours the colour detector is tuned to, and its lens.

Every adapter's mock serves frames painted from these, a floor, a sky above a horizon and an
orange ball, so `roboglot.perception.color_blob` finds the ball in a mock's frame as it would
in a real camera's, and its distance estimate comes out in metres through `focal_px`.
"""

from __future__ import annotations

import math

# Colours are part of the contract: the detector's HSV ranges are tuned to these.
FLOOR = (236, 229, 212)
SKY = (204, 222, 240)
BALL = (255, 140, 0)  # orange — hue ≈ 33°

CAM_FOV_DEG = 90.0
"""The lens a mock's frame is drawn through, the one `color_blob.DEFAULT_FOV_DEG` assumes."""
HORIZON = 0.45  # fraction of frame height


def focal_px(width: int, fov_deg: float = CAM_FOV_DEG) -> float:
    return (width / 2) / math.tan(math.radians(fov_deg) / 2)
