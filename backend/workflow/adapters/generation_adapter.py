"""Pure generation-stage conversions for PromptForge workflows."""

from __future__ import annotations

import configparser
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Mapping

from ...contracts.execution_context import ExecutionContext
from ...contracts.generated_project import GeneratedProject
from ...tools.build_firmware import BuildConfig

__all__ = [
    "GenerationAdapter",
    "GenerationAdapterError",
    "generated_project_to_build_config",
    "project_root_to_build_config",
    "resolve_build_config",
    "update_context_after_generation",
]


class GenerationAdapterError(ValueError):
    """Generation output cannot be converted into a build-stage input."""


class GenerationAdapter:
    """Stateless facade for generation-stage adapter functions."""

    __slots__ = ()

    @staticmethod
    def to_build_config(
        project: GeneratedProject,
        *,
        project_path: str | Path | None = None,
        context: ExecutionContext | None = None,
        environment: str | None = None,
        board: str | None = None,
        timeout_s: float = 180.0,
        executable: str = "pio",
        extra_args: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        session_id: str | None = None,
        version_timeout_s: float = 10.0,
    ) -> BuildConfig:
        return generated_project_to_build_config(
            project,
            project_path=project_path,
            context=context,
            environment=environment,
            board=board,
            timeout_s=timeout_s,
            executable=executable,
            extra_args=extra_args,
            env=env,
            session_id=session_id,
            version_timeout_s=version_timeout_s,
        )

    @staticmethod
    def from_project_root(
        project_path: str | Path | None = None,
        *,
        context: ExecutionContext | None = None,
        environment: str | None = None,
        board: str | None = None,
        timeout_s: float = 180.0,
        executable: str = "pio",
        extra_args: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        session_id: str | None = None,
        version_timeout_s: float = 10.0,
    ) -> BuildConfig:
        return project_root_to_build_config(
            project_path,
            context=context,
            environment=environment,
            board=board,
            timeout_s=timeout_s,
            executable=executable,
            extra_args=extra_args,
            env=env,
            session_id=session_id,
            version_timeout_s=version_timeout_s,
        )

    @staticmethod
    def resolve_build_config(
        project: GeneratedProject | None = None,
        *,
        project_path: str | Path | None = None,
        context: ExecutionContext | None = None,
        environment: str | None = None,
        board: str | None = None,
        timeout_s: float = 180.0,
        executable: str = "pio",
        extra_args: tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        session_id: str | None = None,
        version_timeout_s: float = 10.0,
    ) -> BuildConfig:
        return resolve_build_config(
            project,
            project_path=project_path,
            context=context,
            environment=environment,
            board=board,
            timeout_s=timeout_s,
            executable=executable,
            extra_args=extra_args,
            env=env,
            session_id=session_id,
            version_timeout_s=version_timeout_s,
        )

    @staticmethod
    def update_context(
        context: ExecutionContext,
        project: GeneratedProject,
        *,
        project_path: str | Path,
    ) -> ExecutionContext:
        return update_context_after_generation(
            context,
            project,
            project_path=project_path,
        )

    @staticmethod
    def adapt(
        project: GeneratedProject,
        context: ExecutionContext,
        *,
        project_path: str | Path,
        **build_options: object,
    ) -> tuple[BuildConfig, ExecutionContext]:
        updated = update_context_after_generation(
            context,
            project,
            project_path=project_path,
        )
        config = generated_project_to_build_config(
            project,
            project_path=project_path,
            context=updated,
            **build_options,
        )
        return config, updated


def generated_project_to_build_config(
    project: GeneratedProject,
    *,
    project_path: str | Path | None = None,
    context: ExecutionContext | None = None,
    environment: str | None = None,
    board: str | None = None,
    timeout_s: float = 180.0,
    executable: str = "pio",
    extra_args: tuple[str, ...] = (),
    env: Mapping[str, str] | None = None,
    session_id: str | None = None,
    version_timeout_s: float = 10.0,
) -> BuildConfig:
    """Convert a materialized ``GeneratedProject`` into ``BuildConfig``.

    Materialization is owned by ``ProjectService``. The adapter only receives
    the resulting path explicitly, from an execution context, or from the
    project's ``metadata.project_path`` value.
    """

    _validate_project(project)
    if context is not None:
        _validate_context_project(context, project)
    resolved_path = _project_path(project, project_path, context)
    inferred_environment, inferred_board = _platformio_selection(project)

    return BuildConfig(
        project_dir=resolved_path,
        environment=(
            _optional_text(environment, "environment")
            if environment is not None
            else inferred_environment
        ),
        board=(
            _optional_text(board, "board")
            if board is not None
            else inferred_board
        ),
        timeout_s=timeout_s,
        executable=executable,
        extra_args=tuple(extra_args),
        env={} if env is None else env,
        session_id=session_id,
        version_timeout_s=version_timeout_s,
    )


