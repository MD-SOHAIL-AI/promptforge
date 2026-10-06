/** Coalesce concurrent reads only; settle before allowing the next fresh read. */
export class SingleFlight {
  private pending = new Map<string, Promise<unknown>>();

  run<T>(key: string, read: () => Promise<T>): Promise<T> {
    const existing = this.pending.get(key);
    if (existing) return existing as Promise<T>;
    const result = Promise.resolve().then(read).finally(() => {
      if (this.pending.get(key) === result) this.pending.delete(key);
    });
    this.pending.set(key, result);
    return result;
  }
}
