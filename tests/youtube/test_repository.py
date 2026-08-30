"""The DynamoDB records: claims, the state machine, and the roster."""

from datetime import datetime, timezone

import pytest

from app.services.youtube.clock import to_iso
from app.services.youtube.errors import RepositoryError, RosterError, RosterWriteConflict
from app.services.youtube.models import (
    SKIP_REASON_SHORT,
    STATUS_PENDING,
    STATUS_POSTED,
    STATUS_POSTING,
    STATUS_SKIPPED,
    DeliveryReceipt,
)
from app.services.youtube.repository import (
    ChannelReferenceCache,
    ProcessingRepository,
    RosterRepository,
)
from tests.youtube.fakes import FakeClock, FakeTable, make_item, throttling_error

CHANNEL = "123456789012345678"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def table():
    return FakeTable()


@pytest.fixture
def repo(table, clock):
    return ProcessingRepository(table, clock=clock)


def receipt(clock, message_id="987654321098765432"):
    return DeliveryReceipt(channel_id=CHANNEL, message_id=message_id, posted_at=clock())


def advance_to_posting(repo, item):
    repo.claim(item, CHANNEL)
    repo.mark_posting(item.dedupe_key)


# -- claiming ---------------------------------------------------------------


def test_a_claim_writes_a_pending_record_with_the_audit_fields(repo, table, clock):
    item = make_item("abc123")
    assert repo.claim(item, CHANNEL).claimed is True

    record = table.record("youtube#abc123")
    assert record["status"] == STATUS_PENDING
    assert record["platform"] == "youtube"
    assert record["video_id"] == "abc123"
    assert record["source_name"] == "Damien Burks"
    assert record["relationship"] == "COMMUNITY_PARTNER"
    assert record["url"] == "https://www.youtube.com/watch?v=abc123"
    assert record["discord_channel_id"] == CHANNEL
    assert record["published_at"] == to_iso(item.published_at)
    assert record["first_seen_at"] == to_iso(clock())
    assert int(record["ttl"]) == repo.ttl_at(clock())


def test_the_second_claim_in_an_overlapping_poll_loses(repo):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)

    second = repo.claim(item, CHANNEL)
    assert second.claimed is False
    assert second.status == STATUS_PENDING
    # Another poll owns it and may still be working: not settled.
    assert second.is_settled is False


@pytest.mark.parametrize(
    "status, settled", [(STATUS_POSTED, True), (STATUS_SKIPPED, True), (STATUS_POSTING, True)]
)
def test_a_finished_record_blocks_the_claim_and_counts_as_settled(repo, table, status, settled):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)
    table.items["youtube#abc123"]["status"] = status

    result = repo.claim(item, CHANNEL)
    assert result.claimed is False
    assert result.is_settled is settled


def test_a_stale_pending_claim_is_reclaimable(repo, clock):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)

    clock.advance(minutes=61)
    assert repo.claim(item, CHANNEL).claimed is True


def test_a_fresh_pending_claim_is_not_reclaimable(repo, clock):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)

    clock.advance(minutes=59)
    assert repo.claim(item, CHANNEL).claimed is False


def test_a_posting_record_is_never_reclaimed_however_old(repo, clock):
    # This is what makes a crash mid-post safe: the video can never be
    # announced twice.
    item = make_item("abc123")
    advance_to_posting(repo, item)

    clock.advance(days=10)
    assert repo.claim(item, CHANNEL).claimed is False


def test_a_throttled_claim_is_a_repository_error(table, repo):
    table.break_once("put_item", throttling_error())
    with pytest.raises(RepositoryError, match="claim failed"):
        repo.claim(make_item(), CHANNEL)


def test_different_videos_do_not_collide(repo, table):
    repo.claim(make_item("aaa"), CHANNEL)
    repo.claim(make_item("bbb"), CHANNEL)
    assert set(table.video_records()) == {"youtube#aaa", "youtube#bbb"}


# -- the state machine ------------------------------------------------------


def test_mark_posting_advances_a_pending_claim(repo, table):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)
    repo.mark_posting(item.dedupe_key)
    assert table.record("youtube#abc123")["status"] == STATUS_POSTING


def test_mark_posting_refuses_a_record_that_is_not_ours(repo, table):
    item = make_item("abc123")
    advance_to_posting(repo, item)
    with pytest.raises(RepositoryError, match="not in the expected state"):
        repo.mark_posting(item.dedupe_key)


def test_mark_distributed_records_the_discord_message(repo, table, clock):
    item = make_item("abc123")
    advance_to_posting(repo, item)
    repo.mark_distributed(item.dedupe_key, receipt(clock))

    record = table.record("youtube#abc123")
    assert record["status"] == STATUS_POSTED
    assert record["discord_channel_id"] == CHANNEL
    assert record["discord_message_id"] == "987654321098765432"
    assert record["posted_at"] == to_iso(clock())
    assert int(record["ttl"]) == repo.ttl_at(clock())


def test_mark_distributed_refuses_a_record_that_never_reached_posting(repo, clock):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)
    with pytest.raises(RepositoryError, match="not in the expected state"):
        repo.mark_distributed(item.dedupe_key, receipt(clock))


def test_mark_skipped_records_the_reason(repo, table, clock):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)
    repo.mark_skipped(item.dedupe_key, SKIP_REASON_SHORT)

    record = table.record("youtube#abc123")
    assert record["status"] == STATUS_SKIPPED
    assert record["skip_reason"] == SKIP_REASON_SHORT
    assert int(record["ttl"]) == repo.ttl_at(clock())


