"""Deterministic tools the agents' work passes through. No model in here.

The agents read and judge; these check. Each one exists because an agent left
alone gets this exact thing wrong now and then:

- `check_quote`    the reader must point at words that are really in the post;
- `confirmation`   five channels reposting one statement are one witness;
- `site_figures`   casualty counts grow over a day, so take the latest, not the sum;
- `geocode`        an address must land inside a real Kyiv district polygon.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
from shapely.geometry import Point, shape

from mcp_server.geo import haversine_m, normalize_address, normalize_apostrophes
from pipeline.schemas import DISTRICTS
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
    if len(parts) >= 2 and all(p in t for p in parts):
        return True
    # A full stop where the post has a line break, or a dropped comma, is not a
    # paraphrase: every word must still be there, in the same order.
    words_q, words_t = _words(q), _words(t)
    return len(words_q) >= 3 and f" {words_q} " in f" {words_t} "


def _words(text: str) -> str:
    return " ".join(re.findall(r"[\w'’ʼ-]+", text))


# --- confirmation -----------------------------------------------------------

LEVELS = ["aggregator_only", "media", "official", "confirmed", "disputed"]

# Words too common to prove a newsroom did its own work: they appear in every
# briefing, so finding one in a media quote says nothing.
_COMMON = {
    "внаслідок", "зафіксовано", "влучання", "пошкоджено", "постраждали", "район",
    "районі", "києві", "київ", "києва", "місті", "наслідки", "інформація",
    "оперативна", "рятувальники", "служби", "медики", "попередньо", "станом",
    "повідомив", "повідомили", "продовжують", "triage", "update",
}


_TIME = re.compile(r"\b\d{1,2}[:.]\d{2}\b")


def _date_numbers(posted: str | None) -> set[str]:
    """Numbers that only say when: the post's day of month and the day before
    (a night runs over midnight), its month and its year."""
    if not posted:
        return set()
    from datetime import date, timedelta
    try:
        d = date.fromisoformat(posted[:10])
    except ValueError:
        return set()
    prev = d - timedelta(days=1)
    return {str(d.day), f"{d.day:02d}", str(prev.day), f"{prev.day:02d}",
            str(d.month), f"{d.month:02d}", str(d.year), str(d.year)[2:]}


def is_latin(text: str) -> bool:
    """Is this quote written in English rather than Ukrainian?"""
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return latin > len(re.findall(r"[А-Яа-яІіЇїЄєҐґ]", text or ""))


def _detail_tokens(text: str, posted: str | None = None, names: bool = True) -> set[str]:
    """Numbers and names a report gives: the specifics a newsroom can only have
    from its own reporter, a photo or a witness.

    A capitalised word at the start of a sentence is skipped — it is the
    sentence, not a name. Clock times, and numbers that are just the date the
    post itself was written on, say nothing about the strike and are dropped.
    """
    t = normalize_apostrophes(text or "")
    out = set(re.findall(r"\d+", _TIME.sub(" ", t))) - _date_numbers(posted)
    if not names:
        return out
    for m in re.finditer(r"[А-ЯІЇЄҐA-Z][а-яіїєґa-zʼ'’-]{3,}", t):
        before = t[:m.start()].rstrip(" \t")
        if before and before[-1] not in ".!?:;…«\"'([\n•-":
            word = m.group().lower()
            if word not in _COMMON:
                out.add(word)
    return out - _COMMON


def media_adds_detail(quote: str, official_text: str, posted: str | None = None) -> bool:
    """Does this media quote carry a figure or a name that no official post for
    the same place had already given?

    An English quote cannot be compared word for word with Ukrainian briefings:
    "Kyiv", "Mayor" or "Thursday" would all look new. Until quotes are
    translated, only its numbers are compared.
    """
    official = _vocabulary(official_text)
    new = _detail_tokens(quote, posted, names=not is_latin(quote))
    return any(_stem(w) not in official and not _stem(w).startswith(_COMMON_STEMS) for w in new)


def _stem(word: str) -> str:
    """Crude Ukrainian stem: the first six letters. 'Солом'янському' and
    'Солом'янський' are the same district in two cases."""
    w = word.lower()
    return w if w.isdigit() else w[:6]


# Names that are in every report about Kyiv, so a newsroom using them adds
# nothing. District names are here too: the official quote kept for a place
# often leaves out the district its full post gave, and a newsroom naming the
# district is retelling that post, not reporting from the street.
_COMMON_STEMS = ("україн", "києв", "київ", "kyiv", "столиц",
                 *{normalize_apostrophes(d).lower()[:6] for d in DISTRICTS})


def _vocabulary(text: str) -> set[str]:
    """Every word and number an official post used, in any case and any
    position: a district named at the start of a sentence is still named."""
    t = normalize_apostrophes(text or "")
    return set(re.findall(r"\d+", t)) | {_stem(w) for w in re.findall(r"[^\W\d_][\wʼ'’-]{3,}", t)}


# Auditor complaints that question whether the place, as recorded, is real:
# two places merged into one, or a report from another attack or another town.
# Others (a wrong weapon, two car fires that may be one) leave the strike itself
# beyond doubt and are shown as a note, not as "disputed".
DISPUTING = {"wrong_merge", "not_this_attack", "not_kyiv_city"}


def is_disputed(flag_kinds: list[str]) -> bool:
    return any(k in DISPUTING for k in flag_kinds or [])


def confirmation(sources: list[dict], disputed: bool = False) -> dict:
    """How well a place is confirmed, counting only voices that can disagree.

    `sources` are this place's reports, each with `channel`, `posted` and
    `quote`. City hall's three channels are one voice. A newsroom counts as a
    second voice only when its quote adds a figure or a name that no earlier
    official post for this place gave; otherwise it is retelling the briefing
    and cannot confirm it.
    """
    voices, official, retold = set(), set(), set()
    official_so_far = ""
    for src in sorted(sources, key=lambda s: s.get("posted") or ""):
        handle = src["channel"]
        if handle not in CHANNELS:
            continue
        voice, kind = CHANNELS[handle][1], CHANNELS[handle][2]
        if kind == "aggregator":
            continue
        if kind == "official":
            voices.add(voice)
            official.add(voice)
            official_so_far += " " + (src.get("quote") or "")
        elif media_adds_detail(src.get("quote") or "", official_so_far, src.get("posted")):
            voices.add(voice)
        else:
            retold.add(voice)
    if disputed:
        level = "disputed"
    elif len(voices) >= 2:
        level = "confirmed"
    elif official:
        level = "official"
    elif voices or retold:
        level = "media"
    else:
        level = "aggregator_only"
    return {"level": level, "voices": sorted(voices), "official_voices": sorted(official),
            "retold_voices": sorted(retold),
            "channels": sorted({s["channel"] for s in sources if s["channel"] in CHANNELS})}


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


def district_point(district: str) -> tuple[float, float]:
    """The district's own label point, for a report that names only the district.

    It is deliberately the same point for every such report: scattering them
    inside the district would draw addresses nobody gave us. The map shades the
    district instead and draws dots only where an address is known.
    """
    c = DISTRICT_SHAPES[district].representative_point()
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


def geocode(address: str | None, district: str | None) -> dict:
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
        lat, lon = district_point(district)
        return {"lat": round(lat, 6), "lon": round(lon, 6), "district": district,
                "precision": "district", "matched": None}
    return {"lat": None, "lon": None, "district": district, "precision": None, "matched": None}


def distance_m(a: dict, b: dict) -> float:
    return haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
