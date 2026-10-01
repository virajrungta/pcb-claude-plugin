"""Console output. `kipcb run` sets QUIET so only its progress lines and the
final report print; single commands keep their full output."""

import sys

QUIET = False


def say(*args):
    if not QUIET:
        print(*args)


def always(*args):
    try:
        print(*args)
        sys.stdout.flush()
    except BrokenPipeError:      # output piped into `head` etc.
        pass
