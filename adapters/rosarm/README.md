# quackd-rosarm

The `rosarm` robot adapter for [quackd](https://github.com/rokbenko/quackd): an industrial arm on
ROS 2, a UR5e, a KUKA LBR iisy 3 R760, a KUKA LBR iiwa 7 R800 or a Yaskawa MotoMini, driven
through rosbridge. It is rehearsed only: its `mock` runs the real transport against a fake bridge
and a fake arm, and its `ws` backend refuses every arm until an owner names a date for its first
hardware run. Install it through quackd rather than directly:

```bash
uv pip install "quackd[rosarm]"
```

Every upstream name it relies on is read from upstream source at a pinned commit and listed in
`upstream_api.py`. What it does, what it refuses and why:
[docs/adapters/rosarm/README.md](https://github.com/rokbenko/quackd/blob/main/docs/adapters/rosarm/README.md).
