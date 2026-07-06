"""Sanitized local API for generic bridge runs and event streaming."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ...bridges.agy_execution_router import AGYExecutionRouter
from ...bridges.generic.api_policy import GenericRunAuthorizationError, GenericRunFeatureFlags
from ...bridges.generic.coordinator import GenericBridgeRunCoordinator
from ...bridges.generic.errors import BridgeDomainError, BridgeErrorCode
from ...bridges.generic.event_transport import InternalBridgeEventTransport
from ...bridges.generic.models import BridgeEventType, BridgeRunEvent
from ...bridges.generic.states import TERMINAL_STATES, BridgeRunRecord, BridgeRunStatus
from ...bridges.generic.validation import format_datetime, utc_now, validate_identifier
from ...bridges.providers.agy_generic import AGYBridgeProvider
from ..dependencies import required_state, resolve_project_from_request
from ..errors import APIError, error_responses
from ..schemas.common import ErrorResponse
from ..schemas.generic_runs import (
    GenericProviderCapabilities,
    GenericAgentQaFixtureResponse,
    GenericProviderListResponse,
    GenericProviderResponse,
    GenericRunCancelResponse,
    GenericRunDetailResponse,
    GenericRunListResponse,
    GenericRunStartRequest,
    GenericRunStartResponse,
)


router = APIRouter(prefix="/models/bridges", tags=["generic-agent-runs"])
MAX_ACTIVE_GLOBAL = 3
MAX_ACTIVE_PER_PROJECT = 1
MAX_REPLAY_EVENTS = 200
HEARTBEAT_SECONDS = 15.0
QA_FIXTURE_STATES = frozenset(
    {
        "disabled",
        "ready",
        "submitting",
        "queued",
        "validating",
        "preparing_sandbox",
        "running",
        "collecting_artifacts",
        "cancelling",
        "completed",
        "blocked",
        "failed",
        "cancelled",
        "timed_out",
        "interrupted",
        "resync_required",
        "backend_unavailable",
    }
)


@router.post(
    "/runs",
    response_model=GenericRunStartResponse,
    responses={**error_responses(403, 404, 409, 413, 422, 500, 503), "default": {"model": ErrorResponse}},
)
async def start_generic_run(body: GenericRunStartRequest, request: Request) -> GenericRunStartResponse:
    _require_local_request(request)
    lock = required_state(request, "generic_run_submit_lock", "generic run submission lock")
    assert isinstance(lock, asyncio.Lock)
    async with lock:
        return await _start_generic_run_locked(body, request)


async def _start_generic_run_locked(body: GenericRunStartRequest, request: Request) -> GenericRunStartResponse:
    coordinator = _coordinator(request)
    existing = _idempotent_record(coordinator, body)
    if existing is not None:
        return _start_response(existing, reused=True)
    try:
        _policy(request).require_execution(body.provider_id)
    except GenericRunAuthorizationError as exc:
        raise APIError(403, exc.code, str(exc)) from exc

    metadata = await resolve_project_from_request(request, body.project_id)
    workspace_root = Path(str(getattr(metadata, "project_path"))).expanduser().resolve()
    _enforce_active_limits(coordinator, body.project_id)
    router_owner = _execution_router(request)
    try:
        started = await router_owner.start_run(
            workspace_root=workspace_root,
            prompt=body.instruction,
            timeout_seconds=body.timeout_seconds,
            idempotency_key=body.idempotency_key,
            project_id=body.project_id,
        )
        record = await _wait_until_persisted(coordinator, started.run_id)
    except BridgeDomainError as exc:
        raise _domain_api_error(exc) from exc
    except Exception as exc:
        # Provider, command, and instruction details are intentionally discarded.
        raise APIError(500, "GENERIC_RUN_START_FAILED", "The generic run could not be started safely.") from exc
    return _start_response(record, reused=False)


@router.get(
    "/generic/runs",
    response_model=GenericRunListResponse,
    responses={**error_responses(422, 500, 503), "default": {"model": ErrorResponse}},
)
async def list_generic_runs(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    provider_id: Literal["agy"] | None = None,
    status: BridgeRunStatus | None = None,
    project_id: str | None = Query(default=None, min_length=3, max_length=128),
) -> GenericRunListResponse:
    coordinator = _coordinator(request)
    if project_id is not None:
        try:
            validate_identifier(project_id, "project_id")
        except BridgeDomainError as exc:
            raise APIError(422, "INVALID_PROJECT_ID", "Project ID is invalid.") from exc
    try:
        records = coordinator.list_recent_runs(limit=100, offset=0)
    except BridgeDomainError as exc:
        raise _domain_api_error(exc) from exc
    filtered = [
        item
        for item in records
        if item.execution_mode == "generic"
        and (provider_id is None or item.provider_id == provider_id)
        and (status is None or item.status is status)
        and (project_id is None or item.project_id == project_id)
    ]
    page = filtered[offset : offset + limit]
    return GenericRunListResponse(
        runs=[_public_run(request, item) for item in page],
        count=len(filtered),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/generic/runs/{run_id}",
    response_model=GenericRunDetailResponse,
    responses={**error_responses(404, 500, 503), "default": {"model": ErrorResponse}},
)
async def generic_run_detail(run_id: str, request: Request) -> GenericRunDetailResponse:
    return _public_run(request, _generic_record(request, run_id))


@router.post(
    "/generic/runs/{run_id}/cancel",
    response_model=GenericRunCancelResponse,
    responses={**error_responses(422, 500, 503), "default": {"model": ErrorResponse}},
)
async def cancel_generic_run(run_id: str, request: Request) -> GenericRunCancelResponse:
    try:
        validate_identifier(run_id, "run_id")
    except BridgeDomainError:
        return GenericRunCancelResponse(run_id="run-not-found", disposition="not_found")
    coordinator = _coordinator(request)
    try:
        record = coordinator.get_run(run_id)
    except BridgeDomainError:
        return GenericRunCancelResponse(run_id=run_id, disposition="not_found")
    if record.execution_mode != "generic":
        return GenericRunCancelResponse(run_id=run_id, disposition="rejected", status=record.status.value)
    result = await coordinator.cancel(run_id)
    try:
        updated = coordinator.get_run(run_id)
    except BridgeDomainError:
        updated = None
    return GenericRunCancelResponse(
        run_id=run_id,
        disposition=result.disposition.value,
        status=updated.status.value if updated else None,
        cancellation_requested=bool(updated and updated.cancellation_requested),
    )


@router.get(
    "/providers",
    response_model=GenericProviderListResponse,
    responses={**error_responses(500, 503), "default": {"model": ErrorResponse}},
)
async def list_generic_providers(request: Request) -> GenericProviderListResponse:
    provider = required_state(request, "agy_generic_provider", "AGY generic provider")
    assert isinstance(provider, AGYBridgeProvider)
    try:
        detected = await provider.detect()
    except Exception:
        detected = None
    installed = bool(detected and detected.installed)
    available = bool(detected and detected.available)
    auth = detected.authentication_status if detected else "error"
    # Phase 2.5.8.9: detection remains visible, but local CLI routing is paused
    # regardless of legacy feature flags. API-backed tool plans are the next
    # runtime direction.
    execution_enabled = False
    reason = "Local CLI providers are paused and require the ForgeX-owned tool runtime."
    return GenericProviderListResponse(
        providers=[
            GenericProviderResponse(
                provider_id="agy",
                display_name="Google Antigravity / AGY",
                installed=installed,
                available=available,
                authentication_status=auth,
                capabilities=GenericProviderCapabilities(
                    edit_files=True,
                    streaming_events=True,
                    cancellation=True,
                    timeout=True,
                    artifacts=True,
                    sandbox_required=True,
                ),
                execution_enabled=execution_enabled,
                disabled_reason=reason,
            )
        ]
    )


@router.get(
    "/generic/qa-fixtures/{fixture_state}",
    response_model=GenericAgentQaFixtureResponse,
    include_in_schema=False,
    responses={**error_responses(404), "default": {"model": ErrorResponse}},
)
async def generic_agent_qa_fixture(fixture_state: str) -> GenericAgentQaFixtureResponse:
    """Return inert, sanitized visual state only in the isolated QA runtime."""

    if os.getenv("FORGEX_QA_MODE", "").strip() != "1" or fixture_state not in QA_FIXTURE_STATES:
        raise APIError(404, "QA_FIXTURE_NOT_FOUND", "QA visual fixture was not found.")
    provider_ready = fixture_state not in {"disabled", "backend_unavailable"}
    provider = GenericProviderResponse(
        provider_id="agy",
        display_name="Google Antigravity / AGY",
        installed=provider_ready,
        available=provider_ready,
        authentication_status="authenticated" if provider_ready else "not_installed",
        capabilities=GenericProviderCapabilities(
            edit_files=True,
            streaming_events=True,
            cancellation=True,
            timeout=True,
            artifacts=True,
            sandbox_required=True,
        ),
        execution_enabled=provider_ready,
        disabled_reason=None if provider_ready else "Generic execution is disabled by default.",
    )
    if fixture_state == "backend_unavailable":
        return GenericAgentQaFixtureResponse(
            fixture_state=fixture_state,
            providers=[],
            error="The ForgeX backend is unavailable.",
        )
    if fixture_state in {"disabled", "ready", "submitting"}:
        return GenericAgentQaFixtureResponse(
            fixture_state=fixture_state,
            providers=[provider],
            submitting=fixture_state == "submitting",
        )

    status = "running" if fixture_state == "resync_required" else fixture_state
    progress = _progress(BridgeRunStatus(status))
    terminal = BridgeRunStatus(status) in TERMINAL_STATES
    failed = status in {"blocked", "failed", "timed_out", "interrupted"}
    run_id = f"qa-run-{fixture_state.replace('_', '-')}"
    message = _qa_fixture_message(status)
    return GenericAgentQaFixtureResponse(
        fixture_state=fixture_state,
        providers=[provider],
        run=GenericRunDetailResponse(
            run_id=run_id,
            provider_id="agy",
            status=status,
            created_at="2026-06-29T00:00:00Z",
            updated_at="2026-06-29T00:00:01Z",
            started_at="2026-06-29T00:00:00Z",
            finished_at="2026-06-29T00:00:01Z" if terminal else None,
            failure_code="qa_safe_failure" if failed else None,
            safe_failure_message=message if failed else None,
            progress=progress,
            artifact_count=1 if status == "completed" else 0,
            review_id="qa-review-completed" if status == "completed" else None,
            changed_file_count=1 if status == "completed" else 0,
            cancellable=not terminal,
        ),
        events=[
            {
                "event_id": f"qa-event-{fixture_state.replace('_', '-')}",
                "run_id": run_id,
                "sequence": 1,
                "timestamp": "2026-06-29T00:00:01Z",
                "event_type": "state_changed" if not terminal else "completed" if status == "completed" else "failure",
                "status": status,
                "safe_message": message,
                "progress": progress,
            }
        ],
        connection_state="resync_required" if fixture_state == "resync_required" else "connected" if not terminal else "idle",
    )


@router.get("/runs/{run_id}/events")
@router.get("/generic/runs/{run_id}/events")
async def generic_run_events(run_id: str, request: Request) -> StreamingResponse:
    record = _generic_record(request, run_id)
    coordinator = _coordinator(request)
    transport = _transport(request)
    last_event_id = request.headers.get("last-event-id")
    replay, after_sequence, resync = _prepare_replay(coordinator, record, last_event_id)

    async def stream() -> AsyncIterator[str]:
        subscription = await transport.subscribe(run_id, after_sequence=after_sequence)
        try:
            if os.getenv("FORGEX_QA_MODE", "").strip() == "1":
                # Deterministic packaged QA proof that comment heartbeats are
                # render-safe; production retains the bounded timer below.
                yield ": heartbeat\n\n"
            if resync is not None:
                yield _sse(resync)
                return
            for event in replay:
                yield _sse(event)
                if _event_terminal(event):
                    return
            while True:
                try:
                    event = await asyncio.wait_for(subscription.get(), timeout=_heartbeat_seconds())
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": heartbeat\n\n"
                    continue
                yield _sse(event)
                if _event_terminal(event):
                    return
        finally:
            await subscription.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _policy(request: Request) -> GenericRunFeatureFlags:
    value = required_state(request, "generic_run_feature_flags", "generic run policy")
    assert isinstance(value, GenericRunFeatureFlags)
    return value


def _coordinator(request: Request) -> GenericBridgeRunCoordinator:
    value = required_state(request, "generic_bridge_coordinator", "generic bridge coordinator")
    assert isinstance(value, GenericBridgeRunCoordinator)
    return value


def _transport(request: Request) -> InternalBridgeEventTransport:
    value = required_state(request, "generic_bridge_event_transport", "generic bridge event transport")
    assert isinstance(value, InternalBridgeEventTransport)
    return value


def _execution_router(request: Request) -> AGYExecutionRouter:
    value = required_state(request, "agy_execution_router", "AGY execution router")
    assert isinstance(value, AGYExecutionRouter)
    return value


def _require_local_request(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin or origin == "null" or origin.startswith("file://"):
        return
    if origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:") or origin.startswith("http://[::1]:"):
        return
    raise APIError(403, "REMOTE_ORIGIN_REJECTED", "Generic execution is restricted to the local desktop application.")


def _idempotent_record(coordinator: GenericBridgeRunCoordinator, body: GenericRunStartRequest) -> BridgeRunRecord | None:
    digest = hashlib.sha256(body.instruction.encode("utf-8")).hexdigest()
    try:
        records = coordinator.list_recent_runs(limit=100, offset=0)
    except BridgeDomainError as exc:
        raise _domain_api_error(exc) from exc
    existing = next((item for item in records if item.correlation_id == body.idempotency_key), None)
    if existing is None:
        return None
    if (
        existing.execution_mode != "generic"
        or existing.provider_id != body.provider_id
        or existing.project_id != body.project_id
        or existing.instruction_hash != digest
    ):
        raise APIError(409, "IDEMPOTENCY_CONFLICT", "The idempotency key is already used by another run request.")
    return existing


def _enforce_active_limits(coordinator: GenericBridgeRunCoordinator, project_id: str) -> None:
    active = [item for item in coordinator.list_recent_runs(limit=100) if item.status not in TERMINAL_STATES]
    if len(active) >= MAX_ACTIVE_GLOBAL or sum(item.project_id == project_id for item in active) >= MAX_ACTIVE_PER_PROJECT:
        raise APIError(409, "ACTIVE_RUN_LIMIT", "The local generic run limit has been reached.")


async def _wait_until_persisted(coordinator: GenericBridgeRunCoordinator, run_id: str) -> BridgeRunRecord:
    for _ in range(100):
        try:
            return coordinator.get_run(run_id)
        except BridgeDomainError:
            await asyncio.sleep(0)
    raise APIError(500, "PERSISTENCE_FAILED", "Generic run state could not be persisted.")


def _start_response(record: BridgeRunRecord, *, reused: bool) -> GenericRunStartResponse:
    return GenericRunStartResponse(
        run_id=record.run_id,
        provider_id="agy",
        status=record.status.value,
        created_at=format_datetime(record.created_at) or "",
        idempotent_reuse=reused,
        event_stream_available=True,
    )


def _generic_record(request: Request, run_id: str) -> BridgeRunRecord:
    try:
        validate_identifier(run_id, "run_id")
        record = _coordinator(request).get_run(run_id)
    except BridgeDomainError as exc:
        status = 500 if exc.code is BridgeErrorCode.RECORD_CORRUPT else 404
        code = "GENERIC_RUN_STATE_CORRUPT" if status == 500 else "GENERIC_RUN_NOT_FOUND"
        message = "Generic run state is unavailable." if status == 500 else "Generic run was not found."
        raise APIError(status, code, message) from exc
    if record.execution_mode != "generic" or record.provider_id != "agy":
        raise APIError(404, "GENERIC_RUN_NOT_FOUND", "Generic run was not found.")
    return record


def _public_run(request: Request, record: BridgeRunRecord) -> GenericRunDetailResponse:
    coordinator = _coordinator(request)
    review_id = None
    try:
        artifacts = coordinator.list_run_artifacts(record.run_id)
        review_id = next((item.get("review_id") for item in artifacts if item.get("review_id")), None)
    except BridgeDomainError:
        artifacts = ()
    changed_file_count = 0
    if isinstance(review_id, str):
        reviews = getattr(request.app.state, "bridge_diff_service", None)
        try:
            changed_file_count = len(reviews.get_review(review_id).changed_files)
        except Exception:
            changed_file_count = 0
    return GenericRunDetailResponse(
        run_id=record.run_id,
        provider_id="agy",
        status=record.status.value,
        created_at=format_datetime(record.created_at) or "",
        updated_at=format_datetime(record.updated_at) or "",
        started_at=format_datetime(record.started_at),
        finished_at=format_datetime(record.finished_at),
        failure_code=record.failure_code,
        safe_failure_message=record.safe_failure_message,
        progress=_progress(record.status),
        artifact_count=record.artifact_count,
        review_id=review_id if isinstance(review_id, str) else None,
        changed_file_count=changed_file_count,
        cancellation_requested=record.cancellation_requested,
        cancellation_requested_at=format_datetime(record.cancellation_requested_at),
        cancellable=record.status not in TERMINAL_STATES,
    )


def _progress(status: BridgeRunStatus) -> int:
    return {
        BridgeRunStatus.QUEUED: 5,
        BridgeRunStatus.VALIDATING: 15,
        BridgeRunStatus.PREPARING_SANDBOX: 30,
        BridgeRunStatus.RUNNING: 55,
        BridgeRunStatus.COLLECTING_ARTIFACTS: 85,
        BridgeRunStatus.CANCELLING: 75,
        BridgeRunStatus.COMPLETED: 100,
        BridgeRunStatus.CANCELLED: 100,
        BridgeRunStatus.FAILED: 100,
        BridgeRunStatus.TIMED_OUT: 100,
        BridgeRunStatus.INTERRUPTED: 100,
        BridgeRunStatus.BLOCKED: 100,
    }[status]


def _prepare_replay(
    coordinator: GenericBridgeRunCoordinator,
    record: BridgeRunRecord,
    last_event_id: str | None,
) -> tuple[tuple[BridgeRunEvent, ...], int, BridgeRunEvent | None]:
    events = coordinator.run_store.list_events(record.run_id)
    if not events:
        return (), 0, None
    if not last_event_id:
        replay = events[-MAX_REPLAY_EVENTS:]
        return replay, replay[-1].sequence if replay else 0, None
    matched = next((item for item in events if item.event_id == last_event_id), None)
    if matched is None:
        return (), events[-1].sequence, _resync_event(events[-1])
    missing = tuple(item for item in events if item.sequence > matched.sequence)
    if len(missing) > MAX_REPLAY_EVENTS:
        return (), events[-1].sequence, _resync_event(events[-1])
    return missing, events[-1].sequence, None


def _resync_event(reference: BridgeRunEvent) -> BridgeRunEvent:
    return BridgeRunEvent(
        event_id=f"event-resync-{reference.sequence}",
        run_id=reference.run_id,
        sequence=reference.sequence,
        timestamp=utc_now(),
        event_type=BridgeEventType.RESYNC_REQUIRED,
        status=reference.status,
        safe_message="Event replay history was missed; reload the current run detail.",
    )


def _sse(event: BridgeRunEvent) -> str:
    return f"id: {event.event_id}\ndata: {json.dumps(event.to_dict(), ensure_ascii=True, separators=(',', ':'))}\n\n"


def _event_terminal(event: BridgeRunEvent) -> bool:
    try:
        return bool(event.status and BridgeRunStatus(event.status) in TERMINAL_STATES)
    except ValueError:
        return True


def _heartbeat_seconds() -> float:
    return 0.25 if os.getenv("FORGEX_QA_MODE", "").strip() == "1" else HEARTBEAT_SECONDS


def _qa_fixture_message(status: str) -> str:
    return {
        "queued": "Run queued in the managed QA fixture.",
        "validating": "Validating the managed QA fixture.",
        "preparing_sandbox": "Preparing an isolated QA sandbox.",
        "running": "AGY is running in the managed QA fixture.",
        "collecting_artifacts": "Collecting sanitized review artifacts.",
        "cancelling": "Cancellation requested for the QA fixture.",
        "completed": "Run completed and is ready for review.",
        "blocked": "Run was blocked by local policy.",
        "failed": "Run failed safely without provider output.",
        "cancelled": "Run was cancelled safely.",
        "timed_out": "Run reached its bounded timeout.",
        "interrupted": "Run was interrupted during backend restart.",
    }[status]


def _domain_api_error(exc: BridgeDomainError) -> APIError:
    status = 500 if exc.code in {BridgeErrorCode.PERSISTENCE_FAILED, BridgeErrorCode.RECORD_CORRUPT, BridgeErrorCode.INTERNAL_ERROR} else 422
    return APIError(status, exc.code.value.upper(), exc.safe_message)
