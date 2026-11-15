"""Transports are where upstream uncertainty is contained.

Microduck's agent-facing API is partly shipped and partly a draft. One `DuckTransport`
protocol lets a mock and the real robot look identical to the rest of Roboglot,
while `upstream_api.py` is the single file allowed to spell an upstream method name.
"""
