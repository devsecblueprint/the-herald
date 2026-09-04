"""
Posting a prepared payload to Discord.

This module knows the Discord HTTP API and nothing about YouTube. The only
subtlety is failure classification: a confirmed rejection is safe to retry,
while an ambiguous delivery is not, because a missing audit record is
cheaper than announcing a partner's video twice.
"""

import time
from typing import Any, Dict, Mapping, Protocol

from app.clients.http import HttpClient, HttpError, HttpResponseIncomplete
from app.errors import AmbiguousDeliveryError, DistributionError
from app.utils.text import truncate

DISCORD_API_BASE = "https://discord.com/api/v10"


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

    def _post(
        self, url: str, payload: Mapping[str, Any], headers: Dict[str, str]
    ) -> str:
        """POST with rate-limit and 5xx retries, then interpret the response."""
        last_error = "no attempt was made"

        for attempt in range(self.max_retries):
            try:
                response = self.http.post(url, json=dict(payload), headers=headers)
            except HttpResponseIncomplete as exc:
                # The request was on the wire. The message may be live.
                raise AmbiguousDeliveryError(
                    f"delivery outcome unknown: {exc}"
                ) from exc
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

        raise DistributionError(
            f"Discord post failed after {self.max_retries} attempts: {last_error}"
        )

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
        return self._post(
            f"{url}{separator}wait=true", payload, {"Content-Type": "application/json"}
        )
