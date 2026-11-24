"""The workflow, as a LangGraph graph.

    collect ─► prefilter ─┬─► analyse_day (×54, in parallel) ─► geocode ─► publish
                          │
                          └ one branch per attack day, each a subgraph:

        read ─► check ─► match ─► audit ──approve──► finalize
                            ▲        │
                            └─revise─┘   (at most MAX_REVISIONS times)

The edge out of `audit` is the agentic part: what the auditor finds decides
whether the matcher's work is published or sent back with the auditor's notes.
Every step writes to a trace, which the site shows.
"""

import asyncio
import json
import operator
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from pipeline import agents, collect, llm, prefilter, tools
from pipeline.schemas import DISTRICTS_EN

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "out"
MAX_REVISIONS = 1
PARALLEL = asyncio.Semaphore(6)
_T0 = time.monotonic()


def log(day: str, step: str, message: str) -> dict:
    line = f"[{time.monotonic() - _T0:6.0f}s] {day:10} {step:9} {message}"
    print(line, flush=True)
    return {"day": day, "step": step, "message": message,
            "at": datetime.now().isoformat(timespec="seconds")}


# --- per-day subgraph ---------------------------------------------------------

class DayState(TypedDict, total=False):
    day: str
    posts: list[dict]
    claims: list[dict]
    rejected: list[dict]
    totals: list[dict]
    proposal: dict
    audits: list[dict]
    revisions: int
    result: dict
    trace: Annotated[list[dict], operator.add]


async def _call(fn, *args):
    async with PARALLEL:
        return await asyncio.to_thread(fn, *args)


async def read_node(s: DayState) -> dict:
    r = await _call(agents.read, s["day"], s["posts"])
    return {"claims": r["claims"], "rejected": r["rejected"], "totals": r["totals"],
            "trace": [log(s["day"], "reader", f"{len(s['posts'])} posts -> "
                          f"{len(r['claims']) + len(r['rejected'])} claims")]}


def check_node(s: DayState) -> dict:
    n = len(s["rejected"])
    return {"trace": [log(s["day"], "check", f"{len(s['claims'])} quotes verified"
                          + (f", {n} rejected (not in the post)" if n else ""))]}


async def match_node(s: DayState) -> dict:
    audits = s.get("audits") or []
    feedback = audits[-1]["issues"] if audits and audits[-1]["verdict"] == "revise" else None
    proposal = await _call(agents.match, s["day"], s["claims"], feedback)
    label = "regrouped after audit" if feedback else "grouped"
    return {"proposal": proposal, "revisions": s.get("revisions", 0) + (1 if feedback else 0),
            "trace": [log(s["day"], "matcher", f"{len(s['claims'])} claims {label} into "
                          f"{len(proposal['sites'])} sites")]}


async def audit_node(s: DayState) -> dict:
    verdict = await _call(agents.audit, s["day"], s["claims"], s["proposal"]["sites"])
    kinds = ", ".join(sorted({i["kind"] for i in verdict["issues"]})) or "no issues"
    return {"audits": (s.get("audits") or []) + [verdict],
            "trace": [log(s["day"], "auditor", f"{verdict['verdict']}: {kinds}")]}


def after_audit(s: DayState) -> str:
    last = s["audits"][-1]
    if last["verdict"] == "revise" and last["issues"] and s.get("revisions", 0) < MAX_REVISIONS:
        return "match"
    return "finalize"


