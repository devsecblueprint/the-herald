"""The message that lands in Discord, and the per-video workflow."""

from app.errors import (
    AmbiguousDeliveryError,
    ClassificationError,
    DistributionError,
    RepositoryError,
)
from app.models.youtube import (
    SKIP_REASON_SHORT,
    STATUS_POSTED,
    STATUS_POSTING,
    STATUS_SKIPPED,
    ShortsVerdict,
)
from app.repositories.youtube.processing import ProcessingRepository
from app.services.youtube.publishing import (
    YouTubePublishingService,
    build_message,
)
from tests.youtube.fakes import (
    FakeClock,
    FakeTable,
    RecordingTransport,
    StubDetector,
    make_item,
    make_source,
    throttling_error,
)

CHANNEL = "123456789012345678"


# -- the message ------------------------------------------------------------


def test_the_message_names_the_partner_and_links_the_video():
    payload = build_message(make_item("abc123"))
    assert payload["content"] == (
        "New video from **Damien Burks**.\n"
        "Check it out on YouTube: https://www.youtube.com/watch?v=abc123"
    )


def test_no_custom_embed_is_attached_so_youtubes_card_renders():
    # The bare URL lets Discord render YouTube's own player card; we must not
    # attach a competing embed of our own.
    payload = build_message(make_item("abc123"))
    assert "embeds" not in payload


def test_announcements_never_ping_a_channel():
    assert build_message(make_item())["allowed_mentions"] == {"parse": []}


def test_a_notify_role_is_mentioned_on_its_own_line():
    payload = build_message(make_item("abc123"), notify_role_id="42")
    assert payload["content"] == (
        "<@&42>\n"
        "New video from **Damien Burks**.\n"
        "Check it out on YouTube: https://www.youtube.com/watch?v=abc123"
    )


def test_a_notify_role_is_allow_listed_so_the_ping_fires():
    payload = build_message(make_item(), notify_role_id="42")
    assert payload["allowed_mentions"] == {"parse": [], "roles": ["42"]}


def test_no_notify_role_means_no_role_mention_and_no_ping():
    payload = build_message(make_item(), notify_role_id="")
    assert "<@&" not in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}


def test_the_style_argument_no_longer_changes_the_output():
    # Kept for signature compatibility; both values produce the same message.
    assert build_message(make_item("abc123"), style="plain") == build_message(
        make_item("abc123"), style="embed"
    )


def test_the_service_pings_the_notify_role_on_delivery():
    transport = RecordingTransport()
    service = YouTubePublishingService(
        repository=ProcessingRepository(FakeTable()),
        classifier=StubDetector(),
        transport=transport,
        channel_id=CHANNEL,
        notify_role_id="42",
    )
    service.publish(make_item("abc123"), make_source())

    payload = transport.sent[0]["payload"]
    assert payload["content"].startswith("<@&42>\n")
    assert payload["allowed_mentions"] == {"parse": [], "roles": ["42"]}


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


def test_the_published_message_is_the_plain_announcement_with_the_url():
    publisher = Publisher(message_style="plain")
    publisher.publish(make_item("abc123"))
    payload = publisher.transport.sent[0]["payload"]
    assert payload["content"] == (
        "New video from **Damien Burks**.\n"
        "Check it out on YouTube: https://www.youtube.com/watch?v=abc123"
    )
    assert "embeds" not in payload


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
