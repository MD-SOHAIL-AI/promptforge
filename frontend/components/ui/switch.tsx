"use client";

import * as SwitchPrimitive from "@radix-ui/react-switch";
import * as React from "react";

import { cn } from "@/lib/utils";

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitive.Root
    ref={ref}
    className={cn(
      "inline-flex h-[18px] w-8 shrink-0 cursor-pointer items-center rounded-full border border-transparent outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-[var(--fx-accent)] disabled:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-[var(--fx-accent)] data-[state=unchecked]:bg-[var(--fx-hover)]",
      className,
    )}
    {...props}
  >
    <SwitchPrimitive.Thumb
      className={cn(
        "pointer-events-none block h-3.5 w-3.5 translate-x-[2px] rounded-full bg-[var(--fx-text-muted)] shadow-sm transition-transform duration-150 data-[state=checked]:translate-x-[16px] data-[state=checked]:bg-[#f2fbff]",
      )}
    />
  </SwitchPrimitive.Root>
));
Switch.displayName = SwitchPrimitive.Root.displayName;

export { Switch };
