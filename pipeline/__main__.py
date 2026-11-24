"""python -m pipeline                 run everything (collect → … → publish)
python -m pipeline 2026-10-19      only these attack days (for a quick test)
python -m pipeline --graph         print the workflow as a Mermaid diagram
"""

import asyncio
import sys

from pipeline import graph

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--graph" in args:
        print(graph.build_graph().get_graph().draw_mermaid())
        print(graph.DAY_GRAPH.get_graph().draw_mermaid())
    else:
        asyncio.run(graph.run([a for a in args if not a.startswith("--")]))
