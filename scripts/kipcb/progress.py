"""The progress checklist the user sees in Claude Code while a board is designed.

`kipcb progress --start` begins it right after the requirements, and each
`kipcb run` stage updates it (.kipcb/progress.json in the project, plus a
pointer to the current one). The commands print it as a `progress:` line that
Claude relays in its own message, and Claude Code's status bar can show it live
(install_statusline / scripts/statusline.sh).
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
    from . import kienv
    return os.path.join(kienv.data_dir(), "current_progress.json")


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


def statusline():
    """For Claude Code's status line: the progress bar while a design is active, else ''."""
    state = load()
    if not state or time.time() - state.get("updated", 0) > 3 * 3600:
        return ""
    return render_line(state)


def install_statusline():
    """Point Claude Code's status line at kipcb's progress bar (only if none is set)."""
    path = os.path.expanduser("~/.claude/settings.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except OSError:
        cfg = {}
    except ValueError:
        return "not changed: %s isn't valid JSON" % path
    cur = (cfg.get("statusLine") or {}).get("command", "")
    if "kipcb" in cur:
        return "already set: the status bar shows PCB progress"
    if cur:
        return ("not changed: you already have a status line (%s). To show PCB progress there, "
                "add the output of ~/.local/share/kipcb/statusline.sh to it." % cur)
    launcher = os.path.join(os.path.dirname(_pointer()), "statusline.sh")
    if not os.path.exists(launcher):
        return "not changed: start a new Claude Code session first (it creates %s)" % launcher
    from . import kienv
    command = launcher
    if kienv.WINDOWS:          # Claude Code on Windows runs it through Git Bash: forward slashes
        command = 'bash "%s"' % launcher.replace("\\", "/")
    cfg["statusLine"] = {"type": "command", "command": command}
    if os.path.exists(path):
        with open(path) as f:
            backup = f.read()
        with open(path + ".bak-kipcb", "w") as f:
            f.write(backup)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    return "done: the status bar now shows PCB progress while a design is running (backup: %s.bak-kipcb)" % path


def uninstall_statusline():
    """Remove kipcb's status line again (only if it's the one in place)."""
    path = os.path.expanduser("~/.claude/settings.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return "nothing to remove"
    if "kipcb" not in (cfg.get("statusLine") or {}).get("command", ""):
        return "nothing to remove"
    cfg.pop("statusLine", None)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    return "removed the PCB progress status line"


def statusline_installed():
    try:
        with open(os.path.expanduser("~/.claude/settings.json")) as f:
            return "kipcb" in (json.load(f).get("statusLine") or {}).get("command", "")
    except (OSError, ValueError):
        return False
