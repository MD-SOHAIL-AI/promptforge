"""Connected board detection routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from ...tools.board_detector import BoardDetectionError, BoardDetector
from ..dependencies import required_state
from ..errors import APIError, error_responses
from ..schemas.devices import DetectedBoardResponse, DetectedBoardsResponse

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get(
    "/boards",
    response_model=DetectedBoardsResponse,
    responses=error_responses(500, 503),
    summary="List detected serial-connected boards",
)
async def detected_boards(request: Request) -> DetectedBoardsResponse:
    detector = required_state(request, "board_detector", "board detector")
    assert isinstance(detector, BoardDetector)
    try:
        boards = await asyncio.to_thread(detector.detect_boards)
    except BoardDetectionError as exc:
        raise APIError(503, "BOARD_DETECTION_FAILED", "Connected boards could not be detected") from exc
    response = [
        DetectedBoardResponse(
            board_type=board.board_type.value,
            port=board.port,
            vid=board.vid,
            pid=board.pid,
            manufacturer=board.manufacturer,
            description=board.description,
            serial_number=board.serial_number,
        )
        for board in boards
    ]
    return DetectedBoardsResponse(boards=response, count=len(response))
