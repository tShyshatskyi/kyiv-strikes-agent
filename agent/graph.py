"""Graph assembly.

Everything is a straight line except one edge. That edge — out of `reconcile` —
is where the system stops being a pipeline: what the corroboration tool returns
decides whether the run writes its result or goes back for more evidence.
"""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.nodes import make_nodes, should_continue


class AgentState(TypedDict, total=False):
    query: str
    event_date: str
    round: int
    searched: list[str]
    articles: list[dict]
    claims: list[Any]
    attack_claims: list[Any]
    read_urls: list[str]
    extraction_failures: list[str]
    sites: list[dict]
    attack_id: int | None
    attack: Any
    assessments: list[dict]
    location: dict | None
    reconciliation: dict | None
    unreachable: list[str]
    result: dict | None
    notes: list[str]


def build_graph(tools: dict, extractor):
    nodes = make_nodes(tools, extractor)
    graph = StateGraph(AgentState)

    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "parse_request")
    graph.add_edge("parse_request", "discover_sources")
    graph.add_edge("discover_sources", "extract_claims")
    graph.add_edge("extract_claims", "assess")
    graph.add_edge("assess", "locate")
    graph.add_edge("locate", "reconcile")

    # The one decision in the graph.
    graph.add_conditional_edges(
        "reconcile",
        should_continue,
        {"discover_sources": "discover_sources", "persist": "write_attack"},
    )
    # The attack row is written first so impacts can point at it; the totals
    # check runs last, because it needs those impacts to sum.
    graph.add_edge("write_attack", "persist")
    graph.add_edge("persist", "check_totals")
    graph.add_edge("check_totals", END)

    return graph.compile()
