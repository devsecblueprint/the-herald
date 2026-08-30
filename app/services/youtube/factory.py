"""
Wiring.

Every external dependency is injected, so the test suite runs against an
in-memory DynamoDB fake, canned Atom feeds and a recording Discord
transport. ``build_pipeline()`` is the production wiring of the same parts.
"""

import json
import os
from typing import Any, Mapping, Optional, Union

from app.services.youtube.config import YouTubeConfig, load_config
from app.services.youtube.distribution import (
    BotTokenTransport,
    DiscordDistributionService,
    WebhookTransport,
)
from app.services.youtube.errors import ConfigurationError
from app.services.youtube.http import RequestsHttpClient
from app.services.youtube.ingestion import YouTubeIngestionService
from app.services.youtube.logging_utils import EventLogger
from app.services.youtube.pipeline import YouTubePipeline
from app.services.youtube.repository import (
    DEFAULT_TTL_DAYS,
    ChannelReferenceCache,
    ProcessingRepository,
    RosterRepository,
)
from app.services.youtube.resolver import ChannelResolver
from app.services.youtube.shorts import build_shorts_detector


# pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
def build_pipeline(
    config: Optional[Union[str, Mapping[str, Any], YouTubeConfig]] = None,
    env: Optional[Mapping[str, str]] = None,
    table=None,
    http_client=None,
    parameter_store_client=None,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> YouTubePipeline:
    """
    Assemble a ready-to-run pipeline from the environment.

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
    resolved_config = config if isinstance(config, YouTubeConfig) else load_config(config, env)

    key_attribute = env.get("HERALD_DEDUP_KEY_ATTRIBUTE", "content_id")
    ttl_attribute = env.get("HERALD_DEDUP_TTL_ATTRIBUTE", "ttl")
    table = table if table is not None else _dynamodb_table(env)

    http = http_client or RequestsHttpClient()
    api_key = env.get("HERALD_YOUTUBE_API_KEY") or None
    events = EventLogger("app.services.youtube")

    cache = ChannelReferenceCache(
        table, key_attribute=key_attribute, ttl_attribute=ttl_attribute
    )
    resolver = ChannelResolver(http, cache=cache, api_key=api_key, event_logger=events)
    ingestion = YouTubeIngestionService(http, resolver=resolver)
    detector = build_shorts_detector(http, resolved_config.exclude_shorts, api_key)

    transport = _discord_transport(env, http, parameter_store_client)
    distribution = DiscordDistributionService(
        transport,
        resolved_config.discord_channel_id,
        message_style=resolved_config.message_style,
        post_delay_seconds=resolved_config.post_delay_seconds,
    )

    repository = ProcessingRepository(
        table,
        key_attribute=key_attribute,
        ttl_attribute=ttl_attribute,
        ttl_days=ttl_days,
    )
    roster = RosterRepository(table, key_attribute=key_attribute)

    return YouTubePipeline(
        config=resolved_config,
        ingestion=ingestion,
        distribution=distribution,
        repository=repository,
        roster_repository=roster,
        shorts_detector=detector,
        event_logger=events,
    )


def _dynamodb_table(env: Mapping[str, str]):
    """Resolve the DynamoDB table this feature shares with the dedupe store."""
    table_name = env.get("HERALD_DEDUP_TABLE_NAME")
    if not table_name:
        raise ConfigurationError(
            "HERALD_DEDUP_TABLE_NAME is required to store YouTube dedupe records"
        )
    # Imported lazily so the test suite never needs AWS.
    import boto3  # pylint: disable=import-outside-toplevel

    return boto3.resource("dynamodb", region_name=env.get("AWS_REGION")).Table(table_name)


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
                "HERALD_DISCORD_WEBHOOKS must be JSON of {\"<channel id>\": \"<webhook url>\"}"
            ) from exc
        if not isinstance(webhooks, dict) or not webhooks:
            raise ConfigurationError("HERALD_DISCORD_WEBHOOKS must be a non-empty JSON object")
        return WebhookTransport(http, webhooks)

    raise ConfigurationError(
        "No Discord transport configured: set HERALD_DISCORD_BOT_TOKEN, provide a "
        "Parameter Store client, or set HERALD_DISCORD_WEBHOOKS"
    )
