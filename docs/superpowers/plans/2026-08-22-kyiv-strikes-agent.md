# Kyiv Strikes Incident Registry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a LangGraph agent that turns one strike-on-Kyiv query into verified, geolocated, provenance-tracked incident rows, using Playwright MCP for source acquisition and a custom MCP server for domain reconciliation.

**Architecture:** Two MCP servers run as separate stdio processes. Playwright MCP fetches candidate articles from whitelisted public sites. An agent node performs LLM extraction into a typed `IncidentClaim`; only that typed object crosses the custom MCP boundary. The custom server (`kyiv-strikes-mcp`) is deterministic and LLM-free: it geocodes against Nominatim plus a local district polygon dataset, reconciles claims across independent sources, and writes deduplicated incidents to SQLite with per-field provenance.

**Tech Stack:** Python 3.14, `mcp` 1.29.0 (server), LangGraph 1.2.11 + `langchain-mcp-adapters` 0.3.2 + `langchain-openai` 1.6.0 via OpenRouter (agent), Pydantic v2, Shapely 2.1.2, httpx 0.28.1, SQLite (stdlib), pytest 9.1.1, Node 24 + `@playwright/mcp` (existing server).

**Source spec:** `docs/superpowers/specs/2026-08-22-kyiv-strikes-agent-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.14.4** is the only interpreter available on this machine. All dependencies above are verified to resolve on it.
- **No secrets in the repository.** `OPENROUTER_API_KEY` and `NOMINATIM_CONTACT` come from the environment. `.env` is gitignored; `.env.example` is committed.
- **Nominatim usage policy:** maximum 1 request/second, and every request must carry a `User-Agent` containing a real contact address. Responses are cached in SQLite.
- **Offline mode:** `KSM_OFFLINE=1` must swap only the HTTP transport. Normalization, parsing, validation, and point-in-polygon run through the identical code path in both modes. A conditional branch returning a prewritten answer is an assignment failure, not an optimization.
- **Error envelope:** every custom MCP tool returns exactly `{"ok": true, "data": {...}}` or `{"ok": false, "error": {"code": ..., "message": ..., "retriable": ...}}`. Error codes are `OUT_OF_AREA`, `UPSTREAM_UNAVAILABLE`, `RATE_LIMITED`, `SCHEMA_VIOLATION`, `DUPLICATE_REJECTED`. "No geocoding match" is a *successful empty result*, never an error.
- **The custom MCP server never imports an LLM client** and never receives free-form article text. If a task tempts you to pass `article_text` into a tool, the design has been broken.
- **Playwright MCP version** must be pinned to the exact version the instructor announced for the course, not `@latest`. Record the pinned version in `README.md`.
- **Ethics:** aggregate casualty figures only. No victim names, no personal data, anywhere in the database, fixtures, or logs.
- **Encoding:** all Ukrainian text is UTF-8. On Windows set `PYTHONUTF8=1` before running anything, and open every file with `encoding="utf-8"` explicitly.
- **Commit after every task.** Conventional commit prefixes (`feat:`, `test:`, `chore:`, `docs:`).

## File Structure

| File | Responsibility |
|---|---|
| `common/schemas.py` | Shared Pydantic types and enums crossing the MCP boundary; the tool-result envelope helpers |
| `mcp_server/__main__.py` | Entrypoint: `python -m mcp_server` |
| `mcp_server/server.py` | Thin MCP registration layer — schemas in, logic modules called, envelope out. No domain logic. |
| `mcp_server/db.py` | SQLite schema creation and low-level queries |
| `mcp_server/geo.py` | District polygon loading, point-in-polygon, Ukrainian address normalization, haversine |
| `mcp_server/nominatim.py` | Rate-limited cached geocoding client with fixture-replay transport |
| `mcp_server/credibility.py` | `assess_source_credibility` domain logic |
| `mcp_server/corroboration.py` | `corroborate_incident_claims` domain logic |
| `mcp_server/incidents_store.py` | `upsert_incident` domain logic: dedup, merge, provenance |
| `agent/__main__.py` | Entrypoint: `python -m agent "<query>"` |
| `agent/mcp_clients.py` | MCP connection setup for both servers, with initialization failure handling |
| `agent/nodes.py` | Individual graph node functions |
| `agent/graph.py` | Graph assembly and conditional edges |
| `agent/prompts/extract.md` | The extraction prompt |
| `data/kyiv_districts.geojson` | 10 Kyiv district polygons from OSM |
| `data/source_registry.yaml` | Hand-curated source tiers and independence groups |
| `fixtures/nominatim/` | Recorded genuine Nominatim responses |
| `fixtures/articles/` | Saved article HTML for defence insurance |

Logic modules are pure and testable without MCP. `server.py` stays thin so that protocol concerns never mix with domain rules — that separation is what makes Tasks 4–7 independently testable before any MCP wiring exists.

---

### Task 1: Project scaffold and shared schemas

**Files:**
- Create: `.gitignore`, `.env.example`, `requirements.txt`, `pytest.ini`, `README.md`
- Create: `common/__init__.py`, `common/schemas.py`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Consumes: nothing
- Produces: `KyivDistrict`, `DamageKind`, `StructureType`, `VerificationLevel`, `ClaimStatus`, `ErrorCode`, `IncidentClaim`, `SourceRef`, `ok(data) -> dict`, `err(code, message, retriable=False) -> dict`

- [ ] **Step 1: Create the repository skeleton**

```bash
cd /c/Users/faras/PycharmProjects/kyiv-strikes-agent
mkdir -p common mcp_server agent/prompts data fixtures/nominatim fixtures/articles tests docs
touch common/__init__.py mcp_server/__init__.py agent/__init__.py tests/__init__.py
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
.env
__pycache__/
*.pyc
.venv/
venv/
*.sqlite
*.sqlite-journal
.pytest_cache/
node_modules/
```

- [ ] **Step 3: Write `.env.example`**

```bash
# OpenRouter — used by the AGENT only. The MCP server must never read this.
OPENROUTER_API_KEY=sk-or-v1-REPLACE_ME
OPENROUTER_MODEL=anthropic/claude-sonnet-4.5

# Required by the Nominatim usage policy: a real contact address.
NOMINATIM_CONTACT=your.email@example.com

# Set to 1 to replay recorded Nominatim fixtures instead of calling the network.
KSM_OFFLINE=0

# Path to the incident database.
KSM_DB_PATH=incidents.sqlite
```

- [ ] **Step 4: Write `requirements.txt`**

```
mcp==1.29.0
pydantic==2.12.3
shapely==2.1.2
httpx==0.28.1
PyYAML==6.0.2
langgraph==1.2.11
langchain-core==1.6.0
langchain-openai==1.6.0
langchain-mcp-adapters==0.3.2
pytest==9.1.1
pytest-asyncio==1.3.0
```

- [ ] **Step 5: Write `pytest.ini`**

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
```

- [ ] **Step 6: Install dependencies**

```bash
export PYTHONUTF8=1
python -m pip install -r requirements.txt
```

Expected: successful install, no resolution errors.

- [ ] **Step 7: Write the failing test**

Create `tests/test_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from common.schemas import IncidentClaim, ok, err


def _valid_claim(**overrides):
    base = dict(
        source_url="https://suspilne.media/kyiv/123456",
        published_at="2026-06-17T09:30:00Z",
        occurred_at="2026-06-17T02:10:00Z",
        location_text="вул. Освіти, 4",
        claimed_district="Солом'янський",
        structure_type="residential",
        killed=3,
        injured=12,
        damage=["fire", "collapse"],
        media_urls=[],
        quote="Внаслідок влучання зруйновано під'їзд житлового будинку на вул. Освіти, 4.",
    )
    base.update(overrides)
    return base


def test_valid_claim_parses():
    claim = IncidentClaim(**_valid_claim())
    assert claim.killed == 3
    assert "fire" in claim.damage


def test_unknown_district_rejected():
    with pytest.raises(ValidationError):
        IncidentClaim(**_valid_claim(claimed_district="Одеський"))


def test_negative_casualties_rejected():
    with pytest.raises(ValidationError):
        IncidentClaim(**_valid_claim(killed=-1))


def test_quote_is_required():
    payload = _valid_claim()
    del payload["quote"]
    with pytest.raises(ValidationError):
        IncidentClaim(**payload)


def test_ok_envelope():
    assert ok({"matches": []}) == {"ok": True, "data": {"matches": []}}


def test_err_envelope():
    result = err("OUT_OF_AREA", "Point lies outside Kyiv.")
    assert result["ok"] is False
    assert result["error"]["code"] == "OUT_OF_AREA"
    assert result["error"]["retriable"] is False


def test_err_rejects_unknown_code():
    with pytest.raises(ValueError):
        err("BANANA", "nope")
```

- [ ] **Step 8: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_schemas.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'common.schemas'`

- [ ] **Step 9: Write `common/schemas.py`**

```python
"""Types that cross the MCP boundary. Imported by both the agent and the server."""

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, HttpUrl

KyivDistrict = Literal[
    "Голосіївський", "Дарницький", "Деснянський", "Дніпровський",
    "Оболонський", "Печерський", "Подільський", "Святошинський",
    "Солом'янський", "Шевченківський",
]

DamageKind = Literal["fire", "collapse", "facade", "crater", "vehicles"]

StructureType = Literal[
    "residential", "school", "hospital", "energy", "transport", "other"
]

VerificationLevel = Literal[
    "unverified", "single_source", "corroborated", "well_documented"
]

ClaimStatus = Literal["corroborated", "single_source", "conflicting", "superseded"]

ErrorCode = Literal[
    "OUT_OF_AREA",
    "UPSTREAM_UNAVAILABLE",
    "RATE_LIMITED",
    "SCHEMA_VIOLATION",
    "DUPLICATE_REJECTED",
]

RETRIABLE_CODES = frozenset({"UPSTREAM_UNAVAILABLE", "RATE_LIMITED"})


class IncidentClaim(BaseModel):
    """One article's claims about one impact location.

    Produced by the agent's extraction node, consumed by the custom MCP server.
    The server never sees the article text itself — only this.
    """

    source_url: HttpUrl
    published_at: datetime
    occurred_at: datetime | None = None
    location_text: str = Field(min_length=3, max_length=200)
    claimed_district: KyivDistrict | None = None
    structure_type: StructureType
    killed: int | None = Field(default=None, ge=0, le=1000)
    injured: int | None = Field(default=None, ge=0, le=5000)
    damage: list[DamageKind] = Field(default_factory=list)
    media_urls: list[HttpUrl] = Field(default_factory=list)
    quote: str = Field(min_length=10, max_length=1000)


class SourceRef(BaseModel):
    """A candidate article as handed to the credibility assessor."""

    url: HttpUrl
    domain: str
    published_at: datetime
    byline: str | None = None
    cited_sources: list[str] = Field(default_factory=list)


def ok(data: Any) -> dict:
    """Wrap a successful result. An empty payload is still a success."""
    return {"ok": True, "data": data}


def err(code: str, message: str, retriable: bool | None = None) -> dict:
    """Wrap a failure. Raises ValueError on an undeclared error code."""
    if code not in get_args(ErrorCode):
        raise ValueError(f"Undeclared error code: {code!r}")
    if retriable is None:
        retriable = code in RETRIABLE_CODES
    return {"ok": False, "error": {"code": code, "message": message, "retriable": retriable}}
```

- [ ] **Step 10: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_schemas.py -v
```

Expected: 7 passed.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: project scaffold and shared MCP boundary schemas"
```

---

### Task 2: Kyiv district polygons and the source registry

**Files:**
- Create: `data/kyiv_districts.geojson` (downloaded)
- Create: `data/source_registry.yaml`
- Create: `mcp_server/geo.py`
- Create: `scripts/fetch_districts.sh`
- Test: `tests/test_geo.py`

**Interfaces:**
- Consumes: `common.schemas.KyivDistrict`
- Produces: `load_districts(path) -> dict[str, BaseGeometry]`, `district_for_point(lat, lon, districts) -> str | None`, `normalize_address(text) -> str`, `haversine_m(lat1, lon1, lat2, lon2) -> float`

- [ ] **Step 1: Write the district fetch script**

Create `scripts/fetch_districts.sh`:

```bash
#!/usr/bin/env bash
# Fetches Kyiv's 10 administrative districts from OpenStreetMap via Overpass
# and converts the result to GeoJSON. Run once; the output is committed.
set -euo pipefail

OUT="data/kyiv_districts.geojson"
RAW="$(mktemp)"

curl -s -X POST https://overpass-api.de/api/interpreter \
  --data-urlencode 'data=
[out:json][timeout:120];
area["name"="Київ"]["admin_level"="4"]->.kyiv;
rel(area.kyiv)["boundary"="administrative"]["admin_level"="9"];
out geom;
' > "$RAW"

