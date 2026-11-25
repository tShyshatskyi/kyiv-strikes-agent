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
Next up: **stage 1a** of `docs/review/plan.md` — `segment_attacks`, `validate`,
auditor `public_note_en`, 2 revision rounds, then rerun ONLY the nights of
6–7 Oct, 7–8 Oct, 6–7 Nov, 16–17 Nov, 19–20 Nov and check the pass criteria in
the brief. Verify `claude -p` bills the second account with one cheap call
before any big run. Stage 0 is done and deployed (docs/review/reports/stage-0.md).

1. `PYTHONUTF8=1 KSR_SKIP_COLLECT=1 python -W ignore -m pipeline > data/full_run.log 2>&1`
   (cached work is free; watch the 5-hour window, stop at 90%; October and
   November in separate windows).
2. If the run was stopped: publish the finished nights only:
   `python -m pipeline $(grep finalize data/full_run.log | awk '{print $3}' | sort)`.
3. `python -m pipeline.recompute` after any change to a confirmation or map
   rule — re-derives levels and points with no model call.
4. `python -m pipeline.build_site`, look at new photos in site/media, add bad
   site ids to data/photo_blocklist.txt, rebuild. The build prints any public
   description that still carries agent notes; that count must stay 0.
5. `cd site && npx vercel deploy --prod --yes --scope taras-projects3`.
6. `pytest tests/test_pipeline_rules.py` guards the rules the review caught.
   Backup of the pre-stage-0 data: `data/out_v2_backup/` (not committed).

## Open questions
- Recall looks low on some nights (14 Oct: 32 posts → 2 claims while Wikipedia
  lists 3 killed, 21 injured). The Wikipedia check on the page shows where.
- The 09:00 cut-off in `prefilter.attack_day()` is the root cause of the
  duplicates, the double-counted casualties and the date confusion. Stage 1
  replaces it with alert-based attack windows.

## 2026-11-24 – map-first site
- index.html is now a full-screen map: filters float on top, swipeable place cards along the bottom (card ↔ marker sync, sources sheet), night timeline + replay under the map.
- Other blocks moved to their own pages in the header: patterns.html, wikipedia.html, records.html (how.html unchanged). Shared header/filters/strip helpers live in common.js.
- Deep links: ./#night=YYYY-MM-DD and ./#site=ID open the map on that night/place; records.html#ID opens a record.
- District-only places are spread in a small spiral around their district point so stacked rings stay visible.

## 2026-11-25 – stage 0 of the review plan (no model calls)
A harsh review of the site is in `docs/review/critique.md` and the fix plan in
`docs/review/plan.md`; the report for this stage is
`docs/review/reports/stage-0.md`. The site no longer says anything the data
cannot support, though the duplicates themselves are still there.
- 00:10 confirmation rules rewritten. City hall's three channels are one voice,
  the emergency service is its own, a newsroom counts only where its quote adds
  a figure or name no earlier official post gave, and an open auditor flag makes
  a place `disputed`. Recomputed with `python -m pipeline.recompute`, no model
  call: confirmed 117 → 56, official 162 → 182, media 62 → 60, disputed 0 → 43.
- 00:20 Wikipedia check rewritten. `night_of()` used to move the date to
  whichever day already had places, which made "0 nights missing" a property of
  the code; it is gone. Dates are fixed in advance, the kinds of place named are
  compared too, and there are four verdicts. 17 of 21 "agree" → 5 agree,
  8 differ, 9 nothing to compare, 0 missing, over 22 dates.
- 00:30 map stopped inventing addresses. `tools.district_point()` scattered the
  274 district-only places at random inside their district, which read as 272
  real addresses; now the district is shaded by count and only the 44 places
  with a matched address get a point.
- 00:35 casualties out of the hero (46 / 352 cannot be defended while one attack
  is filed under two dates), weekday chart removed, auditor notes folded into a
  grey toggle, 9 public descriptions trimmed of agent notes by code, alert hours
  347 → 343 (alerts outside the period), "city council" → "Kyiv City State
  Administration", "eight newsrooms" → seven, "$23 API-equivalent cost", and a
  data-as-of line with a warning that Telegram posts can be edited.
- `tests/test_pipeline_rules.py`: 18 tests pinning each of these rules. Every
  page is also executed against a stubbed DOM and Leaflet before deploy.

## 2026-11-25 – review notes on stage 0 (five OPEN items)
- English-language media no longer "adds detail" by being in English: an
  English quote counts only its numbers until `translate` exists; clock times
  and the post's own date numbers never count; Ukrainian words are compared by
  stem against every word of the official quotes, and district names are
  treated as common. Confirmed 56 → 43; 16 of the 43 rest on a media voice,
  each with a real detail (a named institute, an embassy, "built in 1798").
- "Disputed" now means only `wrong_merge` / `not_this_attack` / `not_kyiv_city`
  (`tools.DISPUTING`); other open flags keep the evidence level plus a small
  "auditor note" marker. Flag kinds are kept in `flag_kinds` from now on and
  recovered from `audit_examples` for the old registry. Disputed 43 → 6.
- Levels now: 43 confirmed / 226 official / 66 media / 6 disputed.
- Map: padding clamped so 40% of the map stays free, `minZoom` 9, an initial
  Kyiv view, refit after `load`. Could NOT check it in a real browser this
  time (headless Chrome was not allowed in this session) – needs a look.
- Leftover "counted once" / "nights" wording fixed; records summaries no
  longer auto-placed into the right-hand column.
- Deploy refused by this session's permission check; site built, not live.

## 2026-11-25 – stage 1a: attacks (report: docs/review/reports/stage-1a.md)
- Billing checked: child `claude -p` inherits CLAUDE_CONFIG_DIR=…\.claude-acc2
  (second account); one $0.0007 test call.
- `pipeline/attacks.py`: 362 alerts → 101 attacks (52 nights, 49 daytime).
  The plan's literal rule rebuilt the 09:00 problem in simulation, so nights
  are one attack each and overlapping windows are resolved by what the post
  says (named night, impact time, follow-up, night/day words, alert timing).
- `pipeline/validate.py`, auditor `public_note_en`, two revision rounds.
- Five known nights rerun into data/out_1a: 41 places (was 109 over the ten
  09:00 dates), every bridge and the kindergarten once per attack, no casualty
  report under two attacks, titles from code, no service text. PASS.
- Reading by hand found two bugs the criteria missed (validate's district
  fix, a "сьогодні" post in the night); both fixed and tested.
