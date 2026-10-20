"use client";

import { ShieldCheck } from "lucide-react";
import type { ProductAgentRun } from "@/types";
import { CanvasHeader } from "./work-canvases";
import { verificationLevels } from "@/lib/verification-state";

const evidenceLabels: Record<string, string> = {
  documentation: "Relevant documentation", source: "Implementation source",
  build: "Successful current firmware build", unit_tests: "Successful current unit tests",
  simulation: "Successful current simulation",
};
const issueLabels: Record<string, string> = {
  source_file_required: "Implementation source is required for this requirement.",
  documentation_file_required: "A relevant documentation file is required for this requirement.",
  quote_not_found: "The cited text does not exist in the current file.",
  line_range_invalid: "The cited line range is outside the file or exceeds 80 lines.",
  comment_only: "A comment alone does not establish executable behavior.",
  evidence_missing: "No supporting file reference was submitted.",
  status_not_implemented: "The requirement was reported as unmet.",
  evidence_span_too_large: "The cited span is too large; use a smaller relevant section.",
  build_result_required: "A successful build result for the current source is required.",
  unit_tests_result_required: "Successful unit test results for the current source are required.",
  simulation_result_required: "A successful simulation result for the current source is required.",
};

export function VerificationEvidence({ run }: { run: ProductAgentRun | null }) {
  const report = run?.requirement_report;
  const acceptance = report?.acceptance?.checks ?? [];
  const manifest = run?.build_result?.artifact_manifest;
  const images = manifest && typeof manifest === "object" && "images" in manifest && Array.isArray(manifest.images) ? manifest.images : [];
  const rows = verificationLevels(run);
  return <div className="space-y-4 text-xs">
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3" aria-label="Verification levels">
      {rows.map(row => <div key={row.label}><dt className="text-[var(--fx-text-muted)]">{row.label}</dt><dd className="mt-1 font-medium">{row.status}</dd></div>)}
    </dl>
    <p className="text-[var(--fx-text-muted)]">Source review is a model assessment. Builds, tests, simulation, and physical device qualification provide separate evidence.</p>
    {report?.completion_review?.issues?.length ? <ul aria-label="Completion review findings" className="space-y-2">{report.completion_review.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul> : null}
    {report?.checks?.length ? <details><summary className="cursor-pointer">Requirement assessments ({report.checks.length})</summary><ul className="mt-3 space-y-3">{report.checks.map(check => <li key={check.id}>
      <strong>{check.id}: {check.status}</strong>
      {check.requirement ? <p className="mt-1">{check.requirement}</p> : null}
      {check.expected_evidence?.length ? <p className="mt-1 text-[var(--fx-text-muted)]">Expected: {check.expected_evidence.map(kind => evidenceLabels[kind] ?? kind.replaceAll("_", " ")).join("; ")}.</p> : null}
      {check.issues?.map(issue => <p className="mt-1" key={issue}>{issueLabels[issue] ?? issue.replaceAll("_", " ")}</p>)}
      {(check.rejection_attempts ?? 0) >= 2 ? <p className="mt-1">The same evidence was rejected {check.rejection_attempts} times. Read the matching section before submitting again.</p> : null}
      {check.suggested_sections?.map(section => <p className="mt-1 text-[var(--fx-text-muted)]" key={`${section.path}-${section.start_line}`}>Suggested section: {section.path}, lines {section.start_line}–{section.end_line}: {section.heading}</p>)}
      {check.review_reason ? <p className="mt-1">{check.review_reason}</p> : null}
      {check.evidence?.map((reference, index) => <p className="mt-1 break-all font-mono text-[var(--fx-text-muted)]" key={`${reference.path}-${index}`}>{reference.path}{reference.start_line ? `:${reference.start_line}–${reference.end_line}` : ""}: {reference.quote}</p>)}
    </li>)}</ul></details> : null}
    {acceptance.length ? <details><summary className="cursor-pointer">Acceptance checks</summary><ul className="mt-3 space-y-2">{acceptance.map(check => <li key={check.id}>{check.kind.replaceAll("_", " ")}: {check.status ?? (check.passed ? "Observed" : "Unverified")}. {check.criterion}</li>)}</ul></details> : null}
    {images.length ? <details><summary className="cursor-pointer">Sealed upload images</summary><ul className="mt-3 space-y-2">{images.map((image: unknown, index: number) => {
      if (!image || typeof image !== "object" || !("role" in image) || !("sha256" in image) || !("offset" in image)) return null;
      return <li className="break-all font-mono" key={index}>{String(image.role)} @ 0x{Number(image.offset).toString(16)}<br />{String(image.sha256)}</li>;
    })}</ul></details> : null}
    {!run ? <p>Start a task to collect source, build, and test evidence.</p> : null}
  </div>;
}

export function EvidenceCanvas({ run, onBack }: { run: ProductAgentRun | null; onBack: () => void }) {
  return <section className="flex h-full flex-col"><CanvasHeader icon={ShieldCheck} title="Evidence" subtitle="What was checked for this task" onBack={onBack} /><div className="overflow-y-auto p-5"><VerificationEvidence run={run} /></div></section>;
}
