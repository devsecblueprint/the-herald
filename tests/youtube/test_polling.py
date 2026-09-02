"""
The lifecycle: onboarding, announcing, and the watermark that makes it safe.

There are four states in the whole feature -- a partner is added, their
starting point is established, their new videos are announced, they are
removed -- and these tests walk all of them, plus every failure that must
not lose or repeat an announcement.
"""

import threading
from datetime import datetime, timedelta, timezone

from app.errors import (AmbiguousDeliveryError, ChannelResolutionError,
                        ClassificationError, DistributionError, FeedFetchError,
                        RepositoryError)
from app.models.youtube import (SKIP_REASON_SHORT, STATUS_PENDING,
                                STATUS_POSTED, STATUS_POSTING, STATUS_SKIPPED,
                                ShortsVerdict)
from app.utils.clock import to_iso
from tests.youtube.fakes import throttling_error
from tests.youtube.harness import CHANNEL_ID, build_config, build_harness

DAMIEN = "handle:@damienjburks"
DSB = "handle:@thedsbcommunity"

TWO_SOURCES = [
    {"name": "Damien Burks", "relationship": "COMMUNITY_PARTNER", "channel": "@damienjburks"},
    {"name": "DSB", "relationship": "DSB", "channel": "@thedsbcommunity"},
]


BASE = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)


def at(day, hour=12):
    """A timestamp in "day of the run" terms; day 32 simply rolls over."""
    return BASE + timedelta(days=day - 1, hours=hour)


def short(video_id):
    return ShortsVerdict(is_short=True, detector="stub", reason=SKIP_REASON_SHORT)


def announced_ids(result):
    return [item.video_id for item in result.announced]


# -- onboarding -------------------------------------------------------------


def test_a_new_partners_back_catalogue_is_never_posted():
    herald = build_harness(now=at(30))
    herald.publish(DAMIEN, [("old1", at(10)), ("old2", at(20)), ("old3", at(25))])

    result = herald.run()

    assert result.sources_onboarded == 1
    assert result.announcements_published == 0
    assert result.new_videos == 0
    assert herald.transport.sent == []
    assert herald.table.video_records() == {}
    assert herald.watermarks() == {DAMIEN: to_iso(at(30))}


def test_onboarding_an_empty_channel_still_records_a_starting_point():
    herald = build_harness(now=at(30))
    herald.publish(DAMIEN, [])
    herald.run()
    assert herald.watermarks() == {DAMIEN: to_iso(at(30))}


def test_a_host_clock_running_behind_youtube_cannot_let_back_catalogue_through():
    # The newest video is "in the future" as far as this host is concerned.
    herald = build_harness(now=at(30))
    herald.publish(DAMIEN, [("future", at(31))])

    herald.run()

    assert herald.watermarks() == {DAMIEN: to_iso(at(31))}
    assert herald.transport.sent == []


# -- the steady state -------------------------------------------------------


def onboarded(now=at(30), **kwargs):
    """A harness whose single partner has already been onboarded."""
    herald = build_harness(now=now, **kwargs)
    herald.publish(DAMIEN, [("old", at(20))])
    herald.run()
    return herald


def test_a_new_upload_is_announced_and_the_watermark_moves():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("old", at(20)), ("fresh", at(31))])

    result = herald.run()

    assert announced_ids(result) == ["fresh"]
    assert result.announcements_published == 1
    assert result.new_videos == 1
    assert herald.watermarks() == {DAMIEN: to_iso(at(31))}


def test_the_announcement_records_the_discord_message():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])

    result = herald.run()

    announcement = result.announced[0]
    assert announcement.discord_channel_id == CHANNEL_ID
    assert announcement.discord_message_id == herald.transport.message_ids[0]

    record = herald.table.record("youtube#fresh")
    assert record["status"] == STATUS_POSTED
    assert record["discord_message_id"] == announcement.discord_message_id


def test_nothing_new_leaves_the_watermark_alone_but_refreshes_the_roster():
    herald = onboarded()
    before = herald.watermarks()
    herald.clock.advance(hours=1)

    result = herald.run()

    assert result.announcements_published == 0
    assert herald.watermarks() == before
    # The roster is rewritten every poll, which is what keeps a quiet
    # source's entry current.
    assert herald.revision() == 2


def test_there_is_no_announcement_cap():
    herald = onboarded()
    herald.clock.advance(days=2)
    herald.publish(DAMIEN, [(f"v{index}", at(31, index)) for index in range(12)])

    result = herald.run()

    assert result.announcements_published == 12
    assert len(herald.transport.sent) == 12


