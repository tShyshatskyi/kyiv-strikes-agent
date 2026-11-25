# Pause at 10:33 — budget line crossed during 1b chunk 1 (1–15 Oct)

## Usage
| time | session (5 h) | week |
|---|---|---|
| 10:16 (before 1a rerun) | 3% | 26% |
| 10:21 (after 1a rerun) | 27% | 28% |
| 10:23 (chunk 1 start) | 28% | 28% |
| 10:29 | 36% | 28% |
| 10:32 | **52%** | 30% |

The session went 36% → 52% between two watchdog reads three minutes apart,
so the run was stopped above the 40% pause line and above Taras's 50% limit.
That is my error: a 3-minute poll is too coarse for a run with three
parallel Sonnet calls. Window resets 15:10 Kyiv time.

## Done
- Item 1 (1a rerun) — committed `aad9e0c`, PASS; report `stage-1a-rerun.md`.
- Backup of `data/out/` → `data/out_v2_backup/pre-1b/` (the older
  `data/out_v2_backup/registry.json` was left as it was).
- `scripts/run_with_budget.py` — runs a command, reads `/usage` every N
  seconds, kills the command tree at the session/week line (exit 3).
- Chunk 1 (1–15 Oct, 26 attacks), stopped by the watchdog: reader done for
  all 16 days (cached), segmentation done (678 claims → 650 in 26 attacks),
  25 matcher and 37 auditor rounds, 23 attacks reached finalize. Everything
  is in `data/cache/llm/`; output dir `data/out_1b/` was not written (publish
  never ran).

## Not started (per the pause rule)
The review note of 10:30 (OPEN: make 1b cheaper before chunk 2) is not
started. It is the first job of the next session, before any model call.

## Next session, in order
1. Read the review notes. Do the 10:30 OPEN cost items (fewer matcher
   rounds, no auditor for small attacks, Haiku matcher ≤ 6 claims, stable
   reader batches). **Note:** changing the matcher/validate/auditor flow
   changes prompts, so the 3 unfinished chunk-1 attacks and any attack whose
   prompt changes will miss the cache; the 23 finished ones stay valid only
   if their prompts are unchanged. Decide before rerunning.
2. Check `pytest tests/` and the five known nights
   (`KSR_SKIP_COLLECT=1 KSR_OUT=data/out_1a PYTHONUTF8=1 python -W ignore -m pipeline 2026-10-06-N 2026-10-07-N 2026-10-13-N 2026-11-06-N 2026-11-16-N 2026-11-19-N`,
   then `python scripts/check_known_cases.py data/out_1a/registry.json`).
3. Finish chunk 1, with a tighter watchdog (lower line to leave room for
   a jump, poll every 60 s):
   ```
   D=$(for i in $(seq -w 1 15); do printf "2026-10-%s " $i; done)
   KSR_SKIP_COLLECT=1 KSR_OUT=data/out_1b PYTHONUTF8=1 python scripts/run_with_budget.py --session 32 --week 42 --every 60 -- python -W ignore -m pipeline $D > data/run_1b_c1.log 2>&1
   ```
   Then chunks 16–31 Oct, 1–15 Nov, 16–23 Nov the same way, and a final
   cache-only assembly run of all dates into `data/out`.
