"""`python -m roboglot`, the same command as `roboglot`.

`roboglot ui` starts every run it drives this way, as `python -P -m roboglot run ...`: the Roboglot
installed beside the page's own Python, whatever is on the PATH, and never a `roboglot/` folder
that happens to sit in the working directory, which `-P` keeps off the import path."""

from roboglot.cli import main

if __name__ == "__main__":
    main()
