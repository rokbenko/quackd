#!/usr/bin/env python3
"""Roboglot's AlohaMini host: upstream's own host, with the arms switched on.

Run this on the robot instead of `python -m lerobot.robots.alohamini.alohamini_host`. It is
upstream's host loop, unmodified, with three things done around it:

1. **Arm torque is enabled after connect.** Upstream's `configure()` disables torque on both
   arm buses and both of its `enable_torque()` calls are commented out, so a stock robot's
   arms are limp: a joint command moves nothing and a stop cannot hold. Nothing else in the
   driver turns it back on.
2. **The lift is stopped once, immediately, before any torque.** `home()` runs during connect,
   drives the lift down at full speed, and the write that would zero that register afterwards
   is commented out. Until something writes a zero, the lift is still travelling, and it
   shares the left arm bus, so its zero goes out before either arm bus is energised.
3. **Every observation says so**, with three `roboglot_` fields. Roboglot refuses the arm verbs
   unless it can see them, which is how a stock host is told apart from this one.

This file must never import roboglot: Roboglot's dependencies do not belong on a robot. It ships
in the sdist and never in the wheel. It is plain standard library plus whatever upstream
already needed, and the two functions that do the work take any object with the right
attributes, so they are tested with fakes and no robot.

Requires upstream installed on the robot, at the commit Roboglot was read against. A host from
upstream's main refuses every payload Roboglot sends without a lift height, its stop among them
(docs/adapters/alohamini/README.md):
    git clone https://github.com/liyiteng/lerobot_alohamini
    cd lerobot_alohamini
    git checkout ab4462b713aeb24d0473f1ec6c8812290ab19510
    pip install -e ".[all]"
    python roboglot_alohamini_host.py --robot_model alohamini2
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

VERSION = "1"
"""Bumped when the fields below change. Roboglot reads it and refuses a version it cannot read."""
DESCRIPTION = "Roboglot's AlohaMini host: upstream's own host, with the arms switched on."
"""What `--help` opens with: this module docstring's first line, spelled out because `python -OO`
strips docstrings, and building it from `__doc__` died before a flag was parsed."""

FIELD_TORQUE = "roboglot_arm_torque"
FIELD_CALIBRATED = "roboglot_calibrated"
FIELD_VERSION = "roboglot_host_version"


def enable_arm_torque(robot: Any) -> bool:
    """Stop the lift that homing left travelling, and then turn the arms back on.

    Returns whether both arm buses reported torque enabled. Takes any object with `left_bus`,
    an optional `right_bus` and a `lift`, so a test can drive it with fakes.

    The lift's zero goes out first, before any torque, on every path. The lift shares the left
    bus, and its zero used to come after both buses were energised, so the lift met torque
    with homing's full-speed descent still in its register, a raise from the right bus skipped
    the zero altogether, and a robot with no left bus was never zeroed at all. If the zero
    itself fails, nothing is energised and the error goes up."""
    lift = getattr(robot, "lift", None)
    if lift is not None:
        # home() left full speed descent in the register and never zeroed it
        lift.stop()
    left = getattr(robot, "left_bus", None)
    right = getattr(robot, "right_bus", None)
    if left is None:
        return False
    left.enable_torque()
    if right is not None:
        right.enable_torque()
    return True


def annotate(observation: dict[str, Any], robot: Any, *, torque: bool) -> dict[str, Any]:
    """Add the three fields Roboglot looks for. Everything else is upstream's, untouched."""
    observation[FIELD_TORQUE] = bool(torque)
    observation[FIELD_CALIBRATED] = bool(getattr(robot, "is_calibrated", False))
    observation[FIELD_VERSION] = VERSION
    return observation


def install(robot_cls: Any) -> None:
    """Wrap `connect` and `get_observation` on upstream's own class, before anything runs.

    Wrapping rather than reimplementing is deliberate: upstream's loop reads the cameras, the
    currents and the over-current trip, and transcribing that would mean owning a copy of it
    that drifts. The two wrappers are additive and nothing else changes."""
    if getattr(robot_cls, "_roboglot_installed", False):
        return
    original_connect = robot_cls.connect
    original_observation = robot_cls.get_observation

    def connect(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_connect(self, *args, **kwargs)
        self._roboglot_torque = enable_arm_torque(self)
        logging.info(
            "Roboglot host: arm torque %s, %s",
            "enabled" if self._roboglot_torque else "NOT enabled",
            # enable_arm_torque zeroes a lift before anything else or raises, so a lift that is
            # there has been stopped by now, and a robot with none must not be told it was
            "lift stopped" if getattr(self, "lift", None) is not None else "no lift to stop",
        )
        return result

    def get_observation(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        observation = original_observation(self, *args, **kwargs)
        return annotate(observation, self, torque=getattr(self, "_roboglot_torque", False))

    robot_cls.connect = connect
    robot_cls.get_observation = get_observation
    robot_cls._roboglot_installed = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--check", action="store_true", help="Install the wrappers and exit.")
    args, rest = parser.parse_known_args(argv)

    try:
        from lerobot.robots.alohamini import alohamini_host
        from lerobot.robots.alohamini.alohamini import AlohaMini
    except ImportError as e:
        # stderr rather than print: bridge code follows the same no-print rule as the package
        sys.stderr.write(
            "Roboglot's AlohaMini host needs upstream installed on this robot, at the commit\n"
            "Roboglot was read against, since a host from upstream's main refuses Roboglot's "
            "stop:\n  git clone https://github.com/liyiteng/lerobot_alohamini\n"
            "  cd lerobot_alohamini\n"
            "  git checkout ab4462b713aeb24d0473f1ec6c8812290ab19510\n"
            '  pip install -e ".[all]"\n'
            f"({e})\n"
        )
        return 2

    install(AlohaMini)
    if args.check:
        sys.stderr.write(f"Roboglot alohamini host v{VERSION}: wrappers installed, not starting\n")
        return 0
    # upstream's own main() parses its own arguments from sys.argv
    sys.argv = [sys.argv[0], *rest]
    alohamini_host.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
