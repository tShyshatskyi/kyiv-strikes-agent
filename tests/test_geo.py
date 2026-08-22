import math

import pytest

from mcp_server.geo import (
    load_districts,
    district_for_point,
    normalize_address,
    haversine_m,
)

DISTRICTS = load_districts("data/kyiv_districts.geojson")

# Deep inside Голосіївський, far from any boundary (ВДНГ grounds).
HOLOSIIV = (50.3838, 30.4780)
# Deep inside Оболонський (Obolon residential area).
OBOLON = (50.5100, 30.4980)
# Odesa city centre — outside Kyiv entirely.
ODESA = (46.4825, 30.7233)


def test_all_ten_districts_load():
    assert len(DISTRICTS) == 10
    assert "Голосіївський" in DISTRICTS
    assert "Солом'янський" in DISTRICTS


def test_point_inside_holosiivskyi():
    assert district_for_point(*HOLOSIIV, DISTRICTS) == "Голосіївський"


def test_point_inside_obolonskyi():
    assert district_for_point(*OBOLON, DISTRICTS) == "Оболонський"


def test_point_outside_kyiv_returns_none():
    assert district_for_point(*ODESA, DISTRICTS) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("вул. Освіти, 4", "вулиця освіти 4"),
        ("ВУЛ.  ОСВІТИ 4", "вулиця освіти 4"),
        ("просп. Перемоги, буд. 37", "проспект перемоги 37"),
        ("пр-т Науки 10", "проспект науки 10"),
        ("Солом'янський р-н, вул. Освіти 4", "солом'янський район вулиця освіти 4"),
    ],
)
def test_normalize_address(raw, expected):
    assert normalize_address(raw) == expected


def test_normalize_address_unifies_apostrophes():
    """OSM and news sites disagree on which apostrophe to use in Солом'янський."""
    assert normalize_address("Солом’янський") == normalize_address("Солом'янський")


def test_haversine_known_distance():
    # Roughly 1 km apart along a meridian.
    d = haversine_m(50.4500, 30.5230, 50.4590, 30.5230)
    assert math.isclose(d, 1000, rel_tol=0.02)


def test_haversine_zero():
    assert haversine_m(50.45, 30.52, 50.45, 30.52) == pytest.approx(0.0, abs=1e-6)
