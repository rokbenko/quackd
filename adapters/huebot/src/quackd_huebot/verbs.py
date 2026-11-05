"""Huebot's verbs: the family's (`quackd_microduck.verbs`), bound to its body.

`gaze` and `watch` are not here: they send `robot.look`, which Huebot's shim does not answer yet,
so a later release adds them once its maker says it does. `grab` is not here either: it sends
`ground_pick`, and it waits for Huebot's maker to say what `ground_pick` does on Huebot and how
its state reports that the robot holds something. The three that send `robot.do` are offered on a
connected Huebot only for the skills its shim lists (`SKILLS_OF`).
"""

from __future__ import annotations

from quackd_huebot import HUEBOT_VERBS, huebot_conditions
from quackd_huebot.body import SKILLS_OF
from quackd_microduck.body import KICK_SKILLS, SIT_TOGGLE
from quackd_microduck.verbs import STAND_UP_POLL_S, STAND_UP_WAIT_S, KickParams, QuackParams

sit = HUEBOT_VERBS["sit"].execute
stand = HUEBOT_VERBS["stand"].execute
kick = HUEBOT_VERBS["kick"].execute
stand_up = HUEBOT_VERBS["stand_up"].execute

__all__ = [
    "HUEBOT_VERBS",
    "KICK_SKILLS",
    "SIT_TOGGLE",
    "SKILLS_OF",
    "STAND_UP_POLL_S",
    "STAND_UP_WAIT_S",
    "KickParams",
    "QuackParams",
    "huebot_conditions",
    "kick",
    "sit",
    "stand",
    "stand_up",
]
