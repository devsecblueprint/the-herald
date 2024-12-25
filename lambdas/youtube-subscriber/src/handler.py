"""
Main handler
"""

from os import environ
import re
import logging
import requests
import boto3

HUB_ENDPOINT = "https://pubsubhubbub.appspot.com/subscribe"
CHANNEL_HANDLES = environ.get("YOUTUBE_CHANNEL_HANDLES")
LAMBDA_FUNCTION_NAME = environ.get("AWS_LAMBDA_FUNCTION_NAME")

# Logging Configuration
logging.getLogger().setLevel(logging.INFO)


# Clients
def main(event, _):
    # pylint: disable=broad-exception-raised
    """
    Main function for subscribing to PubSubHubBub or sending new video
    to SQS Topic for Processing.
    """
    logging.info("Endpoint: %s", HUB_ENDPOINT)
    logging.info("Event Trigger: %s", event)

    callback_url = get_lambda_function_url()
    logging.info("Callback URL: %s", callback_url)

    failed_channels = []
    for channel_handle in CHANNEL_HANDLES.split(","):
        channel_id = scrape_channel_id_from_handle(channel_handle)
        if channel_id is None:
            logging.error("Could not find channel ID for handle - %s", channel_handle)

        logging.info("Found channel ID: %s", channel_id)
        logging.info("Subscribing to channel %s", channel_id)

        params = {
            "hub.mode": "subscribe",
            "hub.topic": f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}",
            "hub.callback": callback_url,
            "hub.verify": "async",  # Asynchronous verification method
            "hub.lease_seconds": "86400",  # Lease for 1 day
        }

        # Make a POST request to subscribe to the topic
        response = requests.post(HUB_ENDPOINT, data=params, timeout=5)

        # Check the response
        if response.status_code == 202:
            logging.info("Subscription request accepted!")
        else:
            logging.error("Subscription request failed: %s", response)
            failed_channels.append(channel_handle)

    return {"failedChannelRequests": failed_channels}


def get_lambda_function_url():
    """
    Gets the URL of the Lambda function
    """
    lambda_client = boto3.client("lambda")
    response = lambda_client.get_function_url_config(FunctionName=LAMBDA_FUNCTION_NAME)
    return response["FunctionUrl"]


def scrape_channel_id_from_handle(handle: str) -> str:
    """
    Scrapes the channel ID from the YouTube handle
    """
    logging.info("Scraping channel ID from handle: %s", handle)

    # Remove the '@' if it’s there
    handle_url_part = handle.lstrip("@")
    url = f"https://www.youtube.com/@{handle_url_part}"

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    # Look for "/channel/UC..." in the HTML
    match = re.search(r"/channel/(UC[0-9A-Za-z_-]+)", response.text)
    if match:
        return match.group(1)
    return None
