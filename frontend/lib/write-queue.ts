/** Serialize writes to each resource while allowing unrelated files to save. */
export class WriteQueue {
  private pending = new Map<string, Promise<unknown>>();

  run<T>(key: string, write: () => Promise<T>): Promise<T> {
    const previous = this.pending.get(key) ?? Promise.resolve();
    const result = previous.catch(() => undefined).then(write).finally(() => {
      if (this.pending.get(key) === result) this.pending.delete(key);
    });
    this.pending.set(key, result);
    return result;
  }
}