def test_announcements_go_out_newest_first():
    herald = onboarded()
    herald.clock.advance(days=2)
    herald.publish(DAMIEN, [("older", at(31, 9)), ("newest", at(31, 18)), ("middle", at(31, 12))])

    assert announced_ids(herald.run()) == ["newest", "middle", "older"]


def test_a_future_dated_premiere_is_announced_and_carries_the_watermark_with_it():
    herald = onboarded()
    herald.publish(DAMIEN, [("premiere", at(35))])

    result = herald.run()

    assert announced_ids(result) == ["premiere"]
    assert herald.watermarks() == {DAMIEN: to_iso(at(35))}


def test_two_videos_sharing_a_publish_timestamp_are_both_announced():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("twin_a", at(31)), ("twin_b", at(31))])

    result = herald.run()

    assert sorted(announced_ids(result)) == ["twin_a", "twin_b"]
    assert herald.watermarks() == {DAMIEN: to_iso(at(31))}


def test_a_video_the_watermark_has_passed_can_never_become_eligible_again():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])
    herald.run()

    # Even with the per-video record gone, the watermark alone is enough.
    herald.table.items.pop("youtube#fresh")
    result = herald.run()

    assert result.announcements_published == 0


# -- deduplication ----------------------------------------------------------


def test_a_video_already_in_the_table_is_not_announced_twice():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])
    herald.run()

    # A poll that somehow re-examines the same batch (the roster write lost,
    # say) is stopped by the video's own record.
    herald.table.items["youtube-sources"]["watermarks"][DAMIEN] = to_iso(at(30))
    result = herald.run()

    assert result.announcements_published == 0
    assert result.duplicates_skipped == 1
    assert len(herald.transport.sent) == 1


def test_two_pollers_racing_the_same_video_announce_it_once():
    # Two processes sharing one table, both of which read the roster before
    # either wrote it, so both see the same video as new.
    shared_table = build_harness().table
    first = build_harness(table=shared_table, now=at(30))
    second = build_harness(table=shared_table, now=at(30))
    for herald in (first, second):
        herald.publish(DAMIEN, [("old", at(20))])
    first.run()

    stale_watermark = shared_table.items["youtube-sources"]["watermarks"][DAMIEN]
    for herald in (first, second):
        herald.clock.advance(days=1)
        herald.publish(DAMIEN, [("fresh", at(31))])

    first_result = first.run()
    shared_table.items["youtube-sources"]["watermarks"][DAMIEN] = stale_watermark
    second_result = second.run()

    assert first_result.announcements_published == 1
    assert second_result.announcements_published == 0
    assert second_result.duplicates_skipped == 1
    assert len(shared_table.record("youtube#fresh")["discord_message_id"]) > 0
    assert len(first.transport.sent) == 1
    assert second.transport.sent == []


# -- Shorts -----------------------------------------------------------------


def test_a_short_is_recorded_and_not_announced():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("tiny", at(31))])
    herald.detector.verdicts["tiny"] = short("tiny")

    result = herald.run()

    assert result.shorts_skipped == 1
    assert result.announcements_published == 0
    assert herald.transport.sent == []

    record = herald.table.record("youtube#tiny")
    assert record["status"] == STATUS_SKIPPED
    assert record["skip_reason"] == SKIP_REASON_SHORT


def test_a_skipped_short_still_lets_the_watermark_advance():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("tiny", at(31))])
    herald.detector.verdicts["tiny"] = short("tiny")

    herald.run()

    assert herald.watermarks() == {DAMIEN: to_iso(at(31))}


def test_each_video_is_classified_exactly_once():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("tiny", at(31))])
    herald.detector.verdicts["tiny"] = short("tiny")

    herald.run()
    herald.run()

    assert herald.detector.seen == ["tiny"]


def test_classification_runs_after_the_claim():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])
    herald.detector.verdicts["fresh"] = ClassificationError("probe timed out")

    result = herald.run()

    # The claim is released so the video is retried, never guessed at.
    assert herald.table.record("youtube#fresh") is None
    assert [failure.stage for failure in result.failures] == ["classification"]
    assert result.announcements_published == 0


def test_an_undecidable_video_holds_the_watermark_and_is_retried():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])
    herald.detector.verdicts["fresh"] = ClassificationError("probe timed out")
    herald.run()
    assert herald.watermarks() == {DAMIEN: to_iso(at(30))}

    del herald.detector.verdicts["fresh"]
    result = herald.run()

    assert announced_ids(result) == ["fresh"]
    assert herald.watermarks() == {DAMIEN: to_iso(at(31))}


