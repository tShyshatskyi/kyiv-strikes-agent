"""MCP registration layer: protocol in, domain modules out, envelope back.

Deliberately thin. No domain rule is introduced here, and no LLM client is ever
imported into this process — the server is handed structured claims and stays
deterministic, so the same input always produces the same answer.

Every tool answers in one of exactly two shapes:

    {"ok": true,  "data": {...}}
    {"ok": false, "error": {"code": ..., "message": ..., "retriable": ...}}

An empty result is a success with an empty payload, never an error. That
distinction is the point: the agent must be able to tell "nothing found, keep
looking" from "something is wrong, stop".
"""

import functools
import json
import os
from datetime import date
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field, ValidationError

from common.schemas import (
    AttackClaim,
    IncidentClaim,
    IncidentRecord,
    SourceAssessment,
    SourceRef,
    err,
    ok,
)
from mcp_server.attacks_store import attack_summary
from mcp_server.attacks_store import upsert_attack as store_upsert_attack
from mcp_server.corroboration import corroborate, corroborate_attack
from mcp_server.credibility import assess_sources, load_registry
from mcp_server.db import connect
from mcp_server.geo import load_districts
from mcp_server.incidents_store import upsert_incident as store_upsert
from mcp_server.locate import OutOfArea, resolve_location
from mcp_server.nominatim import NominatimClient, RateLimited, UpstreamUnavailable

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIXTURES = ROOT / "fixtures" / "nominatim"

mcp = FastMCP("kyiv-strikes-mcp")

_districts = load_districts(DATA / "kyiv_districts.geojson")
_registry = load_registry(DATA / "source_registry.yaml")
_conn = connect(os.environ.get("KSM_DB_PATH") or "incidents.sqlite")
_geocoder = NominatimClient(
    contact=os.environ.get("NOMINATIM_CONTACT") or "unset@example.com",
    offline=os.environ.get("KSM_OFFLINE", "0") == "1",
    fixtures_dir=FIXTURES,
)


def envelope(handler):
    """Serialize the result and turn expected failures into error envelopes.

    Anything unexpected is deliberately NOT swallowed: a bug should surface as a
    protocol error rather than be disguised as a domain failure.
    """
    @functools.wraps(handler)
    def wrapper(*args, **kwargs) -> str:
        try:
            result = handler(*args, **kwargs)
        # Order matters: OutOfArea subclasses ValueError, so the specific
        # domain failures must be caught before the generic ones.
        except OutOfArea as exc:
            result = err("OUT_OF_AREA", str(exc))
        except RateLimited as exc:
            result = err("RATE_LIMITED", str(exc))
        except UpstreamUnavailable as exc:
            result = err("UPSTREAM_UNAVAILABLE", str(exc))
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            result = err("SCHEMA_VIOLATION", str(exc))
        return json.dumps(result, ensure_ascii=False, default=str)
    return wrapper


@mcp.tool()
@envelope
def assess_source_credibility(
    sources: Annotated[
        list[SourceRef],
        Field(description="Candidate articles: url, domain, published_at, and any "
                          "primary-source domains each one cites."),
    ],
    event_date: Annotated[
        str, Field(description="Date of the strike, as YYYY-MM-DD."),
    ],
    relevance_window_days: Annotated[
        int, Field(default=14, ge=1, le=365,
                   description="How many days after the event an article may still "
                               "be counted. Widen it to admit later summaries."),
    ] = 14,
) -> dict:
    """Rate candidate articles by source tier and, above all, by independence.

    Call this BEFORE corroborating claims. Five outlets reporting a strike
    usually means one press release and four reprints; counting them as five
    confirmations is the easiest way to produce a confidently wrong record. An
    article that merely relays another source is grouped with that source. The
    exception: an article that also reports a fact the original never gave got
    it somewhere of its own, and counts as a separate voice.

    Returns per article: tier (1 official, 2 newsroom, 3 aggregator),
    independence_group, whether it may be used as corroboration, anything it
    added, and the reason for the verdict. Side effects: none.
    """
    parsed = date.fromisoformat(event_date)
    assessments = assess_sources(
        sources, _registry, parsed, relevance_window_days=relevance_window_days
    )
    return ok({"assessments": assessments})


@mcp.tool()
@envelope
def resolve_strike_location(
    location_text: Annotated[
        str,
        Field(min_length=3, max_length=200,
              description="The location as the article writes it, for example "
                          "\"вул. Освіти, 4\" or \"просп. Перемоги 37\"."),
    ],
    claimed_district: Annotated[
        str | None,
        Field(default=None,
              description="The district the article asserts, if it names one. Used "
                          "to detect a disagreement, never to override geometry."),
    ] = None,
    context_districts: Annotated[
        list[str] | None,
        Field(default=None,
              description="Districts named anywhere in the reporting. Used only to "
                          "break a tie between several matching places."),
    ] = None,
    context_text: Annotated[
        str,
        Field(default="",
              description="Quotes from the reporting. Used only to match a "
                          "neighbourhood name against a candidate location."),
    ] = "",
) -> dict:
    """Geocode a Kyiv location and check it against real district boundaries.

    Returns coordinates, the district the point actually falls inside, and a
    confidence reflecting how precisely the address matched — an exact building
    scores higher than a street with no house number.

    Three outcomes must not be confused:
      - matched cleanly: needs_review is false, use it;
      - matched nothing: a SUCCESS with matches: [] — keep looking elsewhere;
      - matched a place outside Kyiv: an OUT_OF_AREA error — the article is
        probably not about Kyiv, or the wrong address was taken from it.

    Where the article's district disagrees with the geometry, or several
    distinct places match and the reporting cannot separate them, the tool sets
    needs_review with a plain-language reason instead of guessing.
    Side effects: none (a network call to Nominatim unless running offline).
    """
    data = resolve_location(
        location_text, claimed_district, _geocoder, _districts,
        context_districts=context_districts, context_text=context_text,
    )
    return ok(data)


