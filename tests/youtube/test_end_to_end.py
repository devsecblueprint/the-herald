"""
One poll with the real parts wired together.

Only the table, the clock and the network are doubles: the Atom parser,
the handle resolver, the Shorts probe, the claim state machine and the
Discord payload builder are all the production code.
"""

import json
from datetime import datetime, timedelta, timezone

from app.services.youtube.clock import to_iso
from app.services.youtube.distribution import BotTokenTransport, DiscordDistributionService
from app.services.youtube.http import HttpResponse
from app.services.youtube.ingestion import YouTubeIngestionService
from app.services.youtube.models import STATUS_POSTED, STATUS_SKIPPED
from app.services.youtube.pipeline import YouTubePipeline
from app.services.youtube.repository import (
    ChannelReferenceCache,
    ProcessingRepository,
    RosterRepository,
)
from app.services.youtube.resolver import ChannelResolver
from app.services.youtube.shorts import build_shorts_detector
from tests.youtube.fakes import FakeClock, FakeHttpClient, FakeTable, channel_page, feed_xml
from tests.youtube.harness import CHANNEL_ID, build_config

YT_CHANNEL = "UCAAAAAAAAAAAAAAAAAAAAAA"
BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def at(day, hour=12):
    return BASE + timedelta(days=day - 1, hours=hour)


def build(http, table, clock):
    config = build_config()
    cache = ChannelReferenceCache(table, clock=clock)
    resolver = ChannelResolver(http, cache=cache, clock=clock)
    return YouTubePipeline(
        config=config,
        ingestion=YouTubeIngestionService(http, resolver=resolver),
        distribution=DiscordDistributionService(
            BotTokenTransport(http, "token", sleeper=lambda _s: None),
            config.discord_channel_id,
            clock=clock,
        ),
        repository=ProcessingRepository(table, clock=clock),
        roster_repository=RosterRepository(table, clock=clock),
        shorts_detector=build_shorts_detector(http, exclude_shorts=True),
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
        HttpResponse(200, text=feed_xml([{"video_id": "old", "published": at(20)}], YT_CHANNEL)),
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
    http.add("HEAD", "/shorts/fresh", HttpResponse(303, headers={"Location": "/watch?v=fresh"}))
    http.add("HEAD", "/shorts/tiny", HttpResponse(200))
    http.add("POST", "discord.com", HttpResponse(200, text=json.dumps({"id": "555"})))

    pipeline = build(http, table, clock)

    first = pipeline.run()
    assert first.sources_onboarded == 1
    assert first.announcements_published == 0
    assert table.video_records() == {}

    clock.advance(days=1)
    second = pipeline.run()

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

    pipeline = build(http, table, clock)
    pipeline.run()
    clock.advance(days=1)
    pipeline.run()

    post = [call for call in http.calls if call["method"] == "POST"][0]
    payload = post["json"]

    assert post["url"] == f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
    assert payload["content"] == "\U0001F4FA New from **Damien Burks** on YouTube"
    assert payload["allowed_mentions"] == {"parse": []}
    embed = payload["embeds"][0]
    assert embed["title"] == "Threat modelling for platform teams"
    assert embed["url"] == "https://www.youtube.com/watch?v=fresh"
    assert embed["footer"]["text"] == "Damien Burks • Community Partner • YouTube"
