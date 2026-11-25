"""Wikipedia as an outside check on the registry.

Wikipedia retells the same news the registry reads, so it can never confirm a
place on its own (it is not an independent witness). What it is good for is a
second opinion on whole nights: did the registry miss an attack that editors
recorded, and do the death tolls agree?

Three articles are read (English list of strikes on Kyiv, English war timeline,
Ukrainian list of autumn 2026 missile strikes). Every row or timeline entry that
names Kyiv and falls in the period becomes a record; code then lines each record
up with the registry by a fixed date rule and compares both the kinds of place
named and the death toll. No model is involved.

A check that cannot fail is worth nothing, so the rule is fixed in advance: the
dates an entry is compared against never depend on where the registry happens
to have places, and an entry that names a bridge is not satisfied by a night of
burning cars.

    python -m pipeline.wikipedia          refresh the snapshot (needs WIKIMEDIA_CONTACT)
"""

import json
import os
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "wikipedia_snapshot.json"

ARTICLES = [
    ("en", "Kyiv strikes (2022–present)", "table"),
    ("en", "Timeline of the Russo-Ukrainian war (1 September 2026 – present)", "timeline"),
    ("uk", "Перелік ракетних ударів під час російського вторгнення (осінь 2026)", "table"),
]
START, END = "2026-10-01", "2026-11-23"
KYIV = re.compile(r"Kyiv|Kiev|Київ|Києв")
RUSSIAN_STRIKE = re.compile(r"Russian (forces|overnight|missile|drone|strike|attack)|Russia attacked Kyiv"
                            r"|Kyiv was attacked|following a Russian strike")
MONTHS = {"October": 10, "November": 11}


# ---------------------------------------------------------------- collect

def _grid(table) -> list[list[str]]:
    """Table rows with rowspan/colspan filled in, so every row carries its date."""
    rows, carry = [], {}
    for tr in table.find_all("tr"):
        cells = [c for c in tr.find_all(["th", "td"], recursive=False)]
        row, ci, k = {}, 0, 0
        while k < len(cells) or ci in carry:
            if ci in carry:
                text, left = carry[ci]
                row[ci] = text
                carry[ci] = (text, left - 1)
                if left - 1 == 0:
                    del carry[ci]
                ci += 1
                continue
            c = cells[k]
            k += 1
            text = " ".join(c.get_text(" ").split())
            rs, cs = int(c.get("rowspan", 1) or 1), int(c.get("colspan", 1) or 1)
            for _ in range(cs):
                row[ci] = text
                if rs > 1:
                    carry[ci] = (text, rs - 1)
                ci += 1
        rows.append([row.get(i, "") for i in range(max(row) + 1)] if row else [])
    return rows


def collect() -> dict:
    """Fetch the three articles through the MediaWiki API. Wikimedia refuses
    scripts that give no contact address, so this needs WIKIMEDIA_CONTACT."""
    import httpx
    from bs4 import BeautifulSoup

    contact = os.environ.get("WIKIMEDIA_CONTACT")
    if not contact:
        raise SystemExit("Set WIKIMEDIA_CONTACT (an e-mail or URL) to fetch from Wikipedia.")
    ua = {"User-Agent": f"kyiv-strikes-registry/2.0 ({contact})"}
    records, articles = [], []
    for lang, title, kind in ARTICLES:
        r = httpx.get(f"https://{lang}.wikipedia.org/w/api.php", headers=ua, timeout=60, params={
            "action": "parse", "page": title, "prop": "text|revid", "redirects": 1, "format": "json"})
        r.raise_for_status()
        p = r.json()["parse"]
        articles.append({"lang": lang, "title": p["title"], "revid": p["revid"]})
        base = f"https://{lang}.wikipedia.org/w/index.php?oldid={p['revid']}"
        soup = BeautifulSoup(p["text"]["*"], "html.parser")
        for s in soup.select("sup.reference, style"):
            s.decompose()
        if kind == "table" and lang == "uk":
            for t in soup.select("table.wikitable"):
                for row in _grid(t)[1:]:
                    if len(row) < 10 or row[6] != "Київ":
                        continue
                    m = re.search(r"(\d\d)\.(\d\d)\.(\d{4})", row[4])
                    if m:
                        records.append({"date": f"{m[3]}-{m[2]}-{m[1]}", "url": base,
                                        "text": f"{row[4]}, Київ. Засіб: {row[7]}. {row[9]}"})
        elif kind == "table":
            for t in soup.select("table.wikitable"):
                for row in _grid(t)[1:]:
                    m = row and re.match(r"(\d+) (October|November) 2026", row[0])
                    if m:
                        records.append({"date": f"2026-{MONTHS[m[2]]:02d}-{int(m[1]):02d}", "url": base,
                                        "text": f"{row[0]}: {row[1]} Casualties within the city: {row[2]}"})
        else:
            month = day = ""
            for el in soup.find_all(["h2", "h3", "p", "li"]):
                text = " ".join(el.get_text(" ").split())
                if el.name == "h2":
                    month = text
                elif el.name == "h3":
                    day = text
                elif el.find_parent(class_=re.compile("navbox|reflist|toc")) is None:
                    m = re.match(r"(\d+) (October|November)", day)
                    if m and "2026" in month and KYIV.search(text) and RUSSIAN_STRIKE.search(text):
                        records.append({"date": f"2026-{MONTHS[m[2]]:02d}-{int(m[1]):02d}",
                                        "url": f"{base}#{day.replace(' ', '_')}",
                                        "text": f"{day} 2026: {text}"})
        for rec in records:
            rec.setdefault("lang", lang)
            rec.setdefault("article", p["title"])
    records = [r for r in records if START <= r["date"] <= END]
    snap = {"taken": date.today().isoformat(), "articles": articles, "records": records}
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    return snap


