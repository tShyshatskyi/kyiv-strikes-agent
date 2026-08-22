from datetime import date

import pytest

from common.schemas import IncidentClaim, SourceRef
from mcp_server.credibility import assess_sources, load_registry

REGISTRY = load_registry("data/source_registry.yaml")
EVENT_DATE = date(2026, 6, 17)

DSNS = "https://dsns.gov.ua/news/1"
KMVA = "https://kyivcity.gov.ua/news/1"
SUSP = "https://suspilne.media/kyiv/1"
PRAV = "https://www.pravda.com.ua/news/1"
UNIAN = "https://unian.ua/1"
TG = "https://t.me/somechannel/99"
BLOG = "https://randomblog.example/1"


def src(domain, url, published="2026-06-17T09:00:00Z", cited=None):
    return SourceRef(
        url=url, domain=domain, published_at=published, cited_sources=cited or []
    )


def claim(url, published="2026-06-17T09:00:00Z", killed=None, injured=None,
          structure="residential", damage=None):
    return IncidentClaim(
        source_url=url,
        published_at=published,
        occurred_at="2026-06-17T02:10:00Z",
        location_text="вул. Освіти, 4",
        claimed_district="Солом'янський",
        structure_type=structure,
        killed=killed,
        injured=injured,
        damage=damage if damage is not None else ["fire"],
        quote="Зруйновано під'їзд житлового будинку на вул. Освіти, 4.",
    )


def by_url(results):
    return {r["url"]: r for r in results}


# --- tiers ------------------------------------------------------------------

def test_official_source_is_tier_1_and_usable():
    r = by_url(assess_sources([src("dsns.gov.ua", DSNS)], REGISTRY, EVENT_DATE))[DSNS]
    assert r["tier"] == 1
    assert r["independence_group"] == "dsns"
    assert r["usable_as_corroborator"] is True


def test_newsroom_with_original_reporting_is_its_own_voice():
    r = by_url(assess_sources([src("suspilne.media", SUSP)], REGISTRY, EVENT_DATE))[SUSP]
    assert r["tier"] == 2
    assert r["independence_group"] == "suspilne"
    assert r["usable_as_corroborator"] is True


def test_excluded_domain_is_never_usable():
    r = by_url(assess_sources([src("t.me", TG)], REGISTRY, EVENT_DATE))[TG]
    assert r["usable_as_corroborator"] is False
    assert "excluded" in r["reason"].lower()


def test_unknown_domain_is_not_usable():
    r = by_url(assess_sources([src("randomblog.example", BLOG)], REGISTRY, EVENT_DATE))[BLOG]
    assert r["usable_as_corroborator"] is False


# --- the independence rule, and the exception the user asked for ------------

def test_relay_that_adds_nothing_counts_as_the_source_it_relays():
    """Суспільне reprinting a ДСНС statement verbatim is not a second voice."""
    sources = [src("dsns.gov.ua", DSNS),
               src("suspilne.media", SUSP, cited=["dsns.gov.ua"])]
    claims = [claim(DSNS, killed=3), claim(SUSP, killed=3)]
    r = by_url(assess_sources(sources, REGISTRY, EVENT_DATE, claims=claims))
    assert r[SUSP]["independence_group"] == "dsns"
    assert r[SUSP]["adds"] == []


def test_relay_that_adds_its_own_facts_becomes_its_own_voice():
    """Суспільне relays ДСНС but reports an injured count ДСНС never gave. They
    got that somewhere, so on that basis they are a separate voice."""
    sources = [src("dsns.gov.ua", DSNS),
               src("suspilne.media", SUSP, cited=["dsns.gov.ua"])]
    claims = [claim(DSNS, killed=3), claim(SUSP, killed=3, injured=12)]
    r = by_url(assess_sources(sources, REGISTRY, EVENT_DATE, claims=claims))
    assert r[SUSP]["independence_group"] == "suspilne"
    assert "injured" in r[SUSP]["adds"]
    assert "injured" in r[SUSP]["reason"]


def test_aggregator_relaying_a_primary_source_is_usable_as_that_source():
    sources = [src("kyivcity.gov.ua", KMVA),
               src("unian.ua", UNIAN, cited=["kyivcity.gov.ua"])]
    claims = [claim(KMVA, killed=3), claim(UNIAN, killed=3)]
    r = by_url(assess_sources(sources, REGISTRY, EVENT_DATE, claims=claims))
    assert r[UNIAN]["independence_group"] == "kmva"
    assert r[UNIAN]["usable_as_corroborator"] is True


