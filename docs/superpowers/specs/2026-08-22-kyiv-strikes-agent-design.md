# Kyiv Strikes Incident Registry — Design Spec

**Date:** 2026-08-22
**Assignment:** MCP Integration — AI Agentic Lab (`ai_agentic_lab_assignment.md`)
**Status:** approved for planning

## 1. Problem

An analyst or journalist needs a *verified* record of a specific Russian strike on
Kyiv: where each impact happened, what was hit, how many people were killed or
injured, and which published reporting supports every one of those numbers.

Ukrainian news reporting on a single strike is fragmented across many outlets,
much of it republished from one original source, with casualty figures that
change over the following days and district attributions that are frequently
wrong. Reading five articles does not produce a verified record; it produces
five partially-overlapping claims that still have to be reconciled.

The agent takes one incident query and produces reconciled, geolocated,
provenance-tracked incident rows in a database whose shape is directly usable by
an interactive map later.

**Scope of one run:** one night's attack, producing one attack row and one
incident row per distinct impact location.

**Two levels, kept apart.** Ukrainian reporting gives casualty figures for the
city, not for an address: "у Києві загинули пʼятеро". Storing that on each
impact would multiply it — three sites hit would sum to fifteen deaths, and any
filter by district or by month would return a number nobody ever reported. So
city-wide figures, and the tally of what was launched and intercepted, live on
the **attack**; per-address casualties live on the **impact**, and only when an
article ties them to that address. Where an article does not make clear which it
means, neither is recorded.

That split makes one check possible that neither level could do alone: the
deaths attributed to individual addresses cannot add up to more than the city
reported. When they do, a city-wide number was probably read as a local one, and
the attack is flagged for a person.

## 2. Non-goals

Explicitly out of scope for this submission, recorded for later:

- autonomous scheduled monitoring of sources (this build is query-driven). A
  monitoring crawler would additionally need to handle **late retrospective
  reporting**: summary and investigative pieces published weeks after an event
  often carry the most accurate figures, but they fall outside the relevance
  window a single query-driven run uses. Such a crawler must be able to reopen
  an already-written incident when better-sourced reporting appears, rather than
  treating the first pass as final.
- downloading or deduplicating photograph files. Photo URLs and captions *are*
  recorded (`incident_media`); no image bytes are ever fetched or stored.
- the interactive map frontend itself (the schema is map-ready; the map is not built)
- Telegram and social-media sources
- cities other than Kyiv
- **place-context enrichment — what the struck location was and why it mattered.**
  Designed but deferred. A `describe_location_context` tool would query three
  free unauthenticated sources for *facts* about a point: (a) the OSM object
  itself (building type, name, storeys, year, amenity — the only source that
  covers ordinary addresses); (b) notable objects within a radius, which for a
  strike often matters more than the struck building itself (a school 80 m away);
  (c) the Wikipedia extract, but only where OSM already carries a wikipedia or
  wikidata tag. The agent would turn those facts into two or three sentences with
  per-fact attribution. The hard constraint, and the reason this is not a
  quick add: an LLM asked "why did this place matter" will confabulate fluently,
  and an invented fact about a destroyed building reads as testimony. The tool
  must therefore return structured facts and an explicit "nothing found", never
  prose. Searching news for the location's own history was considered and
  rejected as a second search loop that would not fit the defence budget.
- **damage severity, in any form.** Three options were considered and all are
  deferred: (a) a derived `severity` column computed by rule from `damage`,
  `killed`, and `structure_type` — cheap and deterministic, but not needed until
  a map exists to colour; (b) vision-LLM grading of photo URLs against a written
  rubric — would have to live in the agent, not the server, to preserve server
  determinism; (c) a trained classifier on xBD/xView2 satellite imagery — blocked
  by domain gap (overhead satellite vs. ground-level night photos), absence of a
  labelled Ukrainian dataset, and the need for sub-metre commercial imagery that
  requires paid authentication. The `damage` and `structure_type` enums already
  captured from article text carry the information this project needs.

## 3. Architecture

