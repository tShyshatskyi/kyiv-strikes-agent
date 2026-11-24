# Stage 0 — no model calls

Carried out from `docs/review/plan.md` §5 "Етап 0". Nothing was re-run through
the agents: every number below comes from re-deriving rules over the registry
that was already on disk (`python -m pipeline.recompute`), so the reader,
matcher and auditor caches are untouched and still valid for stage 1.

Review notes read before starting: `kyiv_registry_review_notes.md`,
2026-11-24 heading. Both items there were `(info)`, none `OPEN`. The working
tree was clean and `site/` was free, as that note said.

## What changed

**Pipeline**

| File | Change |
|---|---|
| `pipeline/sources.py` | City hall's three channels share one voice `city`; the emergency service is its own voice `emergency`. |
| `pipeline/tools.py` | `confirmation()` takes the sources, not a channel list: media counts as a voice only where its quote adds a figure or name no earlier official post gave; an open auditor flag makes a place `disputed`. `district_point()` no longer scatters district-only reports — the random spread is gone. |
| `pipeline/wikipedia.py` | `night_of()` deleted. Dates to compare are fixed in advance by `window()`; `named_objects()`/`has_object()` compare the kinds of place named; four verdicts (`match`, `differs`, `no_figure`, `missing`). |
| `pipeline/graph.py` | `place_sites()` factored out of `geocode_node` so a no-model recompute reuses it; confirmation now runs after flags are known. |
| `pipeline/build_site.py` | Alert hours and alert count restricted to the period; `strip_service_text()` cuts public descriptions where they turn into notes for the agents; the build prints anything it could not clean. |
| `pipeline/recompute.py` | New. Re-derives levels and map points from `data/out/registry.json` without a model. |
| `tests/test_pipeline_rules.py` | New. 18 tests over the rules above. |

**Site** (mapped onto the new map-first structure from `8a5cb44`, not the
one-page site the critique describes)

| File | Change |
|---|---|
| `site/index.html` | Hero: no casualties, no confirmation percentage; four honest numbers plus the 09:00 caveat in plain words. Map: districts shaded by count, dots only for the 44 places with an address; selecting a district-only place outlines its district. Auditor notes folded into a grey toggle. |
| `site/common.js` | New level labels and notes; `auditorNote()` toggle; `asOf()`; footer says "Kyiv City State Administration", not "city council", and carries the data-as-of and edited-posts warning. |
| `site/wikipedia.html` | Rewritten for four verdicts; lead gives the honest split; shows which nights each entry was compared with and what kinds of place it names. |
| `site/records.html` | Level labels from `common.js`; auditor notes folded; per-date casualty figures removed. |
| `site/patterns.html` | Night-of-the-week chart removed; "night" → "attack"; weapon sub-counts read "(incl. 3 jet drones)". |
| `site/how.html` | "seven newsrooms" and the four official channels; "API-equivalent cost … on a subscription"; posts-read figure is the 2,116 an agent read; Wikipedia step describes the new check; the auditor block is 4 cases with one-line outcomes and the working note folded; a new limit states the 09:00 split plainly. |
| `site/style.css` | Styles for the folded note, the district legend swatch, and the new verdict colours. |

## Numbers, before → after

| | before | after | why |
|---|---|---|---|
| Places | 341 | 341 | unchanged; stage 1 removes the duplicates |
| Confirmed | 117 | **56** | city hall's three channels were counted as two or three voices |
| Official only | 162 | 182 | places whose only "second source" was a newsroom retelling the briefing |
| Media only | 62 (`single_source`) | 60 | |
| Disputed | 0 (shown as confirmed/official) | **43** | places the auditor still queries are no longer counted as confirmed |
| Wikipedia verdicts | 21 nights, 17 agree, 4 differ, 0 missing | **22 dates, 5 agree, 8 differ, 9 nothing to compare, 0 missing** | see below |
| Killed / injured in hero | 46 / 352 | **not shown** | the figures double-count across the 09:00 split |
| Hours under alert | 347 | **343** | alerts outside 1 Oct – 23 Nov no longer counted |
| Places drawn as points | 318 (272 of them invented) | **44** | only places with a matched address get a point |
| Places with an address | 44 | 44 | unchanged, now stated on the page |
| Public descriptions carrying agent notes | 9 | **0** | |
| Newsrooms claimed | eight | seven | |

## Checks run

- `pytest tests/test_pipeline_rules.py` — 18 passed. They pin the things the
  critique caught: three city channels are one voice; a newsroom repeating the
  briefing does not confirm it; an open auditor complaint cannot read as
  confirmed; district-only reports land on one point; the Wikipedia comparison
  dates do not depend on where the registry has places; a named bridge is not
  satisfied by burning cars; both sides silent is not agreement.
- Every page executed against a stubbed DOM and Leaflet (reference errors, not
  just syntax): all five render, 201k/107k/106k/18k/13k chars of output.
- The three content-based `differs` were checked by hand against the registry:
  22 Oct (Wikipedia names a dormitory and a place of education, the registry has
  no education place on 21–22 Oct) and 7 Nov (Wikipedia names energy
  infrastructure, the registry has none on 6–7 Nov) are real recall gaps.
  A third, 20 Nov "a water utility", was a **false positive** in my first
  version — the registry's "Kyivvodokanal facility damaged" does not contain the
  word "water" — and the pattern was fixed before this commit.
- The trimmed descriptions were read one by one; no fact about any strike was
  lost, only the clause explaining the grouping to the auditor.
- Live site checked after deploy (see below).

## What I could not do, and what I am unsure about

- **`missing` is 0, and that is not yet a strong result.** Because the registry
  still splits an attack at 09:00, a Wikipedia entry has to be compared with a
  two-date window, and a two-date window is wide enough that something almost
  always matches. The honest signal today is the content check, which did find
  two real gaps. After `segment_attacks` the window becomes one attack and
  `missing` becomes a real test. The page says the window rule out loud.
- **Casualty figures are only hidden, not fixed.** They stay out of the hero and
  off the date headers until `casualties` runs per attack in stage 2.
- **Titles still say "another", "second", "(overnight)".** The plan assigns
  those to `validate` in stage 1, so they are untouched here; I did not want to
  rewrite a title by hand and have the next run overwrite it.
- **Quotes are still Ukrainian only.** `translate` is stage 2 per the plan.
- **Media independence is judged by a code rule, not by reading.** A quote
  counts as its own reporting when it carries a number or a mid-sentence proper
  name absent from every earlier official quote for that place. It compares
  against the official *quotes* the registry kept, not the full official posts,
  so it is slightly generous. It moved 61 places out of "confirmed", which
  matches the critique's own estimate (34 official-only plus ~56 later
  retellings), so I believe the direction is right even if a few individual
  calls are arguable.
- **The auditor's working notes are folded, not rewritten.** `public_note_en` is
  stage 1. Until then the service text is grey, behind a toggle, and never the
  first thing a visitor meets.
- **"52 dates with a recorded strike", not "52 attacks".** The plan asks for the
  word "attack", but using it before `segment_attacks` would claim the thing
  that is not yet true, so the hero names dates and says why.
