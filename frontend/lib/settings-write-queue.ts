/** Coalesces pending preferences while serializing writes of complete server snapshots. */
export class SettingsWriteQueue<T extends Record<string, unknown>> {
  private pending: Partial<T> = {};
  private waiters: Array<{ resolve: (value: T) => void; reject: (error: unknown) => void }> = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private running = false;

  private readonly write: (patch: Partial<T>) => Promise<T>;
  private readonly delay: number;
  constructor(write: (patch: Partial<T>) => Promise<T>, delay = 120) {
    this.write = write;
    this.delay = delay;
  }

  enqueue(patch: Partial<T>): Promise<T> {
    Object.assign(this.pending, patch);
    const result = new Promise<T>((resolve, reject) => this.waiters.push({ resolve, reject }));
    if (!this.running && !this.timer) this.timer = setTimeout(() => void this.drain(), this.delay);
    return result;
  }

  private async drain() {
    this.timer = null;
    if (this.running || !this.waiters.length) return;
    this.running = true;
    const patch = this.pending;
    const waiters = this.waiters;
    this.pending = {};
    this.waiters = [];
    try {
      const value = await this.write(patch);
      for (const waiter of waiters) waiter.resolve(value);
    } catch (error) {
      for (const waiter of waiters) waiter.reject(error);
    } finally {
      this.running = false;
      if (this.waiters.length) this.timer = setTimeout(() => void this.drain(), this.delay);
    }
  }
}
