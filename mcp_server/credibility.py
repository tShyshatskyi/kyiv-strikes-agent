"""Source tiering and — the part that matters — independence grouping.

Five outlets reporting a strike usually means one press release and four
reprints. Counting them as five confirmations is the single easiest way to
produce a confidently wrong record, so this module collapses them into one
voice.

The exception, and it is an important one: an outlet that relays a statement but
also reports something the original never said got that somewhere of its own. On
the strength of what it added, it counts as a separate voice.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from common.schemas import IncidentClaim, SourceRef

DEFAULT_RELEVANCE_WINDOW_DAYS = 14

# Fields whose presence constitutes original reporting rather than relaying.
_SUBSTANTIVE_FIELDS = ("killed", "injured", "structure_type", "damage")


@dataclass(frozen=True)
class SourceEntry:
    domain: str
    tier: int
    independence_group: str
    name: str


@dataclass(frozen=True)
class Registry:
    entries: dict
    excluded_domains: frozenset


def load_registry(path: str | Path) -> Registry:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    entries = {
        item["domain"]: SourceEntry(
            domain=item["domain"],
            tier=int(item["tier"]),
            independence_group=item["independence_group"],
            name=item.get("name", item["domain"]),
        )
        for item in raw.get("sources", [])
    }
    return Registry(
        entries=entries,
        excluded_domains=frozenset(raw.get("excluded_domains", [])),
    )


def _has_value(claim: IncidentClaim, field: str) -> bool:
    value = getattr(claim, field, None)
    return value not in (None, "", [], ())


def _relayed_group(source: SourceRef, entry: SourceEntry, registry: Registry) -> str | None:
    """The group this article is relaying, or None if it speaks for itself."""
    cited = {
        registry.entries[domain].independence_group
        for domain in source.cited_sources
        if domain in registry.entries
    }
    if len(cited) == 1:
        relayed = next(iter(cited))
        if relayed != entry.independence_group:
            return relayed
    return None


def _additions(claim: IncidentClaim | None, primary_claims: list[IncidentClaim]) -> list[str]:
    """Fields this article reports that no article of the relayed source does.

    With no primary article to compare against we return nothing: we cannot tell
    what was added, and independence is not granted on trust.
    """
    if claim is None or not primary_claims:
        return []
    return [
        field for field in _SUBSTANTIVE_FIELDS
        if _has_value(claim, field)
        and not any(_has_value(p, field) for p in primary_claims)
    ]


def assess_sources(
    sources: list[SourceRef],
    registry: Registry,
    event_date: date,
    claims: list[IncidentClaim] | None = None,
    relevance_window_days: int = DEFAULT_RELEVANCE_WINDOW_DAYS,
) -> list[dict]:
    if relevance_window_days <= 0:
        raise ValueError("relevance_window_days must be a positive number of days.")

    window = timedelta(days=relevance_window_days)
    claims_by_url = {str(c.source_url): c for c in (claims or [])}

    # Claims belonging to sources that speak for their own group, keyed by group.
    # These are what a relay gets compared against.
    original_claims: dict[str, list[IncidentClaim]] = {}
    for source in sources:
        entry = registry.entries.get(source.domain)
        if entry is None or _relayed_group(source, entry, registry) is not None:
            continue
        claim = claims_by_url.get(str(source.url))
        if claim is not None:
            original_claims.setdefault(entry.independence_group, []).append(claim)

    results: list[dict] = []
    for source in sources:
        url = str(source.url)
        published = source.published_at.date()

        if source.domain in registry.excluded_domains:
            results.append({
                "url": url, "tier": None, "independence_group": None,
                "usable_as_corroborator": False, "adds": [],
                "reason": f"Domain {source.domain} is on the excluded list.",
            })
            continue

        entry = registry.entries.get(source.domain)
        if entry is None:
            results.append({
                "url": url, "tier": None, "independence_group": None,
                "usable_as_corroborator": False, "adds": [],
                "reason": f"Domain {source.domain} is not in the source registry.",
            })
            continue

        if not (event_date <= published <= event_date + window):
            results.append({
                "url": url, "tier": entry.tier,
                "independence_group": entry.independence_group,
                "usable_as_corroborator": False, "adds": [],
                "reason": (
                    f"Published {published.isoformat()}, outside the relevance "
                    f"window {event_date.isoformat()} +{relevance_window_days}d."
                ),
            })
            continue

        relayed = _relayed_group(source, entry, registry)
        adds = _additions(claims_by_url.get(url), original_claims.get(relayed or "", []))

        if relayed and not adds:
            group = relayed
            usable = True
            reason = (
                f"Tier {entry.tier}, but relays {relayed} and reports nothing "
                "beyond it — counted as that source, not a second one."
            )
        elif relayed and adds:
            group = entry.independence_group
            usable = True
            reason = (
                f"Tier {entry.tier}; relays {relayed} but also reports "
                f"{', '.join(adds)}, which {relayed} did not — counted as its "
                "own voice."
            )
        elif entry.tier == 3:
            group = entry.independence_group
            usable = False
            reason = "Tier 3 aggregator with no identified primary source."
        else:
            group = entry.independence_group
            usable = True
            reason = f"Tier {entry.tier} original reporting."

        results.append({
            "url": url, "tier": entry.tier, "independence_group": group,
            "usable_as_corroborator": usable, "adds": adds, "reason": reason,
        })

    return results
