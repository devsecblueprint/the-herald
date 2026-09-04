"""
Telling a Short from a long-form video.

The Atom feed carries neither a duration nor a format flag, so the
distinction has to come from somewhere else. Three signals are available,
cheapest first, and a video that cannot be decided is never guessed at --
it is retried next poll. Guessing would mean either a Short in
#content-corner or a partner's real video silently dropped.
"""

import re
from typing import Optional, Protocol

from app.clients.youtube import YouTubeClient
from app.errors import ClassificationError, YouTubeApiError
from app.models.youtube import (
    SKIP_REASON_LIVE,
    SKIP_REASON_PREMIERE,
    SKIP_REASON_SHORT,
    ContentItem,
    ShortsVerdict,
)

# YouTube allows Shorts of up to three minutes. A video of exactly 180
# seconds is therefore a Short.
SHORT_MAX_SECONDS = 180

SHORTS_TAG_RE = re.compile(r"#shorts?\b", re.IGNORECASE)


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

    def classify(
        self, item: ContentItem
    ) -> ShortsVerdict:  # pylint: disable=unused-argument
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

    def __init__(self, client: YouTubeClient):
        self.client = client

    def classify(self, item: ContentItem) -> ShortsVerdict:
        """
        Probe the Shorts URL.

        Raises:
            ClassificationError: On a timeout or an unexpected status.
        """
        try:
            is_short = self.client.shorts_url_answers(item.content_id)
        except YouTubeApiError as exc:
            raise ClassificationError(f"{item.content_id}: {exc}") from exc

        if is_short:
            return ShortsVerdict(True, self.name, SKIP_REASON_SHORT)
        return ShortsVerdict(False, self.name)


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
        client: YouTubeClient,
        fallback: Optional[ShortsDetector] = None,
        max_seconds: int = SHORT_MAX_SECONDS,
    ):
        self.client = client
        self.fallback = fallback
        self.max_seconds = max_seconds

    def classify(self, item: ContentItem) -> ShortsVerdict:
        """
        Classify by duration, falling back to the probe when unusable.

        Raises:
            ClassificationError: If the API is unusable and there is no fallback.
        """
        try:
            details = self.client.fetch_video_details(item.content_id)
        except YouTubeApiError as exc:
            return self._delegate(item, str(exc))

        if details.live_broadcast == "live":
            return ShortsVerdict(True, self.name, SKIP_REASON_LIVE)
        if details.live_broadcast == "upcoming":
            return ShortsVerdict(True, self.name, SKIP_REASON_PREMIERE)

        seconds = details.duration_seconds
        if not seconds:
            return self._delegate(
                item, f"Data API duration unusable ({details.duration!r})"
            )

        if seconds <= self.max_seconds:
            return ShortsVerdict(True, self.name, SKIP_REASON_SHORT)
        return ShortsVerdict(False, self.name)

    def _delegate(self, item: ContentItem, why: str) -> ShortsVerdict:
        """Hand off to the fallback detector, or give up honestly."""
        if self.fallback is None:
            raise ClassificationError(f"{item.content_id}: {why}")
        return self.fallback.classify(item)


def build_shorts_detector(
    client: YouTubeClient, exclude_shorts: bool = True
) -> ShortsDetector:
    """Pick the detector implied by the configuration and the client."""
    if not exclude_shorts:
        return NullShortsDetector()
    probe = ShortsUrlProbeDetector(client)
    if client.has_api_key:
        return DataApiShortsDetector(client, fallback=probe)
    return probe
