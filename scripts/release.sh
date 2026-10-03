#!/usr/bin/env bash
# Cut a release: scripts/release.sh 1.1
#
#  1. moves the "Unreleased" notes in CHANGELOG.md under "V1.1 - <date>"
#  2. sets the version in .claude-plugin/plugin.json and scripts/kipcb/__init__.py
#  3. runs the tests (and `claude plugin validate` if the CLI is installed)
#  4. commits "V1.1", tags v1.1, and pushes; GitHub Actions then publishes the release
set -euo pipefail

VERSION="${1:-}"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+$ ]]; then
  echo "usage: scripts/release.sh <major.minor>   e.g. scripts/release.sh 1.1" >&2
  exit 2
fi
TAG="v$VERSION"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "tag $TAG already exists" >&2; exit 1
fi
if [[ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]]; then
  echo "release from main" >&2; exit 1
fi

python3 - "$VERSION" <<'EOF'
import datetime, json, re, sys
v = sys.argv[1]
today = datetime.date.today().isoformat()

p = "CHANGELOG.md"
s = open(p).read()
m = re.search(r"## Unreleased\n(.*?)(?=\n## |\Z)", s, re.S)
notes = m.group(1).strip() if m else ""
if not notes or notes == "_Nothing yet._":
    sys.exit("CHANGELOG.md has no notes under '## Unreleased'; add what changed first")
s = s.replace(m.group(0), "## Unreleased\n\n_Nothing yet._\n\n## V%s - %s\n\n%s\n" % (v, today, notes), 1)
open(p, "w").write(s)

# one row per version in the README's Releases table and RELEASES.md: the notes' bold
# headline, else the first bullet's bold lead
head = re.search(r"^\*\*(.+?)\*\*", notes, re.M) or re.search(r"^- \*\*(.+?)\*\*", notes, re.M)
summary = head.group(1).strip().rstrip(":") if head else notes.splitlines()[0].lstrip("- ")
row = "| [V%s](https://github.com/virajrungta/pcb-claude-plugin/releases/tag/v%s) | %s | %s |" % (v, v, today, summary)
for p in ("README.md", "RELEASES.md"):
    t = open(p).read()
    i = t.find("| Version | Date | What's new |\n|---|---|---|\n")
    if i < 0:
        sys.exit("%s has no Releases table" % p)
    j = i + len("| Version | Date | What's new |\n|---|---|---|\n")
    open(p, "w").write(t[:j] + row + "\n" + t[j:])

p = ".claude-plugin/plugin.json"
d = json.load(open(p)); d["version"] = v
open(p, "w").write(json.dumps(d, indent=2) + "\n")

p = "scripts/kipcb/__init__.py"
s = open(p).read()
open(p, "w").write(re.sub(r'__version__ = "[^"]*"', '__version__ = "%s"' % v, s))
EOF

PYTHONPATH=scripts python3 -m unittest discover -s tests
if command -v claude >/dev/null; then claude plugin validate --strict . && claude plugin validate --strict skills; fi

NOTES="$(python3 - "$VERSION" <<'EOF'
import re, sys
s = open("CHANGELOG.md").read()
m = re.search(r"## V%s - [^\n]*\n(.*?)(?=\n## |\Z)" % re.escape(sys.argv[1]), s, re.S)
print(m.group(1).strip())
EOF
)"

git add -A
git commit -q -m "V$VERSION" -m "$NOTES"
git tag -a "$TAG" -m "V$VERSION" -m "$NOTES"
git push -q origin main
git push -q origin "$TAG"
echo "released V$VERSION ($TAG). GitHub Actions will publish the release page."
