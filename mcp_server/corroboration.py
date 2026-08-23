"""Reconcile claims from several articles into per-field verified values.

Two ideas do most of the work here.

**A casualty count is not a static fact.** It is completed over days as people
are recovered. So two sources giving different numbers are usually not
contradicting each other — the later one simply knows more. Treating that as a
conflict would flag almost every real incident and make the flag meaningless.
A real contradiction is two sources reporting different numbers *at the same
time*, when neither could know more than the other.

**`missing_for_next_level` is what makes this agentic.** It does not merely
score the evidence; it names what is still absent, and the agent uses that to
decide what to search for next.
"""

from datetime import timedelta

from common.schemas import IncidentClaim

# Reports published within this window of one another describe the same moment,
# so a numeric disagreement between them is a genuine contradiction rather than
# an update.
SIMULTANEITY_WINDOW = timedelta(hours=2)

# Fields whose value is expected to grow as an event is worked through.
COUNT_FIELDS = ("killed", "injured")

# Fields that describe what a thing *is*; these do not legitimately change.
CATEGORICAL_FIELDS = ("structure_type",)

_LEVELS_NEEDING_NOTHING = frozenset({"well_documented"})


def _usable_pairs(claims: list[IncidentClaim], assessments: list[dict]):
    by_url = {a["url"]: a for a in assessments}
    pairs = []
    for claim in claims:
        assessment = by_url.get(str(claim.source_url))
        if assessment and assessment.get("usable_as_corroborator"):
            pairs.append((claim, assessment))
    return pairs


def _reconcile_count(field: str, pairs) -> tuple[dict, str | None] | None:
    """Reconcile a field whose true value grows over time."""
    entries = [
        (claim.published_at, getattr(claim, field), assessment["independence_group"],
         str(claim.source_url))
        for claim, assessment in pairs
        if getattr(claim, field) is not None
    ]
    if not entries:
        return None

    entries.sort(key=lambda e: e[0])
    latest_at, latest_value, _, _ = entries[-1]
    groups = {e[2] for e in entries}
    distinct = {e[1] for e in entries}
    sources = [e[3] for e in entries]

    simultaneous = {e[1] for e in entries if abs(e[0] - latest_at) <= SIMULTANEITY_WINDOW}

    if len(simultaneous) > 1:
        note = (
            f"Sources reported different values for {field} at essentially the "
            f"same time ({sorted(simultaneous)}), so one of them is wrong. "
            "A person needs to decide which."
        )
        return (
            {"value": latest_value, "status": "conflicting",
             "values_seen": sorted(distinct), "supporting_sources": sources},
            note,
        )

    if len(distinct) > 1:
        earliest_value = entries[0][1]
        note = None
        if latest_value < earliest_value:
            note = (
                f"The reported {field} count went down over time, from "
                f"{earliest_value} to {latest_value}. That is usually an early "
                "over-count being corrected, but it is unusual enough to confirm."
            )
        return (
            {"value": latest_value, "status": "superseded",
             "values_seen": sorted(distinct), "supporting_sources": sources},
            note,
        )

    status = "corroborated" if len(groups) > 1 else "single_source"
    return (
        {"value": latest_value, "status": status,
         "values_seen": sorted(distinct), "supporting_sources": sources},
        None,
    )


def _reconcile_categorical(field: str, pairs) -> tuple[dict, str | None] | None:
    """Reconcile a field that describes what something is. It does not change."""
    entries = [
        (getattr(claim, field), assessment["independence_group"], str(claim.source_url))
        for claim, assessment in pairs
        if getattr(claim, field) is not None
    ]
    if not entries:
        return None

    values = {e[0] for e in entries}
    groups = {e[1] for e in entries}
    sources = [e[2] for e in entries]

    if len(values) > 1:
        note = (
            f"Sources disagree about {field} ({sorted(values)}). This does not "
            "change over time, so one of them is simply wrong."
        )
        return (
            {"value": entries[0][0], "status": "conflicting",
             "values_seen": sorted(values), "supporting_sources": sources},
            note,
        )

    status = "corroborated" if len(groups) > 1 else "single_source"
    return (
        {"value": entries[0][0], "status": status,
         "values_seen": sorted(values), "supporting_sources": sources},
        None,
    )


ATTACK_COUNT_FIELDS = ("citywide_killed", "citywide_injured")


