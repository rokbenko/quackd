# quackd's MOSS bridge

Run this on the rover, beside MOSS's own cockpit, with MOSS's own interpreter. It is the only
quackd code that publishes on the rover's bus or starts the arm.

## Why it exists

MOSS's cockpit (`tools/moss_cockpit.py`) is the rover's only network surface, and quackd speaks
it for everything it can: the state, the pose, the battery, a goal for MOSS's planner, the
explorer and the soft stop. What the cockpit has no command for is the reason this file exists:

- **a velocity command**, with a deadman of its own,
- **a frame that says when it was taken**,
- **the arm**: the maker's pick and drop cycle, and a cancel that leaves the arm holding,
- **the litter mission's stop.** The mission listens to no stop at all. Its own `stop` RPC is
  the only thing that ends it, and the cockpit cannot call it.

So the bridge does those four things over HTTP and nothing else.

## What it does

| Path | Method | What it answers |
|---|---|---|
| `/hello` | GET | the protocol and its version, the stack it found, what loaded and why anything did not, the track width, the stop ramp, the shaping it applies and the cameras it has heard |
| `/twist` | POST | `{vx, wz}` published on the stack's pad channel, shaped as below |
| `/stop` | POST | idempotent, never raises, and says what it did and what failed |
| `/frame?cam=nose` or `?cam=wrist` | GET | the first JPEG captured after the request arrived, with the image's own stamp |
| `/arm/cycle` | POST | `{pick_pose, from_phase, session}`: the maker's cycle, as a job of that session's |
| `/arm/cancel` | POST | `{id}`: that cycle of quackd's cancelled while it runs, and nothing else, which is what quackd's close sends |
| `/arm` | GET | the job and how it ended, the joints, the jaw gap, and whether the arm is in its drive pose |
| `/mission` | GET | the litter mission's own `mission_status` |
| `/nav` | GET | the base stack's own navigation, read off the bus: `exploring`, `explore_done`, `goal`, `goal_reached`, which the cockpit cannot show while moss-arm is the newest run |
| `/session/keepalive` | POST | `{session, keepalive_s, started, end}`: one session's keepalive, named by the id its client drew, with what that session started |

**The twist.** On `moss-explore` it goes out on `/tele_cmd_vel`, and on `moss-nav` on `/cmd_vel`,
which is the cockpit's own rule. It is sent again at the mission's own 10 Hz while it is fresh.
After the firmware's 0.3 s with no new request it sends one zero and then nothing at all,
because every message on that channel cancels MOSS's navigation, a zero as much as any. An arc
is kept tight enough that the inner track never reverses, using the stack's own track width,
because a reversed track is a full power pivot on this base. A turn on the spot is sent as the
maker's own pulses, 0.1 s on and a rest of zeros, timed here on the rover. The twist is refused
on `moss-litter`, where the mission owns the base, and while an arm cycle runs.

**The arm.** `/arm/cycle` runs `tools/moss_arm_play.py` from the MOSS checkout as a subprocess,
exactly as MOSS's own mission does, and reads its progress from the script's own lines and its
exit code. The cancel is three steps in this order: stop the script, call `cancel_trajectory`
on the arm's coordinator, then read the joints twice to see that they hold. Killing the script
alone cancels nothing: the whole cycle is one trajectory that the coordinator plays to its end.
`from_phase: back_to_clear` is the stow. `/arm/cancel` runs the same three steps for the one
cycle its `id` names, the id `/arm/cycle` answered with, and does nothing for a cycle that has
ended or that another id names, so quackd's close never cancels a trajectory it did not start.
A script that ends any other way than done or refused, by an exception, an exit of its own or a
signal, has the trajectory it sent cancelled and the arm read before its job ends, since nothing
else would watch that trajectory for lag. The script runs in a session of its own, so a Ctrl-C
on the bridge's terminal never reaches it, and the bridge's own shutdown cancels a cycle still
running.

**The stop.** On `moss-litter` the mission's own `stop` goes first, then at once the end of the
mission's own pick, the arm's cancel and a zero twist, and only then the wait that confirms the
mission's stop. The order matters: a cycle cancelled under a mission not yet told to stop makes
the maker's script exit on its lag watch, the mission marks the pick failed, and its next tick
sets the rover exploring again, while a cancel that waited for the confirmation would leave the
arm moving meanwhile. The mission's stop leaves its own pick running, and a pick still starting
sends its one trajectory after any cancel, so the bridge sends the maker's script SIGTERM, then
SIGKILL if it outlives its grace, before it cancels. The script is found among the stack's own
descendants in `/proc`, by `tools/moss_arm_play.py` or the stack's `MOSS_PICK_CMD`, and no other
process is ever signalled. After every cancel the joints are read twice, and a hold is never
claimed without that reading.
The mission's stop is sent without an answer, as dimOS sends every stop, so the bridge confirms
it by the mission's RPC falling silent, and only for a mission it heard answer before. A stop
it cannot confirm within 4 s of sending it is reported as a failure, and the arm's cancel and
the zero have gone out either way.

**The sessions.** Every quackd transport draws a session id of its own and posts a keepalive
every half second from a thread of its own, so a prompt that blocks quackd's event loop never
reads as a dead quackd. Each session keeps its own period, which the bridge caps at 1 s, its
own word of what it started and its own end, so a doctor or a second quackd never touches a
run's session. Three missed periods after a session's last keepalive the bridge halts what
that session started and nothing else: a goal or an exploration it started through the cockpit
is ended with `stop_explore_cmd`, `stop_movement` and a zero, and an arm cycle it started is
cancelled. A session that started nothing halts nothing, so a goal the owner clicked on the
cockpit's page is left alone. On `moss-litter` the whole stop above runs only for a session
that started the mission, and a session that only watched the owner's mission leaves it
running. The bridge's own shutdown halts what every live session started.

