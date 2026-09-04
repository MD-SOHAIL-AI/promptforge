"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  invalid?: boolean;
};

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, invalid, ...props }, ref) => (
    <textarea
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        "min-h-[72px] w-full resize-y rounded-[var(--fx-radius-sm)] border border-[var(--fx-border)] bg-[var(--fx-input)] px-2.5 py-2 text-[13px] leading-5 text-[var(--fx-text)] outline-none transition-colors placeholder:text-[var(--fx-text-muted)] hover:border-[color-mix(in_srgb,var(--fx-border)_60%,var(--fx-accent))] focus-visible:border-[var(--fx-accent)] focus-visible:ring-2 focus-visible:ring-[color-mix(in_srgb,var(--fx-accent)_28%,transparent)] disabled:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-50 [aria-invalid=true]:border-[var(--fx-error)] [aria-invalid=true]:ring-[color-mix(in_srgb,var(--fx-error)_25%,transparent)]",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";

export { Textarea };
