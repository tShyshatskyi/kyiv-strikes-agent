"""The human end of the loop.

Incidents the agent could not settle on evidence are written but held back from
the verified export. This is where a person looks at them and decides.

    python -m agent.review                     # list what is waiting
    python -m agent.review ok 3 --by taras     # sign off on incident 3
    python -m agent.review export              # verified GeoJSON to stdout
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.db import connect  # noqa: E402
from mcp_server.incidents_store import (  # noqa: E402
    clear_review,
    pending_review,
    to_geojson,
)


def _list(conn) -> int:
    queue = pending_review(conn)
    if not queue:
        print("Nothing is waiting for review.")
        return 0

    print(f"{len(queue)} incident(s) waiting for a human decision:\n")
    for item in queue:
        print(f"  #{item['id']}  {item['occurred_at']}  {item['address_text']}")
        print(f"      district: {item['district']}", end="")
        if item["claimed_district"] and item["claimed_district"] != item["district"]:
            print(f"   (reporting said: {item['claimed_district']})", end="")
        print()
        print(f"      killed: {item['killed']}   injured: {item['injured']}   "
              f"evidence: {item['verification_level']}   "
              f"geocode confidence: {item['geocode_confidence']}")
        print(f"      WHY: {item['review_reason']}")
        print()
    print("Sign off with:  python -m agent.review ok <id> --by <your name>")
    return 0


def _ok(conn, incident_id: int, reviewed_by: str) -> int:
    try:
        clear_review(conn, incident_id, reviewed_by=reviewed_by)
    except KeyError as exc:
        print(exc)
        return 1
    print(f"Incident {incident_id} signed off by {reviewed_by}. "
          "It now appears in the verified export.")
    return 0


def _export(conn, include_unreviewed: bool) -> int:
    print(json.dumps(to_geojson(conn, include_unreviewed=include_unreviewed),
                     ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.review",
                                     description=__doc__)
    parser.add_argument("--db", default=os.environ.get("KSM_DB_PATH", "incidents.sqlite"))
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="show incidents awaiting review (default)")

    ok = sub.add_parser("ok", help="sign off on one incident")
    ok.add_argument("incident_id", type=int)
    ok.add_argument("--by", required=True, help="who is signing off")

    export = sub.add_parser("export", help="print GeoJSON for a map")
    export.add_argument("--include-unreviewed", action="store_true",
                        help="include incidents that still carry unresolved doubt")

    args = parser.parse_args()
    conn = connect(args.db)

    if args.command == "ok":
        return _ok(conn, args.incident_id, args.by)
    if args.command == "export":
        return _export(conn, args.include_unreviewed)
    return _list(conn)


if __name__ == "__main__":
    raise SystemExit(main())
