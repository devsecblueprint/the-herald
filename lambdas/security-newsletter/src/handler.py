import os
import logging
from datetime import datetime, timedelta

import boto3
import feedparser
import pytz


TABLE_ARN = "none"  # os.environ["DYNAMODB_TABLE_ARN"]
ARTIFACT_TYPE = "newsletter"

# Logging Configuration
logging.getLogger().setLevel(logging.INFO)


def main(event, _):
    """
    This is the main entry point for the Lambda function.
    It takes in the event and context as arguments, and returns the response.
    """
    logging.info("Event: %s", event)

    # Fetch articles from both feeds
    bleeping_articles = fetch_bleeping_computer_rss()
    hacker_articles = fetch_hacker_news_rss()

    # Combine articles from both feeds
    all_articles = bleeping_articles + hacker_articles

    # Get today's articles from the combined list
    latest_articles = get_latest_article_with_timezone(all_articles)

    logging.info("Latest articles: %s", latest_articles)

    # Extract links from all articles
    newsletter_links = [article["link"] for article in latest_articles]

    return publish_message_to_table(newsletter_links)


def get_latest_article_with_timezone(articles, timezone_str="UTC"):
    """
    Get latest article(s) with a timezone.
    """
    tz = pytz.timezone(timezone_str)
    today = datetime.now(tz).date()
    todays_articles = []
    for article in articles:
        date_str = article["published"]
        # Handle 'GMT' timezone suffix
        if date_str.endswith(" GMT"):
            date_str = date_str.replace(" GMT", " +0000")
        try:
            parsed_date = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %z")
        except ValueError as e:
            logging.error("Error parsing date '%s': %s", date_str, e)
            continue
        pub_date = parsed_date.astimezone(tz)
        if pub_date.date() == today:
            todays_articles.append(article)
    return todays_articles


def fetch_bleeping_computer_rss(feed_url="https://www.bleepingcomputer.com/feed/"):
    """Fetch articles from the Bleeping Computer RSS feed."""
    feed = feedparser.parse(feed_url)
    if feed.bozo:
        raise ValueError(f"Error parsing feed: {feed.bozo_exception}")
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


def fetch_hacker_news_rss(feed_url="https://feeds.feedburner.com/TheHackersNews"):
    """Fetch articles from The Hacker News RSS feed."""
    feed = feedparser.parse(feed_url)
    if feed.bozo:
        raise ValueError(f"Error parsing feed: {feed.bozo_exception}")
    h_articles = []
    for entry in feed.entries:
        h_articles.append(
            {
                "title": entry.title,
                "link": entry.link,
                "published": entry.published,
                "summary": entry.summary,
            }
        )
    return h_articles


def publish_message_to_table(links: str):
    """
    Sends a message to the DynamoDB table.
    Returns a dictionary with the message and the message ID.
    """
    logging.info("Sending message to DynamoDB table")
    dynamodb_client = boto3.client("dynamodb")
    for link in links:
        logging.info("Link: %s", link)
        response = dynamodb_client.put_item(
            TableName=TABLE_ARN,
            Item={
                "type": {"S": ARTIFACT_TYPE},
                "link": {"S": link},
                "expirationDate": {
                    "S": str(int((datetime.now() + timedelta(days=1)).timestamp()))
                },
            },
        )
        logging.info("Response: %s", response)
    return {"message": "Message has been logged to DynamoDB!", "links": links}
