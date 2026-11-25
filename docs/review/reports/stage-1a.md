# Stage 1a — attacks, validate, public notes, two revision rounds

Code for stage 1 of `docs/review/plan.md`, run on the five known problem nights
only (6–7 Oct, 7–8 Oct, 6–7 Nov, 16–17 Nov, 19–20 Nov). The reader ran from its
cache (no reader call was made); matcher and auditor ran fresh. Output went to
`data/out_1a/`; the live registry in `data/out/` is untouched until 1b.

## Review notes fixed first (`kyiv_registry_review_notes.md`, 2026-11-25)

All five OPEN items, in commit `886240e`:

| Item | Fix | Result |
|---|---|---|
| Map opens on all of Ukraine | Padding clamped so ≥40% of the map stays free; `minZoom` 9 and `maxBounds` around Kyiv; first view set to Kyiv, refitted after `load`; replay uses the same padding. | **Not checked in a real browser** — headless Chrome was refused by the session's permission check. Needs a look at 1280×631, 1440×900, 390×844. |
| English media always "adds detail" | English quotes count only numbers until `translate`; clock times and the post's own date numbers never count; Ukrainian compared by stem against every word of the official quotes; district names count as common. 6 new tests. | Confirmed **56 → 43**; 16 of the 43 rest on a media voice and each has a real detail (Strazhesko Institute, the Austrian and Dutch embassies, "built in 1798", a 72-year-old resident). |
| "Disputed" too strong | Only `wrong_merge`, `not_this_attack`, `not_kyiv_city` (`tools.DISPUTING`); others keep the evidence level plus a grey "· auditor note". Kinds kept in `flag_kinds`, recovered from `audit_examples` for the old registry. | Disputed **43 → 6**. |
| "counted once", "nights" | `<title>`, records "nights" → "dates", how.html Match step, card buttons. "Counted once" stays out until 1b passes. | — |
| Right-aligned summaries on records.html | The grid auto-placed `.sum` into the level's column; text elements now pinned to column 1. | — |

The site with these fixes is **built but not deployed**: `npx vercel deploy` was
refused by the same permission check. Taras needs to run it (see the end).

## What changed (stage 1a)

| File | Change |
|---|---|
| `pipeline/attacks.py` | New. `segment()` alerts → attacks; `assign()` claims → attacks; titles and ids from code. |
| `pipeline/validate.py` | New. District from quotes, weapon from most reports, service words and dates sent back, one named object on two places sent back as `missed_merge`, `build_title()`. |
| `pipeline/graph.py` | Reader per calendar day (cache valid) → `segment` → per attack `match → validate → audit → finalize`; validate may send back once, the auditor twice (second time only for merge errors); public notes and summaries cleaned by code; `KSR_OUT`. |
| `pipeline/agents.py`, `prompts/matcher.md`, `prompts/auditor.md`, `schemas.py` | Matcher gives `object_en` (not a title) and no dates; a named object is one place per attack; auditor writes `public_note_en` (≤140 chars). |
| `pipeline/wikipedia.py` | `crosscheck_attacks()`: an entry for date *d* is compared with the attacks reported on *d* (no window). Used from 1b. |
| `pipeline/build_site.py` | Reads the attack registry (used from 1b). |
| `pipeline/llm.py` | `KSR_CACHE_ONLY=1` refuses any call not in the cache (dry runs). |
| `scripts/check_known_cases.py` | The four pass criteria as code. |
| `scripts/segment_dry_run.py` | Where claims land, from the reader cache, no model. |
| `tests/test_attacks.py` | New, 48 tests. |

### Where I departed from the plan, and why

The plan's rule — merge alerts under 2 h apart, cap each window at the next
attack's start — was simulated on the real alerts first. It rebuilds the 09:00
problem: on 7 Oct a 19-minute daytime alert at 09:48 would have cut off the
morning reports of the night of 6–7 Oct, and that night itself fell into two
pieces (21:09 drones, 03:10 missiles). So, all in code and tested:

