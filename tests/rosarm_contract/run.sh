#!/usr/bin/env bash
# The rosarm contract job, inside an official ROS 2 image (ros:jazzy or ros:humble).
#
# Run it from the repository's root, which it reads and never writes:
#
#   docker run --rm -v "$PWD":/repo:ro -w /repo ros:jazzy bash tests/rosarm_contract/run.sh
#
# (in Git Bash on Windows, put MSYS_NO_PATHCONV=1 in front of it).
#
# It installs the bridge and the action's messages from the distribution's own apt repository,
# quackd with roslibpy into an environment of its own outside the checkout, and runs
# tests/test_rosarm_contract.py, which starts a bridge, a robot_state_publisher and the stand-in
# arm (tests/rosarm_contract/arm.py) for each case. Arguments are handed to pytest.
set -euo pipefail

: "${ROS_DISTRO:?run this in an official ROS 2 image, which sets ROS_DISTRO}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  "ros-${ROS_DISTRO}-rosbridge-server" "ros-${ROS_DISTRO}-control-msgs" curl ca-certificates \
  > /dev/null
dpkg-query -W "ros-${ROS_DISTRO}-rosbridge-server" "ros-${ROS_DISTRO}-control-msgs" \
  "ros-${ROS_DISTRO}-rclpy" "ros-${ROS_DISTRO}-robot-state-publisher"

if ! command -v uv > /dev/null; then
  curl -LsSf https://astral.sh/uv/0.12.21/install.sh \
    | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

# quackd's own environment: its Python, not the distribution's, and none of ROS's packages,
# which are the stand-in's alone
export UV_PROJECT_ENVIRONMENT=/opt/quackd UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH
uv sync --frozen --python 3.12 --extra dev --extra rosarm
/opt/quackd/bin/python -c "import roslibpy; print('roslibpy', roslibpy.__version__)"

QUACKD_ROSARM_CONTRACT=1 /opt/quackd/bin/python -m pytest -p no:cacheprovider -p no:randomly \
  tests/test_rosarm_contract.py "$@"
