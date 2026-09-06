"""Configuration parsing and validation."""

import pytest

from app.config.youtube import load_config, parse_channel_reference, parse_playlist_id
from app.errors import ConfigurationError

CHANNEL_ID = "UCAAAAAAAAAAAAAAAAAAAAAA"


def base_document(sources=None, **overrides):
    """A minimal valid configuration document."""
    section = {
        "discord_channel_id": "123456789012345678",
        "youtube_sources": (
            sources
            if sources is not None
            else [
                {
                    "name": "Damien Burks",
                    "relationship": "COMMUNITY_PARTNER",
                    "channel": "@damienjburks",
                }
            ]
        ),
    }
    section.update(overrides)
    return {"youtube": section}


# -- channel references -----------------------------------------------------


@pytest.mark.parametrize(
    "written, kind, value",
    [
        ("@damienjburks", "handle", "@damienjburks"),
        ("https://www.youtube.com/@damienjburks", "handle", "@damienjburks"),
        ("http://youtube.com/@damienjburks", "handle", "@damienjburks"),
        ("www.youtube.com/@damienjburks", "handle", "@damienjburks"),
        (f"https://www.youtube.com/channel/{CHANNEL_ID}", "id", CHANNEL_ID),
        (CHANNEL_ID, "id", CHANNEL_ID),
        ("https://www.youtube.com/user/somebody", "user", "somebody"),
        ("https://www.youtube.com/c/SomeVanityName", "vanity", "SomeVanityName"),
    ],
)
def test_every_documented_channel_form_is_accepted(written, kind, value):
    reference = parse_channel_reference(written)
    assert (reference.kind, reference.value) == (kind, value)


def test_handles_are_normalised_to_lower_case():
    # YouTube handles are case-insensitive, so a capitalisation change in the
    # config must not read as a different source and re-onboard the partner.
    assert parse_channel_reference("@DamienJBurks").key == "handle:@damienjburks"


def test_handle_url_with_trailing_path_still_resolves():
    assert (
        parse_channel_reference("https://www.youtube.com/@damienjburks/videos").key
        == "handle:@damienjburks"
    )


def test_a_bare_name_is_rejected_as_ambiguous():
    with pytest.raises(ConfigurationError, match="Ambiguous"):
        parse_channel_reference("damienjburks")


@pytest.mark.parametrize(
    "malformed",
    [
        "UC123",
        "UCAAAAAAAAAAAAAAAAAAAAA",  # 21 id characters
        "UCAAAAAAAAAAAAAAAAAAAAAAA",  # 23 id characters
        "UCAAAAAAAAAAAAAAAAAAAA!!",
    ],
)
def test_a_malformed_channel_id_is_never_silently_truncated(malformed):
    # A partial match would turn a typo into a valid-looking id and poll the
    # wrong channel forever.
    with pytest.raises(ConfigurationError, match="Malformed YouTube channel id"):
        parse_channel_reference(malformed)


def test_a_malformed_channel_id_inside_a_url_is_rejected():
    with pytest.raises(ConfigurationError, match="Malformed channel id"):
        parse_channel_reference("https://www.youtube.com/channel/UC123")


def test_a_non_youtube_url_is_rejected():
    with pytest.raises(ConfigurationError, match="Not a youtube.com channel URL"):
        parse_channel_reference("https://vimeo.com/@somebody")


def test_an_unrecognised_youtube_path_is_rejected():
    with pytest.raises(ConfigurationError, match="Unrecognised YouTube channel URL"):
        parse_channel_reference("https://www.youtube.com/watch?v=abc123")


def test_user_and_vanity_namespaces_never_collide():
    user = parse_channel_reference("https://www.youtube.com/user/foo")
    vanity = parse_channel_reference("https://www.youtube.com/c/foo")
    assert user.key != vanity.key
    assert (user.key, vanity.key) == ("user:foo", "vanity:foo")


def test_an_empty_channel_value_is_rejected():
    with pytest.raises(ConfigurationError, match="non-empty string"):
        parse_channel_reference("   ")


@pytest.mark.parametrize("playlist", ["UUAAAAAAAAAAAAAAAAAAAAAA", "PLabcdefghij"])
def test_playlist_ids_are_accepted(playlist):
    assert parse_playlist_id(playlist) == playlist


