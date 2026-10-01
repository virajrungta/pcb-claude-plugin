"""kipcb guide <topic>: print only the matching sections of the design references.

The references (circuit patterns, layout and noise rules, spec format,
manufacturing) are a few thousand tokens each; most questions need one
section. `kipcb guide` with no topic lists the available sections."""

import os
import re

REF_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "skills", "design", "references")
_START = re.compile(r"^(#{2,3} |\*\*[^*]+\*\*|- \*\*[^*]+\*\*)")


def sections():
    out = []
    for fn in sorted(os.listdir(REF_DIR)):
        if not fn.endswith(".md"):
            continue
        with open(os.path.join(REF_DIR, fn)) as f:
            lines = f.read().splitlines()
        cur = None
        parent = ""
        for line in lines:
            if _START.match(line):
                if cur:
                    out.append(cur)
                title = re.sub(r"^- ", "", re.sub(r"[#*]", "", line.split("**:")[0])).strip()
                if line.startswith("#"):
                    parent = title
                cur = {"file": fn, "title": title, "parent": parent, "lines": [line]}
            elif cur:
                cur["lines"].append(line)
        if cur:
            out.append(cur)
    return out


def _norm(s):
    s = s.lower().replace("²", "2").replace("µ", "u")
    return re.sub(r"[^a-z0-9]+", " ", s)


def show(topic):
    secs = sections()
    if not topic:
        by_file = {}
        for s in secs:
            by_file.setdefault(s["file"], []).append(s["title"])
        for fn, titles in by_file.items():
            print("%s: %s" % (fn[:-3], "; ".join(t[:40] for t in titles)))
        return 0
    stem = _norm(topic).strip().replace(" ", "-")
    if os.path.exists(os.path.join(REF_DIR, stem + ".md")):        # a whole reference file by name
        with open(os.path.join(REF_DIR, stem + ".md")) as f:
            text = f.read().strip()
        if len(text) < 6000:
            print(text)
            return 0
    words = _norm(topic).split()
    scored = []
    for s in secs:
        head = _norm(s["title"] + " " + s["parent"])
        body = _norm(" ".join(s["lines"]))
        if all(w in head for w in words):
            scored.append((3, s))
        elif all(w in head + " " + body for w in words):
            scored.append((1, s))
    if not scored:
        print("no section about %r; `kipcb guide` lists them" % topic)
        return 1
    scored.sort(key=lambda t: -t[0])
    best = [s for score, s in scored if score == scored[0][0]][:3]
    for s in best:
        text = "\n".join(s["lines"]).strip()
        print("[%s] %s" % (s["file"][:-3], text if len(text) < 2500 else text[:2500] + "\n..."))
        print()
    return 0
