"""
Main handler of the Discord Bot
"""

import os
import json
import requests
import boto3

TOKEN_PARAMETER = os.environ["DISCORD_TOKEN_PARAMETER"]
GUILD_ID = os.environ["DISCORD_GUILD_ID"]

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

    send_message_to_channel(CHANNEL_ID, "Hello from Lambda!")

    return {"statusCode": response.status_code, "body": response.text}


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


def get_discord_token():
    """
    Retrieves the Discord token from AWS Secrets Manager.
    """
    client = boto3.client("ssm")

    response = client.get_parameter(Name=TOKEN_PARAMETER, WithDecryption=True)

    return response["Parameter"]["Value"]
