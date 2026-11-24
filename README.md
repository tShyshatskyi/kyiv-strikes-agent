# Kyiv Strike Registry

A multi-agent workflow that reads every public report of Russian strikes on
Kyiv, works out which reports describe the same place, has a second agent audit
that work, and publishes a map in which every record links to the exact words
of the people who reported it.

**Live page:** `site/index.html` (static; see *Run it* below).
Period covered: 1 October – 23 November 2026.

## What it does

```
collect ─► filter ─┬─► read ─► check quotes ─► match ─► audit ──approve──► finalize ─┬─► geocode ─► publish
 (code)    (code)  │  agent      (code)        agent    agent                 (code)  │    (code)     (code)
                   │                             ▲        │                          │
                   │                             └─revise─┘                          │
                   └──────────── one branch per day, all days in parallel ───────────┘
```

| Step | Who | What |
|---|---|---|
| Collect | code | Reads 13 public Telegram channels (city administration, mayor, emergency service, six newsrooms, two aggregators) through the t.me/s web preview. No login, no API key. |
| Filter | code | Keeps posts that mention damage in the city itself (not Kyiv oblast). Parses the city's alert / all-clear posts into an alert timeline without any model. |
| Read | **reader agent** (Claude Haiku) | One claim per place per post: what was hit, where, weapon, casualties, and a verbatim quote. |
| Check quotes | code | Every quote is compared with its post character by character. A paraphrase or an invented detail is thrown out. |
| Match | **matcher agent** (Claude Sonnet) | Groups claims into distinct places, so a strike reported by six channels is counted once. |
| Audit | **auditor agent** (Claude Sonnet) | A separate agent hunts for the matcher's mistakes: double counts, wrong merges, statements no source makes, places outside the city. If it finds any, the graph sends the work back to the matcher with its notes. |
| Finalize | code | Enforces what prompts only ask for: every claim used exactly once, nothing lost, places the auditor ruled out of area removed. Counts **independent voices** (two channels of one newsroom are one voice) and takes the latest casualty figure rather than adding updates together. |
| Geocode | code | Nominatim, then a point-in-polygon check against real Kyiv district boundaries. Reports that give only a district are drawn as rings. |
| Publish | code | `data/out/registry.json`, the agents' trace `data/out/trace.jsonl`, and the site. |

The edge out of *audit* is what makes this an agent rather than a script: a
tool result (the auditor's verdict) decides whether work is published or redone.

### Design choices worth knowing

- **Agents judge, code checks.** Every rule that can be checked mechanically is
  (quotes, independence, figures, geography), so a model's mistake is caught by
  something that cannot make the same mistake.
- **The auditor has no stake in the answer.** It is a separate call with its own
  prompt, and it sees the matcher's output as someone else's work.
- **Independence, not volume.** Confirmation counts voices, not channels.
  Aggregators are collected but never count as confirmation.
- **Cached and resumable.** Every model answer is cached by a hash of its
  prompt; a rerun costs nothing for work already done.

## Run it

```bash
python -m pip install -r requirements.txt
python -m pipeline                 # the whole workflow
python -m pipeline 2026-10-19      # one day, for a quick look
python -m pipeline --graph         # print the graph as Mermaid
python -m pipeline.build_site      # site/data.js from the registry
python -m http.server -d site      # open http://localhost:8000
```

Model access: by default the agents run through the Claude Code CLI in headless
mode (`claude -p`, structured output, a lean session with no tools). To use any
OpenAI-compatible endpoint instead:

```bash
LLM_BACKEND=openai OPENAI_BASE_URL=https://openrouter.ai/api/v1 OPENAI_API_KEY=... \
KSR_READER_MODEL=... KSR_MATCHER_MODEL=... KSR_AUDITOR_MODEL=... python -m pipeline
```

## Layout

```
pipeline/            v2 workflow
  graph.py           the LangGraph graph (top level + per-day subgraph)
  agents.py          reader, matcher, auditor
  prompts/           one prompt file per agent
  schemas.py         the JSON each agent must answer in
  tools.py           deterministic checks: quotes, voices, figures, geocoding
  collect.py         Telegram collector
  prefilter.py       damage filter and alert timeline
  build_site.py      registry → site/data.js
site/                the page
data/out/            registry.json, trace.jsonl
mcp_server/, agent/  v1: the course version (MCP server + LangGraph agent); see docs/README_v1_course.md
```

## Limits

- Telegram reports are what they are: early, sometimes corrected later. Casualty
  figures are as reported at the time.
- Most official posts name a district, not an address, so many places are mapped
  to their district only (drawn as rings).
- The auditor is a model too. It catches a lot, but what it still disputes after
  one revision is published with its note attached rather than silently fixed.

## History

v1 was a course project (MCP server + LangGraph agent, one date per run, two
newsrooms via a headless browser). It worked as a demonstration but found little:
see `PROGRESS.md` for why, and for what v2 changed.