- **One night is one attack.** Chains of the same night (18:00–09:00) or the
  same day merge; a night stops at 09:00 next morning (without that, the
  6–7 Nov night chained into the next afternoon).
- **Windows overlap; the post decides.** A claim is placed by, in order: a
  date it names ("у ніч на 7 листопада" → the night of 6–7), its stated impact
  time, whether its post repeats places already filed under the night
  (*follow-up*), night words, day words ("сьогодні" in an evening post), a
  post during an alert or within 2 h of its all-clear, else the night before.
- **Follow-up** exists for one real case: the city's 08:30 recap of the night
  of 7–8 Oct went out the minute a 07:40 daytime alert ended and would have
  been filed under 8 Oct daytime. It is limited to night → following day.
- **Titles omit the address**: the address is Ukrainian and is already in the
  line under the title.

## Numbers for the five nights, before → after

| Night | places before (two 09:00 dates) | after | named objects before → after | casualty figure |
|---|---|---|---|---|
| 6–7 Oct | 19 over 6 + 7 Oct, incl. other attacks | **6** | — | 2 killed |
| 7–8 Oct | 17 over 7 + 8 Oct | **8** | — | 8 injured (see below) |
| 6–7 Nov | 18 | **8** (+1 removed: outside Kyiv) | kindergarten 3 → **1**, Northern Bridge 1 → 1 | 5 injured |
| 16–17 Nov | 12 | **7** | Northern 2 → **1**, Southern 2 → **1** | 1 injured |
| 19–20 Nov | 43 | **12** | Southern 2 → **1**, Darnytskyi 2 → **1**, Podilsko-Voskresenskyi 2 → **1** | 6 killed, 15 injured |

"Before" counts all places on both 09:00 dates, so it mixes in neighbouring
attacks; it is the number a visitor saw for those dates, not a clean baseline.
Under the old split, 7 Oct's figure (4 killed, 21 injured) was shown for both
"6–7" and "7–8 Oct". Now no casualty report is counted under two attacks.

Confirmation levels of the 41 places: 23 official, 12 confirmed, 6 media,
0 disputed. 7 have an address on the map.

## Checks

- `pytest tests/` — **266 passed** (all suites, including 48 new).
- `scripts/check_known_cases.py data/out_1a/registry.json` — **PASS**:
  1. each bridge and the kindergarten on one place per attack (public text);
  2. casualty reports counted under two attacks: 0;
  3. 5 attack titles and 41 place titles all from code;
  4. 87 public texts, none with `c\d+`, `Site \d`, `claim`, `audit`.
- Every place of the five nights read by hand. That reading caught two bugs
  the criteria did not, both fixed before the final run:
  `validate` moved a Podilskyi place to Darnytskyi because the quote said
  "В Подільському та Дарницькому районах" (now only a quote naming one district
  votes, and a field named in any quote is never changed), and St Volodymyr's
  Cathedral sat in the night of 19–20 Nov because a 21:02 post said "Сьогодні…"
  (day-words rule).
- Billing: `CLAUDE_CONFIG_DIR=C:\Users\faras\.claude-acc2` is inherited by the
  child `claude -p`; one test call ($0.0007) went through it.

## Not done, or unsure

- **Casualties per attack are still the reader's city totals**, and they are
  incomplete: 7–8 Oct shows 8 injured, no deaths, while the places themselves
  record one man killed and Kyiv Post says 3 killed, 14 injured. That is
  stage 2 (`casualties`); the hero stays without casualties.
- **Neighbouring attacks can still share a place.** The Dutch embassy appears
  under the night of 19–20 Nov from a post at 08:34 next morning, and probably
  belongs to the daytime attack of 19 Nov. That is what `dedup_neighbours`
  (stage 2) is for.
- In the 1/2 counts above, the second number is places whose *source quotes*
  mention the object, e.g. a morning summary naming two bridges attached to
  one of them. That is a shared summary post, not a double-counted bridge.
- Map and deploy: see the review-fix table.
