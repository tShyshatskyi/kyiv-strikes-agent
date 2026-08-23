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

from common.schemas import ArticleExtraction, IncidentClaim

MAX_ROUNDS = 3
MAX_ARTICLES_PER_OUTLET = 4

PROMPT = (Path(__file__).parent / "prompts" / "extract.md").read_text(encoding="utf-8")

_UA_MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11,
    "грудня": 12,
}

# How to find articles at each outlet, established by scripts/probe_search.py
# against the live sites rather than guessed.
#
# Only hromadske answers a plain search URL. Suspilne's /search/ returns 404 and
# its real search is a JavaScript overlay that ignores a scripted click; Pravda,
# nv.ua, rbc.ua and others answer a search URL with HTTP 403. Search engines are
# walled off even harder. What every one of them does serve is a section feed —
# so for those outlets we read the feed and pick articles by their headline.
#
# Article pages themselves fetch fine everywhere. It is only the finding that is
# hard, which is why discovery differs per outlet while reading does not.
DISCOVERY = {
    "hromadske": {
        "domain": "hromadske.ua",
        "url": "https://hromadske.ua/search?q={q}",
        "kind": "search",
    },
    "suspilne": {
        "domain": "suspilne.media",
        "url": "https://suspilne.media/kyiv/",
        "kind": "feed",          # the outlet's Kyiv section
    },
    "pravda": {
        "domain": "pravda.com.ua",
        "url": "https://www.pravda.com.ua/news/",
        "kind": "feed",
    },
}

# A headline worth opening names both a place in Kyiv and something happening
# to it. Filtering here rather than after extraction saves a model call per
# irrelevant article, and there are a lot of irrelevant articles in a feed.
KYIV_IN_TITLE = re.compile(r"київ|києв|києм|столиц|киев", re.I)
STRIKE_IN_TITLE = re.compile(
    r"удар|обстріл|атак|влучан|уламк|ракет|дрон|шахед|бпла|вибух|"
    r"загин|поранен|зруйнов|пошкодж|пожеж|приліт",
    re.I,
)

# Kyiv oblast is not Kyiv. These name places outside the city that a Kyiv-ish
# headline can otherwise look like, and geocoding would reject them anyway —
# this just avoids paying for the model call first.
OBLAST_ONLY = re.compile(
    r"бориспіл|бровар|ірпін|буча|вишгород|фастів|біла церква|обухів|"
    r"вишнев|област",
    re.I,
)


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
    """Which outlets to try next, given what we already have.

    Outlets that already spoke are skipped: going back to one cannot raise the
    verification level, because its articles collapse into the same voice.
    """
    reconciliation = state.get("reconciliation") or {}
    used = set(reconciliation.get("usable_groups", []))
    already = set(state.get("searched", []))
    remaining = [g for g in DISCOVERY if g not in used and g not in already]
    return remaining or [g for g in DISCOVERY if g not in already]


def build_search_url(group: str, query: str) -> str:
    """The page to open for this outlet. A feed ignores the query by design."""
    return DISCOVERY[group]["url"].format(q=quote_plus(query))


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


def article_candidates(page_text: str, domain: str) -> list[tuple[str, str]]:
    """Pull (headline, url) pairs for one domain out of a page snapshot.

    Headlines matter because several outlets number their articles instead of
    naming them: a Pravda url says /news/2026/08/23/8049976/ and nothing more,
    so the only way to tell a strike report from a football result before
    opening it is the link text.
    """
    pairs = re.findall(r'link "([^"]{15,200})"[^\n]*\n\s*- /url: (\S+)', page_text)
    seen, out = set(), []
    for title, url in pairs:
        if domain not in url or url in seen:
            continue
        if re.search(r"/(tag|tags|category|search|author|event)/", url):
            continue
        if not re.search(r"/\d{4,}|/\d{4}/\d{2}|-\d{4,}", url):
            continue
        seen.add(url)
        out.append((title.strip(), url))
    return out


def worth_opening(title: str) -> bool:
    """Does this headline promise a strike on Kyiv itself?"""
    if OBLAST_ONLY.search(title) and not KYIV_IN_TITLE.search(title):
        return False
    return bool(KYIV_IN_TITLE.search(title) and STRIKE_IN_TITLE.search(title))


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


