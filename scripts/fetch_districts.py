"""Fetch Kyiv's 10 administrative districts from OpenStreetMap as GeoJSON.

Run once; the output is committed. Requires Node (for osmtogeojson).

    python scripts/fetch_districts.py

Findings that cost time, recorded so they are not rediscovered:

- Kyiv city is OSM relation 421866 at admin_level=4.
- Its districts are admin_level=**10**, not 9 or 8. Levels 8 and 9 return zero.
- `area(3600421866)` does not resolve; `rel(421866); map_to_area` does.
- The request must be form-encoded (`data=...`). A raw text body returns HTTP 406.
- Overpass rate-limits aggressively; do not loop without a delay.
"""

import json
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

OVERPASS = "https://overpass-api.de/api/interpreter"
KYIV_RELATION = 421866
ADMIN_LEVEL = 10
OUT = Path("data/kyiv_districts.geojson")

QUERY = f"""
[out:json][timeout:180];
rel({KYIV_RELATION});
map_to_area->.kyiv;
rel(area.kyiv)["boundary"="administrative"]["admin_level"="{ADMIN_LEVEL}"];
out geom;
"""

EXPECTED = {
    "Голосіївський", "Дарницький", "Деснянський", "Дніпровський",
    "Оболонський", "Печерський", "Подільський", "Святошинський",
    "Солом'янський", "Шевченківський",
}


def normalize_apostrophes(text: str) -> str:
    """OSM writes Солом’янський with U+2019. Our enum uses the ASCII apostrophe."""
    return text.replace("’", "'").replace("ʼ", "'").replace("`", "'")


def main() -> int:
    body = urllib.parse.urlencode({"data": QUERY}).encode("utf-8")
    request = urllib.request.Request(
        OVERPASS, data=body,
        headers={"User-Agent": "kyiv-strikes-mcp/1.0 (student project)"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        raw = json.load(response)

    print(f"Overpass returned {len(raw['elements'])} elements.")

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(raw, handle, ensure_ascii=False)
        temp_path = handle.name

    converted = subprocess.run(
        ["npx", "-y", "osmtogeojson", temp_path],
        capture_output=True, text=True, encoding="utf-8", shell=True,
    )
    if converted.returncode != 0:
        print("osmtogeojson failed:", converted.stderr[-2000:])
        return 1

    geojson = json.loads(converted.stdout)

    features = []
    for feature in geojson["features"]:
        name = normalize_apostrophes(feature["properties"].get("name", ""))
        name = name.removesuffix(" район").strip()
        if not name:
            continue
        features.append({
            "type": "Feature",
            "properties": {"name": name},
            "geometry": feature["geometry"],
        })

    names = {f["properties"]["name"] for f in features}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"type": "FeatureCollection", "features": features},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Wrote {len(features)} districts to {OUT} ({OUT.stat().st_size:,} bytes)")
    for name in sorted(names):
        print("  ", name)

    missing = EXPECTED - names
    extra = names - EXPECTED
    if missing or extra:
        print(f"\nMISMATCH  missing={missing}  unexpected={extra}")
        return 1
    print("\nAll 10 expected districts present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
