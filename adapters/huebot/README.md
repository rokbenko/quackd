# quackd-huebot

The [Huebot](https://ce-net.com/huebot) adapter for [quackd](https://github.com/rokbenko/quackd).
Install it through quackd rather than directly:

```bash
uv pip install "quackd[huebot]"
```

No Huebot has run quackd. Huebot's own API is closed, so `huebot:jsonrpc` speaks the JSON-RPC
of the Microduck's `robotd` to a shim its maker runs on the robot, and every name it relies on is
listed in `upstream_api.py`. What the shim has to answer, and what quackd refuses:
[docs/adapters/huebot/README.md](https://github.com/rokbenko/quackd/blob/main/docs/adapters/huebot/README.md).
