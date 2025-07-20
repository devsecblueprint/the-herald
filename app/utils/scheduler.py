"""
# scheduler.py --- IGNORE ---
This module defines the JobScheduler class, which is responsible for managing scheduled jobs in the trading bot application.
It initializes the job scheduler, adds jobs based on configuration, and provides methods to start and shutdown the scheduler.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from config.logger import LoggerConfig


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

    def shutdown(self):
        """
        Shut down the job scheduler and all running jobs.
        """
        self.bg_scheduler.shutdown()
