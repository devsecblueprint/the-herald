"""
Discord distribution: a ``ContentItem`` in, a message in #content-corner out.

Deterministic and template-driven -- no AI summarisation in v1. The only
subtlety is failure classification: a confirmed rejection releases the claim
so the video is retried, while an ambiguous delivery keeps it, because a
missing audit record is cheaper than announcing a partner's video twice.
"""

import time
from typing import Any, Dict, Mapping, Optional, Protocol

from app.services.youtube.clock import to_iso, utcnow
from app.services.youtube.errors import AmbiguousDeliveryError, DistributionError
from app.services.youtube.http import HttpClient, HttpError, HttpResponseIncomplete
from app.services.youtube.models import ContentItem, DeliveryReceipt

DISCORD_API_BASE = "https://discord.com/api/v10"

MAX_TITLE_CHARS = 256
MAX_DESCRIPTION_CHARS = 400

# Embed colour, keyed to the partner's relationship with DSB.
RELATIONSHIP_COLOURS = {
    "COMMUNITY_PARTNER": 0x5865F2,
    "DSB": 0xE67E22,
    "MEMBER": 0x57F287,
    "SPONSOR": 0xFEE75C,
}
DEFAULT_COLOUR = 0x99AAB5

# Announcements never ping a channel.
NO_MENTIONS = {"parse": []}


def truncate(text: Optional[str], limit: int) -> str:
    """Trim text to a limit, marking it with an ellipsis when cut."""
    if not text:
        return ""
    collapsed = text.strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def relationship_label(relationship: str) -> str:
    """Human-readable relationship for the embed footer."""
    return (relationship or "").replace("_", " ").title()


def build_message(item: ContentItem, style: str = "embed") -> Dict[str, Any]:
    """
    Build the Discord message payload for one content item.

    The raw URL lives inside the embed rather than the content line so
    Discord does not add a second, duplicate link preview. The ``plain``
    style posts the bare URL instead and lets Discord's native YouTube
    player card do the work.
    """
    if style == "plain":
        return {"content": item.url, "allowed_mentions": NO_MENTIONS}

    embed: Dict[str, Any] = {
        "title": truncate(item.title, MAX_TITLE_CHARS),
        "url": item.url,
        "color": RELATIONSHIP_COLOURS.get(item.relationship, DEFAULT_COLOUR),
        "timestamp": to_iso(item.published_at),
        "fields": [{"name": "Watch", "value": item.url, "inline": False}],
        "footer": {
            "text": f"{item.source_name} • {relationship_label(item.relationship)} • YouTube"
        },
    }

    description = truncate(item.description, MAX_DESCRIPTION_CHARS)
    if description:
        embed["description"] = description

    if item.author_name:
        author: Dict[str, str] = {"name": item.author_name}
        if item.author_url:
            author["url"] = item.author_url
        embed["author"] = author

    if item.thumbnail_url:
        embed["thumbnail"] = {"url": item.thumbnail_url}

    return {
        "content": f"\U0001F4FA New from **{item.source_name}** on YouTube",
        "embeds": [embed],
        "allowed_mentions": NO_MENTIONS,
    }


class DiscordTransport(Protocol):
    """How a payload actually reaches Discord."""

    name: str

    def send(self, channel_id: str, payload: Mapping[str, Any]) -> str:
        """
        Send a payload and return the created message id.

        Raises:
            DistributionError: On a confirmed failure.
            AmbiguousDeliveryError: When the outcome is unknown.
        """