npx -y osmtogeojson "$RAW" > "$OUT"
rm -f "$RAW"

python - <<'PY'
import json
d = json.load(open("data/kyiv_districts.geojson", encoding="utf-8"))
names = sorted(f["properties"].get("name", "?") for f in d["features"])
print(f"{len(names)} features:")
for n in names:
    print(" ", n)
PY
```

- [ ] **Step 2: Run it and verify the output**

```bash
export PYTHONUTF8=1 && bash scripts/fetch_districts.sh
```

Expected: exactly 10 features, named Голосіївський, Дарницький, Деснянський, Дніпровський, Оболонський, Печерський, Подільський, Святошинський, Солом'янський, Шевченківський (each may carry the suffix "район").

**If the count is not 10:** OSM admin levels for Ukrainian city districts are not perfectly uniform. Re-run the query with `["admin_level"="8"]` and then `["admin_level"="10"]`, and keep whichever returns the 10 expected names. Record the level that worked in a comment at the top of the script.

- [ ] **Step 3: Write `data/source_registry.yaml`**

```yaml
# Hand-curated source registry. Tier assignments are editorial judgements and are
# documented as such in the design rationale.
#
# tier 1 = official / primary (the body that owns the facts)
# tier 2 = established newsroom producing original reporting
# tier 3 = aggregator / republisher — usable only via its primary-source link
#
# independence_group is the reconciliation key. Two articles sharing a group
# count as ONE source no matter how many outlets republished them.

sources:
  - domain: dsns.gov.ua
    tier: 1
    independence_group: dsns
    name: Державна служба з надзвичайних ситуацій
  - domain: kyivcity.gov.ua
    tier: 1
    independence_group: kmva
    name: Київська міська державна адміністрація
  - domain: npu.gov.ua
    tier: 1
    independence_group: police
    name: Національна поліція України
  - domain: gp.gov.ua
    tier: 1
    independence_group: prosecutor
    name: Офіс Генерального прокурора

  - domain: suspilne.media
    tier: 2
    independence_group: suspilne
    name: Суспільне Новини
  - domain: pravda.com.ua
    tier: 2
    independence_group: pravda
    name: Українська правда
  - domain: hromadske.ua
    tier: 2
    independence_group: hromadske
    name: hromadske
  - domain: reuters.com
    tier: 2
    independence_group: reuters
    name: Reuters
  - domain: apnews.com
    tier: 2
    independence_group: ap
    name: Associated Press
  - domain: bbc.com
    tier: 2
    independence_group: bbc
    name: BBC News Україна

  - domain: unian.ua
    tier: 3
    independence_group: unian
    name: УНІАН
  - domain: obozrevatel.com
    tier: 3
    independence_group: obozrevatel
    name: Обозреватель

# Domains that must never be used as evidence.
excluded_domains:
  - t.me
  - telegra.ph
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_geo.py`:

```python
import math

import pytest

from mcp_server.geo import (
    load_districts,
    district_for_point,
    normalize_address,
    haversine_m,
)

DISTRICTS = load_districts("data/kyiv_districts.geojson")

# Deep inside Голосіївський, far from any boundary (ВДНГ grounds).
HOLOSIIV = (50.3838, 30.4780)
# Deep inside Оболонський (Obolon residential area).
OBOLON = (50.5100, 30.4980)
# Odesa city centre — outside Kyiv entirely.
ODESA = (46.4825, 30.7233)


def test_all_ten_districts_load():
    assert len(DISTRICTS) == 10
    assert "Голосіївський" in DISTRICTS
    assert "Солом'янський" in DISTRICTS


def test_point_inside_holosiivskyi():
    assert district_for_point(*HOLOSIIV, DISTRICTS) == "Голосіївський"


def test_point_inside_obolonskyi():
    assert district_for_point(*OBOLON, DISTRICTS) == "Оболонський"


def test_point_outside_kyiv_returns_none():
    assert district_for_point(*ODESA, DISTRICTS) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("вул. Освіти, 4", "вулиця освіти 4"),
        ("ВУЛ.  ОСВІТИ 4", "вулиця освіти 4"),
        ("просп. Перемоги, буд. 37", "проспект перемоги 37"),
        ("пр-т Науки 10", "проспект науки 10"),
        ("Солом'янський р-н, вул. Освіти 4", "солом'янський район вулиця освіти 4"),
    ],
)
def test_normalize_address(raw, expected):
    assert normalize_address(raw) == expected


def test_haversine_known_distance():
    # Roughly 1 km apart along a meridian.
    d = haversine_m(50.4500, 30.5230, 50.4590, 30.5230)
    assert math.isclose(d, 1000, rel_tol=0.02)


def test_haversine_zero():
    assert haversine_m(50.45, 30.52, 50.45, 30.52) == pytest.approx(0.0, abs=1e-6)
```

- [ ] **Step 5: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_geo.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_server.geo'`

- [ ] **Step 6: Write `mcp_server/geo.py`**

```python
"""Geometry and Ukrainian address handling. No network access lives here."""

import json
import math
import re
from pathlib import Path

from shapely.geometry import shape, Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

EARTH_RADIUS_M = 6_371_000.0

# Longest forms first so that "пр-т" is not partially eaten by "пр".
_STREET_ABBREVIATIONS = [
    (r"\bпроспект\b|\bпросп\.?|\bпр-т\b|\bпр-кт\b", "проспект"),
    (r"\bвулиця\b|\bвул\.?", "вулиця"),
    (r"\bбульвар\b|\bбул\.?|\bб-р\b", "бульвар"),
    (r"\bпровулок\b|\bпров\.?", "провулок"),
    (r"\bплоща\b|\bпл\.?", "площа"),
    (r"\bрайон\b|\bр-н\b", "район"),
    (r"\bбудинок\b|\bбуд\.?|\bб\.", ""),
]


def load_districts(path: str | Path) -> dict[str, BaseGeometry]:
    """Load Kyiv district polygons keyed by bare district name.

    The OSM name may carry a "район" suffix; it is stripped so the keys match
    the KyivDistrict literal in common.schemas.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    districts: dict[str, BaseGeometry] = {}
    for feature in data["features"]:
        name = feature["properties"].get("name", "")
        name = re.sub(r"\s*район\s*$", "", name).strip()
        if not name:
            continue
        districts[name] = prep(shape(feature["geometry"]))
    return districts


def district_for_point(
    lat: float, lon: float, districts: dict[str, BaseGeometry]
) -> str | None:
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
    result = text.lower().strip()
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
```

- [ ] **Step 7: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_geo.py -v
```

Expected: all passed. If `test_point_inside_*` fails, the polygon set is wrong — revisit Step 2 before continuing, because every later task depends on it.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: Kyiv district polygons, source registry, geo primitives"
```

---

### Task 3: Rate-limited Nominatim client with fixture replay

**Files:**
- Create: `mcp_server/nominatim.py`
- Create: `scripts/record_fixture.py`
- Test: `tests/test_nominatim.py`

**Interfaces:**
- Consumes: `mcp_server.geo.normalize_address`
- Produces: `NominatimClient(contact: str, offline: bool, fixtures_dir: Path, min_interval_s: float = 1.0)`, method `search(normalized_query: str) -> list[dict]` returning raw Nominatim result dicts, and `fixture_key(normalized_query) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_nominatim.py`:

```python
import json
import time
from pathlib import Path

import pytest

from mcp_server.nominatim import NominatimClient, fixture_key


@pytest.fixture
def fixtures(tmp_path: Path) -> Path:
    d = tmp_path / "nominatim"
    d.mkdir()
    query = "вулиця освіти 4 київ"
    payload = [
        {
            "lat": "50.4310000",
            "lon": "30.4400000",
            "display_name": "4, вулиця Освіти, Солом'янський район, Київ",
            "importance": 0.42,
        }
    ]
    (d / f"{fixture_key(query)}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return d


def test_offline_returns_recorded_fixture(fixtures):
    client = NominatimClient(contact="test@example.com", offline=True, fixtures_dir=fixtures)
    results = client.search("вулиця освіти 4 київ")
    assert len(results) == 1
    assert results[0]["lat"] == "50.4310000"


def test_offline_missing_fixture_returns_empty_not_error(fixtures):
    client = NominatimClient(contact="test@example.com", offline=True, fixtures_dir=fixtures)
    assert client.search("вулиця неіснуюча 999 київ") == []


def test_fixture_key_is_stable():
    assert fixture_key("вулиця освіти 4") == fixture_key("вулиця освіти 4")
    assert fixture_key("вулиця освіти 4") != fixture_key("вулиця освіти 5")


def test_rate_limiter_spaces_calls(fixtures, monkeypatch):
    client = NominatimClient(
        contact="test@example.com",
        offline=True,
        fixtures_dir=fixtures,
        min_interval_s=0.25,
    )
    slept: list[float] = []
    monkeypatch.setattr("mcp_server.nominatim.time.sleep", lambda s: slept.append(s))
    monkeypatch.setattr("mcp_server.nominatim.time.monotonic", lambda: 0.0)

    client.search("вулиця освіти 4 київ")
    client.search("вулиця освіти 4 київ")

    assert slept and slept[0] == pytest.approx(0.25, abs=0.01)


def test_contact_is_required():
    with pytest.raises(ValueError):
        NominatimClient(contact="", offline=True, fixtures_dir=Path("."))
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_nominatim.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_server.nominatim'`

- [ ] **Step 3: Write `mcp_server/nominatim.py`**

```python
"""Nominatim access. The only networked component of the custom server.

Offline mode swaps the transport only: the caller's query normalization and the
downstream parsing, validation, and point-in-polygon logic are identical in both
modes, as the assignment's fixture rules require.
"""

import hashlib
import json
import time
from pathlib import Path

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Bounding box around Kyiv: left, top, right, bottom.
KYIV_VIEWBOX = "30.185,50.590,30.825,50.213"


def fixture_key(normalized_query: str) -> str:
    """Stable filename-safe key for a normalized query."""
    return hashlib.sha1(normalized_query.encode("utf-8")).hexdigest()


class UpstreamUnavailable(RuntimeError):
    """Nominatim could not be reached or returned an unusable response."""


class RateLimited(RuntimeError):
    """Nominatim signalled that we are querying too fast."""


class NominatimClient:
    def __init__(
        self,
        contact: str,
        offline: bool,
        fixtures_dir: Path,
        min_interval_s: float = 1.0,
    ) -> None:
        if not contact:
            raise ValueError(
                "NOMINATIM_CONTACT is required: the Nominatim usage policy "
                "demands a real contact address in the User-Agent."
            )
        self.contact = contact
        self.offline = offline
        self.fixtures_dir = Path(fixtures_dir)
        self.min_interval_s = min_interval_s
        self._last_call_at: float | None = None

    def _throttle(self) -> None:
        if self._last_call_at is None:
            self._last_call_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_call_at
        remaining = self.min_interval_s - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_call_at = time.monotonic()

    def search(self, normalized_query: str) -> list[dict]:
        """Return raw Nominatim results. An empty list is a valid answer."""
        self._throttle()
        if self.offline:
            return self._read_fixture(normalized_query)
        return self._fetch(normalized_query)

    def _read_fixture(self, normalized_query: str) -> list[dict]:
        path = self.fixtures_dir / f"{fixture_key(normalized_query)}.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _fetch(self, normalized_query: str) -> list[dict]:
        headers = {"User-Agent": f"kyiv-strikes-mcp/1.0 ({self.contact})"}
        params = {
            "q": normalized_query,
            "format": "jsonv2",
            "limit": 5,
            "viewbox": KYIV_VIEWBOX,
            "bounded": 1,
            "addressdetails": 1,
        }
        try:
            response = httpx.get(
                NOMINATIM_URL, params=params, headers=headers, timeout=15.0
            )
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable(str(exc)) from exc

        if response.status_code == 429:
            raise RateLimited("Nominatim returned HTTP 429.")
        if response.status_code >= 400:
            raise UpstreamUnavailable(f"Nominatim returned HTTP {response.status_code}.")

        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamUnavailable("Nominatim returned a non-JSON body.") from exc
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_nominatim.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Write the fixture recorder**

Create `scripts/record_fixture.py`:

```python
"""Record a genuine Nominatim response as a replay fixture.

Usage:
    python scripts/record_fixture.py "вул. Освіти, 4, Київ"

Recorded responses are real. Never hand-edit them: an edited fixture is a
hard-coded answer, which the assignment explicitly disallows.
"""

import json
import os
import sys
from pathlib import Path

from mcp_server.geo import normalize_address
from mcp_server.nominatim import NominatimClient, fixture_key

