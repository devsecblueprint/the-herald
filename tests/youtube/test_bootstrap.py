"""Production wiring, and the three ways to start a poll."""

import json

import pytest
from apscheduler.triggers.interval import IntervalTrigger

from app.bootstrap import (
    JOB_ID,
    build_polling_service,
    make_lambda_handler,
    register_youtube_job,
    run_forever,
)
from app.clients.discord import BotTokenTransport, WebhookTransport
from app.errors import ConfigurationError
from app.services.youtube.classification import (
    DataApiShortsDetector,
    ShortsUrlProbeDetector,
)
from tests.youtube.fakes import FakeHttpClient, FakeTable
from tests.youtube.harness import build_config, build_harness

DOCUMENT = {
    "youtube": {
        "discord_channel_id": "123456789012345678",
        "youtube_sources": [
            {
                "name": "Damien Burks",
                "relationship": "COMMUNITY_PARTNER",
                "channel": "@damienjburks",
            }
        ],
    }
}


def build(env=None, **kwargs):
    return build_polling_service(
        DOCUMENT,
        env={"HERALD_DISCORD_BOT_TOKEN": "token", **(env or {})},
        table=FakeTable(),
        http_client=FakeHttpClient(),
        **kwargs,
    )


# -- assembling the service -------------------------------------------------


def test_a_bot_token_is_the_preferred_transport():
    polling = build()
    assert isinstance(polling.publishing.transport, BotTokenTransport)
    assert polling.config.discord_channel_id == "123456789012345678"


def test_webhooks_are_used_only_when_there_is_no_bot_token():
    polling = build_polling_service(
        DOCUMENT,
        env={
            "HERALD_DISCORD_WEBHOOKS": json.dumps(
                {"123456789012345678": "https://discord.com/api/webhooks/1/abc"}
            )
        },
        table=FakeTable(),
        http_client=FakeHttpClient(),
    )
    assert isinstance(polling.publishing.transport, WebhookTransport)


def test_parameter_store_supplies_the_token_when_the_environment_does_not():
    class StubParameterStore:
        def get_discord_token(self):
            return "from-parameter-store"

    polling = build_polling_service(
        DOCUMENT,
        env={},
        table=FakeTable(),
        http_client=FakeHttpClient(),
        parameter_store_client=StubParameterStore(),
    )
    assert polling.publishing.transport.token == "from-parameter-store"


def test_no_transport_at_all_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="No Discord transport"):
        build_polling_service(
            DOCUMENT, env={}, table=FakeTable(), http_client=FakeHttpClient()
        )


def test_malformed_webhook_json_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="must be JSON"):
        build_polling_service(
            DOCUMENT,
            env={"HERALD_DISCORD_WEBHOOKS": "not json"},
            table=FakeTable(),
            http_client=FakeHttpClient(),
        )


def test_a_missing_table_name_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="HERALD_DEDUP_TABLE_NAME"):
        build_polling_service(
            DOCUMENT,
            env={"HERALD_DISCORD_BOT_TOKEN": "token"},
            http_client=FakeHttpClient(),
        )


def test_the_probe_detector_is_wired_by_default():
    assert isinstance(build().publishing.classifier, ShortsUrlProbeDetector)


def test_an_api_key_wires_the_data_api_detector():
    polling = build(env={"HERALD_YOUTUBE_API_KEY": "key"})
    assert isinstance(polling.publishing.classifier, DataApiShortsDetector)
    assert polling.ingestion.client.api_key == "key"


def test_one_youtube_client_is_shared_by_ingestion_and_classification():
    polling = build(env={"HERALD_YOUTUBE_API_KEY": "key"})
    assert polling.publishing.classifier.client is polling.ingestion.client


def test_the_dedupe_attribute_names_are_configurable():
    polling = build(
        env={
            "HERALD_DEDUP_KEY_ATTRIBUTE": "pk",
            "HERALD_DEDUP_TTL_ATTRIBUTE": "expires_at",
        }
    )
    assert polling.publishing.repository.key_attribute == "pk"
    assert polling.publishing.repository.ttl_attribute == "expires_at"
    assert polling.roster.key_attribute == "pk"


def test_an_already_built_config_is_used_as_is():
    config = build_config(poll_interval_minutes=45)
    polling = build_polling_service(
        config,
        env={"HERALD_DISCORD_BOT_TOKEN": "token"},
        table=FakeTable(),
        http_client=FakeHttpClient(),
    )
    assert polling.config.poll_interval_minutes == 45


# -- the scheduled job ------------------------------------------------------


class RecordingScheduler:
    """Captures the arguments a job is registered with."""

    def __init__(self):
        self.jobs = []

    def add_job(self, func, **kwargs):
        self.jobs.append({"func": func, **kwargs})
        return kwargs


def test_the_job_cannot_stack_up_behind_itself():
    scheduler = RecordingScheduler()
    herald = build_harness()

    register_youtube_job(scheduler, herald.polling)

    job = scheduler.jobs[0]
    assert job["id"] == JOB_ID
    assert job["max_instances"] == 1
    assert job["coalesce"] is True
    assert job["replace_existing"] is True
    assert job["func"] == herald.polling.run


def test_the_interval_comes_from_the_configuration():
    scheduler = RecordingScheduler()
    herald = build_harness(config=build_config(poll_interval_minutes=45))

    register_youtube_job(scheduler, herald.polling)

    trigger = scheduler.jobs[0]["trigger"]
    assert isinstance(trigger, IntervalTrigger)
    assert trigger.interval.total_seconds() == 45 * 60


# -- the worker loop --------------------------------------------------------


def test_the_worker_loop_polls_and_sleeps_for_the_interval():
    herald = build_harness()
    slept = []

    run_forever(herald.polling, sleeper=slept.append, iterations=3)

    assert herald.polling.last_result is not None
    # The last iteration returns rather than sleeping for nothing.
    assert slept == [15 * 60, 15 * 60]


def test_the_worker_loop_survives_a_crashing_poll():
    herald = build_harness()

    def explode():
        raise RuntimeError("unexpected")

    herald.polling.run = explode
    run_forever(herald.polling, sleeper=lambda _s: None, iterations=2)


# -- the Lambda handler -----------------------------------------------------


def test_the_lambda_handler_returns_the_poll_summary():
    herald = build_harness()
    handler = make_lambda_handler(lambda: herald.polling)

    body = handler({}, None)

    assert body["status"] == "ok"
    assert body["sources_onboarded"] == 1


def test_the_lambda_handler_reuses_the_service_across_invocations():
    built = []

    def factory():
        herald = build_harness()
        built.append(herald)
        return herald.polling

    handler = make_lambda_handler(factory)
    handler({}, None)
    handler({}, None)

    assert len(built) == 1
