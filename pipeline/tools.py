"""Deterministic tools the agents' work passes through. No model in here.

The agents read and judge; these check. Each one exists because an agent left
alone gets this exact thing wrong now and then:

- `check_quote`    the reader must point at words that are really in the post;
- `confirmation`   five channels reposting one statement are one witness;
- `site_figures`   casualty counts grow over a day, so take the latest, not the sum;
- `geocode`        an address must land inside a real Kyiv district polygon.
"""

import hashlib
import json
import math
import re
import time
from pathlib import Path

import httpx
from shapely.geometry import Point, shape

from mcp_server.geo import haversine_m, normalize_address, normalize_apostrophes
from pipeline.sources import CHANNELS, counts_as_witness

ROOT = Path(__file__).resolve().parent.parent

# --- quotes -----------------------------------------------------------------

_SPACES = re.compile(r"\s+")


def _norm(text: str) -> str:
    text = normalize_apostrophes(text or "").replace("«", '"').replace("»", '"')
    text = text.replace("–", "-").replace("—", "-").replace("’", "'")
    return _SPACES.sub(" ", text).strip().lower()


def check_quote(quote: str, post_text: str) -> bool:
    """Is the quote really in the post? Whitespace, quotes and apostrophes are
    normalised; anything else must match. A quote that only ellipsises the
    middle is accepted if both halves are present."""
    q, t = _norm(quote).strip(" .…\"'"), _norm(post_text)
    if len(q) < 12:
        return False
    if q in t:
        return True
    parts = [p.strip(" .") for p in re.split(r"\.\.\.|…", q) if len(p.strip()) >= 12]
    return len(parts) >= 2 and all(p in t for p in parts)


# --- confirmation -----------------------------------------------------------

LEVELS = ["aggregator_only", "single_source", "official", "confirmed"]


def confirmation(channels: list[str]) -> dict:
    witnesses = {CHANNELS[c][1] for c in channels if counts_as_witness(c)}
    official = {CHANNELS[c][1] for c in channels if CHANNELS[c][2] == "official"}
    if len(witnesses) >= 2:
        level = "confirmed"
    elif official:
        level = "official"
    elif witnesses:
        level = "single_source"
    else:
        level = "aggregator_only"
    return {"level": level, "voices": sorted(witnesses),
            "official_voices": sorted(official), "channels": sorted(set(channels))}


def site_figures(claims: list[dict]) -> dict:
    """Latest reported figure wins: a count that rises through the day is the
    same people being found, not new ones."""
    out = {}
    for field in ("killed", "injured"):
        reported = sorted((c["post_time"], c[field]) for c in claims if c.get(field) is not None)
        out[field] = reported[-1][1] if reported else None
    return out


# --- geography --------------------------------------------------------------

_GEO = json.loads((ROOT / "data" / "kyiv_districts.geojson").read_text(encoding="utf-8"))
DISTRICT_SHAPES = {}
for _f in _GEO["features"]:
    _name = re.sub(r"\s*район\s*$", "", normalize_apostrophes(_f["properties"].get("name", ""))).strip()
    if _name:
        DISTRICT_SHAPES[_name] = shape(_f["geometry"])


def district_of(lat: float, lon: float) -> str | None:
    p = Point(lon, lat)
    for name, poly in DISTRICT_SHAPES.items():
        if poly.contains(p):
            return name
    return None


def district_point(district: str, salt: str) -> tuple[float, float]:
    """A stable point inside the district for a report that names only the
    district. Spread around the district's interior so markers do not stack,
    and always inside the polygon."""
    poly = DISTRICT_SHAPES[district]
    c = poly.representative_point()
    h = int(hashlib.md5(salt.encode()).hexdigest(), 16)
    for i in range(20):
        ang = ((h >> (i * 3)) % 360) * math.pi / 180
        r = 0.004 + ((h >> (i * 5)) % 100) / 100 * 0.018
        p = Point(c.x + r * math.cos(ang) * 1.5, c.y + r * math.sin(ang))
        if poly.contains(p):
            return p.y, p.x
    return c.y, c.x


_CACHE = ROOT / "data" / "cache" / "geocode.json"
_cache = json.loads(_CACHE.read_text(encoding="utf-8")) if _CACHE.exists() else {}
_last_call = [0.0]
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "kyiv-strikes-registry/2.0 (open-source research tool)"}


def _nominatim(query: str) -> list[dict]:
    if query in _cache:
        return _cache[query]
    wait = 1.1 - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()
    try:
        r = httpx.get(NOMINATIM, headers=UA, timeout=30, params={
            "q": query, "format": "jsonv2", "limit": 5, "countrycodes": "ua",
            "viewbox": "30.185,50.590,30.825,50.213", "bounded": 1,
            "accept-language": "uk", "addressdetails": 0})
        result = r.json() if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    _cache[query] = result
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    return result


def geocode(address: str | None, district: str | None, salt: str) -> dict:
    """Place a site on the map, and say how precisely.

    precision: "building" (house number matched), "street", "district"
    (only the district is known) or None (nothing to go on).
    """
    if address:
        has_number = bool(re.search(r"\d", address))
        query = normalize_address(address)
        if "київ" not in query:
            query += " київ"
        hits = []
        for m in _nominatim(query):
            lat, lon = float(m["lat"]), float(m["lon"])
            d = district_of(lat, lon)
            if d:
                hits.append({"lat": lat, "lon": lon, "district": d,
                             "name": m.get("display_name", "")})
        if district:
            same = [h for h in hits if h["district"] == district]
            hits = same or ([] if hits and all(h["district"] != district for h in hits) else hits)
        if hits:
            best = hits[0]
            return {"lat": round(best["lat"], 6), "lon": round(best["lon"], 6),
                    "district": best["district"],
                    "precision": "building" if has_number else "street",
                    "matched": best["name"]}
    if district and district in DISTRICT_SHAPES:
        lat, lon = district_point(district, salt)
        return {"lat": round(lat, 6), "lon": round(lon, 6), "district": district,
                "precision": "district", "matched": None}
    return {"lat": None, "lon": None, "district": district, "precision": None, "matched": None}


def distance_m(a: dict, b: dict) -> float:
    return haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
