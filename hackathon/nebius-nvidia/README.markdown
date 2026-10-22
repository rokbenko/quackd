# Robot Passport on NVIDIA Nemotron and Nebius

quackd's entry to the Nebius x NVIDIA Global AI Hackathon 2026. Robots that have never met
read each other's signed passport, then talk and decide together who does what, with NVIDIA
Nemotron on Nebius Token Factory as each robot's pilot.

This folder is self-contained: it is a consumer of quackd 0.23 from PyPI and changes nothing
in the rest of the repository.

> [!NOTE]
> Work in progress. The full guide (setup, the doorstep scene, `explain`, `whois`, and how
> NVIDIA and Nebius are used) lands here before submission.

```
cd hackathon/nebius-nvidia
uv sync
uv run passport-nebius models
```
