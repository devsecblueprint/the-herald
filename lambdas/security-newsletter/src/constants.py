"""
This file contains the constants used in the security-newsletter lambda.
"""

import os

TABLE_ARN = os.environ.get("DYNAMODB_TABLE_ARN")
ARTIFACT_TYPE = "newsletter"
FEEDS = [
    {
        "name": "Bleeping Computer",
        "url": "https://www.bleepingcomputer.com/feed/",
        "channel_name": "🔐-security-news",
    },
    {
        "name": "The Hacker News",
        "url": "https://feeds.feedburner.com/TheHackersNews",
        "channel_name": "🔐-security-news",
    },
    {
        "name": "CNBC Technology",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069",
        "channel_name": "🪙-investment-news",
    },
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed",
        "channel_name": "🪙-investment-news",
    },
]
