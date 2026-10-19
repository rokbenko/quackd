"""The Microduck's own transports: how quackd reaches this robot, simulated or real.

`sim2d` and `mock` are not here. They are in the core, because they were never the duck's:
four other adapters subclass them and every adapter's mock draws itself with the 2D renderer.
What is here is what only a Microduck has, which is `robotd` over a unix socket, the physics
simulator, and the factory that picks between them.
"""
