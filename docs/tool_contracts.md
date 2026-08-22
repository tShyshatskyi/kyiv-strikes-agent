# Tool contracts

Schemas below are the ones the server actually publishes, read back over MCP
with `list_tools`. Model-facing descriptions are the exact docstrings exposed
through the protocol.

Every custom tool answers in one of exactly two shapes:

```json
{"ok": true,  "data": {...}}
{"ok": false, "error": {"code": "...", "message": "...", "retriable": false}}
```

An empty result is a success with an empty payload, never an error. Error codes:
`OUT_OF_AREA`, `UPSTREAM_UNAVAILABLE` (retriable), `RATE_LIMITED` (retriable),
`SCHEMA_VIOLATION`, `DUPLICATE_REJECTED`.

---

## 1. `assess_source_credibility`

| | |
|---|---|
| **Name** | `assess_source_credibility` |
| **Purpose** | Decide how much each candidate article is worth as evidence, and — the part that matters — how many genuinely independent voices are behind a set of articles. Call it before corroborating anything. |

**Model-facing description** (exact):

> Rate candidate articles by source tier and, above all, by independence.
>
> Call this BEFORE corroborating claims. Five outlets reporting a strike usually means one press release and four reprints; counting them as five confirmations is the easiest way to produce a confidently wrong record. An article that merely relays another source is grouped with that source. The exception: an article that also reports a fact the original never gave got it somewhere of its own, and counts as a separate voice.
>
> Returns per article: tier (1 official, 2 newsroom, 3 aggregator), independence_group, whether it may be used as corroboration, anything it added, and the reason for the verdict. Side effects: none.

**Input schema**

| Field | Type | Required | Constraints |
|---|---|---|---|
| `sources` | array of `SourceRef` | yes | each: `url` (URL), `domain` (string), `published_at` (datetime), `byline` (string, optional), `cited_sources` (array of domain strings) |
| `event_date` | string | yes | `YYYY-MM-DD` |
| `relevance_window_days` | integer | no | 1–365, default 14 |

**Output schema** — `data.assessments`, one object per input article:

| Field | Type | Meaning |
|---|---|---|
| `url` | string | the article |
| `tier` | 1–3 or null | the outlet's own standing |
| `voice_tier` | 1–3 or null | the standing of whoever the fact belongs to |
| `independence_group` | string or null | the reconciliation key; shared means one voice |
| `usable_as_corroborator` | bool | may this count as evidence |
| `adds` | array of field names | what it reported that the source it relays did not |
| `reason` | string | why, in one sentence |

**Error conditions** — `SCHEMA_VIOLATION` for an unparseable `event_date` or a
`relevance_window_days` outside 1–365. Unknown and excluded domains are *not*
errors: they come back as `usable_as_corroborator: false` with a reason.

**Side effects** — none.

**Example**

```json
// in
{"sources": [
   {"url": "https://dsns.gov.ua/n/1", "domain": "dsns.gov.ua",
    "published_at": "2026-06-17T08:00:00Z"},
   {"url": "https://suspilne.media/n/1", "domain": "suspilne.media",
    "published_at": "2026-06-17T09:00:00Z", "cited_sources": ["dsns.gov.ua"]}],
 "event_date": "2026-06-17"}

// out
{"ok": true, "data": {"assessments": [
  {"url": "https://dsns.gov.ua/n/1", "tier": 1, "voice_tier": 1,
   "independence_group": "dsns", "usable_as_corroborator": true, "adds": [],
   "reason": "Tier 1 original reporting."},
  {"url": "https://suspilne.media/n/1", "tier": 2, "voice_tier": 1,
   "independence_group": "dsns", "usable_as_corroborator": true, "adds": [],
   "reason": "Tier 2, but relays dsns and reports nothing beyond it — counted as that source, not a second one."}]}}
```

Two articles in, one voice out. That is the whole point of the tool.

---

## 2. `resolve_strike_location` — the primary data-source tool

| | |
|---|---|
| **Name** | `resolve_strike_location` |
| **Purpose** | Turn an address as an article writes it into coordinates, and check those coordinates against the real boundaries of Kyiv's ten districts. |

**Model-facing description** (exact):

> Geocode a Kyiv location and check it against real district boundaries.
>
> Returns coordinates, the district the point actually falls inside, and a confidence reflecting how precisely the address matched — an exact building scores higher than a street with no house number.
>
> Three outcomes must not be confused:
>   - matched cleanly: needs_review is false, use it;
>   - matched nothing: a SUCCESS with matches: [] — keep looking elsewhere;
>   - matched a place outside Kyiv: an OUT_OF_AREA error — the article is probably not about Kyiv, or the wrong address was taken from it.
>
> Where the article's district disagrees with the geometry, or several distinct places match and the reporting cannot separate them, the tool sets needs_review with a plain-language reason instead of guessing. Side effects: none (a network call to Nominatim unless running offline).

**Input schema**

