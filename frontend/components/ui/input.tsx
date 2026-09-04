"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement> & {
  invalid?: boolean;
};

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, invalid, type, ...props }, ref) => (
    <input
      ref={ref}
      type={type}
      aria-invalid={invalid || undefined}
      className={cn(
        "h-8 w-full min-w-0 rounded-[var(--fx-radius-sm)] border border-[var(--fx-border)] bg-[var(--fx-input)] px-2.5 text-[13px] text-[var(--fx-text)] outline-none transition-colors placeholder:text-[var(--fx-text-muted)] hover:border-[color-mix(in_srgb,var(--fx-border)_60%,var(--fx-accent))] focus-visible:border-[var(--fx-accent)] focus-visible:ring-2 focus-visible:ring-[color-mix(in_srgb,var(--fx-accent)_28%,transparent)] disabled:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-50 [aria-invalid=true]:border-[var(--fx-error)] [aria-invalid=true]:ring-[color-mix(in_srgb,var(--fx-error)_25%,transparent)]",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";

export { Input };
