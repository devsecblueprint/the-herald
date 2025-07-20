"""
This module defines the NewsletterService class, which is responsible for fetching articles from RSS feeds and publishing them to a Discord channel.
It initializes the DiscordService, fetches articles from configured feeds, and publishes the latest articles to the specified Discord channel.
It also handles timezone conversion for article publication dates.
"""

import time
from datetime import datetime
import feedparser
import pytz

from services.discord import DiscordService
from clients.redis import RedisClient
from config.logger import LoggerConfig
from models import FeedsConfig, Feed


class NewsletterService:
    """
    NewsletterService is responsible for fetching articles from RSS feeds and publishing them to a Discord channel.
    It initializes the DiscordService, fetches articles from configured feeds, and publishes the latest articles to the specified Discord channel.
    It also handles timezone conversion for article publication dates.

    Attributes:
        discord_service (DiscordService): Service to interact with Discord API.
        channel_name (str): Name of the Discord channel to publish articles.
        redis_client (RedisClient): Redis client for caching and storing data.
    """

    def __init__(self):
        self.logger = LoggerConfig(__name__).get_logger()
        self.discord_service = DiscordService()

        # Initialize Redis client
        RedisClient().connect()
        self.redis_client = RedisClient().client

    def publish_latest_articles(self):
        """
        Fetches articles from configured RSS feeds and publishes the latest articles to the specified Discord channel.
        It retrieves the articles, checks if they are already published in the Discord channel, and sends new articles.
        It also stores the published articles in Redis for caching.
        """
        self.logger.info("Starting to publish latest articles...")

        # Fetch all articles from configured feeds
        all_articles = []

        feeds = FeedsConfig.from_yaml().feeds
        for feed in feeds:
            self.logger.info("Fetching articles from feed: %s", feed.name)
            articles = self._fetch_articles(feed)
            all_articles.extend(articles)

        # Get today's articles from the combined list
        latest_articles = self.get_latest_article_with_timezone(all_articles)

        self.logger.info("Latest articles: %s", latest_articles)

        # Extract links from all articles
        newsletter_info = [
            (article["link"], article["channel_name"]) for article in latest_articles
        ]

        # Log the newsletter info
        for newsletter in newsletter_info:
            link = newsletter[0]
            channel_name = newsletter[1]
            self.logger.info("Link: %s", link)
            try:
                channel_id = self.discord_service.get_channel_id(channel_name)
                if self.discord_service.check_messages_in_discord([link], channel_id):
                    self.discord_service.send_message_to_channel(
                        channel_id,
                        link,
                    )

                time.sleep(3)  # Small delay to prevent rate limiting
            except Exception as e:
                self.logger.error("Error processing message: %s", str(e))
                continue

        # Store the newsletter info in Redis
        self.redis_client.set(
            f"newsletter:{self.channel_name}",
            newsletter_info,
            expiration=24 * 60 * 60,  # Store for 24 hours
        )

        self.logger.info("Stored newsletter info in Redis")

    def get_latest_article_with_timezone(self, articles, timezone_str="UTC"):
        """
        Filters articles to get only those published today in the specified timezone.
        Args:
            articles (list): List of articles to filter.
            timezone_str (str): Timezone string to filter articles by.
        Returns:
            list: List of articles published today in the specified timezone.
        """
        self.logger.info(
            "Filtering articles for today's date in timezone: %s", timezone_str
        )

        if not articles:
            self.logger.warning("No articles provided for filtering.")
            return []

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
                self.logger.error("Error parsing date '%s': %s", date_str, e)
                continue
            pub_date = parsed_date.astimezone(tz)
            if pub_date.date() == today:
                todays_articles.append(article)
        return todays_articles

    def _fetch_articles(self, feed: Feed) -> list:
        """
        Fetch articles from the specified RSS feed.
        returns a list of dictionaries containing the articles.
        Args:
            feed (Feed): The Feed object containing the feed configuration.
        Returns:
            list: List of articles fetched from the feed.
        Raises:
            ValueError: If there is an error parsing the feed.
        """
        feed = feedparser.parse(feed.url)
        if feed.bozo:
            raise ValueError(f"Error parsing feed '{feed.name}': {feed.bozo_exception}")

        articles = []
        for entry in feed.entries:
            articles.append(
                {
                    "title": entry.title,
                    "link": entry.link,
                    "published": entry.get("published", "N/A"),
                    "summary": entry.get("summary", "N/A"),
                    "channel_name": self.channel_name,
                }
            )

        self.logger.info("Fetched %d articles from feed '%s'", len(articles), feed.name)

        return articles
