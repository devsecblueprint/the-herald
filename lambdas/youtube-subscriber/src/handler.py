"""
Main handler
"""

from os import environ
import re
import logging
import requests

HUB_ENDPOINT = "https://pubsubhubbub.appspot.com/subscribe"
CALLBACK_URL = environ.get("CALLBACK_URL")
CHANNEL_HANDLES = environ.get("YOUTUBE_CHANNEL_HANDLES")

# Logging Configuration
logging.getLogger().setLevel(logging.INFO)


# Clients
def main(event, _):
    # pylint: disable=broad-exception-raised
    """
    Main function for subscribing to PubSubHubBub
    """

    logging.info("Endpoint: %s", HUB_ENDPOINT)
    logging.info("Event Trigger: %s", event)

    for channel_handle in CHANNEL_HANDLES.split(","):
        channel_id = scrape_channel_id_from_handle(channel_handle)
        if channel_id is None:
            logging.error(f"Could not find channel ID for handle - {channel_handle}")

        logging.info("Found channel ID: %s", channel_id)
        logging.info("Subscribing to channel %s", channel_id)

        params = {
            "hub.mode": "subscribe",
            "hub.topic": f"https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}",
            "hub.callback": CALLBACK_URL,
            "hub.verify": "async",  # Asynchronous verification method
            "hub.lease_seconds": "86400",  # Lease for 1 day
        }

        # Make a POST request to subscribe to the topic
        response = requests.post(HUB_ENDPOINT, data=params, timeout=5)

        # Check the response
        if response.status_code == 202:
            logging.info("Subscription request accepted!")

        logging.error("Subscription request failed: %s", response)


def scrape_channel_id_from_handle(handle: str) -> str:
    # Remove the '@' if it’s there
    handle_url_part = handle.lstrip("@")
    url = f"https://www.youtube.com/@{handle_url_part}"

    response = requests.get(url)
    response.raise_for_status()

    # Look for "/channel/UC..." in the HTML
    match = re.search(r"/channel/(UC[0-9A-Za-z_-]+)", response.text)
    if match:
        return match.group(1)
    return None
