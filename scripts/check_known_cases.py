"""Stage 1a pass criteria, checked on a registry produced by the attack pipeline.

    python scripts/check_known_cases.py data/out_1a/registry.json [attack ids...]

1. each bridge and the kindergarten is one place per attack (a place is the
   object when its title names it, or its text does and it is of that kind);
2. no attack repeats another attack's casualty figure (no casualty report
   counted under two attacks);
3. attack titles and place titles come from code;
4. no public text contains c<digits>, "Site <digit>", "claim" or "audit".

Exit code 0 only when all four hold.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import attacks, validate  # noqa: E402

# Named objects as the Ukrainian sources name them, to count which places a
# reader would see the same thing on.
OBJECTS_UK = {
    "Northern Bridge": r"Північн\w*\s+мост|Північн\w*\s+міст",
    "Southern Bridge": r"Південн\w*\s+мост|Південн\w*\s+міст",
    "Darnytskyi Bridge": r"Дарницьк\w*\s+мост|Дарницьк\w*\s+міст|Дарницьк\w* та Південн\w* мост",
    "Podilsko-Voskresenskyi crossing": r"Подільсько-Воскресенськ",
    "Paton Bridge": r"міст\w* Патона|мосту Патона",
    "kindergarten": r"дитсад|дитяч\w*\s+сад|садоч",
}
OBJECTS_EN = {
    "Northern Bridge": r"Northern Bridge|North Bridge",
    "Southern Bridge": r"Southern Bridge|South Bridge|Pivdennyi",
    "Darnytskyi Bridge": r"Darnytskyi\s+(?:and\s+\w+\s+)?[Bb]ridge|Darnytskyi Bridge",
    "Podilsko-Voskresenskyi crossing": r"Podilsko|Podilskyi[- ]Voskresenskyi|Podilskyi bridge",
    "Paton Bridge": r"Paton",
    "kindergarten": r"kindergarten|nursery|preschool",
}
BENCHMARK_ONCE = [
    ("2026-10-13-N", "Desnianskyi 9-storey block", r"9-storey|nine-storey|9-floor", "Деснянський"),
    ("2026-11-19-N", "Kyivvodokanal", r"vodokanal|water utility", None),
]
# A place *is* the named object when its title names it, or when its text names
# it and the place is of the object's kind. Five trucks "in a car park next to
# the Northern Bridge" mention the bridge but are a separate thing that was hit.
OBJECT_KINDS = {"kindergarten": {"education"}}
BRIDGE_KINDS = {"transport", "infrastructure", "other"}


def is_object(s: dict, name: str) -> bool:
    pat = OBJECTS_EN[name]
    if re.search(pat, s["title"], re.I):
        return True
    kinds = OBJECT_KINDS.get(name, BRIDGE_KINDS)
    return s["target"] in kinds and bool(re.search(pat, s["summary"] or "", re.I))


PUBLIC_BAD = re.compile(r"\bc\d+\b|\bSite \d|\bclaims?\b|\baudit\w*", re.I)


def main(path: str, only: list[str]) -> int:
    reg = json.loads(Path(path).read_text(encoding="utf-8"))
    ats = [a for a in reg["attacks"] if not only or a["id"] in only]
    ids = {a["id"] for a in ats}
    sites = [s for s in reg["sites"] if s["attack"] in ids]
    by_attack = defaultdict(list)
    for s in sites:
        by_attack[s["attack"]].append(s)
    ok = True

    print("1. named objects: places per attack (is the object / text mentions it / any source quote)")
    for a in ats:
        rows = []
        for name in OBJECTS_EN:
            pub = [s["id"] for s in by_attack[a["id"]] if is_object(s, name)]
            men = [s["id"] for s in by_attack[a["id"]]
                   if re.search(OBJECTS_EN[name], f"{s['title']} {s['summary']}", re.I)]
            quo = [s["id"] for s in by_attack[a["id"]]
                   if any(re.search(OBJECTS_UK[name], q["quote"], re.I) for q in s["sources"])]
            if pub or quo:
                bad = len(pub) > 1
                ok &= not bad
                rows.append(f"{name}: {len(pub)} / {len(men)} / {len(quo)}{'  <-- FAIL' if bad else ''}")
        print(f"   {a['id']:14} {a['title']:24} {len(by_attack[a['id']]):3} places   " + ("; ".join(rows) or "none named"))

    # From the independent benchmark (Some_shit/kyiv_benchmark/COMPARISON.md):
    # places the 09:00 registry had two or three times.
    for aid, label, pat, district in BENCHMARK_ONCE:
        if aid not in ids:
            continue
        hits = [s["id"] for s in by_attack[aid]
                if re.search(pat, f"{s['title']} {s['summary']}", re.I)
                and (district is None or s.get("district") == district)]
        bad = len(hits) != 1
        ok &= not bad
        print(f"   benchmark {aid} {label}: {len(hits)} record(s) {hits}{'  <-- FAIL' if bad else ''}")

    print("2. casualty figures")
    used = defaultdict(set)
    for a in reg["attacks"]:
        for u in (a.get("city_totals") or {}).get("sources", []):
            used[u].add(a["id"])
    for s in reg["sites"]:
        if s.get("killed") is not None or s.get("injured") is not None:
            for q in s["sources"]:
                used[q["url"]].add(s["attack"])
    shared = {u: sorted(v) for u, v in used.items() if len(v) > 1 and v & ids}
    for u, v in shared.items():
        print(f"   FAIL  {u} counted under {v}")
    ok &= not shared
    figs = Counter((a["city_totals"].get("killed"), a["city_totals"].get("injured")) for a in reg["attacks"]
                   if (a["city_totals"].get("killed") or 0) + (a["city_totals"].get("injured") or 0) > 0)
    for a in ats:
        f = (a["city_totals"].get("killed"), a["city_totals"].get("injured"))
        same = figs[f] > 1 and sum(f[i] or 0 for i in (0, 1)) > 0
        print(f"   {a['id']:14} killed {f[0]}, injured {f[1]}"
              + ("   (same figure as another attack: check by hand)" if same else ""))
    print(f"   casualty reports counted under two attacks: {len(shared)}")

    print("3. titles from code")
    bad_t = [a["id"] for a in ats if a["title"] != attacks.title("N" if a["kind"] == "night" else "D", a["date"])]
    bad_t += [s["id"] for s in sites if s["title"] != validate.build_title(s["object"], s["district"])]
    for x in bad_t:
        print("   FAIL", x)
    ok &= not bad_t
    print(f"   {len(ats)} attack titles, {len(sites)} place titles; not from code: {len(bad_t)}")

    print("4. public text")
    texts = [(a["id"], a.get("summary") or "") for a in ats]
    texts += [(s["id"], t) for s in sites for t in [s["title"], s["summary"], *s.get("notes", [])]]
    dirty = [(i, t) for i, t in texts if PUBLIC_BAD.search(t)]
    for i, t in dirty:
        print(f"   FAIL {i}: {t[:140]}")
    ok &= not dirty
    print(f"   {len(texts)} public texts; with service words: {len(dirty)}")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
