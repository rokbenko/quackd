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
- `brain`: the run slot, one run at a time: a goal from the owner, or the meeting task.
- `radio_side`: who is near, meeting them over the LAN or a link, and the beacon id.
- `control`: the service's connection on 127.0.0.1, behind a token only its user reads.
- `service`: `quackd onboard serve`, every part above in one long-lived process.
- `wizard`: what `quackd onboard` asks, and the signing.
- `systemd`: the unit that keeps the service running on a robot.
- `preflight`: what this computer has for all of it, check by check.
- `demo`: two simulated robots on one laptop, meeting act by act.
"""
