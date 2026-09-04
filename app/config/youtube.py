"""
Configuration loading and validation for the YouTube feature.

Adding an approved partner is one line of YAML. Everything that could go
wrong with that line -- an unrecognisable channel reference, a duplicate, a
typo in a field name -- is caught here, at load time, rather than halfway
through a poll.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union
from urllib.parse import urlparse

import yaml

from app.errors import ConfigurationError
from app.models.youtube import (
    CHANNEL_ID_RE,
    KIND_HANDLE,
    KIND_ID,
    KIND_PLAYLIST,
    KIND_USER,
    KIND_VANITY,
    ChannelReference,
)

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static",
    "youtube_sources.yaml",
)

HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{3,30}$")
PLAYLIST_ID_RE = re.compile(r"^(UU|PL|OL|FL|LL|RD)[A-Za-z0-9_-]{8,}$")
LEGACY_NAME_RE = re.compile(r"^[A-Za-z0-9._%-]{1,100}$")

KNOWN_RELATIONSHIPS = frozenset({"COMMUNITY_PARTNER", "DSB", "MEMBER", "SPONSOR"})

SOURCE_FIELDS = frozenset(
    {
        "name",
        "relationship",
        "channel",
        "channel_id",
        "categories",
        "playlist_id",
        "attribution",
    }
)
SECTION_FIELDS = frozenset(
    {
        "enabled",
        "poll_interval_minutes",
        "exclude_shorts",
        "discord_channel_name",
        "discord_channel_id",
        "youtube_sources",
        "message_style",
        "post_delay_seconds",
    }
)

MESSAGE_STYLES = frozenset({"embed", "plain"})


def parse_channel_reference(raw: Any) -> ChannelReference:
    """
    Turn a configured ``channel`` value into a ``ChannelReference``.

    Accepts an ``@handle``, any ``youtube.com`` channel URL, or a canonical
    ``UC...`` id. A bare name with no ``@`` is ambiguous and rejected.

    Raises:
        ConfigurationError: If the value cannot be recognised.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigurationError("'channel' must be a non-empty string")

    value = raw.strip()

    if "youtube.com" in value.lower() or value.lower().startswith(
        ("http://", "https://")
    ):
        return _parse_channel_url(value)

    if value.startswith("@"):
        if not HANDLE_RE.match(value):
            raise ConfigurationError(f"Malformed YouTube handle: {raw!r}")
        return ChannelReference(KIND_HANDLE, value.lower())

    if CHANNEL_ID_RE.match(value):
        return ChannelReference(KIND_ID, value)

    if value.upper().startswith("UC"):
        raise ConfigurationError(
            f"Malformed YouTube channel id: {raw!r}. A channel id is 'UC' "
            "followed by exactly 22 characters."
        )

    raise ConfigurationError(
        f"Ambiguous channel reference: {raw!r}. Use an '@handle', a "
        "youtube.com URL, or a canonical 'UC...' channel id."
    )


def _parse_channel_url(value: str) -> ChannelReference:
    """Parse a ``youtube.com`` URL into a reference, by namespace."""
    candidate = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate)
    host = (parsed.netloc or "").lower().split(":")[0]

    if host and not (host == "youtube.com" or host.endswith(".youtube.com")):
        raise ConfigurationError(f"Not a youtube.com channel URL: {value!r}")

    segments = [segment for segment in (parsed.path or "").split("/") if segment]
    if not segments:
        raise ConfigurationError(f"URL has no channel path: {value!r}")

    first = segments[0]

    if first.startswith("@"):
        if not HANDLE_RE.match(first):
            raise ConfigurationError(f"Malformed YouTube handle in URL: {value!r}")
        return ChannelReference(KIND_HANDLE, first.lower())

    if first == "channel":
        if len(segments) < 2 or not CHANNEL_ID_RE.match(segments[1]):
            raise ConfigurationError(f"Malformed channel id in URL: {value!r}")
        return ChannelReference(KIND_ID, segments[1])

    if first in ("user", "c"):
        if len(segments) < 2 or not LEGACY_NAME_RE.match(segments[1]):
            raise ConfigurationError(f"Malformed legacy channel name in URL: {value!r}")
        kind = KIND_USER if first == "user" else KIND_VANITY
        return ChannelReference(kind, segments[1])

    raise ConfigurationError(
        f"Unrecognised YouTube channel URL: {value!r}. Expected /@handle, "
        "/channel/UC..., /user/... or /c/..."
    )


def parse_playlist_id(raw: Any) -> str:
    """Validate a ``playlist_id`` value."""
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigurationError("'playlist_id' must be a non-empty string")
    value = raw.strip()
    if not PLAYLIST_ID_RE.match(value):
        raise ConfigurationError(f"Malformed YouTube playlist id: {raw!r}")
    return value


