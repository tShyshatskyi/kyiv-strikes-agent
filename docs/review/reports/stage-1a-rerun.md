# Stage 1a rerun — the five known nights with the `d1caeaf` fixes

Usage at start (10:16): session 3%, week 26%. At end (10:21): session 27%, week 28%.

## Why the last run was killed
Nothing capped how many model calls ran at once. The graph reads every
calendar day in parallel and then matches every attack in parallel, each call
through `asyncio.to_thread` → its own `claude -p` process (150–400 MB each on
this machine; 16 GB total, ~0.8 GB free with the other open sessions). The
killed run had 11 reader processes, then six matchers started at once.
Posts in memory are not the problem (all raw posts are 20 MB).

Fix: `pipeline/llm.py` holds a semaphore around every backend call,
`KSR_MAX_PARALLEL` (default 3). Test: `tests/test_llm_parallel.py`.
This run finished without trouble (254 s).

## What changed
| File | Change |
|---|---|
| `pipeline/llm.py` | At most `KSR_MAX_PARALLEL` (3) model calls at once. |
| `tests/test_llm_parallel.py` | New: 8 calls on 8 threads never run more than 2 at once with a limit of 2. |
| `scripts/check_known_cases.py` | Criterion 1 now asks whether a place *is* the object (its title names it, or its text does and it is of that kind), and still prints text mentions. |
| `data/out_1a/` | Rerun. |

### The criterion change, said plainly
The first check of this run FAILED on 6–7 Nov: "Northern Bridge: 2 places".
The second place is *Five cargo trucks* — "The blast wave damaged five cargo
trucks in a car park next to the Northern Bridge" (city hall: "на парковці
біля мосту пошкоджені 5 вантажних автомобілів"). That is a separate thing that
was hit, not a second bridge record. The check counted any mention in the
description; it now counts places that are the bridge (title, or text plus a
bridge/kindergarten kind) and prints mentions next to it (`1 / 2 / 2` =
is the object / mentioned / in a source quote). If the reviewer thinks the
trucks should fold into the bridge, that is a matcher-prompt rule, not a
checker change.

## Numbers, five nights + 13–14 Oct

| Attack | places (1a run before fixes) | now | city figure now |
|---|---|---|---|
| 6–7 Oct | 6 | 7 | 2 killed |
| 7–8 Oct | 8 | 14 | 4 killed, 13 injured |
| 13–14 Oct | — | 12 | 5 killed, 25 injured |
| 6–7 Nov | 8 | 10 | 5 injured |
| 16–17 Nov | 7 | 11 | 1 injured |
| 19–20 Nov | 12 | 18 | 6 killed, 17 injured |

More places is the wider prefilter (216 more posts) and the
punctuation-tolerant quote check. Levels of the 72: 27 confirmed, 32 official,
11 media, 2 disputed.

## Checks
- `pytest tests/` — 278 passed.
- `scripts/check_known_cases.py data/out_1a/registry.json` — **PASS**:
  every bridge and the kindergarten one place per attack; benchmark
  Desnianskyi 9-storey of 13–14 Oct = 1 record (`2026-10-13-N-1`, was three);
  Kyivvodokanal 19–20 Nov = 1 record (`2026-11-19-N-15`) carrying 2 killed /
  7 injured as the benchmark has it; casualty reports under two attacks: 0;
  all titles from code; 158 public texts, none with service words.

## Unsure
- The 9-storey of 13–14 Oct carries 3 killed / 25 injured, close to the whole
  night's figure (5 / 25). It may have absorbed the city totals. Stage 2
  `casualties` is where that gets checked.
- 7–8 Oct now 4 killed / 13 injured; Kyiv Post said 3 / 14. Stage 2.

## Cost — this changes the plan for 1b
The six attacks cost $3.03 of API-equivalent (22 matcher + 16 auditor calls,
Sonnet) and took **24 points of the 5-hour window and ~2 of the week**.
Extrapolated: the full 1b (re-reading changed days with Haiku, then
matcher + auditor for ~100 attacks, most of them small) is about $25–35,
i.e. roughly the whole remaining weekly budget (26% → 45%) and five to six
5-hour windows at the 40% pause line. 1b will therefore run in pieces across
several windows; stage 2 may not fit this week. Each chunk is cached, so
nothing is lost by pausing.
