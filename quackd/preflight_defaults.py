"""The defaults of `quackd preflight`, apart from the module that runs it: the CLI shows them in
its help, and that module's pydantic models and YAML are much of what help would wait for. It
imports nothing."""

DEFAULT_SEEDS = 3
"""Runs per file when `--seeds` is not given. One seed rehearses one table and one draw of the
pilot's choices, so a single pass says little; three is enough to see a task that passes only
sometimes, at three times one run's cost with a paid model."""
DEFAULT_CONNECT_CYCLES = 3
"""Connects and closes per file before the first run, when `--connect-cycles` is not given. A
fault plan draws a different set of bus faults on each cycle's seed, so three meet a plan's
connect faults on more than one draw, and the close after each is the arm let go of as often as
it was taken hold of."""
