import sys

for _stream in (sys.stdout, sys.stderr):
    try:                      # Windows consoles default to a legacy code page; ✔ ■ ▶ would crash
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from .cli import main

sys.exit(main())
