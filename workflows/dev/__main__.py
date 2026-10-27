from __future__ import annotations

import sys

from .composition.entry import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
