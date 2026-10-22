"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

import { toErrorMessage } from "@/lib/errors";

interface IdeErrorBoundaryProps {
  children: ReactNode;
}

interface IdeErrorBoundaryState {
  error: unknown;
}

export class IdeErrorBoundary extends Component<IdeErrorBoundaryProps, IdeErrorBoundaryState> {
  state: IdeErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): IdeErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("ForgeX UI failed to render", error, info.componentStack);
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <main className="flex h-dvh min-w-0 items-center justify-center bg-[var(--fx-bg)] p-6 text-[var(--fx-text)]">
        <section className="w-full max-w-xl rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-5">
          <h1 className="text-lg font-semibold">ForgeX runtime error</h1>
          <p className="mt-3 break-words text-sm text-[var(--fx-error)]">
            {toErrorMessage(this.state.error, "Unknown runtime error")}
          </p>
          <button
            className="mt-4 rounded bg-[var(--fx-accent)] px-3 py-2 text-sm font-medium text-white hover:opacity-90"
            onClick={() => window.location.reload()}
          >
            Reload ForgeX
          </button>
        </section>
      </main>
    );
  }
}
