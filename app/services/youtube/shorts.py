"""
Telling a Short from a long-form video.

The Atom feed carries neither a duration nor a format flag, so the
distinction has to come from somewhere else. Three signals are available,
cheapest first, and a video that cannot be decided is never guessed at --
it is retried next poll. Guessing would mean either a Short in
#content-corner or a partner's real video silently dropped.
"""

import re
from dataclasses import dataclass
from typing import Optional, Protocol

from app.services.youtube.errors import ClassificationError
from app.services.youtube.http import HttpClient, HttpError
from app.services.youtube.models import (
    SKIP_REASON_LIVE,
    SKIP_REASON_PREMIERE,
    SKIP_REASON_SHORT,
    ContentItem,
)

# YouTube allows Shorts of up to three minutes. A video of exactly 180
# seconds is therefore a Short.
SHORT_MAX_SECONDS = 180

SHORTS_URL_TEMPLATE = "https://www.youtube.com/shorts/{video_id}"
DATA_API_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
SHORTS_TAG_RE = re.compile(r"#shorts?\b", re.IGNORECASE)


@dataclass(frozen=True)
class ShortsVerdict:
    """Whether a video should be announced, and who decided."""

    is_short: bool
    detector: str
    reason: Optional[str] = None


class ShortsDetector(Protocol):
    """Decides whether a video is a Short (or otherwise not announceable)."""

    name: str

    def classify(self, item: ContentItem) -> ShortsVerdict:
        """
        Classify one video.

        Raises:
            ClassificationError: If the video cannot be decided.
        """


class NullShortsDetector:
    """Announces everything. Used when ``exclude_shorts`` is false."""

    name = "disabled"

    def classify(self, item: ContentItem) -> ShortsVerdict:  # pylint: disable=unused-argument
        """Never a Short; no network call is made."""
        return ShortsVerdict(is_short=False, detector=self.name)


class HeuristicShortsDetector:
    """
    Offline detection from a ``#shorts`` tag in the title or description.

    Deliberately *not* used as a shortcut for the other two detectors: a
    long-form video whose description says "clips are on my #shorts
    channel" would be misclassified, and a false positive here is permanent.
    """

    name = "heuristic"

    def classify(self, item: ContentItem) -> ShortsVerdict:
        """Look for a ``#shorts`` tag in the title or description."""
        haystack = f"{item.title or ''}\n{item.description or ''}"
        if SHORTS_TAG_RE.search(haystack):
            return ShortsVerdict(True, self.name, SKIP_REASON_SHORT)
        return ShortsVerdict(False, self.name)


class ShortsUrlProbeDetector:
    """
    The default detector: a ``HEAD`` to ``youtube.com/shorts/<id>``.

    A real Short answers 200. A long-form video is redirected to
    ``/watch?v=``. No API key, no quota, and authoritative.
    """

    name = "url_probe"

    def __init__(self, http_client: HttpClient, timeout: float = 10):
        self.http = http_client
        self.timeout = timeout

    def classify(self, item: ContentItem) -> ShortsVerdict:
        """
        Probe the Shorts URL without following redirects.

        Raises:
            ClassificationError: On a timeout or an unexpected status.
        """
        url = SHORTS_URL_TEMPLATE.format(video_id=item.content_id)
        try:
            response = self.http.head(url, allow_redirects=False, timeout=self.timeout)
        except HttpError as exc:
            raise ClassificationError(f"{item.content_id}: Shorts probe failed: {exc}") from exc

        if response.status_code == 200:
            return ShortsVerdict(True, self.name, SKIP_REASON_SHORT)
        if response.is_redirect:
            return ShortsVerdict(False, self.name)

        raise ClassificationError(
            f"{item.content_id}: Shorts probe returned HTTP {response.status_code}"
        )


class DataApiShortsDetector:
    """
    Duration-based detection using the YouTube Data API.

    Also filters out live broadcasts and unfinished premieres. When the API
    reports no usable duration (``P0D``, which it returns for streams) it
    delegates to the probe rather than reading the zero as "under 180
    seconds".
    """

    name = "data_api"

    def __init__(
        self,
        http_client: HttpClient,
        api_key: str,
        fallback: Optional[ShortsDetector] = None,
        max_seconds: int = SHORT_MAX_SECONDS,
    ):
        self.http = http_client
        self.api_key = api_key
        self.fallback = fallback
        self.max_seconds = max_seconds

    def classify(self, item: ContentItem) -> ShortsVerdict:
        # Each return is a distinct, documented signal from the API.
        # pylint: disable=too-many-return-statements
        """
        Classify by duration, falling back to the probe when unusable.

        Raises:
            ClassificationError: If the API is unusable and there is no fallback.
        """
        try:
            response = self.http.get(
                DATA_API_VIDEOS_URL,
                params={
                    "part": "contentDetails,snippet",
                    "id": item.content_id,
                    "key": self.api_key,
                },
            )
        except HttpError as exc:
            return self._delegate(item, f"Data API request failed: {exc}")

        if not response.ok:
            return self._delegate(item, f"Data API returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError:
            return self._delegate(item, "Data API response was not JSON")

        entries = (payload or {}).get("items") or []
        if not entries:
            return self._delegate(item, "Data API returned no video")

        entry = entries[0]
        broadcast = (entry.get("snippet") or {}).get("liveBroadcastContent")
        if broadcast == "live":
            return ShortsVerdict(True, self.name, SKIP_REASON_LIVE)
        if broadcast == "upcoming":
            return ShortsVerdict(True, self.name, SKIP_REASON_PREMIERE)

        duration = (entry.get("contentDetails") or {}).get("duration")
        seconds = parse_duration_seconds(duration)
        if seconds is None or seconds == 0:
            return self._delegate(item, f"Data API duration unusable ({duration!r})")

        if seconds <= self.max_seconds:
            return ShortsVerdict(True, self.name, SKIP_REASON_SHORT)
        return ShortsVerdict(False, self.name)

    def _delegate(self, item: ContentItem, why: str) -> ShortsVerdict:
        """Hand off to the fallback detector, or give up honestly."""
        if self.fallback is None:
            raise ClassificationError(f"{item.content_id}: {why}")
        return self.fallback.classify(item)


def parse_duration_seconds(duration: Optional[str]) -> Optional[int]:
    """
    Parse an ISO-8601 duration such as ``PT4M13S`` into seconds.

    Returns None when the value is missing or unparseable.
    """
    if not isinstance(duration, str):
        return None
    match = DURATION_RE.match(duration.strip())
    if not match:
        return None
    parts = {key: int(value or 0) for key, value in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def build_shorts_detector(
    http_client: HttpClient,
    exclude_shorts: bool = True,
    api_key: Optional[str] = None,
) -> ShortsDetector:
    """Pick the detector implied by the configuration and environment."""
    if not exclude_shorts:
        return NullShortsDetector()
    probe = ShortsUrlProbeDetector(http_client)
    if api_key:
        return DataApiShortsDetector(http_client, api_key, fallback=probe)
    return probe
