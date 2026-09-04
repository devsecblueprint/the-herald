"""
A pre-wired polling service built entirely from test doubles.

Every collaborator is injected in production too, so this harness assembles
exactly the same objects -- only the table, the clock, the feeds and the
Discord transport are fakes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config.youtube import YouTubeConfig, load_config
from app.models.youtube import SourceFetchResult
from app.repositories.youtube.processing import ProcessingRepository
from app.repositories.youtube.roster import RosterRepository
from app.services.youtube.polling import YouTubePollingService
from app.services.youtube.publishing import YouTubePublishingService
from tests.youtube.fakes import (
    FakeClock,
    FakeTable,
    RecordingTransport,
    StubDetector,
    StubIngestion,
    make_item,
)

CHANNEL_ID = "123456789012345678"


def build_config(sources=None, **overrides) -> YouTubeConfig:
    """Build a validated config from an inline mapping."""
    document = {
        "youtube": {
            "enabled": True,
            "poll_interval_minutes": 15,
            "exclude_shorts": True,
            "discord_channel_name": "content-corner",
            "discord_channel_id": CHANNEL_ID,
            "youtube_sources": (
                sources
                if sources is not None
                else [
                    {
                        "name": "Damien Burks",
                        "relationship": "COMMUNITY_PARTNER",
                        "channel": "@damienjburks",
                        "categories": ["cloud-security"],
                    }
                ]
            ),
        }
    }
    document["youtube"].update(overrides)
    return load_config(document, env={})


@dataclass
class Harness:
    """A polling service and every double it was built from."""

    # One attribute per injected collaborator, so a test can reach any of them.
    # pylint: disable=too-many-instance-attributes

    config: YouTubeConfig
    table: FakeTable
    clock: FakeClock
    ingestion: StubIngestion
    detector: StubDetector
    transport: RecordingTransport
    repository: ProcessingRepository
    roster: RosterRepository
    publishing: YouTubePublishingService
    polling: YouTubePollingService
    published: Dict[str, List[Any]] = field(default_factory=dict)

    def publish(
        self, source_key: str, entries, channel_id: str = "UCxxxxxxxxxxxxxxxxxxxxxx"
    ):
        """
        Register what a source's feed currently contains.

        Each entry is ``(video_id, published_at)`` or a ``ContentItem``.
        """
        source = self._source(source_key)
        items = []
        for entry in entries:
            if isinstance(entry, tuple):
                video_id, published = entry
                items.append(
                    make_item(
                        video_id=video_id,
                        published_at=published,
                        source_name=source.name,
                        relationship=source.relationship,
                        channel_id=channel_id,
                    )
                )
            else:
                items.append(entry)
        items.sort(key=lambda item: item.published_at, reverse=True)
        self.ingestion.set(
            source_key,
            SourceFetchResult(
                source_key=source_key,
                source_name=source.name,
                channel_id=channel_id,
                feed_url=f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
                items=items,
            ),
        )
        return items

    def fail(self, source_key: str, error: Exception) -> None:
        """Make a source's feed fetch raise."""
        self.ingestion.set(source_key, error)

    def _source(self, source_key: str):
        for source in self.config.sources:
            if source.key == source_key:
                return source
        raise KeyError(f"No configured source {source_key}")

    def watermarks(self) -> Dict[str, str]:
        """The roster's watermarks as stored."""
        item = self.table.record("youtube-sources") or {}
        return dict(item.get("watermarks") or {})

    def revision(self) -> Optional[int]:
        """The roster's current revision, or None if it was never written."""
        item = self.table.record("youtube-sources")
        return int(item["revision"]) if item else None

    def run(self):
        """Run one poll."""
        return self.polling.run()


# pylint: disable=too-many-arguments,too-many-positional-arguments
def build_harness(
    config: Optional[YouTubeConfig] = None,
    now: Optional[datetime] = None,
    ttl_days: int = 35,
    stale_claim_minutes: int = 60,
    post_delay_seconds: float = 0.0,
    table: Optional[FakeTable] = None,
    clock: Optional[FakeClock] = None,
) -> Harness:
    """
    Assemble a polling service from doubles.

    Pass an existing ``table`` and ``clock`` to simulate a redeploy with a
    changed configuration against the state a previous poll left behind.
    """
    config = config or build_config()
    clock = clock or (FakeClock(now) if now else FakeClock())
    table = table if table is not None else FakeTable()

    repository = ProcessingRepository(
        table,
        ttl_days=ttl_days,
        stale_claim_minutes=stale_claim_minutes,
        clock=clock,
    )
    roster = RosterRepository(table, clock=clock)
    ingestion = StubIngestion()
    detector = StubDetector()
    transport = RecordingTransport()

    publishing = YouTubePublishingService(
        repository=repository,
        classifier=detector,
        transport=transport,
        channel_id=config.discord_channel_id,
        message_style=config.message_style,
        post_delay_seconds=post_delay_seconds,
        sleeper=lambda _seconds: None,
        clock=clock,
    )
    polling = YouTubePollingService(
        config=config,
        ingestion=ingestion,
        publishing=publishing,
        roster_repository=roster,
        clock=clock,
    )

    return Harness(
        config=config,
        table=table,
        clock=clock,
        ingestion=ingestion,
        detector=detector,
        transport=transport,
        repository=repository,
        roster=roster,
        publishing=publishing,
        polling=polling,
    )
