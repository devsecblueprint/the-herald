"""Message generation and the Discord transports."""

import json

import pytest

from app.services.youtube.clock import to_iso
from app.services.youtube.distribution import (
    DEFAULT_COLOUR,
    RELATIONSHIP_COLOURS,
    BotTokenTransport,
    DiscordDistributionService,
    WebhookTransport,
    build_message,
    truncate,
)
from app.services.youtube.errors import AmbiguousDeliveryError, DistributionError
from app.services.youtube.http import HttpError, HttpResponse, HttpResponseIncomplete
from tests.youtube.fakes import FakeClock, FakeHttpClient, RecordingTransport, make_item

CHANNEL = "123456789012345678"


def accepted(message_id="987654321098765432"):
    return HttpResponse(status_code=200, text=json.dumps({"id": message_id}))


def transport(http):
    return BotTokenTransport(http, "token", base_delay=0, sleeper=lambda _s: None)


# -- the message ------------------------------------------------------------


def test_the_content_line_names_the_partner():
    payload = build_message(make_item())
    assert payload["content"] == "\U0001F4FA New from **Damien Burks** on YouTube"


def test_announcements_never_ping_a_channel():
    assert build_message(make_item())["allowed_mentions"] == {"parse": []}


def test_the_embed_carries_the_video():
    item = make_item("abc123")
    embed = build_message(item)["embeds"][0]

    assert embed["title"] == item.title
    assert embed["url"] == item.url
    assert embed["description"] == "A walkthrough."
    assert embed["timestamp"] == to_iso(item.published_at)
    assert embed["thumbnail"] == {"url": item.thumbnail_url}
    assert embed["author"] == {"name": item.author_name, "url": item.author_url}
    assert embed["footer"] == {"text": "Damien Burks • Community Partner • YouTube"}


def test_the_raw_url_lives_in_a_field_so_discord_adds_no_second_preview():
    embed = build_message(make_item("abc123"))["embeds"][0]
    assert embed["fields"] == [
        {"name": "Watch", "value": "https://www.youtube.com/watch?v=abc123", "inline": False}
    ]
    assert "youtube.com" not in build_message(make_item("abc123"))["content"]


@pytest.mark.parametrize("relationship", sorted(RELATIONSHIP_COLOURS))
def test_the_colour_is_keyed_to_the_relationship(relationship):
    item = make_item(relationship=relationship)
    assert build_message(item)["embeds"][0]["color"] == RELATIONSHIP_COLOURS[relationship]


def test_an_unknown_relationship_gets_the_default_colour():
    assert build_message(make_item(relationship="ALUMNI"))["embeds"][0]["color"] == DEFAULT_COLOUR


def test_a_long_description_is_trimmed_to_400_characters():
    embed = build_message(make_item(description="x" * 900))["embeds"][0]
    assert len(embed["description"]) == 400
    assert embed["description"].endswith("…")


def test_a_long_title_is_trimmed_to_256_characters():
    assert len(build_message(make_item(title="y" * 400))["embeds"][0]["title"]) == 256


def test_an_empty_description_is_omitted():
    assert "description" not in build_message(make_item(description=""))["embeds"][0]


def test_the_plain_style_posts_the_bare_url():
    payload = build_message(make_item("abc123"), style="plain")
    assert payload == {
        "content": "https://www.youtube.com/watch?v=abc123",
        "allowed_mentions": {"parse": []},
    }


def test_truncate_leaves_short_text_alone():
    assert truncate("short", 100) == "short"
    assert truncate(None, 10) == ""


# -- the bot transport ------------------------------------------------------


def test_a_successful_post_returns_the_message_id():
    http = FakeHttpClient().add("POST", "discord.com", accepted())
    assert transport(http).send(CHANNEL, {"content": "hi"}) == "987654321098765432"
    assert http.calls[0]["url"].endswith(f"/channels/{CHANNEL}/messages")
    assert http.calls[0]["headers"]["Authorization"] == "Bot token"


def test_a_rejection_is_a_confirmed_failure():
    http = FakeHttpClient().add("POST", "discord.com", HttpResponse(status_code=403, text="nope"))
    with pytest.raises(DistributionError, match="HTTP 403"):
        transport(http).send(CHANNEL, {})


