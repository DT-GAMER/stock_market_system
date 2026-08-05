import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from ngx_research.config import settings
from ngx_research.jobs import full_market_research_sync
from ngx_research.services.public_errors import public_error_code, public_error_message

_scheduler_task: asyncio.Task | None = None
_run_lock = asyncio.Lock()
logger = logging.getLogger(__name__)
_state: dict[str, Any] = {
    "enabled": False,
    "is_running": False,
    "runs": 0,
    "last_started_at": None,
    "last_finished_at": None,
    "last_error": None,
    "last_result": None,
    "next_run_after_seconds": None,
    "current_step": None,
    "current_index": None,
    "current_total": None,
}


def start_automation_scheduler() -> None:
    global _scheduler_task
    if not settings.automation_enabled or not settings.ngxpulse_api_key:
        _state["enabled"] = False
        _state["last_error"] = (
            None if not settings.automation_enabled else "NGXPULSE_API_KEY is not configured"
        )
        return
    if _scheduler_task and not _scheduler_task.done():
        return
    _state["enabled"] = True
    _state["next_run_after_seconds"] = 5 if settings.automation_run_on_startup else _interval_seconds()
    _scheduler_task = asyncio.create_task(_automation_loop())


async def run_automation_once() -> dict[str, Any]:
    if _run_lock.locked():
        return {"status": "already_running", **automation_status()}
    async with _run_lock:
        _state["is_running"] = True
        _state["last_started_at"] = datetime.now(UTC).isoformat()
        _state["last_error"] = None
        _state["current_step"] = "starting"
        _state["current_index"] = None
        _state["current_total"] = None
        try:
            result = await full_market_research_sync(progress_callback=_set_progress)
            _state["runs"] += 1
            _state["last_result"] = result
            return {"status": "completed", "result": result}
        except Exception as exc:  # noqa: BLE001
            safe_message = public_error_message(exc, action="run automatic market intelligence")
            _state["last_error"] = safe_message
            logger.exception("Automation run failed")
            return {"status": "failed", "error": safe_message, "error_code": public_error_code(exc)}
        finally:
            _state["is_running"] = False
            _state["last_finished_at"] = datetime.now(UTC).isoformat()
            _state["current_step"] = None
            _state["current_index"] = None
            _state["current_total"] = None


def automation_status() -> dict[str, Any]:
    return {
        **_state,
        "interval_minutes": settings.automation_interval_minutes,
        "run_on_startup": settings.automation_run_on_startup,
        "dividend_sync_enabled": settings.automation_dividend_sync_enabled,
    }


def _set_progress(step: str, current: int | None = None, total: int | None = None) -> None:
    _state["current_step"] = step
    _state["current_index"] = current
    _state["current_total"] = total


async def _automation_loop() -> None:
    wait_seconds = 5 if settings.automation_run_on_startup else _interval_seconds()
    while True:
        _state["next_run_after_seconds"] = wait_seconds
        await asyncio.sleep(wait_seconds)
        await run_automation_once()
        wait_seconds = _interval_seconds()


def _interval_seconds() -> int:
    return max(60, settings.automation_interval_minutes * 60)
