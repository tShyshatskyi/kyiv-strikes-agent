"""segment_attacks — plain code, no model.

The unit of the registry is an *attack*: the air-raid alerts of one night (or
one day) plus the morning after, when the reports come in. It replaces the old
calendar day cut at 09:00, which split one night in two and filed its morning
summary under both halves.

1. Alerts less than two hours apart form one chain.
2. A chain is a *night* attack if it starts between 18:00 and 06:00 or runs
   over midnight; otherwise a *daytime* attack. All chains of the same night
   (or the same day) are one attack: "the night of 6–7 October" is one thing
   to the people who lived it, even if the alert was lifted for three hours.
3. An attack's window runs from its first alert to its last all-clear plus
   14 hours, the morning reports.
4. A claim goes to the attack whose window holds its post. Where two windows
   overlap (a short daytime alert after a night attack), the claim is placed by
   what it says, in this order: an impact time it states; a date its post names
   ("у ніч на 7 листопада"); whether most places it names (district and kind of
   place) were already reported under the earlier attack, which makes it that
   attack's follow-up; words about the night ("вночі", "нічної атаки"); a post
   during an alert or within two hours of its all-clear goes to that attack;
   anything later in the morning is the night's.
5. A post that names two or more dates is a recap of several attacks
   ("внаслідок атак 19 та 21 жовтня"); its claims are set aside, not counted.
6. Claims outside every window (no alert at all) are set aside and counted.

Titles and ids come from here, never from a model:
"Night of 6–7 November" → 2026-11-06-N, "Daytime attack, 19 November" → 2026-11-19-D.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

CHAIN_GAP = timedelta(hours=2)
MORNING = timedelta(hours=14)
ACTIVE_AFTER = timedelta(hours=2)
NIGHT_FROM, NIGHT_TO = 18, 6

MONTHS_UK = {"жовтня": 10, "листопада": 11, "вересня": 9, "грудня": 12}
MONTHS_EN = {"october": 10, "november": 11, "september": 9, "december": 12,
             "oct": 10, "nov": 11}
MONTH_NAME = ["", "January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]


# ---------------------------------------------------------------- attacks

def _dt(x) -> datetime:
    if isinstance(x, datetime):
        return x.replace(tzinfo=None)
    return datetime.fromisoformat(str(x)).replace(tzinfo=None)


NIGHT_ENDS = 9      # a night's alerts stop at 09:00 next morning; later ones are the day's


def _night_over(chain: list[dict], start: datetime) -> bool:
    """Would an alert starting at `start` run a night chain into the next day?"""
    first = _dt(chain[0]["start"])
    evening = first.date() if first.hour >= 12 else first.date() - timedelta(days=1)
    return start >= datetime.combine(evening + timedelta(days=1), datetime.min.time()) + timedelta(hours=NIGHT_ENDS)


def _chains(alerts: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    for a in sorted(alerts, key=lambda a: a["start"]):
        start = _dt(a["start"])
        if (out and start - max(_dt(x["end"]) for x in out[-1]) < CHAIN_GAP
                and not (_key(out[-1])[0] == "N" and _night_over(out[-1], start))):
            out[-1].append(a)
        else:
            out.append([a])
    return out


def _key(chain: list[dict]) -> tuple[str, str]:
    """(kind, date): ("N", evening date) for a night, ("D", date) for a day."""
    start = _dt(chain[0]["start"])
    end = max(_dt(a["end"]) for a in chain)
    if start.hour >= NIGHT_FROM:
        return "N", start.date().isoformat()
    if start.hour < NIGHT_TO:
        return "N", (start.date() - timedelta(days=1)).isoformat()
    if end.date() > start.date():          # began in the afternoon, ran past midnight
        return "N", start.date().isoformat()
    return "D", start.date().isoformat()


def title(kind: str, day: str) -> str:
    d = date.fromisoformat(day)
    if kind == "D":
        return f"Daytime attack, {d.day} {MONTH_NAME[d.month]}"
    n = d + timedelta(days=1)
    if n.month == d.month:
        return f"Night of {d.day}–{n.day} {MONTH_NAME[d.month]}"
    return f"Night of {d.day} {MONTH_NAME[d.month]} – {n.day} {MONTH_NAME[n.month]}"


def segment(alerts: list[dict]) -> list[dict]:
    """Alerts → attacks, oldest first."""
    groups: dict[tuple, list] = {}
    for ch in _chains(alerts):
        groups.setdefault(_key(ch), []).extend(ch)
    attacks = []
    for (kind, day), al in groups.items():
        start = min(_dt(a["start"]) for a in al)
        end = max(_dt(a["end"]) for a in al)
        minutes = {"drone": 0, "missile": 0}
        for a in al:
            minutes[a["kind"]] = minutes.get(a["kind"], 0) + a["minutes"]
        attacks.append({
            "id": f"{day}-{kind}", "kind": "night" if kind == "N" else "day", "date": day,
            "title": title(kind, day),
            "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
            "window_end": (end + MORNING).isoformat(timespec="minutes"),
            "alerts": len(al), "alert_minutes": minutes,
            "spans": [(a["start"], a["end"]) for a in sorted(al, key=lambda a: a["start"])],
        })
    attacks.sort(key=lambda a: a["start"])
    return attacks


def dates_of(attack: dict) -> set[str]:
    """Calendar dates a post can use for this attack: a night has two."""
    d = date.fromisoformat(attack["date"])
    return {attack["date"], (d + timedelta(days=1)).isoformat()} if attack["kind"] == "night" else {attack["date"]}


# ---------------------------------------------------------------- claims

_DATE_UK = re.compile(r"\b(\d{1,2})(?:\s*(?:,|та|і|й|-|–)\s*(\d{1,2}))*\s+(" + "|".join(MONTHS_UK) + r")")
_DATE_EN = re.compile(r"\b(?:(\d{1,2})\s+(" + "|".join(MONTHS_EN) + r")\.?|(" + "|".join(MONTHS_EN) + r")\.?\s+(\d{1,2}))\b", re.I)
NIGHT_WORDS = re.compile(r"вночі|нічн|у ніч|в ніч|ніч на|overnight|last night|night", re.I)
DAY_WORDS = re.compile(r"сьогодні|вдень|денн\w* атак|today|this afternoon|during the day", re.I)
_CLOCK = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")


def named_dates(text: str, year: int = 2026) -> set[str]:
    """Calendar dates a post names in words ("7 листопада", "19 та 21 жовтня", "Nov. 7")."""
    out = set()
    for m in _DATE_UK.finditer(text or ""):
        month = MONTHS_UK[m.group(3)]
        nums = re.findall(r"\d{1,2}", m.group(0))
        for n in nums:
            if 1 <= int(n) <= 31:
                out.add(f"{year}-{month:02d}-{int(n):02d}")
    for m in _DATE_EN.finditer(text or ""):
        n, mon = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        if 1 <= int(n) <= 31:
            out.add(f"{year}-{MONTHS_EN[mon.lower()]:02d}-{int(n):02d}")
    return out


_MON = "|".join(MONTHS_UK)
_NIGHT_ON = re.compile(r"ніч\w*\s+(?:на|проти)\s+(\d{1,2})\s+(" + _MON + ")", re.I)
_NIGHT_FROM_TO = re.compile(r"ніч\w*\s+з\s+(\d{1,2})\s+(?:(" + _MON + r")\s+)?на\s+(\d{1,2})\s+(" + _MON + ")", re.I)


def night_named(text: str, year: int = 2026) -> str | None:
    """'у ніч на 7 листопада' and 'в ніч з 6 на 7 листопада' both name the
    night that began on 6 November: its id is 2026-11-06-N."""
    if m := _NIGHT_FROM_TO.search(text or ""):
        month = MONTHS_UK[(m.group(2) or m.group(4)).lower()]
        return f"{year}-{month:02d}-{int(m.group(1)):02d}-N"
    if m := _NIGHT_ON.search(text or ""):
        d = date(year, MONTHS_UK[m.group(2).lower()], int(m.group(1))) - timedelta(days=1)
        return f"{d.isoformat()}-N"
    return None


def _impact_time(claim: dict, posted: datetime) -> datetime | None:
    """The impact time a claim states, on the right date: the latest such
    clock time not after the post."""
    m = _CLOCK.search(str(claim.get("time") or ""))
    if not m:
        return None
    t = posted.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
    return t if t <= posted + timedelta(minutes=5) else t - timedelta(days=1)


def _in_spans(t: datetime, attack: dict) -> bool:
    return any(_dt(s) - timedelta(minutes=30) <= t <= _dt(e) + timedelta(hours=1)
               for s, e in attack["spans"])


def place(claim: dict, text: str, attacks: list[dict], follow: str | None = None) -> tuple[str | None, str]:
    """Which attack a claim belongs to, and why. (None, reason) sets it aside.

    `follow` is an earlier attack this claim's post follows up (see assign)."""
    posted = _dt(claim["post_time"])
    cands = [a for a in attacks if _dt(a["start"]) <= posted < _dt(a["window_end"])]
    named = named_dates(text, posted.year)
    # Only dates near the post count: "on 24 February 2022" is history, not a recap.
    near = {d for d in named if abs((date.fromisoformat(d) - posted.date()).days) <= 3}
    if len(near) >= 2 and not any(near <= dates_of(a) for a in cands):
        return None, "recap of several attacks"
    if not cands:
        return None, "no alert"
    if len(cands) == 1:
        a = cands[0]
        if near and not (near & dates_of(a)):
            return None, "names another date"
        return a["id"], "window"
    if (night_id := night_named(text, posted.year)) and any(a["id"] == night_id for a in cands):
        return night_id, "named night"
    t = _impact_time(claim, posted)
    if t:
        hit = [a for a in cands if _in_spans(t, a)]
        if len(hit) == 1:
            return hit[0]["id"], "impact time"
    if near:
        hit = [a for a in cands if near & dates_of(a)]
        if len(hit) == 1:
            return hit[0]["id"], "named date"
        if len(hit) > 1 and NIGHT_WORDS.search(text):
            night = [a for a in hit if a["kind"] == "night"]
            if night:
                return night[-1]["id"], "named date, night"
    if follow and any(a["id"] == follow for a in cands):
        return follow, "follow-up"
    night = [a for a in cands if a["kind"] == "night"]
    if NIGHT_WORDS.search(text) and night:
        return night[-1]["id"], "night words"
    # "Сьогодні внаслідок атаки…" posted in the evening is the daytime attack,
    # even if the night's first alert has already sounded.
    today = [a for a in cands if a["kind"] == "day" and a["date"] == posted.date().isoformat()]
    if DAY_WORDS.search(text) and today:
        return today[-1]["id"], "day words"
    # Nothing in the post decides. A post during an attack's alerts, or soon
    # after its all-clear, is about that attack; otherwise it is about the
    # attack whose alert sounded most recently. (A night's morning recap that
    # repeats its places has already been caught as a follow-up above.)
    active = [a for a in cands if any(_dt(s) <= posted <= _dt(e) + ACTIVE_AFTER for s, e in a["spans"])]
    if active:
        return active[-1]["id"], "during alert"
    last_alert = lambda a: max(_dt(s) for s, _ in a["spans"] if _dt(s) <= posted)  # noqa: E731
    return max(cands, key=last_alert)["id"], "latest alert"


