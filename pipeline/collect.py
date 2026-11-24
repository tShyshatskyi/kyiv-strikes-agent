"""Step 1 — collect. Read every post of the period from each channel.

Uses Telegram's public web preview (t.me/s/<channel>), which needs no login and
no API key. Pages go backwards 20 posts at a time until the period start.

Output: data/raw/telegram/<channel>.jsonl, one post per line:
    {"id", "utc", "text", "photos", "url", "links"}

Run:  python -m pipeline.collect            (all channels, in parallel)
      python -m pipeline.collect VA_Kyiv     (one channel)
Already collected channels are skipped unless --force is given.
"""

import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from pathlib import Path

import httpx

from pipeline.sources import CHANNELS, PERIOD_END, PERIOD_START

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "telegram"
MARGIN = timedelta(hours=6)
START_UTC = (PERIOD_START - MARGIN).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
END_UTC = (PERIOD_END + MARGIN).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
UA = {"User-Agent": "Mozilla/5.0 (compatible; kyiv-strikes-registry/2.0)"}


def fetch(client: httpx.Client, handle: str, before: int | None) -> str:
    url = f"https://t.me/s/{handle}" + (f"?before={before}" if before else "")
    for attempt in range(5):
        try:
            r = client.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
        except httpx.HTTPError:
            pass
        time.sleep(3 * (attempt + 1))
    return ""


def parse(page: str, handle: str):
    for block in re.split(r'<div class="tgme_widget_message_wrap', page)[1:]:
        post = re.search(rf'data-post="{re.escape(handle)}/(\d+)"', block, re.I)
        # The first <time> in a block can be a video's duration; the post time
        # sits in the message date link.
        when = re.search(r'tgme_widget_message_date.*?datetime="([^"]+)"', block, re.S)
        if not post or not when:
            continue
        body = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
        raw = body.group(1) if body else ""
        links = re.findall(r'<a href="(https?://[^"]+)"', raw)
        text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", raw))).strip()
        photos = re.findall(
            r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", block)
        yield {
            "id": int(post.group(1)),
            "utc": when.group(1)[:19],
            "text": text,
            "photos": photos,
            "links": [l for l in links if "t.me/" not in l][:5],
            "url": f"https://t.me/{handle}/{post.group(1)}",
        }


def collect(handle: str, force: bool = False) -> str:
    target = OUT / f"{handle}.jsonl"
    if target.exists() and not force:
        return f"{handle}: already collected ({sum(1 for _ in target.open(encoding='utf-8'))} posts)"
    kept, before, pages = {}, None, 0
    with httpx.Client(headers=UA, follow_redirects=True) as client:
        while True:
            posts = list(parse(fetch(client, handle, before), handle))
            if not posts:
                break
            pages += 1
            for p in posts:
                if START_UTC <= p["utc"] <= END_UTC:
                    kept[p["id"]] = p
            oldest = min(p["id"] for p in posts)
            if min(p["utc"] for p in posts) < START_UTC or oldest == before:
                break
            before = oldest
            time.sleep(0.5)
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for k in sorted(kept):
            f.write(json.dumps(kept[k], ensure_ascii=False) + "\n")
    tmp.replace(target)
    return f"{handle}: {pages} pages, {len(kept)} posts"


def main(argv: list[str]) -> None:
    force = "--force" in argv
    handles = [a for a in argv if not a.startswith("--")] or list(CHANNELS)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for line in pool.map(lambda h: collect(h, force), handles):
            print(line, flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
