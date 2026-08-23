"""Confirm Playwright MCP starts and that whitelisted sites serve content.

Run this BEFORE building the graph. If a source blocks headless browsers, you
need to know now, not during the defence.

    python scripts/probe_playwright.py
"""

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import agent  # noqa: F401,E402  — silences third-party warnings on import
from agent.mcp_clients import MCPConnectionError, open_tools  # noqa: E402
from mcp_server.credibility import load_registry  # noqa: E402

REGISTRY = load_registry(REPO / "data" / "source_registry.yaml")

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

    surprises, walled, working = [], [], 0
    for domain, url in TARGETS:
        entry = REGISTRY.entries.get(domain)
        known_walled = entry is not None and not entry.directly_reachable

        try:
            await navigate.ainvoke({"url": url})
            page = str(await snapshot.ainvoke({}))
            thin = len(page) < USABLE_MIN_CHARS
            size = f"{len(page):>9,} chars"
        except Exception as exc:
            thin, size = True, f"{type(exc).__name__}: {str(exc)[:50]}"

        if not thin:
            working += 1
            print(f"OK         {domain:20s} {size}")
        elif known_walled:
            walled.append(domain)
            print(f"WALLED     {domain:20s} {size}   known, never fetched")
        else:
            surprises.append(domain)
            print(f"UNEXPECTED {domain:20s} {size}")

    print()
    print(f"{working} of {len(TARGETS)} fetchable; {len(walled)} known to be "
          "walled off and not used for fetching.")

    if surprises:
        print()
        print("Something changed. These were expected to work and did not:")
        for domain in surprises:
            print(f"  - {domain}")
        print("Open the site by hand. If it now blocks automated access, set")
        print("directly_reachable: false in data/source_registry.yaml and record")
        print("why in docs/design_rationale.md.")
        return 1

    print("Nothing unexpected. Safe to run the agent.")
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
