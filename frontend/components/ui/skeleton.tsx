"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden
      className={cn("relative overflow-hidden rounded-[var(--fx-radius-sm)] bg-[var(--fx-hover)]", className)}
      {...props}
    >
      <div className="fx-shimmer absolute inset-y-0 left-0 w-1/3 bg-gradient-to-r from-transparent via-[rgba(255,255,255,0.06)] to-transparent" />
    </div>
  );
}
