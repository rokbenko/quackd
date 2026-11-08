#!/usr/bin/env python3
"""quackd's AlohaMini host: upstream's own host, with the arms switched on.

Run this on the robot instead of `python -m lerobot.robots.alohamini.alohamini_host`. It is
upstream's host loop, unmodified, with three things done around it:

1. **Arm torque is enabled after connect.** Upstream's `configure()` disables torque on both
   arm buses and both of its `enable_torque()` calls are commented out, so a stock robot's
   arms are limp: a joint command moves nothing and a stop cannot hold. Nothing else in the
   driver turns it back on.
2. **The lift is stopped once, immediately.** `home()` runs during connect, drives the lift
   down at full speed, and the write that would zero that register afterwards is commented
   out. Until something writes a zero, the lift is still travelling.
3. **Every observation says so**, with three `quackd_` fields. quackd refuses the arm verbs
   unless it can see them, which is how a stock host is told apart from this one.

This file must never import quackd: quackd's dependencies do not belong on a robot. It ships
in the sdist and never in the wheel. It is plain standard library plus whatever upstream
already needed, and the two functions that do the work take any object with the right
attributes, so they are tested with fakes and no robot.

Requires upstream installed on the robot, at the commit quackd was read against. A host from
upstream's main refuses every payload quackd sends without a lift height, its stop among them
(docs/adapters/alohamini/README.md):
    git clone https://github.com/liyiteng/lerobot_alohamini
    cd lerobot_alohamini
    git checkout ab4462b713aeb24d0473f1ec6c8812290ab19510
    pip install -e ".[all]"
    python quackd_alohamini_host.py --robot_model alohamini2
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

VERSION = "1"
"""Bumped when the fields below change. quackd reads it and refuses a version it cannot read."""

FIELD_TORQUE = "quackd_arm_torque"
FIELD_CALIBRATED = "quackd_calibrated"
FIELD_VERSION = "quackd_host_version"


def enable_arm_torque(robot: Any) -> bool:
    """Turn the arms back on, and stop the lift that homing left travelling.

    Returns whether both arm buses reported torque enabled. Takes any object with `left_bus`,
    an optional `right_bus` and a `lift`, so a test can drive it with fakes."""
    left = getattr(robot, "left_bus", None)
    right = getattr(robot, "right_bus", None)
    if left is None:
        return False
    left.enable_torque()
    if right is not None:
        right.enable_torque()
    lift = getattr(robot, "lift", None)
    if lift is not None:
        # home() left full speed descent in the register and never zeroed it
        lift.stop()
    return True


def annotate(observation: dict[str, Any], robot: Any, *, torque: bool) -> dict[str, Any]:
    """Add the three fields quackd looks for. Everything else is upstream's, untouched."""
    observation[FIELD_TORQUE] = bool(torque)
    observation[FIELD_CALIBRATED] = bool(getattr(robot, "is_calibrated", False))
    observation[FIELD_VERSION] = VERSION
    return observation


def install(robot_cls: Any) -> None:
    """Wrap `connect` and `get_observation` on upstream's own class, before anything runs.

    Wrapping rather than reimplementing is deliberate: upstream's loop reads the cameras, the
    currents and the over-current trip, and transcribing that would mean owning a copy of it
    that drifts. The two wrappers are additive and nothing else changes."""
    if getattr(robot_cls, "_quackd_installed", False):
        return
    original_connect = robot_cls.connect
    original_observation = robot_cls.get_observation

    def connect(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_connect(self, *args, **kwargs)
        self._quackd_torque = enable_arm_torque(self)
        logging.info(
            "quackd host: arm torque %s, lift stopped",
            "enabled" if self._quackd_torque else "NOT enabled",
        )
        return result

    def get_observation(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        observation = original_observation(self, *args, **kwargs)
        return annotate(observation, self, torque=getattr(self, "_quackd_torque", False))

    robot_cls.connect = connect
    robot_cls.get_observation = get_observation
    robot_cls._quackd_installed = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Install the wrappers and exit.")
    args, rest = parser.parse_known_args(argv)

    try:
        from lerobot.robots.alohamini import alohamini_host
        from lerobot.robots.alohamini.alohamini import AlohaMini
    except ImportError as e:
        # stderr rather than print: bridge code follows the same no-print rule as the package
        sys.stderr.write(
            "quackd's AlohaMini host needs upstream installed on this robot, at the commit\n"
            "quackd was read against, since a host from upstream's main refuses quackd's stop:\n"
            "  git clone https://github.com/liyiteng/lerobot_alohamini\n"
            "  cd lerobot_alohamini\n"
            "  git checkout ab4462b713aeb24d0473f1ec6c8812290ab19510\n"
            '  pip install -e ".[all]"\n'
            f"({e})\n"
        )
        return 2

    install(AlohaMini)
    if args.check:
        sys.stderr.write(f"quackd alohamini host v{VERSION}: wrappers installed, not starting\n")
        return 0
    # upstream's own main() parses its own arguments from sys.argv
    sys.argv = [sys.argv[0], *rest]
    alohamini_host.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
