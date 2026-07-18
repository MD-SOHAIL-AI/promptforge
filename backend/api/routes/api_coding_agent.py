"""Internal/dev API coding-agent routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from ...agent_runtime.api_coding_agent_service import ApiCodingAgentServiceError
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse
from ._model_common import api_coding_agent_service


router = APIRouter()


class FakeApiCodingAgentReviewRequest(APIModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    workspace_path: str = Field(min_length=1)
    allow_delete: bool = False


@router.post(
    "/api-coding-agent/fake/generate-review",
    responses={**error_responses(403, 422, 500), "default": {"model": ErrorResponse}},
)
async def generate_fake_api_coding_agent_review(
    body: FakeApiCodingAgentReviewRequest,
    request: Request,
) -> dict[str, Any]:
    service = api_coding_agent_service(request)
    try:
        result = service.generate_review_from_fake_provider(
            body.prompt,
            Path(body.workspace_path),
            allow_delete=body.allow_delete,
        )
    except ApiCodingAgentServiceError as exc:
        status = 403 if exc.code == "FAKE_API_CODING_AGENT_DISABLED" else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return result.to_dict()
