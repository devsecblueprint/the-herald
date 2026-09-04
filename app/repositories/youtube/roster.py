"""
The source roster: one watermark per monitored source.

Stored as a single ``youtube-sources`` item in The Herald's dedupe table.
It never expires; it is the only record of where each partner started, and
losing it would re-onboard everybody.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Tuple

from botocore.exceptions import BotoCoreError, ClientError

from app.errors import RosterError, RosterWriteConflict
from app.repositories.dynamodb import DEFAULT_KEY_ATTRIBUTE, is_condition_failure
from app.utils.clock import parse_iso, to_iso, utcnow

ROSTER_KEY = "youtube-sources"
RECORD_TYPE_ROSTER = "source_roster"


@dataclass
class Roster:
    """Where every monitored source stands, as read from DynamoDB."""

    watermarks: Dict[str, datetime] = field(default_factory=dict)
    revision: int = 0
    exists: bool = False
    unreadable: List[Tuple[str, Any]] = field(default_factory=list)


class RosterRepository:
    """Reads and writes the single roster item."""

    def __init__(self, table, key_attribute: str = DEFAULT_KEY_ATTRIBUTE, clock=utcnow):
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

        return Roster(
            watermarks=watermarks, revision=revision, exists=True, unreadable=unreadable
        )

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
            if is_condition_failure(exc):
                raise RosterWriteConflict(
                    f"roster changed underneath us (expected revision {expected_revision})"
                ) from exc
            raise RosterError(f"roster write failed: {exc}") from exc
        except BotoCoreError as exc:
            raise RosterError(f"roster write failed: {exc}") from exc

        return revision
