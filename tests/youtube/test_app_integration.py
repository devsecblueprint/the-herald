"""How the feature attaches to The Herald's FastAPI application."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.youtube.api import YouTubeTriggerController
from app.services.youtube.errors import ConfigurationError
from tests.youtube.harness import build_harness

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def at(day, hour=12):
    return BASE + timedelta(days=day - 1, hours=hour)


@pytest.fixture
def main(monkeypatch):
    """app.main with its YouTube controller reset between tests."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "youtube_controller", None)
    return main_module


def body_of(response):
    return json.loads(response.body)


def test_the_endpoints_report_unavailable_when_youtube_is_not_configured(main):
    for endpoint in (main.trigger_youtube, main.health_youtube):
        response = asyncio.run(endpoint())
        assert response.status_code == 503
        assert body_of(response)["status"] == "unavailable"


def test_the_trigger_endpoint_runs_a_poll_once_configured(main, monkeypatch):
    herald = build_harness(now=at(30))
    herald.publish("handle:@damienjburks", [("old", at(20))])
    monkeypatch.setattr(main, "youtube_controller", YouTubeTriggerController(herald.pipeline))

    response = asyncio.run(main.trigger_youtube())

    assert response.status_code == 200
    assert body_of(response)["sources_onboarded"] == 1


def test_an_unconfigured_feature_does_not_stop_the_herald_starting(main, monkeypatch):
    def unconfigured(**kwargs):
        raise ConfigurationError("HERALD_DEDUP_TABLE_NAME is required")

    monkeypatch.setattr(main, "build_pipeline", unconfigured)
    monkeypatch.setattr(main, "initialize_clients", lambda: (None, None))

    main.configure_youtube()

    assert main.youtube_controller is None


def test_a_disabled_feature_registers_no_job(main, monkeypatch):
    from tests.youtube.harness import build_config

    herald = build_harness(config=build_config(enabled=False))
    registered = []

    monkeypatch.setattr(main, "build_pipeline", lambda **kwargs: herald.pipeline)
    monkeypatch.setattr(main, "initialize_clients", lambda: (None, None))
    monkeypatch.setattr(main, "register_youtube_job", lambda *a, **k: registered.append(a))

    main.configure_youtube()

    assert registered == []
    # The endpoints still work, so /health/youtube can explain the silence.
    assert main.youtube_controller is not None


def test_an_enabled_feature_registers_the_poll(main, monkeypatch):
    herald = build_harness()
    registered = []

    monkeypatch.setattr(main, "build_pipeline", lambda **kwargs: herald.pipeline)
    monkeypatch.setattr(main, "initialize_clients", lambda: (None, None))
    monkeypatch.setattr(main, "register_youtube_job", lambda *a, **k: registered.append(a))

    main.configure_youtube()

    assert registered == [(main.scheduler, herald.pipeline)]


def test_the_main_health_endpoint_reports_whether_youtube_is_configured(main):
    assert asyncio.run(main.health())["youtube_configured"] is False
