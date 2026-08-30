"""
The Herald's YouTube ingestion and Discord distribution feature.

Subscribes to approved DSB partner YouTube channels, detects newly
published long-form videos, and announces them in #content-corner.

Adding an approved partner is a configuration change -- one line with their
``@handle`` -- never a code change. There is no backfill: a partner
onboarded today gets their next upload announced, not their back catalogue.
"""

from app.services.youtube.config import (
    YouTubeConfig,
    YouTubeSource,
    load_config,
    parse_channel_reference,
)
from app.services.youtube.errors import (
    AmbiguousDeliveryError,
    ChannelResolutionError,
    ClassificationError,
    ConfigurationError,
    DistributionError,
    FeedFetchError,
    RepositoryError,
    RosterError,
    RosterWriteConflict,
    YouTubeError,
)
from app.services.youtube.factory import build_pipeline
from app.services.youtube.models import (
    AnnouncedItem,
    ChannelReference,
    ContentItem,
    DeliveryReceipt,
    PollResult,
    SourceFailure,
    SourceFetchResult,
)
from app.services.youtube.pipeline import YouTubePipeline

__all__ = [
    "AmbiguousDeliveryError",
    "AnnouncedItem",
    "ChannelReference",
    "ChannelResolutionError",
    "ClassificationError",
    "ConfigurationError",
    "ContentItem",
    "DeliveryReceipt",
    "DistributionError",
    "FeedFetchError",
    "PollResult",
    "RepositoryError",
    "RosterError",
    "RosterWriteConflict",
    "SourceFailure",
    "SourceFetchResult",
    "YouTubeConfig",
    "YouTubeError",
    "YouTubePipeline",
    "YouTubeSource",
    "build_pipeline",
    "load_config",
    "parse_channel_reference",
]