# -- distribution failures --------------------------------------------------


def test_a_rejected_post_holds_the_whole_batch_back():
    herald = onboarded()
    herald.clock.advance(days=2)
    herald.publish(DAMIEN, [("good", at(31)), ("bad", at(30, 18))])
    herald.transport.fail_for("bad", DistributionError("500 from Discord"))

    result = herald.run()

    assert announced_ids(result) == ["good"]
    assert herald.table.record("youtube#bad") is None
    assert herald.watermarks() == {DAMIEN: to_iso(at(30))}
    assert [failure.stage for failure in result.failures] == ["distribution"]


def test_the_next_poll_retries_only_the_video_that_failed():
    herald = onboarded()
    herald.clock.advance(days=2)
    herald.publish(DAMIEN, [("good", at(31)), ("bad", at(30, 18))])
    herald.transport.fail_for("bad", DistributionError("500 from Discord"))
    herald.run()

    herald.transport.errors.clear()
    result = herald.run()

    # "good" is stopped by its own record; only "bad" is announced.
    assert announced_ids(result) == ["bad"]
    assert result.duplicates_skipped == 1
    assert herald.watermarks() == {DAMIEN: to_iso(at(31))}


def test_an_ambiguous_delivery_keeps_the_claim_so_it_is_never_repeated():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("maybe", at(31))])
    herald.transport.fail_for("maybe", AmbiguousDeliveryError("connection dropped"))

    result = herald.run()

    record = herald.table.record("youtube#maybe")
    assert record["status"] == STATUS_POSTING
    assert "discord_message_id" not in record
    assert [failure.stage for failure in result.failures] == ["distribution_ambiguous"]

    herald.transport.errors.clear()
    second = herald.run()
    assert second.announcements_published == 0
    assert herald.watermarks() == {DAMIEN: to_iso(at(31))}


def test_a_failed_state_write_after_a_successful_post_keeps_the_claim():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])
    herald.table.break_once("update_item", throttling_error("UpdateItem"))

    result = herald.run()

    # mark_posting is the update that fails first, so nothing was sent.
    assert result.announcements_published == 0
    assert [failure.stage for failure in result.failures] == ["state_update"]
    assert herald.table.record("youtube#fresh")["status"] == STATUS_PENDING


def test_a_failed_mark_distributed_leaves_a_posting_record_to_alert_on():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])

    original = herald.repository.mark_distributed

    def fail_after_posting(content_id, receipt):
        raise RepositoryError("throttled")

    herald.repository.mark_distributed = fail_after_posting
    result = herald.run()
    herald.repository.mark_distributed = original

    assert result.announcements_published == 1
    assert [failure.stage for failure in result.failures] == ["state_update"]
    record = herald.table.record("youtube#fresh")
    assert record["status"] == STATUS_POSTING
    assert "discord_message_id" not in record


def test_a_throttled_claim_holds_the_watermark():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])
    herald.table.break_once("put_item", throttling_error())

    result = herald.run()

    assert [failure.stage for failure in result.failures] == ["deduplication"]
    assert herald.watermarks() == {DAMIEN: to_iso(at(30))}


def test_a_stranded_claim_from_another_poll_holds_the_watermark():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])
    # Another poll claimed it a minute ago and has not finished.
    herald.table.items["youtube#fresh"] = {
        "content_id": "youtube#fresh",
        "status": STATUS_PENDING,
        "first_seen_epoch": int(herald.clock().timestamp()),
    }

    result = herald.run()

    assert result.duplicates_skipped == 1
    assert herald.watermarks() == {DAMIEN: to_iso(at(30))}


def test_a_stale_claim_is_taken_over_on_a_later_poll():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])
    herald.table.items["youtube#fresh"] = {
        "content_id": "youtube#fresh",
        "status": STATUS_PENDING,
        "first_seen_epoch": int(herald.clock().timestamp()) - 3601,
    }

    result = herald.run()

    assert announced_ids(result) == ["fresh"]


# -- the roster -------------------------------------------------------------


