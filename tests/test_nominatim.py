import json
from pathlib import Path

import pytest

from mcp_server.nominatim import NominatimClient, fixture_key


@pytest.fixture
def fixtures(tmp_path: Path) -> Path:
    d = tmp_path / "nominatim"
    d.mkdir()
    query = "вулиця освіти 4 київ"
    payload = [
        {
            "lat": "50.4310000",
            "lon": "30.4400000",
            "display_name": "4, вулиця Освіти, Солом'янський район, Київ",
            "importance": 0.42,
        }
    ]
    (d / f"{fixture_key(query)}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return d


def test_offline_returns_recorded_fixture(fixtures):
    client = NominatimClient(contact="test@example.com", offline=True, fixtures_dir=fixtures)
    results = client.search("вулиця освіти 4 київ")
    assert len(results) == 1
    assert results[0]["lat"] == "50.4310000"


def test_offline_missing_fixture_returns_empty_not_error(fixtures):
    client = NominatimClient(contact="test@example.com", offline=True, fixtures_dir=fixtures)
    assert client.search("вулиця неіснуюча 999 київ") == []


def test_fixture_key_is_stable():
    assert fixture_key("вулиця освіти 4") == fixture_key("вулиця освіти 4")
    assert fixture_key("вулиця освіти 4") != fixture_key("вулиця освіти 5")


def test_rate_limiter_spaces_calls(fixtures, monkeypatch):
    client = NominatimClient(
        contact="test@example.com",
        offline=True,
        fixtures_dir=fixtures,
        min_interval_s=0.25,
    )
    slept: list[float] = []
    monkeypatch.setattr("mcp_server.nominatim.time.sleep", lambda s: slept.append(s))
    monkeypatch.setattr("mcp_server.nominatim.time.monotonic", lambda: 0.0)

    client.search("вулиця освіти 4 київ")
    client.search("вулиця освіти 4 київ")

    assert slept and slept[0] == pytest.approx(0.25, abs=0.01)


def test_contact_is_required():
    with pytest.raises(ValueError):
        NominatimClient(contact="", offline=True, fixtures_dir=Path("."))
