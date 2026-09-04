"""Atom feed ingestion."""

from datetime import datetime, timezone

import pytest

from app.clients.http import HttpError, HttpResponse
from app.clients.youtube import YouTubeClient
from app.config.youtube import YouTubeSource
from app.errors import FeedFetchError
from app.models.youtube import KIND_PLAYLIST, ChannelReference
from app.services.youtube.ingestion import (
    Resolution,
    YouTubeIngestionService,
    feed_url_for,
)
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
    result = YouTubeIngestionService(YouTubeClient(http), resolver=None).fetch(
        playlist_source()
    )
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
    items = YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(
        xml, make_source()
    )
    assert [item.content_id for item in items] == ["newest", "middle", "old"]


def test_an_attribution_override_replaces_the_channel_name():
    xml = feed_xml([{"video_id": "abc123", "published": at(26)}], author="dburks_yt")
    source = make_source(attribution="Damien Burks (DSB)")
    assert YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(xml, source)[
        0
    ].author_name == ("Damien Burks (DSB)")


def test_the_channel_name_is_used_when_there_is_no_override():
    xml = feed_xml([{"video_id": "abc123", "published": at(26)}], author="dburks_yt")
    assert YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(
        xml, make_source()
    )[0].author_name == ("dburks_yt")


def test_an_entry_without_a_video_id_is_skipped_not_fatal():
    xml = feed_xml([{"video_id": "good", "published": at(26)}]).replace(
        "<yt:videoId>good</yt:videoId>", "", 1
    )
    assert (
        YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(
            xml, make_source()
        )
        == []
    )


def test_an_entry_with_an_unparseable_publish_time_is_skipped():
    xml = feed_xml(
        [
            {"video_id": "good", "published": at(26)},
            {"video_id": "bad", "published": "yesterday"},
        ]
    )
    items = YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(
        xml, make_source()
    )
    assert [item.content_id for item in items] == ["good"]


def test_an_empty_feed_parses_to_nothing():
    assert (
        YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(
            feed_xml([]), make_source()
        )
        == []
    )


def test_invalid_xml_is_a_feed_failure():
    with pytest.raises(FeedFetchError, match="not valid XML"):
        YouTubeIngestionService(YouTubeClient(FakeHttpClient())).parse(
            "<feed><oops", make_source()
        )


# -- fetching ---------------------------------------------------------------


def test_fetch_resolves_the_handle_and_reads_the_feed():
    http = FakeHttpClient().add(
        "GET",
        "feeds/videos.xml",
        HttpResponse(
            status_code=200,
            text=feed_xml([{"video_id": "abc123", "published": at(26)}]),
        ),
    )
    service = YouTubeIngestionService(YouTubeClient(http), resolver=StubResolver())
    result = service.fetch(make_source())

    assert result.channel_id == CHANNEL_ID
    assert result.source_key == "handle:@damienjburks"
    assert [item.content_id for item in result.items] == ["abc123"]
    assert f"channel_id={CHANNEL_ID}" in result.feed_url


def test_an_http_error_status_is_a_feed_failure():
    http = FakeHttpClient().add(
        "GET", "feeds/videos.xml", HttpResponse(status_code=503, text="")
    )
    with pytest.raises(FeedFetchError, match="HTTP 503"):
        YouTubeIngestionService(YouTubeClient(http), resolver=StubResolver()).fetch(
            make_source()
        )


def test_a_network_error_is_a_feed_failure():
    http = FakeHttpClient().add("GET", "feeds/videos.xml", HttpError("timed out"))
    with pytest.raises(FeedFetchError, match="feed fetch failed"):
        YouTubeIngestionService(YouTubeClient(http), resolver=StubResolver()).fetch(
            make_source()
        )


def test_a_channel_source_without_a_resolver_is_a_feed_failure():
    with pytest.raises(FeedFetchError, match="needs a resolver"):
        YouTubeIngestionService(YouTubeClient(FakeHttpClient())).fetch(make_source())


# -- Data API listing -------------------------------------------------------

import json

from app.clients.youtube import uploads_playlist_id
from app.errors import YouTubeApiError


