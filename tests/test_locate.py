import json
from pathlib import Path

import pytest

from mcp_server.geo import load_districts, normalize_address
from mcp_server.locate import OutOfArea, resolve_location
from mcp_server.nominatim import NominatimClient, fixture_key

DISTRICTS = load_districts("data/kyiv_districts.geojson")

# Points chosen deep inside their districts, far from any boundary.
IN_HOLOSIIV = ("50.3838", "30.4780")
IN_HOLOSIIV_NEARBY = ("50.3840", "30.4784")   # ~35 m from the above
IN_OBOLON = ("50.5100", "30.4980")            # ~14 km away, another district
IN_ODESA = ("46.4825", "30.7233")


def _match(lat, lon, name, addresstype="building", house="4", road="вулиця Тестова"):
    """Shaped like a genuine Nominatim jsonv2 result."""
    return {
        "lat": lat, "lon": lon,
        "display_name": name,
        "addresstype": addresstype,
        "category": addresstype,
        "place_rank": 30,
        "importance": 8.2587e-05,     # real value for an ordinary address
        "address": {"house_number": house, "road": road},
    }


def _write(dirpath: Path, raw_query: str, payload: list[dict]) -> None:
    key = fixture_key(normalize_address(raw_query))
    (dirpath / f"{key}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def client(tmp_path: Path) -> NominatimClient:
    d = tmp_path / "nominatim"
    d.mkdir()

    _write(d, "вул. Тестова, 1", [
        _match(*IN_HOLOSIIV, "1, вулиця Тестова, Теремки, Голосіївський район, Київ"),
    ])

    # Two matches 14 km apart in different districts — genuinely ambiguous.
    _write(d, "вул. Садова, 5", [
        _match(*IN_HOLOSIIV, "5, Садова, Теремки, Голосіївський район, Київ"),
        _match(*IN_OBOLON, "5, Садова, Мінський масив, Оболонський район, Київ"),
    ])

    # Three matches at one campus: a building plus two businesses in it.
    # Same district, 35 m apart — one place, not an ambiguity.
    _write(d, "просп. Перемоги, 37", [
        _match(*IN_HOLOSIIV, "Корпус №1, 37, Берестейський проспект, Київ",
               addresstype="building", house="37"),
        _match(*IN_HOLOSIIV_NEARBY, "Університет фінансів, 37, Берестейський проспект",
               addresstype="amenity", house="37"),
    ])

    # A business ranked above the plain address at the same location.
    _write(d, "вул. Бізнесова, 9", [
        _match(*IN_HOLOSIIV_NEARBY, "Спортклуб, 9, вулиця Бізнесова, Київ",
               addresstype="leisure", house="9"),
        _match(*IN_HOLOSIIV, "9, вулиця Бізнесова, Теремки, Київ",
               addresstype="building", house="9"),
    ])

    # Street-level match only: no house number.
    _write(d, "вулиця Безномерна", [
        _match(*IN_HOLOSIIV, "вулиця Безномерна, Голосіївський район, Київ",
               addresstype="road", house=None),
    ])

    _write(d, "вул. Дерибасівська, 1, Одеса", [
        _match(*IN_ODESA, "1, Дерибасівська вулиця, Центр, Одеса", house="1"),
    ])

    return NominatimClient(
        contact="test@example.com", offline=True, fixtures_dir=d, min_interval_s=0.0
    )


# --- the ordinary, everything-agrees case -----------------------------------

def test_clean_match_needs_no_review(client):
    r = resolve_location("вул. Тестова, 1", "Голосіївський", client, DISTRICTS)
    assert r["district"] == "Голосіївський"
    assert r["district_conflict"] is False
    assert r["needs_review"] is False
    assert r["review_reason"] is None


def test_exact_building_match_is_high_confidence(client):
    r = resolve_location("вул. Тестова, 1", None, client, DISTRICTS)
    assert r["confidence"] >= 0.85


def test_street_level_match_without_a_house_number_is_low_confidence(client):
    r = resolve_location("вулиця Безномерна", None, client, DISTRICTS)
    assert r["confidence"] <= 0.35
    assert r["needs_review"] is True


# --- ranking: the address, not the business standing at it ------------------

def test_plain_building_is_preferred_over_a_business_at_the_same_address(client):
    r = resolve_location("вул. Бізнесова, 9", None, client, DISTRICTS)
    assert "Спортклуб" not in r["chosen_name"]
    assert r["chosen_name"].startswith("9, вулиця Бізнесова")


# --- ambiguity is spatial, not a matter of ranking scores -------------------

def test_several_matches_at_one_site_are_not_treated_as_ambiguous(client):
    """Nominatim returns a building and the institutions inside it. They are
    metres apart in one district — that is one place, not a choice."""
    r = resolve_location("просп. Перемоги, 37", None, client, DISTRICTS)
    assert r["needs_review"] is False
    assert r["candidates"] == []


def test_matches_in_different_districts_are_ambiguous(client):
    r = resolve_location("вул. Садова, 5", None, client, DISTRICTS)
    assert len(r["candidates"]) == 2
    assert r["needs_review"] is True
    assert r["resolved_by"] is None


# --- disambiguation from what the articles said -----------------------------

def test_ambiguity_resolved_by_district_mentioned_in_articles(client):
    r = resolve_location(
        "вул. Садова, 5", None, client, DISTRICTS,
        context_districts=["Оболонський"],
    )
    assert r["district"] == "Оболонський"
    assert r["resolved_by"] == "district_context"
    assert r["needs_review"] is False


def test_ambiguity_resolved_by_landmark_named_in_the_article_text(client):
    r = resolve_location(
        "вул. Садова, 5", None, client, DISTRICTS,
        context_text="Пожежа сталася у Мінському масиві, поруч зі школою.",
    )
    assert r["district"] == "Оболонський"
    assert r["resolved_by"] == "landmark_context"
    assert r["needs_review"] is False


def test_context_that_matches_nothing_does_not_force_a_choice(client):
    r = resolve_location(
        "вул. Садова, 5", None, client, DISTRICTS,
        context_districts=["Печерський"],
    )
    assert r["needs_review"] is True
    assert r["resolved_by"] is None


# --- district disagreement: recorded, flagged, never silently resolved -------

def test_district_conflict_keeps_both_and_flags_for_review(client):
    r = resolve_location("вул. Тестова, 1", "Оболонський", client, DISTRICTS)
    assert r["district"] == "Голосіївський"          # working value for the map
    assert r["claimed_district"] == "Оболонський"    # what the article said, kept
    assert r["district_conflict"] is True
    assert r["needs_review"] is True
    assert "Оболонський" in r["review_reason"]
    assert "Голосіївський" in r["review_reason"]


# --- the two outcomes that must never look alike ----------------------------

def test_address_not_found_is_an_ordinary_empty_result(client):
    r = resolve_location("вул. Невідома, 999", None, client, DISTRICTS)
    assert r["matches"] == []
    assert r["lat"] is None
    assert r["needs_review"] is False


def test_address_in_another_city_is_an_error_that_explains_itself(client):
    with pytest.raises(OutOfArea) as exc:
        resolve_location("вул. Дерибасівська, 1, Одеса", None, client, DISTRICTS)
    message = str(exc.value)
    assert "Одеса" in message
    assert "wrong address" in message.lower() or "not about kyiv" in message.lower()
