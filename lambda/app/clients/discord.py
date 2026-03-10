"""
Discord bot configuration module.
This module defines the DiscordConfig class, which is responsible for configuring and running a Discord bot.
It initializes the bot with the necessary token and guild ID, registers event handlers, and provides a method to run the bot.
"""

import os
import logging
from dotenv import load_dotenv
from discord.ext import commands
from discord import Intents, Object
from utils.secrets import VaultSecretsLoader

# Load environment variables from .env file
load_dotenv()


class DiscordClient:
    """
    Discord bot configuration class.
    This class initializes the Discord bot with the token and guild ID.
    It registers event handlers and provides a method to run the bot.
    """

    def __init__(self):
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

        self.guild = Object(id=int(os.getenv("DISCORD_GUILD_ID")))
        intents = Intents.default()  # Add this line
        self.bot = commands.Bot(command_prefix="/", intents=intents)
        self._register_events()

    def _register_events(self):
        """
        Register event handlers for the Discord bot.
        This method registers the on_ready event to log when the bot is ready and sync commands to the guild.
        """

        @self.bot.event
        async def on_ready():
            logging.info(f"Logged in as {self.bot.user.name} (ID: {self.bot.user.id})")
            try:
                synced = await self.bot.tree.sync(guild=self.guild)
                logging.info(f"Synced {len(synced)} command(s) to guild {self.guild}.")
            except Exception as e:
                logging.error(f"Failed to sync commands: {e}")

    def run(self):
        """
        Run the Discord bot.
        This method starts the bot using the token provided during initialization.
        """
        self.bot.run(self.token)
