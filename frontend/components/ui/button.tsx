"use client";

import * as React from "react";
import { Loader2 } from "lucide-react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "relative inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap rounded-[var(--fx-radius-sm)] font-medium leading-none outline-none transition-all duration-150 focus-visible:ring-2 focus-visible:ring-[var(--fx-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--fx-bg)] active:translate-y-[0.5px] active:scale-[0.98] disabled:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-50",
  {
    variants: {
      variant: {
        default:
          "border border-transparent bg-[linear-gradient(135deg,var(--fx-accent),color-mix(in_srgb,var(--fx-accent)_62%,var(--fx-accent-secondary)))] text-[#03151b] shadow-[0_0_16px_color-mix(in_srgb,var(--fx-accent)_18%,transparent)] hover:brightness-110",
        secondary:
          "border border-[var(--fx-border)] bg-[color-mix(in_srgb,var(--fx-panel-elevated)_88%,transparent)] text-[var(--fx-text)] hover:border-[color-mix(in_srgb,var(--fx-border)_55%,var(--fx-accent))] hover:bg-[var(--fx-hover)]",
        outline:
          "border border-[var(--fx-border)] bg-transparent text-[var(--fx-text)] hover:border-[color-mix(in_srgb,var(--fx-border)_55%,var(--fx-accent))] hover:bg-[var(--fx-hover)]",
        ghost:
          "border border-transparent text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]",
        subtle:
          "border border-transparent bg-[var(--fx-accent-faint)] text-[var(--fx-accent)] hover:bg-[var(--fx-accent-soft)]",
        danger:
          "border border-transparent bg-[var(--fx-error)] text-white shadow-[0_0_14px_color-mix(in_srgb,var(--fx-error)_25%,transparent)] hover:brightness-110",
        destructive:
          "border border-transparent bg-[var(--fx-error)] text-white shadow-[0_0_14px_color-mix(in_srgb,var(--fx-error)_25%,transparent)] hover:brightness-110",
        success:
          "border border-transparent bg-[var(--fx-success)] text-[#03150c] hover:brightness-110",
      },
      size: {
        sm: "h-7 px-2.5 text-xs",
        md: "h-8 px-3 text-[13px]",
        default: "h-8 px-3 text-[13px]",
        lg: "h-9 px-4 text-sm",
        icon: "h-8 w-8 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  loading?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, loading = false, disabled, type, children, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        type={asChild ? undefined : type ?? "button"}
        disabled={asChild ? undefined : disabled || loading || undefined}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      >
        {asChild ? (
          children
        ) : (
          <>
            {loading ? <Loader2 aria-hidden className="absolute h-3.5 w-3.5 animate-spin" /> : null}
            <span className={cn("flex items-center justify-center gap-1.5", loading && "invisible")}>
              {children}
            </span>
          </>
        )}
      </Comp>
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
export { IconButton } from "@/components/ui/icon-button";
