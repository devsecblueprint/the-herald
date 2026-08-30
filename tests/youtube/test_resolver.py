"""Channel handle resolution and its two cache layers."""

import json

import pytest

from app.services.youtube.config import parse_channel_reference
from app.services.youtube.errors import ChannelResolutionError
from app.services.youtube.http import HttpError, HttpResponse
from app.services.youtube.repository import ChannelReferenceCache
from app.services.youtube.resolver import ChannelResolver
from tests.youtube.fakes import FakeClock, FakeHttpClient, FakeTable, channel_page

CHANNEL_ID = "UCAAAAAAAAAAAAAAAAAAAAAA"
OTHER_CHANNEL_ID = "UCBBBBBBBBBBBBBBBBBBBBBB"


def api_response(channel_id=CHANNEL_ID):
    return HttpResponse(status_code=200, text=json.dumps({"items": [{"id": channel_id}]}))


def build(http=None, cache=None, api_key=None, clock=None):
    return ChannelResolver(
        http or FakeHttpClient(), cache=cache, api_key=api_key, clock=clock or FakeClock()
    )


# -- no lookup needed -------------------------------------------------------


def test_a_canonical_id_needs_no_lookup():
    http = FakeHttpClient()
    resolution = build(http).resolve(parse_channel_reference(CHANNEL_ID))
    assert resolution.channel_id == CHANNEL_ID
    assert resolution.origin == "config"
    assert http.calls == []


def test_a_playlist_reference_is_not_resolvable():
    from app.services.youtube.models import KIND_PLAYLIST, ChannelReference

    with pytest.raises(ChannelResolutionError, match="polled directly"):
        build().resolve(ChannelReference(KIND_PLAYLIST, "UUAAAAAAAAAAAAAAAAAAAAAA"))


# -- the Data API -----------------------------------------------------------


def test_a_handle_resolves_through_the_data_api_when_a_key_is_set():
    http = FakeHttpClient().add("GET", "googleapis.com", api_response())
    resolution = build(http, api_key="key").resolve(parse_channel_reference("@damienjburks"))
    assert (resolution.channel_id, resolution.origin) == (CHANNEL_ID, "api")
    assert "forHandle" in str(http.calls[0]["params"])


def test_a_legacy_user_resolves_through_the_data_api():
    http = FakeHttpClient().add("GET", "googleapis.com", api_response())
    build(http, api_key="key").resolve(parse_channel_reference("https://youtube.com/user/somebody"))
    assert http.calls[0]["params"]["forUsername"] == "somebody"


def test_a_vanity_url_falls_back_to_the_page_because_the_api_cannot_look_it_up():
    http = FakeHttpClient().add(
        "GET", "youtube.com/c/", HttpResponse(status_code=200, text=channel_page(CHANNEL_ID))
    )
    resolution = build(http, api_key="key").resolve(
        parse_channel_reference("https://youtube.com/c/SomeVanityName")
    )
    assert (resolution.channel_id, resolution.origin) == (CHANNEL_ID, "page")


def test_an_empty_data_api_result_is_a_resolution_failure():
    http = FakeHttpClient().add(
        "GET", "googleapis.com", HttpResponse(status_code=200, text=json.dumps({"items": []}))
    )
    with pytest.raises(ChannelResolutionError, match="no channel matched"):
        build(http, api_key="key").resolve(parse_channel_reference("@gone"))


def test_a_data_api_error_status_is_a_resolution_failure():
    http = FakeHttpClient().add("GET", "googleapis.com", HttpResponse(status_code=403, text="{}"))
    with pytest.raises(ChannelResolutionError, match="HTTP 403"):
        build(http, api_key="key").resolve(parse_channel_reference("@quota"))


def test_a_data_api_id_that_is_not_a_channel_id_is_rejected():
    http = FakeHttpClient().add(
        "GET", "googleapis.com", HttpResponse(status_code=200, text=json.dumps({"items": [{"id": "UC1"}]}))
    )
    with pytest.raises(ChannelResolutionError, match="unusable id"):
        build(http, api_key="key").resolve(parse_channel_reference("@weird"))


# -- the public page --------------------------------------------------------


@pytest.mark.parametrize("marker", ["externalId", "canonical", "identifier", "channelId"])
def test_any_one_of_the_four_page_markers_is_enough(marker):
    # One markup change must not break resolution.
    http = FakeHttpClient().add(
        "GET",
        "youtube.com/@",
        HttpResponse(status_code=200, text=channel_page(CHANNEL_ID, marker=marker)),
    )
    assert build(http).resolve(parse_channel_reference("@damienjburks")).channel_id == CHANNEL_ID


def test_a_page_with_no_marker_is_a_resolution_failure():
    http = FakeHttpClient().add(
        "GET", "youtube.com/@", HttpResponse(status_code=200, text="<html>nothing here</html>")
    )
    with pytest.raises(ChannelResolutionError, match="no channel id marker"):
        build(http).resolve(parse_channel_reference("@damienjburks"))


