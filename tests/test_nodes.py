import pytest

from agent.nodes import (
    MAX_ROUNDS,
    article_links,
    build_search_url,
    extract_date,
    next_targets,
    parse_request,
    should_continue,
    unwrap,
)


# --- reading the question ---------------------------------------------------

def test_iso_date_is_read():
    assert extract_date("strike on Kyiv 2026-06-17") == "2026-06-17"


def test_ukrainian_date_is_read():
    assert extract_date("удар по Києву 17 червня 2026") == "2026-06-17"


def test_a_question_without_a_date_says_what_is_missing():
    with pytest.raises(ValueError) as exc:
        extract_date("удар по Києву")
    assert "Include one" in str(exc.value)


def test_parsing_initializes_the_loop():
    state = parse_request({"query": "удар по Києву 17 червня 2026"})
    assert state["event_date"] == "2026-06-17"
    assert state["round"] == 0
    assert state["articles"] == []
    assert state["unreachable"] == []


# --- the one decision in the graph ------------------------------------------

def _state(level, missing=None, conflict=False, round_=0, groups=None,
           has_conflict=False):
    return {
        "round": round_,
        "reconciliation": {
            "verification_level": level,
            "missing_for_next_level": missing if missing is not None else [],
            "usable_groups": groups or [],
            "has_conflict": has_conflict,
        },
        "location": {"district_conflict": conflict},
    }


def test_enough_evidence_writes_the_record():
    assert should_continue(_state("well_documented")) == "persist"


def test_one_voice_alone_sends_the_agent_back_for_another():
    assert should_continue(_state("single_source", ["need another group"])) == "discover_sources"


def test_nothing_usable_sends_the_agent_back():
    assert should_continue(_state("unverified", ["anything at all"])) == "discover_sources"


def test_corroborated_stops_even_though_an_official_source_is_still_missing():
    """Two independent voices is enough. The missing official source is reported
    but not chased: the official sites are unfetchable, so more searching cannot
    reliably close that gap."""
    assert should_continue(
        _state("corroborated", ["An official (tier 1) source."])) == "persist"


def test_a_contradiction_between_sources_does_send_the_agent_back():
    assert should_continue(
        _state("corroborated", ["settle killed"], has_conflict=True)) == "discover_sources"


def test_corroborated_with_nothing_missing_writes():
    assert should_continue(_state("corroborated", [])) == "persist"


def test_a_district_disagreement_sends_the_agent_back_even_when_well_evidenced():
    """Strong evidence about the wrong place is still the wrong place."""
    assert should_continue(_state("well_documented", conflict=True)) == "discover_sources"


def test_the_round_budget_stops_the_loop():
    state = _state("single_source", ["need another group"], round_=MAX_ROUNDS)
    assert should_continue(state) == "persist"


def test_an_incident_nobody_else_reported_is_still_recorded():
    """Not looping forever is the point of the budget: a thinly reported strike
    gets written with its weak evidence stated, not dropped."""
    state = _state("single_source", ["need another group"], round_=MAX_ROUNDS)
    assert should_continue(state) == "persist"


# --- choosing where to look next --------------------------------------------

def test_the_next_round_avoids_voices_that_already_spoke():
    targets = next_targets({"searched": [], "reconciliation": {"usable_groups": ["suspilne"]}})
    assert "suspilne" not in targets
    assert "pravda" in targets


def test_a_source_already_searched_is_not_searched_again():
    targets = next_targets({"searched": ["pravda"], "reconciliation": {"usable_groups": []}})
    assert "pravda" not in targets


def test_when_everything_has_been_tried_the_list_is_empty():
    targets = next_targets({
        "searched": ["suspilne", "pravda", "hromadske"],
        "reconciliation": {"usable_groups": []},
    })
    assert targets == []


def test_search_urls_are_built_with_an_encoded_query():
    url = build_search_url("suspilne", "удар по Києву")
    assert url.startswith("https://suspilne.media/search/?query=")
    assert " " not in url


# --- reading a page snapshot ------------------------------------------------

SNAPSHOT = """
- link "Головна" [ref=e1]:
  - /url: https://suspilne.media/
- link "Новина про удар" [ref=e2]:
  - /url: https://suspilne.media/kyiv/1234567-udar-po-kievu/
- link "Тег Київ" [ref=e3]:
  - /url: https://suspilne.media/tag/kyiv/
- link "Інше видання" [ref=e4]:
  - /url: https://www.pravda.com.ua/news/2026/06/17/7412345/
"""


def test_article_links_keep_only_the_wanted_domain():
    links = article_links(SNAPSHOT, "suspilne.media")
    assert links == ["https://suspilne.media/kyiv/1234567-udar-po-kievu/"]


def test_section_fronts_and_tag_pages_are_skipped():
    links = article_links(SNAPSHOT, "suspilne.media")
    assert not any("/tag/" in url for url in links)
    assert "https://suspilne.media/" not in links


def test_links_from_another_outlet_are_found_under_its_own_domain():
    links = article_links(SNAPSHOT, "pravda.com.ua")
    assert links == ["https://www.pravda.com.ua/news/2026/06/17/7412345/"]


def test_the_link_limit_is_respected():
    many = "\n".join(
        f'- link "n{i}":\n  - /url: https://suspilne.media/kyiv/{1000000 + i}-x/'
        for i in range(20)
    )
    assert len(article_links(many, "suspilne.media", limit=3)) == 3


# --- reading tool answers ---------------------------------------------------

def test_an_envelope_is_read_from_a_plain_string():
    assert unwrap('{"ok": true, "data": {"x": 1}}')["data"]["x"] == 1


def test_an_envelope_is_read_from_mcp_content_blocks():
    blocks = [{"type": "text", "text": '{"ok": false, "error": {"code": "OUT_OF_AREA"}}'}]
    assert unwrap(blocks)["error"]["code"] == "OUT_OF_AREA"
