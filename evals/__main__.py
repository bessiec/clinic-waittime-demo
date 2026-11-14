import sys

from .runner import main

if __name__ == "__main__":   # guard needed: the suite uses worker processes
    sys.exit(main())
