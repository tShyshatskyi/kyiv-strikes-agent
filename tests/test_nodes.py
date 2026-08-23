import pytest

from agent.nodes import (
    MAX_ROUNDS,
    article_candidates,
    article_links,
    build_search_url,
    extract_date,
    next_targets,
    parse_request,
    should_continue,
    unwrap,
    worth_opening,
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
    assert "hromadske" in targets


def test_a_source_already_searched_is_not_searched_again():
    targets = next_targets({"searched": ["hromadske"],
                            "reconciliation": {"usable_groups": []}})
    assert "hromadske" not in targets


def test_when_everything_has_been_tried_the_list_is_empty():
    targets = next_targets({
        "searched": ["suspilne", "hromadske"],
        "reconciliation": {"usable_groups": []},
    })
    assert targets == []


def test_a_date_is_stripped_before_a_site_search():
    """No headline contains "20 серпня 2026". The first live run asked
    hromadske for the whole question and got nothing back."""
    from agent.nodes import search_terms
    assert search_terms("масований удар по Києву 20 серпня 2026") == "масований удар по Києву"
    assert search_terms("удар по Києву 2026-08-20") == "удар по Києву"
    assert search_terms("обстріл Києва") == "обстріл Києва"


def test_a_real_search_endpoint_carries_the_query():
    url = build_search_url("hromadske", "удар по Києву")
    assert url.startswith("https://hromadske.ua/search?q=")
    assert " " not in url


def test_a_section_feed_ignores_the_query_by_design():
    """Suspilne has no usable search, so its Kyiv section is read instead. The
    query cannot narrow it; the headline filter does that job afterwards."""
    assert build_search_url("suspilne", "будь-що") == (
        "https://suspilne.media/kyiv/tag/obstrili/")


# --- deciding which headline is worth a model call --------------------------

@pytest.mark.parametrize("title", [
    "Масований удар по Києву: пошкоджено житловий будинок",
    "У Києві палає бізнес-центр унаслідок падіння уламків дрона",
    "У столиці внаслідок обстрілу загинули троє людей",
])
def test_a_strike_on_kyiv_is_worth_opening(title):
    assert worth_opening(title) is True


@pytest.mark.parametrize("title", [
    "День державного прапора України: у столиці підняли прапор",
    "Зеленський підписав указ про памятник Мазепі в Києві",
    "Росіяни вдарили по передмістю Харкова: є загиблі та поранені",
])
def test_everything_else_is_not(title):
    assert worth_opening(title) is False


def test_the_oblast_is_not_the_city():
    """A strike on Boryspil is a strike, and it is not in Kyiv. Geocoding would
    reject it anyway; this saves the model call."""
    assert worth_opening(
        "РФ атакувала балістикою Бориспільський район: двоє загиблих") is False


def test_headline_and_url_are_read_together():
    page = '''
- link "Масований удар по Києву: пошкоджено житловий будинок" [ref=e2]:
  - /url: https://suspilne.media/kyiv/1385126-masovanij-udar/
- link "Історичні будинки Києва" [ref=e3]:
  - /url: https://suspilne.media/kyiv/tag/istoricni-budivli/
'''
    pairs = article_candidates(page, "suspilne.media")
    assert len(pairs) == 1
    assert pairs[0][0].startswith("Масований удар")
    assert pairs[0][1].endswith("1385126-masovanij-udar/")


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


# --- is what we read actually the latest? -----------------------------------

def _site(status, level="corroborated"):
    return {
        "round": 0,
        "sites": [{"reconciliation": {"verification_level": level,
                                      "fields": {"killed": {"status": status}},
                                      "missing_for_next_level": [],
                                      "usable_groups": ["a", "b"],
                                      "has_conflict": False}}],
        "reconciliation": {"verification_level": level,
                           "fields": {"killed": {"status": status}},
                           "missing_for_next_level": [], "usable_groups": ["a", "b"],
                           "has_conflict": False},
        "location": {"district_conflict": False},
        "unopened": [("Ще одна стаття про удар по Києву", "https://x/1", "x")],
    }


def test_a_figure_still_moving_sends_the_agent_back_for_more():
    """The count changed between the articles we read. The next one we skipped
    may well change it again, and writing a stale figure is the failure mode
    this whole design exists to avoid."""
    from agent.nodes import figures_still_moving
    state = _site("superseded")
    assert figures_still_moving(state) is True
    assert should_continue(state) == "discover_sources"


def test_a_settled_figure_does_not():
    from agent.nodes import figures_still_moving
    state = _site("corroborated")
    assert figures_still_moving(state) is False
    assert should_continue(state) == "persist"


def test_nothing_left_to_read_means_nothing_left_to_do():
    """Still moving, but every relevant article has been read. Going round
    again would fetch the same pages."""
    state = _site("superseded")
    state["unopened"] = []
    assert should_continue(state) == "persist"


def test_the_round_budget_still_wins():
    state = _site("superseded")
    state["round"] = MAX_ROUNDS
    assert should_continue(state) == "persist"


# --- the prompt and the call that fills it must agree -----------------------

def test_the_prompt_is_filled_with_exactly_what_it_asks_for():
    """A placeholder renamed in extract.md but not in the code killed a live run
    after it had already fetched eight articles. The stub extractor the other
    tests use never touches the real prompt, so nothing caught it."""
    import re as _re
    from agent.nodes import PROMPT, build_prompt

    placeholders = set(_re.findall(r"\{(\w+)\}", PROMPT))
    assert placeholders == {"url", "event_date", "text"}, (
        f"extract.md asks for {placeholders}; build_prompt supplies "
        "url, event_date and text"
    )

    filled = build_prompt("https://example.com/a", "2026-06-17", "текст статті")
    assert "{" not in filled.replace("{q}", "")
    assert "2026-06-17" in filled
    assert "https://example.com/a" in filled
    assert "текст статті" in filled


def test_the_article_is_truncated_before_it_reaches_the_model():
    from agent.nodes import ARTICLE_CHARS, build_prompt
    # A character the prompt itself does not contain, so the count is the
    # article and nothing else.
    filled = build_prompt("https://example.com/a", "2026-06-17", "Ω" * 60000)
    assert filled.count("Ω") == ARTICLE_CHARS


# --- one night, not two -----------------------------------------------------

def _attack(occurred=None, published="2026-08-22T12:00:00Z", killed=1):
    from common.schemas import AttackClaim
    return AttackClaim(
        source_url="https://suspilne.media/kyiv/1", published_at=published,
        occurred_at=occurred, citywide_killed=killed,
        quote="Унаслідок вибухів у місті загинула одна людина.",
    )


@pytest.mark.parametrize("occurred,why", [
    ("2026-08-20T02:00:00Z", "the night itself"),
    ("2026-08-20T23:30:00Z", "late the same evening"),
    ("2026-08-21T05:00:00Z", "the morning after, still the same attack"),
    ("2026-08-19T23:00:00Z", "just before midnight, the attack began then"),
])
def test_a_claim_about_that_night_is_kept(occurred, why):
    from agent.nodes import belongs_to_the_night
    assert belongs_to_the_night(_attack(occurred), "2026-08-20") is True, why


@pytest.mark.parametrize("occurred", ["2026-08-22T04:00:00Z", "2026-08-15T02:00:00Z"])
def test_a_claim_about_a_different_night_is_rejected(occurred):
    """This is the check whose absence produced the worst wrong answer this
    project has made. A run for 20 August picked up an article about a smaller
    attack on the 22nd; because a later figure supersedes an earlier one,
    "15 killed" was quietly replaced by "1 killed"."""
    from agent.nodes import belongs_to_the_night
    assert belongs_to_the_night(_attack(occurred), "2026-08-20") is False


def test_without_a_time_of_the_event_publication_stands_in_for_it():
    from agent.nodes import belongs_to_the_night
    assert belongs_to_the_night(
        _attack(occurred=None, published="2026-08-20T23:00:00Z"), "2026-08-20") is True
    assert belongs_to_the_night(
        _attack(occurred=None, published="2026-08-24T09:00:00Z"), "2026-08-20") is False


def test_a_moving_figure_at_the_attack_level_also_counts():
    """The city-wide toll is the one most likely to still be moving, and an
    earlier version only looked at the per-site figures."""
    from agent.nodes import figures_still_moving
    state = {
        "sites": [],
        "reconciliation": {"fields": {}},
        "attack": {"reconciliation": {
            "fields": {"citywide_killed": {"status": "superseded"}}}},
    }
    assert figures_still_moving(state) is True
