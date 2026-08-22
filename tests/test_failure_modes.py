"""The three failure scenarios shown at the defence, pinned as tests.

A demo failure that only exists as a rehearsed click is worth little. Each of
these is reproduced here from the same code path the demo uses.
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.mcp_clients import MCPConnectionError, load_config, open_tools
from agent.nodes import should_continue

REPO = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def server(db_path: Path, **env_overrides):
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
            **env_overrides,
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# --- Scenario A: an article disappears mid-run ------------------------------
# The full graph version lives in test_graph.py; this pins the routing
# consequence, which is what actually matters: losing a source costs evidence.

def test_losing_a_source_costs_evidence_and_the_agent_reacts():
    state = {
        "round": 1,
        "unreachable": ["https://www.pravda.com.ua/x: HTTP 404"],
        "reconciliation": {"verification_level": "single_source",
                           "missing_for_next_level": ["independent confirmation"],
                           "usable_groups": ["suspilne"], "has_conflict": False},
        "location": {"district_conflict": False},
    }
    assert should_continue(state) == "discover_sources"


def test_when_the_budget_is_spent_the_thin_record_is_still_written():
    state = {
        "round": 3,
        "unreachable": ["https://www.pravda.com.ua/x: HTTP 404"],
        "reconciliation": {"verification_level": "single_source",
                           "missing_for_next_level": ["independent confirmation"],
                           "usable_groups": ["suspilne"], "has_conflict": False},
        "location": {"district_conflict": False},
    }
    assert should_continue(state) == "persist"


# --- Scenario B: the existing MCP server will not start ---------------------

async def test_a_server_that_cannot_start_produces_a_sentence_not_a_traceback(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"mcpServers": {"playwright": {
        "command": "definitely-not-a-real-binary", "args": [], "transport": "stdio",
    }}}), encoding="utf-8")

    with pytest.raises(MCPConnectionError) as exc:
        async with open_tools(bad):
            pass

    message = str(exc.value)
    assert "playwright" in message
    assert "failed to start" in message
    assert "check:" in message


async def test_a_config_pointing_at_nothing_is_caught_before_any_process_starts(tmp_path):
    with pytest.raises(MCPConnectionError) as exc:
        load_config(tmp_path / "absent.json")
    assert "not found" in str(exc.value)


# --- Scenario C: failure must not look like an empty result -----------------

async def test_out_of_area_is_an_error_with_an_explanation(tmp_path):
    async with server(tmp_path / "f.sqlite") as session:
        result = await session.call_tool(
            "resolve_strike_location",
            {"location_text": "вул. Дерибасівська, 1, Одеса"},
        )
        body = json.loads(result.content[0].text)

    assert body["ok"] is False
    assert body["error"]["code"] == "OUT_OF_AREA"
    assert body["error"]["retriable"] is False
    assert "Одеса" in body["error"]["message"]
    assert "not about Kyiv" in body["error"]["message"]


async def test_no_match_is_an_ordinary_empty_success(tmp_path):
    async with server(tmp_path / "f.sqlite") as session:
        result = await session.call_tool(
            "resolve_strike_location",
            {"location_text": "вул. Абсолютно Неіснуюча, 4242, Київ"},
        )
        body = json.loads(result.content[0].text)

    assert body["ok"] is True
    assert body["data"]["matches"] == []


async def test_the_two_outcomes_are_distinguishable_by_shape_alone(tmp_path):
    """The requirement in one assertion: a caller can tell them apart without
    reading any message text."""
    async with server(tmp_path / "f.sqlite") as session:
        failure = json.loads((await session.call_tool(
            "resolve_strike_location",
            {"location_text": "вул. Дерибасівська, 1, Одеса"})).content[0].text)
        empty = json.loads((await session.call_tool(
            "resolve_strike_location",
            {"location_text": "вул. Абсолютно Неіснуюча, 4242, Київ"})).content[0].text)

    assert failure["ok"] != empty["ok"]
    assert "error" in failure and "error" not in empty
    assert "data" in empty and "data" not in failure


# --- offline mode must not be a shortcut ------------------------------------

async def test_offline_mode_runs_the_same_validation_path(tmp_path):
    """A fixture replays the transport, not the answer: the Odesa fixture is a
    genuine recorded Nominatim response, and it still has to fail the Kyiv
    point-in-polygon check to produce OUT_OF_AREA."""
    async with server(tmp_path / "f.sqlite", KSM_OFFLINE="1") as session:
        body = json.loads((await session.call_tool(
            "resolve_strike_location",
            {"location_text": "вул. Дерибасівська, 1, Одеса"})).content[0].text)

    # If the fixture were a canned answer, the message could not contain
    # coordinates that were geometrically tested against the district polygons.
    assert "46.48" in body["error"]["message"]
    assert "outside every Kyiv district" in body["error"]["message"]
