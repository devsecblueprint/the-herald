"""
Data models for The Herald bot configuration and data structures.
"""

import os
from dataclasses import dataclass
from typing import List

import yaml


@dataclass
class Feed:
    """
    Represents a news feed configuration.

    Attributes:
        name: Human-readable name of the feed source
        url: RSS/feed URL to fetch content from
        channel_name: Discord channel name where content should be posted
    """

    name: str
    url: str
    channel_name: str

    def __post_init__(self):
        """Validate feed data after initialization."""
        if not self.name:
            raise ValueError("Feed name cannot be empty")
        if not self.url:
            raise ValueError("Feed URL cannot be empty")
        if not self.channel_name:
            raise ValueError("Channel name cannot be empty")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("Feed URL must be a valid HTTP/HTTPS URL")


@dataclass
class FeedsConfig:
    """
    Container for all feed configurations.

    Attributes:
        feeds: List of Feed objects
    """

    feeds: List[Feed]

    @classmethod
    def from_yaml(cls, yaml_path: str = None) -> "FeedsConfig":
        """
        Load feeds configuration from YAML file.

        Args:
            yaml_path: Path to YAML configuration file.
                      Defaults to app/static/config.yaml

        Returns:
            FeedsConfig instance with loaded feeds

        Raises:
            FileNotFoundError: If the YAML file doesn't exist
            yaml.YAMLError: If the YAML file is malformed
            ValueError: If required configuration is missing
        """
        if yaml_path is None:
            # Default to the config.yaml in the static directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            yaml_path = os.path.join(current_dir, "static", "config.yaml")

        try:
            with open(yaml_path, "r", encoding="utf-8") as file:
                config_data = yaml.safe_load(file)
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {yaml_path}")
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Error parsing YAML file: {e}")

        if not config_data or "feeds" not in config_data:
            raise ValueError("Configuration file must contain 'feeds' section")

        feeds = []
        for feed_data in config_data["feeds"]:
            try:
                feed = Feed(
                    name=feed_data["name"],
                    url=feed_data["url"],
                    channel_name=feed_data["channel_name"],
                )
                feeds.append(feed)
            except KeyError as e:
                raise ValueError(f"Missing required field in feed configuration: {e}")

        return cls(feeds=feeds)

    def get_feeds_by_channel(self, channel_name: str) -> List[Feed]:
        """
        Get all feeds for a specific Discord channel.

        Args:
            channel_name: Name of the Discord channel

        Returns:
            List of Feed objects for the specified channel
        """
        return [feed for feed in self.feeds if feed.channel_name == channel_name]

    def get_feed_by_name(self, name: str) -> Feed:
        """
        Get a specific feed by its name.

        Args:
            name: Name of the feed

        Returns:
            Feed object with the specified name

        Raises:
            ValueError: If no feed with the given name is found
        """
        for feed in self.feeds:
            if feed.name == name:
                return feed
        raise ValueError(f"No feed found with name: {name}")

    def get_all_channel_names(self) -> List[str]:
        """
        Get all unique channel names from the feeds configuration.

        Returns:
            List of unique Discord channel names
        """
        return list(set(feed.channel_name for feed in self.feeds))


# Example usage:
if __name__ == "__main__":
    # Load configuration from YAML
    try:
        config = FeedsConfig.from_yaml()
        print(f"Loaded {len(config.feeds)} feeds:")

        for feed in config.feeds:
            print(f"  - {feed.name} -> {feed.channel_name}")

        # Example: Get all security news feeds
        security_feeds = config.get_feeds_by_channel("🔐-security-news")
        print(f"\nSecurity feeds: {len(security_feeds)}")
        for feed in security_feeds:
            print(f"  - {feed.name}: {feed.url}")

    except Exception as e:
        print(f"Error loading configuration: {e}")
