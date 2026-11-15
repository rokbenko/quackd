# roboglot-toddlerbot

The `toddlerbot` robot adapter for [Roboglot](https://github.com/rokbenko/quackd). Install it through
Roboglot rather than directly:

```bash
uv pip install "roboglot[toddlerbot]"
```

Every upstream name it relies on is read from upstream source at a pinned commit and listed in
`upstream_api.py`. What it does, what it refuses and why:
[docs/adapters/toddlerbot/README.md](https://github.com/rokbenko/quackd/blob/main/docs/adapters/toddlerbot/README.md).
