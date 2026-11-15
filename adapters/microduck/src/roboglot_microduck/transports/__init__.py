"""The Microduck's own transports: how Roboglot reaches this robot.

`mock` is not here. It is in the core, because it was never the duck's: Open Duck's mock and
the described bodies subclass it too. What is here is what only a Microduck has, which is
`robotd` over a unix socket, and the factory that picks between it and the mock.
"""
