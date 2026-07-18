"""Dev-only fake API coding-agent pipeline into the ForgeX review system."""

from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from ..bridges.diff_service import BridgeDiffService
from ..bridges.sandbox_service import BridgeSandboxService
from .api_agent_contracts import (
    ApiCodingAgentCommandSuggestion,
    ApiCodingAgentContractError,
    ApiCodingAgentFileOperation,
    ApiCodingAgentProposal,
    parse_api_coding_agent_response,
)
from .api_coding_agent_fake import FakeApiCodingAgentProvider


FAKE_API_CODING_AGENT_FLAG = "FORGEX_ENABLE_FAKE_API_CODING_AGENT"
FAKE_API_CODING_AGENT_DISABLED = "FAKE_API_CODING_AGENT_DISABLED"
FAKE_API_CODING_AGENT_PROVIDER_ID = "fake_api_coding_agent"

_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ApiCodingAgentProvider(Protocol):
    def generate(self, prompt: str, context: object | None = None) -> str: ...


class ApiCodingAgentServiceError(ValueError):
    def __init__(self, code: str, message: str, details: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ApiCodingAgentRunResult:
    status: str
    summary: str = ""
    review_id: str | None = None
    review_path: str | None = None
    files: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    commands_suggested: tuple[ApiCodingAgentCommandSuggestion, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "summary": self.summary,
            "review_id": self.review_id,
            "review_path": self.review_path,
            "files": list(self.files),
            "validation_errors": list(self.validation_errors),
            "risks": list(self.risks),
            "next_steps": list(self.next_steps),
            "commands_suggested": [
                {"command": item.command, "reason": item.reason}
                for item in self.commands_suggested
            ],
        }


class ApiCodingAgentService:
    """Runs deterministic fake output through validation and managed review creation."""

    def __init__(
        self,
        *,
        sandbox_service: BridgeSandboxService,
        review_service: BridgeDiffService,
        provider: ApiCodingAgentProvider | None = None,
        enabled: bool | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        environment = os.environ if env is None else env
        self.enabled = environment.get(FAKE_API_CODING_AGENT_FLAG, "").strip() == "1" if enabled is None else enabled
        self.sandbox_service = sandbox_service
        self.review_service = review_service
        self.provider = provider or FakeApiCodingAgentProvider()

    def generate_review_from_fake_provider(
        self,
        prompt: str,
        workspace_path: Path,
        *,
        allow_delete: bool = False,
    ) -> ApiCodingAgentRunResult:
        if not self.enabled:
            raise ApiCodingAgentServiceError(
                FAKE_API_CODING_AGENT_DISABLED,
                "The fake API coding agent is disabled. Set FORGEX_ENABLE_FAKE_API_CODING_AGENT=1 for dev/test use.",
            )
        if not isinstance(prompt, str) or not prompt.strip():
            raise ApiCodingAgentServiceError("FAKE_API_CODING_AGENT_PROMPT_INVALID", "Prompt must be a non-empty string.")

        raw = self.provider.generate(prompt)
        try:
            proposal = parse_api_coding_agent_response(raw, allow_delete=allow_delete)
        except ApiCodingAgentContractError as exc:
            return ApiCodingAgentRunResult(
                status="rejected",
                validation_errors=exc.errors,
            )

        return self.create_review_from_validated_proposal(
            proposal,
            workspace_path,
            run_id=f"fake-api-coding-agent-{uuid.uuid4().hex}",
            provider_id=FAKE_API_CODING_AGENT_PROVIDER_ID,
            artifact_source="api_coding_agent_fake",
            artifact_type="api_coding_agent_workspace_diff",
            artifact_metadata={
                "schema_version": proposal.schema_version,
                "dev_only": True,
                "real_api_calls": False,
            },
        )

    def create_review_from_validated_proposal(
        self,
        proposal: ApiCodingAgentProposal,
        workspace_path: Path,
        *,
        run_id: str,
        provider_id: str,
        artifact_source: str,
        artifact_type: str,
        artifact_metadata: Mapping[str, object] | None = None,
    ) -> ApiCodingAgentRunResult:
        """Create a managed sandbox review from an already validated proposal."""

        active_root = workspace_path.expanduser().resolve()
        if not active_root.exists() or not active_root.is_dir():
            raise ApiCodingAgentServiceError(
                "API_CODING_AGENT_WORKSPACE_INVALID",
                "Workspace path must be an existing directory.",
            )
        managed_root = self.sandbox_service.sandbox_root.expanduser().resolve()
        try:
            active_root.relative_to(managed_root)
        except ValueError:
            pass
        else:
            raise ApiCodingAgentServiceError(
                "API_CODING_AGENT_WORKSPACE_INVALID",
                "Active workspace cannot be inside the managed coding-agent sandbox root.",
            )

        active_baseline = self.review_service.inspect_workspace(active_root)
        sandbox_root: Path | None = None
        try:
            sandbox_root = self.sandbox_service.create_sandbox(run_id=run_id, workspace_root=active_root)
            baseline = self.review_service.inspect_workspace(sandbox_root)
            for operation in proposal.files:
                self._apply_operation(sandbox_root, operation)

            active_changes = self.review_service.diff_snapshot(active_baseline, active_root)
            if active_changes:
                raise ApiCodingAgentServiceError(
                    "API_CODING_AGENT_ACTIVE_WORKSPACE_CHANGED",
                    "Active workspace changed while the coding agent was running.",
                )

            changed = self.review_service.diff_snapshot(baseline, sandbox_root)
            declared_paths = {item.path.casefold() for item in proposal.files}
            changed_paths = {item.path.casefold() for item in changed}
            if not changed or changed_paths != declared_paths or any(not item.safe for item in changed):
                raise ApiCodingAgentServiceError(
                    "API_CODING_AGENT_DIFF_INVALID",
                    "Sandbox changes did not exactly match the validated proposal.",
                    {"declared_file_count": len(declared_paths), "changed_file_count": len(changed_paths)},
                )

            review = self.review_service.create_review(
                provider_id=provider_id,
                workspace_root=sandbox_root,
                snapshot=baseline,
                artifact_source=artifact_source,
                artifact_type=artifact_type,
                artifact_metadata={
                    **dict(artifact_metadata or {}),
                    "run_id": run_id,
                },
            )
        except Exception:
            if sandbox_root is not None:
                self.sandbox_service.cleanup_sandbox(run_id=run_id)
            raise

        return ApiCodingAgentRunResult(
            status="review_created",
            summary=proposal.summary,
            review_id=review.review_id,
            review_path=str(sandbox_root),
            files=tuple(item.path for item in proposal.files),
            risks=proposal.risks,
            next_steps=proposal.next_steps,
            commands_suggested=proposal.commands_suggested,
        )

    @staticmethod
    def _apply_operation(sandbox_root: Path, operation: ApiCodingAgentFileOperation) -> None:
        root = sandbox_root.resolve(strict=True)
        target = root.joinpath(*operation.path.split("/"))
        _require_contained(root, target)
        _reject_link_or_reparse(root)

        current = root
        for part in operation.path.split("/")[:-1]:
            current = current / part
            if current.exists() or current.is_symlink():
                _reject_link_or_reparse(current)
        if target.exists() or target.is_symlink():
            _reject_link_or_reparse(target)

        if operation.action == "delete":
            if not target.exists() or not target.is_file():
                raise ApiCodingAgentServiceError(
                    "FAKE_API_CODING_AGENT_DELETE_INVALID",
                    "Delete target must be an existing regular file in the managed sandbox.",
                    {"path": operation.path},
                )
            target.unlink()
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        _require_contained(root, target.parent.resolve(strict=True))
        if operation.content is None:
            raise ApiCodingAgentServiceError("FAKE_API_CODING_AGENT_CONTENT_INVALID", "Validated file content was missing.")
        target.write_text(operation.content, encoding="utf-8", newline="")


def _require_contained(root: Path, target: Path) -> None:
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise ApiCodingAgentServiceError(
            "FAKE_API_CODING_AGENT_PATH_UNSAFE",
            "Proposed path escaped the managed sandbox.",
        ) from exc


def _reject_link_or_reparse(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ApiCodingAgentServiceError(
            "FAKE_API_CODING_AGENT_PATH_UNSAFE",
            "Managed sandbox path could not be inspected safely.",
        ) from exc
    if path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE):
        raise ApiCodingAgentServiceError(
            "FAKE_API_CODING_AGENT_PATH_UNSAFE",
            "Links and reparse points are forbidden in proposed paths.",
        )