def test_a_renamed_handle_is_a_resolution_failure_not_a_crash():
    http = FakeHttpClient().add("GET", "youtube.com/@", HttpResponse(status_code=404, text=""))
    with pytest.raises(ChannelResolutionError, match="404"):
        build(http).resolve(parse_channel_reference("@renamed"))


def test_an_unreachable_youtube_is_a_resolution_failure():
    http = FakeHttpClient().add("GET", "youtube.com/@", HttpError("connection refused"))
    with pytest.raises(ChannelResolutionError, match="page fetch failed"):
        build(http).resolve(parse_channel_reference("@damienjburks"))


# -- caching ----------------------------------------------------------------


def test_the_memo_prevents_a_second_lookup():
    http = FakeHttpClient().add(
        "GET", "youtube.com/@", HttpResponse(status_code=200, text=channel_page(CHANNEL_ID))
    )
    resolver = build(http)
    reference = parse_channel_reference("@damienjburks")

    first = resolver.resolve(reference)
    second = resolver.resolve(reference)

    assert (first.origin, second.origin) == ("page", "memo")
    assert len(http.calls) == 1


def test_the_memo_expires_so_a_transferred_handle_is_noticed():
    # A handle can be released and taken over by a different channel; an
    # immortal memo would keep announcing the new owner under the old name.
    clock = FakeClock()
    http = FakeHttpClient().add(
        "GET",
        "youtube.com/@",
        HttpResponse(status_code=200, text=channel_page(CHANNEL_ID)),
        HttpResponse(status_code=200, text=channel_page(OTHER_CHANNEL_ID)),
    )
    resolver = build(http, clock=clock)
    reference = parse_channel_reference("@damienjburks")

    assert resolver.resolve(reference).channel_id == CHANNEL_ID
    clock.advance(hours=7)
    assert resolver.resolve(reference).channel_id == OTHER_CHANNEL_ID


def test_a_dynamodb_cache_hit_survives_a_restart():
    table = FakeTable()
    clock = FakeClock()
    cache = ChannelReferenceCache(table, clock=clock)
    http = FakeHttpClient().add(
        "GET", "youtube.com/@", HttpResponse(status_code=200, text=channel_page(CHANNEL_ID))
    )
    reference = parse_channel_reference("@damienjburks")

    build(http, cache=cache, clock=clock).resolve(reference)
    # A brand new container: no memo, but the durable cache is warm.
    restarted = build(FakeHttpClient(), cache=cache, clock=clock).resolve(reference)

    assert (restarted.channel_id, restarted.origin) == (CHANNEL_ID, "cache")


def test_the_dynamodb_cache_expires_too():
    table = FakeTable()
    clock = FakeClock()
    cache = ChannelReferenceCache(table, clock=clock)
    http = FakeHttpClient().add(
        "GET",
        "youtube.com/@",
        HttpResponse(status_code=200, text=channel_page(CHANNEL_ID)),
        HttpResponse(status_code=200, text=channel_page(OTHER_CHANNEL_ID)),
    )
    reference = parse_channel_reference("@damienjburks")
    build(http, cache=cache, clock=clock).resolve(reference)

    clock.advance(days=31)
    resolution = build(http, cache=cache, clock=clock).resolve(reference)

    assert resolution.channel_id == OTHER_CHANNEL_ID


def test_the_cache_key_includes_the_reference_kind():
    # /user/foo and /c/foo are different namespaces that can be different
    # channels, so they must never share a cache entry.
    table = FakeTable()
    clock = FakeClock()
    cache = ChannelReferenceCache(table, clock=clock)
    http = (
        FakeHttpClient()
        .add("GET", "youtube.com/user/", HttpResponse(status_code=200, text=channel_page(CHANNEL_ID)))
        .add(
            "GET",
            "youtube.com/c/",
            HttpResponse(status_code=200, text=channel_page(OTHER_CHANNEL_ID)),
        )
    )
    resolver = build(http, cache=cache, clock=clock)

    user = resolver.resolve(parse_channel_reference("https://youtube.com/user/foo"))
    vanity = resolver.resolve(parse_channel_reference("https://youtube.com/c/foo"))

    assert user.channel_id == CHANNEL_ID
    assert vanity.channel_id == OTHER_CHANNEL_ID
    assert set(table.items) == {"youtube-channel#user:foo", "youtube-channel#vanity:foo"}


def test_a_corrupt_cache_entry_is_ignored_and_re_resolved():
    table = FakeTable()
    table.items["youtube-channel#handle:@damienjburks"] = {
        "content_id": "youtube-channel#handle:@damienjburks",
        "channel_id": "not-a-channel-id",
    }
    http = FakeHttpClient().add(
        "GET", "youtube.com/@", HttpResponse(status_code=200, text=channel_page(CHANNEL_ID))
    )
    resolver = build(http, cache=ChannelReferenceCache(table))
    assert resolver.resolve(parse_channel_reference("@damienjburks")).channel_id == CHANNEL_ID