```
LangGraph agent (OpenRouter model)
├── MCP #1: Playwright MCP        (stdio, separate process)  — approved server, Part A
└── MCP #2: kyiv-strikes-mcp      (stdio, separate process)  — custom server, Part B
                                   ├─ Nominatim API           (public, no auth)
                                   ├─ kyiv_districts.geojson  (local dataset)
                                   ├─ source_registry.yaml    (local dataset)
                                   └─ incidents.sqlite        (state)
```

Both MCP servers run as separate OS processes over stdio and are startable
independently of the agent.

### 3.1 Key design decision: LLM extraction lives in the agent, not the server

The custom MCP server never receives free-form article text and never calls an
LLM. An agent node reads article text (fetched via Playwright MCP) and emits a
typed `IncidentClaim`; that typed object is what crosses the MCP boundary.

Rationale:

- An `extract(article_text: str) -> dict` tool is exactly what the assignment
  classifies as a thin wrapper: the contract expresses no domain concept and
  would be identical in any project.
- The custom server stays deterministic, so defence-time input variations have
  predictable results.
- The server needs no model credentials and no network beyond Nominatim, which
  keeps it independently startable and secret-free.
- Validation in the server becomes a guardrail *on* the agent's extraction: a
  hallucinated address fails geocoding, a wrong district fails point-in-polygon.

Trade-off: extraction quality now rests entirely on the agent's prompt and the
server cannot repair a bad extraction, only reject an invalid one. Mitigated by a
strict Pydantic schema and a mandatory `quote` field anchoring every claim to a
sentence in the article.

### 3.2 Boundary contract: `IncidentClaim`

```python
class IncidentClaim(BaseModel):
    source_url:       HttpUrl
    published_at:     datetime
    occurred_at:      datetime | None
    location_text:    str = Field(min_length=3, max_length=200)
    claimed_district: KyivDistrict | None      # enum of 10 Kyiv districts
    structure_type:   Literal["residential","school","hospital",
                              "energy","transport","other"]
    killed:           int | None = Field(ge=0, le=1000)
    injured:          int | None = Field(ge=0, le=5000)
    damage:           list[Literal["fire","collapse","facade","crater","vehicles"]]
    media_urls:       list[HttpUrl] = []
    quote:            str          # sentence from the article supporting these fields
```

## 4. Part A — Playwright MCP

**Role.** Source acquisition. The agent navigates whitelisted Ukrainian news and
official sites, runs their on-site search or tag pages for the incident date, and
extracts candidate article URLs, titles, publication dates, and body text.

Playwright is the right server for this because the target sources are ordinary
public web interfaces with no public API: the work is structured interaction with
rendered pages, which is precisely the approved use of this server.

**Safety boundary.** Public pages only, read-only navigation, no credentials
entered, no forms submitted that cause side effects.

**Failure demonstrated:** see section 8, scenario A.

## 5. Part B — custom MCP server `kyiv-strikes-mcp`

Four substantive tools. None is a search or retrieval tool, so the
"at least two beyond retrieval" requirement is met with margin.

### 5.1 `assess_source_credibility`

Classifies candidate articles by trustworthiness and, critically, by
*independence*.

- Input: list of `{url, domain, published_at, byline?, cited_sources?}`
- Processing: looks up the domain in `source_registry.yaml` to assign a tier;
  applies originality heuristics to decide whether the article is original
  reporting or a republication of another source's statement.
- Output per article: `tier` (1–3), `independence_group`, `usable_as_corroborator`
  (bool), `reason`.

Tiers:

| Tier | Meaning | Examples |
|---|---|---|
| 1 | Official / primary | ДСНС, КМВА, Нацполіція, Офіс генпрокурора |
| 2 | Established newsroom, original reporting | Суспільне, Українська правда, hromadske, Reuters, AP, BBC Україна |
| 3 | Aggregator / republisher | requires a link to the primary source |
| — | Excluded | anonymous Telegram, unattributed posts |

**Independence rule.** Two articles that both cite only ДСНС share the
`independence_group` `dsns` and count as one source, not two. This rule is the
single most important piece of domain logic in the project: without it,
"corroborated by five outlets" is meaningless.

**Article relevance rule.** An article is relevant when it is published within
`[event_date, event_date + 14d]`, mentions Kyiv or a Kyiv district, contains
incident markers (влучання, уламки, руйнування, пожежа, загиблі, поранені), and
is reporting rather than opinion or a daily digest.