## Install it on the rover

`bridge/` ships in quackd's sdist and in the repository, never in the wheel, so installing
quackd does not give you this file. Copy `quackd_moss_bridge.py` and `quackd-moss-bridge.service`
to the rover. Then, from the directory that holds them, as the user that runs MOSS's stack, with
MOSS checked out at `~/moss`:

```bash
sudo install -m 0755 -D quackd_moss_bridge.py /opt/quackd/quackd_moss_bridge.py
openssl rand -hex 32 | sudo install -m 0640 -o root -g "$(id -gn)" -D /dev/stdin /etc/quackd/moss-bridge.token
sudo test -s /etc/quackd/moss-bridge.token || echo "no token was written: fix the line above first"
sed -e "s|MOSS_USER|$USER|g" -e "s|MOSS_DIR|$HOME/moss|g" quackd-moss-bridge.service \
  | sudo tee /etc/systemd/system/quackd-moss-bridge.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable quackd-moss-bridge && sudo systemctl restart quackd-moss-bridge
```

The unit runs the bridge with the checkout's own `.venv/bin/python`, the interpreter MOSS's
cockpit runs on, because that is where `moss_dimos`, `dimos` and `lcm` are installed. The
bridge imports them only where it uses them, so a rover missing one still answers `/hello` and
says which. It reads the stack the way the cockpit does, from dimOS's run registry and the
stack's own environment, and it reads the stack's numbers again whenever a new run starts,
so it may start at boot before any stack does. A stack whose bus is not LCM, or whose
`LCM_DEFAULT_URL` is not the bridge's, refuses the twist and the arm until one it can reach
starts, and `/hello` says why.

To try it with no rover, on any machine:

```bash
python quackd_moss_bridge.py --fake --once
```

prints what `/hello` would say about a fake bus, a fake arm and a fake mission, whose numbers
are deliberately not MOSS's. Without `--fake` it reads the machine it runs on. On a machine with
no MOSS installed that is a `/hello` with nothing loaded, and this is part of what it printed on
Windows:

```
  "publisher": {
    "loaded": false,
    "error": "moss_dimos is not importable here: ModuleNotFoundError: No module named 'moss_dimos'",
    "channel": null
  },
```

## The token and the tunnel

Every request needs the token when the bridge has one. It goes in the `X-Quackd-Token` header
and is compared in constant time, and a token in a URL is never read, because URLs get logged.
A named token file that is missing or empty refuses to start, and the bridge exits 2:

```
quackd-moss-bridge ERROR cannot read the token file moss-bridge.token: [Errno 2] No such file or directory: 'moss-bridge.token'. Refusing to start without it.
quackd-moss-bridge ERROR the token file empty.token is empty. Refusing to start without a token.
```

The bridge binds `127.0.0.1` by default. MOSS's cockpit binds `0.0.0.0` with no authentication
at all, so anything that reaches port 8904 reads the rover and commands it. Reach both from the
laptop through one tunnel instead, and give quackd the token:

```bash
export QUACKD_MOSS_TOKEN="$(ssh <rover> cat /etc/quackd/moss-bridge.token)"
ssh -L 8904:127.0.0.1:8904 -L 9876:127.0.0.1:9876 <rover>
```

Then `--robot moss:cockpit --address http://127.0.0.1:8904` reaches the cockpit, and the bridge
on its default port 9876 beside it. A bridge on another port goes in the address as
`?bridge=PORT`. The token never goes in the address. Bound anywhere but loopback with no token,
the bridge warns and starts, and anyone who can reach the port can drive the tracks and the arm.

| Flag | Default | |
|---|---|---|
| `--bind` | `127.0.0.1` | loopback on purpose |
| `--port` | `9876` | the cockpit is 8904, and 9875 is quackd's LeRobot policy server |
| `--token` | `$QUACKD_MOSS_TOKEN` | the token clients must send |
| `--token-file` | unset | read the token from a file, and refuse to start if it is unreadable or empty |
| `--moss` | where `moss_dimos` is installed from | the MOSS checkout, for `tools/moss_arm_play.py` |
| `--fake` | off | a fake bus, a fake arm and a fake mission, no rover |
| `--once` | off | print what `/hello` would say, as JSON, and exit |

## The cockpit's idle stop

MOSS's cockpit stops an idle `moss-nav` or `moss-explore` stack after 300 s with
`tools/moss-fly.sh stop`, which ends every dimOS run, the arm's included, and the arm's
adapter turns its torque off as its run ends. quackd sends the cockpit's keepalive while it
is connected. Once it disconnects nothing does, and an arm left outside its drive pose drops
when the idle stop fires. quackd's close note says so at the end of every run that moved the
arm and leaves it out. `MOSS_IDLE_STOP_S=0` on the cockpit turns the idle stop off. The cockpit
counts idle time only while the newest dimOS run is `moss-nav` or `moss-explore`, so with moss-arm
started after the base stack, as the launcher's first gate makes it, it counts none at all.

## Rules this file lives by

- **It never imports quackd**, and a test reads the file to prove it.
- **lcm, dimos_lcm, dimos and moss_dimos are imported where they are used**, so `--fake` runs
  the whole protocol with none of them, and CI runs it that way on every platform.
- **It never sends a go limp call.** Its own close halts what quackd started, then lets go of
  the bus. quackd never posts the cockpit's `/cmd/stack` either, whose stop drops the arm.
- **It ships in the sdist and never in the wheel.**

Nothing here has been run on a rover. The order to try it in is
[docs/adapters/moss/hardware-checklist.md](../../docs/adapters/moss/hardware-checklist.md), and the
adapter's page, [docs/adapters/moss/README.md](../../docs/adapters/moss/README.md), has the rest.
