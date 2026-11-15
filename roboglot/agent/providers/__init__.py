"""One protocol, many vendors.

Providers exist so the rest of Roboglot never imports a vendor SDK. Each one is an optional
extra, lazily imported; `fake` is always available and is what tests and the zero-API-key
demo run on.
"""
