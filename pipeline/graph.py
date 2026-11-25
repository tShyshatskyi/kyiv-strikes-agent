"""The workflow, as a LangGraph graph.

    collect ─► prefilter ─► read (×calendar day, in parallel) ─► segment_attacks
                                                                     │
                     ┌───────────── one branch per attack ───────────┘
                     ▼
        match ─► validate ─► audit ──approve──► finalize ─► geocode ─► publish
          ▲          │          │
          └──────────┴─revise───┘   (validate once; the auditor at most twice,
                                     the second time only for merge errors)

The reader works on calendar days (09:00 to 09:00), as it always has, so its
cached answers stay valid: it reads posts one by one and does not need to know
which attack a post belongs to. Code then moves every claim into its attack
(pipeline.attacks), and everything after that works on one attack at a time.

The edges out of `validate` and `audit` are the agentic part: what the checks
find decides whether the matcher's work is published or sent back. Every step
writes to a trace.
"""

import asyncio
import json
import operator
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from pipeline import agents, attacks, collect, llm, prefilter, tools, validate
from pipeline.schemas import DISTRICTS_EN

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("KSR_OUT") or ROOT / "data" / "out")
MAX_REVISIONS = 2                      # the second one only for merge errors
MERGE_KINDS = {"missed_merge", "wrong_merge"}
PARALLEL = asyncio.Semaphore(6)
_T0 = time.monotonic()


def log(unit: str, step: str, message: str) -> dict:
    line = f"[{time.monotonic() - _T0:6.0f}s] {unit:12} {step:9} {message}"
    print(line, flush=True)
    return {"day": unit, "step": step, "message": message,
            "at": datetime.now().isoformat(timespec="seconds")}


async def _call(fn, *args):
    async with PARALLEL:
        return await asyncio.to_thread(fn, *args)


# --- per-attack subgraph ------------------------------------------------------

class AttackState(TypedDict, total=False):
    attack: dict
    claims: list[dict]
    totals: list[dict]
    proposal: dict
    feedback: list[dict]
    validated: int
    fixes: list[str]
    audits: list[dict]
    revisions: int
    result: dict
    trace: Annotated[list[dict], operator.add]


async def match_node(s: AttackState) -> dict:
    a, feedback = s["attack"], s.get("feedback") or None
    proposal = await _call(agents.match, a, s["claims"], feedback)
    label = "regrouped" if feedback else "grouped"
    return {"proposal": proposal, "feedback": [],
            "trace": [log(a["id"], "matcher", f"{len(s['claims'])} claims {label} into "
                          f"{len(proposal['sites'])} sites")]}


def validate_node(s: AttackState) -> dict:
    a = s["attack"]
    sites = [dict(x) for x in s["proposal"]["sites"]]
    v = validate.check(s["claims"], sites)
    for site in v["sites"]:
        site["title"] = validate.build_title(validate.clean_object(site.get("object_en"), site["target"]),
                                             site.get("district"))
    proposal = {**s["proposal"], "sites": v["sites"]}
    send_back = bool(v["issues"]) and s.get("validated", 0) < 1
    msg = f"{len(v['fixes'])} fixed in code, {len(v['issues'])} to redo"
    return {"proposal": proposal, "fixes": (s.get("fixes") or []) + v["fixes"],
            "feedback": v["issues"] if send_back else [],
            "validated": s.get("validated", 0) + (1 if send_back else 0),
            "trace": [log(a["id"], "validate", msg + (" -> back to matcher" if send_back else ""))]}


def after_validate(s: AttackState) -> str:
    return "match" if s.get("feedback") else "audit"


async def audit_node(s: AttackState) -> dict:
    a = s["attack"]
    verdict = await _call(agents.audit, a, s["claims"], s["proposal"]["sites"])
    kinds = ", ".join(sorted({i["kind"] for i in verdict["issues"]})) or "no issues"
    again = _send_back(verdict, s.get("revisions", 0))
    return {"audits": (s.get("audits") or []) + [verdict],
            "feedback": verdict["issues"] if again else [],
            "revisions": s.get("revisions", 0) + (1 if again else 0),
            "trace": [log(a["id"], "auditor", f"{verdict['verdict']}: {kinds}")]}


