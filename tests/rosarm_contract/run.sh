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
# Roboglot with roslibpy into an environment of its own outside the checkout, and runs
# tests/test_rosarm_contract.py, which starts a bridge, a robot_state_publisher and the stand-in
# arm (tests/rosarm_contract/arm.py) for each case. Arguments are handed to pytest.
set -euo pipefail

# The uv release CI runs: every setup-uv step in .github/workflows/ installs this one, and
# tests/test_workflows.py holds them equal, so a bump here moves them all.
uv_version=0.12.21

: "${ROS_DISTRO:?run this in an official ROS 2 image, which sets ROS_DISTRO}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  "ros-${ROS_DISTRO}-rosbridge-server" "ros-${ROS_DISTRO}-control-msgs" curl ca-certificates \
  > /dev/null
dpkg-query -W "ros-${ROS_DISTRO}-rosbridge-server" "ros-${ROS_DISTRO}-control-msgs" \
  "ros-${ROS_DISTRO}-rclpy" "ros-${ROS_DISTRO}-robot-state-publisher"

if ! command -v uv > /dev/null; then
  # CI mounts the runner's own uv, which setup-uv installed at this release, so the installer
  # below is for a person running this by hand: a job that lost the mount stops here instead
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "no uv in the container: rosarm-contract.yml mounts the runner's at /usr/local/bin/uv" >&2
    exit 1
  fi
  curl -LsSf "https://astral.sh/uv/${uv_version}/install.sh" \
    | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi

# Roboglot's own environment: its Python, not the distribution's, and none of ROS's packages,
# which are the stand-in's alone. `--locked`, as UV_LOCKED makes every sync in CI: a uv.lock that
# no longer matches a pyproject.toml fails here, and is never rewritten.
export UV_PROJECT_ENVIRONMENT=/opt/roboglot UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH
uv sync --locked --python 3.12 --extra rosarm
/opt/roboglot/bin/python -c "import roslibpy; print('roslibpy', roslibpy.__version__)"

ROBOGLOT_ROSARM_CONTRACT=1 /opt/roboglot/bin/python -m pytest -p no:cacheprovider -p no:randomly \
  tests/test_rosarm_contract.py "$@"
