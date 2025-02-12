"""
Main handler of the Discord Bot
"""

import os
import time
import json
import logging

from datetime import datetime, timedelta
from xml.parsers.expat import ExpatError

import requests
import boto3
import xmltodict

# Environment Variables
TOKEN_PARAMETER = os.environ["DISCORD_TOKEN_PARAMETER"]
GUILD_ID = os.environ["DISCORD_GUILD_ID"]
TABLE_ARN = os.environ["DYNAMODB_TABLE_ARN"]
CONTENT_CORNER_CHANNEL_NAME = os.environ["CONTENT_CORNER_CHANNEL_NAME"]
JOB_BOARD_CHANNEL_NAME = os.environ["JOB_BOARD_CHANNEL_NAME"]
ACTIVELY_HIRING_ROLE_ID = os.environ["ACTIVELY_HIRING_ROLE_ID"]
NOTIFY_ROLE_ID = os.environ["NOTIFY_ROLE_ID"]

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

            channel_id = get_channel_id(CONTENT_CORNER_CHANNEL_NAME)
            processed_messages = process_video(event.get("body"), channel_id)

            return {
                "statusCode": 200,
                "headers": {"Content-Type": "text/plain"},
                "body": "Video message has been published or posted!",
            }

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": "Video information already exists in the channel.",
        }

    # Process newsletters in the Queue
    if event.get("source") == "aws.events":
        processed_messages = process_all_newsletters(channel_id)
        logging.info("Total # of Processed Newsletter Messages: %s", processed_messages)

        channel_id = get_channel_id(JOB_BOARD_CHANNEL_NAME)
        processed_messages = process_all_jobs(channel_id)
        logging.info("Total # of Processed Job Messages: %s", processed_messages)

        return {
            "statusCode": 200,
            "body": "Processing has been completed!",
        }

    return {
        "statusCode": 200,
        "body": "Hello World",
    }


def process_all_jobs(channel_id: str):
    # pylint: disable=line-too-long
    """
    Processes and sends all jobs to the proper
    Discord channel.
    """
    dynamodb_client = boto3.client("dynamodb")
    response = dynamodb_client.scan(
        TableName=TABLE_ARN,
        FilterExpression="#type = :job_type",
        ExpressionAttributeNames={
            "#type": "type"  # 'type' is a reserved word in DynamoDB
        },
        ExpressionAttributeValues={":job_type": {"S": "job"}},
    )
    items = response.get("Items")

    for item in items:
        link = item["link"]["S"]
        job_title = item["title"]["S"]
        company_name = item["companyName"]["S"]

        message = f"<@&{ACTIVELY_HIRING_ROLE_ID}>- check this new job information below:\n\nTitle: {job_title}\nCompany Name: {company_name}\nLink: {link}"
        try:
            if check_messages_in_discord([message], channel_id):
                send_message_to_channel(channel_id, message)
        except Exception as e:
            logging.error("Error processing job: %s", e)
            continue

    # Delete record from DynamoDB
    for item in items:
        dynamodb_client.delete_item(
            TableName=TABLE_ARN,
            Key={"type": {"S": "job"}, "link": {"S": item["link"]["S"]}},
        )

    return len(items)


def process_video(body: str, channel_id: str):
    # pylint: disable=line-too-long
    """
    If the video does not exist in the DynamoDB table, it will send a message.
    """
    dynamodb_client = boto3.client("dynamodb")
    payload = parse_youtube_xml(body)

    if payload is None:
        return "There was an issue processing the XML.", 500

    message = f"Hey <@&{NOTIFY_ROLE_ID}> - Check out Damien's latest video - {payload['videoName']} {payload['videoUrl']}"
    response = dynamodb_client.get_item(
        TableName=TABLE_ARN,
        Key={
            "type": {"S": "video"},
            "link": {"S": payload["videoUrl"]},
        },
    )

    if "Item" in response:
        logging.info("Video already exists in DynamoDB: %s", message)
        return "Video already exists in DynamoDB."

    response = dynamodb_client.put_item(
        TableName=TABLE_ARN,
        Item={
            "type": {"S": "video"},
            "link": {"S": payload["videoUrl"]},
            "videoName": {"S": payload["videoName"]},
            "expirationDate": {
                "S": str(int((datetime.now() + timedelta(days=1)).timestamp()))
            },
        },
    )
    logging.info("Video does not exist in DynamoDB: %s", response)

    if check_messages_in_discord([message], channel_id):
        send_message_to_channel(channel_id, message)
        return "Video has been sent"

    logging.info("Message already exist in the Discord channel.")
    return "Message already exist in the Discord channel."


def process_all_newsletters():
    """
    Processes all newsletters in the DynamoDB table,
    and clears them all out once it's done.
    """
    dynamodb_client = boto3.client("dynamodb")

    response = dynamodb_client.scan(
        TableName=TABLE_ARN,
        FilterExpression="#type = :newsletter_type",
        ExpressionAttributeNames={
            "#type": "type"  # 'type' is a reserved word in DynamoDB
        },
        ExpressionAttributeValues={":newsletter_type": {"S": "newsletter"}},
    )
    items = response.get("Items")

    for item in items:
        link = item["link"]["S"]
        channel_name = item["channelName"]["S"]
        logging.info("Link: %s", link)
        try:
            channel_id = get_channel_id(channel_name)
            if check_messages_in_discord([link], channel_id):
                send_message_to_channel(
                    channel_id,
                    link,
                )

            time.sleep(3)  # Small delay to prevent rate limiting
        except Exception as e:
            logging.error("Error processing message: %s", str(e))
            continue

    # Delete record from DynamoDB
    for item in items:
        dynamodb_client.delete_item(
            TableName=TABLE_ARN,
            Key={"type": {"S": "newsletter"}, "link": {"S": item["link"]["S"]}},
        )

    return len(items)


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
    logging.info("Response: %s", response.text)

    response.raise_for_status()

    time.sleep(3)  # Small delay to prevent rate limiting


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
        logging.error("XML data cannot be processed.")
        return None


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
    Retrieves the Discord token from AWS SSM.
    """
    client = boto3.client("ssm")
    response = client.get_parameter(Name=TOKEN_PARAMETER, WithDecryption=True)
    return response["Parameter"]["Value"]
