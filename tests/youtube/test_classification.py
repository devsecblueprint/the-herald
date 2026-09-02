"""Shorts detection across all three signals."""

import json

import pytest

from app.clients.http import HttpError, HttpResponse
from app.clients.youtube import YouTubeClient, parse_duration_seconds
from app.errors import ClassificationError
from app.models.youtube import (SKIP_REASON_LIVE, SKIP_REASON_PREMIERE,
                                SKIP_REASON_SHORT)
from app.services.youtube.classification import (DataApiShortsDetector,
                                                 HeuristicShortsDetector,
                                                 NullShortsDetector,
                                                 ShortsUrlProbeDetector,
                                                 build_shorts_detector)
from tests.youtube.fakes import FakeHttpClient, make_item


def api_video(duration=None, broadcast="none"):
    entry = {"snippet": {"liveBroadcastContent": broadcast}}
    if duration is not None:
        entry["contentDetails"] = {"duration": duration}
    return HttpResponse(status_code=200, text=json.dumps({"items": [entry]}))


# -- the offline heuristic --------------------------------------------------


@pytest.mark.parametrize(
    "title, description",
    [("A quick tip #shorts", ""), ("A quick tip", "watch more #Shorts"), ("#short", "")],
)
def test_the_heuristic_spots_a_shorts_tag(title, description):
    verdict = HeuristicShortsDetector().classify(
        make_item(title=title, description=description)
    )
    assert verdict.is_short is True
    assert verdict.reason == SKIP_REASON_SHORT


def test_the_heuristic_leaves_an_untagged_video_alone():
    assert HeuristicShortsDetector().classify(make_item()).is_short is False


def test_the_heuristic_needs_no_network():
    item = make_item(title="A quick tip #shorts")
    http = FakeHttpClient()
    HeuristicShortsDetector().classify(item)
    assert http.calls == []


# -- the URL probe ----------------------------------------------------------


def test_a_real_short_answers_200_to_the_probe():
    http = FakeHttpClient().add("HEAD", "/shorts/", HttpResponse(status_code=200))
    verdict = ShortsUrlProbeDetector(YouTubeClient(http)).classify(make_item("abc123"))
    assert (verdict.is_short, verdict.detector) == (True, "url_probe")
    assert http.calls[0]["url"] == "https://www.youtube.com/shorts/abc123"


def test_a_long_form_video_is_redirected_to_watch():
    http = FakeHttpClient().add(
        "HEAD",
        "/shorts/",
        HttpResponse(status_code=303, headers={"Location": "/watch?v=abc123"}),
    )
    assert ShortsUrlProbeDetector(YouTubeClient(http)).classify(make_item()).is_short is False


def test_the_probe_does_not_follow_redirects():
    http = FakeHttpClient().add("HEAD", "/shorts/", HttpResponse(status_code=302))
    ShortsUrlProbeDetector(YouTubeClient(http)).classify(make_item())
    assert http.calls[0]["allow_redirects"] is False


def test_an_unexpected_probe_status_is_undecidable_not_a_guess():
    # Guessing would mean either a Short in #content-corner or a partner's
    # real video silently dropped.
    http = FakeHttpClient().add("HEAD", "/shorts/", HttpResponse(status_code=404))
    with pytest.raises(ClassificationError, match="HTTP 404"):
        ShortsUrlProbeDetector(YouTubeClient(http)).classify(make_item())


def test_a_probe_timeout_is_undecidable():
    http = FakeHttpClient().add("HEAD", "/shorts/", HttpError("read timed out"))
    with pytest.raises(ClassificationError, match="probe failed"):
        ShortsUrlProbeDetector(YouTubeClient(http)).classify(make_item())


# -- the Data API -----------------------------------------------------------


@pytest.mark.parametrize(
    "duration, is_short",
    [
        ("PT30S", True),
        ("PT2M59S", True),
        ("PT3M", True),  # exactly 180 seconds is still a Short
        ("PT3M1S", False),  # 181 seconds is long-form
        ("PT12M4S", False),
        ("PT1H2M3S", False),
    ],
)
def test_duration_is_compared_against_the_180_second_boundary(duration, is_short):
    http = FakeHttpClient().add("GET", "googleapis.com", api_video(duration))
    verdict = DataApiShortsDetector(YouTubeClient(http, "key")).classify(make_item())
    assert verdict.is_short is is_short


