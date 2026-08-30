"""
Turning a configured channel reference into a canonical ``UC...`` id.

Nobody should have to go hunting for an ``externalId`` to onboard a
partner, so an ``@handle`` is resolved for them -- at poll time, not at
startup, so a renamed handle is one source's problem rather than a crash
on boot.

Results are cached twice: in memory for six hours and in DynamoDB for
thirty days. Both layers expire, because a handle can be released and
taken over by a different channel, and an immortal in-process memo would
keep announcing the new owner's videos under the old partner's name.
"""

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.services.youtube.clock import utcnow
from app.services.youtube.config import CHANNEL_ID_RE
from app.services.youtube.errors import ChannelResolutionError
from app.services.youtube.http import HttpClient, HttpError
from app.services.youtube.logging_utils import EventLogger
from app.services.youtube.models import (
    KIND_HANDLE,
    KIND_ID,
    KIND_PLAYLIST,
    KIND_USER,
    KIND_VANITY,
    ChannelReference,
)
from app.services.youtube.repository import ChannelReferenceCache

MEMO_TTL_SECONDS = 6 * 60 * 60
DATA_API_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

# Four independent markers on the public channel page. Any one of them is
# enough, so a single markup change does not break resolution.
PAGE_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("externalId", r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"'),
    ("canonical", r'<link[^>]*rel="canonical"[^>]*href="[^"]*?/channel/(UC[A-Za-z0-9_-]{22})"'),
    ("canonical", r'<link[^>]*href="[^"]*?/channel/(UC[A-Za-z0-9_-]{22})"[^>]*rel="canonical"'),
    ("identifier", r'<meta[^>]*itemprop="identifier"[^>]*content="(UC[A-Za-z0-9_-]{22})"'),
    ("identifier", r'<meta[^>]*content="(UC[A-Za-z0-9_-]{22})"[^>]*itemprop="identifier"'),
    ("channelId", r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"'),
)

_COMPILED_MARKERS = tuple((name, re.compile(pattern)) for name, pattern in PAGE_MARKERS)


@dataclass(frozen=True)
class Resolution:
    """A resolved channel id, and where the answer came from."""

    channel_id: str
    origin: str

    @property
    def was_looked_up(self) -> bool:
        """True when the answer required a network call."""
        return self.origin in ("api", "page")


class ChannelResolver:
    """Resolves channel references, with a memo and a DynamoDB cache."""

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        http_client: HttpClient,
        cache: Optional[ChannelReferenceCache] = None,
        api_key: Optional[str] = None,
        memo_ttl_seconds: int = MEMO_TTL_SECONDS,
        clock=utcnow,
        event_logger: Optional[EventLogger] = None,
    ):
        self.http = http_client
        self.cache = cache
        self.api_key = api_key
        self.memo_ttl_seconds = memo_ttl_seconds
        self.clock = clock
        self.events = event_logger or EventLogger(__name__)
        self._memo: Dict[str, Tuple[str, float]] = {}

    def resolve(self, reference: ChannelReference) -> Resolution:
        """
        Resolve a reference to a canonical ``UC...`` channel id.

        Raises:
            ChannelResolutionError: If the channel cannot be identified.
        """
        if reference.kind == KIND_ID:
            return Resolution(reference.value, "config")

        if reference.kind == KIND_PLAYLIST:
            raise ChannelResolutionError(
                "playlist sources are polled directly and have no channel reference"
            )

        key = reference.key
        now = self.clock().timestamp()

        memoized = self._memo.get(key)
        if memoized and memoized[1] > now:
            return Resolution(memoized[0], "memo")

        if self.cache is not None:
            cached = self.cache.get(key)
            if cached and CHANNEL_ID_RE.match(cached):
                self._memoize(key, cached)
                return Resolution(cached, "cache")

        channel_id, origin = self._look_up(reference)

        self.events.event("youtube.channel.resolved", reference=key, channel_id=channel_id)
        self._memoize(key, channel_id)
        if self.cache is not None:
            self.cache.put(key, channel_id)
        return Resolution(channel_id, origin)

    def _memoize(self, key: str, channel_id: str) -> None:
        """Store a resolution in the in-process memo with its expiry."""
        self._memo[key] = (channel_id, self.clock().timestamp() + self.memo_ttl_seconds)

    def _look_up(self, reference: ChannelReference) -> Tuple[str, str]:
        """Resolve over the network: Data API when possible, page otherwise."""
        if self.api_key and reference.kind in (KIND_HANDLE, KIND_USER):
            return self._look_up_via_api(reference), "api"
        return self._look_up_via_page(reference), "page"

    def _look_up_via_api(self, reference: ChannelReference) -> str:
        """Resolve using the YouTube Data API."""
        params = {"part": "id", "key": self.api_key}
        if reference.kind == KIND_HANDLE:
            params["forHandle"] = reference.value
        else:
            params["forUsername"] = reference.value

        try:
            response = self.http.get(DATA_API_CHANNELS_URL, params=params)
        except HttpError as exc:
            raise ChannelResolutionError(f"{reference.key}: Data API request failed: {exc}") from exc

        if not response.ok:
            raise ChannelResolutionError(
                f"{reference.key}: Data API returned HTTP {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ChannelResolutionError(
                f"{reference.key}: Data API response was not JSON"
            ) from exc

        items = (payload or {}).get("items") or []
        if not items:
            raise ChannelResolutionError(f"{reference.key}: no channel matched")

        channel_id = str(items[0].get("id") or "")
        if not CHANNEL_ID_RE.match(channel_id):
            raise ChannelResolutionError(
                f"{reference.key}: Data API returned an unusable id {channel_id!r}"
            )
        return channel_id

    def _look_up_via_page(self, reference: ChannelReference) -> str:
        """Resolve by reading the public channel page."""
        url = self._page_url(reference)
        try:
            response = self.http.get(url, headers={"Accept-Language": "en-US,en;q=0.9"})
        except HttpError as exc:
            raise ChannelResolutionError(f"{reference.key}: page fetch failed: {exc}") from exc

        if response.status_code == 404:
            raise ChannelResolutionError(f"{reference.key}: channel page not found (404)")
        if not response.ok:
            raise ChannelResolutionError(
                f"{reference.key}: channel page returned HTTP {response.status_code}"
            )

        for _marker, pattern in _COMPILED_MARKERS:
            match = pattern.search(response.text or "")
            if match:
                return match.group(1)

        raise ChannelResolutionError(
            f"{reference.key}: no channel id marker found on {url}"
        )

    @staticmethod
    def _page_url(reference: ChannelReference) -> str:
        """The public page that identifies this reference's channel."""
        if reference.kind == KIND_HANDLE:
            return f"https://www.youtube.com/{reference.value}"
        if reference.kind == KIND_USER:
            return f"https://www.youtube.com/user/{reference.value}"
        if reference.kind == KIND_VANITY:
            return f"https://www.youtube.com/c/{reference.value}"
        raise ChannelResolutionError(f"cannot resolve reference kind {reference.kind!r}")
