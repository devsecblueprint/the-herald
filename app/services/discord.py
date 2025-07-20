"""
DiscordService is responsible for interacting with the Discord API.
It provides methods to get channel IDs and check messages in a Discord channel.
"""

import os
from datetime import datetime, timedelta, timezone
import time
import json
import requests
from dotenv import load_dotenv
from config.logger import LoggerConfig
from utils.secrets import VaultSecretsLoader
from clients.redis import RedisClient

# Load environment variables from .env file
load_dotenv()


class DiscordService:
    """
    DiscordService is responsible for interacting with the Discord API.
    It provides methods to get channel IDs and check messages in a Discord channel.
    Attributes:
        token (str): Discord bot token for authentication.
        self.guild_id (int): ID of the Discord guild (server) to interact with.
    """

    def __init__(self):
        self.logger = LoggerConfig(__name__).get_logger()
        self.redis_client = RedisClient().client
        self.token = VaultSecretsLoader().load_secret("discord-token") or os.getenv(
            "DISCORD_TOKEN"
        )

        if not self.token:
            raise ValueError(
                "Discord token not found. Set DISCORD_TOKEN environment variable or use Vault secrets."
            )

        self.guild_id = int(os.getenv("DISCORD_GUILD_ID"))
        if not self.guild_id:
            raise ValueError(
                "Discord guild ID not found. Set DISCORD_GUILD_ID environment variable."
            )

    def get_channel_id(self, channel_name: str) -> int:
        """
        Get the ID of a Discord channel by its name.
        Args:
            channel_name (str): Name of the Discord channel.
        Returns:
            int: ID of the Discord channel.
        Raises:
            ValueError: If the channel with the given name is not found in the guild.
        """
        self.logger.info("Fetching channel ID for channel: %s", channel_name)
        if not channel_name:
            raise ValueError("Channel name cannot be empty.")

        url = f"https://discord.com/api/v10/guilds/{self.guild_id}/channels"
        headers = {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        channels = response.json()
        for channel in channels:
            if channel["name"] == channel_name:
                return channel["id"]

        raise ValueError(
            f"Channel '{channel_name}' not found in guild {self.guild_id}."
        )

    def check_messages_in_discord(self, messages: list, channel_id: str) -> list:
        """
        Check if the given messages exist in the specified Discord channel.
        Args:
            messages (list): List of messages to check.
            channel_id (str): ID of the Discord channel to check.
        Returns:
            list: List of messages that do not exist in the channel.
        """
        self.logger.info("Checking messages in Discord channel ID: %s", channel_id)
        if not messages:
            self.logger.warning("No messages provided to check.")
            return []

        url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=50"
        headers = {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }
        new_messages = []

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        channel_messages = response.json()
        message_contents = [
            channel_message["content"] for channel_message in channel_messages
        ]
        self.logger.info("Existing messages in channel: %s", message_contents)

        for message in messages:
            if message not in message_contents:
                self.logger.info("This message does not exist: %s", message)
                new_messages.append(message)

        return new_messages

    def send_message_to_channel(self, channel_id: str, message: str) -> None:
        """
        Send a message to a specified Discord channel.
        Args:
            channel_id (str): ID of the Discord channel to send the message to.
            message (str): The message content to send.
        Raises:
            ValueError: If the channel ID or message is empty.
        """
        self.logger.info("Sending message to channel ID: %s", channel_id)
        if not message:
            self.logger.warning("Message cannot be empty.")
            return

        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

        headers = {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }
        data = {"content": message}

        response = requests.post(
            url, headers=headers, data=json.dumps(data), timeout=10
        )

        if response.status_code == 204:
            self.logger.info("Message sent successfully to channel ID: %s", channel_id)
        else:
            self.logger.error(
                "Failed to send message to channel ID: %s. Status code: %d",
                channel_id,
                response.status_code,
            )
            response.raise_for_status()

        time.sleep(3)  # Small delay to prevent rate limiting

    def list_scheduled_events(self) -> list:
        """
        List scheduled events in the Discord guild.
        Returns:
            list: List of scheduled events in the guild.
        Raises:
            HTTPError: If the request to the Discord API fails.
        """
        url = f"https://discord.com/api/v10/guilds/{self.guild_id}/scheduled-events"
        headers = {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        events = response.json()
        self.logger.info("Scheduled events fetched successfully: %s", events)

        return events

    def list_scheduled_events_and_notify(
        self, time_delta: timedelta = timedelta(minutes=1)
    ) -> None:
        # pylint:disable=too-many-locals
        """
        List scheduled events in the Discord guild and send reminders to users.
        This method checks for events starting in about 1 hour and sends reminders to users.
        Args:
            time_delta (timedelta): Time delta to check for events. Defaults to 1 minute.
        Raises:
            HTTPError: If the request to the Discord API fails.
        """
        events = self.list_scheduled_events()

        now = datetime.now(timezone.utc)
        reminder_delta = timedelta(hours=1)

        self.logger.info("Checking scheduled events in guild ID: %s", self.guild_id)
        self.logger.info("Current time: %s", now.isoformat())
        self.logger.info("Time delta for reminders: %s", time_delta)

        for event in events:
            start_str = event[
                "scheduled_start_time"
            ]  # e.g., "2025-08-15T21:00:00+00:00"
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            time_to_event = start_dt - now

            # Only send reminders if event is about 1 hour away (within 1 minute)
            if abs(time_to_event - reminder_delta) < time_delta:
                self.logger.info("Processing event: %s", event["name"])
                self.logger.info(
                    "Scheduled start time: %s", event["scheduled_start_time"]
                )
                self.logger.info("Event ID: %s", event["id"])
                self.logger.info(
                    "Event link: https://discord.com/events/%s/%s",
                    self.guild_id,
                    event["id"],
                )

                event_id = event["id"]
                users_url = f"https://discord.com/api/v10/guilds/{self.guild_id}/scheduled-events/{event_id}/users?limit=100"
                users_resp = requests.get(users_url, headers=headers, timeout=10)
                users_resp.raise_for_status()
                users = users_resp.json()
                event_link = f"https://discord.com/events/{self.guild_id}/{event_id}"

                for user in users:
                    self.logger.info(
                        "Sending reminder for event %s to user %s", event_id, user_id
                    )
                    user_id = user["user"]["id"]
                    username = user["user"]["username"]
                    key = f"reminder:{event_id}:{user_id}:1h"

                    if self.redis_client.exists(key):
                        self.logger.info(
                            "Reminder already sent for event %s to user %s",
                            event_id,
                            user_id,
                        )
                        continue  # Already sent

                    reminder = (
                        f"🌟 Hey <@{user_id}>! Just a quick vibe check — **{event['name']}** is starting in "
                        f"an hour! You don't want to miss this! "
                        f"Grab your snacks, bring your energy, and click the link below to join: \n{event_link}"
                    )
                    try:
                        self._send_dm(user_id, reminder, headers)
                        self.redis_client.set(
                            key, datetime.now(timezone.utc).isoformat()
                        )
                        self.logger.info(
                            "Reminder set in Redis for event %s to user %s",
                            event_id,
                            user_id,
                        )
                    except Exception as e:
                        self.logger.error(f"Could not DM {username}: {e}")

    def _send_dm(self, user_id: str, message: str, headers: dict) -> None:
        """
        Send a direct message to a user in Discord.
        Args:
            user_id (str): ID of the user to send the message to.
            message (str): The message content to send.
            headers (dict): Headers for the HTTP request, including authorization.
        Raises:
            HTTPError: If the request to send the DM fails.
        """
        self.logger.info("Sending DM to user ID: %s", user_id)
        if not user_id or not message:
            self.logger.warning("User ID and message cannot be empty.")
            return
        if not user_id.isdigit():
            self.logger.error("Invalid user ID format: %s", user_id)
            raise ValueError("User ID must be a numeric string.")

        # Create DM channel
        self.logger.info("Creating DM channel for user ID: %s", user_id)

        dm_url = "https://discord.com/api/v10/users/@me/channels"
        dm_data = {"recipient_id": user_id}

        dm_resp = requests.post(dm_url, headers=headers, json=dm_data, timeout=10)
        dm_resp.raise_for_status()
        dm_channel = dm_resp.json()

        self.logger.info("DM channel created successfully for user ID: %s", user_id)

        channel_id = dm_channel["id"]
        msg_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        msg_data = {"content": message}

        msg_resp = requests.post(msg_url, headers=headers, json=msg_data, timeout=10)
        msg_resp.raise_for_status()