def test_a_live_broadcast_is_not_announced():
    http = FakeHttpClient().add("GET", "googleapis.com", api_video("PT0S", broadcast="live"))
    verdict = DataApiShortsDetector(YouTubeClient(http, "key")).classify(make_item())
    assert (verdict.is_short, verdict.reason) == (True, SKIP_REASON_LIVE)


def test_an_unfinished_premiere_is_not_announced():
    http = FakeHttpClient().add("GET", "googleapis.com", api_video("P0D", broadcast="upcoming"))
    verdict = DataApiShortsDetector(YouTubeClient(http, "key")).classify(make_item())
    assert (verdict.is_short, verdict.reason) == (True, SKIP_REASON_PREMIERE)


def test_a_zero_duration_falls_back_rather_than_reading_it_as_short():
    # The API returns P0D for streams; reading the zero as "under 180
    # seconds" would drop a real video.
    http = (
        FakeHttpClient()
        .add("GET", "googleapis.com", api_video("P0D"))
        .add("HEAD", "/shorts/", HttpResponse(status_code=303))
    )
    detector = DataApiShortsDetector(
        YouTubeClient(http, "key"), fallback=ShortsUrlProbeDetector(YouTubeClient(http))
    )
    verdict = detector.classify(make_item())
    assert (verdict.is_short, verdict.detector) == (False, "url_probe")


def test_a_missing_duration_falls_back_to_the_probe():
    http = (
        FakeHttpClient()
        .add("GET", "googleapis.com", api_video(None))
        .add("HEAD", "/shorts/", HttpResponse(status_code=200))
    )
    detector = DataApiShortsDetector(
        YouTubeClient(http, "key"), fallback=ShortsUrlProbeDetector(YouTubeClient(http))
    )
    assert detector.classify(make_item()).is_short is True


def test_an_api_outage_falls_back_to_the_probe():
    http = (
        FakeHttpClient()
        .add("GET", "googleapis.com", HttpResponse(status_code=403, text="quota"))
        .add("HEAD", "/shorts/", HttpResponse(status_code=303))
    )
    detector = DataApiShortsDetector(
        YouTubeClient(http, "key"), fallback=ShortsUrlProbeDetector(YouTubeClient(http))
    )
    assert detector.classify(make_item()).is_short is False


def test_an_api_outage_with_no_fallback_is_undecidable():
    http = FakeHttpClient().add("GET", "googleapis.com", HttpResponse(status_code=500, text=""))
    with pytest.raises(ClassificationError, match="HTTP 500"):
        DataApiShortsDetector(YouTubeClient(http, "key")).classify(make_item())


def test_an_unknown_video_falls_back():
    http = (
        FakeHttpClient()
        .add("GET", "googleapis.com", HttpResponse(status_code=200, text=json.dumps({"items": []})))
        .add("HEAD", "/shorts/", HttpResponse(status_code=303))
    )
    detector = DataApiShortsDetector(
        YouTubeClient(http, "key"), fallback=ShortsUrlProbeDetector(YouTubeClient(http))
    )
    assert detector.classify(make_item()).is_short is False


# -- durations --------------------------------------------------------------


@pytest.mark.parametrize(
    "duration, seconds",
    [("PT30S", 30), ("PT3M", 180), ("PT1H", 3600), ("P1DT2H3M4S", 93784), ("P0D", 0)],
)
def test_iso_durations_parse(duration, seconds):
    assert parse_duration_seconds(duration) == seconds


@pytest.mark.parametrize("duration", [None, "", "3 minutes", "PTX"])
def test_unparseable_durations_return_none(duration):
    assert parse_duration_seconds(duration) is None


# -- selection --------------------------------------------------------------


def test_disabling_shorts_filtering_skips_classification_entirely():
    http = FakeHttpClient()
    detector = build_shorts_detector(YouTubeClient(http), exclude_shorts=False)
    assert isinstance(detector, NullShortsDetector)
    assert detector.classify(make_item(title="A quick tip #shorts")).is_short is False
    assert http.calls == []


def test_the_probe_is_the_default_detector():
    assert isinstance(build_shorts_detector(YouTubeClient(FakeHttpClient())), ShortsUrlProbeDetector)


def test_an_api_key_promotes_the_data_api_detector_with_the_probe_behind_it():
    detector = build_shorts_detector(YouTubeClient(FakeHttpClient(), api_key="key"))
    assert isinstance(detector, DataApiShortsDetector)
    assert isinstance(detector.fallback, ShortsUrlProbeDetector)
