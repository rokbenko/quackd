"""Robot Passport with NVIDIA Nemotron on Nebius Token Factory.

quackd's entry to the Nebius x NVIDIA Global AI Hackathon 2026, kept whole in this one folder
so that it can leave the repository in one commit. It changes nothing in quackd: it runs
quackd's command line and calls its importable passport API, from the released 0.23 line.

- `nebius`: Token Factory as this edition reaches it, a key and the live model list.
- `pilot`: `quackd run` with its local provider pointed at Token Factory, a Nemotron pilot.
- `doorstep`: two robots that never met agree a handoff by Robot Passport.
"""

__version__ = "0.1.0"
