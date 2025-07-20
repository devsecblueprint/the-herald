import requests

from config.logger import LoggerConfig

class DiscordService:

    def __init__(self, token: str, guild_id: int):
        self.logger = LoggerConfig(__name__).get_logger()
        self.token = token
        self.guild_id = guild_id

    def get_channel_id(self, channel_name: str) -> int:
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

    def check_messages_in_discord(self,messages: list, channel_id: str):
        """
        Checks if a message exists in the discord channel and returns messages that
        are not in the discord channel.
        """
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
        self.logger.info("Message contents: %s", message_contents)

        for message in messages:
            if message not in message_contents:
                self.logger.info("This message does not exist: %s", message)
                new_messages.append(message)

        return new_messages