### 5.2 `resolve_strike_location` — primary data source tool

Turns a free-text Ukrainian location description into validated coordinates.

- Input: `location_text`, optional `claimed_district`
- Processing:
  1. normalizes Ukrainian address forms (`вул.`, `просп.`, `буд.`, genitive street names)
  2. queries Nominatim with a viewbox bounded to Kyiv, rate-limited to 1 req/s,
     with a `User-Agent` carrying contact details per the Nominatim usage policy
  3. runs point-in-polygon against `kyiv_districts.geojson`
- Output: `lat`, `lon`, `district` (geometric working value), `claimed_district`
  (what the article asserted, preserved), `confidence`, `district_conflict`,
  `candidates`, `needs_review`, `review_reason`, `resolved_by`.

Results are cached in SQLite, so a repeated demo run issues no API traffic at all.

**Ambiguity resolution.** When the top two matches are close in importance, the
tool does not silently pick one. It first attempts two cheap deterministic
disambiguations:

1. *District filter* — keep only candidates falling inside a district that some
   article actually names. One survivor resolves the ambiguity
   (`resolved_by: "district_context"`).
2. *Landmark filter* — Nominatim returns neighbourhood names in `display_name`
   ("Шулявка", "Авіаційне селище"). If exactly one candidate's neighbourhood
   token appears in the article quotes, that candidate wins
   (`resolved_by: "landmark_context"`).

If neither resolves it, all candidates are returned with `needs_review: true`.

**Human in the loop.** `district_conflict` and unresolved ambiguity are not
errors and do not silently resolve themselves. The geometric district is kept as
the working value because a map needs a point, but the record is written with
`needs_review = 1` and a plain-language `review_reason`, and the article's own
claim is preserved beside it. Such a record is *recorded, not trusted*: it is
excluded from the verified GeoJSON export until a person clears it via
`python -m agent.review`.

This is a deliberate product decision, not caution for its own sake: in this
domain a confidently misplaced point is a false claim about where a strike
landed, which is worse than an acknowledged gap.

### 5.3 `corroborate_incident_claims` — the feedback engine

Reconciles claim sets from multiple articles into per-field verified values.

- Input: `list[IncidentClaim]` plus the credibility assessment for their sources
- Processing:
  - weights claims by tier
  - collapses claims sharing an `independence_group` into one
  - applies field-level agreement rules
- Output: per-field `{value, status, supporting_sources}` where
  `status ∈ {corroborated, single_source, conflicting, superseded}`; an overall
  `verification_level ∈ {unverified, single_source, corroborated, well_documented}`;
  and `missing_for_next_level` — an explicit list of what the agent must still find.

**Casualty-figure rule.** Later reports of higher casualty counts *supersede*
earlier ones rather than contradicting them (`superseded`). A genuine conflict is
two independent sources of comparable publication time reporting different
numbers (`conflicting`).

`missing_for_next_level` is what makes the workflow agentic rather than a
pipeline: it names the gap, and the agent acts on it.

### 5.4 `upsert_incident`

Writes a reconciled incident to the registry without creating duplicates.

- Input: reconciled incident fields, geocode result, verification result, sources
- Processing: matches against existing incidents by spatial proximity (≤150 m)
  and time window (±6 h); merges into the existing record when matched; refuses
  to overwrite a record that already holds a higher `verification_level`; writes
  one `incident_claims` row per (incident, source, field) with its `quote`.
- Output: `incident_id`, `action ∈ {created, merged, rejected_duplicate,
  rejected_lower_confidence}`, and a diff of changed fields.
- Side effects: writes to `incidents`, `incident_claims`, `incident_media`,
  `sources`. This is the only tool with persistent side effects.

## 6. Agent workflow (LangGraph)

```
parse_request → discover_sources (Playwright) → extract_claims (LLM)
   → assess_source_credibility → resolve_strike_location
   → corroborate_incident_claims
        ├─ verification_level = corroborated ──────→ upsert_incident → report
        ├─ single_source / conflicting ─┐
        └─ district_conflict ───────────┴→ back to discover_sources
                                           targeting a DIFFERENT
                                           independence_group (max 3 rounds)
```

