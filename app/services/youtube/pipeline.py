"""
The poll itself.

``YouTubePipeline`` is the only component that knows about all the stages,
and the only place that catches exceptions. An unresolvable handle, a
failing feed, a rejected Discord post or a throttled DynamoDB write is
recorded as a ``SourceFailure`` and the poll continues with the next source.

There is no announcement cap: if a partner has a busy month, every one of
their long-form videos is announced.
"""

import threading
from datetime import datetime
from typing import Dict, List, Optional

from app.services.youtube.clock import to_iso, utcnow
from app.services.youtube.config import YouTubeConfig, YouTubeSource
from app.services.youtube.errors import (
    AmbiguousDeliveryError,
    ClassificationError,
    DistributionError,
    RepositoryError,
    RosterError,
    RosterWriteConflict,
    YouTubeError,
)
from app.services.youtube.logging_utils import EventLogger
from app.services.youtube.models import (
    SKIP_REASON_SHORT,
    AnnouncedItem,
    ContentItem,
    PollResult,
    SourceFailure,
)


class YouTubePipeline:
    """Runs one poll: ingest, dedupe, classify, announce, advance."""

    # Every stage is injected so the suite can run without AWS or a network.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        config: YouTubeConfig,
        ingestion,
        distribution,
        repository,
        roster_repository,
        shorts_detector,
        clock=utcnow,
        event_logger: Optional[EventLogger] = None,
    ):
        self.config = config
        self.ingestion = ingestion
        self.distribution = distribution
        self.repository = repository
        self.roster = roster_repository
        self.shorts = shorts_detector
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
        if not self._lock.acquire(blocking=False):  # pylint: disable=consider-using-with
            self.events.event("youtube.poll.already_running")
            return PollResult(
                skipped=True,
                sources_configured=len(self.config.sources),
                discord_channel_id=self.config.discord_channel_id,
                ttl_days=self.repository.ttl_days,
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
            "shorts_detector": getattr(self.shorts, "name", "unknown"),
            "discord_channel_id": self.config.discord_channel_id,
            "discord_channel_name": self.config.discord_channel_name,
            "ttl_days": self.repository.ttl_days,
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
            ttl_days=self.repository.ttl_days,
        )

        if not self.config.enabled:
            result.disabled = True
            return self._finish(result)

        self.events.event("youtube.poll.started", sources_configured=len(self.config.sources))

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
            self._poll_source(source, roster.watermarks.get(source.key), watermarks, result)

        self._save_roster(watermarks, roster.revision, result)
        return self._finish(result)

    def _poll_source(
        self,
        source: YouTubeSource,
        watermark: Optional[datetime],
        watermarks: Dict[str, datetime],
        result: PollResult,
    ) -> None:
        """Fetch one source and process whatever is new."""
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
            if not self._process(item, source, result):
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

    # -- one video ---------------------------------------------------------

    def _process(self, item: ContentItem, source: YouTubeSource, result: PollResult) -> bool:
        """
        Take one video from claim to announcement.

        Returns:
            True when the video reached a terminal state and no longer holds
            the source's watermark back.
        """
        content_id = item.dedupe_key

        try:
            claim = self.repository.claim(item, self.config.discord_channel_id)
        except RepositoryError as exc:
            result.add_failure(self._failure("deduplication", exc, source, item))
            return False

        if not claim.claimed:
            result.duplicates_skipped += 1
            self.events.debug("youtube.video.duplicate_skipped", video_id=item.content_id)
            return claim.is_settled

        self.events.event(
            "youtube.video.claimed",
            video_id=item.content_id,
            discord_channel_id=self.config.discord_channel_id,
            published_at=to_iso(item.published_at),
            ttl=self.repository.ttl_at(self.clock()),
        )

        classified = self._classify(item, source, result, content_id)
        if classified is None:
            return False
        if classified:
            return True

        return self._announce(item, source, result, content_id)

    def _classify(self, item, source, result, content_id) -> Optional[bool]:
        """
        Decide Short vs long-form.

        Returns:
            True if the video was skipped, False if it should be announced,
            or None if it could not be decided (the claim is released and
            the video is retried next poll).
        """
        try:
            verdict = self.shorts.classify(item)
        except ClassificationError as exc:
            self.events.warning(
                "youtube.video.classification_failed", video_id=item.content_id, error=str(exc)
            )
            self._release(content_id)
            result.add_failure(self._failure("classification", exc, source, item))
            return None

        self.events.debug(
            "youtube.video.classified",
            video_id=item.content_id,
            is_short=verdict.is_short,
            detector=verdict.detector,
        )

        if not verdict.is_short:
            return False

        reason = verdict.reason or SKIP_REASON_SHORT
        try:
            self.repository.mark_skipped(content_id, reason)
        except RepositoryError as exc:
            result.add_failure(self._failure("state_update", exc, source, item))
            return None

        result.shorts_skipped += 1
        self.events.event("youtube.video.skipped", video_id=item.content_id, reason=reason)
        return True

    def _announce(self, item, source, result, content_id) -> bool:
        """Post to Discord and record the message id."""
        try:
            self.repository.mark_posting(content_id)
        except RepositoryError as exc:
            # Nothing was sent and nothing was deleted: the record is no
            # longer ours to advance.
            self.events.warning(
                "youtube.video.mark_posting_failed", video_id=item.content_id, error=str(exc)
            )
            result.add_failure(self._failure("state_update", exc, source, item))
            return False

        try:
            receipt = self.distribution.distribute(item)
        except AmbiguousDeliveryError as exc:
            # The message may be live. A missing record is cheaper than a
            # duplicate announcement, so the claim is kept in POSTING.
            self.events.error(
                "youtube.video.distribution_ambiguous", video_id=item.content_id, error=str(exc)
            )
            result.add_failure(self._failure("distribution_ambiguous", exc, source, item))
            return False
        except DistributionError as exc:
            self.events.warning(
                "youtube.video.distribution_failed", video_id=item.content_id, error=str(exc)
            )
            self._release(content_id)
            result.add_failure(self._failure("distribution", exc, source, item))
            return False

        self.events.event(
            "youtube.discord.posted",
            content_id=content_id,
            discord_channel_id=receipt.channel_id,
            discord_message_id=receipt.message_id,
        )
        result.announcements_published += 1
        result.announced.append(
            AnnouncedItem(
                video_id=item.content_id,
                source_name=item.source_name,
                youtube_channel_id=item.channel_id,
                published_at=item.published_at,
                discord_channel_id=receipt.channel_id,
                discord_message_id=receipt.message_id,
            )
        )

        try:
            self.repository.mark_distributed(content_id, receipt)
        except RepositoryError as exc:
            # The post happened; only the bookkeeping failed. Keep the claim
            # so it can never be announced twice, and alert on this.
            self.events.error(
                "youtube.video.state_update_failed",
                video_id=item.content_id,
                discord_message_id=receipt.message_id,
                error=str(exc),
            )
            result.add_failure(self._failure("state_update", exc, source, item))
            return False

        self.events.event(
            "youtube.video.distributed",
            video_id=item.content_id,
            discord_message_id=receipt.message_id,
            posted_at=to_iso(receipt.posted_at),
            ttl=self.repository.ttl_at(receipt.posted_at),
        )
        return True

    # -- helpers -----------------------------------------------------------

    def _release(self, content_id: str) -> None:
        """Give up a claim, tolerating a repository that is misbehaving."""
        try:
            self.repository.release(content_id)
        except RepositoryError as exc:
            self.events.warning(
                "youtube.video.release_failed", video_id=content_id, error=str(exc)
            )

    def _save_roster(self, watermarks, expected_revision: int, result: PollResult) -> None:
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

    @staticmethod
    def _failure(stage: str, exc: Exception, source: YouTubeSource, item: ContentItem):
        """Build a SourceFailure attributed to a source and a video."""
        return SourceFailure(
            stage=stage,
            error=str(exc),
            source_name=source.name,
            source_key=source.key,
            video_id=item.content_id,
        )

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
