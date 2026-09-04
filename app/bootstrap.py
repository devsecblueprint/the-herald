"""
Dependency assembly, in one place at the application boundary.

Every collaborator in the YouTube feature is injected, which is what lets
the test suite run against an in-memory DynamoDB table, canned Atom feeds
and a recording Discord transport. ``build_polling_service()`` is the
production wiring of the same parts, and the only module that reaches for
``boto3`` or the environment.

The three ways to run a poll -- an APScheduler job inside The Herald, a
plain worker loop, or an AWS Lambda behind an EventBridge rule -- also live
here, because each is a way of starting the assembled service rather than
part of the poll itself.
"""

import json
import os
import time
from typing import Any, Mapping, Optional, Union

import boto3
from apscheduler.triggers.interval import IntervalTrigger

from app.clients.discord import BotTokenTransport, WebhookTransport
from app.clients.http import RequestsHttpClient
from app.clients.youtube import YouTubeClient
from app.config.youtube import YouTubeConfig, load_config
from app.errors import ConfigurationError
from app.repositories.youtube.channel_cache import ChannelReferenceCache
from app.repositories.youtube.processing import DEFAULT_TTL_DAYS, ProcessingRepository
from app.repositories.youtube.roster import RosterRepository
from app.services.youtube.classification import build_shorts_detector
from app.services.youtube.ingestion import ChannelResolver, YouTubeIngestionService
from app.services.youtube.polling import YouTubePollingService
from app.services.youtube.publishing import YouTubePublishingService
from app.utils.logging import EventLogger

JOB_ID = "youtube_job"
JOB_NAME = "Announce partner YouTube uploads in Discord"


# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
def build_polling_service(
    config: Optional[Union[str, Mapping[str, Any], YouTubeConfig]] = None,
    env: Optional[Mapping[str, str]] = None,
    table=None,
    http_client=None,
    parameter_store_client=None,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> YouTubePollingService:
    """
    Assemble a ready-to-run polling service from the environment.

    Args:
        config: A ``YouTubeConfig``, a path, or an already-parsed mapping.
            Defaults to ``HERALD_YOUTUBE_CONFIG_PATH``.
        env: Environment mapping. Defaults to ``os.environ``.
        table: A DynamoDB Table resource. Defaults to the table named by
            ``HERALD_DEDUP_TABLE_NAME``.
        http_client: An ``HttpClient``. Defaults to a requests-backed one.
        parameter_store_client: Used to look up the Discord bot token when
            ``HERALD_DISCORD_BOT_TOKEN`` is not set.

    Raises:
        ConfigurationError: If the table or a Discord transport is missing.
    """
    env = os.environ if env is None else env
    resolved_config = (
        config if isinstance(config, YouTubeConfig) else load_config(config, env)
    )

    key_attribute = env.get("HERALD_DEDUP_KEY_ATTRIBUTE", "content_id")
    ttl_attribute = env.get("HERALD_DEDUP_TTL_ATTRIBUTE", "ttl")
    table = table if table is not None else _dynamodb_table(env)

    http = http_client or RequestsHttpClient()
    events = EventLogger("app.services.youtube")
    api_key = _youtube_api_key(env, parameter_store_client)
    youtube = YouTubeClient(http, api_key=api_key)

    cache = ChannelReferenceCache(
        table, key_attribute=key_attribute, ttl_attribute=ttl_attribute
    )
    resolver = ChannelResolver(youtube, cache=cache, event_logger=events)
    ingestion = YouTubeIngestionService(youtube, resolver=resolver, event_logger=events)

    publishing = YouTubePublishingService(
        repository=ProcessingRepository(
            table,
            key_attribute=key_attribute,
            ttl_attribute=ttl_attribute,
            ttl_days=ttl_days,
        ),
        classifier=build_shorts_detector(youtube, resolved_config.exclude_shorts),
        transport=_discord_transport(env, http, parameter_store_client),
        channel_id=resolved_config.discord_channel_id,
        message_style=resolved_config.message_style,
        post_delay_seconds=resolved_config.post_delay_seconds,
        event_logger=events,
    )

    return YouTubePollingService(
        config=resolved_config,
        ingestion=ingestion,
        publishing=publishing,
        roster_repository=RosterRepository(table, key_attribute=key_attribute),
        event_logger=events,
    )


def _dynamodb_table(env: Mapping[str, str]):
    """Resolve the DynamoDB table this feature shares with the dedupe store."""
    table_name = env.get("HERALD_DEDUP_TABLE_NAME")
    if not table_name:
        raise ConfigurationError(
            "HERALD_DEDUP_TABLE_NAME is required to store YouTube dedupe records"
        )
    return boto3.resource("dynamodb", region_name=env.get("AWS_REGION")).Table(
        table_name
    )


def _youtube_api_key(env: Mapping[str, str], parameter_store_client) -> Optional[str]:
    """
    Resolve the YouTube Data API key.

    The environment wins so a developer can override without touching AWS;
    otherwise it comes from Parameter Store, consistent with the Discord
    token. A missing key is not fatal -- ingestion falls back to the public
    Atom feed -- so any Parameter Store error is swallowed here.
    """
    key = env.get("HERALD_YOUTUBE_API_KEY")
    if key:
        return key

    if parameter_store_client is not None:
        try:
            return parameter_store_client.get_youtube_api_key()
        except (ValueError, AttributeError):
            return None

    return None


def _discord_transport(env: Mapping[str, str], http, parameter_store_client):
    """Pick a Discord transport: bot token first, webhooks as a fallback."""
    token = env.get("HERALD_DISCORD_BOT_TOKEN")

    if not token and parameter_store_client is not None:
        try:
            token = parameter_store_client.get_discord_token()
        except ValueError:
            token = None

    if token:
        return BotTokenTransport(http, token)

    raw_webhooks = env.get("HERALD_DISCORD_WEBHOOKS")
    if raw_webhooks:
        try:
            webhooks = json.loads(raw_webhooks)
        except ValueError as exc:
            raise ConfigurationError(
                'HERALD_DISCORD_WEBHOOKS must be JSON of {"<channel id>": "<webhook url>"}'
            ) from exc
        if not isinstance(webhooks, dict) or not webhooks:
            raise ConfigurationError(
                "HERALD_DISCORD_WEBHOOKS must be a non-empty JSON object"
            )
        return WebhookTransport(http, webhooks)

    raise ConfigurationError(
        "No Discord transport configured: set HERALD_DISCORD_BOT_TOKEN, provide a "
        "Parameter Store client, or set HERALD_DISCORD_WEBHOOKS"
    )


# -- ways to run the poll ---------------------------------------------------


def register_youtube_job(scheduler, polling_service, job_id: str = JOB_ID):
    """
    Register the poll with an APScheduler instance and return the job.

    ``max_instances=1`` and ``coalesce=True`` stop a slow poll stacking up
    behind itself. Independently of the scheduler,
    ``YouTubePollingService.run()`` holds a non-blocking lock, so the
    scheduled job and the manual trigger can never run concurrently either.
    """
    return scheduler.add_job(
        polling_service.run,
        trigger=IntervalTrigger(minutes=polling_service.config.poll_interval_minutes),
        id=job_id,
        name=JOB_NAME,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def run_forever(polling_service, sleeper=time.sleep, iterations=None) -> None:
    """
    Run the poll in a plain worker loop.

    Args:
        polling_service: The service to run.
        sleeper: Sleep function, injectable for testing.
        iterations: Stop after this many polls. None runs forever.
    """
    events = EventLogger(__name__)
    interval_seconds = polling_service.config.poll_interval_minutes * 60
    completed = 0
    while iterations is None or completed < iterations:
        try:
            polling_service.run()
        except Exception as exc:  # pragma: no cover - last-resort guard
            events.error("youtube.poll.crashed", error=str(exc))
        completed += 1
        if iterations is not None and completed >= iterations:
            return
        sleeper(interval_seconds)


def make_lambda_handler(factory):
    """
    Build an AWS Lambda handler from a polling-service factory.

    The factory is called once per cold start and the service is reused
    across invocations, so the resolver's in-memory cache survives.
    """
    state = {}

    def handler(event=None, context=None):  # pylint: disable=unused-argument
        """Run one poll and return its summary as the Lambda response."""
        if "service" not in state:
            state["service"] = factory()
        return state["service"].run().to_dict()

    return handler
