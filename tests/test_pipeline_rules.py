"""The v2 rules that decide what the site is allowed to claim."""

import pytest

from pipeline import tools, wikipedia


def src(channel, posted, quote):
    return {"channel": channel, "posted": posted, "quote": quote}


# --- confirmation ------------------------------------------------------------

def test_city_hall_three_channels_are_one_voice():
    """The administration, the mayor and the city state administration publish
    the same briefing, so three of them are not two independent sources."""
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-11-07 09:01", "У Подільському районі зафіксовано влучання"),
        src("vitaliy_klitschko", "2026-11-07 09:05", "У Подільському районі влучання"),
        src("KyivCityOfficial", "2026-11-07 09:20", "У Подільському районі влучання"),
    ])
    assert conf["voices"] == ["city"]
    assert conf["level"] == "official"


def test_emergency_service_is_a_separate_voice():
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-11-07 09:01", "Зафіксовано влучання в житловий будинок"),
        src("dsns_kyiv", "2026-11-07 10:00", "Рятувальники загасили пожежу"),
    ])
    assert conf["voices"] == ["city", "emergency"]
    assert conf["level"] == "confirmed"


def test_media_retelling_the_briefing_does_not_confirm_it():
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-11-07 09:01", "У Подільському районі зафіксовано влучання в будинок"),
        src("suspilne_kyiv", "2026-11-07 09:40", "У Подільському районі зафіксовано влучання в будинок"),
    ])
    assert conf["level"] == "official"
    assert conf["retold_voices"] == ["suspilne"]


def test_media_with_its_own_detail_does_confirm():
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-11-07 09:01", "У Подільському районі зафіксовано влучання в будинок"),
        src("suspilne_kyiv", "2026-11-07 11:30",
            "Влучання сталося на вулиці Правди, 31, вибито 40 вікон"),
    ])
    assert conf["level"] == "confirmed"
    assert conf["voices"] == ["city", "suspilne"]


def test_media_detail_must_be_new_not_just_repeated():
    """A newsroom quoting the address the administration already gave has added
    nothing, even though its wording differs."""
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-11-07 09:01", "Влучання на вулиці Правди, 31"),
        src("suspilne_kyiv", "2026-11-07 11:30", "Влучання на вулиці Правди, 31"),
    ])
    assert conf["level"] == "official"


def test_media_only_place_is_not_called_confirmed():
    conf = tools.confirmation([
        src("suspilne_kyiv", "2026-11-07 11:30", "Горить склад на вулиці Богатирській"),
    ])
    assert conf["level"] == "media"


def test_aggregators_never_count():
    conf = tools.confirmation([
        src("truexakyiv", "2026-11-07 02:00", "Вибухи в Києві, працює ППО"),
        src("kievreal1", "2026-11-07 02:05", "Вибухи в Києві"),
    ])
    assert conf["level"] == "aggregator_only"
    assert conf["voices"] == []


def test_an_open_auditor_complaint_makes_a_place_disputed():
    """A place the auditor still calls a duplicate must not be counted as
    confirmed, however many channels reported it."""
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-11-07 09:01", "Влучання в будинок"),
        src("dsns_kyiv", "2026-11-07 10:00", "Рятувальники загасили пожежу"),
    ], disputed=True)
    assert conf["level"] == "disputed"


def test_english_quote_does_not_add_detail_by_being_in_english():
    """'Kyiv', 'Mayor', 'Klitschko' are new only because the briefing is in
    Ukrainian. Until quotes are translated, an English quote counts only its numbers."""
    conf = tools.confirmation([
        src("vitaliy_klitschko", "2026-10-13 07:10", "У Солом'янському районі пошкоджено міст"),
        src("kyivpost_official", "2026-10-13 09:00",
            "Kyiv Mayor Vitali Klitschko said a Russian strike damaged a bridge in Solomianskyi district"),
    ])
    assert conf["level"] == "official"


def test_english_quote_with_its_own_figure_still_counts():
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-10-13 07:10", "У Солом'янському районі пошкоджено будинок"),
        src("kyivindependent_official", "2026-10-13 09:00",
            "A Russian strike damaged a building, injuring 7 people"),
    ])
    assert conf["level"] == "confirmed"


