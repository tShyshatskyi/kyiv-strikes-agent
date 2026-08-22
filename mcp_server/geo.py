"""Geometry and Ukrainian address handling. No network access lives here."""

import json
import math
import re
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.prepared import prep

EARTH_RADIUS_M = 6_371_000.0

# OSM writes Солом’янський with U+2019; news sites use the ASCII apostrophe or
# U+02BC. All three must collapse to one form or district matching silently fails.
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "´": "'"})

# Longest forms first so that "пр-т" is not partially eaten by a shorter pattern.
_STREET_ABBREVIATIONS = [
    (r"\bпроспект\b|\bпросп\.?|\bпр-т\b|\bпр-кт\b", "проспект"),
    (r"\bвулиця\b|\bвул\.?", "вулиця"),
    (r"\bбульвар\b|\bбул\.?|\bб-р\b", "бульвар"),
    (r"\bпровулок\b|\bпров\.?", "провулок"),
    (r"\bплоща\b|\bпл\.?", "площа"),
    (r"\bрайон\b|\bр-н\b", "район"),
    (r"\bбудинок\b|\bбуд\.?", ""),
]


def normalize_apostrophes(text: str) -> str:
    return text.translate(_APOSTROPHES)


def load_districts(path: str | Path) -> dict:
    """Load Kyiv district polygons keyed by bare district name.

    Names are apostrophe-normalized and stripped of the "район" suffix so the
    keys match the KyivDistrict literal in common.schemas exactly.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    districts: dict = {}
    for feature in data["features"]:
        name = normalize_apostrophes(feature["properties"].get("name", ""))
        name = re.sub(r"\s*район\s*$", "", name).strip()
        if not name:
            continue
        districts[name] = prep(shape(feature["geometry"]))
    return districts


def district_for_point(lat: float, lon: float, districts: dict) -> str | None:
    """Return the district containing the point, or None if outside Kyiv."""
    point = Point(lon, lat)  # GeoJSON is (x=lon, y=lat)
    for name, polygon in districts.items():
        if polygon.contains(point):
            return name
    return None


def normalize_address(text: str) -> str:
    """Collapse Ukrainian address abbreviations into a canonical lowercase form.

    Used both as the geocoder query and as the cache/fixture key, so it must be
    stable: the same address written five ways must produce one string.
    """
    result = normalize_apostrophes(text).lower().strip()
    for pattern, replacement in _STREET_ABBREVIATIONS:
        result = re.sub(pattern, replacement, result)
    result = result.replace(",", " ")
    result = re.sub(r"\s+", " ", result)
    return result.strip()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
