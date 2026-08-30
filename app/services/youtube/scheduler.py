"""
Ways to run the poll on a schedule.

The APScheduler job registers with ``max_instances=1`` and
``coalesce=True`` so a slow poll cannot stack up behind itself.
Independently of the scheduler, ``YouTubePipeline.run()`` holds a
non-blocking lock, so the scheduled job and the manual trigger can never
run concurrently either.
"""

import time

from apscheduler.triggers.interval import IntervalTrigger

from app.services.youtube.logging_utils import EventLogger

JOB_ID = "youtube_job"
JOB_NAME = "Announce partner YouTube uploads in Discord"


def register_youtube_job(scheduler, pipeline, job_id: str = JOB_ID):
    """Register the poll with an APScheduler instance and return the job."""
    return scheduler.add_job(
        pipeline.run,
        trigger=IntervalTrigger(minutes=pipeline.config.poll_interval_minutes),
        id=job_id,
        name=JOB_NAME,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def run_forever(pipeline, sleeper=time.sleep, iterations=None) -> None:
    """
    Run the poll in a plain worker loop.

    Args:
        pipeline: The pipeline to run.
        sleeper: Sleep function, injectable for testing.
        iterations: Stop after this many polls. None runs forever.
    """
    events = EventLogger(__name__)
    interval_seconds = pipeline.config.poll_interval_minutes * 60
    completed = 0
    while iterations is None or completed < iterations:
        try:
            pipeline.run()
        except Exception as exc:  # pragma: no cover - last-resort guard
            events.error("youtube.poll.crashed", error=str(exc))
        completed += 1
        if iterations is not None and completed >= iterations:
            return
        sleeper(interval_seconds)


def make_lambda_handler(factory):
    """
    Build an AWS Lambda handler from a pipeline factory.

    The factory is called once per cold start and the pipeline is reused
    across invocations, so the resolver's in-memory cache survives.
    """
    state = {}

    def handler(event=None, context=None):  # pylint: disable=unused-argument
        """Run one poll and return its summary as the Lambda response."""
        if "pipeline" not in state:
            state["pipeline"] = factory()
        return state["pipeline"].run().to_dict()

    return handler
