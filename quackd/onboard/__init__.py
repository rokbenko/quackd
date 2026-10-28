"""Robot Passport on the robot's own computer: `quackd onboard`.

A robot that will meet strangers carries a computer that can run quackd itself, so its passport,
its keys and its talk live there rather than on a laptop that speaks for it. This package is that
side of quackd, and it grows a module at a time:

- `keys`: the owner key sealed with a passphrase, the robot key in the clear, and the one way a
  secret is written to disk.
- `store`: the one directory each robot keeps everything in.
"""
