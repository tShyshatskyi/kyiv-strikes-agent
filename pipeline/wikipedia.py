"""Wikipedia as an outside check on the registry.

Wikipedia retells the same news the registry reads, so it can never confirm a
place on its own (it is not an independent witness). What it is good for is a
second opinion on whole nights: did the registry miss an attack that editors
recorded, and do the death tolls agree?

Three articles are read (English list of strikes on Kyiv, English war timeline,
Ukrainian list of autumn 2026 missile strikes). Every row or timeline entry that
names Kyiv and falls in the period becomes a record; code then lines each record
up with an attack night and compares the numbers. No model is involved.

    python -m pipeline.wikipedia          refresh the snapshot (needs WIKIMEDIA_CONTACT)
"""

import json
import os
import re
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


def night_of(rec: dict, days_with_sites: set[str]) -> str:
    """Wikipedia dates an attack by the calendar day it was reported; the
    registry files a night attack under the evening it started. Night attacks
    are therefore moved back a day, evening attacks are not."""
    d = date.fromisoformat(rec["date"])
    if re.search(r"вечірн|evening", rec["text"], re.I):
        return d.isoformat()
    prev = (d - timedelta(days=1)).isoformat()
    if prev not in days_with_sites and d.isoformat() in days_with_sites:
        return d.isoformat()
    return prev


def crosscheck(days: list[dict], sites: list[dict]) -> dict:
    """Per attack night: what Wikipedia says, and whether it agrees."""
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    with_sites = {s["day"] for s in sites}
    city = {d["day"]: d.get("city") or {} for d in days}
    processed = {d["day"] for d in days if d.get("stats")}
    by_night: dict = {}
    for rec in snap["records"]:
        k, i = figures(rec["text"])
        night = night_of(rec, with_sites)
        by_night.setdefault(night, []).append({**rec, "killed": k, "injured": i})
    out = {}
    for night, recs in sorted(by_night.items()):
        ours = city.get(night, {}).get("killed")
        theirs = [r["killed"] for r in recs if r["killed"] is not None]
        if night not in processed:
            verdict = "pending"           # the agents have not read this night yet
        elif night not in with_sites:
            verdict = "missing"           # Wikipedia records an attack we have no place for
        elif theirs and ours is not None and max(theirs) != ours:
            verdict = "differs"
        else:
            verdict = "agrees"
        out[night] = {"records": recs, "verdict": verdict, "wiki_killed": max(theirs) if theirs else None}
    return {"taken": snap["taken"], "articles": snap["articles"], "nights": out,
            "summary": {"records": len(snap["records"]), "nights": len(out),
                        "agrees": sum(v["verdict"] == "agrees" for v in out.values()),
                        "differs": sum(v["verdict"] == "differs" for v in out.values()),
                        "missing": sum(v["verdict"] == "missing" for v in out.values()),
                        "pending": sum(v["verdict"] == "pending" for v in out.values())}}


if __name__ == "__main__":
    s = collect()
    print(f"{len(s['records'])} records from {len(s['articles'])} articles")
