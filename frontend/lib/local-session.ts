/** Authentication loss is sticky until an explicit authenticated reconnection. */
export class LocalSessionState {
  private blocked = false;
  private listeners = new Set<() => void>();

  getSnapshot = (): boolean => this.blocked;
  getServerSnapshot = (): boolean => false;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  reject(): void { this.update(true); }
  restore(): void { this.update(false); }
  private update(blocked: boolean): void {
    if (this.blocked === blocked) return;
    this.blocked = blocked;
    for (const listener of this.listeners) listener();
  }
}

export const localSession = new LocalSessionState();
