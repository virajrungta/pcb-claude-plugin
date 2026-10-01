#!/usr/bin/env bash
# SessionStart hook for the PCB Design plugin.
#  - saves the install-time settings where kipcb can read them
#  - welcomes the user after install, and announces new versions after an update
#  - warns (only) when a required tool is missing
# Fast and dependency-free: checks for files, never loads KiCad.
set -u

ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.claude/plugins/data/pcb}"
SETTINGS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/kipcb"
CACHE="${KIPCB_CACHE:-$HOME/.cache/kipcb}"
mkdir -p "$DATA" "$SETTINGS_DIR" 2>/dev/null

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
printf '#!/usr/bin/env bash\nexec "%s/scripts/statusline.sh"\n' "$CLAUDE_PLUGIN_ROOT" \
  > "$SETTINGS_DIR/statusline.sh" 2>/dev/null && chmod +x "$SETTINGS_DIR/statusline.sh" 2>/dev/null

# 2. toolchain check (files only)
missing=()
if [[ -x /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli || -x "$HOME/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli" ]] \
   || command -v kicad-cli >/dev/null 2>&1 || [[ -n "${KIPCB_KICAD_CLI:-}" ]]; then :; else
  missing+=("KiCad 9 (https://www.kicad.org/download/)")
fi
if command -v java >/dev/null 2>&1; then
  jv="$(java -version 2>&1 | sed -n 's/.*version "\([0-9]*\).*/\1/p' | head -1)"
  if [[ -n "$jv" && "$jv" -lt 21 ]]; then missing+=("Java 21+ for the autorouter (found Java $jv)"); fi
else
  missing+=("Java 21+ for the autorouter (macOS: brew install --cask temurin)")
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
