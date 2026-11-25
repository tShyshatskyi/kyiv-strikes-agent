"""validate — plain code between the matcher and the auditor.

It catches what nobody should have to argue about, so the auditor's attention
goes to the one real question: is this one place or two?

Fixed in place, for free:
- the district in the field contradicts the district the quotes name;
- the weapon contradicts most of the place's own reports.

Sent back to the matcher, with the exact words:
- service text in a public field ("another", "second", "(overnight)", claim
  ids, "site 3", "classified as", "per feedback");
- dates in a summary (the attack's date is already in its header);
- one named object (a bridge, a kindergarten, a metro station, an embassy) on
  two places of the same attack.

Titles are built here too, never written by a model: what was hit, then the
district.
"""

from __future__ import annotations

import re
from collections import Counter

from mcp_server.geo import normalize_apostrophes
from pipeline.schemas import DISTRICTS, DISTRICTS_EN

# Words that only make sense to the agents, never to a reader.
SERVICE = re.compile(
    r"\banother\b|\bsecond\b(?!\s+(?:floor|storey|story|entrance))|\(overnight\)|\bclaims?\b|"
    r"\baudit\w*|\bmatcher\b|\bc\d+\b|"
    r"\bsite \d|\bsites? #?\d|classified as|per feedback|\bduplicate\b|kept (?:separate|apart)", re.I)
# Case-sensitive on purpose: "may" is a verb far more often than a month.
DATES = re.compile(
    r"\b(?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\b|\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|"
    r"\b(?:Oct|Nov)\.\s*\d|\b20\d\d\b")
CYRILLIC = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")

TARGET_EN = {
    "residential_highrise": "Apartment block", "residential_house": "House", "energy": "Energy site",
    "industrial": "Industrial site", "warehouse": "Warehouse", "commercial": "Commercial building",
    "office": "Office building", "education": "School or university building", "medical": "Medical building",
    "transport": "Transport site", "infrastructure": "City infrastructure", "cultural_religious": "Cultural site",
    "vehicle": "Vehicles", "open_area": "Debris on open ground", "other": "Building",
}

_STEMS = {"голосіївськ": "Голосіївський", "дарницьк": "Дарницький", "деснянськ": "Деснянський",
          "дніпровськ": "Дніпровський", "оболонськ": "Оболонський", "печерськ": "Печерський",
          "подільськ": "Подільський", "святошинськ": "Святошинський", "солом'янськ": "Солом'янський",
          "шевченківськ": "Шевченківський"}
# A district adjective, unless it names a bridge ("Дарницький міст",
# "Подільсько-Воскресенський мостовий перехід") rather than the district.
_STEMS = {normalize_apostrophes(k): v for k, v in _STEMS.items()}
_DISTRICT_WORD = re.compile(r"\b(" + "|".join(map(re.escape, _STEMS)) + r")(?!о-)\w*(?![\w-])"
                            r"(?!\s+(?:та\s+\w+\s+)?(?:міст|мост|перехід))", re.I)


def districts_named(quote: str) -> set[str]:
    """Every district a quote names ("у Подільському та Дарницькому районах"
    names two). Bridges named after a district do not count."""
    t = normalize_apostrophes(quote or "").lower()
    return {_STEMS[m.group(1)] for m in _DISTRICT_WORD.finditer(t)}


def object_keys(text: str) -> set[str]:
    """Named objects in a place's English description: one of these on two
    places of one attack is almost always one strike counted twice."""
    t = text or ""
    keys = set()
    for m in re.finditer(r"\b([A-Z][\w'-]+(?:-[A-Z][\w'-]+)?)\s+[Bb]ridge\b", t):
        keys.add("bridge:" + m.group(1).lower())
    for m in re.finditer(r"\b([A-Z][\w'-]+) and ([A-Z][\w'-]+) [Bb]ridges\b", t):
        keys.update({"bridge:" + m.group(1).lower(), "bridge:" + m.group(2).lower()})
    if re.search(r"kindergarten|nursery school|preschool", t, re.I):
        keys.add("kindergarten")
    for m in re.finditer(r"\b([A-Z][\w'-]+) metro station\b", t):
        keys.add("metro:" + m.group(1).lower())
    for m in re.finditer(r"\b(?:embassy of (?:the )?([A-Z]\w+)|([A-Z]\w+) embassy)", t, re.I):
        keys.add("embassy:" + (m.group(1) or m.group(2)).lower())
    keys.discard("bridge:the")
    return keys


