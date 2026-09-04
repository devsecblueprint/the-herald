"""
The YouTube feature's HTTP surface.

``POST /trigger/youtube``  -> 200 ok | 207 completed_with_errors | 409 already_running
``GET  /health/youtube``   -> configuration, configured sources, last run summary

``YouTubeTriggerController`` holds the status-code mapping and knows no web
framework; ``create_fastapi_router`` is the thin adapter that mounts it.
Another framework needs another adapter here, not a change to the service.
"""

from typing import Any, Dict, Tuple

from fastapi import APIRouter
from fastapi.responses import JSONResponse

STATUS_CODES = {
    "ok": 200,
    "disabled": 200,
    "completed_with_errors": 207,
    "already_running": 409,
}


class YouTubeTriggerController:
    """Framework-agnostic handlers for the two endpoints."""

    def __init__(self, polling_service):
        self.polling = polling_service

    def trigger(self) -> Tuple[int, Dict[str, Any]]:
        """Run a poll now and return an HTTP status and body."""
        result = self.polling.run()
        return STATUS_CODES.get(result.status, 200), result.to_dict()

    def health(self) -> Tuple[int, Dict[str, Any]]:
        """Return the feature's configuration and last run summary."""
        return 200, self.polling.health()


def create_fastapi_router(controller: YouTubeTriggerController, prefix: str = "") -> APIRouter:
    """Build a FastAPI router exposing the trigger and health endpoints."""
    router = APIRouter(prefix=prefix)

    @router.post("/trigger/youtube")
    async def trigger_youtube():
        """Run a YouTube poll now."""
        status_code, body = controller.trigger()
        return JSONResponse(status_code=status_code, content=body)

    @router.get("/health/youtube")
    async def health_youtube():
        """Report YouTube ingestion configuration and last run."""
        status_code, body = controller.health()
        return JSONResponse(status_code=status_code, content=body)

    return router
