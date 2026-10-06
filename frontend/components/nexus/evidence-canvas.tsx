"use client";

import { ShieldCheck } from "lucide-react";
import type { ProductAgentRun } from "@/types";
import { CanvasHeader } from "./work-canvases";

export function VerificationEvidence({ run }: { run: ProductAgentRun | null }) {
  const report = run?.requirement_report;
  const reviewed = report?.verification_level === "model_review" && report.valid === true;
  const acceptance = report?.acceptance?.checks ?? [];
  const manifest = run?.build_result?.artifact_manifest;
  const images = manifest && typeof manifest === "object" && "images" in manifest && Array.isArray(manifest.images) ? manifest.images : [];
  const rows = [
    { label: "Source references", status: report?.references_valid ? "Referenced" : "Unverified" },
    { label: "Requirement fidelity", status: reviewed ? "Reviewed" : "Unverified" },
    { label: "Firmware compilation", status: run?.build_verified ? "Built" : "Unverified" },
    { label: "Logic tests", status: acceptance.some(check => check.kind === "unit_tests" && check.passed) ? "Tested" : "Unverified" },
    { label: "Simulation", status: acceptance.some(check => check.kind === "simulation" && check.passed) ? "Tested" : "Unverified" },
    { label: "Device behavior", status: "Unqualified" },
  ];
  return <div className="space-y-4 text-xs">
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3" aria-label="Verification levels">
      {rows.map(row => <div key={row.label}><dt className="text-[var(--fx-text-muted)]">{row.label}</dt><dd className="mt-1 font-medium">{row.status}</dd></div>)}
    </dl>
    <p className="text-[var(--fx-text-muted)]">Source review is a model assessment. Builds, tests, simulation, and physical device qualification provide separate evidence.</p>
    {report?.checks?.length ? <details><summary className="cursor-pointer">Requirement assessments ({report.checks.length})</summary><ul className="mt-3 space-y-3">{report.checks.map(check => <li key={check.id}><strong>{check.id}: {check.status}</strong>{check.review_reason ? <p className="mt-1">{check.review_reason}</p> : null}{check.evidence?.map((reference, index) => <p className="mt-1 break-all font-mono text-[var(--fx-text-muted)]" key={`${reference.path}-${index}`}>{reference.path}: {reference.quote}</p>)}</li>)}</ul></details> : null}
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