Tool results drive control flow through conditional edges, not merely logging:

- `assess_source_credibility` marking every candidate as one independence group
  forces another discovery round with a different target.
- `corroborate_incident_claims` returning `single_source` plus
  `missing_for_next_level` refines the next Playwright query.
- `resolve_strike_location` returning `district_conflict` blocks the write until
  a resolving source is found or the round budget is exhausted.

The round budget is 3; on exhaustion the incident is written at whatever
`verification_level` was reached, with the gap recorded rather than hidden.

## 7. Data model

SQLite. Map-ready means `incidents` converts to a GeoJSON FeatureCollection with
one SELECT; no PostGIS is warranted at Kyiv-sized volumes.

```sql
CREATE TABLE sources (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  domain TEXT NOT NULL,
  tier INTEGER NOT NULL CHECK (tier IN (1,2,3)),
  independence_group TEXT NOT NULL,
  title TEXT,
  published_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE incidents (
  id INTEGER PRIMARY KEY,
  occurred_at TEXT NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL,
  district TEXT NOT NULL,
  address_text TEXT,
  structure_type TEXT NOT NULL,
  killed INTEGER, injured INTEGER,
  damage TEXT NOT NULL,
  verification_level TEXT NOT NULL CHECK (verification_level IN
      ('unverified','single_source','corroborated','well_documented')),
  geocode_confidence REAL NOT NULL,
  claimed_district TEXT,           -- what the article said, kept beside geometry
  needs_review INTEGER NOT NULL DEFAULT 0,
  review_reason TEXT,              -- plain language, written for a human reader
  reviewed_by TEXT, reviewed_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_incidents_time ON incidents(occurred_at);
CREATE INDEX idx_incidents_bbox ON incidents(lat, lon);

CREATE TABLE incident_claims (
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

CREATE TABLE incident_media (
  id INTEGER PRIMARY KEY,
  incident_id INTEGER NOT NULL REFERENCES incidents(id),
  source_id   INTEGER NOT NULL REFERENCES sources(id),
  url TEXT NOT NULL, caption TEXT,
  UNIQUE(incident_id, url)
);
```

One `incident_claims` row per (incident, source, field) is what makes a conflict
representable at all: two rows, same field, different values, different
independence groups. It also answers the defence requirement to trace one value
from source to final output.

**Ethics.** Aggregate casualty figures from published reporting only. No victim
names, no personal data. Stated in the README.

## 8. Failure scenarios

| # | Broken | Trigger | Behaviour |
|---|---|---|---|
| A (required) | Playwright: 404 or missing selector | navigate to a removed article in the candidate list | tool returns an MCP error; the source is marked `unreachable`; the agent continues with remaining sources; final `verification_level` drops to `single_source`; the report states "1 of 3 sources unreachable" |
| B | Playwright MCP fails to start | kill the process / break the config path | agent catches MCP client initialization failure and stops with a clear message, not a traceback |
| C | `resolve_strike_location` outside Kyiv | "вул. Дерибасівська 1, Одеса" | `OUT_OF_AREA` error, distinguishable from an empty success |

Scenario A is the required Part A demonstration and is the strongest of the
three because the failure visibly changes the final output rather than merely
being caught.

**Error envelope.** Every custom tool returns one of:

```json
{"ok": false, "error": {"code": "OUT_OF_AREA", "message": "...", "retriable": false}}
{"ok": true,  "data": {"matches": []}}
```

Error codes: `OUT_OF_AREA`, `UPSTREAM_UNAVAILABLE` (retriable), `RATE_LIMITED`
(retriable), `SCHEMA_VIOLATION`, `DUPLICATE_REJECTED`.

"No geocoding match" is deliberately **not** an error code: it is a successful
empty result, `{"ok": true, "data": {"matches": []}}`. That distinction is the
requirement being demonstrated, so it must not be blurred by a `NO_MATCH` error.

## 9. Fixtures and offline mode

- **Nominatim:** genuine responses recorded to
  `fixtures/nominatim/<sha1(normalized_query)>.json`. `KSM_OFFLINE=1` swaps only
  the HTTP transport; normalization, parsing, validation, and point-in-polygon
  run through the same code path, as Part D requires.
