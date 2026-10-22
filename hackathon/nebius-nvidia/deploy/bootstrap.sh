#!/usr/bin/env bash
# Get one Nebius AI Cloud VM ready to play the doorstep: Ubuntu, CPU only, no GPU needed, since
# the robots run on quackd's 2D simulator and their pilots on Token Factory.
#
#   bash bootstrap.sh [BRANCH]
#
# It installs uv, checks quackd out at BRANCH (main unless named), and syncs this folder. It
# writes no key and opens no port: put NEBIUS_API_KEY in hackathon/nebius-nvidia/.env yourself.
# On one VM the two robots talk over the VM's own address, so nothing needs to be reachable
# from outside; keep ports 8790 and 8791 closed to the internet.
set -euo pipefail

branch="${1:-main}"
repo="https://github.com/rokbenko/quackd.git"

sudo apt-get update -y
sudo apt-get install -y git curl ca-certificates

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

if [ ! -d quackd ]; then
  git clone --branch "$branch" --depth 1 "$repo" quackd
fi
cd quackd/hackathon/nebius-nvidia
uv sync
cp -n .env.example .env || true
uv run passport-nebius version

echo
echo "Next, in $(pwd):"
echo "  put your key in .env (NEBIUS_API_KEY=...), then"
echo "  uv run passport-nebius models"
echo "  uv run passport-nebius doorstep run --act appointment --model <an id from models>"
