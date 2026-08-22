"""Confirm Playwright MCP starts and that whitelisted sites serve content.

Run this BEFORE building the graph. If a source blocks headless browsers, you
need to know now, not during the defence.

    python scripts/probe_playwright.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.mcp_clients import MCPConnectionError, open_tools  # noqa: E402

TARGETS = [
    ("dsns.gov.ua", "https://dsns.gov.ua/"),
    ("kyivcity.gov.ua", "https://kyivcity.gov.ua/"),
    ("suspilne.media", "https://suspilne.media/"),
    ("pravda.com.ua", "https://www.pravda.com.ua/"),
    ("hromadske.ua", "https://hromadske.ua/"),
]

USABLE_MIN_CHARS = 5000


async def probe(tools: dict) -> int:
    playwright = sorted(n for n in tools if n.startswith("browser_"))
    custom = sorted(n for n in tools if not n.startswith("browser_"))
    print(f"Playwright MCP  ({len(playwright)} tools): {', '.join(playwright[:8])} ...")
    print(f"kyiv-strikes    ({len(custom)} tools): {', '.join(custom)}")
    print()

    navigate = tools.get("browser_navigate")
    snapshot = tools.get("browser_snapshot")
    if not navigate or not snapshot:
        print("Expected browser_navigate and browser_snapshot. Adjust for this version.")
        return 1

    failures = 0
    for domain, url in TARGETS:
        try:
            await navigate.ainvoke({"url": url})
            page = str(await snapshot.ainvoke({}))
        except Exception as exc:
            failures += 1
            print(f"FAIL {domain:20s} {type(exc).__name__}: {str(exc)[:80]}")
            continue

        if len(page) < USABLE_MIN_CHARS:
            failures += 1
            print(f"THIN {domain:20s} {len(page):>9,} chars — blocked or JS-gated?")
        else:
            print(f"OK   {domain:20s} {len(page):>9,} chars")

    print()
    print(f"{len(TARGETS) - failures}/{len(TARGETS)} sources usable.")
    if failures:
        print("Drop or replace unusable sources in data/source_registry.yaml and")
        print("record which and why in docs/design_rationale.md.")
    return 0


async def main() -> int:
    try:
        async with open_tools() as tools:
            return await probe(tools)
    except MCPConnectionError as exc:
        print(f"\nMCP connection failed:\n{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
