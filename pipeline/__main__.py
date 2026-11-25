"""python -m pipeline                     run everything (collect → … → publish)
python -m pipeline 2026-11-06-N        only these attacks (ids, dates or months:
python -m pipeline 2026-10             2026-11-06-N, 2026-11-06, 2026-10)
python -m pipeline --graph             print the workflow as a Mermaid diagram

KSR_OUT=data/out_1a writes the registry somewhere other than data/out.
"""

import asyncio
import sys

from pipeline import graph

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--graph" in args:
        print(graph.build_graph().get_graph().draw_mermaid())
        print(graph.ATTACK_GRAPH.get_graph().draw_mermaid())
    else:
        asyncio.run(graph.run([a for a in args if not a.startswith("--")]))
