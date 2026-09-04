"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import * as React from "react";

import { IconButton } from "@/components/ui/icon-button";
import { cn } from "@/lib/utils";

export type FxOverlaySize = "sm" | "md" | "lg" | "drawer";

const sizeClasses: Record<Exclude<FxOverlaySize, "drawer">, string> = {
  sm: "max-w-[400px]",
  md: "max-w-[520px]",
  lg: "max-w-[720px]",
};

const EASE: [number, number, number, number] = [0.2, 0.8, 0.2, 1];

function isMotionOff() {
  if (typeof window === "undefined") return false;
  if (document.documentElement.getAttribute("data-motion") === "off") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export interface FxOverlayProps {
  open: boolean;
  onOpenChange?: (open: boolean) => void;
  onClose?: () => void;
  title?: string;
  label?: string;
  description?: string;
  children?: React.ReactNode;
  size?: FxOverlaySize;
  initialFocusId?: string;
}

export function FxOverlay({
  open,
  onOpenChange,
  onClose,
  title,
  label,
  description,
  children,
  size = "md",
  initialFocusId,
}: FxOverlayProps) {
  const [motionOff, setMotionOff] = React.useState(false);

  React.useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setMotionOff(isMotionOff());
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, [open]);

  const isDrawer = size === "drawer";
  const hasHeader = Boolean(title || description);

  const handleOpenChange = React.useCallback(
    (next: boolean) => {
      if (!next) onClose?.();
      onOpenChange?.(next);
    },
    [onClose, onOpenChange],
  );

  const handleOpenAutoFocus = (event: Event) => {
    if (!initialFocusId) return;
    const target = document.getElementById(initialFocusId);
    if (!target) return;
    event.preventDefault();
    target.focus();
    if (target instanceof HTMLInputElement) target.select();
  };

  return (
    <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
      <AnimatePresence>
        {open ? (
          <DialogPrimitive.Portal forceMount>
            <DialogPrimitive.Overlay asChild forceMount>
              <motion.div
                initial={motionOff ? false : { opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={motionOff ? undefined : { opacity: 0 }}
                transition={{ duration: motionOff ? 0 : 0.15, ease: "easeOut" }}
                className="fixed inset-0 z-[180] bg-black/60 backdrop-blur-[2px]"
              />
            </DialogPrimitive.Overlay>
            <DialogPrimitive.Content asChild forceMount onOpenAutoFocus={handleOpenAutoFocus}>
              <div
                className={cn(
                  "pointer-events-none fixed inset-0 z-[190] flex outline-none",
                  isDrawer ? "items-stretch justify-end" : "items-center justify-center p-4",
                )}
              >
                <motion.div
                  initial={
                    motionOff
                      ? false
                      : isDrawer
                        ? { x: "100%" }
                        : { opacity: 0, scale: 0.96, y: 8 }
                  }
                  animate={isDrawer ? { x: 0 } : { opacity: 1, scale: 1, y: 0 }}
                  exit={
                    motionOff
                      ? undefined
                      : isDrawer
                        ? { x: "100%" }
                        : { opacity: 0, scale: 0.96, y: 8 }
                  }
                  transition={{ duration: motionOff ? 0 : 0.18, ease: EASE }}
                  className={cn(
                    "pointer-events-auto relative flex min-h-0 flex-col overflow-hidden border bg-[var(--fx-panel-elevated)] text-[var(--fx-text)] shadow-[var(--fx-shadow)]",
                    isDrawer
                      ? "h-full w-[min(420px,90vw)] rounded-l-[var(--fx-radius)] border-l border-[var(--fx-border)]"
                      : cn(
                          "max-h-[86vh] w-full rounded-[var(--fx-radius)] border-[var(--fx-border-soft)]",
                          sizeClasses[size],
                        ),
                  )}
                >
                  {hasHeader ? (
                    <header className="flex items-start justify-between gap-3 border-b border-[var(--fx-border-soft)] px-4 py-3 pr-2">
                      <div className="min-w-0 space-y-1">
                        <DialogPrimitive.Title className="truncate text-sm font-semibold tracking-[-0.01em]">
                          {title}
                        </DialogPrimitive.Title>
                        {description ? (
                          <DialogPrimitive.Description className="whitespace-pre-wrap text-xs leading-5 text-[var(--fx-text-muted)]">
                            {description}
                          </DialogPrimitive.Description>
                        ) : null}
                      </div>
                      <DialogPrimitive.Close asChild>
                        <IconButton label="Close" className="-mr-1">
                          <X className="h-4 w-4" />
                        </IconButton>
                      </DialogPrimitive.Close>
                    </header>
                  ) : (
                    <>
                      {label ? (
                        <DialogPrimitive.Title className="sr-only">{label}</DialogPrimitive.Title>
                      ) : null}
                      <DialogPrimitive.Close asChild>
                        <IconButton label="Close" className="absolute right-2 top-2 z-10">
                          <X className="h-4 w-4" />
                        </IconButton>
                      </DialogPrimitive.Close>
                    </>
                  )}
                  <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
                </motion.div>
              </div>
            </DialogPrimitive.Content>
          </DialogPrimitive.Portal>
        ) : null}
      </AnimatePresence>
    </DialogPrimitive.Root>
  );
}
