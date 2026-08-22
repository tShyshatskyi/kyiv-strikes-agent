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
    reconciliation = final.get("reconciliation") or {}
    location = final.get("location") or {}
    result = final.get("result") or {}

    print("\n" + "=" * 62)
    print(f"Question      : {final['query']}")
    print(f"Event date    : {final.get('event_date')}")
    print(f"Search rounds : {final.get('round')}  "
          f"(outlets tried: {', '.join(final.get('searched', [])) or 'none'})")
    print(f"Articles read : {len(final.get('articles', []))}")
    print(f"Claims        : {len(final.get('claims', []))}")

    if location.get("lat") is not None:
        print(f"Location      : {location['district']}  "
              f"({location['lat']:.5f}, {location['lon']:.5f})  "
              f"confidence {location['confidence']}")
    else:
        print("Location      : not resolved")

    print(f"Evidence      : {reconciliation.get('verification_level', '—')}"
          f"   voices: {', '.join(reconciliation.get('usable_groups', [])) or 'none'}")

    for field, value in (reconciliation.get("fields") or {}).items():
        print(f"  {field:16s} {value['value']}   [{value['status']}]")

    if reconciliation.get("missing_for_next_level"):
        print("Still missing :")
        for item in reconciliation["missing_for_next_level"]:
            print(f"  - {item}")

    if final.get("unreachable"):
        print("Unreachable   :")
        for item in final["unreachable"]:
            print(f"  - {item}")

    for note in final.get("notes", []):
        print(f"Note          : {note}")

    print(f"Write         : {json.dumps(result, ensure_ascii=False)}")

    if result.get("needs_review"):
        print("\n>>> This incident needs a human decision before it is trusted.")
        print(">>> Run:  python -m agent.review")
    print("=" * 62)


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

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
