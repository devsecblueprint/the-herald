"""
Everything The Herald asks YouTube over HTTP.

Three questions, three endpoints: what has a channel published (the public
Atom feed), which channel does this ``@handle`` belong to (the channel page
or the Data API), and how long is this video (the Shorts URL or the Data
API). The feed is unauthenticated and quota-free, which is why it is the
primary source rather than the Data API.

The client answers those questions and nothing more. What to do with the
answers -- caching a resolution, deciding a video is a Short, turning a feed
into content items -- belongs to the services.
"""

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from app.clients.http import HttpClient, HttpError
from app.errors import ChannelResolutionError, FeedFetchError, YouTubeApiError
from app.models.youtube import (CHANNEL_ID_RE, KIND_HANDLE, KIND_USER,
                                KIND_VANITY, ChannelReference)

FEED_BASE_URL = "https://www.youtube.com/feeds/videos.xml"
SHORTS_URL_TEMPLATE = "https://www.youtube.com/shorts/{video_id}"
DATA_API_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
DATA_API_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

DEFAULT_PROBE_TIMEOUT = 10

DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)

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


def channel_feed_url(channel_id: str) -> str:
    """The Atom feed for one channel."""
    return f"{FEED_BASE_URL}?channel_id={channel_id}"


def playlist_feed_url(playlist_id: str) -> str:
    """The Atom feed for one playlist."""
    return f"{FEED_BASE_URL}?playlist_id={playlist_id}"


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


@dataclass(frozen=True)
class VideoDetails:
    """What the Data API knows about one video."""

    duration: Optional[str]
    duration_seconds: Optional[int]
    live_broadcast: Optional[str]


class YouTubeClient:
    """The YouTube endpoints The Herald reads."""

    def __init__(
        self,
        http_client: HttpClient,
        api_key: Optional[str] = None,
        probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    ):
        self.http = http_client
        self.api_key = api_key
        self.probe_timeout = probe_timeout

    @property
    def has_api_key(self) -> bool:
        """True when the Data API is available to this client."""
        return bool(self.api_key)

    # -- feeds -------------------------------------------------------------

    def fetch_feed(self, url: str, source_name: str) -> str:
        """
        Fetch one Atom feed body.

        Raises:
            FeedFetchError: If the feed cannot be fetched.
        """
        try:
            response = self.http.get(url)
        except HttpError as exc:
            raise FeedFetchError(f"'{source_name}': feed fetch failed: {exc}") from exc

        if not response.ok:
            raise FeedFetchError(f"'{source_name}': feed returned HTTP {response.status_code}")

        return response.text

    # -- channel resolution -------------------------------------------------

    def resolve_channel_id(self, reference: ChannelReference) -> Tuple[str, str]:
        """
        Look up the canonical ``UC...`` id behind a channel reference.

        The Data API can answer for handles and legacy usernames; everything
        else is read off the public channel page.

        Returns:
            The channel id and where it came from (``api`` or ``page``).

        Raises:
            ChannelResolutionError: If the channel cannot be identified.
        """
        if self.api_key and reference.kind in (KIND_HANDLE, KIND_USER):
            return self._channel_id_via_api(reference), "api"
        return self._channel_id_via_page(reference), "page"

    def _channel_id_via_api(self, reference: ChannelReference) -> str:
        """Resolve using the YouTube Data API."""
        params = {"part": "id", "key": self.api_key}
        if reference.kind == KIND_HANDLE:
            params["forHandle"] = reference.value
        else:
            params["forUsername"] = reference.value

        try:
            response = self.http.get(DATA_API_CHANNELS_URL, params=params)
        except HttpError as exc:
            raise ChannelResolutionError(
                f"{reference.key}: Data API request failed: {exc}"
            ) from exc

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

    def _channel_id_via_page(self, reference: ChannelReference) -> str:
        """Resolve by reading the public channel page."""
        url = self.channel_page_url(reference)
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

        raise ChannelResolutionError(f"{reference.key}: no channel id marker found on {url}")

    @staticmethod
    def channel_page_url(reference: ChannelReference) -> str:
        """The public page that identifies this reference's channel."""
        if reference.kind == KIND_HANDLE:
            return f"https://www.youtube.com/{reference.value}"
        if reference.kind == KIND_USER:
            return f"https://www.youtube.com/user/{reference.value}"
        if reference.kind == KIND_VANITY:
            return f"https://www.youtube.com/c/{reference.value}"
        raise ChannelResolutionError(f"cannot resolve reference kind {reference.kind!r}")

    # -- video shape --------------------------------------------------------

    def shorts_url_answers(self, video_id: str) -> bool:
        """
        Ask ``youtube.com/shorts/<id>`` whether this video is a Short.

        A real Short answers 200. A long-form video is redirected to
        ``/watch?v=``. No API key, no quota, and authoritative.

        Raises:
            YouTubeApiError: On a timeout or an unexpected status.
        """
        url = SHORTS_URL_TEMPLATE.format(video_id=video_id)
        try:
            response = self.http.head(url, allow_redirects=False, timeout=self.probe_timeout)
        except HttpError as exc:
            raise YouTubeApiError(f"Shorts probe failed: {exc}") from exc

        if response.status_code == 200:
            return True
        if response.is_redirect:
            return False

        raise YouTubeApiError(f"Shorts probe returned HTTP {response.status_code}")

    def fetch_video_details(self, video_id: str) -> VideoDetails:
        """
        Read one video's duration and broadcast state from the Data API.

        Raises:
            YouTubeApiError: If the API cannot answer.
        """
        try:
            response = self.http.get(
                DATA_API_VIDEOS_URL,
                params={
                    "part": "contentDetails,snippet",
                    "id": video_id,
                    "key": self.api_key,
                },
            )
        except HttpError as exc:
            raise YouTubeApiError(f"Data API request failed: {exc}") from exc

        if not response.ok:
            raise YouTubeApiError(f"Data API returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise YouTubeApiError("Data API response was not JSON") from exc

        entries = (payload or {}).get("items") or []
        if not entries:
            raise YouTubeApiError("Data API returned no video")

        return _video_details(entries[0])


def _video_details(entry: Mapping[str, Any]) -> VideoDetails:
    """Read the fields we care about out of one Data API entry."""
    duration = (entry.get("contentDetails") or {}).get("duration")
    return VideoDetails(
        duration=duration,
        duration_seconds=parse_duration_seconds(duration),
        live_broadcast=(entry.get("snippet") or {}).get("liveBroadcastContent"),
    )
