"""Record a genuine Nominatim response as a replay fixture.

Usage:
    python scripts/record_fixture.py "вул. Освіти, 4, Київ"

Recorded responses are real. Never hand-edit them: an edited fixture is a
hard-coded answer, which the assignment explicitly disallows.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.geo import normalize_address  # noqa: E402
from mcp_server.nominatim import NominatimClient, fixture_key  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nominatim"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    contact = os.environ.get("NOMINATIM_CONTACT", "")
    client = NominatimClient(contact=contact, offline=False, fixtures_dir=FIXTURES)

    raw = sys.argv[1]
    query = normalize_address(raw)
    results = client.search(query)

    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f"{fixture_key(query)}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"input      : {raw}")
    print(f"query      : {query}")
    print(f"results    : {len(results)}")
    if results:
        best = results[0]
        print(f"best       : {best.get('display_name', '')[:90]}")
        print(f"coords     : {best.get('lat')}, {best.get('lon')}")
    print(f"written to : {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
