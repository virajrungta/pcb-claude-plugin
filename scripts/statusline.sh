#!/usr/bin/env bash
# Claude Code status line: the PCB design progress bar while a design is in progress
# (empty otherwise). Uses a plain Python (no KiCad needed), so it's instant.
cat >/dev/null 2>&1 &   # Claude Code sends session JSON on stdin; we don't need it
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) HERE="$(cygpath -w "$HERE" 2>/dev/null || echo "$HERE")" ;;
esac
for PY in python3 python py; do
  if command -v "$PY" >/dev/null 2>&1; then
    out="$(PYTHONIOENCODING=utf-8 PYTHONUTF8=1 "$PY" -c "import sys; sys.path.insert(0, r'$HERE'); from kipcb import progress; print(progress.statusline())" 2>/dev/null)" \
      && { printf '%s\n' "$out"; exit 0; }
  fi
done
exit 0
