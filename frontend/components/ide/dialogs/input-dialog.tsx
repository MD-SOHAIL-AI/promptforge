"use client";

import { FilePenLine, X } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";

export interface InputDialogOptions {
  title: string;
  description?: string;
  label: string;
  placeholder?: string;
  defaultValue?: string;
  confirmText?: string;
  cancelText?: string;
  validate?: (value: string) => string | null;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export function InputDialog({
  title,
  description,
  label,
  placeholder,
  defaultValue = "",
  confirmText = "Create",
  cancelText = "Cancel",
  validate,
  onConfirm,
  onCancel,
}: InputDialogOptions) {
  const [value, setValue] = useState(defaultValue);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
      }
      if (event.key === "Tab") {
        const nodes = [inputRef.current, cancelRef.current, confirmRef.current].filter(Boolean) as HTMLElement[];
        const first = nodes[0];
        const last = nodes.at(-1);
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

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const nextError = validate?.(value) ?? null;
    setError(nextError);
    if (nextError) return;
    onConfirm(value.trim().replaceAll("\\", "/"));
  };

  return (
    <section
      role="dialog"
      aria-modal="true"
      aria-labelledby="forgex-input-title"
      className="w-[min(92vw,520px)] overflow-hidden rounded-2xl border border-[var(--fx-border)] bg-[var(--fx-panel)] text-[var(--fx-text)] shadow-[var(--fx-shadow)]"
    >
      <form onSubmit={submit}>
        <div className="flex items-start justify-between gap-3 border-b border-[var(--fx-border)] px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <FilePenLine className="h-4 w-4 shrink-0 text-[var(--fx-accent)]" />
            <h2 id="forgex-input-title" className="truncate text-sm font-semibold">
              {title}
            </h2>
          </div>
          <button
            type="button"
            className="rounded p-1 text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
            onClick={onCancel}
            title="Cancel"
            aria-label="Cancel"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="grid gap-3 px-4 py-4">
          {description ? <p className="text-sm leading-5 text-[var(--fx-text-muted)]">{description}</p> : null}
          <label className="grid gap-1 text-sm">
            <span className="text-[var(--fx-code-text)]">{label}</span>
            <input
              ref={inputRef}
              className="h-9 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)] focus:border-[var(--fx-accent)]"
              value={value}
              placeholder={placeholder}
              onChange={(event) => {
                setValue(event.target.value);
                if (error) setError(null);
              }}
            />
          </label>
          {error ? (
            <p className="rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-2 py-1 text-xs text-[var(--fx-error)]">
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-[var(--fx-border)] px-4 py-3">
          <button
            ref={cancelRef}
            type="button"
            className="h-9 rounded border border-[var(--fx-border)] px-3 text-sm text-[var(--fx-text)] hover:bg-[var(--fx-hover)]"
            onClick={onCancel}
          >
            {cancelText}
          </button>
          <button
            ref={confirmRef}
            type="submit"
            className="h-9 rounded bg-[var(--fx-accent)] px-3 text-sm font-medium text-white hover:opacity-90"
          >
            {confirmText}
          </button>
        </div>
      </form>
    </section>
  );
}
