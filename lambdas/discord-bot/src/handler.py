"""
Main handler of the Discord Bot
"""

import os
import json
import logging
import requests
import boto3

TOKEN_PARAMETER = os.environ["DISCORD_TOKEN_PARAMETER"]
GUILD_ID = os.environ["DISCORD_GUILD_ID"]
QUEUE_URL = os.environ["SQS_QUEUE_URL"]

CHANNEL_ID = "1320604883484672102"  # security-news-test


def main(event, _):
    """
    This is the main entry point for the Lambda function.
    It takes in the event and context as arguments, and returns the response.
    """
    print("Event: %s", event)
    token = get_discord_token()

    url = f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    response = requests.get(url, headers=headers, timeout=10)
    logging.info("Response: %s", response.text)

    sqs_message = process_messages()

    return {"statusCode": 200, "body": f"Processing has been completed: {sqs_message}"}


def send_message_to_channel(channel_id, message):
    """
    Sends a message to a specific Discord channel.
    """
    token = get_discord_token()
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    data = {"content": message}

    response = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
    response.raise_for_status()


def process_messages():
    """
    Process messages for the SQS Queue
    in a systematic way.
    """
    sqs_client = boto3.client("sqs")
    response_receive = sqs_client.receive_message(
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=1,  # how many messages to pull at once
        WaitTimeSeconds=5,  # enable short polling (up to 20 is possible for long polling)
    )

    messages = response_receive.get("Messages", [])
    if not messages:
        print("No messages found in the queue.")
        return None

    # We have at least one message, process the first
    message = messages[0]
    receipt_handle = message["ReceiptHandle"]
    print(f"Received message: {message['Body']}")
    print(f"ReceiptHandle: {receipt_handle}")

    # 3. Delete the message from the queue
    sqs_client.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=receipt_handle)
    print("Message deleted from the queue.")

    return message["Body"]


def get_discord_token():
    """
    Retrieves the Discord token from AWS Secrets Manager.
    """
    client = boto3.client("ssm")

    response = client.get_parameter(Name=TOKEN_PARAMETER, WithDecryption=True)

    return response["Parameter"]["Value"]
