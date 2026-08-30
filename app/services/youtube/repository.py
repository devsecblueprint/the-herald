"""
DynamoDB persistence for the YouTube feature.

Three kinds of item share The Herald's dedupe table, told apart by their
partition key:

* ``youtube#<video id>`` -- one per video: the dedupe guard and the audit
  trail of which Discord message announced it. Expires after 35 days.
* ``youtube-sources``    -- the roster: one watermark per monitored source.
  Never expires; it is the only record of where each partner started.
* ``youtube-channel#<kind>:<value>`` -- a resolved channel reference,
  cached for 30 days.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Tuple

from botocore.exceptions import BotoCoreError, ClientError

from app.services.youtube.clock import parse_iso, to_iso, utcnow
from app.services.youtube.errors import RepositoryError, RosterError, RosterWriteConflict
from app.services.youtube.models import (
    STATUS_PENDING,
    STATUS_POSTED,
    STATUS_POSTING,
    STATUS_SKIPPED,
    TERMINAL_STATUSES,
    ContentItem,
    DeliveryReceipt,
)

ROSTER_KEY = "youtube-sources"
CHANNEL_CACHE_PREFIX = "youtube-channel#"

RECORD_TYPE_ROSTER = "source_roster"
RECORD_TYPE_CHANNEL = "channel_reference"

DEFAULT_TTL_DAYS = 35
DEFAULT_CHANNEL_CACHE_TTL_DAYS = 30
DEFAULT_STALE_CLAIM_MINUTES = 60

CONDITION_FAILED = "ConditionalCheckFailedException"


def _is_condition_failure(exc: ClientError) -> bool:
    """True when a ClientError is a failed conditional write."""
    return exc.response.get("Error", {}).get("Code") == CONDITION_FAILED


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
        key_attribute: str = "content_id",
        ttl_attribute: str = "ttl",
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
        stale_cutoff = int((now - timedelta(minutes=self.stale_claim_minutes)).timestamp())
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
            if _is_condition_failure(exc):
                return ClaimResult(claimed=False, status=self._status_of(item.dedupe_key))
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
            names={"#pk": self.key_attribute, "#status": "status", "#ttl": self.ttl_attribute},
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
            names={"#pk": self.key_attribute, "#status": "status", "#ttl": self.ttl_attribute},
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
            if _is_condition_failure(exc):
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
            if _is_condition_failure(exc):
                raise RepositoryError(
                    f"{what} rejected for {content_id}: record is not in the expected state"
                ) from exc
            raise RepositoryError(f"{what} failed for {content_id}: {exc}") from exc
        except BotoCoreError as exc:
            raise RepositoryError(f"{what} failed for {content_id}: {exc}") from exc


@dataclass
class Roster:
    """Where every monitored source stands, as read from DynamoDB."""

    watermarks: Dict[str, datetime] = field(default_factory=dict)
    revision: int = 0
    exists: bool = False
    unreadable: List[Tuple[str, Any]] = field(default_factory=list)


class RosterRepository:
    """Reads and writes the single roster item."""

    def __init__(self, table, key_attribute: str = "content_id", clock=utcnow):
        self.table = table
        self.key_attribute = key_attribute
        self.clock = clock

    def load(self) -> Roster:
        """
        Read the roster.

        A single unparseable entry is dropped (that source re-onboards); an
        unreadable *item* is fatal, because without watermarks there is no
        safe way to decide what is new.

        Raises:
            RosterError: If the item cannot be read at all.
        """
        try:
            response = self.table.get_item(Key={self.key_attribute: ROSTER_KEY})
        except (ClientError, BotoCoreError) as exc:
            raise RosterError(f"roster read failed: {exc}") from exc

        item = response.get("Item")
        if not item:
            return Roster()

        watermarks: Dict[str, datetime] = {}
        unreadable: List[Tuple[str, Any]] = []
        raw = item.get("watermarks") or {}
        if not isinstance(raw, Mapping):
            raise RosterError("roster 'watermarks' attribute is not a map")

        for source_key, value in raw.items():
            try:
                watermarks[source_key] = parse_iso(value)
            except (ValueError, TypeError):
                unreadable.append((source_key, value))

        try:
            revision = int(item.get("revision", 0))
        except (TypeError, ValueError):
            revision = 0

        return Roster(watermarks=watermarks, revision=revision, exists=True, unreadable=unreadable)

    def save(self, watermarks: Mapping[str, datetime], expected_revision: int) -> int:
        """
        Rewrite the roster from the current configuration.

        The write is guarded on ``revision`` so a slow poller cannot
        overwrite a newer roster and silently drop a partner added in the
        meantime.

        Returns:
            The revision that was written.

        Raises:
            RosterWriteConflict: If another poll wrote first.
            RosterError: If the write fails for any other reason.
        """
        now = self.clock()
        revision = expected_revision + 1
        item = {
            self.key_attribute: ROSTER_KEY,
            "record_type": RECORD_TYPE_ROSTER,
            "watermarks": {key: to_iso(value) for key, value in watermarks.items()},
            "updated_at": to_iso(now),
            "revision": revision,
        }

        if expected_revision == 0:
            condition = "attribute_not_exists(#pk)"
            values = None
        else:
            condition = "#rev = :expected"
            values = {":expected": expected_revision}

        names = {"#pk": self.key_attribute}
        if expected_revision != 0:
            names["#rev"] = "revision"

        kwargs = {
            "Item": item,
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
        }
        if values:
            kwargs["ExpressionAttributeValues"] = values

        try:
            self.table.put_item(**kwargs)
        except ClientError as exc:
            if _is_condition_failure(exc):
                raise RosterWriteConflict(
                    f"roster changed underneath us (expected revision {expected_revision})"
                ) from exc
            raise RosterError(f"roster write failed: {exc}") from exc
        except BotoCoreError as exc:
            raise RosterError(f"roster write failed: {exc}") from exc

        return revision


class ChannelReferenceCache:
    """The durable half of channel-reference resolution."""

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        table,
        key_attribute: str = "content_id",
        ttl_attribute: str = "ttl",
        ttl_days: int = DEFAULT_CHANNEL_CACHE_TTL_DAYS,
        clock=utcnow,
    ):
        self.table = table
        self.key_attribute = key_attribute
        self.ttl_attribute = ttl_attribute
        self.ttl_days = ttl_days
        self.clock = clock

    def _item_key(self, reference_key: str) -> str:
        """Partition key for a cached reference."""
        return f"{CHANNEL_CACHE_PREFIX}{reference_key}"

    def get(self, reference_key: str) -> Optional[str]:
        """
        Read a cached channel id, honouring the TTL.

        DynamoDB deletes expired items lazily, so an entry past its TTL is
        treated as a miss: a released handle must not keep resolving to its
        old owner.
        """
        try:
            response = self.table.get_item(Key={self.key_attribute: self._item_key(reference_key)})
        except (ClientError, BotoCoreError):
            return None

        item = response.get("Item")
        if not item:
            return None

        expiry = item.get(self.ttl_attribute)
        if expiry is not None:
            try:
                if int(expiry) <= int(self.clock().timestamp()):
                    return None
            except (TypeError, ValueError):
                return None

        return item.get("channel_id")

    def put(self, reference_key: str, channel_id: str) -> None:
        """Cache a resolved channel id. Failure here is never fatal."""
        now = self.clock()
        try:
            self.table.put_item(
                Item={
                    self.key_attribute: self._item_key(reference_key),
                    "record_type": RECORD_TYPE_CHANNEL,
                    "reference": reference_key,
                    "channel_id": channel_id,
                    "resolved_at": to_iso(now),
                    self.ttl_attribute: int((now + timedelta(days=self.ttl_days)).timestamp()),
                }
            )
        except (ClientError, BotoCoreError):
            # A cold cache costs one page fetch; it is never worth failing a poll.
            pass