@dataclass(frozen=True)
class YouTubeSource:
    """One approved partner, exactly as written in the configuration."""

    name: str
    relationship: str
    reference: ChannelReference
    categories: List[str] = field(default_factory=list)
    attribution: Optional[str] = None

    @property
    def key(self) -> str:
        """Roster identity: the reference as configured, kind included."""
        return self.reference.key

    @property
    def is_playlist(self) -> bool:
        """True when this source polls a playlist feed rather than a channel."""
        return self.reference.kind == KIND_PLAYLIST

    @property
    def relationship_label(self) -> str:
        """Human-readable relationship for the Discord embed footer."""
        return self.relationship.replace("_", " ").title()


@dataclass(frozen=True)
class YouTubeConfig:
    """Validated configuration for the whole feature."""

    # pylint: disable=too-many-instance-attributes

    enabled: bool
    poll_interval_minutes: int
    exclude_shorts: bool
    discord_channel_id: str
    discord_channel_name: str
    sources: List[YouTubeSource]
    message_style: str = "embed"
    post_delay_seconds: float = 0.0

    @property
    def source_keys(self) -> List[str]:
        """Roster keys for every configured source."""
        return [source.key for source in self.sources]


def load_config(
    source: Optional[Union[str, Mapping[str, Any]]] = None,
    env: Optional[Mapping[str, str]] = None,
) -> YouTubeConfig:
    """
    Load and validate the YouTube configuration.

    Args:
        source: A path to a YAML file, an already-parsed mapping (either the
            whole document or just its ``youtube`` section), or None to use
            ``HERALD_YOUTUBE_CONFIG_PATH`` / the bundled default.
        env: Environment mapping, for testing. Defaults to ``os.environ``.

    Returns:
        A validated ``YouTubeConfig``.

    Raises:
        ConfigurationError: For any unusable configuration.
    """
    env = os.environ if env is None else env
    document = _read_document(source, env)
    section = _select_section(document)

    unknown = set(section) - SECTION_FIELDS
    if unknown:
        raise ConfigurationError(
            f"Unknown key(s) in youtube configuration: {', '.join(sorted(unknown))}"
        )

    enabled = _bool_setting(env, "HERALD_YOUTUBE_ENABLED", section.get("enabled", True))
    exclude_shorts = _bool_setting(
        env, "HERALD_YOUTUBE_EXCLUDE_SHORTS", section.get("exclude_shorts", True)
    )
    poll_interval = _int_setting(
        env,
        "HERALD_YOUTUBE_POLL_INTERVAL_MINUTES",
        section.get("poll_interval_minutes", 15),
        "poll_interval_minutes",
    )
    if poll_interval < 1:
        raise ConfigurationError("'poll_interval_minutes' must be at least 1")

    channel_id = _channel_id_setting(env, section, required=enabled)
    channel_name = str(
        env.get("HERALD_DISCORD_CHANNEL_NAME")
        or section.get("discord_channel_name")
        or "content-corner"
    )

    message_style = (
        str(
            env.get("HERALD_YOUTUBE_MESSAGE_STYLE")
            or section.get("message_style")
            or "embed"
        )
        .strip()
        .lower()
    )
    if message_style not in MESSAGE_STYLES:
        raise ConfigurationError(
            f"'message_style' must be one of {sorted(MESSAGE_STYLES)}, got {message_style!r}"
        )

    post_delay = _float_setting(
        env,
        "HERALD_YOUTUBE_POST_DELAY_SECONDS",
        section.get("post_delay_seconds", 0),
        "post_delay_seconds",
    )
    if post_delay < 0:
        raise ConfigurationError("'post_delay_seconds' cannot be negative")

    sources = _parse_sources(section.get("youtube_sources") or [])

    return YouTubeConfig(
        enabled=enabled,
        poll_interval_minutes=poll_interval,
        exclude_shorts=exclude_shorts,
        discord_channel_id=channel_id,
        discord_channel_name=channel_name,
        sources=sources,
        message_style=message_style,
        post_delay_seconds=post_delay,
    )


def _read_document(source, env) -> Mapping[str, Any]:
    """Resolve the configuration source into a parsed mapping."""
    if isinstance(source, Mapping):
        return source

    path = source or env.get("HERALD_YOUTUBE_CONFIG_PATH") or DEFAULT_CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"YouTube configuration file not found: {path}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"Error parsing YouTube configuration {path}: {exc}"
        ) from exc

    if document is None:
        return {}
    if not isinstance(document, Mapping):
        raise ConfigurationError(f"YouTube configuration {path} must be a mapping")
    return document


