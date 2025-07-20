"""
# scheduler.py --- IGNORE ---
This module defines the JobScheduler class, which is responsible for managing scheduled jobs in the trading bot application.
It initializes the job scheduler, adds jobs based on configuration, and provides methods to start and shutdown the scheduler.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from config.logger import LoggerConfig

from services.newsletter import NewsletterService


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
            minutes=1,  # Run every minute
            id="daily_newsletter_job",
            replace_existing=True,
        )
        self.logger.info("Adding job to publish latest articles every minute.")

        # Start the background scheduler
        self.bg_scheduler.start()
        self.logger.info("Job scheduler started and jobs added.")

    def shutdown(self):
        """
        Shut down the job scheduler and all running jobs.
        """
        self.bg_scheduler.shutdown()
