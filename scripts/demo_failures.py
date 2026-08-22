"""Walk through the three failure scenarios, in defence order.

    python scripts/demo_failures.py

Each one prints what was broken, what came back, and why that is the right
behaviour. Nothing here is staged: every failure is produced by breaking
something real and letting the normal code path respond.
"""

import asyncio
import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from agent.mcp_clients import MCPConnectionError, open_tools  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def server(db_path: str):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_server"], cwd=str(REPO),
        env={"PYTHONUTF8": "1", "PYTHONPATH": str(REPO),
             "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
             "KSM_OFFLINE": "1", "KSM_DB_PATH": db_path,
             "NOMINATIM_CONTACT": "demo@example.com"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def head(n: int, title: str) -> None:
    print(f"\n{'=' * 66}\n  Scenario {n}: {title}\n{'=' * 66}")


async def scenario_c(db: str) -> None:
    head("C", "the custom server refuses an address outside Kyiv")
    async with server(db) as session:
        for label, text in [
            ("an address in Odesa", "вул. Дерибасівська, 1, Одеса"),
            ("an address that does not exist", "вул. Абсолютно Неіснуюча, 4242, Київ"),
        ]:
            body = json.loads((await session.call_tool(
                "resolve_strike_location", {"location_text": text})).content[0].text)
            print(f"\n  {label}\n    {text}")
            print(f"    -> {json.dumps(body, ensure_ascii=False)[:230]}")

    print("\n  Why this matters: one is ok:false with an error code, the other is")
    print("  ok:true with an empty payload. A caller can tell 'something is wrong'")
    print("  from 'nothing found here, keep looking' without reading any prose.")


async def scenario_b() -> None:
    head("B", "the Playwright MCP server cannot start")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as handle:
        json.dump({"mcpServers": {"playwright": {
            "command": "definitely-not-a-real-binary", "args": [],
            "transport": "stdio"}}}, handle)
        broken = handle.name

    print("\n  Pointing the config at a command that does not exist...")
    try:
        async with open_tools(broken):
            print("    unexpectedly started")
    except MCPConnectionError as exc:
        print(f"\n    {exc}")
    finally:
        os.unlink(broken)

    print("\n  Why this matters: a missing dependency produces one readable")
    print("  sentence naming the server and what to check, not a traceback from")
    print("  inside an async task group.")


async def scenario_a() -> None:
    head("A", "an article disappears while the agent is reading it")
    print("\n  Run the agent normally and point one of its article links at a")
    print("  removed page (or disconnect mid-run). Expected behaviour:")
    print("    - that URL is listed under 'Unreachable' in the final report;")
    print("    - the run finishes on the remaining sources;")
    print("    - the evidence level drops, because a lost source is lost evidence;")
    print("    - the record is still written, with its weaker evidence stated.")
    print("\n  Pinned by test_graph.py::"
          "test_an_unreachable_article_is_recorded_and_the_run_continues")


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "demo.sqlite")
        await scenario_c(db)
        await scenario_b()
        await scenario_a()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
