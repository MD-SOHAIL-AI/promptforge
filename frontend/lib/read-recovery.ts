/** Retry transient observation failures within the caller's existing deadline. */
export async function recoverRead<T>(method: string, read: () => Promise<T>, options: {
  signal: AbortSignal;
  onRetry?: (attempt: number, cause: unknown) => void;
  delayMs?: number;
}): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    if (options.signal.aborted) throw options.signal.reason ?? new DOMException("Request cancelled", "AbortError");
    try { return await read(); }
    catch (error) {
      const code = error && typeof error === "object" && "code" in error ? error.code : undefined;
      if (method.toUpperCase() !== "GET" || attempt >= 2 || options.signal.aborted
        || !(code === "BACKEND_UNAVAILABLE" || error instanceof TypeError)) throw error;
      options.onRetry?.(attempt + 1, error);
      await new Promise<void>((resolve, reject) => {
        const abort = () => {
          clearTimeout(timer);
          options.signal.removeEventListener("abort", abort);
          reject(options.signal.reason ?? new DOMException("Request cancelled", "AbortError"));
        };
        const timer = setTimeout(() => { options.signal.removeEventListener("abort", abort); resolve(); },
          (options.delayMs ?? 250) * (attempt + 1));
        options.signal.addEventListener("abort", abort, {once: true});
        if (options.signal.aborted) abort();
      });
    }
  }
}
