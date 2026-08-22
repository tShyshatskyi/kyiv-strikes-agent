"""Graph nodes.

The routing functions at the top are pure and are what the tests exercise: they
are where tool output becomes control flow, which is the whole point of the
agent. The async nodes below them do the talking to MCP.
"""

import json
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

from common.schemas import IncidentClaim

MAX_ROUNDS = 3

PROMPT = (Path(__file__).parent / "prompts" / "extract.md").read_text(encoding="utf-8")

_UA_MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11,
    "грудня": 12,
}

# Search entry points, confirmed working by scripts/probe_playwright.py.
# The official bodies are absent on purpose: their sites answer a headless
# browser with a bot-protection wall, so official facts reach us through
# newsrooms that cite them. See data/source_registry.yaml.
SEARCH_PAGES = {
    "suspilne": "https://suspilne.media/search/?query={q}",
    "pravda": "https://www.pravda.com.ua/search/?query={q}",
    "hromadske": "https://hromadske.ua/?s={q}",
}


# --- pure routing -----------------------------------------------------------

def extract_date(query: str) -> str:
    """Pull an event date out of the user's question."""
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", query)
    if iso:
        return iso.group(0)

    ua = re.search(r"\b(\d{1,2})\s+([а-яіїєґ]+)\s+(\d{4})\b", query.lower())
    if ua and ua.group(2) in _UA_MONTHS:
        return date(int(ua.group(3)), _UA_MONTHS[ua.group(2)],
                    int(ua.group(1))).isoformat()

    raise ValueError(
        f"No date found in {query!r}. Include one, for example "
        "'удар по Києву 17 червня 2026' or '2026-06-17'."
    )


def parse_request(state: dict) -> dict:
    """Normalize the question and initialize the loop."""
    return {
        **state,
        "event_date": extract_date(state["query"]),
        "round": 0,
        "articles": [],
        "claims": [],
        "assessments": [],
        "unreachable": [],
        "notes": [],
        "searched": [],
    }


def should_continue(state: dict) -> str:
    """The conditional edge. Tool output decides what happens next.

    Going back costs a round and buys evidence; going forward writes what we
    have. The budget exists so that an incident nobody else reported still gets
    recorded, honestly labelled, rather than looping forever.
    """
    if state["round"] >= MAX_ROUNDS:
        return "persist"

    reconciliation = state.get("reconciliation") or {}
    location = state.get("location") or {}

    if location.get("district_conflict"):
        return "discover_sources"

    level = reconciliation.get("verification_level", "unverified")
    if level in ("unverified", "single_source"):
        return "discover_sources"

    # "corroborated" is a stopping point, even though missing_for_next_level
    # still names the official source that would raise it to well_documented.
    # That gap is reported honestly but does not drive another round: the
    # official sites are unfetchable, so an official voice only ever arrives by
    # chance, inside an article that happens to cite one. More searching burns
    # the budget for a gain we cannot steer towards.
    if reconciliation.get("has_conflict"):
        return "discover_sources"
    return "persist"


def next_targets(state: dict) -> list[str]:
    """Which search pages to try next, given what we already have.

    Groups that already spoke are skipped: repeating them cannot raise the
    verification level, because they would collapse into the same voice.
    """
    reconciliation = state.get("reconciliation") or {}
    used = set(reconciliation.get("usable_groups", []))
    already = set(state.get("searched", []))
    remaining = [g for g in SEARCH_PAGES if g not in used and g not in already]
    return remaining or [g for g in SEARCH_PAGES if g not in already]


def build_search_url(group: str, query: str) -> str:
    return SEARCH_PAGES[group].format(q=quote_plus(query))


def unwrap(raw) -> dict:
    """Read the envelope a custom tool answered with."""
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        raw = raw[0].get("text", "")
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def as_text(raw) -> str:
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0].get("text", "")
    return str(raw)


def article_links(page_text: str, domain: str, limit: int = 4) -> list[str]:
    """Pull plausible article URLs for one domain out of a page snapshot."""
    urls = re.findall(r"/url:\s*(\S+)", page_text)
    seen, keep = set(), []
    for url in urls:
        if domain not in url or url in seen:
            continue
        # Section fronts and tag pages carry no incident facts.
        if re.search(r"/(tag|tags|category|search|author)/", url):
            continue
        if not re.search(r"/\d{4,}|/\d{4}/\d{2}|-\d{4,}", url):
            continue
        seen.add(url)
        keep.append(url)
        if len(keep) >= limit:
            break
    return keep


# --- nodes ------------------------------------------------------------------