class _RetryingTransport:
    """Shared retry and response handling for the Discord transports."""

    name = "discord"

    def __init__(
        self,
        http_client: HttpClient,
        max_retries: int = 5,
        base_delay: float = 1.0,
        sleeper=time.sleep,
    ):
        self.http = http_client
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.sleeper = sleeper

    def _post(self, url: str, payload: Mapping[str, Any], headers: Dict[str, str]) -> str:
        """POST with rate-limit and 5xx retries, then interpret the response."""
        last_error = "no attempt was made"

        for attempt in range(self.max_retries):
            try:
                response = self.http.post(url, json=dict(payload), headers=headers)
            except HttpResponseIncomplete as exc:
                # The request was on the wire. The message may be live.
                raise AmbiguousDeliveryError(f"delivery outcome unknown: {exc}") from exc
            except HttpError as exc:
                last_error = str(exc)
                if attempt == self.max_retries - 1:
                    break
                self.sleeper(self.base_delay * (2**attempt))
                continue

            if response.status_code == 429:
                last_error = "rate limited by Discord"
                if attempt == self.max_retries - 1:
                    break
                self.sleeper(self._retry_after(response, attempt))
                continue

            if response.status_code >= 500:
                last_error = f"Discord returned HTTP {response.status_code}"
                if attempt == self.max_retries - 1:
                    break
                self.sleeper(self.base_delay * (2**attempt))
                continue

            if not response.ok:
                raise DistributionError(
                    f"Discord rejected the post with HTTP {response.status_code}: "
                    f"{truncate(response.text, 200)}"
                )

            return self._message_id(response)

        raise DistributionError(f"Discord post failed after {self.max_retries} attempts: {last_error}")

    def _retry_after(self, response, attempt: int) -> float:
        """Honour Discord's Retry-After header, else exponential backoff."""
        raw = (response.headers or {}).get("Retry-After")
        try:
            if raw is not None:
                return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
        return self.base_delay * (2**attempt)

    @staticmethod
    def _message_id(response) -> str:
        """Extract the created message id, or declare the outcome unknown."""
        try:
            body = response.json()
        except ValueError as exc:
            raise AmbiguousDeliveryError(
                f"Discord answered HTTP {response.status_code} with an unparseable body"
            ) from exc

        message_id = (body or {}).get("id")
        if not message_id:
            raise AmbiguousDeliveryError(
                f"Discord answered HTTP {response.status_code} with no message id"
            )
        return str(message_id)


class BotTokenTransport(_RetryingTransport):
    """Posts as the bot. The preferred transport."""

    name = "bot"

    def __init__(self, http_client: HttpClient, token: str, **kwargs):
        super().__init__(http_client, **kwargs)
        self.token = token

    def send(self, channel_id: str, payload: Mapping[str, Any]) -> str:
        """Post a message to a channel as the bot."""
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }
        return self._post(url, payload, headers)


class WebhookTransport(_RetryingTransport):
    """Posts through a per-channel webhook. Used when no bot token is set."""

    name = "webhook"

    def __init__(self, http_client: HttpClient, webhooks: Mapping[str, str], **kwargs):
        super().__init__(http_client, **kwargs)
        self.webhooks = dict(webhooks)

    def send(self, channel_id: str, payload: Mapping[str, Any]) -> str:
        """Post a message through the webhook configured for a channel."""
        url = self.webhooks.get(str(channel_id))
        if not url:
            raise DistributionError(f"no webhook configured for channel {channel_id}")
        separator = "&" if "?" in url else "?"
        return self._post(f"{url}{separator}wait=true", payload, {"Content-Type": "application/json"})


class DiscordDistributionService:
    """Formats an item and publishes it to the announcement channel."""

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        transport: DiscordTransport,
        channel_id: str,
        message_style: str = "embed",
        post_delay_seconds: float = 0.0,
        sleeper=time.sleep,
        clock=utcnow,
    ):
        self.transport = transport
        self.channel_id = str(channel_id)
        self.message_style = message_style
        self.post_delay_seconds = post_delay_seconds
        self.sleeper = sleeper
        self.clock = clock
        self._has_posted = False

    def distribute(self, item: ContentItem) -> DeliveryReceipt:
        """
        Announce one item in the configured channel.

        Raises:
            DistributionError: On a confirmed failure; the claim is released.
            AmbiguousDeliveryError: When the outcome is unknown; the claim is kept.
        """
        if self._has_posted and self.post_delay_seconds > 0:
            self.sleeper(self.post_delay_seconds)

        payload = build_message(item, self.message_style)
        message_id = self.transport.send(self.channel_id, payload)
        self._has_posted = True

        return DeliveryReceipt(
            channel_id=self.channel_id,
            message_id=message_id,
            posted_at=self.clock(),
        )
