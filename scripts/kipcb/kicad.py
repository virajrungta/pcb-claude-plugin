"""Import pcbnew quietly (it prints wx asserts to stderr on headless import)."""

import os
import sys

_pcbnew = None


def pcbnew():
    global _pcbnew
    if _pcbnew is None:
        sys.stderr.flush()
        saved = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 2)
        try:
            import pcbnew as m
        finally:
            os.dup2(saved, 2)
            os.close(devnull)
            os.close(saved)
        _pcbnew = m
    return _pcbnew


def mm(v):
    return int(round(v * 1e6))


def to_mm(v):
    return v / 1e6
