#!/usr/bin/env bash
# PostToolUse hook: after a `kipcb run` / `kipcb estimate` command, show the design's progress
# checklist to the user. Any other command exits immediately (no Python started).
input="$(cat)"
case "$input" in
  *"kipcb run"*|*"kipcb estimate"*) ;;
  *) exit 0 ;;
esac
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || true)"
[ -n "$PY" ] || exit 0
printf '%s' "$input" | PYTHONPATH="$HERE" "$PY" -c 'import sys; from kipcb import progress; out = progress.hook(sys.stdin.read()); print(out) if out else None' 2>/dev/null
exit 0