def test_a_throttled_state_write_is_a_repository_error(repo, table, clock):
    item = make_item("abc123")
    advance_to_posting(repo, item)
    table.break_once("update_item", throttling_error("UpdateItem"))
    with pytest.raises(RepositoryError, match="mark_distributed failed"):
        repo.mark_distributed(item.dedupe_key, receipt(clock))


# -- releasing --------------------------------------------------------------


def test_release_deletes_a_pending_claim(repo, table):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)
    repo.release(item.dedupe_key)
    assert table.record("youtube#abc123") is None


def test_release_deletes_a_posting_claim_after_a_confirmed_rejection(repo, table):
    item = make_item("abc123")
    advance_to_posting(repo, item)
    repo.release(item.dedupe_key)
    assert table.record("youtube#abc123") is None


@pytest.mark.parametrize("status", [STATUS_POSTED, STATUS_SKIPPED])
def test_release_never_deletes_a_finished_record(repo, table, status):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)
    table.items["youtube#abc123"]["status"] = status

    repo.release(item.dedupe_key)
    assert table.record("youtube#abc123") is not None


def test_releasing_a_record_that_is_gone_is_harmless(repo):
    repo.release("youtube#never-existed")


def test_a_throttled_release_is_a_repository_error(repo, table):
    item = make_item("abc123")
    repo.claim(item, CHANNEL)
    table.break_once("delete_item", throttling_error("DeleteItem"))
    with pytest.raises(RepositoryError, match="release failed"):
        repo.release(item.dedupe_key)


# -- the roster -------------------------------------------------------------


@pytest.fixture
def roster(table, clock):
    return RosterRepository(table, clock=clock)


def at(day):
    return datetime(2026, 8, day, 12, 0, 0, tzinfo=timezone.utc)


def test_an_absent_roster_reads_as_empty(roster):
    loaded = roster.load()
    assert loaded.watermarks == {}
    assert loaded.revision == 0
    assert loaded.exists is False


def test_the_first_write_creates_revision_one(roster, table):
    assert roster.save({"handle:@a": at(19)}, expected_revision=0) == 1
    item = table.record("youtube-sources")
    assert item["record_type"] == "source_roster"
    assert item["watermarks"] == {"handle:@a": to_iso(at(19))}
    assert int(item["revision"]) == 1


def test_the_roster_never_expires(roster, table):
    roster.save({"handle:@a": at(19)}, expected_revision=0)
    # It is the only record of where each partner started, so a long polling
    # outage must not silently expire it and re-onboard everyone.
    assert "ttl" not in table.record("youtube-sources")


def test_a_roster_round_trips(roster):
    roster.save({"handle:@a": at(19), "handle:@b": at(18)}, expected_revision=0)
    loaded = roster.load()
    assert loaded.watermarks == {"handle:@a": at(19), "handle:@b": at(18)}
    assert loaded.revision == 1
    assert loaded.exists is True


def test_a_stale_writer_cannot_overwrite_a_newer_roster(roster):
    roster.save({"handle:@a": at(19)}, expected_revision=0)
    roster.save({"handle:@a": at(20)}, expected_revision=1)

    with pytest.raises(RosterWriteConflict, match="expected revision 1"):
        roster.save({"handle:@a": at(19)}, expected_revision=1)


def test_a_first_write_that_races_another_first_write_conflicts(roster):
    roster.save({"handle:@a": at(19)}, expected_revision=0)
    with pytest.raises(RosterWriteConflict):
        roster.save({"handle:@b": at(19)}, expected_revision=0)


def test_an_unreadable_entry_is_dropped_and_reported(roster, table):
    roster.save({"handle:@a": at(19)}, expected_revision=0)
    table.items["youtube-sources"]["watermarks"]["handle:@broken"] = "not-a-timestamp"

    loaded = roster.load()
    assert set(loaded.watermarks) == {"handle:@a"}
    assert loaded.unreadable == [("handle:@broken", "not-a-timestamp")]


def test_an_unreadable_roster_item_is_fatal(roster, table):
    table.break_always("get_item", throttling_error("GetItem"))
    with pytest.raises(RosterError, match="roster read failed"):
        roster.load()


def test_a_throttled_roster_write_is_reported(roster, table):
    table.break_once("put_item", throttling_error())
    with pytest.raises(RosterError, match="roster write failed"):
        roster.save({"handle:@a": at(19)}, expected_revision=0)


# -- the channel reference cache -------------------------------------------


def test_a_cached_reference_round_trips(table, clock):
    cache = ChannelReferenceCache(table, clock=clock)
    cache.put("handle:@a", "UCAAAAAAAAAAAAAAAAAAAAAA")

    item = table.record("youtube-channel#handle:@a")
    assert item["record_type"] == "channel_reference"
    assert item["reference"] == "handle:@a"
    assert cache.get("handle:@a") == "UCAAAAAAAAAAAAAAAAAAAAAA"


def test_an_expired_cache_entry_reads_as_a_miss(table, clock):
    cache = ChannelReferenceCache(table, clock=clock)
    cache.put("handle:@a", "UCAAAAAAAAAAAAAAAAAAAAAA")

    clock.advance(days=31)
    assert cache.get("handle:@a") is None


def test_a_cache_miss_is_not_an_error(table):
    assert ChannelReferenceCache(table).get("handle:@nobody") is None


def test_a_cache_write_failure_is_swallowed(table):
    table.break_once("put_item", throttling_error())
    ChannelReferenceCache(table).put("handle:@a", "UCAAAAAAAAAAAAAAAAAAAAAA")
