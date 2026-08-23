import json

import pytest

from common.schemas import AttackClaim, IncidentClaim
from mcp_server.attacks_store import upsert_attack
from mcp_server.db import connect
from mcp_server.incidents_store import pending_review, upsert_incident

SUSP = "https://suspilne.media/kyiv/1"


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "a.sqlite")


def attack(killed=5, injured=23, occurred="2026-06-17T02:00:00Z",
           weapons=None, level="corroborated", needs_review=False, reason=None):
    return {
        "occurred_at": occurred,
        "citywide_killed": killed,
        "citywide_injured": injured,
        "weapons": weapons if weapons is not None else [
            {"kind": "shahed", "launched": 200, "intercepted": 187},
            {"kind": "cruise_missile", "launched": 12, "intercepted": 9},
        ],
        "verification_level": level,
        "needs_review": needs_review,
        "review_reason": reason,
    }


def attack_claim(url=SUSP, killed=5, published="2026-06-17T09:00:00Z"):
    return AttackClaim(
        source_url=url, published_at=published,
        occurred_at="2026-06-17T02:00:00Z",
        citywide_killed=killed, citywide_injured=23,
        weapons=[{"kind": "shahed", "launched": 200, "intercepted": 187}],
        quote="Унаслідок нічної атаки на Київ загинули пʼятеро людей.",
    )


def incident(lat=50.4310, lon=30.4400, killed=None, attack_id=None):
    return {
        "occurred_at": "2026-06-17T02:10:00Z",
        "lat": lat, "lon": lon, "district": "Солом'янський",
        "claimed_district": None, "address_text": "вул. Освіти, 4",
        "structure_type": "residential", "killed": killed, "injured": None,
        "damage": ["fire"], "verification_level": "corroborated",
        "geocode_confidence": 0.9, "needs_review": False, "review_reason": None,
        "attack_id": attack_id,
    }


def incident_claim(url=SUSP, killed=None):
    return IncidentClaim(
        source_url=url, published_at="2026-06-17T09:00:00Z",
        occurred_at="2026-06-17T02:10:00Z", location_text="вул. Освіти, 4",
        structure_type="residential", killed=killed, damage=["fire"],
        quote="Зруйновано перший підʼїзд житлового будинку.",
    )


def recon():
    return {"fields": {}, "verification_level": "corroborated",
            "missing_for_next_level": [], "usable_groups": ["suspilne"],
            "needs_review": False, "review_reason": None, "has_conflict": False}


def assessment():
    return [{"url": SUSP, "tier": 2, "voice_tier": 2,
             "independence_group": "suspilne", "usable_as_corroborator": True,
             "adds": [], "reason": "test"}]


# --- one night, one row -----------------------------------------------------

def test_first_write_creates_the_attack(conn):
    result = upsert_attack(conn, attack(), [attack_claim()], assessment(), recon())
    assert result["action"] == "created"
    assert result["attack_id"] > 0


def test_the_same_night_reported_again_merges(conn):
    upsert_attack(conn, attack(killed=3), [attack_claim(killed=3)], assessment(), recon())
    result = upsert_attack(conn, attack(killed=5), [attack_claim()], assessment(), recon())
    assert result["action"] == "merged"
    assert conn.execute("SELECT COUNT(*) n FROM attacks").fetchone()["n"] == 1
    assert conn.execute("SELECT citywide_killed FROM attacks").fetchone()[0] == 5


def test_a_different_night_is_a_different_attack(conn):
    upsert_attack(conn, attack(), [attack_claim()], assessment(), recon())
    result = upsert_attack(conn, attack(occurred="2026-06-25T02:00:00Z"),
                           [attack_claim()], assessment(), recon())
    assert result["action"] == "created"
    assert conn.execute("SELECT COUNT(*) n FROM attacks").fetchone()["n"] == 2


def test_reports_hours_apart_on_the_same_night_are_one_attack(conn):
    upsert_attack(conn, attack(occurred="2026-06-17T01:00:00Z"),
                  [attack_claim()], assessment(), recon())
    result = upsert_attack(conn, attack(occurred="2026-06-17T04:30:00Z"),
                           [attack_claim()], assessment(), recon())
    assert result["action"] == "merged"


def test_weapons_are_stored_and_read_back(conn):
    result = upsert_attack(conn, attack(), [attack_claim()], assessment(), recon())
    row = conn.execute("SELECT weapons FROM attacks WHERE id = ?",
                       (result["attack_id"],)).fetchone()
    weapons = json.loads(row["weapons"])
    assert {w["kind"] for w in weapons} == {"shahed", "cruise_missile"}
    assert weapons[0]["launched"] == 200


# --- the consistency check --------------------------------------------------

def test_per_address_deaths_may_not_exceed_the_citywide_figure(conn):
    """Three killed here plus four killed there cannot happen on a night that
    killed five. Something is wrong and a person has to look."""
    result = upsert_attack(conn, attack(killed=5), [attack_claim()], assessment(), recon())
    aid = result["attack_id"]
    upsert_incident(conn, incident(killed=3, attack_id=aid),
                    [incident_claim(killed=3)], assessment(), recon())
    upsert_incident(conn, incident(lat=50.5100, lon=30.4980, killed=4, attack_id=aid),
                    [incident_claim(killed=4)], assessment(), recon())

    check = upsert_attack(conn, attack(killed=5), [attack_claim()], assessment(), recon())
    assert check["site_total_killed"] == 7
    assert check["needs_review"] is True
    assert "7" in check["review_reason"] and "5" in check["review_reason"]


def test_per_address_deaths_within_the_citywide_figure_are_fine(conn):
    result = upsert_attack(conn, attack(killed=5), [attack_claim()], assessment(), recon())
    aid = result["attack_id"]
    upsert_incident(conn, incident(killed=3, attack_id=aid),
                    [incident_claim(killed=3)], assessment(), recon())

    check = upsert_attack(conn, attack(killed=5), [attack_claim()], assessment(), recon())
    assert check["site_total_killed"] == 3
    assert check["needs_review"] is False


def test_incidents_without_per_address_figures_do_not_trip_the_check(conn):
    result = upsert_attack(conn, attack(killed=5), [attack_claim()], assessment(), recon())
    aid = result["attack_id"]
    upsert_incident(conn, incident(killed=None, attack_id=aid),
                    [incident_claim()], assessment(), recon())

    check = upsert_attack(conn, attack(killed=5), [attack_claim()], assessment(), recon())
    assert check["site_total_killed"] == 0
    assert check["needs_review"] is False


def test_a_flagged_attack_reaches_the_review_queue(conn):
    upsert_attack(conn, attack(needs_review=True, reason="Weapon counts disagree."),
                  [attack_claim()], assessment(), recon())
    queue = pending_review(conn)
    assert any(item["kind"] == "attack" for item in queue)


def test_incidents_are_linked_to_their_attack(conn):
    result = upsert_attack(conn, attack(), [attack_claim()], assessment(), recon())
    aid = result["attack_id"]
    written = upsert_incident(conn, incident(attack_id=aid), [incident_claim()],
                              assessment(), recon())
    row = conn.execute("SELECT attack_id FROM incidents WHERE id = ?",
                       (written["incident_id"],)).fetchone()
    assert row["attack_id"] == aid
