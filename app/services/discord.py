# services/discord_bot.py

import os
import logging
import time
from io import BytesIO
from dotenv import load_dotenv
from discord.ext import commands
from discord import Intents, Interaction, Object, Attachment, File, User

from PyPDF2 import PdfReader
from src.utils import Utils
from src.services.prompt import PromptBuilder
from src.services.resume import ResumeReviewService
from utils import VaultSecretsLoader

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class DiscordBotClient:
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
        @self.bot.event
        async def on_ready():
            logging.info(f"Logged in as {self.bot.user.name} (ID: {self.bot.user.id})")
            try:
                synced = await self.bot.tree.sync(guild=self.guild)
                logging.info(f"Synced {len(synced)} command(s) to guild {self.guild}.")
            except Exception as e:
                logging.error(f"Failed to sync commands: {e}")

    def run(self):
        self.bot.run(self.token)