# ---------------------------------------------------------------- compare

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def _num(s: str) -> int:
    return int(s) if s.isdigit() else WORDS[s.lower()]


def figures(text: str) -> tuple[int | None, int | None]:
    """Killed and injured as a record states them, or None where it is silent."""
    m = re.search(r"жертви: (\d+|\?)/(\d+|\?)", text)
    if m:
        return (None if m[1] == "?" else int(m[1])), (None if m[2] == "?" else int(m[2]))
    n = r"(\d+|" + "|".join(WORDS) + r")"
    killed = injured = None
    for pat in (rf"{n} (?:dead|killed)", rf"kill(?:ed|ing)(?: at least)? {n}", rf"{n} (?:people )?(?:were )?killed",
                r"(One) person killed", r"killed (one) person"):
        if m := re.search(pat, text, re.I):
            killed = _num(m[1])
            break
    for pat in (rf"{n} injured", rf"injur(?:ed|ing) {n}", rf"wound(?:ed|ing) {n}"):
        if m := re.search(pat, text, re.I):
            injured = _num(m[1])
            break
    if killed is None and re.search(r"[Nn]o casualties", text):
        killed = injured = 0
    return killed, injured


# What a Wikipedia entry names, and what would have to be in the registry for
# that night if the registry had seen the same attack. Residential buildings are
# left out on purpose: almost every entry mentions them, so they prove nothing.
#   (label, what Wikipedia calls it, what the registry calls it, matching targets)
OBJECTS = [
    ("a bridge", r"міст\w*|мост\w*|bridge", r"bridge|міст", set()),
    ("a metro station", r"метро|metro|subway", r"metro|subway", set()),
    ("a kindergarten", r"дитсад\w*|дитячий сад|kindergarten|nursery",
     r"kindergarten|nursery|preschool|day ?care", set()),
    ("a hospital or clinic", r"медзаклад\w*|медичн\w*|лікарн\w*|hospital|clinic|institute of trauma",
     r"hospital|clinic|medical|burn cent|institute|polyclinic", {"medical"}),
    ("a school or university", r"заклад\w* освіти|школ\w*|університет\w*|гуртожит\w*|school|university|campus|dormitory",
     r"school|universit|campus|dormitor|colleg|lyceum|academy", {"education"}),
    ("an energy site", r"енергетик\w*|енергетичн\w*|ТЕЦ|підстанц\w*|power (?:station|plant|grid)|energy infrastructure|substation",
     r"power|energy|substation|heat|boiler|transformer|electric", {"energy"}),
    ("a shopping centre", r"ТРЦ|ТЦ\b|multimall|shopping (?:centre|center|mall)|mall",
     r"mall|shopping|multimall|retail cent", {"commercial"}),
    ("a data centre", r"data ?cent(?:er|re)|дата-?центр", r"data ?cent", set()),
    ("a water utility", r"водоканал|водопровід|water utility|water facility",
     r"water|vodokanal|водоканал", set()),
]


