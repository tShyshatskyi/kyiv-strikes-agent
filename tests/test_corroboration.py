import pytest

from common.schemas import IncidentClaim
from mcp_server.corroboration import corroborate

DSNS = "https://dsns.gov.ua/news/1"
SUSP = "https://suspilne.media/kyiv/1"
PRAV = "https://www.pravda.com.ua/news/1"
HROM = "https://hromadske.ua/news/1"


def claim(url, published, killed=None, injured=None, structure="residential"):
    return IncidentClaim(
        source_url=url,
        published_at=published,
        occurred_at="2026-06-17T02:10:00Z",
        location_text="вул. Освіти, 4",
        claimed_district="Солом'янський",
        structure_type=structure,
        killed=killed,
        injured=injured,
        damage=["fire"],
        quote="Зруйновано під'їзд житлового будинку на вул. Освіти, 4.",
    )


def assessment(url, group, tier=2, usable=True):
    return {"url": url, "tier": tier, "independence_group": group,
            "usable_as_corroborator": usable, "adds": [], "reason": "test"}


# --- agreement --------------------------------------------------------------

def test_one_voice_alone_is_single_source():
    result = corroborate([claim(SUSP, "2026-06-17T09:00:00Z", killed=3)],
                         [assessment(SUSP, "suspilne")])
    assert result["fields"]["killed"]["status"] == "single_source"
    assert result["verification_level"] == "single_source"
    assert result["needs_review"] is False


def test_two_articles_of_one_voice_are_still_single_source():
    claims = [claim(DSNS, "2026-06-17T08:00:00Z", killed=3),
              claim(SUSP, "2026-06-17T09:00:00Z", killed=3)]
    assessments = [assessment(DSNS, "dsns", tier=1), assessment(SUSP, "dsns")]
    result = corroborate(claims, assessments)
    assert result["fields"]["killed"]["status"] == "single_source"
    assert result["verification_level"] == "single_source"


def test_two_independent_voices_agreeing_are_corroborated():
    claims = [claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
              claim(PRAV, "2026-06-17T09:30:00Z", killed=3)]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    assert result["fields"]["killed"]["value"] == 3
    assert result["fields"]["killed"]["status"] == "corroborated"
    assert result["verification_level"] == "corroborated"
    assert result["needs_review"] is False


def test_official_plus_independent_newsroom_is_well_documented():
    claims = [claim(DSNS, "2026-06-17T08:00:00Z", killed=3),
              claim(SUSP, "2026-06-17T09:00:00Z", killed=3)]
    assessments = [assessment(DSNS, "dsns", tier=1), assessment(SUSP, "suspilne")]
    result = corroborate(claims, assessments)
    assert result["verification_level"] == "well_documented"
    assert result["missing_for_next_level"] == []


def test_no_usable_sources_is_unverified():
    result = corroborate([claim(DSNS, "2026-06-17T08:00:00Z", killed=3)],
                         [assessment(DSNS, "dsns", tier=1, usable=False)])
    assert result["verification_level"] == "unverified"


# --- the count rising over time is an update, not a disagreement ------------

def test_a_later_higher_count_supersedes_and_needs_no_review():
    """Bodies are recovered from rubble over days. That is the count being
    completed, not two sources contradicting each other."""
    claims = [claim(SUSP, "2026-06-17T06:00:00Z", killed=3),
              claim(PRAV, "2026-06-17T18:00:00Z", killed=5)]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    assert result["fields"]["killed"]["value"] == 5
    assert result["fields"]["killed"]["status"] == "superseded"
    assert result["needs_review"] is False


def test_a_later_lower_count_supersedes_but_asks_for_a_human():
    """A downward revision is legitimate — early reports sometimes overcount —
    but it is unusual enough that a person should see it."""
    claims = [claim(SUSP, "2026-06-17T06:00:00Z", killed=7),
              claim(PRAV, "2026-06-18T18:00:00Z", killed=5)]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    assert result["fields"]["killed"]["value"] == 5
    assert result["fields"]["killed"]["status"] == "superseded"
    assert result["needs_review"] is True
    assert "killed" in result["review_reason"]
    assert "7" in result["review_reason"] and "5" in result["review_reason"]


# --- genuine contradiction --------------------------------------------------

def test_same_hour_disagreement_is_a_contradiction_and_goes_to_a_human():
    claims = [claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
              claim(PRAV, "2026-06-17T09:20:00Z", killed=7)]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    assert result["fields"]["killed"]["status"] == "conflicting"
    assert result["needs_review"] is True
    assert sorted(result["fields"]["killed"]["values_seen"]) == [3, 7]


def test_a_contradiction_is_named_in_what_is_still_missing():
    claims = [claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
              claim(PRAV, "2026-06-17T09:20:00Z", killed=7)]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    assert any("killed" in item for item in result["missing_for_next_level"])


def test_disagreement_about_what_was_hit_is_a_contradiction():
    claims = [claim(SUSP, "2026-06-17T09:00:00Z", structure="residential"),
              claim(PRAV, "2026-06-17T09:30:00Z", structure="school")]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    assert result["fields"]["structure_type"]["status"] == "conflicting"
    assert result["needs_review"] is True


# --- what the agent should go and look for next ------------------------------

def test_single_source_names_the_voice_to_avoid_repeating():
    result = corroborate([claim(SUSP, "2026-06-17T09:00:00Z", killed=3)],
                         [assessment(SUSP, "suspilne")])
    missing = " ".join(result["missing_for_next_level"]).lower()
    assert "suspilne" in missing
    assert "independent" in missing


def test_corroborated_still_asks_for_an_official_source():
    claims = [claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
              claim(PRAV, "2026-06-17T09:30:00Z", killed=3)]
    assessments = [assessment(SUSP, "suspilne"), assessment(PRAV, "pravda")]
    result = corroborate(claims, assessments)
    missing = " ".join(result["missing_for_next_level"]).lower()
    assert "official" in missing or "tier 1" in missing


def test_usable_groups_are_reported_for_the_next_search():
    claims = [claim(SUSP, "2026-06-17T09:00:00Z", killed=3),
              claim(HROM, "2026-06-17T09:30:00Z", killed=3)]
    assessments = [assessment(SUSP, "suspilne"), assessment(HROM, "hromadske")]
    result = corroborate(claims, assessments)
    assert result["usable_groups"] == ["hromadske", "suspilne"]


def test_a_field_nobody_reported_is_simply_absent():
    result = corroborate([claim(SUSP, "2026-06-17T09:00:00Z", killed=3)],
                         [assessment(SUSP, "suspilne")])
    assert "injured" not in result["fields"]