def group_by_location(claims: list) -> dict[str, list]:
    """Group claims by the place they describe.

    One night hits several addresses, and each address is reported by several
    outlets. Grouping on the normalized address is what turns "eleven claims"
    into "three impact sites, each with its own evidence".
    """
    from mcp_server.geo import normalize_address

    grouped: dict[str, list] = {}
    for claim in claims:
        grouped.setdefault(normalize_address(claim.location_text), []).append(claim)
    return grouped


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
            outlet = DISCOVERY[group]
            domain = outlet["domain"]
            searched.append(group)
            try:
                await navigate.ainvoke({"url": build_search_url(group, state["query"])})
                page = as_text(await snapshot.ainvoke({}))
            except Exception as exc:
                unreachable.append(f"{group}: {type(exc).__name__}: {exc}")
                continue

            candidates = article_candidates(page, domain)
            chosen = [(t, u) for t, u in candidates if worth_opening(t)]
            notes.append(
                f"{group}: {len(candidates)} articles listed, "
                f"{len(chosen)} headlines look like a strike on Kyiv"
            )

            for title, url in chosen[:MAX_ARTICLES_PER_OUTLET]:
                if any(a["url"] == url for a in articles):
                    continue
                try:
                    await navigate.ainvoke({"url": url})
                    body = as_text(await snapshot.ainvoke({}))
                except Exception as exc:
                    unreachable.append(f"{url}: {type(exc).__name__}: {exc}")
                    continue
                articles.append({"url": url, "domain": domain,
                                 "title": title, "text": body})

        return {**state, "round": state["round"] + 1, "articles": articles,
                "unreachable": unreachable, "searched": searched, "notes": notes}

    async def extract_claims(state: dict) -> dict:
        claims = list(state["claims"])
        attack_claims = list(state.get("attack_claims", []))
        read = set(state.get("read_urls", []))

        for article in state["articles"]:
            if article["url"] in read:
                continue
            read.add(article["url"])
            extracted = extractor(article["url"], state["event_date"],
                                  article["text"])
            if isinstance(extracted, ArticleExtraction):
                claims.extend(extracted.incidents)
                if extracted.attack is not None:
                    attack_claims.append(extracted.attack)
            else:                       # a plain list of incident claims
                claims.extend(extracted)

        notes = list(state["notes"])
        for failure in getattr(extractor, "failures", [])[len(state.get(
                "extraction_failures", [])):]:
            notes.append(f"could not read: {failure}")

        return {**state, "claims": claims, "attack_claims": attack_claims,
                "read_urls": sorted(read), "notes": notes,
                "extraction_failures": list(getattr(extractor, "failures", []))}

    async def assess(state: dict) -> dict:
        every_claim = state["claims"] + state.get("attack_claims", [])
        if not every_claim:
            return {**state, "assessments": []}
        seen, sources = set(), []
        for c in every_claim:
            url = str(c.source_url)
            if url in seen:
                continue
            seen.add(url)
            sources.append(
                {"url": url,
                 "domain": url.split("/")[2].removeprefix("www."),
                 "published_at": c.published_at.isoformat(),
                 "cited_sources": getattr(c, "cited_sources", []) or []})
        body = unwrap(await tools["assess_source_credibility"].ainvoke(
            {"sources": sources, "event_date": state["event_date"]}
        ))
        if not body["ok"]:
            return {**state, "assessments": [],
                    "notes": state["notes"] + [f"credibility: {body['error']['code']}"]}
        return {**state, "assessments": body["data"]["assessments"]}

    async def locate(state: dict) -> dict:
        """Geocode every distinct place the reporting describes, not just one."""
        grouped = group_by_location(state["claims"])
        if not grouped:
            return {**state, "sites": [], "location": None}

        context_districts = [c.claimed_district for c in state["claims"]
                             if c.claimed_district]
        context_text = " ".join(c.quote for c in state["claims"])

        sites, notes = [], list(state["notes"])
        for key, claims in grouped.items():
            first = claims[0]
            body = unwrap(await tools["resolve_strike_location"].ainvoke({
                "location_text": first.location_text,
                "claimed_district": first.claimed_district,
                "context_districts": context_districts,
                "context_text": context_text,
            }))
            if not body["ok"]:
                notes.append(f"{first.location_text}: geocoding failed "
                             f"({body['error']['code']})")
                continue
            sites.append({"key": key, "claims": claims, "location": body["data"]})

        # The routing check needs one location: surface a disagreeing site if
        # there is one, since that is what would send the agent back.
        headline = next((s["location"] for s in sites
                         if s["location"].get("district_conflict")),
                        sites[0]["location"] if sites else None)
        return {**state, "sites": sites, "notes": notes, "location": headline}

    async def reconcile(state: dict) -> dict:
        empty = {"fields": {}, "verification_level": "unverified",
                 "missing_for_next_level": ["Any usable source at all."],
                 "usable_groups": [], "needs_review": False,
                 "review_reason": None, "has_conflict": False}
        if not state["claims"]:
            return {**state, "reconciliation": empty, "sites": state.get("sites", [])}

        sites = []
        for site in state.get("sites", []):
            body = unwrap(await tools["corroborate_incident_claims"].ainvoke({
                "claims": [json.loads(c.model_dump_json()) for c in site["claims"]],
                "assessments": state["assessments"],
            }))
            sites.append(site | {"reconciliation": body["data"]})

        # The run as a whole is only as good as its weakest site: if any one of
        # them is thin or contradictory, there is still work worth doing.
        order = {"unverified": 0, "single_source": 1, "corroborated": 2,
                 "well_documented": 3}
        weakest = min((s["reconciliation"] for s in sites),
                      key=lambda r: order[r["verification_level"]], default=empty)
        return {**state, "sites": sites, "reconciliation": weakest}

    async def write_attack(state: dict) -> dict:
        """Write the night-level record first, so impacts can point at it."""
        claims = state.get("attack_claims") or []
        if not claims:
            return {**state, "attack": None, "attack_id": None}
        body = unwrap(await tools["upsert_attack"].ainvoke({
            "claims": [json.loads(c.model_dump_json()) for c in claims],
            "assessments": state["assessments"],
        }))
        if not body["ok"]:
            return {**state, "attack": None, "attack_id": None,
                    "notes": state["notes"] + [
                        f"attack record failed: {body['error']['code']}"]}
        return {**state, "attack": body["data"],
                "attack_id": body["data"]["attack_id"]}

    async def persist(state: dict) -> dict:
        all_sites = state.get("sites", [])
        sites = [s for s in all_sites if s["location"].get("lat") is not None]

        # A place we know was hit but cannot put on a map is reported, not
        # dropped in silence.
        unplaced = [s["claims"][0].location_text for s in all_sites
                    if s["location"].get("lat") is None]
        notes = list(state["notes"]) + [
            f"could not be placed on the map: {address}" for address in unplaced]

        if not sites:
            return {**state, "notes": notes, "result": {
                "action": "skipped",
                "reason": "no usable location, so there is nothing to put on a map"}}

        written = []
        for site in sites:
            location = site["location"]
            reconciliation = site.get("reconciliation") or state["reconciliation"]
            fields = reconciliation["fields"]
            first = site["claims"][0]

            reasons = [r for r in (location.get("review_reason"),
                                   reconciliation.get("review_reason")) if r]
            incident = {
                "attack_id": state.get("attack_id"),
                "occurred_at": (first.occurred_at or first.published_at).isoformat(),
                "lat": location["lat"], "lon": location["lon"],
                "district": location["district"],
                "claimed_district": location.get("claimed_district"),
                "address_text": first.location_text,
                "structure_type": fields.get("structure_type", {}).get(
                    "value", first.structure_type),
                # Per-address casualties only. A city-wide figure lives on the
                # attack; repeating it here would multiply it by the site count.
                "killed": fields.get("killed", {}).get("value"),
                "injured": fields.get("injured", {}).get("value"),
                "damage": first.damage,
                "verification_level": reconciliation["verification_level"],
                "geocode_confidence": location["confidence"],
                "needs_review": bool(location.get("needs_review"))
                                or bool(reconciliation.get("needs_review")),
                "review_reason": " ".join(reasons) if reasons else None,
            }
            body = unwrap(await tools["upsert_incident"].ainvoke({
                "incident": incident,
                "claims": [json.loads(c.model_dump_json()) for c in site["claims"]],
                "assessments": state["assessments"],
                "reconciliation": reconciliation,
            }))
            written.append(body.get("data") or body.get("error"))

        return {**state, "notes": notes,
                "result": {"action": "written", "sites": written,
                           "unplaced": unplaced}}

    async def check_totals(state: dict) -> dict:
        """Re-run the attack tool now that impacts exist.

        The cross-level check — do the per-address deaths add up to more than
        the city reported — can only run once there are impacts to sum.
        """
        if not state.get("attack_claims"):
            return state
        body = unwrap(await tools["upsert_attack"].ainvoke({
            "claims": [json.loads(c.model_dump_json())
                       for c in state["attack_claims"]],
            "assessments": state["assessments"],
        }))
        if not body["ok"]:
            return state
        return {**state, "attack": body["data"]}

    return {
        "parse_request": parse_request,
        "discover_sources": discover_sources,
        "extract_claims": extract_claims,
        "assess": assess,
        "locate": locate,
        "reconcile": reconcile,
        "write_attack": write_attack,
        "persist": persist,
        "check_totals": check_totals,
    }