def make_nodes(tools: dict, extractor):
    """Bind the graph nodes to discovered tools and an extraction function.

    `extractor(url, published_at, text) -> list[IncidentClaim]` is injected so
    the graph can be exercised without a model in the loop.
    """

    async def discover_sources(state: dict) -> dict:
        navigate, snapshot = tools["browser_navigate"], tools["browser_snapshot"]
        articles = list(state["articles"])
        unreachable = list(state["unreachable"])
        searched = list(state["searched"])
        notes = list(state["notes"])

        targets = next_targets(state)
        if state["round"] > 0:
            notes.append(f"Round {state['round'] + 1}: trying {', '.join(targets)}.")

        for group in targets[:2]:
            domain = {"suspilne": "suspilne.media", "pravda": "pravda.com.ua",
                      "hromadske": "hromadske.ua"}[group]
            searched.append(group)
            try:
                await navigate.ainvoke({"url": build_search_url(group, state["query"])})
                page = as_text(await snapshot.ainvoke({}))
            except Exception as exc:
                unreachable.append(f"{group} search: {type(exc).__name__}: {exc}")
                continue

            for url in article_links(page, domain):
                if any(a["url"] == url for a in articles):
                    continue
                try:
                    await navigate.ainvoke({"url": url})
                    body = as_text(await snapshot.ainvoke({}))
                except Exception as exc:
                    unreachable.append(f"{url}: {type(exc).__name__}: {exc}")
                    continue
                articles.append({"url": url, "domain": domain, "text": body})

        return {**state, "round": state["round"] + 1, "articles": articles,
                "unreachable": unreachable, "searched": searched, "notes": notes}

    async def extract_claims(state: dict) -> dict:
        claims = list(state["claims"])
        known = {str(c.source_url) for c in claims}
        for article in state["articles"]:
            if article["url"] in known:
                continue
            claims.extend(extractor(article["url"], state["event_date"],
                                    article["text"]))
        return {**state, "claims": claims}

    async def assess(state: dict) -> dict:
        if not state["claims"]:
            return {**state, "assessments": []}
        sources = [
            {"url": str(c.source_url),
             "domain": str(c.source_url).split("/")[2].removeprefix("www."),
             "published_at": c.published_at.isoformat(),
             "cited_sources": getattr(c, "cited_sources", []) or []}
            for c in state["claims"]
        ]
        body = unwrap(await tools["assess_source_credibility"].ainvoke(
            {"sources": sources, "event_date": state["event_date"]}
        ))
        if not body["ok"]:
            return {**state, "assessments": [],
                    "notes": state["notes"] + [f"credibility: {body['error']['code']}"]}
        return {**state, "assessments": body["data"]["assessments"]}

    async def locate(state: dict) -> dict:
        if not state["claims"]:
            return {**state, "location": None}
        first = state["claims"][0]
        body = unwrap(await tools["resolve_strike_location"].ainvoke({
            "location_text": first.location_text,
            "claimed_district": first.claimed_district,
            "context_districts": [c.claimed_district for c in state["claims"]
                                  if c.claimed_district],
            "context_text": " ".join(c.quote for c in state["claims"]),
        }))
        if not body["ok"]:
            return {**state, "location": None,
                    "notes": state["notes"] + [
                        f"geocoding failed ({body['error']['code']}): "
                        f"{body['error']['message']}"]}
        return {**state, "location": body["data"]}

    async def reconcile(state: dict) -> dict:
        if not state["claims"]:
            return {**state, "reconciliation": {
                "fields": {}, "verification_level": "unverified",
                "missing_for_next_level": ["Any usable source at all."],
                "usable_groups": [], "needs_review": False, "review_reason": None}}
        body = unwrap(await tools["corroborate_incident_claims"].ainvoke({
            "claims": [json.loads(c.model_dump_json()) for c in state["claims"]],
            "assessments": state["assessments"],
        }))
        return {**state, "reconciliation": body["data"]}

    async def persist(state: dict) -> dict:
        location = state.get("location")
        if not location or location.get("lat") is None:
            return {**state, "result": {
                "action": "skipped",
                "reason": "no usable location, so there is nothing to put on a map"}}

        reconciliation = state["reconciliation"]
        fields = reconciliation["fields"]
        first = state["claims"][0]

        needs_review = bool(location.get("needs_review")) or bool(
            reconciliation.get("needs_review"))
        reasons = [r for r in (location.get("review_reason"),
                               reconciliation.get("review_reason")) if r]

        incident = {
            "occurred_at": (first.occurred_at or first.published_at).isoformat(),
            "lat": location["lat"], "lon": location["lon"],
            "district": location["district"],
            "claimed_district": location.get("claimed_district"),
            "address_text": first.location_text,
            "structure_type": fields.get("structure_type", {}).get(
                "value", first.structure_type),
            "killed": fields.get("killed", {}).get("value"),
            "injured": fields.get("injured", {}).get("value"),
            "damage": first.damage,
            "verification_level": reconciliation["verification_level"],
            "geocode_confidence": location["confidence"],
            "needs_review": needs_review,
            "review_reason": " ".join(reasons) if reasons else None,
        }
        body = unwrap(await tools["upsert_incident"].ainvoke({
            "incident": incident,
            "claims": [json.loads(c.model_dump_json()) for c in state["claims"]],
            "assessments": state["assessments"],
            "reconciliation": reconciliation,
        }))
        return {**state, "result": body.get("data") or body.get("error")}

    return {
        "parse_request": parse_request,
        "discover_sources": discover_sources,
        "extract_claims": extract_claims,
        "assess": assess,
        "locate": locate,
        "reconcile": reconcile,
        "persist": persist,
    }


def llm_extractor():
    """The real extraction step: one model call per article, schema-constrained."""
    from langchain_openai import ChatOpenAI

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. The agent needs a model to read "
            "articles; the MCP server does not. Put it in .env (see .env.example)."
        )

    model = ChatOpenAI(
        model=os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5"),
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
        temperature=0,
    ).with_structured_output(IncidentClaim)

    def extract(url: str, published_at: str, text: str) -> list[IncidentClaim]:
        prompt = PROMPT.format(url=url, published_at=published_at, text=text[:20000])
        try:
            claim = model.invoke(prompt)
        except Exception:
            return []
        return [claim] if claim else []

    return extract
