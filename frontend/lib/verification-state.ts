import type { ProductAgentRun } from "@/types";

export function verificationLevels(run: ProductAgentRun | null) {
  const report = run?.requirement_report;
  const acceptance = report?.acceptance?.checks ?? [];
  const requested = run?.task_state?.acceptance_checks;
  const complex = report?.completion_review_required ?? ((run?.task_state?.requirements?.length ?? report?.checks?.length ?? 0) >= 8);
  const completion = report?.completion_review;
  const risk = report?.risk_review;
  const completionStatus = completion?.passed ? "Reviewed"
    : completion?.failure_code ? "Could not finish"
    : completion ? "Issues found" : complex ? "Not started" : "Not requested";
  const requestedKind = (kind: string) => acceptance.some(check => check.kind === kind)
    || (Array.isArray(requested) && requested.some(check => check?.kind === kind));
  const executionStatus = (kind: string) => acceptance.some(check => check.kind === kind && check.passed)
    ? "Tested" : requestedKind(kind) ? "Unverified" : "Not requested";
  return [
    { label: "Requirement references", status: report?.references_valid ? "Referenced" : report?.checks?.length ? "Incomplete" : "Not started" },
    { label: "Requirement fidelity", status: report?.verification_level === "model_review" && report.valid ? "Reviewed" : report?.semantic_review ? "Issues found" : "Not started" },
    { label: "Completion review", status: completionStatus },
    { label: "Change risk review", status: risk?.verdict === "approve" && !risk.failure_code ? "Reviewed"
      : risk ? "Needs review" : "Not requested" },
    { label: "Firmware compilation", status: run?.build_verified ? "Built" : run?.build_result?.verification_stale ? "Stale" : run?.build_result?.success === false ? "Failed" : "Not started" },
    { label: "Logic tests", status: executionStatus("unit_tests") },
    { label: "Simulation", status: executionStatus("simulation") },
    { label: "Device behavior", status: "Not tested" },
  ];
}

export function verificationFailureMessage(run?: ProductAgentRun): string | null {
  if (run?.classification === "CHANGESET_REVIEW_REQUIRED") {
    return run.requirement_report?.risk_review?.failure_code
      ? "Automatic change-risk review could not finish. Inspect its diagnostics and review the staged changes before explicit approval."
      : "Change-risk review requires attention. Inspect the findings before explicit approval.";
  }
  if (run?.classification === "REQUIREMENTS_UNVERIFIED") {
    return run.requirement_report?.references_valid
      ? "Requirement review found unmet or insufficiently supported requirements. Inspect the review findings."
      : "Requirement citation validation failed. Correct the missing or rejected references in the saved draft.";
  }
  if (run?.classification === "AGENT_REVIEW_REQUIRED") {
    if (run.requirement_report?.completion_review?.failure_code) {
      return "The completion review could not finish. Inspect the response diagnostics, then continue the saved draft.";
    }
    return run.requirement_report?.completion_review
      ? "The completion review did not pass. Inspect its findings before continuing the saved draft."
      : "The independent requirement review could not pass. Inspect the review findings and response diagnostics.";
  }
  return null;
}