def test_a_rate_limit_is_retried_and_then_succeeds():
    http = FakeHttpClient().add(
        "POST",
        "discord.com",
        HttpResponse(status_code=429, headers={"Retry-After": "0"}),
        accepted(),
    )
    assert transport(http).send(CHANNEL, {}) == "987654321098765432"
    assert len(http.calls) == 2


def test_a_server_error_is_retried_and_then_succeeds():
    http = FakeHttpClient().add(
        "POST", "discord.com", HttpResponse(status_code=502, text=""), accepted()
    )
    assert transport(http).send(CHANNEL, {}) == "987654321098765432"


def test_exhausted_retries_are_a_confirmed_failure():
    http = FakeHttpClient().add("POST", "discord.com", HttpResponse(status_code=503, text=""))
    with pytest.raises(DistributionError, match="after 5 attempts"):
        transport(http).send(CHANNEL, {})
    assert len(http.calls) == 5


def test_a_connection_error_is_retried_then_reported_as_a_failure():
    http = FakeHttpClient().add("POST", "discord.com", HttpError("connection refused"))
    with pytest.raises(DistributionError):
        transport(http).send(CHANNEL, {})


def test_a_dropped_connection_is_ambiguous_and_never_retried():
    # The request was on the wire: retrying could announce it twice.
    http = FakeHttpClient().add("POST", "discord.com", HttpResponseIncomplete("read timed out"))
    with pytest.raises(AmbiguousDeliveryError, match="outcome unknown"):
        transport(http).send(CHANNEL, {})
    assert len(http.calls) == 1


def test_an_unparseable_success_body_is_ambiguous():
    http = FakeHttpClient().add("POST", "discord.com", HttpResponse(status_code=200, text="<html>"))
    with pytest.raises(AmbiguousDeliveryError, match="unparseable body"):
        transport(http).send(CHANNEL, {})


def test_a_success_with_no_message_id_is_ambiguous():
    http = FakeHttpClient().add("POST", "discord.com", HttpResponse(status_code=204, text="{}"))
    with pytest.raises(AmbiguousDeliveryError, match="no message id"):
        transport(http).send(CHANNEL, {})


# -- the webhook transport --------------------------------------------------


def test_a_webhook_post_asks_discord_to_wait_for_the_message():
    http = FakeHttpClient().add("POST", "hooks", accepted())
    hook = WebhookTransport(http, {CHANNEL: "https://discord.com/api/webhooks/1/abc"})
    assert hook.send(CHANNEL, {}) == "987654321098765432"
    assert http.calls[0]["url"].endswith("?wait=true")


def test_a_channel_with_no_webhook_is_a_confirmed_failure():
    hook = WebhookTransport(FakeHttpClient(), {})
    with pytest.raises(DistributionError, match="no webhook configured"):
        hook.send(CHANNEL, {})


# -- the service ------------------------------------------------------------


def test_the_service_returns_a_receipt():
    clock = FakeClock()
    recorder = RecordingTransport()
    service = DiscordDistributionService(recorder, CHANNEL, clock=clock)

    receipt = service.distribute(make_item("abc123"))

    assert receipt.channel_id == CHANNEL
    assert receipt.message_id == recorder.message_ids[0]
    assert receipt.posted_at == clock()
    assert recorder.sent[0]["payload"]["embeds"][0]["url"].endswith("abc123")


def test_the_configured_style_is_honoured():
    recorder = RecordingTransport()
    service = DiscordDistributionService(recorder, CHANNEL, message_style="plain")
    service.distribute(make_item("abc123"))
    assert recorder.sent[0]["payload"]["content"] == "https://www.youtube.com/watch?v=abc123"


def test_the_post_delay_applies_between_consecutive_posts_only():
    slept = []
    service = DiscordDistributionService(
        RecordingTransport(), CHANNEL, post_delay_seconds=3, sleeper=slept.append
    )
    service.distribute(make_item("one"))
    service.distribute(make_item("two"))
    assert slept == [3]


def test_no_delay_is_taken_when_none_is_configured():
    slept = []
    service = DiscordDistributionService(RecordingTransport(), CHANNEL, sleeper=slept.append)
    service.distribute(make_item("one"))
    service.distribute(make_item("two"))
    assert slept == []
