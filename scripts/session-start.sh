#!/usr/bin/env bash
# SessionStart hook for the PCB Design plugin.
#  - saves the install-time settings where kipcb can read them
#  - welcomes the user after install, and announces new versions after an update
#  - warns (only) when a required tool is missing
# Fast and dependency-free: checks for files, never loads KiCad.
set -u

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/plugins/data/pcb}"

# Windows: Claude Code runs hooks in Git Bash. Use the same folders as kipcb's Python side
# (kienv.data_dir / cache_dir): %LOCALAPPDATA%\kipcb instead of ~/.local/share and ~/.cache.
IS_WINDOWS=0
case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;; esac
if [[ $IS_WINDOWS == 1 && -z "${XDG_DATA_HOME:-}" && -n "${LOCALAPPDATA:-}" ]]; then
  LAD="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")"
  SETTINGS_DIR="$LAD/kipcb"
  CACHE="${KIPCB_CACHE:-$LAD/kipcb/cache}"
else
  SETTINGS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/kipcb"
  CACHE="${KIPCB_CACHE:-$HOME/.cache/kipcb}"
fi
mkdir -p "$DATA" "$SETTINGS_DIR" 2>/dev/null

# a Python that really runs (on Windows "python3" can be the Microsoft Store stub);
# kipcb's own launcher (KiCad's Python) is the fallback
plain_python() {
  local c
  for c in python3 python py; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import json" >/dev/null 2>&1; then echo "$c"; return; fi
  done
}
PY="$(plain_python)"
run_progress() {   # run_progress install_statusline|uninstall_statusline
  if [[ -n "$PY" ]]; then
    local pp="$ROOT/scripts"
    [[ $IS_WINDOWS == 1 ]] && pp="$(cygpath -w "$pp" 2>/dev/null || echo "$pp")"
    PYTHONUTF8=1 "$PY" -c "import sys; sys.path.insert(0, r'$pp'); from kipcb import progress; progress.$1()" >/dev/null 2>&1
  else
    "$ROOT/bin/kipcb" progress --${1//_/-} >/dev/null 2>&1
  fi
}

json_escape() { local s="${1//\\/\\\\}"; s="${s//\"/\\\"}"; printf '%s' "${s//$'\n'/\\n}"; }

# 1. settings from the install dialog (exported by Claude Code as CLAUDE_PLUGIN_OPTION_<KEY>)
FAB="${CLAUDE_PLUGIN_OPTION_FAB_HOUSE:-JLCPCB}"
BUILD="${CLAUDE_PLUGIN_OPTION_BUILD_METHOD:-Assembled by the manufacturer}"
LAYERS="${CLAUDE_PLUGIN_OPTION_LAYERS:-2}"
LEARN="${CLAUDE_PLUGIN_OPTION_LEARNING:-true}"
printf '{"fab_house": "%s", "build_method": "%s", "layers": %s, "learning": %s}\n' \
  "$(json_escape "$FAB")" "$(json_escape "$BUILD")" "${LAYERS//[^0-9]/}" \
  "$([[ "$LEARN" == "false" || "$LEARN" == "0" ]] && echo false || echo true)" \
  > "$SETTINGS_DIR/settings.json" 2>/dev/null

# stable launcher for Claude Code's status line (the plugin's own folder changes per version):
# "statusLine": {"type": "command", "command": "~/.local/share/kipcb/statusline.sh"}
printf '#!/usr/bin/env bash\nT="%s/scripts/statusline.sh"\n[ -f "$T" ] && exec bash "$T"\n' "$ROOT" \
  > "$SETTINGS_DIR/statusline.sh" 2>/dev/null && chmod +x "$SETTINGS_DIR/statusline.sh" 2>/dev/null
# install option "Show design progress in the status bar": set it up (never replacing a status
# line the user already has), or take ours away again when the option is turned off
if [[ "${CLAUDE_PLUGIN_OPTION_PROGRESS_STATUSLINE:-true}" == "false" || "${CLAUDE_PLUGIN_OPTION_PROGRESS_STATUSLINE:-}" == "0" ]]; then
  run_progress uninstall_statusline
else
  run_progress install_statusline
fi

# 2. toolchain check (files only)
missing=()
have_kicad=0
if [[ -x /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli || -x "$HOME/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli" ]] \
   || command -v kicad-cli >/dev/null 2>&1 || [[ -n "${KIPCB_KICAD_CLI:-}" ]]; then have_kicad=1; fi
if [[ $IS_WINDOWS == 1 && $have_kicad == 0 ]]; then
  for root in "${KIPCB_KICAD_ROOT:-}" "${ProgramW6432:+$ProgramW6432/KiCad/9.0}" \
              "${PROGRAMFILES:+$PROGRAMFILES/KiCad/9.0}" "${LOCALAPPDATA:+$LOCALAPPDATA/Programs/KiCad/9.0}"; do
    [[ -n "$root" && -f "$(cygpath -u "$root" 2>/dev/null || echo "$root")/bin/kicad-cli.exe" ]] && have_kicad=1
  done
fi
if [[ $have_kicad == 0 ]]; then
  if [[ $IS_WINDOWS == 1 ]]; then missing+=("KiCad 9 (winget install KiCad.KiCad)")
  else missing+=("KiCad 9 (https://www.kicad.org/download/)"); fi
fi
if command -v java >/dev/null 2>&1; then
  jv="$(java -version 2>&1 | sed -n 's/.*version "\([0-9]*\).*/\1/p' | head -1)"
  if [[ -n "$jv" && "$jv" -lt 21 ]]; then missing+=("Java 21+ for the autorouter (found Java $jv)"); fi
else
  if [[ $IS_WINDOWS == 1 ]]; then missing+=("Java 21+ for the autorouter (winget install EclipseAdoptium.Temurin.21.JDK)")
  else missing+=("Java 21+ for the autorouter (macOS: brew install --cask temurin)"); fi
fi
if [[ -z "${KIPCB_FREEROUTING_JAR:-}" ]] && ! ls "$CACHE"/freerouting-*.jar >/dev/null 2>&1; then
  router_missing=1
fi

# 3. welcome / update notice
VERSION="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$ROOT/.claude-plugin/plugin.json" | head -1)"
SEEN_FILE="$DATA/last-version"
SEEN="$(cat "$SEEN_FILE" 2>/dev/null || true)"
msg=""
if [[ -z "$SEEN" ]]; then
  msg="PCB Design plugin V$VERSION installed. Try: /pcb:design a USB-C powered board with an ESP32 and a temperature sensor. Defaults: $FAB, $BUILD, $LAYERS layers (change with /plugin configure pcb@pcb-claude-plugin)."
elif [[ "$SEEN" != "$VERSION" ]]; then
  msg="PCB Design plugin updated to V$VERSION. What's new: https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v$VERSION"
fi
echo "$VERSION" > "$SEEN_FILE" 2>/dev/null

if (( ${#missing[@]} )); then
  needs="$(printf '%s; ' "${missing[@]}")"
  msg="${msg:+$msg }PCB Design plugin needs: ${needs%; }. Run kipcb doctor for details."
fi

context="PCB plugin defaults from the user's settings: manufacturer $FAB; build: $BUILD; $LAYERS layers; learning ${LEARN}."
if [[ -n "${router_missing:-}" ]]; then
  context="$context Freerouting is not installed yet; ask the user before running kipcb setup-router (downloads ~65 MB)."
fi

printf '{'
if [[ -n "$msg" ]]; then printf '"systemMessage": "%s", ' "$(json_escape "$msg")"; fi
printf '"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' "$(json_escape "$context")"
