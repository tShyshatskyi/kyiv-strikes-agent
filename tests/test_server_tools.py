import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parent.parent

EXPECTED_TOOLS = {
    "assess_source_credibility",
    "resolve_strike_location",
    "corroborate_incident_claims",
    "upsert_incident",
}


@asynccontextmanager
async def mcp_session(db_path: Path):
    """Start the custom server exactly as it starts on its own, over stdio.

    Written as a context manager rather than an async fixture on purpose: an
    async yield fixture sets up and tears down in different tasks, which anyio
    rejects when the stdio streams are closed.
    """
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
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield client


def payload(result) -> dict:
    return json.loads(result.content[0].text)


# --- discovery --------------------------------------------------------------

async def test_all_four_tools_are_discoverable(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        tools = await session.list_tools()
        assert EXPECTED_TOOLS <= {t.name for t in tools.tools}


async def test_every_tool_declares_a_schema_and_a_description(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        tools = await session.list_tools()
        for tool in tools.tools:
            assert tool.inputSchema, f"{tool.name} exposes no input schema"
            assert tool.description, f"{tool.name} exposes no model-facing description"


async def test_the_write_tool_is_marked_as_having_side_effects(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
        described = tools["upsert_incident"].description.lower()
        assert "side effect" in described or "writes" in described


# --- failure must never look like an empty success --------------------------

async def test_an_address_in_another_city_is_an_error(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        result = await session.call_tool(
            "resolve_strike_location",
            {"location_text": "вул. Дерибасівська, 1, Одеса"},
        )
        body = payload(result)
        assert body["ok"] is False
        assert body["error"]["code"] == "OUT_OF_AREA"
        assert body["error"]["retriable"] is False
        assert "Одеса" in body["error"]["message"]


async def test_an_address_that_matches_nothing_is_an_empty_success(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        result = await session.call_tool(
            "resolve_strike_location",
            {"location_text": "вул. Абсолютно Неіснуюча, 4242, Київ"},
        )
        body = payload(result)
        assert body["ok"] is True
        assert body["data"]["matches"] == []


async def test_a_real_address_resolves_through_the_recorded_fixture(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        result = await session.call_tool(
            "resolve_strike_location",
            {"location_text": "вул. Освіти, 4, Київ", "claimed_district": "Солом'янський"},
        )
        body = payload(result)
        assert body["ok"] is True
        assert body["data"]["district"] == "Солом'янський"
        assert body["data"]["needs_review"] is False


async def test_a_district_disagreement_comes_back_flagged_for_a_human(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        result = await session.call_tool(
            "resolve_strike_location",
            {"location_text": "вул. Освіти, 4, Київ", "claimed_district": "Оболонський"},
        )
        body = payload(result)
        assert body["ok"] is True
        assert body["data"]["needs_review"] is True
        assert body["data"]["district_conflict"] is True


# --- invalid input ----------------------------------------------------------

async def test_input_violating_the_published_schema_is_refused_at_the_protocol(tmp_path):
    """Validation happens in two layers, and this is the outer one.

    location_text declares min_length=3 in the tool's published schema, so a
    caller sending "x" is refused by the MCP layer before our code runs. The
    result is a protocol error, not a domain answer — which is correct: the
    request was never well-formed enough to have a domain answer.
    """
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        result = await session.call_tool("resolve_strike_location", {"location_text": "x"})
        assert result.isError is True
        assert "location_text" in result.content[0].text
        assert "at least 3 characters" in result.content[0].text


async def test_the_published_schema_states_the_constraint_the_model_must_respect(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
        schema = tools["resolve_strike_location"].inputSchema
        assert schema["properties"]["location_text"]["minLength"] == 3
        assert schema["properties"]["location_text"]["maxLength"] == 200


async def test_a_malformed_event_date_is_rejected(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        result = await session.call_tool(
            "assess_source_credibility",
            {"sources": [], "event_date": "17 червня"},
        )
        body = payload(result)
        assert body["ok"] is False
        assert body["error"]["code"] == "SCHEMA_VIOLATION"


# --- the domain tools over the wire -----------------------------------------

async def test_credibility_collapses_a_relay_into_the_source_it_relays(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        result = await session.call_tool("assess_source_credibility", {
            "sources": [
                {"url": "https://dsns.gov.ua/n/1", "domain": "dsns.gov.ua",
                 "published_at": "2026-06-17T08:00:00Z"},
                {"url": "https://suspilne.media/n/1", "domain": "suspilne.media",
                 "published_at": "2026-06-17T09:00:00Z",
                 "cited_sources": ["dsns.gov.ua"]},
            ],
            "event_date": "2026-06-17",
        })
        body = payload(result)
        assert body["ok"] is True
        groups = {a["url"]: a["independence_group"] for a in body["data"]["assessments"]}
        assert groups["https://suspilne.media/n/1"] == "dsns"


async def test_corroboration_reports_what_is_still_missing(tmp_path):
    async with mcp_session(tmp_path / "demo.sqlite") as session:
        claim = {
            "source_url": "https://suspilne.media/n/1",
            "published_at": "2026-06-17T09:00:00Z",
            "location_text": "вул. Освіти, 4",
            "structure_type": "residential",
            "killed": 3,
            "damage": ["fire"],
            "quote": "Загинули троє людей унаслідок влучання у житловий будинок.",
        }
        assessment = {
            "url": "https://suspilne.media/n/1", "tier": 2,
            "independence_group": "suspilne", "usable_as_corroborator": True,
            "adds": [], "reason": "test",
        }
        result = await session.call_tool("corroborate_incident_claims", {
            "claims": [claim], "assessments": [assessment],
        })
        body = payload(result)
        assert body["ok"] is True
        assert body["data"]["verification_level"] == "single_source"
        assert body["data"]["missing_for_next_level"]
