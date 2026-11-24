# v2 rebuild — progress log (branch `v2-pipeline`)

Goal (Taras, 2026-11-24): turn the course project into a working, good-looking
agentic workflow to show employers. Results over academic rigour. Period
1 Oct – 23 Nov 2026, page in English.

## Why v1 gave poor results (diagnosis, 2026-11-24)
- Discovery read only two outlets: hromadske search and Suspilne's latest-news
  tag feed. A feed shows today, so for any past date there was nothing to find.
- One date per run, several minutes each, through a real headless browser.
- The OpenRouter key in `.env` returns 401, so the extraction model could not run.
- Telegram was excluded as evidence, though the city administration's channel is
  where nearly every impact is first reported officially.
- Whole database after all runs: 1 attack, 4 sites, 3 of them from 2025 articles.
  No visual output at all.

## v2 design
`python -m pipeline` runs a LangGraph graph:
collect (13 Telegram channels, t.me/s previews) → prefilter (code) →
per day in parallel: reader agent (Haiku) → quote check (code) → matcher agent
(Sonnet) → auditor agent (Sonnet) → [revise → matcher, at most once] → finalize
(code: voices, latest figures, every claim used once) → geocode (Nominatim +
district polygons) → publish (data/out/registry.json, trace.jsonl).
`python -m pipeline.build_site` → site/data.js; site/index.html is the page.

LLM backend: `claude -p` (headless Claude Code CLI) with a JSON schema, in a lean
session (own system prompt, no tools/MCP/skills), cached on disk by prompt hash.
`LLM_BACKEND=openai` + OPENAI_BASE_URL/OPENAI_API_KEY for any other endpoint.

## Lessons
- The CLI loads the user's whole environment (plugins, skills, hooks) into every
  call: up to 58k extra tokens and random "prompt too long" failures. Fixed with
  --system-prompt, --strict-mcp-config, --disable-slash-commands.
- Thinking on the reader: 3x slower and 5x dearer for transcription work. Off.
- Taras is on Pro: one heavy day cost ≈ $1.4 of quota. Aggregators (Trukha,
  Real Kyiv) are collected but not read; matcher thinking off; one revision max.

## Log
- 10:30 collected 25,512 posts from 13 channels (Oct 1 – Nov 24 morning).
- 10:55 first test day (18 Oct): 85 posts → 28 claims, 2 quotes rejected,
  matcher 9 sites, auditor caught a double count, matcher regrouped → 8.