FIXTURES = Path("fixtures/nominatim")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    contact = os.environ.get("NOMINATIM_CONTACT", "")
    client = NominatimClient(contact=contact, offline=False, fixtures_dir=FIXTURES)

    raw = sys.argv[1]
    query = normalize_address(raw)
    results = client.search(query)

    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f"{fixture_key(query)}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"query      : {query}")
    print(f"results    : {len(results)}")
    print(f"written to : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Record the demo fixtures**

```bash
export PYTHONUTF8=1 NOMINATIM_CONTACT="your.email@example.com"
python scripts/record_fixture.py "вул. Освіти, 4, Київ"
python scripts/record_fixture.py "просп. Перемоги, 37, Київ"
python scripts/record_fixture.py "вул. Дерибасівська, 1, Одеса"
```

Expected: three files written under `fixtures/nominatim/`. Wait at least one second between commands — the client enforces this, but do not run them in a tight loop from a script.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: rate-limited Nominatim client with fixture replay"
```

---

### Task 4: `resolve_strike_location` domain logic

**Files:**
- Create: `mcp_server/locate.py`
- Test: `tests/test_locate.py`

**Interfaces:**
- Consumes: `mcp_server.geo.normalize_address`, `district_for_point`, `load_districts`; `mcp_server.nominatim.NominatimClient`
- Produces: `resolve_location(location_text, claimed_district, client, districts) -> dict` — the raw payload later wrapped by `ok()`/`err()` in `server.py`. Shape on success: `{"lat", "lon", "district", "confidence", "district_conflict", "matches", "candidates"}`. Raises `OutOfArea` when a match resolves outside Kyiv.

- [ ] **Step 1: Write the failing test**

Create `tests/test_locate.py`:

```python
import json
from pathlib import Path

import pytest

from mcp_server.geo import load_districts, normalize_address
from mcp_server.locate import OutOfArea, resolve_location
from mcp_server.nominatim import NominatimClient, fixture_key

DISTRICTS = load_districts("data/kyiv_districts.geojson")


def _write(dirpath: Path, raw_query: str, payload: list[dict]) -> None:
    key = fixture_key(normalize_address(raw_query))
    (dirpath / f"{key}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def client(tmp_path: Path) -> NominatimClient:
    d = tmp_path / "nominatim"
    d.mkdir()
    # A point deep inside Голосіївський.
    _write(d, "вул. Тестова, 1", [
        {"lat": "50.3838", "lon": "30.4780",
         "display_name": "1, вулиця Тестова, Київ", "importance": 0.55},
    ])
    # Two plausible matches — ambiguous.
    _write(d, "вул. Садова, 5", [
        {"lat": "50.3838", "lon": "30.4780", "display_name": "A", "importance": 0.31},
        {"lat": "50.5100", "lon": "30.4980", "display_name": "B", "importance": 0.29},
    ])
    # Odesa — outside Kyiv.
    _write(d, "вул. Дерибасівська, 1, Одеса", [
        {"lat": "46.4825", "lon": "30.7233",
         "display_name": "Дерибасівська, Одеса", "importance": 0.60},
    ])
    return NominatimClient(
        contact="test@example.com", offline=True, fixtures_dir=d, min_interval_s=0.0
    )


def test_resolves_and_assigns_geometric_district(client):
    result = resolve_location("вул. Тестова, 1", None, client, DISTRICTS)
    assert result["district"] == "Голосіївський"
    assert result["lat"] == pytest.approx(50.3838)
    assert result["district_conflict"] is False


def test_district_conflict_flagged_when_claim_disagrees(client):
    result = resolve_location("вул. Тестова, 1", "Оболонський", client, DISTRICTS)
    assert result["district"] == "Голосіївський"      # geometry wins
    assert result["district_conflict"] is True


def test_no_conflict_when_claim_agrees(client):
    result = resolve_location("вул. Тестова, 1", "Голосіївський", client, DISTRICTS)
    assert result["district_conflict"] is False


def test_no_match_is_empty_success_not_error(client):
    result = resolve_location("вул. Невідома, 999", None, client, DISTRICTS)
    assert result["matches"] == []
    assert result["lat"] is None
    assert result["district"] is None


def test_ambiguous_match_lowers_confidence_and_lists_candidates(client):
    result = resolve_location("вул. Садова, 5", None, client, DISTRICTS)
    assert len(result["candidates"]) == 2
    assert result["confidence"] < 0.5


def test_point_outside_kyiv_raises_out_of_area(client):
    with pytest.raises(OutOfArea):
        resolve_location("вул. Дерибасівська, 1, Одеса", None, client, DISTRICTS)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_locate.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_server.locate'`

- [ ] **Step 3: Write `mcp_server/locate.py`**

```python
"""Turn a free-text Ukrainian location into a district-validated coordinate."""

from shapely.geometry.base import BaseGeometry

from mcp_server.geo import district_for_point, normalize_address
from mcp_server.nominatim import NominatimClient

# Below this separation between the top two matches, the answer is ambiguous.
_AMBIGUITY_MARGIN = 0.10


class OutOfArea(ValueError):
    """The best match resolved to a point outside every Kyiv district."""


def resolve_location(
    location_text: str,
    claimed_district: str | None,
    client: NominatimClient,
    districts: dict[str, BaseGeometry],
) -> dict:
    """Geocode a location and validate it against Kyiv district geometry.

    An empty result is a successful empty answer, not a failure. A match that
    lands outside Kyiv is a failure, because it means the caller asked about the
    wrong city.
    """
    query = normalize_address(location_text)
    matches = client.search(query)

    if not matches:
        return {
            "lat": None,
            "lon": None,
            "district": None,
            "confidence": 0.0,
            "district_conflict": False,
            "matches": [],
            "candidates": [],
        }

    ranked = sorted(matches, key=lambda m: float(m.get("importance", 0.0)), reverse=True)
    best = ranked[0]
    lat, lon = float(best["lat"]), float(best["lon"])

    district = district_for_point(lat, lon, districts)
    if district is None:
        raise OutOfArea(
            f"Best match for {location_text!r} resolved to ({lat}, {lon}), "
            "which lies outside every Kyiv district."
        )

    confidence = float(best.get("importance", 0.0))
    candidates: list[dict] = []
    if len(ranked) > 1:
        runner_up = float(ranked[1].get("importance", 0.0))
        if confidence - runner_up < _AMBIGUITY_MARGIN:
            confidence = min(confidence, 0.45)
            candidates = [
                {"lat": float(m["lat"]), "lon": float(m["lon"]),
                 "display_name": m.get("display_name", "")}
                for m in ranked[:3]
            ]

    conflict = claimed_district is not None and claimed_district != district

    return {
        "lat": lat,
        "lon": lon,
        "district": district,
        "confidence": round(confidence, 3),
        "district_conflict": conflict,
        "matches": [m.get("display_name", "") for m in ranked],
        "candidates": candidates,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_locate.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: resolve_strike_location domain logic with district validation"
```

---

### Task 5: `assess_source_credibility` domain logic

**Files:**
- Create: `mcp_server/credibility.py`
- Test: `tests/test_credibility.py`

**Interfaces:**
- Consumes: `common.schemas.SourceRef`
- Produces: `load_registry(path) -> Registry`, `assess_sources(sources: list[SourceRef], registry: Registry, event_date: date) -> list[dict]` with each item `{"url", "tier", "independence_group", "usable_as_corroborator", "reason"}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_credibility.py`:

```python
from datetime import date

import pytest

from common.schemas import SourceRef
from mcp_server.credibility import assess_sources, load_registry

REGISTRY = load_registry("data/source_registry.yaml")
EVENT_DATE = date(2026, 6, 17)


def _src(domain, url, published, cited=None):
    return SourceRef(
        url=url, domain=domain, published_at=published, cited_sources=cited or []
    )


def test_tier1_official_is_usable():
    result = assess_sources(
        [_src("dsns.gov.ua", "https://dsns.gov.ua/news/1", "2026-06-17T08:00:00Z")],
        REGISTRY, EVENT_DATE,
    )
    assert result[0]["tier"] == 1
    assert result[0]["independence_group"] == "dsns"
    assert result[0]["usable_as_corroborator"] is True


def test_tier2_original_reporting_is_usable():
    result = assess_sources(
        [_src("suspilne.media", "https://suspilne.media/kyiv/1", "2026-06-17T09:00:00Z")],
        REGISTRY, EVENT_DATE,
    )
    assert result[0]["tier"] == 2
    assert result[0]["independence_group"] == "suspilne"
    assert result[0]["usable_as_corroborator"] is True


def test_tier2_that_only_cites_dsns_inherits_dsns_group():
    """The independence rule: republishing an official statement is not
    independent corroboration, however reputable the outlet."""
    result = assess_sources(
        [_src("suspilne.media", "https://suspilne.media/kyiv/2",
              "2026-06-17T09:00:00Z", cited=["dsns.gov.ua"])],
        REGISTRY, EVENT_DATE,
    )
    assert result[0]["independence_group"] == "dsns"


def test_tier3_without_primary_link_is_not_usable():
    result = assess_sources(
        [_src("unian.ua", "https://unian.ua/1", "2026-06-17T10:00:00Z")],
        REGISTRY, EVENT_DATE,
    )
    assert result[0]["tier"] == 3
    assert result[0]["usable_as_corroborator"] is False


def test_tier3_with_primary_link_inherits_that_group():
    result = assess_sources(
        [_src("unian.ua", "https://unian.ua/2", "2026-06-17T10:00:00Z",
              cited=["kyivcity.gov.ua"])],
        REGISTRY, EVENT_DATE,
    )
    assert result[0]["independence_group"] == "kmva"
    assert result[0]["usable_as_corroborator"] is True


def test_excluded_domain_is_never_usable():
    result = assess_sources(
        [_src("t.me", "https://t.me/somechannel/99", "2026-06-17T03:00:00Z")],
        REGISTRY, EVENT_DATE,
    )
    assert result[0]["usable_as_corroborator"] is False
    assert "excluded" in result[0]["reason"].lower()


def test_unknown_domain_is_not_usable():
    result = assess_sources(
        [_src("randomblog.example", "https://randomblog.example/1",
              "2026-06-17T10:00:00Z")],
        REGISTRY, EVENT_DATE,
    )
    assert result[0]["usable_as_corroborator"] is False


def test_article_published_before_the_event_is_not_usable():
    result = assess_sources(
        [_src("suspilne.media", "https://suspilne.media/kyiv/3",
              "2026-06-10T09:00:00Z")],
        REGISTRY, EVENT_DATE,
    )
    assert result[0]["usable_as_corroborator"] is False
    assert "window" in result[0]["reason"].lower()


def test_article_published_more_than_14_days_later_is_not_usable():
    result = assess_sources(
        [_src("suspilne.media", "https://suspilne.media/kyiv/4",
              "2026-07-20T09:00:00Z")],
        REGISTRY, EVENT_DATE,
    )
    assert result[0]["usable_as_corroborator"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_credibility.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_server.credibility'`

- [ ] **Step 3: Write `mcp_server/credibility.py`**

```python
"""Source tiering and — the part that matters — independence grouping.

Without the independence rule, "five outlets confirmed it" is meaningless when
all five reprinted one press release.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from common.schemas import SourceRef

RELEVANCE_WINDOW = timedelta(days=14)


@dataclass(frozen=True)
class SourceEntry:
    domain: str
    tier: int
    independence_group: str
    name: str


@dataclass(frozen=True)
class Registry:
    entries: dict[str, SourceEntry]
    excluded_domains: frozenset[str]


def load_registry(path: str | Path) -> Registry:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    entries = {
        item["domain"]: SourceEntry(
            domain=item["domain"],
            tier=int(item["tier"]),
            independence_group=item["independence_group"],
            name=item.get("name", item["domain"]),
        )
        for item in raw.get("sources", [])
    }
    return Registry(
        entries=entries,
        excluded_domains=frozenset(raw.get("excluded_domains", [])),
    )


def _resolve_group(source: SourceRef, entry: SourceEntry, registry: Registry) -> str:
    """A source that only relays another body's statement belongs to that body's
    group, not its own. Tier 3 has no independent voice at all."""
    cited_groups = {
        registry.entries[domain].independence_group
        for domain in source.cited_sources
        if domain in registry.entries
    }
    if len(cited_groups) == 1:
        return next(iter(cited_groups))
    return entry.independence_group


def assess_sources(
    sources: list[SourceRef], registry: Registry, event_date: date
) -> list[dict]:
    results: list[dict] = []
    for source in sources:
        url = str(source.url)
        published = source.published_at.date()

        if source.domain in registry.excluded_domains:
            results.append({
                "url": url, "tier": None, "independence_group": None,
                "usable_as_corroborator": False,
                "reason": f"Domain {source.domain} is on the excluded list.",
            })
            continue

        entry = registry.entries.get(source.domain)
        if entry is None:
            results.append({
                "url": url, "tier": None, "independence_group": None,
                "usable_as_corroborator": False,
                "reason": f"Domain {source.domain} is not in the source registry.",
            })
            continue

        if not (event_date <= published <= event_date + RELEVANCE_WINDOW):
            results.append({
                "url": url, "tier": entry.tier,
                "independence_group": entry.independence_group,
                "usable_as_corroborator": False,
                "reason": (
                    f"Published {published.isoformat()}, outside the relevance "
                    f"window {event_date.isoformat()} +14d."
                ),
            })
            continue

        group = _resolve_group(source, entry, registry)
        relays_primary = group != entry.independence_group

        if entry.tier == 3 and not relays_primary:
            usable = False
            reason = "Tier 3 aggregator with no identified primary source."
        else:
            usable = True
            reason = (
                f"Tier {entry.tier}; relays {group}." if relays_primary
                else f"Tier {entry.tier} original reporting."
            )

        results.append({
            "url": url, "tier": entry.tier, "independence_group": group,
            "usable_as_corroborator": usable, "reason": reason,
        })

    return results
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_credibility.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: source credibility tiering and independence grouping"
```

---

### Task 6: `corroborate_incident_claims` domain logic

**Files:**
- Create: `mcp_server/corroboration.py`
- Test: `tests/test_corroboration.py`

**Interfaces:**
- Consumes: `common.schemas.IncidentClaim`, `VerificationLevel`, `ClaimStatus`; the assessment dicts produced by `assess_sources`
- Produces: `corroborate(claims: list[IncidentClaim], assessments: list[dict]) -> dict` shaped `{"fields": {name: {"value", "status", "supporting_sources"}}, "verification_level", "missing_for_next_level", "usable_groups"}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_corroboration.py`:

```python
import pytest

from common.schemas import IncidentClaim
from mcp_server.corroboration import corroborate


def claim(url, published, killed=None, injured=None, structure="residential"):
    return IncidentClaim(
        source_url=url,
        published_at=published,
        occurred_at="2026-06-17T02:10:00Z",
        location_text="вул. Освіти, 4",
        claimed_district="Солом'янський",
        structure_type=structure,
        killed=killed,
        injured=injured,
        damage=["fire"],
        quote="Зруйновано під'їзд житлового будинку на вул. Освіти, 4.",
    )


def assessment(url, group, tier=2, usable=True):
    return {
        "url": url, "tier": tier, "independence_group": group,
        "usable_as_corroborator": usable, "reason": "test",
    }


DSNS = "https://dsns.gov.ua/news/1"
SUSP = "https://suspilne.media/kyiv/1"
PRAV = "https://pravda.com.ua/news/1"


def test_two_articles_from_one_group_are_single_source():
    claims = [
        claim(DSNS, "2026-06-17T08:00:00Z", killed=3),
        claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
    ]
    assessments = [
        assessment(DSNS, "dsns", tier=1),
        assessment(SUSP, "dsns", tier=2),   # relays DSNS
    ]
    result = corroborate(claims, assessments)
    assert result["fields"]["killed"]["status"] == "single_source"
    assert result["verification_level"] == "single_source"


def test_two_independent_groups_agreeing_are_corroborated():
    claims = [
        claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
        claim(PRAV, "2026-06-17T09:30:00Z", killed=3),
    ]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    assert result["fields"]["killed"]["value"] == 3
    assert result["fields"]["killed"]["status"] == "corroborated"
    assert result["verification_level"] == "corroborated"


def test_later_higher_count_supersedes_rather_than_conflicts():
    claims = [
        claim(SUSP, "2026-06-17T06:00:00Z", killed=3),
        claim(PRAV, "2026-06-17T18:00:00Z", killed=5),
    ]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    assert result["fields"]["killed"]["value"] == 5
    assert result["fields"]["killed"]["status"] == "superseded"


def test_same_hour_disagreement_is_conflicting():
    claims = [
        claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
        claim(PRAV, "2026-06-17T09:20:00Z", killed=7),
    ]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    assert result["fields"]["killed"]["status"] == "conflicting"


def test_tier1_plus_independent_tier2_is_well_documented():
    claims = [
        claim(DSNS, "2026-06-17T08:00:00Z", killed=3),
        claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
    ]
    assessments = [
        assessment(DSNS, "dsns", tier=1),
        assessment(SUSP, "suspilne", tier=2),
    ]
    result = corroborate(claims, assessments)
    assert result["verification_level"] == "well_documented"


def test_no_usable_sources_is_unverified():
    claims = [claim(DSNS, "2026-06-17T08:00:00Z", killed=3)]
    assessments = [assessment(DSNS, "dsns", tier=1, usable=False)]
    result = corroborate(claims, assessments)
    assert result["verification_level"] == "unverified"


def test_single_source_names_what_is_missing():
    claims = [claim(SUSP, "2026-06-17T09:00:00Z", killed=3)]
    assessments = [assessment(SUSP, "suspilne")]
    result = corroborate(claims, assessments)
    assert result["verification_level"] == "single_source"
    missing = " ".join(result["missing_for_next_level"]).lower()
    assert "suspilne" in missing          # names the group to avoid
    assert "independent" in missing


def test_corroborated_still_asks_for_an_official_source():
    claims = [
        claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
        claim(PRAV, "2026-06-17T09:30:00Z", killed=3),
    ]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    missing = " ".join(result["missing_for_next_level"]).lower()
    assert "tier 1" in missing or "official" in missing


def test_well_documented_needs_nothing_further():
    claims = [
        claim(DSNS, "2026-06-17T08:00:00Z", killed=3),
        claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
    ]
    assessments = [
        assessment(DSNS, "dsns", tier=1),
        assessment(SUSP, "suspilne", tier=2),
    ]
    result = corroborate(claims, assessments)
    assert result["missing_for_next_level"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_corroboration.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_server.corroboration'`

- [ ] **Step 3: Write `mcp_server/corroboration.py`**

```python
"""Reconcile claims from several articles into per-field verified values.

This module is the feedback engine: `missing_for_next_level` is what turns the
graph from a pipeline into an agent.
"""

from datetime import timedelta

from common.schemas import IncidentClaim

# Reports published within this window of each other describe the same moment,
# so a numeric disagreement between them is a genuine conflict rather than an
# update.
SIMULTANEITY_WINDOW = timedelta(hours=2)

MONOTONIC_FIELDS = ("killed", "injured")
CATEGORICAL_FIELDS = ("structure_type",)


def _usable_pairs(
    claims: list[IncidentClaim], assessments: list[dict]
) -> list[tuple[IncidentClaim, dict]]:
    by_url = {a["url"]: a for a in assessments}
    pairs = []
    for c in claims:
        a = by_url.get(str(c.source_url))
        if a and a.get("usable_as_corroborator"):
            pairs.append((c, a))
    return pairs


def _reconcile_monotonic(field: str, pairs) -> dict | None:
    entries = [
        (c.published_at, getattr(c, field), a["independence_group"], str(c.source_url))
        for c, a in pairs
        if getattr(c, field) is not None
    ]
    if not entries:
        return None

    entries.sort(key=lambda e: e[0])
    latest_at, latest_value, _, _ = entries[-1]
    groups = {e[2] for e in entries}

    simultaneous_values = {
        e[1] for e in entries if abs(e[0] - latest_at) <= SIMULTANEITY_WINDOW
    }
    distinct_all = {e[1] for e in entries}

    if len(simultaneous_values) > 1:
        status = "conflicting"
    elif len(distinct_all) > 1:
        status = "superseded"
    elif len(groups) > 1:
        status = "corroborated"
    else:
        status = "single_source"

    return {
        "value": latest_value,
        "status": status,
        "supporting_sources": [e[3] for e in entries],
    }


def _reconcile_categorical(field: str, pairs) -> dict | None:
    entries = [
        (getattr(c, field), a["independence_group"], str(c.source_url))
        for c, a in pairs
        if getattr(c, field) is not None
    ]
    if not entries:
        return None

    values = {e[0] for e in entries}
    groups = {e[1] for e in entries}

    if len(values) > 1:
        status = "conflicting"
    elif len(groups) > 1:
        status = "corroborated"
    else:
        status = "single_source"

    return {
        "value": entries[0][0],
        "status": status,
        "supporting_sources": [e[2] for e in entries],
    }


def corroborate(claims: list[IncidentClaim], assessments: list[dict]) -> dict:
    pairs = _usable_pairs(claims, assessments)
    groups = {a["independence_group"] for _, a in pairs}
    has_tier1 = any(a.get("tier") == 1 for _, a in pairs)

    fields: dict[str, dict] = {}
    for field in MONOTONIC_FIELDS:
        reconciled = _reconcile_monotonic(field, pairs)
        if reconciled:
            fields[field] = reconciled
    for field in CATEGORICAL_FIELDS:
        reconciled = _reconcile_categorical(field, pairs)
        if reconciled:
            fields[field] = reconciled

    if not groups:
        level = "unverified"
    elif len(groups) == 1:
        level = "single_source"
    elif has_tier1:
        level = "well_documented"
    else:
        level = "corroborated"

    missing: list[str] = []
    if level == "unverified":
        missing.append("Any source that passes the credibility check.")
    elif level == "single_source":
        seen = ", ".join(sorted(groups))
        missing.append(
            f"Independent confirmation from a source outside group(s): {seen}."
        )
        missing.append("An official (tier 1) source such as ДСНС, КМВА, or поліція.")
    elif level == "corroborated":
        missing.append("An official (tier 1) source to reach well_documented.")

    conflicting = [name for name, f in fields.items() if f["status"] == "conflicting"]
    if conflicting:
        missing.append(
            "A source resolving the conflicting field(s): " + ", ".join(conflicting) + "."
        )

    return {
        "fields": fields,
        "verification_level": level,
        "missing_for_next_level": missing,
        "usable_groups": sorted(groups),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_corroboration.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: cross-source claim reconciliation and verification levels"
```

---

### Task 7: Database schema and `upsert_incident` domain logic

**Files:**
- Create: `mcp_server/db.py`
- Create: `mcp_server/incidents_store.py`
- Test: `tests/test_incidents_store.py`

**Interfaces:**
- Consumes: `mcp_server.geo.haversine_m`; `common.schemas.IncidentClaim`
- Produces: `connect(path) -> sqlite3.Connection` (schema created on first call), `upsert_incident(conn, incident: dict, claims: list[IncidentClaim], assessments: list[dict], reconciliation: dict) -> dict` returning `{"incident_id", "action", "changed_fields"}`, and `to_geojson(conn) -> dict`

The `incident` argument is shaped `{"occurred_at", "lat", "lon", "district", "address_text", "structure_type", "killed", "injured", "damage", "verification_level", "geocode_confidence"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_incidents_store.py`:

```python
import pytest

from common.schemas import IncidentClaim
from mcp_server.db import connect
from mcp_server.incidents_store import to_geojson, upsert_incident

LEVELS = ["unverified", "single_source", "corroborated", "well_documented"]


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "test.sqlite")


def make_incident(lat=50.4310, lon=30.4400, occurred="2026-06-17T02:10:00Z",
                  killed=3, level="corroborated"):
    return {
        "occurred_at": occurred,
        "lat": lat, "lon": lon,
        "district": "Солом'янський",
        "address_text": "вул. Освіти, 4",
        "structure_type": "residential",
        "killed": killed, "injured": 12,
        "damage": ["fire", "collapse"],
        "verification_level": level,
        "geocode_confidence": 0.55,
    }


def make_claim(url="https://suspilne.media/kyiv/1", killed=3):
    return IncidentClaim(
        source_url=url,
        published_at="2026-06-17T09:00:00Z",
        occurred_at="2026-06-17T02:10:00Z",
        location_text="вул. Освіти, 4",
        claimed_district="Солом'янський",
        structure_type="residential",
        killed=killed, injured=12,
        damage=["fire"],
        media_urls=["https://suspilne.media/img/1.jpg"],
        quote="Загинули троє людей, дванадцятеро дістали поранення.",
    )


def make_assessment(url="https://suspilne.media/kyiv/1", group="suspilne", tier=2):
    return {"url": url, "tier": tier, "independence_group": group,
            "usable_as_corroborator": True, "reason": "test"}


def make_reconciliation(killed=3, status="corroborated"):
    return {
        "fields": {
            "killed": {"value": killed, "status": status,
                       "supporting_sources": ["https://suspilne.media/kyiv/1"]},
        },
        "verification_level": "corroborated",
        "missing_for_next_level": [],
        "usable_groups": ["suspilne"],
    }


def test_first_write_creates(conn):
    result = upsert_incident(conn, make_incident(), [make_claim()],
                             [make_assessment()], make_reconciliation())
    assert result["action"] == "created"
    assert result["incident_id"] > 0


def test_nearby_and_recent_merges(conn):
    upsert_incident(conn, make_incident(), [make_claim()],
                    [make_assessment()], make_reconciliation())
    # ~50 m north, 2 hours later, better verification.
    nearby = make_incident(lat=50.4315, occurred="2026-06-17T04:10:00Z",
                           killed=5, level="well_documented")
    result = upsert_incident(conn, nearby, [make_claim(killed=5)],
                             [make_assessment()], make_reconciliation(killed=5))
    assert result["action"] == "merged"
    assert "killed" in result["changed_fields"]


def test_far_away_creates_separate_incident(conn):
    upsert_incident(conn, make_incident(), [make_claim()],
                    [make_assessment()], make_reconciliation())
    far = make_incident(lat=50.4600)          # ~3 km away
    result = upsert_incident(conn, far, [make_claim()],
                             [make_assessment()], make_reconciliation())
    assert result["action"] == "created"


def test_same_place_but_days_later_creates_separate_incident(conn):
    upsert_incident(conn, make_incident(), [make_claim()],
                    [make_assessment()], make_reconciliation())
    later = make_incident(occurred="2026-06-25T02:10:00Z")
    result = upsert_incident(conn, later, [make_claim()],
                             [make_assessment()], make_reconciliation())
    assert result["action"] == "created"


def test_merge_refuses_to_downgrade_verification(conn):
    upsert_incident(conn, make_incident(level="well_documented"), [make_claim()],
                    [make_assessment()], make_reconciliation())
    weaker = make_incident(lat=50.4315, level="single_source", killed=99)
    result = upsert_incident(conn, weaker, [make_claim(killed=99)],
                             [make_assessment()], make_reconciliation(killed=99))
    assert result["action"] == "rejected_lower_confidence"
    row = conn.execute("SELECT killed, verification_level FROM incidents").fetchone()
    assert row["killed"] == 3
    assert row["verification_level"] == "well_documented"


def test_provenance_row_carries_the_quote(conn):
    upsert_incident(conn, make_incident(), [make_claim()],
                    [make_assessment()], make_reconciliation())
    row = conn.execute(
        "SELECT value, quote, status FROM incident_claims WHERE field = 'killed'"
    ).fetchone()
    assert row["value"] == "3"
    assert "Загинули троє" in row["quote"]
    assert row["status"] == "corroborated"


def test_media_urls_are_recorded_without_duplicates(conn):
    upsert_incident(conn, make_incident(), [make_claim()],
                    [make_assessment()], make_reconciliation())
    upsert_incident(conn, make_incident(lat=50.4315), [make_claim()],
                    [make_assessment()], make_reconciliation())
    count = conn.execute("SELECT COUNT(*) AS n FROM incident_media").fetchone()["n"]
    assert count == 1


def test_geojson_export_shape(conn):
    upsert_incident(conn, make_incident(), [make_claim()],
                    [make_assessment()], make_reconciliation())
    gj = to_geojson(conn)
    assert gj["type"] == "FeatureCollection"
    feature = gj["features"][0]
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [30.4400, 50.4310]
    assert feature["properties"]["district"] == "Солом'янський"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_incidents_store.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_server.db'`

- [ ] **Step 3: Write `mcp_server/db.py`**

```python
"""SQLite schema and connection handling. No domain rules live here."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  domain TEXT NOT NULL,
  tier INTEGER,
  independence_group TEXT,
  title TEXT,
  published_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY,
  occurred_at TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  district TEXT NOT NULL,
  address_text TEXT,
  structure_type TEXT NOT NULL,
  killed INTEGER,
  injured INTEGER,
  damage TEXT NOT NULL,
  verification_level TEXT NOT NULL CHECK (verification_level IN
      ('unverified','single_source','corroborated','well_documented')),
  geocode_confidence REAL NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_time ON incidents(occurred_at);
CREATE INDEX IF NOT EXISTS idx_incidents_bbox ON incidents(lat, lon);

CREATE TABLE IF NOT EXISTS incident_claims (
  id INTEGER PRIMARY KEY,
  incident_id INTEGER NOT NULL REFERENCES incidents(id),
  source_id   INTEGER NOT NULL REFERENCES sources(id),
  field TEXT NOT NULL,
  value TEXT NOT NULL,
  quote TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN
      ('corroborated','single_source','conflicting','superseded')),
  extracted_at TEXT NOT NULL,
  UNIQUE(incident_id, source_id, field)
);

CREATE TABLE IF NOT EXISTS incident_media (
  id INTEGER PRIMARY KEY,
  incident_id INTEGER NOT NULL REFERENCES incidents(id),
  source_id   INTEGER NOT NULL REFERENCES sources(id),
  url TEXT NOT NULL,
  caption TEXT,
  UNIQUE(incident_id, url)
);

CREATE TABLE IF NOT EXISTS geocode_cache (
  query_key TEXT PRIMARY KEY,
  response_json TEXT NOT NULL,
  cached_at TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the incident database, creating the schema if needed."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
```

- [ ] **Step 4: Write `mcp_server/incidents_store.py`**

```python
"""Deduplicating writes with per-field provenance."""

import json
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urlparse

from common.schemas import IncidentClaim
from mcp_server.geo import haversine_m

# ponytail: linear scan over all incidents with haversine. At Kyiv volumes
# (thousands of rows) this is microseconds; add an R-tree index if it ever isn't.
DEDUP_RADIUS_M = 150.0
DEDUP_TIME_WINDOW = timedelta(hours=6)

LEVEL_ORDER = {
    "unverified": 0,
    "single_source": 1,
    "corroborated": 2,
    "well_documented": 3,
}


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _find_duplicate(conn: sqlite3.Connection, incident: dict) -> sqlite3.Row | None:
    occurred = _parse_ts(incident["occurred_at"])
    low = (occurred - DEDUP_TIME_WINDOW).isoformat()
    high = (occurred + DEDUP_TIME_WINDOW).isoformat()

    rows = conn.execute(
        "SELECT * FROM incidents WHERE occurred_at BETWEEN ? AND ?", (low, high)
    ).fetchall()

    for row in rows:
        distance = haversine_m(incident["lat"], incident["lon"], row["lat"], row["lon"])
        if distance <= DEDUP_RADIUS_M:
            return row
    return None


def _upsert_source(conn: sqlite3.Connection, claim: IncidentClaim,
                   assessment: dict | None) -> int:
    url = str(claim.source_url)
    domain = urlparse(url).netloc
    conn.execute(
        """INSERT INTO sources (url, domain, tier, independence_group,
                                published_at, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
               tier = excluded.tier,
               independence_group = excluded.independence_group""",
        (
            url, domain,
            (assessment or {}).get("tier"),
            (assessment or {}).get("independence_group"),
            claim.published_at.isoformat(),
            _now(),
        ),
    )
    return conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()["id"]


def _write_provenance(conn: sqlite3.Connection, incident_id: int,
                      claims: list[IncidentClaim], assessments: list[dict],
                      reconciliation: dict) -> None:
    by_url = {a["url"]: a for a in assessments}
    for claim in claims:
        source_id = _upsert_source(conn, claim, by_url.get(str(claim.source_url)))

        for field, reconciled in reconciliation["fields"].items():
            value = getattr(claim, field, None)
            if value is None:
                continue
            conn.execute(
                """INSERT INTO incident_claims
                       (incident_id, source_id, field, value, quote, status, extracted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(incident_id, source_id, field) DO UPDATE SET
                       value = excluded.value,
                       quote = excluded.quote,
                       status = excluded.status""",
                (incident_id, source_id, field, str(value), claim.quote,
                 reconciled["status"], _now()),
            )

        for media_url in claim.media_urls:
            conn.execute(
                """INSERT OR IGNORE INTO incident_media (incident_id, source_id, url)
                   VALUES (?, ?, ?)""",
                (incident_id, source_id, str(media_url)),
            )


def upsert_incident(conn: sqlite3.Connection, incident: dict,
                    claims: list[IncidentClaim], assessments: list[dict],
                    reconciliation: dict) -> dict:
    """Create or merge an incident. The only tool with persistent side effects."""
    existing = _find_duplicate(conn, incident)

    if existing is None:
        cursor = conn.execute(
            """INSERT INTO incidents
                   (occurred_at, lat, lon, district, address_text, structure_type,
                    killed, injured, damage, verification_level, geocode_confidence,
                    created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                incident["occurred_at"], incident["lat"], incident["lon"],
                incident["district"], incident.get("address_text"),
                incident["structure_type"], incident.get("killed"),
                incident.get("injured"),
                json.dumps(incident.get("damage", []), ensure_ascii=False),
                incident["verification_level"], incident["geocode_confidence"],
                _now(), _now(),
            ),
        )
        incident_id = cursor.lastrowid
        _write_provenance(conn, incident_id, claims, assessments, reconciliation)
        conn.commit()
        return {"incident_id": incident_id, "action": "created",
                "changed_fields": sorted(reconciliation["fields"])}

    incoming_rank = LEVEL_ORDER[incident["verification_level"]]
    existing_rank = LEVEL_ORDER[existing["verification_level"]]
    if incoming_rank < existing_rank:
        return {
            "incident_id": existing["id"],
            "action": "rejected_lower_confidence",
            "changed_fields": [],
        }

    changed: list[str] = []
    for field in ("killed", "injured", "structure_type", "address_text"):
        new_value = incident.get(field)
        if new_value is not None and new_value != existing[field]:
            conn.execute(
                f"UPDATE incidents SET {field} = ? WHERE id = ?",
                (new_value, existing["id"]),
            )
            changed.append(field)

    conn.execute(
        "UPDATE incidents SET verification_level = ?, updated_at = ? WHERE id = ?",
        (incident["verification_level"], _now(), existing["id"]),
    )
    _write_provenance(conn, existing["id"], claims, assessments, reconciliation)
    conn.commit()
    return {"incident_id": existing["id"], "action": "merged", "changed_fields": changed}


def to_geojson(conn: sqlite3.Connection) -> dict:
    """Export the registry in the shape an interactive map consumes directly."""
    features = []
    for row in conn.execute("SELECT * FROM incidents ORDER BY occurred_at"):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {
                "id": row["id"],
                "occurred_at": row["occurred_at"],
                "district": row["district"],
                "address_text": row["address_text"],
                "structure_type": row["structure_type"],
                "killed": row["killed"],
                "injured": row["injured"],
                "damage": json.loads(row["damage"]),
                "verification_level": row["verification_level"],
            },
        })
    return {"type": "FeatureCollection", "features": features}
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_incidents_store.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: SQLite registry with deduplicating upsert and provenance"
```

---

### Task 8: MCP server wiring — expose the four tools

**Files:**
- Create: `mcp_server/server.py`, `mcp_server/__main__.py`
- Test: `tests/test_server_tools.py`

**Interfaces:**
- Consumes: everything from Tasks 4–7
- Produces: an MCP stdio server exposing `assess_source_credibility`, `resolve_strike_location`, `corroborate_incident_claims`, `upsert_incident`, each returning the `ok`/`err` envelope

- [ ] **Step 1: Write the failing test**

Create `tests/test_server_tools.py`:

```python
import json

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "assess_source_credibility",
    "resolve_strike_location",
    "corroborate_incident_claims",
    "upsert_incident",
}


@pytest.fixture
async def session(tmp_path, monkeypatch):
    params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_server"],
        env={
            "PYTHONUTF8": "1",
            "KSM_OFFLINE": "1",
            "KSM_DB_PATH": str(tmp_path / "demo.sqlite"),
            "NOMINATIM_CONTACT": "test@example.com",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


async def test_all_four_tools_are_discoverable(session):
    tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert EXPECTED_TOOLS <= names


async def test_every_tool_declares_an_input_schema(session):
    tools = await session.list_tools()
    for tool in tools.tools:
        assert tool.inputSchema, f"{tool.name} has no input schema"
        assert tool.description, f"{tool.name} has no model-facing description"


async def test_out_of_area_returns_error_envelope(session):
    result = await session.call_tool(
        "resolve_strike_location",
        {"location_text": "вул. Дерибасівська, 1, Одеса"},
    )
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "OUT_OF_AREA"
    assert payload["error"]["retriable"] is False


async def test_unmatched_address_returns_empty_success(session):
    result = await session.call_tool(
        "resolve_strike_location",
        {"location_text": "вул. Абсолютно Неіснуюча, 4242"},
    )
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is True
    assert payload["data"]["matches"] == []


async def test_invalid_input_returns_schema_violation(session):
    result = await session.call_tool(
        "resolve_strike_location",
        {"location_text": "x"},           # below min_length
    )
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SCHEMA_VIOLATION"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_server_tools.py -v
```

Expected: FAIL — the server module does not exist.

- [ ] **Step 3: Write `mcp_server/server.py`**

```python
"""MCP registration layer. Protocol in, domain modules out, envelope back.

Deliberately thin: no domain rule may be introduced here, and no LLM client may
ever be imported into this process.
"""

import json
import os
from datetime import date
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field, ValidationError

from common.schemas import IncidentClaim, SourceRef, err, ok
from mcp_server.corroboration import corroborate
from mcp_server.credibility import assess_sources, load_registry
from mcp_server.db import connect
from mcp_server.geo import load_districts
from mcp_server.locate import OutOfArea, resolve_location
from mcp_server.incidents_store import upsert_incident as store_upsert
from mcp_server.nominatim import NominatimClient, RateLimited, UpstreamUnavailable

DATA = Path(__file__).resolve().parent.parent / "data"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nominatim"

mcp = FastMCP("kyiv-strikes-mcp")

_districts = load_districts(DATA / "kyiv_districts.geojson")
_registry = load_registry(DATA / "source_registry.yaml")
_conn = connect(os.environ.get("KSM_DB_PATH", "incidents.sqlite"))
_geocoder = NominatimClient(
    contact=os.environ.get("NOMINATIM_CONTACT", "unset@example.com"),
    offline=os.environ.get("KSM_OFFLINE", "0") == "1",
    fixtures_dir=FIXTURES,
)


@mcp.tool()
def assess_source_credibility(
    sources: Annotated[
        list[SourceRef],
        Field(description="Candidate articles with domain, publication time, and any "
                          "primary sources they cite."),
    ],
    event_date: Annotated[
        str, Field(description="Date of the strike, ISO YYYY-MM-DD.")
    ],
) -> str:
    """Rate candidate articles by source tier and, critically, by independence.

    Use this BEFORE corroborating claims. Two outlets that both merely reprint
    one official statement share an independence_group and count as a single
    source, not two. Returns per-article tier, independence_group, and whether
    the article may be used as corroboration.
    """
    try:
        parsed_date = date.fromisoformat(event_date)
    except ValueError as exc:
        return json.dumps(err("SCHEMA_VIOLATION", str(exc)), ensure_ascii=False)
    result = assess_sources(sources, _registry, parsed_date)
    return json.dumps(ok({"assessments": result}), ensure_ascii=False)


@mcp.tool()
def resolve_strike_location(
    location_text: Annotated[
        str,
        Field(min_length=3, max_length=200,
              description="Location as written in the article, e.g. "
                          "\"вул. Освіти, 4\" or \"Солом'янський район, просп. Перемоги 37\"."),
    ],
    claimed_district: Annotated[
        str | None,
        Field(default=None,
              description="District the article asserts, if any. Used only to detect "
                          "a conflict; the geometric district always wins."),
    ] = None,
) -> str:
    """Geocode a Kyiv location and validate it against real district polygons.

    Returns coordinates plus the district the point actually falls in, and sets
    district_conflict when the article's claimed district disagrees. A location
    that cannot be matched is a successful EMPTY result (matches: []), not an
    error. A location outside Kyiv is an OUT_OF_AREA error.
    """
    try:
        data = resolve_location(location_text, claimed_district, _geocoder, _districts)
    except OutOfArea as exc:
        return json.dumps(err("OUT_OF_AREA", str(exc)), ensure_ascii=False)
    except RateLimited as exc:
        return json.dumps(err("RATE_LIMITED", str(exc)), ensure_ascii=False)
    except UpstreamUnavailable as exc:
        return json.dumps(err("UPSTREAM_UNAVAILABLE", str(exc)), ensure_ascii=False)
    return json.dumps(ok(data), ensure_ascii=False)


@mcp.tool()
def corroborate_incident_claims(
    claims: Annotated[
        list[IncidentClaim],
        Field(description="One structured claim set per article, extracted upstream."),
    ],
    assessments: Annotated[
        list[dict],
        Field(description="Output of assess_source_credibility for the same articles."),
    ],
) -> str:
    """Reconcile claims from several articles into per-field verified values.

    Collapses articles sharing an independence_group, treats a later higher
    casualty count as superseding rather than contradicting an earlier one, and
    flags genuine same-hour disagreements as conflicting. Returns an overall
    verification_level and missing_for_next_level — an explicit list of what
    still has to be found. Act on missing_for_next_level: it tells you what to
    search for next.
    """
    result = corroborate(claims, assessments)
    return json.dumps(ok(result), ensure_ascii=False)


@mcp.tool()
def upsert_incident(
    incident: Annotated[
        dict,
        Field(description="Reconciled incident: occurred_at, lat, lon, district, "
                          "address_text, structure_type, killed, injured, damage, "
                          "verification_level, geocode_confidence."),
    ],
    claims: Annotated[list[IncidentClaim], Field(description="Supporting claims.")],
    assessments: Annotated[list[dict], Field(description="Their credibility assessments.")],
    reconciliation: Annotated[dict, Field(description="Output of corroborate_incident_claims.")],
) -> str:
    """Write a verified incident to the registry, merging instead of duplicating.

    Matches existing incidents within 150 m and 6 hours and merges into them.
    Refuses to overwrite a record that already holds a higher verification_level.
    Writes one provenance row per (incident, source, field) carrying the quote
    that supports the value. This is the only tool with persistent side effects.
    """
    try:
        result = store_upsert(_conn, incident, claims, assessments, reconciliation)
    except (KeyError, ValidationError, ValueError) as exc:
        return json.dumps(err("SCHEMA_VIOLATION", str(exc)), ensure_ascii=False)
    if result["action"] == "rejected_lower_confidence":
        return json.dumps(
            err("DUPLICATE_REJECTED",
                f"Incident {result['incident_id']} already holds a higher "
                "verification level; refusing to downgrade."),
            ensure_ascii=False,
        )
    return json.dumps(ok(result), ensure_ascii=False)
```

- [ ] **Step 4: Write `mcp_server/__main__.py`**

```python
from mcp_server.server import mcp

if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 5: Verify the server starts independently**

```bash
export PYTHONUTF8=1 KSM_OFFLINE=1 NOMINATIM_CONTACT="you@example.com"
python -m mcp_server
```

Expected: the process starts and waits on stdin without error. Press Ctrl+C. This is exactly the independent start you must demonstrate at the defence.

- [ ] **Step 6: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_server_tools.py -v
```

Expected: 5 passed. If `test_invalid_input_returns_schema_violation` fails because FastMCP raises before your handler runs, wrap the four tool bodies with a shared decorator that catches `ValidationError` and returns `err("SCHEMA_VIOLATION", ...)`, and re-run.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: MCP server exposing four domain tools with error envelopes"
```

---

### Task 9: Playwright MCP connection and source probe

**Files:**
- Create: `mcp_config.json`
- Create: `agent/mcp_clients.py`
- Create: `scripts/probe_playwright.py`
- Test: `tests/test_mcp_clients.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `build_client(config_path) -> MultiServerMCPClient`, `MCPConnectionError`, `load_config(path) -> dict`

- [ ] **Step 1: Write `mcp_config.json`**

Replace `PINNED_VERSION` with the exact version the instructor announced.

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@PINNED_VERSION", "--headless"],
      "transport": "stdio"
    },
    "kyiv-strikes": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "transport": "stdio",
      "env": {
        "PYTHONUTF8": "1",
        "KSM_OFFLINE": "${KSM_OFFLINE}",
        "KSM_DB_PATH": "${KSM_DB_PATH}",
        "NOMINATIM_CONTACT": "${NOMINATIM_CONTACT}"
      }
    }
  }
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_mcp_clients.py`:

```python
import json

