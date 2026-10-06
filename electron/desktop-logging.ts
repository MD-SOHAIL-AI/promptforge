/** Logging must not interrupt the desktop when its launching terminal closes. */

interface LogSink {
  on(event: "error", listener: (error: unknown) => void): unknown;
  readonly destroyed?: boolean;
  readonly writableEnded?: boolean;
}

interface LogConsole {
  log(...data: unknown[]): void;
  warn(...data: unknown[]): void;
  error(...data: unknown[]): void;
}

function isClosedPipe(error: unknown): boolean {
  if (!error || typeof error !== "object" || !("code" in error)) return false;
  return error.code === "EPIPE"
    || error.code === "ERR_STREAM_DESTROYED"
    || error.code === "ERR_STREAM_WRITE_AFTER_END";
}

function guardOutput(sink: LogSink): (write: () => void) => void {
  let unavailable = false;
  // Pipe errors can arrive after console.log returns, through the stream's
  // error event. Catching only the console call does not cover that case.
  sink.on("error", (error) => {
    if (!isClosedPipe(error)) throw error;
    unavailable = true;
  });
  return (write) => {
    if (unavailable || sink.destroyed || sink.writableEnded) return;
    try {
      write();
    } catch (error) {
      if (!isClosedPipe(error)) throw error;
      unavailable = true;
    }
  };
}

export function createDesktopLogger(options: {
  stdout: LogSink;
  stderr: LogSink;
  console: LogConsole;
}): LogConsole {
  const stdout = guardOutput(options.stdout);
  const stderr = guardOutput(options.stderr);
  return {
    log: (...data) => stdout(() => options.console.log(...data)),
    warn: (...data) => stderr(() => options.console.warn(...data)),
    error: (...data) => stderr(() => options.console.error(...data)),
  };
}

export const desktopLog = createDesktopLogger({
  stdout: process.stdout,
  stderr: process.stderr,
  console,
});
