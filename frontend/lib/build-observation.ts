export type BuildState = "idle" | "running" | "passed" | "failed" | "unavailable" | "cancelled" | "stale";
export interface BuildObservation {
  state: BuildState;
  projectId: string | null;
  environment: string | null;
  revision: number;
  operationId: string | null;
  observedAt: string | null;
  message?: string;
}
