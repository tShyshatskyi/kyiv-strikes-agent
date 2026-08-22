"""Turn a free-text Ukrainian location into a district-validated coordinate.

Design stance: this module never resolves an uncertainty by quietly guessing. It
either disambiguates on evidence, or it says plainly that it could not and hands
the decision to a person. A confidently misplaced point is a false claim about
where a strike landed; an acknowledged gap is not.

Two things about Nominatim shaped this code, both discovered from recorded
responses rather than assumed:

- `importance` is a Wikipedia-derived *fame* score, not a match-quality score.
  An ordinary residential address scores ~0.00008 while a museum at the same
  street number scores 0.38. Ranking or scoring confidence by it would put the
  museum above the building that was actually hit. It is ignored entirely.
- Multiple results usually mean "a building plus the businesses inside it",
  not "several possible places". Ambiguity is therefore measured in metres and
  districts, not in score differences.
"""

import re

from mcp_server.geo import (
    district_for_point,
    haversine_m,
    normalize_address,
    normalize_apostrophes,
)
from mcp_server.nominatim import NominatimClient

# Candidates farther apart than this, or in different districts, are genuinely
# different places and a person has to choose between them.
AMBIGUITY_RADIUS_M = 500.0

CONFIDENCE_EXACT_BUILDING = 0.90    # house number + the building itself
CONFIDENCE_BUSINESS_AT_ADDRESS = 0.65  # house number, but matched via a tenant
CONFIDENCE_STREET_ONLY = 0.30       # no house number: street or broader
CONFIDENCE_CONTEXT_RESOLVED = 0.60  # a tie broken using the reporting
CONFIDENCE_AMBIGUOUS = 0.40         # unresolved; kept for a human

_BUILDING_TYPES = {"building", "house", "residential", "place", "yes"}

# display_name fragments that carry no locality signal.
_GENERIC_TOKENS = {
    "київ", "україна", "район", "вулиця", "проспект", "бульвар",
    "провулок", "площа", "місто", "область",
}


class OutOfArea(ValueError):
    """The best match resolved to a point outside every Kyiv district."""


def _empty_result() -> dict:
    return {
        "lat": None, "lon": None, "district": None, "claimed_district": None,
        "chosen_name": None, "confidence": 0.0, "district_conflict": False,
        "matches": [], "candidates": [],
        "needs_review": False, "review_reason": None, "resolved_by": None,
    }


def _locality_tokens(display_name: str) -> set[str]:
    """Neighbourhood-ish words from a display_name.

    "5, Садова, Мінський масив, Оболонський район, Київ" yields
    {"садова", "мінський", "масив", "оболонський"} minus generic filler.
    """
    tokens: set[str] = set()
    for part in normalize_apostrophes(display_name).lower().split(","):
        for word in re.findall(r"[а-яіїєґa-z]{4,}", part):
            if word not in _GENERIC_TOKENS:
                tokens.add(word)
    return tokens


def _score(match: dict) -> tuple[int, float]:
    """Sort key: prefer the address itself over businesses standing at it."""
    address = match.get("address") or {}
    has_number = bool(address.get("house_number"))
    is_building = (match.get("addresstype") or "") in _BUILDING_TYPES
    if has_number and is_building:
        return (0, CONFIDENCE_EXACT_BUILDING)
    if has_number:
        return (1, CONFIDENCE_BUSINESS_AT_ADDRESS)
    return (2, CONFIDENCE_STREET_ONLY)


def _describe(candidate: dict) -> dict:
    return {
        "lat": candidate["lat"],
        "lon": candidate["lon"],
        "district": candidate["district"],
        "display_name": candidate["display_name"],
    }


def _is_ambiguous(candidates: list[dict]) -> bool:
    """True when the candidates describe genuinely different places."""
    if len(candidates) < 2:
        return False
    if len({c["district"] for c in candidates}) > 1:
        return True
    first = candidates[0]
    return any(
        haversine_m(first["lat"], first["lon"], c["lat"], c["lon"]) > AMBIGUITY_RADIUS_M
        for c in candidates[1:]
    )


