"""Nominatim access. The only networked component of the custom server.

Offline mode swaps the transport only: the caller's query normalization and the
downstream parsing, validation, and point-in-polygon logic are identical in both
modes, as the assignment's fixture rules require.
"""

import hashlib
import json
import time
from pathlib import Path

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Bounding box around Kyiv: left, top, right, bottom.
KYIV_VIEWBOX = "30.185,50.590,30.825,50.213"


def fixture_key(normalized_query: str) -> str:
    """Stable filename-safe key for a normalized query."""
    return hashlib.sha1(normalized_query.encode("utf-8")).hexdigest()


class UpstreamUnavailable(RuntimeError):
    """Nominatim could not be reached or returned an unusable response."""


class RateLimited(RuntimeError):
    """Nominatim signalled that we are querying too fast."""


class NominatimClient:
    def __init__(
        self,
        contact: str,
        offline: bool,
        fixtures_dir: Path,
        min_interval_s: float = 1.0,
    ) -> None:
        if not contact:
            raise ValueError(
                "NOMINATIM_CONTACT is required: the Nominatim usage policy "
                "demands a real contact address in the User-Agent."
            )
        self.contact = contact
        self.offline = offline
        self.fixtures_dir = Path(fixtures_dir)
        self.min_interval_s = min_interval_s
        self._last_call_at: float | None = None

    def _throttle(self) -> None:
        if self._last_call_at is None:
            self._last_call_at = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_call_at
        remaining = self.min_interval_s - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_call_at = time.monotonic()

    def search(self, normalized_query: str) -> list[dict]:
        """Return raw Nominatim results. An empty list is a valid answer."""
        self._throttle()
        if self.offline:
            return self._read_fixture(normalized_query)
        return self._fetch(normalized_query)

    def _read_fixture(self, normalized_query: str) -> list[dict]:
        path = self.fixtures_dir / f"{fixture_key(normalized_query)}.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _fetch(self, normalized_query: str) -> list[dict]:
        headers = {"User-Agent": f"kyiv-strikes-mcp/1.0 ({self.contact})"}
        params = {
            "q": normalized_query,
            "format": "jsonv2",
            "limit": 5,
            # viewbox biases ranking toward Kyiv but deliberately does NOT bound
            # the search. With bounded=1 Nominatim could never return a point
            # outside Kyiv, which would make the district point-in-polygon check
            # dead code and OUT_OF_AREA unreachable. Validation must be able to
            # actually reject something.
            "viewbox": KYIV_VIEWBOX,
            "addressdetails": 1,
        }
        try:
            response = httpx.get(
                NOMINATIM_URL, params=params, headers=headers, timeout=15.0
            )
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable(str(exc)) from exc

        if response.status_code == 429:
            raise RateLimited("Nominatim returned HTTP 429.")
        if response.status_code >= 400:
            raise UpstreamUnavailable(f"Nominatim returned HTTP {response.status_code}.")

        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamUnavailable("Nominatim returned a non-JSON body.") from exc
