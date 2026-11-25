"""Re-derive everything in the registry that is pure code, without the agents.

The agents' output — which claims make up a place, its title, its summary — is
expensive and is left untouched. Confirmation levels and map points are rules,
not judgements, so when a rule changes they can be recomputed from the registry
that is already on disk. No model is called and nothing is fetched.

    python -m pipeline.recompute
"""

import json
from collections import Counter
from pathlib import Path

from pipeline import tools
from pipeline.graph import place_sites

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "data" / "out" / "registry.json"


def main() -> None:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    sites = reg["sites"]
    before_levels = Counter(s["level"] for s in sites)
    before_precision = Counter((s.get("geo") or {}).get("precision") for s in sites)

    # Older registries kept only the auditor's words; the kind of each open
    # complaint is recovered from that day's audit record.
    open_kind = {(d["day"], e["explanation"]): e["kind"]
                 for d in reg["days"] for e in d.get("audit_examples", []) if not e["fixed"]}
    for s in sites:
        if "flag_kinds" not in s:
            s["flag_kinds"] = [open_kind.get((s["day"], f), "unknown") for f in s.get("flags", [])]
        s.update(tools.confirmation(s["sources"], disputed=tools.is_disputed(s["flag_kinds"])))
    place_sites(sites)

    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    after_levels = Counter(s["level"] for s in sites)
    after_precision = Counter((s.get("geo") or {}).get("precision") for s in sites)
    for name, before, after in (("levels", before_levels, after_levels),
                                ("precision", before_precision, after_precision)):
        print(name)
        for k in sorted(set(before) | set(after), key=str):
            print(f"  {str(k):16} {before.get(k, 0):4} -> {after.get(k, 0):4}")


if __name__ == "__main__":
    main()
