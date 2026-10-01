"""The progress checklist the user sees in Claude Code while a board is designed.

`kipcb estimate` starts it and each `kipcb run` stage updates it (written to
.kipcb/progress.json in the project, plus a pointer to the current one). A
plugin hook (hooks/hooks.json, PostToolUse on Bash) renders it as a box after
every kipcb command, so the user sees the steps tick off without relying on any
particular Claude Code tool.
"""

import json
import os
import time

ITEMS = ["Requirements", "Components & circuit", "Schematic", "Placement", "Preflight checks",
         "Routing", "DRC & noise checks", "Manufacturing files", "Review & hand-off"]
# which checklist items a pipeline stage completes, and the estimate key for their time
STAGE_ITEMS = {"build": ["Components & circuit", "Schematic", "Placement"],
               "preflight": ["Preflight checks"], "route": ["Routing", "DRC & noise checks"],
               "fab": ["Manufacturing files"]}
TIME_KEY = {"Placement": "build", "Preflight checks": "preflight", "Routing": "route",
            "Manufacturing files": "fab"}


def _pointer():
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(base, "kipcb", "current_progress.json")


def _path(pdir):
    from . import paths
    return os.path.join(paths.work(pdir), "progress.json")


def load(pdir=None):
    try:
        if pdir is None:
            with open(_pointer()) as f:
                pdir = json.load(f)["project"]
        with open(_path(pdir)) as f:
            return json.load(f)
    except (OSError, ValueError, KeyError):
        return None


def _save(pdir, state):
    state["updated"] = time.time()
    os.makedirs(os.path.dirname(_path(pdir)), exist_ok=True)
    with open(_path(pdir), "w") as f:
        json.dump(state, f)
    try:
        os.makedirs(os.path.dirname(_pointer()), exist_ok=True)
        with open(_pointer(), "w") as f:
            json.dump({"project": pdir}, f)
    except OSError:
        pass


def start(pdir, name, estimates, why="", keep=False):
    """A fresh checklist: requirements done, components in progress. keep: if this project
    already has one (started right after the requirements), only fill in the times."""
    old = load(pdir) if keep else None
    if old:
        for it in old["items"]:
            key = TIME_KEY.get(it["name"])
            if key and estimates.get(key) and it["status"] in ("todo", "active"):
                it["est"] = estimates[key]
            if it["name"] == "Routing" and why:
                it["note"] = why
        _save(pdir, old)
        return
    items = []
    for it in ITEMS:
        st = "done" if it == "Requirements" else ("active" if it == "Components & circuit" else "todo")
        items.append({"name": it, "status": st, "est": estimates.get(TIME_KEY.get(it, ""), ""),
                      "note": why if it == "Routing" else ""})
    _save(pdir, {"name": name, "items": items})


def stage(pdir, name, stage_name, ok, seconds=None, note="", estimates=None):
    """Mark a pipeline stage done (or failed) and the next item active."""
    state = load(pdir) or {"name": name, "items": [{"name": it, "status": "todo", "est": "", "note": ""}
                                                    for it in ITEMS]}
    names = STAGE_ITEMS.get(stage_name, [])
    for it in state["items"]:
        if it["name"] == "Requirements":
            it["status"] = "done"
        if it["name"] in names:
            it["status"] = "done" if ok else "failed"
            if seconds is not None and it["name"] in TIME_KEY:
                it["took"] = "%ds" % seconds if seconds < 90 else "%d min" % round(seconds / 60.0)
            if note and not ok:
                it["note"] = note
        if estimates and it["name"] in TIME_KEY and it["status"] == "todo":
            it["est"] = estimates.get(TIME_KEY[it["name"]], it.get("est", ""))
    # earlier items are necessarily done; the first open one becomes active
    if ok:
        last = max(i for i, it in enumerate(state["items"]) if it["name"] in names) if names else -1
        for i, it in enumerate(state["items"]):
            if i < last and it["status"] != "failed":
                it["status"] = "done"
        for it in state["items"]:
            if it["status"] == "active":
                it["status"] = "todo"
        for it in state["items"]:
            if it["status"] == "todo":
                it["status"] = "active"
                break
    _save(pdir, state)


def finish(pdir):
    state = load(pdir)
    if state:
        for it in state["items"]:
            if it["status"] in ("todo", "active"):
                it["status"] = "done"
        _save(pdir, state)


def render(state):
    marks = {"done": "✔", "active": "▶", "todo": "☐", "failed": "✘"}
    width = max(len(it["name"]) for it in state["items"]) + 2
    lines = ["PCB progress · %s" % state.get("name", "")]
    for it in state["items"]:
        extra = ""
        if it["status"] == "done" and it.get("took"):
            extra = it["took"]
        elif it["status"] == "active":
            extra = "in progress" + (", %s" % it["est"] if it.get("est") else "")
        elif it["status"] in ("todo",) and it.get("est"):
            extra = it["est"]
        if it.get("note") and it["status"] in ("todo", "active", "failed"):
            extra = (extra + " · " if extra else "") + it["note"]
        lines.append(" %s %s%s" % (marks[it["status"]], it["name"].ljust(width), extra))
    return "\n".join(lines)


SHORT = {"Requirements": "Requirements", "Components & circuit": "Components", "Schematic": "Schematic",
         "Placement": "Placement", "Preflight checks": "Preflight", "Routing": "Routing",
         "DRC & noise checks": "DRC", "Manufacturing files": "Files", "Review & hand-off": "Hand-off"}


def render_line(state):
    """Compact progress bar: 'PCB · board  ■■■■■□□□□ 5/9  ▶ Routing ~1 min'."""
    items = state["items"]
    cells = {"done": "■", "failed": "✘", "active": "□", "todo": "□"}
    bar = "".join(cells[it["status"]] for it in items)
    done = sum(1 for it in items if it["status"] == "done")
    failed = [it for it in items if it["status"] == "failed"]
    active = [it for it in items if it["status"] == "active"]
    if failed:
        it = failed[0]
        tail = "✘ %s%s" % (SHORT.get(it["name"], it["name"]), (": " + it["note"]) if it.get("note") else "")
    elif active:
        it = active[0]
        tail = "▶ %s%s" % (SHORT.get(it["name"], it["name"]), (" " + it["est"]) if it.get("est") else "")
    else:
        tail = "✔ done"
    return "PCB · %s  %s %d/%d  %s" % (state.get("name", ""), bar, done, len(items), tail)


def hook(stdin_text):
    """PostToolUse hook: after a kipcb command, show the checklist box to the user."""
    try:
        data = json.loads(stdin_text or "{}")
    except ValueError:
        return None
    cmd = str((data.get("tool_input") or {}).get("command", ""))
    if "kipcb run" not in cmd and "kipcb estimate" not in cmd and "kipcb progress" not in cmd:
        return None
    state = load()
    if not state or time.time() - state.get("updated", 0) > 3600:
        return None
    line = render_line(state)
    if state.get("shown") == line:
        return None                    # nothing changed since the last line the user saw
    _remember_shown(line)
    return json.dumps({"systemMessage": line})


def _remember_shown(line):
    try:
        with open(_pointer()) as f:
            pdir = json.load(f)["project"]
        with open(_path(pdir)) as f:
            state = json.load(f)
        state["shown"] = line
        with open(_path(pdir), "w") as f:
            json.dump(state, f)
    except (OSError, ValueError, KeyError):
        pass


def statusline():
    """For Claude Code's status line: the progress bar while a design is active, else ''."""
    state = load()
    if not state or time.time() - state.get("updated", 0) > 3 * 3600:
        return ""
    return render_line(state)
