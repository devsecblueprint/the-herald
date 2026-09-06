"""
One video, from claim to announcement.

``YouTubePublishingService`` owns the whole per-video workflow -- claim,
classify, publish, record -- and nothing wider. It never sees the roster,
the watermark or the other sources, so the rules that keep a single video
correct all live in one place:

* the claim comes first, so two polls cannot announce the same upload;
* an undecidable video is retried, never guessed at;
* a confirmed delivery failure releases the claim, and an ambiguous one
  keeps it, because a missing audit record is cheaper than a duplicate
  announcement.

The formatting is deterministic and template-driven -- no AI summarisation
in v1.
"""

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.clients.discord import DiscordTransport
from app.config.youtube import YouTubeSource
from app.errors import (
    AmbiguousDeliveryError,
    ClassificationError,
    DistributionError,
    RepositoryError,
)
from app.models.youtube import (
    SKIP_REASON_SHORT,
    AnnouncedItem,
    ContentItem,
    DeliveryReceipt,
    SourceFailure,
)
from app.repositories.youtube.processing import ProcessingRepository
from app.utils.clock import to_iso, utcnow
from app.utils.logging import EventLogger

# Announcements never ping a channel or @everyone. When a notify role is
# configured it is allow-listed explicitly, so that role -- and only that
# role -- is actually pinged.
NO_MENTIONS = {"parse": []}


def _mentions_for(role_id: str) -> Dict[str, Any]:
    """The ``allowed_mentions`` that ping only the notify role, if any."""
    if role_id:
        return {"parse": [], "roles": [str(role_id)]}
    return NO_MENTIONS


def _announcement_content(item: ContentItem, role_id: str) -> str:
    """
    Build the announcement text.

    Deliberately plain: no custom embed and the bare video URL on its own
    line, so Discord renders YouTube's native player card with the channel's
    own thumbnail. The optional role ping leads on its own line.
    """
    lines = []
    if role_id:
        lines.append(f"<@&{role_id}>")
    lines.append(f"New video from **{item.source_name}**.")
    lines.append(f"Check it out on YouTube: {item.url}")
    return "\n".join(lines)


def build_message(
    item: ContentItem, style: str = "embed", notify_role_id: str = ""
) -> Dict[str, Any]:
    """
    Build the Discord message payload for one content item.

    No custom embed is attached: the bare YouTube URL lets Discord render
    YouTube's own player card and thumbnail. The ``style`` argument is kept
    for signature compatibility but no longer changes the output.

    When ``notify_role_id`` is set, the role is mentioned on its own line and
    allow-listed in ``allowed_mentions`` so the ping fires.
    """
    return {
        "content": _announcement_content(item, notify_role_id),
        "allowed_mentions": _mentions_for(notify_role_id),
    }


@dataclass(frozen=True)
class PublishOutcome:
    """What became of one video, in terms the poll can add up."""

    settled: bool
    duplicate: bool = False
    skipped_short: bool = False
    announced: Optional[AnnouncedItem] = None
    failure: Optional[SourceFailure] = None


