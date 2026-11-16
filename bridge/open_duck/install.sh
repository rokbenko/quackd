#!/usr/bin/env bash
# Install Roboglot's bridge and camera server on an Open Duck Mini v2's Raspberry Pi.
#
# It checks rather than fixes. Every step it refuses is a step you should understand before
# a 42 cm biped starts taking commands from your network.
#
#   curl -fsSL .../install.sh | bash        is NOT how to run this. Read it, then:
#   bash install.sh
set -euo pipefail

RUNTIME_DIR="${RUNTIME_DIR:-$HOME/Open_Duck_Mini_Runtime}"
VENV="${VENV:-$HOME/.virtualenvs/open-duck-mini-runtime}"
PORT_DEV="${PORT_DEV:-/dev/ttyACM0}"
# The token's own directory, which this script makes and puts right on every run. A TOKEN_FILE
# anywhere else is in a directory this script never changes: it checks that one is safe for a
# secret and stops, saying what to run, when it is not (see secret_dir_or_die).
TOKEN_DIR=/etc/roboglot
TOKEN_FILE="${TOKEN_FILE:-/etc/roboglot/duck-bridge.token}"
TOKEN_FILE_DIR="$(dirname -- "$TOKEN_FILE")"
DUCK_CONFIG="${DUCK_CONFIG:-$HOME/duck_config.json}"
# The walk policy, from the Apache-2.0 design repo. Roboglot does not ship it, and the unit
# cannot start without it, so it is checked here rather than discovered at first boot.
ONNX_PATH="${ONNX_PATH:-$HOME/BEST_WALK_ONNX_2.onnx}"
# The units ship with pi's paths in them because they have to say something. Nothing here
# assumes you are pi: Bookworm has no default pi user, and these are substituted below.
SVC_USER="${SVC_USER:-$(id -un)}"
CAMERA_URL="${CAMERA_URL:-http://127.0.0.1:9872/snapshot.jpg}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n== %s\n' "$1"; }
warn() { printf 'warning: %s\n' "$1"; }
die() { printf '\nstopped: %s\n' "$1" >&2; exit 1; }

# Before anything is done. The path is written into the unit by sed, so it is plain
# characters only, and it names a file, not the directory itself or the one above it.
case "$TOKEN_FILE" in
  /*) ;;
  *) die "TOKEN_FILE=$TOKEN_FILE is not an absolute path. Give the whole path from /." ;;
esac
case "$TOKEN_FILE" in
  *[!A-Za-z0-9._/-]* | */. | */.. | */) die "TOKEN_FILE=$TOKEN_FILE is not a plain file name.
  Use letters, digits, dot, dash, underscore and slash." ;;
esac

