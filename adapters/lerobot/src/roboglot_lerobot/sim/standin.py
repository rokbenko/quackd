"""A primitives-only arm shaped like an SO-101, for wherever the real model cannot be fetched.

CI's physics job fetches nothing, and no test anywhere else does either, so the maker's model
is out of reach wherever the simulator is tested. This arm stands in for it there. It has what
the simulator's code reaches into on either model: six hinge joints and six position
actuators carrying the names LeRobot gives the SO-101's motors, a gripper body with a fixed
finger and a moving jaw on a hinge of its own, so that a grasp is possible, and a body named
as upstream names the wrist camera's, so the wrist view hangs from the same name on both.
Everything is a box, a capsule or a cylinder, so there is nothing to fetch and nothing of
anyone else's to ship.

It is shaped like the arm and is not a model of it. Its sizes come from the one figure Roboglot
publishes about the SO-101's shape, the datasheet's reach, and its joint ranges from the
manifest's own joint limit. Its gripper range, masses and servo gains are its own, chosen so a
joint follows its goal, and none of them is a measurement of an arm. Nor is its travel: the
simulator gives it a generic calibration built from these ranges, never a real arm's file.

Its fingers are the boxes the simulator's scene expects to find on the gripper, under the names
`model.py` gives them, so the contact settings Roboglot lays over any model reach them here as
they reach the pads it cuts for the real one.

Nothing here imports `mujoco`: this module writes text.
"""

from __future__ import annotations

import math

from roboglot_lerobot import REACH, lerobot_manifest
from roboglot_lerobot.sim import upstream_api as up
from roboglot_lerobot.sim.model import FIXED_PAD, MOVING_PAD, PALM_PAD
from roboglot_lerobot.verbs import JOINTS

UPPER_ARM = 0.30
FOREARM = 0.35
WRIST = 0.10
HAND = 0.25
"""Each link's share of the reach, from the shoulder_lift axis to the fingertips. They add up to
one, so the stand-in reaches exactly as far as the datasheet says an SO-101 does."""
PEDESTAL = 0.25
"""The shoulder_lift axis's height above the table, as a share of the reach."""
LINK_RADIUS = 0.05
"""Each link's capsule radius, as a share of the reach: about the size of a servo."""
FINGER = 0.6
"""How much of the hand is finger. The rest is the palm and the servo housing behind it."""
PALM = 0.1
FINGER_THICKNESS = 0.08
FINGER_WIDTH = 0.2
"""The palm's depth and each finger's thickness and width, as shares of the hand's length."""
FINGER_GAP_M = 0.002
"""How far apart the fingers stay at the closed end. They never meet, so a gripper closed on
nothing stops at its closed end instead of pressing one finger into the other."""
GRIPPER_OPEN_DEG = 60.0
"""The moving jaw's swing from closed to open, the stand-in's own choice. Its fingertips then
stand apart by the finger's length times sin(60°), wide enough for the scene's default cube."""
CAMERA_SIZE = 0.08
"""The wrist camera body's half width, as a share of the hand's length."""

LINK_MASS_KG = 0.1
WRIST_MASS_KG = 0.05
HAND_MASS_KG = 0.1
JAW_MASS_KG = 0.01
"""Masses of the stand-in's own choosing, about what an SO-101's links weigh, so gravity pulls
on it much as on the real arm and a limp joint falls."""
KP = 30.0
KV = 1.5
FORCE_NM = 3.0
"""Each servo's proportional gain (N m/rad), its damping (N m s/rad) and its force limit (N m).
The gain holds the arm straight out against gravity about a degree and a half short of its
goal, well inside what a verb calls arrived, the damping keeps the shoulder from ringing, and
the force limit is about four times what holding the arm straight out takes."""
JOINT_DAMPING = 0.1
ARMATURE = 0.01
"""A little friction and rotor inertia in every joint, so that a limp one falls without
bouncing off its stop and the explicit integrator stays steady on the lightest links."""

ARM_RGBA = "0.62 0.63 0.65 1"
DARK_RGBA = "0.25 0.26 0.28 1"
"""Greys: nothing on the arm is a colour a detector looks for."""


def _n(*values: float) -> str:
    return " ".join(f"{v:.12g}" for v in values)


