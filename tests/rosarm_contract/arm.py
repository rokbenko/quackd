"""A stand-in for an industrial arm's ROS 2 driver, for the rosarm contract job and nothing else.

It is no arm and no controller. It publishes `sensor_msgs/msg/JointState` for the joints it is
given, in an order other than theirs, and serves `control_msgs/action/FollowJointTrajectory` on
the action it is given: a goal that names exactly its joints is taken, and the joints move from
where they are to the goal's last point along the same minimum jerk curve the suite's fake arm
plays (`quackd_rosarm.fake.minimum_jerk`), over that point's `time_from_start`. A cancel is taken
and the joints hold where it found them; a goal sent over a running one ends the running one
ABORTED, as Jazzy's JointTrajectoryController does; and with `active` false every goal is
rejected, as an inactive controller rejects them.

The action server is rclpy's own, so the bridge, the action's status topic and everything that
crosses the socket are ROS 2's; what is done with a goal is this script's. That is why the job it
serves proves the transport, roslibpy and rosbridge, and nothing about a JointTrajectoryController
or MotoROS2 (tests/test_rosarm_contract.py).

It runs on the distribution's own Python, 3.10 on Humble and 3.12 on Jazzy, with rclpy and
control_msgs from the image, so it uses nothing newer than 3.10. Its parameters:

    joints        the controller's joint names, in its order
    positions     where each joint starts, in radians (zeros where none is given)
    action        the FollowJointTrajectory action's name
    joint_states  the topic the readings go out on
    rate_hz       how often they go out
    active        false rejects every goal
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

import rclpy
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import JointState

STEP_S = 0.01
"""How often a running goal moves the joints and says where they are."""


def minimum_jerk(share: float) -> float:
    """How far along a segment that starts and ends at rest one is at `share` of its time."""
    s = min(1.0, max(0.0, share))
    return s * s * s * (10.0 - 15.0 * s + 6.0 * s * s)


def seconds(duration: Any) -> float:
    return float(duration.sec) + float(duration.nanosec) * 1e-9


class StandIn(Node):
    def __init__(self) -> None:
        super().__init__("rosarm_stand_in")
        self.declare_parameter("joints", ["joint_1"])
        self.declare_parameter("positions", [0.0])
        self.declare_parameter("action", "/follow_joint_trajectory")
        self.declare_parameter("joint_states", "/joint_states")
        self.declare_parameter("rate_hz", 100.0)
        self.declare_parameter("active", True)
        self.joints = list(self.get_parameter("joints").value)
        given = list(self.get_parameter("positions").value)
        start = (given + [0.0] * len(self.joints))[: len(self.joints)]
        self.positions = {name: float(v) for name, v in zip(self.joints, start, strict=True)}
        self.active = bool(self.get_parameter("active").value)
        self.lock = threading.Lock()
        self.running: Any = None
        """The goal handle whose move is being played, which a newer goal replaces."""
        group = ReentrantCallbackGroup()
        self.readings = self.create_publisher(
            JointState, str(self.get_parameter("joint_states").value), 10
        )
        self.create_timer(
            1.0 / float(self.get_parameter("rate_hz").value), self.publish, callback_group=group
        )
        self.server = ActionServer(
            self,
            FollowJointTrajectory,
            str(self.get_parameter("action").value),
            execute_callback=self.execute,
            goal_callback=self.take,
            cancel_callback=lambda _handle: CancelResponse.ACCEPT,
            callback_group=group,
        )

    def publish(self) -> None:
        message = JointState()
        message.header.stamp = self.get_clock().now().to_msg()
        with self.lock:
            names = list(reversed(self.joints))
            message.name = names
            message.position = [self.positions[name] for name in names]
        message.velocity = [0.0] * len(names)
        message.effort = [0.0] * len(names)
        self.readings.publish(message)

    def take(self, goal: FollowJointTrajectory.Goal) -> GoalResponse:
        names = list(goal.trajectory.joint_names)
        points = list(goal.trajectory.points)
        fits = sorted(names) == sorted(self.joints) and len(points) > 0
        fits = fits and all(len(point.positions) == len(names) for point in points)
        return GoalResponse.ACCEPT if self.active and fits else GoalResponse.REJECT

    def execute(self, handle: Any) -> FollowJointTrajectory.Result:
        trajectory = handle.request.trajectory
        names = list(trajectory.joint_names)
        last = trajectory.points[-1]
        target = {name: float(v) for name, v in zip(names, last.positions, strict=True)}
        duration = seconds(last.time_from_start)
        with self.lock:
            self.running = handle
            start = {name: self.positions[name] for name in names}
        began = time.monotonic()
        result = FollowJointTrajectory.Result()
        while True:
            with self.lock:
                replaced = self.running is not handle
            if replaced:
                handle.abort()
                result.error_code = FollowJointTrajectory.Result.INVALID_GOAL
                result.error_string = "Current goal preempted by new incoming action."
                return result
            if handle.is_cancel_requested:
                handle.canceled()
                return result
            share = (time.monotonic() - began) / duration if duration > 0 else 1.0
            along = minimum_jerk(share)
            with self.lock:
                for name in names:
                    self.positions[name] = start[name] + (target[name] - start[name]) * along
                actual = [self.positions[name] for name in names]
            feedback = FollowJointTrajectory.Feedback()
            feedback.joint_names = names
            feedback.actual.positions = actual
            handle.publish_feedback(feedback)
            if share >= 1.0:
                handle.succeed()
                result.error_string = "Goal successfully reached!"
                return result
            time.sleep(STEP_S)


def main() -> None:
    rclpy.init()
    node = StandIn()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    node.get_logger().info(f"ready: {node.joints}")
    with contextlib.suppress(KeyboardInterrupt):
        executor.spin()


if __name__ == "__main__":
    main()