def _send_back(verdict: dict, revisions: int) -> bool:
    if verdict["verdict"] != "revise" or not verdict["issues"]:
        return False
    if revisions == 0:
        return True
    # A second round only for the error that double-counts or merges places.
    return revisions < MAX_REVISIONS and any(i["kind"] in MERGE_KINDS for i in verdict["issues"])


def after_audit(s: AttackState) -> str:
    return "match" if s.get("feedback") else "finalize"


# What a reader sees when the auditor's own public note is unusable.
GENERIC_NOTE = {
    "missed_merge": "This may be the same strike as another record from this attack.",
    "wrong_merge": "This record may combine reports of two different places.",
    "wrong_field": "The weapon, district or type of place is uncertain.",
    "unsupported_summary": "Part of the description may not be backed by the sources.",
    "not_this_attack": "This report may belong to a different attack.",
    "not_kyiv_city": "This place may be outside the city of Kyiv.",
    "lost_claim": "Some reports may be missing from this record.",
}


def public_note(issue: dict) -> str:
    note = (issue.get("public_note_en") or "").strip()
    if (not note or len(note) > 140 or validate.CYRILLIC.search(note)
            or validate.public_text_problems(note) or validate.SERVICE.search(note)):
        return GENERIC_NOTE.get(issue["kind"], "The auditor left a note on this record.")
    return note


def public_summary(text: str, fallback: str) -> str:
    """The matcher's summary with any sentence for the pipeline dropped."""
    from pipeline.build_site import strip_service_text
    text = strip_service_text(text or "")
    if not validate.public_text_problems(text) and not validate.SERVICE.search(text):
        return text
    import re
    keep = [x for x in re.split(r"(?<=[.!?])\s+", text)
            if not validate.public_text_problems(x) and not validate.SERVICE.search(x)]
    return " ".join(keep) or fallback