class YouTubePublishingService:
    """Takes one video from claim to announcement."""

    # Every collaborator is injected so the suite runs without AWS or a network.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        repository: ProcessingRepository,
        classifier,
        transport: DiscordTransport,
        channel_id: str,
        message_style: str = "embed",
        post_delay_seconds: float = 0.0,
        notify_role_id: str = "",
        sleeper=time.sleep,
        clock=utcnow,
        event_logger: Optional[EventLogger] = None,
    ):
        self.repository = repository
        self.classifier = classifier
        self.transport = transport
        self.channel_id = str(channel_id)
        self.message_style = message_style
        self.post_delay_seconds = post_delay_seconds
        self.notify_role_id = str(notify_role_id or "")
        self.sleeper = sleeper
        self.clock = clock
        self.events = event_logger or EventLogger(__name__)
        self._has_posted = False

    @property
    def ttl_days(self) -> int:
        """How long a per-video record is kept."""
        return self.repository.ttl_days

    @property
    def detector_name(self) -> str:
        """Which Shorts detector is wired in, for the health endpoint."""
        return getattr(self.classifier, "name", "unknown")

    def publish(self, item: ContentItem, source: YouTubeSource) -> PublishOutcome:
        """
        Take one video from claim to announcement.

        Returns:
            A ``PublishOutcome`` whose ``settled`` flag says whether the
            video reached a terminal state and no longer holds the source's
            watermark back.
        """
        content_id = item.dedupe_key

        try:
            claim = self.repository.claim(item, self.channel_id)
        except RepositoryError as exc:
            return PublishOutcome(
                settled=False, failure=_failure("deduplication", exc, source, item)
            )

        if not claim.claimed:
            self.events.debug(
                "youtube.video.duplicate_skipped", video_id=item.content_id
            )
            return PublishOutcome(settled=claim.is_settled, duplicate=True)

        self.events.event(
            "youtube.video.claimed",
            video_id=item.content_id,
            discord_channel_id=self.channel_id,
            published_at=to_iso(item.published_at),
            ttl=self.repository.ttl_at(self.clock()),
        )

        classified = self._classify(item, source, content_id)
        if classified is not None:
            return classified

        return self._announce(item, source, content_id)

    # -- classification ----------------------------------------------------

    def _classify(
        self, item: ContentItem, source: YouTubeSource, content_id: str
    ) -> Optional[PublishOutcome]:
        """
        Decide Short vs long-form.

        Returns:
            An outcome when the video is settled here (skipped) or cannot be
            decided, or None when it should go on to be announced.
        """
        try:
            verdict = self.classifier.classify(item)
        except ClassificationError as exc:
            self.events.warning(
                "youtube.video.classification_failed",
                video_id=item.content_id,
                error=str(exc),
            )
            self._release(content_id)
            return PublishOutcome(
                settled=False, failure=_failure("classification", exc, source, item)
            )

        self.events.debug(
            "youtube.video.classified",
            video_id=item.content_id,
            is_short=verdict.is_short,
            detector=verdict.detector,
        )

        if not verdict.is_short:
            return None

        reason = verdict.reason or SKIP_REASON_SHORT
        try:
            self.repository.mark_skipped(content_id, reason)
        except RepositoryError as exc:
            return PublishOutcome(
                settled=False, failure=_failure("state_update", exc, source, item)
            )

        self.events.event(
            "youtube.video.skipped", video_id=item.content_id, reason=reason
        )
        return PublishOutcome(settled=True, skipped_short=True)

    # -- announcement ------------------------------------------------------

    def _announce(
        self, item: ContentItem, source: YouTubeSource, content_id: str
    ) -> PublishOutcome:
        """Post to Discord and record the message id."""
        try:
            self.repository.mark_posting(content_id)
        except RepositoryError as exc:
            # Nothing was sent and nothing was deleted: the record is no
            # longer ours to advance.
            self.events.warning(
                "youtube.video.mark_posting_failed",
                video_id=item.content_id,
                error=str(exc),
            )
            return PublishOutcome(
                settled=False, failure=_failure("state_update", exc, source, item)
            )

        try:
            receipt = self.deliver(item)
        except AmbiguousDeliveryError as exc:
            # The message may be live. A missing record is cheaper than a
            # duplicate announcement, so the claim is kept in POSTING.
            self.events.error(
                "youtube.video.distribution_ambiguous",
                video_id=item.content_id,
                error=str(exc),
            )
            return PublishOutcome(
                settled=False,
                failure=_failure("distribution_ambiguous", exc, source, item),
            )
        except DistributionError as exc:
            self.events.warning(
                "youtube.video.distribution_failed",
                video_id=item.content_id,
                error=str(exc),
            )
            self._release(content_id)
            return PublishOutcome(
                settled=False, failure=_failure("distribution", exc, source, item)
            )

        self.events.event(
            "youtube.discord.posted",
            content_id=content_id,
            discord_channel_id=receipt.channel_id,
            discord_message_id=receipt.message_id,
        )
        announced = AnnouncedItem(
            video_id=item.content_id,
            source_name=item.source_name,
            youtube_channel_id=item.channel_id,
            published_at=item.published_at,
            discord_channel_id=receipt.channel_id,
            discord_message_id=receipt.message_id,
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
            return PublishOutcome(
                settled=False,
                announced=announced,
                failure=_failure("state_update", exc, source, item),
            )

        self.events.event(
            "youtube.video.distributed",
            video_id=item.content_id,
            discord_message_id=receipt.message_id,
            posted_at=to_iso(receipt.posted_at),
            ttl=self.repository.ttl_at(receipt.posted_at),
        )
        return PublishOutcome(settled=True, announced=announced)

    def deliver(self, item: ContentItem) -> DeliveryReceipt:
        """
        Format one item and hand it to the Discord transport.

        Raises:
            DistributionError: On a confirmed failure; the claim is released.
            AmbiguousDeliveryError: When the outcome is unknown; the claim is kept.
        """
        if self._has_posted and self.post_delay_seconds > 0:
            self.sleeper(self.post_delay_seconds)

        message_id = self.transport.send(
            self.channel_id,
            build_message(item, self.message_style, self.notify_role_id),
        )
        self._has_posted = True

        return DeliveryReceipt(
            channel_id=self.channel_id,
            message_id=message_id,
            posted_at=self.clock(),
        )

    def _release(self, content_id: str) -> None:
        """Give up a claim, tolerating a repository that is misbehaving."""
        try:
            self.repository.release(content_id)
        except RepositoryError as exc:
            self.events.warning(
                "youtube.video.release_failed", video_id=content_id, error=str(exc)
            )


def _failure(
    stage: str, exc: Exception, source: YouTubeSource, item: ContentItem
) -> SourceFailure:
    """Build a SourceFailure attributed to a source and a video."""
    return SourceFailure(
        stage=stage,
        error=str(exc),
        source_name=source.name,
        source_key=source.key,
        video_id=item.content_id,
    )