def test_a_malformed_playlist_id_is_rejected():
    with pytest.raises(ConfigurationError, match="Malformed YouTube playlist id"):
        parse_playlist_id("not-a-playlist")


# -- documents --------------------------------------------------------------


def test_a_minimal_document_loads():
    config = load_config(base_document(), env={})
    assert config.enabled is True
    assert config.poll_interval_minutes == 15
    assert config.exclude_shorts is True
    assert config.discord_channel_name == "content-corner"
    assert [source.key for source in config.sources] == ["handle:@damienjburks"]


def test_a_bare_section_without_the_youtube_key_loads():
    config = load_config(base_document()["youtube"], env={})
    assert len(config.sources) == 1


def test_sources_default_to_no_categories():
    config = load_config(base_document(), env={})
    assert config.sources[0].categories == []


def test_relationship_is_normalised_and_labelled():
    config = load_config(
        base_document(
            sources=[
                {
                    "name": "DSB",
                    "relationship": "community partner",
                    "channel": "@thedsbcommunity",
                }
            ]
        ),
        env={},
    )
    source = config.sources[0]
    assert source.relationship == "COMMUNITY_PARTNER"
    assert source.relationship_label == "Community Partner"


def test_an_unknown_relationship_is_accepted_and_title_cased():
    config = load_config(
        base_document(
            sources=[
                {"name": "Someone", "relationship": "ALUMNI", "channel": "@someone"}
            ]
        ),
        env={},
    )
    assert config.sources[0].relationship_label == "Alumni"


def test_channel_id_is_accepted_as_a_legacy_alias():
    config = load_config(
        base_document(
            sources=[
                {"name": "Someone", "relationship": "MEMBER", "channel_id": CHANNEL_ID}
            ]
        ),
        env={},
    )
    assert config.sources[0].key == f"id:{CHANNEL_ID}"


def test_setting_both_channel_and_its_alias_is_an_error():
    with pytest.raises(ConfigurationError, match="use one"):
        load_config(
            base_document(
                sources=[
                    {
                        "name": "Someone",
                        "relationship": "MEMBER",
                        "channel": "@someone",
                        "channel_id": CHANNEL_ID,
                    }
                ]
            ),
            env={},
        )


def test_a_playlist_id_replaces_the_channel_as_the_source_identity():
    config = load_config(
        base_document(
            sources=[
                {
                    "name": "Someone",
                    "relationship": "MEMBER",
                    "channel": "@someone",
                    "playlist_id": "UUAAAAAAAAAAAAAAAAAAAAAA",
                }
            ]
        ),
        env={},
    )
    source = config.sources[0]
    assert source.is_playlist is True
    assert source.key == "playlist:UUAAAAAAAAAAAAAAAAAAAAAA"


def test_duplicate_channels_are_rejected_case_insensitively():
    with pytest.raises(ConfigurationError, match="Duplicate channel"):
        load_config(
            base_document(
                sources=[
                    {
                        "name": "One",
                        "relationship": "MEMBER",
                        "channel": "@damienjburks",
                    },
                    {
                        "name": "Two",
                        "relationship": "MEMBER",
                        "channel": "@DamienJBurks",
                    },
                ]
            ),
            env={},
        )


def test_an_unknown_source_field_is_rejected():
    with pytest.raises(ConfigurationError, match="unknown field"):
        load_config(
            base_document(
                sources=[
                    {
                        "name": "One",
                        "relationship": "MEMBER",
                        "channel": "@someone",
                        "enabled": True,
                    }
                ]
            ),
            env={},
        )


def test_an_unknown_section_key_is_rejected():
    with pytest.raises(ConfigurationError, match="Unknown key"):
        load_config(base_document(backfill=True), env={})


@pytest.mark.parametrize("missing", ["name", "relationship", "channel"])
def test_a_missing_required_field_is_rejected(missing):
    entry = {"name": "One", "relationship": "MEMBER", "channel": "@someone"}
    del entry[missing]
    with pytest.raises(ConfigurationError):
        load_config(base_document(sources=[entry]), env={})


def test_a_missing_discord_channel_id_is_rejected():
    document = base_document()
    del document["youtube"]["discord_channel_id"]
    with pytest.raises(ConfigurationError, match="discord_channel_id"):
        load_config(document, env={})