def resolve_location(
    location_text: str,
    claimed_district: str | None,
    client: NominatimClient,
    districts: dict,
    context_districts: list[str] | None = None,
    context_text: str = "",
) -> dict:
    """Geocode a location and validate it against Kyiv district geometry.

    context_districts and context_text carry what the surrounding articles said,
    and are used only to break a tie the geocoder could not break alone.
    """
    query = normalize_address(location_text)
    raw_matches = client.search(query)

    if not raw_matches:
        return _empty_result()

    ranked = sorted(raw_matches, key=_score)

    candidates = []
    for match in ranked:
        lat, lon = float(match["lat"]), float(match["lon"])
        candidates.append({
            "lat": lat, "lon": lon,
            "district": district_for_point(lat, lon, districts),
            "display_name": match.get("display_name", ""),
            "base_confidence": _score(match)[1],
        })

    best = candidates[0]

    # A best match that is not in Kyiv at all means the caller is asking about
    # the wrong place. That is a failure, not an empty result.
    if best["district"] is None:
        raise OutOfArea(
            f"The best match for {location_text!r} is {best['display_name']!r} at "
            f"({best['lat']}, {best['lon']}), which lies outside every Kyiv "
            "district. This usually means either the article is not about Kyiv, "
            "or the wrong address was taken from its text. Check the source "
            "before recording anything."
        )

    in_kyiv = [c for c in candidates if c["district"] is not None]

    chosen = best
    confidence = best["base_confidence"]
    resolved_by: str | None = None
    needs_review = False
    review_reason: str | None = None
    reported_candidates: list[dict] = []

    if _is_ambiguous(in_kyiv):
        wanted = {normalize_apostrophes(d) for d in (context_districts or [])}
        by_district = [c for c in in_kyiv if c["district"] in wanted]

        haystack = normalize_apostrophes(context_text).lower()
        by_landmark = [
            c for c in in_kyiv
            if haystack
            and any(tok in haystack for tok in _locality_tokens(c["display_name"]))
        ]

        if len(by_district) == 1:
            chosen, resolved_by = by_district[0], "district_context"
            confidence = CONFIDENCE_CONTEXT_RESOLVED
        elif len(by_landmark) == 1:
            chosen, resolved_by = by_landmark[0], "landmark_context"
            confidence = CONFIDENCE_CONTEXT_RESOLVED
        else:
            needs_review = True
            confidence = CONFIDENCE_AMBIGUOUS
            reported_candidates = [_describe(c) for c in in_kyiv]
            names = ", ".join(sorted({c["district"] for c in in_kyiv}))
            review_reason = (
                f"The address {location_text!r} matched {len(in_kyiv)} distinct "
                f"places in Kyiv ({names}), and nothing in the reporting "
                "distinguishes them. A person needs to pick the right one."
            )

    elif confidence <= CONFIDENCE_STREET_ONLY:
        needs_review = True
        review_reason = (
            f"The reporting gave no house number for {location_text!r}, so this "
            "is a street-level match only. The point is somewhere along that "
            "street, not at a specific building."
        )

    conflict = (
        claimed_district is not None
        and normalize_apostrophes(claimed_district) != chosen["district"]
    )
    if conflict:
        needs_review = True
        conflict_note = (
            f"The reporting places this at {claimed_district}, but the "
            f"coordinates for {location_text!r} fall inside {chosen['district']}. "
            "The map district is used as the working value; a person should "
            "confirm which is right."
        )
        review_reason = (
            f"{review_reason} {conflict_note}" if review_reason else conflict_note
        )

    return {
        "lat": chosen["lat"],
        "lon": chosen["lon"],
        "district": chosen["district"],
        "claimed_district": claimed_district,
        "chosen_name": chosen["display_name"],
        "confidence": confidence,
        "district_conflict": conflict,
        "matches": [c["display_name"] for c in candidates],
        "candidates": reported_candidates,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "resolved_by": resolved_by,
    }
