"""The three agents. Each is a role, a prompt file and a schema.

    READER   (fast model)    posts  -> claims, each pinned to a verbatim quote
    MATCHER  (strong model)  claims -> distinct sites (one real impact, once)
    AUDITOR  (strong model)  checks the matcher; can send its work back

The auditor is a separate call with its own prompt and no stake in the
matcher's answer, which is the point: the same model asked "is this right?" in
the same breath it produced the answer tends to say yes.
"""

import json
from pathlib import Path

from pipeline import llm, schemas, tools

PROMPTS = Path(__file__).resolve().parent / "prompts"
READ_BATCH = 30


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def read(day: str, posts: list[dict]) -> dict:
    """Reader over a day's posts, in batches. Returns kept claims, rejected
    claims (quote not found) and city-wide totals."""
    by_pid = {p["pid"]: p for p in posts}
    kept, rejected, totals = [], [], []
    for start in range(0, len(posts), READ_BATCH):
        batch = posts[start:start + READ_BATCH]
        listing = "\n\n".join(
            f"[{p['pid']}] {p['channel_name']} · {p['time']}\n{p['text']}" for p in batch)
        answer = llm.ask("reader", _prompt("reader").replace("{posts}", listing),
                         schemas.READER)
        for imp in answer.get("impacts", []):
            post = by_pid.get(imp.get("post"))
            if not post:
                rejected.append({**imp, "why": "post id not in batch"})
                continue
            if not tools.check_quote(imp.get("quote", ""), post["text"]):
                rejected.append({**imp, "why": "quote not found in post"})
                continue
            kept.append({**imp, "channel": post["channel"], "channel_name": post["channel_name"],
                         "voice": post["voice"], "kind": post["kind"], "url": post["url"],
                         "post_time": post["time"], "photos": post["photos"]})
        for t in answer.get("city_totals", []):
            post = by_pid.get(t.get("post"))
            if post and tools.check_quote(t.get("quote", ""), post["text"]):
                totals.append({**t, "channel": post["channel"], "url": post["url"],
                               "post_time": post["time"]})
    for i, c in enumerate(kept, 1):
        c["id"] = f"c{i}"
    return {"claims": kept, "rejected": rejected, "totals": totals}


def _claims_listing(claims: list[dict]) -> str:
    rows = []
    for c in claims:
        rows.append(json.dumps({
            "id": c["id"], "source": c["channel_name"], "posted": c["post_time"],
            "district": c["district"], "address": c["address"], "place": c["place_en"],
            "target": c["target"], "weapon": c["weapon"], "effects": c["effects"],
            "time": c["time"], "quote": c["quote"]}, ensure_ascii=False))
    return "\n".join(rows)


def match(day: str, claims: list[dict], feedback: list[dict] | None = None) -> dict:
    note = ""
    if feedback:
        note = ("## The auditor rejected your previous grouping\n\n"
                "Fix every issue below. Where you disagree with one, keep your grouping "
                "but make sure the summary explains why.\n\n" +
                "\n".join(f"- {i['kind']} (sites {i['sites']}, claims {i['claims']}): "
                          f"{i['explanation']}" for i in feedback))
    prompt = (_prompt("matcher").replace("{feedback}", note).replace("{day}", day)
              .replace("{claims}", _claims_listing(claims)))
    return llm.ask("matcher", prompt, schemas.MATCHER)


def audit(day: str, claims: list[dict], sites: list[dict]) -> dict:
    listing = "\n".join(
        f"Site {i}: {json.dumps({k: s[k] for k in ('title_en', 'summary_en', 'district', 'address', 'target', 'weapon', 'claims')}, ensure_ascii=False)}"
        for i, s in enumerate(sites, 1))
    prompt = (_prompt("auditor").replace("{claims}", _claims_listing(claims))
              .replace("{sites}", listing))
    return llm.ask("auditor", prompt, schemas.AUDITOR)
