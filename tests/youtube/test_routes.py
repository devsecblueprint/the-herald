"""The manual trigger and health endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import FeedFetchError
from app.models.youtube import PollResult, SourceFailure
from app.routes.youtube import YouTubeTriggerController, create_fastapi_router
from tests.youtube.harness import build_harness

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def at(day, hour=12):
    return BASE + timedelta(days=day - 1, hours=hour)


class StubPolling:
    """A polling service that returns a canned result."""

    def __init__(self, result):
        self.result = result
        self.runs = 0

    def run(self):
        self.runs += 1
        return self.result

    def health(self):
        return {"status": "healthy", "sources_configured": 0}


def test_a_clean_poll_answers_200():
    controller = YouTubeTriggerController(StubPolling(PollResult()))
    assert controller.trigger()[0] == 200


def test_a_poll_with_failures_answers_207():
    result = PollResult(failures=[SourceFailure(stage="ingestion", error="boom")])
    assert YouTubeTriggerController(StubPolling(result)).trigger()[0] == 207


def test_a_poll_that_was_already_running_answers_409():
    assert (
        YouTubeTriggerController(StubPolling(PollResult(skipped=True))).trigger()[0]
        == 409
    )


def test_a_disabled_feature_answers_200():
    assert (
        YouTubeTriggerController(StubPolling(PollResult(disabled=True))).trigger()[0]
        == 200
    )


def client_for(polling):
    app = FastAPI()
    app.include_router(create_fastapi_router(YouTubeTriggerController(polling)))
    return TestClient(app)


def test_the_trigger_endpoint_runs_a_poll_and_returns_its_summary():
    herald = build_harness(now=at(30))
    herald.publish("handle:@damienjburks", [("old", at(20))])

    response = client_for(herald.polling).post("/trigger/youtube")

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["sources_onboarded"] == 1
    assert body["announcements_published"] == 0
    assert body["ttl_days"] == 35


def test_the_trigger_endpoint_reports_failures_with_207():
    herald = build_harness(now=at(30))
    herald.fail("handle:@damienjburks", FeedFetchError("feed returned HTTP 503"))

    response = client_for(herald.polling).post("/trigger/youtube")

    assert response.status_code == 207
    assert response.json()["failures"][0]["stage"] == "ingestion"


def test_the_announcement_list_carries_the_discord_ids():
    herald = build_harness(now=at(30))
    herald.publish("handle:@damienjburks", [("old", at(20))])
    herald.run()
    herald.clock.advance(days=1)
    herald.publish("handle:@damienjburks", [("fresh", at(31))])

    body = client_for(herald.polling).post("/trigger/youtube").json()

    announcement = body["announced"][0]
    assert announcement["video_id"] == "fresh"
    assert announcement["source_name"] == "Damien Burks"
    assert announcement["published_at"] == "2026-08-31T12:00:00Z"
    assert announcement["discord_message_id"]


def test_the_health_endpoint_describes_the_configuration():
    herald = build_harness(now=at(30))
    body = client_for(herald.polling).get("/health/youtube").json()

    assert body["status"] == "healthy"
    assert body["enabled"] is True
    assert body["discord_channel_name"] == "content-corner"
    assert body["sources_configured"] == 1
    assert body["last_run"] is None


def test_health_includes_the_last_run_once_there_has_been_one():
    herald = build_harness(now=at(30))
    herald.publish("handle:@damienjburks", [("old", at(20))])
    herald.run()

    body = client_for(herald.polling).get("/health/youtube").json()
    assert body["last_run"]["sources_onboarded"] == 1
