"""The physics simulator: a MuJoCo arena the same shape as the cartoon's.

`sim2d` exists to test the agent loop, and says so; it will never tell you whether a gait
works, because it has no joints. This package is where a robot's own controllers run for
real: a rigid body world, a camera that renders what the head would see, and, once a body
brings one, a walking policy trained for the robot. The Microduck brings `alpha_walking.onnx`
and `alpha_stand.onnx` (`upstream_api.WALK_POLICY`, `STAND_POLICY`), the pair Pollen trained
before `velstand.onnx`, which every robotd from daemon-v0.13.0 walks and stands on instead
(`quackd_microduck.upstream_api.VELSTAND`), so the gait here is not the one a stock robot
walks with. The arena, the ball, the kick cone, the deadman and the seeded spawn order are
the cartoon's, deliberately, so a `.duck` that asks for those runs unchanged against either
and a seed puts the duck and the ball in the same place in both. The one thing not carried
over is the cartoon's person marker: nobody stands in this arena, so a `.duck` about a
person — `follow-me` — is a 2D task.

Nearly everything here imports `mujoco`, which is an optional extra (`quackd[mujoco]`):
nothing on the default path imports this package, and the transport that uses it imports it
inside `connect()` so `--robot microduck:mujoco` fails with the extra's name rather than a
stack. The exception is `gait.py`, which is deliberately pure arithmetic over floats, so the
rule that decides whether a duck moves or only reports moving is tested on every runner
rather than only where the extra and a filled asset cache happen to meet.
"""
