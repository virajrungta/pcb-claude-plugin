"""Look up and search LCSC / JLCPCB parts (value, package, basic or extended, stock, price).

Uses JLCPCB's public parts-library search (the data behind jlcpcb.com/parts),
falling back to the EasyEDA component API for lookups. Results are cached in
~/.cache/kipcb/jlc_parts.json for a week so repeated lookups cost nothing.
"""

import json
import os
import re
import subprocess
import time

from . import kienv

JLC = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
EASYEDA = "https://easyeda.com/api/products/%s/components?version=6.4.19.5"
TTL = 7 * 24 * 3600


def _cache_path():
    return os.path.join(kienv.cache_dir(), "jlc_parts.json")


def _load_cache():
    try:
        with open(_cache_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    with open(_cache_path(), "w") as f:
        json.dump(cache, f)


def _curl(args):
    p = subprocess.run(["curl", "-s", "-m", "20"] + args, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, universal_newlines=True)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def _jlc_query(keyword, size=10, library=None):
    body = {"currentPage": 1, "pageSize": size, "keyword": keyword, "searchSource": "search"}
    if library:
        body["componentLibraryType"] = library
    r = _curl(["-X", "POST", "-H", "Content-Type: application/json", "-d", json.dumps(body), JLC])
    try:
        return r["data"]["componentPageInfo"]["list"] or []
    except (TypeError, KeyError):
        return None


def _from_jlc(p):
    prices = p.get("componentPrices") or []
    return {
        "code": p["componentCode"], "found": True,
        "mpn": p.get("componentModelEn", ""),
        "manufacturer": p.get("componentBrandEn", ""),
        "value": "",
        "package": p.get("componentSpecificationEn", ""),
        "class": "basic" if p.get("componentLibraryType") == "base" else
                 ("preferred" if p.get("preferredComponentFlag") else "extended"),
        "stock": p.get("stockCount"),
        "price": prices[0]["productPrice"] if prices else None,
        "category": p.get("firstSortName", ""),
        "description": (p.get("describe") or "")[:120],
    }


def _fetch_easyeda(code):
    r = _curl([EASYEDA % code])
    if r is None:
        return None
    r = r.get("result")
    if not r:
        return {"code": code, "found": False}
    para = (r.get("dataStr") or {}).get("head", {}).get("c_para", {}) if isinstance(r.get("dataStr"), dict) else {}
    lc = r.get("lcsc") or {}
    cls = para.get("JLCPCB Part Class", "")
    return {
        "code": code, "found": True,
        "mpn": para.get("Manufacturer Part") or r.get("title", ""),
        "manufacturer": para.get("Manufacturer", "").split("(")[0],
        "value": para.get("Value", ""),
        "package": para.get("package", ""),
        "class": "basic" if "Basic" in cls else ("preferred" if "Preferred" in cls else "extended"),
        "stock": lc.get("stock"),
        "price": lc.get("price"),
        "description": (r.get("description") or "")[:120],
    }


def _fetch(code):
    lst = _jlc_query(code, size=5)
    if lst is not None:
        for p in lst:
            if p.get("componentCode", "").upper() == code:
                return _from_jlc(p)
    info = _fetch_easyeda(code)
    if info is None and lst is not None:
        return {"code": code, "found": False}
    return info


def normalize(code):
    code = str(code).strip().upper()
    return code if code.startswith("C") else "C" + code


def lookup(codes, refresh=False):
    """{code: info} for LCSC numbers like 'C25804'. Unknown numbers get found=False."""
    cache = _load_cache()
    out = {}
    now = time.time()
    dirty = False
    for code in codes:
        code = normalize(code)
        hit = cache.get(code)
        if hit and not refresh and now - hit.get("_t", 0) < TTL:
            out[code] = hit
            continue
        info = _fetch(code)
        if info is None:
            out[code] = {"code": code, "found": False, "error": "network"}
            continue
        info["_t"] = now
        cache[code] = info
        out[code] = info
        dirty = True
        time.sleep(0.3)          # be gentle with the public API
    if dirty:
        _save_cache(cache)
    return out


def search(keyword, limit=8, basic_only=False):
    """Parts matching a keyword ('AMS1117-3.3', '10uF 0805', 'SHT31'), most stock first.
    Returns None when offline."""
    lst = _jlc_query(keyword, size=30, library="base" if basic_only else None)
    if lst is None:
        return None
    rows = [_from_jlc(p) for p in lst]
    rank = {"basic": 0, "preferred": 1, "extended": 2}
    rows.sort(key=lambda r: (r["stock"] in (0, None), rank.get(r["class"], 3), -(r["stock"] or 0)))
    return rows[:limit]


def describe(info):
    if not info.get("found"):
        return "%s: not found%s" % (info["code"], " (network error)" if info.get("error") else "")
    stock = info.get("stock")
    price = info.get("price")
    desc = info.get("value") or re.sub(r"\s*ROHS$", "", info.get("description", ""))[:70]
    return "%s: %s | %s | %s, stock %s%s%s" % (
        info["code"], info.get("mpn", ""), info.get("package", ""), info.get("class", "?"),
        "{:,}".format(stock) if isinstance(stock, int) else "?",
        (", $%.4f" % price) if isinstance(price, (int, float)) else "",
        (" | " + desc) if desc else "")


def check_bom(codes):
    """Stock problems for a BOM's LCSC numbers: list of (code, message). Empty when all fine or offline."""
    if not codes:
        return []
    res = lookup(sorted(set(codes)))
    issues = []
    for code, info in sorted(res.items()):
        if info.get("error"):
            return []            # offline: say nothing rather than guess
        if not info.get("found"):
            issues.append((code, "not a JLCPCB/LCSC part number"))
        elif info.get("stock") == 0:
            issues.append((code, "%s is out of stock at JLCPCB" % info.get("mpn", code)))
    return issues
