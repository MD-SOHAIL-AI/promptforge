"use client";

import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Kbd({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded-[4px] border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 py-0.5 font-mono text-[11px] leading-none text-[var(--fx-text-muted)]",
        className,
      )}
      {...props}
    />
  );
}
