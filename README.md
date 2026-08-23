# Kyiv Strikes Incident Registry

An agent that turns one question about a strike on Kyiv into verified,
geolocated, provenance-tracked incident records.

Given *"удар по Києву 17 червня 2026"* it looks through two Ukrainian
newsrooms in a real browser — hromadske by its search, Suspilne through its
Kyiv section — picks the headlines that promise a strike on the city, extracts
what each article claims, works out
how many genuinely independent sources are behind those claims, geolocates every
address it finds and checks each against real district boundaries, reconciles
the numbers, and writes the night as one record with one row per impact site —
each carrying the sentence that supports every value.

City-wide casualty figures are stored once, on the night. Per-address figures
are stored on the address, and only when an article says so. Mixing the two
would turn "five killed in Kyiv" into fifteen the moment three sites were hit.

Anything it cannot settle on evidence is written but held back for a person.

## Architecture

```
LangGraph agent  ──┬── Playwright MCP     (separate process)  finds and reads articles
                   └── kyiv-strikes MCP   (separate process)  the domain work
                                           ├── Nominatim          public API, no key
                                           ├── kyiv_districts.geojson
                                           ├── source_registry.yaml
                                           └── incidents.sqlite
```

The custom server never sees article text and never calls a model. The agent
extracts a typed `IncidentClaim`; only that crosses the MCP boundary. The server
is therefore deterministic, needs no model credentials, and starts on its own.

## Prerequisites

- Python 3.14
- Node 24 (Playwright MCP runs through `npx`)
- `npx playwright install chromium`

## Installation

```bash
python -m pip install -r requirements.txt
cp .env .env      # then fill it in
```

## Configuration

| Variable | Used by | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | agent | Model access. The MCP server never reads it. |
| `OPENROUTER_BASE_URL` | agent | Any OpenAI-compatible endpoint. |
| `OPENROUTER_MODEL` | agent | Model id; must support structured output. |
| `NOMINATIM_CONTACT` | server | A real contact address, required by the Nominatim usage policy. |
| `KSM_OFFLINE` | server | `1` replays recorded Nominatim responses. |
| `KSM_DB_PATH` | server | Incident database path. |

`.env.example` lists working settings for OpenRouter, a Gemini
OpenAI-compatible endpoint, and a local Ollama. Nothing but configuration
changes between them.

Playwright MCP is pinned in `mcp_config.json` to `@playwright/mcp@0.0.79`, not
`@latest`, so the demo cannot change under you between rehearsal and defence.

## Running

Start the custom MCP server on its own, in its own terminal:

```bash
PYTHONUTF8=1 KSM_OFFLINE=1 NOMINATIM_CONTACT="you@example.com" python -m mcp_server
```

Then run the agent:

```bash
PYTHONUTF8=1 python -m agent "удар по Києву 17 червня 2026"
```

Review what the agent could not settle, and sign off on it:

```bash
python -m agent.review
python -m agent.review ok 3 --by taras
python -m agent.review ok 1 --by taras --kind attack
python -m agent.review export > incidents.geojson
```

## Checking things work

```bash
python -m pytest -q                     # 181 tests
python scripts/probe_playwright.py      # are the news sites reachable today?
python scripts/probe_search.py          # does each outlet's search still work?
python scripts/demo_failures.py         # the three failure scenarios
python scripts/debug_extraction.py      # what the model does with one real article
```

## Regenerating the data

Both are committed; you only need these if you want to rebuild them.

```bash
python scripts/fetch_districts.py                        # Kyiv district polygons from OSM
NOMINATIM_CONTACT=you@example.com \
  python scripts/record_fixture.py "вул. Освіти, 4, Київ"  # record a geocoding fixture
```

Fixtures are genuine recorded responses. Never hand-edit them: an edited fixture
is a hard-coded answer, and `KSM_OFFLINE=1` swaps only the transport — parsing,
validation, and the point-in-polygon check run identically either way.

## Data and ethics

The registry holds aggregate casualty figures drawn from published reporting. It
contains no victims' names and no personal data. Photographs are referenced by
URL and attributed to their source; no image files are downloaded or
redistributed. Two official sites answer automated requests with a
bot-protection wall; that is respected rather than circumvented, and the
consequence is documented in `data/source_registry.yaml`.

## Documentation

- [`docs/tool_contracts.md`](docs/tool_contracts.md) — full contract for every custom tool, and the Playwright tool used
- [`docs/design_rationale.md`](docs/design_rationale.md) — why each boundary sits where it does, and what it cost
- [`docs/defence_checklist.md`](docs/defence_checklist.md) — the demonstration script
- [`docs/superpowers/specs/`](docs/superpowers/specs/) — the design spec
