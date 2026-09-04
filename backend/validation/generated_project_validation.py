"""Unified deterministic validation for generated firmware projects."""

from __future__ import annotations

import configparser
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..contracts.execution_context import ExecutionContext
from ..contracts.execution_plan import ExecutionPlan
from ..contracts.generated_project import GeneratedProject
from ..hardware.compatibility.compatibility_matrix import CompatibilityMatrix
from ..hardware.constraints.constraint_engine import (
    ConstraintEngine,
    ConstraintSeverity,
    HardwareRequirements,
)
from .firmware_validator import FirmwareValidationSeverity, FirmwareValidator
from .safety_validator import SafetySeverity, SafetyValidator

__all__ = [
    "GeneratedProjectValidationError",
    "GeneratedProjectValidationService",
    "ProjectValidationIssue",
    "ProjectValidationReport",
]


@dataclass(frozen=True, slots=True)
class ProjectValidationIssue:
    validator: str
    rule_id: str
    severity: str
    category: str
    message: str
    recommendation: str
    affected_files: tuple[str, ...] = ()
    repairable: bool = False
    intent_conflict: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "validator": self.validator,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "recommendation": self.recommendation,
            "affected_files": list(self.affected_files),
            "repairable": self.repairable,
            "intent_conflict": self.intent_conflict,
        }


@dataclass(frozen=True, slots=True)
class ProjectValidationReport:
    valid: bool
    issues: tuple[ProjectValidationIssue, ...]
    suggested_alternatives: tuple[str, ...] = ()

    @property
    def blocking(self) -> tuple[ProjectValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity in {"ERROR", "CRITICAL"})

    @property
    def repairable(self) -> bool:
        return bool(self.blocking) and all(issue.repairable and not issue.intent_conflict for issue in self.blocking)

    @property
    def requires_user_decision(self) -> bool:
        return any(issue.intent_conflict for issue in self.blocking)

    def message(self) -> str:
        return "; ".join(f"{issue.rule_id}: {issue.message}" for issue in self.blocking) or "Project validation passed"

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "issues": [issue.to_dict() for issue in self.issues],
            "repairable": self.repairable,
            "requires_user_decision": self.requires_user_decision,
            "suggested_alternatives": list(self.suggested_alternatives),
            "message": self.message(),
        }


class GeneratedProjectValidationError(ValueError):
    def __init__(self, report: ProjectValidationReport, *, repair_attempt_count: int = 0) -> None:
        super().__init__(f"project validation failed: {report.message()}")
        self.report = report
        self.repair_attempt_count = repair_attempt_count


