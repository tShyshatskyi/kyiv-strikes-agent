"""Show what the model actually does with one real article.

The agent's extraction node swallows failures so one bad article cannot end a
run. That is right in production and useless when nothing is being extracted at
all, so this script does the same call with nothing hidden: it prints the page
the model was given, the raw model reply, and any exception in full.

    python scripts/debug_extraction.py
    python scripts/debug_extraction.py "удар по Києву 20 серпня 2026"
"""

import asyncio
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import agent  # noqa: F401,E402  — silences third-party warnings
from agent.config import load_env  # noqa: E402
from agent.mcp_clients import open_tools  # noqa: E402
from agent.nodes import (  # noqa: E402
    PROMPT,
    article_links,
    as_text,
    build_search_url,
)
from common.schemas import ArticleExtraction  # noqa: E402


async def one_article(tools: dict, query: str) -> tuple[str, str]:
    """Fetch the first article the Suspilne search offers for this query."""
    navigate, snapshot = tools["browser_navigate"], tools["browser_snapshot"]

    await navigate.ainvoke({"url": build_search_url("suspilne", query)})
    page = as_text(await snapshot.ainvoke({}))
    links = article_links(page, "suspilne.media")

    print(f"Search page      : {len(page):,} characters")
    print(f"Article links    : {len(links)}")
    for link in links:
        print(f"  {link}")
    if not links:
        raise SystemExit("\nNo article links found. The problem is discovery, "
                         "not extraction — the search page markup has changed.")

    await navigate.ainvoke({"url": links[0]})
    return links[0], as_text(await snapshot.ainvoke({}))


async def main() -> int:
    load_env()
    query = sys.argv[1] if len(sys.argv) > 1 else "удар по Києву 20 серпня 2026"

    async with open_tools() as tools:
        url, text = await one_article(tools, query)

    print(f"\nArticle          : {url}")
    print(f"Article length   : {len(text):,} characters")
    print("\n--- first 1500 characters of what the model is given " + "-" * 14)
    print(text[:1500])
    print("-" * 68)

    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=os.environ["OPENROUTER_MODEL"],
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL",
                                "https://openrouter.ai/api/v1"),
        temperature=0,
    )
    prompt = PROMPT.format(url=url, published_at="unknown", text=text[:20000])

    print("\n1. Plain call, no schema — does the model answer at all?")
    try:
        plain = await model.ainvoke(prompt + "\n\nAnswer in two sentences: is "
                                             "this article about a strike on "
                                             "Kyiv, and what addresses does it "
                                             "name?")
        print(f"   OK: {plain.content[:400]}")
    except Exception:
        print("   FAILED:")
        traceback.print_exc()
        return 1

    print("\n2. Structured call — the one the agent actually makes.")
    try:
        result = await model.with_structured_output(ArticleExtraction).ainvoke(prompt)
        print(f"   OK: {result}")
        if result is None or (not result.incidents and result.attack is None):
            print("\n   The model answered but found nothing in this article.")
            print("   Either the article is not about a strike, or the prompt "
                  "needs work.")
    except Exception:
        print("   FAILED — this is why the agent extracted nothing:")
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