import pytest

from agent.mcp_clients import MCPConnectionError, load_config


def test_config_declares_both_servers():
    config = load_config("mcp_config.json")
    assert set(config["mcpServers"]) == {"playwright", "kyiv-strikes"}


def test_playwright_version_is_pinned():
    config = load_config("mcp_config.json")
    args = " ".join(config["mcpServers"]["playwright"]["args"])
    assert "@latest" not in args, "pin the instructor-announced version, not @latest"


def test_missing_config_raises_connection_error(tmp_path):
    with pytest.raises(MCPConnectionError):
        load_config(tmp_path / "nope.json")


def test_env_placeholders_are_expanded(monkeypatch):
    monkeypatch.setenv("KSM_OFFLINE", "1")
    monkeypatch.setenv("KSM_DB_PATH", "test.sqlite")
    monkeypatch.setenv("NOMINATIM_CONTACT", "a@b.c")
    config = load_config("mcp_config.json")
    env = config["mcpServers"]["kyiv-strikes"]["env"]
    assert env["KSM_OFFLINE"] == "1"
    assert env["KSM_DB_PATH"] == "test.sqlite"
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_mcp_clients.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'agent.mcp_clients'`

- [ ] **Step 4: Write `agent/mcp_clients.py`**

```python
"""MCP client wiring for both servers, with legible connection failures.

Failure scenario B of the design depends on this module: when Playwright MCP
cannot start, the agent must say so plainly rather than emit a traceback.
"""

