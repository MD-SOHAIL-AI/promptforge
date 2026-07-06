"""Sanitized provider-run evidence with forbidden authority fixed false."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ProviderRunEvidence:
    run_id: str
    provider_id: str
    provider_version: str | None
    transport_mode: str
    sandbox_id: str
    policy_id: str
    confirmation_flag_present: bool
    feature_flags_present: bool
    execution_attempted: bool
    execution_count: int
    created_file_count: int
    modified_file_count: int
    deleted_file_count: int
    active_workspace_unchanged: bool
    marker_unchanged: bool
    classification: str
    review_created: bool = False
    patch_export_run: bool = False
    patch_verify_run: bool = False
    preflight_run: bool = False
    apply_run: bool = False
    build_run: bool = False
    flash_run: bool = False
    raw_prompt_persisted: bool = False
    raw_output_persisted: bool = False
    credential_files_read: bool = False
    safe_summary: str = ""

    def __post_init__(self) -> None:
        if min(self.execution_count, self.created_file_count, self.modified_file_count, self.deleted_file_count) < 0:
            raise ValueError("evidence_count_invalid")
        if self.raw_prompt_persisted or self.raw_output_persisted or self.credential_files_read:
            raise ValueError("sensitive_evidence_forbidden")
        if self.apply_run or self.build_run or self.flash_run:
            raise ValueError("investigation_authority_forbidden")
        if len(self.safe_summary) > 512 or "\n" in self.safe_summary:
            raise ValueError("safe_summary_invalid")

    def to_safe_dict(self) -> dict[str, object]:
        return asdict(self)


class ProviderEvidenceLedger:
    def __init__(self) -> None:
        self._records: dict[str, ProviderRunEvidence] = {}

    def record(self, evidence: ProviderRunEvidence) -> None:
        if evidence.run_id in self._records:
            raise ValueError("evidence_run_duplicate")
        self._records[evidence.run_id] = evidence

    def get(self, run_id: str) -> ProviderRunEvidence:
        return self._records[run_id]

    def list(self) -> tuple[ProviderRunEvidence, ...]:
        return tuple(self._records[key] for key in sorted(self._records))
