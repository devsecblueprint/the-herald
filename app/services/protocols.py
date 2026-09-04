"""
The two seams that keep ingestion and publishing independent.

A future LinkedIn, podcast or partner-blog integration implements
``IngestionService`` and emits ``ContentItem``s with a new ``platform``
value. Because dedupe keys are ``"<platform>#<content id>"``, the shared
table stays collision-free and the publishing service is untouched.
"""

from typing import Protocol

from app.models.youtube import ContentItem, DeliveryReceipt, SourceFetchResult


class IngestionService(Protocol):
    """Fetches content for one configured source."""

    platform: str

    def fetch(self, source) -> SourceFetchResult:
        """
        Return everything currently published by one source.

        Raises:
            YouTubeError: If the source cannot be read.
        """


class PublishingService(Protocol):
    """Publishes a single content item to its destination."""

    def deliver(self, item: ContentItem) -> DeliveryReceipt:
        """
        Publish one item and return proof of delivery.

        Raises:
            DistributionError: On a confirmed failure.
            AmbiguousDeliveryError: When the outcome is unknown.
        """
