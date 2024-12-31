"""
Main handler of the Discord Bot
"""

import os
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

            send_message_to_channel(
                "content-corner-test",
                f"New Video Detected! Here is the link: {payload['videoUrl']}",
            )

            return {
                "statusCode": 200,
                "headers": {"Content-Type": "text/plain"},
                "body": "Video message has been published or posted.",
            }
    if event.get("source") == "aws.events":
        sqs_message = process_messages()

        return {
            "statusCode": 200,
            "body": f"Processing has been completed: {sqs_message}",
        }

    return {
        "statusCode": 200,
        "body": "Hello World",
    }


def send_message_to_channel(channel_name, message):
    """
    Sends a message to a specific Discord channel.
    """
    token = get_discord_token()
    channel_id = get_channel_id(channel_name)

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
