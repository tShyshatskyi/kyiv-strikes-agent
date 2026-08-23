import pytest

from common.schemas import IncidentClaim
from mcp_server.db import connect
from mcp_server.incidents_store import (
    clear_review,
    pending_review,
    to_geojson,
    upsert_incident,
)

SUSP = "https://suspilne.media/kyiv/1"
PRAV = "https://www.pravda.com.ua/news/1"


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "test.sqlite")


def incident(lat=50.4310, lon=30.4400, occurred="2026-06-17T02:10:00Z",
             killed=3, level="corroborated", needs_review=False, reason=None,
             claimed_district=None):
    return {
        "occurred_at": occurred,
        "lat": lat, "lon": lon,
        "district": "Солом'янський",
        "claimed_district": claimed_district,
        "address_text": "вул. Освіти, 4",
        "structure_type": "residential",
        "killed": killed, "injured": 12,
        "damage": ["fire", "collapse"],
        "verification_level": level,
        "geocode_confidence": 0.9,
        "needs_review": needs_review,
        "review_reason": reason,
    }


def claim(url=SUSP, killed=3, media=None):
    return IncidentClaim(
        source_url=url,
        published_at="2026-06-17T09:00:00Z",
        occurred_at="2026-06-17T02:10:00Z",
        location_text="вул. Освіти, 4",
        claimed_district="Солом'янський",
        structure_type="residential",
        killed=killed, injured=12,
        damage=["fire"],
        media_urls=media if media is not None else ["https://suspilne.media/img/1.jpg"],
        quote="Загинули троє людей, дванадцятеро дістали поранення.",
    )


def assessment(url=SUSP, group="suspilne", tier=2):
    return {"url": url, "tier": tier, "independence_group": group,
            "usable_as_corroborator": True, "adds": [], "reason": "test"}


def reconciliation(killed=3, status="corroborated"):
    return {
        "fields": {"killed": {"value": killed, "status": status,
                              "values_seen": [killed], "supporting_sources": [SUSP]}},
        "verification_level": "corroborated",
        "missing_for_next_level": [],
        "usable_groups": ["suspilne"],
        "needs_review": False,
        "review_reason": None,
    }


def write(conn, inc=None, claims=None, assessments=None, recon=None):
    return upsert_incident(
        conn,
        inc or incident(),
        claims or [claim()],
        assessments or [assessment()],
        recon or reconciliation(),
    )


# --- creating and merging ---------------------------------------------------

def test_first_write_creates(conn):
    result = write(conn)
    assert result["action"] == "created"
    assert result["incident_id"] > 0


def test_nearby_and_recent_merges(conn):
    write(conn)
    # ~55 m north, 2 hours later, better verification.
    nearby = incident(lat=50.4315, occurred="2026-06-17T04:10:00Z",
                      killed=5, level="well_documented")
    result = write(conn, nearby, [claim(killed=5)], None, reconciliation(killed=5))
    assert result["action"] == "merged"
    assert "killed" in result["changed_fields"]
    assert conn.execute("SELECT COUNT(*) n FROM incidents").fetchone()["n"] == 1


def test_far_away_creates_a_separate_incident(conn):
    write(conn)
    result = write(conn, incident(lat=50.4600))       # ~3 km away
    assert result["action"] == "created"
    assert conn.execute("SELECT COUNT(*) n FROM incidents").fetchone()["n"] == 2


def test_same_place_days_later_creates_a_separate_incident(conn):
    write(conn)
    result = write(conn, incident(occurred="2026-06-25T02:10:00Z"))
    assert result["action"] == "created"


def test_merge_refuses_to_downgrade_verification(conn):
    write(conn, incident(level="well_documented"))
    weaker = incident(lat=50.4315, level="single_source", killed=99)
    result = write(conn, weaker, [claim(killed=99)], None, reconciliation(killed=99))
    assert result["action"] == "rejected_lower_confidence"
    row = conn.execute("SELECT killed, verification_level FROM incidents").fetchone()
    assert row["killed"] == 3
    assert row["verification_level"] == "well_documented"


# --- provenance -------------------------------------------------------------

