"""Deduplicating writes with per-field provenance and a human review queue.

Two strikes on one night can land eighty metres apart and be reported as one
event, or a single strike can be reported five times with slightly different
addresses. Matching on proximity in space and time, rather than on text, is what
keeps one event as one row.

Nothing here decides whether a record is *true*. A record carrying doubt is
written and kept, but flagged: it stays out of the verified export until a
person clears it.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urlparse

from common.schemas import IncidentClaim
from mcp_server.geo import haversine_m

# ponytail: linear scan with haversine over the incidents in the time window.
# At Kyiv volumes (thousands of rows) this is microseconds; add an R-tree index
# if it ever isn't.
DEDUP_RADIUS_M = 150.0
DEDUP_TIME_WINDOW = timedelta(hours=6)

LEVEL_ORDER = {
    "unverified": 0,
    "single_source": 1,
    "corroborated": 2,
    "well_documented": 3,
}

MERGEABLE_FIELDS = ("killed", "injured", "structure_type", "address_text",
                    "claimed_district", "attack_id")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _find_duplicate(conn: sqlite3.Connection, incident: dict) -> sqlite3.Row | None:
    occurred = _parse_ts(incident["occurred_at"])
    low = (occurred - DEDUP_TIME_WINDOW).isoformat()
    high = (occurred + DEDUP_TIME_WINDOW).isoformat()

    rows = conn.execute(
        "SELECT * FROM incidents WHERE occurred_at BETWEEN ? AND ?", (low, high)
    ).fetchall()

    for row in rows:
        if haversine_m(incident["lat"], incident["lon"],
                       row["lat"], row["lon"]) <= DEDUP_RADIUS_M:
            return row
    return None


def _upsert_source(conn: sqlite3.Connection, claim: IncidentClaim,
                   assessment: dict | None) -> int:
    url = str(claim.source_url)
    conn.execute(
        """INSERT INTO sources (url, domain, tier, independence_group,
                                published_at, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
               tier = excluded.tier,
               independence_group = excluded.independence_group""",
        (url, urlparse(url).netloc,
         (assessment or {}).get("tier"),
         (assessment or {}).get("independence_group"),
         claim.published_at.isoformat(), _now()),
    )
    return conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()["id"]


def _write_provenance(conn: sqlite3.Connection, parent_id: int,
                      claims, assessments: list[dict],
                      reconciliation: dict,
                      table: str = "incident_claims") -> None:
    """Store, per (record, source, field), the sentence that supports the value.

    Works for both levels: incident_claims links to an incident, attack_claims
    to an attack. The shape is identical because the question is identical —
    where did this number come from.
    """
    parent_column = "incident_id" if table == "incident_claims" else "attack_id"
    by_url = {a["url"]: a for a in assessments}
    for claim in claims:
        source_id = _upsert_source(conn, claim, by_url.get(str(claim.source_url)))

        for field, reconciled in reconciliation["fields"].items():
            value = getattr(claim, field, None)
            if value is None:
                continue
            conn.execute(
                f"""INSERT INTO {table}
                       ({parent_column}, source_id, field, value, quote, status,
                        extracted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT({parent_column}, source_id, field) DO UPDATE SET
                       value = excluded.value,
                       quote = excluded.quote,
                       status = excluded.status""",
                (parent_id, source_id, field, str(value), claim.quote,
                 reconciled["status"], _now()),
            )

        for media_url in getattr(claim, "media_urls", []):
            conn.execute(
                """INSERT OR IGNORE INTO incident_media (incident_id, source_id, url)
                   VALUES (?, ?, ?)""",
                (parent_id, source_id, str(media_url)),
            )


def upsert_incident(conn: sqlite3.Connection, incident: dict,
                    claims: list[IncidentClaim], assessments: list[dict],
                    reconciliation: dict) -> dict:
    """Create or merge an incident. The only operation with persistent effects."""
    existing = _find_duplicate(conn, incident)

    if existing is None:
        cursor = conn.execute(
            """INSERT INTO incidents
                   (attack_id, occurred_at, lat, lon, district, claimed_district,
                    address_text, structure_type, killed, injured, damage,
                    verification_level, geocode_confidence, needs_review,
                    review_reason, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (incident.get("attack_id"),
             incident["occurred_at"], incident["lat"], incident["lon"],
             incident["district"], incident.get("claimed_district"),
             incident.get("address_text"), incident["structure_type"],
             incident.get("killed"), incident.get("injured"),
             json.dumps(incident.get("damage", []), ensure_ascii=False),
             incident["verification_level"], incident["geocode_confidence"],
             1 if incident.get("needs_review") else 0,
             incident.get("review_reason"), _now(), _now()),
        )
        incident_id = cursor.lastrowid
        _write_provenance(conn, incident_id, claims, assessments, reconciliation)
        conn.commit()
        return {"incident_id": incident_id, "action": "created",
                "changed_fields": sorted(reconciliation["fields"]),
                "needs_review": bool(incident.get("needs_review"))}

    if LEVEL_ORDER[incident["verification_level"]] < LEVEL_ORDER[existing["verification_level"]]:
        return {"incident_id": existing["id"], "action": "rejected_lower_confidence",
                "changed_fields": [], "needs_review": bool(existing["needs_review"])}

    changed: list[str] = []
    for field in MERGEABLE_FIELDS:
        new_value = incident.get(field)
        if new_value is not None and new_value != existing[field]:
            conn.execute(f"UPDATE incidents SET {field} = ? WHERE id = ?",
                         (new_value, existing["id"]))
            changed.append(field)

    conn.execute(
        "UPDATE incidents SET verification_level = ?, updated_at = ? WHERE id = ?",
        (incident["verification_level"], _now(), existing["id"]),
    )

    # New doubt reopens a review that was already cleared: the record changed
    # after a person signed off on it, so their sign-off no longer covers it.
    if incident.get("needs_review"):
        conn.execute(
            """UPDATE incidents
                  SET needs_review = 1, review_reason = ?,
                      reviewed_by = NULL, reviewed_at = NULL
                WHERE id = ?""",
            (incident.get("review_reason"), existing["id"]),
        )

    _write_provenance(conn, existing["id"], claims, assessments, reconciliation)
    conn.commit()
    return {"incident_id": existing["id"], "action": "merged",
            "changed_fields": changed,
            "needs_review": bool(incident.get("needs_review")) or bool(existing["needs_review"])}


def pending_review(conn: sqlite3.Connection) -> list[dict]:
    """Everything awaiting a human decision, at either level, oldest first."""
    items = [
        dict(row) | {"kind": "attack"}
        for row in conn.execute(
            """SELECT id, occurred_at, citywide_killed, citywide_injured,
                      verification_level, review_reason
                 FROM attacks WHERE needs_review = 1""")
    ]
    items += [
        dict(row) | {"kind": "incident"}
        for row in conn.execute(
            """SELECT id, attack_id, occurred_at, district, claimed_district,
                      address_text, killed, injured, verification_level,
                      geocode_confidence, review_reason
                 FROM incidents WHERE needs_review = 1""")
    ]
    return sorted(items, key=lambda item: (item["occurred_at"], item["kind"]))


def clear_review(conn: sqlite3.Connection, record_id: int, reviewed_by: str,
                 kind: str = "incident") -> None:
    """Record that a person has checked this and stands behind it."""
    table = "attacks" if kind == "attack" else "incidents"
    cursor = conn.execute(
        f"""UPDATE {table}
               SET needs_review = 0, reviewed_by = ?, reviewed_at = ?, updated_at = ?
             WHERE id = ?""",
        (reviewed_by, _now(), _now(), record_id),
    )
    if cursor.rowcount == 0:
        raise KeyError(f"No {kind} with id {record_id}.")
    conn.commit()


def to_geojson(conn: sqlite3.Connection, include_unreviewed: bool = False) -> dict:
    """Export in the shape an interactive map consumes directly.

    By default this is the *verified* export: incidents still carrying an
    unresolved doubt are left out rather than shown as established fact.
    """
    query = "SELECT * FROM incidents"
    if not include_unreviewed:
        query += " WHERE needs_review = 0"
    query += " ORDER BY occurred_at"

    features = []
    for row in conn.execute(query):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {
                "id": row["id"],
                "occurred_at": row["occurred_at"],
                "district": row["district"],
                "address_text": row["address_text"],
                "structure_type": row["structure_type"],
                "killed": row["killed"],
                "injured": row["injured"],
                "damage": json.loads(row["damage"]),
                "verification_level": row["verification_level"],
                "needs_review": bool(row["needs_review"]),
                "review_reason": row["review_reason"],
            },
        })
    return {"type": "FeatureCollection", "features": features}