def resolve_build_config(
    project: GeneratedProject | None = None,
    *,
    project_path: str | Path | None = None,
    context: ExecutionContext | None = None,
    environment: str | None = None,
    board: str | None = None,
    timeout_s: float = 180.0,
    executable: str = "pio",
    extra_args: tuple[str, ...] = (),
    env: Mapping[str, str] | None = None,
    session_id: str | None = None,
    version_timeout_s: float = 10.0,
) -> BuildConfig:
    """Resolve build input from a generated project or active workspace root."""

    if project is not None:
        return generated_project_to_build_config(
            project,
            project_path=project_path,
            context=context,
            environment=environment,
            board=board,
            timeout_s=timeout_s,
            executable=executable,
            extra_args=extra_args,
            env=env,
            session_id=session_id,
            version_timeout_s=version_timeout_s,
        )
    return project_root_to_build_config(
        project_path,
        context=context,
        environment=environment,
        board=board,
        timeout_s=timeout_s,
        executable=executable,
        extra_args=extra_args,
        env=env,
        session_id=session_id,
        version_timeout_s=version_timeout_s,
    )


def project_root_to_build_config(
    project_path: str | Path | None = None,
    *,
    context: ExecutionContext | None = None,
    environment: str | None = None,
    board: str | None = None,
    timeout_s: float = 180.0,
    executable: str = "pio",
    extra_args: tuple[str, ...] = (),
    env: Mapping[str, str] | None = None,
    session_id: str | None = None,
    version_timeout_s: float = 10.0,
) -> BuildConfig:
    """Create ``BuildConfig`` from an active PlatformIO workspace root."""

    if context is not None and not isinstance(context, ExecutionContext):
        raise TypeError("context must be an ExecutionContext")
    root = _workspace_project_path(project_path, context)
    ini = root / "platformio.ini"
    if not ini.is_file() or ini.is_symlink():
        raise GenerationAdapterError(
            "Build requires platformio.ini. Generate or initialize a "
            f"PlatformIO project first. Workspace root: {root}"
        )
    inferred_environment, inferred_board = _platformio_selection_from_text(
        ini.read_text(encoding="utf-8-sig")
    )
    return BuildConfig(
        project_dir=str(root),
        environment=(
            _optional_text(environment, "environment")
            if environment is not None
            else inferred_environment
        ),
        board=(
            _optional_text(board, "board")
            if board is not None
            else inferred_board
        ),
        timeout_s=timeout_s,
        executable=executable,
        extra_args=tuple(extra_args),
        env={} if env is None else env,
        session_id=session_id,
        version_timeout_s=version_timeout_s,
    )


def update_context_after_generation(
    context: ExecutionContext,
    project: GeneratedProject,
    *,
    project_path: str | Path,
) -> ExecutionContext:
    """Return the immutable context snapshot after project generation."""

    _validate_context_project(context, project)
    path = _path_text(project_path, "project_path")
    metadata = _stage_metadata(
        context,
        "generation",
        {
            "project_id": project.project_id,
            "project_name": project.project_name,
            "file_count": len(project.files),
        },
    )
    active_workspace = metadata.get("active_workspace")
    if isinstance(active_workspace, dict):
        root = Path(path).expanduser().resolve()
        ini = root / "platformio.ini"
        active_workspace["rootPath"] = str(root)
        active_workspace["root_path"] = str(root)
        active_workspace["platformioIniPath"] = str(ini)
        active_workspace["platformio_ini_path"] = str(ini)
        active_workspace["has_platformio_ini"] = ini.is_file() and not ini.is_symlink()
        if active_workspace.get("project_type") == "generic" and active_workspace["has_platformio_ini"]:
            active_workspace["project_type"] = "platformio"
        metadata["active_workspace"] = active_workspace
    return replace(
        context,
        project_path=path,
        target_board=_identifier(project.target_board),
        framework=_identifier(project.framework),
        metadata=metadata,
    )


def _validate_project(project: object) -> None:
    if not isinstance(project, GeneratedProject):
        raise TypeError("project must be a GeneratedProject")
    framework = _identifier(project.framework).casefold().replace("-", "")
    if framework != "platformio":
        raise GenerationAdapterError(
            "GeneratedProject framework must be PlatformIO"
        )


