"""
The Herald - Discord Bot FastAPI Application.

This module serves as the entry point for the containerized application.
It provides a health check endpoint, uses APScheduler to run periodic
tasks (newsletter publishing and event notifications), and maintains a
persistent Discord gateway connection so the bot appears online.
"""

import os
import asyncio
import logging
import threading
from contextlib import asynccontextmanager

import discord
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.clients.parameter_store import ParameterStoreClient
from app.clients.dynamodb import DynamoDBClient
from app.services.newsletter import NewsletterService
from app.services.discord import DiscordService
from app.services.youtube import YouTubeError, build_pipeline
from app.services.youtube.api import YouTubeTriggerController
from app.services.youtube.scheduler import register_youtube_job


# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------

def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure structured logging for container stdout."""
    logger = logging.getLogger()
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    if logger.handlers:
        for handler in logger.handlers:
            logger.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setLevel(level)
    formatter = logging.Formatter(
        '{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", '
        '"message": "%(message)s"}'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


logger = setup_logging(os.environ.get("LOG_LEVEL", "INFO"))


# ---------------------------------------------------------------------------
# Shared Clients (initialized once at startup)
# ---------------------------------------------------------------------------

parameter_store_client: ParameterStoreClient = None
dynamodb_client: DynamoDBClient = None

# Built at startup. Stays None if the YouTube feature is not configured,
# which leaves the rest of The Herald running normally.
youtube_controller: YouTubeTriggerController = None


def initialize_clients() -> tuple:
    """Initialize AWS clients for use across scheduled jobs."""
    global parameter_store_client, dynamodb_client

    if parameter_store_client is None:
        prefix = os.environ.get("PARAMETER_STORE_PREFIX", "/the-herald/prod/")
        logger.info(f"Initializing Parameter Store client with prefix: {prefix}")
        parameter_store_client = ParameterStoreClient(prefix=prefix)

    if dynamodb_client is None:
        table_name = os.environ.get("DYNAMODB_TABLE_NAME", "the-herald-reminders")
        logger.info(f"Initializing DynamoDB client for table: {table_name}")
        dynamodb_client = DynamoDBClient(table_name=table_name)

    return parameter_store_client, dynamodb_client


# ---------------------------------------------------------------------------
# Scheduled Job Functions
# ---------------------------------------------------------------------------

def run_newsletter_job():
    """Fetch RSS feeds and publish new articles to Discord channels."""
    logger.info("Running newsletter job...")
    try:
        newsletter_service = NewsletterService()
        newsletter_service.publish_latest_articles()
        logger.info("Newsletter job completed successfully")
    except Exception as e:
        logger.error(f"Newsletter job failed: {e}", exc_info=True)


def run_event_notification_job():
    """Check Discord scheduled events and send DM reminders."""
    logger.info("Running event notification job...")
    try:
        ps_client, db_client = initialize_clients()
        discord_service = DiscordService(
            parameter_store_client=ps_client, dynamodb_client=db_client
        )
        discord_service.list_scheduled_events_and_notify()
        logger.info("Event notification job completed successfully")
    except Exception as e:
        logger.error(f"Event notification job failed: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# APScheduler Setup
# ---------------------------------------------------------------------------

scheduler = BackgroundScheduler()


def configure_scheduler():
    """Add scheduled jobs to the APScheduler instance."""
    newsletter_interval = int(os.environ.get("NEWSLETTER_INTERVAL_MINUTES", "60"))
    event_notification_interval = int(
        os.environ.get("EVENT_NOTIFICATION_INTERVAL_MINUTES", "5")
    )

    scheduler.add_job(
        run_newsletter_job,
        trigger=IntervalTrigger(minutes=newsletter_interval),
        id="newsletter_job",
        replace_existing=True,
        name="Publish latest RSS articles to Discord",
    )

    scheduler.add_job(
        run_event_notification_job,
        trigger=IntervalTrigger(minutes=event_notification_interval),
        id="event_notification_job",
        replace_existing=True,
        name="Send Discord event reminders",
    )

    logger.info(
        f"Scheduler configured: newsletter every {newsletter_interval}m, "
        f"event notifications every {event_notification_interval}m"
    )


# ---------------------------------------------------------------------------
# YouTube Ingestion (partner uploads announced in #content-corner)
# ---------------------------------------------------------------------------

def configure_youtube():
    """
    Build the YouTube pipeline and register its poll, if it is configured.

    A missing or invalid YouTube configuration is logged and skipped rather
    than raised: it is an additive feature and must not stop The Herald
    from publishing newsletters or sending event reminders.
    """
    global youtube_controller

    ps_client, _ = initialize_clients()

    try:
        pipeline = build_pipeline(parameter_store_client=ps_client)
    except YouTubeError as e:
        logger.warning(f"YouTube ingestion is not configured, skipping it: {e}")
        return

    youtube_controller = YouTubeTriggerController(pipeline)

    if not pipeline.config.enabled:
        logger.info("YouTube ingestion is configured but disabled; no job registered")
        return

    register_youtube_job(scheduler, pipeline)
    logger.info(
        f"YouTube ingestion configured: {len(pipeline.config.sources)} source(s), "
        f"every {pipeline.config.poll_interval_minutes}m, "
        f"announcing in #{pipeline.config.discord_channel_name}"
    )


# ---------------------------------------------------------------------------
# Discord Gateway Presence (keeps bot "Online" in Discord)
# ---------------------------------------------------------------------------

discord_client: discord.Client = None
_discord_thread: threading.Thread = None


def start_discord_presence():
    """Start the Discord gateway connection in a background thread."""
    global discord_client, _discord_thread

    ps_client, _ = initialize_clients()

    try:
        token = ps_client.get_discord_token()
    except ValueError as e:
        logger.error(f"Cannot start Discord presence: {e}")
        return

    intents = discord.Intents.default()
    discord_client = discord.Client(intents=intents)

    @discord_client.event
    async def on_ready():
        logger.info(f"Discord presence connected as {discord_client.user} (ID: {discord_client.user.id})")
        await discord_client.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over the community",
            )
        )

    def _run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(discord_client.start(token))
        except Exception as e:
            logger.error(f"Discord presence connection lost: {e}")
        finally:
            loop.close()

    _discord_thread = threading.Thread(target=_run_bot, daemon=True, name="discord-presence")
    _discord_thread.start()
    logger.info("Discord presence thread started")


async def stop_discord_presence():
    """Gracefully close the Discord gateway connection."""
    global discord_client
    if discord_client and not discord_client.is_closed():
        await discord_client.close()
        logger.info("Discord presence connection closed")


# ---------------------------------------------------------------------------
# FastAPI Application with Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    # Startup
    logger.info("Starting The Herald...")
    initialize_clients()
    configure_scheduler()
    configure_youtube()
    scheduler.start()
    start_discord_presence()
    logger.info("Scheduler started. The Herald is running.")
    yield
    # Shutdown
    logger.info("Shutting down The Herald...")
    scheduler.shutdown(wait=False)
    await stop_discord_presence()
    logger.info("Scheduler stopped. Goodbye.")


app = FastAPI(
    title="The Herald",
    description="Discord bot for newsletter publishing and event notifications",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check endpoint for ECS container health monitoring."""
    jobs = scheduler.get_jobs()
    discord_connected = discord_client is not None and not discord_client.is_closed() and discord_client.is_ready()
    return {
        "status": "healthy",
        "service": "the-herald",
        "scheduler_running": scheduler.running,
        "active_jobs": len(jobs),
        "discord_connected": discord_connected,
        "youtube_configured": youtube_controller is not None,
    }


@app.post("/trigger/newsletter")
async def trigger_newsletter():
    """Manually trigger the newsletter job (useful for testing/ops)."""
    run_newsletter_job()
    return {"status": "ok", "message": "Newsletter job triggered"}


@app.post("/trigger/event-notifications")
async def trigger_event_notifications():
    """Manually trigger the event notification job (useful for testing/ops)."""
    run_event_notification_job()
    return {"status": "ok", "message": "Event notification job triggered"}


@app.post("/trigger/youtube")
async def trigger_youtube():
    """
    Run a YouTube poll now.

    200 ok | 207 completed_with_errors | 409 already_running.
    """
    if youtube_controller is None:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "message": "YouTube ingestion is not configured"},
        )
    status_code, body = youtube_controller.trigger()
    return JSONResponse(status_code=status_code, content=body)


@app.get("/health/youtube")
async def health_youtube():
    """Report YouTube ingestion configuration, sources and last run."""
    if youtube_controller is None:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "message": "YouTube ingestion is not configured"},
        )
    status_code, body = youtube_controller.health()
    return JSONResponse(status_code=status_code, content=body)
