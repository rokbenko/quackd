"""The arm's simulator: the real backend with a MuJoCo follower underneath.

`lerobot:real` reaches the arm through two builders, one for the follower and one for each
camera, and nothing else it does touches hardware. The connect retries, the calibrated travel
and its refusals, the rest pose, hold and stop, release and take-hold are all code about an
arm rather than the arm. So the simulator keeps every line of that and changes only what the
builders return: a follower and cameras backed by a physics model of the SO-101. A task file
rehearsed at home then runs through the same code that will drive the arm in the lab. The
faults the lab bench found on 2026-09-23 were integration faults rather than the pilot's, and
that integration is exactly what a rehearsal here exercises.

The model is the maker's own, TheRobotStudio's SO-ARM100, fetched at a pinned commit at run
time and never shipped: `upstream_api.py` says what it is and what Roboglot assumes about it,
and `assets.py` fetches it. `standin.py` is a primitives-only arm for wherever that model cannot
be fetched, CI above all. `model.py` sets either one in Roboglot's scene and maps LeRobot's units
onto it, and `world.py` steps it and keeps the truth about the table. `follower.py` is the
follower the real backend drives over that world, and `faults.py` the seeded bus faults it can
be told to have. `clock.py` is the world's time, `camera.py` its cameras, and `transport.py`
the backend all of it makes, `lerobot:mujoco`.

Nothing here may import `mujoco` when the module is imported. It is an optional extra, and
`make()` and `describe()` have to work without it (`tests/test_extras_absent.py`), so the
modules that need it import it inside `connect()` or later.
"""

SIM_EXTRA = "roboglot[lerobot-sim]"
"""What installs MuJoCo for the arm's simulator: named by a connect that finds it missing and
by doctor's row for the simulator. Here rather than in `transport.py`, so that doctor can name
it without importing the backend."""
