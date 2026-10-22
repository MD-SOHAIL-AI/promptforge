/** Process recovery is bounded; it grants no task or hardware replay authority. */
export class BackendRecoveryPolicy {
  private attempts: number[] = [];
  constructor(private readonly delays: readonly number[] = [2000, 4000, 8000], private readonly windowMs = 120_000) {}
  nextDelay(now = Date.now()): number | null {
    this.attempts = this.attempts.filter(value => now - value < this.windowMs);
    if (this.attempts.length >= this.delays.length) return null;
    const delay = this.delays[this.attempts.length];
    this.attempts.push(now);
    return delay;
  }
  get used(): number { return this.attempts.length; }
}
