"""`python -m quackd`, the same command as `quackd`.

`quackd ui` starts every run it drives this way, as `python -P -m quackd run ...`: the quackd
installed beside the page's own Python, whatever is on the PATH, and never a `quackd/` folder
that happens to sit in the working directory, which `-P` keeps off the import path."""

from quackd.cli import main

if __name__ == "__main__":
    main()
