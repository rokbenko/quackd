"""quackd: One CLI for all your robots.

Each robot joins through an adapter that declares a manifest, is registered once by name,
and is piloted by any LLM through a safety-enforced vocabulary of *verbs* that the manifest
declares, under a `.duck` task contract. A flock of them works one task together, one pilot
each, dividing the work by talking over a bus. The LLM picks verbs, the robot's own
controllers move, quackd enforces the contract. Everything else in the repo serves that
sentence.
"""

__version__ = "0.17.1"