def finalize_node(s: AttackState) -> dict:
    """Turn the approved grouping into records. Plain code: it enforces what the
    prompts only ask for — every claim used once, none lost, nothing out of
    area, no service text in public."""
    a, claims = s["attack"], s.get("claims") or []
    by_id = {c["id"]: c for c in claims}
    used, sites, dropped = set(), [], []
    audits = s.get("audits") or []
    last = audits[-1] if audits else {}
    open_issues = last.get("issues", []) if last.get("verdict") == "revise" else []
    drop_idx = {i for iss in open_issues if iss["kind"] in ("not_kyiv_city", "not_this_attack")
                for i in iss["sites"]}

    proposed = list((s.get("proposal") or {}).get("sites", []))
    lost = [c for c in claims if not any(c["id"] in p["claims"] for p in proposed)]
    for c in lost:                      # a claim the matcher dropped gets its own site
        proposed.append({"claims": [c["id"]], "object_en": None, "summary_en": "",
                         "district": c["district"], "address": c["address"],
                         "target": c["target"], "weapon": c["weapon"], "time": c["time"]})

    for n, p in enumerate(proposed, 1):
        mine = [by_id[i] for i in p["claims"] if i in by_id and i not in used]
        if not mine:
            continue
        used.update(c["id"] for c in mine)
        obj = validate.clean_object(p.get("object_en"), p["target"])
        title = validate.build_title(obj, p.get("district"))
        if n in drop_idx:
            dropped.append({"title": title, "why": [i["explanation"] for i in open_issues
                                                    if n in i["sites"]][0]})
            continue
        photos = [ph for c in sorted(mine, key=lambda c: c["kind"] != "official")
                  for ph in c["photos"]][:3]
        sources, seen = [], set()
        for c in sorted(mine, key=lambda c: c["post_time"]):
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            sources.append({"channel": c["channel"], "name": c["channel_name"], "kind": c["kind"],
                            "url": c["url"], "posted": c["post_time"], "quote": c["quote"]})
        mine_issues = [i for i in open_issues if n in i["sites"]]
        flag_kinds = [i["kind"] for i in mine_issues]
        conf = tools.confirmation(sources, disputed=tools.is_disputed(flag_kinds))
        sites.append({
            "id": f"{a['id']}-{len(sites) + 1}", "attack": a["id"], "day": a["date"],
            "title": title, "object": obj,
            "summary": public_summary(p.get("summary_en"), title + "."),
            "district": p["district"], "district_en": DISTRICTS_EN.get(p["district"] or ""),
            "address": p["address"], "target": p["target"], "weapon": p["weapon"],
            "time": p["time"], "first_report": sources[0]["posted"],
            **tools.site_figures(mine), **conf,
            "sources": sources, "photos": photos,
            "flags": [i["explanation"] for i in mine_issues], "flag_kinds": flag_kinds,
            "notes": list(dict.fromkeys(public_note(i) for i in mine_issues)),
            "addresses": list(dict.fromkeys(x for x in [p["address"]] + [c["address"] for c in mine] if x)),
            "claims": len(mine), "placed_by": sorted({c.get("placed_by", "") for c in mine}),
        })

    totals = sorted(s.get("totals") or [], key=lambda t: t["post_time"])
    city = {"killed": None, "injured": None, "sources": []}
    for t in totals:
        for f in ("killed", "injured"):
            if t.get(f) is not None:
                city[f] = t[f]
        city["sources"].append(t["url"])
    examples = []
    for k, au in enumerate(audits):
        final = k == len(audits) - 1
        for iss in au["issues"]:
            examples.append({"kind": iss["kind"], "explanation": iss["explanation"],
                             "public_note": public_note(iss), "round": k + 1,
                             "fixed": not (final and au["verdict"] == "revise")})
    summary = public_summary((s.get("proposal") or {}).get("attack_summary_en", ""), "")
    if validate.DATES.search(summary or ""):
        summary = ""                    # the header carries the date; never two dates
    # A summary written before the auditor removed a place may still name it.
    for d in dropped:
        words = [w for w in re.findall(r"[A-Za-z']{5,}", d["title"].split(" – ")[0])
                 if w.lower() not in ("building", "buildings", "storey", "house")]
        if any(w.lower() in summary.lower() for w in words):
            summary = ""
    result = {
        **{k: a[k] for k in ("id", "kind", "date", "title", "start", "end", "window_end",
                             "alerts", "alert_minutes")},
        "summary": summary, "sites": sites, "dropped": dropped, "city_totals": city,
        "audit_examples": examples, "validate_fixes": s.get("fixes") or [],
        "stats": {"claims": len(claims), "sites": len(sites), "audit_rounds": len(audits),
                  "revisions": s.get("revisions", 0), "validate_sent_back": s.get("validated", 0),
                  "issues_found": [i["kind"] for au in audits for i in au["issues"]],
                  "open_issues": len(open_issues), "dropped": len(dropped)},
    }
    return {"result": result, "trace": [log(a["id"], "finalize", f"{len(sites)} sites recorded"
                                            + (f", {len(dropped)} removed by auditor" if dropped else ""))]}


