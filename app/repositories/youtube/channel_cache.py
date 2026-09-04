"""
The durable half of channel-reference resolution.

A resolved ``@handle`` is cached as a ``youtube-channel#<kind>:<value>``
item for thirty days. The TTL matters: a handle can be released and taken
over by a different channel, and an immortal cache entry would keep
announcing the new owner's videos under the old partner's name.
"""

from datetime import timedelta
from typing import Optional

from botocore.exceptions import BotoCoreError, ClientError

from app.repositories.dynamodb import DEFAULT_KEY_ATTRIBUTE, DEFAULT_TTL_ATTRIBUTE
from app.utils.clock import to_iso, utcnow

CHANNEL_CACHE_PREFIX = "youtube-channel#"
RECORD_TYPE_CHANNEL = "channel_reference"

DEFAULT_CHANNEL_CACHE_TTL_DAYS = 30


class ChannelReferenceCache:
    """Reads and writes cached channel-reference resolutions."""

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        table,
        key_attribute: str = DEFAULT_KEY_ATTRIBUTE,
        ttl_attribute: str = DEFAULT_TTL_ATTRIBUTE,
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
            response = self.table.get_item(
                Key={self.key_attribute: self._item_key(reference_key)}
            )
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
                    self.ttl_attribute: int(
                        (now + timedelta(days=self.ttl_days)).timestamp()
                    ),
                }
            )
        except (ClientError, BotoCoreError):
            # A cold cache costs one page fetch; it is never worth failing a poll.
            pass
