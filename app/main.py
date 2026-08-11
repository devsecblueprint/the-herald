"""
The Herald - Discord Bot FastAPI Application.

This module serves as the entry point for the containerized application.
It provides a health check endpoint and uses APScheduler to run periodic
tasks (newsletter publishing and event notifications).
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.clients.parameter_store import ParameterStoreClient
from app.clients.dynamodb import DynamoDBClient
from app.services.newsletter import NewsletterService
from app.services.discord import DiscordService


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
# FastAPI Application with Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    # Startup
    logger.info("Starting The Herald...")
    initialize_clients()
    configure_scheduler()
    scheduler.start()
    logger.info("Scheduler started. The Herald is running.")
    yield
    # Shutdown
    logger.info("Shutting down The Herald...")
    scheduler.shutdown(wait=False)
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
    return {
        "status": "healthy",
        "service": "the-herald",
        "scheduler_running": scheduler.running,
        "active_jobs": len(jobs),
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
