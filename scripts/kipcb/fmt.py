"""Rewrite a design spec in the compact layout: one line per component and per
net, nets and no_connect as space-separated strings. Same meaning, roughly
half the size, so it is cheaper to read and to edit."""

import json
from collections import OrderedDict


def _one_line(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))


def format_spec(raw):
    d = OrderedDict(raw)
    if isinstance(d.get("power_nets"), list):
        d["power_nets"] = " ".join(d["power_nets"])
    if isinstance(d.get("no_connect"), list):
        d["no_connect"] = " ".join(d["no_connect"])
    nets = d.get("nets") or {}
    d["nets"] = OrderedDict((k, " ".join(v) if isinstance(v, list) else v) for k, v in nets.items())
    out = ["{"]
    keys = list(d.keys())
    for i, k in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        v = d[k]
        if k == "components":
            out.append('  "components": [')
            out += ["    %s%s" % (_one_line(c), "," if j < len(v) - 1 else "") for j, c in enumerate(v)]
            out.append("  ]" + comma)
        elif k == "nets":
            out.append('  "nets": {')
            items = list(v.items())
            out += ["    %s: %s%s" % (json.dumps(n), json.dumps(p), "," if j < len(items) - 1 else "")
                    for j, (n, p) in enumerate(items)]
            out.append("  }" + comma)
        else:
            out.append("  %s: %s%s" % (json.dumps(k), _one_line(v), comma))
    out.append("}")
    return "\n".join(out) + "\n"


def fmt_file(path):
    with open(path) as f:
        raw = json.load(f, object_pairs_hook=OrderedDict)
    before = len(json.dumps(raw, indent=2))
    text = format_spec(raw)
    from . import paths
    paths.atomic_write(path, text)      # the spec is the source of truth: never half-written
    return before, len(text)
