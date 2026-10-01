#!/usr/bin/env bash
# Claude Code status line: the PCB design progress bar while a design is in progress
# (empty otherwise). Uses plain python3 (no KiCad) so it's instant.
cat >/dev/null 2>&1 &   # Claude Code sends session JSON on stdin; we don't need it
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="$HERE" python3 -c 'from kipcb import progress; print(progress.statusline())' 2>/dev/null
