"""Run one incident through the agent.

    python -m agent "удар по Києву 17 червня 2026"

Both MCP servers are started as separate processes and their tools are listed at
startup, so you can see what the agent actually discovered.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_env  # noqa: E402
from agent.graph import build_graph  # noqa: E402
from agent.mcp_clients import MCPConnectionError, open_tools  # noqa: E402
from agent.nodes import llm_extractor  # noqa: E402


def _root_cause(exc: BaseException) -> BaseException:
    """Unwrap nested exception groups down to the exception that actually fired."""
    while True:
        inner = getattr(exc, "exceptions", None)
        if inner:
            exc = inner[0]
            continue
        if exc.__cause__ is not None:
            exc = exc.__cause__
            continue
        return exc


def report(final: dict) -> None:
    attack = final.get("attack") or {}
    result = final.get("result") or {}
    sites = final.get("sites", [])

    print("\n" + "=" * 68)
    print(f"Question      : {final['query']}")
    print(f"Event date    : {final.get('event_date')}")
    print(f"Search rounds : {final.get('round')}  "
          f"(outlets tried: {', '.join(final.get('searched', [])) or 'none'})")
    print(f"Articles read : {len(final.get('articles', []))}   "
          f"claims: {len(final.get('claims', []))} impact, "
          f"{len(final.get('attack_claims', []))} attack-level")

    if attack:
        print("\n--- the night as a whole " + "-" * 42)
        print(f"  city-wide     : {attack.get('citywide_killed')} killed, "
              f"{(attack.get('reconciliation') or {}).get('fields', {}).get('citywide_injured', {}).get('value')} injured")
        weapons = (attack.get("reconciliation") or {}).get("weapons", [])
        for weapon in weapons:
            print(f"  {weapon['kind']:16s} launched {weapon.get('launched')}, "
                  f"intercepted {weapon.get('intercepted')}")
        print(f"  evidence      : "
              f"{(attack.get('reconciliation') or {}).get('verification_level')}")
        print(f"  attributed to addresses: {attack.get('site_total_killed')} killed")
        if attack.get("needs_review"):
            print(f"  NEEDS REVIEW  : {attack.get('review_reason')}")

    print(f"\n--- impact sites ({len(sites)}) " + "-" * 46)
    for site in sites:
        location = site["location"]
        reconciliation = site.get("reconciliation") or {}
        fields = reconciliation.get("fields", {})
        address = site["claims"][0].location_text
        if location.get("lat") is None:
            print(f"  {address}\n      could not be placed on the map")
            continue
        print(f"  {address}")
        print(f"      {location['district']}  "
              f"({location['lat']:.5f}, {location['lon']:.5f})  "
              f"confidence {location['confidence']}")
        here = fields.get("killed", {}).get("value")
        print(f"      at this address: "
              f"{here if here is not None else 'not stated'} killed   "
              f"evidence: {reconciliation.get('verification_level', '—')}")
        if location.get("needs_review") or reconciliation.get("needs_review"):
            reason = location.get("review_reason") or reconciliation.get("review_reason")
            print(f"      NEEDS REVIEW: {reason}")

    if final.get("reconciliation", {}).get("missing_for_next_level"):
        print("\nStill missing :")
        for item in final["reconciliation"]["missing_for_next_level"]:
            print(f"  - {item}")

    if final.get("unreachable"):
        print("\nUnreachable   :")
        for item in final["unreachable"]:
            print(f"  - {item}")

    for note in final.get("notes", []):
        print(f"Note          : {note}")

    print(f"\nWrite         : {json.dumps(result, ensure_ascii=False)[:300]}")

    flagged = bool(attack.get("needs_review")) or any(
        s["location"].get("needs_review")
        or (s.get("reconciliation") or {}).get("needs_review")
        for s in sites)
    if flagged:
        print("\n>>> Some of this needs a human decision before it is trusted.")
        print(">>> Run:  python -m agent.review")
    print("=" * 68)


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    from agent.config import describe
    load_env()
    print(f"Configuration    : {describe()}")

    try:
        extractor = llm_extractor()
    except RuntimeError as exc:
        print(f"\n{exc}\n")
        return 1

    try:
        async with open_tools() as tools:
            playwright = sorted(n for n in tools if n.startswith("browser_"))
            custom = sorted(n for n in tools if not n.startswith("browser_"))
            print(f"Playwright MCP  : {len(playwright)} tools discovered")
            print(f"kyiv-strikes MCP: {len(custom)} tools discovered — "
                  f"{', '.join(custom)}")

            graph = build_graph(tools, extractor)
            final = await graph.ainvoke({"query": sys.argv[1]})
    except MCPConnectionError as exc:
        print(f"\nCannot start:\n{exc}\n")
        return 1
    except BaseException as exc:                      # noqa: BLE001
        # LangGraph wraps a node's exception in a task ExceptionGroup, which
        # would bury a perfectly clear message under a traceback. Dig out the
        # exception that actually fired and show that instead.
        cause = _root_cause(exc)
        if isinstance(cause, ValueError):
            print(f"\n{cause}\n")
            return 1
        raise

    report(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
