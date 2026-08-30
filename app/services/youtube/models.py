"""
Platform-neutral data structures shared by ingestion and distribution.

``ContentItem`` is the contract between the two halves of the feature. A
future LinkedIn or podcast ingestion service emits the same object with a
different ``platform`` value and the Discord distributor needs no changes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.youtube.clock import to_iso

# Reference kinds, in the order they appear in the configuration docs.
KIND_HANDLE = "handle"
KIND_ID = "id"
KIND_USER = "user"
KIND_VANITY = "vanity"
KIND_PLAYLIST = "playlist"

# Per-video record states.
STATUS_PENDING = "PENDING"
STATUS_POSTING = "POSTING"
STATUS_POSTED = "POSTED"
STATUS_SKIPPED = "SKIPPED"

# A record in one of these states will never be processed again, so the
# watermark is free to move past the video it belongs to.
TERMINAL_STATUSES = frozenset({STATUS_POSTED, STATUS_SKIPPED, STATUS_POSTING})

SKIP_REASON_SHORT = "SHORT"
SKIP_REASON_LIVE = "LIVE"
SKIP_REASON_PREMIERE = "PREMIERE"


@dataclass(frozen=True)
class ChannelReference:
    """
    A parsed ``channel:`` value from the configuration.

    The ``kind`` is part of the identity: ``/user/foo`` and ``/c/foo`` are
    different YouTube namespaces that can point at different channels, so
    they must never share roster or cache state.
    """

    kind: str
    value: str

    @property
    def key(self) -> str:
        """Stable identity used for roster entries and cache keys."""
        return f"{self.kind}:{self.value}"

    def __str__(self) -> str:
        return self.key


@dataclass
class ContentItem:
    """A single piece of publishable content, ready for distribution."""

    # pylint: disable=too-many-instance-attributes

    platform: str
    content_id: str
    title: str
    url: str
    published_at: datetime
    source_name: str
    relationship: str
    categories: List[str] = field(default_factory=list)
    description: str = ""
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    channel_id: Optional[str] = None

    @property
    def dedupe_key(self) -> str:
        """Partition key for the shared dedupe table: ``<platform>#<id>``."""
        return f"{self.platform}#{self.content_id}"


@dataclass
class SourceFetchResult:
    """Everything one ingestion pass learned about one configured source."""

    source_key: str
    source_name: str
    channel_id: Optional[str]
    feed_url: str
    items: List[ContentItem] = field(default_factory=list)


@dataclass(frozen=True)
class SourceFailure:
    """One thing that went wrong, attributed to a stage and a source."""

    stage: str
    error: str
    source_name: Optional[str] = None
    source_key: Optional[str] = None
    video_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Render for the trigger endpoint's JSON body."""
        payload = {"stage": self.stage, "error": self.error}
        for name in ("source_name", "source_key", "video_id"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload


@dataclass(frozen=True)
class DeliveryReceipt:
    """Proof that a message landed in Discord."""

    channel_id: str
    message_id: str
    posted_at: datetime


@dataclass(frozen=True)
class AnnouncedItem:
    """One announcement made during a poll, reported back to the caller."""

    video_id: str
    source_name: str
    youtube_channel_id: Optional[str]
    published_at: datetime
    discord_channel_id: str
    discord_message_id: str

    def to_dict(self) -> Dict[str, Any]:
        """Render for the trigger endpoint's JSON body."""
        return {
            "video_id": self.video_id,
            "source_name": self.source_name,
            "youtube_channel_id": self.youtube_channel_id,
            "published_at": to_iso(self.published_at),
            "discord_channel_id": self.discord_channel_id,
            "discord_message_id": self.discord_message_id,
        }


@dataclass
class PollResult:
    """The outcome of a single poll, and the body of ``POST /trigger/youtube``."""

    # Every counter the trigger endpoint reports.
    # pylint: disable=too-many-instance-attributes

    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: int = 0
    sources_configured: int = 0
    sources_checked: int = 0
    sources_onboarded: int = 0
    videos_in_feeds: int = 0
    new_videos: int = 0
    shorts_skipped: int = 0
    announcements_published: int = 0
    duplicates_skipped: int = 0
    ttl_days: int = 35
    discord_channel_id: Optional[str] = None
    failures: List[SourceFailure] = field(default_factory=list)
    announced: List[AnnouncedItem] = field(default_factory=list)
    skipped: bool = False
    disabled: bool = False

    @property
    def status(self) -> str:
        """``already_running``, ``completed_with_errors`` or ``ok``."""
        if self.skipped:
            return "already_running"
        if self.disabled:
            return "disabled"
        if self.failures:
            return "completed_with_errors"
        return "ok"

    @property
    def failed_sources(self) -> int:
        """How many distinct sources contributed at least one failure."""
        return len({f.source_key for f in self.failures if f.source_key})

    def add_failure(self, failure: SourceFailure) -> None:
        """Record a failure without interrupting the poll."""
        self.failures.append(failure)

    def to_dict(self) -> Dict[str, Any]:
        """Render for the trigger endpoint's JSON body."""
        return {
            "status": self.status,
            "started_at": to_iso(self.started_at) if self.started_at else None,
            "finished_at": to_iso(self.finished_at) if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "sources_configured": self.sources_configured,
            "sources_checked": self.sources_checked,
            "sources_onboarded": self.sources_onboarded,
            "videos_in_feeds": self.videos_in_feeds,
            "new_videos": self.new_videos,
            "shorts_skipped": self.shorts_skipped,
            "announcements_published": self.announcements_published,
            "duplicates_skipped": self.duplicates_skipped,
            "ttl_days": self.ttl_days,
            "discord_channel_id": self.discord_channel_id,
            "failures": [f.to_dict() for f in self.failures],
            "announced": [a.to_dict() for a in self.announced],
        }