def llm_extractor():
    """The real extraction step: one model call per article, schema-constrained."""
    from langchain_openai import ChatOpenAI

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. The agent needs a model to read "
            "articles; the MCP server does not. Put it in .env (see .env)."
        )

    # The endpoint is configurable so the same code runs against OpenRouter, a
    # Gemini OpenAI-compatible endpoint, or a local Ollama, with no code change.
    # Whichever is used gets documented in the README rather than hard-coded.
    model = ChatOpenAI(
        model=os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5"),
        api_key=key,
        base_url=os.environ.get("OPENROUTER_BASE_URL",
                                "https://openrouter.ai/api/v1"),
        temperature=0,
    ).with_structured_output(ArticleExtraction)

    failures: list[str] = []

    def extract(url: str, published_at: str, text: str) -> ArticleExtraction:
        prompt = PROMPT.format(url=url, published_at=published_at, text=text[:20000])
        try:
            result = model.invoke(prompt)
        except Exception as exc:
            # One unreadable article must not end a run, but a swallowed error
            # is how you end up staring at "0 claims" with no idea why. Keep it
            # and let the report say so.
            failures.append(f"{url}: {type(exc).__name__}: {str(exc)[:200]}")
            return ArticleExtraction()
        return result or ArticleExtraction()

    extract.failures = failures
    return extract
