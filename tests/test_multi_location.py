"""One night, several addresses.

The thing worth testing here is not that three rows appear. It is that the
city-wide death toll does *not* appear three times.
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.graph import build_graph
from common.schemas import ArticleExtraction, AttackClaim, IncidentClaim
from mcp_server.db import connect

REPO = Path(__file__).resolve().parent.parent

SUSP = "https://suspilne.media/kyiv/1111111-udar/"
HROM = "https://hromadske.ua/suspilstvo/269686-masovanyi-udar-po-kyyevu/"

SEARCH_PAGE = f"""
- link "Масований удар по Києву: є загиблі та поранені" [ref=e2]:
  - /url: {SUSP}
- link "Наслідки нічного удару по Києву: зруйновано будинок" [ref=e3]:
  - /url: {HROM}
"""

# Three real Kyiv addresses in three different districts.
SITES = [
    ("вул. Освіти, 4, Київ", "Солом'янський"),
    ("просп. Оболонський, 12, Київ", "Оболонський"),
    ("вул. Кибальчича, 3, Київ", "Дніпровський"),
]


@asynccontextmanager
async def custom_tools(db_path: Path):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_server"], cwd=str(REPO),
        env={"PYTHONUTF8": "1", "PYTHONPATH": str(REPO),
             "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
             "KSM_OFFLINE": "1", "KSM_DB_PATH": str(db_path),
             "NOMINATIM_CONTACT": "test@example.com"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield {t.name: t for t in await load_mcp_tools(session)}


class FakeBrowser:
    def __init__(self, pages: dict):
        self.pages = pages
        self._current = ""

    def tools(self) -> dict:
        outer = self

        class _Nav:
            async def ainvoke(self, args):
                outer._current = outer.pages.get(args["url"], "")
                return ""

        class _Snap:
            async def ainvoke(self, args=None):
                return [{"type": "text", "text": outer._current}]

        return {"browser_navigate": _Nav(), "browser_snapshot": _Snap()}


def incident_claim(url, address, district, killed=None, published="2026-06-17T09:00:00Z"):
    return IncidentClaim(
        source_url=url, published_at=published,
        occurred_at="2026-06-17T02:10:00Z",
        location_text=address, claimed_district=district,
        structure_type="residential", killed=killed, damage=["fire"],
        quote=f"Зафіксовано влучання за адресою {address}.",
    )


def attack_claim(url, killed=5, published="2026-06-17T09:00:00Z"):
    return AttackClaim(
        source_url=url, published_at=published,
        occurred_at="2026-06-17T02:00:00Z",
        citywide_killed=killed, citywide_injured=23,
        weapons=[{"kind": "shahed", "launched": 200, "intercepted": 187},
                 {"kind": "cruise_missile", "launched": 12, "intercepted": 9}],
        quote="Унаслідок нічної атаки на Київ загинули пʼятеро людей.",
    )


def geocodable_sites():
    """Only addresses with a recorded fixture can be geocoded offline."""
    return [("вул. Освіти, 4, Київ", "Солом'янський")]


def scripted(by_url: dict):
    def extract(url, published_at, text):
        return by_url.get(url, ArticleExtraction())
    return extract


def search_pages(page: str) -> dict:
    from agent.nodes import DISCOVERY, build_search_url
    return {build_search_url(g, "удар по Києву 17 червня 2026"): page
            for g in DISCOVERY}


async def run(db: Path, extraction_by_url: dict):
    browser = FakeBrowser(search_pages(SEARCH_PAGE)
                          | {SUSP: "article one", HROM: "article two"})
    async with custom_tools(db) as tools:
        graph = build_graph(tools | browser.tools(), scripted(extraction_by_url))
        return await graph.ainvoke({"query": "удар по Києву 17 червня 2026"})


# --- several places in one run ----------------------------------------------

async def test_every_distinct_address_becomes_its_own_site(tmp_path):
    """Two articles, three addresses between them, one night."""
    extraction = {
        SUSP: ArticleExtraction(
            attack=attack_claim(SUSP),
            incidents=[incident_claim(SUSP, a, d) for a, d in SITES]),
        HROM: ArticleExtraction(
            attack=attack_claim(HROM, published="2026-06-17T09:40:00Z"),
            incidents=[incident_claim(HROM, a, d, published="2026-06-17T09:40:00Z")
                       for a, d in SITES]),
    }
    final = await run(tmp_path / "m.sqlite", extraction)

    assert len(final["sites"]) == 3
    keys = {site["key"] for site in final["sites"]}
    assert len(keys) == 3


async def test_the_same_address_written_two_ways_is_one_site(tmp_path):
    """'вул. Освіти, 4' and 'ВУЛ. ОСВІТИ 4' are one place."""
    extraction = {
        SUSP: ArticleExtraction(
            attack=attack_claim(SUSP),
            incidents=[incident_claim(SUSP, "вул. Освіти, 4, Київ", "Солом'янський")]),
        HROM: ArticleExtraction(
            attack=attack_claim(HROM, published="2026-06-17T09:40:00Z"),
            incidents=[incident_claim(HROM, "ВУЛ.  ОСВІТИ 4, Київ", "Солом'янський",
                                      published="2026-06-17T09:40:00Z")]),
    }
    final = await run(tmp_path / "m.sqlite", extraction)
    assert len(final["sites"]) == 1
    assert len(final["sites"][0]["claims"]) == 2


# --- the whole point: city-wide figures must not multiply -------------------

async def test_the_citywide_toll_is_stored_once_not_once_per_site(tmp_path):
    db = tmp_path / "m.sqlite"
    extraction = {
        SUSP: ArticleExtraction(
            attack=attack_claim(SUSP, killed=5),
            incidents=[incident_claim(SUSP, a, d) for a, d in SITES]),
        HROM: ArticleExtraction(
            attack=attack_claim(HROM, killed=5, published="2026-06-17T09:40:00Z"),
            incidents=[incident_claim(HROM, a, d, published="2026-06-17T09:40:00Z")
                       for a, d in SITES]),
    }
    await run(db, extraction)

    conn = connect(db)
    attacks = conn.execute("SELECT citywide_killed FROM attacks").fetchall()
    assert len(attacks) == 1
    assert attacks[0]["citywide_killed"] == 5

    # No impact row carries the city-wide number.
    per_site = conn.execute("SELECT COALESCE(SUM(killed), 0) AS t FROM incidents").fetchone()
    assert per_site["t"] == 0, "a city-wide figure leaked onto the impact rows"


async def test_deaths_attributed_to_an_address_are_kept_there(tmp_path):
    db = tmp_path / "m.sqlite"
    extraction = {
        SUSP: ArticleExtraction(
            attack=attack_claim(SUSP, killed=5),
            incidents=[incident_claim(SUSP, "вул. Освіти, 4, Київ",
                                      "Солом'янський", killed=3)]),
    }
    await run(db, extraction)

    conn = connect(db)
    assert conn.execute("SELECT killed FROM incidents").fetchone()["killed"] == 3
    assert conn.execute("SELECT citywide_killed FROM attacks").fetchone()[0] == 5


async def test_impacts_are_linked_to_the_night_they_belong_to(tmp_path):
    db = tmp_path / "m.sqlite"
    extraction = {
        SUSP: ArticleExtraction(
            attack=attack_claim(SUSP),
            incidents=[incident_claim(SUSP, a, d) for a, d in SITES]),
    }
    final = await run(db, extraction)

    conn = connect(db)
    attack_id = conn.execute("SELECT id FROM attacks").fetchone()["id"]
    linked = conn.execute(
        "SELECT COUNT(*) n FROM incidents WHERE attack_id = ?", (attack_id,)
    ).fetchone()["n"]
    placed = [s for s in final["sites"] if s["location"]["lat"] is not None]
    assert linked == len(placed) == 3


async def test_weapons_are_recorded_on_the_night(tmp_path):
    db = tmp_path / "m.sqlite"
    extraction = {SUSP: ArticleExtraction(
        attack=attack_claim(SUSP),
        incidents=[incident_claim(SUSP, "вул. Освіти, 4, Київ", "Солом'янський")])}
    await run(db, extraction)

    weapons = json.loads(connect(db).execute(
        "SELECT weapons FROM attacks").fetchone()["weapons"])
    assert {w["kind"] for w in weapons} == {"shahed", "cruise_missile"}
    assert sum(w["launched"] for w in weapons) == 212


# --- the cross-level check --------------------------------------------------

async def test_addresses_adding_up_to_more_than_the_city_flags_the_night(tmp_path):
    """Four dead here and four dead there, on a night the city said killed five."""
    db = tmp_path / "m.sqlite"
    extraction = {
        SUSP: ArticleExtraction(
            attack=attack_claim(SUSP, killed=5),
            incidents=[
                incident_claim(SUSP, "вул. Освіти, 4, Київ", "Солом'янський", killed=4),
                incident_claim(SUSP, "просп. Оболонський, 12, Київ", "Оболонський", killed=4),
            ]),
    }
    final = await run(db, extraction)

    attack = final.get("attack") or {}
    assert attack.get("needs_review") is True
    assert "add up to" in attack["review_reason"]

    from mcp_server.incidents_store import pending_review
    assert any(item["kind"] == "attack" for item in pending_review(connect(db)))


async def test_a_night_whose_numbers_add_up_is_not_flagged(tmp_path):
    db = tmp_path / "m.sqlite"
    extraction = {
        SUSP: ArticleExtraction(
            attack=attack_claim(SUSP, killed=5),
            incidents=[incident_claim(SUSP, "вул. Освіти, 4, Київ",
                                      "Солом'янський", killed=3)]),
    }
    final = await run(db, extraction)
    assert (final.get("attack") or {}).get("needs_review") is False


# --- an article with no attack summary at all -------------------------------

async def test_a_run_without_any_attack_summary_still_records_impacts(tmp_path):
    db = tmp_path / "m.sqlite"
    extraction = {SUSP: ArticleExtraction(
        attack=None,
        incidents=[incident_claim(SUSP, "вул. Освіти, 4, Київ", "Солом'янський")])}
    final = await run(db, extraction)

    assert final["result"]["action"] == "written"
    assert connect(db).execute("SELECT COUNT(*) n FROM attacks").fetchone()["n"] == 0
    assert connect(db).execute("SELECT COUNT(*) n FROM incidents").fetchone()["n"] == 1