def named_objects(text: str) -> list[str]:
    """The specific things a Wikipedia entry names, as labels."""
    return [label for label, theirs, _, _ in OBJECTS if re.search(theirs, text, re.I)]


def has_object(label: str, places: list[dict]) -> bool:
    _, _, ours, targets = next(o for o in OBJECTS if o[0] == label)
    return any(re.search(ours, p["title"], re.I) or (targets and p.get("target") in targets)
               for p in places)


def window(d: str) -> list[str]:
    """The registry nights a Wikipedia entry for date `d` could be filed under.

    Wikipedia dates an attack by the calendar day it was reported. The registry
    still files reports by a 09:00 cut-off, so one attack lands partly on the
    evening it began and partly on the next morning. Until attacks are
    segmented (stage 1) an entry for `d` is therefore checked against both the
    night of `d-1` and the night of `d` — a fixed rule, not a search for the
    night that happens to have places in it.
    """
    return [(date.fromisoformat(d) - timedelta(days=1)).isoformat(), d]


def crosscheck(days: list[dict], sites: list[dict]) -> dict:
    """Per date Wikipedia records: what it says, and whether the registry agrees.

    Four verdicts, and no verdict can be reached by moving the date around:

    - `match`     both sides name the same kinds of place and give the same death toll;
    - `differs`   a place Wikipedia names is absent, or the death tolls disagree;
    - `no_figure` the places line up but one side gives no death toll, so there
                  is nothing to compare;
    - `missing`   Wikipedia records an attack for which the registry has no place at all.
    """
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    by_night: dict = {}
    for s in sites:
        by_night.setdefault(s["day"], []).append(s)
    city = {d["day"]: d.get("city") or {} for d in days}
    processed = {d["day"] for d in days if d.get("stats")}

    by_date: dict = {}
    for rec in snap["records"]:
        k, i = figures(rec["text"])
        by_date.setdefault(rec["date"], []).append(
            {**rec, "killed": k, "injured": i, "names": named_objects(rec["text"])})

    out = {}
    for d, recs in sorted(by_date.items()):
        nights = window(d)
        places = [p for nd in nights for p in by_night.get(nd, [])]
        theirs = [r["killed"] for r in recs if r["killed"] is not None]
        wiki_killed = max(theirs) if theirs else None
        # The same attack's toll is repeated on both sides of the 09:00 split,
        # so the larger of the two nights is what the registry says it was.
        ours_each = {nd: city.get(nd, {}).get("killed") for nd in nights}
        said = [(nd, v) for nd, v in ours_each.items() if v is not None]
        ours_night, ours_killed = max(said, key=lambda x: x[1]) if said else (None, None)
        named = sorted({n for r in recs for n in r["names"]})
        absent = [n for n in named if not has_object(n, places)]

        if not any(nd in processed for nd in nights):
            verdict, detail = "pending", "the agents have not read these nights yet"
        elif not places:
            verdict = "missing"
            detail = "no place found in the channels for either night"
        elif absent:
            verdict = "differs"
            detail = f"Wikipedia names {', '.join(absent)}; the registry has none for these nights"
        elif wiki_killed is not None and ours_killed is not None and wiki_killed != ours_killed:
            verdict = "differs"
            detail = f"Wikipedia {wiki_killed} killed, the registry {ours_killed}"
        elif wiki_killed is None and ours_killed is None:
            verdict = "no_figure"
            detail = f"neither side gives a death toll; {len(places)} places line up"
        elif wiki_killed is None:
            verdict = "no_figure"
            detail = f"Wikipedia gives no death toll; the registry has {ours_killed}"
        elif ours_killed is None:
            verdict = "no_figure"
            detail = f"Wikipedia gives {wiki_killed} killed; the registry has no figure for these nights"
        else:
            verdict = "match"
            detail = f"{wiki_killed} killed on both sides; {len(places)} places line up"
        out[d] = {"records": recs, "verdict": verdict, "detail": detail, "nights": nights,
                  "wiki_killed": wiki_killed, "ours_killed": ours_killed,
                  "ours_night": ours_night, "places": len(places),
                  "named": named, "absent": absent}

    counts = Counter(v["verdict"] for v in out.values())
    return {"taken": snap["taken"], "articles": snap["articles"], "dates": out,
            "window_note": "A Wikipedia entry for a date is compared with the registry's "
                           "nights of the evening before and that evening, because the registry "
                           "still splits one attack at 09:00.",
            "summary": {"records": len(snap["records"]), "dates": len(out),
                        "match": counts["match"], "differs": counts["differs"],
                        "no_figure": counts["no_figure"], "missing": counts["missing"],
                        "pending": counts["pending"]}}