def build_attack_graph():
    g = StateGraph(AttackState)
    g.add_node("match", match_node)
    g.add_node("validate", validate_node)
    g.add_node("audit", audit_node)
    g.add_node("finalize", finalize_node)
    g.add_conditional_edges(START, lambda s: "match" if s.get("claims") else "finalize",
                            {"match": "match", "finalize": "finalize"})
    g.add_edge("match", "validate")
    g.add_conditional_edges("validate", after_validate, {"match": "match", "audit": "audit"})
    g.add_conditional_edges("audit", after_audit, {"match": "match", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()


ATTACK_GRAPH = build_attack_graph()


# --- top-level graph ---------------------------------------------------------

class RunState(TypedDict, total=False):
    only: list[str]
    by_day: dict
    alerts: list[dict]
    stats: dict
    attacks: list[dict]
    read: Annotated[list[dict], operator.add]
    results: Annotated[list[dict], operator.add]
    failed: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]
    aside: dict
    sites: list[dict]


def collect_node(s: RunState) -> dict:
    if os.environ.get("KSR_SKIP_COLLECT") != "1":
        collect.main([])
    return {"trace": [log("all", "collect", "Telegram channels up to date")]}


def selected(all_attacks: list[dict], only: list[str]) -> list[dict]:
    """`only` holds attack ids (2026-11-06-N), dates (2026-11-06) or months (2026-11)."""
    if not only:
        return all_attacks
    return [a for a in all_attacks if any(a["id"] == o or a["date"] == o or a["date"].startswith(o + "-")
                                          for o in only)]


def prefilter_node(s: RunState) -> dict:
    r = prefilter.run()
    all_attacks = attacks.segment(r["alerts"])
    chosen = selected(all_attacks, s.get("only") or [])
    need = {d for a in chosen for d in attacks.reading_days(a)}
    by_day = {d: v for d, v in r["by_day"].items() if d in need}
    st = r["stats"]
    return {"by_day": by_day, "alerts": r["alerts"], "stats": st, "attacks": all_attacks,
            "trace": [log("all", "prefilter", f"{st['posts_read']} posts from {st['channels']} "
                          f"channels -> {st['candidates']} mention damage; {st['alerts']} alerts -> "
                          f"{len(all_attacks)} attacks; reading {len(by_day)} days for "
                          f"{len(chosen)} attacks")]}


def fan_out_read(s: RunState) -> list[Send]:
    return [Send("read_day", {"day": d, "posts": p}) for d, p in sorted(s["by_day"].items())]


async def read_day(s: dict) -> dict:
    # One day failing (a timeout, a usage limit) must not sink the others.
    # Its work so far is cached, so the next run picks it up where it stopped.
    try:
        r = await _call(agents.read, s["day"], s["posts"])
    except Exception as exc:
        return {"failed": [s["day"]],
                "trace": [log(s["day"], "error", f"{type(exc).__name__}: {str(exc)[:200]}")]}
    msg = f"{len(s['posts'])} posts -> {len(r['claims'])} claims"
    if r["rejected"]:
        msg += f", {len(r['rejected'])} rejected (quote not in the post)"
    return {"read": [{"day": s["day"], "posts": s["posts"], **r}],
            "trace": [log(s["day"], "reader", msg)]}


def segment_node(s: RunState) -> dict:
    """Claims → attacks. See pipeline.attacks for the rules."""
    texts = {p["pid"]: p["text"] for r in s["read"] for p in r["posts"]}
    claims = [{**c, "rid": f"{r['day']}/{c['id']}"} for r in s["read"] for c in r["claims"]]
    totals = [{**t, "rid": f"{r['day']}/total"} for r in s["read"] for t in r["totals"]]
    chosen = selected(s["attacks"], s.get("only") or [])
    placed = attacks.assign(claims, texts, s["attacks"])
    placed_t = attacks.assign(totals, texts, s["attacks"])
    aside: dict = {}
    for c in placed["aside"]:
        aside[c["aside"]] = aside.get(c["aside"], 0) + 1
    out = []
    for a in chosen:
        mine = sorted(placed["by_attack"].get(a["id"], []), key=lambda c: c["post_time"])
        for i, c in enumerate(mine, 1):
            c["id"] = f"c{i}"
        out.append({**a, "claims": mine, "totals": placed_t["by_attack"].get(a["id"], [])})
    n = sum(len(a["claims"]) for a in out)
    return {"attacks": out, "aside": {"claims": aside, "examples": [
                {k: c.get(k) for k in ("rid", "post_time", "quote", "aside")} for c in placed["aside"][:40]]},
            "trace": [log("all", "segment", f"{len(claims)} claims -> {n} in {len(out)} attacks; "
                          f"set aside: {aside or 'none'}")]}


def fan_out_attacks(s: RunState):
    sends = [Send("analyse_attack", {"attack": {k: v for k, v in a.items() if k not in ("claims", "totals")},
                                     "claims": a["claims"], "totals": a["totals"]})
             for a in s["attacks"] if a["claims"] or a["totals"]]
    return sends or "geocode"


async def analyse_attack(s: dict) -> dict:
    try:
        out = await ATTACK_GRAPH.ainvoke({**s, "trace": []})
    except Exception as exc:
        return {"failed": [s["attack"]["id"]],
                "trace": [log(s["attack"]["id"], "error", f"{type(exc).__name__}: {str(exc)[:200]}")]}
    return {"results": [out["result"]], "trace": out["trace"]}


def place_sites(sites: list[dict]) -> None:
    """Put every site on the map, in place. Also used by pipeline.recompute."""
    for site in sites:
        # Try the matcher's address, then every address a source gave.
        geo = None
        for address in site.get("addresses") or [None]:
            geo = tools.geocode(address, site["district"])
            if geo["precision"] in ("building", "street"):
                break
        site["geo"] = geo
        if site["geo"]["district"] and not site["district"]:
            site["district"] = site["geo"]["district"]
            site["district_en"] = DISTRICTS_EN.get(site["district"])
            if site.get("object"):          # the title names the district, so it follows
                site["title"] = validate.build_title(site["object"], site["district"])


def geocode_node(s: RunState) -> dict:
    sites = [site for d in s.get("results") or [] for site in d["sites"]]
    place_sites(sites)
    placed = sum(1 for x in sites if x["geo"]["lat"] is not None)
    precise = sum(1 for x in sites if x["geo"]["precision"] in ("building", "street"))
    return {"sites": sites, "trace": [log("all", "geocode", f"{placed}/{len(sites)} sites on the map, "
                                         f"{precise} at address level")]}


def publish_node(s: RunState) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    results = sorted(s.get("results") or [], key=lambda d: d["start"])
    registry = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "unit": "attack",
        "period": [min(d["date"] for d in results), max(d["date"] for d in results)] if results else [],
        "collection": s["stats"], "alerts": s["alerts"],
        "attacks": [{k: v for k, v in d.items() if k != "sites"} for d in results],
        "sites": sorted(s.get("sites") or [], key=lambda x: (x["day"], x["first_report"])),
        "set_aside": s.get("aside") or {},
        "reading": {"days": len(s.get("read") or []),
                    "posts": sum(len(r["posts"]) for r in s.get("read") or []),
                    "claims": sum(len(r["claims"]) + len(r["rejected"]) for r in s.get("read") or []),
                    "rejected_quotes": sum(len(r["rejected"]) for r in s.get("read") or [])},
        "llm": llm.spent(),
        "failed": sorted(s.get("failed") or []),
    }
    (OUT / "registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    trace = sorted(s["trace"], key=lambda t: t["at"])
    (OUT / "trace.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in trace),
                                     encoding="utf-8")
    return {"trace": [log("all", "publish", f"{len(registry['sites'])} sites in "
                          f"{len(results)} attacks -> {OUT / 'registry.json'}")]}


def build_graph():
    g = StateGraph(RunState)
    g.add_node("collect", collect_node)
    g.add_node("prefilter", prefilter_node)
    g.add_node("read_day", read_day)
    g.add_node("segment", segment_node)
    g.add_node("analyse_attack", analyse_attack)
    g.add_node("geocode", geocode_node)
    g.add_node("publish", publish_node)
    g.add_edge(START, "collect")
    g.add_edge("collect", "prefilter")
    g.add_conditional_edges("prefilter", fan_out_read, ["read_day"])
    g.add_edge("read_day", "segment")
    g.add_conditional_edges("segment", fan_out_attacks, ["analyse_attack", "geocode"])
    g.add_edge("analyse_attack", "geocode")
    g.add_edge("geocode", "publish")
    g.add_edge("publish", END)
    return g.compile()


async def run(only: list[str] | None = None) -> dict:
    return await build_graph().ainvoke({"only": only or [], "read": [], "results": [],
                                        "failed": [], "trace": []})