def test_aggregator_with_its_own_facts_becomes_its_own_voice():
    sources = [src("kyivcity.gov.ua", KMVA),
               src("unian.ua", UNIAN, cited=["kyivcity.gov.ua"])]
    claims = [claim(KMVA, killed=3),
              claim(UNIAN, killed=3, damage=["fire", "collapse"], injured=8)]
    r = by_url(assess_sources(sources, REGISTRY, EVENT_DATE, claims=claims))
    assert r[UNIAN]["independence_group"] == "unian"
    assert r[UNIAN]["usable_as_corroborator"] is True


def test_aggregator_with_no_primary_link_is_not_usable():
    r = by_url(assess_sources([src("unian.ua", UNIAN)], REGISTRY, EVENT_DATE))[UNIAN]
    assert r["tier"] == 3
    assert r["usable_as_corroborator"] is False


def test_relay_without_the_primary_present_is_not_granted_independence():
    """With nothing to compare against we cannot tell what was added, so we do
    not hand out independence on trust."""
    sources = [src("suspilne.media", SUSP, cited=["dsns.gov.ua"])]
    claims = [claim(SUSP, killed=3, injured=12)]
    r = by_url(assess_sources(sources, REGISTRY, EVENT_DATE, claims=claims))
    assert r[SUSP]["independence_group"] == "dsns"


def test_assessment_without_claims_falls_back_to_plain_relay_rule():
    sources = [src("suspilne.media", SUSP, cited=["dsns.gov.ua"])]
    r = by_url(assess_sources(sources, REGISTRY, EVENT_DATE))[SUSP]
    assert r["independence_group"] == "dsns"


# --- the relevance window ---------------------------------------------------

def test_article_published_before_the_event_is_not_usable():
    r = by_url(assess_sources(
        [src("suspilne.media", SUSP, published="2026-06-10T09:00:00Z")],
        REGISTRY, EVENT_DATE))[SUSP]
    assert r["usable_as_corroborator"] is False
    assert "window" in r["reason"].lower()


def test_article_long_after_the_event_falls_outside_the_default_window():
    r = by_url(assess_sources(
        [src("suspilne.media", SUSP, published="2026-07-08T09:00:00Z")],
        REGISTRY, EVENT_DATE))[SUSP]
    assert r["usable_as_corroborator"] is False


def test_the_window_can_be_widened_to_admit_later_summaries():
    r = by_url(assess_sources(
        [src("suspilne.media", SUSP, published="2026-07-08T09:00:00Z")],
        REGISTRY, EVENT_DATE, relevance_window_days=30))[SUSP]
    assert r["usable_as_corroborator"] is True


def test_window_must_be_positive():
    with pytest.raises(ValueError):
        assess_sources([src("suspilne.media", SUSP)], REGISTRY, EVENT_DATE,
                       relevance_window_days=0)


# --- what the probe found: some official sources cannot be fetched ----------

def test_bot_walled_officials_are_kept_for_grouping_but_not_for_fetching():
    """ДСНС and КМВА answer a headless browser with HTTP 403 and a security
    interstitial. We do not try to get around that. They stay in the registry
    so an outlet citing them is still grouped under them, but the agent never
    navigates to them."""
    from mcp_server.credibility import reachable_domains
    assert "dsns.gov.ua" in REGISTRY.entries
    assert REGISTRY.entries["dsns.gov.ua"].tier == 1
    assert REGISTRY.entries["dsns.gov.ua"].directly_reachable is False
    assert "dsns.gov.ua" not in reachable_domains(REGISTRY)
    assert "npu.gov.ua" in reachable_domains(REGISTRY)


def test_a_relay_of_an_unreachable_official_still_carries_its_voice():
    sources = [src("suspilne.media", SUSP, cited=["dsns.gov.ua"])]
    r = by_url(assess_sources(sources, REGISTRY, EVENT_DATE))[SUSP]
    assert r["independence_group"] == "dsns"
    assert r["usable_as_corroborator"] is True


def test_a_newsroom_relaying_an_official_statement_carries_an_official_voice():
    """The official sites are unfetchable, so official facts reach us through
    newsrooms. The voice is still official even though the masthead is tier 2."""
    r = by_url(assess_sources(
        [src("suspilne.media", SUSP, cited=["dsns.gov.ua"])],
        REGISTRY, EVENT_DATE))[SUSP]
    assert r["tier"] == 2           # the outlet
    assert r["voice_tier"] == 1     # whose fact it is
    assert r["independence_group"] == "dsns"


def test_a_newsroom_speaking_for_itself_has_its_own_tier_as_its_voice():
    r = by_url(assess_sources([src("suspilne.media", SUSP)], REGISTRY, EVENT_DATE))[SUSP]
    assert r["tier"] == 2
    assert r["voice_tier"] == 2