def report_date(attack: dict) -> str:
    """The date the news carries for an attack, which is how Wikipedia dates it:
    a night is reported the next morning, a daytime attack the same day."""
    d = date.fromisoformat(attack["date"])
    return (d + timedelta(days=1)).isoformat() if attack["kind"] == "night" else attack["date"]


def crosscheck_attacks(atts: list[dict], sites: list[dict]) -> dict:
    """The same four verdicts as `crosscheck`, with attacks instead of 09:00 days.

    A Wikipedia entry for date `d` is compared with the attacks reported on `d`
    (the night that ended that morning and that day's daytime attack), fixed by
    `report_date` alone. There is no window to widen, so `missing` can now
    happen: an entry for a date on which the registry has no place at all.
    """
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    by_attack: dict = {}
    for s in sites:
        by_attack.setdefault(s["attack"], []).append(s)
    on_date: dict = {}
    for a in atts:
        on_date.setdefault(report_date(a), []).append(a)

    by_date: dict = {}
    for rec in snap["records"]:
        k, i = figures(rec["text"])
        by_date.setdefault(rec["date"], []).append(
            {**rec, "killed": k, "injured": i, "names": named_objects(rec["text"])})

    out = {}
    for d, recs in sorted(by_date.items()):
        mine = on_date.get(d, [])
        places = [p for a in mine for p in by_attack.get(a["id"], [])]
        theirs = [r["killed"] for r in recs if r["killed"] is not None]
        wiki_killed = max(theirs) if theirs else None
        said = [a["city"]["killed"] for a in mine if (a.get("city") or {}).get("killed") is not None]
        ours_killed = sum(said) if said else None
        named = sorted({n for r in recs for n in r["names"]})
        absent = [n for n in named if not has_object(n, places)]
        if not places:
            verdict, detail = "missing", "no place in the registry for the attacks reported that day"
        elif absent:
            verdict, detail = "differs", f"Wikipedia names {', '.join(absent)}; the registry has none for this attack"
        elif wiki_killed is not None and ours_killed is not None and wiki_killed != ours_killed:
            verdict, detail = "differs", f"Wikipedia {wiki_killed} killed, the registry {ours_killed}"
        elif wiki_killed is None and ours_killed is None:
            verdict, detail = "no_figure", f"neither side gives a death toll; {len(places)} places line up"
        elif wiki_killed is None:
            verdict, detail = "no_figure", f"Wikipedia gives no death toll; the registry has {ours_killed}"
        elif ours_killed is None:
            verdict, detail = "no_figure", f"Wikipedia gives {wiki_killed} killed; the registry has no figure"
        else:
            verdict, detail = "match", f"{wiki_killed} killed on both sides; {len(places)} places line up"
        out[d] = {"records": recs, "verdict": verdict, "detail": detail,
                  "attacks": [a["id"] for a in mine], "wiki_killed": wiki_killed,
                  "ours_killed": ours_killed, "places": len(places), "named": named, "absent": absent}

    counts = Counter(v["verdict"] for v in out.values())
    return {"taken": snap["taken"], "articles": snap["articles"], "dates": out,
            "window_note": "A Wikipedia entry for a date is compared with the attacks reported on that "
                           "date: the night that ended that morning and that day's daytime attack.",
            "summary": {"records": len(snap["records"]), "dates": len(out),
                        "match": counts["match"], "differs": counts["differs"],
                        "no_figure": counts["no_figure"], "missing": counts["missing"], "pending": 0}}


if __name__ == "__main__":
    s = collect()
    print(f"{len(s['records'])} records from {len(s['articles'])} articles")
