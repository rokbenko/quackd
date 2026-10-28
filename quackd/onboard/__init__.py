"""Robot Passport on the robot's own computer: `quackd onboard`.

A robot that will meet strangers carries a computer that can run quackd itself, so its passport,
its keys and its talk live there rather than on a laptop that speaks for it. This package is that
side of quackd, and it grows a module at a time:

- `keys`: the owner key sealed with a passphrase, the robot key in the clear, and the one way a
  secret is written to disk.
- `store`: the one directory each robot keeps everything in.
- `config`: robot.yaml, the one file an owner writes, read strictly.
- `facts`: the passport robot.yaml describes, and what changed in it since its owner signed.
- `body`: the body of a robot quackd only talks for, which reports a state and stops.
- `meeting`: the standing task an idle robot's brain runs when another robot comes near.
"""
