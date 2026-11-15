"""The one exception a reader raises, carrying the rule of the specification it applied."""

from __future__ import annotations


class PassportError(ValueError):
    """A document, a signature, a stamp or a message a reader refused.

    `rule` names the part of `docs/passport/spec.md` that refused it, `rule 2` or `4.4`, so a
    message can say which rule and a test can check which one fired, and the text says what
    was wrong in words a person can act on."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
