"""The real radio: BlueZ, Linux's Bluetooth stack, over D-Bus, with bleak for the central half.

`quackd.radio.open_radio("bluez")` is the one way in, and it imports `radio.py` only on Linux and
only once bleak and dbus-fast are installed (the `onboard` extra), so nothing here is imported
anywhere else. This package's own `__init__` imports nothing: `core.py` and `privacy.py` are
plain Python, tested on every platform, and the modules that need D-Bus or bleak (`objects`,
`peripheral`, `central`, `probe`, `radio`) import them at their top, as nothing else does.

What is here follows BlueZ's documented D-Bus API and bleak's, read at their sources: BlueZ's
doc/org.bluez.*.rst, dbus-fast 5.2 and bleak 3.0.2. None of it has run against a real controller;
`docs/passport/roadmap.md` lists what has to be tried on a robot before anybody relies on it.
"""
