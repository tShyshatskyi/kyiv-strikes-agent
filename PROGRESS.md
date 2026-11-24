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
- 11:00 full run stopped at 87% of the 5-hour window; resumed 12:36 from cache.
- 12:40 Wikipedia added as an outside check (pipeline/wikipedia.py). The API
  refuses scripts without a contact address, so the snapshot
  (data/wikipedia_snapshot.json, 31 records from 3 articles, revision ids kept)
  was taken through a browser; set WIKIMEDIA_CONTACT to refresh it from code.
  It never counts as a witness; it lines up nights and death tolls.
- 13:00 site rebuilt in the Fall in Kyiv scheme (site/index.html, how.html,
  style.css, common.js). Photos: 8 picked by the strict rule, 1 rejected by eye
  (2026-10-31-3: trucks, not a homestead) → data/photo_blocklist.txt.
- 13:10 live at https://kyiv-strike-registry.vercel.app (Vercel project
  kyiv-strike-registry, scope taras-projects3), 14 nights so far.

- 13:30 full run done at 84% of the window: 54 nights, 641 claims (39 quotes
  rejected), 341 places, 85 audits, 33 nights sent back, 14 places removed by
  the auditor, 43 still flagged. 117 confirmed / 162 official / 62 one source.
  Wikipedia: 21 nights, all in the registry, 17 agree, 4 tolls differ.
  Photos: 30 picked, 9 rejected by eye, 21 published. Redeployed.

## Resume
1. `PYTHONUTF8=1 KSR_SKIP_COLLECT=1 python -W ignore -m pipeline > data/full_run.log 2>&1`
   (cached work is free; watch the 5-hour window, stop at 90%).
2. If the run was stopped: publish the finished nights only:
   `python -m pipeline $(grep finalize data/full_run.log | awk '{print $3}' | sort)`.
3. `python -m pipeline.build_site`, look at new photos in site/media, add bad
   site ids to data/photo_blocklist.txt, rebuild.
4. `cd site && npx vercel deploy --prod --yes --scope taras-projects3`.

## Open questions
- Recall looks low on some nights (14 Oct: 32 posts → 2 claims while Wikipedia
  lists 3 killed, 21 injured). The Wikipedia check on the page shows where.

## 2026-11-24 – map-first site
- index.html is now a full-screen map: filters float on top, swipeable place cards along the bottom (card ↔ marker sync, sources sheet), night timeline + replay under the map.
- Other blocks moved to their own pages in the header: patterns.html, wikipedia.html, records.html (how.html unchanged). Shared header/filters/strip helpers live in common.js.
- Deep links: ./#night=YYYY-MM-DD and ./#site=ID open the map on that night/place; records.html#ID opens a record.
- District-only places are spread in a small spiral around their district point so stacked rings stay visible.
