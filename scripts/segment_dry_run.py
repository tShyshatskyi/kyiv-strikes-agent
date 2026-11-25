"""Where would claims land, without calling any model? Reads the reader's
cached answers for the days an attack needs and runs segment_attacks.

    KSR_CACHE_ONLY=1 python scripts/segment_dry_run.py 2026-10-06-N ...
"""
import sys
from collections import Counter

from pipeline import agents, attacks, prefilter
from pipeline.graph import selected

r = prefilter.run()
all_a = attacks.segment(r["alerts"])
chosen = selected(all_a, sys.argv[1:])
need = sorted({d for a in chosen for d in attacks.reading_days(a)})
claims, texts = [], {}
for d in need:
    posts = r["by_day"].get(d, [])
    texts.update({p["pid"]: p["text"] for p in posts})
    out = agents.read(d, posts)
    claims += [{**c, "rid": f"{d}/{c['id']}"} for c in out["claims"]]
placed = attacks.assign(claims, texts, all_a)
print(f"days read: {need}; {len(claims)} claims")
for a in chosen:
    mine = placed["by_attack"].get(a["id"], [])
    print(f"\n{a['id']}  {a['title']}  {a['start']} – {a['end']}  -> {len(mine)} claims",
          dict(Counter(c["placed_by"] for c in mine)))
others = Counter(aid for aid, cs in placed["by_attack"].items() for _ in cs if aid not in {a["id"] for a in chosen})
print("\nclaims from these days placed in other attacks:", dict(others))
print("set aside:", dict(Counter(c["aside"] for c in placed["aside"])))
for c in placed["aside"][:12]:
    print("  ", c["post_time"], c["aside"], c["quote"][:90].replace("\n", " "))
