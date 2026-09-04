"""Pure build-stage conversions for PromptForge workflows."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ...contracts.execution_context import ExecutionContext
from ...runtime.result import BuildResult, ResultStatus
from ...tools.build_firmware import BuildArtifact

__all__ = [
    "BuildAdapter",
    "BuildAdapterError",
    "build_result_to_artifact",
    "update_context_after_build",
    "validate_build_artifact",
]


class BuildAdapterError(ValueError):
    """Build output cannot be converted into a workflow artifact."""


class BuildAdapter:
    """Stateless facade for build-stage adapter functions."""

    __slots__ = ()

    @staticmethod
    def to_artifact(
        result: BuildResult,
        *,
        environment: str | None = None,
        artifact_type: str | None = None,
    ) -> BuildArtifact:
        return build_result_to_artifact(
            result,
            environment=environment,
            artifact_type=artifact_type,
        )

    @staticmethod
    def update_context(
        context: ExecutionContext,
        artifact: BuildArtifact,
        *,
        result: BuildResult | None = None,
    ) -> ExecutionContext:
        return update_context_after_build(
            context,
            artifact,
            result=result,
        )

    @staticmethod
    def adapt(
        result: BuildResult,
        context: ExecutionContext,
        *,
        environment: str | None = None,
        artifact_type: str | None = None,
    ) -> tuple[BuildArtifact, ExecutionContext]:
        artifact = build_result_to_artifact(
            result,
            environment=environment,
            artifact_type=artifact_type,
        )
        return artifact, update_context_after_build(
            context,
            artifact,
            result=result,
        )


def build_result_to_artifact(
    result: BuildResult,
    *,
    environment: str | None = None,
    artifact_type: str | None = None,
) -> BuildArtifact:
    """Convert a successful ``BuildResult`` into ``BuildArtifact``."""

    if not isinstance(result, BuildResult):
        raise TypeError("result must be a BuildResult")
    if not result.success or result.status is not ResultStatus.SUCCESS:
        raise BuildAdapterError("BuildResult must represent a successful build")
    path = _artifact_path(result.firmware_path)
    size = result.build_size_bytes
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise BuildAdapterError("build_size_bytes must be a positive integer")

    resolved_environment = environment
    if resolved_environment is None:
        resolved_environment = result.metadata.get("artifact_environment")
    if resolved_environment is None:
        resolved_environment = path.parent.name
    resolved_environment = _text(resolved_environment, "environment")

    resolved_type = artifact_type
    if resolved_type is None:
        resolved_type = result.metadata.get("artifact_type")
    if resolved_type is None:
        resolved_type = path.suffix.lstrip(".")
    resolved_type = _text(resolved_type, "artifact_type").lstrip(".").lower()
    if not resolved_type:
        raise BuildAdapterError("artifact_type cannot be empty")
    if path.suffix and path.suffix.lstrip(".").casefold() != resolved_type.casefold():
        raise BuildAdapterError(
            "artifact_type must match the firmware_path extension"
        )

    artifact = BuildArtifact(
        path=path,
        environment=resolved_environment,
        artifact_type=resolved_type,
        size_bytes=size,
    )
    validate_build_artifact(artifact)
    return artifact


def update_context_after_build(
    context: ExecutionContext,
    artifact: BuildArtifact,
    *,
    result: BuildResult | None = None,
) -> ExecutionContext:
    """Return the immutable context snapshot after a successful build."""

    if not isinstance(context, ExecutionContext):
        raise TypeError("context must be an ExecutionContext")
    validate_build_artifact(artifact)
    if result is not None:
        converted = build_result_to_artifact(result)
        if converted != artifact:
            raise BuildAdapterError("artifact must match the supplied BuildResult")

    snapshot = {
        "path": str(artifact.path),
        "environment": artifact.environment,
        "artifact_type": artifact.artifact_type,
        "size_bytes": artifact.size_bytes,
    }
    payload: dict[str, object] = dict(snapshot)
    if result is not None:
        payload.update(
            {
                "platform": result.platform,
                "board": result.board,
                "toolchain_version": result.toolchain_version,
                "warnings_count": result.warnings_count,
            }
        )
    return replace(
        context,
        firmware_path=str(artifact.path),
        build_artifact=snapshot,
        metadata=_stage_metadata(context, "build", payload),
    )


def validate_build_artifact(artifact: object) -> None:
    if not isinstance(artifact, BuildArtifact):
        raise TypeError("artifact must be a BuildArtifact")
    _artifact_path(artifact.path)
    _text(artifact.environment, "environment")
    artifact_type = _text(artifact.artifact_type, "artifact_type")
    if artifact_type.startswith("."):
        raise BuildAdapterError("artifact_type cannot start with a dot")
    if (
        not isinstance(artifact.size_bytes, int)
        or isinstance(artifact.size_bytes, bool)
        or artifact.size_bytes <= 0
    ):
        raise BuildAdapterError("artifact size_bytes must be positive")


def _artifact_path(value: object) -> Path:
    if not isinstance(value, (str, Path)):
        raise BuildAdapterError("firmware_path must be a path")
    text = str(value)
    if not text.strip() or "\x00" in text:
        raise BuildAdapterError(
            "firmware_path must be a non-empty NUL-free path"
        )
    path = Path(value)
    if not path.name or not path.suffix:
        raise BuildAdapterError("firmware_path must include a file extension")
    return path


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise BuildAdapterError(
            f"{field_name} must be a non-empty NUL-free string"
        )
    return value.strip()


def _stage_metadata(
    context: ExecutionContext,
    stage: str,
    payload: dict[str, object],
) -> dict[str, object]:
    metadata = context.to_dict()["metadata"]
    workflow = metadata.get("workflow", {})
    if not isinstance(workflow, dict):
        raise BuildAdapterError("context metadata.workflow must be a mapping")
    workflow[stage] = payload
    metadata["workflow"] = workflow
    return metadata
