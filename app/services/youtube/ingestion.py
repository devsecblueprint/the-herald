"""
YouTube ingestion: a channel's public Atom feed in, ``ContentItem``s out.

The feed is unauthenticated and quota-free, which is why it is the primary
source rather than the Data API. It carries no duration and no format flag,
so Shorts are dealt with separately -- see ``shorts.py``.
"""

from typing import List, Optional
from xml.etree import ElementTree

from app.services.youtube.clock import parse_iso
from app.services.youtube.config import YouTubeSource
from app.services.youtube.errors import FeedFetchError
from app.services.youtube.http import HttpClient, HttpError
from app.services.youtube.models import ContentItem, SourceFetchResult

PLATFORM = "youtube"
FEED_BASE_URL = "https://www.youtube.com/feeds/videos.xml"

ATOM_NS = "http://www.w3.org/2005/Atom"
YT_NS = "http://www.youtube.com/xml/schemas/2015"
MEDIA_NS = "http://search.yahoo.com/mrss/"

NAMESPACES = {"atom": ATOM_NS, "yt": YT_NS, "media": MEDIA_NS}

MAX_DESCRIPTION_CHARS = 400


def feed_url_for(source: YouTubeSource, channel_id: Optional[str]) -> str:
    """Build the Atom feed URL for a source."""
    if source.is_playlist:
        return f"{FEED_BASE_URL}?playlist_id={source.reference.value}"
    if not channel_id:
        raise FeedFetchError(f"'{source.name}' has no resolved channel id")
    return f"{FEED_BASE_URL}?channel_id={channel_id}"


class YouTubeIngestionService:
    """Reads a channel or playlist feed and returns it newest-first."""

    platform = PLATFORM

    def __init__(self, http_client: HttpClient, resolver=None):
        self.http = http_client
        self.resolver = resolver

    def fetch(self, source: YouTubeSource) -> SourceFetchResult:
        """
        Fetch and parse one source's feed.

        Raises:
            ChannelResolutionError: If the configured handle cannot be resolved.
            FeedFetchError: If the feed cannot be fetched or parsed.
        """
        channel_id = None
        if not source.is_playlist:
            if self.resolver is None:
                raise FeedFetchError(f"'{source.name}' needs a resolver to find its channel id")
            channel_id = self.resolver.resolve(source.reference).channel_id

        url = feed_url_for(source, channel_id)

        try:
            response = self.http.get(url)
        except HttpError as exc:
            raise FeedFetchError(f"'{source.name}': feed fetch failed: {exc}") from exc

        if not response.ok:
            raise FeedFetchError(
                f"'{source.name}': feed returned HTTP {response.status_code}"
            )

        items = self.parse(response.text, source, channel_id)
        return SourceFetchResult(
            source_key=source.key,
            source_name=source.name,
            channel_id=channel_id,
            feed_url=url,
            items=items,
        )

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
            raise FeedFetchError(f"'{source.name}': feed is not valid XML: {exc}") from exc

        items: List[ContentItem] = []
        for entry in root.findall("atom:entry", NAMESPACES):
            item = self._parse_entry(entry, source, channel_id)
            if item is not None:
                items.append(item)

        items.sort(key=lambda item: item.published_at, reverse=True)
        return items

    def _parse_entry(self, entry, source: YouTubeSource, channel_id) -> Optional[ContentItem]:
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
        author_name = _text(author.find("atom:name", NAMESPACES)) if author is not None else None
        author_url = _text(author.find("atom:uri", NAMESPACES)) if author is not None else None

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
