#!/usr/bin/env bash
# CI helper: run a command; if it fails, publish its last lines as one annotation
# (readable through GitHub's API and on the run page without signing in).
set -o pipefail
title="$1"; shift
"$@" 2>&1 | tee .ci-last.log
rc=${PIPESTATUS[0]}
if [[ $rc -ne 0 ]]; then
  body="$(tail -40 .ci-last.log | sed -e 's/%/%25/g' | awk '{printf "%s%%0A", $0}')"
  echo "::error title=${title} (exit ${rc})::${body}"
fi
exit $rc
