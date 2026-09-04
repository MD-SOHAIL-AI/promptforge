"use client";

import { createElement, isValidElement } from "react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type EmptyStateIcon = LucideIcon | ReactNode;

export interface EmptyStateProps {
  icon?: EmptyStateIcon;
  title: string;
  hint?: string;
  action?: ReactNode;
  className?: string;
}

function renderIcon(icon: EmptyStateIcon): ReactNode {
  if (isValidElement(icon)) return icon;
  return createElement(icon as unknown as LucideIcon, { className: "h-5 w-5" });
}

export function EmptyState({ icon, title, hint, action, className }: EmptyStateProps) {
  return (
    <div className={cn("flex w-full items-center justify-center p-6", className)}>
      <div className="flex max-w-sm flex-col items-center gap-3 rounded-[var(--fx-radius)] border border-dashed border-[var(--fx-border)] bg-[color-mix(in_srgb,var(--fx-panel)_55%,transparent)] px-6 py-8 text-center">
        {icon ? (
          <div className="grid h-10 w-10 place-items-center rounded-[var(--fx-radius-sm)] bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]">
            {renderIcon(icon)}
          </div>
        ) : null}
        <div className="space-y-1">
          <p className="text-[13px] font-semibold text-[var(--fx-text)]">{title}</p>
          {hint ? <p className="text-xs leading-5 text-[var(--fx-text-muted)]">{hint}</p> : null}
        </div>
        {action ? <div className="pt-1">{action}</div> : null}
      </div>
    </div>
  );
}
