"""
The poll itself.

``YouTubePollingService`` does four things and delegates the rest: it loads
the roster, asks ingestion what each source has published, hands anything
new to publishing, and advances the source's watermark. It is also the only
place that catches exceptions -- an unresolvable handle, a failing feed, a
rejected Discord post or a throttled DynamoDB write is recorded as a
``SourceFailure`` and the poll carries on with the next source.

There is no announcement cap: if a partner has a busy month, every one of
their long-form videos is announced.
"""

import threading
from datetime import datetime
from typing import Dict, List, Optional

from app.config.youtube import YouTubeConfig, YouTubeSource
from app.errors import RosterError, RosterWriteConflict, YouTubeError
from app.models.youtube import ContentItem, PollResult, SourceFailure
from app.services.youtube.publishing import YouTubePublishingService
from app.utils.clock import to_iso, utcnow
from app.utils.logging import EventLogger


class YouTubePollingService:
    """Runs one poll: load sources, ingest, publish, advance."""

    # Every collaborator is injected so the suite runs without AWS or a network.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        config: YouTubeConfig,
        ingestion,
        publishing: YouTubePublishingService,
        roster_repository,
        clock=utcnow,
        event_logger: Optional[EventLogger] = None,
    ):
        self.config = config
        self.ingestion = ingestion
        self.publishing = publishing
        self.roster = roster_repository
        self.clock = clock
        self.events = event_logger or EventLogger(__name__)
        self.last_result: Optional[PollResult] = None
        self._lock = threading.Lock()

    # -- entry points ------------------------------------------------------

    def run(self) -> PollResult:
        """
        Run one poll, unless one is already in flight.

        The scheduled job and the manual trigger share this lock, so they
        can never run concurrently even though they arrive by different
        routes.
        """
        # Deliberately not a `with`: a poll already in flight is reported,
        # not waited for.
        if not self._lock.acquire(
            blocking=False
        ):  # pylint: disable=consider-using-with
            self.events.event("youtube.poll.already_running")
            return PollResult(
                skipped=True,
                sources_configured=len(self.config.sources),
                discord_channel_id=self.config.discord_channel_id,
                ttl_days=self.publishing.ttl_days,
            )
        try:
            return self._run()
        finally:
            self._lock.release()

    @property
    def is_running(self) -> bool:
        """True while a poll is in flight."""
        return self._lock.locked()

    def health(self) -> Dict:
        """Configuration, configured sources and the last run summary."""
        return {
            "status": "healthy",
            "enabled": self.config.enabled,
            "running": self.is_running,
            "poll_interval_minutes": self.config.poll_interval_minutes,
            "exclude_shorts": self.config.exclude_shorts,
            "message_style": self.config.message_style,
            "shorts_detector": self.publishing.detector_name,
            "discord_channel_id": self.config.discord_channel_id,
            "discord_channel_name": self.config.discord_channel_name,
            "ttl_days": self.publishing.ttl_days,
            "sources_configured": len(self.config.sources),
            "sources": [
                {
                    "name": source.name,
                    "relationship": source.relationship,
                    "source_key": source.key,
                    "categories": list(source.categories),
                }
                for source in self.config.sources
            ],
            "last_run": self.last_result.to_dict() if self.last_result else None,
        }

    # -- the poll ----------------------------------------------------------

    def _run(self) -> PollResult:
        """Poll every configured source once."""
        started_at = self.clock()
        result = PollResult(
            started_at=started_at,
            sources_configured=len(self.config.sources),
            discord_channel_id=self.config.discord_channel_id,
            ttl_days=self.publishing.ttl_days,
        )

        if not self.config.enabled:
            result.disabled = True
            return self._finish(result)

        self.events.event(
            "youtube.poll.started", sources_configured=len(self.config.sources)
        )

        try:
            roster = self.roster.load()
        except RosterError as exc:
            # Without watermarks there is no safe way to decide what is new,
            # so the poll stops here rather than posting anything.
            result.add_failure(SourceFailure(stage="roster", error=str(exc)))
            return self._finish(result)

        for source_key, value in roster.unreadable:
            self.events.warning(
                "youtube.roster.unreadable_entry", source_key=source_key, value=value
            )

        configured_keys = set(self.config.source_keys)
        removed = sorted(set(roster.watermarks) - configured_keys)
        if removed:
            self.events.event("youtube.roster.sources_removed", sources=removed)

        watermarks: Dict[str, datetime] = {}
        for source in self.config.sources:
            self._poll_source(
                source, roster.watermarks.get(source.key), watermarks, result
            )

        self._save_roster(watermarks, roster.revision, result)
        return self._finish(result)

    def _poll_source(
        self,
        source: YouTubeSource,
        watermark: Optional[datetime],
        watermarks: Dict[str, datetime],
        result: PollResult,
    ) -> None:
        """Fetch one source and hand whatever is new to publishing."""
        try:
            fetched = self.ingestion.fetch(source)
        except YouTubeError as exc:
            self.events.error(
                "youtube.source.fetch_failed",
                source_name=source.name,
                source_key=source.key,
                error=str(exc),
            )
            result.add_failure(
                SourceFailure(
                    stage="ingestion",
                    error=str(exc),
                    source_name=source.name,
                    source_key=source.key,
                )
            )
            # A transient YouTube outage must not look like a removal.
            if watermark is not None:
                watermarks[source.key] = watermark
            return

        result.sources_checked += 1
        result.videos_in_feeds += len(fetched.items)
        self.events.event(
            "youtube.source.fetched",
            source_name=source.name,
            source_key=source.key,
            channel_id=fetched.channel_id,
            videos_in_feed=len(fetched.items),
        )

        if watermark is None:
            watermarks[source.key] = self._onboard(source, fetched, result)
            return

        # Items arrive newest-first, and that is the order they are announced.
        candidates: List[ContentItem] = [
            item for item in fetched.items if item.published_at > watermark
        ]
        result.new_videos += len(candidates)
        self.events.event(
            "youtube.source.evaluated",
            source_name=source.name,
            videos_in_feed=len(fetched.items),
            new_videos=len(candidates),
            watermark=to_iso(watermark),
        )

        settled = True
        for item in candidates:
            if not self._publish(item, source, result):
                settled = False

        # All-or-nothing: the watermark moves only when every video in this
        # batch reached a terminal state, and then straight to the newest.
        if candidates and settled:
            advanced = max(item.published_at for item in candidates)
            watermarks[source.key] = advanced
            self.events.event(
                "youtube.source.watermark_advanced",
                source_name=source.name,
                watermark=to_iso(advanced),
            )
        else:
            watermarks[source.key] = watermark

    def _publish(
        self, item: ContentItem, source: YouTubeSource, result: PollResult
    ) -> bool:
        """
        Publish one video and fold its outcome into the poll summary.

        Returns:
            True when the video reached a terminal state and no longer holds
            the source's watermark back.
        """
        outcome = self.publishing.publish(item, source)

        if outcome.duplicate:
            result.duplicates_skipped += 1
        if outcome.skipped_short:
            result.shorts_skipped += 1
        if outcome.announced is not None:
            result.announcements_published += 1
            result.announced.append(outcome.announced)
        if outcome.failure is not None:
            result.add_failure(outcome.failure)

        return outcome.settled

    def _onboard(self, source: YouTubeSource, fetched, result: PollResult) -> datetime:
        """
        Record where a newly configured partner starts. Announce nothing.

        The watermark is now, or the newest video already on the channel if
        that is later, so a host clock running behind YouTube's cannot let
        back catalogue slip through.
        """
        now = self.clock()
        watermark = now
        if fetched.items:
            newest = max(item.published_at for item in fetched.items)
            watermark = max(now, newest)

        result.sources_onboarded += 1
        self.events.event(
            "youtube.source.onboarded",
            source_name=source.name,
            watermark=to_iso(watermark),
            videos_in_feed=len(fetched.items),
        )
        return watermark

    # -- helpers -----------------------------------------------------------

    def _save_roster(
        self, watermarks, expected_revision: int, result: PollResult
    ) -> None:
        """Rewrite the roster from the configuration, guarded on revision."""
        try:
            self.roster.save(watermarks, expected_revision)
        except RosterWriteConflict as exc:
            self.events.warning(
                "youtube.roster.write_conflict", expected_revision=expected_revision
            )
            result.add_failure(SourceFailure(stage="roster", error=str(exc)))
        except RosterError as exc:
            result.add_failure(SourceFailure(stage="roster", error=str(exc)))

    def _finish(self, result: PollResult) -> PollResult:
        """Stamp the timings and emit the completion event."""
        result.finished_at = self.clock()
        if result.started_at:
            result.duration_ms = int(
                (result.finished_at - result.started_at).total_seconds() * 1000
            )
        self.last_result = result

        if not result.disabled:
            self.events.event(
                "youtube.poll.completed",
                sources_configured=result.sources_configured,
                sources_checked=result.sources_checked,
                sources_onboarded=result.sources_onboarded,
                videos_in_feeds=result.videos_in_feeds,
                new_videos=result.new_videos,
                shorts_skipped=result.shorts_skipped,
                announcements_published=result.announcements_published,
                duplicates_skipped=result.duplicates_skipped,
                source_failures=len(result.failures),
                failed_sources=result.failed_sources,
                duration_ms=result.duration_ms,
            )
        return result
