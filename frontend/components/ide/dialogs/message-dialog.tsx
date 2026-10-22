"use client";

import { Info, X } from "lucide-react";
import { useEffect, useRef } from "react";

export interface MessageDialogOptions {
  title: string;
  description?: string;
  confirmText?: string;
  onConfirm: () => void;
}

export function MessageDialog({
  title,
  description,
  confirmText = "OK",
  onConfirm,
}: MessageDialogOptions) {
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    buttonRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onConfirm();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onConfirm]);

  return (
    <section
      role="dialog"
      aria-modal="true"
      aria-labelledby="forgex-message-title"
      className="w-[min(92vw,460px)] overflow-hidden rounded-2xl border border-[var(--fx-border)] bg-[var(--fx-panel)] text-[var(--fx-text)] shadow-[var(--fx-shadow)]"
    >
      <div className="flex items-start justify-between gap-3 border-b border-[var(--fx-border)] px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <Info className="h-4 w-4 shrink-0 text-[var(--fx-info)]" />
          <h2 id="forgex-message-title" className="truncate text-sm font-semibold">
            {title}
          </h2>
        </div>
        <button
          className="rounded p-1 text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
          onClick={onConfirm}
          title="Close"
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {description ? (
        <div className="whitespace-pre-wrap px-4 py-4 text-sm leading-5 text-[var(--fx-code-text)]">
          {description}
        </div>
      ) : null}
      <div className="flex justify-end border-t border-[var(--fx-border)] px-4 py-3">
        <button
          ref={buttonRef}
          className="h-9 rounded bg-[var(--fx-accent)] px-3 text-sm font-medium text-white hover:opacity-90"
          onClick={onConfirm}
        >
          {confirmText}
        </button>
      </div>
    </section>
  );
}
