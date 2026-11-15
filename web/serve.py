#!/usr/bin/env python3
"""Serve this directory the way production does: mounted at /simulator/, not at the root.

The page is deployed under www.roboglot.org/simulator, so every reference in index.html is
absolute — `/simulator/style.css`, not `style.css`. Relative paths cannot survive that mount:
roboglot-web sets `trailingSlash: false`, so the browser lands on `/simulator` with no slash and
resolves a relative `style.css` to `/style.css`, which is the landing page's root and not this
directory at all.

The cost of absolute paths is that a plain `http.server --directory web` no longer works, since
nothing answers on /simulator. This is that missing piece: one stdlib server, no dependencies,
serving web/ under the same prefix the deploy uses, so what you test is what ships.

    python web/serve.py [port]        # then open http://localhost:8000/simulator/
"""

from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PREFIX = "/simulator"
ROOT = Path(__file__).resolve().parent


class MountedAtSimulator(SimpleHTTPRequestHandler):
    """Strip the mount prefix before the file lookup, and send the bare root to it."""

    # do_GET, not do_get: the casing is BaseHTTPRequestHandler's, not ours.
    def do_GET(self) -> None:
        if self.path in ("/", ""):
            self.send_response(302)
            self.send_header("Location", f"{PREFIX}/")
            self.end_headers()
            return
        # Anything outside the mount is a 404 here exactly as it is in production, where the
        # root belongs to the landing page. A dev server that answered /style.css would hide
        # the one mistake this mount makes easy: a relative path that resolves off the mount.
        if self.path != PREFIX and not self.path.startswith(PREFIX + "/"):
            self.send_error(404, f"nothing is mounted here; the page lives under {PREFIX}/")
            return
        super().do_GET()

    def translate_path(self, path: str) -> str:
        # Only the prefix is removed; everything else, including the query string and the
        # directory traversal guard, stays SimpleHTTPRequestHandler's own business.
        if path == PREFIX:
            path = "/"
        elif path.startswith(PREFIX + "/"):
            path = path[len(PREFIX) :]
        return super().translate_path(path)


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    handler = partial(MountedAtSimulator, directory=str(ROOT))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"serving {ROOT} at http://localhost:{port}{PREFIX}/")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()
