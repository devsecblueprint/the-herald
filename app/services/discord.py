"""
DiscordService is responsible for interacting with the Discord API.
It provides methods to get channel IDs and check messages in a Discord channel.
"""

import os
import time
import json
import requests
from dotenv import load_dotenv
from config.logger import LoggerConfig
from utils.secrets import VaultSecretsLoader

# Load environment variables from .env file
load_dotenv()


class DiscordService:
    """
    DiscordService is responsible for interacting with the Discord API.
    It provides methods to get channel IDs and check messages in a Discord channel.
    Attributes:
        token (str): Discord bot token for authentication.
        guild_id (int): ID of the Discord guild (server) to interact with.
    """

    def __init__(self):
        self.logger = LoggerConfig(__name__).get_logger()
        self.token = VaultSecretsLoader().load_secret("discord-token") or os.getenv(
            "DISCORD_TOKEN"
        )

        if not self.token:
            raise ValueError(
                "Discord token not found. Set DISCORD_TOKEN environment variable or use Vault secrets."
            )

        if not os.getenv("DISCORD_GUILD_ID"):
            raise ValueError(
                "Discord guild ID not found. Set DISCORD_GUILD_ID environment variable."
            )

        self.guild_id = int(os.getenv("DISCORD_GUILD_ID"))

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

    def check_messages_in_discord(self, messages: list, channel_id: str):
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

    def send_message_to_channel(self, channel_id: str, message: str):
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
