"""Scheduling the poll."""

from datetime import datetime, timezone

from apscheduler.triggers.interval import IntervalTrigger

from app.services.youtube.scheduler import (
    JOB_ID,
    make_lambda_handler,
    register_youtube_job,
    run_forever,
)
from tests.youtube.harness import build_config, build_harness


class RecordingScheduler:
    """Captures the arguments a job is registered with."""

    def __init__(self):
        self.jobs = []

    def add_job(self, func, **kwargs):
        self.jobs.append({"func": func, **kwargs})
        return kwargs


def test_the_job_cannot_stack_up_behind_itself():
    scheduler = RecordingScheduler()
    herald = build_harness()

    register_youtube_job(scheduler, herald.pipeline)

    job = scheduler.jobs[0]
    assert job["id"] == JOB_ID
    assert job["max_instances"] == 1
    assert job["coalesce"] is True
    assert job["replace_existing"] is True
    assert job["func"] == herald.pipeline.run


def test_the_interval_comes_from_the_configuration():
    scheduler = RecordingScheduler()
    herald = build_harness(config=build_config(poll_interval_minutes=45))

    register_youtube_job(scheduler, herald.pipeline)

    trigger = scheduler.jobs[0]["trigger"]
    assert isinstance(trigger, IntervalTrigger)
    assert trigger.interval.total_seconds() == 45 * 60


def test_the_worker_loop_polls_and_sleeps_for_the_interval():
    herald = build_harness()
    slept = []

    run_forever(herald.pipeline, sleeper=slept.append, iterations=3)

    assert herald.pipeline.last_result is not None
    # The last iteration returns rather than sleeping for nothing.
    assert slept == [15 * 60, 15 * 60]


def test_the_worker_loop_survives_a_crashing_poll():
    herald = build_harness()

    def explode():
        raise RuntimeError("unexpected")

    herald.pipeline.run = explode
    run_forever(herald.pipeline, sleeper=lambda _s: None, iterations=2)


def test_the_lambda_handler_returns_the_poll_summary():
    herald = build_harness()
    handler = make_lambda_handler(lambda: herald.pipeline)

    body = handler({}, None)

    assert body["status"] == "ok"
    assert body["sources_onboarded"] == 1


def test_the_lambda_handler_reuses_the_pipeline_across_invocations():
    built = []

    def factory():
        herald = build_harness()
        built.append(herald)
        return herald.pipeline

    handler = make_lambda_handler(factory)
    handler({}, None)
    handler({}, None)

    assert len(built) == 1
