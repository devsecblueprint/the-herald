"""
AWS Lambda handler for Discord bot operations.

This module serves as the entry point for the Lambda function, routing requests
to appropriate handlers based on EventBridge event payloads. It initializes
shared resources (Parameter Store, DynamoDB) and implements structured logging
to CloudWatch.
"""

import os
import sys
import logging
import json
import traceback
from typing import Dict, Any

# Add app directory to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))


# Import AWS clients
from clients.parameter_store import ParameterStoreClient
from clients.dynamodb import DynamoDBClient

# Import service handlers
from services.newsletter import NewsletterService
from services.discord import DiscordService


# Configure structured logging for CloudWatch
def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """
    Configure structured logging for CloudWatch Logs.

    Args:
        log_level: Logging level (INFO, DEBUG, ERROR)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger()

    # Set log level from environment variable or default to INFO
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    # Remove existing handlers to avoid duplicate logs
    if logger.handlers:
        for handler in logger.handlers:
            logger.removeHandler(handler)

    # Create console handler with structured format
    handler = logging.StreamHandler()
    handler.setLevel(level)

    # Use JSON-like format for structured logging
    formatter = logging.Formatter(
        '{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", '
        '"message": "%(message)s"}'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


# Initialize logger
logger = setup_logging(os.environ.get("LOG_LEVEL", "INFO"))

# Global clients (cached across Lambda invocations in the same execution context)
parameter_store_client = None
dynamodb_client = None


def initialize_clients() -> tuple:
    """
    Initialize AWS clients with caching for Lambda execution context.

    Returns:
        Tuple of (ParameterStoreClient, DynamoDBClient)

    Raises:
        ValueError: If client initialization fails
    """
    global parameter_store_client, dynamodb_client

    # Initialize Parameter Store client if not already cached
    if parameter_store_client is None:
        prefix = os.environ.get("PARAMETER_STORE_PREFIX", "/the-herald/prod/")

        logger.info(f"Initializing Parameter Store client with prefix: {prefix}")
        parameter_store_client = ParameterStoreClient(prefix=prefix)
        logger.info("Parameter Store client initialized successfully")

    # Initialize DynamoDB client if not already cached
    if dynamodb_client is None:
        table_name = os.environ.get("DYNAMODB_TABLE_NAME", "the-herald-reminders")

        logger.info(f"Initializing DynamoDB client for table: {table_name}")
        dynamodb_client = DynamoDBClient(table_name=table_name)
        logger.info("DynamoDB client initialized successfully")

    return parameter_store_client, dynamodb_client


def handle_newsletter(ps_client: ParameterStoreClient) -> Dict[str, Any]:
    """
    Handle newsletter publishing operations.

    Args:
        ps_client: Parameter Store client for retrieving secrets

    Returns:
        Response dictionary with status and message
    """
    logger.info("Starting newsletter handler")

    try:
        # Initialize newsletter service (which internally uses DiscordService)
        newsletter_service = NewsletterService()

        # Publish latest articles
        newsletter_service.publish_latest_articles()

        logger.info("Newsletter handler completed successfully")
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Newsletter published successfully",
                    "handler": "newsletter",
                }
            ),
        }

    except Exception as e:
        logger.error(f"Newsletter handler failed: {str(e)}")
        logger.error(traceback.format_exc())
        raise


def handle_event_notification(
    ps_client: ParameterStoreClient, db_client: DynamoDBClient
) -> Dict[str, Any]:
    """
    Handle Discord event notification operations.

    Args:
        ps_client: Parameter Store client for retrieving secrets
        db_client: DynamoDB client for reminder tracking

    Returns:
        Response dictionary with status and message
    """
    logger.info("Starting event notification handler")

    try:
        # Initialize Discord service with clients
        discord_service = DiscordService(
            parameter_store_client=ps_client, dynamodb_client=db_client
        )

        # List scheduled events and send notifications
        discord_service.list_scheduled_events_and_notify()

        logger.info("Event notification handler completed successfully")
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Event notifications processed successfully",
                    "handler": "event_notification",
                }
            ),
        }

    except Exception as e:
        logger.error(f"Event notification handler failed: {str(e)}")
        logger.error(traceback.format_exc())
        raise


def main(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler function - entry point for all invocations.

    Routes requests to appropriate handlers based on event['handler_type'].
    Initializes shared resources and implements structured logging.

    Args:
        event: Lambda event payload from EventBridge
        context: Lambda context object

    Returns:
        Response dictionary with statusCode and body

    Expected event format:
        {
            "handler_type": "newsletter" | "event_notification",
            "source": "eventbridge.schedule"
        }
    """
    # Log invocation details
    logger.info(f"Lambda function invoked with request ID: {context.aws_request_id}")
    logger.info(f"Event payload: {json.dumps(event)}")
    logger.info(f"Memory limit: {context.memory_limit_in_mb} MB")

    start_time = context.get_remaining_time_in_millis()

    try:
        # Initialize AWS clients (cached across invocations)
        ps_client, db_client = initialize_clients()

        # Extract handler type from event
        handler_type = event.get("handler_type")

        if not handler_type:
            error_msg = "Missing 'handler_type' in event payload"
            logger.error(error_msg)
            return {
                "statusCode": 400,
                "body": json.dumps({"error": error_msg, "event": event}),
            }

        logger.info(f"Routing to handler: {handler_type}")

        # Route to appropriate handler
        if handler_type == "newsletter":
            response = handle_newsletter(ps_client)

        elif handler_type == "event_notification":
            response = handle_event_notification(ps_client, db_client)

        else:
            error_msg = f"Unknown handler_type: {handler_type}"
            logger.error(error_msg)
            return {
                "statusCode": 400,
                "body": json.dumps({"error": error_msg, "handler_type": handler_type}),
            }

        # Log execution metrics
        end_time = context.get_remaining_time_in_millis()
        execution_time = start_time - end_time
        logger.info(f"Execution completed in {execution_time} ms")
        logger.info(f"Response: {json.dumps(response)}")

        return response

    except Exception as e:
        # Log critical error with full context
        logger.error(f"Lambda execution failed: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Traceback: {traceback.format_exc()}")

        # Return error response to trigger CloudWatch alarms
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "error": "Internal server error",
                    "message": str(e),
                    "type": type(e).__name__,
                    "request_id": context.aws_request_id,
                }
            ),
        }
