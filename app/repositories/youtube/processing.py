"""
Per-video state in DynamoDB: the dedupe guard and the audit trail.

One ``youtube#<video id>`` item per video records which Discord message
announced it, and expires after 35 days. Every transition is a conditional
write, which is what stops two polls announcing the same upload twice.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional

from botocore.exceptions import BotoCoreError, ClientError

from app.errors import RepositoryError
from app.models.youtube import (
    STATUS_PENDING,
    STATUS_POSTED,
    STATUS_POSTING,
    STATUS_SKIPPED,
    TERMINAL_STATUSES,
    ContentItem,
    DeliveryReceipt,
)
from app.repositories.dynamodb import (
    DEFAULT_KEY_ATTRIBUTE,
    DEFAULT_TTL_ATTRIBUTE,
    is_condition_failure,
)
from app.utils.clock import to_iso, utcnow

DEFAULT_TTL_DAYS = 35
DEFAULT_STALE_CLAIM_MINUTES = 60


@dataclass(frozen=True)
class ClaimResult:
    """The outcome of trying to claim a video for announcement."""

    claimed: bool
    status: Optional[str] = None

    @property
    def is_settled(self) -> bool:
        """
        True when the existing record will never be processed again.

        ``POSTED`` and ``SKIPPED`` are done. ``POSTING`` is never reclaimed,
        so it is settled too -- holding the watermark for it would stall the
        source forever. A fresh ``PENDING`` belongs to a poll still in
        flight, so it is *not* settled and holds the watermark back.
        """
        return self.status in TERMINAL_STATUSES


class ProcessingRepository:
    """Per-video claim, state transitions and audit trail."""

    # Every dependency is injected so the suite can run without AWS.
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        table,
        key_attribute: str = DEFAULT_KEY_ATTRIBUTE,
        ttl_attribute: str = DEFAULT_TTL_ATTRIBUTE,
        ttl_days: int = DEFAULT_TTL_DAYS,
        stale_claim_minutes: int = DEFAULT_STALE_CLAIM_MINUTES,
        clock=utcnow,
    ):
        self.table = table
        self.key_attribute = key_attribute
        self.ttl_attribute = ttl_attribute
        self.ttl_days = ttl_days
        self.stale_claim_minutes = stale_claim_minutes
        self.clock = clock

    def ttl_at(self, moment: datetime) -> int:
        """Epoch seconds at which a record written at ``moment`` expires."""
        return int((moment + timedelta(days=self.ttl_days)).timestamp())

    def claim(self, item: ContentItem, discord_channel_id: str) -> ClaimResult:
        """
        Take ownership of a video by writing a ``PENDING`` record.

        The write is conditional on the record not existing, or on it being
        a stale ``PENDING`` claim left behind by a process that died. The
        loser of a race gets a ``ClaimResult`` describing what is already
        there.

        Raises:
            RepositoryError: If DynamoDB is unreachable or throttled.
        """
        now = self.clock()
        stale_cutoff = int(
            (now - timedelta(minutes=self.stale_claim_minutes)).timestamp()
        )
        record = {
            self.key_attribute: item.dedupe_key,
            "platform": item.platform,
            "video_id": item.content_id,
            "youtube_channel_id": item.channel_id,
            "source_name": item.source_name,
            "relationship": item.relationship,
            "title": item.title,
            "url": item.url,
            "categories": list(item.categories),
            "published_at": to_iso(item.published_at),
            "first_seen_at": to_iso(now),
            "first_seen_epoch": int(now.timestamp()),
            "discord_channel_id": discord_channel_id,
            "status": STATUS_PENDING,
            self.ttl_attribute: self.ttl_at(now),
        }
        record = {key: value for key, value in record.items() if value is not None}

        try:
            self.table.put_item(
                Item=record,
                ConditionExpression=(
                    "attribute_not_exists(#pk) OR "
                    "(#status = :pending AND #first_seen < :stale_cutoff)"
                ),
                ExpressionAttributeNames={
                    "#pk": self.key_attribute,
                    "#status": "status",
                    "#first_seen": "first_seen_epoch",
                },
                ExpressionAttributeValues={
                    ":pending": STATUS_PENDING,
                    ":stale_cutoff": stale_cutoff,
                },
            )
        except ClientError as exc:
            if is_condition_failure(exc):
                return ClaimResult(
                    claimed=False, status=self._status_of(item.dedupe_key)
                )
            raise RepositoryError(f"claim failed for {item.dedupe_key}: {exc}") from exc
        except BotoCoreError as exc:
            raise RepositoryError(f"claim failed for {item.dedupe_key}: {exc}") from exc

        return ClaimResult(claimed=True, status=STATUS_PENDING)

    def _status_of(self, content_id: str) -> Optional[str]:
        """Read the status of an existing record, or None if unreadable."""
        try:
            response = self.table.get_item(Key={self.key_attribute: content_id})
        except (ClientError, BotoCoreError):
            return None
        return (response.get("Item") or {}).get("status")

    def get(self, content_id: str) -> Optional[Mapping[str, Any]]:
        """Read a whole record, or None if it does not exist."""
        try:
            response = self.table.get_item(Key={self.key_attribute: content_id})
        except (ClientError, BotoCoreError) as exc:
            raise RepositoryError(f"read failed for {content_id}: {exc}") from exc
        return response.get("Item")

    def mark_posting(self, content_id: str) -> None:
        """
        Move a claim from ``PENDING`` to ``POSTING``.

        Issued immediately before the Discord request, so a crash mid-post
        leaves a record that is never reclaimed and never announced twice.

        Raises:
            RepositoryError: If the record is no longer ours to advance.
        """
        now = self.clock()
        self._update(
            content_id,
            update="SET #status = :posting, posting_at = :now",
            condition="attribute_exists(#pk) AND #status = :pending",
            names={"#pk": self.key_attribute, "#status": "status"},
            values={
                ":posting": STATUS_POSTING,
                ":pending": STATUS_PENDING,
                ":now": to_iso(now),
            },
            what="mark_posting",
        )

    def mark_distributed(self, content_id: str, receipt: DeliveryReceipt) -> None:
        """
        Record the Discord message that announced this video.

        Raises:
            RepositoryError: If the state write fails. The post already
                happened, so the caller must keep the claim.
        """
        self._update(
            content_id,
            update=(
                "SET #status = :posted, discord_channel_id = :channel, "
                "discord_message_id = :message, posted_at = :posted_at, #ttl = :ttl"
            ),
            condition="attribute_exists(#pk) AND #status = :posting",
            names={
                "#pk": self.key_attribute,
                "#status": "status",
                "#ttl": self.ttl_attribute,
            },
            values={
                ":posted": STATUS_POSTED,
                ":posting": STATUS_POSTING,
                ":channel": receipt.channel_id,
                ":message": receipt.message_id,
                ":posted_at": to_iso(receipt.posted_at),
                ":ttl": self.ttl_at(receipt.posted_at),
            },
            what="mark_distributed",
        )

    def mark_skipped(self, content_id: str, reason: str) -> None:
        """
        Record that a claimed video was deliberately not announced.

        Raises:
            RepositoryError: If the record is no longer ours.
        """
        now = self.clock()
        self._update(
            content_id,
            update=(
                "SET #status = :skipped, skip_reason = :reason, "
                "skipped_at = :now, #ttl = :ttl"
            ),
            condition="attribute_exists(#pk) AND #status = :pending",
            names={
                "#pk": self.key_attribute,
                "#status": "status",
                "#ttl": self.ttl_attribute,
            },
            values={
                ":skipped": STATUS_SKIPPED,
                ":pending": STATUS_PENDING,
                ":reason": reason,
                ":now": to_iso(now),
                ":ttl": self.ttl_at(now),
            },
            what="mark_skipped",
        )

    def release(self, content_id: str) -> None:
        """
        Give up a claim so the video is retried next poll.

        Called only by the owner of the claim, and only when the outcome is
        known: nothing was sent, or Discord confirmed a rejection. A
        ``POSTED`` or ``SKIPPED`` record is never deleted, and neither is a
        claim that some other poll has taken over.

        Raises:
            RepositoryError: If the delete fails for any other reason.
        """
        try:
            self.table.delete_item(
                Key={self.key_attribute: content_id},
                ConditionExpression="#status = :pending OR #status = :posting",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": STATUS_PENDING,
                    ":posting": STATUS_POSTING,
                },
            )
        except ClientError as exc:
            if is_condition_failure(exc):
                # Somebody else advanced it; leaving it alone is correct.
                return
            raise RepositoryError(f"release failed for {content_id}: {exc}") from exc
        except BotoCoreError as exc:
            raise RepositoryError(f"release failed for {content_id}: {exc}") from exc

    def _update(self, content_id, update, condition, names, values, what) -> None:
        """Apply a guarded update, translating failures into RepositoryError."""
        try:
            self.table.update_item(
                Key={self.key_attribute: content_id},
                UpdateExpression=update,
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if is_condition_failure(exc):
                raise RepositoryError(
                    f"{what} rejected for {content_id}: record is not in the expected state"
                ) from exc
            raise RepositoryError(f"{what} failed for {content_id}: {exc}") from exc
        except BotoCoreError as exc:
            raise RepositoryError(f"{what} failed for {content_id}: {exc}") from exc
