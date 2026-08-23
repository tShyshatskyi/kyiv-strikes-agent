"""Find which Ukrainian outlets have a search we can actually drive.

The first real run failed here, not in the model: the guessed search URLs were
not searches. Suspilne's /search/ answers 404 and its real search is a
JavaScript overlay that does not respond to a scripted click; Pravda's search
page returns no links a browser snapshot can see.

Rather than fight one site's front end, this asks a wider set of outlets which
of them answer a plain URL query with a real result list. A result list is one
where most article slugs mention Kyiv; a news feed is one where almost none do.

    python scripts/probe_search.py
    python scripts/probe_search.py "масований удар по Києву"
"""

import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import agent  # noqa: F401,E402
from agent.mcp_clients import open_tools  # noqa: E402
from agent.nodes import article_links, as_text  # noqa: E402

CANDIDATES = {
    "pravda.com.ua": [
        "https://www.pravda.com.ua/search/?query={q}",
        "https://www.pravda.com.ua/rus/search/?query={q}",
    ],
    "hromadske.ua": [
        "https://hromadske.ua/search?q={q}",
    ],
    "babel.ua": [
        "https://babel.ua/search?query={q}",
        "https://babel.ua/?s={q}",
    ],
    "nv.ua": [
        "https://nv.ua/ukr/search.html?query={q}",
        "https://nv.ua/search.html?query={q}",
    ],
    "liga.net": [
        "https://www.liga.net/search?q={q}",
        "https://news.liga.net/search?q={q}",
    ],
    "unian.ua": [
        "https://www.unian.ua/search?q={q}",
        "https://www.unian.ua/search/{q}",
    ],
    "espreso.tv": [
        "https://espreso.tv/search?query={q}",
        "https://espreso.tv/search?q={q}",
    ],
    "rbc.ua": [
        "https://www.rbc.ua/ukr/search?query={q}",
    ],
}

# A slug about Kyiv carries one of these. Ukrainian outlets transliterate the
# city half a dozen ways — kyyevu, kiyevi, kyiv, kiev — and a regex that misses
# any of them makes a working search look broken. This one was written after
# exactly that mistake, so it is deliberately generous.
KYIV_IN_SLUG = re.compile(r"[-/]k[iy]{1,2}[iy]?[e]?v", re.I)


async def try_one(tools: dict, domain: str, template: str, query: str) -> dict:
    url = template.format(q=quote_plus(query))
    try:
        await tools["browser_navigate"].ainvoke({"url": url})
        page = as_text(await tools["browser_snapshot"].ainvoke({}))
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:50]}"}

    status = re.search(r"HTTP status: (\d+)", page)
    links = article_links(page, domain, limit=15)
    about_kyiv = [u for u in links if KYIV_IN_SLUG.search(u)]
    return {
        "status": status.group(1) if status else "200",
        "chars": len(page),
        "links": len(links),
        "kyiv": len(about_kyiv),
        "sample": (about_kyiv or links)[:2],
    }


async def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "удар по Києву"
    print(f'Query: "{query}"\n')
    print(f"{'outlet':<16}{'code':<6}{'chars':>8}{'links':>7}{'kyiv':>6}  verdict")
    print("-" * 78)

    usable = []
    async with open_tools() as tools:
        for domain, templates in CANDIDATES.items():
            for template in templates:
                r = await try_one(tools, domain, template, query)
                if "error" in r:
                    print(f"{domain:<16}{'--':<6}{'':>8}{'':>7}{'':>6}  {r['error']}")
                    continue

                if r["links"] and r["kyiv"] >= max(2, r["links"] // 3):
                    verdict, good = "SEARCH WORKS", True
                elif r["links"]:
                    verdict, good = "feed, not results", False
                else:
                    verdict, good = "no links visible", False

                print(f"{domain:<16}{r['status']:<6}{r['chars']:>8,}"
                      f"{r['links']:>7}{r['kyiv']:>6}  {verdict}")
                print(f"{'':<16}{template}")
                for s in r["sample"]:
                    print(f"{'':<18}{s[:92]}")
                if good:
                    usable.append((domain, template))
                    break               # first working template per outlet wins

    print("\n" + "=" * 78)
    if usable:
        print("Usable search endpoints:")
        for domain, template in usable:
            print(f'    "{domain}": "{template}",')
    else:
        print("Nothing usable. Discovery needs a different approach.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