def mjcf() -> str:
    """The stand-in's MJCF, sized from the datasheet's reach and ranged from the manifest.

    At zero every joint is upright, the arm pointing straight up, and a positive shoulder_lift
    tips it forward along +x, the side of the table the scene lays objects on. The gripper's
    closed end is the bottom of its range, and nothing in the simulator is told so: it finds
    the closed end from the fingers, as it does on the real model."""
    reach = float(REACH.value)
    limit = math.radians(float(lerobot_manifest("mujoco").limits["joint_deg"]))
    upper, fore, wrist, hand = (share * reach for share in (UPPER_ARM, FOREARM, WRIST, HAND))
    pedestal = PEDESTAL * reach
    radius = LINK_RADIUS * reach
    finger = FINGER * hand
    palm = PALM * hand
    thick = FINGER_THICKNESS * hand
    width = FINGER_WIDTH * hand
    camera = CAMERA_SIZE * hand
    housing = hand - finger - palm
    half_gap = FINGER_GAP_M / 2
    body = {name: f'name="{name}" range="{_n(-limit, limit)}"' for name in JOINTS[:-1]}
    open_ = math.radians(GRIPPER_OPEN_DEG)
    ctrl = {name: _n(-limit, limit) for name in JOINTS[:-1]} | {JOINTS[-1]: _n(0.0, open_)}
    actuators = "\n".join(
        f'    <position name="{name}" joint="{name}" ctrlrange="{ctrl[name]}"/>' for name in JOINTS
    )
    return f"""<mujoco model="roboglot-standin-arm">
  <compiler angle="radian" autolimits="true"/>
  <default>
    <joint type="hinge" damping="{_n(JOINT_DAMPING)}" armature="{_n(ARMATURE)}"/>
    <position kp="{_n(KP)}" kv="{_n(KV)}" forcerange="{_n(-FORCE_NM, FORCE_NM)}"/>
    <geom rgba="{ARM_RGBA}"/>
  </default>
  <worldbody>
    <body name="base">
      <geom name="pedestal" type="cylinder" fromto="0 0 0 {_n(0, 0, pedestal / 2)}"
            size="{_n(2 * radius)}" rgba="{DARK_RGBA}" mass="{_n(LINK_MASS_KG)}"/>
      <body name="turret" pos="{_n(0, 0, pedestal / 2)}">
        <joint axis="0 0 1" {body["shoulder_pan"]}/>
        <geom type="cylinder" fromto="0 0 0 {_n(0, 0, pedestal / 2)}" size="{_n(radius)}"
              mass="{_n(LINK_MASS_KG)}"/>
        <body name="upper_arm" pos="{_n(0, 0, pedestal / 2)}">
          <joint axis="0 1 0" {body["shoulder_lift"]}/>
          <geom type="capsule" fromto="0 0 0 {_n(0, 0, upper)}" size="{_n(radius)}"
                mass="{_n(LINK_MASS_KG)}"/>
          <body name="forearm" pos="{_n(0, 0, upper)}">
            <joint axis="0 1 0" {body["elbow_flex"]}/>
            <geom type="capsule" fromto="0 0 0 {_n(0, 0, fore)}" size="{_n(radius)}"
                  mass="{_n(LINK_MASS_KG)}"/>
            <body name="wrist" pos="{_n(0, 0, fore)}">
              <joint axis="0 1 0" {body["wrist_flex"]}/>
              <geom type="capsule" fromto="0 0 0 {_n(0, 0, wrist)}" size="{_n(radius)}"
                    mass="{_n(WRIST_MASS_KG)}"/>
              <body name="hand" pos="{_n(0, 0, wrist)}">
                <joint axis="0 0 1" {body["wrist_roll"]}/>
                <geom name="housing" type="box" pos="{_n(0, 0, housing / 2)}"
                      size="{_n(half_gap + thick, width / 2, housing / 2)}"
                      mass="{_n(HAND_MASS_KG)}"/>
                <geom name="{PALM_PAD}" type="box" pos="{_n(0, 0, housing + palm / 2)}"
                      size="{_n(half_gap + thick, width / 2, palm / 2)}" rgba="{DARK_RGBA}"
                      mass="0"/>
                <geom name="{FIXED_PAD}" type="box"
                      pos="{_n(-half_gap - thick / 2, 0, hand - finger / 2)}"
                      size="{_n(thick / 2, width / 2, finger / 2)}" rgba="{DARK_RGBA}"
                      mass="0"/>
                <body name="{up.WRIST_CAMERA_BODY}"
                      pos="{_n(0, width / 2 + camera, housing / 2)}">
                  <geom type="box" size="{_n(camera, camera, camera)}" rgba="{DARK_RGBA}"
                        mass="0.001"/>
                </body>
                <body name="jaw" pos="{_n(half_gap + thick / 2, 0, hand - finger)}">
                  <joint name="{JOINTS[-1]}" axis="0 1 0" range="{_n(0.0, open_)}"/>
                  <geom name="{MOVING_PAD}" type="box" pos="{_n(0, 0, finger / 2)}"
                        size="{_n(thick / 2, width / 2, finger / 2)}" rgba="{DARK_RGBA}"
                        mass="{_n(JAW_MASS_KG)}"/>
                </body>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
  <actuator>
{actuators}
  </actuator>
</mujoco>
"""
