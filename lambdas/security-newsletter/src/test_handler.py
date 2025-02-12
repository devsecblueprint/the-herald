import unittest
from handler import NewsFeedFetcher, get_latest_article_with_timezone
from constants import FEEDS

class TestNewsFeedFetcher(unittest.TestCase):
    def test_fetch_and_parse_dates(self):
        """
        Test the functionality of fetching feeds.
        """

        all_articles = []
        for feed_info in FEEDS:
            fetcher = NewsFeedFetcher(feed_info["name"], feed_info["url"])
            try:
                articles = fetcher.fetch_articles()
                all_articles.extend(articles)
            except ValueError as e:
                self.fail(f"Error with feed '{feed_info['name']}': {e}")

        latest_articles = get_latest_article_with_timezone(all_articles)
        print(f"Total articles fetched: {len(latest_articles)}")
        print(f"Today's articles: {len(latest_articles)}")

        self.assertGreater(len(latest_articles), 0, "No articles were fetched.")

if __name__ == "__main__":
    unittest.main()
