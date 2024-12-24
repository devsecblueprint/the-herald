"""
Main handler.
"""

import os
import logging
import boto3

QUEUE_URL = os.environ["SQS_QUEUE_URL"]
QUEUE_GROUP = "newsletter"


def main(event, _):
    """
    This is the main entry point for the Lambda function.
    It takes in the event and context as arguments, and returns the response.
    """
    logging.info("Event: %s", event)
    response = send_message()
    return {
        "statusCode": 200,
        "body": response,
    }


def send_message():
    """
    Sends a message to the SQS queue.
    Returns a dictionary with the message and the message ID.
    """
    logging.info("Sending message to SQS queue")

    sqs = boto3.client("sqs")

    response_send = sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody="Hello from a FIFO queue!",
        MessageGroupId=QUEUE_GROUP,
    )

    return {"message": "Message has been sent!", "id": response_send["MessageId"]}