def test_a_source_that_fails_to_fetch_keeps_its_place_on_the_roster():
    herald = build_harness(config=build_config(TWO_SOURCES), now=at(30))
    herald.publish(DAMIEN, [("a", at(20))])
    herald.publish(DSB, [("b", at(20))])
    herald.run()

    herald.fail(DAMIEN, FeedFetchError("'Damien Burks': feed returned HTTP 503"))
    result = herald.run()

    # A transient YouTube outage must not look like a removal.
    assert set(herald.watermarks()) == {DAMIEN, DSB}
    assert [failure.stage for failure in result.failures] == ["ingestion"]
    assert result.sources_checked == 1


def test_an_unresolvable_handle_is_one_sources_problem():
    herald = build_harness(config=build_config(TWO_SOURCES), now=at(30))
    herald.fail(DAMIEN, ChannelResolutionError("handle:@damienjburks: channel page not found"))
    herald.publish(DSB, [("b", at(20))])

    result = herald.run()

    assert result.sources_onboarded == 1
    assert set(herald.watermarks()) == {DSB}
    assert result.failures[0].source_name == "Damien Burks"


def test_a_failing_source_does_not_stop_the_others_announcing():
    herald = build_harness(config=build_config(TWO_SOURCES), now=at(30))
    herald.publish(DAMIEN, [("a", at(20))])
    herald.publish(DSB, [("b", at(20))])
    herald.run()

    herald.clock.advance(days=1)
    herald.fail(DAMIEN, FeedFetchError("boom"))
    herald.publish(DSB, [("b", at(20)), ("fresh", at(31))])

    result = herald.run()

    assert announced_ids(result) == ["fresh"]
    assert result.failed_sources == 1


def test_removing_a_partner_removes_their_state():
    herald = build_harness(config=build_config(TWO_SOURCES), now=at(30))
    herald.publish(DAMIEN, [("a", at(20))])
    herald.publish(DSB, [("b", at(20))])
    herald.run()
    assert set(herald.watermarks()) == {DAMIEN, DSB}

    # A partner who is no longer in the config is simply not carried over.
    trimmed = build_harness(
        config=build_config([TWO_SOURCES[1]]), table=herald.table, clock=herald.clock
    )
    trimmed.publish(DSB, [("b", at(20))])
    trimmed.run()

    assert set(trimmed.watermarks()) == {DSB}


def test_adding_a_partner_back_is_a_fresh_onboarding():
    herald = build_harness(config=build_config(TWO_SOURCES), now=at(30))
    herald.publish(DAMIEN, [("a", at(20))])
    herald.publish(DSB, [("b", at(20))])
    herald.run()

    trimmed = build_harness(
        config=build_config([TWO_SOURCES[1]]), table=herald.table, clock=herald.clock
    )
    trimmed.publish(DSB, [("b", at(20))])
    trimmed.run()

    # While they were off the list they published something.
    trimmed.clock.advance(days=2)
    restored = build_harness(config=build_config(TWO_SOURCES), table=herald.table, clock=herald.clock)
    restored.publish(DAMIEN, [("a", at(20)), ("missed", at(31))])
    restored.publish(DSB, [("b", at(20))])

    result = restored.run()

    assert result.sources_onboarded == 1
    assert result.announcements_published == 0
    assert restored.watermarks()[DAMIEN] == to_iso(at(32))


def test_rewriting_a_channel_line_reads_as_a_removal_plus_an_addition():
    herald = build_harness(now=at(30))
    herald.publish(DAMIEN, [("a", at(20))])
    herald.run()

    rewritten = build_harness(
        config=build_config(
            [
                {
                    "name": "Damien Burks",
                    "relationship": "COMMUNITY_PARTNER",
                    "channel": "UCAAAAAAAAAAAAAAAAAAAAAA",
                }
            ]
        ),
        table=herald.table,
        clock=herald.clock,
    )
    rewritten.clock.advance(days=1)
    rewritten.publish("id:UCAAAAAAAAAAAAAAAAAAAAAA", [("a", at(20)), ("missed", at(30, 18))])

    result = rewritten.run()

    assert result.sources_onboarded == 1
    assert result.announcements_published == 0
    assert set(rewritten.watermarks()) == {"id:UCAAAAAAAAAAAAAAAAAAAAAA"}


def test_namespaces_that_share_a_name_keep_separate_state():
    sources = [
        {"name": "Legacy User", "relationship": "MEMBER", "channel": "https://youtube.com/user/foo"},
        {"name": "Vanity", "relationship": "MEMBER", "channel": "https://youtube.com/c/foo"},
    ]
    herald = build_harness(config=build_config(sources), now=at(30))
    herald.publish("user:foo", [], channel_id="UCAAAAAAAAAAAAAAAAAAAAAA")
    herald.publish("vanity:foo", [], channel_id="UCBBBBBBBBBBBBBBBBBBBBBB")

    herald.run()

    assert set(herald.watermarks()) == {"user:foo", "vanity:foo"}


