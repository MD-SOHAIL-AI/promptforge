"""Exact-output gate between provider evidence and review creation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from .provider_capabilities import ProviderCapabilityEvidence


NATIVE_PASS_CLASSIFICATIONS = frozenset({"NATIVE_WRITE_PASS", "CODEX_NATIVE_WRITE_PASS", "PROVIDER_NATIVE_WRITE_PASS"})
ARTIFACT_PASS_CLASSIFICATIONS = frozenset({"SAFE_ARTIFACT_IMPORT_PASS", "PROVIDER_ARTIFACT_IMPORT_PASS"})


@dataclass(frozen=True, slots=True)
class ReviewOutputFile:
    path: str
    is_symlink_or_reparse: bool = False


@dataclass(frozen=True, slots=True)
class ReviewEligibilityEvidence:
    classification: str
    output_mode: str
    changed_files: tuple[ReviewOutputFile, ...]
    expected_files: tuple[str, ...]
    active_workspace_unchanged: bool
    marker_unchanged: bool
    raw_prompt_persisted: bool = False
    raw_output_persisted: bool = False


@dataclass(frozen=True, slots=True)
class ReviewEligibilityDecision:
    eligible: bool
    failure_code: str | None = None


def evaluate_review_eligibility(
    provider: ProviderCapabilityEvidence,
    evidence: ReviewEligibilityEvidence,
) -> ReviewEligibilityDecision:
    if not provider.review_eligible:
        return ReviewEligibilityDecision(False, "PROVIDER_NOT_REVIEW_ELIGIBLE")
    expected_classifications = NATIVE_PASS_CLASSIFICATIONS if evidence.output_mode == "native_write" else ARTIFACT_PASS_CLASSIFICATIONS
    if evidence.classification not in expected_classifications:
        return ReviewEligibilityDecision(False, "PROVIDER_NATIVE_WRITE_UNPROVEN" if evidence.output_mode == "native_write" else "PROVIDER_ARTIFACT_UNRELIABLE")
    if not evidence.active_workspace_unchanged or not evidence.marker_unchanged:
        return ReviewEligibilityDecision(False, "PROVIDER_NOT_REVIEW_ELIGIBLE")
    if evidence.raw_prompt_persisted or evidence.raw_output_persisted:
        return ReviewEligibilityDecision(False, "PROVIDER_NOT_REVIEW_ELIGIBLE")
    changed_paths = tuple(item.path for item in evidence.changed_files)
    if changed_paths != evidence.expected_files or len(set(changed_paths)) != len(changed_paths):
        return ReviewEligibilityDecision(False, "PROVIDER_NOT_REVIEW_ELIGIBLE")
    for item in evidence.changed_files:
        if item.is_symlink_or_reparse or not _safe_relative_path(item.path):
            return ReviewEligibilityDecision(False, "PROVIDER_NOT_REVIEW_ELIGIBLE")
    return ReviewEligibilityDecision(True)


def _safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/") or normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        return False
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    return not posix.is_absolute() and not windows.is_absolute() and not windows.drive
