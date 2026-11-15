"""The deliberation loop: observe → think → enforce → act.

This package exists to make the LLM a *high-level* controller and nothing more: one verb
per turn, judged against the `.glot` success criteria, with every prompt and result written
to a transcript so a run can be replayed and argued about.
"""
