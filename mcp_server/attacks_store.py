"""The attack: one night of shelling, and the figures that belong to it.

City-wide casualty counts are reported for the city, not for an address. Storing
them per impact site would multiply them — three sites hit on a night that
killed five would sum to fifteen. They live here, once, and impacts point at
them.

This module also runs the one cross-level check the two-table split makes
possible: the deaths attributed to individual addresses cannot add up to more
than the city reported.
"""

import json
import sqlite3
from datetime import datetime, timedelta

from common.schemas import AttackClaim
from mcp_server.incidents_store import _now, _parse_ts, _write_provenance

# Reports of one night's attack are scattered across the following day, and the
# strike itself can run for hours. Anything within this window of an existing
# attack is the same night.
SAME_ATTACK_WINDOW = timedelta(hours=12)

LEVEL_ORDER = {
    "unverified": 0,
    "single_source": 1,
    "corroborated": 2,
    "well_documented": 3,
}


def _find_attack(conn: sqlite3.Connection, occurred_at: str) -> sqlite3.Row | None:
    occurred = _parse_ts(occurred_at)
    low = (occurred - SAME_ATTACK_WINDOW).isoformat()
    high = (occurred + SAME_ATTACK_WINDOW).isoformat()
    return conn.execute(
        "SELECT * FROM attacks WHERE occurred_at BETWEEN ? AND ? ORDER BY occurred_at",
        (low, high),
    ).fetchone()


def _site_total_killed(conn: sqlite3.Connection, attack_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(killed), 0) AS total FROM incidents WHERE attack_id = ?",
        (attack_id,),
    ).fetchone()
    return int(row["total"])


def _consistency_note(citywide: int | None, site_total: int) -> str | None:
    """Deaths pinned to addresses cannot outnumber the deaths the city reported."""
    if citywide is None or site_total <= citywide:
        return None
    return (
        f"The deaths attributed to individual addresses add up to {site_total}, "
        f"but the city-wide figure for this attack is {citywide}. Either a "
        "city-wide number was mistakenly read as belonging to one address, or "
        "the city-wide total is out of date. A person needs to check."
    )


def upsert_attack(conn: sqlite3.Connection, attack: dict,
                  claims: list[AttackClaim], assessments: list[dict],
                  reconciliation: dict) -> dict:
    """Create or merge the attack these reports describe.

    Returns the attack id, what happened, the summed per-address death toll, and
    whether the two levels disagree.
    """
    existing = _find_attack(conn, attack["occurred_at"])

    if existing is None:
        cursor = conn.execute(
            """INSERT INTO attacks
                   (occurred_at, citywide_killed, citywide_injured, weapons,
                    verification_level, needs_review, review_reason,
                    created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (attack["occurred_at"], attack.get("citywide_killed"),
             attack.get("citywide_injured"),
             json.dumps(attack.get("weapons", []), ensure_ascii=False),
             attack["verification_level"],
             1 if attack.get("needs_review") else 0,
             attack.get("review_reason"), _now(), _now()),
        )
        attack_id = cursor.lastrowid
        action = "created"
    else:
        attack_id = existing["id"]
        action = "merged"
        if LEVEL_ORDER[attack["verification_level"]] >= LEVEL_ORDER[existing["verification_level"]]:
            for field in ("citywide_killed", "citywide_injured"):
                value = attack.get(field)
                if value is not None and value != existing[field]:
                    conn.execute(f"UPDATE attacks SET {field} = ? WHERE id = ?",
                                 (value, attack_id))
            if attack.get("weapons"):
                conn.execute(
                    "UPDATE attacks SET weapons = ? WHERE id = ?",
                    (json.dumps(attack["weapons"], ensure_ascii=False), attack_id))
            conn.execute(
                "UPDATE attacks SET verification_level = ?, updated_at = ? WHERE id = ?",
                (attack["verification_level"], _now(), attack_id))

    _write_provenance(conn, attack_id, claims, assessments, reconciliation,
                      table="attack_claims")

    citywide = conn.execute(
        "SELECT citywide_killed FROM attacks WHERE id = ?", (attack_id,)
    ).fetchone()["citywide_killed"]
    site_total = _site_total_killed(conn, attack_id)

    reasons = [r for r in (attack.get("review_reason"),
                           _consistency_note(citywide, site_total)) if r]
    needs_review = bool(attack.get("needs_review")) or bool(
        _consistency_note(citywide, site_total))

    if needs_review:
        conn.execute(
            """UPDATE attacks SET needs_review = 1, review_reason = ?,
                                  reviewed_by = NULL, reviewed_at = NULL
                WHERE id = ?""",
            (" ".join(reasons), attack_id))

    conn.commit()
    return {
        "attack_id": attack_id,
        "action": action,
        "site_total_killed": site_total,
        "citywide_killed": citywide,
        "needs_review": needs_review,
        "review_reason": " ".join(reasons) if reasons else None,
    }


def attack_summary(conn: sqlite3.Connection, attack_id: int) -> dict:
    """The attack and every impact recorded under it."""
    row = conn.execute("SELECT * FROM attacks WHERE id = ?", (attack_id,)).fetchone()
    if row is None:
        raise KeyError(f"No attack with id {attack_id}.")
    impacts = conn.execute(
        """SELECT id, district, address_text, structure_type, killed, injured,
                  verification_level, needs_review
             FROM incidents WHERE attack_id = ? ORDER BY district""",
        (attack_id,),
    ).fetchall()
    return {
        "attack_id": row["id"],
        "occurred_at": row["occurred_at"],
        "citywide_killed": row["citywide_killed"],
        "citywide_injured": row["citywide_injured"],
        "weapons": json.loads(row["weapons"]),
        "verification_level": row["verification_level"],
        "needs_review": bool(row["needs_review"]),
        "review_reason": row["review_reason"],
        "impacts": [dict(i) for i in impacts],
        "site_total_killed": _site_total_killed(conn, attack_id),
    }
