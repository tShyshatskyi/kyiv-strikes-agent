"""Step 7 — the site. Turns data/out/registry.json and the agents' trace into
site/data.js, which site/index.html reads. Also downloads one photo per site
from official channels (Telegram's photo links expire, so this runs right after
the pipeline).

Run:  python -m pipeline.build_site            (add --no-photos to skip downloads)
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import httpx
from shapely.geometry import mapping

from pipeline.sources import CHANNELS
from pipeline.tools import DISTRICT_SHAPES
from pipeline.schemas import DISTRICTS_EN

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "out"
SITE = ROOT / "site"
MEDIA = SITE / "media"

WEAPON_CLASS = {"drone": "drone", "jet_drone": "drone", "ballistic": "missile",
                "cruise_missile": "missile", "missile": "missile", "unknown": "unknown"}


def districts_geojson() -> dict:
    feats = []
    for name, poly in DISTRICT_SHAPES.items():
        feats.append({"type": "Feature", "properties": {"name": DISTRICTS_EN.get(name, name)},
                      "geometry": mapping(poly.simplify(0.0008, preserve_topology=True))})
    return {"type": "FeatureCollection", "features": feats}


def round_coords(obj):
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, (list, tuple)):
        return [round_coords(x) for x in obj]
    if isinstance(obj, dict):
        return {k: round_coords(v) for k, v in obj.items()}
    return obj


BLOCKLIST = ROOT / "data" / "photo_blocklist.txt"


def _post_photos() -> dict:
    """Photo links per post, straight from the collected posts of official
    channels (the links expire within a day or two of collection)."""
    out = {}
    for handle, (_, _, kind) in CHANNELS.items():
        path = ROOT / "data" / "raw" / "telegram" / f"{handle}.jsonl"
        if kind != "official" or not path.exists():
            continue
        for line in path.open(encoding="utf-8"):
            p = json.loads(line)
            if p["photos"]:
                out[p["url"]] = p["photos"]
    return out


POST_PHOTOS = _post_photos()


def pick_photos(sites: list[dict], client: httpx.Client) -> None:
    """One photo per site, only when it is safe to say the photo shows that site.

    - from an official channel (city administration, emergency service);
    - from a post that the matcher assigned to this site and no other (a morning
      summary with one photo covers several places, so its photo shows none of
      them for certain);
    - not an image that turns up for more than one site (the administration's
      "operational information" title card is the same file every time);
    - not on data/photo_blocklist.txt (site ids rejected after a human look).
    """
    import hashlib
    blocked = set(BLOCKLIST.read_text(encoding="utf-8").split()) if BLOCKLIST.exists() else set()
    post_sites: dict = {}
    for s in sites:
        for src in s["sources"]:
            post_sites.setdefault(src["url"], set()).add(s["id"])
    chosen: dict = {}
    for s in sites:
        s["photo"] = None
        if s["id"] in blocked:
            continue
        own = [src["url"] for src in s["sources"]
               if src["kind"] == "official" and len(post_sites[src["url"]]) == 1]
        urls = [ph for u in own for ph in POST_PHOTOS.get(u, [])]
        if not urls:
            continue
        target = MEDIA / f"{s['id']}.jpg"
        data = target.read_bytes() if target.exists() else None
        if data is None:
            for url in urls[:2]:
                try:
                    r = client.get(url, timeout=30)
                except httpx.HTTPError:
                    continue
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                    data = r.content
                    break
        if data:
            chosen.setdefault(hashlib.md5(data).hexdigest(), []).append((s, target, data))
    for h, users in chosen.items():
        if len(users) > 1 or h in blocked:
            for _, target, _ in users:
                target.unlink(missing_ok=True)
            continue
        s, target, data = users[0]
        MEDIA.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        s["photo"] = f"media/{target.name}"


def main(argv: list[str]) -> None:
    reg = json.loads((OUT / "registry.json").read_text(encoding="utf-8"))
    trace = [json.loads(l) for l in (OUT / "trace.jsonl").read_text(encoding="utf-8").splitlines() if l]
    sites = reg["sites"]

    with httpx.Client(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        pick_photos(sites, client)

    # Alerts per attack day, split by kind.
    from datetime import datetime, timedelta
    alerts_by_day: dict = {}
    for a in reg["alerts"]:
        start = datetime.fromisoformat(a["start"])
        day = (start - timedelta(hours=9)).date().isoformat()
        d = alerts_by_day.setdefault(day, {"drone": 0, "missile": 0, "count": 0})
        d[a["kind"]] += a["minutes"]
        d["count"] += 1

    days = []
    for d in reg["days"]:
        days.append({"day": d["day"], "summary": d["summary"], "city": d["city_totals"],
                     "alerts": alerts_by_day.get(d["day"], {"drone": 0, "missile": 0, "count": 0}),
                     "stats": d["stats"]})
    known = {d["day"] for d in days}
    for day, a in alerts_by_day.items():
        if "2026-10-01" <= day <= "2026-11-23" and day not in known:
            days.append({"day": day, "summary": "", "city": {"killed": None, "injured": None},
                         "alerts": a, "stats": {}})
    days.sort(key=lambda d: d["day"])

    # Workflow numbers, all computed from what the agents actually did.
    st = [d["stats"] for d in reg["days"]]
    issues = Counter(k for s in st for k in s.get("issues_found", []))
    levels = Counter(s["level"] for s in sites)
    precision = Counter((s.get("geo") or {}).get("precision") for s in sites)
    auditor_notes = [t for t in trace if t["step"] == "auditor" and t["message"].startswith("revise")]
    workflow = {
        "collected": reg["collection"]["posts_read"],
        "channels": reg["collection"]["channels"],
        "per_channel": {CHANNELS[h][0]: n for h, n in reg["collection"]["posts_per_channel"].items()},
        "candidates": reg["collection"]["candidates"],
        "claims": sum(s.get("claims", 0) for s in st) + sum(s.get("rejected_quotes", 0) for s in st),
        "rejected_quotes": sum(s.get("rejected_quotes", 0) for s in st),
        "kept_claims": sum(s.get("claims", 0) for s in st),
        "sites": len(sites),
        "audits": sum(s.get("audit_rounds", 0) for s in st),
        "sent_back": sum(s.get("revisions", 0) for s in st),
        "issues": dict(issues),
        "removed_by_auditor": sum(s.get("dropped", 0) for s in st),
        "open_flags": sum(1 for s in sites if s.get("flags")),
        "levels": dict(levels),
        "precision": {str(k): v for k, v in precision.items()},
        "llm": reg.get("llm", {}),
        "alerts": len(reg["alerts"]),
        "alert_hours": round(sum(a["minutes"] for a in reg["alerts"]) / 60),
        "revise_days": len(auditor_notes),
    }

    # Real examples of the auditor catching something, for the workflow section.
    examples = []
    for d in reg["days"]:
        for item in d.get("audit_examples", []):
            examples.append({"day": d["day"], **item})

    slim = []
    for s in sites:
        g = s.get("geo") or {}
        slim.append({
            "id": s["id"], "day": s["day"], "title": s["title"], "summary": s["summary"],
            "district": s.get("district_en"), "address": s.get("address"),
            "target": s["target"], "weapon": s["weapon"], "wclass": WEAPON_CLASS.get(s["weapon"], "unknown"),
            "time": s.get("time"), "first": s["first_report"], "killed": s.get("killed"),
            "injured": s.get("injured"), "level": s["level"], "voices": len(s["voices"]),
            "lat": g.get("lat"), "lon": g.get("lon"), "precision": g.get("precision"),
            "sources": [{"name": x["name"], "kind": x["kind"], "url": x["url"],
                         "posted": x["posted"], "quote": x["quote"]} for x in s["sources"]],
            "flags": s.get("flags", []), "photo": s.get("photo"),
        })

    from pipeline.wikipedia import crosscheck
    wiki = crosscheck(days, slim)
    workflow["wikipedia"] = wiki["summary"]

    from datetime import timedelta as _td
    from pipeline.sources import PERIOD_START, PERIOD_END
    period = [PERIOD_START.date().isoformat(), (PERIOD_END - _td(days=1)).date().isoformat()]
    days = [d for d in days if period[0] <= d["day"] <= period[1]]
    data = {"generated": reg["generated"], "period": period, "days": days,
            "sites": slim, "workflow": workflow, "examples": examples[:12],
            "wiki": wiki, "districts": round_coords(districts_geojson())}
    SITE.mkdir(exist_ok=True)
    (SITE / "data.js").write_text("window.REGISTRY = " + json.dumps(data, ensure_ascii=False) + ";\n",
                                  encoding="utf-8")
    print(f"site/data.js: {len(slim)} sites, {len(days)} days, "
          f"{sum(1 for s in slim if s['photo'])} photos")


if __name__ == "__main__":
    main(sys.argv[1:])