def _validate_context_project(
    context: object,
    project: GeneratedProject,
) -> None:
    if not isinstance(context, ExecutionContext):
        raise TypeError("context must be an ExecutionContext")
    if not isinstance(project, GeneratedProject):
        raise TypeError("project must be a GeneratedProject")
    task_id = project.metadata.get("task_id")
    if task_id is not None and task_id != context.task_id:
        raise GenerationAdapterError(
            "project metadata task_id must match context.task_id"
        )
    if context.target_board != "UNKNOWN" and (
        context.target_board != _identifier(project.target_board)
    ):
        raise GenerationAdapterError(
            "project target_board must match the execution context"
        )
    if context.framework != "UNKNOWN" and (
        context.framework != _identifier(project.framework)
    ):
        raise GenerationAdapterError(
            "project framework must match the execution context"
        )


def _project_path(
    project: GeneratedProject,
    explicit: str | Path | None,
    context: ExecutionContext | None,
) -> str:
    candidate: object = explicit
    if candidate is None and context is not None:
        candidate = context.project_path
    if candidate is None:
        candidate = project.metadata.get("project_path")
    if candidate is None:
        raise GenerationAdapterError(
            "project_path is required after ProjectService materialization"
        )
    return _path_text(candidate, "project_path")


def _platformio_selection(
    project: GeneratedProject,
) -> tuple[str | None, str | None]:
    ini = project.get_file("platformio.ini")
    if ini is None:
        raise GenerationAdapterError(
            "GeneratedProject must contain platformio.ini"
        )
    return _platformio_selection_from_text(ini.content)


def _platformio_selection_from_text(content: str) -> tuple[str | None, str | None]:
    parser = configparser.RawConfigParser(
        strict=True,
        inline_comment_prefixes=(";", "#"),
    )
    try:
        parser.read_string(content)
    except configparser.Error as exc:
        raise GenerationAdapterError(
            f"platformio.ini is invalid: {exc}"
        ) from exc

    environments = tuple(
        section[4:].strip()
        for section in parser.sections()
        if section.startswith("env:") and section[4:].strip()
    )
    if not environments:
        raise GenerationAdapterError(
            "platformio.ini must define at least one environment"
        )

    selected: str | None = None
    defaults = parser.get("platformio", "default_envs", fallback="")
    default_names = tuple(
        item.strip()
        for item in defaults.replace("\n", ",").split(",")
        if item.strip()
    )
    if len(default_names) == 1:
        selected = default_names[0]
        if selected not in environments:
            raise GenerationAdapterError(
                "platformio.ini default_envs references an unknown environment"
            )
    elif len(environments) == 1:
        selected = environments[0]

    selected_board = None
    if selected is not None:
        selected_board = parser.get(
            f"env:{selected}",
            "board",
            fallback="",
        ).strip() or None
    return selected, selected_board


def _workspace_project_path(
    explicit: str | Path | None,
    context: ExecutionContext | None,
) -> Path:
    candidate: object = explicit
    if candidate is None and context is not None and context.project_path is not None:
        candidate = context.project_path
    if candidate is None and context is not None:
        workspace = context.metadata.get("active_workspace")
        if isinstance(workspace, Mapping):
            candidate = workspace.get("rootPath") or workspace.get("root_path")
    if candidate is None:
        raise GenerationAdapterError(
            "Build requires platformio.ini. Generate or initialize a "
            "PlatformIO project first."
        )
    text = _path_text(candidate, "project_path")
    try:
        return Path(text).expanduser().resolve()
    except OSError as exc:
        raise GenerationAdapterError(f"project_path is invalid: {exc}") from exc


def _stage_metadata(
    context: ExecutionContext,
    stage: str,
    payload: dict[str, object],
) -> dict[str, object]:
    metadata = context.to_dict()["metadata"]
    workflow = metadata.get("workflow", {})
    if not isinstance(workflow, dict):
        raise GenerationAdapterError("context metadata.workflow must be a mapping")
    workflow[stage] = payload
    metadata["workflow"] = workflow
    return metadata


def _path_text(value: object, field_name: str) -> str:
    if not isinstance(value, (str, Path)):
        raise GenerationAdapterError(f"{field_name} must be a path")
    text = str(value)
    if not text.strip() or "\x00" in text:
        raise GenerationAdapterError(
            f"{field_name} must be a non-empty NUL-free path"
        )
    return text


def _optional_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise GenerationAdapterError(
            f"{field_name} must be a non-empty NUL-free string"
        )
    return value.strip()


def _identifier(value: str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return value