def assign(claims: list[dict], texts: dict[str, str], attacks: list[dict]) -> dict:
    """Claims (or city totals) → {attack id: [claims]}, plus what was set aside.

    `texts` maps a post id to its full text, so a claim is judged by its whole
    post and not only by the quote the reader kept."""
    by_attack: dict[str, list] = {}
    aside: list[dict] = []
    seen: dict[str, set] = {}               # attack id -> (district, kind of place) already filed
    posts: dict[str, list] = {}
    for c in sorted(claims, key=lambda c: c["post_time"]):
        posts.setdefault(c.get("post") or id(c), []).append(c)
    for pid, group in posts.items():
        text = texts.get(pid, "") or " ".join(c.get("quote", "") for c in group)
        follow = _follows(group, attacks, seen)
        for c in group:
            aid, why = place(c, text, attacks, follow)
            if aid is None:
                aside.append({**c, "aside": why})
                continue
            by_attack.setdefault(aid, []).append({**c, "placed_by": why})
            if c.get("district"):
                seen.setdefault(aid, set()).add((c["district"], c.get("target")))
    return {"by_attack": by_attack, "aside": aside}


def _follows(group: list[dict], attacks: list[dict], seen: dict) -> str | None:
    """The earlier attack a post reports on again, if any.

    A morning summary often goes out the minute a new, short alert ends, and
    by time alone it would be filed under that new alert. But if most of the
    places it names (district and kind of place) were already reported under
    an earlier attack still in its window, it is that attack's summary."""
    posted = _dt(group[0]["post_time"])
    cands = [a for a in attacks if _dt(a["start"]) <= posted < _dt(a["window_end"])]
    keyed = [(c["district"], c.get("target")) for c in group if c.get("district")]
    # Only a night followed by a daytime alert: posts during a night's own
    # alerts are that night's, whatever an earlier day had in the same district.
    if len(cands) < 2 or not keyed or cands[-1]["kind"] != "day":
        return None
    best, score = None, 0
    for a in [x for x in cands[:-1] if x["kind"] == "night"]:
        n = sum(1 for k in keyed if k in seen.get(a["id"], set()))
        if n * 2 >= len(keyed) and n > score:
            best, score = a["id"], n
    return best


def reading_days(attack: dict) -> list[str]:
    """The reader's calendar days (09:00 to 09:00) whose posts can fall in this
    attack's window. The reader keeps working on those days so its cache stays
    valid; claims are moved to attacks after reading."""
    first = (_dt(attack["start"]) - timedelta(hours=9)).date()
    last = (_dt(attack["window_end"]) - timedelta(hours=9)).date()
    out, d = [], first
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out
