"""
Shared DynamoDB helpers for the repository layer.

Conditional writes are how every repository here stays correct under
concurrency, and telling a *rejected* condition apart from a *failed*
request is the distinction each one turns on.
"""

from botocore.exceptions import ClientError

CONDITION_FAILED = "ConditionalCheckFailedException"

DEFAULT_KEY_ATTRIBUTE = "content_id"
DEFAULT_TTL_ATTRIBUTE = "ttl"


def is_condition_failure(exc: ClientError) -> bool:
    """True when a ClientError is a failed conditional write."""
    return exc.response.get("Error", {}).get("Code") == CONDITION_FAILED