import json
import os
import re
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


class MCPConnectionError(RuntimeError):
    """A configured MCP server could not be loaded, started, or initialized."""


def _expand(value: str) -> str:
    return _PLACEHOLDER.sub(lambda m: os.environ.get(m.group(1), ""), value)


def load_config(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise MCPConnectionError(
            f"MCP configuration not found at {path}. "
            "Copy mcp_config.json into place and pin the Playwright MCP version."
        )
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPConnectionError(f"MCP configuration at {path} is not valid JSON: {exc}") from exc

    for server in config.get("mcpServers", {}).values():
        server["env"] = {k: _expand(v) for k, v in server.get("env", {}).items()}
    return config


async def build_client(config_path: str | Path = "mcp_config.json") -> MultiServerMCPClient:
    """Create a client for both servers and verify each one initializes."""
    config = load_config(config_path)
    client = MultiServerMCPClient(config["mcpServers"])
    for name in config["mcpServers"]:
        try:
            await client.get_tools(server_name=name)
        except Exception as exc:
            raise MCPConnectionError(
                f"MCP server {name!r} failed to initialize: {exc}\n"
                f"Check that its command is installed and its configuration is correct."
            ) from exc
    return client
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_mcp_clients.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Write the Playwright probe**

Create `scripts/probe_playwright.py`:

```python
"""Confirm that Playwright MCP starts and that whitelisted sites serve content.

Run this BEFORE building the graph. If a source blocks headless browsers, you
need to know now, not during the defence.

Usage:
    python scripts/probe_playwright.py
"""

import asyncio

from agent.mcp_clients import MCPConnectionError, build_client

TARGETS = [
    "https://suspilne.media/",
    "https://www.pravda.com.ua/",
    "https://dsns.gov.ua/",
    "https://kyivcity.gov.ua/",
]


async def main() -> int:
    try:
        client = await build_client()
    except MCPConnectionError as exc:
        print(f"MCP connection failed:\n{exc}")
        return 1

    tools = {t.name: t for t in await client.get_tools(server_name="playwright")}
    print("Playwright tools discovered:", ", ".join(sorted(tools)))

    navigate = tools.get("browser_navigate")
    snapshot = tools.get("browser_snapshot")
    if not navigate or not snapshot:
        print("Expected browser_navigate and browser_snapshot; adjust for this version.")
        return 1

    for url in TARGETS:
        try:
            await navigate.ainvoke({"url": url})
            page = await snapshot.ainvoke({})
            print(f"OK   {url}  ({len(str(page))} chars)")
        except Exception as exc:
            print(f"FAIL {url}  {type(exc).__name__}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 7: Run the probe**

```bash
export PYTHONUTF8=1 KSM_OFFLINE=1 KSM_DB_PATH=incidents.sqlite NOMINATIM_CONTACT="you@example.com"
python scripts/probe_playwright.py
```

Expected: the tool list prints, and each target reports OK with a non-trivial character count. Any site reporting FAIL must be removed from the whitelist in `data/source_registry.yaml` or replaced — record which, and why, in `docs/design_rationale.md`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: MCP client wiring for both servers and a Playwright source probe"
```

---

### Task 10: Extraction node and the linear graph

**Files:**
- Create: `agent/prompts/extract.md`, `agent/nodes.py`, `agent/graph.py`, `agent/__main__.py`
- Test: `tests/test_nodes.py`

**Interfaces:**
- Consumes: `common.schemas.IncidentClaim`; `agent.mcp_clients.build_client`
- Produces: `AgentState` (TypedDict), node functions `parse_request`, `discover_sources`, `extract_claims`, `assess`, `locate`, `reconcile`, `persist`, `report`; `build_graph(tools) -> CompiledGraph`

`AgentState` keys: `query: str`, `event_date: str`, `round: int`, `avoid_groups: list[str]`, `articles: list[dict]`, `claims: list[IncidentClaim]`, `assessments: list[dict]`, `location: dict | None`, `reconciliation: dict | None`, `unreachable: list[str]`, `result: dict | None`, `notes: list[str]`

- [ ] **Step 1: Write the extraction prompt**

Create `agent/prompts/extract.md`:

```markdown
You extract structured facts about a single strike impact location from one news article.

Rules:

- Report ONLY what the article states. Never infer, average, or complete missing values.
- If the article gives no casualty figure, leave the field null. Zero is not the same as unknown.
- `location_text` must be the address exactly as the article writes it, not your normalization.
- `claimed_district` is filled only when the article names a district explicitly.
- `quote` must be a verbatim sentence from the article that supports the values you filled in. Never paraphrase it. This field is how a reader traces the number back to its origin.
- Never include names of victims or any personal data.
- If the article describes several distinct impact locations, produce one claim per location.

Article URL: {url}
Published: {published_at}

Article text:
{text}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_nodes.py`:

```python
from datetime import date

from agent.nodes import next_query, parse_request, should_continue


def test_parse_request_extracts_iso_date():
    state = parse_request({"query": "удар по Києву 17 червня 2026"})
    assert state["event_date"] == "2026-06-17"
    assert state["round"] == 0


def test_parse_request_accepts_explicit_iso_date():
    state = parse_request({"query": "strike on Kyiv 2026-06-17"})
    assert state["event_date"] == "2026-06-17"


def test_should_continue_stops_when_corroborated():
    state = {
        "round": 0,
        "reconciliation": {"verification_level": "corroborated",
                           "missing_for_next_level": []},
        "location": {"district_conflict": False},
    }
    assert should_continue(state) == "persist"


def test_should_continue_retries_on_single_source():
    state = {
        "round": 0,
        "reconciliation": {"verification_level": "single_source",
                           "missing_for_next_level": ["need another group"]},
        "location": {"district_conflict": False},
    }
    assert should_continue(state) == "discover_sources"


def test_should_continue_retries_on_district_conflict():
    state = {
        "round": 0,
        "reconciliation": {"verification_level": "corroborated",
                           "missing_for_next_level": []},
        "location": {"district_conflict": True},
    }
    assert should_continue(state) == "discover_sources"


def test_should_continue_stops_after_three_rounds():
    state = {
        "round": 3,
        "reconciliation": {"verification_level": "single_source",
                           "missing_for_next_level": ["need another group"]},
        "location": {"district_conflict": False},
    }
    assert should_continue(state) == "persist"


def test_next_query_excludes_groups_already_used():
    state = {
        "query": "удар по Києву 17 червня",
        "event_date": "2026-06-17",
        "reconciliation": {"usable_groups": ["suspilne"],
                           "missing_for_next_level": ["An official (tier 1) source."]},
    }
    query = next_query(state)
    assert "suspilne" not in query.lower()
    assert "dsns.gov.ua" in query or "kyivcity.gov.ua" in query
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_nodes.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'agent.nodes'`

- [ ] **Step 4: Write the routing and query logic in `agent/nodes.py`**

Write the two pure functions first; the async MCP-calling nodes follow in Step 6.

```python
"""Graph nodes. The pure routing functions are tested directly; the async nodes
are exercised end-to-end in Task 12."""

import re
from datetime import date

MAX_ROUNDS = 3

_UA_MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}

# Tier-1 domains, used to steer a follow-up search toward official confirmation.
_OFFICIAL_DOMAINS = ["dsns.gov.ua", "kyivcity.gov.ua", "npu.gov.ua"]


def _extract_date(query: str) -> str:
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", query)
    if iso:
        return iso.group(0)

    ua = re.search(r"\b(\d{1,2})\s+([а-яіїєґ]+)\s+(\d{4})\b", query.lower())
    if ua and ua.group(2) in _UA_MONTHS:
        return date(int(ua.group(3)), _UA_MONTHS[ua.group(2)], int(ua.group(1))).isoformat()

    raise ValueError(
        f"No date found in {query!r}. Include a date, e.g. '2026-06-17' or "
        "'17 червня 2026'."
    )


def parse_request(state: dict) -> dict:
    """Normalize the user query into an event date and initialize the loop."""
    return {
        **state,
        "event_date": _extract_date(state["query"]),
        "round": 0,
        "avoid_groups": [],
        "unreachable": [],
        "notes": [],
    }


def should_continue(state: dict) -> str:
    """Conditional edge: go back for more sources, or write what we have.

    This function is the agentic part of the system. Tool output — the
    verification level, the missing-evidence list, the district conflict flag —
    decides what happens next.
    """
    if state["round"] >= MAX_ROUNDS:
        return "persist"

    reconciliation = state.get("reconciliation") or {}
    location = state.get("location") or {}

    if location.get("district_conflict"):
        return "discover_sources"

    level = reconciliation.get("verification_level", "unverified")
    if level in ("unverified", "single_source"):
        return "discover_sources"
    if reconciliation.get("missing_for_next_level") and level == "corroborated":
        return "discover_sources"
    return "persist"


def next_query(state: dict) -> str:
    """Build the follow-up search, steered by what corroboration says is missing."""
    reconciliation = state.get("reconciliation") or {}
    used = set(reconciliation.get("usable_groups", []))
    missing = " ".join(reconciliation.get("missing_for_next_level", [])).lower()

    parts = [state["query"]]
    if "official" in missing or "tier 1" in missing:
        parts.append("site:" + " OR site:".join(_OFFICIAL_DOMAINS))
    for group in sorted(used):
        parts.append(f"-{group}")
    return " ".join(parts)
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_nodes.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Append the async nodes to `agent/nodes.py`**

```python
import json
import os
from pathlib import Path

from langchain_openai import ChatOpenAI

from common.schemas import IncidentClaim

PROMPT = (Path(__file__).parent / "prompts" / "extract.md").read_text(encoding="utf-8")


def _model() -> ChatOpenAI:
    return ChatOpenAI(
        model=os.environ["OPENROUTER_MODEL"],
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
    )


def _call(tools: dict, name: str, args: dict) -> dict:
    """Invoke a custom-server tool and unwrap the envelope."""
    raw = tools[name].invoke(args)
    return json.loads(raw if isinstance(raw, str) else raw[0].text)


def make_nodes(tools: dict):
    """Bind graph nodes to a discovered tool mapping {tool_name: tool}."""

    async def discover_sources(state: dict) -> dict:
        query = state["query"] if state["round"] == 0 else next_query(state)
        articles, unreachable = [], list(state.get("unreachable", []))

        for url in _search_urls(query):
            try:
                await tools["browser_navigate"].ainvoke({"url": url})
                page = await tools["browser_snapshot"].ainvoke({})
            except Exception as exc:
                unreachable.append(f"{url}: {type(exc).__name__}: {exc}")
                continue
            articles.append({"url": url, "text": str(page)})

        return {**state, "round": state["round"] + 1,
                "articles": articles, "unreachable": unreachable}

    async def extract_claims(state: dict) -> dict:
        model = _model().with_structured_output(IncidentClaim)
        claims = list(state.get("claims", []))
        for article in state["articles"]:
            prompt = PROMPT.format(
                url=article["url"],
                published_at=article.get("published_at", "unknown"),
                text=article["text"][:20000],
            )
            claims.append(await model.ainvoke(prompt))
        return {**state, "claims": claims}

    async def assess(state: dict) -> dict:
        payload = {
            "sources": [
                {"url": str(c.source_url),
                 "domain": str(c.source_url).split("/")[2],
                 "published_at": c.published_at.isoformat()}
                for c in state["claims"]
            ],
            "event_date": state["event_date"],
        }
        result = _call(tools, "assess_source_credibility", payload)
        return {**state, "assessments": result["data"]["assessments"]}

    async def locate(state: dict) -> dict:
        first = state["claims"][0]
        result = _call(tools, "resolve_strike_location", {
            "location_text": first.location_text,
            "claimed_district": first.claimed_district,
        })
        if not result["ok"]:
            notes = state["notes"] + [f"geocoding failed: {result['error']['code']}"]
            return {**state, "location": None, "notes": notes}
        return {**state, "location": result["data"]}

    async def reconcile(state: dict) -> dict:
        result = _call(tools, "corroborate_incident_claims", {
            "claims": [c.model_dump(mode="json") for c in state["claims"]],
            "assessments": state["assessments"],
        })
        return {**state, "reconciliation": result["data"]}

    async def persist(state: dict) -> dict:
        location = state.get("location")
        if not location or location.get("lat") is None:
            return {**state, "result": {"action": "skipped",
                                        "reason": "no usable geocode"}}
        fields = state["reconciliation"]["fields"]
        incident = {
            "occurred_at": (state["claims"][0].occurred_at
                            or state["claims"][0].published_at).isoformat(),
            "lat": location["lat"], "lon": location["lon"],
            "district": location["district"],
            "address_text": state["claims"][0].location_text,
            "structure_type": fields.get("structure_type", {}).get(
                "value", state["claims"][0].structure_type),
            "killed": fields.get("killed", {}).get("value"),
            "injured": fields.get("injured", {}).get("value"),
            "damage": state["claims"][0].damage,
            "verification_level": state["reconciliation"]["verification_level"],
            "geocode_confidence": location["confidence"],
        }
        result = _call(tools, "upsert_incident", {
            "incident": incident,
            "claims": [c.model_dump(mode="json") for c in state["claims"]],
            "assessments": state["assessments"],
            "reconciliation": state["reconciliation"],
        })
        return {**state, "result": result.get("data", result.get("error"))}

    return {
        "parse_request": parse_request,
        "discover_sources": discover_sources,
        "extract_claims": extract_claims,
        "assess": assess,
        "locate": locate,
        "reconcile": reconcile,
        "persist": persist,
    }


def _search_urls(query: str) -> list[str]:
    """Whitelisted search entry points for the query.

    Kept deliberately simple: each whitelisted site's own search page. No general
    web search engine is used, so the source whitelist cannot be bypassed.
    """
    from urllib.parse import quote_plus

    q = quote_plus(query)
    return [
        f"https://suspilne.media/search/?query={q}",
        f"https://www.pravda.com.ua/search/?query={q}",
        f"https://dsns.gov.ua/search?q={q}",
    ]
```

- [ ] **Step 7: Write `agent/graph.py`**

```python
"""Graph assembly. The conditional edge from `reconcile` is where tool output
becomes control flow."""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.nodes import make_nodes, should_continue


class AgentState(TypedDict, total=False):
    query: str
    event_date: str
    round: int
    avoid_groups: list[str]
    articles: list[dict]
    claims: list[Any]
    assessments: list[dict]
    location: dict | None
    reconciliation: dict | None
    unreachable: list[str]
    result: dict | None
    notes: list[str]


def build_graph(tools: dict):
    nodes = make_nodes(tools)
    graph = StateGraph(AgentState)

    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "parse_request")
    graph.add_edge("parse_request", "discover_sources")
    graph.add_edge("discover_sources", "extract_claims")
    graph.add_edge("extract_claims", "assess")
    graph.add_edge("assess", "locate")
    graph.add_edge("locate", "reconcile")
    graph.add_conditional_edges(
        "reconcile",
        should_continue,
        {"discover_sources": "discover_sources", "persist": "persist"},
    )
    graph.add_edge("persist", END)

    return graph.compile()