def _playlist_items_json(*videos):
    """Build a minimal playlistItems.list response body."""
    items = []
    for video in videos:
        items.append(
            {
                "snippet": {
                    "title": video.get("title", video["video_id"]),
                    "description": video.get("description", ""),
                    "videoOwnerChannelId": video.get("channel_id", CHANNEL_ID),
                    "videoOwnerChannelTitle": video.get(
                        "channel_title", "Damien Burks"
                    ),
                    "thumbnails": {
                        "high": {
                            "url": f"https://i.ytimg.com/vi/{video['video_id']}/hq.jpg"
                        }
                    },
                    "resourceId": {"videoId": video["video_id"]},
                },
                "contentDetails": {
                    "videoId": video["video_id"],
                    "videoPublishedAt": video["published"],
                },
            }
        )
    return json.dumps({"items": items})


def _keyed_service(http):
    """An ingestion service whose client has a Data API key."""
    client = YouTubeClient(http, api_key="test-key")
    return YouTubeIngestionService(client, resolver=StubResolver())


def test_uploads_playlist_id_swaps_the_uc_prefix_for_uu():
    assert uploads_playlist_id(CHANNEL_ID) == "UU" + CHANNEL_ID[2:]


def test_uploads_playlist_id_rejects_a_non_channel_id():
    with pytest.raises(YouTubeApiError, match="cannot derive"):
        uploads_playlist_id("not-a-channel")


def test_fetch_uses_the_data_api_when_a_key_is_present():
    http = FakeHttpClient().add(
        "GET",
        "playlistItems",
        HttpResponse(
            status_code=200,
            text=_playlist_items_json(
                {"video_id": "abc123", "published": "2026-08-26T12:00:00Z"}
            ),
        ),
    )
    result = _keyed_service(http).fetch(make_source())

    assert result.channel_id == CHANNEL_ID
    assert result.feed_url.startswith("data_api:")
    assert [item.content_id for item in result.items] == ["abc123"]
    # The Atom feed must not have been touched.
    assert http.urls_for("GET") == [
        call for call in http.urls_for("GET") if "playlistItems" in call
    ]


def test_data_api_items_map_all_the_content_fields():
    http = FakeHttpClient().add(
        "GET",
        "playlistItems",
        HttpResponse(
            status_code=200,
            text=_playlist_items_json(
                {
                    "video_id": "abc123",
                    "published": "2026-08-26T12:00:00Z",
                    "title": "Threat modelling",
                    "description": "A walkthrough.",
                }
            ),
        ),
    )
    item = (
        _keyed_service(http).fetch(make_source(categories=["cloud-security"])).items[0]
    )

    assert item.content_id == "abc123"
    assert item.title == "Threat modelling"
    assert item.url == "https://www.youtube.com/watch?v=abc123"
    assert item.published_at == at(26)
    assert item.description == "A walkthrough."
    assert item.categories == ["cloud-security"]
    assert item.channel_id == CHANNEL_ID
    assert item.thumbnail_url.endswith("/abc123/hq.jpg")


def test_data_api_items_come_back_newest_first():
    http = FakeHttpClient().add(
        "GET",
        "playlistItems",
        HttpResponse(
            status_code=200,
            text=_playlist_items_json(
                {"video_id": "old", "published": "2026-08-20T12:00:00Z"},
                {"video_id": "newest", "published": "2026-08-28T12:00:00Z"},
                {"video_id": "middle", "published": "2026-08-24T12:00:00Z"},
            ),
        ),
    )
    items = _keyed_service(http).fetch(make_source()).items
    assert [item.content_id for item in items] == ["newest", "middle", "old"]


def test_fetch_falls_back_to_the_feed_when_the_data_api_fails():
    http = (
        FakeHttpClient()
        .add("GET", "playlistItems", HttpResponse(status_code=500, text=""))
        .add(
            "GET",
            "feeds/videos.xml",
            HttpResponse(
                status_code=200,
                text=feed_xml([{"video_id": "fromfeed", "published": at(26)}]),
            ),
        )
    )
    result = _keyed_service(http).fetch(make_source())

    assert [item.content_id for item in result.items] == ["fromfeed"]
    assert f"channel_id={CHANNEL_ID}" in result.feed_url


def test_a_playlist_source_ignores_the_data_api_even_with_a_key():
    http = FakeHttpClient().add(
        "GET", "playlist_id=", HttpResponse(status_code=200, text=feed_xml([]))
    )
    client = YouTubeClient(http, api_key="test-key")
    result = YouTubeIngestionService(client, resolver=None).fetch(playlist_source())

    assert result.items == []
    assert "playlistItems" not in " ".join(http.urls_for("GET"))
