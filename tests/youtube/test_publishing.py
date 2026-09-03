"""The message that lands in Discord, and the per-video workflow."""

import pytest

from app.errors import (AmbiguousDeliveryError, ClassificationError,
                        DistributionError, RepositoryError)
from app.models.youtube import (SKIP_REASON_SHORT, STATUS_POSTED,
                                STATUS_POSTING, STATUS_SKIPPED, ShortsVerdict)
from app.repositories.youtube.processing import ProcessingRepository
from app.services.youtube.publishing import (DEFAULT_COLOUR,
                                             RELATIONSHIP_COLOURS,
                                             YouTubePublishingService,
                                             build_message)
from app.utils.clock import to_iso
from app.utils.text import truncate
from tests.youtube.fakes import (FakeClock, FakeTable, RecordingTransport,
                                 StubDetector, make_item, make_source,
                                 throttling_error)

CHANNEL = "123456789012345678"


# -- the message ------------------------------------------------------------


def test_the_content_line_names_the_partner():
    payload = build_message(make_item())
    assert payload["content"] == "\U0001F4FA New from **Damien Burks** on YouTube"


def test_announcements_never_ping_a_channel():
    assert build_message(make_item())["allowed_mentions"] == {"parse": []}


def test_the_embed_carries_the_video():
    item = make_item("abc123")
    embed = build_message(item)["embeds"][0]

    assert embed["title"] == item.title
    assert embed["url"] == item.url
    assert embed["description"] == "A walkthrough."
    assert embed["timestamp"] == to_iso(item.published_at)
    assert embed["thumbnail"] == {"url": item.thumbnail_url}
    assert embed["author"] == {"name": item.author_name, "url": item.author_url}
    assert embed["footer"] == {"text": "Damien Burks • Community Partner • YouTube"}


def test_the_raw_url_lives_in_a_field_so_discord_adds_no_second_preview():
    embed = build_message(make_item("abc123"))["embeds"][0]
    assert embed["fields"] == [
        {"name": "Watch", "value": "https://www.youtube.com/watch?v=abc123", "inline": False}
    ]
    assert "youtube.com" not in build_message(make_item("abc123"))["content"]


@pytest.mark.parametrize("relationship", sorted(RELATIONSHIP_COLOURS))
def test_the_colour_is_keyed_to_the_relationship(relationship):
    item = make_item(relationship=relationship)
    assert build_message(item)["embeds"][0]["color"] == RELATIONSHIP_COLOURS[relationship]


def test_an_unknown_relationship_gets_the_default_colour():
    assert build_message(make_item(relationship="ALUMNI"))["embeds"][0]["color"] == DEFAULT_COLOUR


def test_a_long_description_is_trimmed_to_400_characters():
    embed = build_message(make_item(description="x" * 900))["embeds"][0]
    assert len(embed["description"]) == 400
    assert embed["description"].endswith("…")


def test_a_long_title_is_trimmed_to_256_characters():
    assert len(build_message(make_item(title="y" * 400))["embeds"][0]["title"]) == 256


def test_an_empty_description_is_omitted():
    assert "description" not in build_message(make_item(description=""))["embeds"][0]


def test_the_plain_style_posts_the_bare_url():
    payload = build_message(make_item("abc123"), style="plain")
    assert payload == {
        "content": "https://www.youtube.com/watch?v=abc123",
        "allowed_mentions": {"parse": []},
    }


def test_truncate_leaves_short_text_alone():
    assert truncate("short", 100) == "short"
    assert truncate(None, 10) == ""


# -- the per-video workflow -------------------------------------------------


