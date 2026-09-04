"""
Ingestion: a configured source in, ``ContentItem``s out.

Two steps. First the source's channel reference is resolved to a canonical
``UC...`` id -- at poll time, not at startup, so a renamed handle is one
source's problem rather than a crash on boot. Then its Atom feed is read
and parsed newest-first.

Resolutions are cached twice: in memory for six hours and in DynamoDB for
thirty days. Both layers expire, because a handle can be released and taken
over by a different channel, and an immortal in-process memo would keep
announcing the new owner's videos under the old partner's name.

The feed carries no duration and no format flag, so Shorts are dealt with
separately -- see ``classification.py``.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

from app.clients.youtube import (
    UploadItem,
    YouTubeClient,
    channel_feed_url,
    playlist_feed_url,
)
from app.config.youtube import YouTubeSource
from app.errors import ChannelResolutionError, FeedFetchError, YouTubeApiError
from app.models.youtube import (
    CHANNEL_ID_RE,
    KIND_ID,
    KIND_PLAYLIST,
    ChannelReference,
    ContentItem,
    SourceFetchResult,
)
from app.repositories.youtube.channel_cache import ChannelReferenceCache
from app.utils.clock import parse_iso, utcnow
from app.utils.logging import EventLogger

PLATFORM = "youtube"

MEMO_TTL_SECONDS = 6 * 60 * 60

ATOM_NS = "http://www.w3.org/2005/Atom"
YT_NS = "http://www.youtube.com/xml/schemas/2015"
MEDIA_NS = "http://search.yahoo.com/mrss/"

NAMESPACES = {"atom": ATOM_NS, "yt": YT_NS, "media": MEDIA_NS}


def feed_url_for(source: YouTubeSource, channel_id: Optional[str]) -> str:
    """Build the Atom feed URL for a source."""
    if source.is_playlist:
        return playlist_feed_url(source.reference.value)
    if not channel_id:
        raise FeedFetchError(f"'{source.name}' has no resolved channel id")
    return channel_feed_url(channel_id)


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
        client: YouTubeClient,
        cache: Optional[ChannelReferenceCache] = None,
        memo_ttl_seconds: int = MEMO_TTL_SECONDS,
        clock=utcnow,
        event_logger: Optional[EventLogger] = None,
    ):
        self.client = client
        self.cache = cache
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

        channel_id, origin = self.client.resolve_channel_id(reference)

        self.events.event(
            "youtube.channel.resolved", reference=key, channel_id=channel_id
        )
        self._memoize(key, channel_id)
        if self.cache is not None:
            self.cache.put(key, channel_id)
        return Resolution(channel_id, origin)

    def _memoize(self, key: str, channel_id: str) -> None:
        """Store a resolution in the in-process memo with its expiry."""
        self._memo[key] = (channel_id, self.clock().timestamp() + self.memo_ttl_seconds)


class YouTubeIngestionService:
    """Reads a channel or playlist feed and returns it newest-first."""

    platform = PLATFORM

    def __init__(
        self,
        client: YouTubeClient,
        resolver: Optional[ChannelResolver] = None,
        event_logger: Optional[EventLogger] = None,
    ):
        self.client = client
        self.resolver = resolver
        self.events = event_logger or EventLogger(__name__)

    def fetch(self, source: YouTubeSource) -> SourceFetchResult:
        """
        Fetch and parse one source's uploads.

        Channel sources are read from the YouTube Data API when the client
        has an API key, because the unauthenticated Atom feed is throttled
        by IP and returns 404s under load. The Atom feed remains the path
        for playlists and the fallback when no key is configured.

        Raises:
            ChannelResolutionError: If the configured handle cannot be resolved.
            FeedFetchError: If the source cannot be fetched or parsed.
        """
        channel_id = None
        if not source.is_playlist:
            if self.resolver is None:
                raise FeedFetchError(
                    f"'{source.name}' needs a resolver to find its channel id"
                )
            channel_id = self.resolver.resolve(source.reference).channel_id

        # Data API listing: channel sources only, and only with a key.
        if channel_id and self.client.has_api_key:
            try:
                uploads = self.client.list_uploads(channel_id)
                items = self._items_from_uploads(uploads, source, channel_id)
                return SourceFetchResult(
                    source_key=source.key,
                    source_name=source.name,
                    channel_id=channel_id,
                    feed_url=f"data_api:playlistItems:{channel_id}",
                    items=items,
                )
            except YouTubeApiError as exc:
                # Fall back to the feed rather than fail the source outright.
                self.events.warning(
                    "youtube.source.data_api_fallback",
                    source_name=source.name,
                    source_key=source.key,
                    error=str(exc),
                )

        url = feed_url_for(source, channel_id)
        items = self.parse(self.client.fetch_feed(url, source.name), source, channel_id)

        return SourceFetchResult(
            source_key=source.key,
            source_name=source.name,
            channel_id=channel_id,
            feed_url=url,
            items=items,
        )

    def _items_from_uploads(
        self,
        uploads: List[UploadItem],
        source: YouTubeSource,
        channel_id: Optional[str],
    ) -> List[ContentItem]:
        """Map Data API uploads into content items, newest first."""
        items: List[ContentItem] = []
        for upload in uploads:
            if not upload.video_id or not upload.published_at:
                continue
            try:
                published_at = parse_iso(upload.published_at)
            except ValueError:
                continue

            items.append(
                ContentItem(
                    platform=PLATFORM,
                    content_id=upload.video_id,
                    title=upload.title or upload.video_id,
                    url=f"https://www.youtube.com/watch?v={upload.video_id}",
                    published_at=published_at,
                    source_name=source.name,
                    relationship=source.relationship,
                    categories=list(source.categories),
                    description=upload.description or "",
                    author_name=source.attribution or upload.channel_title,
                    author_url=(
                        f"https://www.youtube.com/channel/{upload.channel_id}"
                        if upload.channel_id
                        else None
                    ),
                    thumbnail_url=upload.thumbnail_url,
                    channel_id=upload.channel_id or channel_id,
                )
            )

        items.sort(key=lambda item: item.published_at, reverse=True)
        return items

    def parse(
        self, xml_text: str, source: YouTubeSource, channel_id: Optional[str] = None
    ) -> List[ContentItem]:
        """
        Parse an Atom feed body into content items, newest first.

        Entries missing a video id or a publish time are skipped rather than
        failing the whole feed.

        Raises:
            FeedFetchError: If the body is not parseable XML.
        """
        try:
            root = ElementTree.fromstring(xml_text or "")
        except ElementTree.ParseError as exc:
            raise FeedFetchError(
                f"'{source.name}': feed is not valid XML: {exc}"
            ) from exc

        items: List[ContentItem] = []
        for entry in root.findall("atom:entry", NAMESPACES):
            item = self._parse_entry(entry, source, channel_id)
            if item is not None:
                items.append(item)

        items.sort(key=lambda item: item.published_at, reverse=True)
        return items

    def _parse_entry(
        self, entry, source: YouTubeSource, channel_id
    ) -> Optional[ContentItem]:
        """Turn one ``<entry>`` into a ``ContentItem``, or None if unusable."""
        video_id = _text(entry.find("yt:videoId", NAMESPACES))
        published_raw = _text(entry.find("atom:published", NAMESPACES))
        if not video_id or not published_raw:
            return None

        try:
            published_at = parse_iso(published_raw)
        except ValueError:
            return None

        group = entry.find("media:group", NAMESPACES)
        description = ""
        thumbnail = None
        if group is not None:
            description = _text(group.find("media:description", NAMESPACES)) or ""
            thumb = group.find("media:thumbnail", NAMESPACES)
            if thumb is not None:
                thumbnail = thumb.get("url")

        author = entry.find("atom:author", NAMESPACES)
        author_name = (
            _text(author.find("atom:name", NAMESPACES)) if author is not None else None
        )
        author_url = (
            _text(author.find("atom:uri", NAMESPACES)) if author is not None else None
        )

        entry_channel_id = _text(entry.find("yt:channelId", NAMESPACES)) or channel_id

        return ContentItem(
            platform=PLATFORM,
            content_id=video_id,
            title=_text(entry.find("atom:title", NAMESPACES)) or video_id,
            url=_link(entry) or f"https://www.youtube.com/watch?v={video_id}",
            published_at=published_at,
            source_name=source.name,
            relationship=source.relationship,
            categories=list(source.categories),
            description=description,
            author_name=source.attribution or author_name,
            author_url=author_url,
            thumbnail_url=thumbnail,
            channel_id=entry_channel_id,
        )


def _text(node) -> Optional[str]:
    """Stripped text of an element, or None."""
    if node is None or node.text is None:
        return None
    return node.text.strip()


def _link(entry) -> Optional[str]:
    """The entry's alternate link, which is the watch URL."""
    for link in entry.findall("atom:link", NAMESPACES):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href")
    return None
