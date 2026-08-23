"""Types that cross the MCP boundary. Imported by both the agent and the server."""

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, HttpUrl

KyivDistrict = Literal[
    "Голосіївський", "Дарницький", "Деснянський", "Дніпровський",
    "Оболонський", "Печерський", "Подільський", "Святошинський",
    "Солом'янський", "Шевченківський",
]

DamageKind = Literal["fire", "collapse", "facade", "crater", "vehicles"]

StructureType = Literal[
    "residential", "school", "hospital", "energy", "transport", "other"
]

VerificationLevel = Literal[
    "unverified", "single_source", "corroborated", "well_documented"
]

ClaimStatus = Literal["corroborated", "single_source", "conflicting", "superseded"]

ErrorCode = Literal[
    "OUT_OF_AREA",
    "UPSTREAM_UNAVAILABLE",
    "RATE_LIMITED",
    "SCHEMA_VIOLATION",
    "DUPLICATE_REJECTED",
]

RETRIABLE_CODES = frozenset({"UPSTREAM_UNAVAILABLE", "RATE_LIMITED"})


class IncidentClaim(BaseModel):
    """One article's claims about one impact location.

    Produced by the agent's extraction node, consumed by the custom MCP server.
    The server never sees the article text itself — only this.
    """

    source_url: HttpUrl
    published_at: datetime
    occurred_at: datetime | None = None
    location_text: str = Field(min_length=3, max_length=200)
    claimed_district: KyivDistrict | None = None
    structure_type: StructureType
    # Casualties AT THIS ADDRESS, and only when the article attributes them
    # here. A city-wide total belongs on the AttackClaim; when an article does
    # not say which it means, both stay null rather than being guessed.
    killed: int | None = Field(default=None, ge=0, le=1000)
    injured: int | None = Field(default=None, ge=0, le=5000)
    damage: list[DamageKind] = Field(default_factory=list)
    media_urls: list[HttpUrl] = Field(default_factory=list)
    quote: str = Field(min_length=10, max_length=1000)


WeaponKind = Literal[
    "shahed", "cruise_missile", "ballistic_missile", "guided_bomb", "other"
]


class WeaponTally(BaseModel):
    """How many of one kind of weapon were launched and how many were downed."""

    kind: WeaponKind
    launched: int | None = Field(default=None, ge=0, le=5000)
    intercepted: int | None = Field(default=None, ge=0, le=5000)


class AttackClaim(BaseModel):
    """What one article says about the attack as a whole, not about one address.

    Casualty figures in Ukrainian reporting are usually city-wide: "у Києві
    загинули пʼятеро". Storing that on each impact site would multiply it — three
    sites would sum to fifteen deaths. It belongs to the night, once.
    """

    source_url: HttpUrl
    published_at: datetime
    occurred_at: datetime | None = None
    citywide_killed: int | None = Field(default=None, ge=0, le=5000)
    citywide_injured: int | None = Field(default=None, ge=0, le=20000)
    weapons: list[WeaponTally] = Field(default_factory=list)
    districts_affected: list[KyivDistrict] = Field(default_factory=list)
    quote: str = Field(min_length=10, max_length=1000)


class ArticleExtraction(BaseModel):
    """Everything one article yields: the attack it describes, and each impact."""

    attack: AttackClaim | None = None
    incidents: list[IncidentClaim] = Field(default_factory=list)


class AttackRecord(BaseModel):
    """A reconciled attack, ready to be written."""

    occurred_at: datetime
    citywide_killed: int | None = Field(default=None, ge=0, le=5000)
    citywide_injured: int | None = Field(default=None, ge=0, le=20000)
    weapons: list[WeaponTally] = Field(default_factory=list)
    verification_level: VerificationLevel
    needs_review: bool = False
    review_reason: str | None = None


class SourceAssessment(BaseModel):
    """One article's credibility verdict, as produced by assess_source_credibility."""

    url: HttpUrl
    tier: int | None = Field(default=None, ge=1, le=3)
    voice_tier: int | None = Field(default=None, ge=1, le=3)
    independence_group: str | None = None
    usable_as_corroborator: bool
    adds: list[str] = Field(default_factory=list)
    reason: str = ""


class IncidentRecord(BaseModel):
    """A reconciled incident, ready to be written to the registry.

    Constrained rather than a free-form dict on purpose: this is the payload of
    the only tool with persistent side effects, so the shape of what gets
    written is part of the contract.
    """

    occurred_at: datetime
    lat: float = Field(ge=50.20, le=50.60, description="Latitude, inside Kyiv.")
    lon: float = Field(ge=30.15, le=30.85, description="Longitude, inside Kyiv.")
    district: KyivDistrict
    claimed_district: KyivDistrict | None = None
    address_text: str | None = Field(default=None, max_length=200)
    structure_type: StructureType
    killed: int | None = Field(default=None, ge=0, le=1000)
    injured: int | None = Field(default=None, ge=0, le=5000)
    damage: list[DamageKind] = Field(default_factory=list)
    verification_level: VerificationLevel
    geocode_confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool = False
    review_reason: str | None = None
    attack_id: int | None = Field(default=None, description="The attack this impact belongs to.")


class SourceRef(BaseModel):
    """A candidate article as handed to the credibility assessor."""

    url: HttpUrl
    domain: str
    published_at: datetime
    byline: str | None = None
    cited_sources: list[str] = Field(default_factory=list)


def ok(data: Any) -> dict:
    """Wrap a successful result. An empty payload is still a success."""
    return {"ok": True, "data": data}


def err(code: str, message: str, retriable: bool | None = None) -> dict:
    """Wrap a failure. Raises ValueError on an undeclared error code."""
    if code not in get_args(ErrorCode):
        raise ValueError(f"Undeclared error code: {code!r}")
    if retriable is None:
        retriable = code in RETRIABLE_CODES
    return {"ok": False, "error": {"code": code, "message": message, "retriable": retriable}}
