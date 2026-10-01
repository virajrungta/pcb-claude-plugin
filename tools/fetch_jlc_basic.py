"""Fetch JLCPCB's basic parts library (~350 parts) into data/jlc_parts.json.

Maintenance tool, run before a release:  python3 tools/fetch_jlc_basic.py
Uses JLCPCB's public parts-search endpoint; only stores what the catalog needs.
"""

import datetime
import json
import os
import subprocess
import sys
import time

URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "jlc_parts.json")


def page(n, extra):
    body = dict({"currentPage": n, "pageSize": 100, "keyword": "", "searchSource": "search"}, **extra)
    p = subprocess.run(["curl", "-s", "-m", "60", "-X", "POST", URL, "-H", "Content-Type: application/json",
                        "-H", "User-Agent: Mozilla/5.0", "-d", json.dumps(body)],
                       stdout=subprocess.PIPE, universal_newlines=True, check=True)
    return json.loads(p.stdout)["data"]["componentPageInfo"]


def fetch(extra, label):
    first = page(1, extra)
    rows = list(first["list"])
    pages = (first["total"] + 99) // 100
    for n in range(2, pages + 1):
        time.sleep(0.5)
        rows += page(n, extra)["list"]
    print("%s: %d parts" % (label, len(rows)), file=sys.stderr)
    return rows


def slim(x, cls):
    return {"code": x["componentCode"], "mpn": x.get("componentModelEn") or "",
            "package": x.get("componentSpecificationEn") or "", "category": x.get("componentTypeEn") or "",
            "brand": x.get("componentBrandEn") or "", "describe": (x.get("describe") or "")[:160],
            "stock": x.get("stockCount"), "class": cls}


def main():
    parts = {}
    for x in fetch({"componentLibraryType": "base"}, "basic"):
        parts[x["componentCode"]] = slim(x, "basic")
    data = {"fetched": datetime.date.today().isoformat(), "source": "JLCPCB basic parts library",
            "parts": sorted(parts.values(), key=lambda p: (p["category"], p["code"]))}
    with open(OUT, "w") as f:
        json.dump(data, f, indent=0, ensure_ascii=False)
    print("wrote %s (%d parts)" % (OUT, len(parts)), file=sys.stderr)


if __name__ == "__main__":
    main()
