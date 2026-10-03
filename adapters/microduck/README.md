# quackd-microduck

The [Microduck](https://github.com/pollen-robotics/microduck) adapter for
[quackd](https://github.com/rokbenko/quackd), and the two simulators that come with it: the
2D cartoon arena and the MuJoCo physics backend. Install it through quackd:

```bash
uv pip install "quackd[microduck]"            # the cartoon, the mock and the real duck
uv pip install "quackd[mujoco]"               # and the physics simulator
```

No Microduck has run quackd. Every name the `jsonrpc` backend relies on is read from upstream
source at a pinned commit and listed in `upstream_api.py`. What it does and what it refuses:
[docs/adapters/microduck/README.md](https://github.com/rokbenko/quackd/blob/main/docs/adapters/microduck/README.md).
