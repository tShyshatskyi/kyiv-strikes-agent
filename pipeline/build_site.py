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
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from shapely.geometry import mapping

from pipeline.sources import CHANNELS, PERIOD_END, PERIOD_START
from pipeline.tools import DISTRICT_SHAPES
from pipeline.schemas import DISTRICTS_EN

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "out"
SITE = ROOT / "site"
MEDIA = SITE / "media"

WEAPON_CLASS = {"drone": "drone", "jet_drone": "drone", "ballistic": "missile",
                "cruise_missile": "missile", "missile": "missile", "unknown": "unknown"}


# Phrases where the matcher stops describing the strike and starts explaining
# its own bookkeeping to the auditor. Everything from there on is service text.
SERVICE = re.compile(
    r"\bc\d+\b|\bsite \d|\bclaims?\b|\baudit(?:or)?\b|kept (?:separate|apart|as its own)|"
    r"per (?:audit|auditor|feedback)|classified as|likely an early", re.I)
# Where a sentence or clause ends, so the trim lands on a readable boundary.
BOUNDARY = re.compile(r"[.;—]|,\s+(?=while|although|though|since|because|plus)")


def strip_service_text(text: str) -> str:
    """Cut a public description at the point it turns into notes for the agents.

    Nothing is reworded and nothing is added — the trailing clause is simply
    dropped, because it describes the registry, not the attack. Descriptions
    that are service text from the first word are left alone and counted, so
    the build says out loud how many it could not clean.
    """
    m = SERVICE.search(text or "")
    if not m:
        return text
    cuts = [b.end() for b in BOUNDARY.finditer(text, 0, m.start())]
    if not cuts:
        return text
    kept = text[:cuts[-1]].rstrip(" ,;—")
    return kept if kept.endswith(".") else kept + "."


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


def short_label(a: dict) -> str:
    """'6–7 Oct' for a night, '19 Oct, day' for a daytime attack."""
    from datetime import date as _d
    d = _d.fromisoformat(a["date"])
    mon = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if a["kind"] == "day":
        return f"{d.day} {mon[d.month]}, day"
    n = d + timedelta(days=1)
    return f"{d.day}–{n.day} {mon[n.month]}" if n.month == d.month else f"{d.day} {mon[d.month]} – {n.day} {mon[n.month]}"


