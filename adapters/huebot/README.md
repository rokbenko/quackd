# roboglot-huebot

The [Huebot](https://ce-net.com/huebot) adapter for [Roboglot](https://github.com/rokbenko/quackd).
Install it through Roboglot rather than directly:

```bash
uv pip install "roboglot[huebot]"
```

No Huebot has run Roboglot. Huebot's own API is closed, so `huebot:jsonrpc` speaks the JSON-RPC of the
Microduck's `robotd` to a shim its maker is writing for the robot, and every name it relies on is
listed in `upstream_api.py`. This package is one record of what is different about Huebot, over the
verbs and the transport in `roboglot-microduck`, which it installs. What the shim has to answer, and
what Roboglot refuses:
[docs/adapters/huebot/README.md](https://github.com/rokbenko/quackd/blob/main/docs/adapters/huebot/README.md).
