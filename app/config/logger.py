"""
logger.py --- IGNORE ---
This module defines the LoggerConfig class, which is responsible for configuring and providing a logger instance.
It initializes the logger with a specified class name and provides a method to retrieve the logger instance.
"""

import logging


class LoggerConfig:
    """
    LoggerConfig is responsible for configuring and providing a logger instance.
    It initializes the logger with a specified class name and provides a method to retrieve the logger instance.
    """

    def __init__(self, class_name: str):
        self.logger = logging.getLogger(class_name)

    def get_logger(self):
        """
        Get the logger instance.
        Returns:
            logging.Logger: The logger instance configured for the specified class.
        """
        return self.logger