def clean_object(obj: str | None, target: str) -> str:
    """The matcher's short name for what was hit, or the type of place if it
    gave none we can use."""
    o = (obj or "").strip().rstrip(".")
    if not o or len(o) > 48 or SERVICE.search(o) or DATES.search(o) or CYRILLIC.search(o):
        return TARGET_EN.get(target, "Building")
    return o[0].upper() + o[1:]


def build_title(obj: str, district: str | None) -> str:
    d = DISTRICTS_EN.get(district or "")
    return f"{obj} – {d}" if d else obj


def _majority(values: list) -> object | None:
    c = Counter(v for v in values if v)
    if not c:
        return None
    (top, n), = c.most_common(1)
    return top if n * 2 > sum(c.values()) else None


def check(claims: list[dict], sites: list[dict]) -> dict:
    """Fix what is mechanical; return what the matcher must redo."""
    by_id = {c["id"]: c for c in claims}
    fixes, issues = [], []
    for n, s in enumerate(sites, 1):
        mine = [by_id[i] for i in s.get("claims", []) if i in by_id]
        # District from the quotes.
        # Only a quote that names exactly one district votes, and the field is
        # changed only if its own district is named in none of the quotes.
        named_each = [districts_named(c["quote"]) for c in mine]
        votes = [next(iter(d)) for d in named_each if len(d) == 1]
        named = _majority(votes)
        field_named = any(s.get("district") in d for d in named_each)
        if named and named != s.get("district") and not field_named:
            fixes.append(f"place {n}: district {s.get('district')} -> {named} (from the quotes)")
            s["district"] = named
        # Weapon from the place's own reports.
        weapon = _majority([c["weapon"] for c in mine if c["weapon"] != "unknown"])
        if weapon and weapon != s.get("weapon"):
            fixes.append(f"place {n}: weapon {s.get('weapon')} -> {weapon} (most reports)")
            s["weapon"] = weapon
        # Wording.
        for field in ("object_en", "summary_en"):
            text = s.get(field) or ""
            if m := SERVICE.search(text):
                issues.append({"kind": "wording", "sites": [n], "claims": [],
                               "explanation": f"{field} of place {n} contains \"{m.group()}\", which is a "
                                              f"note for the pipeline, not for a reader: \"{text}\". "
                                              "Describe only the strike."})
        if m := DATES.search(s.get("summary_en") or ""):
            issues.append({"kind": "wording", "sites": [n], "claims": [],
                           "explanation": f"summary_en of place {n} gives a date (\"{m.group()}\"). The attack's "
                                          "date is in its header; remove dates and weekdays from summaries."})
    # One named object, two places.
    holders: dict[str, list[int]] = {}
    for n, s in enumerate(sites, 1):
        for k in object_keys(f"{s.get('object_en') or ''}. {s.get('summary_en') or ''}"):
            holders.setdefault(k, []).append(n)
    for k, ns in holders.items():
        if len(ns) > 1:
            what = k.replace(":", " ").replace("bridge ", "the bridge ").replace("metro ", "metro station ")
            issues.append({"kind": "missed_merge", "sites": ns, "claims": [],
                           "explanation": f"Places {', '.join(map(str, ns))} all describe {what}. "
                                          "Within one attack a named object is one place: put every report "
                                          "of it together, unless the quotes clearly show two separate hits."})
    return {"sites": sites, "fixes": fixes, "issues": issues}


def public_text_problems(text: str) -> list[str]:
    """What must never reach the site: agent ids, service words, Cyrillic in English."""
    out = []
    if m := re.search(r"\bc\d+\b|\bSite \d|\bclaims?\b|\baudit\w*", text or "", re.I):
        out.append(m.group())
    return out