def test_a_non_numeric_discord_channel_id_is_rejected():
    with pytest.raises(ConfigurationError, match="must be numeric"):
        load_config(base_document(discord_channel_id="content-corner"), env={})


def test_a_disabled_feature_does_not_need_a_channel_id():
    document = base_document(enabled=False)
    del document["youtube"]["discord_channel_id"]
    config = load_config(document, env={})
    assert config.enabled is False
    assert config.discord_channel_id == ""


def test_no_notify_role_id_defaults_to_empty():
    assert load_config(base_document(), env={}).notify_role_id == ""


def test_a_notify_role_id_is_read_from_the_document():
    config = load_config(base_document(discord_notify_role_id="42"), env={})
    assert config.notify_role_id == "42"


def test_the_environment_overrides_the_notify_role_id():
    config = load_config(
        base_document(discord_notify_role_id="42"),
        env={"HERALD_DISCORD_NOTIFY_ROLE_ID": "99"},
    )
    assert config.notify_role_id == "99"


def test_a_non_numeric_notify_role_id_is_rejected():
    with pytest.raises(ConfigurationError, match="discord_notify_role_id"):
        load_config(base_document(discord_notify_role_id="Notifs"), env={})


def test_an_unusable_message_style_is_rejected():
    with pytest.raises(ConfigurationError, match="message_style"):
        load_config(base_document(message_style="haiku"), env={})


def test_a_zero_poll_interval_is_rejected():
    with pytest.raises(ConfigurationError, match="at least 1"):
        load_config(base_document(poll_interval_minutes=0), env={})


# -- environment overrides --------------------------------------------------


def test_environment_overrides_the_document():
    config = load_config(
        base_document(),
        env={
            "HERALD_YOUTUBE_ENABLED": "false",
            "HERALD_YOUTUBE_EXCLUDE_SHORTS": "false",
            "HERALD_YOUTUBE_POLL_INTERVAL_MINUTES": "30",
            "HERALD_DISCORD_CHANNEL_ID": "999999999999999999",
            "HERALD_DISCORD_CHANNEL_NAME": "partner-uploads",
            "HERALD_YOUTUBE_MESSAGE_STYLE": "plain",
            "HERALD_YOUTUBE_POST_DELAY_SECONDS": "2.5",
        },
    )
    assert config.enabled is False
    assert config.exclude_shorts is False
    assert config.poll_interval_minutes == 30
    assert config.discord_channel_id == "999999999999999999"
    assert config.discord_channel_name == "partner-uploads"
    assert config.message_style == "plain"
    assert config.post_delay_seconds == 2.5


def test_an_unparseable_boolean_environment_value_is_rejected():
    with pytest.raises(ConfigurationError, match="must be a boolean"):
        load_config(base_document(), env={"HERALD_YOUTUBE_ENABLED": "maybe"})


# -- files ------------------------------------------------------------------


def test_a_yaml_file_loads(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        "youtube:\n"
        "  discord_channel_id: '123456789012345678'\n"
        "  youtube_sources:\n"
        "    - name: Damien Burks\n"
        "      relationship: COMMUNITY_PARTNER\n"
        "      channel: '@damienjburks'\n",
        encoding="utf-8",
    )
    config = load_config(str(path), env={})
    assert config.sources[0].name == "Damien Burks"


def test_the_config_path_can_come_from_the_environment(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        "youtube:\n  discord_channel_id: '1'\n  youtube_sources: []\n", encoding="utf-8"
    )
    config = load_config(None, env={"HERALD_YOUTUBE_CONFIG_PATH": str(path)})
    assert config.sources == []


def test_a_missing_file_is_reported_clearly():
    with pytest.raises(ConfigurationError, match="not found"):
        load_config("/nonexistent/youtube.yaml", env={})


def test_malformed_yaml_is_reported_clearly(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text("youtube: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Error parsing"):
        load_config(str(path), env={})


def test_the_bundled_config_file_is_valid():
    from app.config.youtube import DEFAULT_CONFIG_PATH

    config = load_config(DEFAULT_CONFIG_PATH, env={"HERALD_DISCORD_CHANNEL_ID": "1"})
    assert config.discord_channel_name == "content-corner"
    assert config.sources
