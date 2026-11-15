# roboglot-moss

The `moss` robot adapter for [Roboglot](https://github.com/rokbenko/quackd): MOSS, the small tracked
litter-picking rover by Laurent Genoud (Show Robotics). Install it through Roboglot rather than
directly:

```bash
uv pip install "roboglot[moss]"
```

It speaks MOSS's own cockpit over HTTP and, for what the cockpit cannot do, a small bridge Roboglot
ships for the rover. It imports nothing of MOSS's or dimOS's. Every upstream name it relies on is
read from upstream source at a pinned commit and listed in `upstream_api.py`. It has never been run
against a rover. What it does, what it refuses and why:
[docs/adapters/moss/README.md](https://github.com/rokbenko/quackd/blob/main/docs/adapters/moss/README.md).