class GeneratedProjectValidationService:
    """Validate only typed domain inputs; control metadata is never inferred."""

    def __init__(self, compatibility: CompatibilityMatrix | None = None) -> None:
        self.compatibility = compatibility or CompatibilityMatrix()

    def validate(
        self,
        project: GeneratedProject,
        plan: ExecutionPlan,
        context: ExecutionContext,
    ) -> ProjectValidationReport:
        issues: list[ProjectValidationIssue] = []
        firmware = FirmwareValidator().validate_project(project)
        for issue in firmware.issues:
            if issue.rule_id == "PF-FW-012":
                # Existing PlatformIO compatibility: inherited environment
                # values are validated later by the build adapter.
                continue
            issues.append(
                ProjectValidationIssue(
                    validator="firmware",
                    rule_id=issue.rule_id,
                    severity=issue.severity.value,
                    category=issue.category.value,
                    message=issue.message,
                    recommendation=issue.recommendation,
                    affected_files=((issue.file_path,) if issue.file_path else ()),
                    repairable=(
                        issue.severity is FirmwareValidationSeverity.ERROR
                        and issue.category.value not in {"SECURITY"}
                    ),
                )
            )

        board_name = _identifier(plan.target_board)
        framework = _constraint_framework(plan.framework)
        safety = SafetyValidator().validate(
            prompt=context.metadata.get("prompt"),
            planner_output={
                "target_board": board_name,
                "framework": _identifier(plan.framework),
                "requirements": plan.requirements,
                "execution_steps": tuple(step.value for step in plan.execution_steps),
            },
            firmware_metadata={
                "target_board": _identifier(project.target_board),
                "framework": _identifier(project.framework),
                "requirements": plan.requirements,
                "execution_steps": tuple(step.value for step in plan.execution_steps),
            },
            hardware_metadata={"target_board": board_name, "framework": _identifier(plan.framework)},
        )
        for issue in safety.issues:
            issues.append(
                ProjectValidationIssue(
                    validator="safety",
                    rule_id=issue.rule_id,
                    severity=issue.severity.value,
                    category=issue.category.value,
                    message=issue.message,
                    recommendation=issue.recommendation,
                    repairable=False,
                )
            )

        # Artifact dependencies come from generated code and are repairable.
        # Only explicitly planned requirements below can block for a decision.
        generated_libraries = _project_libraries(project)
        for library in generated_libraries:
            if self.compatibility.is_library_supported(board_name, library, framework):
                continue
            issues.append(
                ProjectValidationIssue(
                    validator="compatibility",
                    rule_id="PF-COMPAT-001",
                    severity="ERROR",
                    category="LIBRARY",
                    message=(
                        f"Generated dependency {library!r} is not approved for "
                        f"{board_name} with {framework}."
                    ),
                    recommendation=(
                        "Use a dependency declared in the reviewed board compatibility catalog."
                    ),
                    affected_files=("platformio.ini",),
                    repairable=True,
                )
            )

        raw_hardware = plan.metadata.get("hardware_requirements")
        try:
            hardware = HardwareRequirements.from_mapping(raw_hardware, framework=framework)
        except (TypeError, ValueError) as exc:
            issues.append(
                ProjectValidationIssue(
                    validator="context",
                    rule_id="PF-CONTEXT-001",
                    severity="ERROR",
                    category="METADATA",
                    message=str(exc) or "Hardware requirement metadata is invalid.",
                    recommendation="Regenerate the deterministic hardware requirement contract.",
                    repairable=False,
                )
            )
            hardware = HardwareRequirements(framework=framework)

        violations = ConstraintEngine(self.compatibility).validate_constraints(
            board_name,
            hardware,
            framework=framework,
        )
        for violation in violations:
            intent_conflict = (
                violation.severity is ConstraintSeverity.ERROR
                and violation.rule_id in {"PF-CONSTRAINT-002", "PF-CONSTRAINT-003", "PF-CONSTRAINT-008", "PF-CONSTRAINT-009", "PF-CONSTRAINT-010", "PF-CONSTRAINT-011"}
            )
            issues.append(
                ProjectValidationIssue(
                    validator="constraint",
                    rule_id=violation.rule_id,
                    severity=violation.severity.value,
                    category=violation.category.value,
                    message=violation.message,
                    recommendation=violation.recommendation,
                    repairable=False,
                    intent_conflict=intent_conflict,
                )
            )

        alternatives: tuple[str, ...] = ()
        if any(issue.intent_conflict for issue in issues):
            alternatives = self.compatibility.get_compatible_boards(
                framework=framework,
                peripheral=tuple(hardware.peripheral_instances),
                library=hardware.libraries,
            )
            alternatives = tuple(item for item in alternatives if item.casefold() != board_name.casefold())
        blocking = tuple(issue for issue in issues if issue.severity in {"ERROR", "CRITICAL"})
        return ProjectValidationReport(not blocking, tuple(issues), alternatives)


def _identifier(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip()


def _constraint_framework(value: object) -> str:
    framework = _identifier(value)
    return "Arduino" if framework == "PlatformIO" else framework


def _project_libraries(project: GeneratedProject) -> tuple[str, ...]:
    platformio = next(
        (
            file.content
            for file in project.files
            if file.path.replace("\\", "/").casefold() == "platformio.ini"
        ),
        None,
    )
    if not platformio:
        return ()
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_file(io.StringIO(platformio))
    except configparser.Error:
        return ()
    values: list[str] = []
    for section in parser.sections():
        raw = parser.get(section, "lib_deps", fallback="")
        for line in re.split(r"[,\n]", raw):
            dependency = line.strip()
            if dependency and not dependency.startswith(("#", ";")):
                values.append(dependency)
    return tuple(dict.fromkeys(values))
