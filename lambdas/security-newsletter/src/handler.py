"""
Main handler.
"""

import os
import logging
from datetime import datetime

import boto3
import feedparser
import pytz


TABLE_ARN = os.environ["DYNAMODB_TABLE_ARN"]
ARTIFACT_TYPE = "newsletter"

# Logging Configuration
logging.getLogger().setLevel(logging.INFO)


def main(event, _):
    """
    This is the main entry point for the Lambda function.
    It takes in the event and context as arguments, and returns the response.
    """
    logging.info("Event: %s", event)

    latest_articles = get_latest_article_with_timezone(fetch_hacker_news_rss())

    logging.info("Latest articles: %s", latest_articles)
    links = [article["link"] for article in latest_articles]
    return publish_message_to_table(links)


def get_latest_article_with_timezone(articles, timezone_str="UTC"):
    """
    Get latest article(s) with a timezone.
    """
    tz = pytz.timezone(timezone_str)
    today = datetime.now(tz).date()
    todays_articles = []
    for article in articles:
        date_str = article["published"]
        parsed_date = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %z")
        formatted_date = parsed_date.isoformat()
        pub_date = datetime.fromisoformat(formatted_date.replace("Z", "+00:00"))
        pub_date = pub_date.astimezone(tz)
        if pub_date.date() == today:
            todays_articles.append(article)

    return todays_articles


def fetch_hacker_news_rss(feed_url="https://feeds.feedburner.com/TheHackersNews"):
    """Fetch articles from The Hacker News RSS feed."""
    # Parse the RSS feed
    feed = feedparser.parse(feed_url)

    if feed.bozo:
        raise ValueError(f"Error parsing feed: {feed.bozo_exception}")

    # Extract articles
    articles = []
    for entry in feed.entries:
        articles.append(
            {
                "title": entry.title,
                "link": entry.link,
                "published": entry.published,
                "summary": entry.summary,
            }
        )

    return articles


def publish_message_to_table(links: str):
    """
    Sends a message to the SQS queue.
    Returns a dictionary with the message and the message ID.
    """
    logging.info("Sending message to SQS queue")

    dynamodb_client = boto3.client("dynamodb")

    for link in links:
        logging.info("Link: %s", link)
        response = dynamodb_client.put_item(
            TableName=TABLE_ARN, Item={"string": {"type": ARTIFACT_TYPE, "link": link}}
        )
        logging.info("Response: %s", response)

    return {"message": "Message has been logged to DynamoDB!", "links": links}
