"use client";

import * as React from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  side?: "top" | "right" | "bottom" | "left";
}

const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, side = "bottom", className, children, type = "button", ...props }, ref) => (
    <Tooltip delayDuration={300}>
      <TooltipTrigger asChild>
        <button
          ref={ref}
          type={type}
          aria-label={label}
          className={cn(
            "inline-grid h-7 w-7 shrink-0 place-items-center rounded-[var(--fx-radius-sm)] text-[var(--fx-text-muted)] outline-none transition-all duration-150 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] focus-visible:ring-2 focus-visible:ring-[var(--fx-accent)] active:translate-y-[0.5px] active:scale-[0.98] disabled:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-50",
            className,
          )}
          {...props}
        >
          {children}
        </button>
      </TooltipTrigger>
      <TooltipContent side={side}>{label}</TooltipContent>
    </Tooltip>
  ),
);
IconButton.displayName = "IconButton";

export { IconButton };
