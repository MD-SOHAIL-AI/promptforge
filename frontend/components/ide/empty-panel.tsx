"use client";

import type { ComponentType } from "react";

interface EmptyPanelProps {
  icon: ComponentType<{ className?: string }>;
  title: string;
  description: string;
}

export function EmptyPanel({ icon: Icon, title, description }: EmptyPanelProps) {
  return (
    <section className="flex h-full min-h-0 flex-col border-r border-[var(--fx-border)] bg-[var(--fx-panel)]">
      <div className="border-b border-[var(--fx-border)] px-3 py-3">
        <p className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">{title}</p>
      </div>
      <div className="flex flex-1 items-center justify-center px-5 text-center">
        <div className="max-w-[220px]">
          <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] text-[var(--fx-info)]">
            <Icon className="h-5 w-5" />
          </div>
          <p className="text-sm font-medium text-[var(--fx-text)]">{title}</p>
          <p className="mt-1 text-xs leading-5 text-[var(--fx-text-muted)]">{description}</p>
        </div>
      </div>
    </section>
  );
}
