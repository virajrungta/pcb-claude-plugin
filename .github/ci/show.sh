#!/usr/bin/env bash
# CI helper: publish a command's output (first 60 lines) as a warning annotation, whatever happens.
title="$1"; shift
out="$("$@" 2>&1 | head -60; echo "[exit ${PIPESTATUS[0]}]")"
body="$(printf '%s\n' "$out" | sed -e 's/%/%25/g' | awk '{printf "%s%%0A", $0}')"
echo "::warning title=${title}::${body}"
printf '%s\n' "$out"
