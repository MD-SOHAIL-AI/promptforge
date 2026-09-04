"use client";

import { AlertTriangle, Cpu } from "lucide-react";

import { Button } from "@/components/ui/button";

export type ConfirmDialogVariant = "default" | "danger" | "hardware";

export interface ConfirmDialogOptions {
  title?: string;
  description?: string;
  confirmText?: string;
  cancelText?: string;
  variant?: ConfirmDialogVariant;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  confirmText = "Confirm",
  cancelText = "Cancel",
  variant = "default",
  onConfirm,
  onCancel,
}: ConfirmDialogOptions) {
  const Icon = variant === "hardware" ? Cpu : variant === "danger" ? AlertTriangle : null;

  return (
    <div className="flex justify-end gap-2 border-t border-[var(--fx-border-soft)] px-4 py-3">
      <Button variant="secondary" size="md" onClick={onCancel}>
        {cancelText}
      </Button>
      <Button
        variant={variant === "danger" ? "danger" : "default"}
        size="md"
        onClick={onConfirm}
        className={
          variant === "hardware"
            ? "border-[color-mix(in_srgb,var(--fx-warning)_45%,transparent)] bg-[var(--fx-warning-soft)] text-[var(--fx-warning)] shadow-none hover:brightness-110"
            : undefined
        }
      >
        {Icon ? <Icon className="h-3.5 w-3.5" /> : null}
        {confirmText}
      </Button>
    </div>
  );
}