def main(argv: list[str]) -> None:
    from pipeline import attacks as attack_rules
    from pipeline.wikipedia import crosscheck_attacks

    reg = json.loads((OUT / "registry.json").read_text(encoding="utf-8"))
    if reg.get("unit") != "attack":
        raise SystemExit("data/out/registry.json is from the old day-based pipeline; rerun the pipeline")
    sites = reg["sites"]

    if "--no-photos" in argv:
        for s in sites:
            target = MEDIA / f"{s['id']}.jpg"
            s["photo"] = f"media/{target.name}" if target.exists() else None
    else:
        with httpx.Client(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            pick_photos(sites, client)

    first = PERIOD_START.date().isoformat()
    last = (PERIOD_END - timedelta(days=1)).date().isoformat()

    # Every attack the alerts show, including quiet ones the agents found no
    # damage for: the timeline shows alert hours for all of them.
    done = {a["id"]: a for a in reg["attacks"]}
    units = []
    for a in attack_rules.segment(reg["alerts"]):
        if not (first <= a["date"] <= last):
            continue
        r = done.get(a["id"], {})
        city = r.get("city_totals") or {}
        units.append({
            "id": a["id"], "title": a["title"], "short": short_label(a), "kind": a["kind"],
            "date": a["date"], "start": a["start"], "end": a["end"],
            "alerts": {"drone": a["alert_minutes"].get("drone", 0),
                       "missile": a["alert_minutes"].get("missile", 0), "count": a["alerts"]},
            "summary": strip_service_text(r.get("summary") or ""),
            "city": {"killed": city.get("killed"), "injured": city.get("injured")},
            "stats": r.get("stats") or {},
        })

    in_period = [a for a in reg["alerts"] if first <= a["start"][:10] <= last]
    st = [a.get("stats") or {} for a in reg["attacks"]]
    issues = Counter(k for s in st for k in s.get("issues_found", []))
    levels = Counter(s["level"] for s in sites)
    precision = Counter((s.get("geo") or {}).get("precision") for s in sites)
    reading = reg.get("reading") or {}
    workflow = {
        "collected": reg["collection"]["posts_read"],
        "channels": reg["collection"]["channels"],
        "per_channel": {CHANNELS[h][0]: n for h, n in reg["collection"]["posts_per_channel"].items()},
        "candidates": reg["collection"]["candidates"],
        "claims": reading.get("claims", 0),
        "rejected_quotes": reading.get("rejected_quotes", 0),
        "kept_claims": sum(s.get("claims", 0) for s in st),
        "set_aside": (reg.get("set_aside") or {}).get("claims", {}),
        "sites": len(sites),
        "attacks": len({s["attack"] for s in sites}),
        "attacks_night": len({s["attack"] for s in sites if s["attack"].endswith("-N")}),
        "audits": sum(s.get("audit_rounds", 0) for s in st),
        "sent_back": sum(s.get("revisions", 0) for s in st),
        "sent_back_attacks": sum(1 for s in st if s.get("revisions")),
        "validate_sent_back": sum(s.get("validate_sent_back", 0) for s in st),
        "validate_fixes": sum(len(a.get("validate_fixes") or []) for a in reg["attacks"]),
        "issues": dict(issues),
        "removed_by_auditor": sum(s.get("dropped", 0) for s in st),
        "open_flags": sum(1 for s in sites if s.get("notes")),
        "levels": dict(levels),
        "precision": {str(k): v for k, v in precision.items()},
        "llm": reg.get("llm", {}),
        "alerts": len(in_period),
        "alert_hours": round(sum(a["minutes"] for a in in_period) / 60),
    }

    titles = {u["id"]: u["title"] for u in units}
    examples = [{"attack": a["id"], "title": titles.get(a["id"], a["title"]), **item}
                for a in reg["attacks"] for item in a.get("audit_examples", [])]

    slim = []
    for s in sites:
        g = s.get("geo") or {}
        slim.append({
            "id": s["id"], "attack": s["attack"], "day": s["day"], "title": s["title"],
            "summary": strip_service_text(s["summary"]),
            "district": s.get("district_en"), "address": s.get("address"),
            "target": s["target"], "weapon": s["weapon"], "wclass": WEAPON_CLASS.get(s["weapon"], "unknown"),
            "time": s.get("time"), "first": s["first_report"], "killed": s.get("killed"),
            "injured": s.get("injured"), "level": s["level"], "voices": len(s["voices"]),
            "lat": g.get("lat"), "lon": g.get("lon"), "precision": g.get("precision"),
            "sources": [{"name": x["name"], "kind": x["kind"], "url": x["url"],
                         "posted": x["posted"], "quote": x["quote"]} for x in s["sources"]],
            # Only the auditor's note for readers; its working notes stay in the trace.
            "flags": s.get("notes", []), "photo": s.get("photo"),
        })

    wiki = crosscheck_attacks(units, slim)
    workflow["wikipedia"] = wiki["summary"]

    data = {"generated": reg["generated"], "period": [first, last], "attacks": units,
            "sites": slim, "workflow": workflow, "examples": examples[:12],
            "wiki": wiki, "districts": round_coords(districts_geojson())}
    SITE.mkdir(exist_ok=True)
    (SITE / "data.js").write_text("window.REGISTRY = " + json.dumps(data, ensure_ascii=False) + ";\n",
                                  encoding="utf-8")
    public = [x["summary"] for x in slim] + [u["summary"] for u in units] + [n for x in slim for n in x["flags"]]
    dirty = [t for t in public if SERVICE.search(t or "")]
    print(f"site/data.js: {len(slim)} sites, {len(units)} attacks "
          f"({workflow['attacks']} with places), {sum(1 for s in slim if s['photo'])} photos")
    print(f"public text: {len(dirty)} descriptions still carry agent notes")
    for t in dirty:
        print("  !", t[:160])


if __name__ == "__main__":
    main(sys.argv[1:])
