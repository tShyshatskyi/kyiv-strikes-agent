"""End-to-end graph tests.

Playwright and the model are stubbed; the custom MCP server is real, running in
its own process over stdio. What is being tested is the loop: that tool results
decide what the graph does next.
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools

from agent.graph import build_graph
from common.schemas import IncidentClaim

REPO = Path(__file__).resolve().parent.parent

SUSP = "https://suspilne.media/kyiv/1111111-udar/"
HROM = "https://hromadske.ua/suspilstvo/269686-masovanyi-udar-po-kyyevu/"

SEARCH_PAGE = f"""
- link "Масований удар по Києву: є загиблі та поранені" [ref=e2]:
  - /url: {SUSP}
- link "Наслідки нічного удару по Києву: зруйновано будинок" [ref=e3]:
  - /url: {HROM}
"""


@asynccontextmanager
async def custom_tools(db_path: Path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server"],
        cwd=str(REPO),
        env={
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(REPO),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "KSM_OFFLINE": "1",
            "KSM_DB_PATH": str(db_path),
            "NOMINATIM_CONTACT": "test@example.com",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            yield {t.name: t for t in tools}


class FakeBrowser:
    """Stands in for Playwright: serves canned pages and counts navigations."""

    def __init__(self, pages: dict, fail: set | None = None):
        self.pages = pages
        self.fail = fail or set()
        self.visited: list[str] = []
        self._current = ""

    def tools(self) -> dict:
        outer = self

        class _Nav:
            name = "browser_navigate"

            async def ainvoke(self, args):
                url = args["url"]
                outer.visited.append(url)
                if url in outer.fail:
                    raise RuntimeError(f"HTTP 404 for {url}")
                outer._current = outer.pages.get(url, "")
                return ""

        class _Snap:
            name = "browser_snapshot"

            async def ainvoke(self, args=None):
                return [{"type": "text", "text": outer._current}]

        return {"browser_navigate": _Nav(), "browser_snapshot": _Snap()}


def claim_for(url, killed=3, published="2026-06-17T09:00:00Z", cited=None,
              district="Солом'янський"):
    payload = IncidentClaim(
        source_url=url,
        published_at=published,
        occurred_at="2026-06-17T02:10:00Z",
        location_text="вул. Освіти, 4, Київ",
        claimed_district=district,
        structure_type="residential",
        killed=killed,
        damage=["fire"],
        quote="Унаслідок влучання загинули троє людей на вул. Освіти, 4.",
    )
    object.__setattr__(payload, "__dict__", payload.__dict__ | {
        "cited_sources": cited or []})
    return payload


def scripted_extractor(by_url: dict):
    """Returns whatever claims the test scripted for that article."""
    def extract(url, published_at, text):
        return by_url.get(url, [])
    return extract


def search_pages(page: str) -> dict:
    from agent.nodes import DISCOVERY, build_search_url
    return {build_search_url(g, "удар по Києву 17 червня 2026"): page
            for g in DISCOVERY}


# --- the happy path ---------------------------------------------------------

async def test_the_agent_stops_as_soon_as_two_voices_agree(tmp_path):
    browser = FakeBrowser(
        search_pages(SEARCH_PAGE) | {SUSP: "article one", HROM: "article two"}
    )
    extractor = scripted_extractor({
        SUSP: [claim_for(SUSP)],
        HROM: [claim_for(HROM, published="2026-06-17T09:40:00Z")],
    })

    async with custom_tools(tmp_path / "g.sqlite") as tools:
        graph = build_graph(tools | browser.tools(), extractor)
        final = await graph.ainvoke({"query": "удар по Києву 17 червня 2026"})

    # Both outlets are read in the first round, so two voices cost one round
    # and the remaining budget is not spent.
    assert final["round"] == 1
    assert final["reconciliation"]["verification_level"] == "corroborated"
    assert final["result"]["action"] == "written"
    assert [site["action"] for site in final["result"]["sites"]] == ["created"]


async def test_the_written_record_carries_the_reconciled_value(tmp_path):
    browser = FakeBrowser(
        search_pages(SEARCH_PAGE) | {SUSP: "one", HROM: "two"}
    )
    extractor = scripted_extractor({
        SUSP: [claim_for(SUSP, killed=3)],
        HROM: [claim_for(HROM, killed=5, published="2026-06-17T20:00:00Z")],
    })

    db = tmp_path / "g.sqlite"
    async with custom_tools(db) as tools:
        graph = build_graph(tools | browser.tools(), extractor)
        final = await graph.ainvoke({"query": "удар по Києву 17 червня 2026"})

    # The later, higher count supersedes the earlier one.
    assert final["reconciliation"]["fields"]["killed"]["value"] == 5

    from mcp_server.db import connect
    row = connect(db).execute("SELECT killed, district FROM incidents").fetchone()
    assert row["killed"] == 5
    assert row["district"] == "Солом'янський"


# --- the loop ---------------------------------------------------------------

async def test_a_single_voice_makes_the_agent_search_again(tmp_path):
    """Only one article exists, and it never becomes two. The agent should try
    other outlets, exhaust its budget, and still record what it has."""
    browser = FakeBrowser(search_pages(f'- link "Удар по Києву: зруйновано житловий будинок":\n  - /url: {SUSP}\n')
                          | {SUSP: "article one"})
    extractor = scripted_extractor({SUSP: [claim_for(SUSP)]})

    async with custom_tools(tmp_path / "g.sqlite") as tools:
        graph = build_graph(tools | browser.tools(), extractor)
        final = await graph.ainvoke({"query": "удар по Києву 17 червня 2026"})

    # Both outlets are tried in the first round; there is nowhere left to look,
    # so a second round would only re-fetch the same pages.
    assert final["round"] == 1, "should have tried every outlet"
    assert final["reconciliation"]["verification_level"] == "single_source"
    assert [site["action"] for site in final["result"]["sites"]] == ["created"]
    assert final["searched"].count("suspilne") == 1


async def test_the_agent_looks_at_outlets_it_has_not_tried_yet(tmp_path):
    browser = FakeBrowser(search_pages(f'- link "Удар по Києву: зруйновано житловий будинок":\n  - /url: {SUSP}\n')
                          | {SUSP: "article one"})
    extractor = scripted_extractor({SUSP: [claim_for(SUSP)]})

    async with custom_tools(tmp_path / "g.sqlite") as tools:
        graph = build_graph(tools | browser.tools(), extractor)
        final = await graph.ainvoke({"query": "удар по Києву 17 червня 2026"})

    assert set(final["searched"]) == {"suspilne", "hromadske"}


# --- failure ----------------------------------------------------------------

async def test_an_unreachable_article_is_recorded_and_the_run_continues(tmp_path):
    """Failure scenario A: one source 404s. The run must finish on the rest and
    say plainly which source was lost."""
    browser = FakeBrowser(
        search_pages(SEARCH_PAGE) | {SUSP: "article one", HROM: "article two"},
        fail={HROM},
    )
    extractor = scripted_extractor({
        SUSP: [claim_for(SUSP)],
        HROM: [claim_for(HROM)],
    })

    async with custom_tools(tmp_path / "g.sqlite") as tools:
        graph = build_graph(tools | browser.tools(), extractor)
        final = await graph.ainvoke({"query": "удар по Києву 17 червня 2026"})

    assert any(HROM in note for note in final["unreachable"])
    assert [site["action"] for site in final["result"]["sites"]] == ["created"]
    # Losing the second voice costs evidence, and the record says so.
    assert final["reconciliation"]["verification_level"] == "single_source"


async def test_a_district_disagreement_reaches_the_review_queue(tmp_path):
    browser = FakeBrowser(
        search_pages(SEARCH_PAGE) | {SUSP: "one", HROM: "two"}
    )
    extractor = scripted_extractor({
        SUSP: [claim_for(SUSP, district="Оболонський")],
        HROM: [claim_for(HROM, district="Оболонський",
                         published="2026-06-17T09:40:00Z")],
    })

    db = tmp_path / "g.sqlite"
    async with custom_tools(db) as tools:
        graph = build_graph(tools | browser.tools(), extractor)
        final = await graph.ainvoke({"query": "удар по Києву 17 червня 2026"})

    assert final["location"]["district_conflict"] is True

    from mcp_server.db import connect
    from mcp_server.incidents_store import pending_review, to_geojson
    conn = connect(db)
    queue = pending_review(conn)
    assert len(queue) == 1
    assert "Оболонський" in queue[0]["review_reason"]
    # Held out of the verified map until a person settles it.
    assert to_geojson(conn)["features"] == []


async def test_a_question_with_no_date_fails_before_any_browsing(tmp_path):
    browser = FakeBrowser({})
    async with custom_tools(tmp_path / "g.sqlite") as tools:
        graph = build_graph(tools | browser.tools(), scripted_extractor({}))
        with pytest.raises(ValueError):
            await graph.ainvoke({"query": "удар по Києву"})
    assert browser.visited == []
