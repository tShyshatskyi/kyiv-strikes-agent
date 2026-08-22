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
    killed: int | None = Field(default=None, ge=0, le=1000)
    injured: int | None = Field(default=None, ge=0, le=5000)
    damage: list[DamageKind] = Field(default_factory=list)
    media_urls: list[HttpUrl] = Field(default_factory=list)
    quote: str = Field(min_length=10, max_length=1000)


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