def corroborate_attack(claims, assessments: list[dict]) -> dict:
    """Reconcile the city-wide figures several articles give for one attack.

    Same rules as for an impact site, because they are the same rules: a
    city-wide death toll is also completed over days, so a later higher figure
    supersedes rather than contradicts, and two sources disagreeing at the same
    time is a real contradiction.

    Weapon tallies are taken from the most recent usable report rather than
    reconciled field by field: the air force revises "launched / intercepted" as
    a set, and merging two partial sets would invent a combination nobody
    published.
    """
    pairs = _usable_pairs(claims, assessments)
    groups = {assessment["independence_group"] for _, assessment in pairs}
    has_official = any(
        (assessment.get("voice_tier") or assessment.get("tier")) == 1
        for _, assessment in pairs
    )

    fields: dict[str, dict] = {}
    review_notes: list[str] = []
    for field in ATTACK_COUNT_FIELDS:
        reconciled = _reconcile_count(field, pairs)
        if reconciled:
            fields[field], note = reconciled
            if note:
                review_notes.append(note)

    weapons = []
    if pairs:
        latest = max(pairs, key=lambda pair: pair[0].published_at)[0]
        weapons = [w.model_dump() for w in getattr(latest, "weapons", [])]

    if not groups:
        level = "unverified"
    elif len(groups) == 1:
        level = "single_source"
    elif has_official:
        level = "well_documented"
    else:
        level = "corroborated"

    conflicting = [name for name, f in fields.items() if f["status"] == "conflicting"]
    missing: list[str] = []
    if level == "unverified":
        missing.append("Any source that passes the credibility check.")
    elif level == "single_source":
        missing.append(
            "Independent confirmation from a source outside group(s): "
            + ", ".join(sorted(groups)) + "."
        )
    if conflicting:
        missing.append(
            "A source that settles the disagreement over: " + ", ".join(conflicting) + "."
        )

    return {
        "fields": fields,
        "weapons": weapons,
        "has_conflict": bool(conflicting),
        "verification_level": level,
        "missing_for_next_level": missing,
        "usable_groups": sorted(groups),
        "needs_review": bool(review_notes),
        "review_reason": " ".join(review_notes) if review_notes else None,
    }


def corroborate(claims: list[IncidentClaim], assessments: list[dict]) -> dict:
    pairs = _usable_pairs(claims, assessments)
    groups = {assessment["independence_group"] for _, assessment in pairs}
    # The voice speaking, not the masthead printing it: a newsroom relaying an
    # official statement delivers an official fact.
    has_official = any(
        (assessment.get("voice_tier") or assessment.get("tier")) == 1
        for _, assessment in pairs
    )

    fields: dict[str, dict] = {}
    review_notes: list[str] = []

    for field in COUNT_FIELDS:
        reconciled = _reconcile_count(field, pairs)
        if reconciled:
            fields[field], note = reconciled
            if note:
                review_notes.append(note)

    for field in CATEGORICAL_FIELDS:
        reconciled = _reconcile_categorical(field, pairs)
        if reconciled:
            fields[field], note = reconciled
            if note:
                review_notes.append(note)

    if not groups:
        level = "unverified"
    elif len(groups) == 1:
        level = "single_source"
    elif has_official:
        level = "well_documented"
    else:
        level = "corroborated"

    missing: list[str] = []
    if level == "unverified":
        missing.append("Any source that passes the credibility check.")
    elif level == "single_source":
        missing.append(
            "Independent confirmation from a source outside group(s): "
            + ", ".join(sorted(groups)) + "."
        )
        missing.append("An official (tier 1) source such as ДСНС, КМВА, or поліція.")
    elif level not in _LEVELS_NEEDING_NOTHING:
        missing.append("An official (tier 1) source to reach well_documented.")

    conflicting = [name for name, f in fields.items() if f["status"] == "conflicting"]
    if conflicting:
        missing.append(
            "A source that settles the disagreement over: " + ", ".join(conflicting) + "."
        )

    return {
        "fields": fields,
        "has_conflict": bool(conflicting),
        "verification_level": level,
        "missing_for_next_level": missing,
        "usable_groups": sorted(groups),
        "needs_review": bool(review_notes),
        "review_reason": " ".join(review_notes) if review_notes else None,
    }
