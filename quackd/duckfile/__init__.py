"""The `.duck` file is the contract between a human, an LLM, and a robot.

This package exists because the LLM must never be trusted to self-police: the frontmatter
is parsed into a pydantic model that the executor enforces, and the Markdown body is the
only part the LLM gets to interpret.
"""