class Publisher:
    """A publishing service and the doubles it was built from."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, **overrides):
        self.clock = FakeClock()
        self.table = FakeTable()
        self.detector = StubDetector()
        self.transport = RecordingTransport()
        self.repository = ProcessingRepository(self.table, clock=self.clock)
        self.slept = []
        self.service = YouTubePublishingService(
            repository=self.repository,
            classifier=self.detector,
            transport=self.transport,
            channel_id=CHANNEL,
            sleeper=self.slept.append,
            clock=self.clock,
            **overrides,
        )
        self.source = make_source()

    def publish(self, item):
        return self.service.publish(item, self.source)

    def record(self, video_id):
        return self.table.record(f"youtube#{video_id}")


def test_a_long_form_video_is_claimed_posted_and_recorded():
    publisher = Publisher()

    outcome = publisher.publish(make_item("fresh"))

    assert outcome.settled is True
    assert outcome.announced.video_id == "fresh"
    assert outcome.announced.discord_message_id == publisher.transport.message_ids[0]
    assert outcome.failure is None
    assert publisher.record("fresh")["status"] == STATUS_POSTED


def test_the_configured_style_is_honoured():
    publisher = Publisher(message_style="plain")
    publisher.publish(make_item("abc123"))
    payload = publisher.transport.sent[0]["payload"]
    assert payload["content"] == "https://www.youtube.com/watch?v=abc123"


def test_the_post_delay_applies_between_consecutive_posts_only():
    publisher = Publisher(post_delay_seconds=3)
    publisher.publish(make_item("one"))
    publisher.publish(make_item("two"))
    assert publisher.slept == [3]


def test_no_delay_is_taken_when_none_is_configured():
    publisher = Publisher()
    publisher.publish(make_item("one"))
    publisher.publish(make_item("two"))
    assert publisher.slept == []


def test_a_short_is_recorded_and_never_posted():
    publisher = Publisher()
    publisher.detector.verdicts["tiny"] = ShortsVerdict(True, "stub", SKIP_REASON_SHORT)

    outcome = publisher.publish(make_item("tiny"))

    assert (outcome.settled, outcome.skipped_short) == (True, True)
    assert publisher.transport.sent == []
    assert publisher.record("tiny")["skip_reason"] == SKIP_REASON_SHORT
    assert publisher.record("tiny")["status"] == STATUS_SKIPPED


def test_a_second_publish_of_the_same_video_is_a_duplicate():
    publisher = Publisher()
    publisher.publish(make_item("fresh"))

    outcome = publisher.publish(make_item("fresh"))

    assert (outcome.duplicate, outcome.settled) == (True, True)
    assert len(publisher.transport.sent) == 1


def test_an_undecidable_video_releases_its_claim_so_it_is_retried():
    publisher = Publisher()
    publisher.detector.verdicts["fresh"] = ClassificationError("probe timed out")

    outcome = publisher.publish(make_item("fresh"))

    assert outcome.settled is False
    assert outcome.failure.stage == "classification"
    assert publisher.record("fresh") is None


def test_a_confirmed_rejection_releases_the_claim():
    publisher = Publisher()
    publisher.transport.fail_for("fresh", DistributionError("403 from Discord"))

    outcome = publisher.publish(make_item("fresh"))

    assert outcome.failure.stage == "distribution"
    assert publisher.record("fresh") is None


def test_an_ambiguous_delivery_keeps_the_claim_in_posting():
    publisher = Publisher()
    publisher.transport.fail_for("maybe", AmbiguousDeliveryError("connection dropped"))

    outcome = publisher.publish(make_item("maybe"))

    assert outcome.failure.stage == "distribution_ambiguous"
    assert publisher.record("maybe")["status"] == STATUS_POSTING
    assert "discord_message_id" not in publisher.record("maybe")


def test_a_failed_record_after_a_successful_post_still_reports_the_announcement():
    publisher = Publisher()

    def fail(content_id, receipt):
        raise RepositoryError("throttled")

    publisher.repository.mark_distributed = fail
    outcome = publisher.publish(make_item("fresh"))

    # The post happened, so it is announced; the claim is kept to be alerted on.
    assert outcome.announced is not None
    assert outcome.failure.stage == "state_update"
    assert outcome.settled is False
    assert publisher.record("fresh")["status"] == STATUS_POSTING


def test_a_throttled_claim_is_a_deduplication_failure():
    publisher = Publisher()
    publisher.table.break_once("put_item", throttling_error())

    outcome = publisher.publish(make_item("fresh"))

    assert outcome.failure.stage == "deduplication"
    assert outcome.settled is False


def test_a_failure_names_the_source_and_the_video():
    publisher = Publisher()
    publisher.detector.verdicts["fresh"] = ClassificationError("probe timed out")

    failure = publisher.publish(make_item("fresh")).failure

    assert failure.source_name == "Damien Burks"
    assert failure.source_key == "handle:@damienjburks"
    assert failure.video_id == "fresh"