@pytest.mark.parametrize("quote", [
    "On Thursday, October 8, Russia attacked Kyiv",     # day of month
    "Explosions were heard in Kyiv at 03:40",            # clock time
    "Kyiv came under attack overnight on Oct. 7",       # the night before
    "Russia struck Kyiv in 2026 again",                  # year
])
def test_date_and_time_numbers_are_not_detail(quote):
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-10-08 07:10", "У Дніпровському районі пошкоджено будинок"),
        src("kyivpost_official", "2026-10-08 09:00", quote),
    ])
    assert conf["level"] == "official"


def test_a_district_in_another_case_is_not_new():
    """The official said 'Солом'янський' at the start of a sentence; the
    newsroom says 'у Солом'янському районі'. Same district, nothing new."""
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-10-07 07:10", "Солом'янський район: пошкоджено будинок"),
        src("suspilnenews", "2026-10-07 09:00", "У Солом'янському районі пошкоджено будинок"),
    ])
    assert conf["level"] == "official"


def test_ukrainian_media_with_a_date_only_does_not_confirm():
    conf = tools.confirmation([
        src("VA_Kyiv", "2026-10-21 07:10", "У Дніпровському районі пошкоджено будинок"),
        src("suspilne_kyiv", "2026-10-21 09:00", "У ніч на 21 жовтня пошкоджено будинок"),
    ])
    assert conf["level"] == "official"


@pytest.mark.parametrize("kinds, disputed", [
    (["wrong_merge"], True), (["not_this_attack"], True), (["not_kyiv_city"], True),
    (["missed_merge"], False), (["wrong_field"], False), (["unsupported_summary"], False),
    ([], False),
])
def test_only_complaints_about_the_place_itself_make_it_disputed(kinds, disputed):
    """A wrong weapon or two car fires that may be one leave no doubt that the
    strike happened, so they must not read as 'disputed'."""
    assert tools.is_disputed(kinds) is disputed


# --- the map -----------------------------------------------------------------

def test_district_only_reports_all_land_on_one_point():
    """Spreading them would draw addresses no source gave."""
    a = tools.district_point("Подільський")
    b = tools.district_point("Подільський")
    assert a == b
    assert tools.district_of(*a) == "Подільський"


# --- Wikipedia ---------------------------------------------------------------

def test_the_compared_dates_do_not_depend_on_the_registry():
    assert wikipedia.window("2026-11-16") == ["2026-11-15", "2026-11-16"]


def test_a_named_bridge_is_not_satisfied_by_burning_cars():
    places = [{"title": "Two cars burned out in a yard", "target": "vehicle"}]
    assert "a bridge" in wikipedia.named_objects("влучання в Північний та Південний мости")
    assert not wikipedia.has_object("a bridge", places)


def test_a_named_bridge_is_satisfied_by_a_bridge():
    places = [{"title": "Ballistic missile hit the Northern Bridge", "target": "infrastructure"}]
    assert wikipedia.has_object("a bridge", places)


def test_residential_buildings_are_not_used_as_evidence():
    """Nearly every entry mentions them, so they would make any night 'agree'."""
    assert wikipedia.named_objects("пошкоджена житлова забудова") == []


@pytest.mark.parametrize("text, killed, injured", [
    ("07.11.2026, Київ. Засіб: балістична ракета. жертви: 0/6", 0, 6),
    ("20 November 2026: Russian strikes damaged three bridges, killing at least nine people.", 9, None),
    ("4 October 2026: Russian forces struck the bridge, injuring two people.", None, 2),
])
def test_figures_are_read_as_stated(text, killed, injured):
    assert wikipedia.figures(text) == (killed, injured)


def test_a_night_with_no_places_is_reported_missing_not_hidden():
    days = [{"day": "2026-10-29", "stats": {"posts": 1}, "city": {}},
            {"day": "2026-10-30", "stats": {"posts": 1}, "city": {}}]
    out = wikipedia.crosscheck(days, [])
    assert out["dates"]["2026-10-30"]["verdict"] == "missing"


def test_no_figure_is_kept_apart_from_a_real_match():
    """Both sides silent is not agreement, and must not be counted as one."""
    days = [{"day": "2026-10-11", "stats": {"posts": 1}, "city": {}},
            {"day": "2026-10-12", "stats": {"posts": 1}, "city": {}}]
    sites = [{"day": "2026-10-12", "title": "Warehouse building damaged", "target": "warehouse"}]
    out = wikipedia.crosscheck(days, sites)
    assert out["dates"]["2026-10-12"]["verdict"] == "no_figure"
    assert out["summary"]["match"] == 0
