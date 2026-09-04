"""Atom feed ingestion."""

from datetime import datetime, timezone

import pytest

from app.clients.http import HttpError, HttpResponse
from app.clients.youtube import YouTubeClient
from app.config.youtube import YouTubeSource
from app.errors import FeedFetchError
from app.models.youtube import KIND_PLAYLIST, ChannelReference
from app.services.youtube.ingestion import (Resolution,
                                            YouTubeIngestionService,
                                            feed_url_for)
from tests.youtube.fakes import FakeHttpClient, feed_xml, make_source

CHANNEL_ID = "UCAAAAAAAAAAAAAAAAAAAAAA"


def at(day, hour=12):
    return datetime(2026, 8, day, hour, 0, 0, tzinfo=timezone.utc)


class StubResolver:
    """A resolver that always returns the same channel id."""

    def __init__(self, channel_id=CHANNEL_ID, error=None):
        self.channel_id = channel_id
        self.error = error

    def resolve(self, reference):
        if self.error:
            raise self.error
        return Resolution(self.channel_id, "page")


def playlist_source():
    return YouTubeSource(
        name="Damien Burks",
        relationship="COMMUNITY_PARTNER",
        reference=ChannelReference(KIND_PLAYLIST, "UUAAAAAAAAAAAAAAAAAAAAAA"),
    )


# -- feed URLs --------------------------------------------------------------


def test_a_channel_source_polls_the_channel_feed():
    url = feed_url_for(make_source(), CHANNEL_ID)
    assert url == f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"


def test_a_playlist_source_polls_the_playlist_feed():
    url = feed_url_for(playlist_source(), None)
    assert url.endswith("?playlist_id=UUAAAAAAAAAAAAAAAAAAAAAA")


def test_a_playlist_source_never_needs_a_resolver():
    http = FakeHttpClient().add(
        "GET", "playlist_id=", HttpResponse(status_code=200, text=feed_xml([]))
    )
    result = YouTubeIngestionService(YouTubeClient(http), resolver=None).fetch(playlist_source())
    assert result.items == []


# -- parsing ----------------------------------------------------------------


def test_entries_become_content_items():
    xml = feed_xml(
        [
            {
                "video_id": "abc123",
                "published": at(26),
                "title": "Threat modelling for platform teams",
                "description": "A walkthrough.",
            }
        ],
        channel_id=CHANNEL_ID,
    )
    source = make_source(categories=["cloud-security"])
    items = YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(xml, source)

    item = items[0]
    assert item.platform == "youtube"
    assert item.content_id == "abc123"
    assert item.dedupe_key == "youtube#abc123"
    assert item.title == "Threat modelling for platform teams"
    assert item.url == "https://www.youtube.com/watch?v=abc123"
    assert item.published_at == at(26)
    assert item.source_name == "Damien Burks"
    assert item.relationship == "COMMUNITY_PARTNER"
    assert item.categories == ["cloud-security"]
    assert item.description == "A walkthrough."
    assert item.channel_id == CHANNEL_ID
    assert item.thumbnail_url.endswith("/abc123/hqdefault.jpg")


def test_items_come_back_newest_first():
    xml = feed_xml(
        [
            {"video_id": "old", "published": at(20)},
            {"video_id": "newest", "published": at(28)},
            {"video_id": "middle", "published": at(24)},
        ]
    )
    items = YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(xml, make_source())
    assert [item.content_id for item in items] == ["newest", "middle", "old"]


def test_an_attribution_override_replaces_the_channel_name():
    xml = feed_xml([{"video_id": "abc123", "published": at(26)}], author="dburks_yt")
    source = make_source(attribution="Damien Burks (DSB)")
    assert YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(xml, source)[0].author_name == (
        "Damien Burks (DSB)"
    )


def test_the_channel_name_is_used_when_there_is_no_override():
    xml = feed_xml([{"video_id": "abc123", "published": at(26)}], author="dburks_yt")
    assert YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(xml, make_source())[0].author_name == (
        "dburks_yt"
    )


def test_an_entry_without_a_video_id_is_skipped_not_fatal():
    xml = feed_xml([{"video_id": "good", "published": at(26)}]).replace(
        "<yt:videoId>good</yt:videoId>", "", 1
    )
    assert YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(xml, make_source()) == []


def test_an_entry_with_an_unparseable_publish_time_is_skipped():
    xml = feed_xml(
        [{"video_id": "good", "published": at(26)}, {"video_id": "bad", "published": "yesterday"}]
    )
    items = YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(xml, make_source())
    assert [item.content_id for item in items] == ["good"]


def test_an_empty_feed_parses_to_nothing():
    assert YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(feed_xml([]), make_source()) == []


def test_invalid_xml_is_a_feed_failure():
    with pytest.raises(FeedFetchError, match="not valid XML"):
        YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse("<feed><oops", make_source())


# -- fetching ---------------------------------------------------------------


def test_fetch_resolves_the_handle_and_reads_the_feed():
    http = FakeHttpClient().add(
        "GET",
        "feeds/videos.xml",
        HttpResponse(status_code=200, text=feed_xml([{"video_id": "abc123", "published": at(26)}])),
    )
    service = YouTubeIngestionService(YouTubeClient(http), resolver=StubResolver())
    result = service.fetch(make_source())

    assert result.channel_id == CHANNEL_ID
    assert result.source_key == "handle:@damienjburks"
    assert [item.content_id for item in result.items] == ["abc123"]
    assert f"channel_id={CHANNEL_ID}" in result.feed_url


def test_an_http_error_status_is_a_feed_failure():
    http = FakeHttpClient().add("GET", "feeds/videos.xml", HttpResponse(status_code=503, text=""))
    with pytest.raises(FeedFetchError, match="HTTP 503"):
        YouTubeIngestionService(YouTubeClient(http), resolver=StubResolver()).fetch(make_source())


def test_a_network_error_is_a_feed_failure():
    http = FakeHttpClient().add("GET", "feeds/videos.xml", HttpError("timed out"))
    with pytest.raises(FeedFetchError, match="feed fetch failed"):
        YouTubeIngestionService(YouTubeClient(http), resolver=StubResolver()).fetch(make_source())


def test_a_channel_source_without_a_resolver_is_a_feed_failure():
    with pytest.raises(FeedFetchError, match="needs a resolver"):
        YouTubeIngestionService(YouTubeClient(FakeHttpClient())).fetch(make_source())
