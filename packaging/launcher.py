"""Entry point of the packaged executable: no arguments starts the map UI, otherwise the usual command line."""

import sys

from quickscene.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["gui"]))
