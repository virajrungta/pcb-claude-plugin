"""Minimal S-expression reader/writer for KiCad files.

Parsed form: nested Python lists. Unquoted tokens become `Sym` (a str
subclass), quoted strings become plain `str`. Writers accept Sym, str,
int and float.
"""

import re


class Sym(str):
    """An unquoted atom (keyword or number) as it appears in the file."""
    __slots__ = ()


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_UNESCAPE = re.compile(r'\\(.)', re.S)
_ESC_MAP = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def loads(text):
    """Parse text containing one or more S-expressions; returns the first."""
    stack = [[]]
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError("sexp parse error at offset %d" % pos)
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            done = stack.pop()
            stack[-1].append(done)
        elif m.group(3) is not None:
            s = _UNESCAPE.sub(lambda e: _ESC_MAP.get(e.group(1), e.group(1)), m.group(3))
            stack[-1].append(s)
        elif m.group(4) is not None:
            stack[-1].append(Sym(m.group(4)))
    if len(stack) != 1 or not stack[0]:
        raise ValueError("unbalanced s-expression")
    return stack[0][0]


def _fmt_num(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    s = ("%.6f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _atom(v):
    if isinstance(v, Sym):
        return str(v)
    if isinstance(v, (int, float)):
        return _fmt_num(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"%s"' % s


# Nodes that KiCad keeps on a single line; keeps output compact and diffable.
_INLINE = {"at", "xy", "size", "font", "start", "end", "mid", "center", "width",
           "type", "length", "offset", "uuid", "stroke", "fill", "color",
           "justify", "hide", "thickness", "layer", "layers", "drill", "net",
           "unit", "page", "reference", "effects", "paper", "version",
           "generator", "generator_version", "in_bom", "on_board", "dnp",
           "exclude_from_sim", "lib_id", "radius", "diameter", "pin_numbers",
           "pin_names", "angle", "rotate", "embedded_fonts", "fields_autoplaced"}


def dumps(node, indent=0):
    if not isinstance(node, list):
        return _atom(node)
    if not node:
        return "()"
    head = node[0]
    simple = all(not isinstance(c, list) for c in node)
    if simple or (isinstance(head, str) and head in _INLINE and _depth(node) <= 3):
        return "(" + " ".join(dumps(c) for c in node) + ")"
    pad = "\t" * (indent + 1)
    parts = []
    # keep leading atoms on the header line
    i = 0
    header = []
    while i < len(node) and not isinstance(node[i], list):
        header.append(_atom(node[i]))
        i += 1
    out = "(" + " ".join(header)
    for c in node[i:]:
        parts.append(pad + dumps(c, indent + 1))
    return out + "\n" + "\n".join(parts) + "\n" + "\t" * indent + ")"


def _depth(node):
    if not isinstance(node, list):
        return 0
    return 1 + max([_depth(c) for c in node] or [0])


# ---------- helpers for navigating parsed trees ----------

def find(node, key):
    """First child list whose head == key."""
    for c in node:
        if isinstance(c, list) and c and c[0] == key:
            return c
    return None


def find_all(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def value(node, key, default=None):
    c = find(node, key)
    if c is None or len(c) < 2:
        return default
    return c[1]


def num(x):
    return float(x)


def extract_toplevel(text, head, name):
    """Return the raw text of a top-level `(head "name" ...)` block in a lib file.

    Library files indent top-level symbols with exactly one tab, so we locate
    the opening line and then bracket-match (respecting quoted strings).
    """
    pat = re.compile(r'^(?:\t| {2})\(%s "%s"(?=[\s)])' % (re.escape(head), re.escape(name)), re.M)
    m = pat.search(text)
    if not m:
        # older / single-line formatting
        pat = re.compile(r'\(%s "%s"[\s)]' % (re.escape(head), re.escape(name)))
        m = pat.search(text)
        if not m:
            return None
    start = m.start() + text[m.start():].index("(")
    depth = 0
    i = start
    in_str = False
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None
