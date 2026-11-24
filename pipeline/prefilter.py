"""Step 2 — prefilter. Plain code, no model.

Two jobs:

1. **Alerts.** The city administration posts every air-raid alert and all-clear
   in a fixed format (🟡 drones, 🔴 missiles, 🟢 all clear). Those are parsed
   into a timeline directly; nothing about them needs a model.
2. **Candidate posts.** Of ~20,000 posts, a few thousand mention damage at all.
   Only those go to the reader agent, and national channels only when the post
   names the city itself (Kyiv oblast is not Kyiv).
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from pipeline.sources import (CHANNELS, CITY_ONLY, PERIOD_END, PERIOD_START,
                              channel, to_kyiv)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "telegram"

IMPACT = re.compile(
    r"уламк|влучан|падінн|загоранн|пожеж|пошкодж|зруйн|руйнуван|постраждал|"
    r"загинул|загибл|поранен|травмован|госпіталіз|вибит|приліт|"
    r"debris|\bhit\b|struck|strike|fire|damag|killed|injur|wounded|casualt",
    re.I)
KYIV_CITY = re.compile(
    r"Київ(?!щин|ськ)|Києв(?!щин|ськ)|Києві|столиц|Kyiv(?!\s+[Oo]blast|\s+region)|Kiev",
)
# Posts that only report alert levels or shelter advice, even if a damage word
# slips in ("до відбою ... уламки можуть падати").
NOISE = re.compile(r"^\s*(🟢|🟡|🔴)", re.M)


def load_posts() -> list[dict]:
    posts = []
    for handle in CHANNELS:
        path = RAW / f"{handle}.jsonl"
        if not path.exists():
            continue
        seen = {}
        for line in path.open(encoding="utf-8"):
            p = json.loads(line)
            local = to_kyiv(p["utc"])
            if not (PERIOD_START - timedelta(hours=6) <= local < PERIOD_END + timedelta(hours=6)):
                continue
            # The same text again within 30 minutes in one channel is a repost.
            key = re.sub(r"\W+", "", p["text"])[:200]
            if key and key in seen and local - seen[key] < timedelta(minutes=30):
                continue
            seen[key] = local
            posts.append({**p, "channel": handle, "local": local})
    posts.sort(key=lambda p: p["local"])
    return posts


def is_candidate(post: dict) -> bool:
    # Aggregators are collected (they show how much noise there is) but never
    # read: they repost the others and can never confirm anything.
    if CHANNELS[post["channel"]][2] == "aggregator":
        return False
    text = post["text"]
    if len(text) < 25 or not IMPACT.search(text):
        return False
    if NOISE.match(text) and len(text) < 300:
        return False
    if post["channel"] not in CITY_ONLY and not KYIV_CITY.search(text):
        return False
    return True


def parse_alerts(posts: list[dict]) -> list[dict]:
    """Alert intervals from the city administration's channel."""
    alerts, open_alert = [], None
    for p in posts:
        if p["channel"] != "VA_Kyiv":
            continue
        t = p["text"]
        if t.startswith("🟢") or "Відбій" in t[:40]:
            if open_alert:
                open_alert["end"] = p["local"]
                alerts.append(open_alert)
                open_alert = None
        elif t.startswith(("🟡", "🔴")) or "Оголошено повітряну тривогу" in t:
            kind = "missile" if t.startswith("🔴") or "ракет" in t[:120] else "drone"
            if open_alert:
                # Escalation from drones to missiles inside one alert.
                if kind == "missile":
                    open_alert["kind"] = "missile"
                continue
            open_alert = {"start": p["local"], "kind": kind, "url": p["url"]}
    out = []
    for a in alerts:
        minutes = (a["end"] - a["start"]).total_seconds() / 60
        if 0 < minutes < 24 * 60:          # a missed all-clear would run for days
            out.append({"start": a["start"].isoformat(), "end": a["end"].isoformat(),
                        "kind": a["kind"], "minutes": round(minutes), "url": a["url"]})
    return out


def attack_day(local: datetime) -> str:
    """Overnight reports belong to the night before: 00:00–08:59 counts as the
    previous date, so 'the night of 4–5 October' is filed under 4 October."""
    return (local - timedelta(hours=9)).date().isoformat()


def run() -> dict:
    posts = load_posts()
    candidates = [p for p in posts if is_candidate(p)]
    alerts = parse_alerts(posts)
    by_day: dict[str, list] = {}
    for i, p in enumerate(candidates):
        day = attack_day(p["local"])
        if not (PERIOD_START.date().isoformat() <= day < PERIOD_END.date().isoformat()):
            continue
        ch = channel(p["channel"])
        by_day.setdefault(day, []).append({
            "pid": f"{p['channel']}/{p['id']}", "channel": p["channel"],
            "channel_name": ch["name"], "voice": ch["voice"], "kind": ch["kind"],
            "time": p["local"].strftime("%Y-%m-%d %H:%M"), "url": p["url"],
            "text": p["text"][:1200], "photos": p["photos"][:4], "links": p["links"],
        })
    stats = {
        "posts_read": len(posts),
        "channels": len({p["channel"] for p in posts}),
        "posts_per_channel": {h: sum(1 for p in posts if p["channel"] == h) for h in CHANNELS},
        "candidates": sum(len(v) for v in by_day.values()),
        "alerts": len(alerts),
        "alert_minutes": sum(a["minutes"] for a in alerts),
    }
    return {"by_day": by_day, "alerts": alerts, "stats": stats}


if __name__ == "__main__":
    r = run()
    print(json.dumps(r["stats"], ensure_ascii=False, indent=1))
    for d, ps in sorted(r["by_day"].items()):
        print(d, len(ps))