| Field | Type | Required | Constraints |
|---|---|---|---|
| `location_text` | string | yes | 3–200 characters |
| `claimed_district` | string or null | no | default null; used to detect disagreement, never to override geometry |
| `context_districts` | array of strings or null | no | default null; only used to break a tie |
| `context_text` | string | no | default `""`; only used to match a neighbourhood name |

**Output schema**

| Field | Type | Meaning |
|---|---|---|
| `lat`, `lon` | float or null | null when nothing matched |
| `district` | string or null | the district the point is geometrically inside |
| `claimed_district` | string or null | what the article said, preserved beside it |
| `chosen_name` | string or null | the matched place, as Nominatim names it |
| `confidence` | 0.0–1.0 | 0.90 exact building, 0.65 business at that address, 0.30 street only |
| `district_conflict` | bool | article and geometry disagree |
| `matches` | array of strings | every candidate considered, best first |
| `candidates` | array of objects | populated only when ambiguity was left unresolved |
| `needs_review` | bool | a person must settle this |
| `review_reason` | string or null | plain language, written for a human |
| `resolved_by` | string or null | `district_context`, `landmark_context`, or null |

**Error conditions**

| Code | When |
|---|---|
| `OUT_OF_AREA` | the best match is outside every Kyiv district |
| `UPSTREAM_UNAVAILABLE` | Nominatim unreachable or returned a non-JSON body |
| `RATE_LIMITED` | Nominatim returned HTTP 429 |
| `SCHEMA_VIOLATION` | `location_text` shorter than 3 or longer than 200 characters |

**Side effects** — none persisted. One outbound Nominatim request, rate-limited
to 1/second with a contact `User-Agent`, unless `KSM_OFFLINE=1`.

**Example**

```json
// in
{"location_text": "вул. Освіти, 4, Київ", "claimed_district": "Оболонський"}

// out
{"ok": true, "data": {
  "lat": 50.4265827, "lon": 30.4641512,
  "district": "Солом'янський", "claimed_district": "Оболонський",
  "chosen_name": "4, вулиця Освіти, Авіаційне селище, ...",
  "confidence": 0.9, "district_conflict": true,
  "candidates": [], "needs_review": true, "resolved_by": null,
  "review_reason": "The reporting places this at Оболонський, but the coordinates for 'вул. Освіти, 4, Київ' fall inside Солом'янський. The map district is used as the working value; a person should confirm which is right."}}
```

---

## 3. `corroborate_incident_claims`

| | |
|---|---|
| **Name** | `corroborate_incident_claims` |
| **Purpose** | Reconcile what several articles claim into one set of values, each labelled with how well it is supported, plus an explicit list of what is still missing. |

**Model-facing description** (exact):

> Reconcile what several articles claim into one verified set of values.
>
> Articles sharing an independence_group are collapsed into one voice. A casualty count that rises over time is treated as being completed, not contradicted — the later figure supersedes. A genuine contradiction is disagreement at the same time, when neither source could know more; that is flagged for a human rather than silently averaged away.
>
> Returns per-field value and status, an overall verification_level, and missing_for_next_level — an explicit list of what is still absent. Act on that list: it tells you what to search for next. Side effects: none.

**Input schema**

| Field | Type | Required |
|---|---|---|
| `claims` | array of `IncidentClaim` | yes |
| `assessments` | array of `SourceAssessment` | yes |

`IncidentClaim`: `source_url`, `published_at`, `occurred_at?`, `location_text`
(3–200), `claimed_district?` (one of the ten), `structure_type` (enum),
`killed?` (0–1000), `injured?` (0–5000), `damage[]` (enum), `media_urls[]`,
`quote` (10–1000 characters, required).

**Output schema**

| Field | Type | Meaning |
|---|---|---|
| `fields` | object | per field: `value`, `status`, `values_seen`, `supporting_sources` |
| `has_conflict` | bool | any field is contradictory |
| `verification_level` | enum | `unverified` / `single_source` / `corroborated` / `well_documented` |
| `missing_for_next_level` | array of strings | what would raise it |
| `usable_groups` | array of strings | voices that counted |
| `needs_review` | bool | something needs a person |
| `review_reason` | string or null | plain language |

Field `status`: `corroborated` (two voices agree), `single_source` (one voice),
`superseded` (a later report replaced an earlier figure), `conflicting`
(disagreement at the same time — impossible to reconcile automatically).

**Error conditions** — `SCHEMA_VIOLATION` when a claim fails validation, e.g. a
`quote` under 10 characters or a district outside the ten. Zero usable sources
is not an error: it is `verification_level: "unverified"`.

**Side effects** — none.

**Example**

