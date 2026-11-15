# roboglot-microduck

The [Microduck](https://github.com/pollen-robotics/microduck) adapter for
[Roboglot](https://github.com/rokbenko/quackd), and the two simulators that come with it: the
2D cartoon arena and the MuJoCo physics backend. Install it through Roboglot:

```bash
uv pip install "roboglot[microduck]"            # the cartoon, the mock and the real duck
uv pip install "roboglot[mujoco]"               # and the physics simulator
```

No Microduck has run Roboglot. Every name the `jsonrpc` backend relies on is read from upstream
source at a pinned commit and listed in `upstream_api.py`. What it does and what it refuses:
[docs/adapters/microduck/README.md](https://github.com/rokbenko/quackd/blob/main/docs/adapters/microduck/README.md).

Another robot that answers `robotd`'s calls is a body of this package, `roboglot_microduck.body.Body`:
`roboglot-huebot` is the first, and depends on this one.
