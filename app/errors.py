"""
The Herald's domain exceptions.

Errors live at the application root because every layer raises them:
clients, repositories, configuration and services alike. Keeping them here
is what lets a repository signal a failure without a service importing it,
or a client without importing a service.

Every failure a poll is expected to survive is one of these. The polling
service records them as ``SourceFailure`` entries and carries on with the
next source, so a single bad partner never stops a poll.
"""


class YouTubeError(Exception):
    """Base class for every error raised by the YouTube feature."""


class ConfigurationError(YouTubeError):
    """Raised at load time for invalid or unusable configuration."""


class ChannelResolutionError(YouTubeError):
    """Raised when an ``@handle`` (or other reference) cannot be resolved."""


class FeedFetchError(YouTubeError):
    """Raised when a channel or playlist feed cannot be fetched or parsed."""


class YouTubeApiError(YouTubeError):
    """Raised when a YouTube endpoint cannot answer the question it was asked."""


class RosterError(YouTubeError):
    """Raised when the source roster cannot be read or written."""


class RepositoryError(YouTubeError):
    """Raised when a per-video state transition cannot be applied."""


class ClassificationError(YouTubeError):
    """Raised when a video cannot be decided as Short vs long-form."""


class DistributionError(YouTubeError):
    """A confirmed delivery failure. Nothing was posted; retry is safe."""


class AmbiguousDeliveryError(DistributionError):
    """
    Delivery may or may not have happened.

    The connection dropped, the response body was unreadable, or Discord
    answered 2xx without a message id. The claim is kept so the video is
    never announced twice.
    """


class RosterWriteConflict(RosterError):
    """Another poll wrote the roster first. The next poll reconciles."""
