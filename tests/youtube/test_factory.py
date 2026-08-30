"""Production wiring."""

import json

import pytest

from app.services.youtube.distribution import BotTokenTransport, WebhookTransport
from app.services.youtube.errors import ConfigurationError
from app.services.youtube.factory import build_pipeline
from app.services.youtube.shorts import DataApiShortsDetector, ShortsUrlProbeDetector
from tests.youtube.fakes import FakeHttpClient, FakeTable

DOCUMENT = {
    "youtube": {
        "discord_channel_id": "123456789012345678",
        "youtube_sources": [
            {"name": "Damien Burks", "relationship": "COMMUNITY_PARTNER", "channel": "@damienjburks"}
        ],
    }
}


def build(env=None, **kwargs):
    return build_pipeline(
        DOCUMENT,
        env={"HERALD_DISCORD_BOT_TOKEN": "token", **(env or {})},
        table=FakeTable(),
        http_client=FakeHttpClient(),
        **kwargs,
    )


def test_a_bot_token_is_the_preferred_transport():
    pipeline = build()
    assert isinstance(pipeline.distribution.transport, BotTokenTransport)
    assert pipeline.config.discord_channel_id == "123456789012345678"


def test_webhooks_are_used_only_when_there_is_no_bot_token():
    pipeline = build_pipeline(
        DOCUMENT,
        env={
            "HERALD_DISCORD_WEBHOOKS": json.dumps(
                {"123456789012345678": "https://discord.com/api/webhooks/1/abc"}
            )
        },
        table=FakeTable(),
        http_client=FakeHttpClient(),
    )
    assert isinstance(pipeline.distribution.transport, WebhookTransport)


def test_parameter_store_supplies_the_token_when_the_environment_does_not():
    class StubParameterStore:
        def get_discord_token(self):
            return "from-parameter-store"

    pipeline = build_pipeline(
        DOCUMENT,
        env={},
        table=FakeTable(),
        http_client=FakeHttpClient(),
        parameter_store_client=StubParameterStore(),
    )
    assert pipeline.distribution.transport.token == "from-parameter-store"


def test_no_transport_at_all_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="No Discord transport"):
        build_pipeline(DOCUMENT, env={}, table=FakeTable(), http_client=FakeHttpClient())


def test_malformed_webhook_json_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="must be JSON"):
        build_pipeline(
            DOCUMENT,
            env={"HERALD_DISCORD_WEBHOOKS": "not json"},
            table=FakeTable(),
            http_client=FakeHttpClient(),
        )


def test_a_missing_table_name_is_a_configuration_error():
    with pytest.raises(ConfigurationError, match="HERALD_DEDUP_TABLE_NAME"):
        build_pipeline(
            DOCUMENT,
            env={"HERALD_DISCORD_BOT_TOKEN": "token"},
            http_client=FakeHttpClient(),
        )


def test_the_probe_detector_is_wired_by_default():
    assert isinstance(build().shorts, ShortsUrlProbeDetector)


def test_an_api_key_wires_the_data_api_detector():
    pipeline = build(env={"HERALD_YOUTUBE_API_KEY": "key"})
    assert isinstance(pipeline.shorts, DataApiShortsDetector)
    assert pipeline.ingestion.resolver.api_key == "key"


def test_the_dedupe_attribute_names_are_configurable():
    pipeline = build(
        env={"HERALD_DEDUP_KEY_ATTRIBUTE": "pk", "HERALD_DEDUP_TTL_ATTRIBUTE": "expires_at"}
    )
    assert pipeline.repository.key_attribute == "pk"
    assert pipeline.repository.ttl_attribute == "expires_at"
    assert pipeline.roster.key_attribute == "pk"


def test_an_already_built_config_is_used_as_is():
    from tests.youtube.harness import build_config

    config = build_config(poll_interval_minutes=45)
    pipeline = build_pipeline(
        config,
        env={"HERALD_DISCORD_BOT_TOKEN": "token"},
        table=FakeTable(),
        http_client=FakeHttpClient(),
    )
    assert pipeline.config.poll_interval_minutes == 45
