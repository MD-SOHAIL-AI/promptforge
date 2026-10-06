export class RequestDeadlineError extends Error {
  readonly mutation: boolean;
  constructor(mutation: boolean) {
    super(mutation
      ? "The request timed out. Its result is unknown; refresh the saved operation before retrying."
      : "The request timed out. Saved work is preserved; reconnect to refresh its status.");
    this.name = "RequestDeadlineError";
    this.mutation = mutation;
  }
}

/** Bounds headers and body consumption, even when a transport ignores abort. */
export async function withRequestDeadline<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  options: { timeoutMs: number; signal?: AbortSignal | null; mutation?: boolean },
): Promise<T> {
  if (options.signal?.aborted) throw new DOMException("Request cancelled", "AbortError");
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel: () => void = () => {};
  const deadline = new Promise<never>((_, reject) => {
    cancel = () => {
      reject(new DOMException("Request cancelled", "AbortError"));
      controller.abort();
    };
    if (options.signal?.aborted) { cancel(); return; }
    options.signal?.addEventListener("abort", cancel, { once: true });
    timer = setTimeout(() => {
      reject(new RequestDeadlineError(options.mutation === true));
      controller.abort();
    }, options.timeoutMs);
  });
  try { return await Promise.race([deadline, operation(controller.signal)]); }
  finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", cancel);
  }
}
