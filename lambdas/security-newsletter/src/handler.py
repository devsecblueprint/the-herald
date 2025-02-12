import logging
from datetime import datetime, timedelta
import boto3
import feedparser
import pytz
from constants import ARTIFACT_TYPE, TABLE_ARN, FEEDS

# Logging Configuration
logging.getLogger().setLevel(logging.INFO)

class NewsFeedFetcher:
    def __init__(self, feed_name, feed_url):
        """
        Initialize the RSS feed fetcher with a name and feed URL.
        :param feed_name: A descriptive name for the feed (e.g., "Bleeping Computer")
        :param feed_url: The RSS feed URL to fetch articles from
        """
        self.feed_name = feed_name
        self.feed_url = feed_url

    def fetch_articles(self):
        """
        Fetch articles from the specified RSS feed.
        returns a list of dictionaries containing the articles
        """
        feed = feedparser.parse(self.feed_url)
        if feed.bozo:
            raise ValueError(f"Error parsing feed '{self.feed_name}': {feed.bozo_exception}")

        articles = []
        for entry in feed.entries:
            articles.append(
                {
                    "title": entry.title,
                    "link": entry.link,
                    "published": entry.get("published", "N/A"),
                    "summary": entry.get("summary", "N/A"),
                }
            )
        return articles

    def __repr__(self):
        return f"NewsFeedFetcher(feed_name='{self.feed_name}', feed_url='{self.feed_url}')"


def main(event, _):
    """
    This is the main entry point for the Lambda function.
    It takes in the event and context as arguments, and returns the response.
    """
    logging.info("Event: %s", event)

    all_articles = []
    for feed_info in FEEDS:
        fetcher = NewsFeedFetcher(feed_info["name"], feed_info["url"])
        try:
            articles = fetcher.fetch_articles()
            logging.info(f"Fetched {len(articles)} articles from {feed_info['name']}.")
            all_articles.extend(articles)
        except ValueError as e:
            logging.error(f"Error fetching articles from {feed_info['name']}: {e}")

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
