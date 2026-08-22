import pytest
from pydantic import ValidationError

from common.schemas import IncidentClaim, ok, err


def _valid_claim(**overrides):
    base = dict(
        source_url="https://suspilne.media/kyiv/123456",
        published_at="2026-06-17T09:30:00Z",
        occurred_at="2026-06-17T02:10:00Z",
        location_text="вул. Освіти, 4",
        claimed_district="Солом'янський",
        structure_type="residential",
        killed=3,
        injured=12,
        damage=["fire", "collapse"],
        media_urls=[],
        quote="Внаслідок влучання зруйновано під'їзд житлового будинку на вул. Освіти, 4.",
    )
    base.update(overrides)
    return base


def test_valid_claim_parses():
    claim = IncidentClaim(**_valid_claim())
    assert claim.killed == 3
    assert "fire" in claim.damage


def test_unknown_district_rejected():
    with pytest.raises(ValidationError):
        IncidentClaim(**_valid_claim(claimed_district="Одеський"))


def test_negative_casualties_rejected():
    with pytest.raises(ValidationError):
        IncidentClaim(**_valid_claim(killed=-1))


def test_quote_is_required():
    payload = _valid_claim()
    del payload["quote"]
    with pytest.raises(ValidationError):
        IncidentClaim(**payload)


def test_ok_envelope():
    assert ok({"matches": []}) == {"ok": True, "data": {"matches": []}}


def test_err_envelope():
    result = err("OUT_OF_AREA", "Point lies outside Kyiv.")
    assert result["ok"] is False
    assert result["error"]["code"] == "OUT_OF_AREA"
    assert result["error"]["retriable"] is False


def test_err_rejects_unknown_code():
    with pytest.raises(ValueError):
        err("BANANA", "nope")