```json
// in: two independent voices, the later one higher
{"claims": [
   {"source_url": "https://suspilne.media/n/1", "published_at": "2026-06-17T06:00:00Z",
    "location_text": "вул. Освіти, 4", "structure_type": "residential", "killed": 3,
    "damage": ["fire"], "quote": "Загинули троє людей унаслідок влучання."},
   {"source_url": "https://www.pravda.com.ua/n/1", "published_at": "2026-06-17T18:00:00Z",
    "location_text": "вул. Освіти, 4", "structure_type": "residential", "killed": 5,
    "damage": ["fire"], "quote": "Кількість загиблих зросла до пʼяти."}],
 "assessments": [
   {"url": "https://suspilne.media/n/1", "tier": 2, "voice_tier": 2,
    "independence_group": "suspilne", "usable_as_corroborator": true},
   {"url": "https://www.pravda.com.ua/n/1", "tier": 2, "voice_tier": 2,
    "independence_group": "pravda", "usable_as_corroborator": true}]}

// out
{"ok": true, "data": {
  "fields": {"killed": {"value": 5, "status": "superseded", "values_seen": [3, 5],
                        "supporting_sources": ["https://suspilne.media/n/1",
                                               "https://www.pravda.com.ua/n/1"]}},
  "has_conflict": false, "verification_level": "corroborated",
  "missing_for_next_level": ["An official (tier 1) source to reach well_documented."],
  "usable_groups": ["pravda", "suspilne"],
  "needs_review": false, "review_reason": null}}
```

---

## 4. `upsert_incident`

| | |
|---|---|
| **Name** | `upsert_incident` |
| **Purpose** | Write a reconciled incident to the registry, merging with an existing one rather than duplicating it, and record which article supports each value. |

**Model-facing description** (exact):

> Write a verified incident to the registry, merging rather than duplicating.
>
> Matches an existing incident within 150 m and 6 hours and merges into it, so one event reported five ways stays one row. Refuses to overwrite a record that already rests on stronger evidence. Stores one provenance row per (incident, source, field) carrying the sentence that supports the value, so any number can be traced back to the article it came from.
>
> SIDE EFFECTS: this is the only tool that writes. It modifies the incidents, incident_claims, incident_media, and sources tables. An incident written with needs_review stays out of the verified map export until a person signs off on it.

**Input schema**

| Field | Type | Required |
|---|---|---|
| `incident` | `IncidentRecord` | yes |
| `claims` | array of `IncidentClaim` | yes |
| `assessments` | array of `SourceAssessment` | yes |
| `reconciliation` | object | yes — the output of `corroborate_incident_claims`, passed through |

`IncidentRecord` is constrained, not a free dict: `lat` 50.20–50.60, `lon`
30.15–30.85 (a bounding box around Kyiv), `district` one of the ten,
`structure_type` and `verification_level` enums, `geocode_confidence` 0.0–1.0,
`killed` 0–1000, `injured` 0–5000.

**Output schema**

| Field | Type | Meaning |
|---|---|---|
| `incident_id` | integer | the row |
| `action` | enum | `created`, `merged` |
| `changed_fields` | array of strings | what a merge altered |
| `needs_review` | bool | held back from the verified export |

**Error conditions**

| Code | When |
|---|---|
| `DUPLICATE_REJECTED` | a matching incident already rests on stronger evidence; nothing is written |
| `SCHEMA_VIOLATION` | coordinates outside the Kyiv box, an unknown district, a bad enum |

**Side effects** — writes to `incidents`, `incident_claims`, `incident_media`,
`sources`. The only tool with persistent effects. A record written with
`needs_review: true` is excluded from `to_geojson()` until cleared through
`python -m agent.review`.

**Example**

```json
// out, after a second article about the same impact
{"ok": true, "data": {"incident_id": 1, "action": "merged",
                      "changed_fields": ["killed"], "needs_review": false}}
```

---

## The existing server: Playwright MCP

Pinned to `@playwright/mcp@0.0.79`, started over stdio by `mcp_config.json`.
It exposes 24 tools; this project uses two of them.

### `browser_navigate`

| | |
|---|---|
| **Purpose in this project** | Open a whitelisted newsroom's search page for the incident date, then open each candidate article. |
| **Input** | `url` (string, required). |
| **Output** | An empty text result on success — the page state is read separately. |
| **Errors** | Navigation to an unreachable, removed, or blocked page raises through the MCP client. Two official sites answer with HTTP 403 behind a bot-protection interstitial; that is a *successful* navigation returning a wall, so it must be detected by inspecting the page, not by catching an error. |
| **Side effects** | Real: it drives a browser. Read-only here — public pages, no credentials, no form submissions. Playwright writes trace and console logs under `.playwright-mcp/`, which is gitignored. |

### `browser_snapshot`

| | |
|---|---|
| **Purpose in this project** | Read the current page as an accessibility tree, which is where article links and article text are extracted from. |
| **Input** | None. |
| **Output** | Text: page URL, title, HTTP status, and a YAML tree with each link's `/url:`. |
| **Errors** | Returns a near-empty snapshot of `about:blank` if nothing has been navigated to. |
| **Side effects** | None. |

**Why these two are stateful, and why that shaped the code.** `browser_snapshot`
reports whatever `browser_navigate` last opened. The two calls must therefore
share one MCP session. The convenience path in `langchain-mcp-adapters` opens a
fresh connection per tool call — harmless for the four stateless custom tools,
and silently wrong here: navigate would open a browser, load the page, and close
it, and the following snapshot would get a new blank browser reporting
`about:blank`. `agent/mcp_clients.py::open_tools` holds one session per server
open for the whole run for exactly this reason.
