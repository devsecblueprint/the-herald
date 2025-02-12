import os

TABLE_ARN = os.environ.get("DYNAMODB_TABLE_ARN")
ARTIFACT_TYPE = "newsletter"
FEEDS = [
    {"name": "Bleeping Computer", "url": "https://www.bleepingcomputer.com/feed/"},
    {"name": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews"},
    {"name": "CNBC Technology",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069"},
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed"},
]