/** Recover renderer crashes without disturbing the backend's durable jobs. */
export class RendererRecoveryCircuitBreaker {
  private attempts: number[] = [];

  register(now: number): boolean {
    this.attempts = this.attempts.filter((time) => time > now - 60_000);
    if (this.attempts.length >= 3) return false;
    this.attempts.push(now);
    return true;
  }

  reset(): void { this.attempts = []; }
}

type RecoveryContents = Pick<import("electron").WebContents, "on" | "reload" | "getURL" | "isDestroyed" | "loadURL">;

export function attachRendererRecovery(contents: RecoveryContents, report: (event: string) => void, now = Date.now): void {
  const breaker = new RendererRecoveryCircuitBreaker();
  let stopped = false;
  let lastUrl = "";
  contents.on("did-finish-load", () => {
    if (!stopped) lastUrl = contents.getURL();
  });
  contents.on("will-navigate", (_event, target: string) => {
    if (stopped && target === lastUrl) {
      breaker.reset();
      stopped = false;
    }
  });
  contents.on("render-process-gone", (_event, details: {reason: string}) => {
    if (stopped || contents.isDestroyed() || !["crashed", "oom", "abnormal-exit"].includes(details.reason)) return;
    if (breaker.register(now())) {
      report("desktop.renderer.recovering");
      try {
        contents.reload();
        return;
      } catch {
        report("desktop.renderer.recovery_reload_failed");
      }
    }
    stopped = true;
    report("desktop.renderer.recovery_blocked");
    const url = lastUrl || contents.getURL();
    const escaped = url.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
    const html = `<!doctype html><html lang="en"><meta charset="utf-8"><title>ForgeX recovery</title>
      <body style="background:#0d1117;color:#e6edf3;font:16px system-ui;padding:48px">
      <h1>ForgeX could not restore this window.</h1><p>Your task history and saved drafts remain available.</p>
      <p>The window stopped reloading after repeated crashes.</p><a style="color:#79c0ff" href="${escaped}">Reload ForgeX</a></body></html>`;
    void contents.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`).catch(() => report("desktop.renderer.recovery_screen_failed"));
  });
}
