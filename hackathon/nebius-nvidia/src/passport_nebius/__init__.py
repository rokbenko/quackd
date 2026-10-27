"""quackd Passport with NVIDIA Nemotron on Nebius Token Factory.

quackd's entry to the Nebius x NVIDIA Global AI Hackathon 2026, kept whole in this one folder
so that it can leave the repository in one commit. It changes nothing in quackd: it runs
quackd's command line and calls its importable passport API, from the released 0.23 line.

- `nebius`: Token Factory and a robot's own model server as this edition reaches them, a key
  and a live model list each.
- `pilot`: `quackd run` with its local provider pointed at one of them, a Nemotron pilot.
- `doorstep`: two robots that never met agree a handoff by quackd Passport, the postman
  thinking onboard with `--onboard`.
- `agile_one_sn`: Agile ONE SN, the postman, as a mock body quackd finds by its entry point.
"""

__version__ = "0.1.0"
