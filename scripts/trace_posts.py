"""Follow a fact through the pipeline without calling a model: which raw posts
mention it, whether the prefilter kept them, which reader day they went to,
what the reader made of them (from its cache), and which attack each claim
would land in.

    KSR_CACHE_ONLY=1 python scripts/trace_posts.py "регex" 2026-10-07 2026-10-08
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import agents, attacks, prefilter  # noqa: E402

pattern, days = re.compile(sys.argv[1], re.I), sys.argv[2:]
posts = prefilter.load_posts()
r = prefilter.run()
cand = {p["pid"]: d for d, ps in r["by_day"].items() for p in ps}
hits = [p for p in posts if p["local"].date().isoformat() in days and pattern.search(p["text"])]
print(f"{len(hits)} raw posts match")
claims_by_post = {}
for d in sorted({cand[f"{p['channel']}/{p['id']}"] for p in hits if f"{p['channel']}/{p['id']}" in cand}):
    try:
        out = agents.read(d, r["by_day"][d])
    except Exception as exc:          # not cached
        print(f"  reader {d}: {exc}")
        continue
    texts = {p["pid"]: p["text"] for p in r["by_day"][d]}
    placed = attacks.assign(out["claims"], texts, attacks.segment(r["alerts"]))
    for aid, cs in placed["by_attack"].items():
        for c in cs:
            claims_by_post.setdefault(c["post"], []).append((aid, c["placed_by"], c["quote"][:70]))
    for c in out["rejected"]:
        claims_by_post.setdefault(c.get("post"), []).append(("REJECTED", c.get("why"), c.get("quote", "")[:70]))
for p in hits:
    pid = f"{p['channel']}/{p['id']}"
    kept = cand.get(pid)
    print(f"\n{p['local']:%m-%d %H:%M} {pid}  prefilter: {'day ' + kept if kept else 'DROPPED'}")
    print("   ", re.sub(r"\s+", " ", p["text"])[:220])
    for x in claims_by_post.get(pid, []) if kept else []:
        print("    ->", x)
    if kept and pid not in claims_by_post:
        print("    -> reader made no claim from this post")