@mcp.tool()
@envelope
def upsert_attack(
    claims: Annotated[
        list[AttackClaim],
        Field(description="What each article said about the attack as a whole: "
                          "city-wide casualties, and what was launched."),
    ],
    assessments: Annotated[
        list[SourceAssessment],
        Field(description="Their credibility verdicts, from "
                          "assess_source_credibility."),
    ],
) -> dict:
    """Record one night of shelling, and check it against the impacts under it.

    City-wide casualty figures belong to the attack, not to any address: put
    "five killed in Kyiv" on each of three impact rows and any sum over
    districts or over a month returns fifteen. This tool is where they live.

    It reconciles the city-wide figures across sources itself, using the same
    rules as for an impact site — a later higher toll supersedes rather than
    contradicts, and disagreement at the same time is a real conflict.

    Its distinct job, which no per-impact tool can do, is the cross-level check:
    the deaths attributed to individual addresses are summed and compared with
    the city-wide total. If the addresses add up to more than the city reported,
    a city-wide number was probably mistaken for a local one, and the attack is
    flagged for a person rather than quietly stored.

    Reports of one night arrive across the following day, so anything within 12
    hours of an existing attack merges into it.

    SIDE EFFECTS: writes to the attacks, attack_claims, and sources tables.
    """
    if not claims:
        return err("SCHEMA_VIOLATION", "At least one attack claim is required.")

    verdicts = [json.loads(a.model_dump_json()) for a in assessments]
    reconciliation = corroborate_attack(claims, verdicts)

    first = claims[0]
    record = {
        "occurred_at": (first.occurred_at or first.published_at).isoformat(),
        "citywide_killed": reconciliation["fields"].get("citywide_killed", {}).get("value"),
        "citywide_injured": reconciliation["fields"].get("citywide_injured", {}).get("value"),
        "weapons": reconciliation["weapons"],
        "verification_level": reconciliation["verification_level"],
        "needs_review": reconciliation["needs_review"],
        "review_reason": reconciliation["review_reason"],
    }
    written = store_upsert_attack(_conn, record, claims, verdicts, reconciliation)
    return ok(written | {"reconciliation": reconciliation})


@mcp.tool()
@envelope
def corroborate_incident_claims(
    claims: Annotated[
        list[IncidentClaim],
        Field(description="One structured claim set per article."),
    ],
    assessments: Annotated[
        list[SourceAssessment],
        Field(description="The output of assess_source_credibility for those "
                          "same articles."),
    ],
) -> dict:
    """Reconcile what several articles claim into one verified set of values.

    Articles sharing an independence_group are collapsed into one voice. A
    casualty count that rises over time is treated as being completed, not
    contradicted — the later figure supersedes. A genuine contradiction is
    disagreement at the same time, when neither source could know more; that is
    flagged for a human rather than silently averaged away.

    Returns per-field value and status, an overall verification_level, and
    missing_for_next_level — an explicit list of what is still absent. Act on
    that list: it tells you what to search for next. Side effects: none.
    """
    parsed = [IncidentClaim.model_validate(c) for c in claims]
    verdicts = [json.loads(a.model_dump_json()) for a in assessments]
    return ok(corroborate(parsed, verdicts))


@mcp.tool()
@envelope
def upsert_incident(
    incident: Annotated[
        IncidentRecord,
        Field(description="The reconciled incident to write. Coordinates must "
                          "fall inside Kyiv and the district must be one of the "
                          "ten real ones."),
    ],
    claims: Annotated[list[IncidentClaim], Field(description="Supporting claims.")],
    assessments: Annotated[
        list[SourceAssessment],
        Field(description="Their credibility verdicts, from assess_source_credibility."),
    ],
    reconciliation: Annotated[dict, Field(description="Output of corroborate_incident_claims.")],
) -> dict:
    """Write a verified incident to the registry, merging rather than duplicating.

    Matches an existing incident within 150 m and 6 hours and merges into it, so
    one event reported five ways stays one row. Refuses to overwrite a record
    that already rests on stronger evidence. Stores one provenance row per
    (incident, source, field) carrying the sentence that supports the value, so
    any number can be traced back to the article it came from.

    SIDE EFFECTS: this is the only tool that writes. It modifies the incidents,
    incident_claims, incident_media, and sources tables. An incident written
    with needs_review stays out of the verified map export until a person signs
    off on it.
    """
    parsed = [IncidentClaim.model_validate(c) for c in claims]
    verdicts = [json.loads(a.model_dump_json()) for a in assessments]
    record = json.loads(incident.model_dump_json())
    result = store_upsert(_conn, record, parsed, verdicts, reconciliation)
    if result["action"] == "rejected_lower_confidence":
        return err(
            "DUPLICATE_REJECTED",
            f"Incident {result['incident_id']} already rests on stronger evidence; "
            "refusing to overwrite it with a weaker record.",
        )
    return ok(result)
