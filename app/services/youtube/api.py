"""
Manual trigger and health endpoints.

``POST /trigger/youtube``  -> 200 ok | 207 completed_with_errors | 409 already_running
``GET  /health/youtube``   -> configuration, configured sources, last run summary
"""

from typing import Any, Dict, Tuple

STATUS_CODES = {
    "ok": 200,
    "disabled": 200,
    "completed_with_errors": 207,
    "already_running": 409,
}


class YouTubeTriggerController:
    """Framework-agnostic handlers for the two endpoints."""

    def __init__(self, pipeline):
        self.pipeline = pipeline

    def trigger(self) -> Tuple[int, Dict[str, Any]]:
        """Run a poll now and return an HTTP status and body."""
        result = self.pipeline.run()
        body = result.to_dict()
        return STATUS_CODES.get(result.status, 200), body

    def health(self) -> Tuple[int, Dict[str, Any]]:
        """Return the feature's configuration and last run summary."""
        return 200, self.pipeline.health()


def create_fastapi_router(controller: YouTubeTriggerController, prefix: str = ""):
    """Build a FastAPI router exposing the trigger and health endpoints."""
    # Imported here so the feature does not depend on a web framework being
    # installed unless these helpers are actually used.
    # pylint: disable=import-outside-toplevel
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

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


def create_flask_blueprint(controller: YouTubeTriggerController, name: str = "youtube"):
    """Build a Flask blueprint exposing the trigger and health endpoints."""
    # pylint: disable=import-outside-toplevel
    from flask import Blueprint, jsonify

    blueprint = Blueprint(name, __name__)

    @blueprint.post("/trigger/youtube")
    def trigger_youtube():
        """Run a YouTube poll now."""
        status_code, body = controller.trigger()
        return jsonify(body), status_code

    @blueprint.get("/health/youtube")
    def health_youtube():
        """Report YouTube ingestion configuration and last run."""
        status_code, body = controller.health()
        return jsonify(body), status_code

    return blueprint
