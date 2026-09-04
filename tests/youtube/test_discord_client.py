"""The Discord transports: retries, and the confirmed/ambiguous split."""

import json

import pytest

from app.clients.discord import BotTokenTransport, WebhookTransport
from app.clients.http import HttpError, HttpResponse, HttpResponseIncomplete
from app.errors import AmbiguousDeliveryError, DistributionError
from tests.youtube.fakes import FakeHttpClient

CHANNEL = "123456789012345678"


def accepted(message_id="987654321098765432"):
    return HttpResponse(status_code=200, text=json.dumps({"id": message_id}))


def transport(http):
    return BotTokenTransport(http, "token", base_delay=0, sleeper=lambda _s: None)


# -- the bot transport ------------------------------------------------------


def test_a_successful_post_returns_the_message_id():
    http = FakeHttpClient().add("POST", "discord.com", accepted())
    assert transport(http).send(CHANNEL, {"content": "hi"}) == "987654321098765432"
    assert http.calls[0]["url"].endswith(f"/channels/{CHANNEL}/messages")
    assert http.calls[0]["headers"]["Authorization"] == "Bot token"


def test_a_rejection_is_a_confirmed_failure():
    http = FakeHttpClient().add(
        "POST", "discord.com", HttpResponse(status_code=403, text="nope")
    )
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
    http = FakeHttpClient().add(
        "POST", "discord.com", HttpResponse(status_code=503, text="")
    )
    with pytest.raises(DistributionError, match="after 5 attempts"):
        transport(http).send(CHANNEL, {})
    assert len(http.calls) == 5


def test_a_connection_error_is_retried_then_reported_as_a_failure():
    http = FakeHttpClient().add("POST", "discord.com", HttpError("connection refused"))
    with pytest.raises(DistributionError):
        transport(http).send(CHANNEL, {})


def test_a_dropped_connection_is_ambiguous_and_never_retried():
    # The request was on the wire: retrying could announce it twice.
    http = FakeHttpClient().add(
        "POST", "discord.com", HttpResponseIncomplete("read timed out")
    )
    with pytest.raises(AmbiguousDeliveryError, match="outcome unknown"):
        transport(http).send(CHANNEL, {})
    assert len(http.calls) == 1


def test_an_unparseable_success_body_is_ambiguous():
    http = FakeHttpClient().add(
        "POST", "discord.com", HttpResponse(status_code=200, text="<html>")
    )
    with pytest.raises(AmbiguousDeliveryError, match="unparseable body"):
        transport(http).send(CHANNEL, {})


def test_a_success_with_no_message_id_is_ambiguous():
    http = FakeHttpClient().add(
        "POST", "discord.com", HttpResponse(status_code=204, text="{}")
    )
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
