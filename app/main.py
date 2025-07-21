"""
Main entry point for the FastAPI application that integrates with a Discord bot.
"""

import threading
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI
from clients.discord import DiscordClient
from utils.scheduler import JobScheduler


# Launch Discord bot in a background thread using lifespan event handler
@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    Lifespan event handler to start the Discord bot in a background thread.
    This allows the Discord bot to run concurrently with the FastAPI application.
    It initializes the Discord bot and starts it in a separate thread.
    Yields:
        None: This is a context manager, so it yields nothing.
    After yielding, the context manager will clean up the thread.
    Raises:
        ValueError: If the Discord token or guild ID is not found.
    """
    # Start scheduler
    JobScheduler().start()

    # Initialize Discord bot configuration
    discord_bot = DiscordClient()
    discord_thread = threading.Thread(target=discord_bot.run)
    discord_thread.start()
    yield


# Initialize services
app = FastAPI(lifespan=lifespan)

if __name__ == "__main__":
    # Run the FastAPI application with Uvicorn server
    # This will start the application on the specified host and port.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s [%(levelname)s] %(name)s.%(funcName)s:%(lineno)d - %(message)s",
                    "use_colors": True,
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["default"],
            },
            "loggers": {
                "uvicorn": {
                    "level": "INFO",
                    "handlers": ["default"],
                    "propagate": False,
                },
                "uvicorn.error": {
                    "level": "INFO",
                    "handlers": ["default"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": "INFO",
                    "handlers": ["default"],
                    "propagate": False,
                },
            },
        },
    )
