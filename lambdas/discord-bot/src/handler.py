"""
Main handler of the Discord Bot
"""

import os
import time
import json
import logging
from xml.parsers.expat import ExpatError

import requests
import boto3
import xmltodict

TOKEN_PARAMETER = os.environ["DISCORD_TOKEN_PARAMETER"]
GUILD_ID = os.environ["DISCORD_GUILD_ID"]
QUEUE_URL = os.environ["SQS_QUEUE_URL"]

# Logging Configuration
logging.getLogger().setLevel(logging.INFO)


def main(event, _):
    """
    This is the main entry point for the Lambda function.
    It takes in the event and context as arguments, and returns the response.
    """
    logging.info("Event: %s", event)

    if event.get("queryStringParameters"):
        hub_challenge = event["queryStringParameters"].get("hub.challenge")
        logging.info("Verifying subscription to PubSubHubbub: %s", hub_challenge)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": hub_challenge,  # Respond with the exact hub.challenge value
        }

    # Assume it is a YouTube video
    if event.get("body"):
        if "xml" in event.get("body"):
            logging.info("New YouTube video detected! Parsing XML body")
            payload = parse_youtube_xml(event.get("body"))
            logging.info("Payload: %s", payload)

            channel_id = get_channel_id("📹-content-corner")
            send_message_to_channel(
                channel_id,
                f"@everyone - Check out my latest video: \
                    {payload['videoName']} {payload['videoUrl']}",
            )

            return {
                "statusCode": 200,
                "headers": {"Content-Type": "text/plain"},
                "body": "Video message has been published or posted.",
            }

    # Process newsletters in the Queue
    if event.get("source") == "aws.events":
        channel_id = get_channel_id("📰-security-news")
        processed_messages = process_all_queue_messages(channel_id)

        logging.info("Total # of Processed Messages: %s", processed_messages)

        return {
            "statusCode": 200,
            "body": "Processing has been completed!",
        }

    return {
        "statusCode": 200,
        "body": "Hello World",
    }


def send_message_to_channel(channel_id, message):
    """
    Sends a message to a specific Discord channel.
    """
    token = get_discord_token()

    logging.info("Channel ID: %s", channel_id)
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    data = {"content": message}

    response = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
    response.raise_for_status()


def get_channel_id(channel_name):
    """
    Retrieves the channel ID for a given channel name.
    """
    token = get_discord_token()
    url = f"https://discord.com/api/v10/guilds/{GUILD_ID}/channels"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    channels = response.json()
    for channel in channels:
        if channel["name"] == channel_name:
            return channel["id"]

    raise ValueError(f"Channel '{channel_name}' not found.")


def parse_youtube_xml(xml_body: str):
    """
    Parses the YouTube XML body
    """
    logging.info("Parsing YouTube XML body")
    logging.info("XML Body: %s", xml_body)

    try:
        # Parse the XML from the POST request into a dict.
        xml_dict = xmltodict.parse(xml_body)

        # Parse out the video URL & the title
        video_url = xml_dict["feed"]["entry"]["link"]["@href"]
        video_title = xml_dict["feed"]["entry"]["title"]

        # Trigger Step Function by passing in the video title and URL
        payload = {
            "videoName": video_title,
            "videoUrl": video_url,
            "contentType": "video",
        }

        return payload

    except (ExpatError, LookupError):
        # request.data contains malformed XML or no XML at all, return FORBIDDEN.
        return "XML data cannot be processed.", 500


def process_all_queue_messages(channel_id: str):
    """
    Processes all messages in the SQS queue.
    """
    messages_processed = 0
    sqs_client = boto3.client("sqs")

    while True:
        try:
            # Receive messages from the queue
            response = sqs_client.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=10,  # Maximum allowed by SQS
                WaitTimeSeconds=5,
            )

            # Check if we got any messages
            messages = response.get("Messages", [])
            if not messages:
                logging.info(
                    "Queue is empty. Total messages processed: %s", messages_processed
                )
                break

            # Delete messages before processing
            for message in messages:
                sqs_client.delete_message(
                    QueueUrl=QUEUE_URL, ReceiptHandle=message["ReceiptHandle"]
                )
                messages_processed += 1
                logging.info(
                    "Message processed and deleted. Receipt Handle: %s",
                    message["ReceiptHandle"],
                )

            new_messages = check_messages_in_discord(
                [message["Body"] for message in messages], channel_id
            )

            if new_messages:
                logging.info("New messages found: %s", new_messages)
            else:
                logging.info("No new messages found.")
                break

            for message in new_messages:
                try:
                    # Process the message
                    send_message_to_channel(
                        channel_id,
                        message,
                    )
                    time.sleep(3)  # Small delay to prevent rate limiting

                except Exception as e:
                    logging.error("Error processing message: %s", str(e))
                    continue

        except Exception as e:
            logging.error("Error receiving messages from queue: %s", str(e))
            break

    return messages_processed


def check_messages_in_discord(messages: list, channel_id: str):
    """
    Checks if a message exists in the discord channel and returns messages that
    are not in the discord channel.
    """
    token = get_discord_token()
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=50"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    new_messages = []

    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    channel_messages = response.json()
    message_contents = [
        channel_message["content"] for channel_message in channel_messages
    ]
    logging.info("Message contents: %s", message_contents)

    for message in messages:
        if message not in message_contents:
            logging.info("This message does not exist: %s", message)
            new_messages.append(message)

    return new_messages


def get_discord_token():
    """
    Retrieves the Discord token from AWS Secrets Manager.
    """
    client = boto3.client("ssm")
    response = client.get_parameter(Name=TOKEN_PARAMETER, WithDecryption=True)
    return response["Parameter"]["Value"]


if __name__ == "__main__":
    check_messages_in_discord(
        [
            "https://thehackernews.com/2025/01/new-doubleclickjacking-exploit-bypasses.html"
        ],
        "1320604883484672102",
    )