# What makes a directory that is not this script's own safe to hold the token. Only the
# directory itself is checked, not the ones above it: it exists and is no symlink, it belongs to
# root or to the service user, neither its group nor anyone else may write in it (so nobody else
# can replace the token or put a file of their own in its place), and the service user can
# search it (so the bridge can reach the token). The script never changes such a directory: it
# stops, and says what to run.
secret_dir_or_die() {  # $1 = the directory TOKEN_FILE is in
  local dir="$1" owner mode
  if ! sudo test -d "$dir" || sudo test -L "$dir"; then
    die "$dir, where TOKEN_FILE is, is not a directory, or is a symlink. Make one with
  install -d (sudo install -d -m 0750 -o root -g $SVC_USER $dir), or leave TOKEN_FILE unset
  for $TOKEN_DIR/duck-bridge.token, in a directory this script makes and keeps right itself."
  fi
  read -r owner mode < <(sudo stat -c '%U %a' "$dir")
  if [ "$owner" != root ] && [ "$owner" != "$SVC_USER" ]; then
    die "$dir, where TOKEN_FILE is, belongs to $owner, who could replace the token. This
  script does not change a directory it did not make: run sudo chown root $dir yourself, or
  leave TOKEN_FILE unset."
  fi
  if (( (8#$mode & 8#022) != 0 )); then
    die "$dir, where TOKEN_FILE is, can be written by its group or by anyone (mode $mode),
  so they could replace the token. This script does not change a directory it did not make:
  run sudo chmod go-w $dir yourself, or leave TOKEN_FILE unset."
  fi
  sudo -u "$SVC_USER" test -x "$dir" || die "$SVC_USER cannot search $dir, where TOKEN_FILE
  is, so the bridge could not reach the token. This script does not change a directory it did
  not make: give $SVC_USER search on it yourself, for instance with sudo chgrp $SVC_USER $dir
  and sudo chmod g+x $dir, or leave TOKEN_FILE unset."
}

say "checking the robot's own runtime"
[ -d "$RUNTIME_DIR" ] || die "no runtime at $RUNTIME_DIR. Install it first, from
  https://github.com/apirrone/Open_Duck_Mini_Runtime (branch v2). Roboglot does not ship it."
[ -x "$VENV/bin/python" ] || die "no virtualenv at $VENV. Upstream's README uses
  mkvirtualenv -p python3 open-duck-mini-runtime, then pip install -e . in the checkout.
  Set VENV=... if yours lives elsewhere."
"$VENV/bin/python" -c 'import mini_bdx_runtime' \
  || die "$VENV cannot import mini_bdx_runtime. Run pip install -e . inside $RUNTIME_DIR."

say "checking that nothing else owns the serial bus"
[ -e "$PORT_DEV" ] || die "$PORT_DEV is missing. Is the servo board plugged in and powered?"
# USB CDC device numbers are assignment-ordered, not stable: anything that enumerates ahead
# of the servo board - a USB serial console left plugged in, or the board itself
# re-enumerating after a brownout - makes it ttyACM1, and the unit's ConditionPathExists then
# silently skips the service or upstream opens the wrong device.
if [ "$(ls /dev/ttyACM* 2>/dev/null | wc -l)" -gt 1 ]; then
  warn "more than one /dev/ttyACM* is present, so $PORT_DEV is not a stable name for the
  servo board. Find its by-id path and use that:
      ls -l /dev/serial/by-id/
  then set PORT_DEV=... and edit ConditionPathExists= in the unit to match."
fi
if [ -d /dev/serial/by-id ]; then
  printf 'stable names for what is plugged in now:\n'
  ls -l /dev/serial/by-id/ 2>/dev/null | sed 's/^/  /'
fi
[ -r "$PORT_DEV" ] || die "$PORT_DEV is not readable by you. sudo usermod -aG dialout $USER, then log out and in."
if pgrep -f 'v2_rl_walk_mujoco.py' >/dev/null; then
  die "upstream's walk script is already running. The Feetech bus has exactly one owner,
  and this service replaces however you started that script before. Stop it first."
fi

say "checking I2C and the IMU"
[ -e /dev/i2c-1 ] || warn "no /dev/i2c-1. Enable it with sudo raspi-config nonint do_i2c 0, then reboot."
if command -v i2cdetect >/dev/null && [ -e /dev/i2c-1 ]; then
  i2cdetect -y 1 | grep -q ' 28 ' || warn "no BNO055 at 0x28 on i2c-1."
fi

say "checking Wi-Fi power save, which is the difference between 10 Hz and a stutter"
if command -v iw >/dev/null; then
  iw dev wlan0 get power_save 2>/dev/null | grep -q 'off' \
    || warn "Wi-Fi power save is on. sudo iw dev wlan0 set power_save off"
fi

say "checking the walk policy"
[ -f "$ONNX_PATH" ] || die "no walk policy at $ONNX_PATH. It is BEST_WALK_ONNX_2.onnx from
  https://github.com/apirrone/Open_Duck_Mini (Apache-2.0); Roboglot does not ship it. Download
  it, or set ONNX_PATH=... to where yours is."

say "checking upstream's motion data is where its loop will look"
[ -f "$RUNTIME_DIR/scripts/polynomial_coefficients.pkl" ] \
  || die "no polynomial_coefficients.pkl in $RUNTIME_DIR/scripts. Upstream's loop opens it
  by relative path from that directory, inside RLWalk.__init__ and *after* it has powered
  the servos, so a missing one is a traceback over fourteen energised joints."

say "checking the walk policy is not paused on boot"
if grep -q '"start_paused"[[:space:]]*:[[:space:]]*true' "$DUCK_CONFIG" 2>/dev/null; then
  die "duck_config.json has start_paused true. Roboglot cannot release it: upstream toggles
  pause on its gamepad's A button and the bridge replaces that pad, so a bridge started
  paused can never walk — and the paused loop runs at about 10 Hz, which Roboglot's heartbeat
  reports as a starved Pi. Set start_paused false in $DUCK_CONFIG."
fi

say "checking who owns the camera"
if grep -q '"camera"[[:space:]]*:[[:space:]]*true' "$DUCK_CONFIG" 2>/dev/null; then
  warn "duck_config.json has expression_features.camera true, so the robot's own runtime
  claims to own the camera. Upstream's walk loop opens none, so roboglot-duck-camd will warn
  and start anyway. Setting that flag false is still tidier."
fi

# The camd unit runs under the same virtualenv as the bridge, but picamera2 and libcamera are
# apt packages that cannot be pip-installed into one. A venv made the way upstream's README
# suggests (mkvirtualenv, no --system-site-packages) therefore cannot import picamzero, and
# camd restarts every 5 s forever. The cv2 case is quieter and worse: the unit stays active
# while every /snapshot.jpg answers 503. Neither is fatal to the bridge, so this warns.
say "checking the camera server's imports"
CAMD_OK=0
if "$VENV/bin/python" -c 'import picamzero, cv2' 2>/dev/null; then
  if "$VENV/bin/python" - <<'PROBE' 2>/dev/null
from picamzero import Camera
Camera().capture_array()
PROBE
  then
    CAMD_OK=1
  else
    warn "picamzero imports but could not take a frame. Is the ribbon seated, and is the
  camera enabled? Installing without a camera URL: the verbs that need one will not exist
  rather than exist and fail. Re-run this once it can capture."
  fi
else
  warn "$VENV cannot import picamzero and cv2, so roboglot-duck-camd will not serve frames and
  observe, go_to, search_scan and approach_and will not exist for this duck. They are apt
  packages (python3-picamera2, python3-opencv) and cannot be pip-installed into a plain
  virtualenv: recreate it with --system-site-packages, or skip the camera server."
fi

say "installing the bridge and the camera server to /opt/roboglot"
sudo install -m 0755 -D "$HERE/roboglot_duck_bridge.py" /opt/roboglot/roboglot_duck_bridge.py
sudo install -m 0755 -D "$HERE/roboglot_duck_camd.py" /opt/roboglot/roboglot_duck_camd.py

say "generating a token"
# `[ ! -f ]` ran unprivileged against a root-only directory, so it was fail-open: every
# re-run of this script silently rotated a live token and locked out the laptop that had the
# old one. `sudo test -f` actually looks.
# The service has to be able to read it. Installed 0600 root:root inside a 0700 root
# directory, it could not: os.path.exists() swallowed the EACCES, the bridge started with no
# token, and the handshake then accepted every client while four places in the docs promised
# otherwise. So this script's own directory is made, and put right on every run, root's and
# group-owned by the service user, which can then search it and nobody else can. Any other
# directory is not this script's to change, and is only checked.
if [ "$TOKEN_FILE_DIR" = "$TOKEN_DIR" ]; then
  sudo install -d -m 0750 "$TOKEN_DIR"
  sudo chown root:"$SVC_USER" "$TOKEN_DIR"
  sudo chmod 0750 "$TOKEN_DIR"
else
  secret_dir_or_die "$TOKEN_FILE_DIR"
fi
if ! sudo test -f "$TOKEN_FILE"; then
  # created with its owner, group and mode as the token is written, so it is never readable
  # by anyone else, not even for a moment under the umask
  openssl rand -hex 32 | sudo install -m 0640 -o root -g "$SVC_USER" /dev/stdin "$TOKEN_FILE"
  printf 'wrote a new token to %s\n' "$TOKEN_FILE"
else
  printf 'keeping the existing token at %s\n' "$TOKEN_FILE"
fi
# Group-read for the service user, and verified rather than assumed.
sudo chgrp "$SVC_USER" "$TOKEN_FILE"
sudo chmod 0640 "$TOKEN_FILE"
sudo -u "$SVC_USER" test -r "$TOKEN_FILE" || die "$SVC_USER still cannot read $TOKEN_FILE.
  The bridge refuses to start rather than run with authentication silently disabled, so fix
  the ownership before starting it."
printf '%s can read the token\n' "$SVC_USER"

# The shipped units are valid systemd files with pi's paths in them, so they can be read and
# reasoned about on their own. What gets installed is those files with your user, your venv,
# your checkout and your policy substituted in, because Bookworm has no default pi user and
# an unsubstituted unit fails with a User= that does not resolve.
say "installing the services as $SVC_USER"
id -u "$SVC_USER" >/dev/null 2>&1 || die "no such user: $SVC_USER. Set SVC_USER=... to the
  account that should own the two services."

substitute() {  # $1 = source unit, $2 = destination
  sed -e "s|^User=pi$|User=$SVC_USER|" \
      -e "s|/home/pi/.virtualenvs/open-duck-mini-runtime|$VENV|g" \
      -e "s|/home/pi/Open_Duck_Mini_Runtime|$RUNTIME_DIR|g" \
      -e "s|/home/pi/BEST_WALK_ONNX_2.onnx|$ONNX_PATH|g" \
      -e "s|--bind 127.0.0.1 --deadman-ms 300|& --token-file $TOKEN_FILE|" \
      "$1" | sudo tee "$2" >/dev/null
  sudo chmod 0644 "$2"
}

substitute "$HERE/roboglot-duck-bridge.service" /etc/systemd/system/roboglot-duck-bridge.service
substitute "$HERE/roboglot-duck-camd.service" /etc/systemd/system/roboglot-duck-camd.service
# The token written above is the token the bridge reads only if the unit names it. Without
# this a TOKEN_FILE set for this script left the bridge reading its default path, finding no
# file, and admitting any client.
sudo grep -q -- "--token-file $TOKEN_FILE" /etc/systemd/system/roboglot-duck-bridge.service \
  || die "could not add --token-file $TOKEN_FILE to the bridge's unit. Add it to the
  ExecStart by hand before starting anything, or the bridge will not read your token."

# The snapshot URL is what gives this duck observe, go_to, search_scan and approach_and, so
# it goes in only when a camera was actually proved to work above. A duck that cannot serve
# frames should not advertise them: the verbs not existing is a refusal at validate time,
# while a URL nothing answers is four verbs that fail one at a time during a run.
if [ "$CAMD_OK" = "1" ]; then
  BRIDGE_UNIT=/etc/systemd/system/roboglot-duck-bridge.service
  sudo sed -i "s|--bind 127.0.0.1 --deadman-ms 300|& --camera-url $CAMERA_URL|" "$BRIDGE_UNIT"
  sudo grep -q -- "--camera-url" "$BRIDGE_UNIT" || die "could not add --camera-url to
  $BRIDGE_UNIT. Add it to the ExecStart by hand, or observe, go_to, search_scan and
  approach_and will not exist for this duck."
  printf 'camera proved; the bridge will advertise %s\n' "$CAMERA_URL"
else
  printf 'no camera proved; installing without --camera-url\n'
fi

# Anything left pointing at pi is a path this script did not know how to rewrite, and it
# would fail at boot rather than here. `systemd-analyze verify` does not resolve User=
# against passwd, so it cannot catch this and is not what we gate on.
for unit in /etc/systemd/system/roboglot-duck-bridge.service \
            /etc/systemd/system/roboglot-duck-camd.service; do
  if [ "$SVC_USER" != "pi" ] && sudo grep -nE '/home/pi|^User=pi$' "$unit"; then
    die "$unit still refers to pi after substitution (the lines above). Edit it by hand
  before starting anything: a unit with the wrong User= or the wrong paths fails at boot."
  fi
done
sudo systemctl daemon-reload

cat <<'NEXT'

Installed, and deliberately not started.

Before you start anything:

  1. Read both installed units. Your user, virtualenv, checkout and walk policy have been
     substituted into them, and this script refused to finish if anything still said pi:
        systemctl cat roboglot-duck-bridge roboglot-duck-camd
  2. Put your duck on a stand, with its feet off the ground.
  3. Dry run both, with no robot and no camera at all:
        python /opt/roboglot/roboglot_duck_camd.py --fake --seconds 30
        python /opt/roboglot/roboglot_duck_bridge.py serve --fake --seconds 30
  4. Start the camera first, so the bridge has something behind the URL it advertises.
     --fail matters here: without it curl prints nothing and exits 0 when camd is down.
        sudo systemctl start roboglot-duck-camd
        curl --fail --max-time 5 http://127.0.0.1:9872/healthz || \
            journalctl -u roboglot-duck-camd -n 40
        sudo systemctl start roboglot-duck-bridge
        journalctl -u roboglot-duck-bridge -f
     Both units are Restart=no or start-limited, so if you end up editing and restarting a
     few times, clear the counter with:
        sudo systemctl reset-failed roboglot-duck-bridge
  5. From your laptop, safest first, over an ssh tunnel because the bridge binds loopback.
     Forward both ports, and tell Roboglot where the camera is on your side of the tunnel:
        ssh -L 9871:127.0.0.1:9871 -L 9872:127.0.0.1:9872 <your-pi>
        roboglot doctor --robot open_duck:bridge --address tcp://127.0.0.1:9871
        roboglot run open-duck-lookout --robot open_duck:bridge \
            --address tcp://127.0.0.1:9871 \
            --camera-url http://127.0.0.1:9872/snapshot.jpg

Nothing in open-duck-lookout's allowlist moves a leg. Run it before you ever run a task
that walks, and remember this robot cannot get back up if it falls.
NEXT