def test_every_value_keeps_the_sentence_that_supports_it(conn):
    write(conn)
    row = conn.execute(
        "SELECT value, quote, status FROM incident_claims WHERE field = 'killed'"
    ).fetchone()
    assert row["value"] == "3"
    assert "Загинули троє" in row["quote"]
    assert row["status"] == "corroborated"


def test_a_value_can_be_traced_back_to_its_article(conn):
    write(conn)
    row = conn.execute(
        """SELECT i.killed, s.url, c.quote
             FROM incidents i
             JOIN incident_claims c ON c.incident_id = i.id AND c.field = 'killed'
             JOIN sources s ON s.id = c.source_id"""
    ).fetchone()
    assert row["killed"] == 3
    assert row["url"] == SUSP
    assert "Загинули троє" in row["quote"]


def test_media_urls_are_recorded_once(conn):
    write(conn)
    write(conn, incident(lat=50.4315))
    assert conn.execute("SELECT COUNT(*) n FROM incident_media").fetchone()["n"] == 1


# --- the human review queue -------------------------------------------------

def test_a_flagged_incident_lands_in_the_review_queue(conn):
    write(conn, incident(needs_review=True, reason="District disagrees.",
                         claimed_district="Оболонський"))
    queue = pending_review(conn)
    assert len(queue) == 1
    assert queue[0]["review_reason"] == "District disagrees."
    assert queue[0]["claimed_district"] == "Оболонський"
    assert queue[0]["district"] == "Солом'янський"


def test_a_clean_incident_does_not_land_in_the_queue(conn):
    write(conn)
    assert pending_review(conn) == []


def test_clearing_a_review_records_who_did_it(conn):
    result = write(conn, incident(needs_review=True, reason="District disagrees."))
    clear_review(conn, result["incident_id"], reviewed_by="taras")
    assert pending_review(conn) == []
    row = conn.execute("SELECT needs_review, reviewed_by FROM incidents").fetchone()
    assert row["needs_review"] == 0
    assert row["reviewed_by"] == "taras"


def test_clearing_an_unknown_incident_is_an_error(conn):
    with pytest.raises(KeyError):
        clear_review(conn, 999, reviewed_by="taras")


def test_a_merge_that_introduces_doubt_reopens_the_review(conn):
    result = write(conn)
    clear_review(conn, result["incident_id"], reviewed_by="taras")
    write(conn, incident(lat=50.4315, needs_review=True, reason="Count went down."))
    assert len(pending_review(conn)) == 1


# --- export -----------------------------------------------------------------

def test_geojson_export_shape(conn):
    write(conn)
    gj = to_geojson(conn)
    feature = gj["features"][0]
    assert gj["type"] == "FeatureCollection"
    assert feature["geometry"]["coordinates"] == [30.4400, 50.4310]
    assert feature["properties"]["district"] == "Солом'янський"


def test_unreviewed_incidents_are_kept_out_of_the_verified_export(conn):
    write(conn, incident(needs_review=True, reason="District disagrees."))
    assert to_geojson(conn)["features"] == []
    assert len(to_geojson(conn, include_unreviewed=True)["features"]) == 1


def test_a_cleared_incident_enters_the_verified_export(conn):
    result = write(conn, incident(needs_review=True, reason="District disagrees."))
    clear_review(conn, result["incident_id"], reviewed_by="taras")
    assert len(to_geojson(conn)["features"]) == 1


def test_a_database_from_an_older_version_says_what_to_do(tmp_path):
    """A leftover file with the old schema must fail loudly at open time, not
    cryptically on the first insert."""
    import sqlite3
    stale = tmp_path / "stale.sqlite"
    old = sqlite3.connect(str(stale))
    old.execute("""CREATE TABLE incidents (
        id INTEGER PRIMARY KEY, occurred_at TEXT, lat REAL, lon REAL,
        district TEXT, structure_type TEXT, damage TEXT,
        verification_level TEXT, geocode_confidence REAL,
        created_at TEXT, updated_at TEXT)""")
    old.commit()
    old.close()

    with pytest.raises(RuntimeError) as exc:
        connect(stale)
    assert "attack_id" in str(exc.value)
    assert "Delete the file" in str(exc.value)
