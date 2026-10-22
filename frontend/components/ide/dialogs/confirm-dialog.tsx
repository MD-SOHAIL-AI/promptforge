"use client";

import { AlertTriangle, Cpu, X } from "lucide-react";
import { useEffect, useRef } from "react";

export type ConfirmDialogVariant = "default" | "danger" | "hardware";

export interface ConfirmDialogOptions {
  title: string;
  description?: string;
  confirmText?: string;
  cancelText?: string;
  variant?: ConfirmDialogVariant;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  description,
  confirmText = "Confirm",
  cancelText = "Cancel",
  variant = "default",
  onConfirm,
  onCancel,
}: ConfirmDialogOptions) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
      }
      if (event.key === "Tab") {
        const first = cancelRef.current;
        const last = confirmRef.current;
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  const Icon = variant === "hardware" ? Cpu : AlertTriangle;
  const confirmClass =
    variant === "danger"
      ? "bg-[var(--fx-error)] text-white hover:opacity-90"
      : variant === "hardware"
        ? "bg-[var(--fx-warning)] text-white hover:opacity-90"
        : "bg-[var(--fx-accent)] text-white hover:opacity-90";

  return (
    <section
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="forgex-confirm-title"
      className="w-[min(92vw,460px)] overflow-hidden rounded-2xl border border-[var(--fx-border)] bg-[var(--fx-panel)] text-[var(--fx-text)] shadow-[var(--fx-shadow)]"
    >
      <div className="flex items-start justify-between gap-3 border-b border-[var(--fx-border)] px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <Icon className={`h-4 w-4 shrink-0 ${variant === "danger" ? "text-[var(--fx-error)]" : variant === "hardware" ? "text-[var(--fx-warning)]" : "text-[var(--fx-accent)]"}`} />
          <h2 id="forgex-confirm-title" className="truncate text-sm font-semibold">
            {title}
          </h2>
        </div>
        <button
          className="rounded p-1 text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
          onClick={onCancel}
          title="Cancel"
          aria-label="Cancel"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {description ? (
        <div className="whitespace-pre-wrap px-4 py-4 text-sm leading-5 text-[var(--fx-code-text)]">
          {description}
        </div>
      ) : null}
      <div className="flex justify-end gap-2 border-t border-[var(--fx-border)] px-4 py-3">
        <button
          ref={cancelRef}
          className="h-9 rounded border border-[var(--fx-border)] px-3 text-sm text-[var(--fx-text)] hover:bg-[var(--fx-hover)]"
          onClick={onCancel}
        >
          {cancelText}
        </button>
        <button
          ref={confirmRef}
          className={`h-9 rounded px-3 text-sm font-medium ${confirmClass}`}
          onClick={onConfirm}
        >
          {confirmText}
        </button>
      </div>
    </section>
  );
}
