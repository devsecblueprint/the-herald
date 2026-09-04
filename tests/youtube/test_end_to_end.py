"""
One poll with the real parts wired together.

Only the table, the clock and the network are doubles: the Atom parser,
the handle resolver, the Shorts probe, the claim state machine and the
Discord payload builder are all the production code.
"""

import json
from datetime import datetime, timedelta, timezone

from app.clients.discord import BotTokenTransport
from app.clients.http import HttpResponse
from app.clients.youtube import YouTubeClient
from app.models.youtube import STATUS_POSTED, STATUS_SKIPPED
from app.repositories.youtube.channel_cache import ChannelReferenceCache
from app.repositories.youtube.processing import ProcessingRepository
from app.repositories.youtube.roster import RosterRepository
from app.services.youtube.classification import build_shorts_detector
from app.services.youtube.ingestion import ChannelResolver, YouTubeIngestionService
from app.services.youtube.polling import YouTubePollingService
from app.services.youtube.publishing import YouTubePublishingService
from app.utils.clock import to_iso
from tests.youtube.fakes import (
    FakeClock,
    FakeHttpClient,
    FakeTable,
    channel_page,
    feed_xml,
)
from tests.youtube.harness import CHANNEL_ID, build_config

YT_CHANNEL = "UCAAAAAAAAAAAAAAAAAAAAAA"
BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def at(day, hour=12):
    return BASE + timedelta(days=day - 1, hours=hour)


def build(http, table, clock):
    """The production assembly, with only the network and the table faked."""
    config = build_config()
    youtube = YouTubeClient(http)
    resolver = ChannelResolver(
        youtube, cache=ChannelReferenceCache(table, clock=clock), clock=clock
    )
    return YouTubePollingService(
        config=config,
        ingestion=YouTubeIngestionService(youtube, resolver=resolver),
        publishing=YouTubePublishingService(
            repository=ProcessingRepository(table, clock=clock),
            classifier=build_shorts_detector(youtube, exclude_shorts=True),
            transport=BotTokenTransport(http, "token", sleeper=lambda _s: None),
            channel_id=config.discord_channel_id,
            clock=clock,
        ),
        roster_repository=RosterRepository(table, clock=clock),
        clock=clock,
    )


def test_a_partner_is_onboarded_then_their_next_upload_is_announced():
    table = FakeTable()
    clock = FakeClock(at(30))
    http = FakeHttpClient()

    http.add("GET", "youtube.com/@", HttpResponse(200, text=channel_page(YT_CHANNEL)))
    http.add(
        "GET",
        "feeds/videos.xml",
        HttpResponse(
            200, text=feed_xml([{"video_id": "old", "published": at(20)}], YT_CHANNEL)
        ),
        HttpResponse(
            200,
            text=feed_xml(
                [
                    {"video_id": "old", "published": at(20)},
                    {
                        "video_id": "fresh",
                        "published": at(31),
                        "title": "Threat modelling for platform teams",
                        "description": "A walkthrough.",
                    },
                    {"video_id": "tiny", "published": at(31, 9)},
                ],
                YT_CHANNEL,
            ),
        ),
    )
    http.add(
        "HEAD",
        "/shorts/fresh",
        HttpResponse(303, headers={"Location": "/watch?v=fresh"}),
    )
    http.add("HEAD", "/shorts/tiny", HttpResponse(200))
    http.add("POST", "discord.com", HttpResponse(200, text=json.dumps({"id": "555"})))

    polling = build(http, table, clock)

    first = polling.run()
    assert first.sources_onboarded == 1
    assert first.announcements_published == 0
    assert table.video_records() == {}

    clock.advance(days=1)
    second = polling.run()

    assert second.announcements_published == 1
    assert second.shorts_skipped == 1
    assert [item.video_id for item in second.announced] == ["fresh"]

    posted = table.record("youtube#fresh")
    assert posted["status"] == STATUS_POSTED
    assert posted["discord_message_id"] == "555"
    assert posted["youtube_channel_id"] == YT_CHANNEL
    assert table.record("youtube#tiny")["status"] == STATUS_SKIPPED

    roster = table.record("youtube-sources")
    assert roster["watermarks"] == {"handle:@damienjburks": to_iso(at(31))}
    assert "ttl" not in roster

    # The handle was resolved once and then served from the caches.
    assert len([call for call in http.calls if "youtube.com/@" in call["url"]]) == 1


def test_the_discord_payload_is_what_lands_in_the_channel():
    table = FakeTable()
    clock = FakeClock(at(30))
    http = FakeHttpClient()

    http.add("GET", "youtube.com/@", HttpResponse(200, text=channel_page(YT_CHANNEL)))
    http.add(
        "GET",
        "feeds/videos.xml",
        HttpResponse(200, text=feed_xml([], YT_CHANNEL)),
        HttpResponse(
            200,
            text=feed_xml(
                [
                    {
                        "video_id": "fresh",
                        "published": at(31),
                        "title": "Threat modelling for platform teams",
                        "description": "A walkthrough.",
                    }
                ],
                YT_CHANNEL,
            ),
        ),
    )
    http.add("HEAD", "/shorts/", HttpResponse(303))
    http.add("POST", "discord.com", HttpResponse(200, text=json.dumps({"id": "555"})))

    polling = build(http, table, clock)
    polling.run()
    clock.advance(days=1)
    polling.run()

    post = [call for call in http.calls if call["method"] == "POST"][0]
    payload = post["json"]

    assert post["url"] == f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
    assert payload["content"] == "\U0001f4fa New from **Damien Burks** on YouTube"
    assert payload["allowed_mentions"] == {"parse": []}
    embed = payload["embeds"][0]
    assert embed["title"] == "Threat modelling for platform teams"
    assert embed["url"] == "https://www.youtube.com/watch?v=fresh"
    assert embed["footer"]["text"] == "Damien Burks • Community Partner • YouTube"
