"""Huebot's transports: the shim on the robot, and the factory that picks one.

`mock` is not here. It is the core's, which every adapter's mock is. What is here is what only a
Huebot has, its maker's shim answering the Microduck's JSON-RPC, and the factory.
"""
