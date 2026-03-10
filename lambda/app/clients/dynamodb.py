"""
DynamoDB client for reminder tracking.

This module provides a client for tracking sent Discord event reminders using DynamoDB.
It prevents duplicate notifications by storing reminder records with a 2-hour TTL.
"""

import logging
import time
from typing import Optional
import boto3
from botocore.exceptions import ClientError, BotoCoreError


logger = logging.getLogger(__name__)


class DynamoDBClient:
    """
    DynamoDB client for tracking sent reminders.

    This client manages reminder state to prevent duplicate notifications.
    Records are automatically expired after 2 hours using DynamoDB TTL.
    """

    def __init__(self, table_name: str, region_name: str = None):
        """
        Initialize the DynamoDB client.

        Args:
            table_name: Name of the DynamoDB table for reminder tracking
            region_name: AWS region name (default: None, uses AWS_REGION env var or boto3 default)
        """
        self.table_name = table_name
        self.dynamodb = boto3.resource("dynamodb", region_name=region_name)
        self.table = self.dynamodb.Table(table_name)
        logger.info(f"Initialized DynamoDB client for table: {table_name}")

    @staticmethod
    def generate_reminder_key(event_id: str, user_id: str, reminder_type: str) -> str:
        """
        Generate a composite key for reminder tracking.

        Args:
            event_id: Discord event ID
            user_id: Discord user ID
            reminder_type: Type of reminder (e.g., "1h" for 1-hour reminder)

        Returns:
            Composite key in format: {event_id}:{user_id}:{reminder_type}
        """
        return f"{event_id}:{user_id}:{reminder_type}"

    def check_reminder_sent(
        self, event_id: str, user_id: str, reminder_type: str
    ) -> bool:
        """
        Check if a reminder has already been sent.

        Args:
            event_id: Discord event ID
            user_id: Discord user ID
            reminder_type: Type of reminder (e.g., "1h")

        Returns:
            True if reminder was already sent, False otherwise
        """
        reminder_key = self.generate_reminder_key(event_id, user_id, reminder_type)

        try:
            response = self.table.get_item(Key={"reminder_key": reminder_key})

            if "Item" in response:
                # Check if the item has expired (TTL might not have cleaned it up yet)
                ttl = response["Item"].get("ttl")
                current_time = int(time.time())

                if ttl and ttl > current_time:
                    logger.debug(
                        f"Reminder already sent: {reminder_key} (expires at {ttl})"
                    )
                    return True
                else:
                    logger.debug(
                        f"Reminder record expired: {reminder_key} (TTL: {ttl})"
                    )
                    return False

            logger.debug(f"No reminder record found: {reminder_key}")
            return False

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            logger.error(
                f"DynamoDB ClientError checking reminder {reminder_key}: "
                f"{error_code} - {e}",
                exc_info=True,
            )
            # On error, assume reminder was not sent to avoid blocking notifications
            return False

        except BotoCoreError as e:
            logger.error(
                f"BotoCoreError checking reminder {reminder_key}: {e}", exc_info=True
            )
            return False

        except Exception as e:
            logger.error(
                f"Unexpected error checking reminder {reminder_key}: {e}", exc_info=True
            )
            return False

    def record_reminder_sent(
        self, event_id: str, user_id: str, reminder_type: str
    ) -> bool:
        """
        Record that a reminder has been sent.

        The record will automatically expire after 2 hours via DynamoDB TTL.

        Args:
            event_id: Discord event ID
            user_id: Discord user ID
            reminder_type: Type of reminder (e.g., "1h")

        Returns:
            True if record was successfully created, False otherwise
        """
        reminder_key = self.generate_reminder_key(event_id, user_id, reminder_type)
        current_time = int(time.time())
        ttl = current_time + 7200  # 2 hours = 7200 seconds

        try:
            self.table.put_item(
                Item={
                    "reminder_key": reminder_key,
                    "timestamp": current_time,
                    "ttl": ttl,
                }
            )
            logger.info(f"Recorded reminder: {reminder_key} (expires at {ttl})")
            return True

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            logger.error(
                f"DynamoDB ClientError recording reminder {reminder_key}: "
                f"{error_code} - {e}",
                exc_info=True,
            )
            return False

        except BotoCoreError as e:
            logger.error(
                f"BotoCoreError recording reminder {reminder_key}: {e}", exc_info=True
            )
            return False

        except Exception as e:
            logger.error(
                f"Unexpected error recording reminder {reminder_key}: {e}",
                exc_info=True,
            )
            return False

    def delete_reminder_record(
        self, event_id: str, user_id: str, reminder_type: str
    ) -> bool:
        """
        Delete a reminder record (optional utility method).

        Args:
            event_id: Discord event ID
            user_id: Discord user ID
            reminder_type: Type of reminder (e.g., "1h")

        Returns:
            True if record was successfully deleted, False otherwise
        """
        reminder_key = self.generate_reminder_key(event_id, user_id, reminder_type)

        try:
            self.table.delete_item(Key={"reminder_key": reminder_key})
            logger.info(f"Deleted reminder record: {reminder_key}")
            return True

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            logger.error(
                f"DynamoDB ClientError deleting reminder {reminder_key}: "
                f"{error_code} - {e}",
                exc_info=True,
            )
            return False

        except BotoCoreError as e:
            logger.error(
                f"BotoCoreError deleting reminder {reminder_key}: {e}", exc_info=True
            )
            return False

        except Exception as e:
            logger.error(
                f"Unexpected error deleting reminder {reminder_key}: {e}", exc_info=True
            )
            return False