- **Rate limits:** 1 request/second, contact `User-Agent`, SQLite response cache.
- **Playwright:** not required to have fixtures, but article HTML is saved to
  `fixtures/articles/` and served over `python -m http.server 8899` as defence
  insurance against a site block or connectivity failure. Real HTML through the
  real parser, not a canned answer.

## 10. Repository layout

```
kyiv-strikes-agent/
├─ agent/          graph.py, nodes.py, schemas.py, prompts/
├─ mcp_server/     server.py, tools/, geo.py, corroboration.py, db.py
├─ data/           kyiv_districts.geojson, source_registry.yaml
├─ fixtures/       nominatim/, articles/
├─ docs/           tool_contracts.md, design_rationale.md, defence_checklist.md
├─ tests/          test_corroboration.py, test_dedup.py, test_geo.py
├─ .env.example
└─ README.md
```

No secrets in the repository. `OPENROUTER_API_KEY` and the Nominatim contact
string come from the environment; `.env.example` documents them.

## 11. Build order

1. Data: `kyiv_districts.geojson`, `source_registry.yaml`
2. MCP server: `db.py`, `upsert_incident`, `resolve_strike_location` with
   fixtures — testable standalone, before any agent exists
3. `assess_source_credibility`, `corroborate_incident_claims`, unit tests for the
   reconciliation and dedup rules
4. Standalone Playwright MCP probe: confirm whitelisted sites actually serve content
5. LangGraph graph, linear first
6. Conditional edges: the re-search loop
7. Failure scenarios and offline mode
8. Contract documentation, defence checklist, timed dry run

## 12. Defence checklist mapping

| Required demo step | Covered by |
|---|---|
| 1. custom server started independently | `python -m kyiv_strikes_mcp` in its own terminal |
| 2. agent discovers both connections | startup prints both tool lists |
| 3. existing server tool invoked | Playwright fetches candidate articles |
| 4. its result affects a later step | credibility assessment reroutes the graph |
| 5. explain its contract and role | `browser_navigate` walkthrough |
| 6. complete custom-server workflow | full run through `upsert_incident`, row shown in DB |
| 7. at least three custom tools exposed | four listed at discovery |
| 8. explain one custom contract | `corroborate_incident_claims` and `missing_for_next_level` |
| 9. realistic failure | scenario A |
| variation + invalid input + value tracing | different date; out-of-area address; killed count traced DB → `incident_claims.quote` → article |

## 12a. Open decision: where the model comes from

Unresolved as of 2026-08-22; the instructor was asked for course OpenRouter
access and is expected to answer 2026-08-23. The agent needs a model to read
articles; the MCP server needs none and runs without any of this.

The endpoint, key, and model name are three environment variables, so this
decision changes configuration only — no code. Three viable options:

| Option | Cost | Work | Structured output |
|---|---|---|---|
| OpenRouter, course key | none | none | native |
| Google AI Studio (Gemini) | free tier | none | native |
| Claude Agent SDK | covered by an existing Claude subscription | ~30 lines for one node | must be parsed and validated, with a retry |

The assignment explicitly sanctions all three: it provides OpenRouter, names
Gemini and Anthropic credentials as acceptable alternative model paths, and asks
only that the configuration be documented. Whatever is chosen goes in the README.

Driving the Claude Code CLI as a subprocess was considered and rejected: the
official SDK exists for this, and scripting the CLI as a general-purpose text
API is a grey area in its terms.

## 13. Known limitations

- Extraction quality depends on the agent prompt; the server can reject invalid
  claims but cannot repair a poor extraction.
- Nominatim resolves Kyiv addresses unevenly; damaged or demolished buildings may
  have no entry, so `NO_MATCH` with district-centroid fallback at low confidence
  is expected for some incidents.
- The 150 m / ±6 h dedup window is a hand-tuned heuristic, not derived from data.
  Two genuinely distinct impacts on one large site may merge.
- `source_registry.yaml` is hand-curated; its tier assignments are editorial
  judgements and are documented as such.
- Sources sometimes report a district without a street address; those incidents
  land at district centroid with `geocode_confidence` near zero and are not
  map-precise.
