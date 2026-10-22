"use client";
import {useEffect, useRef, useState} from "react";
import type {AgentJob, ProductAgentRun} from "@/types";
import {promptForgeApi} from "@/lib/api";
import {downloadJson} from "@/lib/download-json";
import {verificationFailureMessage} from "@/lib/verification-state";
import {configurationRepairMessage} from "@/lib/agent-recovery";

const BUDGET_LABELS: Readonly<Record<string, string>> = {output_tokens:"model response allowance", input_bytes:"cumulative model input allowance", requests:"model API requests", tool_calls_used:"tool calls", elapsed_seconds:"active work time"};
const PHASE_LABELS = {configuration: "Checking project configuration", generation: "Generating project", repair: "Repairing build", verification: "Verifying requirements"};

export function AgentJobControls({job, recovery, disabled, onAction, run, selection, onResume, onInspect, onVerify, onRefresh}: {job: AgentJob; recovery?: ProductAgentRun["recovery"]; disabled: boolean; onAction: (action: "pause" | "resume" | "budget" | "recovery-policy", policy?: "manual" | "safe_auto") => Promise<void>; run?: ProductAgentRun; selection?: {provider_id: string; model_id: string}; onResume?: (selection?: {provider_id: string; model_id: string}) => Promise<unknown>; onInspect?: () => void; onVerify?: () => Promise<unknown>; onRefresh?: () => Promise<void>}) {
  const [trusted, setTrusted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savingTrust, setSavingTrust] = useState(false);
  const [exporting, setExporting] = useState(false);
  const projectRef = useRef(job.project_id);
  const trustRevision = useRef(0);
  projectRef.current = job.project_id;
  useEffect(() => {
    let active = true;
    const revision = ++trustRevision.current;
    setTrusted(false);
    setError(null);
    void promptForgeApi.projectAgentTrust(job.project_id).then(result => {if (active && trustRevision.current === revision) setTrusted(result.trusted);}).catch(() => {if (active && trustRevision.current === revision) setError("Project trust status could not be loaded.");});
    return () => {active = false;};
  }, [job.project_id]);
  const paused = ["paused", "interrupted", "blocked", "paused_model_unavailable", "failed", "timed_out"].includes(job.status);
  const modelRetired = run?.classification === "MODEL_RETIRED";
  const budgetPaused = job.paused_by === "budget";
  const continuation = recovery?.actions?.continue_generation;
  const verification = recovery?.actions?.verify_locally;
  const needsBuildPermission = verification?.blocking_reason === "AGENT_PROJECT_TRUST_REQUIRED" || job.blocking_reason === "AGENT_PROJECT_TRUST_REQUIRED";
  const canContinue = continuation?.allowed ?? (recovery?.eligible === true && !budgetPaused);
  const configurationFailure = configurationRepairMessage(run);
  const verificationReserve = job.blocking_reason === "AGENT_VERIFICATION_RESERVE_REACHED";
  const workerFailure = run?.classification === "MODEL_WORKER_FAILED";
  const canVerify = !configurationFailure && (verification?.allowed ?? (recovery?.eligible === true && trusted && job.budget.remaining.elapsed_seconds > 5 && job.budget.remaining.tool_calls_used > 0));
  const workspaceUnavailable = (recovery?.blocking_reason ?? job.blocking_reason) === "AGENT_RESUME_WORKSPACE_UNAVAILABLE";
  const exhausted = Object.entries(job.budget.remaining).filter(([, remaining]) => remaining <= 0).map(([key]) => BUDGET_LABELS[key] ?? key);
  const lowAllowances = Object.entries(job.budget.remaining).filter(([key, remaining]) => remaining > 0 && remaining <= job.budget.limits[key] * 0.1).map(([key]) => BUDGET_LABELS[key] ?? key);
  const verificationFailure = verificationFailureMessage(run);
  const execution = run?.task_state?.execution;
  return <div className="fx-approval" style={{flexDirection:"column", alignItems:"stretch"}} aria-label="Saved task controls">
    <div>
      <strong>{paused ? "Task saved" : "Task progress"} · {job.execution_profile === "advanced" ? "Advanced" : "Standard"}</strong>
      <div className="fx-recovery-facts">
        <span><b>Current state</b>{verificationReserve ? "Verification allowance preserved" : budgetPaused ? "Budget extension required" : needsBuildPermission ? "Local build permission required" : job.status === "paused_model_unavailable" ? "Model response interrupted" : job.status.replaceAll("_", " ")}</span>
        <span><b>Saved work</b>{run?.change_set_id ? "Draft available" : "Checkpoint preserved"}</span>
        <span><b>Next action</b>{budgetPaused && !verificationReserve ? "Add task budget" : needsBuildPermission ? "Allow local builds, then build the saved draft" : configurationFailure ? "Continue generation to repair platformio.ini" : run?.build_verified && run.change_set_id ? "Review task requirements and saved changes" : canVerify && run?.change_set_id ? "Build saved draft" : canContinue ? "Continue generation" : "Inspect recovery details"}</span>
      </div>
      {run && <p>Selected model: {run.provider_id} / {run.model_id ?? "provider default"}.</p>}
      {execution?.version === 1 && <div aria-label="Task completion status">
        <p><strong>{PHASE_LABELS[execution.phase] ?? "Task progress"}</strong>{typeof execution.active_milestone?.text === "string" ? ` · ${execution.active_milestone.text}` : ""}</p>
        {!!execution.missing_artifacts?.length && <p role="status">Files still needed: {execution.missing_artifacts.slice(0, 5).join(", ")}{execution.missing_artifacts.length > 5 ? ` and ${execution.missing_artifacts.length - 5} more` : ""}.</p>}
        {!!execution.blocking_symbols?.length && <p role="status">Build needs these implementations: {execution.blocking_symbols.slice(0, 5).join(", ")}{execution.blocking_symbols.length > 5 ? " …" : ""}.</p>}
        {!!execution.remaining_requirement_ids?.length && <p>{execution.remaining_requirement_ids.length} requirement{execution.remaining_requirement_ids.length === 1 ? "" : "s"} still need independent acceptance.</p>}
        {run?.task_state?.next_action && <p>Next action: {run.task_state.next_action}</p>}
      </div>}
      {configurationFailure && <p role="status">{configurationFailure}</p>}
      {workspaceUnavailable && <p role="status">The original project folder is missing or cannot be opened. Restore that folder to continue this saved task, or open an existing folder to start a new project. Your task history and sealed checkpoint are preserved.</p>}
      {job.recovery && !configurationFailure && !workspaceUnavailable && <p role="status">{job.blocking_reason === "AGENT_PROJECT_TRUST_REQUIRED" ? "Automatic recovery is waiting for project build permission." : job.recovery.http_status === 503 ? "The model service is temporarily unavailable." : "The selected model could not finish its response."} {job.recovery.phase === "retrying" ? `Request attempt ${job.recovery.attempt} of ${job.recovery.max_attempts}.` : `Automatic recovery cycles used: ${job.recovery.attempt} of ${job.recovery.max_attempts}.`} Your settled work is preserved.</p>}
      {job.recovery?.outage_deadline_at && !configurationFailure && !workspaceUnavailable && <p>Automatic outage recovery ends at {new Date(job.recovery.outage_deadline_at).toLocaleTimeString()}. You can then continue manually or select another configured model.</p>}
      {verificationFailure && <p role="status">{verificationFailure}</p>}
      {run?.provider_diagnostics && !configurationFailure && !verificationFailure && !workspaceUnavailable && !needsBuildPermission && ["failed", "timed_out", "paused_model_unavailable"].includes(run.status) && <p>Failure origin: {job.recovery?.origin ?? run.failure?.cause_origin ?? run.failure?.category ?? run.provider_diagnostics.last_failure_origin ?? "ForgeX runtime"}. {run.provider_diagnostics.http_status ? `HTTP ${run.provider_diagnostics.http_status}. ` : ""}{run.provider_diagnostics.request_reached_provider ? "A request reached the provider." : "No provider response was received; check the request diagnostics."}</p>}
      {lowAllowances.length > 0 && !budgetPaused && <p role="status">Approaching the task limit: {lowAllowances.join(", ")}. The task will pause when an allowance cannot cover more work.</p>}
      <p>{job.budget.remaining.tool_calls_used} tool calls and {job.budget.remaining.requests} model API requests remaining. {Math.ceil(job.budget.remaining.elapsed_seconds / 60)} minutes of active runtime remaining. Requests and short waits count; durable outage cooldowns are tracked separately.</p>
      {!!job.durable_wait_seconds && <p>{Math.ceil(job.durable_wait_seconds)} seconds spent in durable outage cooldowns.</p>}
      {run?.change_set_id && <p>{run.build_verified ? "The saved draft compiled. Requirement review and hardware verification remain separate." : "Saved changes require verification before completion."}</p>}
      {paused && run?.task_state?.next_action && <p>Next step: {run.task_state.next_action}</p>}
      {verification?.blocking_reason === "AGENT_PROJECT_TRUST_REQUIRED" && <p role="status">Trust this project for local builds and tests before building the saved draft. You can inspect saved work or continue generation.</p>}
      {verification?.blocking_reason === "AGENT_RECOVERY_DRAFT_INCOMPLETE" && !configurationFailure && <p role="status">The saved draft has configuration but no firmware source. Continue generation before building it.</p>}
      {run?.provider_diagnostics?.timeout_stage && <p>The model request stopped during {run.provider_diagnostics.timeout_stage.replaceAll("_", " ")}. {run.provider_diagnostics.first_event_ms != null ? "The model had started responding." : "No meaningful model output was recorded."}</p>}
      {recovery && !recovery.eligible && !workspaceUnavailable && <p>Continuation blocked: {recovery.blocking_reason?.replaceAll("_", " ").toLowerCase() ?? "a safe checkpoint is unavailable"}.</p>}
      {typeof job.budget.remaining.output_tokens === "number" && <p>{job.budget.remaining.output_tokens.toLocaleString()} output tokens available for model responses. Each attempt reserves its maximum response size; confirmed completed usage releases unused allowance.</p>}
      {typeof job.budget.remaining.input_bytes === "number" && <p>{(job.budget.remaining.input_bytes / 1_000_000).toFixed(2)} MB of cumulative model input allowance remaining.</p>}
      {workerFailure && <p role="status">ForgeX’s local model process stopped. Saved work is preserved. Reopen ForgeX, then continue the saved task.</p>}
      {budgetPaused ? <p role="status">{verificationReserve ? "Generation paused to preserve verification time. Build the saved draft, or add task budget to continue generation." : exhausted.length ? `Task paused: ${exhausted.join(", ")} exhausted. Add budget before continuing.` : "Task paused: the remaining allowance cannot cover another attempt. Add budget before continuing."} Your saved work is preserved.</p> : job.blocking_reason && !workspaceUnavailable && <p>Recovery: {job.blocking_reason.replaceAll("_", " ").toLowerCase()}</p>}
      {budgetPaused && <p>Adds 15 minutes, 100 tool calls, 120 model requests, 245,760 output tokens and 8 MB of model context, up to the task limit.</p>}
      {job.retry_due_at && job.status === "paused_model_unavailable" ? <p>The selected model can be retried after {new Date(job.retry_due_at).toLocaleTimeString()}. The task budget applies to retries.</p> : null}
      <details className="fx-recovery-diagnostics"><summary>Request diagnostics</summary>
      {run?.provider_diagnostics && <dl className="fx-diagnostics-grid">{Object.entries({"Attempt":run.provider_diagnostics.physical_attempt_id, "Headers":run.provider_diagnostics.response_headers_received ? "Received" : "Unavailable", "First output":run.provider_diagnostics.first_event_ms == null ? "Unavailable" : `${run.provider_diagnostics.first_event_ms} ms`, "Last output":run.provider_diagnostics.last_event_ms == null ? "Unavailable" : `${run.provider_diagnostics.last_event_ms} ms`, "Events":run.provider_diagnostics.stream_event_count, "Completion":run.provider_diagnostics.completion_received ? "Received" : "Missing", "Timeout stage":run.provider_diagnostics.timeout_stage ?? "None"}).map(([name,value]) => <div key={name}><dt>{name}</dt><dd>{String(value ?? "Unavailable")}</dd></div>)}</dl>}
      </details>
      <label className="flex gap-2"><input type="checkbox" checked={trusted} disabled={disabled || savingTrust} onChange={async event => {
        const project = job.project_id;
        const previous = trusted;
        const next = event.target.checked;
        ++trustRevision.current;
        setTrusted(next);
        setSavingTrust(true);
        try {const result = await promptForgeApi.setProjectAgentTrust(project, next); if(projectRef.current === project) {setTrusted(result.trusted); setError(null); if (onRefresh) await onRefresh().catch(() => setError("Project permission saved; recovery status will refresh shortly."));}}
        catch {if(projectRef.current === project) {setTrusted(previous); setError("Project trust could not be saved.");}}
        finally {setSavingTrust(false);}
      }}/>I trust this project for local builds and tests.</label>
      <label className="flex gap-2"><input type="checkbox" checked={job.recovery_policy === "safe_auto"} disabled={disabled} onChange={event => void onAction("recovery-policy",event.target.checked ? "safe_auto" : "manual")}/>Resume eligible work when ForgeX reopens.</label>
      {error && <p role="alert">{error}</p>}
    </div>
    <div className="flex flex-wrap gap-2">
      {job.status === "running" && <button className="fx-suggestion" disabled={disabled} onClick={() => void onAction("pause")}>Pause task</button>}
      {paused && <button className="fx-suggestion" disabled={disabled || !canContinue || modelRetired} onClick={() => void (onResume ? onResume() : onAction("resume"))}>{configurationFailure || verificationReserve ? "Continue generation" : "Retry selected model"}</button>}
      {paused && selection && onResume && <button className="fx-suggestion" disabled={disabled || !canContinue} onClick={() => void onResume(selection)}>Continue with {selection.provider_id} / {selection.model_id}</button>}
      {paused && !selection && <p>Select another configured model in the task composer to continue with it.</p>}
      {paused && onInspect && <button className="fx-suggestion" disabled={disabled || recovery?.actions?.inspect.allowed === false} onClick={onInspect}>Inspect saved work</button>}
      {paused && run?.change_set_id && onVerify && <button className="fx-suggestion" disabled={disabled || !canVerify} onClick={() => void onVerify()}>Build saved draft</button>}
      {budgetPaused && <button className="fx-suggestion" disabled={disabled} onClick={() => void onAction("budget")}>Add task budget</button>}
      {run && <button className="fx-suggestion" disabled={exporting} onClick={async () => {
        setExporting(true);
        try {downloadJson(`forgex-${run.run_id.replace(/[^a-zA-Z0-9_-]/g, "")}-diagnostics.json`, await promptForgeApi.runDiagnostics(run.run_id)); setError(null);}
        catch {setError("Request diagnostics could not be exported. Saved work is preserved.");}
        finally {setExporting(false);}
      }}>{exporting ? "Exporting…" : "Export request diagnostics"}</button>}
    </div>
  </div>;
}