def test_an_unparseable_roster_entry_re_onboards_only_that_source():
    herald = build_harness(config=build_config(TWO_SOURCES), now=at(30))
    herald.publish(DAMIEN, [("a", at(20))])
    herald.publish(DSB, [("b", at(20))])
    herald.run()

    herald.table.items["youtube-sources"]["watermarks"][DAMIEN] = "not-a-timestamp"
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("a", at(20)), ("missed", at(30, 18))])
    herald.publish(DSB, [("b", at(20)), ("fresh", at(30, 18))])

    result = herald.run()

    assert result.sources_onboarded == 1
    assert announced_ids(result) == ["fresh"]


def test_an_unreadable_roster_stops_the_poll_before_anything_is_posted():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])
    herald.table.break_always("get_item", throttling_error("GetItem"))

    result = herald.run()

    assert result.status == "completed_with_errors"
    assert [failure.stage for failure in result.failures] == ["roster"]
    assert herald.transport.sent == []


def test_a_roster_write_conflict_does_not_repeat_the_announcements():
    herald = onboarded()
    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("fresh", at(31))])

    # Another poll bumped the revision while this one was working.
    original_save = herald.roster.save

    def conflicting(watermarks, expected_revision):
        herald.table.items["youtube-sources"]["revision"] = expected_revision + 5
        return original_save(watermarks, expected_revision)

    herald.roster.save = conflicting
    result = herald.run()
    herald.roster.save = original_save

    assert result.announcements_published == 1
    assert [failure.stage for failure in result.failures] == ["roster"]

    # The next poll reconciles, and the announcement is not repeated.
    second = herald.run()
    assert second.announcements_published == 0
    assert second.duplicates_skipped == 1


# -- running ----------------------------------------------------------------


def test_a_poll_already_in_flight_is_skipped_not_queued():
    herald = onboarded()
    started = threading.Event()
    release = threading.Event()

    def blocking_fetch(source):
        started.set()
        release.wait(timeout=5)
        raise FeedFetchError("done blocking")

    herald.ingestion.fetch = blocking_fetch
    worker = threading.Thread(target=herald.polling.run)
    worker.start()
    started.wait(timeout=5)

    skipped = herald.polling.run()
    release.set()
    worker.join(timeout=5)

    assert skipped.skipped is True
    assert skipped.status == "already_running"


def test_a_disabled_feature_does_nothing():
    herald = build_harness(config=build_config(enabled=False), now=at(30))
    herald.publish(DAMIEN, [("fresh", at(31))])

    result = herald.run()

    assert result.status == "disabled"
    assert herald.transport.sent == []
    assert herald.table.items == {}


def test_the_poll_summary_reports_every_counter():
    herald = build_harness(config=build_config(TWO_SOURCES), now=at(30))
    herald.publish(DAMIEN, [("a", at(20))])
    herald.publish(DSB, [("b", at(20))])
    herald.run()

    herald.clock.advance(days=1)
    herald.publish(DAMIEN, [("a", at(20)), ("fresh", at(31)), ("tiny", at(30, 18))])
    herald.publish(DSB, [("b", at(20))])
    herald.detector.verdicts["tiny"] = short("tiny")

    summary = herald.run().to_dict()

    assert summary["status"] == "ok"
    assert summary["sources_configured"] == 2
    assert summary["sources_checked"] == 2
    assert summary["sources_onboarded"] == 0
    assert summary["videos_in_feeds"] == 4
    assert summary["new_videos"] == 2
    assert summary["shorts_skipped"] == 1
    assert summary["announcements_published"] == 1
    assert summary["duplicates_skipped"] == 0
    assert summary["ttl_days"] == 35
    assert summary["discord_channel_id"] == CHANNEL_ID
    assert summary["failures"] == []
    assert summary["announced"][0]["video_id"] == "fresh"


def test_health_reports_the_configuration_and_the_last_run():
    herald = onboarded()
    health = herald.polling.health()

    assert health["enabled"] is True
    assert health["poll_interval_minutes"] == 15
    assert health["discord_channel_name"] == "content-corner"
    assert health["sources_configured"] == 1
    assert health["sources"][0]["source_key"] == DAMIEN
    assert health["last_run"]["sources_onboarded"] == 1
