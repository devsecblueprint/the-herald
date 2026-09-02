"""
Test doubles for the YouTube feature.

The whole suite runs with no network and no AWS: an in-memory DynamoDB
table that evaluates the real condition expressions, a programmable HTTP
client, canned Atom feeds and a recording Discord transport.
"""

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional

from botocore.exceptions import ClientError

from app.clients.http import HttpError, HttpResponse
from app.config.youtube import YouTubeSource, parse_channel_reference
from app.errors import DistributionError
from app.models.youtube import (ChannelReference, ContentItem, ShortsVerdict,
                                SourceFetchResult)
from app.utils.clock import to_iso
from tests.youtube.condition import apply_update, evaluate_condition

CONDITION_FAILURE = ClientError(
    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition failed"}},
    "PutItem",
)


def throttling_error(operation: str = "PutItem") -> ClientError:
    """A ClientError that looks like DynamoDB throttling."""
    return ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}},
        operation,
    )


def to_dynamo(value):
    """Coerce numbers to Decimal, the way DynamoDB stores and returns them."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {key: to_dynamo(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamo(inner) for inner in value]
    return value


class FakeTable:
    """An in-memory stand-in for a boto3 DynamoDB Table resource."""

    def __init__(self, key_attribute: str = "content_id"):
        self.key_attribute = key_attribute
        self.items: Dict[str, Dict[str, Any]] = {}
        self.calls: List[str] = []
        # Map of operation name -> exception to raise on the next call.
        self.fail_next: Dict[str, Exception] = {}
        # Map of operation name -> exception to raise on every call.
        self.fail_always: Dict[str, Exception] = {}

    # -- failure injection -------------------------------------------------

    def break_once(self, operation: str, error: Exception) -> None:
        """Make the next call to ``operation`` raise."""
        self.fail_next[operation] = error

    def break_always(self, operation: str, error: Exception) -> None:
        """Make every call to ``operation`` raise."""
        self.fail_always[operation] = error

    def _maybe_fail(self, operation: str) -> None:
        self.calls.append(operation)
        if operation in self.fail_always:
            raise self.fail_always[operation]
        if operation in self.fail_next:
            raise self.fail_next.pop(operation)

    # -- table operations --------------------------------------------------

    def put_item(
        self,
        Item,
        ConditionExpression=None,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
    ):
        """Write an item, honouring any condition expression."""
        self._maybe_fail("put_item")
        key = Item[self.key_attribute]
        existing = self.items.get(key)

        if ConditionExpression and not evaluate_condition(
            ConditionExpression,
            existing,
            ExpressionAttributeNames,
            to_dynamo(ExpressionAttributeValues or {}),
        ):
            raise CONDITION_FAILURE

        self.items[key] = to_dynamo(copy.deepcopy(dict(Item)))
        return {}

    def get_item(self, Key):
        """Read an item by its partition key."""
        self._maybe_fail("get_item")
        item = self.items.get(Key[self.key_attribute])
        return {"Item": copy.deepcopy(item)} if item is not None else {}

    def update_item(
        self,
        Key,
        UpdateExpression,
        ConditionExpression=None,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
    ):
        """Apply a SET update, honouring any condition expression."""
        self._maybe_fail("update_item")
        key = Key[self.key_attribute]
        existing = self.items.get(key)
        values = to_dynamo(ExpressionAttributeValues or {})

        if ConditionExpression and not evaluate_condition(
            ConditionExpression, existing, ExpressionAttributeNames, values
        ):
            raise CONDITION_FAILURE

        item = copy.deepcopy(existing) if existing else {self.key_attribute: key}
        self.items[key] = apply_update(item, UpdateExpression, ExpressionAttributeNames, values)
        return {}

    def delete_item(
        self,
        Key,
        ConditionExpression=None,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
    ):
        """Delete an item, honouring any condition expression."""
        self._maybe_fail("delete_item")
        key = Key[self.key_attribute]
        existing = self.items.get(key)

        if ConditionExpression and not evaluate_condition(
            ConditionExpression,
            existing,
            ExpressionAttributeNames,
            to_dynamo(ExpressionAttributeValues or {}),
        ):
            raise CONDITION_FAILURE

        self.items.pop(key, None)
        return {}

    # -- assertions helpers ------------------------------------------------

    def record(self, content_id: str) -> Optional[Dict[str, Any]]:
        """The stored item for a key, or None."""
        return self.items.get(content_id)

    def video_records(self) -> Dict[str, Dict[str, Any]]:
        """Every per-video record currently stored."""
        return {
            key: value for key, value in self.items.items() if key.startswith("youtube#")
        }


class FakeClock:
    """A clock that only moves when a test tells it to."""

    def __init__(self, start: Optional[datetime] = None):
        self.now = start or datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        """Move the clock forward."""
        self.now = self.now + timedelta(**kwargs)
        return self.now


class FakeHttpClient:
    """A programmable HttpClient that records every call."""

    def __init__(self):
        self.routes: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []

    def add(self, method: str, contains: str, *responses) -> "FakeHttpClient":
        """
        Register responses for requests whose URL contains a substring.

        Responses are consumed in order; the last one repeats. A response
        may be an ``HttpResponse`` or an exception instance to raise.
        """
        self.routes.append(
            {"method": method.upper(), "contains": contains, "responses": list(responses)}
        )
        return self

    def add_text(self, method: str, contains: str, status: int, text: str = "", headers=None):
        """Register a single textual response."""
        return self.add(
            method, contains, HttpResponse(status_code=status, text=text, headers=headers or {})
        )

    def get(self, url, *, params=None, headers=None, timeout=None):
        """Issue a recorded GET."""
        return self._respond("GET", url, params=params, headers=headers)

    def head(self, url, *, headers=None, allow_redirects=False, timeout=None):
        """Issue a recorded HEAD."""
        return self._respond("HEAD", url, headers=headers, allow_redirects=allow_redirects)

    def post(self, url, *, json=None, headers=None, timeout=None):
        """Issue a recorded POST."""
        return self._respond("POST", url, json=json, headers=headers)

    def _respond(self, method, url, **details):
        self.calls.append({"method": method, "url": url, **details})
        for route in self.routes:
            if route["method"] == method and route["contains"] in url:
                responses = route["responses"]
                response = responses[0] if len(responses) == 1 else responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response
        raise HttpError(f"No fake route for {method} {url}")

    def urls_for(self, method: str) -> List[str]:
        """Every URL requested with a given method."""
        return [call["url"] for call in self.calls if call["method"] == method.upper()]


class RecordingTransport:
    """A Discord transport that records payloads instead of sending them."""

    name = "recording"

    def __init__(self, errors: Optional[Dict[str, Exception]] = None):
        self.sent: List[Dict[str, Any]] = []
        self.errors = errors or {}
        self._next_id = 1000

    def fail_for(self, video_id: str, error: Exception) -> None:
        """Raise ``error`` when the payload mentions this video id."""
        self.errors[video_id] = error

    def send(self, channel_id, payload):
        """Record a payload and return a synthetic message id."""
        rendered = str(payload)
        for video_id, error in self.errors.items():
            if video_id in rendered:
                self.sent.append({"channel_id": channel_id, "payload": payload, "failed": True})
                raise error

        self._next_id += 1
        self.sent.append({"channel_id": channel_id, "payload": payload, "id": str(self._next_id)})
        return str(self._next_id)

    @property
    def message_ids(self) -> List[str]:
        """The ids of every message that was accepted."""
        return [sent["id"] for sent in self.sent if "id" in sent]


class StubIngestion:
    """An ingestion service that returns canned results per source key."""

    platform = "youtube"

    def __init__(self, results: Optional[Dict[str, Any]] = None):
        self.results = results or {}
        self.fetched: List[str] = []

    def set(self, source_key: str, result) -> None:
        """Register the result (or exception) for a source key."""
        self.results[source_key] = result

    def fetch(self, source):
        """Return the canned result, raising it if it is an exception."""
        self.fetched.append(source.key)
        result = self.results.get(source.key)
        if isinstance(result, Exception):
            raise result
        if result is None:
            return SourceFetchResult(
                source_key=source.key,
                source_name=source.name,
                channel_id=None,
                feed_url="https://www.youtube.com/feeds/videos.xml",
                items=[],
            )
        return result


class StubDetector:
    """A Shorts detector driven by an explicit verdict map."""

    name = "stub"

    def __init__(self, verdicts: Optional[Dict[str, Any]] = None, default=None):
        self.verdicts = verdicts or {}
        self.default = default
        self.seen: List[str] = []

    def classify(self, item):
        """Return the registered verdict, raising it if it is an exception."""
        self.seen.append(item.content_id)
        verdict = self.verdicts.get(item.content_id, self.default)
        if isinstance(verdict, Exception):
            raise verdict
        if verdict is None:
            return ShortsVerdict(is_short=False, detector=self.name)
        return verdict


# -- fixtures ---------------------------------------------------------------


def make_source(
    name: str = "Damien Burks",
    channel: str = "@damienjburks",
    relationship: str = "COMMUNITY_PARTNER",
    categories=None,
    attribution=None,
) -> YouTubeSource:
    """Build a configured source without going through YAML."""
    return YouTubeSource(
        name=name,
        relationship=relationship,
        reference=parse_channel_reference(channel),
        categories=list(categories or []),
        attribution=attribution,
    )


def make_item(
    video_id: str = "abc123",
    published_at: Optional[datetime] = None,
    title: str = "Threat modelling for platform teams",
    source_name: str = "Damien Burks",
    relationship: str = "COMMUNITY_PARTNER",
    description: str = "A walkthrough.",
    channel_id: str = "UCxxxxxxxxxxxxxxxxxxxxxx",
) -> ContentItem:
    """Build a content item directly."""
    return ContentItem(
        platform="youtube",
        content_id=video_id,
        title=title,
        url=f"https://www.youtube.com/watch?v={video_id}",
        published_at=published_at or datetime(2026, 8, 26, 9, 0, 0, tzinfo=timezone.utc),
        source_name=source_name,
        relationship=relationship,
        categories=["cloud-security"],
        description=description,
        author_name=source_name,
        author_url="https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx",
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        channel_id=channel_id,
    )


def feed_xml(entries, channel_id: str = "UCxxxxxxxxxxxxxxxxxxxxxx", author="Damien Burks") -> str:
    """
    Build a canned YouTube Atom feed.

    Each entry is a mapping with at least ``video_id`` and ``published``.
    """
    rendered = []
    for entry in entries:
        published = entry["published"]
        if isinstance(published, datetime):
            published = to_iso(published)
        rendered.append(
            f"""
  <entry>
    <id>yt:video:{entry['video_id']}</id>
    <yt:videoId>{entry['video_id']}</yt:videoId>
    <yt:channelId>{entry.get('channel_id', channel_id)}</yt:channelId>
    <title>{entry.get('title', 'A video')}</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v={entry['video_id']}"/>
    <author>
      <name>{entry.get('author', author)}</name>
      <uri>https://www.youtube.com/channel/{entry.get('channel_id', channel_id)}</uri>
    </author>
    <published>{published}</published>
    <updated>{published}</updated>
    <media:group>
      <media:title>{entry.get('title', 'A video')}</media:title>
      <media:content url="https://www.youtube.com/v/{entry['video_id']}" type="application/x-shockwave-flash"/>
      <media:thumbnail url="https://i.ytimg.com/vi/{entry['video_id']}/hqdefault.jpg" width="480" height="360"/>
      <media:description>{entry.get('description', '')}</media:description>
    </media:group>
  </entry>"""
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <yt:channelId>{channel_id}</yt:channelId>
  <title>{author}</title>
  <author><name>{author}</name></author>{''.join(rendered)}
</feed>
"""


def channel_page(channel_id: str = "UCxxxxxxxxxxxxxxxxxxxxxx", marker: str = "externalId") -> str:
    """Build a channel page carrying exactly one of the four id markers."""
    markers = {
        "externalId": f'<script>var ytInitialData = {{"externalId":"{channel_id}"}};</script>',
        "canonical": f'<link rel="canonical" href="https://www.youtube.com/channel/{channel_id}">',
        "identifier": f'<meta itemprop="identifier" content="{channel_id}">',
        "channelId": f'<script>{{"channelId":"{channel_id}"}}</script>',
    }
    return f"<html><head>{markers[marker]}</head><body>a channel</body></html>"


__all__ = [
    "CONDITION_FAILURE",
    "DistributionError",
    "ChannelReference",
    "FakeClock",
    "FakeHttpClient",
    "FakeTable",
    "RecordingTransport",
    "StubDetector",
    "StubIngestion",
    "channel_page",
    "feed_xml",
    "make_item",
    "make_source",
    "throttling_error",
    "to_dynamo",
]