def _select_section(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept either a whole Herald config document or just its section."""
    if "youtube" in document:
        section = document["youtube"]
        if section is None:
            return {}
        if not isinstance(section, Mapping):
            raise ConfigurationError("'youtube' section must be a mapping")
        return section
    return document


def _parse_sources(raw_sources: Any) -> List[YouTubeSource]:
    """Validate the ``youtube_sources`` list, rejecting duplicates."""
    if not isinstance(raw_sources, list):
        raise ConfigurationError("'youtube_sources' must be a list")

    sources: List[YouTubeSource] = []
    seen: Dict[str, str] = {}

    for index, entry in enumerate(raw_sources):
        source = _parse_source(index, entry)

        duplicate_key = source.key.lower()
        if duplicate_key in seen:
            raise ConfigurationError(
                f"Duplicate channel {source.key!r} configured for both "
                f"'{seen[duplicate_key]}' and '{source.name}'"
            )
        seen[duplicate_key] = source.name
        sources.append(source)

    return sources


def _parse_source(index: int, entry: Any) -> YouTubeSource:
    """Validate one entry of ``youtube_sources``."""
    if not isinstance(entry, Mapping):
        raise ConfigurationError(f"youtube_sources[{index}] must be a mapping")

    unknown = set(entry) - SOURCE_FIELDS
    if unknown:
        raise ConfigurationError(
            f"youtube_sources[{index}] has unknown field(s): {', '.join(sorted(unknown))}"
        )

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError(f"youtube_sources[{index}] is missing 'name'")
    name = name.strip()

    relationship = entry.get("relationship")
    if not isinstance(relationship, str) or not relationship.strip():
        raise ConfigurationError(f"'{name}' is missing 'relationship'")

    attribution = entry.get("attribution")
    if attribution is not None and (
        not isinstance(attribution, str) or not attribution.strip()
    ):
        raise ConfigurationError(f"'{name}': 'attribution' must be a non-empty string")

    return YouTubeSource(
        name=name,
        relationship=relationship.strip().upper().replace(" ", "_").replace("-", "_"),
        reference=_parse_source_reference(name, entry),
        categories=_parse_categories(name, entry.get("categories")),
        attribution=attribution.strip() if attribution else None,
    )


def _parse_source_reference(name: str, entry: Mapping[str, Any]) -> ChannelReference:
    """Resolve one entry's roster identity from ``channel`` or ``playlist_id``."""
    if "channel" in entry and "channel_id" in entry:
        raise ConfigurationError(
            f"'{name}' sets both 'channel' and its legacy alias 'channel_id'; use one"
        )

    raw_channel = entry.get("channel", entry.get("channel_id"))
    if raw_channel is None:
        raise ConfigurationError(f"'{name}' is missing 'channel'")

    try:
        # A playlist replaces the channel as the source's identity, but the
        # channel reference is still validated so a typo cannot hide behind it.
        reference = parse_channel_reference(raw_channel)
        if entry.get("playlist_id") is not None:
            reference = ChannelReference(
                KIND_PLAYLIST, parse_playlist_id(entry["playlist_id"])
            )
    except ConfigurationError as exc:
        raise ConfigurationError(f"'{name}': {exc}") from exc

    return reference


def _parse_categories(name: str, raw: Any) -> List[str]:
    """Validate the optional ``categories`` list."""
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ConfigurationError(f"'{name}': 'categories' must be a list of strings")
    return [item.strip() for item in raw if item.strip()]


def _channel_id_setting(
    env: Mapping[str, str], section: Mapping[str, Any], required: bool = True
) -> str:
    """
    Resolve and validate the announcement channel id.

    Only required when the feature is enabled: a switched-off Herald should
    not need a channel it will never post to.
    """
    raw = env.get("HERALD_DISCORD_CHANNEL_ID") or section.get("discord_channel_id")
    if raw is None or str(raw).strip() == "":
        if not required:
            return ""
        raise ConfigurationError(
            "'discord_channel_id' is required (set it in config or "
            "HERALD_DISCORD_CHANNEL_ID)"
        )
    value = str(raw).strip()
    if not value.isdigit():
        raise ConfigurationError(f"'discord_channel_id' must be numeric, got {raw!r}")
    return value


def _bool_setting(env: Mapping[str, str], variable: str, fallback: Any) -> bool:
    """Read a boolean from the environment, falling back to the YAML value."""
    raw = env.get(variable)
    if raw is None:
        if isinstance(fallback, bool):
            return fallback
        return _coerce_bool(variable, fallback)
    return _coerce_bool(variable, raw)


def _coerce_bool(label: str, raw: Any) -> bool:
    """Coerce a YAML or environment value into a boolean."""
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ConfigurationError(f"{label} must be a boolean, got {raw!r}")


def _int_setting(
    env: Mapping[str, str], variable: str, fallback: Any, label: str
) -> int:
    """Read an integer from the environment, falling back to the YAML value."""
    raw = env.get(variable, fallback)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"'{label}' must be an integer, got {raw!r}") from exc


def _float_setting(
    env: Mapping[str, str], variable: str, fallback: Any, label: str
) -> float:
    """Read a float from the environment, falling back to the YAML value."""
    raw = env.get(variable, fallback)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"'{label}' must be a number, got {raw!r}") from exc
