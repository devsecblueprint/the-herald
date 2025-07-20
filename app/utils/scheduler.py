"""
# scheduler.py --- IGNORE ---
This module defines the JobScheduler class, which is responsible for managing scheduled jobs in the trading bot application.
It initializes the job scheduler, adds jobs based on configuration, and provides methods to start and shutdown the scheduler.
"""

from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from services.discord import DiscordService
from config.logger import LoggerConfig

from services.newsletter import NewsletterService
from services.calendar import GoogleCalendarService


class JobScheduler:
    """
    JobScheduler is responsible for managing scheduled jobs in the trading bot application.
    It initializes the job scheduler, adds jobs based on configuration, and provides methods to start and shutdown the scheduler.

    Attributes:
        bg_scheduler (BackgroundScheduler): Background scheduler instance to manage jobs.
        logger (LoggerConfig): Logger instance for logging events and errors.
    """

    def __init__(self):
        self.bg_scheduler = BackgroundScheduler()
        self.logger = LoggerConfig(__name__).get_logger()

    def start(self):
        """
        Start the job scheduler and add jobs to it.
        This method initializes the scheduler and adds jobs based on the configuration.
        """
        # Add NewsletterService job to the scheduler
        self.bg_scheduler.add_job(
            NewsletterService().publish_latest_articles,
            trigger="interval",
            minutes=60,  # Run every hour
            id="hourly_newsletter_job",
            replace_existing=True,
            next_run_time=datetime.now(),
        )
        self.logger.info("Adding job to publish latest articles every hour.")

        # Check events with Discord
        self.bg_scheduler.add_job(
            DiscordService().list_scheduled_events_and_notify,
            trigger="interval",
            minutes=1,  # Run every minute
            id="minute_discord_events_job",
            replace_existing=True,
            next_run_time=datetime.now(),
        )
        self.logger.info("Adding job to check Discord events every minute.")

        # Sync Discord events with Google Calendar
        self.bg_scheduler.add_job(
            GoogleCalendarService().sync_discord_events,
            trigger="interval",
            minutes=15,  # Run every 15 minutes
            id="fifteen_minute_sync_discord_events_job",
            replace_existing=True,
            next_run_time=datetime.now(),
        )
        self.logger.info(
            "Adding job to sync Discord events with Google Calendar every 15 minutes."
        )

        # Start the background scheduler
        self.bg_scheduler.start()
        self.logger.info("Job scheduler started and jobs added.")

    def shutdown(self):
        """
        Shut down the job scheduler and all running jobs.
        """
        self.bg_scheduler.shutdown()
