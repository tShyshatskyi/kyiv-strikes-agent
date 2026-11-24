"""Who we read, and how much each one counts.

Every channel belongs to a *voice*. Two channels with the same voice are one
witness, however many times they repost each other.

The city administration, the mayor and the city state administration are one
voice, not three. They are the same city hall publishing the same morning
briefing on three channels, so a strike all three report is still one voice.
The emergency service is a separate voice: its crews are physically on site.

Aggregators (Trukha, Real Kyiv) are read because they are fast and often first,
but they never count towards confirmation: they mostly repost the others.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")

# Period covered, in Kyiv time. Collection adds a few hours of margin either side
# so a night that runs over midnight is complete.
PERIOD_START = datetime(2026, 10, 1, tzinfo=KYIV)
PERIOD_END = datetime(2026, 11, 24, tzinfo=KYIV)  # exclusive: up to 23 Nov

CHANNELS = {
    # handle:                  (display name,                          voice,        kind)
    "VA_Kyiv":                 ("Kyiv City Military Administration",   "city",       "official"),
    "vitaliy_klitschko":       ("Mayor Vitali Klitschko",              "city",       "official"),
    "KyivCityOfficial":        ("Kyiv City State Administration",      "city",       "official"),
    "dsns_kyiv":               ("State Emergency Service, Kyiv",       "emergency",  "official"),
    "suspilne_kyiv":           ("Suspilne Kyiv",                       "suspilne",   "media"),
    "suspilnenews":            ("Suspilne News",                       "suspilne",   "media"),
    "ukrpravda_news":          ("Ukrainska Pravda",                    "pravda",     "media"),
    "hromadske_ua":            ("hromadske",                           "hromadske",  "media"),
    "kyivindependent_official": ("The Kyiv Independent",               "kyiv_independent", "media"),
    "kyivpost_official":       ("Kyiv Post",                           "kyiv_post",  "media"),
    "bbcukrainian":            ("BBC News Ukrainian",                  "bbc",        "media"),
    "truexakyiv":              ("Trukha Kyiv",                         "agg_trukha", "aggregator"),
    "kievreal1":               ("Real Kyiv",                           "agg_realkyiv", "aggregator"),
}

# Channels that only ever post about Kyiv. Everything else is national and is
# kept only when a post names the city.
CITY_ONLY = {"VA_Kyiv", "vitaliy_klitschko", "KyivCityOfficial", "dsns_kyiv",
             "suspilne_kyiv", "truexakyiv", "kievreal1"}


def channel(handle: str) -> dict:
    name, voice, kind = CHANNELS[handle]
    return {"handle": handle, "name": name, "voice": voice, "kind": kind}


def counts_as_witness(handle: str) -> bool:
    return CHANNELS[handle][2] != "aggregator"


def to_kyiv(utc_iso: str) -> datetime:
    return datetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc).astimezone(KYIV)
