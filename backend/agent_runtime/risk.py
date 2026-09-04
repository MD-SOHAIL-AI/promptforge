"""Deterministic risk and review decisions for agent-produced ChangeSets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from backend.changes import ChangeSet


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: str
    score: int
    reasons: tuple[str, ...]
    reviewer_required: bool
    user_approval_required: bool
    scoped_auto_apply_allowed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "score": self.score,
            "reasons": list(self.reasons),
            "reviewer_required": self.reviewer_required,
            "user_approval_required": self.user_approval_required,
            "scoped_auto_apply_allowed": self.scoped_auto_apply_allowed,
        }


class RiskAssessmentService:
    """Classify the final diff; reviewer use is based on evidence, not role names."""

    _HIGH_RISK_NAMES = frozenset({
        "platformio.ini",
        "partitions.csv",
        "sdkconfig",
        "cmakelists.txt",
        "library.json",
        "library.properties",
    })
    _SECURITY_MARKERS = ("credential", "secret", "password", "token", "certificate", "private_key", "secure_boot")
    _HARDWARE_MARKERS = ("gpio", "pin", "partition", "bootloader", "fuse", "upload_port", "board_build")

    def assess(
        self,
        change_set: ChangeSet,
        *,
        explicit_edit_authorized: bool,
        planner_confidence: float = 1.0,
        validation_failed: bool = False,
        repair_attempt: int = 0,
        user_requested_review: bool = False,
    ) -> RiskAssessment:
        score = 0
        reasons: list[str] = []
        paths = [PurePosixPath(item.path) for item in change_set.changed_files]
        if len(paths) > 5:
            score += 2
            reasons.append("broad_file_change")
        if any(item.change_type == "deleted" for item in change_set.changed_files):
            score += 4
            reasons.append("file_deletion")
        if any(path.name.casefold() in self._HIGH_RISK_NAMES for path in paths):
            score += 3
            reasons.append("build_or_board_configuration")
        combined = "\n".join((item.diff_preview or "").casefold() for item in change_set.changed_files)
        if any(marker in combined for marker in self._SECURITY_MARKERS):
            score += 4
            reasons.append("security_sensitive_change")
        if any(marker in combined for marker in self._HARDWARE_MARKERS):
            score += 3
            reasons.append("hardware_configuration_change")
        if sum(len(item.diff_preview or "") for item in change_set.changed_files) > 8_000:
            score += 2
            reasons.append("large_diff")
        if planner_confidence < 0.7:
            score += 2
            reasons.append("low_planner_confidence")
        if validation_failed:
            score += 4
            reasons.append("validation_failed")
        if repair_attempt:
            score += min(3, repair_attempt)
            reasons.append("repair_after_failure")
        if user_requested_review:
            score = max(score, 3)
            reasons.append("user_requested_review")

        level = "high" if score >= 6 else "medium" if score >= 3 else "low"
        reviewer_required = level in {"medium", "high"}
        approval_required = level == "high" or not explicit_edit_authorized
        auto_apply = explicit_edit_authorized and level == "low" and not validation_failed
        if not reasons:
            reasons.append("localized_validated_edit")
        return RiskAssessment(
            level=level,
            score=score,
            reasons=tuple(dict.fromkeys(reasons)),
            reviewer_required=reviewer_required,
            user_approval_required=approval_required,
            scoped_auto_apply_allowed=auto_apply,
        )


def paths_in_scope(change_set: ChangeSet, allowed_prefixes: Iterable[str]) -> bool:
    prefixes = tuple(prefix.strip("/") for prefix in allowed_prefixes if prefix.strip("/"))
    if not prefixes:
        return True
    return all(any(item.path == prefix or item.path.startswith(prefix + "/") for prefix in prefixes) for item in change_set.changed_files)