```

- [ ] **Step 8: Write `agent/__main__.py`**

```python
import asyncio
import json
import sys

from agent.graph import build_graph
from agent.mcp_clients import MCPConnectionError, build_client


async def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python -m agent "удар по Києву 17 червня 2026"')
        return 2

    try:
        client = await build_client()
    except MCPConnectionError as exc:
        print(f"\nCannot start: {exc}\n")
        return 1

    playwright_tools = await client.get_tools(server_name="playwright")
    custom_tools = await client.get_tools(server_name="kyiv-strikes")

    print(f"Playwright MCP tools ({len(playwright_tools)}):",
          ", ".join(sorted(t.name for t in playwright_tools)))
    print(f"kyiv-strikes MCP tools ({len(custom_tools)}):",
          ", ".join(sorted(t.name for t in custom_tools)))

    tools = {t.name: t for t in playwright_tools + custom_tools}
    graph = build_graph(tools)
    final = await graph.ainvoke({"query": sys.argv[1]})

    print("\n=== RESULT ===")
    print(json.dumps({
        "rounds": final.get("round"),
        "verification_level": (final.get("reconciliation") or {}).get("verification_level"),
        "missing": (final.get("reconciliation") or {}).get("missing_for_next_level"),
        "unreachable_sources": final.get("unreachable"),
        "write": final.get("result"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 9: Run the full test suite**

```bash
export PYTHONUTF8=1 && python -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: LangGraph agent with extraction node and re-search loop"
```

---

### Task 11: Failure scenarios and offline mode verification

**Files:**
- Create: `scripts/demo_failures.sh`
- Modify: `agent/nodes.py` — the `discover_sources` node's `unreachable` handling is already written; this task verifies it end to end
- Test: `tests/test_failure_modes.py`

**Interfaces:**
- Consumes: everything above
- Produces: `scripts/demo_failures.sh`, reproducing all three failure scenarios in order

- [ ] **Step 1: Write the failing test**

Create `tests/test_failure_modes.py`:

```python
import json

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.mcp_clients import MCPConnectionError, build_client
from agent.nodes import should_continue


@pytest.fixture
async def session(tmp_path):
    params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_server"],
        env={"PYTHONUTF8": "1", "KSM_OFFLINE": "1",
             "KSM_DB_PATH": str(tmp_path / "f.sqlite"),
             "NOMINATIM_CONTACT": "test@example.com"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


async def test_scenario_b_broken_config_raises_clear_error(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text('{"mcpServers": {"playwright": {"command": "definitely-not-a-real-binary",'
                   ' "args": [], "transport": "stdio"}}}', encoding="utf-8")
    with pytest.raises(MCPConnectionError) as exc:
        await build_client(bad)
    assert "playwright" in str(exc.value)
    assert "failed to initialize" in str(exc.value)


async def test_scenario_c_out_of_area_is_error_not_empty(session):
    result = await session.call_tool(
        "resolve_strike_location", {"location_text": "вул. Дерибасівська, 1, Одеса"}
    )
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "OUT_OF_AREA"


def test_scenario_a_unreachable_source_degrades_verification_not_crash():
    """With one source lost, corroboration falls to single_source and the graph
    routes back for another round rather than failing."""
    state = {
        "round": 1,
        "unreachable": ["https://pravda.com.ua/x: TimeoutError"],
        "reconciliation": {"verification_level": "single_source",
                           "missing_for_next_level": ["independent confirmation"],
                           "usable_groups": ["suspilne"]},
        "location": {"district_conflict": False},
    }
    assert should_continue(state) == "discover_sources"


def test_exhausted_budget_writes_degraded_record_rather_than_failing():
    state = {
        "round": 3,
        "unreachable": ["https://pravda.com.ua/x: TimeoutError"],
        "reconciliation": {"verification_level": "single_source",
                           "missing_for_next_level": ["independent confirmation"],
                           "usable_groups": ["suspilne"]},
        "location": {"district_conflict": False},
    }
    assert should_continue(state) == "persist"
```

- [ ] **Step 2: Run the test to verify it fails, then passes**

```bash
export PYTHONUTF8=1 && python -m pytest tests/test_failure_modes.py -v
```

Expected: initially FAIL on the import if any module is missing; after Task 10 it should pass without new implementation code. If `test_scenario_b` fails, `build_client` is not catching startup errors — fix `agent/mcp_clients.py`.

- [ ] **Step 3: Write `scripts/demo_failures.sh`**

```bash
#!/usr/bin/env bash
# Reproduces all three failure scenarios in defence order.
set -uo pipefail
export PYTHONUTF8=1

echo "=== Scenario C: custom server rejects an out-of-Kyiv address ==="
KSM_OFFLINE=1 python - <<'PY'
import asyncio, json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    p = StdioServerParameters(command="python", args=["-m", "mcp_server"],
        env={"PYTHONUTF8":"1","KSM_OFFLINE":"1","KSM_DB_PATH":"demo.sqlite",
             "NOMINATIM_CONTACT":"demo@example.com"})
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for text in ["вул. Дерибасівська, 1, Одеса", "вул. Абсолютно Неіснуюча, 4242"]:
                out = await s.call_tool("resolve_strike_location", {"location_text": text})
                print(text, "->", out.content[0].text)

asyncio.run(main())
PY

echo
echo "=== Scenario B: Playwright MCP cannot start ==="
cp mcp_config.json mcp_config.json.bak
python - <<'PY'
import json
c = json.load(open("mcp_config.json", encoding="utf-8"))
c["mcpServers"]["playwright"]["command"] = "definitely-not-a-real-binary"
json.dump(c, open("mcp_config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
python -m agent "удар по Києву 2026-06-17"
mv mcp_config.json.bak mcp_config.json

echo
echo "=== Scenario A: one article 404s mid-run ==="
echo "Run the agent normally and point browser_navigate at a removed article."
echo "Expected: that source is listed under unreachable_sources, the run"
echo "completes, and verification_level is lower than a clean run."
```

- [ ] **Step 4: Run the failure demo**

```bash
export PYTHONUTF8=1 && bash scripts/demo_failures.sh
```

Expected: scenario C prints one `OUT_OF_AREA` error envelope and one empty-success envelope; scenario B prints the clear "Cannot start" message with no traceback.

- [ ] **Step 5: Verify offline mode is faithful**

```bash
export PYTHONUTF8=1 NOMINATIM_CONTACT="you@example.com"
KSM_OFFLINE=0 python -m pytest tests/test_locate.py -v
KSM_OFFLINE=1 python -m pytest tests/test_locate.py -v
```

Expected: identical results. The tests inject their own fixtures, so both runs exercise the same parsing path — which is the point.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "test: failure scenarios and offline-mode fidelity checks"
```

---

### Task 12: Documentation and the defence package

**Files:**
- Create: `docs/tool_contracts.md`, `docs/design_rationale.md`, `docs/defence_checklist.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the finished system
- Produces: the submission artefacts listed in the assignment

- [ ] **Step 1: Write `README.md`**

```markdown
# Kyiv Strikes Incident Registry

An agent that turns one query about a strike on Kyiv into verified, geolocated,
provenance-tracked incident records.

## Prerequisites

- Python 3.14
- Node 24 (for Playwright MCP)
- `npx playwright install chromium`

## Installation

    python -m pip install -r requirements.txt
    cp .env.example .env      # then fill in the values

## Configuration

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Model inference. Read by the AGENT only. |
| `OPENROUTER_MODEL` | Model id; must support structured output. |
| `NOMINATIM_CONTACT` | Real contact address, required by the Nominatim usage policy. |
| `KSM_OFFLINE` | `1` replays recorded Nominatim fixtures. |
| `KSM_DB_PATH` | Incident database path. |

The Playwright MCP version is pinned in `mcp_config.json` to the version
announced for the course: `@playwright/mcp@PINNED_VERSION`.

## Running

Start the custom MCP server independently:

    PYTHONUTF8=1 KSM_OFFLINE=1 python -m mcp_server

Then, in a second terminal, run the agent:

    PYTHONUTF8=1 python -m agent "удар по Києву 17 червня 2026"

## Tests

    PYTHONUTF8=1 python -m pytest -v

## Data and ethics

The registry stores aggregate casualty figures drawn from published reporting.
It contains no victim names and no personal data. Photographs are referenced by
URL and attributed to their source; no image files are downloaded or
redistributed.

## Documentation

- `docs/tool_contracts.md` — full contract for every custom tool and the Playwright tool used
- `docs/design_rationale.md` — why each boundary sits where it does
- `docs/defence_checklist.md` — the demonstration script
- `docs/superpowers/specs/2026-08-22-kyiv-strikes-agent-design.md` — the design spec
```

- [ ] **Step 2: Write `docs/tool_contracts.md`**

Document all four custom tools plus one Playwright tool, using the exact table structure the assignment's Part C requires: Name, Purpose, Model-facing description, Input schema, Output schema, Error conditions, Side effects, Example.

Copy the model-facing descriptions verbatim from the docstrings in `mcp_server/server.py` — if they drift apart, the documentation contradicts behaviour, which the rubric penalizes directly. For each tool, run it once and paste the genuine input/output pair as the example.

For the Playwright tool, document `browser_navigate` as configured in this project: its arguments, what a snapshot returns, and the error conditions observed during `scripts/probe_playwright.py`.

- [ ] **Step 3: Write `docs/design_rationale.md`**

Cover the six points the assignment's submission section requires:

1. why Playwright MCP is relevant here (public sites, no APIs, structured page interaction)
2. why each custom tool belongs at the MCP boundary rather than inside the agent
3. how the four tools support the workflow, including the `missing_for_next_level` feedback loop
4. the main trade-off: LLM extraction in the agent, determinism in the server
5. known limitations, copied from section 13 of the spec
6. which whitelisted sources failed the Playwright probe and what was done about it

- [ ] **Step 4: Write `docs/defence_checklist.md`**

```markdown
# Defence checklist

Target: 10–15 minutes. Rehearse once with a timer before recording.

## 0. Before starting (not recorded)

- [ ] `.env` filled in, `KSM_OFFLINE=0` for the live run
- [ ] `npx playwright install chromium` done
- [ ] `python -m pytest` green
- [ ] `fixtures/articles/` populated, `python -m http.server 8899` ready as fallback
- [ ] a known-removed article URL ready for scenario A

## 1. Independent startup and architecture (2 min)

- [ ] Terminal 1: `PYTHONUTF8=1 python -m mcp_server` — server runs alone
- [ ] Terminal 2: `python -m agent "..."` — both tool lists print at startup
- [ ] Point at the architecture diagram: two processes, two connections

## 2. Playwright MCP inside an agent flow (2–3 min)

- [ ] Show `browser_navigate` fetching candidate articles
- [ ] Show `assess_source_credibility` consuming those results and rerouting the graph
- [ ] Explain the `browser_navigate` contract: arguments, return, failure modes

## 3. Custom MCP end-to-end (3–4 min)

- [ ] Full run to `upsert_incident`; show the new row in SQLite
- [ ] Show all four custom tools in the discovery output
- [ ] Explain `corroborate_incident_claims`: the independence rule and why
      `missing_for_next_level` exists

## 4. Failure and offline mode (2 min)

- [ ] Scenario A: 404 article — source marked unreachable, verification degrades
- [ ] Scenario C: out-of-Kyiv address — `OUT_OF_AREA` vs empty success
- [ ] Show `KSM_OFFLINE=1` producing the same result through the same code path

## 5. Variation and tracing (3–4 min)

- [ ] Different valid input: another date
- [ ] Invalid input: `location_text` of one character → `SCHEMA_VIOLATION`
- [ ] Trace a value: `SELECT killed FROM incidents` → the matching
      `incident_claims` row → its `quote` → that sentence in the live article
```

- [ ] **Step 5: Run the whole suite and a clean end-to-end demo**

```bash
export PYTHONUTF8=1
python -m pytest -v
python -m agent "удар по Києву 17 червня 2026"
```

Expected: tests green; the agent prints both tool lists, the rounds taken, the verification level, and the write action.

- [ ] **Step 6: Verify no secrets are committed**

```bash
git ls-files | xargs grep -lE "sk-or-v1-[A-Za-z0-9]|OPENROUTER_API_KEY=sk-" || echo "clean"
```

Expected: `clean`. `.env.example` contains only the placeholder `sk-or-v1-REPLACE_ME`, which is fine.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "docs: tool contracts, design rationale, and defence checklist"
```

---

## Self-Review Notes

**Spec coverage.** Every section of the design spec maps to a task: §3.2 boundary schema → Task 1; §5.2 primary data source → Tasks 2–4; §5.1 → Task 5; §5.3 → Task 6; §5.4 and §7 → Task 7; §3 process separation → Task 8; §4 Playwright → Task 9; §6 workflow and conditional edges → Task 10; §8 failures and §9 fixtures → Task 11; §10–12 → Task 12. The deferred severity decision in §2 is intentionally unimplemented.

**Known plan risks, to resolve during execution rather than by guessing now:**

1. **OSM admin level for Kyiv districts** (Task 2, Step 2). The query uses `admin_level=9`. If it returns other than 10 features, the fallback procedure is written into the step. Everything downstream depends on this dataset, so do not proceed past a failing Step 7 in Task 2.
2. **FastMCP validation timing** (Task 8, Step 6). Whether a Pydantic failure surfaces as your `SCHEMA_VIOLATION` envelope or as a protocol-level error depends on where FastMCP validates. The fallback — a shared decorator catching `ValidationError` — is written into the step.
3. **Site search URL patterns** (Task 10, Step 6). `_search_urls` guesses each outlet's search path. Task 9's probe exists precisely to correct these before the graph is built; update `_search_urls` with whatever the probe confirms.
4. **Playwright MCP tool names** (Task 9, Step 6). `browser_navigate` and `browser_snapshot` are the names in current versions; the probe prints the actual list. If the pinned version differs, adjust `make_nodes` accordingly.