def finalize_node(s: DayState) -> dict:
    """Turn the approved grouping into records. Plain code: it enforces what the
    prompts only ask for — every claim used once, none lost, nothing out of area."""
    day, claims = s["day"], s["claims"]
    by_id = {c["id"]: c for c in claims}
    used, sites, dropped = set(), [], []
    last_audit = (s.get("audits") or [{}])[-1]
    open_issues = last_audit.get("issues", []) if last_audit.get("verdict") == "revise" else []
    drop_idx = {i for iss in open_issues if iss["kind"] in ("not_kyiv_city", "not_this_attack")
                for i in iss["sites"]}

    proposed = list((s.get("proposal") or {}).get("sites", []))
    lost = [c for c in claims if not any(c["id"] in p["claims"] for p in proposed)]
    for c in lost:                      # a claim the matcher dropped gets its own site
        proposed.append({"claims": [c["id"]], "title_en": c["place_en"].capitalize(),
                         "summary_en": c["place_en"].capitalize() + ".",
                         "district": c["district"], "address": c["address"],
                         "target": c["target"], "weapon": c["weapon"], "time": c["time"]})

    for n, p in enumerate(proposed, 1):
        mine = [by_id[i] for i in p["claims"] if i in by_id and i not in used]
        if not mine:
            continue
        used.update(c["id"] for c in mine)
        if n in drop_idx:
            dropped.append({"title": p["title_en"], "why": [i["explanation"] for i in open_issues
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
        flags = [i["explanation"] for i in open_issues if n in i["sites"]]
        conf = tools.confirmation(sources, disputed=bool(flags))
        sites.append({
            "id": f"{day}-{len(sites) + 1}", "day": day,
            "title": p["title_en"], "summary": p["summary_en"],
            "district": p["district"], "district_en": DISTRICTS_EN.get(p["district"] or ""),
            "address": p["address"], "target": p["target"], "weapon": p["weapon"],
            "time": p["time"], "first_report": sources[0]["posted"],
            **tools.site_figures(mine), **conf,
            "sources": sources, "photos": photos, "flags": flags,
            "addresses": list(dict.fromkeys(a for a in [p["address"]] + [c["address"] for c in mine] if a)),
            "claims": len(mine),
        })

    totals = sorted(s.get("totals") or [], key=lambda t: t["post_time"])
    city = {"killed": None, "injured": None}
    for t in totals:
        for f in city:
            if t.get(f) is not None:
                city[f] = t[f]
    audits = s.get("audits") or []
    # What the auditor caught, and whether the matcher's next attempt fixed it.
    # Issues raised before the final audit were sent back and redone; issues in
    # a final "revise" were not, and stay on the records as flags.
    examples = []
    for n, a in enumerate(audits):
        final = n == len(audits) - 1
        for iss in a["issues"]:
            examples.append({"kind": iss["kind"], "explanation": iss["explanation"],
                             "round": n + 1, "fixed": not (final and a["verdict"] == "revise")})
    result = {
        "day": day, "summary": (s.get("proposal") or {}).get("night_summary_en", ""),
        "sites": sites, "dropped": dropped, "city_totals": city,
        "audit_examples": examples,
        "stats": {"posts": len(s["posts"]), "claims": len(claims),
                  "rejected_quotes": len(s.get("rejected") or []),
                  "sites": len(sites), "audit_rounds": len(audits),
                  "revisions": s.get("revisions", 0),
                  "issues_found": [i["kind"] for a in audits for i in a["issues"]],
                  "open_issues": len(open_issues), "dropped": len(dropped)},
    }
    return {"result": result, "trace": [log(day, "finalize", f"{len(sites)} sites recorded"
                                            + (f", {len(dropped)} removed by auditor" if dropped else ""))]}


def build_day_graph():
    g = StateGraph(DayState)
    g.add_node("read", read_node)
    g.add_node("check", check_node)
    g.add_node("match", match_node)
    g.add_node("audit", audit_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "read")
    g.add_edge("read", "check")
    g.add_conditional_edges("check", lambda s: "match" if s["claims"] else "finalize",
                            {"match": "match", "finalize": "finalize"})
    g.add_edge("match", "audit")
    g.add_conditional_edges("audit", after_audit, {"match": "match", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()


DAY_GRAPH = build_day_graph()


# --- top-level graph ---------------------------------------------------------

class RunState(TypedDict, total=False):
    only_days: list[str]
    by_day: dict
    alerts: list[dict]
    stats: dict
    days: Annotated[list[dict], operator.add]
    failed: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]
    sites: list[dict]


def collect_node(s: RunState) -> dict:
    import os
    if os.environ.get("KSR_SKIP_COLLECT") != "1":
        collect.main([])
    return {"trace": [log("all", "collect", "Telegram channels up to date")]}


def prefilter_node(s: RunState) -> dict:
    r = prefilter.run()
    by_day = r["by_day"]
    if s.get("only_days"):
        by_day = {d: v for d, v in by_day.items() if d in s["only_days"]}
    st = r["stats"]
    return {"by_day": by_day, "alerts": r["alerts"], "stats": st,
            "trace": [log("all", "prefilter", f"{st['posts_read']} posts from {st['channels']} "
                          f"channels -> {st['candidates']} mention damage; {st['alerts']} alerts")]}


def fan_out(s: RunState) -> list[Send]:
    return [Send("analyse_day", {"day": d, "posts": p}) for d, p in sorted(s["by_day"].items())]


async def analyse_day(s: dict) -> dict:
    # One day failing (a timeout, a usage limit) must not sink the other 53.
    # Its work so far is cached, so the next run picks it up where it stopped.
    try:
        out = await DAY_GRAPH.ainvoke({"day": s["day"], "posts": s["posts"], "trace": []})
    except Exception as exc:
        return {"failed": [s["day"]],
                "trace": [log(s["day"], "error", f"{type(exc).__name__}: {str(exc)[:200]}")]}
    return {"days": [out["result"]], "trace": out["trace"]}


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


def geocode_node(s: RunState) -> dict:
    sites = [site for d in s["days"] for site in d["sites"]]
    place_sites(sites)
    placed = sum(1 for x in sites if x["geo"]["lat"] is not None)
    precise = sum(1 for x in sites if x["geo"]["precision"] in ("building", "street"))
    return {"sites": sites, "trace": [log("all", "geocode", f"{placed}/{len(sites)} sites on the map, "
                                         f"{precise} at address level")]}


def publish_node(s: RunState) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    days = sorted(s["days"], key=lambda d: d["day"])
    registry = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "period": [min(d["day"] for d in days), max(d["day"] for d in days)] if days else [],
        "collection": s["stats"], "alerts": s["alerts"],
        "days": [{k: v for k, v in d.items() if k != "sites"} for d in days],
        "sites": sorted(s["sites"], key=lambda x: (x["day"], x["first_report"])),
        "llm": llm.spent(),
        "failed_days": sorted(s.get("failed") or []),
    }
    (OUT / "registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    trace = sorted(s["trace"], key=lambda t: t["at"])
    (OUT / "trace.jsonl").write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in trace),
                                     encoding="utf-8")
    return {"trace": [log("all", "publish", f"{len(registry['sites'])} sites -> data/out/registry.json")]}


def build_graph():
    g = StateGraph(RunState)
    g.add_node("collect", collect_node)
    g.add_node("prefilter", prefilter_node)
    g.add_node("analyse_day", analyse_day)
    g.add_node("geocode", geocode_node)
    g.add_node("publish", publish_node)
    g.add_edge(START, "collect")
    g.add_edge("collect", "prefilter")
    g.add_conditional_edges("prefilter", fan_out, ["analyse_day"])
    g.add_edge("analyse_day", "geocode")
    g.add_edge("geocode", "publish")
    g.add_edge("publish", END)
    return g.compile()


async def run(only_days: list[str] | None = None) -> dict:
    return await build_graph().ainvoke({"only_days": only_days or [], "days": [], "failed": [],
                                        "trace": []})
